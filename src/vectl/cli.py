"""CLI interface using Typer + Rich.

Spec authority: tools/vectl/plan.yaml, phases cli_read + cli_write.
"""

from __future__ import annotations

import enum
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from vectl import __version__
from vectl.agents_md import AgentsTarget, upsert_agents_md
from vectl.claims import get_current_branch, repair_claims
from vectl.core import (
    RecoverResult,
    add_phase,
    add_step,
    add_steps_bulk,
    apply_duplicate_step_id_migration,
    build_duplicate_step_id_migration_dry_run,
    build_duplicate_step_id_migration_evidence,
    claim_step,
    clipboard_clear,
    clipboard_write,
    complete_phase,
    complete_step,
    defer_step,
    diff_plans,
    edit_phase,
    edit_step,
    format_lock_changes,
    get_claimed_steps,
    get_next_steps,
    move_step,
    recalc_lock_status,
    reject_step,
    remove_step,
    render_plan,
    require_unambiguous_target_step_id,
    review_plan,
    search_plan,
    skip_phase,
    skip_step,
    unlock_phase,
    update_checklist,
    validate_plan,
)
from vectl.core import (
    gate_check as core_gate_check,
)
from vectl.dashboard import generate_dashboard
from vectl.duplicate_step_id_format import (
    format_duplicate_step_id_diagnostics,
    format_duplicate_step_id_recommendation,
    get_duplicate_step_id_recommendation,
)
from vectl.guide import GUIDE_ALL as _GUIDE_ALL
from vectl.guide import GUIDE_TOPICS as _GUIDE_TOPICS
from vectl.io import (
    _resolve_git_dir,
    load_plan_definition,
    save_plan,
)
from vectl.lifecycle import ClaimConflictError
from vectl.merge_driver import merge_plans
from vectl.migration import migrate_from_split_state, resolve_state_path
from vectl.models import (
    AffinityMode,
    CASConflictError,
    PhaseStatus,
    Plan,
    PlanError,
    PlanIOError,
    SkipReason,
    StepStatus,
    format_step_selector,
)
from vectl.orch_app import ControlResult
from vectl.plan_helpers import get_next_steps_with_phase
from vectl.orchestration.recovery import LegacyRunStatus
from vectl.plan_path import (
    is_linked_worktree,
    resolve_claims_path,
    resolve_plan_path,
)
from vectl.semantics import is_step_locked

console = Console(stderr=True)
out = Console()

# Backward-compatible alias for characterization tests and older imports.
_get_next_steps_with_phase = get_next_steps_with_phase

# ---------------------------------------------------------------------------
# Status display helpers
# ---------------------------------------------------------------------------

_STEP_STATUS_STYLE = {
    StepStatus.PENDING: ("○", "dim"),
    StepStatus.CLAIMED: ("◉", "yellow"),
    StepStatus.DONE: ("✓", "green"),
    StepStatus.SKIPPED: ("⊘", "dim"),
    StepStatus.REJECTED: ("✗", "red bold"),
}

_PHASE_STATUS_STYLE = {
    PhaseStatus.LOCKED: ("🔒", "dim"),
    PhaseStatus.PENDING: ("○", ""),
    PhaseStatus.IN_PROGRESS: ("▶", "yellow"),
    PhaseStatus.DONE: ("✓", "green"),
}


def _step_icon(status: StepStatus, locked: bool = False, verify: str | None = None) -> Text:
    if locked and status == StepStatus.PENDING:
        return Text("🔒 locked", style="dim")
    # Special rendering for expected_red completed steps
    if status == StepStatus.DONE and verify == "expected_red":
        return Text("✓ gap reproduced", style="cyan")
    icon, style = _STEP_STATUS_STYLE[status]
    return Text(f"{icon} {status.value}", style=style)


def _phase_icon(status: PhaseStatus) -> Text:
    icon, style = _PHASE_STATUS_STYLE[status]
    return Text(f"{icon} {status.value}", style=style)


def _one_line_summary(description: str, max_len: int = 72) -> str:
    """Extract first meaningful line from description, truncated.

    Skips empty lines and checklist markers. Returns empty string if
    description is empty or only whitespace.
    """
    for line in description.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip checklist items as summaries — they're usually not informative
        if stripped.startswith("- ["):
            continue
        if len(stripped) > max_len:
            return stripped[: max_len - 1] + "…"
        return stripped
    return ""


def _print_duplicate_id_warning_block(p: Plan) -> None:
    """Print duplicate step-ID diagnostics for read-only surfaces."""
    lines = format_duplicate_step_id_diagnostics(p)
    if not lines:
        return

    out.print("[yellow]⚠ Duplicate step-ID diagnostics:[/]")
    for line in lines:
        out.print(f"  [yellow]{_esc(line)}[/]")


def _print_duplicate_id_recommendation_for_step(p: Plan, step_id: str) -> None:
    """Print duplicate-ID repair recommendation for an ambiguous step target."""
    recommendation = get_duplicate_step_id_recommendation(p, step_id)
    if recommendation is None:
        return

    out.print(
        "[yellow]⚠ Ambiguous step target: duplicate ID detected across phases.[/]",
    )
    for line in format_duplicate_step_id_recommendation(recommendation):
        label, _, rest = line.partition(": ")
        if label.startswith("resolution."):
            out.print(f"  [dim]{label}:[/] {_esc(rest)}")
        else:
            out.print(f"  [dim]{_esc(line)}[/]")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _die(msg: str, code: int = 1, *, cause: Exception | None = None) -> NoReturn:
    console.print(f"[red bold]Error:[/] {msg}")
    if cause is None:
        raise typer.Exit(code)
    raise typer.Exit(code) from cause


def _check_not_linked_worktree(plan: Path | None = None) -> None:
    """Guard: block mutations in linked worktrees.

    Raises typer.Exit if running in a linked worktree without explicit plan path.
    Explicit path can be provided via:
      - VECTL_PLAN_PATH environment variable
      - --plan CLI option
    """
    # Escape hatch: explicit path via env var
    if os.environ.get("VECTL_PLAN_PATH"):
        return

    # Escape hatch: explicit path via --plan option
    if plan is not None:
        return

    is_linked, main_root = is_linked_worktree()
    if is_linked:
        if main_root is not None:
            _die(
                f"Mutate blocked: running in a linked worktree. "
                f"Plan mutations must be performed in the main worktree at {main_root}. "
                f"Override: set VECTL_PLAN_PATH={main_root}/plan.yaml"
            )
        _die(
            "Mutate blocked: linked worktree detected but main worktree root "
            "could not be resolved from git output. "
            "Set VECTL_PLAN_PATH to the main worktree plan.yaml and retry."
        )


def _load(plan_path: Path | None) -> tuple[Plan, str, Path]:
    """Load plan.yaml and return plan with CAS hash.

    Source: docs/ADR-unified-state.md migration posture.
    Normal load path reads unified inline state from plan.yaml only.
    """

    target = resolve_plan_path(plan_path)

    try:
        plan_def, def_hash = load_plan_definition(target)
    except PlanIOError as e:
        _die(str(e))
        raise  # unreachable, for type checker

    return plan_def, def_hash, target


def _save_plan(plan: Plan, plan_path: Path, expected_def_hash: str, msg: str) -> None:
    """Save plan.yaml with CAS semantics and lock recalculation."""

    changed_ids = recalc_lock_status(plan)

    # Source: claim-consistency-recovery.integration-verify-fix Issue 1.
    # When --plan targets a different repo/worktree, write should still succeed
    # quietly without misleading git pathspec/autosave warnings.
    commit_message = msg if _should_autosave_commit(plan_path) else None

    try:
        save_plan(
            plan,
            plan_path,
            expected_hash=expected_def_hash,
            commit_message=commit_message,
        )
    except CASConflictError:
        _die(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl status` or `vectl show`, then retry your mutation."
        )

    notice = format_lock_changes(changed_ids, plan)
    if notice:
        print(notice)


def _git_toplevel_for(path: Path) -> Path | None:
    """Return git toplevel for a path, or None when unavailable."""
    import subprocess as sp

    try:
        result = sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, sp.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    resolved = result.stdout.strip()
    if not resolved:
        return None

    return Path(resolved).resolve()


def _should_autosave_commit(plan_path: Path) -> bool:
    """Allow autosave commit only when plan is in current repo/worktree."""
    cwd_repo = _git_toplevel_for(Path.cwd())
    plan_repo = _git_toplevel_for(plan_path.parent.resolve())
    return cwd_repo is not None and plan_repo is not None and cwd_repo == plan_repo


def _is_claim_consistency_scoped_gate(phase_id: str) -> bool:
    """Return True when scoped verification note should be shown."""
    return phase_id == "claim-consistency-recovery"


def _check_claim_mismatch(p: Plan, plan_path: Path) -> tuple[bool, list[str], list[str]]:
    """Check for claims.json vs plan.yaml mismatch.

    Returns:
        Tuple of (has_mismatch, ghost_claims, stale_plan_claims)
        - ghost_claims: claims entries with no corresponding claimed step in plan
        - stale_plan_claims: claimed steps in plan with no entry in claims.json
    """
    from vectl.claims import get_current_branch, load_claims, resolve_claims_path

    claims_path = resolve_claims_path(plan_path)
    claims = load_claims(claims_path)
    branch = get_current_branch()

    # Build current branch's claimed steps from plan
    plan_claimed: set[str] = set()
    for phase in p.phases:
        for step in phase.steps:
            if step.status == StepStatus.CLAIMED and step.claimed_by:
                plan_claimed.add(f"{branch}:{step.id}")

    # Find ghost claims (in claims but not claimed in plan)
    ghost_claims = [
        key for key in claims if key.startswith(f"{branch}:") and key not in plan_claimed
    ]

    # Find stale plan claims (claimed in plan but not in claims)
    stale_plan_claims = [key.split(":", 1)[1] for key in plan_claimed if key not in claims]

    has_mismatch = bool(ghost_claims or stale_plan_claims)
    return has_mismatch, ghost_claims, stale_plan_claims


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        out.print(f"vectl {__version__}")
        raise typer.Exit()


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

PlanOption = typer.Option(
    None,
    "--plan",
    "-p",
    help="Path to plan YAML file. Defaults to auto-discovery (walk-up). (env: VECTL_PLAN_PATH)",
)

# B008-compliant singleton options (defined at module level to avoid function call in defaults)
RenderOutputOption = typer.Option(None, "--output", "-o", help="Write to file instead of stdout.")
AgentsMdDirOption = typer.Option(
    Path("."),
    "--dir",
    help="Directory containing AGENTS.md/CLAUDE.md to update.",
)
AgentsMdTargetOption = typer.Option(
    "auto",
    "--target",
    help="Target file: auto (detect .claude/), agents (AGENTS.md), claude (CLAUDE.md).",
)
InitTargetOption = typer.Option(
    "auto",
    "--target",
    help="Target file for agent instructions: auto, agents, claude.",
)
EvidenceTemplateFileOption = typer.Option(
    None,
    "--evidence-template-file",
    help="Read the completion evidence template from a file.",
)
ProjectGuidanceFileOption = typer.Option(
    None,
    "--project-guidance-file",
    help="Read project-level guidance from a file.",
)
ContextFileOption = typer.Option(
    None,
    "--context-file",
    help="Read plan context from a file.",
)
DashboardOutputOption = typer.Option(
    Path("plan-dashboard.html"),
    "--out",
    "-o",
    help="Output file path for the HTML dashboard.",
)


# ---------------------------------------------------------------------------
# cli.1: init + --version
# ---------------------------------------------------------------------------


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
) -> None:
    """vectl: Agentic Implementation Plan Manager."""


@app.command()
def mcp() -> None:
    """Start the MCP server (stdio mode)."""
    from vectl.mcp_server import mcp

    mcp.run()


@app.command("merge-driver", hidden=True)
def merge_driver_cmd(
    base: str = typer.Argument(..., help="Git merge-base file path (%O)."),
    ours: str = typer.Argument(..., help="Git ours file path (%A)."),
    theirs: str = typer.Argument(..., help="Git theirs file path (%B)."),
) -> None:
    """Git merge-driver entrypoint for plan.yaml merges."""
    raise typer.Exit(merge_plans(base, ours, theirs))


# ---------------------------------------------------------------------------
# vectl orch: orchestration operator commands
# Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 6
# ---------------------------------------------------------------------------



from vectl.cli_orchestration import _build_orchestration_runtime_app, _build_orchestration_runtime_app_or_die, _enrich_drive_scope_result, _step_id_for_run, orch_case_list, orch_case_respond, orch_case_show, orch_config_show, orch_config_tools, orch_config_validate, orch_control_pause, orch_control_stop, orch_control_unpause, orch_drive, orch_drive_recover, orch_drive_resume, orch_drive_runs, orch_drive_status, orch_inspect_actions, orch_inspect_artifacts, orch_inspect_events, orch_inspect_logs, orch_inspect_status, orch_migration_advance_state, orch_migration_validate_cutover, orch_prune, orch_recover, orch_resume, orch_run, orch_runs
from vectl.cli_render import diff_cmd, log_cmd, render
from vectl.cli_agents_md import agents_md_cmd, init
from vectl.cli_plan import add_phase_cmd, add_step_cmd, add_steps_cmd, cancel, check_cmd, checkpoint, claim, clipboard_clear_cmd, clipboard_read_cmd, clipboard_write_cmd, complete, complete_phase_cmd, dag, dashboard, defer, edit_phase_cmd, edit_plan_cmd, edit_step_cmd, gate_check, guide_cmd, migrate_cmd, migrate_step_id_cmd, mine, move_step_cmd, next_cmd, recalc_lock, recover, reject, remove_step_cmd, repair_claims_cmd, review, search, show, skip, skip_phase_cmd, status, unlock, validate

# Registered command implementations moved to behavior modules.
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
