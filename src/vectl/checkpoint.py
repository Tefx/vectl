"""Checkpoint builder logic (Shared by CLI and MCP).

Source: checkpoint schema v1 (src/vectl/checkpoint_schema.py)
Intent: Provide a deterministic, bounded snapshot of the plan state.
"""

from __future__ import annotations

from typing import Any, Optional

from vectl.core import get_next_steps
from vectl.models import Plan, Step, StepStatus
from vectl import __version__


def build_checkpoint(
    plan: Plan,
    file_hash: str,
    agent: str | None = None,
    next_limit: int = 3,
    include_guidance: bool = False,
) -> dict[str, Any]:
    """Build a checkpoint dictionary according to schema v1.

    Args:
        plan: The plan object.
        file_hash: SHA-256 of the plan file (for etag).
        agent: Optional agent name to influence focus selection.
        next_limit: Max number of next steps to include.
        include_guidance: Whether to include guidance (refs/template) in output.

    Returns:
        A dictionary matching the checkpoint schema v1.
    """
    # 1. Select Focus (Deterministic)
    focus_step = _select_focus(plan, agent)

    # 2. Build Focus Block
    focus_data = None
    guidance_data = None
    blockers_data: list[str] = []
    phase_data = None

    if focus_step:
        # Find phase
        phase_obj = None
        for ph in plan.phases:
            if any(s.id == focus_step.id for s in ph.steps):
                phase_obj = ph
                break

        if phase_obj:
            ctx = (phase_obj.context or "").strip()
            if len(ctx) > 300:
                ctx = ctx[:299] + "…"
            phase_data = {"id": phase_obj.id, "name": phase_obj.name, "context": ctx}

        focus_data = {
            "step_id": focus_step.id,
            "status": focus_step.status.value,
            "claimed_by": focus_step.claimed_by,
            "depends_on": focus_step.depends_on,
        }

        if include_guidance:
            # Build bounded guidance
            refs = _dedupe([r.strip() for r in focus_step.refs if r.strip()])[:3]

            # policy banner (project guidance)
            policy = (plan.project_guidance or "").strip()
            if len(policy) > 600:
                policy = policy[:599] + "…"

            tpl = (focus_step.evidence_template or "").strip()
            if len(tpl) > 900:
                tpl = tpl[:899] + "…"

            guidance_data = {
                "read_before": refs,
                "evidence_template": tpl,
                "policy_banner": policy,
            }

        # Blockers (deps that are not done?)
        # Current logic just lists deps.
        # Ideally blockers should list *why* next steps are blocked, but for focus step
        # blockers usually means "what I'm waiting on".
        # But schema v1 had blockers separate from focus.
        # In v1.1 we added focus.depends_on.
        # Let's keep blockers for backward compat or general context?
        # The schema v1.1 still has "blockers".
        # Let's align with schema:
        if focus_step.depends_on:
            blockers_data = [f"{focus_step.id} depends_on {dep}" for dep in focus_step.depends_on][
                :3
            ]

    # 3. Build Next (Bounded)
    next_candidates = get_next_steps(plan, agent=agent)
    next_ids = [s.id for s in next_candidates[:next_limit]]

    # 4. Build Active Steps (Concurrency Visibility)
    active_steps = []
    all_claimed = _get_all_claimed(plan)
    for s in all_claimed[:3]:  # Max 3 active steps
        active_steps.append(
            {
                "step_id": s.id,
                "owner": s.claimed_by,
                "status": s.status.value,
            }
        )

    return {
        "schema": "vectl.checkpoint/v1",
        "generated_at": _iso_now(),
        "tool": {"name": "vectl", "version": __version__},
        "plan": {
            "project": plan.project,
            "etag": f"sha256:{file_hash}",
        },
        "phase": phase_data,
        "focus": focus_data,
        "guidance": guidance_data,
        "blockers": blockers_data if blockers_data else [],
        "next": next_ids,
        "active_steps": active_steps,
        "active_steps_total": len(all_claimed),
        "active_steps_truncated": len(all_claimed) > 3,
    }


def _select_focus(plan: Plan, agent: str | None) -> Step | None:
    """Select the single focus step deterministically."""
    all_claimed = _get_all_claimed(plan)

    # Priority 1: Claimed by this agent (if agent provided)
    if agent:
        mine = [s for s in all_claimed if s.claimed_by == agent]
        if mine:
            # Tie-break: claimed_at desc (newest claim), then id asc
            # Note: Step doesn't have claimed_at on model yet? Check model.
            # Actually Step has claimed_at (str) from previous PRs.
            # If not available or string sorting is weird, rely on ID stable sort.
            # Let's sort by ID for pure determinism first.
            # Ideally we want "most recently worked on".
            # For now: stable sort by ID is safest for v1 determinism.
            return sorted(mine, key=lambda s: s.id)[0]

    # Priority 2: Any claimed step
    if all_claimed:
        return sorted(all_claimed, key=lambda s: s.id)[0]

    # Priority 3: Next available
    next_steps = get_next_steps(plan, agent=agent)
    if next_steps:
        # get_next_steps already returns stable order (phase order -> step order)
        return next_steps[0]

    return None


def _get_all_claimed(plan: Plan) -> list[Step]:
    claimed = []
    for ph in plan.phases:
        for s in ph.steps:
            if s.status == StepStatus.CLAIMED:
                claimed.append(s)
    return claimed


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
