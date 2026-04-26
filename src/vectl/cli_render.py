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


def render(
    phase: str | None = typer.Option(None, "--phase", help="Render only this phase."),
    full: bool = typer.Option(
        False, "--full", help="Show complete step descriptions (no truncation)."
    ),
    output: Path | None = RenderOutputOption,
    plan: Path | None = PlanOption,
) -> None:
    """Render plan as Markdown (read-only export).

    Stakeholder report: phase progress, step status, one-line descriptions.
    Omits operational detail (claimed_by, rejection_history, etc.).
    Use --full to include complete step descriptions.
    """
    p, _, plan_path = _load(plan)

    try:
        md = render_plan(p, phase_id=phase, full=full)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    if output is not None:
        output.write_text(md, encoding="utf-8")
        console.print(f"[green]Written:[/] {output}")
    else:
        out.print(md)


def diff_cmd(
    ref: str = typer.Argument("HEAD", help="Git ref to compare against (default: HEAD)."),
    plan: Path | None = PlanOption,
) -> None:
    """Show plan changes since a git ref (default: last commit).

    Compares current plan.yaml against the version at the given git ref.
    Shows added/removed phases and steps, status transitions, and modifications.
    """
    import subprocess as sp

    p_new, _, plan = _load(plan)

    # Get old plan from git
    try:
        result = sp.run(
            ["git", "show", f"{ref}:./{plan.name}"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(plan.parent.resolve()),
        )
    except (sp.TimeoutExpired, OSError) as e:
        _die(f"Git error: {e}")
        return  # unreachable

    if result.returncode != 0:
        # Likely file didn't exist at that ref
        if "does not exist" in result.stderr or "not exist" in result.stderr:
            out.print(f"[dim]No plan.yaml at {ref} — showing current state as all new.[/]")
            from vectl.models import Plan as PlanModel

            p_old = PlanModel(project=p_new.project)
        else:
            _die(f"git show {ref}:./{plan.name} failed: {result.stderr.strip()}")
            return  # unreachable
    else:
        import yaml

        from vectl.models import Plan as PlanModel

        try:
            raw = yaml.safe_load(result.stdout)
            p_old = PlanModel(**raw)
        except Exception as e:
            _die(f"Failed to parse plan at {ref}: {e}")
            return  # unreachable

    diff = diff_plans(p_old, p_new)

    if not diff.has_changes:
        out.print(f"[dim]No changes since {ref}.[/]")
        return

    out.print(f"[bold]Changes since {ref}:[/]\n")

    if diff.phase_changes:
        out.print("[bold]Phases:[/]")
        for pc in diff.phase_changes:
            if pc.kind == "added":
                out.print(f"  [green]+[/] {pc.phase_id} — {pc.phase_name}")
            elif pc.kind == "removed":
                out.print(f"  [red]-[/] {pc.phase_id} — {pc.phase_name}")
            elif pc.kind == "status_changed":
                old_val = pc.old_status.value if pc.old_status else "?"
                new_val = pc.new_status.value if pc.new_status else "?"
                out.print(f"  [yellow]~[/] {pc.phase_id} — {old_val} → {new_val}")
        out.print()

    if diff.step_changes:
        out.print("[bold]Steps:[/]")
        for sc in diff.step_changes:
            if sc.kind == "added":
                out.print(f"  [green]+[/] {sc.step_id} — {sc.step_name} ({sc.phase_id})")
            elif sc.kind == "removed":
                out.print(f"  [red]-[/] {sc.step_id} — {sc.step_name} ({sc.phase_id})")
            elif sc.kind == "status_changed":
                old_val = sc.old_status.value if sc.old_status else "?"
                new_val = sc.new_status.value if sc.new_status else "?"
                out.print(f"  [yellow]~[/] {sc.step_id} — {old_val} → {new_val} ({sc.phase_id})")
            elif sc.kind == "modified":
                out.print(f"  [cyan]≈[/] {sc.step_id} — {sc.detail} ({sc.phase_id})")

    total = len(diff.phase_changes) + len(diff.step_changes)
    out.print(f"\n[dim]{total} change(s) total[/]")


def log_cmd(
    last: int = typer.Option(
        5, "--last", "-n", help="Number of recent commits to show (default: 5)."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Show recent plan mutations from git history.

    Reads git log filtered to plan.yaml commits and shows what changed
    in each commit. Reuses diff_plans for structured change detection.
    """
    import subprocess as sp

    plan_resolved = resolve_plan_path(plan)
    plan_dir = str(plan_resolved.parent)

    # Get recent commits that touched plan.yaml
    try:
        result = sp.run(
            ["git", "log", f"-{last}", "--format=%H|%ai|%s", "--", plan_resolved.name],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=plan_dir,
        )
    except (sp.TimeoutExpired, OSError) as e:
        _die(f"Git error: {e}")
        return  # unreachable

    if result.returncode != 0:
        _die(f"git log failed: {result.stderr.strip()}")
        return  # unreachable

    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        out.print("[dim]No git history for plan.yaml.[/]")
        return

    out.print(f"[bold]Plan history (last {len(lines)}):[/]\n")

    for i, line in enumerate(lines):
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        commit_hash, date, message = parts[0], parts[1], parts[2]
        short_hash = commit_hash[:8]
        short_date = date[:10]

        out.print(f"  [bold cyan]{short_hash}[/] {short_date}  {message}")

        # Compute diff for this commit vs its parent
        parent_ref = f"{commit_hash}~1"

        old_plan = _git_plan_at_ref(parent_ref, plan_resolved, plan_dir)
        new_plan = _git_plan_at_ref(commit_hash, plan_resolved, plan_dir)

        if old_plan is not None and new_plan is not None:
            diff = diff_plans(old_plan, new_plan)
            if diff.has_changes:
                for pc in diff.phase_changes:
                    if pc.kind == "added":
                        out.print(f"    [green]+phase[/] {pc.phase_id} ({pc.phase_name})")
                    elif pc.kind == "removed":
                        out.print(f"    [red]-phase[/] {pc.phase_id}")
                    elif pc.kind == "status_changed":
                        old_v = pc.old_status.value if pc.old_status else "?"
                        new_v = pc.new_status.value if pc.new_status else "?"
                        out.print(f"    [yellow]~phase[/] {pc.phase_id}: {old_v} → {new_v}")
                for sc in diff.step_changes:
                    if sc.kind == "added":
                        out.print(f"    [green]+step[/]  {sc.step_id} ({sc.step_name})")
                    elif sc.kind == "removed":
                        out.print(f"    [red]-step[/]  {sc.step_id}")
                    elif sc.kind == "status_changed":
                        old_v = sc.old_status.value if sc.old_status else "?"
                        new_v = sc.new_status.value if sc.new_status else "?"
                        out.print(f"    [yellow]~step[/]  {sc.step_id}: {old_v} → {new_v}")
                    elif sc.kind == "modified":
                        out.print(f"    [cyan]≈step[/]  {sc.step_id}: {sc.detail}")
            else:
                out.print("    [dim](no structural changes)[/]")
        elif new_plan is not None and old_plan is None:
            out.print("    [green](initial commit)[/]")
        else:
            out.print("    [dim](unable to parse)[/]")

        if i < len(lines) - 1:
            out.print()


def _git_plan_at_ref(ref: str, plan_path: Path, cwd: str) -> Plan | None:
    """Try to load plan.yaml at a given git ref. Returns None on failure."""
    import subprocess as sp

    import yaml

    try:
        result = sp.run(
            ["git", "show", f"{ref}:./{plan_path.name}"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except (sp.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    try:
        raw = yaml.safe_load(result.stdout)
        return Plan(**raw)
    except Exception:
        return None


_AGENTS_MD_LEGACY_HEADER = "## Plan Tracking (vectl)"
_AGENTS_MD_BEGIN = "<!-- VECTL:AGENTS:BEGIN -->"
_AGENTS_MD_END = "<!-- VECTL:AGENTS:END -->"

# Source:
#   - User instruction in this conversation (2026-02-12): no manual YAML edits, and
#     agreed safe AGENTS.md migration approach using begin/end markers.
_AGENTS_MD_SNIPPET = f"""\
{_AGENTS_MD_BEGIN}
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `vectl_guide` (CLI fallback: `vectl guide`)
Quick view: `vectl_status` (CLI fallback: `vectl status`)

### MCP vs CLI
- Source of truth: `plan.yaml` (channel-agnostic).
- **Always prefer MCP tools** (``vectl_status``, ``vectl_claim``,
  ``vectl_complete``, etc.) when available.
- CLI fallback priority: `uv run vectl` > `vectl` > `uvx vectl`.
- Evidence requirements are identical across MCP and CLI.

### Claim-time Guidance
- `vectl claim` may emit a bounded Guidance block delimited by:
  - `--- VECTL:GUIDANCE:BEGIN ---`
  - `--- VECTL:GUIDANCE:END ---`
- For automation/CI: use `vectl claim --no-guidance` to keep stdout clean.

### plan.yaml — Managed File (DO NOT EDIT DIRECTLY)

`plan.yaml` is exclusively owned by vectl. Direct edits (Edit, Write, sed, or
any file tool) **will** corrupt plan state — vectl performs CAS writes, lock
recalculation, and schema validation on every save, none of which run on direct
edits.

**To modify plan state, ONLY use:**
- MCP (preferred): `vectl_claim`, `vectl_complete`, `vectl_mutate`, etc.
- CLI (fallback): `uv run vectl claim`, `vectl claim`, or `uvx vectl claim`, etc.

If a vectl command fails, report the error — do **not** edit `plan.yaml`
directly as a workaround. Use `vectl guide stuck` for troubleshooting.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.

### Step ID Uniqueness
**Step IDs must be globally unique across ALL phases.**
- Example: `auth.login` and `api.login` are different step IDs.
- Example: Using just `login` in two phases creates a duplicate — not allowed.
- If you have legacy duplicate step IDs, use `vectl migrate-step-id --dry-run`
  to preview and `--yes` to repair.

### For Architects / Planners
- **Design Mode**: Run ``vectl_guide`` (CLI fallback:
  ``vectl guide --on planning``) to learn the Architect Protocol.
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof (e.g., "Paste logs here").
  - `--refs`: Pin specific files (e.g., "src/auth.py") to the worker's context.
{_AGENTS_MD_END}
"""

