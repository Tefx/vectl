"""CLI surface tests for ``vectl orch`` command tree wiring.

Authority:
    orch_operator_control_surface.impl_vectl_orch_cli_surface step contract
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.1-7.5
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
import typer
from typer.testing import CliRunner

import vectl.cli as cli
from vectl.cli import app
from vectl.orchestration.driver import DriveAdmissionError

runner = CliRunner()


@dataclass(frozen=True)
class _RunResult:
    run_id: str
    step_id: str
    status: str | None = "running"
    output_summary: str = ""


@dataclass(frozen=True)
class _Result:
    success: bool = True
    message: str = "ok"
    run_id: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class _DriveStartResult:
    drive_id: str
    status: str = "running"
    frontier_step_ids: tuple[str, ...] = ("s1",)
    summary: str = "started"


@dataclass(frozen=True)
class _DriveLoopResult:
    drive_id: str
    status: str = "running"
    summary: str = "looped"


@dataclass(frozen=True)
class _DriveStatusResult:
    drive_id: str
    status: str = "running"
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    blocked_case_ids: tuple[str, ...] = ()
    summary: str = "drive status"


@dataclass(frozen=True)
class _DriveRecoverResult:
    drive_id: str
    status: str = "running"
    recovered_child_run_ids: tuple[str, ...] = ()
    failed_child_run_ids: tuple[str, ...] = ()
    conflict_resolutions: tuple[str, ...] = ("dry-run: no changes applied",)
    summary: str = "dry-run: no changes applied"


@dataclass(frozen=True)
class _InspectResult:
    view_type: str
    data: tuple[str, ...]


@dataclass(frozen=True)
class _CaseResult:
    case_id: str | None = None
    status: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class _ConfigResult:
    show_output: str = "ok"
    validation_passed: bool = True
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CutoverResult:
    can_cutover: bool
    criteria_results: tuple[str, ...] = ()
    blocking_items: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


class _FakeOrchApp:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pause_reason: str | None = None
        self.unpause_reason: str | None = None
        self.stop_reason: str | None = None
        self.stop_force: bool = False
        self.stop_run_id: str | None = None
        self.config_show_effective: bool = False
        self.drive_loop_id: str | None = None

    def run(self, *, step_id: str | None, agent: str | None) -> _Result:
        self.calls.append("run")
        return _Result(success=True, message="started", run_id="r1", step_id=step_id or "s1")

    def resume(self, *, run_id: str) -> _Result:
        self.calls.append("resume")
        return _Result(success=True, message="resumed", run_id=run_id, step_id="s1")

    def recover(self, *, step_id: str | None = None) -> _Result:
        self.calls.append("recover")
        return _Result(success=True, message="recovered", step_id=step_id)

    def runs(self, *, step_id: str | None = None, limit: int = 100) -> tuple[_RunResult, ...]:
        self.calls.append("runs")
        del limit
        return (_RunResult(run_id="r1", step_id=step_id or "s1"),)

    def prune(self, *, before: float | None = None, force: bool = False) -> _Result:
        self.calls.append("prune")
        del before, force
        return _Result(success=True, message="pruned")

    def inspect_status(self, *, step_id: str | None = None) -> _InspectResult:
        self.calls.append("inspect_status")
        del step_id
        return _InspectResult(view_type="status", data=("status=running",))

    def inspect_events(self, *, step_id: str | None = None, limit: int = 100) -> _InspectResult:
        self.calls.append("inspect_events")
        del step_id, limit
        return _InspectResult(view_type="events", data=("event=run_started",))

    def inspect_logs(
        self, *, run_id: str | None = None, step_id: str | None = None
    ) -> _InspectResult:
        self.calls.append("inspect_logs")
        del run_id, step_id
        return _InspectResult(view_type="logs", data=("line=1",))

    def inspect_artifacts(self, *, step_id: str | None = None) -> _InspectResult:
        self.calls.append("inspect_artifacts")
        del step_id
        return _InspectResult(view_type="artifacts", data=("path=a",))

    def inspect_actions(self, *, run_id: str | None = None) -> _InspectResult:
        self.calls.append("inspect_actions")
        del run_id
        return _InspectResult(view_type="actions", data=("status=pending",))

    def case_list(self, *, status: str | None = None) -> tuple[_CaseResult, ...]:
        self.calls.append("case_list")
        return (_CaseResult(case_id="c1", status=status or "open"),)

    def case_show(self, *, case_id: str) -> _CaseResult:
        self.calls.append("case_show")
        return _CaseResult(case_id=case_id, status="open")

    def case_respond(self, *, case_id: str, response: str) -> _Result:
        self.calls.append("case_respond")
        del response
        return _Result(success=True, message="queued", step_id=case_id)

    def control_pause(self, *, step_id: str | None = None, reason: str | None = None):
        self.calls.append("control_pause")
        self.pause_reason = reason
        del step_id
        return _Result(success=True, message="paused")

    def control_unpause(self, *, step_id: str | None = None, reason: str | None = None):
        self.calls.append("control_unpause")
        self.unpause_reason = reason
        del step_id
        return _Result(success=True, message="unpaused")

    def control_stop(
        self,
        *,
        run_id: str | None = None,
        reason: str | None = None,
        force: bool = False,
    ):
        self.calls.append("control_stop")
        self.stop_run_id = run_id
        self.stop_reason = reason
        self.stop_force = force
        return _Result(success=True, message="stopped")

    def config_show(self, *, effective: bool = False) -> _ConfigResult:
        self.calls.append("config_show")
        self.config_show_effective = effective
        return _ConfigResult(show_output="show")

    def config_validate(self) -> _ConfigResult:
        self.calls.append("config_validate")
        return _ConfigResult(show_output="valid", validation_passed=True)

    def config_tools(self) -> _ConfigResult:
        self.calls.append("config_tools")
        return _ConfigResult(show_output="tools", tools=("core", "orchestration"))

    def cutover_validate(self) -> _CutoverResult:
        self.calls.append("cutover_validate")
        return _CutoverResult(can_cutover=True, criteria_results=("criterion.1: met",))

    def migration_advance_state(
        self, *, status: object, legacy_run_id: str | None = None
    ) -> _Result:
        self.calls.append("migration_advance_state")
        del status, legacy_run_id
        return _Result(success=True, message="advanced")

    def has_active_drive_for_plan(self) -> str | None:
        """Return None (no active drive) for fake app."""
        return None

    def start_drive(self, *, agent: str = "", max_parallelism: int = 4) -> _DriveStartResult:
        self.calls.append("start_drive")
        del agent, max_parallelism
        return _DriveStartResult(drive_id="drv-start")

    def run_drive_loop(self, drive_id: str) -> _DriveLoopResult:
        self.calls.append("run_drive_loop")
        self.drive_loop_id = drive_id
        return _DriveLoopResult(drive_id=drive_id)

    def run_drive_foreground(
        self,
        drive_id: str,
        *,
        poll_interval_seconds: float = 2.0,
        status_interval_seconds: float = 30.0,
        progress_callback=None,
        progress_mode: str = "auto",
        verbose: bool = False,
    ) -> _DriveLoopResult:
        self.calls.append("run_drive_foreground")
        self.drive_loop_id = drive_id
        self.poll_interval_seconds = poll_interval_seconds
        self.status_interval_seconds = status_interval_seconds
        self.progress_mode = progress_mode
        self.verbose = verbose
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "drive_started",
                    "timestamp": 1.0,
                    "drive_id": drive_id,
                    "max_parallelism": 1,
                    "poll_interval_seconds": poll_interval_seconds,
                    "status_interval_seconds": status_interval_seconds,
                    "progress_mode": progress_mode,
                }
            )
            progress_callback(
                {
                    "type": "drive_terminal",
                    "timestamp": 2.0,
                    "drive_id": drive_id,
                    "status": "completed",
                    "summary": "foreground complete",
                }
            )
        return _DriveLoopResult(drive_id=drive_id, summary="foreground complete")

    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        return _DriveRecoverResult(drive_id=drive_id)

    def inspect_drive_status(
        self, *, drive_id: str, child_run_id: str | None = None
    ) -> _InspectResult:
        self.calls.append("inspect_drive_status")
        del drive_id, child_run_id
        return _InspectResult(view_type="status", data=("drive_status=running",))

    def inspect_drive_events(
        self, *, drive_id: str, child_run_id: str | None = None, limit: int = 100
    ) -> _InspectResult:
        self.calls.append("inspect_drive_events")
        del drive_id, child_run_id, limit
        return _InspectResult(view_type="events", data=("event=drive_event",))

    def inspect_drive_logs(
        self, *, drive_id: str, child_run_id: str | None = None
    ) -> _InspectResult:
        self.calls.append("inspect_drive_logs")
        del drive_id, child_run_id
        return _InspectResult(view_type="logs", data=("drive_log_line=1",))

    def inspect_drive_artifacts(
        self, *, drive_id: str, child_run_id: str | None = None
    ) -> _InspectResult:
        self.calls.append("inspect_drive_artifacts")
        del drive_id, child_run_id
        return _InspectResult(view_type="artifacts", data=("drive_artifact=a",))

    def inspect_drive_actions(
        self, *, drive_id: str, child_run_id: str | None = None
    ) -> _InspectResult:
        self.calls.append("inspect_drive_actions")
        del drive_id, child_run_id
        return _InspectResult(view_type="actions", data=("drive_action=pending",))

    def control_drive_pause(self, *, drive_id: str, reason: str | None = None):
        self.calls.append("control_drive_pause")
        self.pause_reason = reason
        del drive_id
        return _Result(success=True, message="drive paused")

    def control_drive_unpause(self, *, drive_id: str, reason: str | None = None):
        self.calls.append("control_drive_unpause")
        self.unpause_reason = reason
        del drive_id
        return _Result(success=True, message="drive unpaused")

    def control_drive_stop(self, *, drive_id: str, reason: str | None = None, force: bool = False):
        self.calls.append("control_drive_stop")
        self.stop_reason = reason
        self.stop_force = force
        del drive_id
        return _Result(success=True, message="drive stopped")

    def drive_status(self, *, drive_id: str):
        self.calls.append("drive_status")
        return _DriveStatusResult(drive_id=drive_id)

    def resolve_latest_drive_id(self) -> str | None:
        return None

    def _drive_store(self):
        return None


class _FakeActiveDriveOrchApp(_FakeOrchApp):
    def start_drive(self, *, agent: str = "", max_parallelism: int = 4) -> _DriveStartResult:
        self.calls.append("start_drive")
        del agent, max_parallelism
        raise DriveAdmissionError("active drive exists", active_drive_id="drv-active")


class _FakeRecoveringActiveDriveOrchApp(_FakeActiveDriveOrchApp):
    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        if dry_run:
            return _DriveRecoverResult(
                drive_id=drive_id,
                status="recovering",
                conflict_resolutions=("recovering drive can safely transition to running",),
                summary=(
                    "dry-run: would recover 0 child runs, fail 0 stale runs, "
                    "transition to running"
                ),
            )
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="running",
            conflict_resolutions=("safe startup auto-recovery applied",),
            summary=(
                "drive recovered; 0 child runs restored; 0 stale runs classified as "
                "failed; status=running"
            ),
        )


class _FakeRetryableFailedChildDriveOrchApp(_FakeActiveDriveOrchApp):
    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        if dry_run:
            return _DriveRecoverResult(
                drive_id=drive_id,
                status="running",
                failed_child_run_ids=("run-stale",),
                conflict_resolutions=(
                    "child run run-stale: persisted status=running but live process missing "
                    "and no terminal artifact; classifying as stale (failed); "
                    "scheduler may retry after claim release",
                ),
                summary=(
                    "dry-run: would recover 0 child runs, fail 1 stale runs, "
                    "transition to running"
                ),
            )
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="running",
            failed_child_run_ids=("run-stale",),
            conflict_resolutions=("marked stale child run failed and released claim",),
            summary=(
                "drive recovered; 0 child runs restored; 1 stale runs classified as "
                "failed; status=running"
            ),
        )


class _FakeOrphanedClaimActiveDriveOrchApp(_FakeActiveDriveOrchApp):
    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        if dry_run:
            return _DriveRecoverResult(
                drive_id=drive_id,
                status="blocked_operator",
                conflict_resolutions=(
                    "released orphaned drive claim for step.a: no active child run remains",
                ),
                summary=(
                    "dry-run: would recover 0 child runs, fail 0 stale runs, "
                    "transition to resolving"
                ),
            )
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="resolving",
            conflict_resolutions=(
                "released orphaned drive claim for step.a: no active child run remains",
            ),
            summary=(
                "drive recovered; 0 child runs restored; 0 stale runs classified as "
                "failed; status=resolving"
            ),
        )


class _FakeAgentAssistedRecoveryDriveApp(_FakeActiveDriveOrchApp):
    def __init__(self) -> None:
        super().__init__()
        self.agent_assisted = False

    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        if dry_run and not self.agent_assisted:
            return _DriveRecoverResult(
                drive_id=drive_id,
                status="blocked_operator",
                failed_child_run_ids=("run-ambiguous",),
                conflict_resolutions=(
                    "child run run-ambiguous: manual inspection required before retry",
                ),
                summary="dry-run: would fail 1 ambiguous child run; operator required",
            )
        if dry_run:
            return _DriveRecoverResult(
                drive_id=drive_id,
                status="running",
                failed_child_run_ids=("run-ambiguous",),
                conflict_resolutions=(
                    "child run run-ambiguous: persisted status=running but live process missing "
                    "and no terminal artifact; classifying as stale (failed); "
                    "scheduler may retry after claim release",
                ),
                summary=(
                    "dry-run: would recover 0 child runs, fail 1 stale runs, "
                    "transition to running"
                ),
            )
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="running",
            failed_child_run_ids=("run-ambiguous",),
            conflict_resolutions=("agent-assisted recovery classified stale child for retry",),
            summary="drive recovered after agent-assisted classification; status=running",
        )

    def attempt_agent_assisted_drive_recovery(
        self,
        *,
        drive_id: str,
        reason: str = "",
        max_attempts: int = 2,
    ) -> _DriveLoopResult:
        self.calls.append("attempt_agent_assisted_drive_recovery")
        self.agent_assisted = True
        del reason, max_attempts
        return _DriveLoopResult(
            drive_id=drive_id,
            status="resolving",
            summary="resolver agent classified ambiguous recovery preview",
        )


class _FakeUnsafeActiveDriveOrchApp(_FakeActiveDriveOrchApp):
    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="running",
            failed_child_run_ids=("run-stale",),
            conflict_resolutions=(
                "child run run-stale: live process missing; classifying as stale",
            ),
            summary=(
                "dry-run: would recover 0 child runs, fail 1 stale runs, "
                "transition to running"
            ),
        )


class _FakeRetryLimitActiveDriveOrchApp(_FakeActiveDriveOrchApp):
    def recover_drive(self, *, drive_id: str, dry_run: bool = False) -> _DriveRecoverResult:
        self.calls.append("recover_drive_dry_run" if dry_run else "recover_drive_apply")
        return _DriveRecoverResult(
            drive_id=drive_id,
            status="running",
            failed_child_run_ids=("run-stale",),
            conflict_resolutions=(
                "operator required: retry limit reached for step.a after 3 stale child failures",
            ),
            summary=(
                "dry-run: would recover 0 child runs, fail 1 stale runs, "
                "transition to blocked_operator"
            ),
        )


class _FakeAutomationStoppedDriveApp(_FakeOrchApp):
    def run_drive_loop(self, drive_id: str) -> _DriveLoopResult:
        self.calls.append("run_drive_loop")
        self.drive_loop_id = drive_id
        return _DriveLoopResult(
            drive_id=drive_id,
            status="resolving",
            summary="automation stopped: barrier did not progress after 3 passes",
        )


class _FakeDriveAwareOrchApp(_FakeOrchApp):
    def has_active_drive_for_plan(self) -> str | None:
        return "drv-active"

    def resolve_latest_drive_id(self) -> str | None:
        return "drv-latest"


class _FakeBlockedCaseDriveApp(_FakeDriveAwareOrchApp):
    def drive_status(self, *, drive_id: str):
        self.calls.append("drive_status")
        return _DriveStatusResult(
            drive_id=drive_id,
            blocked_case_ids=("case-a", "case-b"),
        )


def test_orch_command_registration_matrix() -> None:
    orch_help = runner.invoke(app, ["orch", "--help"])
    inspect_help = runner.invoke(app, ["orch", "inspect", "--help"])
    case_help = runner.invoke(app, ["orch", "case", "--help"])
    control_help = runner.invoke(app, ["orch", "control", "--help"])
    config_help = runner.invoke(app, ["orch", "config", "--help"])
    migration_help = runner.invoke(app, ["orch", "migration", "--help"])

    assert orch_help.exit_code == 0
    assert inspect_help.exit_code == 0
    assert case_help.exit_code == 0
    assert control_help.exit_code == 0
    assert config_help.exit_code == 0
    assert migration_help.exit_code == 0

    for expected in (
        "run",
        "resume",
        "recover",
        "runs",
        "prune",
        "inspect",
        "case",
        "control",
        "config",
        "migration",
        "cutover-validate",
        "migration-advance-state",
    ):
        assert expected in orch_help.output
    for expected in ("status", "events", "logs", "artifacts", "actions"):
        assert expected in inspect_help.output
    for expected in ("list", "show", "respond"):
        assert expected in case_help.output
    for expected in ("pause", "unpause", "stop"):
        assert expected in control_help.output
    for expected in ("show", "validate", "tools"):
        assert expected in config_help.output
    for expected in ("validate-cutover", "advance-state"):
        assert expected in migration_help.output


def test_orch_commands_delegate_through_orch_app_boundary(monkeypatch) -> None:
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    assert runner.invoke(app, ["orch", "runs", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "inspect", "status", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "case", "list", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "control", "pause", "--step", "s1", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "config", "tools", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "migration", "validate-cutover", "--json"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "orch",
                "migration",
                "advance-state",
                "retired",
                "--legacy-run-id",
                "legacy-1",
                "--json",
            ],
        ).exit_code
        == 0
    )

    assert "runs" in fake.calls
    assert "inspect_status" in fake.calls
    assert "case_list" in fake.calls
    assert "control_pause" in fake.calls
    assert "config_tools" in fake.calls
    assert "cutover_validate" in fake.calls
    assert "migration_advance_state" in fake.calls


def test_orch_drive_runs_first_loop_after_start(monkeypatch) -> None:
    """`vectl orch drive --once` enters one drive loop, not only create DriveRecord."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == ["start_drive", "run_drive_loop"]
    assert fake.drive_loop_id == "drv-start"
    assert "looped" in result.output


def test_orch_drive_defaults_to_foreground_supervisor(monkeypatch) -> None:
    """`vectl orch drive` supervises foreground execution by default."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--json"])

    assert result.exit_code == 0
    assert fake.calls == ["start_drive", "run_drive_foreground"]
    assert fake.drive_loop_id == "drv-start"
    assert "foreground complete" in result.output


def test_orch_drive_attaches_to_active_drive_for_foreground_supervision(monkeypatch) -> None:
    """`vectl orch drive` resumes supervision when a same-plan drive is active."""
    fake = _FakeActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--json"])

    assert result.exit_code == 0
    assert fake.calls == ["start_drive", "recover_drive_dry_run", "run_drive_foreground"]
    assert fake.drive_loop_id == "drv-active"
    assert "foreground complete" in result.output


def test_orch_drive_once_runs_loop_for_active_drive(monkeypatch) -> None:
    """`vectl orch drive --once` advances an active drive instead of failing."""
    fake = _FakeActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == ["start_drive", "recover_drive_dry_run", "run_drive_loop"]
    assert fake.drive_loop_id == "drv-active"
    assert "looped" in result.output


def test_orch_drive_auto_applies_safe_recovery_for_active_drive(monkeypatch) -> None:
    """`vectl orch drive` auto-applies safe recovery before continuing."""
    fake = _FakeRecoveringActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == [
        "start_drive",
        "recover_drive_dry_run",
        "recover_drive_apply",
        "run_drive_loop",
    ]
    payload = json.loads(result.output)
    assert payload["auto_recovery"]["applied"] is True
    assert payload["reason_code"] == "ok"


def test_orch_drive_auto_retries_retryable_failed_child_run(monkeypatch) -> None:
    """Stale failed child runs are recovered so normal scheduling can retry."""
    fake = _FakeRetryableFailedChildDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == [
        "start_drive",
        "recover_drive_dry_run",
        "recover_drive_apply",
        "run_drive_loop",
    ]
    payload = json.loads(result.output)
    assert payload["auto_recovery"]["applied"] is True
    assert payload["auto_recovery"]["failed_child_run_ids"] == ["run-stale"]
    assert payload["human_required"] is False


def test_orch_drive_auto_recovers_orphaned_claim_only_preview(monkeypatch) -> None:
    """An orphaned claim release with no child work is safe to auto-apply."""
    fake = _FakeOrphanedClaimActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == [
        "start_drive",
        "recover_drive_dry_run",
        "recover_drive_apply",
        "run_drive_loop",
    ]
    payload = json.loads(result.output)
    assert payload["auto_recovery"]["applied"] is True
    assert payload["auto_recovery"]["failed_child_run_ids"] == []
    assert payload["human_required"] is False


def test_orch_drive_agent_assisted_recovery_before_operator_stop(monkeypatch) -> None:
    """Unsafe previews get one resolver-agent attempt before human handoff."""
    fake = _FakeAgentAssistedRecoveryDriveApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    assert fake.calls == [
        "start_drive",
        "recover_drive_dry_run",
        "attempt_agent_assisted_drive_recovery",
        "recover_drive_dry_run",
        "recover_drive_apply",
        "run_drive_loop",
    ]
    payload = json.loads(result.output)
    assert payload["auto_recovery"]["applied"] is True
    assert payload["auto_recovery"]["agent_assisted"]["attempted"] is True
    assert payload["auto_recovery"]["failed_child_run_ids"] == ["run-ambiguous"]
    assert payload["human_required"] is False


def test_orch_drive_stops_for_operator_recovery_when_preview_is_unsafe(monkeypatch) -> None:
    """Unsafe recovery previews stop with structured next-action guidance."""
    fake = _FakeUnsafeActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 4
    assert fake.calls == ["start_drive", "recover_drive_dry_run"]
    payload = json.loads(result.output)
    assert payload["human_required"] is True
    assert payload["reason_code"] == "drive_recovery_failed_children"
    assert "drive-recover --latest --dry-run" in payload["suggested_action"]
    assert payload["auto_recovery"]["applied"] is False


def test_orch_drive_reports_retry_limit_as_stop_reason(monkeypatch) -> None:
    """Retry-limit recovery previews get a specific compact stop reason."""
    fake = _FakeRetryLimitActiveDriveOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 4
    payload = json.loads(result.output)
    assert payload["human_required"] is True
    assert payload["reason_code"] == "retry_limit"
    assert payload["stop_reason"].startswith("retry_limit:")


def test_orch_drive_json_includes_compact_stop_reason(monkeypatch) -> None:
    """Drive JSON results explain why automation stopped."""
    fake = _FakeAutomationStoppedDriveApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--once", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["human_required"] is True
    assert payload["reason_code"] == "automation_stalled"
    assert payload["stop_reason"].startswith("automation_stalled:")


def test_orch_drive_human_mode_emits_progress_events(monkeypatch) -> None:
    """Human foreground mode prints sparse progress events instead of raw JSON."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--progress", "plain"])

    assert result.exit_code == 0
    assert "Starting drive drv-start" in result.output
    assert "COMPLETED" in result.output
    assert fake.progress_mode == "plain"


def test_orch_drive_quiet_human_mode_prints_final_result_only(monkeypatch) -> None:
    """--quiet suppresses foreground progress in human mode."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--quiet"])

    assert result.exit_code == 0
    assert "Starting drive" not in result.output
    assert "drive_id=drv-start" in result.output


def test_orch_drive_jsonl_emits_progress_event_stream(monkeypatch) -> None:
    """--jsonl streams progress events rather than a final compact object."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "drive", "--jsonl"])

    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    payloads = [cli.json.loads(line) for line in lines]
    assert [payload["type"] for payload in payloads] == ["drive_started", "drive_terminal"]
    assert payloads[-1]["status"] == "completed"


def test_orch_case_list_latest_reads_drive_blocked_cases_without_attribute_error(
    monkeypatch,
) -> None:
    """Drive-scoped case-list consumes DriveStatusResult.blocked_case_ids."""
    fake = _FakeBlockedCaseDriveApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "case-list", "--latest", "--json"])

    assert result.exit_code == 0
    assert "case-a" in result.output
    assert "case-b" in result.output
    assert fake.calls == ["drive_status"]


def test_orch_case_list_latest_status_filter_respects_open_only_drive_cases(
    monkeypatch,
) -> None:
    """Drive status exposes open blocked cases; non-open filters return no rows."""
    fake = _FakeBlockedCaseDriveApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(
        app, ["orch", "case-list", "--latest", "--status", "resolved", "--json"]
    )

    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_orch_output_mode_conflict_is_rejected() -> None:
    result = runner.invoke(app, ["orch", "runs", "--json", "--output", "jsonl"])
    assert result.exit_code == 1
    assert "Conflicting output flags" in result.output


def test_orch_resume_requires_selector() -> None:
    result = runner.invoke(app, ["orch", "resume"])
    assert result.exit_code == 3
    assert "explicit selector" in result.output


def test_orch_inspect_actions_requires_selector() -> None:
    result = runner.invoke(app, ["orch", "inspect", "actions"])
    assert result.exit_code == 1
    assert "Run selector required" in result.output


def test_orch_inspect_actions_requires_selector_even_with_active_drive(monkeypatch) -> None:
    fake = _FakeDriveAwareOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "inspect", "actions"])

    assert result.exit_code == 1
    assert "Run selector required" in result.output
    assert "inspect_drive_actions" not in fake.calls


def test_orch_status_latest_falls_back_to_legacy_run_scope_when_no_drives(monkeypatch) -> None:
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    result = runner.invoke(app, ["orch", "status", "--latest"])

    assert result.exit_code == 0
    assert "status=running" in result.output
    assert fake.calls == ["runs", "runs", "inspect_status"]


def test_orch_control_and_config_flags_propagate_to_orch_app(monkeypatch) -> None:
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    pause_result = runner.invoke(
        app,
        ["orch", "control", "pause", "--step", "s1", "--reason", "maintenance"],
    )
    unpause_result = runner.invoke(
        app,
        ["orch", "control", "unpause", "--step", "s1", "--reason", "resume"],
    )
    stop_result = runner.invoke(
        app,
        ["orch", "control", "stop", "--reason", "halt", "--force"],
    )
    config_result = runner.invoke(app, ["orch", "config", "show", "--effective"])

    assert pause_result.exit_code == 0
    assert unpause_result.exit_code == 0
    assert stop_result.exit_code == 0
    assert config_result.exit_code == 0

    assert fake.pause_reason == "maintenance"
    assert fake.unpause_reason == "resume"
    assert fake.stop_run_id is None
    assert fake.stop_reason == "halt"
    assert fake.stop_force is True
    assert fake.config_show_effective is True


def test_orch_control_stop_forwards_explicit_run_id(monkeypatch) -> None:
    """Stop command with --run uses legacy run-scoped path."""
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)
    monkeypatch.setattr("vectl.cli._step_id_for_run", lambda app_runtime, run_id: "s1")

    result = runner.invoke(
        app, ["orch", "control", "stop", "--run", "r1", "--reason", "halt", "--force"]
    )

    assert result.exit_code == 0
    assert fake.stop_run_id == "r1"
    assert fake.stop_reason == "halt"
    assert fake.stop_force is True


def test_orch_explicit_exit_code_mapping_for_not_found_and_recovery(monkeypatch) -> None:
    fake = _FakeOrchApp()

    def _resume_not_found(*, run_id: str) -> _Result:
        del run_id
        return _Result(success=False, message="Run not found: r-missing")

    def _resume_recovery(*, run_id: str) -> _Result:
        del run_id
        return _Result(
            success=False,
            message="Resume refused: frozen config snapshot missing for authoritative run r1",
        )

    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    monkeypatch.setattr(fake, "resume", _resume_not_found)
    not_found = runner.invoke(app, ["orch", "resume", "r-missing"])
    assert not_found.exit_code == 2
    assert "Run not found" in not_found.output

    monkeypatch.setattr(fake, "resume", _resume_recovery)
    recovery = runner.invoke(app, ["orch", "resume", "r1"])
    assert recovery.exit_code == 4
    assert "Resume refused" in recovery.output


def test_orch_internal_error_maps_to_exit_code_5(monkeypatch) -> None:
    def _boom(plan):
        del plan
        raise RuntimeError("boom")

    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", _boom)

    result = runner.invoke(app, ["orch", "config", "show", "--effective"])
    assert result.exit_code == 5
    assert "Internal orchestration error" in result.output

    with pytest.raises(typer.Exit) as exc_info:
        cli._build_orchestration_runtime_app_or_die(plan=None)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "boom"
