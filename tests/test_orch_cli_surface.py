"""CLI surface tests for ``vectl orch`` command tree wiring.

Authority:
    orch_operator_control_surface.impl_vectl_orch_cli_surface step contract
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.1-7.5
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import typer
from typer.testing import CliRunner

import vectl.cli as cli
from vectl.cli import app

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
        self, drive_id: str, *, poll_interval_seconds: float = 2.0
    ) -> _DriveLoopResult:
        self.calls.append("run_drive_foreground")
        self.drive_loop_id = drive_id
        self.poll_interval_seconds = poll_interval_seconds
        return _DriveLoopResult(drive_id=drive_id, summary="foreground complete")

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
