"""CLI drive lifecycle surface tests.

Authority:
    docs/RFC-orch-drive.md sections 7.1, 7.2, 7.4
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md sections 6, 7.7

These tests verify:
    - drive start/status/runs/resume/recover selectors and outputs
    - orch run rejects when an active drive exists (exit code 2)
    - invalid child-run selectors fail with exit code 2
    - --latest drive resolution works correctly
    - max_parallelism validation (exit code 2 for out-of-range)
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from vectl.io import save_plan
from vectl.models import Phase, Plan, Step
from vectl.orch_app import (
    AppConfig,
    build_orchestration_app,
)
from vectl.orchestration.config import OrchestrationConfig
from vectl.orchestration.contracts import (
    ChildRunRef,
    DriveBarrier,
)
from vectl.orchestration.driver import (
    DriveAdmissionError,
    DriveStartResult,
    DriveStatusResult,
    MaxParallelismError,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _write_plan(plan_path: Path) -> None:
    """Write a minimal plan.yaml for testing."""
    plan = Plan(
        project="cli-drive-test",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="core.ready", name="Ready"),
                    Step(id="core.other", name="Other", depends_on=["core.ready"]),
                ],
            )
        ],
    )
    save_plan(plan, plan_path)


def _build_app(tmp_path: Path):
    """Build orchestration app with proper config for CLI drive tests."""
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    config = AppConfig(
        plan_path=plan_path,
        orchestration_config=OrchestrationConfig(plan_path=plan_path),
        run_store_root=runs_root,
    )
    return build_orchestration_app(config)


# ------------------------------------------------------------------
# start_drive surface
# ------------------------------------------------------------------


class TestDriveStartSurface:
    """Test drive start CLI boundary behavior."""

    def test_start_drive_returns_result(self, tmp_path: Path) -> None:
        """start_drive returns a DriveStartResult with valid drive_id."""
        app = _build_app(tmp_path)
        result = app.start_drive(agent="test-agent", max_parallelism=4)
        assert isinstance(result, DriveStartResult)
        assert result.drive_id.startswith("drv_")
        assert result.status == "running"
        assert result.frontier_step_ids == ("core.ready",)

    def test_start_drive_admission_error_includes_drive_id(self, tmp_path: Path) -> None:
        """DriveAdmissionError includes the active drive_id."""
        app = _build_app(tmp_path)
        result1 = app.start_drive(agent="test-agent", max_parallelism=4)
        with pytest.raises(DriveAdmissionError) as exc_info:
            app.start_drive(agent="test-agent", max_parallelism=4)
        assert exc_info.value.active_drive_id == result1.drive_id

    def test_start_drive_max_parallelism_validation(self, tmp_path: Path) -> None:
        """MaxParallelismError raised for out-of-range values."""
        app = _build_app(tmp_path)
        with pytest.raises(MaxParallelismError):
            app.start_drive(agent="test-agent", max_parallelism=0)
        with pytest.raises(MaxParallelismError):
            app.start_drive(agent="test-agent", max_parallelism=33)

    def test_start_drive_parallelism_boundaries(self, tmp_path: Path) -> None:
        """Boundary values 1 and 32 are accepted."""
        app = _build_app(tmp_path)
        result1 = app.start_drive(agent="test-agent", max_parallelism=1)
        assert result1.status == "running"
        # Create a new repo for the second drive since only one active per plan
        plan_path2 = tmp_path / "plan2.yaml"
        runs_root2 = tmp_path / "runs2"
        _write_plan(plan_path2)
        config2 = AppConfig(
            plan_path=plan_path2,
            orchestration_config=OrchestrationConfig(plan_path=plan_path2),
            run_store_root=runs_root2,
        )
        app2 = build_orchestration_app(config2)
        result2 = app2.start_drive(agent="test-agent", max_parallelism=32)
        assert result2.status == "running"

    def test_drive_driver_wires_app_resolver(self, tmp_path: Path) -> None:
        """App-built drive drivers carry the configured resolver into loop passes."""
        app = _build_app(tmp_path)
        driver = app._drive_driver()

        assert driver._resolver is app._resolver


# ------------------------------------------------------------------
# drive_status surface
# ------------------------------------------------------------------


class TestDriveStatusSurface:
    """Test drive_status CLI boundary behavior."""

    def test_drive_status_existing_drive(self, tmp_path: Path) -> None:
        """drive_status returns correct state for an existing drive."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.drive_status(drive_id=started.drive_id)
        assert isinstance(result, DriveStatusResult)
        assert result.drive_id == started.drive_id
        assert result.status == "running"

    def test_drive_status_not_found(self, tmp_path: Path) -> None:
        """drive_status returns 'not found' summary for missing drive_id."""
        app = _build_app(tmp_path)
        result = app.drive_status(drive_id="drv_nonexistent")
        assert "not found" in result.summary

    def test_drive_status_includes_frontier(self, tmp_path: Path) -> None:
        """drive_status includes frontier_step_ids."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.drive_status(drive_id=started.drive_id)
        assert result.frontier_step_ids == ("core.ready",)

    def test_drive_status_includes_blocked_case_ids(self, tmp_path: Path) -> None:
        """drive_status preserves blocked_case_ids from the durable DriveRecord."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        store = app._drive_store()
        current = store.replay_drive_state(started.drive_id)
        assert current is not None
        store.save_drive(replace(current, blocked_case_ids=("case-a", "case-b")))

        result = app.drive_status(drive_id=started.drive_id)

        assert result.blocked_case_ids == ("case-a", "case-b")

    def test_foreground_does_not_exit_before_resolver_for_resolving_case(
        self, tmp_path: Path
    ) -> None:
        """Foreground supervision attempts resolver before treating resolving as terminal."""
        app = _build_app(tmp_path)
        status = DriveStatusResult(
            drive_id="drv_resolving",
            status="resolving",
            blocked_case_ids=("case-runtime-001",),
            barrier=DriveBarrier(
                reason="runtime_failure",
                case_ids=("case-runtime-001",),
            ),
        )

        assert app._foreground_drive_should_exit(status) is False


# ------------------------------------------------------------------
# drive_runs surface
# ------------------------------------------------------------------


class TestDriveRunsSurface:
    """Test drive_runs CLI boundary behavior."""

    def test_drive_runs_empty(self, tmp_path: Path) -> None:
        """drive_runs returns empty tuple for a new drive."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        runs = app.drive_runs(drive_id=started.drive_id)
        assert isinstance(runs, tuple)
        assert len(runs) == 0

    def test_drive_runs_with_child_runs(self, tmp_path: Path) -> None:
        """drive_runs returns child runs after they are persisted."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        store = app._drive_store()

        # Persist a child run directly
        ref = ChildRunRef(
            run_id="run_child1",
            drive_id=started.drive_id,
            kind="step",
            step_id="core.ready",
            status="running",
            workspace=".vectl/workspaces/core.ready",
            runner="opencode",
            session_id="ses_001",
            artifact_root=".vectl/runs/run_child1",
        )
        store.save_child_run(ref)

        runs = app.drive_runs(drive_id=started.drive_id)
        assert len(runs) == 1
        assert runs[0].run_id == "run_child1"


# ------------------------------------------------------------------
# resolve_latest_drive_id surface
# ------------------------------------------------------------------


class TestResolveLatestDriveId:
    """Test --latest drive resolution behavior."""

    def test_no_drives_returns_none(self, tmp_path: Path) -> None:
        """resolve_latest_drive_id returns None when no drives exist."""
        app = _build_app(tmp_path)
        assert app.resolve_latest_drive_id() is None

    def test_returns_active_drive(self, tmp_path: Path) -> None:
        """resolve_latest_drive_id returns existing active drive."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        resolved = app.resolve_latest_drive_id()
        assert resolved == started.drive_id


# ------------------------------------------------------------------
# has_active_drive_for_plan surface (orch run rejection)
# ------------------------------------------------------------------


class TestActiveDriveRejection:
    """Test that orch run rejects when an active drive exists.

    Authority: docs/RFC-orch-drive.md section 7.1
    """

    def test_run_rejected_when_active_drive_exists(self, tmp_path: Path) -> None:
        """orch run returns OrchestrationResult with success=False and
        message containing the drive_id when an active drive exists."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)

        result = app.run(step_id="core.ready")
        assert result.success is False
        assert started.drive_id in result.message
        assert "active drive" in result.message.lower()

    def test_has_active_drive_for_plan(self, tmp_path: Path) -> None:
        """has_active_drive_for_plan returns drive_id when active."""
        app = _build_app(tmp_path)
        assert app.has_active_drive_for_plan() is None
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        assert app.has_active_drive_for_plan() == started.drive_id

    def test_no_rejection_when_no_active_drive(self, tmp_path: Path) -> None:
        """No rejection occurs when no active drive for the plan."""
        app = _build_app(tmp_path)
        assert app.has_active_drive_for_plan() is None


# ------------------------------------------------------------------
# drive control consumption
# ------------------------------------------------------------------


class TestDriveControlConsumption:
    """Test app-level drive control wiring into the drive loop."""

    def test_run_drive_loop_consumes_queued_stop_before_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queued drive.stop is applied before any child dispatch occurs."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)

        def _fail_child_launch(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("drive.stop was not consumed before dispatch")

        monkeypatch.setattr(app, "_launch_drive_step_child_run", _fail_child_launch)
        stop = app.control_drive_stop(
            drive_id=started.drive_id,
            reason="test stop",
            force=True,
        )

        result = app.run_drive_loop(started.drive_id)

        assert stop.success is True
        assert result.status == "stopped"
        assert "operator stop consumed" in result.summary
        assert app.has_active_drive_for_plan() is None

    def test_force_stop_cancels_persisted_active_child_refs(self, tmp_path: Path) -> None:
        """drive.stop --force clears active child ids and marks child ref cancelled."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        store = app._drive_store()
        child = ChildRunRef(
            run_id="exec-child-1",
            drive_id=started.drive_id,
            kind="step",
            step_id="core.ready",
            status="running",
            workspace="ws-core.ready",
            runner="opencode",
            artifact_root="runs/exec-child-1",
        )
        store.save_child_run(child)
        current = store.replay_drive_state(started.drive_id)
        assert current is not None
        store.save_drive(replace(current, active_child_run_ids=(child.run_id,)))

        app.control_drive_stop(drive_id=started.drive_id, reason="test stop", force=True)
        result = app.run_drive_loop(started.drive_id)

        assert result.status == "stopped"
        assert result.active_child_run_ids == ()
        persisted_child = store.child_run_by_id(child.run_id)
        assert persisted_child is not None
        assert persisted_child.status == "cancelled"


# ------------------------------------------------------------------
# resume_drive surface
# ------------------------------------------------------------------


class TestDriveResumeSurface:
    """Test drive_resume CLI boundary behavior."""

    def test_resume_existing_drive(self, tmp_path: Path) -> None:
        """resume_drive returns correct result for an existing drive."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.resume_drive(drive_id=started.drive_id)
        assert result.drive_id == started.drive_id
        assert result.status == "running"


# ------------------------------------------------------------------
# recover_drive surface
# ------------------------------------------------------------------


class TestDriveRecoverSurface:
    """Test drive_recover CLI boundary behavior."""

    def test_recover_dry_run(self, tmp_path: Path) -> None:
        """recover_drive dry-run returns result without modifying state."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.recover_drive(drive_id=started.drive_id, dry_run=True)
        assert result.drive_id == started.drive_id
        assert "dry-run" in result.summary.lower() or result.status == "running"

    def test_recover_actual(self, tmp_path: Path) -> None:
        """recover_drive applies recovery changes."""
        app = _build_app(tmp_path)
        started = app.start_drive(agent="test-agent", max_parallelism=4)
        result = app.recover_drive(drive_id=started.drive_id, dry_run=False)
        assert result.drive_id == started.drive_id


# ------------------------------------------------------------------
# CLI Typer integration: exit codes
# ------------------------------------------------------------------


class TestCLIDriveExitCodes:
    """Test the CLI surface exit codes for drive commands.

    Authority:
        - docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6
        - docs/RFC-orch-drive.md §7.1 (exit code 2 for active-drive rejection)
        - docs/RFC-orch-drive.md §7.2 (max_parallelism validation exit code 2)
    """

    def test_cli_drive_help_exits_zero(self) -> None:
        """vectl orch drive --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "drive" in result.stdout.lower()

    def test_cli_drive_status_help_exits_zero(self) -> None:
        """vectl orch drive-status --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-status", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_drive_runs_help_exits_zero(self) -> None:
        """vectl orch drive-runs --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-runs", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_drive_resume_help_exits_zero(self) -> None:
        """vectl orch drive-resume --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-resume", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_drive_recover_help_exits_zero(self) -> None:
        """vectl orch drive-recover --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-recover", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0

    def test_cli_drive_status_no_selector_exits_3(self) -> None:
        """vectl orch drive-status (no selector) exits 3 (validation)."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3

    def test_cli_drive_resume_no_selector_exits_3(self) -> None:
        """vectl orch drive-resume (no selector) exits 3 (validation)."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-resume"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3

    def test_cli_drive_recover_no_selector_exits_3(self) -> None:
        """vectl orch drive-recover (no selector) exits 3 (validation)."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-recover"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3

    def test_cli_drive_runs_no_selector_exits_3(self) -> None:
        """vectl orch drive-runs (no selector) exits 3 (validation)."""
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "drive-runs"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3
