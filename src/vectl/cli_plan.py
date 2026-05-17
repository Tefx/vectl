# @invar:allow file_size: legacy Typer plan-management surface must remain in this module to preserve public command registration.
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
from typing import Any, Literal, cast

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from vectl import __version__
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
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    PlanIOError,
    SkipReason,
    Step,
    StepStatus,
    format_step_selector,
)
from vectl.orch_app import ControlResult
from vectl.orchestration.recovery import LegacyRunStatus
from vectl.plan_path import (
    is_linked_worktree,
    resolve_claims_path,
    resolve_plan_path,
)
from vectl.semantics import _get_active_phase_ids, is_step_locked

console = Console(stderr=True)
out = Console()
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


# @shell_orchestration: Rich rendering helper belongs to CLI output surface.
def _step_icon(status: StepStatus, locked: bool = False, verify: str | None = None):
    if locked and status == StepStatus.PENDING:
        return Text("🔒 locked", style="dim")
    # Special rendering for expected_red completed steps
    if status == StepStatus.DONE and verify == "expected_red":
        return Text("✓ gap reproduced", style="cyan")
    icon, style = _STEP_STATUS_STYLE[status]
    return Text(f"{icon} {status.value}", style=style)


# @shell_orchestration: Rich rendering helper belongs to CLI output surface.
def _phase_icon(status: PhaseStatus):
    icon, style = _PHASE_STATUS_STYLE[status]
    return Text(f"{icon} {status.value}", style=style)


# @shell_orchestration: Text formatting helper belongs to CLI output surface.
# @shell_complexity: checklist skipping and truncation are intentionally kept together for CLI summary compatibility.
def _one_line_summary(description: str, max_len: int = 72):
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
        out.print(f"  [yellow]{line}[/]")


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
# @shell_orchestration: Phase-aware claimability helper belongs to CLI output flow.
# @shell_complexity: phase-aware claimability mirrors existing get_next_steps ordering without changing CLI semantics.
def _get_next_steps_with_phase(plan: Plan, agent: str | None = None):
    """Get next steps with their containing phase.

    Returns list of (phase, step) tuples to correctly track phase membership
    for duplicate step IDs across different phases.

    Args:
        plan: The plan to query.
        agent: If provided, prioritize steps whose `agent` field matches.
    """
    active_phase_ids = _get_active_phase_ids(plan)
    result: list[tuple[Phase, Step]] = []

    for phase in plan.phases:
        if phase.id not in active_phase_ids:
            continue
        done_step_ids = {
            s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)
        }
        for step in phase.steps:
            if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
                continue
            # All deps satisfied?
            if all(dep in done_step_ids for dep in step.depends_on):
                result.append((phase, step))

    # Sort with same priority as get_next_steps
    def _sort_key(item: tuple[Phase, Step]) -> tuple[int, int, str]:
        _, s = item
        # Priority 0: rejected (needs rework)
        status_rank = 0 if s.status == StepStatus.REJECTED else 1
        # Agent affinity: 0 = matches, 1 = unassigned, 2 = different agent
        if agent is None or s.agent is None:
            agent_rank = 1
        elif s.agent == agent:
            agent_rank = 0
        else:
            agent_rank = 2
        return (status_rank, agent_rank, s.id)

    result.sort(key=_sort_key)
    return result


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _die(msg: str, code: int = 1, *, cause: Exception | None = None):
    console.print(f"[red bold]Error:[/] {msg}")
    if cause is None:
        raise typer.Exit(code)
    raise typer.Exit(code) from cause


# @shell_complexity: guard preserves explicit env/CLI escape hatches and diagnostic branches.
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

    root_cli = sys.modules.get("vectl.cli")
    linked_probe = getattr(root_cli, "is_linked_worktree", is_linked_worktree)
    is_linked, main_root = linked_probe()
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


# @shell_orchestration: Plan loader is a CLI boundary helper that delegates actual file I/O.
def _load(plan_path: Path | None):
    """Load plan.yaml and return plan with CAS hash.

    Source: docs/ADR-unified-state.md migration posture.
    Normal load path reads unified inline state from plan.yaml only.
    """

    root_cli = sys.modules.get("vectl.cli")
    resolver = getattr(root_cli, "resolve_plan_path", resolve_plan_path)
    target = resolver(plan_path)

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


def _git_toplevel_for(path: Path):
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


def _should_autosave_commit(plan_path: Path):
    """Allow autosave commit only when plan is in current repo/worktree."""
    cwd_repo = _git_toplevel_for(Path.cwd())
    plan_repo = _git_toplevel_for(plan_path.parent.resolve())
    return cwd_repo is not None and plan_repo is not None and cwd_repo == plan_repo


def _is_claim_consistency_scoped_gate(phase_id: str):
    """Return True when scoped verification note should be shown."""
    return phase_id == "claim-consistency-recovery"


# @shell_orchestration: Claims mismatch helper belongs to status/show CLI diagnostics.
def _check_claim_mismatch(p: Plan, plan_path: Path):
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


# @shell_complexity: Typer command coordinates strategy panel, duplicate phase display, and guidance text in one output surface.
def next_cmd(
    detail: bool = typer.Option(False, "--detail", help="Show full descriptions inline."),
    limit: int = typer.Option(3, "--limit", "-n", help="Max steps to show (default: 3)."),
    all_steps: bool = typer.Option(False, "--all", help="Show all available steps."),
    agent: str | None = typer.Option(
        None, "--agent", "-a", help="Prioritize steps suggested for this agent."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Show claimable steps (what to work on next)."""
    p, _, _ = _load(plan)

    # Show strategy context if present
    if p.context.strip():
        out.print(Panel(p.context.strip(), title="Strategy", border_style="blue"))

    # Get next steps with phase info to correctly track phase for duplicate step IDs
    steps_with_phase = _get_next_steps_with_phase(p, agent=agent)
    if not steps_with_phase:
        out.print("[dim]No claimable steps. All phases may be done or locked.[/]")
        return

    total = len(steps_with_phase)
    display_limit = total if all_steps else limit
    visible = steps_with_phase[:display_limit]

    out.print("[bold]Next Steps[/]\n")

    for i, (phase, step) in enumerate(visible, 1):
        phase_id = phase.id

        icon = _step_icon(step.status, verify=step.verify)
        # One-line summary: first line of description, truncated
        summary = _one_line_summary(step.description)

        deps_str = f"  deps: {', '.join(step.depends_on)}" if step.depends_on else ""
        agent_str = f"  suggested: {step.agent}" if step.agent else ""
        summary_str = f"  {summary}" if summary else ""
        # RFC: docs/RFC-affinity.md
        # Show exclusive affinity icon
        affinity_icon = ""
        if step.agent and (step.affinity or p.default_affinity) == AffinityMode.EXCLUSIVE:
            affinity_icon = " 🔐"

        out.print(
            f"  {i}. {icon}  {_esc(step.id)} — {_esc(step.name)}  "
            f"[dim]({_esc(phase_id)}){_esc(deps_str)}{_esc(agent_str)}[/]{affinity_icon}"
        )
        if summary_str:
            out.print(f"     [dim]{_esc(summary)}[/]")

        if detail and step.description.strip():
            out.print(Panel(Text(step.description.strip()), border_style="dim", padding=(0, 2)))
            if step.verification:
                out.print(f"     [green]Verify:[/] {_esc(step.verification)}")
            if step.refs:
                out.print(f"     [dim]Refs: {_esc(', '.join(step.refs))}[/]")
            out.print()

    hidden = total - len(visible)
    if hidden > 0:
        out.print(f"  [dim]... and {hidden} more[/]")

    out.print()
    out.print("[dim]→ vectl claim <id> --agent <name>   Claim a step[/]")
    out.print("[dim]→ vectl show <id>                   Inspect before claiming[/]")
    if hidden > 0:
        out.print("[dim]→ vectl next --all                  Show all steps[/]")


# ---------------------------------------------------------------------------
# cli.3: status
# ---------------------------------------------------------------------------


# @shell_complexity: status must preserve phase-detail, mismatch, duplicate-warning, and hint output formatting.
def status(
    plan: Path | None = PlanOption,
    phase: str | None = typer.Option(None, "--phase", help="Show detail for a specific phase."),
) -> None:
    """Show plan status overview."""
    p, _, plan_path = _load(plan)

    if phase is not None:
        _show_phase_detail(p, phase)
        _print_duplicate_id_warning_block(p)
        return

    # Check for claims.json vs plan.yaml mismatch
    has_mismatch, ghost_claims, stale_plan_claims = _check_claim_mismatch(p, plan_path)

    # Overview: all phases
    table = Table(title=f"Plan: {p.project}", show_lines=True)
    table.add_column("Phase", style="bold")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Progress")
    table.add_column("Depends On")

    for ph in p.phases:
        total = len(ph.steps)
        done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        bar = f"{done}/{total}"
        if total > 0:
            pct = done / total * 100
            bar += f" ({pct:.0f}%)"

        table.add_row(
            _esc(ph.id),
            _esc(ph.name),
            _phase_icon(ph.status),
            bar,
            _esc(", ".join(ph.depends_on)) if ph.depends_on else "-",
        )

    out.print(table)
    out.print()
    _print_duplicate_id_warning_block(p)

    # B1: Show mismatch indicator when detected
    if has_mismatch:
        out.print()
        out.print("[dim]ℹ Stale claims detected (routine after agent restart):[/]")
        if ghost_claims:
            out.print(f"  [dim]Ghost claims: {len(ghost_claims)}[/]")
        if stale_plan_claims:
            out.print(f"  [dim]Stale plan claims: {len(stale_plan_claims)}[/]")
        out.print("[dim]  → Run `vectl repair claims` to fix (safe, idempotent)[/]")

    out.print()
    out.print("[dim]→ vectl next                      See claimable steps[/]")
    out.print("[dim]→ vectl show <phase>               Phase detail[/]")


# @shell_complexity: phase renderer preserves existing Rich layout and conditional detail fields.
def _show_phase_detail(p: Plan, phase_id: str) -> None:
    phase = next((ph for ph in p.phases if ph.id == phase_id), None)
    if not phase:
        _die(f"Phase '{phase_id}' not found.")
        return  # unreachable

    out.print(f"## Phase: {phase.name}", markup=False)
    out.print(f"**Name:** {phase.name}", markup=False)
    out.print(f"**Status:** {_phase_icon(phase.status)}")
    if phase.depends_on:
        out.print(f"**Depends on:** {', '.join(phase.depends_on)}", markup=False)

    if phase.context:
        out.print(f"**Context:** {phase.context}", markup=False)

    if phase.gate:
        out.print(f"\n**Gate:** {phase.gate}", markup=False)

    out.print("\n### Steps\n")
    for step in phase.steps:
        locked = is_step_locked(p, phase, step)
        icon = _step_icon(step.status, locked=locked, verify=step.verify)
        suggested = f"  [dim]suggested: {_esc(step.agent)}[/]" if step.agent else ""
        # RFC: docs/RFC-affinity.md
        # Show exclusive affinity icon
        affinity_icon = ""
        if step.agent and (step.affinity or p.default_affinity) == AffinityMode.EXCLUSIVE:
            affinity_icon = " 🔐"
        out.print(
            f"  {icon} **{_esc(step.id)}** — {_esc(step.name)} "
            f"({_esc(phase.id)}){suggested}{affinity_icon}"
        )
        if step.status == StepStatus.CLAIMED:
            out.print(f"    [dim]Claimed by {_esc(step.claimed_by or '')}[/]")
            # RFC: docs/RFC-affinity.md
            # Show affinity override warning
            if step.affinity_override:
                out.print("    [yellow]⚠ Affinity overridden[/]")

    out.print()


# @shell_complexity: step renderer preserves existing Rich layout across all step states and diagnostics.
def _show_step_detail(p: Plan, step_id: str, plan_path: Path | None = None) -> None:
    found = p.find_step(step_id)
    if not found:
        _die(f"Step '{_canonical_selector_error_target(step_id)}' not found.")
        return  # unreachable
    phase, step = found

    # B2: Check for mismatch for this specific step
    mismatch_info = ""
    if plan_path is not None and step.status == StepStatus.CLAIMED:
        has_mismatch, ghost_claims, stale_plan_claims = _check_claim_mismatch(p, plan_path)
        if has_mismatch:
            from vectl.claims import get_current_branch

            branch = get_current_branch()
            step_key = f"{branch}:{step_id}"
            if step_key in ghost_claims:
                mismatch_info = (
                    "⚠ Claim exists in claims.json but step is not claimed in plan (ghost)"
                )
            elif step_key not in ghost_claims and step_id in stale_plan_claims:
                mismatch_info = "⚠ Step is claimed in plan but missing from claims.json"

    out.print(f"## Step: {step.name}", markup=False)
    out.print(f"**ID:** {step.id}", markup=False)
    out.print(f"**Phase:** {phase.name} ({phase.id})", markup=False)

    # R1 Source: Bug report "vectl show <step> does not display parent phase description or context"
    # Phase context carries operational guidance for all steps in the phase.
    if phase.context:
        out.print(f"**Phase Context:** {phase.context}", markup=False)

    locked = is_step_locked(p, phase, step)
    out.print(f"**Status:** {_step_icon(step.status, locked=locked, verify=step.verify)}")

    # B2: Show mismatch explanation when detected
    if mismatch_info:
        out.print(f"[yellow]{mismatch_info}[/]")

    _print_duplicate_id_recommendation_for_step(p, step.id)

    if step.agent:
        out.print(f"**Suggested agent:** {step.agent}", markup=False)
        # RFC: docs/RFC-affinity.md
        # Show affinity mode when agent is set
        effective_affinity = step.affinity or p.default_affinity
        affinity_str = effective_affinity.value if effective_affinity else "suggested"
        out.print(f"**Affinity:** {affinity_str}", markup=False)

    # RFC: docs/RFC-affinity.md
    # Show affinity override status if present
    if step.affinity_override:
        out.print("[yellow]**Affinity Override:** true[/]")
        if step.affinity_override_by:
            out.print(f"**Overridden by:** {step.affinity_override_by}", markup=False)
        if step.affinity_override_at:
            out.print(f"**At:** {step.affinity_override_at}", markup=False)

    if step.description:
        out.print(f"\n**Description:**\n{step.description}", markup=False)

    if step.depends_on:
        out.print(f"**Depends On:** {', '.join(step.depends_on)}", markup=False)

    if step.verification:
        out.print(f"\n**Verification:**\n{step.verification}", markup=False)

    if step.status == StepStatus.CLAIMED:
        out.print(f"\n**Claimed By:** {step.claimed_by}", markup=False)
        out.print(f"**At:** {step.claimed_at}", markup=False)

    if step.evidence:
        out.print(f"\n**Evidence:**\n{step.evidence}", markup=False)

    if step.rejection_reason:
        out.print("[red]**Rejection Reason:**[/]")
        out.print(step.rejection_reason, markup=False)
        if step.rejection_history:
            last = step.rejection_history[-1]
            if last.reviewer:
                out.print(f"**Reviewer:** {last.reviewer}", markup=False)

    out.print()
    eid = _esc(step.id)
    if step.status == StepStatus.PENDING:
        out.print(f"[dim]→ vectl claim {eid} --agent <name>[/]")
    elif step.status == StepStatus.CLAIMED:
        out.print(f'[dim]→ vectl complete {eid} --evidence "..."[/]')
        out.print(f"[dim]→ vectl defer {eid}                    Release claim[/]")
    elif step.status == StepStatus.DONE:
        out.print("[dim]→ vectl next                          Find more work[/]")
        out.print(f'[dim]→ vectl reject {eid} --reason "..."     Reject for rework[/]')
    elif step.status == StepStatus.SKIPPED:
        out.print("[dim]→ vectl next                          Find more work[/]")
    elif step.status == StepStatus.REJECTED:
        out.print(f"[dim]→ vectl claim {eid} --agent <name>   Re-claim for rework[/]")


# @shell_orchestration: Selector formatting helper belongs to CLI diagnostics.
def _canonical_selector_error_target(selector: str):
    """Collapse accidental double-prefix selector forms for error output."""
    if "." not in selector:
        return selector
    phase_prefix, _, step_suffix = selector.partition(".")
    if phase_prefix and step_suffix.startswith(f"{phase_prefix}."):
        return format_step_selector(phase_prefix, step_suffix)
    return selector


def show(
    target: str = typer.Argument(..., help="Step ID or Phase ID."),
    plan: Path | None = PlanOption,
) -> None:
    """Show details for a step or phase."""
    p, _, plan_path = _load(plan)

    # Try phase match
    ph = p.find_phase(target)
    if ph is not None:
        _show_phase_detail(p, target)
        _print_duplicate_id_warning_block(p)
        return

    # Try step match
    found = p.find_step(target)
    if found is not None:
        _show_step_detail(p, target, plan_path=plan_path)
        return

    canonical_target = _canonical_selector_error_target(target)
    _die(f"'{canonical_target}' not found as step or phase.")


# ---------------------------------------------------------------------------
# cli.4: guide (renamed from help-agent)
# ---------------------------------------------------------------------------


def guide_cmd(
    on: str | None = typer.Option(
        None,
        "--on",
        help="Show one topic only: startup, stuck, recovery, review, planning, or migration.",
    ),
) -> None:
    """Show agent onboarding guide."""
    if on is None:
        combined = "\n---\n\n".join(g.strip() for g in _GUIDE_ALL)
        combined += (
            "\n\n---\n*Use ``vectl_guide`` (CLI fallback:\n"
            "``vectl guide --on <topic>``) to revisit one section.*"
        )
        out.print(Markdown(combined))
    else:
        guide = _GUIDE_TOPICS.get(on)
        if guide is None:
            _die(
                f"Unknown topic '{on}'. Use: startup, stuck, recovery, review, planning, migration."
            )
            return  # unreachable, for type checker
        out.print(Markdown(guide.strip()))


def dag(
    phase: str | None = typer.Option(
        None, "--phase", help="Show step-level DAG within this phase."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Output dependency graph as Mermaid flowchart.

    Default: phase-level DAG (nodes = phases, edges = depends_on).
    With --phase: step-level DAG within that phase.

    Paste output into GitHub/Obsidian to render as a diagram.
    """
    from vectl.core import generate_mermaid_dag

    p, _, _ = _load(plan)

    try:
        mmd = generate_mermaid_dag(p, phase_id=phase)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _print_duplicate_id_warning_block(p)
    out.print(mmd)


# ---------------------------------------------------------------------------
# cli.5: claim + complete
# ---------------------------------------------------------------------------


# @shell_complexity: claim preserves conflict diagnostics, affinity output, autosave, guidance, and detail rendering semantics.
def claim(
    step_id: str | None = typer.Argument(
        None, help="Step ID to claim (auto-picks first available if omitted)."
    ),
    agent: str = typer.Option(
        os.environ.get("VECTL_AGENT", "agent"),
        "--by",
        "--agent",
        "-a",
        help="Agent name. (env: VECTL_AGENT)",
    ),
    guidance: bool = typer.Option(
        True,
        "--guidance/--no-guidance",
        help="Show claim-time Guidance block (refs + evidence template).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Override exclusive affinity violations (sets audit trail).",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Claim a step for work.

    When step_id is omitted, automatically claims the first available step
    from get_next_steps() (halves the common-path tool calls: next+claim → claim).

    RFC: docs/RFC-affinity.md
    Enforces agent affinity. Use --force to override exclusive affinity.
    """
    p, def_h, plan_path = _load(plan)

    if step_id is None:
        candidates = get_next_steps(p, agent=agent)
        if not candidates:
            _die("No claimable steps available. All phases may be done or locked.")
        step_id = candidates[0].id
        out.print(f"[dim]Auto-selected:[/] {step_id}")

    try:
        p, result = claim_step(
            p,
            step_id,
            agent,
            force=force,
            claims_path=resolve_claims_path(plan_path),
        )
    except ClaimConflictError as e:
        # Rich claim conflict diagnostics with actionable next step
        metadata = e.metadata
        console.print(f"[red bold]Error:[/] {e}", soft_wrap=True)
        console.print()
        console.print("[bold]Claim Details:[/]")
        console.print(f"  Step ID:    {metadata.step_id}", soft_wrap=True)
        console.print(f"  Branch:     {metadata.branch}", soft_wrap=True)
        console.print(f"  Claimed by: {metadata.claimant}", soft_wrap=True)
        console.print(f"  Claimed at: {metadata.claimed_at}", soft_wrap=True)
        console.print()
        console.print("[bold]Next Steps:[/]")
        console.print(f"  1. Inspect the step: [cyan]vectl show {metadata.step_id}[/]")
        console.print("  2. If this claim is stale, you can repair claims with:")
        console.print("     [cyan]vectl repair claims --dry-run[/]")
        raise typer.Exit(1) from None
    except PlanError as e:
        _die(str(e))
        return  # unreachable, but satisfies Pyright

    # RFC: docs/RFC-affinity.md
    # Display affinity warning/override messages
    if result.warning_message:
        if result.affinity_override:
            out.print(f"[yellow]⚠️  {result.warning_message}[/]")
        else:
            out.print(f"[yellow]⚠️  Affinity warning: {result.warning_message}[/]")

    _save_plan(p, plan_path, def_h, f"vectl: claim {step_id} by {agent}")
    out.print(f"[green]Claimed:[/] {step_id} by {agent}")

    # Show affinity override in claim summary if applicable
    if result.affinity_override:
        step = p.find_step(step_id)
        if step:
            step_obj = step[1]
            out.print(f"  [dim]Affinity Override: true (by {step_obj.affinity_override_by})[/]")

    # Feature request (2026-02-12): claim-time guidance.
    # Source: user feature request "Output guidance when running vectl claim".
    # R1/R4: clear markers + bounded content.
    if guidance:
        found = p.find_step(step_id)
        if found is not None:
            from vectl.claim_guidance import build_claim_guidance

            phase_obj, step_obj = found
            payload = build_claim_guidance(p, phase_obj, step_obj)
            out.print()
            out.print(payload.markdown, markup=False)

    # Show full step spec (saves a show call)
    found = p.find_step(step_id)
    if found is not None:
        out.print()
        _show_step_detail(p, step_id, plan_path=plan_path)


# @shell_complexity: complete preserves mutation, next-step preview, and no-more-work output in one Typer handler.
def complete(
    step_id: str = typer.Argument(help="Step ID to complete."),
    evidence: str = typer.Option(..., "--evidence", "-e", help="Evidence of completion."),
    plan: Path | None = PlanOption,
) -> None:
    """Mark a step as done with evidence."""
    p, def_h, plan_path = _load(plan)
    try:
        p = complete_step(p, step_id, evidence, claims_path=resolve_claims_path(plan_path))
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: complete {step_id}")
    out.print(f"[green]Completed:[/] {step_id}")

    # Show next available steps - use _get_next_steps_with_phase for correct phase tracking
    next_steps_with_phase = _get_next_steps_with_phase(p)
    if next_steps_with_phase:
        out.print()
        out.print("[bold]Next available:[/]")
        for phase, s in next_steps_with_phase[:3]:
            icon = _step_icon(s.status, verify=s.verify)
            suggested = f"  suggested: {_esc(s.agent)}" if s.agent else ""
            out.print(f"  {icon}  {s.id} — {s.name}  [dim]({phase.id}){suggested}[/]")
        if len(next_steps_with_phase) > 3:
            out.print(f"  [dim]... and {len(next_steps_with_phase) - 3} more[/]")
        out.print()
        out.print("[dim]→ vectl claim <id> --agent <name>[/]")
        out.print("[dim]→ vectl show <id>                  [/]")
    else:
        out.print("[dim]No more claimable steps.[/]")


def complete_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to complete."),
    evidence: str = typer.Option(
        ..., "--evidence", "-e", help="Evidence/audit note for phase completion."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Mark a phase as done with evidence.

    Source:
        - Eidos migration requires explicit phase completion to avoid dependency
          deadlocks when importing historical step statuses.
        - User instruction in this conversation: choose option B (extend tool
          then test) before continuing migrate phase.
    """
    p, def_h, plan_path = _load(plan)
    try:
        p, unlocked = complete_phase(p, phase_id, evidence)
    except PlanError as e:
        _die(str(e))
        return  # unreachable (typer exits), but keeps type-checkers honest

    _save_plan(p, plan_path, def_h, f"vectl: complete phase {phase_id}")
    out.print(f"[green]Completed phase:[/] {phase_id}")
    if unlocked:
        out.print(f"[green]Unlocked:[/] {', '.join(unlocked)}")
    out.print()
    out.print("[dim]→ vectl status                      See full plan[/]")
    out.print("[dim]→ vectl next                        See claimable steps[/]")


# ---------------------------------------------------------------------------
# cli.6: defer + reject + skip
# ---------------------------------------------------------------------------


def defer(
    step_id: str = typer.Argument(help="Step ID to defer."),
    plan: Path | None = PlanOption,
) -> None:
    """Return a claimed step to pending."""
    p, def_h, plan_path = _load(plan)
    try:
        p = defer_step(p, step_id, claims_path=resolve_claims_path(plan_path))
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: defer {step_id}")
    out.print(f"[yellow]Deferred:[/] {step_id}")
    out.print()
    out.print("[dim]→ vectl next                      Find new work[/]")


def reject(
    step_id: str = typer.Argument(help="Step ID to reject."),
    reason: str = typer.Option(..., "--reason", "-r", help="Rejection reason."),
    reviewer: str = typer.Option(
        "", "--by", "--reviewer", help="Reviewer name. (env: VECTL_AGENT)"
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Reject a completed step, moving it back for rework."""
    p, def_h, plan_path = _load(plan)
    try:
        p = reject_step(p, step_id, reason, reviewer)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: reject {step_id}")
    out.print(f"[red]Rejected:[/] {step_id} — {reason}")
    out.print()
    out.print("[dim]→ vectl next                      Rejected steps appear first[/]")


def skip(
    step_id: str = typer.Argument(help="Step ID to skip."),
    reason: str = typer.Option(
        ...,
        "--reason",
        "-r",
        help="Reason for skipping: superseded, irrelevant, absorbed, deprioritized.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Skip a step with a reason.

    Valid reasons: superseded, irrelevant, absorbed, deprioritized.
    """
    p, def_h, plan_path = _load(plan)
    try:
        p = skip_step(p, step_id, reason)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: skip {step_id}")
    out.print(f"[dim]Skipped:[/] {step_id} — {reason}")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")


def cancel(
    step_id: str = typer.Argument(help="Step ID to cancel (alias for skip --reason irrelevant)."),
    plan: Path | None = PlanOption,
) -> None:
    """Cancel a step (alias for skip --reason irrelevant)."""
    p, def_h, plan_path = _load(plan)
    try:
        p = skip_step(p, step_id, SkipReason.IRRELEVANT.value)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: cancel {step_id}")
    out.print(f"[dim]Cancelled:[/] {step_id} — irrelevant")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")


def skip_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to skip."),
    reason: str = typer.Option(
        ...,
        "--reason",
        "-r",
        help="Reason for skipping: superseded, irrelevant, absorbed, deprioritized.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Allow skipping a locked phase with remaining steps.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Skip all remaining steps in a phase.

    Pending/rejected steps are skipped. Claimed steps are deferred first, then skipped.
    Done/skipped steps are left unchanged. Phase auto-completes when all steps
    are done or skipped.

    Locked phases with 0 steps can always be skipped (the lock protects nothing).
    Use --force to skip a locked phase that still has remaining steps.
    """
    p, def_h, plan_path = _load(plan)
    skipped_ids: list[str] = []
    try:
        p, skipped_ids = skip_phase(p, phase_id, reason, force=force)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: skip phase {phase_id}")
    if skipped_ids:
        out.print(f"[dim]Skipped phase:[/] {phase_id} — {reason}")
        for sid in skipped_ids:
            out.print(f"  [dim]↳[/] {sid}")
    else:
        out.print(f"[dim]Phase {phase_id}:[/] no steps to skip (all already done/skipped or empty)")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.7: update-checklist
# ---------------------------------------------------------------------------


def check_cmd(
    step_id: str = typer.Argument(help="Step ID containing the checklist."),
    keyword: str | None = typer.Argument(None, help="Keyword to toggle a checklist item."),
    add: str | None = typer.Option(None, "--add", help="Text for a new checklist item."),
    plan: Path | None = PlanOption,
) -> None:
    """Toggle or add a checklist item in a step's description."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)
    try:
        p = update_checklist(p, step_id, check=keyword, append=add)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: update checklist {step_id}")
    out.print(f"[green]Updated checklist:[/] {step_id}")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")


# ---------------------------------------------------------------------------
# cli.8: validate
# ---------------------------------------------------------------------------


# @shell_complexity: validate preserves grouped error/warning output and CLI exit behavior.
def validate(
    plan: Path | None = PlanOption,
    check_refs: bool = typer.Option(False, "--check-refs", help="Check that ref files exist."),
) -> None:
    """Validate plan structure and consistency."""
    p, _, plan_path = _load(plan)

    base_path = plan_path.parent if check_refs else None
    errors = validate_plan(p, check_refs=check_refs, base_path=base_path)

    if not errors:
        out.print("[green bold]✓ Plan is valid.[/]")
        out.print()
        out.print("[dim]→ vectl status                    Plan overview[/]")
        out.print("[dim]→ vectl next                      See claimable steps[/]")
        return

    errs = [e for e in errors if not e.is_warning]
    warns = [e for e in errors if e.is_warning]

    for e in errs:
        out.print(f"  [red]ERROR:[/] {e.message}")
    for w in warns:
        out.print(f"  [yellow]WARN:[/]  {w.message}")

    out.print()
    out.print(f"[red]{len(errs)} error(s)[/], [yellow]{len(warns)} warning(s)[/]")

    if errs:
        raise typer.Exit(1)


# @shell_complexity: migration command preserves preview, confirmation, idempotence, and warning output semantics.
def migrate_cmd(
    plan: Path | None = PlanOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Migrate legacy split-state runtime data into unified plan.yaml.

    Source: docs/ADR-unified-state.md (explicit migration phase requirement).
    """
    plan_path = resolve_plan_path(plan)
    state_path = resolve_state_path(plan_path)
    migrated_path = state_path.with_suffix(".json.migrated")

    if not state_path.exists():
        if migrated_path.exists():
            out.print(f"[green]Already migrated:[/] {migrated_path}")
            return
        out.print(f"[yellow]No legacy state file found:[/] {state_path}")
        return

    loaded_plan, _, _ = _load(plan)
    try:
        for phase in loaded_plan.phases:
            for step in phase.steps:
                require_unambiguous_target_step_id(loaded_plan, step.id, operation="migrate")
    except PlanError as e:
        _die(str(e))
        return

    out.print("[bold]Migration preview[/]")
    out.print(f"  plan: {plan_path}")
    out.print(f"  legacy state: {state_path}")
    out.print(f"  backup target: {migrated_path}")

    if not yes:
        out.print()
        confirm = typer.prompt("Run split-state migration now? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        result = migrate_from_split_state(plan_path)
    except PlanIOError as e:
        _die(str(e))
        return  # unreachable, keeps type-checkers honest

    if result.already_migrated:
        out.print(f"[green]Already migrated:[/] {migrated_path}")
        return

    out.print(
        "[green]Migration complete:[/] "
        f"steps={result.migrated_steps}, phases={result.migrated_phases}"
    )
    out.print(f"[dim]Backup:[/] {migrated_path}")

    if result.warnings:
        out.print("[yellow]Warnings:[/]")
        for warning in result.warnings:
            out.print(f"  - {warning}")


# @invar:allow function_size: duplicate-ID migration CLI must preserve dry-run/apply JSON and human output contract in one public command.
# @shell_complexity: duplicate-ID migration CLI must preserve dry-run/apply JSON and human output contract in one public command.
def migrate_step_id_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview duplicate step-ID migration without writing plan.yaml.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt for apply."),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable payload including report and evidence.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Migrate duplicate step IDs to globally unique IDs.

    Source: step `step-id-migration-tooling.surfaces` and contract
    docs/contracts/duplicate-id-migration-contract.yaml dry-run/apply schema.
    """

    loaded_plan, def_h, plan_path = _load(plan)
    claims_path = resolve_claims_path(plan_path)
    branch = get_current_branch()

    report, _ = build_duplicate_step_id_migration_dry_run(
        loaded_plan,
        claims_path=claims_path,
        branch=branch,
    )
    preview_evidence = build_duplicate_step_id_migration_evidence(
        command="vectl migrate-step-id",
        command_args=["--dry-run"],
        run_mode="dry-run",
        migrated=False,
        report=report,
    )

    if dry_run:
        dry_run_payload: dict[str, object] = {
            "status": "recommendation_only",
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }
        if as_json:
            typer.echo(json.dumps(dry_run_payload, sort_keys=True))
            return

        out.print("[bold]Duplicate step-ID migration dry-run[/]")
        out.print(f"Plan: {plan_path}")
        out.print(f"Branch: {branch}")
        out.print(f"Duplicate groups: {len(report.duplicate_groups)}")
        out.print(f"Rename map entries: {len(report.rename_map)}")
        affected_phases = ", ".join(report.affected_phases) if report.affected_phases else "-"
        out.print(f"Affected phases: {affected_phases}")
        out.print("[dim]report[/]")
        out.print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        out.print("[dim]evidence[/]")
        out.print(json.dumps(preview_evidence.to_dict(), indent=2, sort_keys=True))
        return

    if not yes:
        out.print("[bold]Duplicate step-ID migration apply preview[/]")
        out.print(f"Plan: {plan_path}")
        out.print(f"Branch: {branch}")
        out.print(f"Duplicate groups: {len(report.duplicate_groups)}")
        out.print(f"Rename map entries: {len(report.rename_map)}")
        if report.claimed_step_conflicts:
            out.print("[yellow]Claimed-step conflicts detected; apply will be blocked.[/]")
        out.print()
        confirm = typer.prompt("Apply duplicate step-ID migration now? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        apply_result = apply_duplicate_step_id_migration(
            plan_path,
            expected_hash=def_h,
            claims_path=claims_path,
            branch=branch,
            command="vectl migrate-step-id",
            command_args=["--yes"] if yes else [],
            run_mode="apply",
        )
    except CASConflictError:
        _die(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl status` or `vectl show`, then retry migration."
        )
        return
    except PlanError as e:
        _die(str(e))
        return

    apply_payload: dict[str, object] = {
        "status": "repair_applied",
        "migrated": apply_result.migrated,
        "new_plan_hash": apply_result.new_plan_hash,
        "report": apply_result.report.to_dict(),
        "evidence": apply_result.evidence.to_dict(),
    }

    if as_json:
        typer.echo(json.dumps(apply_payload, sort_keys=True))
        return

    out.print("[green]Duplicate step-ID migration complete.[/]")
    out.print(f"migrated={apply_result.migrated}")
    out.print(f"new_plan_hash={apply_result.new_plan_hash or '-'}")
    out.print("[dim]report[/]")
    out.print(json.dumps(apply_result.report.to_dict(), indent=2, sort_keys=True))
    out.print("[dim]evidence[/]")
    out.print(json.dumps(apply_result.evidence.to_dict(), indent=2, sort_keys=True))


# @shell_complexity: recovery command preserves backup discovery, diff preview, confirmation, and restore diagnostics.
def recover(
    plan: Path | None = PlanOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Recover plan from backup in .git/vectl/plan.yaml.bak.

    Restores the plan to a previous state from the backup file.
    Shows a diff of what will change before applying.
    """
    from vectl.core import apply_recovery, preview_recovery

    _, _, plan_path = _load(plan)

    # Find backup path
    root_cli = sys.modules.get("vectl.cli")
    resolve_git_dir = getattr(root_cli, "_resolve_git_dir", _resolve_git_dir)
    git_dir = resolve_git_dir(plan_path)
    if git_dir is None:
        _die("Not in a git repository or in a linked worktree")

    assert git_dir is not None  # Type narrowing for pyright
    backup_path = git_dir / "vectl" / "plan.yaml.bak"
    if not backup_path.exists():
        _die(f"Backup not found: {backup_path}")

    # Preview diff without writing
    result: RecoverResult | None = None
    try:
        result = preview_recovery(plan_path, backup_path)
    except PlanError as e:
        _die(str(e))
    except PlanIOError as e:
        _die(str(e))

    assert result is not None
    out.print("[bold]Recovery diff:[/]")
    out.print(result.diff_summary)
    diff_output = result.diff_summary

    if not yes:
        out.print()
        confirm = typer.prompt("Restore plan.yaml from backup? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        apply_recovery(backup_path, plan_path)
    except PlanError as e:
        _die(str(e))
    except PlanIOError as e:
        _die(str(e))

    out.print("[green]Plan restored from backup.[/]")
    out.print(diff_output)
    out.print()
    out.print("[dim]Note: For git-based recovery, use `git log --all --follow --plan.yaml`[/]")
    out.print("[dim]to find commits and `git show <ref>:plan.yaml` to preview.[/]")


def checkpoint(
    agent: str | None = typer.Option(
        os.environ.get("VECTL_AGENT"),
        "--agent",
        "-a",
        help="Agent name (affects focus selection). (env: VECTL_AGENT)",
    ),
    next_limit: int = typer.Option(3, "--next", "-n", help="Max next steps."),
    include_guidance: bool = typer.Option(
        False, "--include-guidance", help="Include guidance (refs/templates)."
    ),
    lite: bool = typer.Option(
        True, "--lite/--full", help="Minimize output (omit metadata, redundant info)."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON."),
    plan: Path | None = PlanOption,
) -> None:
    """Output machine-readable plan checkpoint (JSON).

    Source: FR "vectl checkpoint".
    Intent: Provide a deterministic, bounded snapshot for compaction/handoff.
    """
    import json

    from vectl.checkpoint import build_checkpoint

    p, def_h, _ = _load(plan)

    data = build_checkpoint(
        p,
        file_hash=def_h,
        agent=agent,
        next_limit=next_limit,
        include_guidance=include_guidance,
        lite=lite,
    )

    if pretty:
        out.print(json.dumps(data, indent=2))
    else:
        # Print raw JSON string to stdout
        print(json.dumps(data))


# ---------------------------------------------------------------------------
# cli.9: add-step + add-phase (architect commands)
# ---------------------------------------------------------------------------


# @shell_complexity: add-step preserves import-status parsing, template source validation, mutation, and guidance output.
def add_step_cmd(
    phase: str = typer.Option(..., "--phase", help="Phase ID to add step to."),
    name: str = typer.Option(..., "--name", help="Step name."),
    desc: str = typer.Option("", "--desc", "--description", help="Step description."),
    after: str | None = typer.Option(
        None, "--after", help="Comma-separated step IDs this depends on."
    ),
    verify: str = typer.Option("", "--verify", "--verification", help="Verification command."),
    refs: str | None = typer.Option(None, "--refs", help="Comma-separated ref paths."),
    evidence_template: str = typer.Option(
        "",
        "--evidence-template",
        help="Optional short template for completion evidence (inline).",
    ),
    evidence_template_file: Path | None = EvidenceTemplateFileOption,
    step_id: str | None = typer.Option(None, "--id", help="Explicit step ID (auto if omitted)."),
    import_status: str | None = typer.Option(
        None,
        "--status",
        help="Initial status for import: pending (default), done, skipped.",
    ),
    import_evidence: str | None = typer.Option(
        None,
        "--evidence",
        "-e",
        help="Evidence (required when --status=done).",
    ),
    skipped_reason: str | None = typer.Option(
        None,
        "--skipped-reason",
        help="Skip reason (required when --status=skipped).",
    ),
    step_agent: str | None = typer.Option(
        None,
        "--agent",
        help="Advisory agent suggestion (which agent should work on this step).",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Add a new step to a phase.

    Supports importing steps with pre-set status for migration:
      --status done --evidence "commit abc"
      --status skipped --skipped-reason "absorbed into X"
    """
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    depends_on = [d.strip() for d in after.split(",") if d.strip()] if after else None
    refs_list = [r.strip() for r in refs.split(",") if r.strip()] if refs else None

    if evidence_template_file is not None and evidence_template:
        _die("Use only one of --evidence-template or --evidence-template-file.")
        return  # unreachable

    template_val = evidence_template
    if evidence_template_file is not None:
        template_val = evidence_template_file.read_text(encoding="utf-8")

    # Parse import status
    step_status: StepStatus | None = None
    if import_status is not None:
        try:
            step_status = StepStatus(import_status)
        except ValueError:
            _die(f"Invalid status '{import_status}'. Must be one of: pending, done, skipped.")
            return  # unreachable

    try:
        p, generated_id = add_step(
            p,
            phase,
            name,
            step_id=step_id,
            description=desc,
            depends_on=depends_on,
            verification=verify,
            evidence_template=template_val,
            refs=refs_list,
            status=step_status,
            evidence=import_evidence,
            skipped_reason=skipped_reason,
            agent=step_agent,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    _save_plan(p, plan_path, def_h, f"vectl: add step {generated_id}")

    out.print(f"[green]Added step:[/] {generated_id} → phase [bold]{phase}[/]")

    # Long slug warning (only for auto-generated IDs)
    if step_id is None and len(generated_id) > 40:
        out.print(f"[yellow]⚠ Slug is {len(generated_id)} chars. Use --id to set a shorter ID.[/]")

    out.print()
    out.print(f"[dim]→ vectl claim {generated_id} --agent <name>   Claim this step[/]")
    out.print(f"[dim]→ vectl add-step --phase {phase}       Add another step[/]")
    out.print(f"[dim]→ vectl status --phase {phase}         See all steps in phase[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


def add_phase_cmd(
    name: str = typer.Option(..., "--name", help="Phase name."),
    after: str | None = typer.Option(
        None, "--after", help="Comma-separated phase IDs this depends on."
    ),
    gate: str = typer.Option("", "--gate", help="Gate criterion text."),
    context: str = typer.Option("", "--context", "--ctx", help="Phase context/description."),
    phase_id: str | None = typer.Option(None, "--id", help="Explicit phase ID (auto if omitted)."),
    plan: Path | None = PlanOption,
) -> None:
    """Add a new phase to the plan."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    depends_on = [d.strip() for d in after.split(",") if d.strip()] if after else None

    try:
        p, generated_id = add_phase(
            p,
            name,
            phase_id=phase_id,
            depends_on=depends_on,
            gate=gate,
            context=context,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    _save_plan(p, plan_path, def_h, f"vectl: add phase {generated_id}")

    # Show status based on auto-determined initial status
    ph = p.find_phase(generated_id)
    assert ph is not None  # just created it
    status_icon = _phase_icon(ph.status)

    out.print(f"[green]Added phase:[/] {generated_id} ({status_icon})")

    # Long slug warning (only for auto-generated IDs)
    if phase_id is None and len(generated_id) > 40:
        out.print(f"[yellow]⚠ Slug is {len(generated_id)} chars. Use --id to set a shorter ID.[/]")

    out.print()
    out.print(f"[dim]→ vectl add-step --phase {generated_id}       Add steps to this phase[/]")
    out.print(f"[dim]→ vectl status --phase {generated_id}         Inspect phase[/]")
    out.print("[dim]→ vectl status                      See full plan[/]")


# @shell_complexity: edit-plan preserves mutually-exclusive file/inline options and sentinel update semantics.
def edit_plan_cmd(
    project_guidance: str | None = typer.Option(
        None,
        "--project-guidance",
        help="Project-level claim-time guidance (inline; use '' to clear).",
    ),
    project_guidance_file: Path | None = ProjectGuidanceFileOption,
    strategy_ref: str | None = typer.Option(
        None,
        "--strategy-ref",
        help="Plan-level strategy reference (use '' to clear).",
    ),
    context: str | None = typer.Option(
        None,
        "--context",
        help="Plan context (inline; use '' to clear).",
    ),
    context_file: Path | None = ContextFileOption,
    plan: Path | None = PlanOption,
) -> None:
    """Edit plan-level metadata without manually editing plan.yaml.

    Source:
        - User instruction in this conversation (2026-02-12): "No manual YAML edits",
          and agreed `--*-file` approach.
    """
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL, edit_plan

    if project_guidance_file is not None and project_guidance is not None:
        _die("Use only one of --project-guidance or --project-guidance-file.")
        return  # unreachable
    if context_file is not None and context is not None:
        _die("Use only one of --context or --context-file.")
        return  # unreachable

    if (
        project_guidance is None
        and project_guidance_file is None
        and strategy_ref is None
        and context is None
        and context_file is None
    ):
        _die(
            "Nothing to edit. Provide at least one of: --project-guidance, "
            "--project-guidance-file, --strategy-ref, --context, --context-file."
        )
        return  # unreachable

    p, def_h, plan_path = _load(plan)

    pg_val = project_guidance
    if project_guidance_file is not None:
        pg_val = project_guidance_file.read_text(encoding="utf-8")

    ctx_val = context
    if context_file is not None:
        ctx_val = context_file.read_text(encoding="utf-8")

    try:
        p = edit_plan(
            p,
            project_guidance=pg_val if pg_val is not None else _SENTINEL,
            strategy_ref=strategy_ref if strategy_ref is not None else _SENTINEL,
            context=ctx_val if ctx_val is not None else _SENTINEL,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _save_plan(p, plan_path, def_h, "vectl: edit plan metadata")
    out.print("[green]Updated plan metadata[/]")
    out.print("[dim]→ vectl status                      See plan overview[/]")


# ---------------------------------------------------------------------------
# cli.11: edit-step, remove-step, move-step (architect mutations)
# ---------------------------------------------------------------------------


# @shell_orchestration: Edit-step option validation is coupled to Typer CLI diagnostics.
def _ensure_edit_step_has_change(
    *,
    name: str | None,
    desc: str | None,
    verify: str | None,
    step_agent: str | None,
    add_deps: list[str] | None,
    rm_deps: list[str] | None,
    add_refs: list[str] | None,
    rm_refs: list[str] | None,
    evidence_template: str | None,
    new_step_id: str | None,
    evidence_template_file: Path | None,
) -> None:
    if any((name, desc, verify, step_agent, add_deps, rm_deps, add_refs, rm_refs)):
        return
    if evidence_template is not None or new_step_id is not None or evidence_template_file is not None:
        return
    _die(
        "Nothing to edit. Provide at least one of --name, --desc, --verify, --agent, "
        "--add-dep, --rm-dep, --add-ref, --rm-ref, --evidence_template, "
        "--new-id, "
        "--evidence_template_file."
    )


# @shell_complexity: edit-step preserves mutually-exclusive template sources, sentinel semantics, dependency/ref edits, and rename output.
def edit_step_cmd(
    step_id: str = typer.Argument(help="Step ID to edit."),
    name: str | None = typer.Option(None, "--name", help="New step name."),
    desc: str | None = typer.Option(None, "--desc", "--description", help="New description."),
    verify: str | None = typer.Option(
        None, "--verify", "--verification", help="New verification command."
    ),
    step_agent: str | None = typer.Option(
        None, "--agent", help="New agent suggestion (use '' to clear)."
    ),
    add_dep: str | None = typer.Option(
        None, "--add-dep", help="Comma-separated step IDs to add as dependencies."
    ),
    rm_dep: str | None = typer.Option(
        None, "--rm-dep", help="Comma-separated step IDs to remove from dependencies."
    ),
    add_ref: str | None = typer.Option(None, "--add-ref", help="Comma-separated ref paths to add."),
    rm_ref: str | None = typer.Option(
        None, "--rm-ref", help="Comma-separated ref paths to remove."
    ),
    evidence_template: str | None = typer.Option(
        None,
        "--evidence-template",
        help="New completion evidence template (inline; use '' to clear).",
    ),
    new_step_id: str | None = typer.Option(
        None,
        "--new-id",
        help="Rename this step to a new globally unique ID.",
    ),
    evidence_template_file: Path | None = EvidenceTemplateFileOption,
    plan: Path | None = PlanOption,
) -> None:
    """Edit a step's metadata."""
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL

    p, def_h, plan_path = _load(plan)

    add_deps = [d.strip() for d in add_dep.split(",") if d.strip()] if add_dep else None
    rm_deps = [d.strip() for d in rm_dep.split(",") if d.strip()] if rm_dep else None
    add_refs = [r.strip() for r in add_ref.split(",") if r.strip()] if add_ref else None
    rm_refs = [r.strip() for r in rm_ref.split(",") if r.strip()] if rm_ref else None

    _ensure_edit_step_has_change(
        name=name,
        desc=desc,
        verify=verify,
        step_agent=step_agent,
        add_deps=add_deps,
        rm_deps=rm_deps,
        add_refs=add_refs,
        rm_refs=rm_refs,
        evidence_template=evidence_template,
        new_step_id=new_step_id,
        evidence_template_file=evidence_template_file,
    )

    if evidence_template_file is not None and evidence_template is not None:
        _die("Use only one of --evidence-template or --evidence-template-file.")
        return  # unreachable

    # Agent: None means "not provided" (sentinel); "" means "clear"
    agent_val = _SENTINEL
    if step_agent is not None:
        agent_val = None if step_agent == "" else step_agent  # type: ignore[assignment]

    template_val = _SENTINEL
    if evidence_template_file is not None:
        template_val = evidence_template_file.read_text(encoding="utf-8")  # type: ignore[assignment]
    elif evidence_template is not None:
        template_val = evidence_template  # type: ignore[assignment]

    try:
        p = edit_step(
            p,
            step_id,
            name=name if name is not None else _SENTINEL,
            description=desc if desc is not None else _SENTINEL,
            verification=verify if verify is not None else _SENTINEL,
            evidence_template=template_val,
            agent=agent_val,
            add_deps=add_deps,
            remove_deps=rm_deps,
            add_refs=add_refs,
            remove_refs=rm_refs,
            new_step_id=new_step_id if new_step_id is not None else _SENTINEL,
        )
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: edit step {step_id}")

    out.print(f"[green]Edited:[/] {step_id}")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# @shell_complexity: edit-phase preserves no-op validation, dependency parsing, sentinel semantics, and output hints.
def edit_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to edit."),
    name: str | None = typer.Option(None, "--name", help="New phase name."),
    context: str | None = typer.Option(None, "--context", "--ctx", help="New context."),
    gate: str | None = typer.Option(None, "--gate", help="New gate criterion."),
    add_dep: str | None = typer.Option(
        None, "--add-dep", help="Comma-separated phase IDs to add as dependencies."
    ),
    rm_dep: str | None = typer.Option(
        None, "--rm-dep", help="Comma-separated phase IDs to remove from dependencies."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Edit a phase's metadata."""
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL

    p, def_h, plan_path = _load(plan)

    add_deps = [d.strip() for d in add_dep.split(",") if d.strip()] if add_dep else None
    rm_deps = [d.strip() for d in rm_dep.split(",") if d.strip()] if rm_dep else None

    if name is None and context is None and gate is None and not add_deps and not rm_deps:
        _die(
            "Nothing to edit. Provide at least one of --name, --context, --gate, "
            "--add-dep, --rm-dep."
        )
        return  # unreachable

    try:
        p = edit_phase(
            p,
            phase_id,
            name=name if name is not None else _SENTINEL,
            context=context if context is not None else _SENTINEL,
            gate=gate if gate is not None else _SENTINEL,
            add_deps=add_deps,
            remove_deps=rm_deps,
        )
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: edit phase {phase_id}")

    out.print(f"[green]Edited phase:[/] {phase_id}")
    out.print()
    out.print(f"[dim]→ vectl show {phase_id}[/]")
    out.print("[dim]→ vectl search <pattern>              Check consistency[/]")


def remove_step_cmd(
    step_id: str = typer.Argument(help="Step ID to remove (must be pending)."),
    force: bool = typer.Option(
        False, "--force", help="Remove even if other steps depend on it (cleans up dep refs)."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Remove a pending step from its phase."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)
    try:
        p = remove_step(p, step_id, force=force)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: remove step {step_id}")
    out.print(f"[yellow]Removed:[/] {step_id}")
    if force:
        out.print("[dim]  Dependency references cleaned up in remaining steps.[/]")
    out.print()
    out.print("[dim]→ vectl status                    Plan overview[/]")
    out.print("[dim]→ vectl next                      See claimable steps[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


def move_step_cmd(
    step_id: str = typer.Argument(help="Step ID to move (must be pending)."),
    to_phase: str = typer.Option(..., "--to-phase", help="Target phase ID."),
    plan: Path | None = PlanOption,
) -> None:
    """Move a pending step to a different phase."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    # Capture deps before move (move_step clears them — lossy operation)
    found = p.find_step(step_id)
    cleared_deps = list(found[1].depends_on) if found else []

    try:
        p = move_step(p, step_id, to_phase)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: move step {step_id} to {to_phase}")
    out.print(f"[green]Moved:[/] {step_id} → {to_phase}")
    if cleared_deps:
        out.print(f"[yellow]⚠ Cleared {len(cleared_deps)} dep(s):[/] {', '.join(cleared_deps)}")
        out.print("[dim]  Dependencies are phase-scoped. Re-add in target phase if needed.[/]")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")
    out.print(f"[dim]→ vectl show {to_phase}[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.12: unlock (explicit phase unlock)
# ---------------------------------------------------------------------------


def unlock(
    phase_id: str = typer.Argument(help="Phase ID to unlock."),
    evidence: str = typer.Option("", "--evidence", "-e", help="Evidence for unlocking."),
    plan: Path | None = PlanOption,
) -> None:
    """Unlock a locked phase by validating all dependencies are done."""
    p, def_h, plan_path = _load(plan)
    try:
        p = unlock_phase(p, phase_id)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: unlock phase {phase_id}")
    out.print(f"[green]Unlocked:[/] {phase_id}")
    if evidence:
        out.print(f"[dim]Evidence: {evidence}[/]")
    out.print()
    out.print(f"[dim]→ vectl show {phase_id}[/]")
    out.print("[dim]→ vectl next[/]")


# ---------------------------------------------------------------------------
# recalc-lock: repair lock/pending status for all phases
# Source: vectl plan step cli-recalc-lock (task specification).
# ---------------------------------------------------------------------------


def recalc_lock(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would change without saving.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Recalculate LOCKED/PENDING status for all phases.

    Walks every phase and reapplies dependency rules: phases whose
    dependencies are not all DONE become LOCKED; phases whose dependencies
    are all DONE (or have none) become PENDING. DONE and IN_PROGRESS phases
    are never affected.

    Normally vectl maintains lock status automatically on every write. Use
    this command only for human diagnosis after direct YAML edits that may
    have left lock status inconsistent.

    For each phase whose status changes, prints a line of the form:
        [vectl] Lock status updated: phase-a (pending)

    --dry-run previews what would change without saving the plan file.
    """
    p, def_h, plan_path = _load(plan)

    if dry_run:
        # Operate on a deep copy so the original plan is not mutated.
        p_copy = p.model_copy(deep=True)
        changed = recalc_lock_status(p_copy)
        msg = format_lock_changes(changed, p_copy)
        out.print(msg if msg else "[vectl] Lock status is consistent. No changes needed.")
    else:
        changed = recalc_lock_status(p)
        msg = format_lock_changes(changed, p)
        _save_plan(p, plan_path, def_h, "vectl: recalc lock status")
        out.print(msg if msg else "[vectl] Lock status is consistent. No changes needed.")


# ---------------------------------------------------------------------------
# cli.12b: repair claims (operator recovery)
# Source: claim-consistency-recovery.repair-claims-command
# ---------------------------------------------------------------------------


# @shell_complexity: repair-claims preserves dry-run/json modes and human reconciliation report formatting.
def repair_claims_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview reconciliation without writing claims.json.",
    ),
    step: str | None = typer.Option(
        None,
        "--step",
        help="Repair only this step's claim on current branch.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Repair claims.json consistency against plan.yaml (current branch scope)."""

    p, _, plan_path = _load(plan)
    claims_path = resolve_claims_path(plan_path)

    try:
        result = repair_claims(
            p,
            plan_path,
            claims_path,
            dry_run=dry_run,
            step_id=step,
        )
    except PlanError as e:
        _die(str(e))
        return

    payload = result.to_dict()

    if as_json:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    mode = "[dry-run]" if dry_run else ""
    out.print(f"[bold]Repair claims {mode}[/]")
    out.print(f"Status: {result.status.value}")
    out.print(f"Policy: {result.policy}")
    out.print(f"Branch: {result.branch}")
    out.print(f"Plan: {result.plan_path}")
    out.print(f"Claims: {result.claims_path}")
    if result.step_scope is not None:
        out.print(f"Scope: step={result.step_scope}")
    if result.missing_claims_file:
        out.print("Claims file was missing; fallback started from empty map.")

    if not result.actions:
        out.print("No claim inconsistencies detected.")
        return

    out.print(f"Applied actions: {len(result.actions)}")
    for action in result.actions:
        out.print(f"- {action.action}: {action.key} ({action.reason})")


# @shell_complexity: batch add command preserves stdin/YAML validation and post-mutation output contract.
def add_steps_cmd(
    phase: str = typer.Option(..., "--phase", help="Phase ID to add steps to."),
    plan: Path | None = PlanOption,
) -> None:
    """Batch add steps from stdin (YAML format).

    Reads a YAML list from stdin. Each entry has:
      - name (required)
      - desc, after, verify, refs, id (optional)
      - status: pending (default), done, skipped (optional, for import)
      - evidence: required when status=done (optional)
      - skipped_reason: required when status=skipped (optional)

    Example input:
      - name: "Step A"
        desc: "Do thing A"
        status: done
        evidence: "commit abc"
      - name: "Step B"
        after: ["phase.step-a"]
    """
    _check_not_linked_worktree(plan)
    import yaml

    raw = sys.stdin.read()
    if not raw.strip():
        _die("No input from stdin. Pipe YAML step definitions.")
        return  # unreachable

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        _die(f"Invalid YAML from stdin: {e}")
        return  # unreachable

    if not isinstance(data, list):
        _die("Expected YAML list of step definitions from stdin.")
        return  # unreachable

    p, def_h, plan_path = _load(plan)
    try:
        p, generated_ids = add_steps_bulk(p, phase, data)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _save_plan(p, plan_path, def_h, f"vectl: add steps to phase {phase}")

    out.print(f"[green]Added {len(generated_ids)} step(s)[/] to phase [bold]{phase}[/]:")
    for gid in generated_ids:
        out.print(f"  • {gid}")
    out.print()
    out.print(f"[dim]→ vectl show {phase}         See all steps[/]")
    out.print("[dim]→ vectl next              See claimable steps[/]")
    out.print("[dim]→ vectl search <pattern>  Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.14: search (plan-wide pattern search)
# ---------------------------------------------------------------------------


# @shell_complexity: search preserves grouped phase output and empty-result CLI messaging.
def search(
    pattern: str = typer.Argument(help="Pattern to search for (case-insensitive substring)."),
    phase: str | None = typer.Option(None, "--phase", help="Restrict to a specific phase."),
    state: str | None = typer.Option(
        None, "--state", help="Filter by status (e.g. pending, done, claimed)."
    ),
    regex: bool = typer.Option(False, "--regex", help="Treat pattern as regex."),
    plan: Path | None = PlanOption,
) -> None:
    """Search across phases and steps for a pattern."""
    p, _, _ = _load(plan)

    try:
        matches = search_plan(p, pattern, phase_id=phase, state=state, use_regex=regex)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    if not matches:
        out.print(f"[dim]No matches for '{pattern}'.[/]")
        return

    out.print(f"[bold]{len(matches)} match(es)[/] for [cyan]'{pattern}'[/]:\n")

    # Group by phase
    current_phase = ""
    for m in matches:
        if m.phase_id != current_phase:
            current_phase = m.phase_id
            out.print(f"  [bold blue]{current_phase}[/]")

        if m.step_id:
            out.print(f"    {m.step_id} [dim]({m.field})[/]: {m.snippet}")
        else:
            out.print(f"    [dim]phase.{m.field}[/]: {m.snippet}")

    out.print()
    out.print("[dim]→ vectl show <id>                   Inspect a match[/]")
    out.print("[dim]→ vectl edit-step <id> --desc ...    Edit if needed[/]")


# ---------------------------------------------------------------------------
# mine: agent crash recovery
# ---------------------------------------------------------------------------


# @shell_complexity: mine preserves env fallback, --all behavior, empty-state hints, and claimed-step detail output.
def mine(
    agent: str | None = typer.Option(
        None,
        "--by",
        "--agent",
        "-a",
        help="Agent name to filter by. (env: VECTL_AGENT)",
    ),
    show_all: bool = typer.Option(
        False, "--all", help="Show all claimed steps regardless of agent."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Show steps currently claimed by an agent (crash recovery).

    After a context reset, an agent can run this to find what it was working on.
    Use --all to see all claimed steps across agents.
    """
    if not agent and not show_all:
        agent = os.environ.get("VECTL_AGENT")
    if not agent and not show_all:
        _die("Provide --by <agent> or --all. (Or set VECTL_AGENT env var.)")

    p, _, _ = _load(plan)
    claimed = get_claimed_steps(p, agent=agent)

    if not claimed:
        label = "any agent" if show_all else f"'{agent}'"
        out.print(f"[dim]No steps claimed by {label}.[/]")
        out.print()
        if not show_all:
            out.print(f"[dim]→ vectl claim --by {agent}              Auto-claim next step[/]")
        out.print("[dim]→ vectl mine --all                   Show all claimed steps[/]")
        return

    header = "all agents" if show_all else str(agent)
    out.print(f"[bold]Steps claimed by {header}:[/]\n")
    for phase_id, step in claimed:
        claimed_label = f"  [dim][{step.claimed_by}][/]" if show_all else ""
        suggested_label = f"  [dim]suggested: {step.agent}[/]" if step.agent else ""
        out.print(
            f"  ◉  {step.id} — {step.name}  [dim]({phase_id})[/]{claimed_label}{suggested_label}"
        )
        if step.description.strip():
            summary = _one_line_summary(step.description)
            if summary:
                out.print(f"     [dim]{summary}[/]")
        if step.claimed_at:
            out.print(f"     [dim]Claimed at: {step.claimed_at}[/]")

    out.print()
    out.print("[dim]→ vectl show <id>                     Inspect step details[/]")
    out.print('[dim]→ vectl complete <id> --evidence "..." Mark done[/]')
    out.print("[dim]→ vectl defer <id>                    Release claim[/]")


# ---------------------------------------------------------------------------
# review: multi-layer plan review
# ---------------------------------------------------------------------------


# @shell_orchestration: Review phase filtering is tied to the CLI --phase option.
def _restrict_review_result_to_phase(p: Plan, result: Any, phase_id: str):
    selected_phase = p.find_phase(phase_id)
    if selected_phase is None:
        _die(f"Phase '{phase_id}' not found.")
    return replace(
        result,
        phase_progress=[pp for pp in result.phase_progress if pp.phase_id == phase_id],
        active_phases=[ph for ph in result.active_phases if ph.id == phase_id],
        ref_index={
            ref: step_ids
            for ref, step_ids in result.ref_index.items()
            if any(step.id in step_ids for step in selected_phase.steps)
        },
        total_done=sum(
            1
            for step in selected_phase.steps
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED)
        ),
        total_steps=len(selected_phase.steps),
    )


# @shell_complexity: review preserves layered L1-L4 output and exit behavior for plan diagnostics.
def review(
    phase: str | None = typer.Option(None, "--phase", help="Restrict review to a single phase."),
    show_all: bool = typer.Option(False, "--all", help="Include DONE and LOCKED phases in detail."),
    check_refs: bool = typer.Option(False, "--check-refs", help="Check that ref files exist."),
    plan: Path | None = PlanOption,
) -> None:
    """Multi-layer plan review.

    Layers:
      L1: Validation checks (errors/warnings)
      L2: Phase overview with progress
      L3: Active phase detail (steps, deps, refs)
      L4: Spec coverage (reverse index from refs)
    """
    p, _, plan_path = _load(plan)

    # ── Compute review data ────────────────────────────────────────────
    base_path = plan_path.parent if check_refs else None
    result = review_plan(p, check_refs=check_refs, base_path=base_path, include_done=show_all)
    if phase is not None:
        result = _restrict_review_result_to_phase(p, result, phase)

    # ── L1: Validation ──────────────────────────────────────────────────
    out.print("[bold]L1: Validation[/]")
    if not result.validation_issues:
        out.print("  [green]✓ Plan is valid — 0 errors, 0 warnings[/]")
    else:
        for e in result.errors:
            out.print(f"  [red]ERROR:[/] {_esc(e.message)}")
        for w in result.warnings:
            out.print(f"  [yellow]WARN:[/]  {_esc(w.message)}")
        out.print(f"  [dim]{len(result.errors)} error(s), {len(result.warnings)} warning(s)[/]")
    out.print()

    # ── L2: Phase overview with progress ────────────────────────────────
    out.print("[bold]L2: Phase Overview[/]")
    for pp in result.phase_progress:
        icon = _PHASE_STATUS_STYLE[pp.status][0]
        bar_len = 20
        filled = int(bar_len * pp.pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        out.print(f"  {icon} {pp.phase_id:<20s} [{bar}] {pp.done}/{pp.total} ({pp.pct:.0f}%)")

    out.print(
        f"\n  [bold]Overall: {result.total_done}/{result.total_steps} "
        f"({result.overall_pct:.0f}%)[/]"
    )
    out.print()

    # ── L3: Active phase detail ─────────────────────────────────────────
    out.print("[bold]L3: Active Phase Detail[/]")

    if not result.active_phases:
        if not show_all:
            out.print("  [dim]No active phases. Use --all to include DONE/LOCKED.[/]")
        else:
            out.print("  [dim]No phases found.[/]")
    else:
        for ph in result.active_phases:
            out.print(f"\n  [bold]{_esc(ph.id)}[/] — {_esc(ph.name)} ({ph.status.value})")
            if ph.depends_on:
                out.print(f"    deps: {_esc(', '.join(ph.depends_on))}")
            if ph.gate:
                out.print(f"    gate: [yellow]{_esc(ph.gate)}[/]")
                if _is_claim_consistency_scoped_gate(ph.id):
                    out.print(
                        "    [dim]scoped verification evidence: targeted integration run only "
                        "(not full package-wide coverage proof)[/]"
                    )
            for step in ph.steps:
                if is_step_locked(p, ph, step):
                    icon, style = ("🔒", "dim")
                else:
                    icon, style = _STEP_STATUS_STYLE[step.status]
                # NOTE: Rich treats square brackets as markup tags. Step IDs often contain
                # dots (e.g. "mig.1"), so "deps=[mig.1]" is parsed as markup and disappears.
                # Use parentheses to keep deps/refs visible.
                dep_info = f" deps=({_esc(', '.join(step.depends_on))})" if step.depends_on else ""
                ref_info = f" refs=({_esc(', '.join(step.refs))})" if step.refs else ""
                claimed = f" [yellow]@{_esc(step.claimed_by or '')}[/]" if step.claimed_by else ""
                suggested = f" [dim]suggested: {_esc(step.agent)}[/]" if step.agent else ""
                out.print(
                    f"    [{style}]{icon}[/] {_esc(step.id)} — {_esc(step.name)}"
                    f"{claimed}{suggested}{dep_info}{ref_info}"
                )
    out.print()

    # ── L4: Spec coverage (refs reverse index) ──────────────────────────
    out.print("[bold]L4: Spec Coverage[/]")

    if not result.ref_index:
        out.print("  [dim]No refs defined in any steps.[/]")
    else:
        for ref_path in sorted(result.ref_index):
            step_ids = result.ref_index[ref_path]
            out.print(f"  {_esc(ref_path)} ← {_esc(', '.join(step_ids))}")

    out.print()
    if result.errors:
        out.print("[red bold]⚠ Fix validation errors before proceeding.[/]")
        out.print("[dim]→ vectl validate --check-refs       Full validation[/]")
    else:
        out.print("[dim]→ vectl gate-check <phase>           Check phase gate readiness[/]")
        out.print("[dim]→ vectl next                         See claimable steps[/]")

    if result.errors:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# gate-check: phase gate readiness
# ---------------------------------------------------------------------------


def _exit_if_gate_validation_fails(p: Plan) -> None:
    validation_issues = validate_plan(p)
    validation_errors = [issue for issue in validation_issues if not issue.is_warning]
    if not validation_errors:
        return

    out.print("[red bold]✗ Gate check blocked: plan validation failed.[/]")
    for issue in validation_errors:
        out.print(f"  [red]ERROR:[/] {_esc(issue.message)}")
    out.print("[dim]→ vectl validate                  Full validation report[/]")
    raise typer.Exit(1)


# @shell_complexity: gate-check preserves validation, subprocess gate script execution, manual criteria, and summary output.
def gate_check(
    phase_id: str = typer.Argument(help="Phase ID to check gate readiness."),
    plan: Path | None = PlanOption,
) -> None:
    """Check if a phase is ready to pass its gate.

    Checks:
    1. All steps done/skipped
    2. Run gate_script if defined (subprocess, check exit code)
    3. Print manual gate criteria
    4. Recommend unlock if all pass
    """
    import subprocess as sp

    p, _, plan_path = _load(plan)

    _exit_if_gate_validation_fails(p)

    try:
        gc = core_gate_check(p, phase_id)
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    out.print(Panel(f"Gate Check: {gc.phase_id} — {gc.phase_name}", style="bold"))

    all_pass = True

    # ── Check 1: step completion ──────────────────────────────────────
    if gc.steps_complete:
        out.print(f"  [green]✓ Steps:[/] {gc.done_count}/{gc.total_count} complete")
    else:
        all_pass = False
        out.print(
            f"  [red]✗ Steps:[/] {gc.done_count}/{gc.total_count} complete — "
            f"{len(gc.pending_steps)} remaining:"
        )
        for s in gc.pending_steps:
            icon, style = _STEP_STATUS_STYLE[s.status]
            suggested = f"  [dim]suggested: {s.agent}[/]" if s.agent else ""
            out.print(f"    [{style}]{icon}[/] {s.id} — {s.name}{suggested}")

    # ── Check 2: gate_script (CLI-only, subprocess) ───────────────────
    if gc.gate_script:
        out.print(f"\n  Running gate script: [bold]{gc.gate_script}[/]")
        try:
            result = sp.run(
                gc.gate_script,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(plan_path.parent),
            )
            if result.returncode == 0:
                out.print("  [green]✓ Gate script passed[/]")
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines()[:5]:
                        out.print(f"    {line}")
            else:
                all_pass = False
                out.print(f"  [red]✗ Gate script failed (exit {result.returncode})[/]")
                if result.stderr.strip():
                    for line in result.stderr.strip().splitlines()[:5]:
                        out.print(f"    [red]{line}[/]")
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines()[:5]:
                        out.print(f"    {line}")
        except sp.TimeoutExpired:
            all_pass = False
            out.print("  [red]✗ Gate script timed out (120s)[/]")
        except OSError as e:
            all_pass = False
            out.print(f"  [red]✗ Gate script error: {e}[/]")
    else:
        out.print("\n  [dim]No gate_script defined[/]")

    # ── Check 3: manual gate criteria ─────────────────────────────────
    if gc.gate_criterion:
        out.print("\n  [yellow]Manual gate criterion:[/]")
        out.print(f"  {gc.gate_criterion}")
        if _is_claim_consistency_scoped_gate(gc.phase_id):
            # Source: claim-consistency-recovery.integration-verify-fix Issue 2.
            out.print(
                "  [dim]Scoped verification evidence: interpret targeted integration "
                "output as scoped signal only (not full package-wide coverage proof).[/]"
            )
    else:
        out.print("\n  [dim]No manual gate criterion[/]")

    # ── Summary ───────────────────────────────────────────────────────
    out.print()
    if all_pass:
        out.print("[green bold]✓ Phase is gate-ready.[/]")
        if gc.downstream_locked:
            out.print(f"  Downstream phases: {', '.join(gc.downstream_locked)}")
            out.print("[dim]→ vectl complete will auto-unlock downstream phases[/]")
        out.print("[dim]→ vectl status                       View plan overview[/]")
    else:
        out.print("[red bold]✗ Phase is NOT gate-ready.[/]")
        out.print("[dim]→ vectl show <phase>                  Inspect phase details[/]")
        out.print("[dim]→ vectl next                          See claimable steps[/]")


# ---------------------------------------------------------------------------
# Clipboard Commands
# ---------------------------------------------------------------------------


def clipboard_write_cmd(
    author: str = typer.Option(..., "--author", "-a", help="Who is writing."),
    summary: str = typer.Option(..., "--summary", "-s", help="One-line description."),
    content: str = typer.Option(..., "--content", "-c", help="Payload content."),
    ttl: int = typer.Option(24, "--ttl", "-t", help="Time-to-live in hours (default 24)."),
    plan: Path | None = PlanOption,
) -> None:
    """Write to the plan clipboard.

    Overwrites any existing clipboard content. Use for cross-agent handoffs,
    broadcasts, or notes that don't follow DAG edges.
    """
    p, def_h, plan_path = _load(plan)

    try:
        p = clipboard_write(p, author, summary, content, ttl)
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: clipboard write by {author}")

    cb = p.clipboard
    assert cb is not None
    out.print("[green]Clipboard written.[/]")
    out.print(f"  Author: {cb.author}")
    out.print(f"  Summary: {cb.summary}")
    out.print(f"  Expires: {cb.expires_at}")


def clipboard_read_cmd(
    plan: Path | None = PlanOption,
) -> None:
    """Read the plan clipboard.

    Returns clipboard content if present and unexpired. Shows empty message
    if clipboard is empty or expired.
    """
    from vectl.core import _clipboard_expired

    p, _, _ = _load(plan)

    if p.clipboard is None:
        out.print("[dim]Clipboard is empty.[/]")
        return

    if _clipboard_expired(p.clipboard):
        from datetime import datetime, timezone

        try:
            expires = datetime.fromisoformat(p.clipboard.expires_at.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - expires).total_seconds() / 3600
            out.print("[dim]Clipboard is empty.[/]")
            out.print(
                f"[dim]Note: previous clipboard by {p.clipboard.author} expired "
                f"{hours_ago:.1f}h ago.[/]"
            )
        except (ValueError, AttributeError):
            out.print("[dim]Clipboard is empty.[/]")
        return

    cb = p.clipboard
    out.print(Panel("Clipboard", style="bold"))
    out.print(f"  [bold]Author:[/] {cb.author}")
    out.print(f"  [bold]Summary:[/] {cb.summary}")
    out.print(f"  [bold]Written:[/] {cb.written_at}")
    out.print(f"  [bold]Expires:[/] {cb.expires_at}")
    out.print()
    out.print(Panel(cb.content, title="Content", border_style="dim"))


def clipboard_clear_cmd(
    plan: Path | None = PlanOption,
) -> None:
    """Clear the plan clipboard."""
    p, def_h, plan_path = _load(plan)

    if p.clipboard is None:
        out.print("[dim]Clipboard was already empty.[/]")
        return

    p = clipboard_clear(p)
    _save_plan(p, plan_path, def_h, "vectl: clipboard clear")
    out.print("[green]Clipboard cleared.[/]")


# ---------------------------------------------------------------------------
# Dashboard command
# ---------------------------------------------------------------------------


def dashboard(
    output: Path = DashboardOutputOption,
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open the dashboard in a browser after generation.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Generate a static HTML dashboard for visual project overview.

    Creates a single-file HTML page with:
    - Phase navigation sidebar
    - Progress bars and status pills
    - Step tables with expandable details
    - Dependency graph visualization (DAG)

    Open the generated file in any browser to view. No server required.
    """
    import webbrowser

    p, _, _ = _load(plan)

    try:
        html = generate_dashboard(p)
    except Exception as e:
        _die(f"Failed to generate dashboard: {e}")
        return

    # Ensure parent directory exists
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    console.print(f"[green]Dashboard written to:[/] {output.resolve()}")

    if open_browser:
        # Use file:// URL for local file
        file_url = output.resolve().as_uri()
        webbrowser.open(file_url)
        console.print("[dim]Opening in browser...[/]")
