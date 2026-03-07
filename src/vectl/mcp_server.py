"""MCP server exposing vectl tools to agents.

14 tools (8 consolidated per expert panel dec-001, plus guide, dag, clipboard,
init, render, and check):
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

Tools generally return Markdown-formatted text.

Feature request (2026-02-12): claim-time guidance ("Output guidance when running vectl claim").
To satisfy FR R6 (structured guidance output), vectl_claim returns a structured
payload including a Guidance object plus a Markdown rendition.
Plan path: resolved via shared plan_path.resolve_plan_path() —
  VECTL_PLAN_PATH env var > VECTL_PLAN (deprecated) > walk-up > ./plan.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from pydantic import BaseModel

from vectl.claim_guidance import GuidancePayload, build_claim_guidance
from vectl.core import (
    _SENTINEL,
    AgentsTarget,
    add_phase,
    add_step,
    add_steps_bulk,
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
)
from vectl.io import (
    extract_state,
    load_plan_definition,
    load_state,
    merge_plan,
    save_plan,
    save_state,
    strip_state,
)
from vectl.models import (
    AffinityError,
    AmbiguousMatchError,
    CASConflictError,
    InitResult,
    NoMatchError,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)
from vectl.plan_path import resolve_plan_path, resolve_state_path
from vectl.semantics import is_step_locked

mcp = FastMCP(
    "vectl",
    instructions=(
        "vectl manages phased development plans for AI agents. "
        "Use vectl_status to understand the plan, vectl_claim to start work, "
        "vectl_complete when done. Each tool returns Markdown text."
    ),
)

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


def _load() -> tuple[Plan, str, str]:
    """Load merged plan with separate definition/state hashes for CAS.

    Lazy migration behavior:
      - If state.json exists and contains state, merge it into plan.yaml.
      - If state.json is missing/empty, return legacy plan from plan.yaml.

    Returns:
        (merged_plan, definition_hash, state_hash)
    """
    plan_path = _plan_path()
    plan_def, def_hash = load_plan_definition(plan_path)

    state_path = resolve_state_path(plan_path)
    state, state_hash = load_state(state_path)

    has_state = (
        bool(state.steps)
        or bool(state.phases)
        or state.clipboard is not None
        or bool(state.plan_id)
    )
    if state_hash and has_state:
        return merge_plan(plan_def, state), def_hash, state_hash

    # Legacy mode: no state document yet.
    return plan_def, def_hash, ""


def _save_state(plan: Plan, expected_state_hash: str, *, recalc_locks: bool = True) -> str:
    """Save mutable runtime state with CAS semantics.

    If expected_state_hash is empty, CAS is skipped (first state write).

    Args:
        plan: The merged plan to persist state from.
        expected_state_hash: CAS hash for state.json; empty skips CAS.
        recalc_locks: Recompute phase lock status before save and include
            lock-change notice in return value.
    """
    changed: list[str] = []
    if recalc_locks:
        changed = recalc_lock_status(plan)

    state_path = resolve_state_path(_plan_path())
    state = extract_state(plan)
    cas_hash: str | None = expected_state_hash if expected_state_hash != "" else None
    try:
        save_state(state, state_path, cas_hash)
    except CASConflictError as err:
        raise PlanError(
            "CAS conflict: state.json was modified by another process since you loaded it. "
            "Re-read with `vectl_status` or `vectl_show`, then retry your mutation."
        ) from err
    return format_lock_changes(changed, plan)


def _save_definition(plan: Plan, expected_def_hash: str, *, recalc_locks: bool = True) -> str:
    """Save definition-only plan.yaml with CAS semantics."""
    changed: list[str] = []
    if recalc_locks:
        changed = recalc_lock_status(plan)

    stripped = strip_state(plan)
    try:
        save_plan(stripped, _plan_path(), expected_def_hash)
    except CASConflictError as err:
        raise PlanError(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl_status` or `vectl_show`, then retry your mutation."
        ) from err
    return format_lock_changes(changed, plan)


def _save_both(plan: Plan, expected_def_hash: str, expected_state_hash: str) -> str:
    """Save merged plan to split definition/state stores.

    Recalculates lock status on the merged/full plan before writing definition+state.
    Definition is written first so a plan.yaml CAS conflict cannot leave orphaned
    state.json entries (structural additions like new phases/steps would be in
    state.json but missing from plan.yaml, making them invisible to merge_plan).
    """
    changed = recalc_lock_status(plan)
    def_notice = _save_definition(plan, expected_def_hash, recalc_locks=False)
    state_notice = _save_state(plan, expected_state_hash, recalc_locks=False)
    lock_notice = format_lock_changes(changed, plan)
    return "\n\n".join(part for part in (lock_notice, state_notice, def_notice) if part)


def _fmt_step(plan: Plan, step: Step, phase_id: str) -> str:
    phase = plan.find_phase(phase_id)
    if phase and is_step_locked(plan, phase, step):
        icon = "🔒"
    else:
        icon = _STEP_ICON.get(step.status, "?")
    claimed = f" (claimed by {step.claimed_by})" if step.claimed_by else ""
    agent = f" [suggested: {step.agent}]" if step.agent else ""
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


# ---------------------------------------------------------------------------
# Tool 1: vectl_status
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Show plan overview, next available steps, and optionally claimed steps for an agent. "
        "Use this to understand the current state of the plan."
    ),
)
def vectl_status(agent: str | None = None) -> str:
    """Show plan status overview + next steps + optionally mine.

    Args:
        agent: If provided, also show steps claimed by this agent.
    """
    plan, _, _ = _load()

    parts: list[str] = [_fmt_phase_summary(plan)]

    # Next steps (prioritized by agent if provided)
    available = get_next_steps(plan, agent=agent)
    if available:
        parts.append("\n## Next Available Steps\n")
        for step in available[:3]:
            found = plan.find_step(step.id)
            phase_id = found[0].id if found else "?"
            parts.append(_fmt_step(plan, step, phase_id))
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

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 2: vectl_show
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Show detailed information about a step or phase. "
        "Pass a step ID (e.g. 'core.validate') or phase ID (e.g. 'core')."
    ),
)
def vectl_show(id: str) -> str:
    """Show step or phase detail.

    Args:
        id: Step ID (e.g. 'phase.step') or phase ID (e.g. 'phase').
    """
    plan, _, _ = _load()

    # Try step first
    found = plan.find_step(id)
    if found:
        phase, step = found
        locked = is_step_locked(plan, phase, step)
        if locked:
            status = "🔒 locked"
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
        return "\n".join(lines)

    # Try phase
    phase = plan.find_phase(id)
    if phase:
        lines = [
            f"## Phase: {phase.id}",
            f"**Name:** {phase.name}",
            f"**Status:** {_STATUS_ICON.get(phase.status, '?')} {phase.status.value}",
        ]
        if phase.depends_on:
            lines.append(f"**Depends on:** {', '.join(phase.depends_on)}")
        if phase.context:
            lines.append(f"**Context:** {phase.context}")
        if phase.gate:
            lines.append(f"**Gate:** {phase.gate}")
        if phase.steps:
            lines.append("\n### Steps\n")
            for step in phase.steps:
                lines.append(_fmt_step(plan, step, phase.id))
        return "\n".join(lines)

    return f"**Error:** '{id}' not found as step or phase."


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


class AffinityWarningData(BaseModel):
    """Affinity warning details.

    RFC: docs/RFC-affinity.md
    Returned when claim proceeds despite affinity mismatch.
    """

    step_agent: str
    claiming_agent: str
    affinity: str
    message: str


class AffinityOverrideData(BaseModel):
    """Affinity override details.

    RFC: docs/RFC-affinity.md
    Returned when --force overrides exclusive affinity.
    """

    overridden_agent: str
    override_by: str
    message: str


class ClaimResult(BaseModel):
    """Structured response for vectl_claim.

    Source: FR R6 — return structured fields; clients can decide how to display.
    """

    ok: bool
    markdown: str
    claimed: ClaimStepData | None = None
    guidance: GuidancePayload | None = None
    affinity_warning: AffinityWarningData | None = None
    affinity_override: AffinityOverrideData | None = None
    error: str | None = None
    error_code: str | None = None


@mcp.tool(
    description=(
        "Claim a step for work. If step_id is omitted, auto-claims the first "
        "available step. Returns the claimed step details. "
        "RFC: docs/RFC-affinity.md — enforces agent affinity, use force=true to override."
    ),
)
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
    plan, _, expected_state_hash = _load()

    if step_id is None:
        available = get_next_steps(plan, agent=agent)
        if not available:
            err = "No steps available to claim."
            return ClaimResult(ok=False, markdown=f"**Error:** {err}", error=err).model_dump(
                mode="json", exclude_none=True
            )
        step_id = available[0].id

    lock_notice: str = ""
    try:
        plan, result = claim_step(plan, step_id, agent, force=force)
        lock_notice = _save_state(plan, expected_state_hash)
    except AffinityError as e:
        # RFC: docs/RFC-affinity.md
        # Exclusive affinity violation
        err = str(e)
        return ClaimResult(
            ok=False,
            markdown=f"**Affinity Error:** {err}",
            error=err,
            error_code="affinity_violation",
        ).model_dump(mode="json", exclude_none=True)
    except PlanError as e:
        err = str(e)
        return ClaimResult(ok=False, markdown=f"**Error:** {err}", error=err).model_dump(
            mode="json", exclude_none=True
        )

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
        affinity_warning_data: AffinityWarningData | None = None
        affinity_override_data: AffinityOverrideData | None = None

        if result.warning_message:
            if result.affinity_override:
                affinity_override_data = AffinityOverrideData(
                    overridden_agent=step.agent or "",
                    override_by=agent,
                    message=result.warning_message,
                )
                md_lines.append(f"⚠️ **Affinity Override:** {result.warning_message}")
            elif result.affinity_warning:
                affinity_warning_data = AffinityWarningData(
                    step_agent=step.agent or "",
                    claiming_agent=agent,
                    affinity="suggested",
                    message=result.warning_message,
                )
                md_lines.append(f"⚠️ **Affinity Warning:** {result.warning_message}")
            md_lines.append("")

        gp: GuidancePayload | None = None
        if guidance:
            gp = build_claim_guidance(plan, phase, step)
            md_lines.append(gp.markdown.rstrip())

        markdown = "\n".join(md_lines).rstrip() + "\n"
        if lock_notice:
            markdown = markdown.rstrip("\n") + f"\n\n{lock_notice}\n"
        return ClaimResult(
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
    return ClaimResult(
        ok=True,
        markdown=fallback_markdown,
    ).model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Tool 4: vectl_complete
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Complete a claimed step with evidence. Evidence should describe "
        "what was done and how it was verified."
    ),
)
def vectl_complete(step_id: str, evidence: str) -> str:
    """Complete a step.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        step_id: The step ID to complete.
        evidence: Description of what was done and verification.
    """
    plan, _, expected_state_hash = _load()

    try:
        plan = complete_step(plan, step_id, evidence)
        lock_notice = _save_state(plan, expected_state_hash)
    except PlanError as e:
        return f"**Error:** {e}"

    # Check if phase completed
    found = plan.find_step(step_id)
    parts = [f"**Completed:** {step_id}"]
    if found:
        phase, _ = found
        if phase.status == PhaseStatus.DONE:
            parts.append(f"**Phase '{phase.id}' is now DONE!**")

    # Show next available
    available = get_next_steps(plan)
    if available:
        parts.append("\n**Next available:**")
        for step in available[:3]:
            f = plan.find_step(step.id)
            pid = f[0].id if f else "?"
            parts.append(_fmt_step(plan, step, pid))
        if len(available) > 3:
            parts.append(f"  ... and {len(available) - 3} more")

    if lock_notice:
        parts.append(f"\n{lock_notice}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 5: vectl_lifecycle
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Change step/phase lifecycle state. Actions: "
        "defer (release claimed step), "
        "reject (reject completed step for rework), "
        "skip (skip a step with reason: superseded/irrelevant/absorbed/deprioritized), "
        "skip-phase (skip all remaining steps in a phase), "
        "complete-phase (mark a phase done for historical imports)."
    ),
)
def vectl_lifecycle(
    action: Literal["defer", "reject", "skip", "skip-phase", "complete-phase"],
    id: str,
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
        id: Step ID (for defer/reject/skip) or phase ID (for skip-phase/complete-phase).
        reason: Required for reject, skip, skip-phase. For skip/skip-phase
            must be: superseded, irrelevant, absorbed, deprioritized.
        evidence: Required for complete-phase.
        reviewer: Optional reviewer name for reject.
        force: For skip-phase, allow skipping a locked phase with remaining steps.
            Empty phases (0 steps) are always allowed regardless of lock.
    """
    plan, expected_def_hash, expected_state_hash = _load()

    try:
        if action == "defer":
            plan = defer_step(plan, id)
            msg = f"**Deferred:** {id} → back to pending"
            lock_notice = _save_state(plan, expected_state_hash)
        elif action == "reject":
            if not reason:
                return "**Error:** reason is required for reject."
            plan = reject_step(plan, id, reason, reviewer)
            msg = f"**Rejected:** {id} — {reason}"
            lock_notice = _save_state(plan, expected_state_hash)
        elif action == "skip":
            if not reason:
                return (
                    "**Error:** reason is required for skip "
                    "(superseded/irrelevant/absorbed/deprioritized)."
                )
            plan = skip_step(plan, id, reason)
            msg = f"**Skipped:** {id} — {reason}"
            lock_notice = _save_state(plan, expected_state_hash)
        elif action == "skip-phase":
            if not reason:
                return "**Error:** reason is required for skip-phase."
            plan, skipped_ids = skip_phase(plan, id, reason, force=force)
            msg = f"**Skipped phase '{id}':** {len(skipped_ids)} steps skipped"
            if skipped_ids:
                msg += "\n" + "\n".join(f"  - {sid}" for sid in skipped_ids)
            lock_notice = _save_state(plan, expected_state_hash)
        elif action == "complete-phase":
            if not evidence:
                return "**Error:** evidence is required for complete-phase."
            plan, unlocked = complete_phase(plan, id, evidence)
            msg = f"**Completed phase '{id}':** {len(unlocked)} phase(s) unlocked"
            if unlocked:
                msg += "\n" + "\n".join(f"  - {pid}" for pid in unlocked)
            lock_notice = _save_both(plan, expected_def_hash, expected_state_hash)
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


@mcp.tool(
    description=(
        "Search the plan for a pattern. Searches phase names/IDs/context, "
        "step names/IDs/descriptions. Returns matching snippets."
    ),
)
def vectl_search(pattern: str, phase_id: str | None = None, regex: bool = False) -> str:
    """Search the plan.

    Args:
        pattern: Search pattern (substring or regex).
        phase_id: Restrict search to a specific phase.
        regex: If True, interpret pattern as regex.
    """
    plan, _, _ = _load()

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


@mcp.tool(
    description=(
        "Modify plan structure. Actions: "
        "add-step (add a new step to a phase), "
        "add-steps (add multiple steps in batch), "
        "edit-step (update step fields), "
        "remove-step (remove a step, use force=true to clean dep refs), "
        "move-step (move step between phases), "
        "add-phase (add a new phase), "
        "edit-phase (update phase fields), "
        "edit-plan (update plan-level metadata). "
        "After mutations, use vectl_search to verify consistency."
    ),
)
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
    plan, expected_def_hash, expected_state_hash = _load()

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

        lock_notice = _save_both(plan, expected_def_hash, expected_state_hash)
    except PlanError as e:
        return f"**Error:** {e}"

    result = msg + "\n\n→ Use `vectl_search` to verify plan consistency."
    if lock_notice:
        result += f"\n\n{lock_notice}"
    return result


@mcp.tool(
    description=(
        "Output machine-readable plan checkpoint (JSON). "
        "Provides a deterministic, bounded snapshot for compaction and handoff."
    ),
)
def vectl_checkpoint(
    agent: str | None = None,
    next: int = 3,
    include_guidance: bool = False,
    lite: bool = True,
    pretty: bool = False,
) -> dict:
    """Output machine-readable plan checkpoint (JSON).

    Args:
        agent: Agent name (affects focus selection).
        next: Max next steps (default 3).
        include_guidance: Include guidance refs/templates (default False).
        lite: Minimize output (omit metadata, redundant active_steps). Default True.
        pretty: Pretty-print JSON (only affects rendering if returned as str, here returns dict).
    """
    from vectl.checkpoint import build_checkpoint

    plan, expected_def_hash, expected_state_hash = _load()

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


@mcp.tool(
    description=(
        "Review plan health and optionally check gate readiness for a phase. "
        "Returns validation summary, phase progress, active phase detail, "
        "spec coverage, and gate check results."
    ),
)
def vectl_review(
    phase_id: str | None = None,
    check_refs: bool = False,
    include_done: bool = False,
) -> str:
    """Multi-layer plan review with optional gate check.

    Args:
        phase_id: If provided, also include gate readiness check for this phase.
        check_refs: If true, verify that ref file paths exist on disk.
        include_done: If true, include DONE and LOCKED phases in L3 detail.
    """
    plan, _, _ = _load()
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
                else:
                    icon = _STEP_ICON.get(step.status, "?")
                claimed = f" @{step.claimed_by}" if step.claimed_by else ""
                dep_info = f" deps={', '.join(step.depends_on)}" if step.depends_on else ""
                suggested = f" suggested={step.agent}" if step.agent else ""
                parts.append(f"  {icon} {step.id} — {step.name}{claimed}{suggested}{dep_info}")
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
        try:
            gc = gate_check(plan, phase_id)
            parts.append(f"\n## Gate Check: {gc.phase_id}\n")
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

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 9: vectl_guide
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Show agent onboarding guide. Topics: startup, stuck, review, planning, migration. "
        "Returns Markdown guide text. Call with no arguments to get all topics."
    ),
)
def vectl_guide(topic: str | None = None) -> str:
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


@mcp.tool(
    description=(
        "Show dependency graph as Mermaid flowchart. "
        "Default: phase-level DAG. Pass phase_id to drill into step-level DAG. "
        "Returns Mermaid text (paste into GitHub/Obsidian to render)."
    ),
)
def vectl_dag(phase_id: str | None = None) -> str:
    """Show dependency graph as Mermaid flowchart.

    Args:
        phase_id: Optional phase ID. If provided, shows step-level DAG
            within that phase. Otherwise shows phase-level DAG.
    """
    from vectl.core import generate_mermaid_dag

    plan, _, _ = _load()

    try:
        return generate_mermaid_dag(plan, phase_id=phase_id)
    except PlanError as e:
        return f"**Error:** {e}"


# ---------------------------------------------------------------------------
# Tool 11: vectl_clipboard
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Cross-agent communication via plan clipboard. Actions: write, read, clear. "
        "Write overwrites any existing content. Read returns empty if expired. "
        "Use for handoffs, broadcasts, reviewer notes that don't follow DAG edges."
    ),
)
def vectl_clipboard(
    action: Literal["write", "read", "clear"],
    author: str = "",
    summary: str = "",
    content: str = "",
    ttl: int = 24,
) -> str:
    """Cross-agent communication via single-slot clipboard.

    Args:
        action: One of "write", "read", "clear".
        author: Who is writing (required for write).
        summary: One-line description (required for write, max 80 chars).
        content: Payload (required for write, max 8000 chars).
        ttl: Time-to-live in hours (default 24).

    Returns:
        Markdown-formatted result.
    """
    if action == "write":
        return _clipboard_write(author, summary, content, ttl)
    elif action == "read":
        return _clipboard_read()
    elif action == "clear":
        return _clipboard_clear()
    else:
        return f"**Error:** Unknown action '{action}'. Valid actions: write, read, clear."


def _clipboard_write(author: str, summary: str, content: str, ttl: int) -> str:
    """Write to clipboard with CAS."""
    try:
        plan, _, expected_state_hash = _load()
    except PlanError as e:
        return f"**Error:** {e}"

    try:
        plan = clipboard_write(plan, author, summary, content, ttl)
    except PlanError as e:
        return f"**Error:** {e}"

    try:
        lock_notice = _save_state(plan, expected_state_hash)
    except PlanError as e:
        # CAS conflict - provide actionable message per RFC
        if "CAS conflict" in str(e):
            return (
                "**Clipboard write conflict** — another agent modified the plan. "
                "Re-read with `vectl_status` or `vectl_show`, then retry."
            )
        return f"**Error:** {e}"

    cb = plan.clipboard
    assert cb is not None  # We just wrote it
    result = (
        f"**Clipboard written.**\n\n"
        f"- **Author:** {cb.author}\n"
        f"- **Summary:** {cb.summary}\n"
        f"- **Expires:** {cb.expires_at}\n"
    )
    if lock_notice:
        result += f"\n{lock_notice}\n"
    return result


def _clipboard_read() -> str:
    """Read clipboard (pure read, no CAS)."""
    try:
        plan, _, _ = _load()
    except PlanError as e:
        return f"**Error:** {e}"

    # Check for expired clipboard before reading
    from vectl.core import _clipboard_expired

    if plan.clipboard is None:
        return "**Clipboard is empty.**"

    if _clipboard_expired(plan.clipboard):
        # Calculate hours since expiry
        from datetime import datetime, timezone

        try:
            expires = datetime.fromisoformat(plan.clipboard.expires_at.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - expires).total_seconds() / 3600
            return (
                "**Clipboard is empty.**\n"
                f"*Note: previous clipboard by {plan.clipboard.author} "
                f"expired {hours_ago:.1f}h ago.*"
            )
        except (ValueError, AttributeError):
            return "**Clipboard is empty.**"

    cb = plan.clipboard
    return (
        f"**Clipboard**\n\n"
        f"- **Author:** {cb.author}\n"
        f"- **Summary:** {cb.summary}\n"
        f"- **Written:** {cb.written_at}\n"
        f"- **Expires:** {cb.expires_at}\n\n"
        f"**Content:**\n```\n{cb.content}\n```\n"
    )


def _clipboard_clear() -> str:
    """Clear clipboard with CAS."""
    try:
        plan, _, expected_state_hash = _load()
    except PlanError as e:
        return f"**Error:** {e}"

    if plan.clipboard is None:
        return "**Clipboard was already empty.**"

    plan = clipboard_clear(plan)

    try:
        lock_notice = _save_state(plan, expected_state_hash)
    except PlanError as e:
        if "CAS conflict" in str(e):
            return (
                "**Clipboard clear conflict** — another agent modified the plan. "
                "Re-read with `vectl_status` or `vectl_show`, then retry."
            )
        return f"**Error:** {e}"

    if lock_notice:
        return f"**Clipboard cleared.**\n\n{lock_notice}"
    return "**Clipboard cleared.**"


# ---------------------------------------------------------------------------
# Tool 12: vectl_init
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Initialize a new vectl project. Creates plan.yaml and optionally "
        "configures AGENTS.md/CLAUDE.md with vectl instructions. "
        "Use this to start tracking a new project with vectl."
    ),
)
def vectl_init(
    project: str,
    plan_path: str | None = None,
    agents_target: Literal["auto", "agents", "claude"] = "auto",
) -> dict:
    """Initialize a new vectl project.

    Creates a minimal plan.yaml and optionally upserts AGENTS.md/CLAUDE.md
    with vectl instructions.

    Args:
        project: Project name (required).
        plan_path: Path for plan.yaml. Defaults to ./plan.yaml.
        agents_target: Target for agent instructions file:
            - "auto": Auto-detect (AGENTS.md or CLAUDE.md based on .claude/ dir)
            - "agents": Always use AGENTS.md
            - "claude": Always use CLAUDE.md

    Returns:
        Structured InitResult with created paths and status.
    """
    from pathlib import Path

    # Resolve plan path
    if plan_path:
        target = Path(plan_path)
    else:
        target = Path("plan.yaml")

    # Check if plan already exists
    if target.exists():
        return InitResult(
            ok=False,
            plan_path=str(target),
            message=f"Plan file already exists: {target}",
            error="Plan file already exists. Delete it first or use a different path.",
        ).model_dump(mode="json", exclude_none=True)

    # Create minimal plan template
    template = Plan(
        project=project,
        context=f"Implementation plan for {project}.",
    )

    # Create parent directory if needed
    target.parent.mkdir(parents=True, exist_ok=True)

    # Save plan (no CAS for new file)
    try:
        save_plan(template, target)
    except Exception as e:
        return InitResult(
            ok=False,
            plan_path=str(target),
            message=f"Failed to create plan: {e}",
            error=str(e),
        ).model_dump(mode="json", exclude_none=True)

    # Upsert AGENTS.md/CLAUDE.md
    target_enum = AgentsTarget(agents_target)
    agents_message, agents_filename = upsert_agents_md(target.parent, target_enum)

    return InitResult(
        ok=True,
        plan_path=str(target),
        agents_target=agents_filename,
        message=f"Created {target}. {agents_message}.",
    ).model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Tool 13: vectl_render
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Render plan as Markdown report. Shows phase progress, step status, "
        "and one-line descriptions. Use --full for complete step descriptions. "
        "Use --phase to filter to a specific phase."
    ),
)
def vectl_render(
    phase_id: str | None = None,
    full: bool = False,
) -> str:
    """Render plan as Markdown stakeholder report.

    Args:
        phase_id: Optional phase ID to filter to a specific phase.
        full: If True, show complete step descriptions without truncation.

    Returns:
        Markdown-formatted progress report.
    """
    plan, _, _ = _load()

    try:
        return render_plan(plan, phase_id=phase_id, full=full)
    except PlanError as e:
        return f"**Error:** {e}"


# ---------------------------------------------------------------------------
# Tool 14: vectl_check
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Toggle or add a checklist item in a step's description. "
        "Use 'keyword' to toggle an existing item by keyword match. "
        "Use 'add' to append a new unchecked item. "
        "Operates on markdown checklists: '- [ ] item' / '- [x] item'."
    ),
)
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

    plan, expected_def_hash, expected_state_hash = _load()

    try:
        plan = update_checklist(plan, step_id, check=keyword, append=add)
        lock_notice = _save_both(plan, expected_def_hash, expected_state_hash)
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
