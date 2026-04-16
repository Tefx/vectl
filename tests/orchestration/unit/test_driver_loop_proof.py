"""
End-to-end proof-path tests for drive driver app surface wiring.

Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15
Step: orch_drive_driver.fix-driver-loop-blockers

These tests verify that the four driver app surface methods
(start_drive, run_drive_loop, resume_drive, recover_drive) no
longer raise NotImplementedError and that they wire through to
the authoritative driver-loop runtime/control/store surfaces.

The tests exercise:
  - start_drive: creates a DriveRecord, persists it, returns result
  - run_drive_loop: evaluates control, persists updated state, returns result
  - resume_drive: restores drive state from persistence, transitions status
  - recover_drive: rebuilds drive state, reconciles child runs
  - drive_status: loads and returns current drive state
  - transition validation: enforce the canonical transition table
  - admission guard: reject duplicate active drives for same plan
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    ChildRunRef,
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import PlanAwareControl, ControlInputSources
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.driver import (
    DRIVE_TRANSITIONS,
    ConcreteDriveDriver,
    DriveAdmissionError,
    DriveLoopResult,
    DriveRecoverResult,
    DriveResumeResult,
    DriveStartResult,
    DriveStatusResult,
    InvalidDriveTransitionError,
    MaxParallelismError,
    TERMINAL_DRIVE_STATUSES,
    validate_drive_transition,
)
from vectl.orchestration.run_store import DriveStore


# ------------------------------------------------------------------
# Test helpers
# ------------------------------------------------------------------


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
        _ = agent
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
) -> ConcreteDriveDriver:
    store = DriveStore(store_root=tmp_path)
    control = _make_control(core)
    return ConcreteDriveDriver(
        drive_store=store,
        core_adapter=control.sources.core_adapter,
        control=control,
        max_parallelism=max_parallelism,
    )


# ------------------------------------------------------------------
# start_drive: app surface proof
# ------------------------------------------------------------------


class TestStartDriveAppSurface:
    """Verify start_drive wires through to the real driver loop runtime."""

    def test_start_drive_creates_persisted_drive(self, tmp_path: Path) -> None:
        """start_drive creates a DriveRecord and returns a DriveStartResult."""
        driver = _make_driver(tmp_path)

        result = driver.start_drive(
            plan_path="/repo/plan.yaml",
            agent="python-executor",
            max_parallelism=4,
        )

        assert isinstance(result, DriveStartResult)
        assert result.drive_id.startswith("drv_")
        assert result.status == "running"
        assert result.summary != ""

    def test_start_drive_persists_to_store(self, tmp_path: Path) -> None:
        """start_drive persists the DriveRecord in DriveStore."""
        driver = _make_driver(tmp_path)

        result = driver.start_drive(
            plan_path="/repo/plan.yaml",
            agent="python-executor",
        )

        store = driver._drive_store
        loaded = store.load_drive(result.drive_id)
        assert loaded is not None
        assert loaded.drive_id == result.drive_id
        assert loaded.plan_path == "/repo/plan.yaml"
        assert loaded.status == "running"
        assert loaded.max_parallelism == 4

    def test_start_drive_rejects_duplicate_active_drive(self, tmp_path: Path) -> None:
        """start_drive raises DriveAdmissionError for duplicate active drives."""
        driver = _make_driver(tmp_path)

        driver.start_drive(plan_path="/repo/plan.yaml", agent="agent1")

        with pytest.raises(DriveAdmissionError) as exc_info:
            driver.start_drive(plan_path="/repo/plan.yaml", agent="agent2")

        assert exc_info.value.active_drive_id != ""

    def test_start_drive_allows_after_terminal(self, tmp_path: Path) -> None:
        """start_drive allows a new drive after the previous one is terminal."""
        driver = _make_driver(tmp_path)

        result1 = driver.start_drive(plan_path="/repo/plan.yaml", agent="agent1")
        store = driver._drive_store

        # Mark the first drive as completed (terminal).
        record = store.load_drive(result1.drive_id)
        assert record is not None
        terminal = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="completed",
            started_at=record.started_at,
            updated_at=time.time(),
            finished_at=time.time(),
        )
        store.save_drive(terminal)

        # A new drive for the same plan should succeed.
        result2 = driver.start_drive(plan_path="/repo/plan.yaml", agent="agent2")
        assert result2.drive_id != result1.drive_id

    def test_start_drive_max_parallelism_validation(self, tmp_path: Path) -> None:
        """start_drive rejects max_parallelism outside [1, 32]."""
        driver = _make_driver(tmp_path)

        with pytest.raises(MaxParallelismError):
            driver.start_drive(plan_path="/repo/plan.yaml", max_parallelism=0)

        with pytest.raises(MaxParallelismError):
            driver.start_drive(plan_path="/repo/plan.yaml", max_parallelism=33)

    def test_start_drive_snapshots_frontier(self, tmp_path: Path) -> None:
        """start_drive reads claimable frontier from core snapshot."""
        core = _core(claimable=("step.a", "step.b"))
        driver = _make_driver(tmp_path, core=core)

        result = driver.start_drive(plan_path="/repo/plan.yaml")

        assert result.frontier_step_ids == ("step.a", "step.b")


# ------------------------------------------------------------------
# run_drive_loop: app surface proof
# ------------------------------------------------------------------


class TestRunDriveLoopAppSurface:
    """Verify run_drive_loop wires through to control evaluation."""

    def test_run_drive_loop_returns_result(self, tmp_path: Path) -> None:
        """run_drive_loop returns a DriveLoopResult (not NotImplementedError)."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.run_drive_loop(start.drive_id)

        assert isinstance(result, DriveLoopResult)
        assert result.drive_id == start.drive_id

    def test_run_drive_loop_dispatch_batch(self, tmp_path: Path) -> None:
        """run_drive_loop evaluates control and returns dispatch_batch summary."""
        core = _core(claimable=("step.a", "step.b"))
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.run_drive_loop(start.drive_id)

        assert result.status == "running"
        assert "dispatch_batch" in result.summary

    def test_run_drive_loop_done(self, tmp_path: Path) -> None:
        """run_drive_loop transitions to completed when plan is complete."""
        core = _core(plan_complete=True)
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.run_drive_loop(start.drive_id)

        assert result.status == "completed"

    def test_run_drive_loop_terminal_drive_returns_immediately(self, tmp_path: Path) -> None:
        """run_drive_loop returns immediately for terminal drives."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")
        store = driver._drive_store

        # Manually mark as completed.
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

        result = driver.run_drive_loop(start.drive_id)
        assert result.status == "completed"
        assert "terminal" in result.summary

    def test_run_drive_loop_persists_status(self, tmp_path: Path) -> None:
        """run_drive_loop persists the updated drive record."""
        core = _core(plan_complete=True)
        driver = _make_driver(tmp_path, core=core)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        driver.run_drive_loop(start.drive_id)

        loaded = driver._drive_store.load_drive(start.drive_id)
        assert loaded is not None
        assert loaded.status == "completed"

    def test_run_drive_loop_nonexistent_drive_raises(self, tmp_path: Path) -> None:
        """run_drive_loop raises for a nonexistent drive_id."""
        from vectl.orchestration.run_store import DriveStoreError

        driver = _make_driver(tmp_path)

        with pytest.raises(DriveStoreError):
            driver.run_drive_loop("drv_nonexistent")


# ------------------------------------------------------------------
# resume_drive: app surface proof
# ------------------------------------------------------------------


class TestResumeDriveAppSurface:
    """Verify resume_drive wires through to persistence restoration."""

    def test_resume_drive_returns_result(self, tmp_path: Path) -> None:
        """resume_drive returns a DriveResumeResult (not NotImplementedError)."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.resume_drive(start.drive_id)

        assert isinstance(result, DriveResumeResult)
        assert result.drive_id == start.drive_id
        assert result.status == "running"

    def test_resume_drive_restores_child_runs(self, tmp_path: Path) -> None:
        """resume_drive reads active child runs from store."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        # Seed a child run.
        child = ChildRunRef(
            run_id="run_step_01",
            drive_id=start.drive_id,
            kind="step",
            status="running",
            step_id="core.verify",
        )
        driver._drive_store.save_child_run(child)

        result = driver.resume_drive(start.drive_id)

        assert "run_step_01" in result.restored_child_run_ids

    def test_resume_drive_terminal_drive(self, tmp_path: Path) -> None:
        """resume_drive on a terminal drive returns current state."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")
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
        assert "terminal" in result.summary

    def test_resume_drive_nonexistent_drive_raises(self, tmp_path: Path) -> None:
        """resume_drive raises for a nonexistent drive_id."""
        from vectl.orchestration.run_store import DriveStoreError

        driver = _make_driver(tmp_path)

        with pytest.raises(DriveStoreError):
            driver.resume_drive("drv_nonexistent")


# ------------------------------------------------------------------
# recover_drive: app surface proof
# ------------------------------------------------------------------


class TestRecoverDriveAppSurface:
    """Verify recover_drive wires through to persistence reconciliation."""

    def test_recover_drive_returns_result(self, tmp_path: Path) -> None:
        """recover_drive returns a DriveRecoverResult (not NotImplementedError)."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.recover_drive(start.drive_id)

        assert isinstance(result, DriveRecoverResult)
        assert result.drive_id == start.drive_id

    def test_recover_drive_dry_run(self, tmp_path: Path) -> None:
        """recover_drive with dry_run=True does not change drive status."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")

        result = driver.recover_drive(start.drive_id, dry_run=True)

        # Dry run should keep current status.
        assert "dry-run" in result.summary.lower() or result.status == "running"

    def test_recover_drive_with_barrier(self, tmp_path: Path) -> None:
        """recover_drive restores barrier state and transitions correctly."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")
        store = driver._drive_store

        # Set up a drive with a barrier.
        record = store.load_drive(start.drive_id)
        assert record is not None
        barrier = DriveBarrier(
            reason="merge_conflict", entered_at=time.time(), case_ids=("case_01",)
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

        # Barrier state should be preserved.
        assert result.barrier is not None
        assert result.barrier.reason == "merge_conflict"

    def test_recover_drive_terminal_drive(self, tmp_path: Path) -> None:
        """recover_drive on a terminal drive returns immediately."""
        driver = _make_driver(tmp_path)
        start = driver.start_drive(plan_path="/repo/plan.yaml")
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

    def test_recover_drive_nonexistent_drive_raises(self, tmp_path: Path) -> None:
        """recover_drive raises for a nonexistent drive_id."""
        from vectl.orchestration.run_store import DriveStoreError

        driver = _make_driver(tmp_path)

        with pytest.raises(DriveStoreError):
            driver.recover_drive("drv_nonexistent")


# ------------------------------------------------------------------
# Transition validation
# ------------------------------------------------------------------


class TestTransitionValidation:
    """Verify drive status transition validation against RFC table."""

    def test_valid_transitions(self) -> None:
        """All transitions in DRIVE_TRANSITIONS table are valid."""
        for (from_status, to_status), reason in DRIVE_TRANSITIONS.items():
            result = validate_drive_transition(from_status, to_status)
            assert result == reason

    def test_invalid_transition_raises(self) -> None:
        """Invalid transitions raise InvalidDriveTransitionError."""
        with pytest.raises(InvalidDriveTransitionError):
            validate_drive_transition("completed", "running")

        with pytest.raises(InvalidDriveTransitionError):
            validate_drive_transition("halted", "running")

        with pytest.raises(InvalidDriveTransitionError):
            validate_drive_transition("stopped", "paused")

    def test_invalid_transition_preserves_statuses(self) -> None:
        """InvalidDriveTransitionError preserves from/to statuses."""
        try:
            validate_drive_transition("completed", "running")
        except InvalidDriveTransitionError as exc:
            assert exc.from_status == "completed"
            assert exc.to_status == "running"


# ------------------------------------------------------------------
# ConcreteDriveDriver wiring through OrchestrationApp
# ------------------------------------------------------------------


class TestOrchestrationAppDriveSurface:
    """Verify OrchestrationApp delegates to ConcreteDriveDriver."""

    def test_start_drive_app_delegation(self, tmp_path: Path) -> None:
        """OrchestrationApp.start_drive delegates to ConcreteDriveDriver."""
        from vectl.orch_app import OrchestrationApp, AppConfig

        core = _core()
        control = _make_control(core)
        core_adapter = control.sources.core_adapter

        # Build minimal app with real control.
        config = AppConfig(
            plan_path=Path("/repo/plan.yaml"),
            run_store_root=tmp_path,
        )
        # We need a minimal set of components to construct OrchestrationApp.
        # Since OrchestrationApp requires control, roster, runtime, resolver,
        # core_adapter, we use test doubles.
        from vectl.orchestration.roster import Roster
        from vectl.orchestration.runtime import Runtime

        # Use simple doubles for roster and runtime.
        class _FakeRoster:
            def snapshot(self):
                return _roster()

            def claim(self, role, isolation=None):
                return None

            def release(self, lease):
                pass

            def register(self, lease, expires_at):
                pass

        class _FakeRuntime:
            def snapshot(self):
                return _runtime()

            def prepare(self, request):
                return "workspace_fake"

            def workspace_worktree_path(self, workspace_id):
                return str(tmp_path / "ws" / workspace_id)

            def start(self, request, workspace):
                return "execution_fake"

            def collect(self, execution_id):
                return None

            def begin_reconcile(self, execution_id):
                return None

            def can_complete(self, execution_id):
                return True, "ok"

            def reconcile_disposition(self, execution_id):
                return "noop"

            def terminate_execution(self, execution_id):
                return True

            def cleanup(self, workspace):
                pass

        from vectl.orchestration.resolver import Resolver

        class _FakeResolver:
            def resolve(self, case):
                from vectl.orchestration.contracts import ResolutionReport

                return ResolutionReport(status="unblocked", summary="fake resolve")

        app = OrchestrationApp(
            config=config,
            control=control,
            roster=_FakeRoster(),
            runtime=_FakeRuntime(),
            resolver=_FakeResolver(),
            core_adapter=core_adapter,
        )

        # Ensure the test plan.yaml exists for path resolution.
        plan_path = tmp_path / "repo" / "plan.yaml"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("phases: []\n", encoding="utf-8")

        # Configure with a valid plan path.
        app_config = AppConfig(
            plan_path=plan_path,
            run_store_root=tmp_path,
        )
        app._config = app_config

        result = app.start_drive(agent="test-agent")
        assert isinstance(result, DriveStartResult)
        assert result.status == "running"

    def test_drive_status_app_delegation(self, tmp_path: Path) -> None:
        """OrchestrationApp.drive_status delegates to DriveStore."""
        from vectl.orch_app import OrchestrationApp, AppConfig

        core = _core()
        control = _make_control(core)
        core_adapter = control.sources.core_adapter

        config = AppConfig(
            plan_path=Path("/repo/plan.yaml"),
            run_store_root=tmp_path,
        )

        from vectl.orchestration.roster import Roster
        from vectl.orchestration.runtime import Runtime

        class _FakeRoster:
            def snapshot(self):
                return _roster()

            def claim(self, role, isolation=None):
                return None

            def release(self, lease):
                pass

            def register(self, lease, expires_at):
                pass

        class _FakeRuntime:
            def snapshot(self):
                return _runtime()

            def prepare(self, request):
                return "workspace_fake"

            def workspace_worktree_path(self, workspace_id):
                return str(tmp_path / "ws" / workspace_id)

            def start(self, request, workspace):
                return "execution_fake"

            def collect(self, execution_id):
                return None

            def begin_reconcile(self, execution_id):
                return None

            def can_complete(self, execution_id):
                return True, "ok"

            def reconcile_disposition(self, execution_id):
                return "noop"

            def terminate_execution(self, execution_id):
                return True

            def cleanup(self, workspace):
                pass

        class _FakeResolver:
            def resolve(self, case):
                from vectl.orchestration.contracts import ResolutionReport

                return ResolutionReport(status="unblocked", summary="fake resolve")

        app = OrchestrationApp(
            config=config,
            control=control,
            roster=_FakeRoster(),
            runtime=_FakeRuntime(),
            resolver=_FakeResolver(),
            core_adapter=core_adapter,
        )

        # Create a drive directly through the store.
        store = app._drive_store()
        record = DriveRecord(
            drive_id="drv_status_test",
            plan_path="/repo/plan.yaml",
            status="running",
            started_at=time.time(),
            updated_at=time.time(),
            max_parallelism=4,
            summary="test drive",
        )
        store.save_drive(record)

        result = app.drive_status("drv_status_test")
        assert isinstance(result, DriveStatusResult)
        assert result.drive_id == "drv_status_test"
        assert result.status == "running"


# ------------------------------------------------------------------
# Generate drive ID
# ------------------------------------------------------------------


class TestGenerateDriveId:
    """Verify drive ID generation format."""

    def test_drive_id_format(self) -> None:
        """Generated drive IDs have the drv_ prefix."""
        from vectl.orchestration.driver import _generate_drive_id

        drive_id = _generate_drive_id()
        assert drive_id.startswith("drv_")
        assert len(drive_id) > 4

    def test_drive_id_uniqueness(self) -> None:
        """Generated drive IDs are unique."""
        from vectl.orchestration.driver import _generate_drive_id

        ids = {_generate_drive_id() for _ in range(100)}
        assert len(ids) == 100
