"""MCP server exposing vectl tools to agents.

15 tools (8 consolidated per expert panel dec-001, plus guide, dag, clipboard,
init, render, check, and decide):
  1. vectl_status  — plan overview + next steps + mine
  2. vectl_show    — step/phase detail
  3. vectl_claim   — claim step (auto-claim supported)
  4. vectl_complete — complete step with evidence
  5. vectl_lifecycle — defer/reject/skip/skip-phase
  6. vectl_search  — search plan
  7. vectl_mutate  — add-step/edit-step/remove-step/move-step/edit-phase/add-phase
  8. vectl_review  — plan review + gate check
  9. vectl_guide   — agent onboarding guide (startup/stuck/review/planning/migration)
  10. vectl_dag    — dependency graph as Mermaid flowchart
  11. vectl_clipboard — cross-agent communication (write/read/clear)
  12. vectl_init   — initialize new vectl project (create plan.yaml + AGENTS.md/CLAUDE.md)
  13. vectl_render — render plan as Markdown stakeholder report
  14. vectl_check  — toggle/add checklist items in step descriptions
  15. vectl_decide — deterministic orchestration advisor (session reuse, status/reason_code)

Tools generally return Markdown-formatted text.

Feature request (2026-02-12): claim-time guidance ("Output guidance when running vectl claim").
To satisfy FR R6 (structured guidance output), vectl_claim returns a structured
payload including a Guidance object plus a Markdown rendition.
Plan path: resolved via shared plan_path.resolve_plan_path() —
  explicit/override path > VECTL_PLAN_PATH > VECTL_PLAN (deprecated) >
  linked-worktree main-root plan path (no local walk-up fallback, even when
  missing) > malformed linked-worktree probe fail-closed sentinel
  (absolute cwd/plan.yaml, no walk-up) > walk-up > ./plan.yaml.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel
from returns.result import Result

from vectl.claim_guidance import GuidancePayload, build_claim_guidance
from vectl.claims import get_current_branch, repair_claims
from vectl.core import (
    _SENTINEL,
    AgentsTarget,
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
    edit_phase,
    edit_plan,
    edit_step,
    format_lock_changes,
    gate_check,
    get_claimed_steps,
    get_next_steps,
    move_step,
    recalc_lock_status,
    reject_step,
    remove_step,
    render_plan,
    review_plan,
    search_plan,
    skip_phase,
    skip_step,
    update_checklist,
    upsert_agents_md,
    validate_plan,
)
from vectl.decide import decide as _decide_impl
from vectl.duplicate_step_id_format import (
    format_duplicate_step_id_diagnostics,
    format_duplicate_step_id_recommendation,
    get_duplicate_step_id_recommendation,
)
from vectl.io import (
    _backup_definition,
    _resolve_git_dir,
    load_plan_definition,
    save_plan,
)
from vectl.lifecycle import (
    AffinityOverrideMetadata,
    AffinityWarningMetadata,
    ClaimConflictError,
    ClaimConflictMetadata,
)
from vectl.models import (
    AffinityError,
    AmbiguousMatchError,
    CASConflictError,
    CompletedResult,
    InitResult,
    NoMatchError,
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    RunningTask,
    Step,
    StepStatus,
)
from vectl.plan_path import (
    is_linked_worktree,
    resolve_claims_path,
    resolve_plan_path,
)
from vectl.semantics import is_step_locked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    PhaseStatus.LOCKED: "🔒",
    PhaseStatus.PENDING: "○",
    PhaseStatus.IN_PROGRESS: "▶",
    PhaseStatus.DONE: "✓",
}

_STEP_ICON = {
    StepStatus.PENDING: "○",
    StepStatus.CLAIMED: "◉",
    StepStatus.DONE: "✓",
    StepStatus.SKIPPED: "⊘",
    StepStatus.REJECTED: "✗",
}


def _plan_path() -> Result[Path, str]:
    return resolve_plan_path()


def _load() -> Result[tuple[Plan, str], str]:
    """Load plan and definition hash from plan.yaml only.

    Source: docs/ADR-unified-state.md migration posture.

    Returns:
        (plan, definition_hash)
    """
    plan_path = _plan_path()
    return load_plan_definition(plan_path)


def _save_plan(
    plan: Plan,
    expected_def_hash: str,
    commit_message: str,
    *,
    recalc_locks: bool = True,
) -> Result[str, str]:
    """Save plan.yaml with CAS semantics.

    Args:
        plan: The plan to persist.
        expected_def_hash: CAS hash for plan.yaml.
        commit_message: Git commit message for best-effort plan commit.
        recalc_locks: Recompute phase lock status before save and include
            lock-change notice in return value.
    """
    changed: list[str] = []
    if recalc_locks:
        changed = recalc_lock_status(plan)

    plan_path = _plan_path()
    try:
        save_plan(
            plan,
            plan_path,
            expected_hash=expected_def_hash,
            commit_message=commit_message,
        )
    except CASConflictError as err:
        raise PlanError(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl_status` or `vectl_show`, then retry your mutation."
        ) from err

    # Create backup after successful save (non-blocking)
    try:
        _backup_definition(plan_path)
    except OSError:
        # Log via standard logging, backup failure should not block save
        import logging

        logging.warning("Backup failed", exc_info=True)

    return format_lock_changes(changed, plan)


# @shell_complexity: Step formatting preserves existing state, lock, expected-red, claim, and affinity display semantics.
def _fmt_step(plan: Plan, step: Step, phase_id: str) -> Result[str, str]:
    phase = plan.find_phase(phase_id)
    if phase and is_step_locked(plan, phase, step):
        icon = "🔒"
    elif step.status == StepStatus.DONE and step.verify == "expected_red":
        icon = "✓"
        status_text = "gap reproduced"
    else:
        icon = _STEP_ICON.get(step.status, "?")
        status_text = step.status.value
    claimed = f" (claimed by {step.claimed_by})" if step.claimed_by else ""
    agent = f" [suggested: {step.agent}]" if step.agent else ""
    if step.status == StepStatus.DONE and step.verify == "expected_red":
        return f"  {icon} **{step.id}** — {step.name} ({phase_id}){claimed}{agent} [{status_text}]"
    return f"  {icon} **{step.id}** — {step.name} ({phase_id}){claimed}{agent}"


def _fmt_phase_summary(plan: Plan) -> Result[str, str]:
    lines: list[str] = []
    for p in plan.phases:
        done = sum(1 for s in p.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        total = len(p.steps)
        icon = _STATUS_ICON.get(p.status, "?")
        deps = ", ".join(p.depends_on) if p.depends_on else "—"
        lines.append(f"| {icon} {p.status.value} | {p.id} | {p.name} | {done}/{total} | {deps} |")
    header = (
        "| Status | Phase | Name | Progress | Depends On |\n"
        "|--------|-------|------|----------|------------|"
    )
    return f"## Plan: {plan.project}\n\n{header}\n" + "\n".join(lines)


def _duplicate_id_diagnostics_lines(plan: Plan) -> Result[list[str], str]:
    """Format duplicate step-ID diagnostics for read-only MCP tools."""
    lines = format_duplicate_step_id_diagnostics(plan)
    if not lines:
        return []
    return ["## Duplicate Step-ID Diagnostics", "" , *lines]


def _duplicate_id_recommendation_lines(plan: Plan, step_id: str) -> Result[list[str], str]:
    """Format duplicate-ID repair recommendation for targeted step reads."""
    recommendation = get_duplicate_step_id_recommendation(plan, step_id)
    if recommendation is None:
        return []
    return [
        "",
        "## Duplicate-ID Repair Recommendation",
        *format_duplicate_step_id_recommendation(recommendation),
    ]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


# @shell_complexity: Phase-aware selection mirrors existing get_next_steps ordering without changing MCP output.
def _get_next_steps_with_phase(
    plan: Plan, agent: str | None = None
) -> Result[list[tuple[Phase, Step]], str]:
    """Get next steps with their containing phase.

    Returns list of (phase, step) tuples to correctly track phase membership
    for duplicate step IDs across different phases.

    Args:
        plan: The plan to query.
        agent: If provided, prioritize steps whose `agent` field matches.
    """
    from vectl.core import _get_active_phase_ids

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
# Tool 1: vectl_status
# ---------------------------------------------------------------------------



def vectl_checkpoint(
    agent: str | None = None,
    next: int = 3,
    include_guidance: bool = False,
    lite: bool = True,
    pretty: bool = False,
) -> Result[dict[str, Any], str]:
    """Output machine-readable plan checkpoint (JSON).

    Args:
        agent: Agent name (affects focus selection).
        next: Max next steps (default 3).
        include_guidance: Include guidance refs/templates (default False).
        lite: Minimize output (omit metadata, redundant active_steps). Default True.
        pretty: Pretty-print JSON (only affects rendering if returned as str, here returns dict).
    """
    from vectl.checkpoint import build_checkpoint

    _ = pretty

    plan, expected_def_hash = _load()

    # Note: 'pretty' is accepted for CLI parity but ignored for the dict return
    # since MCP clients handle formatting.
    return build_checkpoint(
        plan,
        file_hash=expected_def_hash,
        agent=agent,
        next_limit=next,
        include_guidance=include_guidance,
        lite=lite,
    )


# @shell_complexity: Review preserves layered L1-L4 report, active phase detail, and gate-check formatting.
def vectl_review(
    phase_id: str | None = None,
    check_refs: bool = False,
    include_done: bool = False,
) -> Result[str, str]:
    """Multi-layer plan review with optional gate check.

    Args:
        phase_id: If provided, also include gate readiness check for this phase.
        check_refs: If true, verify that ref file paths exist on disk.
        include_done: If true, include DONE and LOCKED phases in L3 detail.
    """
    plan, _ = _load()
    base_path = _plan_path().parent if check_refs else None
    result = review_plan(
        plan, check_refs=check_refs, base_path=base_path, include_done=include_done
    )

    parts: list[str] = []

    # L1: Validation
    parts.append("## L1: Validation\n")
    if not result.validation_issues:
        parts.append("✓ Plan is valid — 0 errors, 0 warnings")
    else:
        for e in result.errors:
            parts.append(f"ERROR: {e.message}")
        for w in result.warnings:
            parts.append(f"WARN: {w.message}")
        parts.append(f"\n{len(result.errors)} error(s), {len(result.warnings)} warning(s)")

    # L2: Phase progress
    parts.append("\n## L2: Phase Overview\n")
    parts.append("| Status | Phase | Progress |")
    parts.append("|--------|-------|----------|")
    for pp in result.phase_progress:
        icon = _STATUS_ICON.get(pp.status, "?")
        parts.append(
            f"| {icon} {pp.status.value} | {pp.phase_id} | {pp.done}/{pp.total} ({pp.pct:.0f}%) |"
        )
    parts.append(
        f"\n**Overall: {result.total_done}/{result.total_steps} ({result.overall_pct:.0f}%)**"
    )

    # L3: Active phases
    parts.append("\n## L3: Active Phases\n")
    if result.active_phases:
        for ph in result.active_phases:
            parts.append(f"### {ph.id} — {ph.name} ({ph.status.value})")
            if ph.depends_on:
                parts.append(f"Deps: {', '.join(ph.depends_on)}")
            if ph.gate:
                parts.append(f"Gate: {ph.gate}")
            for step in ph.steps:
                if is_step_locked(plan, ph, step):
                    icon = "🔒"
                elif step.status == StepStatus.DONE and step.verify == "expected_red":
                    icon = "✓"
                else:
                    icon = _STEP_ICON.get(step.status, "?")
                claimed = f" @{step.claimed_by}" if step.claimed_by else ""
                dep_info = f" deps={', '.join(step.depends_on)}" if step.depends_on else ""
                suggested = f" suggested={step.agent}" if step.agent else ""
                extra = (
                    " [gap reproduced]"
                    if step.status == StepStatus.DONE and step.verify == "expected_red"
                    else ""
                )
                parts.append(
                    f"  {icon} {step.id} — {step.name}{claimed}{suggested}{dep_info}{extra}"
                )
    else:
        parts.append("*No active phases.*")

    # L4: Spec coverage
    parts.append("\n## L4: Spec Coverage\n")
    if result.ref_index:
        for ref_path in sorted(result.ref_index):
            step_ids = result.ref_index[ref_path]
            parts.append(f"  {ref_path} ← {', '.join(step_ids)}")
    else:
        parts.append("*No refs defined in any steps.*")

    # Gate check (if phase_id provided)
    if phase_id:
        parts.append(f"\n## Gate Check: {phase_id}\n")
        if result.errors:
            parts.append(
                "✗ Gate check blocked: plan validation failed. "
                "Fix validation errors before evaluating gate readiness."
            )
        else:
            try:
                gc = gate_check(plan, phase_id)
                if gc.steps_complete:
                    parts.append(f"✓ Steps: {gc.done_count}/{gc.total_count} complete")
                else:
                    parts.append(
                        f"✗ Steps: {gc.done_count}/{gc.total_count} — "
                        f"{len(gc.pending_steps)} remaining:"
                    )
                    for s in gc.pending_steps:
                        icon = _STEP_ICON.get(s.status, "?")
                        suggested = f" suggested={s.agent}" if s.agent else ""
                        parts.append(f"  {icon} {s.id} — {s.name}{suggested}")
                if gc.gate_criterion:
                    parts.append(f"\nManual gate criterion: {gc.gate_criterion}")
                if gc.gate_script:
                    parts.append(
                        f"\n⚠ gate_script defined ({gc.gate_script}) but not executable via MCP. "
                        "Use CLI `vectl gate-check`."
                    )
                if gc.downstream_locked:
                    parts.append(f"\nDownstream phases: {', '.join(gc.downstream_locked)}")
            except PlanError as e:
                parts.append(f"\n**Gate Check Error:** {e}")

    diag_lines = _duplicate_id_diagnostics_lines(plan)
    if diag_lines:
        parts.append("")
        parts.extend(diag_lines)

    return "\n".join(parts)



def vectl_guide(topic: str | None = None) -> Result[str, str]:
    """Show agent onboarding guide.

    Args:
        topic: Optional topic to show. One of: startup, stuck, review, planning, migration.
            If omitted, returns all topics combined.
    """
    from vectl.guide import GUIDE_ALL, GUIDE_TOPICS, VALID_TOPICS

    if topic is None:
        combined = "\n---\n\n".join(g.strip() for g in GUIDE_ALL)
        combined += "\n\n---\n*Topics: " + ", ".join(VALID_TOPICS) + "*"
        return combined

    guide = GUIDE_TOPICS.get(topic)
    if guide is None:
        return f"**Error:** Unknown topic '{topic}'. Valid topics: {', '.join(VALID_TOPICS)}."

    return guide.strip()


# ---------------------------------------------------------------------------
# Tool 10: vectl_dag
# ---------------------------------------------------------------------------


def vectl_dag(phase_id: str | None = None) -> Result[str, str]:
    """Show dependency graph as Mermaid flowchart.

    Args:
        phase_id: Optional phase ID. If provided, shows step-level DAG
            within that phase. Otherwise shows phase-level DAG.
    """
    from vectl.core import generate_mermaid_dag

    plan, _ = _load()

    try:
        mmd = generate_mermaid_dag(plan, phase_id=phase_id)
    except PlanError as e:
        return f"**Error:** {e}"

    lines = format_duplicate_step_id_diagnostics(plan)
    if not lines:
        return mmd

    warning_lines = ["%% Duplicate Step-ID Diagnostics"]
    for line in lines:
        warning_lines.append(f"%% {line}")
    return "\n".join([*warning_lines, "", mmd])


# ---------------------------------------------------------------------------
# Tool 11: vectl_clipboard
# ---------------------------------------------------------------------------



def vectl_render(
    phase_id: str | None = None,
    full: bool = False,
) -> Result[str, str]:
    """Render plan as Markdown stakeholder report.

    Args:
        phase_id: Optional phase ID to filter to a specific phase.
        full: If True, show complete step descriptions without truncation.

    Returns:
        Markdown-formatted progress report.
    """
    plan, _ = _load()

    try:
        return render_plan(plan, phase_id=phase_id, full=full)
    except PlanError as e:
        return f"**Error:** {e}"


# ---------------------------------------------------------------------------
# Tool 14: vectl_check
# ---------------------------------------------------------------------------
