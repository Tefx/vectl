"""
Drive-scoped inspection and control surface tests.

Authority:
    docs/RFC-orch-drive.md sections 7.3, 7.4, 7.3.1
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

Step: orch_drive_surfaces.inspection-and-control-surface

Verification:
    Main path: query/control tests prove drive-scoped inspection and control behavior.
    Failure path: regressions prove child-run drill-down never escapes drive scope
    and stop --force semantics preserve artifacts/control notes correctly.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from vectl.orchestration.contracts import (
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    DriveBarrier,
    DriveRecord,
)
from vectl.orchestration.control_channel import (
    ActionRequest,
    FilesystemControlChannel,
    send_drive_control,
)
from vectl.orchestration.inspection_queries import (
    ChildRunScopeError,
    DriveInspectQuery,
    DriveInspectView,
    query_drive_actions,
    query_drive_artifacts,
    query_drive_events,
    query_drive_logs,
    query_drive_status,
    validate_child_run_in_drive,
)
from vectl.orchestration.run_store import DriveStore


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def drive_store(tmp_path: Path) -> DriveStore:
    """Create a DriveStore backed by tmp_path."""
    return DriveStore(store_root=tmp_path)


@pytest.fixture()
def sample_drive(drive_store: DriveStore) -> DriveRecord:
    """Create and save a sample drive record."""
    record = DriveRecord(
        drive_id="drv_test01",
        plan_path="/repo/plan.yaml",
        status="running",
        started_at=time.time(),
        updated_at=time.time(),
        active_child_run_ids=("run_alpha", "run_beta"),
        frontier_step_ids=("step.1", "step.2"),
        blocked_case_ids=("case_01",),
        summary="2 child runs active; frontier width=2",
    )
    drive_store.save_drive(record)
    return record


@pytest.fixture()
def sample_child_runs(drive_store: DriveStore) -> tuple[ChildRunRef, ...]:
    """Create and save sample child runs for the sample drive."""
    refs = (
        ChildRunRef(
            run_id="run_alpha",
            drive_id="drv_test01",
            kind="step",
            status="running",
            step_id="step.1",
            artifact_root="/runs/run_alpha",
        ),
        ChildRunRef(
            run_id="run_beta",
            drive_id="drv_test01",
            kind="step",
            status="pending",
            step_id="step.2",
            artifact_root="/runs/run_beta",
        ),
        ChildRunRef(
            run_id="run_resolver",
            drive_id="drv_test01",
            kind="resolver",
            status="running",
            case_id="case_01",
            artifact_root="/runs/run_resolver",
        ),
    )
    for ref in refs:
        drive_store.save_child_run(ref)
    return refs


# ------------------------------------------------------------------
# DriveInspectQuery / DriveInspectView
# ------------------------------------------------------------------


class TestDriveInspectQuery:
    """Verify DriveInspectQuery construction and defaults."""

    def test_minimal_query(self) -> None:
        query = DriveInspectQuery(drive_id="drv_01")
        assert query.drive_id == "drv_01"
        assert query.child_run_id is None
        assert query.limit == 100
        assert query.offset == 0

    def test_query_with_child_run(self) -> None:
        query = DriveInspectQuery(drive_id="drv_01", child_run_id="run_alpha")
        assert query.child_run_id == "run_alpha"

    def test_query_pagination(self) -> None:
        query = DriveInspectQuery(drive_id="drv_01", limit=50, offset=10)
        assert query.limit == 50
        assert query.offset == 10


class TestDriveInspectView:
    """Verify DriveInspectView construction."""

    def test_view_defaults(self) -> None:
        view = DriveInspectView(drive_id="drv_01")
        assert view.status == ""
        assert view.active_child_run_ids == ()
        assert view.frontier_step_ids == ()
        assert view.blocked_case_ids == ()
        assert view.summary == ""
        assert view.child_runs == ()


# ------------------------------------------------------------------
# query_drive_status
# ------------------------------------------------------------------


class TestQueryDriveStatus:
    """Verify drive-scoped status queries."""

    def test_returns_drive_state(
        self,
        drive_store: DriveStore,
        sample_drive: DriveRecord,
        sample_child_runs: tuple[ChildRunRef, ...],
    ) -> None:
        """Drive status query rebuilds active_child_run_ids from child-run index.

        Note: replay_drive_state derives active_child_run_ids from the child-run
        index, not from the persisted DriveRecord. All child runs with status
        pending/running are included.
        """
        query = DriveInspectQuery(drive_id="drv_test01")
        result = query_drive_status(query, drive_store=drive_store)
        assert result.drive_id == "drv_test01"
        assert result.status == "running"
        # All active child runs (running + pending) should be present
        assert "run_alpha" in result.active_child_run_ids
        assert "run_beta" in result.active_child_run_ids
        assert "run_resolver" in result.active_child_run_ids
        assert result.frontier_step_ids == ("step.1", "step.2")
        assert result.blocked_case_ids == ("case_01",)

    def test_with_child_run_selector(
        self,
        drive_store: DriveStore,
        sample_drive: DriveRecord,
        sample_child_runs: tuple[ChildRunRef, ...],
    ) -> None:
        query = DriveInspectQuery(drive_id="drv_test01", child_run_id="run_alpha")
        result = query_drive_status(query, drive_store=drive_store)
        # View should still contain drive-level metadata
        assert result.drive_id == "drv_test01"
        # But child_runs filtered to the specific child
        assert len(result.child_runs) == 1
        assert result.child_runs[0].run_id == "run_alpha"

    def test_child_run_not_in_drive_raises(
        self,
        drive_store: DriveStore,
        sample_drive: DriveRecord,
        sample_child_runs: tuple[ChildRunRef, ...],
    ) -> None:
        query = DriveInspectQuery(drive_id="drv_test01", child_run_id="run_nonexistent")
        with pytest.raises(ChildRunScopeError, match="does not belong"):
            query_drive_status(query, drive_store=drive_store)


# ------------------------------------------------------------------
# validate_child_run_in_drive
# ------------------------------------------------------------------


class TestValidateChildRunInDrive:
    """Verify child-run scope validation per RFC §7.4."""

    def test_valid_child_run(
        self,
        drive_store: DriveStore,
        sample_drive: DriveRecord,
        sample_child_runs: tuple[ChildRunRef, ...],
    ) -> None:
        ref = validate_child_run_in_drive("drv_test01", "run_alpha", drive_store=drive_store)
        assert ref.run_id == "run_alpha"

    def test_invalid_child_run_raises(
        self,
        drive_store: DriveStore,
        sample_drive: DriveRecord,
        sample_child_runs: tuple[ChildRunRef, ...],
    ) -> None:
        with pytest.raises(ChildRunScopeError, match="does not belong"):
            validate_child_run_in_drive("drv_test01", "run_nonexistent", drive_store=drive_store)

    def test_child_run_from_different_drive_raises(
        self,
        drive_store: DriveStore,
    ) -> None:
        """Child run belonging to a different drive must raise."""
        # Create two drives with separate child runs
        drive_a = DriveRecord(drive_id="drv_A", plan_path="/repo/plan.yaml")
        drive_b = DriveRecord(drive_id="drv_B", plan_path="/repo/plan.yaml")
        drive_store.save_drive(drive_a)
        drive_store.save_drive(drive_b)

        child_a = ChildRunRef(run_id="run_A1", drive_id="drv_A", kind="step", status="running")
        child_b = ChildRunRef(run_id="run_B1", drive_id="drv_B", kind="step", status="running")
        drive_store.save_child_run(child_a)
        drive_store.save_child_run(child_b)

        # run_A1 does not belong to drv_B
        with pytest.raises(ChildRunScopeError, match="does not belong"):
            validate_child_run_in_drive("drv_B", "run_A1", drive_store=drive_store)

    def test_scope_boundary_never_escapes_drive(
        self,
        drive_store: DriveStore,
    ) -> None:
        """Regression: child-run drill-down must never escape drive scope."""
        drive_a = DriveRecord(drive_id="drv_A", plan_path="/repo/plan.yaml")
        drive_b = DriveRecord(drive_id="drv_B", plan_path="/repo/plan.yaml")
        drive_store.save_drive(drive_a)
        drive_store.save_drive(drive_b)

        # Many child runs across both drives
        for i in range(5):
            drive_store.save_child_run(
                ChildRunRef(run_id=f"run_A{i}", drive_id="drv_A", kind="step", status="running")
            )
            drive_store.save_child_run(
                ChildRunRef(run_id=f"run_B{i}", drive_id="drv_B", kind="step", status="running")
            )

        # All A children are valid for drv_A
        for i in range(5):
            ref = validate_child_run_in_drive("drv_A", f"run_A{i}", drive_store=drive_store)
            assert ref.drive_id == "drv_A"

        # All B children are invalid for drv_A (scope boundary)
        for i in range(5):
            with pytest.raises(ChildRunScopeError):
                validate_child_run_in_drive("drv_A", f"run_B{i}", drive_store=drive_store)


# ------------------------------------------------------------------
# query_drive_events
# ------------------------------------------------------------------


class TestQueryDriveEvents:
    """Verify drive-scoped event queries."""

    def test_filters_events_by_drive_id(self) -> None:
        """Only events for the specified drive are returned."""
        from dataclasses import dataclass

        @dataclass
        class FakeEvent:
            drive_id: str
            child_run_id: str | None = None
            kind: str = "test"
            seq: int = 0
            step_id: str | None = None

        events = (
            FakeEvent(drive_id="drv_01", seq=1),
            FakeEvent(drive_id="drv_02", seq=2),
            FakeEvent(drive_id="drv_01", seq=3),
            FakeEvent(drive_id="drv_01", child_run_id="run_alpha", seq=4),
        )
        query = DriveInspectQuery(drive_id="drv_01")
        result = query_drive_events(query, events)
        assert len(result) == 3  # 3 events for drv_01

    def test_filters_by_child_run_id(self) -> None:
        """When child_run_id is set, returns only that child's events."""
        from dataclasses import dataclass

        @dataclass
        class FakeEvent:
            drive_id: str
            child_run_id: str | None = None
            kind: str = "test"
            seq: int = 0
            step_id: str | None = None

        events = (
            FakeEvent(drive_id="drv_01", seq=1),
            FakeEvent(drive_id="drv_01", child_run_id="run_alpha", seq=2),
            FakeEvent(drive_id="drv_01", child_run_id="run_beta", seq=3),
        )
        query = DriveInspectQuery(drive_id="drv_01", child_run_id="run_alpha")
        result = query_drive_events(query, events)
        assert len(result) == 1
        assert result[0].child_run_id == "run_alpha"


# ------------------------------------------------------------------
# query_drive_logs
# ------------------------------------------------------------------


class TestQueryDriveLogs:
    """Verify drive-scoped log queries."""

    def test_filters_logs_by_drive_id(self) -> None:
        logs = (
            "drive_id=drv_01 run_id=run_alpha step started",
            "drive_id=drv_02 run_id=run_gamma step started",
            "drive_id=drv_01 run_id=run_beta step completed",
        )
        query = DriveInspectQuery(drive_id="drv_01")
        result = query_drive_logs(query, logs)
        assert len(result) == 2
        assert all("drv_01" in line for line in result)

    def test_filters_by_child_run_id(self) -> None:
        logs = (
            "drive_id=drv_01 run_id=run_alpha step started",
            "drive_id=drv_01 run_id=run_beta step started",
        )
        query = DriveInspectQuery(drive_id="drv_01", child_run_id="run_alpha")
        result = query_drive_logs(query, logs)
        assert len(result) == 1
        assert "run_alpha" in result[0]


# ------------------------------------------------------------------
# query_drive_actions
# ------------------------------------------------------------------


class TestQueryDriveActions:
    """Verify drive-scoped action queries."""

    def test_filters_by_child_run_id(self) -> None:
        actions = (
            "run_id=run_alpha status=pending action_id=act1 type=pause",
            "run_id=run_beta status=pending action_id=act2 type=pause",
        )
        query = DriveInspectQuery(drive_id="drv_01", child_run_id="run_alpha")
        result = query_drive_actions(query, actions)
        assert len(result) == 1
        assert "run_alpha" in result[0]


# ------------------------------------------------------------------
# Drive-Scoped Control: send_drive_control
# ------------------------------------------------------------------


class TestSendDriveControl:
    """Verify drive-scoped control message persistence."""

    def test_pause_persists_action(self, tmp_path: Path) -> None:
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="pause",
            channel=channel,
        )
        assert result.msg_type == "drive.pause"
        assert result.run_id == "drv_01"

    def test_unpause_persists_action(self, tmp_path: Path) -> None:
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="unpause",
            channel=channel,
        )
        assert result.msg_type == "drive.unpause"

    def test_stop_persists_action(self, tmp_path: Path) -> None:
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="stop",
            channel=channel,
        )
        assert result.msg_type == "drive.stop"

    def test_stop_force_appends_marker(self, tmp_path: Path) -> None:
        """Per RFC §7.3.1: stop --force appends force=true to payload."""
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="stop",
            channel=channel,
            force=True,
        )
        assert "force=true" in result.payload

    def test_stop_without_force_no_marker(self, tmp_path: Path) -> None:
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="stop",
            channel=channel,
            force=False,
        )
        assert "force=true" not in result.payload

    def test_reason_included_in_payload(self, tmp_path: Path) -> None:
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="pause",
            channel=channel,
            reason="operator requested pause",
        )
        assert "operator requested pause" in result.payload

    def test_stop_force_preserves_artifact_notes(self, tmp_path: Path) -> None:
        """Regression: stop --force must preserve artifact/control notes.

        Per RFC §7.3.1: 'preserve child-run artifacts even when
        cancellation is requested.' This test ensures the control
        message is persisted correctly so downstream handlers can
        preserve artifacts during force stop.
        """
        channel = FilesystemControlChannel(runs_root=tmp_path)
        result = send_drive_control(
            drive_id="drv_01",
            action="stop",
            channel=channel,
            reason="emergency stop",
            force=True,
        )
        # Verify the message is persisted (available for artifact-preservation logic)
        assert result.action_id  # Has a valid action_id
        assert result.run_id == "drv_01"
        # Both reason and force marker should be present
        assert "emergency stop" in result.payload
        assert "force=true" in result.payload

        # Verify the action is readable from the control channel
        requests = channel.list_requests("drv_01", status="pending")
        assert len(requests) >= 1
        saved = requests[0]
        assert saved.msg_type == "drive.stop"
        assert "force=true" in saved.payload
        assert "emergency stop" in saved.payload


# ------------------------------------------------------------------
# Scope Isolation: child-run drill-down must not escape drive
# ------------------------------------------------------------------


class TestChildRunScopeIsolation:
    """Regression suite: prove child-run drill-down never escapes drive scope.

    Authority: docs/RFC-orch-drive.md section 7.4
    """

    def test_query_status_with_wrong_child_raises(
        self,
        drive_store: DriveStore,
    ) -> None:
        """query_drive_status with a child from another drive must fail."""
        drive_a = DriveRecord(drive_id="drv_A", plan_path="/repo/plan.yaml")
        drive_b = DriveRecord(drive_id="drv_B", plan_path="/repo/plan.yaml")
        drive_store.save_drive(drive_a)
        drive_store.save_drive(drive_b)

        child_b = ChildRunRef(run_id="run_B1", drive_id="drv_B", kind="step", status="running")
        drive_store.save_child_run(child_b)

        query = DriveInspectQuery(drive_id="drv_A", child_run_id="run_B1")
        with pytest.raises(ChildRunScopeError, match="does not belong"):
            query_drive_status(query, drive_store=drive_store)

    def test_query_artifacts_with_wrong_child_raises(
        self,
        drive_store: DriveStore,
    ) -> None:
        """query_drive_artifacts with a child from another drive must fail."""
        child_refs = (
            ChildRunRef(run_id="run_A1", drive_id="drv_A", kind="step", status="running"),
        )
        query = DriveInspectQuery(drive_id="drv_A", child_run_id="run_wrong")
        with pytest.raises(ChildRunScopeError, match="does not belong"):
            query_drive_artifacts(query, child_refs, artifact_roots=())

    def test_query_actions_is_scoped(self) -> None:
        """query_drive_actions with child_run_id only returns that child's actions."""
        actions = (
            "run_id=run_A1 status=pending action_id=act1 type=pause",
            "run_id=run_B1 status=pending action_id=act2 type=pause",
        )
        query = DriveInspectQuery(drive_id="drv_A", child_run_id="run_A1")
        result = query_drive_actions(query, actions)
        # Only actions matching the child_run_id should be returned
        assert all("run_A1" in r for r in result)
        assert not any("run_B1" in r for r in result)
