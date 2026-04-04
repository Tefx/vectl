"""CLI surface tests for ``vectl orch`` command tree wiring.

Authority:
    orch_operator_control_surface.impl_vectl_orch_cli_surface step contract
    docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.1-7.5
"""

from __future__ import annotations

from dataclasses import dataclass

from typer.testing import CliRunner

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


class _FakeOrchApp:
    def __init__(self) -> None:
        self.calls: list[str] = []

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

    def control_pause(self, *, step_id: str | None = None):
        self.calls.append("control_pause")
        del step_id
        return _Result(success=True, message="paused")

    def control_unpause(self, *, step_id: str | None = None):
        self.calls.append("control_unpause")
        del step_id
        return _Result(success=True, message="unpaused")

    def control_stop(self, *, reason: str | None = None):
        self.calls.append("control_stop")
        del reason
        return _Result(success=True, message="stopped")

    def config_show(self) -> _ConfigResult:
        self.calls.append("config_show")
        return _ConfigResult(show_output="show")

    def config_validate(self) -> _ConfigResult:
        self.calls.append("config_validate")
        return _ConfigResult(show_output="valid", validation_passed=True)

    def config_tools(self) -> _ConfigResult:
        self.calls.append("config_tools")
        return _ConfigResult(show_output="tools", tools=("core", "orchestration"))


def test_orch_command_registration_matrix() -> None:
    orch_help = runner.invoke(app, ["orch", "--help"])
    inspect_help = runner.invoke(app, ["orch", "inspect", "--help"])
    case_help = runner.invoke(app, ["orch", "case", "--help"])
    control_help = runner.invoke(app, ["orch", "control", "--help"])
    config_help = runner.invoke(app, ["orch", "config", "--help"])

    assert orch_help.exit_code == 0
    assert inspect_help.exit_code == 0
    assert case_help.exit_code == 0
    assert control_help.exit_code == 0
    assert config_help.exit_code == 0

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


def test_orch_commands_delegate_through_orch_app_boundary(monkeypatch) -> None:
    fake = _FakeOrchApp()
    monkeypatch.setattr("vectl.cli._build_orchestration_runtime_app", lambda plan: fake)

    assert runner.invoke(app, ["orch", "runs", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "inspect", "status", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "case", "list", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "control", "pause", "--step", "s1", "--json"]).exit_code == 0
    assert runner.invoke(app, ["orch", "config", "tools", "--json"]).exit_code == 0

    assert "runs" in fake.calls
    assert "inspect_status" in fake.calls
    assert "case_list" in fake.calls
    assert "control_pause" in fake.calls
    assert "config_tools" in fake.calls


def test_orch_output_mode_conflict_is_rejected() -> None:
    result = runner.invoke(app, ["orch", "runs", "--json", "--output", "jsonl"])
    assert result.exit_code == 1
    assert "Conflicting output flags" in result.output


def test_orch_resume_requires_selector() -> None:
    result = runner.invoke(app, ["orch", "resume"])
    assert result.exit_code == 1
    assert "Run selector required" in result.output


def test_orch_inspect_actions_requires_selector() -> None:
    result = runner.invoke(app, ["orch", "inspect", "actions"])
    assert result.exit_code == 1
    assert "Run selector required" in result.output
