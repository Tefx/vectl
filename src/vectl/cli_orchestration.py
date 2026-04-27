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


class OrchOutputMode(str, enum.Enum):
    """CLI output mode for orchestration command surfaces."""

    HUMAN = "human"
    JSON = "json"
    JSONL = "jsonl"


class DriveProgressMode(str, enum.Enum):
    """Foreground drive progress rendering mode."""

    AUTO = "auto"
    PLAIN = "plain"
    RICH = "rich"
    NONE = "none"


class OrchMigrationState(str, enum.Enum):
    """CLI enum for legacy migration-state advancement."""

    PARALLEL = "parallel"
    PREFERRED = "preferred"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


OrchPlanOption = typer.Option(
    None,
    "--plan",
    "-p",
    help="Path to plan YAML file. Defaults to auto-discovery (walk-up). (env: VECTL_PLAN_PATH)",
)
OrchAgentOption = typer.Option(
    None,
    "--agent",
    "-a",
    help="Agent name to use for orchestration.",
)
OrchRunArgument = typer.Argument(None, help="Optional run selector.")
OrchConfigPathArgument = typer.Argument(
    None,
    help="Optional config file path (mapped to --plan).",
)
OrchOutputOption = typer.Option(
    OrchOutputMode.HUMAN,
    "--output",
    help="Output mode: human|json|jsonl",
    case_sensitive=False,
)
OrchJsonOption = typer.Option(False, "--json", help="Emit machine-readable JSON output.")
OrchJsonlOption = typer.Option(False, "--jsonl", help="Emit JSON Lines output.")
OrchDryRunOption = typer.Option(False, "--dry-run", help="Run in dry-run mode.")
OrchLatestOption = typer.Option(False, "--latest", help="Select latest run automatically.")
OrchWatchOption = typer.Option(False, "--watch", help="Poll once more before returning.")
OrchFollowOption = typer.Option(False, "--follow", help="Follow once more before returning.")
OrchMigrationStateArgument = typer.Argument(
    ...,
    help="Target migration state: parallel|preferred|deprecated|retired.",
)


def _build_orchestration_runtime_app(plan: Path | None) -> Any:
    """Compose orchestration app using frozen-capable runtime config wiring."""
    root_cli = sys.modules.get("vectl.cli")
    patched = getattr(root_cli, "_build_orchestration_runtime_app", None)
    if patched is not None and patched is not _build_orchestration_runtime_app:
        return patched(plan)

    from vectl.orch_app import AppConfig, build_orchestration_app
    from vectl.orchestration.config import load_orchestration_config

    loaded_config, _ = load_orchestration_config()
    if plan is not None:
        loaded_config = replace(loaded_config, plan_path=plan)

    app_config = AppConfig(
        plan_path=loaded_config.plan_path,
        worktree_base_dir=loaded_config.runtime.workspace_root,
        default_agent=loaded_config.dispatch.default_role_id,
        resolver_timeout_seconds=loaded_config.resolver.invocation_timeout_seconds,
        orchestration_config=loaded_config,
        run_store_root=loaded_config.runtime.artifact_root,
    )
    return build_orchestration_app(app_config)


def _resolve_orch_output_mode(
    *,
    output: OrchOutputMode,
    json_flag: bool,
    jsonl_flag: bool,
) -> OrchOutputMode:
    """Resolve one concrete output mode with conflict checks."""

    if json_flag and jsonl_flag:
        _die("Conflicting output flags: --json and --jsonl are mutually exclusive")
    if json_flag and output != OrchOutputMode.HUMAN:
        _die("Conflicting output flags: --json cannot be combined with --output")
    if jsonl_flag and output != OrchOutputMode.HUMAN:
        _die("Conflicting output flags: --jsonl cannot be combined with --output")
    if json_flag:
        return OrchOutputMode.JSON
    if jsonl_flag:
        return OrchOutputMode.JSONL
    return output


def _orch_failure_exit_code(message: str) -> int:
    """Map orchestration failure messages to documented exit codes.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6
    """

    normalized = message.lower()
    if "not found" in normalized or "no runs available" in normalized:
        return 2
    if "resume refused" in normalized or "recovery required" in normalized:
        return 4
    return 1


def _orch_die_on_failure(message: str) -> None:
    """Exit with explicit orchestration failure-class mapping."""

    _die(message, code=_orch_failure_exit_code(message))


def _orch_internal_error(exc: Exception) -> NoReturn:
    """Surface unexpected orchestration app exceptions as internal errors."""

    _die(f"Internal orchestration error: {exc}", code=5, cause=exc)


def _build_orchestration_runtime_app_or_die(plan: Path | None) -> Any:
    """Build orchestration runtime app with internal-error mapping."""

    try:
        return _build_orchestration_runtime_app(plan=plan)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)


def _enrich_drive_scope_result(result: ControlResult, drive_id: str) -> dict[str, Any]:
    """Enrich a ControlResult with drive-scoped discrimination metadata.

    Authority: docs/RFC-orch-drive.md §7.4 — flat surfaces must report
    ``scope_kind: "drive"`` and a drive identifier to allow operators to
    verify that ``--latest`` resolved to drive scope, not single-run scope.
    """
    payload: dict[str, Any] = asdict(result)
    payload["scope_kind"] = "drive"
    payload["drive_id"] = drive_id
    return payload


def _json_ready(value: Any) -> Any:
    """Convert CLI payload into JSON-serializable structure."""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def _drive_action_guidance(payload: dict[str, Any]) -> dict[str, Any]:
    """Add operator-action guidance to drive result payloads.

    This is deliberately a tiny presentation-layer helper: drive state remains
    owned by ``OrchestrationApp``/``DriveDriver`` and the CLI only adds explicit
    next-action fields for humans and JSON automation.
    """

    if "drive_id" not in payload or "status" not in payload:
        return payload
    if "reason_code" in payload and "suggested_action" in payload and "human_required" in payload:
        return payload

    status = str(payload.get("status") or "")
    summary = str(payload.get("summary") or "")
    summary_lower = summary.lower()
    conflict_text = "\n".join(str(note) for note in payload.get("conflict_resolutions") or ())
    conflict_lower = conflict_text.lower()
    failed_child_run_ids = payload.get("failed_child_run_ids") or ()

    reason_code = "ok"
    suggested_action = "continue drive supervision"
    human_required = False

    if "retry limit" in summary_lower or "retry limit" in conflict_lower:
        reason_code = "retry_limit"
        suggested_action = "inspect repeated child-run failures, adjust the step, then rerun `vectl orch drive`"
        human_required = True
    elif "automation stopped" in summary_lower:
        reason_code = "automation_stalled"
        suggested_action = "inspect the unchanged barrier/case, then rerun `vectl orch drive` after fixing the blocker"
        human_required = True
    elif failed_child_run_ids:
        reason_code = "drive_recovery_failed_children"
        suggested_action = "run `vectl orch drive-recover --latest --dry-run`, inspect stale child runs, then rerun drive"
        human_required = True
    elif "runtime non-closure" in summary_lower:
        reason_code = "runtime_nonclosure"
        suggested_action = "inspect the failed child run/case, create or run the remediation step, then rerun drive"
        human_required = True
    elif status == "blocked_operator":
        reason_code = "operator_required"
        suggested_action = "inspect drive cases and respond via `vectl orch case-*` or rerun `vectl orch drive-recover --latest --dry-run`"
        human_required = True
    elif status in {"resolving", "replanning"}:
        reason_code = "barrier_active"
        suggested_action = "drive will continue through resolver/planner; if stale, run `vectl orch drive-recover --latest --dry-run`"
    elif status == "recovering":
        reason_code = "recovery_needed"
        suggested_action = "rerun `vectl orch drive`; it will attempt safe recovery, or run `vectl orch drive-recover --latest --dry-run`"
    elif status in {"halted", "failed_unrecoverable", "stopped"}:
        reason_code = "terminal_attention_required"
        suggested_action = "inspect drive status/logs and start a new drive only after the terminal cause is understood"
        human_required = status != "stopped"
    elif status == "completed":
        reason_code = "completed"
        suggested_action = "no action required"

    enriched = dict(payload)
    enriched.setdefault("reason_code", reason_code)
    enriched.setdefault("suggested_action", suggested_action)
    enriched.setdefault("human_required", human_required)
    if human_required or reason_code not in {"ok", "completed"}:
        stop_reason = summary or suggested_action
        enriched.setdefault("stop_reason", f"{reason_code}: {stop_reason}"[:300])
    return enriched


def _drive_payload(value: Any) -> Any:
    """Convert and enrich drive payloads with action guidance."""

    payload = _json_ready(value)
    if isinstance(payload, dict):
        return _drive_action_guidance(payload)
    if isinstance(payload, list):
        return [_drive_action_guidance(item) if isinstance(item, dict) else item for item in payload]
    return payload


def _emit_orch_payload(value: Any, mode: OrchOutputMode) -> None:
    """Emit orchestration payload in human/json/jsonl formats."""

    payload = _drive_payload(value)

    if mode == OrchOutputMode.JSON:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    if mode == OrchOutputMode.JSONL:
        if isinstance(payload, list):
            for item in payload:
                typer.echo(json.dumps(item, sort_keys=True))
        else:
            typer.echo(json.dumps(payload, sort_keys=True))
        return

    if isinstance(payload, list):
        if not payload:
            out.print("[dim]No results.[/]")
            return
        for item in payload:
            if isinstance(item, dict):
                out.print(" ".join(f"{k}={_esc(str(v))}" for k, v in item.items()))
            else:
                out.print(_esc(str(item)))
        return

    if isinstance(payload, dict):
        out.print("\n".join(f"{k}={_esc(str(v))}" for k, v in payload.items()))
        return

    out.print(_esc(str(payload)))


def _format_duration(seconds: float | int | None) -> str:
    """Format seconds as compact HH:MM:SS/MM:SS text for CLI progress."""

    if seconds is None:
        return "--:--"
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _drive_event_time(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return time.strftime("%H:%M:%S", time.localtime(timestamp))
    return time.strftime("%H:%M:%S")


def _drive_progress_label(progress: DriveProgressMode) -> DriveProgressMode:
    if progress is DriveProgressMode.AUTO:
        return DriveProgressMode.RICH if out.is_terminal else DriveProgressMode.PLAIN
    return progress


def _emit_drive_status_snapshot(event: dict[str, Any], *, rich_mode: bool) -> None:
    status = str(event.get("status", "unknown")).upper()
    drive_id = str(event.get("drive_id", ""))
    elapsed = _format_duration(cast(float | int | None, event.get("elapsed_seconds")))
    done = event.get("done_steps", 0)
    total = event.get("total_steps", 0)
    running = event.get("running_count", 0)
    capacity = event.get("max_parallelism", "?")
    ready = event.get("ready_count", 0)
    blocked = event.get("blocked_count", 0)
    cases = event.get("case_count", 0)
    summary = str(event.get("summary", ""))
    header = f"Drive {drive_id}  {status}  {elapsed}"
    row = (
        f"Plan: {done}/{total} complete | Running: {running}/{capacity} | "
        f"Queue: {ready} ready | Blocked: {blocked} | Cases: {cases}"
    )
    if rich_mode:
        style = "green" if status == "COMPLETED" else "yellow" if cases else "cyan"
        out.print(Panel(f"{row}\n[dim]{_esc(summary)}[/]", title=_esc(header), border_style=style))
    else:
        out.print(header)
        out.print(row)
        if summary:
            out.print(f"summary={_esc(summary)}")


def _format_drive_progress_event(event: dict[str, Any], *, verbose: bool) -> str | None:
    event_type = str(event.get("type", ""))
    ts = _drive_event_time(event)
    step_id = str(event.get("step_id", ""))
    run_id = str(event.get("run_id", ""))

    if event_type == "drive_started":
        return (
            f"Starting drive {event.get('drive_id')}  mode=foreground  "
            f"max_parallelism={event.get('max_parallelism')}  "
            f"poll={event.get('poll_interval_seconds')}s  "
            f"progress={event.get('progress_mode', 'auto')}"
        )
    if event_type == "child_dispatched":
        detail = f" workspace={event.get('workspace')}" if verbose else ""
        return (
            f"{ts}  dispatched  {step_id} -> {event.get('agent')} {run_id}{detail}"
        )
    if event_type == "child_running":
        elapsed = _format_duration(cast(float | int | None, event.get("elapsed_seconds")))
        idle = _format_duration(cast(float | int | None, event.get("idle_seconds")))
        ws = event.get("workspace_changes", 0)
        workspace = f" workspace={event.get('workspace')}" if verbose else ""
        return (
            f"{ts}  still running  {step_id}  "
            f"elapsed={elapsed} idle={idle} ws={ws} files{workspace}"
        )
    if event_type == "child_completed":
        summary = str(event.get("summary", ""))
        if summary and not verbose:
            summary = summary[:160]
        return f"{ts}  completed   child {run_id} status={event.get('status')} {summary}".rstrip()
    if event_type == "step_completed":
        return f"{ts}  step done   {step_id}"
    if event_type == "case_opened":
        return (
            f"{ts}  case opened {event.get('case_id')}  from {step_id}  "
            f"reason={event.get('reason')}"
        )
    if event_type == "drive_blocked":
        return (
            f"BLOCKED  case resolution required\n"
            f"Case(s): {', '.join(cast(list[str], event.get('case_ids', [])))}\n"
            f"Reason: {event.get('summary')}\n"
            f"Next:\n  vectl orch case-list --latest\n  vectl orch drive-status --latest"
        )
    if event_type == "drive_terminal":
        status = str(event.get("status", "unknown")).upper()
        summary = str(event.get("summary", ""))
        if status == "COMPLETED":
            return f"COMPLETED  all reachable work finished\nSummary: {summary}"
        if status == "STOPPED":
            return f"STOPPED  external stop requested\nSummary: {summary}"
        return f"{status}  drive finished\nSummary: {summary}"
    return None


def _build_drive_progress_callback(
    *,
    mode: OrchOutputMode,
    progress: DriveProgressMode,
    quiet: bool,
    verbose: bool,
) -> Any | None:
    """Return an orch drive progress callback for human/jsonl modes."""

    resolved_progress = _drive_progress_label(progress)
    if mode == OrchOutputMode.JSON:
        return None

    if mode == OrchOutputMode.JSONL:
        def _jsonl(event: dict[str, Any]) -> None:
            typer.echo(json.dumps(_json_ready(event), sort_keys=True))

        return _jsonl

    if quiet or resolved_progress is DriveProgressMode.NONE:
        return None

    rich_mode = resolved_progress is DriveProgressMode.RICH

    def _human(event: dict[str, Any]) -> None:
        if event.get("type") == "status_snapshot":
            _emit_drive_status_snapshot(event, rich_mode=rich_mode)
            return
        line = _format_drive_progress_event(event, verbose=verbose)
        if line:
            if rich_mode and str(event.get("type", "")).startswith("drive_"):
                out.print(line)
            else:
                out.print(_esc(line))

    return _human


def _resolve_latest_run_id(app_runtime: Any) -> str | None:
    """Resolve latest non-terminal run ID from app boundary."""

    runs = app_runtime.runs(limit=200)
    for run in runs:
        if run.status in {"running", "pending", "stall"}:
            return run.run_id
    if runs:
        return runs[0].run_id
    return None


def _step_id_for_run(app_runtime: Any, run_id: str) -> str | None:
    """Resolve step ID for run selector via app read boundary."""
    root_cli = sys.modules.get("vectl.cli")
    patched = getattr(root_cli, "_step_id_for_run", None)
    if patched is not None and patched is not _step_id_for_run:
        return patched(app_runtime, run_id)

    for run in app_runtime.runs(limit=500):
        if run.run_id == run_id:
            return run.step_id
    return None


# --- vectl orch run ---


def orch_run(
    step_id: str | None = typer.Argument(
        None, help="Step ID to run (auto-selects next if omitted)."
    ),
    agent: str | None = OrchAgentOption,
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Start or resume an orchestration run.

    Contract authority: orch_app.py::OrchestrationApp.run()

    Authority: docs/RFC-orch-drive.md section 7.1
      - orch run must fail with exit code 2 if an active drive exists for
        the same plan
      - error text must include the active drive_id
      - error text must instruct the operator to use drive-scoped commands

    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6
      - exit 2 = not found / validation boundary for active-drive rejection
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: run start validation completed",
                "step_id": step_id,
                "agent": agent,
            },
            mode,
        )
        return
    try:
        result = app_runtime.run(step_id=step_id, agent=agent)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        # Authority: RFC-orch-drive.md section 7.1
        # "orch run must fail with exit code 2 if an active drive exists"
        message = result.message.lower()
        if "active drive exists" in message or "drive_id=" in message:
            _die(result.message, code=2)
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch resume ---


def orch_resume(
    run_id: str | None = typer.Argument(None, help="Run identifier to resume."),
    latest: bool = OrchLatestOption,
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Resume an existing orchestration run from artifacts.

    Contract authority: orch_app.py::OrchestrationApp.resume()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.1, §6
      §7.1 — resume accepts [RUN_ID|--latest]
      §6   — exit 2 = not found, exit 3 = validation
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    if run_id is None and not latest:
        _die("Resume requires explicit selector: provide RUN_ID or --latest", code=3)
    resolved_run_id = run_id
    if latest:
        resolved_run_id = _resolve_latest_run_id(app_runtime)
    if latest and resolved_run_id is None:
        _die("No runs available for --latest selector", code=2)
    if resolved_run_id is None:
        _die("Run selector required: provide RUN_ID or --latest")
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: resume validation completed",
                "run_id": resolved_run_id,
            },
            mode,
        )
        return
    assert resolved_run_id is not None
    try:
        result = app_runtime.resume(run_id=resolved_run_id)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch recover ---


def orch_recover(
    run_id: str | None = typer.Argument(None, help="Run identifier to recover."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Step ID to recover."),
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """Recover orchestration state from continuity artifacts.

    Contract authority: orch_app.py::OrchestrationApp.recover()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.1, §6
      §7.1 — recover accepts [RUN_ID|--latest]
      §6   — exit 2 = not found
    """
    del yes
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    if run_id is None and not latest:
        _die("Recover requires explicit selector: provide RUN_ID or --latest", code=3)
    resolved_run_id = run_id
    if latest:
        resolved_run_id = _resolve_latest_run_id(app_runtime)
    if latest and resolved_run_id is None:
        _die("No runs available for --latest selector", code=2)
    resolved_step_id = step_id
    if resolved_step_id is None and resolved_run_id is not None:
        resolved_step_id = _step_id_for_run(app_runtime, resolved_run_id)
    try:
        result = app_runtime.recover(step_id=resolved_step_id, dry_run=dry_run)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch runs ---


def orch_runs(
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    status: str | None = typer.Option(None, "--status", help="Optional run status filter."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum runs to show."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """List orchestration runs.

    Contract authority: orch_app.py::OrchestrationApp.runs()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    runs = app_runtime.runs(step_id=step_id, limit=limit)
    if status is not None:
        runs = tuple(run for run in runs if run.status == status)
    _emit_orch_payload(runs, mode)


# --- vectl orch prune ---


def orch_prune(
    before: float | None = typer.Option(None, "--before", help="Unix timestamp threshold."),
    older_than: int | None = typer.Option(
        None,
        "--older-than",
        help="Prune entries older than N days.",
    ),
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    force: bool = typer.Option(False, "--force", "-y", help="Skip confirmation prompt."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """Prune old runs and artifacts.

    Contract authority: orch_app.py::OrchestrationApp.prune()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    if older_than is not None and before is not None:
        _die("Specify either --before or --older-than, not both")
    resolved_before = before
    if older_than is not None:
        resolved_before = time.time() - float(older_than) * 86400.0
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: prune candidate scan completed",
                "before": resolved_before,
            },
            mode,
        )
        return
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    result = app_runtime.prune(before=resolved_before, force=force)
    if not result.success:
        _die(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch migration (cutover validate / state advance) ---


def orch_migration_validate_cutover(
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Validate cutover readiness against migration retirement criteria.

    Contract authority: recovery.py::CutoverValidator.validate_cutover_readiness()
    """

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    result = app_runtime.cutover_validate()
    _emit_orch_payload(result, mode)


def orch_migration_advance_state(
    status: OrchMigrationState = OrchMigrationStateArgument,
    legacy_run_id: str | None = typer.Option(
        None,
        "--legacy-run-id",
        help="Optional imported legacy run ID scope. Omit to update all imported runs.",
    ),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Advance imported legacy migration state via canonical bridge wiring.

    Contract authority: recovery.py::RunStoreLegacyRunBridge.set_migration_status()
    """

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    mapped_status: LegacyRunStatus
    if status is OrchMigrationState.PARALLEL:
        mapped_status = LegacyRunStatus.PARALLEL
    elif status is OrchMigrationState.PREFERRED:
        mapped_status = LegacyRunStatus.PREFERRED
    elif status is OrchMigrationState.DEPRECATED:
        mapped_status = LegacyRunStatus.DEPRECATED
    else:
        mapped_status = LegacyRunStatus.RETIRED

    result = app_runtime.migration_advance_state(
        status=mapped_status,
        legacy_run_id=legacy_run_id,
    )
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch inspect (status / events / logs / artifacts / actions) ---


def orch_inspect_status(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Step ID to inspect."),
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect current orchestration status.

    Contract authority: orch_app.py::OrchestrationApp.inspect_status()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.2, §6

    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive_id or child_run_id is provided, or --latest
    # selects a drive, or active drive exists as default, use drive inspection.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4 — "--latest resolves to
    # active drive scope, not single-run scope, when a drive exists."
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_status(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        _emit_orch_payload(payload, mode)
        if watch:
            payload = app_runtime.inspect_drive_status(
                drive_id=resolved_drive_id, child_run_id=child_run_id
            )
            _emit_orch_payload(payload, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if latest and resolved_run is None:
        _die("No runs available for --latest selector", code=2)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_status(step_id=step_id)
    _emit_orch_payload(payload, mode)
    if watch:
        payload = app_runtime.inspect_status(step_id=step_id)
        _emit_orch_payload(payload, mode)


def orch_inspect_events(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    follow: bool = OrchFollowOption,
    jsonl_flag: bool = OrchJsonlOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum events to show."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect orchestration events.

    Contract authority: orch_app.py::OrchestrationApp.inspect_events()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=jsonl_flag)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_events(
            drive_id=resolved_drive_id, child_run_id=child_run_id, limit=limit
        )
        _emit_orch_payload(payload.data, mode)
        if follow:
            payload = app_runtime.inspect_drive_events(
                drive_id=resolved_drive_id, child_run_id=child_run_id, limit=limit
            )
            _emit_orch_payload(payload.data, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_events(step_id=step_id, limit=limit)
    _emit_orch_payload(payload.data, mode)
    if follow:
        payload = app_runtime.inspect_events(step_id=step_id, limit=limit)
        _emit_orch_payload(payload.data, mode)


def orch_inspect_logs(
    latest: bool = OrchLatestOption,
    tail: int = typer.Option(100, "--tail", help="Show only the last N log lines."),
    follow: bool = OrchFollowOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID."),
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect run logs.

    Contract authority: orch_app.py::OrchestrationApp.inspect_logs()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either --run or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_logs(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data[-tail:] if tail >= 0 else payload.data
        _emit_orch_payload(rows, mode)
        if follow:
            rows = app_runtime.inspect_drive_logs(
                drive_id=resolved_drive_id, child_run_id=child_run_id
            ).data[-tail:]
            _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    payload = app_runtime.inspect_logs(run_id=resolved_run, step_id=step_id)
    rows = payload.data[-tail:] if tail >= 0 else payload.data
    _emit_orch_payload(rows, mode)
    if follow:
        rows = app_runtime.inspect_logs(run_id=resolved_run, step_id=step_id).data[-tail:]
        _emit_orch_payload(rows, mode)


def orch_inspect_artifacts(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    kind: str | None = typer.Option(None, "--kind", help="Optional artifact kind filter token."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect derived artifacts from runs.

    Contract authority: orch_app.py::OrchestrationApp.inspect_artifacts()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_artifacts(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data
        if kind is not None:
            rows = tuple(row for row in rows if f"kind={kind}" in row or kind in row)
        _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_artifacts(step_id=step_id)
    rows = payload.data
    if kind is not None:
        rows = tuple(row for row in rows if f"kind={kind}" in row or kind in row)
    _emit_orch_payload(rows, mode)


def orch_inspect_actions(
    latest: bool = OrchLatestOption,
    status: str | None = typer.Option(None, "--status", help="Action status filter token."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect actions taken during a run.

    Contract authority: orch_app.py::OrchestrationApp.inspect_actions()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either --run or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=False,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_actions(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data
        if status is not None:
            rows = tuple(row for row in rows if f"status={status}" in row)
        _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is None:
        _die("Run selector required for action inspection: use --run or --latest")
    payload = app_runtime.inspect_actions(run_id=resolved_run)
    rows = payload.data
    if status is not None:
        rows = tuple(row for row in rows if f"status={status}" in row)
    _emit_orch_payload(rows, mode)


# --- vectl orch case (list / show / respond) ---


def orch_case_list(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status: open, resolved, halt.",
    ),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """List cases (blocked/unresolved situations).

    Contract authority: orch_app.py::OrchestrationApp.case_list()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided or active drive exists
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        # Use drive status to get blocked case IDs
        allowed_statuses = {"open", "resolved", "halt"}
        if status is not None and status not in allowed_statuses:
            _die("Invalid --status value. Expected one of: open, resolved, halt")
        drive_status = app_runtime.drive_status(drive_id=resolved_drive_id)
        rows = (
            tuple(f"case_id={cid} status=open" for cid in drive_status.blocked_case_ids)
            if status in (None, "open")
            else ()
        )
        _emit_orch_payload(rows, mode)
        if watch:
            drive_status = app_runtime.drive_status(drive_id=resolved_drive_id)
            rows = (
                tuple(f"case_id={cid} status=open" for cid in drive_status.blocked_case_ids)
                if status in (None, "open")
                else ()
            )
            _emit_orch_payload(rows, mode)
        return

    # Legacy path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    del resolved_run
    allowed_statuses = {"open", "resolved", "halt"}
    if status is not None and status not in allowed_statuses:
        _die("Invalid --status value. Expected one of: open, resolved, halt")
    typed_status = cast(
        Literal["open", "resolved", "halt"] | None,
        status if status in allowed_statuses else None,
    )
    payload = app_runtime.case_list(status=typed_status)
    _emit_orch_payload(payload, mode)
    if watch:
        _emit_orch_payload(app_runtime.case_list(status=typed_status), mode)


def orch_case_show(
    case_id: str = typer.Argument(..., help="Case identifier to show."),
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show detail for a specific case.

    Contract authority: orch_app.py::OrchestrationApp.case_show()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    payload = app_runtime.case_show(case_id=case_id)
    _emit_orch_payload(payload, mode)
    if watch:
        _emit_orch_payload(app_runtime.case_show(case_id=case_id), mode)


def orch_case_respond(
    case_id: str = typer.Argument(..., help="Case identifier to respond to."),
    action: str | None = typer.Option(None, "--action", help="Bounded operator action token."),
    data: str | None = typer.Option(None, "--data", help="Optional response payload string."),
    reason: str | None = typer.Option(None, "--reason", help="Optional operator reason."),
    response: str | None = typer.Option(
        None, "--response", "-r", help="Legacy response text alias."
    ),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Respond to a case (operator input to resolver).

    Contract authority: orch_app.py::OrchestrationApp.case_respond()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    resolved_response = response
    if resolved_response is None and action is not None:
        parts = [f"action={action}"]
        if data is not None:
            parts.append(f"data={data}")
        if reason is not None:
            parts.append(f"reason={reason}")
        resolved_response = " ".join(parts)
    if resolved_response is None:
        _die("Missing operator response: provide --action or --response")
    assert resolved_response is not None
    result = app_runtime.case_respond(case_id=case_id, response=resolved_response)
    if not result.success:
        _die(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch control (pause / unpause / stop) ---


def orch_control_pause(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", help="Optional pause reason."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Specific step to pause."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Pause orchestration (stop dispatching new work).

    Contract authority: orch_app.py::OrchestrationApp.control_pause()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided, --latest resolves to
    # active drive, or active drive exists as default (no explicit run/step).
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_pause(drive_id=resolved_drive_id, reason=reason)
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if step_id is None and resolved_run is not None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    try:
        result = app_runtime.control_pause(step_id=step_id, reason=reason)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


def orch_control_unpause(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", help="Optional unpause reason."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Specific step to unpause."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Unpause orchestration (resume dispatching).

    Contract authority: orch_app.py::OrchestrationApp.control_unpause()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided, --latest resolves to
    # active drive, or active drive exists as default (no explicit run/step).
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_unpause(drive_id=resolved_drive_id, reason=reason)
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if step_id is None and resolved_run is not None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    try:
        result = app_runtime.control_unpause(step_id=step_id, reason=reason)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


def orch_control_stop(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", "-r", help="Reason for stopping."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Immediate stop: cancel active step child runs (RFC §7.3.1).",
    ),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Stop orchestration entirely.

    Contract authority: orch_app.py::OrchestrationApp.control_stop()

    With --force, requests immediate stop semantics per RFC §7.3.1:
    cancel active step child runs, preserve artifacts, allow resolver/planner
    child runs to complete.

    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.3.1
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.3.1, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_stop(
                drive_id=resolved_drive_id, reason=reason, force=force
            )
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is None and latest:
        _die("No runs available for --latest selector", code=2)
    try:
        result = app_runtime.control_stop(run_id=resolved_run, reason=reason, force=force)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch config (show / validate / tools) ---


def orch_config_show(
    effective: bool = typer.Option(False, "--effective", help="Show effective merged config."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show current orchestration configuration.

    Contract authority: orch_app.py::OrchestrationApp.config_show()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    try:
        payload = app_runtime.config_show(effective=effective)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if mode == OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
        return
    _emit_orch_payload(payload, mode)


def orch_config_validate(
    path: Path | None = OrchConfigPathArgument,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Validate current orchestration configuration.

    Contract authority: orch_app.py::OrchestrationApp.config_validate()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    target_plan = path if path is not None else plan
    app_runtime = _build_orchestration_runtime_app(plan=target_plan)
    payload = app_runtime.config_validate()
    if mode == OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
    else:
        _emit_orch_payload(payload, mode)
    if not payload.validation_passed:
        raise typer.Exit(3)


def orch_config_tools(
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show registered tool families and allowlist.

    Contract authority: orch_app.py::OrchestrationApp.config_tools()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    payload = app_runtime.config_tools()
    if mode == OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
        return
    _emit_orch_payload(payload, mode)


# ---------------------------------------------------------------------------
# vectl orch drive: drive-scoped orchestration commands
# Authority: docs/RFC-orch-drive.md sections 7.2, 7.3
# ---------------------------------------------------------------------------


OrchDriveIdArgument = typer.Argument(
    None,
    help="Drive identifier (omit to use --latest).",
)
OrchMaxParallelismOption = typer.Option(
    4,
    "--max-parallelism",
    help="Maximum concurrent step child runs (1-32, default 4).",
)
OrchDryRunDriveOption = typer.Option(
    False, "--dry-run", help="Compute result without applying changes."
)
OrchChildRunIdOption = typer.Option(
    None,
    "--child-run-id",
    help="Child-run selector for drill-down (exclusive with DRIVE_ID and --latest).",
)
OrchDriveForceOption = typer.Option(
    False,
    "--force",
    help="Request immediate stop (cancel active step child runs).",
)


def _resolve_latest_drive_id(app_runtime: Any) -> str | None:
    """Resolve the latest drive ID from the orchestration app boundary.

    Authority: docs/RFC-orch-drive.md section 7.5

    Returns:
        The latest drive_id, or None if no drives exist.
    """
    return app_runtime.resolve_latest_drive_id()


def _resolve_optional_drive_scope(
    app_runtime: Any,
    *,
    drive_id: str | None,
    latest: bool,
    child_run_id: str | None = None,
    allow_active_drive_default: bool,
    legacy_run_id: str | None = None,
    legacy_step_id: str | None = None,
) -> str | None:
    """Resolve drive scope only when a drive selector is actually available.

    Authority: docs/RFC-orch-drive.md sections 7.3, 7.4

    ``--latest`` routes to drive scope only when a drive exists. Otherwise the
    flat CLI surface falls back to the legacy run-scoped ``--latest`` behavior.

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: Explicit drive selector.
        latest: Whether ``--latest`` was provided.
        child_run_id: Optional child-run selector.
        allow_active_drive_default: Whether missing selectors may default to
            the active drive for the plan.
        legacy_run_id: Legacy run selector for conflict/default checks.
        legacy_step_id: Legacy step selector for conflict/default checks.

    Returns:
        The resolved drive identifier when drive scope applies, else ``None``.

    Raises:
        typer.Exit: On selector conflicts or invalid child-run defaults.
    """
    if child_run_id is not None and (drive_id is not None or latest):
        _die("--child-run-id is exclusive with DRIVE_ID and --latest", code=2)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")

    if drive_id is not None:
        return drive_id

    if latest:
        return _resolve_latest_drive_id(app_runtime)

    active_drive_id = app_runtime.has_active_drive_for_plan()
    if child_run_id is not None:
        if active_drive_id is None:
            _die(
                "No active drive found. Provide DRIVE_ID, --latest, or --child-run-id",
                code=3,
            )
        return active_drive_id

    if not allow_active_drive_default or active_drive_id is None:
        return None

    if legacy_run_id is not None or legacy_step_id is not None:
        return None

    return active_drive_id


def _resolve_drive_selector_or_die(
    app_runtime: Any,
    drive_id: str | None,
    latest: bool,
    child_run_id: str | None = None,
) -> str:
    """Resolve a drive selector and validate selector exclusivity.

    Authority: docs/RFC-orch-drive.md sections 7.3, 7.4
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

    Selector composition rules (per RFC §7.4):
    - --child-run-id is exclusive with positional DRIVE_ID and --latest
    - When an active drive exists, inspection/control defaults target the drive
    - --latest selects the most recently updated drive for the plan

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: Explicit drive ID, or None.
        latest: Whether --latest was specified.
        child_run_id: Optional child-run selector for drill-down.

    Returns:
        The resolved drive_id string.

    Raises:
        typer.Exit: On selector conflict or missing selector.
    """
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
    )
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)

    return resolved_drive_id


def _validate_child_run_scope_or_die(
    app_runtime: Any,
    drive_id: str,
    child_run_id: str | None,
) -> None:
    """Validate that a child run belongs to the specified drive.

    Authority: docs/RFC-orch-drive.md section 7.4

    Per RFC: "a child-run selector that does not belong to the selected
    drive must fail with exit code 2."

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: The drive to validate against.
        child_run_id: The child run to validate, or None (no-op).
    """
    if child_run_id is None:
        return
    from vectl.orchestration.inspection_queries import ChildRunScopeError

    try:
        store = app_runtime._drive_store()
        from vectl.orchestration.inspection_queries import validate_child_run_in_drive

        validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)
    except ChildRunScopeError as exc:
        _die(str(exc), code=2)


def _drive_recovery_preview_has_work(preview: Any) -> bool:
    """Return True when a dry-run recovery preview found recoverable work."""

    if getattr(preview, "recovered_child_run_ids", ()):
        return True
    if getattr(preview, "failed_child_run_ids", ()):
        return True
    notes = tuple(getattr(preview, "conflict_resolutions", ()) or ())
    if any(note != "dry-run: no changes applied" for note in notes):
        return True
    return False


def _drive_recovery_preview_requires_operator(preview: Any) -> bool:
    """Conservatively identify recovery previews that should not auto-apply."""

    status = str(getattr(preview, "status", ""))
    if status in {"blocked_operator", "failed_unrecoverable", "halted", "stopped"}:
        return True
    notes = "\n".join(str(note) for note in getattr(preview, "conflict_resolutions", ()) or ())
    notes_lower = notes.lower()
    if any(token in notes_lower for token in ("merge conflict", "operator", "manual")):
        return True

    failed_child_run_ids = tuple(str(run_id) for run_id in getattr(preview, "failed_child_run_ids", ()) or ())
    if not failed_child_run_ids:
        return False

    # Auto-retry only the narrow stale-run case recover_drive can prove:
    # persisted pending/running child, no live process, no terminal artifact.
    # Everything else stays operator-owned.
    return not (
        "live process missing" in notes_lower
        and "no terminal artifact" in notes_lower
        and "stale" in notes_lower
        and all(run_id.lower() in notes_lower for run_id in failed_child_run_ids)
    )


def _auto_recover_active_drive(app_runtime: Any, drive_id: str) -> dict[str, Any] | None:
    """Attempt safe startup recovery for an already-active drive.

    Returns a blocking payload when recovery needs operator attention.  Returns
    ``None`` when no recovery is needed or safe recovery was applied.
    """

    preview = app_runtime.recover_drive(drive_id=drive_id, dry_run=True)
    if not _drive_recovery_preview_has_work(preview):
        return None
    if _drive_recovery_preview_requires_operator(preview):
        payload = _drive_action_guidance(_json_ready(preview))
        payload["auto_recovery"] = {
            "applied": False,
            "reason": "operator_required",
            "preview_summary": getattr(preview, "summary", ""),
        }
        payload["human_required"] = True
        payload["reason_code"] = payload.get("reason_code") or "recovery_requires_operator"
        return payload

    applied = app_runtime.recover_drive(drive_id=drive_id, dry_run=False)
    return {
        "drive_id": drive_id,
        "status": getattr(applied, "status", "running"),
        "summary": getattr(applied, "summary", "drive auto-recovery applied"),
        "auto_recovery": {
            "applied": True,
            "conflict_resolutions": tuple(getattr(applied, "conflict_resolutions", ()) or ()),
            "recovered_child_run_ids": tuple(getattr(applied, "recovered_child_run_ids", ()) or ()),
            "failed_child_run_ids": tuple(getattr(applied, "failed_child_run_ids", ()) or ()),
        },
    }


def _merge_auto_recovery_notice(result: Any, notice: dict[str, Any] | None) -> Any:
    """Attach an auto-recovery note to a drive result payload."""

    if notice is None:
        return result
    payload = _json_ready(result)
    if not isinstance(payload, dict):
        return result
    payload["auto_recovery"] = notice.get("auto_recovery", notice)
    return payload


def orch_drive(
    agent: str | None = OrchAgentOption,
    max_parallelism: int = OrchMaxParallelismOption,
    once: bool = typer.Option(
        False,
        "--once",
        help="Run one scheduling loop pass and exit instead of supervising in foreground.",
    ),
    poll_interval_seconds: float = typer.Option(
        2.0,
        "--poll-interval",
        help="Seconds between child-run polls in foreground mode.",
    ),
    status_interval_seconds: float = typer.Option(
        30.0,
        "--status-interval",
        help="Seconds between quiet-period progress summaries in foreground mode.",
    ),
    progress: DriveProgressMode = typer.Option(
        DriveProgressMode.AUTO,
        "--progress",
        help="Foreground progress renderer: auto|plain|rich|none.",
        case_sensitive=False,
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress foreground progress; print only the final result in human mode.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Include extra child-run and workspace detail in progress output.",
    ),
    jsonl_flag: bool = OrchJsonlOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Start or resolve a full-plan orchestration drive.

    Authority: docs/RFC-orch-drive.md section 7.2

    If an active drive already exists for the same plan, this command attaches
    to that drive instead of starting a duplicate.  This keeps foreground
    supervision/resolver recovery moving after a previous invocation stopped at
    a drive barrier.

    Contract authority: orch_app.py::OrchestrationApp.start_drive()
    """
    from vectl.orchestration.driver import DriveAdmissionError, MaxParallelismError

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=jsonl_flag)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if max_parallelism < 1 or max_parallelism > 32:
        _die(f"max-parallelism must be between 1 and 32, got {max_parallelism}", code=2)
    drive_id: str
    auto_recovery_notice: dict[str, Any] | None = None
    try:
        result = app_runtime.start_drive(agent=agent or "", max_parallelism=max_parallelism)
        drive_id = result.drive_id
    except DriveAdmissionError as exc:
        drive_id = exc.active_drive_id
        try:
            auto_recovery_notice = _auto_recover_active_drive(app_runtime, drive_id)
        except Exception as recover_exc:
            _orch_internal_error(recover_exc)
            return
        if auto_recovery_notice is not None and auto_recovery_notice.get("human_required"):
            _emit_orch_payload(auto_recovery_notice, mode)
            raise typer.Exit(4)
    except MaxParallelismError as exc:
        _die(str(exc), code=2)
        return
    except Exception as exc:
        _orch_internal_error(exc)
        return
    try:
        if once:
            loop_result = app_runtime.run_drive_loop(drive_id)
        else:
            progress_callback = _build_drive_progress_callback(
                mode=mode,
                progress=progress,
                quiet=quiet,
                verbose=verbose,
            )
            loop_result = app_runtime.run_drive_foreground(
                drive_id,
                poll_interval_seconds=poll_interval_seconds,
                status_interval_seconds=status_interval_seconds,
                progress_callback=progress_callback,
                progress_mode=progress.value,
                verbose=verbose,
            )
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if mode == OrchOutputMode.JSONL and not once:
        return
    if (
        mode == OrchOutputMode.HUMAN
        and not once
        and not quiet
        and progress is not DriveProgressMode.NONE
    ):
        return
    _emit_orch_payload(_merge_auto_recovery_notice(loop_result, auto_recovery_notice), mode)


def orch_drive_status(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show current drive status.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.drive_status()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive status requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.drive_status(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if result.summary.startswith("drive ") and "not found" in result.summary:
        _die(f"Drive not found: {resolved_drive_id}", code=2)
    _emit_orch_payload(result, mode)


def orch_drive_runs(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """List child runs belonging to a drive.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.drive_runs()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive runs requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        runs = app_runtime.drive_runs(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if not runs:
        _emit_orch_payload([], mode)
        return
    _emit_orch_payload(runs, mode)


def orch_drive_resume(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Resume an interrupted drive session.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.resume_drive()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive resume requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.resume_drive(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    _emit_orch_payload(result, mode)


def orch_drive_recover(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    dry_run: bool = OrchDryRunDriveOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_flag: bool = OrchJsonOption,
    output: OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Recover a drive from interrupted state.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.recover_drive()
    """
    del yes
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive recover requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.recover_drive(drive_id=resolved_drive_id, dry_run=dry_run)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    _emit_orch_payload(result, mode)
