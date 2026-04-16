"""
Durable persistence tests for DriveRecord, ChildRunRef, and DriveStore.

These tests verify:
  - DriveRecord round-trip: save → load → field equality
  - ChildRunRef round-trip: save → query → field equality
  - Active-drive lookup ignores terminal history only
  - Child-run persistence survives restart/reload (simulated by
    re-reading the store from disk)
  - Projection replay rebuilds active_child_run_ids and frontier
    facts truthfully
  - Drive admission guard prevents conflicting active drives

Authority: docs/RFC-orch-drive.md sections 8, 9, 10, 14
Step: orch_drive_state_store.run-store-drive-records
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from vectl.orchestration.contracts import (
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
)
from vectl.orchestration.projections import DriveProjection, rebuild_drive_projection
from vectl.orchestration.run_store import (
    DriveAdmissionConflictError,
    DriveStore,
    DriveStoreError,
    TERMINAL_DRIVE_STATUSES,
)


# ------------------------------------------------------------------
# DriveRecord round-trip
# ------------------------------------------------------------------


class TestDriveRecordRoundTrip:
    """Verify DriveRecord JSONL persistence and retrieval."""

    def test_save_and_load_minimal_drive(self, tmp_path: Path) -> None:
        """Minimal DriveRecord (required fields only) round-trips."""
        store = DriveStore(store_root=tmp_path)
        record = DriveRecord(drive_id="drv_01", plan_path="/repo/plan.yaml")
        store.save_drive(record)

        loaded = store.load_drive("drv_01")
        assert loaded is not None
        assert loaded.drive_id == "drv_01"
        assert loaded.plan_path == "/repo/plan.yaml"
        assert loaded.status == "running"

    def test_save_and_load_full_drive(self, tmp_path: Path) -> None:
        """Full DriveRecord with all fields round-trips."""
        store = DriveStore(store_root=tmp_path)
        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=1776124820.0,
            case_ids=("case_01M",),
            pending_resolver_run_id="run_resolve_01",
        )
        record = DriveRecord(
            drive_id="drv_01K",
            plan_path="/repo/plan.yaml",
            status="resolving",
            started_at=1776124800.0,
            updated_at=1776124812.0,
            finished_at=None,
            agent="python-executor",
            max_parallelism=4,
            active_child_run_ids=("run_01A", "run_01B"),
            frontier_step_ids=("core.verify", "core.snapshot"),
            blocked_case_ids=(),
            barrier=barrier,
            operator_pause_state="active",
            summary="2 child runs active; frontier width=2",
        )
        store.save_drive(record)

        loaded = store.load_drive("drv_01K")
        assert loaded is not None
        assert loaded.drive_id == "drv_01K"
        assert loaded.status == "resolving"
        assert loaded.barrier is not None
        assert loaded.barrier.reason == "merge_conflict"
        assert loaded.barrier.case_ids == ("case_01M",)
        assert loaded.active_child_run_ids == ("run_01A", "run_01B")
        assert loaded.frontier_step_ids == ("core.verify", "core.snapshot")

    def test_save_update_latest_wins(self, tmp_path: Path) -> None:
        """Multiple saves of same drive_id: latest record wins."""
        store = DriveStore(store_root=tmp_path)
        record_v1 = DriveRecord(
            drive_id="drv_01", plan_path="/p", status="running", updated_at=100.0
        )
        store.save_drive(record_v1)

        record_v2 = DriveRecord(
            drive_id="drv_01",
            plan_path="/p",
            status="completed",
            updated_at=200.0,
            finished_at=200.0,
        )
        store.save_drive(record_v2)

        loaded = store.load_drive("drv_01")
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.finished_at == 200.0

    def test_auto_timestamp_on_zero_updated_at(self, tmp_path: Path) -> None:
        """Save normalizes zero updated_at to current timestamp."""
        store = DriveStore(store_root=tmp_path)
        record = DriveRecord(drive_id="drv_ts", plan_path="/p", updated_at=0.0)
        store.save_drive(record)

        loaded = store.load_drive("drv_ts")
        assert loaded is not None
        assert loaded.updated_at > 0.0

    def test_barrier_none_round_trips(self, tmp_path: Path) -> None:
        """DriveRecord with barrier=None persists and loads correctly."""
        store = DriveStore(store_root=tmp_path)
        record = DriveRecord(drive_id="drv_nb", plan_path="/p", barrier=None)
        store.save_drive(record)

        loaded = store.load_drive("drv_nb")
        assert loaded is not None
        assert loaded.barrier is None

    def test_terminal_status_values_round_trip(self, tmp_path: Path) -> None:
        """Each terminal status value can be persisted and retrieved."""
        store = DriveStore(store_root=tmp_path)
        for status in ("completed", "halted", "failed_unrecoverable", "stopped"):
            record = DriveRecord(drive_id=f"drv_{status}", plan_path="/p", status=status)
            store.save_drive(record)
            loaded = store.load_drive(f"drv_{status}")
            assert loaded is not None
            assert loaded.status == status


# ------------------------------------------------------------------
# ChildRunRef persistence
# ------------------------------------------------------------------


class TestChildRunRefRoundTrip:
    """Verify ChildRunRef JSONL persistence and retrieval."""

    def test_save_and_load_step_child_run(self, tmp_path: Path) -> None:
        """Step child run with step_id round-trips."""
        store = DriveStore(store_root=tmp_path)
        ref = ChildRunRef(
            run_id="run_01A",
            drive_id="drv_01",
            kind="step",
            status="running",
            step_id="core.verify",
            workspace=".vectl/workspaces/core.verify",
            runner="opencode",
        )
        store.save_child_run(ref)

        loaded = store.child_run_by_id("run_01A")
        assert loaded is not None
        assert loaded.run_id == "run_01A"
        assert loaded.drive_id == "drv_01"
        assert loaded.kind == "step"
        assert loaded.status == "running"
        assert loaded.step_id == "core.verify"

    def test_save_and_load_resolver_child_run(self, tmp_path: Path) -> None:
        """Resolver child run with case_id round-trips."""
        store = DriveStore(store_root=tmp_path)
        ref = ChildRunRef(
            run_id="run_resolve_01",
            drive_id="drv_01",
            kind="resolver",
            status="running",
            case_id="case_01M",
        )
        store.save_child_run(ref)

        loaded = store.child_run_by_id("run_resolve_01")
        assert loaded is not None
        assert loaded.kind == "resolver"
        assert loaded.case_id == "case_01M"

    def test_save_and_load_planner_child_run(self, tmp_path: Path) -> None:
        """Planner child run with planner_request_id round-trips."""
        store = DriveStore(store_root=tmp_path)
        ref = ChildRunRef(
            run_id="run_plan_01",
            drive_id="drv_01",
            kind="planner",
            status="pending",
            planner_request_id="pr_01",
        )
        store.save_child_run(ref)

        loaded = store.child_run_by_id("run_plan_01")
        assert loaded is not None
        assert loaded.kind == "planner"
        assert loaded.planner_request_id == "pr_01"

    def test_child_runs_for_drive(self, tmp_path: Path) -> None:
        """Multiple child runs for a drive are all returned."""
        store = DriveStore(store_root=tmp_path)
        for i, kind in enumerate(("step", "step", "resolver")):
            ref = ChildRunRef(
                run_id=f"run_{i:03d}",
                drive_id="drv_01",
                kind=kind,
                status="running",
                step_id=f"core.step{i}" if kind == "step" else None,
                case_id="case_01" if kind == "resolver" else None,
            )
            store.save_child_run(ref)

        refs = store.child_runs_for_drive("drv_01")
        assert len(refs) == 3

    def test_child_runs_for_step(self, tmp_path: Path) -> None:
        """Step-indexed lookup returns only matching step within a drive."""
        store = DriveStore(store_root=tmp_path)
        store.save_child_run(
            ChildRunRef(
                run_id="run_a",
                drive_id="drv_01",
                kind="step",
                step_id="core.verify",
                status="running",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_b",
                drive_id="drv_01",
                kind="step",
                step_id="core.impl",
                status="pending",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_c",
                drive_id="drv_01",
                kind="resolver",
                case_id="case_01",
                status="running",
            )
        )

        verify_refs = store.child_runs_for_step("drv_01", "core.verify")
        assert len(verify_refs) == 1
        assert verify_refs[0].step_id == "core.verify"

        impl_refs = store.child_runs_for_step("drv_01", "core.impl")
        assert len(impl_refs) == 1

    def test_child_runs_by_kind(self, tmp_path: Path) -> None:
        """Kind-filtered child run lookup works correctly."""
        store = DriveStore(store_root=tmp_path)
        store.save_child_run(ChildRunRef(run_id="s1", drive_id="drv_01", kind="step", step_id="a"))
        store.save_child_run(
            ChildRunRef(run_id="r1", drive_id="drv_01", kind="resolver", case_id="c1")
        )
        store.save_child_run(
            ChildRunRef(run_id="p1", drive_id="drv_01", kind="planner", planner_request_id="pr1")
        )

        steps = store.child_runs_by_kind("drv_01", "step")
        assert len(steps) == 1
        assert steps[0].run_id == "s1"

        resolvers = store.child_runs_by_kind("drv_01", "resolver")
        assert len(resolvers) == 1

        planners = store.child_runs_by_kind("drv_01", "planner")
        assert len(planners) == 1

    def test_child_run_update_latest_wins(self, tmp_path: Path) -> None:
        """Later child run entry for same run_id supersedes prior."""
        store = DriveStore(store_root=tmp_path)
        store.save_child_run(
            ChildRunRef(run_id="run_01", drive_id="drv_01", kind="step", status="pending")
        )
        store.save_child_run(
            ChildRunRef(run_id="run_01", drive_id="drv_01", kind="step", status="running")
        )

        loaded = store.child_run_by_id("run_01")
        assert loaded is not None
        assert loaded.status == "running"

    def test_empty_store_returns_none(self, tmp_path: Path) -> None:
        """Lookup on empty store returns None."""
        store = DriveStore(store_root=tmp_path)
        assert store.load_drive("nonexistent") is None
        assert store.child_run_by_id("nonexistent") is None

    def test_cross_drive_isolation(self, tmp_path: Path) -> None:
        """Child runs for one drive don't leak into another."""
        store = DriveStore(store_root=tmp_path)
        store.save_child_run(
            ChildRunRef(run_id="run_d1", drive_id="drv_01", kind="step", step_id="a")
        )
        store.save_child_run(
            ChildRunRef(run_id="run_d2", drive_id="drv_02", kind="step", step_id="b")
        )

        d1_refs = store.child_runs_for_drive("drv_01")
        assert len(d1_refs) == 1
        assert d1_refs[0].run_id == "run_d1"

        d2_refs = store.child_runs_for_drive("drv_02")
        assert len(d2_refs) == 1
        assert d2_refs[0].run_id == "run_d2"


# ------------------------------------------------------------------
# Active vs terminal drive lookup
# ------------------------------------------------------------------


class TestActiveDriveLookup:
    """Verify that active drive lookup correctly excludes terminal drives."""

    def test_active_drives_excludes_terminal(self, tmp_path: Path) -> None:
        """Terminal drives are excluded from active_drives()."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_running", plan_path="/p1", status="running", updated_at=1.0)
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_completed", plan_path="/p2", status="completed", updated_at=2.0
            )
        )
        store.save_drive(
            DriveRecord(drive_id="drv_halted", plan_path="/p3", status="halted", updated_at=3.0)
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_failed",
                plan_path="/p4",
                status="failed_unrecoverable",
                updated_at=4.0,
            )
        )
        store.save_drive(
            DriveRecord(drive_id="drv_stopped", plan_path="/p5", status="stopped", updated_at=5.0)
        )

        active = store.active_drives()
        active_ids = {d.drive_id for d in active}
        assert active_ids == {"drv_running"}
        for status in TERMINAL_DRIVE_STATUSES:
            assert status not in {d.status for d in active}

    def test_terminal_drives_returns_completed_ones(self, tmp_path: Path) -> None:
        """terminal_drives() returns only terminal statuses."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_running", plan_path="/p1", status="running", updated_at=1.0)
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_completed", plan_path="/p2", status="completed", updated_at=2.0
            )
        )
        store.save_drive(
            DriveRecord(drive_id="drv_halted", plan_path="/p3", status="halted", updated_at=3.0)
        )

        terminal = store.terminal_drives()
        terminal_ids = {d.drive_id for d in terminal}
        assert "drv_running" not in terminal_ids
        assert "drv_completed" in terminal_ids
        assert "drv_halted" in terminal_ids

    def test_all_drives_returns_everything(self, tmp_path: Path) -> None:
        """all_drives() returns both active and terminal."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(drive_id="drv_running", plan_path="/p1", status="running", updated_at=1.0)
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_completed", plan_path="/p2", status="completed", updated_at=2.0
            )
        )

        all_records = store.all_drives()
        all_ids = {d.drive_id for d in all_records}
        assert all_ids == {"drv_running", "drv_completed"}

    def test_active_drive_for_plan(self, tmp_path: Path) -> None:
        """active_drive_for_plan() returns the active drive for a plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_active",
                plan_path="/repo/plan.yaml",
                status="running",
                updated_at=10.0,
            )
        )

        found = store.active_drive_for_plan("/repo/plan.yaml")
        assert found is not None
        assert found.drive_id == "drv_active"

    def test_active_drive_for_plan_returns_none_for_terminal(self, tmp_path: Path) -> None:
        """active_drive_for_plan() returns None if drive is terminal."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_done",
                plan_path="/repo/plan.yaml",
                status="completed",
                updated_at=10.0,
            )
        )

        found = store.active_drive_for_plan("/repo/plan.yaml")
        assert found is None

    def test_active_drive_for_plan_prefers_latest(self, tmp_path: Path) -> None:
        """When multiple active drives exist for same plan, most recent wins."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_old",
                plan_path="/repo/plan.yaml",
                status="running",
                updated_at=10.0,
            )
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_new",
                plan_path="/repo/plan.yaml",
                status="running",
                updated_at=20.0,
            )
        )

        found = store.active_drive_for_plan("/repo/plan.yaml")
        assert found is not None
        # Both are active; picks the most recently updated


# ------------------------------------------------------------------
# Drive admission guard
# ------------------------------------------------------------------


class TestDriveAdmission:
    """Verify drive admission prevents conflicting active drives."""

    def test_admit_when_no_active_drive(self, tmp_path: Path) -> None:
        """Admission succeeds when no active drive for the plan."""
        store = DriveStore(store_root=tmp_path)
        # Should not raise
        store.assert_can_admit_drive("/repo/plan.yaml")

    def test_admit_rejects_conflicting_active_drive(self, tmp_path: Path) -> None:
        """Admission raises when an active drive exists for the plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_active",
                plan_path="/repo/plan.yaml",
                status="running",
                updated_at=10.0,
            )
        )

        with pytest.raises(DriveAdmissionConflictError) as exc_info:
            store.assert_can_admit_drive("/repo/plan.yaml")
        assert exc_info.value.active_drive_id == "drv_active"

    def test_admit_allows_when_terminal_exists(self, tmp_path: Path) -> None:
        """Admission succeeds when a terminal drive exists for the plan."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_done",
                plan_path="/repo/plan.yaml",
                status="completed",
                updated_at=10.0,
            )
        )

        # Should not raise
        store.assert_can_admit_drive("/repo/plan.yaml")

    def test_admission_error_has_active_drive_id(self, tmp_path: Path) -> None:
        """DriveAdmissionConflictError preserves the conflicting drive_id."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(
            DriveRecord(
                drive_id="drv_abc123",
                plan_path="/repo/plan.yaml",
                status="resolving",
                updated_at=10.0,
            )
        )

        with pytest.raises(DriveAdmissionConflictError) as exc_info:
            store.assert_can_admit_drive("/repo/plan.yaml")
        assert exc_info.value.active_drive_id == "drv_abc123"


# ------------------------------------------------------------------
# Persistence survives restart/reload
# ------------------------------------------------------------------


class TestPersistenceSurvivesRestart:
    """Verify that persisted data can be re-read from disk (restarting store)."""

    def test_drive_persists_across_store_instances(self, tmp_path: Path) -> None:
        """DriveRecord survives store re-creation (simulates restart)."""
        store_v1 = DriveStore(store_root=tmp_path)
        record = DriveRecord(
            drive_id="drv_persist",
            plan_path="/repo/plan.yaml",
            status="running",
            updated_at=100.0,
            active_child_run_ids=("run_01", "run_02"),
        )
        store_v1.save_drive(record)

        # Re-create store (simulates restart)
        store_v2 = DriveStore(store_root=tmp_path)
        loaded = store_v2.load_drive("drv_persist")
        assert loaded is not None
        assert loaded.drive_id == "drv_persist"
        assert loaded.status == "running"
        assert loaded.active_child_run_ids == ("run_01", "run_02")

    def test_child_run_persists_across_store_instances(self, tmp_path: Path) -> None:
        """ChildRunRef survives store re-creation (simulates restart)."""
        store_v1 = DriveStore(store_root=tmp_path)
        ref = ChildRunRef(
            run_id="run_restart",
            drive_id="drv_01",
            kind="step",
            status="pending",
            step_id="core.verify",
        )
        store_v1.save_child_run(ref)

        # Re-create store
        store_v2 = DriveStore(store_root=tmp_path)
        loaded = store_v2.child_run_by_id("run_restart")
        assert loaded is not None
        assert loaded.step_id == "core.verify"
        assert loaded.kind == "step"

    def test_drive_and_child_run_persist_together(self, tmp_path: Path) -> None:
        """Both drive and child run data persist together across restart."""
        store_v1 = DriveStore(store_root=tmp_path)
        drive = DriveRecord(
            drive_id="drv_together",
            plan_path="/repo/plan.yaml",
            status="running",
            updated_at=100.0,
        )
        store_v1.save_drive(drive)
        store_v1.save_child_run(
            ChildRunRef(
                run_id="run_child1",
                drive_id="drv_together",
                kind="step",
                status="running",
                step_id="core.verify",
            )
        )

        # Re-create store
        store_v2 = DriveStore(store_root=tmp_path)
        loaded_drive = store_v2.load_drive("drv_together")
        assert loaded_drive is not None
        assert loaded_drive.status == "running"

        active_children = store_v2.active_child_runs_for_drive("drv_together")
        assert len(active_children) == 1
        assert active_children[0].run_id == "run_child1"


# ------------------------------------------------------------------
# Projection replay
# ------------------------------------------------------------------


class TestProjectionReplay:
    """Verify that projection replay rebuilds scheduler-loop facts truthfully."""

    def _seed_store(self, store: DriveStore) -> None:
        """Seed store with drive records and child runs for projection tests."""
        store.save_drive(
            DriveRecord(
                drive_id="drv_proj",
                plan_path="/repo/plan.yaml",
                status="running",
                updated_at=100.0,
                max_parallelism=4,
                active_child_run_ids=("run_01", "run_02", "run_03"),
                frontier_step_ids=("core.verify", "core.snapshot"),
                barrier=None,
                operator_pause_state="active",
                summary="3 child runs active; frontier width=2",
            )
        )
        # Active child runs (pending/running)
        store.save_child_run(
            ChildRunRef(
                run_id="run_01",
                drive_id="drv_proj",
                kind="step",
                status="running",
                step_id="core.verify",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_02",
                drive_id="drv_proj",
                kind="step",
                status="pending",
                step_id="core.snapshot",
            )
        )
        store.save_child_run(
            ChildRunRef(
                run_id="run_03",
                drive_id="drv_proj",
                kind="resolver",
                status="running",
                case_id="case_01M",
            )
        )
        # Terminal child run (should NOT be in active set)
        store.save_child_run(
            ChildRunRef(
                run_id="run_done",
                drive_id="drv_proj",
                kind="step",
                status="success",
                step_id="core.impl",
            )
        )

    def test_replay_rebuilds_active_child_run_ids(self, tmp_path: Path) -> None:
        """replay_active_child_run_ids returns only pending/running child runs."""
        store = DriveStore(store_root=tmp_path)
        self._seed_store(store)

        active_ids = store.replay_active_child_run_ids("drv_proj")
        assert set(active_ids) == {"run_01", "run_02", "run_03"}
        assert "run_done" not in active_ids

    def test_replay_drive_state_rebuilds_record(self, tmp_path: Path) -> None:
        """replay_drive_state rebuilds DriveRecord with active_child_run_ids from store."""
        store = DriveStore(store_root=tmp_path)
        self._seed_store(store)

        rebuilt = store.replay_drive_state("drv_proj")
        assert rebuilt.drive_id == "drv_proj"
        # active_child_run_ids rebuilt from child-run index, not persisted stale
        assert set(rebuilt.active_child_run_ids) == {"run_01", "run_02", "run_03"}
        # frontier comes from persisted record (plan DAG authority)
        assert rebuilt.frontier_step_ids == ("core.verify", "core.snapshot")

    def test_replay_drive_state_raises_for_missing_drive(self, tmp_path: Path) -> None:
        """replay_drive_state raises DriveStoreError for missing drive."""
        store = DriveStore(store_root=tmp_path)
        with pytest.raises(DriveStoreError):
            store.replay_drive_state("nonexistent_drive")

    def test_projection_rebuild_function(self, tmp_path: Path) -> None:
        """rebuild_drive_projection produces DriveProjection from DriveStore."""
        store = DriveStore(store_root=tmp_path)
        self._seed_store(store)

        projection = rebuild_drive_projection(store, "drv_proj")
        assert projection.drive_id == "drv_proj"
        assert projection.status == "running"
        assert set(projection.active_child_run_ids) == {"run_01", "run_02", "run_03"}
        assert projection.frontier_step_ids == ("core.verify", "core.snapshot")
        assert projection.barrier is None
        assert projection.operator_pause_state == "active"
        assert projection.max_parallelism == 4

        # Active step child runs
        assert len(projection.active_step_child_runs) == 2  # two steps with active refs
        step_ids_in_projection = {step_id for step_id, _ in projection.active_step_child_runs}
        assert "core.verify" in step_ids_in_projection
        assert "core.snapshot" in step_ids_in_projection

    def test_projection_rebuild_with_barrier(self, tmp_path: Path) -> None:
        """Projection includes barrier state from persisted drive record."""
        store = DriveStore(store_root=tmp_path)
        barrier = DriveBarrier(
            reason="merge_conflict",
            entered_at=1776124820.0,
            case_ids=("case_01M",),
            pending_resolver_run_id="run_resolve_01",
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_barrier",
                plan_path="/p",
                status="resolving",
                barrier=barrier,
                updated_at=100.0,
            )
        )

        projection = rebuild_drive_projection(store, "drv_barrier")
        assert projection.barrier is not None
        assert projection.barrier.reason == "merge_conflict"

    def test_projection_terminal_child_runs(self, tmp_path: Path) -> None:
        """Projection includes terminal (success/fail) child runs per step."""
        store = DriveStore(store_root=tmp_path)
        self._seed_store(store)

        projection = rebuild_drive_projection(store, "drv_proj")
        terminal_ids = {
            ref.run_id for _, refs in projection.terminal_step_child_runs for ref in refs
        }
        assert "run_done" in terminal_ids

    def test_projection_raises_for_missing_drive(self, tmp_path: Path) -> None:
        """rebuild_drive_projection raises for missing drive_id."""
        store = DriveStore(store_root=tmp_path)
        with pytest.raises(DriveStoreError):
            rebuild_drive_projection(store, "nonexistent_drive")

    def test_projection_raises_for_wrong_store_type(self, tmp_path: Path) -> None:
        """rebuild_drive_projection raises TypeError for non-DriveStore."""
        with pytest.raises(TypeError, match="DriveStore"):
            rebuild_drive_projection("not_a_store", "drv_01")


# ------------------------------------------------------------------
# Active child run filtering regression
# ------------------------------------------------------------------


class TestActiveChildRunFiltering:
    """Regression tests for active-vs-terminal child run filtering."""

    def test_pending_child_runs_are_active(self, tmp_path: Path) -> None:
        """Pending child runs are included in active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_pending", drive_id="drv_01", kind="step", status="pending")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 1
        assert active[0].status == "pending"

    def test_running_child_runs_are_active(self, tmp_path: Path) -> None:
        """Running child runs are included in active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_running", drive_id="drv_01", kind="step", status="running")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 1
        assert active[0].status == "running"

    def test_success_child_runs_not_active(self, tmp_path: Path) -> None:
        """Terminal (success) child runs are excluded from active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_success", drive_id="drv_01", kind="step", status="success")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 0

    def test_fail_child_runs_not_active(self, tmp_path: Path) -> None:
        """Terminal (fail) child runs are excluded from active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_fail", drive_id="drv_01", kind="step", status="fail")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 0

    def test_stall_child_runs_not_active(self, tmp_path: Path) -> None:
        """Stall child runs are excluded from active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_stall", drive_id="drv_01", kind="step", status="stall")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 0

    def test_cancelled_child_runs_not_active(self, tmp_path: Path) -> None:
        """Cancelled child runs are excluded from active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(run_id="run_cancel", drive_id="drv_01", kind="step", status="cancelled")
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 0

    def test_transport_error_child_runs_not_active(self, tmp_path: Path) -> None:
        """Transport error child runs are excluded from active set."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))
        store.save_child_run(
            ChildRunRef(
                run_id="run_transport",
                drive_id="drv_01",
                kind="step",
                status="transport_error",
            )
        )

        active = store.active_child_runs_for_drive("drv_01")
        assert len(active) == 0

    def test_mixed_active_and_terminal_only_returns_active(self, tmp_path: Path) -> None:
        """Mix of active and terminal child runs: only active returned."""
        store = DriveStore(store_root=tmp_path)
        store.save_drive(DriveRecord(drive_id="drv_01", plan_path="/p", updated_at=1.0))

        for status in ("pending", "running", "success", "fail", "stall", "cancelled"):
            store.save_child_run(
                ChildRunRef(
                    run_id=f"run_{status}",
                    drive_id="drv_01",
                    kind="step",
                    status=status,
                )
            )

        active = store.active_child_runs_for_drive("drv_01")
        active_statuses = {ref.status for ref in active}
        assert active_statuses == {"pending", "running"}


# ------------------------------------------------------------------
# JSONL durability and corruption handling
# ------------------------------------------------------------------


class TestDriveStoreJSONLDurability:
    """Verify JSONL persistence handles edge cases correctly."""

    def test_empty_store_returns_empty_collections(self, tmp_path: Path) -> None:
        """An empty store returns empty tuples and None for lookups."""
        store = DriveStore(store_root=tmp_path)
        assert store.active_drives() == ()
        assert store.terminal_drives() == ()
        assert store.all_drives() == ()
        assert store.child_runs_for_drive("nonexistent") == ()
        assert store.active_child_runs_for_drive("nonexistent") == ()
        assert store.load_drive("nonexistent") is None

    def test_drive_store_creates_directories(self, tmp_path: Path) -> None:
        """Store auto-creates its root directory on first write."""
        nested_root = tmp_path / "deep" / "nested" / "drives"
        store = DriveStore(store_root=nested_root)
        store.save_drive(DriveRecord(drive_id="drv_auto", plan_path="/p", updated_at=1.0))
        assert (nested_root / "drives.jsonl").exists()

    def test_child_run_store_creates_directories(self, tmp_path: Path) -> None:
        """Store auto-creates directories for child runs on first write."""
        nested_root = tmp_path / "deep" / "nested" / "drives"
        store = DriveStore(store_root=nested_root)
        store.save_child_run(ChildRunRef(run_id="run_auto", drive_id="drv_01", kind="step"))
        assert (nested_root / "child_runs.jsonl").exists()

    def test_operator_pause_state_round_trips(self, tmp_path: Path) -> None:
        """operator_pause_state='paused' round-trips through persistence."""
        store = DriveStore(store_root=tmp_path)
        record = DriveRecord(
            drive_id="drv_paused_ops",
            plan_path="/p",
            operator_pause_state="paused",
            updated_at=10.0,
        )
        store.save_drive(record)

        loaded = store.load_drive("drv_paused_ops")
        assert loaded is not None
        assert loaded.operator_pause_state == "paused"

    def test_barrier_with_all_fields_round_trips(self, tmp_path: Path) -> None:
        """DriveBarrier with all optional fields persists and loads correctly."""
        store = DriveStore(store_root=tmp_path)
        barrier = DriveBarrier(
            reason="recovery_gate",
            entered_at=1234567890.0,
            case_ids=("case_1", "case_2"),
            pending_resolver_run_id="run_resolver",
            pending_planner_run_id="run_planner",
            active_child_run_ids_at_entry=("run_a", "run_b"),
        )
        store.save_drive(
            DriveRecord(
                drive_id="drv_full_barrier",
                plan_path="/p",
                barrier=barrier,
                updated_at=10.0,
            )
        )

        loaded = store.load_drive("drv_full_barrier")
        assert loaded is not None
        assert loaded.barrier is not None
        assert loaded.barrier.reason == "recovery_gate"
        assert loaded.barrier.case_ids == ("case_1", "case_2")
        assert loaded.barrier.pending_resolver_run_id == "run_resolver"
        assert loaded.barrier.pending_planner_run_id == "run_planner"
        assert loaded.barrier.active_child_run_ids_at_entry == ("run_a", "run_b")
