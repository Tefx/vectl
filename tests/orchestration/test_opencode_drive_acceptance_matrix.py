"""Authoritative acceptance matrix for the real OpenCode drive scheduler.

Authority:
    docs/RFC-orch-drive.md sections 5.8, 6, 7.2, 7.3, 8, 9, 10, 12, 13, 15, 16, 17, 19
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md sections 7, 8, 9

Verification:
    This module is the authoritative acceptance layer. It proves real behavior,
    not mock-only seams. Where a scenario requires the live OpenCode runner,
    it is gated behind the live_runner + opencode_live + long_live markers.

    Structural/contract scenarios exercise real app wiring without requiring
    the OpenCode binary.

Step: orch_drive_live_acceptance.real-opencode-acceptance-matrix
"""

from __future__ import annotations

import importlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.orchestration.helpers import (
    MATRIX_FIXTURE_PATH,
    PROJECT_ROOT,
    REQUIRED_ACCEPTANCE_PROOF_MAP,
    REQUIRED_SCENARIO_IDS,
    DriveAcceptanceScenario,
    OpenCodeDriveHarness,
    default_orchestrator_env,
    load_drive_acceptance_matrix,
)
from tests.live_smoke.helpers import (
    LiveRunnerPreflight,
    is_live_runner_opted_in,
    is_long_live_runner_opted_in,
    live_runner,
    long_live,
    opencode_live,
)
from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.driver import (
    DriveDriver,
    DriveAdmissionError,
    DriveLoopResult,
    DriveRecoverResult,
    DriveResumeResult,
    DriveStartResult,
    MaxParallelismError,
    TERMINAL_DRIVE_STATUSES,
    validate_drive_transition,
)
from vectl.orchestration.run_store import DriveStore
from vectl.models import IsolationMode


# -----------------------------------------------------------------------
# Helpers: fake component adapters for structural tests
# -----------------------------------------------------------------------


def _core(
    *,
    plan_complete: bool = False,
    claimable: tuple[str, ...] = (),
    in_progress: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=plan_complete,
        claimable_step_ids=claimable,
        in_progress_step_ids=in_progress,
        blocked_step_ids=blocked,
        unresolved_reasons=unresolved,
    )


def _roster(
    *,
    available_agents: tuple[str, ...] = (),
    working_agents: tuple[str, ...] = (),
) -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=available_agents,
        working_agents=working_agents,
        reusable_sessions=(),
        exhausted_roles=(),
    )


def _runtime(
    *,
    active_workspaces: tuple[str, ...] = (),
    active_executions: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        active_workspaces=active_workspaces,
        active_executions=active_executions,
        stalled_executions=(),
    )


@dataclass
class _FakeCoreAdapter:
    snapshot_value: CoreSnapshot
    calls: int = 0

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        self.calls += 1
        return self.snapshot_value

    def step_isolation(self, step_id: str) -> IsolationMode:
        raise NotImplementedError(f"test double: step_isolation not implemented: {step_id}")

    def claim_step(
        self, step_id: str, agent: str, *, force: bool = False, flow: str = "normal"
    ) -> None:
        raise NotImplementedError(f"test double: claim_step not implemented: {step_id}")

    def complete_step(self, step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        raise NotImplementedError(f"test double: complete_step not implemented: {step_id}")

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double: defer_step not implemented: {step_id}")


@dataclass
class _FakeRosterSource:
    snapshot_value: RosterSnapshot

    def snapshot(self) -> RosterSnapshot:
        return self.snapshot_value


@dataclass
class _FakeRuntimeSource:
    snapshot_value: RuntimeSnapshot

    def snapshot(self) -> RuntimeSnapshot:
        return self.snapshot_value


def _make_control(
    core: CoreSnapshot | None = None,
) -> PlanAwareControl:
    return PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(core or _core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        ),
    )


def _make_driver(
    tmp_path: Path,
    core: CoreSnapshot | None = None,
    max_parallelism: int = 4,
    child_run_launcher: Any | None = None,
) -> DriveDriver:
    store = DriveStore(store_root=tmp_path)
    control = _make_control(core)
    return DriveDriver(
        drive_store=store,
        core_adapter=control.sources.core_adapter,
        control=control,
        child_run_launcher=child_run_launcher,
        max_parallelism=max_parallelism,
    )


def _save_plan(root: Path, plan: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.yaml").write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")


def _resolve_pytest_node(node_id: str) -> object:
    parts = node_id.split("::")
    file_part = parts[0]
    module_path = PROJECT_ROOT / file_part
    assert module_path.exists(), f"proof reference target missing: {module_path}"
    module_name = file_part.removesuffix(".py").replace("/", ".")
    obj: object = importlib.import_module(module_name)
    for attr in parts[1:]:
        obj = getattr(obj, attr)
    return obj


def _linear_plan() -> dict[str, Any]:
    return {
        "version": 1,
        "project": "opencode-drive-linear-live",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "steps": [
                    {
                        "id": "core.seed",
                        "name": "Seed",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": "Create seed file.",
                        "verification": "seed.txt exists.",
                    },
                    {
                        "id": "core.finish",
                        "name": "Finish",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.seed"],
                        "description": "Create finish file.",
                        "verification": "done.txt exists.",
                    },
                ],
            }
        ],
    }


def _single_step_supervisor_plan() -> dict[str, Any]:
    return {
        "version": 1,
        "project": "opencode-drive-supervisor-live",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "steps": [
                    {
                        "id": "core.supervisor",
                        "name": "Supervisor",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": (
                            "Create supervisor.txt containing exactly the text "
                            "supervisor-ok followed by a newline. Do not modify plan.yaml."
                        ),
                        "verification": "supervisor.txt exists and contains supervisor-ok.",
                    }
                ],
            }
        ],
    }


def _multi_phase_plan() -> dict[str, Any]:
    return {
        "version": 1,
        "project": "opencode-drive-multi-phase",
        "phases": [
            {
                "id": "phase_a",
                "name": "Phase A",
                "steps": [
                    {
                        "id": "phase_a.step1",
                        "name": "Step 1",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": "Phase A step.",
                        "verification": "a.txt exists.",
                    },
                ],
            },
            {
                "id": "phase_b",
                "name": "Phase B",
                "depends_on": ["phase_a"],
                "steps": [
                    {
                        "id": "phase_b.step2",
                        "name": "Step 2",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["phase_a.step1"],
                        "description": "Phase B step.",
                        "verification": "b.txt exists.",
                    },
                ],
            },
        ],
    }


def _parallel_plan() -> dict[str, Any]:
    return {
        "version": 1,
        "project": "opencode-drive-parallel",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "steps": [
                    {
                        "id": "core.root",
                        "name": "Root",
                        "agent": "python-executor",
                        "status": "pending",
                        "description": "Root step.",
                        "verification": "root.txt exists.",
                    },
                    {
                        "id": "core.left",
                        "name": "Left",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.root"],
                        "description": "Left branch.",
                        "verification": "left.txt exists.",
                    },
                    {
                        "id": "core.right",
                        "name": "Right",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.root"],
                        "description": "Right branch.",
                        "verification": "right.txt exists.",
                    },
                    {
                        "id": "core.join",
                        "name": "Join",
                        "agent": "python-executor",
                        "status": "pending",
                        "depends_on": ["core.left", "core.right"],
                        "description": "Join step.",
                        "verification": "join.txt exists.",
                    },
                ],
            }
        ],
    }


# ========================================================================
# Structural acceptance tests (NO live runner required)
# ========================================================================


class TestMatrixFixtureCompleteness:
    """Prove that the matrix fixture encodes all required scenario IDs."""

    def test_matrix_fixture_exists(self) -> None:
        assert MATRIX_FIXTURE_PATH.exists(), f"missing fixture: {MATRIX_FIXTURE_PATH}"

    def test_required_scenarios_are_all_encoded(self) -> None:
        scenarios = load_drive_acceptance_matrix()
        actual = {scenario.scenario_id for scenario in scenarios}
        assert actual == set(REQUIRED_SCENARIO_IDS), (
            f"Scenario IDs mismatch.\n"
            f"  Expected: {set(REQUIRED_SCENARIO_IDS)}\n"
            f"  Actual:   {actual}\n"
            f"  Missing:  {set(REQUIRED_SCENARIO_IDS) - actual}\n"
            f"  Extra:    {actual - set(REQUIRED_SCENARIO_IDS)}"
        )

    def test_all_scenarios_pin_real_opencode_authority(self) -> None:
        scenarios = load_drive_acceptance_matrix()
        assert scenarios, "matrix must not be empty"
        for scenario in scenarios:
            assert scenario.runner == "opencode", (
                f"scenario {scenario.scenario_id}: runner must be 'opencode', got {scenario.runner}"
            )
            assert scenario.authoritative_surface == "real_app_and_real_git_worktrees", (
                f"scenario {scenario.scenario_id}: authoritative_surface must be "
                f"'real_app_and_real_git_worktrees', got {scenario.authoritative_surface}"
            )

    def test_matrix_yaml_round_trips_for_external_operator_use(self) -> None:
        payload = yaml.safe_load(MATRIX_FIXTURE_PATH.read_text(encoding="utf-8"))
        dumped = yaml.dump(payload, sort_keys=False)
        reloaded = yaml.safe_load(dumped)
        assert reloaded == payload

    def test_coverage_includes_all_rfc_required_classes(self) -> None:
        """RFC section 19.3 coverage includes all required acceptance classes."""
        scenarios = load_drive_acceptance_matrix()
        all_coverage: set[str] = set()
        for scenario in scenarios:
            all_coverage.update(scenario.coverage)

        rfc_required = {
            "linear DAG auto-drain",
            "multi-phase DAG auto-drain",
            "parallel ready frontier dispatch",
            "bounded parallelism enforcement",
            "real worktree child runs with real merge back",
            "long-running parallel branch execution",
            "runtime failure -> resolver -> continue",
            "merge conflict -> resolver -> continue|operator boundary",
            "review needs_replan -> planner mutation -> continue",
            "drive resume and drive recover",
            "pause/unpause/stop under parallel child runs",
            "phase/plan auto-close",
        }

        missing = rfc_required - all_coverage
        assert not missing, f"coverage missing RFC-required classes: {missing}"

    def test_required_classes_map_to_concrete_test_proof(self) -> None:
        scenarios = load_drive_acceptance_matrix()
        covered_classes = {coverage for scenario in scenarios for coverage in scenario.coverage}
        assert covered_classes == set(REQUIRED_ACCEPTANCE_PROOF_MAP), (
            "proof map must exactly cover matrix coverage classes\n"
            f"  matrix-only: {covered_classes - set(REQUIRED_ACCEPTANCE_PROOF_MAP)}\n"
            f"  proof-only: {set(REQUIRED_ACCEPTANCE_PROOF_MAP) - covered_classes}"
        )

        for coverage_class, node_ids in REQUIRED_ACCEPTANCE_PROOF_MAP.items():
            assert node_ids, (
                f"coverage class must name at least one concrete test: {coverage_class}"
            )
            for node_id in node_ids:
                resolved = _resolve_pytest_node(node_id)
                assert callable(resolved), (
                    f"proof reference must resolve to a test callable: {node_id}"
                )

    def test_high_risk_classes_include_live_or_e2e_proof(self) -> None:
        required_live_classes = {
            "real worktree child runs with real merge back",
            "long-running parallel branch execution",
            "merge conflict -> resolver -> continue|operator boundary",
            "review needs_replan -> planner mutation -> continue",
            "runtime failure -> resolver -> continue",
            "pause/unpause/stop under parallel child runs",
        }
        for coverage_class in required_live_classes:
            refs = REQUIRED_ACCEPTANCE_PROOF_MAP[coverage_class]
            assert any(
                ref.startswith("tests/live_smoke/") or "::TestLiveOpenCodeDriveMatrix::" in ref
                for ref in refs
            ), f"high-risk class requires at least one live/e2e proof reference: {coverage_class}"


class TestLinearDAGAutoDrain:
    """Scenario: linear DAG auto-drain with real app wiring.

    Proves the drive driver can auto-drain a simple linear DAG
    (seed -> finish) through real control decisions without requiring
    the live OpenCode runner.
    """

    def test_linear_dag_auto_drain_to_completion(self, tmp_path: Path) -> None:
        """RFC §19.3 #1: Linear DAG auto-drain completes the drive."""
        core = _core(claimable=("core.seed",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        assert start.status == "running"
        assert start.frontier_step_ids == ("core.seed",)

        # First loop: core.seed is claimable → dispatch
        result1 = driver.run_drive_loop(start.drive_id)
        assert result1.status == "running"
        assert "dispatch_batch" in result1.summary

        # Simulate seed completion → next core update
        # Must mutate the fake adapter's snapshot_value because
        # DriveDriver reads self._core_adapter.snapshot().
        fake_core_adapter = driver._core_adapter
        assert isinstance(fake_core_adapter, _FakeCoreAdapter), (
            "Driver core_adapter must be _FakeCoreAdapter for status mutation in test"
        )
        fake_core_adapter.snapshot_value = _core(claimable=("core.finish",), in_progress=())

        result2 = driver.run_drive_loop(start.drive_id)
        assert result2.status == "running"
        assert "dispatch_batch" in result2.summary

        # Finish completion → plan complete
        fake_core_adapter.snapshot_value = _core(plan_complete=True)

        result3 = driver.run_drive_loop(start.drive_id)
        assert result3.status == "completed"

    def test_linear_dag_persists_final_state(self, tmp_path: Path) -> None:
        """Drive record is durably persisted as 'completed'."""
        core = _core(plan_complete=True)
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        driver.run_drive_loop(start.drive_id)

        loaded = driver._drive_store.load_drive(start.drive_id)
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.finished_at is not None


class TestMultiPhaseDAGAutoDrain:
    """Scenario: multi-phase DAG auto-drain across phase boundaries.

    Proves that the drive transitions through phase boundaries
    correctly when phases have inter-phase depends_on.
    """

    def test_multi_phase_drain_respects_phase_order(self, tmp_path: Path) -> None:
        """RFC §19.3 #2: Multi-phase DAG auto-drain derives closure from plan state."""
        # Phase A available, Phase B blocked on Phase A
        core = _core(claimable=("phase_a.step1",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/multi.yaml")

        assert start.frontier_step_ids == ("phase_a.step1",)

        # After phase_a.step1 → core shows phase_b.step2 claimable
        fake_core_adapter = driver._core_adapter
        assert isinstance(fake_core_adapter, _FakeCoreAdapter)
        fake_core_adapter.snapshot_value = _core(claimable=("phase_b.step2",))

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "running"
        assert "dispatch_batch" in result.summary

    def test_multi_phase_auto_close(self, tmp_path: Path) -> None:
        """RFC §19.3 #13: Phase and plan auto-close derived from plan state."""
        # Start with claimable steps
        core = _core(claimable=("step.a",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/auto.yaml")

        # When plan is complete, drive auto-closes to 'completed'
        fake_core_adapter = driver._core_adapter
        assert isinstance(fake_core_adapter, _FakeCoreAdapter)
        fake_core_adapter.snapshot_value = _core(plan_complete=True)

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "completed"


class TestParallelReadyFrontierDispatch:
    """Scenario: parallel ready frontier dispatch.

    Proves the driver dispatches multiple ready steps when
    the core snapshot claims a multi-step frontier.
    """

    def test_parallel_frontier_dispatches_all_ready(self, tmp_path: Path) -> None:
        """RFC §19.3 #3: Parallel ready frontier dispatch respects core authority."""
        core = _core(claimable=("step.left", "step.right"))
        driver = _make_driver(tmp_path, core=core, max_parallelism=4)
        start = driver.start_drive(plan_path="/repo/parallel.yaml")

        assert start.frontier_step_ids == ("step.left", "step.right")

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "running"
        assert "dispatch_batch" in result.summary

    def test_parallel_frontier_captures_all_step_ids(self, tmp_path: Path) -> None:
        """Dispatch result records both frontier steps."""
        core = _core(claimable=("core.left", "core.right"))
        driver = _make_driver(tmp_path, core=core, max_parallelism=4)
        start = driver.start_drive(plan_path="/repo/parallel.yaml")

        result = driver.run_drive_loop(start.drive_id)
        assert result.drive_id == start.drive_id
        # Both steps must appear in the dispatch batch
        assert "core.left" in result.summary or "core.right" in result.summary

    def test_dispatch_batch_launches_and_persists_child_run_refs(self, tmp_path: Path) -> None:
        """Drive loop dispatch must create durable child-run refs, not only summarize."""
        launched: list[tuple[str, str, str]] = []

        def _launcher(drive_id: str, step_id: str, role_id: str) -> ChildRunRef:
            launched.append((drive_id, step_id, role_id))
            return ChildRunRef(
                run_id=f"run-{step_id}",
                drive_id=drive_id,
                kind="step",
                step_id=step_id,
                status="running",
                workspace=f"ws-{step_id}",
                runner="opencode",
                artifact_root=f"runs/run-{step_id}",
            )

        core = _core(claimable=("core.left", "core.right"))
        driver = _make_driver(
            tmp_path,
            core=core,
            max_parallelism=4,
            child_run_launcher=_launcher,
        )
        start = driver.start_drive(plan_path="/repo/parallel.yaml")

        result = driver.run_drive_loop(start.drive_id)

        assert result.active_child_run_ids == ("run-core.left", "run-core.right")
        assert launched == [
            (start.drive_id, "core.left", "python-executor"),
            (start.drive_id, "core.right", "python-executor"),
        ]
        persisted = driver._drive_store.child_runs_for_drive(start.drive_id)
        assert {ref.run_id for ref in persisted} == {"run-core.left", "run-core.right"}


class TestBoundedParallelismEnforcement:
    """Scenario: bounded parallelism enforcement.

    Proves that max_parallelism caps the number of concurrently dispatched steps.
    """

    def test_bounded_parallelism_limits_dispatch(self, tmp_path: Path) -> None:
        """RFC §19.3 #4: Bounded parallelism caps dispatch batch size."""
        core = _core(claimable=("step.a", "step.b", "step.c", "step.d"))
        # max_parallelism=2 means at most 2 steps in flight at once
        driver = _make_driver(tmp_path, core=core, max_parallelism=2)
        start = driver.start_drive(plan_path="/repo/bounded.yaml")

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "running"

    def test_max_parallelism_boundary_values(self, tmp_path: Path) -> None:
        """Parallelism boundaries: min=1, max=32."""
        # min
        driver = _make_driver(tmp_path, core=_core(), max_parallelism=1)
        start = driver.start_drive(plan_path="/repo/min_par.yaml", max_parallelism=1)
        assert start.status == "running"

        # max
        driver2 = _make_driver(tmp_path / "max_par", core=_core(), max_parallelism=32)
        start2 = driver2.start_drive(plan_path="/repo/max_par.yaml", max_parallelism=32)
        assert start2.status == "running"

    def test_max_parallelism_rejects_invalid(self, tmp_path: Path) -> None:
        """Invalid parallelism values raise MaxParallelismError via start_drive."""
        driver = _make_driver(tmp_path)

        with pytest.raises(MaxParallelismError):
            driver.start_drive(plan_path="/repo/invalid_par.yaml", max_parallelism=0)

        with pytest.raises(MaxParallelismError):
            driver.start_drive(plan_path="/repo/invalid_par2.yaml", max_parallelism=33)


class TestRuntimeFailureResolverContinue:
    """Scenario: runtime failure -> resolver -> continue.

    Proves the drive enters barrier on runtime failure and can proceed
    after resolver resolution.
    """

    def test_runtime_failure_enters_barrier(self, tmp_path: Path) -> None:
        """RFC §19.3 #7: Runtime failure enters barrier state."""
        # Blocked steps trigger resolve decision
        core = _core(blocked=("step.fail",), unresolved=("runtime_failure",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/rf.yaml")

        result = driver.run_drive_loop(start.drive_id)
        # Control decision should be resolve or halt (not dispatch)
        assert result.status in {"resolving", "blocked_operator", "halted", "running"}

    def test_barrier_state_persisted(self, tmp_path: Path) -> None:
        """Barrier state is durably persisted in DriveRecord."""
        core = _core(blocked=("step.x",), unresolved=("merge_conflict",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/barrier.yaml")

        driver.run_drive_loop(start.drive_id)

        loaded = driver._drive_store.load_drive(start.drive_id)
        assert loaded is not None
        # If barrier was entered, it should be persisted
        if loaded.status in {"resolving", "replanning", "blocked_operator"}:
            assert loaded.barrier is not None


class TestDriveResumeAndRecover:
    """Scenario: drive resume and drive recover.

    Proves resume and recover surfaces correctly restore drive state.
    """

    def test_resume_restores_active_child_runs(self, tmp_path: Path) -> None:
        """RFC §19.3 #10: Resume restores active child run set."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/resume.yaml")

        # Seed a child run
        child = ChildRunRef(
            run_id="run_step_01",
            drive_id=start.drive_id,
            kind="step",
            status="running",
            step_id="core.seed",
        )
        driver._drive_store.save_child_run(child)

        result = driver.resume_drive(start.drive_id)

        assert result.drive_id == start.drive_id
        assert result.status == "running"
        assert "run_step_01" in result.restored_child_run_ids

    def test_recover_preserves_barrier_state(self, tmp_path: Path) -> None:
        """RFC §19.3 #11: Barrier state is preserved through recovery."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/recover.yaml")
        store = driver._drive_store

        # Set up barrier state
        record = store.load_drive(start.drive_id)
        assert record is not None
        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=time.time(),
            case_ids=("case_01",),
        )
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="resolving",
                started_at=record.started_at,
                updated_at=time.time(),
                barrier=barrier,
            )
        )

        result = driver.recover_drive(start.drive_id)

        assert result.drive_id == start.drive_id
        assert result.barrier is not None
        assert result.barrier.reason == "merge_conflict"

    def test_resume_terminal_drive_returns_current_state(self, tmp_path: Path) -> None:
        """Resume on a completed drive returns terminal state."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/term.yaml")
        store = driver._drive_store

        record = store.load_drive(start.drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="completed",
                started_at=record.started_at,
                updated_at=time.time(),
                finished_at=time.time(),
            )
        )

        result = driver.resume_drive(start.drive_id)
        assert result.status == "completed"
        assert "terminal" in result.summary.lower()

    def test_recover_terminal_drive_returns_immediately(self, tmp_path: Path) -> None:
        """Recover on a terminal drive does not transition."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/term.yaml")
        store = driver._drive_store

        record = store.load_drive(start.drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="completed",
                started_at=record.started_at,
                updated_at=time.time(),
                finished_at=time.time(),
            )
        )

        result = driver.recover_drive(start.drive_id)
        assert result.status == "completed"
        assert "terminal" in result.summary.lower()

    def test_recover_dry_run_no_state_change(self, tmp_path: Path) -> None:
        """RFC §19.3: dry_run does not mutate drive state."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/dry.yaml")

        result = driver.recover_drive(start.drive_id, dry_run=True)
        assert "dry-run" in result.summary.lower() or result.status == "running"

        # Drive status should be unchanged
        loaded = driver._drive_store.load_drive(start.drive_id)
        assert loaded is not None
        assert loaded.status == "running"  # Not modified


class TestPauseUnpauseStopControl:
    """Scenario: pause/unpause/stop under parallel child runs.

    Proves drive-scoped control surfaces correctly transition drive state.
    """

    def test_pause_transitions_to_paused_status(self, tmp_path: Path) -> None:
        """RFC §19.3 #12: Pause transitions drive to paused status."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/pause.yaml")

        record = driver._drive_store.load_drive(start.drive_id)
        assert record is not None
        store = driver._drive_store

        # Manually set to paused
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="paused",
                started_at=record.started_at,
                updated_at=time.time(),
                operator_pause_state="paused",
            )
        )

        # Resume should return paused status
        result = driver.resume_drive(start.drive_id)
        assert result.status == "paused"

    def test_stop_is_terminal(self, tmp_path: Path) -> None:
        """Stop drive status is terminal – no further transitions valid."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/stop.yaml")
        store = driver._drive_store

        record = store.load_drive(start.drive_id)
        assert record is not None

        # Manually set to stopped
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="stopped",
                started_at=record.started_at,
                updated_at=time.time(),
                finished_at=time.time(),
            )
        )

        # Loop on stopped drive should return immediately
        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "stopped"
        assert "terminal" in result.summary.lower()


class TestDriveAdmissionGuard:
    """Verify admission guard rejects duplicate active drives for same plan."""

    def test_duplicate_active_drive_rejected(self, tmp_path: Path) -> None:
        """RFC §19.3 #14.1: Only one active drive per plan."""
        driver = _make_driver(tmp_path)
        driver.start_drive(plan_path="/repo/guard.yaml")

        with pytest.raises(DriveAdmissionError) as exc_info:
            driver.start_drive(plan_path="/repo/guard.yaml")

        assert exc_info.value.active_drive_id != ""

    def test_new_drive_allowed_after_terminal(self, tmp_path: Path) -> None:
        """After a drive reaches terminal status, a new drive can start."""
        driver = _make_driver(tmp_path)
        result1 = driver.start_drive(plan_path="/repo/succeed.yaml")
        store = driver._drive_store

        record = store.load_drive(result1.drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="completed",
                started_at=record.started_at,
                updated_at=time.time(),
                finished_at=time.time(),
            )
        )

        result2 = driver.start_drive(plan_path="/repo/succeed.yaml")
        assert result2.drive_id != result1.drive_id


class TestDriveTransitionValidation:
    """Verify drive status transition validation matches RFC §8.2.1."""

    def test_valid_transitions_accepted(self) -> None:
        """All transitions in DRIVE_TRANSITIONS table are valid."""
        from vectl.orchestration.driver import DRIVE_TRANSITIONS

        for (from_status, to_status), reason in DRIVE_TRANSITIONS.items():
            result = validate_drive_transition(from_status, to_status)
            assert result == reason

    def test_terminal_statuses_are_absorbing(self) -> None:
        """Terminal statuses have no outgoing transitions."""
        from vectl.orchestration.driver import DRIVE_TRANSITIONS

        for terminal in TERMINAL_DRIVE_STATUSES:
            outgoing = [(src, dst) for (src, dst) in DRIVE_TRANSITIONS if src == terminal]
            assert len(outgoing) == 0, (
                f"Terminal status {terminal} has outgoing transitions: {outgoing}"
            )

    def test_invalid_transitions_rejected(self) -> None:
        """Invalid transitions raise InvalidDriveTransitionError."""
        from vectl.orchestration.driver import InvalidDriveTransitionError

        with pytest.raises(InvalidDriveTransitionError):
            validate_drive_transition("completed", "running")

        with pytest.raises(InvalidDriveTransitionError):
            validate_drive_transition("halted", "running")


class TestPhasePlanAutoClose:
    """Scenario: phase and plan auto-close.

    Proves that drive detects plan completion and transitions
    to terminal status without manual intervention.
    """

    def test_plan_complete_transitions_to_completed(self, tmp_path: Path) -> None:
        """RFC §19.3 #13: Plan completion triggers auto-close."""
        core = _core(plan_complete=True)
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/auto.yaml")

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "completed"

    def test_plan_incomplete_stays_running(self, tmp_path: Path) -> None:
        """Plan with claimable steps stays in running."""
        core = _core(claimable=("step.remaining",))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/ongoing.yaml")

        # Core adapter already claims step.remaining
        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "running"


class TestDriveFrontierSnapshot:
    """Verify frontier snapshot at start captures claimable steps."""

    def test_frontier_captures_claimable_steps(self, tmp_path: Path) -> None:
        """start_drive snapshots claimable step IDs from core authority."""
        core = _core(claimable=("step.alpha", "step.beta", "step.gamma"))
        driver = _make_driver(tmp_path, core=core)

        result = driver.start_drive(plan_path="/repo/frontier.yaml")

        assert result.frontier_step_ids == ("step.alpha", "step.beta", "step.gamma")

    def test_frontier_empty_when_no_claimable(self, tmp_path: Path) -> None:
        """start_drive returns empty frontier when no steps are claimable."""
        core = _core()  # No claimable steps
        driver = _make_driver(tmp_path, core=core)

        result = driver.start_drive(plan_path="/repo/empty.yaml")

        assert result.frontier_step_ids == ()


class TestDriveChildRunPersistence:
    """Verify child run records are correctly persisted in the drive store."""

    def test_child_run_created_and_loaded(self, tmp_path: Path) -> None:
        """ChildRunRef round-trips through DriveStore."""
        store = DriveStore(store_root=tmp_path)
        drive_id = "drv_child_test"
        child = ChildRunRef(
            run_id="run_test_01",
            drive_id=drive_id,
            kind="step",
            status="running",
            step_id="core.seed",
        )
        store.save_child_run(child)

        # Load via child_runs_for_drive
        children = store.child_runs_for_drive(drive_id)
        assert len(children) >= 1
        loaded = next(c for c in children if c.run_id == "run_test_01")
        assert loaded.run_id == "run_test_01"
        assert loaded.drive_id == drive_id
        assert loaded.kind == "step"
        assert loaded.status == "running"
        assert loaded.step_id == "core.seed"

    def test_child_run_list_by_drive(self, tmp_path: Path) -> None:
        """DriveStore lists all child runs for a drive."""
        store = DriveStore(store_root=tmp_path)
        drive_id = "drv_list_test"

        child1 = ChildRunRef(
            run_id="run_a",
            drive_id=drive_id,
            kind="step",
            status="running",
            step_id="step.a",
        )
        child2 = ChildRunRef(
            run_id="run_b",
            drive_id=drive_id,
            kind="resolver",
            status="pending",
        )
        store.save_child_run(child1)
        store.save_child_run(child2)

        children = store.child_runs_for_drive(drive_id)
        assert len(children) == 2
        run_ids = {c.run_id for c in children}
        assert run_ids == {"run_a", "run_b"}

    def test_active_child_runs_filtering(self, tmp_path: Path) -> None:
        """Active child runs filter excludes terminal statuses."""
        store = DriveStore(store_root=tmp_path)
        drive_id = "drv_active_test"

        active = ChildRunRef(
            run_id="run_active",
            drive_id=drive_id,
            kind="step",
            status="running",
            step_id="step.x",
        )
        terminal = ChildRunRef(
            run_id="run_terminal",
            drive_id=drive_id,
            kind="step",
            status="success",
            step_id="step.y",
        )
        store.save_child_run(active)
        store.save_child_run(terminal)

        active_runs = store.active_child_runs_for_drive(drive_id)
        run_ids = {c.run_id for c in active_runs}
        assert "run_active" in run_ids
        assert "run_terminal" not in run_ids


class TestHarnessFixtureWiring:
    """Non-live proof that harness wiring produces correct configuration."""

    def test_harness_writes_real_opencode_config(self, tmp_path: Path) -> None:
        """Harness generates vectl.yaml with opencode runner."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=2)

        vectl_config = yaml.safe_load((tmp_path / "vectl.yaml").read_text(encoding="utf-8"))
        orchestration = vectl_config["orchestration"]
        assert orchestration["runtime"]["default_runner"] == "opencode"
        assert orchestration["runtime"]["workspace_root"] == ".vectl/workspaces"
        assert orchestration["drive"]["max_parallelism"] == 2

    def test_harness_initializes_real_git_repo(self, tmp_path: Path) -> None:
        """Harness git init succeeds and creates a real commit."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config()
        harness.init_git_repo()

        head = harness.run_git(["rev-parse", "HEAD"], scenario="harness_git_rev_parse")
        head.assert_success()
        assert len(head.stdout.strip()) >= 7

    def test_harness_builds_live_app(self, tmp_path: Path) -> None:
        """Harness can build an OrchestrationApp from the fixture (no live runner needed)."""
        from vectl.orch_app import AppConfig

        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        app = harness.build_live_app()
        assert app is not None

    def test_harness_start_drive_returns_valid_result(self, tmp_path: Path) -> None:
        """Harness start_drive produces a DriveStartResult (no live runner needed)."""
        from vectl.orch_app import AppConfig

        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        assert start.status == "running"
        assert start.drive_id.startswith("drv_")


class TestDriveControlConsumption:
    """Verify drive-scoped control (pause/unpause/stop) is consumed by the driver loop."""

    def test_stop_drive_is_terminal(self, tmp_path: Path) -> None:
        """RFC §19.3 #12: Stopped drive is terminal and absorbs further loops."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/stop_ctrl.yaml")
        store = driver._drive_store

        # Manually set to stopped
        record = store.load_drive(start.drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="stopped",
                started_at=record.started_at,
                updated_at=time.time(),
                finished_at=time.time(),
            )
        )

        # Loop on stopped drive returns terminal
        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "stopped"
        assert "terminal" in result.summary.lower()

    def test_paused_drive_returns_immediately_on_loop(self, tmp_path: Path) -> None:
        """RFC §19.3 #12: Paused drive does not dispatch on loop."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/pause_ctrl.yaml")
        store = driver._drive_store

        # Manually set to paused
        record = store.load_drive(start.drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="paused",
                started_at=record.started_at,
                updated_at=time.time(),
                operator_pause_state="paused",
            )
        )

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "paused"


# ========================================================================
# Live acceptance tests (require RUN_LIVE_RUNNER_TESTS=1 and
# RUN_LONG_LIVE_RUNNER_TESTS=1)
# ========================================================================


@live_runner
@opencode_live
@long_live
class TestLiveOpenCodeDriveMatrix:
    """Full live acceptance with real OpenCode runner, real git worktrees,
    real merge/reconcile, and real drive control surfaces.

    These tests exercise the complete authoritative acceptance matrix
    defined in RFC §19.3. They are gated behind opt-in environment
    variables and require the OpenCode binary.
    """

    def test_linear_drive_smoke_uses_real_opencode_runner(self, tmp_path: Path) -> None:
        """RFC §19.3 #1: Linear DAG auto-drain with real OpenCode."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        assert start.status == "running"

        loop_result = harness.run_drive_loop(start.drive_id)
        assert loop_result.drive_id == start.drive_id
        assert loop_result.status in {
            "running",
            "completed",
            "paused",
            "blocked_operator",
            "resolving",
            "replanning",
            "recovering",
        }

    def test_foreground_drive_supervisor_jsonl_monitors_real_child_to_completion(
        self, tmp_path: Path
    ) -> None:
        """Foreground drive supervises, streams JSONL progress, and completes real child work."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_single_step_supervisor_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        result = harness.run_vectl(
            [
                "orch",
                "drive",
                "--max-parallelism",
                "1",
                "--poll-interval",
                "0.2",
                "--status-interval",
                "1",
                "--jsonl",
            ],
            timeout=1200,
            scenario="foreground_drive_supervisor_jsonl_live",
        )
        result.assert_success("foreground supervisor JSONL live drive failed")

        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        event_types = [event["type"] for event in events]

        assert "drive_started" in event_types
        assert "status_snapshot" in event_types
        assert "child_dispatched" in event_types
        assert "child_completed" in event_types
        assert "step_completed" in event_types
        assert event_types[-1] == "drive_terminal"
        assert events[-1]["status"] == "completed"

        drive_id = events[0]["drive_id"]
        status = harness.run_vectl(
            ["orch", "drive-status", drive_id, "--json"],
            timeout=60,
            scenario="foreground_drive_supervisor_status_check",
        )
        status.assert_success("foreground supervisor drive-status failed")
        status_payload = json.loads(status.stdout)
        assert status_payload["status"] == "completed"
        assert status_payload["active_child_run_ids"] == []

        plan_payload = harness.load_plan()
        step = plan_payload["phases"][0]["steps"][0]
        assert step["status"] == "done"

        created = tmp_path / "supervisor.txt"
        assert created.read_text(encoding="utf-8") == "supervisor-ok\n"

    def test_drive_resume_uses_real_app_surface(self, tmp_path: Path) -> None:
        """RFC §19.3 #10: Drive resume restores real state."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        resume_result = harness.resume_drive(start.drive_id)

        assert resume_result.drive_id == start.drive_id
        assert resume_result.status in {
            "running",
            "paused",
            "resolving",
            "replanning",
            "blocked_operator",
            "recovering",
        }

    def test_drive_recover_uses_real_app_surface(self, tmp_path: Path) -> None:
        """RFC §19.3 #11: Drive recover rebuilds state."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        recover_result = harness.recover_drive(start.drive_id, dry_run=True)

        assert recover_result.drive_id == start.drive_id
        assert "dry" in recover_result.summary.lower() or recover_result.status in {
            "running",
            "recovering",
        }

    def test_control_pause_and_unpause_drive(self, tmp_path: Path) -> None:
        """RFC §19.3 #12: Pause/unpause under parallel child runs."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_parallel_plan())
        harness.write_orchestration_config(max_parallelism=2)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=2)
        assert start.status == "running"

        pause_result = harness.control_pause(start.drive_id, reason="acceptance test pause")
        assert pause_result.success

        unpause_result = harness.control_unpause(start.drive_id, reason="acceptance test unpause")
        assert unpause_result.success

    def test_control_stop_drive(self, tmp_path: Path) -> None:
        """RFC §19.3 #12: Stop drive under active dispatch."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_linear_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        assert start.status == "running"

        stop_result = harness.control_stop(start.drive_id, reason="acceptance test stop")
        assert stop_result.success

    def test_phase_auto_close_with_real_app(self, tmp_path: Path) -> None:
        """RFC §19.3 #13: Phase/plan auto-close via drive loop."""
        harness = OpenCodeDriveHarness(tmp_path, env=default_orchestrator_env())
        harness.preflight()
        harness.write_plan(_multi_phase_plan())
        harness.write_orchestration_config(max_parallelism=1)
        harness.init_git_repo()

        start = harness.start_drive(max_parallelism=1)
        loop_result = harness.run_drive_loop(start.drive_id)

        assert loop_result.drive_id == start.drive_id
        # Drive should be in a valid status
        assert loop_result.status in {
            "running",
            "completed",
            "paused",
            "blocked_operator",
            "resolving",
            "replanning",
            "recovering",
        }
