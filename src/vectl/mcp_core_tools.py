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


def _plan_path() -> Path:
    return resolve_plan_path()


def _load() -> tuple[Plan, str]:
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
) -> str:
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


def _fmt_step(plan: Plan, step: Step, phase_id: str) -> str:
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


def _fmt_phase_summary(plan: Plan) -> str:
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


def _duplicate_id_diagnostics_lines(plan: Plan) -> list[str]:
    """Format duplicate step-ID diagnostics for read-only MCP tools."""
    lines = format_duplicate_step_id_diagnostics(plan)
    if not lines:
        return []
    return ["## Duplicate Step-ID Diagnostics", "" , *lines]


def _duplicate_id_recommendation_lines(plan: Plan, step_id: str) -> list[str]:
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


def _get_next_steps_with_phase(plan: Plan, agent: str | None = None) -> list[tuple[Phase, Step]]:
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



def vectl_status(agent: str | None = None) -> str:
    """Show plan status overview + next steps + optionally mine.

    Args:
        agent: If provided, also show steps claimed by this agent.
    """
    plan, _ = _load()

    parts: list[str] = [_fmt_phase_summary(plan)]

    # Next steps (prioritized by agent if provided)
    # Use _get_next_steps_with_phase to correctly track phase for duplicate step IDs
    available = _get_next_steps_with_phase(plan, agent=agent)
    if available:
        parts.append("\n## Next Available Steps\n")
        for phase, step in available[:3]:
            parts.append(_fmt_step(plan, step, phase.id))
        if len(available) > 3:
            parts.append(f"\n  ... and {len(available) - 3} more")
    else:
        parts.append("\n*No steps available — all phases may be done or locked.*")

    # Mine
    if agent:
        claimed = get_claimed_steps(plan, agent)
        if claimed:
            parts.append(f"\n## Claimed by {agent}\n")
            for phase_id, step in claimed:
                parts.append(_fmt_step(plan, step, phase_id))
        else:
            parts.append(f"\n*No steps claimed by {agent}.*")

    diag_lines = _duplicate_id_diagnostics_lines(plan)
    if diag_lines:
        parts.append("\n")
        parts.extend(diag_lines)

    # Auto-repair ghost claims (routine maintenance, not an error)
    try:
        plan_path = _plan_path()
        claims_path = resolve_claims_path(plan_path)
        repair_result = repair_claims(
            plan,
            plan_path,
            claims_path,
            dry_run=False,
        )
        if repair_result.changed:
            actions_summary = ", ".join(f"{a.action} {a.key}" for a in repair_result.actions)
            parts.append(f"\n> **Auto-repaired claims** (routine): {actions_summary}")
    except Exception:
        pass  # non-critical; don't block status

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 2: vectl_show
# ---------------------------------------------------------------------------


def vectl_show(target: str) -> str:
    """Show step or phase detail.

    Args:
        target: Step ID (e.g. 'core.validate') or phase ID (e.g. 'core').
    """
    plan, _ = _load()

    # Try step first
    found = plan.find_step(target)
    if found:
        phase, step = found
        locked = is_step_locked(plan, phase, step)
        if locked:
            status = "🔒 locked"
        elif step.status == StepStatus.DONE and step.verify == "expected_red":
            status = "✓ gap reproduced"
        else:
            status = f"{_STEP_ICON.get(step.status, '?')} {step.status.value}"
        lines = [
            f"## Step: {step.id}",
            f"**Name:** {step.name}",
            f"**Status:** {status}",
            f"**Phase:** {phase.id}",
        ]
        if step.agent:
            lines.append(f"**Suggested agent:** {step.agent}")
        if step.description:
            lines.append(f"**Description:** {step.description}")
        if step.depends_on:
            lines.append(f"**Depends on:** {', '.join(step.depends_on)}")
        if step.claimed_by:
            lines.append(f"**Claimed by:** {step.claimed_by} (at {step.claimed_at})")
        if step.evidence:
            lines.append(f"**Evidence:** {step.evidence}")
        if step.skipped_reason:
            lines.append(f"**Skip reason:** {step.skipped_reason}")
        if step.rejection_reason:
            lines.append(f"**Rejection reason:** {step.rejection_reason}")
        if step.verification:
            lines.append(f"**Verification:** {step.verification}")
        if step.refs:
            lines.append(f"**Refs:** {', '.join(step.refs)}")
        lines.extend(_duplicate_id_recommendation_lines(plan, step.id))
        return "\n".join(lines)

    # Try phase
    phase_obj = plan.find_phase(target)
    if phase_obj:
        lines = [
            f"## Phase: {phase_obj.id}",
            f"**Name:** {phase_obj.name}",
            f"**Status:** {_STATUS_ICON.get(phase_obj.status, '?')} {phase_obj.status.value}",
        ]
        if phase_obj.depends_on:
            lines.append(f"**Depends on:** {', '.join(phase_obj.depends_on)}")
        if phase_obj.context:
            lines.append(f"**Context:** {phase_obj.context}")
        if phase_obj.gate:
            lines.append(f"**Gate:** {phase_obj.gate}")
        if phase_obj.steps:
            lines.append("\n### Steps\n")
            for step in phase_obj.steps:
                lines.append(_fmt_step(plan, step, phase_obj.id))
        diag_lines = _duplicate_id_diagnostics_lines(plan)
        if diag_lines:
            lines.append("")
            lines.extend(diag_lines)
        return "\n".join(lines)

    return f"**Error:** '{target}' not found as step or phase."


# ---------------------------------------------------------------------------
# Tool 3: vectl_claim
# ---------------------------------------------------------------------------


class ClaimStepData(BaseModel):
    """Claimed step summary.

    Source: FR R6 — MCP structured output should include guidance and step metadata.
    """

    step_id: str
    step_name: str
    phase_id: str
    phase_name: str
    claimed_by: str
    suggested_agent: str | None = None
    affinity_override: bool = False


class ClaimResponseEnvelope(BaseModel):
    """Structured response for vectl_claim.

    Source: FR R6 — return structured fields; clients can decide how to display.
    Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-004 —
    keep MCP as a thin transport envelope around lifecycle-owned claim metadata.
    """

    ok: bool
    markdown: str
    claimed: ClaimStepData | None = None
    guidance: GuidancePayload | None = None
    affinity_warning: AffinityWarningMetadata | None = None
    affinity_override: AffinityOverrideMetadata | None = None
    error: str | None = None
    error_code: str | None = None
    duplicate_step_id_recommendation: dict[str, Any] | None = None
    claim_conflict: ClaimConflictMetadata | None = None


def vectl_claim(
    agent: str, step_id: str | None = None, guidance: bool = True, force: bool = False
) -> dict:
    """Claim a step.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        agent: Agent name claiming the step.
        step_id: Step ID to claim. If omitted, auto-selects first available.
        guidance: If True, include claim-time guidance (refs + evidence template).
        force: If True, override exclusive affinity violations (sets audit trail).

    Returns:
        Structured result with claimed step, affinity warnings, and guidance.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    if step_id is None:
        available = get_next_steps(plan, agent=agent)
        if not available:
            err = "No steps available to claim."
            return ClaimResponseEnvelope(
                ok=False,
                markdown=f"**Error:** {err}",
                error=err,
            ).model_dump(mode="json", exclude_none=True)
        step_id = available[0].id

    lock_notice: str = ""
    try:
        plan, result = claim_step(plan, step_id, agent, force=force, claims_path=claims_path)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: claim step {step_id} by {agent}",
        )
    except ClaimConflictError as e:
        # Claim conflict: existing claim record blocking this attempt
        err = str(e)
        conflict_md_lines = [
            f"**Claim Conflict:** {e.step_id} is already claimed on branch '{e.branch}'",
            f"**Claimed by:** {e.claimant}",
            f"**Claimed at:** {e.claimed_at}",
            "",
            "**Next Steps:**",
            f"1. Inspect: `vectl_show` step_id={e.step_id}",
            "2. If this is a stale/ghost claim (common after agent restarts), "
            "run `vectl_repair_claims()` to fix it — this is routine maintenance, not an error.",
        ]
        return ClaimResponseEnvelope(
            ok=False,
            markdown="\n".join(conflict_md_lines),
            error=err,
            error_code="claim_conflict",
            claim_conflict=e.metadata,
        ).model_dump(mode="json", exclude_none=True)
    except AffinityError as e:
        # RFC: docs/RFC-affinity.md
        # Exclusive affinity violation
        err = str(e)
        return ClaimResponseEnvelope(
            ok=False,
            markdown=f"**Affinity Error:** {err}",
            error=err,
            error_code="affinity_violation",
        ).model_dump(mode="json", exclude_none=True)
    except PlanError as e:
        err = str(e)
        error_code = None
        if "error_code=duplicate_step_id_ambiguous_target" in err:
            error_code = "duplicate_step_id_ambiguous_target"
        elif "error_code=duplicate_step_id_auto_migrate_required" in err:
            error_code = "duplicate_step_id_auto_migrate_required"

        recommendation_payload: dict[str, Any] | None = None
        if error_code == "duplicate_step_id_ambiguous_target" and step_id is not None:
            recommendation = get_duplicate_step_id_recommendation(plan, step_id)
            if recommendation is not None:
                recommendation_payload = {
                    "type": recommendation.type,
                    "step_id": recommendation.step_id,
                    "duplicates": [
                        {"phase": duplicate.phase} for duplicate in recommendation.duplicates
                    ],
                    "resolution_path": {
                        "explicit_phase": recommendation.resolution_path.explicit_phase,
                        "auto_migrate_flag": recommendation.resolution_path.auto_migrate_flag,
                        "migration_tool": recommendation.resolution_path.migration_tool,
                    },
                }

        markdown_lines = [f"**Error:** {err}"]
        if recommendation_payload is not None:
            markdown_lines.extend(
                [
                    "",
                    "Structured repair recommendation:",
                    f"- type: {recommendation_payload['type']}",
                    f"- step_id: {recommendation_payload['step_id']}",
                ]
            )
            duplicates = recommendation_payload["duplicates"]
            for duplicate in duplicates:
                markdown_lines.append(f"  - phase: {duplicate['phase']}")

            resolution_path = recommendation_payload["resolution_path"]
            markdown_lines.extend(
                [
                    "- resolution_path:",
                    f"  - explicit_phase: {resolution_path['explicit_phase']}",
                    f"  - auto_migrate_flag: {resolution_path['auto_migrate_flag']}",
                    f"  - migration_tool: {resolution_path['migration_tool']}",
                ]
            )

        markdown = "\n".join(markdown_lines)
        return ClaimResponseEnvelope(
            ok=False,
            markdown=markdown,
            error=err,
            error_code=error_code,
            duplicate_step_id_recommendation=recommendation_payload,
        ).model_dump(mode="json", exclude_none=True)

    found = plan.find_step(step_id)
    if found:
        phase, step = found

        claimed = ClaimStepData(
            step_id=step.id,
            step_name=step.name,
            phase_id=phase.id,
            phase_name=phase.name,
            claimed_by=agent,
            suggested_agent=step.agent,
            affinity_override=step.affinity_override,
        )

        md_lines: list[str] = [
            f"**Claimed:** {step.id} — {step.name}",
            f"**Phase:** {phase.id}",
            f"**Claimed by:** {agent}",
        ]
        if step.agent:
            md_lines.append(f"**Suggested agent:** {step.agent}")
        md_lines.append("")
        md_lines.append("→ Use `vectl_complete` with evidence when done.")
        md_lines.append("→ Use `vectl_lifecycle` action=defer to release.")
        md_lines.append("")

        # RFC: docs/RFC-affinity.md
        # Build affinity warning/override data
        affinity_warning_data: AffinityWarningMetadata | None = None
        affinity_override_data: AffinityOverrideMetadata | None = None

        if result.warning_message:
            if result.affinity_override:
                affinity_override_data = result.affinity_override_metadata
                md_lines.append(f"⚠️ **Affinity Override:** {result.warning_message}")
            elif result.affinity_warning:
                affinity_warning_data = result.affinity_warning_metadata
                md_lines.append(f"⚠️ **Affinity Warning:** {result.warning_message}")
            md_lines.append("")

        gp: GuidancePayload | None = None
        if guidance:
            gp = build_claim_guidance(plan, phase, step)
            md_lines.append(gp.markdown.rstrip())

        markdown = "\n".join(md_lines).rstrip() + "\n"
        if lock_notice:
            markdown = markdown.rstrip("\n") + f"\n\n{lock_notice}\n"
        return ClaimResponseEnvelope(
            ok=True,
            markdown=markdown,
            claimed=claimed,
            guidance=gp,
            affinity_warning=affinity_warning_data,
            affinity_override=affinity_override_data,
        ).model_dump(mode="json", exclude_none=True)

    fallback_markdown = f"Claimed: {step_id}\n"
    if lock_notice:
        fallback_markdown = fallback_markdown.rstrip("\n") + f"\n\n{lock_notice}\n"
    return ClaimResponseEnvelope(
        ok=True,
        markdown=fallback_markdown,
    ).model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Tool 4: vectl_complete
# ---------------------------------------------------------------------------


def vectl_complete(step_id: str, evidence: str) -> str:
    """Complete a step.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        step_id: The step ID to complete.
        evidence: Description of what was done and verification.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    try:
        plan = complete_step(plan, step_id, evidence, claims_path=claims_path)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: complete step {step_id}",
        )
    except PlanError as e:
        return f"**Error:** {e}"

    # Check if phase completed
    found = plan.find_step(step_id)
    parts = [f"**Completed:** {step_id}"]
    if found:
        phase, _ = found
        if phase.status == PhaseStatus.DONE:
            parts.append(f"**Phase '{phase.id}' is now DONE!**")

    # Show next available - use _get_next_steps_with_phase for correct phase tracking
    available = _get_next_steps_with_phase(plan)
    if available:
        parts.append("\n**Next available:**")
        for phase, step in available[:3]:
            parts.append(_fmt_step(plan, step, phase.id))
        if len(available) > 3:
            parts.append(f"  ... and {len(available) - 3} more")

    if lock_notice:
        parts.append(f"\n{lock_notice}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 5: vectl_lifecycle
# ---------------------------------------------------------------------------


def vectl_lifecycle(
    action: Literal["defer", "reject", "skip", "skip-phase", "complete-phase"],
    target: str,
    reason: str = "",
    evidence: str = "",
    reviewer: str = "",
    force: bool = False,
) -> str:
    """Change step/phase lifecycle state.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        action: One of: defer, reject, skip, skip-phase, complete-phase.
        target: Step ID (for defer/reject/skip) or phase ID (for skip-phase/complete-phase).
        reason: Required for reject, skip, skip-phase. For skip/skip-phase
            must be: superseded, irrelevant, absorbed, deprioritized.
        evidence: Required for complete-phase.
        reviewer: Optional reviewer name for reject.
        force: For skip-phase, allow skipping a locked phase with remaining steps.
            Empty phases (0 steps) are always allowed regardless of lock.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    try:
        if action == "defer":
            plan = defer_step(plan, target, claims_path=claims_path)
            msg = f"**Deferred:** {target} → back to pending"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: defer step {target}",
            )
        elif action == "reject":
            if not reason:
                return "**Error:** reason is required for reject."
            plan = reject_step(plan, target, reason, reviewer)
            msg = f"**Rejected:** {target} — {reason}"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: reject step {target}",
            )
        elif action == "skip":
            if not reason:
                return (
                    "**Error:** reason is required for skip "
                    "(superseded/irrelevant/absorbed/deprioritized)."
                )
            plan = skip_step(plan, target, reason)
            msg = f"**Skipped:** {target} — {reason}"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: skip step {target}",
            )
        elif action == "skip-phase":
            if not reason:
                return "**Error:** reason is required for skip-phase."
            plan, skipped_ids = skip_phase(plan, target, reason, force=force)
            msg = f"**Skipped phase '{target}':** {len(skipped_ids)} steps skipped"
            if skipped_ids:
                msg += "\n" + "\n".join(f"  - {sid}" for sid in skipped_ids)
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: skip phase {target}",
            )
        elif action == "complete-phase":
            if not evidence:
                return "**Error:** evidence is required for complete-phase."
            plan, unlocked = complete_phase(plan, target, evidence)
            msg = f"**Completed phase '{target}':** {len(unlocked)} phase(s) unlocked"
            if unlocked:
                msg += "\n" + "\n".join(f"  - {pid}" for pid in unlocked)
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: complete phase {target}",
            )
        else:
            return (
                f"**Error:** Unknown action '{action}'. Use: defer, reject, skip, "
                "skip-phase, complete-phase."
            )
    except PlanError as e:
        return f"**Error:** {e}"

    if lock_notice:
        msg += f"\n\n{lock_notice}"
    return msg


# ---------------------------------------------------------------------------
# Tool 6: vectl_search
# ---------------------------------------------------------------------------


def vectl_search(pattern: str, phase_id: str | None = None, regex: bool = False) -> str:
    """Search the plan.

    Args:
        pattern: Search pattern (substring or regex).
        phase_id: Restrict search to a specific phase.
        regex: If True, interpret pattern as regex.
    """
    plan, _ = _load()

    try:
        matches = search_plan(plan, pattern, phase_id=phase_id, use_regex=regex)
    except PlanError as e:
        return f"**Error:** {e}"

    if not matches:
        return f"*No matches for '{pattern}'.*"

    lines = [f"## Search: {pattern}\n", f"**{len(matches)} match(es)**\n"]
    for match in matches[:20]:
        loc = match.step_id or match.phase_id
        lines.append(f"- **{loc}** ({match.field}) — {match.snippet}")
    if len(matches) > 20:
        lines.append(f"\n  ... and {len(matches) - 20} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7: vectl_mutate
# ---------------------------------------------------------------------------


def vectl_mutate(
    action: Literal[
        "add-step",
        "add-steps",
        "edit-step",
        "remove-step",
        "move-step",
        "add-phase",
        "edit-phase",
        "edit-plan",
    ],
    phase_id: str = "",
    step_id: str = "",
    name: str = "",
    description: str = "",
    depends_on: list[str] | None = None,
    add_refs: list[str] | None = None,
    remove_refs: list[str] | None = None,
    refs: list[str] | None = None,
    target_phase: str = "",
    force: bool = False,
    gate: str = "",
    context: str = "",
    verification: str = "",
    evidence_template: str = "",
    project_guidance: str | None = None,
    strategy_ref: str | None = None,
    plan_context: str | None = None,
    status: str = "",
    evidence: str = "",
    skipped_reason: str = "",
    agent: str = "",
    new_step_id: str = "",
    steps: list[dict] | None = None,
) -> str:
    """Modify plan structure.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        action: The mutation action to perform.
        phase_id: Phase ID (for add-step, add-steps, add-phase, edit-phase).
        step_id: Step ID (for edit-step, remove-step, move-step). Also used
            as explicit --id for add-step/add-phase if provided.
        name: Name (for add-step, add-phase, edit-step, edit-phase).
        description: Description (for add-step, edit-step).
        depends_on: Dependencies (for add-step, add-phase, edit-phase, edit-step).
        add_refs: Refs to add (for edit-step).
        remove_refs: Refs to remove (for edit-step).
        refs: Refs to set (for edit-step, overrides add_refs/remove_refs).
        target_phase: Target phase (for move-step).
        force: Force remove (clean dep refs) for remove-step.
        gate: Gate criterion (for add-phase, edit-phase).
        context: Context (for edit-phase).
        verification: Verification (for add-step, edit-step).
        evidence_template: Optional short template for completion evidence
            (for add-step, edit-step).
        project_guidance: Project-level claim-time guidance (edit-plan only).
        strategy_ref: Plan-level strategy ref (edit-plan only).
        plan_context: Plan context (edit-plan only).
        status: Initial status for import (add-step only): pending, done, skipped.
        evidence: Evidence string (add-step only, required when status=done).
        skipped_reason: Skip reason (add-step only, required when status=skipped).
        agent: Advisory agent suggestion (add-step, edit-step). Which agent should
            work on this step. Not enforced; any agent can still claim any step.
        steps: List of step dicts for add-steps (batch add). Each dict supports:
            name (required), description/desc, depends_on/after, verification/verify,
            refs, status (pending/done/skipped), evidence, skipped_reason, agent, id.
            For intra-batch refs, use short slugs (e.g. "step-a" instead of "phase.step-a").
    """
    # Guard: block mutations in linked worktrees
    import os

    if not os.environ.get("VECTL_PLAN_PATH"):
        import sys

        root_mcp = sys.modules.get("vectl.mcp_server")
        linked_probe = getattr(root_mcp, "is_linked_worktree", is_linked_worktree)
        is_linked, main_root = linked_probe()
        if is_linked and main_root is not None:
            return (
                f"Mutate blocked: running in a linked worktree. "
                f"Plan mutations must be performed in the main worktree at {main_root}. "
                f"Override: set VECTL_PLAN_PATH={main_root}/plan.yaml"
            )
        if is_linked and main_root is None:
            return (
                "Mutate blocked: linked worktree detected but main worktree root "
                "could not be resolved from git output. "
                "Set VECTL_PLAN_PATH to the main worktree plan.yaml and retry."
            )

    plan, expected_def_hash = _load()

    try:
        if action == "add-step":
            if not phase_id or not name:
                return "**Error:** phase_id and name required for add-step."
            step_status = StepStatus(status) if status else None
            plan, new_id = add_step(
                plan,
                phase_id,
                name,
                description=description,
                depends_on=depends_on or [],
                step_id=step_id or None,
                verification=verification,
                evidence_template=evidence_template,
                status=step_status,
                evidence=evidence or None,
                skipped_reason=skipped_reason or None,
                agent=agent or None,
            )
            msg = f"**Added step:** {new_id} to phase '{phase_id}'"

        elif action == "add-steps":
            if not phase_id:
                return "**Error:** phase_id required for add-steps."
            if steps is None:
                return "**Error:** steps required for add-steps."
            # Translate MCP parameter names to internal names
            normalized_steps: list[dict[str, object]] = []
            for i, entry in enumerate(steps):
                if not isinstance(entry, dict):
                    return f"**Error:** Step {i} must be a dict."
                name_val = entry.get("name")
                if not name_val:
                    return f"**Error:** Step {i}: 'name' is required."
                step_dict: dict[str, object] = {"name": name_val}
                # description/desc
                if "description" in entry:
                    step_dict["desc"] = entry["description"]
                elif "desc" in entry:
                    step_dict["desc"] = entry["desc"]
                # depends_on/after -> after for internal
                if "depends_on" in entry:
                    step_dict["after"] = entry["depends_on"]
                elif "after" in entry:
                    step_dict["after"] = entry["after"]
                # verification/verify
                if "verification" in entry:
                    step_dict["verify"] = entry["verification"]
                elif "verify" in entry:
                    step_dict["verify"] = entry["verify"]
                # refs
                if "refs" in entry:
                    step_dict["refs"] = entry["refs"]
                # status
                if "status" in entry:
                    step_dict["status"] = entry["status"]
                # evidence
                if "evidence" in entry:
                    step_dict["evidence"] = entry["evidence"]
                # skipped_reason
                if "skipped_reason" in entry:
                    step_dict["skipped_reason"] = entry["skipped_reason"]
                # agent
                if "agent" in entry:
                    step_dict["agent"] = entry["agent"]
                # id
                if "id" in entry:
                    step_dict["id"] = entry["id"]
                normalized_steps.append(step_dict)
            plan, generated_ids = add_steps_bulk(plan, phase_id, normalized_steps)
            msg = f"**Added {len(generated_ids)} step(s) to phase '{phase_id}':\n"
            msg += "\n".join(f"  - {sid}" for sid in generated_ids)

        elif action == "edit-step":
            if not step_id:
                return "**Error:** step_id required for edit-step."
            plan = edit_step(
                plan,
                step_id,
                name=name or _SENTINEL,
                description=description or _SENTINEL,
                verification=verification or _SENTINEL,
                evidence_template=evidence_template or _SENTINEL,
                agent=agent if agent else _SENTINEL,
                depends_on=depends_on if depends_on is not None else _SENTINEL,
                add_refs=add_refs,
                remove_refs=remove_refs,
                refs=refs if refs is not None else _SENTINEL,
                new_step_id=new_step_id if new_step_id else _SENTINEL,
            )
            msg = f"**Updated step:** {step_id}"

        elif action == "remove-step":
            if not step_id:
                return "**Error:** step_id required for remove-step."
            plan = remove_step(plan, step_id, force=force)
            msg = f"**Removed step:** {step_id}"

        elif action == "move-step":
            if not step_id or not target_phase:
                return "**Error:** step_id and target_phase required for move-step."
            plan = move_step(plan, step_id, target_phase)
            msg = f"**Moved step:** {step_id} → phase '{target_phase}'"

        elif action == "add-phase":
            if not name:
                return "**Error:** name required for add-phase."
            plan, new_id = add_phase(
                plan,
                name,
                depends_on=depends_on or [],
                gate=gate,
                phase_id=phase_id or None,
            )
            msg = f"**Added phase:** {new_id}"

        elif action == "edit-phase":
            if not phase_id:
                return "**Error:** phase_id required for edit-phase."
            plan = edit_phase(
                plan,
                phase_id,
                name=name or _SENTINEL,
                context=context or _SENTINEL,
                gate=gate or _SENTINEL,
                depends_on=depends_on if depends_on is not None else _SENTINEL,
            )
            msg = f"**Updated phase:** {phase_id}"

        elif action == "edit-plan":
            if project_guidance is None and strategy_ref is None and plan_context is None:
                return (
                    "**Error:** Provide at least one of: project_guidance, strategy_ref, "
                    "plan_context."
                )
            plan = edit_plan(
                plan,
                project_guidance=project_guidance if project_guidance is not None else _SENTINEL,
                strategy_ref=strategy_ref if strategy_ref is not None else _SENTINEL,
                context=plan_context if plan_context is not None else _SENTINEL,
            )
            msg = "**Updated plan metadata**"

        else:
            return f"**Error:** Unknown action '{action}'."

        commit_target = step_id or phase_id or action
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: mutate {action} {commit_target}",
        )
    except PlanError as e:
        return f"**Error:** {e}"

    result = msg + "\n\n→ Use `vectl_search` to verify plan consistency."
    if lock_notice:
        result += f"\n\n{lock_notice}"
    return result



def vectl_validate(check_refs: bool = False) -> str:
    """Validate plan structure and consistency (MCP read-only equivalent)."""
    plan, _ = _load()
    base_path = _plan_path().parent if check_refs else None
    issues = validate_plan(plan, check_refs=check_refs, base_path=base_path)

    parts: list[str] = ["## Validation"]
    if not issues:
        parts.append("✓ Plan is valid — 0 errors, 0 warnings")
        return "\n".join(parts)

    errors = [issue for issue in issues if not issue.is_warning]
    warnings = [issue for issue in issues if issue.is_warning]
    for issue in errors:
        parts.append(f"ERROR: {issue.message}")
    for issue in warnings:
        parts.append(f"WARN: {issue.message}")
    parts.append(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return "\n".join(parts)


def vectl_migrate_step_id(run_mode: Literal["dry-run", "apply"] = "dry-run") -> dict[str, Any]:
    """Expose duplicate step-ID migration dry-run/apply surfaces.

    Source: step `step-id-migration-tooling.surfaces` and contract
    docs/contracts/duplicate-id-migration-contract.yaml dry_run_schema and
    migration_evidence_schema.
    """
    plan, expected_def_hash = _load()
    plan_path = _plan_path()
    claims_path = resolve_claims_path(plan_path)
    branch = get_current_branch()

    report, _ = build_duplicate_step_id_migration_dry_run(
        plan,
        claims_path=claims_path,
        branch=branch,
    )
    preview_evidence = build_duplicate_step_id_migration_evidence(
        command="vectl_migrate_step_id",
        command_args=["run_mode=dry-run"],
        run_mode="dry-run",
        migrated=False,
        report=report,
    )

    if run_mode == "dry-run":
        return {
            "ok": True,
            "status": "recommendation_only",
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }

    try:
        apply_result = apply_duplicate_step_id_migration(
            plan_path,
            expected_hash=expected_def_hash,
            claims_path=claims_path,
            branch=branch,
            command="vectl_migrate_step_id",
            command_args=["run_mode=apply"],
            run_mode="apply",
        )
    except CASConflictError as e:
        return {
            "ok": False,
            "status": "recommendation_only",
            "error": (
                "CAS conflict: plan.yaml was modified by another process since you loaded it. "
                "Re-read with `vectl_status` or `vectl_show`, then retry migration."
            ),
            "raw_error": str(e),
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }
    except PlanError as e:
        return {
            "ok": False,
            "status": "recommendation_only",
            "error": str(e),
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }

    return {
        "ok": True,
        "status": "repair_applied",
        "migrated": apply_result.migrated,
        "new_plan_hash": apply_result.new_plan_hash,
        "report": apply_result.report.to_dict(),
        "evidence": apply_result.evidence.to_dict(),
    }


# ---------------------------------------------------------------------------
# Tool 9: vectl_guide
# ---------------------------------------------------------------------------



def vectl_check(
    step_id: str,
    keyword: str | None = None,
    add: str | None = None,
) -> str:
    """Toggle or add a checklist item in a step's description.

    Args:
        step_id: Step ID containing the checklist.
        keyword: Keyword to toggle a checklist item. Finds the checklist item
            containing this keyword (case-insensitive) and toggles its checked state.
        add: Text for a new unchecked checklist item to append.

    Returns:
        Markdown-formatted result showing the updated checklist.
    """
    if keyword is None and add is None:
        return "**Error:** Must provide either 'keyword' or 'add'."

    plan, expected_def_hash = _load()

    try:
        plan = update_checklist(plan, step_id, check=keyword, append=add)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: update checklist {step_id}",
        )
    except NoMatchError as e:
        return f"**Error:** No checklist item matches keyword '{e.keyword}'."
    except AmbiguousMatchError as e:
        lines = [f"**Error:** Keyword '{e.keyword}' matches multiple items:"]
        for item in e.candidates:
            lines.append(f"  - {item}")
        lines.append("\nUse a more specific keyword to match exactly one item.")
        return "\n".join(lines)
    except PlanError as e:
        return f"**Error:** {e}"

    # Show updated step
    found = plan.find_step(step_id)
    if found:
        phase, step = found
        lines = [f"**Updated checklist:** {step_id}\n"]
        if step.description:
            lines.append("```markdown")
            lines.append(step.description.rstrip())
            lines.append("```")
        if lock_notice:
            lines.append(f"\n{lock_notice}")
        return "\n".join(lines)

    if lock_notice:
        return f"**Updated checklist:** {step_id}\n\n{lock_notice}"
    return f"**Updated checklist:** {step_id}"


# ---------------------------------------------------------------------------
# Tool 15: vectl_recover
# ---------------------------------------------------------------------------



def vectl_decide(
    running_tasks: list[RunningTask],
    completed_results: list[CompletedResult] | None = None,
    advisor_state: dict[str, object] | None = None,
    max_parallelism: int = 5,
) -> dict:
    """Deterministic orchestration advisor.

    Analyzes running tasks and completed results to determine what actions
    the orchestrator should take next. Supports session reuse decisions
    for efficient agent workflow continuation.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md

    Args:
        running_tasks: Currently running tasks (in-flight work).
            Each task has step_id, agent, task_id (execution identity only, NOT reuse handle),
            runner (runner namespace), dispatched_at.
        completed_results: Tasks that have completed since last decision.
            Each result has step_id, task_id, runner, status (SUCCESS/FAIL), output_summary.
        advisor_state: Caller-owned state (completion_times, session_registry, failure_counts).
            Pass your persisted state here; replace it with next_state from output.
            If None, a fresh ephemeral state is used per call.
        max_parallelism: Maximum allowed parallel dispatches (default 5).

    Returns:
        Structured output containing:
        - status: dispatch | wait | blocked | done
        - reason_code: dispatch_available | waiting_on_running | capacity_full |
            no_executable_steps | repeated_failures
        - message: optional human-readable summary
        - actions: list of Action objects
        - next_state: full replacement for caller-owned advisor state
        - policy: explicit policy metadata (reuse_ttl_s, escalation_threshold)
        - decision_log: optional debug/explanatory entries
    """
    result = _decide_impl(
        running_tasks=running_tasks,
        completed_results=completed_results,
        max_parallelism=max_parallelism,
        advisor_state=advisor_state,
    )
    # Return as dict for MCP JSON serialization
    # Use exclude_none=False to ensure all expected fields are present even when None
    return result.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# Test compatibility shim
# ---------------------------------------------------------------------------

