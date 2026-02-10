"""MCP server exposing vectl tools to agents.

8 consolidated tools per expert panel decision (dec-001):
  1. vectl_status  — plan overview + next steps + mine
  2. vectl_show    — step/phase detail
  3. vectl_claim   — claim step (auto-claim supported)
  4. vectl_complete — complete step with evidence
  5. vectl_lifecycle — defer/reject/skip/skip-phase
  6. vectl_search  — search plan
  7. vectl_mutate  — add-step/edit-step/remove-step/move-step/edit-phase/add-phase
  8. vectl_review  — plan review + gate check

Each tool returns Markdown-formatted text.
Plan path: resolved via shared plan_path.resolve_plan_path() —
  VECTL_PLAN_PATH env var > VECTL_PLAN (deprecated) > walk-up > ./plan.yaml.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

from vectl.plan_path import resolve_plan_path
from vectl.semantics import is_step_locked
from vectl.core import (
    _SENTINEL,
    add_phase,
    add_step,
    auto_unlock_phases,
    claim_step,
    complete_phase,
    complete_step,
    defer_step,
    edit_phase,
    edit_step,
    gate_check,
    get_claimed_steps,
    get_next_steps,
    move_step,
    reject_step,
    remove_step,
    review_plan,
    search_plan,
    skip_phase,
    skip_step,
    validate_plan,
)
from vectl.io import load_plan, save_plan
from vectl.models import (
    CASConflictError,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)

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


def _load() -> tuple[Plan, str]:
    """Load plan + hash for CAS."""
    return load_plan(_plan_path())


def _save(plan: Plan, expected_hash: str) -> None:
    """Save plan with CAS.

    Raises a user-friendly error on CAS conflict (concurrent edit detected).
    """
    try:
        save_plan(plan, _plan_path(), expected_hash)
    except CASConflictError:
        raise PlanError(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl_status` or `vectl_show`, then retry your mutation."
        )


def _fmt_step(plan: Plan, step: Step, phase_id: str) -> str:
    phase = plan.find_phase(phase_id)
    if phase and is_step_locked(plan, phase, step):
        icon = "🔒"
    else:
        icon = _STEP_ICON.get(step.status, "?")
    claimed = f" (claimed by {step.claimed_by})" if step.claimed_by else ""
    return f"  {icon} **{step.id}** — {step.name} ({phase_id}){claimed}"


def _fmt_phase_summary(plan: Plan) -> str:
    lines: list[str] = []
    for p in plan.phases:
        done = sum(1 for s in p.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        total = len(p.steps)
        icon = _STATUS_ICON.get(p.status, "?")
        deps = ", ".join(p.depends_on) if p.depends_on else "—"
        lines.append(f"| {icon} {p.status.value} | {p.id} | {p.name} | {done}/{total} | {deps} |")
    header = "| Status | Phase | Name | Progress | Depends On |\n|--------|-------|------|----------|------------|"
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
    plan, _ = _load()

    parts: list[str] = [_fmt_phase_summary(plan)]

    # Next steps
    available = get_next_steps(plan)
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
    plan, _ = _load()

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


@mcp.tool(
    description=(
        "Claim a step for work. If step_id is omitted, auto-claims the first "
        "available step. Returns the claimed step details."
    ),
)
def vectl_claim(agent: str, step_id: str | None = None) -> str:
    """Claim a step.

    Args:
        agent: Agent name claiming the step.
        step_id: Step ID to claim. If omitted, auto-selects first available.
    """
    plan, expected_hash = _load()

    if step_id is None:
        available = get_next_steps(plan)
        if not available:
            return "**Error:** No steps available to claim."
        step_id = available[0].id

    try:
        plan = claim_step(plan, step_id, agent)
        _save(plan, expected_hash)
    except PlanError as e:
        return f"**Error:** {e}"

    found = plan.find_step(step_id)
    if found:
        phase, step = found
        return (
            f"**Claimed:** {step.id} — {step.name}\n"
            f"**Phase:** {phase.id}\n"
            f"**Agent:** {agent}\n\n"
            f"→ Use `vectl_complete` with evidence when done.\n"
            f"→ Use `vectl_lifecycle` action=defer to release."
        )
    return f"Claimed: {step_id}"


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

    Args:
        step_id: The step ID to complete.
        evidence: Description of what was done and verification.
    """
    plan, expected_hash = _load()

    try:
        plan = complete_step(plan, step_id, evidence)
        _save(plan, expected_hash)
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
) -> str:
    """Change step/phase lifecycle state.

    Args:
        action: One of: defer, reject, skip, skip-phase, complete-phase.
        id: Step ID (for defer/reject/skip) or phase ID (for skip-phase/complete-phase).
        reason: Required for reject, skip, skip-phase. For skip/skip-phase
            must be: superseded, irrelevant, absorbed, deprioritized.
        evidence: Required for complete-phase.
        reviewer: Optional reviewer name for reject.
    """
    plan, expected_hash = _load()

    try:
        if action == "defer":
            plan = defer_step(plan, id)
            msg = f"**Deferred:** {id} → back to pending"
        elif action == "reject":
            if not reason:
                return "**Error:** reason is required for reject."
            plan = reject_step(plan, id, reason, reviewer)
            msg = f"**Rejected:** {id} — {reason}"
        elif action == "skip":
            if not reason:
                return "**Error:** reason is required for skip (superseded/irrelevant/absorbed/deprioritized)."
            plan = skip_step(plan, id, reason)
            msg = f"**Skipped:** {id} — {reason}"
        elif action == "skip-phase":
            if not reason:
                return "**Error:** reason is required for skip-phase."
            plan, skipped_ids = skip_phase(plan, id, reason)
            msg = f"**Skipped phase '{id}':** {len(skipped_ids)} steps skipped"
            if skipped_ids:
                msg += "\n" + "\n".join(f"  - {sid}" for sid in skipped_ids)
        elif action == "complete-phase":
            if not evidence:
                return "**Error:** evidence is required for complete-phase."
            plan, unlocked = complete_phase(plan, id, evidence)
            msg = f"**Completed phase '{id}':** {len(unlocked)} phase(s) unlocked"
            if unlocked:
                msg += "\n" + "\n".join(f"  - {pid}" for pid in unlocked)
        else:
            return f"**Error:** Unknown action '{action}'. Use: defer, reject, skip, skip-phase, complete-phase."
        _save(plan, expected_hash)
    except PlanError as e:
        return f"**Error:** {e}"

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


@mcp.tool(
    description=(
        "Modify plan structure. Actions: "
        "add-step (add a new step to a phase), "
        "edit-step (update step fields), "
        "remove-step (remove a step, use force=true to clean dep refs), "
        "move-step (move step between phases), "
        "add-phase (add a new phase), "
        "edit-phase (update phase fields). "
        "After mutations, use vectl_search to verify consistency."
    ),
)
def vectl_mutate(
    action: Literal["add-step", "edit-step", "remove-step", "move-step", "add-phase", "edit-phase"],
    phase_id: str = "",
    step_id: str = "",
    name: str = "",
    description: str = "",
    depends_on: list[str] | None = None,
    target_phase: str = "",
    force: bool = False,
    gate: str = "",
    context: str = "",
    verification: str = "",
    status: str = "",
    evidence: str = "",
    skipped_reason: str = "",
) -> str:
    """Modify plan structure.

    Args:
        action: The mutation action to perform.
        phase_id: Phase ID (for add-step, add-phase, edit-phase).
        step_id: Step ID (for edit-step, remove-step, move-step). Also used
            as explicit --id for add-step/add-phase if provided.
        name: Name (for add-step, add-phase, edit-step, edit-phase).
        description: Description (for add-step, edit-step).
        depends_on: Dependencies (for add-step, add-phase).
        target_phase: Target phase (for move-step).
        force: Force remove (clean dep refs) for remove-step.
        gate: Gate criterion (for add-phase, edit-phase).
        context: Context (for edit-phase).
        verification: Verification (for add-step, edit-step).
        status: Initial status for import (add-step only): pending, done, skipped.
        evidence: Evidence string (add-step only, required when status=done).
        skipped_reason: Skip reason (add-step only, required when status=skipped).
    """
    plan, expected_hash = _load()

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
                status=step_status,
                evidence=evidence or None,
                skipped_reason=skipped_reason or None,
            )
            msg = f"**Added step:** {new_id} to phase '{phase_id}'"

        elif action == "edit-step":
            if not step_id:
                return "**Error:** step_id required for edit-step."
            plan = edit_step(
                plan,
                step_id,
                name=name or _SENTINEL,
                description=description or _SENTINEL,
                verification=verification or _SENTINEL,
                depends_on=depends_on if depends_on is not None else _SENTINEL,
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
            )
            msg = f"**Updated phase:** {phase_id}"

        else:
            return f"**Error:** Unknown action '{action}'."

        _save(plan, expected_hash)
    except PlanError as e:
        return f"**Error:** {e}"

    return msg + "\n\n→ Use `vectl_search` to verify plan consistency."


# ---------------------------------------------------------------------------
# Tool 8: vectl_review
# ---------------------------------------------------------------------------


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
                else:
                    icon = _STEP_ICON.get(step.status, "?")
                claimed = f" @{step.claimed_by}" if step.claimed_by else ""
                dep_info = f" deps={', '.join(step.depends_on)}" if step.depends_on else ""
                parts.append(f"  {icon} {step.id} — {step.name}{claimed}{dep_info}")
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
                    f"✗ Steps: {gc.done_count}/{gc.total_count} — {len(gc.pending_steps)} remaining:"
                )
                for s in gc.pending_steps:
                    icon = _STEP_ICON.get(s.status, "?")
                    parts.append(f"  {icon} {s.id} — {s.name}")
            if gc.gate_criterion:
                parts.append(f"\nManual gate criterion: {gc.gate_criterion}")
            if gc.gate_script:
                parts.append(
                    f"\n⚠ gate_script defined ({gc.gate_script}) but not executable via MCP. Use CLI `vectl gate-check`."
                )
            if gc.downstream_locked:
                parts.append(f"\nDownstream phases: {', '.join(gc.downstream_locked)}")
        except PlanError as e:
            parts.append(f"\n**Gate Check Error:** {e}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
