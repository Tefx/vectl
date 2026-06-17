"""Internal implementation slice split from mcp_core_tools.py.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
"""

from __future__ import annotations

from vectl.mcp_core_common import *

# @shell_complexity: Status preserves summary, next-work, mine, duplicate diagnostics, and repair notice output.
def vectl_status(agent: str | None = None) -> Result[str, str]:
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
# vectl_show
# ---------------------------------------------------------------------------


# @shell_complexity: Show preserves step-vs-phase rendering and all optional detail fields in one public tool.
def vectl_show(target: str) -> Result[str, str]:
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
# vectl_claim
# ---------------------------------------------------------------------------


