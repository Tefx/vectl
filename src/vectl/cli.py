"""Top-level Typer command registration for vectl."""

from __future__ import annotations

import json

import typer
from returns.result import Result, Success
from rich.console import Console

from vectl import __version__
from vectl.cli_agents_md import agents_md_cmd, init
from vectl.cli_orchestration import (
    _build_orchestration_runtime_app,
    orch_case_list,
    orch_case_respond,
    orch_case_show,
    orch_config_show,
    orch_config_tools,
    orch_config_validate,
    orch_control_pause,
    orch_control_stop,
    orch_control_unpause,
    orch_drive,
    orch_drive_recover,
    orch_drive_resume,
    orch_drive_runs,
    orch_drive_status,
    orch_inspect_actions,
    orch_inspect_artifacts,
    orch_inspect_events,
    orch_inspect_logs,
    orch_inspect_status,
    orch_migration_advance_state,
    orch_migration_validate_cutover,
    orch_prune,
    orch_recover,
    orch_resume,
    orch_run,
    orch_runs,
)
from vectl.cli_orchestration_runtime_helpers import (
    _build_orchestration_runtime_app_or_die,
    _step_id_for_run,
)
from vectl.cli_plan import (
    add_phase_cmd,
    add_step_cmd,
    add_steps_cmd,
    cancel,
    check_inventory_cmd,
    check_cmd,
    checkpoint,
    claim,
    clipboard_clear_cmd,
    clipboard_read_cmd,
    clipboard_write_cmd,
    complete,
    complete_phase_cmd,
    dag,
    dashboard,
    defer,
    edit_phase_cmd,
    edit_plan_cmd,
    edit_step_cmd,
    gate_check,
    guide_cmd,
    migrate_cmd,
    migrate_step_id_cmd,
    mine,
    move_step_cmd,
    next_cmd,
    recalc_lock,
    recover,
    reject,
    remove_step_cmd,
    repair_claims_cmd,
    review,
    search,
    show,
    skip,
    skip_phase_cmd,
    status,
    unlock,
    validate,
)
from vectl.cli_plan_common import _get_next_steps_with_phase
from vectl.cli_render import _load, _save_plan, diff_cmd, log_cmd, render
from vectl.io import _resolve_git_dir
from vectl.merge_driver import merge_plans
from vectl.plan_path import is_linked_worktree, resolve_plan_path
from vectl.semantics import is_step_locked
from vectl.cli_orchestration_runtime_helpers import _enrich_drive_scope_result

console = Console(stderr=True)
out = Console()

app = typer.Typer(
    name="vectl",
    help="Agentic Implementation Plan Manager.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
repair_app = typer.Typer(help="Operator recovery commands.")
app.add_typer(repair_app, name="repair")

orch_app = typer.Typer(
    help=(
        "Orchestration operator commands: run, resume, recover, inspect, "
        "case, control, config, migration."
    ),
)
app.add_typer(orch_app, name="orch")
orch_inspect_app = typer.Typer(help="Inspect orchestration runtime surfaces.")
orch_case_app = typer.Typer(help="Case inspection and operator response surfaces.")
orch_control_app = typer.Typer(help="Operator control actions (pause/unpause/stop).")
orch_config_app = typer.Typer(help="Orchestration config surfaces.")
orch_migration_app = typer.Typer(help="Legacy migration and cutover surfaces.")
orch_app.add_typer(orch_inspect_app, name="inspect")
orch_app.add_typer(orch_case_app, name="case")
orch_app.add_typer(orch_control_app, name="control")
orch_app.add_typer(orch_config_app, name="config")
orch_app.add_typer(orch_migration_app, name="migration")


def _version_callback(value: bool) -> Result[None, str]:
    if value:
        out.print(f"vectl {__version__}")
        raise typer.Exit()
    return Success(None)


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> Result[None, str]:
    """vectl: Agentic Implementation Plan Manager."""
    return Success(None)


@app.command()
def mcp() -> Result[None, str]:
    """Start the MCP server (stdio mode)."""

    from vectl.mcp_server import mcp

    mcp.run()
    return Success(None)


@app.command("merge-driver", hidden=True)
def merge_driver_cmd(
    base: str = typer.Argument(..., help="Git merge-base file path (%O)."),
    ours: str = typer.Argument(..., help="Git ours file path (%A)."),
    theirs: str = typer.Argument(..., help="Git theirs file path (%B)."),
) -> Result[None, str]:
    """Git merge-driver entrypoint for plan.yaml merges."""

    raise typer.Exit(merge_plans(base, ours, theirs))


orch_app.command("run")(orch_run)
orch_app.command("resume")(orch_resume)
orch_app.command("recover")(orch_recover)
orch_app.command("runs")(orch_runs)
orch_app.command("prune")(orch_prune)
orch_migration_app.command("validate-cutover")(orch_migration_validate_cutover)
orch_app.command("cutover-validate")(orch_migration_validate_cutover)
orch_migration_app.command("advance-state")(orch_migration_advance_state)
orch_app.command("migration-advance-state")(orch_migration_advance_state)
orch_inspect_app.command("status")(orch_inspect_status)
orch_app.command("status")(orch_inspect_status)
orch_inspect_app.command("events")(orch_inspect_events)
orch_app.command("events")(orch_inspect_events)
orch_inspect_app.command("logs")(orch_inspect_logs)
orch_app.command("logs")(orch_inspect_logs)
orch_inspect_app.command("artifacts")(orch_inspect_artifacts)
orch_app.command("artifacts")(orch_inspect_artifacts)
orch_inspect_app.command("actions")(orch_inspect_actions)
orch_app.command("actions")(orch_inspect_actions)
orch_case_app.command("list")(orch_case_list)
orch_app.command("case-list")(orch_case_list)
orch_case_app.command("show")(orch_case_show)
orch_app.command("case-show")(orch_case_show)
orch_case_app.command("respond")(orch_case_respond)
orch_app.command("case-respond")(orch_case_respond)
orch_control_app.command("pause")(orch_control_pause)
orch_app.command("pause")(orch_control_pause)
orch_control_app.command("unpause")(orch_control_unpause)
orch_app.command("unpause")(orch_control_unpause)
orch_control_app.command("stop")(orch_control_stop)
orch_app.command("stop")(orch_control_stop)
orch_config_app.command("show")(orch_config_show)
orch_app.command("config-show")(orch_config_show)
orch_config_app.command("validate")(orch_config_validate)
orch_app.command("config-validate")(orch_config_validate)
orch_config_app.command("tools")(orch_config_tools)
orch_app.command("config-tools")(orch_config_tools)
orch_app.command("drive")(orch_drive)
orch_app.command("drive-status")(orch_drive_status)
orch_app.command("drive-runs")(orch_drive_runs)
orch_app.command("drive-resume")(orch_drive_resume)
orch_app.command("drive-recover")(orch_drive_recover)
app.command()(render)
app.command("diff")(diff_cmd)
app.command("log")(log_cmd)
app.command("agents-md")(agents_md_cmd)
app.command()(init)
app.command("next")(next_cmd)
app.command()(status)
app.command()(show)
app.command("guide")(guide_cmd)
app.command()(dag)
app.command()(claim)
app.command()(complete)
app.command("complete-phase")(complete_phase_cmd)
app.command()(defer)
app.command()(reject)
app.command()(skip)
app.command()(cancel)
app.command("skip-phase")(skip_phase_cmd)
app.command("check")(check_cmd)
app.command("check-inventory")(check_inventory_cmd)
app.command()(validate)
app.command("migrate")(migrate_cmd)
app.command("migrate-step-id")(migrate_step_id_cmd)
app.command()(recover)
app.command()(checkpoint)
app.command("add-step")(add_step_cmd)
app.command("add-phase")(add_phase_cmd)
app.command("edit-plan")(edit_plan_cmd)
app.command("edit-step")(edit_step_cmd)
app.command("edit-phase")(edit_phase_cmd)
app.command("remove-step")(remove_step_cmd)
app.command("move-step")(move_step_cmd)
app.command()(unlock)
app.command("recalc-lock")(recalc_lock)
repair_app.command("claims")(repair_claims_cmd)
app.command("add-steps")(add_steps_cmd)
app.command()(search)
app.command()(mine)
app.command()(review)
app.command("gate-check")(gate_check)
app.command("clipboard-write")(clipboard_write_cmd)
app.command("clipboard-read")(clipboard_read_cmd)
app.command("clipboard-clear")(clipboard_clear_cmd)
app.command()(dashboard)


if __name__ == "__main__":
    app()
