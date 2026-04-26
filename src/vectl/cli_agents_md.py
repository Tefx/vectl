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
from vectl.core_plan_mutations import (
    _AGENTS_MD_BEGIN,
    _AGENTS_MD_END,
    _AGENTS_MD_LEGACY_HEADER,
    _AGENTS_MD_SNIPPET,
)

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



def _get_next_steps_with_phase(plan: Plan, agent: str | None = None) -> list[tuple[Phase, Step]]:
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


class AgentsTarget(str, enum.Enum):
    """Target file for the vectl agents-md section."""

    auto = "auto"
    agents = "agents"
    claude = "claude"


def _detect_agents_target(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> Path:
    """Detect the best target file for the vectl agents-md section.

    Args:
        directory: Project directory to scan.
        target: Explicit override. ``auto`` uses detection heuristics.

    Priority (when ``auto``):
    1. Existing file with vectl markers → use it (stability over detection).
    2. Existing file without markers → prefer AGENTS.md > CLAUDE.md.
    3. Neither exists → .claude/ dir present → CLAUDE.md; otherwise AGENTS.md.
    """
    agents_md = directory / "AGENTS.md"
    claude_md = directory / "CLAUDE.md"

    if target is AgentsTarget.agents:
        return agents_md
    if target is AgentsTarget.claude:
        return claude_md

    # Auto mode: existing file with markers wins (don't break working setups)
    for candidate in (agents_md, claude_md):
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            if _AGENTS_MD_BEGIN in content:
                return candidate

    # Existing file without markers (append target)
    if agents_md.exists():
        return agents_md
    if claude_md.exists():
        return claude_md

    # Fresh project: auto-detect Claude Code projects
    if (directory / ".claude").is_dir():
        return claude_md

    return agents_md


def _upsert_agents_md(directory: Path, target: AgentsTarget = AgentsTarget.auto) -> str:
    """Create or upsert vectl section in AGENTS.md or CLAUDE.md.

    Safety policy (agreed in this conversation, 2026-02-12):
    - If begin/end markers exist, replace that block.
    - If only legacy header exists (no markers), do not rewrite; append the new block.

    Target selection delegated to ``_detect_agents_target()``.

    Returns:
        A status message describing what was done.
    """
    target_path = _detect_agents_target(directory, target)

    if not target_path.exists():
        target_path.write_text(_AGENTS_MD_SNIPPET, encoding="utf-8")
        return f"Created {target_path.name}"

    content = target_path.read_text(encoding="utf-8")

    begin = content.find(_AGENTS_MD_BEGIN)
    end = content.find(_AGENTS_MD_END)
    if begin != -1 and end != -1 and begin < end:
        end_inclusive = end + len(_AGENTS_MD_END)
        new_content = content[:begin].rstrip() + "\n\n" + _AGENTS_MD_SNIPPET + "\n"
        new_content += content[end_inclusive:].lstrip()
        target_path.write_text(new_content, encoding="utf-8")
        return f"Updated {target_path.name} (replaced vectl block)"

    if _AGENTS_MD_LEGACY_HEADER in content:
        with target_path.open("a", encoding="utf-8") as f:
            f.write("\n\n" + _AGENTS_MD_SNIPPET)
        return f"Appended updated vectl block to {target_path.name} (legacy block preserved)"

    with target_path.open("a", encoding="utf-8") as f:
        f.write("\n\n" + _AGENTS_MD_SNIPPET)
    return f"Appended vectl section to {target_path.name}"


def agents_md_cmd(
    directory: Path = AgentsMdDirOption,
    target: str = AgentsMdTargetOption,
) -> None:
    """Upsert the vectl section in AGENTS.md or CLAUDE.md."""
    result = _upsert_agents_md(directory, AgentsTarget(target))
    out.print(result)


def _upsert_gitattributes(directory: Path) -> str:
    """Configure .gitattributes with plan.yaml merge driver (idempotent).

    Returns:
        A status message describing what was done.
    """
    gitattributes_path = directory / ".gitattributes"

    # Build the target line
    target_line = "plan.yaml merge=vectl"

    if not gitattributes_path.exists():
        gitattributes_path.write_text(f"{target_line}\n", encoding="utf-8")
        return "Created .gitattributes with plan.yaml merge driver"

    content = gitattributes_path.read_text(encoding="utf-8")

    # Check if plan.yaml merge driver is already configured
    for line in content.splitlines():
        stripped = line.strip()
        # Match lines like "plan.yaml merge=vectl" or "plan.yaml merge=vectl " (with trailing space)
        if stripped.startswith("plan.yaml") and "merge=vectl" in stripped:
            # Already configured
            return ".gitattributes already has plan.yaml merge driver"

    # Append the configuration
    with gitattributes_path.open("a", encoding="utf-8") as f:
        f.write(f"\n{target_line}\n")
    return "Updated .gitattributes with plan.yaml merge driver"


def init(
    project: str = typer.Option(..., "--project", prompt="Project name"),
    plan: Path | None = PlanOption,
    agents_target: str = InitTargetOption,
) -> None:
    """Create a new plan.yaml template and configure AGENTS.md / CLAUDE.md."""
    target = plan or Path("plan.yaml")
    if target.exists():
        _die(f"{target} already exists. Delete it first or use a different path.")

    template = Plan(
        project=project,
        context=f"Implementation plan for {project}.",
    )
    save_plan(template, target)
    out.print(f"[green]Created:[/] {target}")

    # Ensure AGENTS.md / CLAUDE.md has vectl section (idempotent)
    agents_result = _upsert_agents_md(target.parent, AgentsTarget(agents_target))
    out.print(f"[green]Agent instructions:[/] {agents_result}")

    # Configure .gitattributes with plan.yaml merge driver (idempotent)
    gitattr_result = _upsert_gitattributes(target.parent)
    out.print(f"[green]Git attributes:[/] {gitattr_result}")

    out.print()
    out.print("[dim]→ vectl add-phase --phase-id <id> --name <name>   Add a phase[/]")
    out.print("[dim]→ vectl guide                     Full onboarding guide[/]")


# ---------------------------------------------------------------------------
# cli.2: next
# ---------------------------------------------------------------------------
