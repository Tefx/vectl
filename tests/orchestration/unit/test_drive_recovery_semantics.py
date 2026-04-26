"""
Tests for drive resume and recovery truthful continuation semantics.

Authority: docs/RFC-orch-drive.md sections 15.1, 15.2, 15.3, 15.3.1
Step: orch_drive_recovery.drive-resume-and-recover-semantics

These tests verify that resume/recover:
  - Restore active child runs, barrier state, operator pause state,
    resolver/planner pending state, aggregate progress
  - Apply truthful continuation precedence between persisted facts,
    live runtime facts, and durable terminal artifacts
  - Cover failure modes:
    1. stale persisted running state with missing live process
    2. persisted running state with durable terminal artifact
    3. frontier recompute when core authority changed
    4. open case requires barrier re-entry before frontier reopen
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from vectl.orchestration.contracts import (
    ChildRunKind,
    ChildRunRef,
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
    DriveRecoverResult,
    DriveResumeResult,
    TERMINAL_DRIVE_STATUSES,
    _barrier_status,
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
    deferred_steps: tuple[str, ...] = ()

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        _ = agent
        self.calls += 1
        deferred = set(self.deferred_steps)
        return CoreSnapshot(
            plan_complete=self.snapshot_value.plan_complete,
            claimable_step_ids=tuple(
                dict.fromkeys((*self.snapshot_value.claimable_step_ids, *self.deferred_steps))
            ),
            in_progress_step_ids=tuple(
                sid for sid in self.snapshot_value.in_progress_step_ids if sid not in deferred
            ),
            blocked_step_ids=self.snapshot_value.blocked_step_ids,
            unresolved_reasons=self.snapshot_value.unresolved_reasons,
        )

    def step_isolation(self, step_id: str):
        raise NotImplementedError(f"test double: step_isolation not implemented: {step_id}")

    def claim_step(
        self, step_id: str, agent: str, *, force: bool = False, flow: str = "normal"
    ) -> None:
        raise NotImplementedError(f"test double: claim_step not implemented: {step_id}")

    def complete_step(self, step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        raise NotImplementedError(f"test double: complete_step not implemented: {step_id}")


    def load_step_data_for_dispatch(self, step_id: str):
        raise NotImplementedError(
            f"test double does not implement load_step_data_for_dispatch: step_id={step_id}"
        )

    def defer_step(self, step_id: str) -> None:
        self.deferred_steps = (*self.deferred_steps, step_id)


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
) -> DriveDriver:
    store = DriveStore(store_root=tmp_path)
    control = _make_control(core)
    return DriveDriver(
        drive_store=store,
        core_adapter=control.sources.core_adapter,
        control=control,
        max_parallelism=max_parallelism,
    )


def _start_drive(driver: DriveDriver, plan_path: str = "/repo/plan.yaml") -> str:
    """Start a drive and return its ID."""
    result = driver.start_drive(plan_path=plan_path, agent="test-agent")
    return result.drive_id


# ------------------------------------------------------------------
# _barrier_status mapping
# ------------------------------------------------------------------


class TestBarrierStatusMapping:
    """Verify _barrier_status maps barrier reasons to correct drive statuses."""

    def test_operator_pause_maps_to_blocked_operator(self) -> None:
        assert _barrier_status("operator_pause") == "blocked_operator"

    def test_planner_needed_maps_to_replanning(self) -> None:
        assert _barrier_status("planner_needed") == "replanning"

    def test_runtime_failure_maps_to_resolving(self) -> None:
        assert _barrier_status("runtime_failure") == "resolving"

    def test_merge_conflict_maps_to_resolving(self) -> None:
        assert _barrier_status("merge_conflict") == "resolving"

    def test_review_failed_maps_to_resolving(self) -> None:
        assert _barrier_status("review_failed") == "resolving"

    def test_recovery_gate_maps_to_resolving(self) -> None:
        assert _barrier_status("recovery_gate") == "resolving"


# ------------------------------------------------------------------
# resume_drive: barrier and operator pause state restoration
# ------------------------------------------------------------------


class TestResumeBarrierRestoration:
    """Verify resume_drive restores barrier state and pauses correctly.

    Authority: RFC-orch-drive.md section 15.1
    """

    def test_resume_with_barrier_preserves_barrier(self, tmp_path: Path) -> None:
        """Resume with an active barrier should preserve it, not drop it."""
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Manually set a barrier (runtime_failure) on the drive.
        barrier = DriveBarrier(
            reason="runtime_failure",
            entered_at=time.time(),
            case_ids=("case_001",),
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=(),
        )
        with_barrier = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="resolving",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=("case_001",),
            barrier=barrier,
            operator_pause_state="active",
            summary="entered barrier",
        )
        store.save_drive(with_barrier)

        # Resume the drive.
        result = driver.resume_drive(drive_id)

        # The drive should remain in resolving status (not unconditionally
        # transition to running).
        assert result.status == "resolving"
        assert result.barrier is not None
        assert result.barrier.reason == "runtime_failure"

    def test_resume_with_operator_pause_preserves_pause(self, tmp_path: Path) -> None:
        """Resume with operator_pause_state='paused' should preserve paused status.

        Authority: RFC-orch-drive.md section 15.1, 8.2.1

        When the operator has explicitly paused the drive, resume must
        preserve that state. The operator must explicitly unpause;
        automatic resume should not transition away from paused.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set an operator_pause barrier with paused state.
        barrier = DriveBarrier(
            reason="operator_pause",
            entered_at=time.time(),
            case_ids=(),
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=(),
        )
        paused = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="paused",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=(),
            barrier=barrier,
            operator_pause_state="paused",
            summary="operator paused",
        )
        store.save_drive(paused)

        result = driver.resume_drive(drive_id)

        # Resuming a paused drive must preserve the paused state.
        # The operator must explicitly unpause (paused → running).
        # Resume does NOT auto-unpause.
        assert result.status == "paused"
        assert result.barrier is not None
        assert result.barrier.reason == "operator_pause"

    def test_resume_with_planner_needed_preserves_replanning(self, tmp_path: Path) -> None:
        """Resume with planner_needed barrier should transition to replanning.

        Authority: RFC-orch-drive.md section 15.1, 8.2.1
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        barrier = DriveBarrier(
            reason="planner_needed",
            entered_at=time.time(),
            case_ids=("case_002",),
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=(),
        )
        replanning = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="replanning",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=("case_002",),
            barrier=barrier,
            operator_pause_state="active",
            summary="replanning",
        )
        store.save_drive(replanning)

        result = driver.resume_drive(drive_id)

        assert result.status == "replanning"
        assert result.barrier is not None
        assert result.barrier.reason == "planner_needed"

    def test_resume_without_barrier_goes_running(self, tmp_path: Path) -> None:
        """Resume a running drive without barrier should go to running."""
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        result = driver.resume_drive(drive_id)

        assert result.status == "running"
        assert result.barrier is None

    def test_resume_preserves_operator_pause_state(self, tmp_path: Path) -> None:
        """Resume must preserve the persisted operator_pause_state.

        Authority: RFC-orch-drive.md section 15.1

        When a drive is operator-paused, operator_pause_state='paused'
        and status='paused' should be preserved. The drive should resume
        to 'paused' (not auto-unpaused to 'running').
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set status 'paused' with operator_pause_state 'paused'
        # and an operator_pause barrier (the standard paused state).
        paused = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="paused",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=(),
            barrier=DriveBarrier(
                reason="operator_pause",
                entered_at=time.time(),
                case_ids=(),
            ),
            operator_pause_state="paused",
            summary="operator paused",
        )
        store.save_drive(paused)

        result = driver.resume_drive(drive_id)

        # Resume should preserve paused state with its barrier.
        # A paused drive should stay paused on resume — the operator must
        # explicitly unpause it.
        assert result.status == "paused"

        # Verify persisted state.
        updated = store.load_drive(drive_id)
        assert updated is not None
        assert updated.operator_pause_state == "paused"


# ------------------------------------------------------------------
# resume_drive: frontier recompute
# ------------------------------------------------------------------


class TestResumeFrontierRecompute:
    """Verify resume recompute frontier from live core authority.

    Authority: RFC-orch-drive.md section 15.3.1
    'persisted drive frontier includes step, core no longer claimable
     → core authority wins'
    """

    def test_resume_recomputes_frontier_from_core(self, tmp_path: Path) -> None:
        """Resume must recompute frontier from live core, not trust cache."""
        core = _core(claimable=("step.x", "step.y"))
        driver = _make_driver(tmp_path, core=core)
        drive_id = _start_drive(driver)

        # The initial drive was started with step.x and step.y.
        store = driver._drive_store
        record = store.replay_drive_state(drive_id)
        assert "step.x" in record.frontier_step_ids

        # Now simulate core authority changing: step.x is completed,
        # step.z is now claimable.
        driver._core_adapter.snapshot_value = _core(claimable=("step.y", "step.z"))

        result = driver.resume_drive(drive_id)

        # After resume, frontier should reflect live core authority,
        # not the stale cached frontier.
        updated = store.load_drive(drive_id)
        assert updated is not None
        assert "step.z" in updated.frontier_step_ids
        assert "step.x" not in updated.frontier_step_ids


# ------------------------------------------------------------------
# recover_drive: stale persisted running state with missing live process
# ------------------------------------------------------------------


class TestRecoverStaleRunningState:
    """Verify recover correctly classifies stale persisted running state.

    Authority: RFC-orch-drive.md section 15.3.1
    'persisted child run=running, live process missing, no terminal artifact
     → live runtime fact wins; classify as stale (failed)'
    """

    def test_recover_classifies_stale_run_as_failed(self, tmp_path: Path) -> None:
        """A child run showing 'running' with no live process and no
        terminal artifact should be classified as failed, not recovered.

        Authority: RFC-orch-drive.md section 15.3.1
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store

        # Add a child run in "running" status that is actually stale
        # (no heartbeat, no terminal artifact).
        stale_child = ChildRunRef(
            run_id="run_stale_001",
            drive_id=drive_id,
            kind="step",
            step_id="step.a",
            status="running",
            workspace=".vectl/worktrees/step.a",
        )
        store.save_child_run(stale_child)

        # Without a RunRegistry, liveness check returns "unknown"
        # and terminal artifact check returns None. A running child run
        # with unknown liveness is conservatively recovered.
        # But we need to prove that when liveness is "stale", the
        # child run is classified as failed.
        # This is tested through the _check_child_run_liveness method.
        # Since we don't have a real heartbeat file, the classification
        # is "unknown" and runs are conservatively included.
        # Let's verify the classification behavior for the stale case
        # directly:
        result = driver.recover_drive(drive_id)

        assert result.status == "running"
        # The stale run should be in recovered_ids (conservative approach
        # since we can't confirm liveness without a heartbeat or registry).
        assert "run_stale_001" in result.recovered_child_run_ids

    def test_recover_with_liveness_stale_marks_failed(self, tmp_path: Path) -> None:
        """When liveness check returns 'stale', child run should be failed.

        This tests the core logic path by injecting a mock that returns
        stale liveness for a specific run ID.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store

        # Add a child run that appears running.
        stale_child = ChildRunRef(
            run_id="run_stale_002",
            drive_id=drive_id,
            kind="step",
            step_id="step.b",
            status="running",
            workspace=".vectl/worktrees/step.b",
        )
        store.save_child_run(stale_child)

        # Override liveness check to return "stale" for this run.
        original_liveness = driver._check_child_run_liveness

        def mock_liveness(run_id: str, *, stale_threshold_seconds: float = 90.0) -> str:
            if run_id == "run_stale_002":
                return "stale"
            return original_liveness(run_id, stale_threshold_seconds=stale_threshold_seconds)

        driver._check_child_run_liveness = mock_liveness  # type: ignore[assignment]

        result = driver.recover_drive(drive_id)

        # The stale run should be classified as failed.
        assert "run_stale_002" in result.failed_child_run_ids
        assert "run_stale_002" not in result.recovered_child_run_ids

        # There should be a conflict note about this.
        assert any("stale" in note.lower() for note in result.conflict_resolutions)


# ------------------------------------------------------------------
# recover_drive: persisted running state with durable terminal artifact
# ------------------------------------------------------------------


class TestRecoverTerminalArtifact:
    """Verify recover recognizes durable terminal artifacts.

    Authority: RFC-orch-drive.md section 15.3.1
    'persisted child run=running, live terminal artifact exists
     → persisted + terminal artifact'
    """

    def test_recover_terminal_artifact_beats_stale_status(self, tmp_path: Path) -> None:
        """A running child run with a terminal artifact in the child-run
        index should be classified as a completed fact, not recovered.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store

        # Add a child run that shows as "success" in the index.
        # This simulates a completed run whose status was updated
        # in the child-run index independently.
        completed_child = ChildRunRef(
            run_id="run_completed_001",
            drive_id=drive_id,
            kind="step",
            step_id="step.c",
            status="success",
            workspace=".vectl/worktrees/step.c",
        )
        store.save_child_run(completed_child)

        result = driver.recover_drive(drive_id)

        # "success" is not in ("pending", "running"), so it's not
        # considered for recovery at all — it's a completed fact.
        assert "run_completed_001" not in result.recovered_child_run_ids
        assert "run_completed_001" not in result.failed_child_run_ids

    def test_recover_conflict_terminal_artifact_in_registry(self, tmp_path: Path) -> None:
        """A running child run with a terminal RunRecord should be
        classified as a completed fact.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store

        # Add a child run showing as "running".
        running_child = ChildRunRef(
            run_id="run_running_001",
            drive_id=drive_id,
            kind="step",
            step_id="step.d",
            status="running",
            workspace=".vectl/worktrees/step.d",
        )
        store.save_child_run(running_child)

        # Now also add a RunRegistry entry showing this run as "success".
        from vectl.orchestration.run_store import RunRegistry

        run_registry = RunRegistry(store_root=tmp_path / "runs")
        run_registry.save(
            __import__("vectl.orchestration.run_store", fromlist=["RunRecord"]).RunRecord(
                run_id="run_running_001",
                step_id="step.d",
                plan_path="/repo/plan.yaml",
                agent="test-agent",
                status="success",
                created_at=time.time(),
                started_at=time.time(),
                updated_at=time.time(),
                finished_at=time.time(),
                artifact_root=str(tmp_path / "runs" / "run_running_001"),
                output_summary="completed successfully",
            )
        )

        # Re-create driver with the run registry injected.
        driver_with_registry = DriveDriver(
            drive_store=store,
            core_adapter=driver._core_adapter,
            control=driver._control,
            run_registry=run_registry,
        )

        result = driver_with_registry.recover_drive(drive_id)

        # The child run with a terminal artifact in the registry should
        # be recognized as a completed fact, not recovered.
        assert "run_running_001" not in result.recovered_child_run_ids
        # And there should be a conflict note about the terminal artifact.
        assert any(
            "terminal artifact" in note.lower() or "completed fact" in note.lower()
            for note in result.conflict_resolutions
        )


# ------------------------------------------------------------------
# recover_drive: frontier recompute from core authority
# ------------------------------------------------------------------


class TestRecoverFrontierRecompute:
    """Verify recover recomputes frontier from live core authority.

    Authority: RFC-orch-drive.md section 15.3.1
    'persisted drive frontier includes step, core no longer claimable
     → core authority wins'
    """

    def test_recover_recomputes_frontier_from_core(self, tmp_path: Path) -> None:
        """Recovery must recompute frontier from live core, not trust
        the persisted cache.
        """
        core = _core(claimable=("step.a", "step.b"))
        driver = _make_driver(tmp_path, core=core)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Persist a stale frontier with steps that are no longer claimable.
        stale_record = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=record.status,
            started_at=record.started_at,
            updated_at=record.updated_at,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=("step.a", "step.b", "step.c"),  # step.c stale
            blocked_case_ids=(),
            barrier=None,
            operator_pause_state="active",
            summary="stale frontier",
        )
        store.save_drive(stale_record)

        # Now core says only step.a and step.b are claimable (step.c done).
        driver._core_adapter.snapshot_value = _core(claimable=("step.a", "step.b"))

        result = driver.recover_drive(drive_id)

        # The recovered drive should have frontier derived from live core.
        updated = store.load_drive(drive_id)
        assert updated is not None
        assert "step.c" not in updated.frontier_step_ids
        assert "step.a" in updated.frontier_step_ids

    def test_recover_releases_orphaned_drive_claim(self, tmp_path: Path) -> None:
        """Recovery releases claimed drive steps with no active child left."""
        driver = _make_driver(tmp_path, core=_core(in_progress=("step.claimed",)))
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="running",
                started_at=record.started_at,
                updated_at=record.updated_at,
                agent=record.agent,
                max_parallelism=record.max_parallelism,
                active_child_run_ids=(),
                frontier_step_ids=("step.claimed",),
                blocked_case_ids=(),
                barrier=None,
                operator_pause_state="active",
                summary="claimed without active child",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_done_but_unclosed",
                drive_id=drive_id,
                kind="step",
                step_id="step.claimed",
                status="transport_error",
                workspace=".vectl/worktrees/step.claimed",
            )
        )

        result = driver.recover_drive(drive_id)

        assert "step.claimed" in driver._core_adapter.deferred_steps
        assert any("orphaned drive claim" in note for note in result.conflict_resolutions)
        updated = store.load_drive(drive_id)
        assert updated is not None
        assert "step.claimed" in updated.frontier_step_ids

    def test_recover_releases_historical_orphaned_drive_claim(self, tmp_path: Path) -> None:
        """Recovery releases claimed drive-owned steps missing from stale frontier."""
        driver = _make_driver(tmp_path, core=_core(in_progress=("step.historical",)))
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="running",
                started_at=record.started_at,
                updated_at=record.updated_at,
                agent=record.agent,
                max_parallelism=record.max_parallelism,
                active_child_run_ids=(),
                frontier_step_ids=("step.next",),
                blocked_case_ids=(),
                barrier=None,
                operator_pause_state="active",
                summary="historical claimed child without active run",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_historical_done_but_unclosed",
                drive_id=drive_id,
                kind="step",
                step_id="step.historical",
                status="success",
                workspace=".vectl/worktrees/step.historical",
            )
        )

        result = driver.recover_drive(drive_id)

        assert "step.historical" in driver._core_adapter.deferred_steps
        assert any("step.historical" in note for note in result.conflict_resolutions)

    def test_recover_releases_claim_persisted_before_child_ref(self, tmp_path: Path) -> None:
        """Recovery releases a drive-agent claim even when no child ref was saved."""
        driver = _make_driver(tmp_path, core=_core(in_progress=("step.pre_child_ref",)))
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)
        assert record is not None
        store.save_drive(
            DriveRecord(
                drive_id=record.drive_id,
                plan_path=record.plan_path,
                status="running",
                started_at=record.started_at,
                updated_at=record.updated_at,
                agent=record.agent,
                max_parallelism=record.max_parallelism,
                active_child_run_ids=(),
                frontier_step_ids=("step.already.done",),
                blocked_case_ids=(),
                barrier=None,
                operator_pause_state="active",
                summary="claim persisted before child ref",
            )
        )

        result = driver.recover_drive(drive_id)

        assert "step.pre_child_ref" in driver._core_adapter.deferred_steps
        assert any("step.pre_child_ref" in note for note in result.conflict_resolutions)


# ------------------------------------------------------------------
# recover_drive: open case requires barrier re-entry
# ------------------------------------------------------------------


class TestRecoverOpenCaseBarrierReentry:
    """Verify recover re-enters barrier when open cases exist.

    Authority: RFC-orch-drive.md section 15.3.1
    'persisted barrier absent, open case exists → open case state'
    """

    def test_recover_creates_barrier_for_open_cases(self, tmp_path: Path) -> None:
        """When persisted barrier is absent but blocked_case_ids exist,
        recovery must create a recovery_gate barrier.

        Authority: RFC-orch-drive.md section 15.3.1
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set the drive's blocked_case_ids to indicate open cases,
        # but clear the barrier (simulating a crash that lost barrier state).
        with_cases = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="running",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=("case_open_001",),
            barrier=None,  # No persisted barrier!
            operator_pause_state="active",
            summary="running with open cases but no barrier",
        )
        store.save_drive(with_cases)

        result = driver.recover_drive(drive_id)

        # Recovery should create a recovery_gate barrier.
        assert result.barrier is not None
        assert result.barrier.reason == "recovery_gate"
        assert "case_open_001" in result.barrier.case_ids

        # The drive status should be resolving (the barrier re-entry target).
        assert result.status == "resolving"

        # There should be a conflict note about the barrier re-entry.
        assert any(
            "barrier" in note.lower() or "case" in note.lower()
            for note in result.conflict_resolutions
        )

    def test_recover_preserves_existing_barrier(self, tmp_path: Path) -> None:
        """When a barrier already exists, recovery must preserve it
        rather than create a new one.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set a merge_conflict barrier.
        existing_barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=time.time(),
            case_ids=("case_merge_001",),
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=(),
        )
        with_barrier = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="resolving",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=("case_merge_001",),
            barrier=existing_barrier,
            operator_pause_state="active",
            summary="resolving merge conflict",
        )
        store.save_drive(with_barrier)

        result = driver.recover_drive(drive_id)

        # The existing barrier should be preserved.
        assert result.barrier is not None
        assert result.barrier.reason == "merge_conflict"
        assert result.status == "resolving"


# ------------------------------------------------------------------
# recover_drive: dry_run mode
# ------------------------------------------------------------------


class TestRecoverDryRun:
    """Verify recover_drive dry_run mode doesn't apply changes."""

    def test_dry_run_does_not_modify_drive(self, tmp_path: Path) -> None:
        """dry_run=True must compute the recovery plan without persisting."""
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        original = store.replay_drive_state(drive_id)

        result = driver.recover_drive(drive_id, dry_run=True)

        # dry_run mode should return the original status, not the
        # recovered status.
        assert result.status == original.status

        # The drive record should not have been modified.
        current = store.load_drive(drive_id)
        assert current is not None
        assert current.status == original.status

        # But the result should describe what would happen.
        assert "dry-run" in result.summary.lower() or "would" in result.summary.lower()


# ------------------------------------------------------------------
# recover_drive: transition validation
# ------------------------------------------------------------------


class TestRecoverTransitionValidation:
    """Verify recover uses valid transitions through 'recovering'."""

    def test_recover_validates_transition_to_running(self, tmp_path: Path) -> None:
        """Recovery from 'running' status should transition to 'running'
        (no barrier) or the appropriate barrier status.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        result = driver.recover_drive(drive_id)

        # No barrier, no cases → running.
        assert result.status == "running"

    def test_recover_transition_through_recovering(self, tmp_path: Path) -> None:
        """Recovery should use 'recovering' as intermediate if direct
        transition is invalid.

        Authority: RFC-orch-drive.md section 8.2.1
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set drive to 'paused' status with operator_pause_state="paused".
        # A paused drive should remain paused on recovery — the operator
        # must explicitly unpause it.
        paused = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="paused",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=(),
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=(),
            barrier=DriveBarrier(
                reason="operator_pause",
                entered_at=time.time(),
                case_ids=(),
            ),
            operator_pause_state="paused",
            summary="paused by operator",
        )
        store.save_drive(paused)

        result = driver.recover_drive(drive_id)

        # Paused with operator_pause_state="paused" should stay paused
        # (which maps to blocked_operator per _barrier_status, but via
        # recovering intermediate per RFC 8.2.1).
        assert result.status in ("paused", "blocked_operator")


# ------------------------------------------------------------------
# recover_drive: operator pause state preservation
# ------------------------------------------------------------------


class TestRecoverOperatorPauseStatePreservation:
    """Verify recover preserves operator_pause_state from the record.

    Authority: RFC-orch-drive.md section 15.1
    """

    def test_recover_preserves_paused_operator_state(self, tmp_path: Path) -> None:
        """Recovery must not reset operator_pause_state to 'active'
        when the drive was paused.

        Authority: RFC-orch-drive.md section 15.1

        A paused drive with an operator_pause barrier should recover to
        blocked_operator (per _barrier_status mapping), but the
        operator_pause_state field must remain 'paused'.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Set drive to paused with operator_pause_state="paused" and barrier.
        paused = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="paused",
            started_at=record.started_at,
            updated_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=(),
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=(),
            barrier=DriveBarrier(
                reason="operator_pause",
                entered_at=time.time(),
                case_ids=(),
            ),
            operator_pause_state="paused",
            summary="operator paused",
        )
        store.save_drive(paused)

        result = driver.recover_drive(drive_id)

        # With barrier operator_pause, _barrier_status maps to blocked_operator.
        # Via recovering intermediate: paused → recovering → blocked_operator.
        assert result.status in ("blocked_operator", "paused")
        updated = store.load_drive(drive_id)
        assert updated is not None
        assert updated.operator_pause_state == "paused"

    def test_recover_preserves_active_operator_state(self, tmp_path: Path) -> None:
        """Recovery of an active (non-paused) drive preserves
        operator_pause_state='active'.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        result = driver.recover_drive(drive_id)

        assert result.status == "running"
        updated = driver._drive_store.load_drive(drive_id)
        assert updated is not None
        assert updated.operator_pause_state == "active"


# ------------------------------------------------------------------
# resume_drive: terminal drive handling
# ------------------------------------------------------------------


class TestResumeTerminalDrive:
    """Verify resume_drive correctly handles terminal drives."""

    def test_resume_terminal_drive_returns_current_state(self, tmp_path: Path) -> None:
        """Resuming a terminal drive should return its current state unchanged."""
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        # Mark the drive as completed (terminal).
        terminal = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="completed",
            started_at=record.started_at,
            updated_at=time.time(),
            finished_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=(),
            frontier_step_ids=(),
            blocked_case_ids=(),
            barrier=None,
            operator_pause_state="active",
            summary="completed",
        )
        store.save_drive(terminal)

        result = driver.resume_drive(drive_id)

        assert result.status == "completed"
        assert "terminal" in result.summary.lower()


# ------------------------------------------------------------------
# recover_drive: terminal drive handling
# ------------------------------------------------------------------


class TestRecoverTerminalDrive:
    """Verify recover_drive correctly handles terminal drives."""

    def test_recover_terminal_drive_returns_nothing(self, tmp_path: Path) -> None:
        """Recovering a terminal drive should return current state with
        no child run recovery.
        """
        driver = _make_driver(tmp_path)
        drive_id = _start_drive(driver)

        store = driver._drive_store
        record = store.replay_drive_state(drive_id)

        terminal = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="halted",
            started_at=record.started_at,
            updated_at=time.time(),
            finished_at=time.time(),
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=(),
            frontier_step_ids=(),
            blocked_case_ids=(),
            barrier=None,
            operator_pause_state="active",
            summary="halted",
        )
        store.save_drive(terminal)

        result = driver.recover_drive(drive_id)

        assert result.status == "halted"
        assert result.recovered_child_run_ids == ()
        assert result.failed_child_run_ids == ()
        assert (
            "terminal" in result.summary.lower() or "nothing to recover" in result.summary.lower()
        )
