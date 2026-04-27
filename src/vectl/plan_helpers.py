"""Shared plan-query helpers for CLI and MCP surfaces."""

from __future__ import annotations

from vectl.core import _get_active_phase_ids
from vectl.models import Phase, Plan, Step, StepStatus


def get_next_steps_with_phase(plan: Plan, agent: str | None = None) -> list[tuple[Phase, Step]]:
    """Return claimable/rejected next steps with their containing phase."""

    active_phase_ids = _get_active_phase_ids(plan)
    result: list[tuple[Phase, Step]] = []

    for phase in plan.phases:
        if phase.id not in active_phase_ids:
            continue
        done_step_ids = {
            step.id
            for step in phase.steps
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED)
        }
        for step in phase.steps:
            if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
                continue
            if all(dep in done_step_ids for dep in step.depends_on):
                result.append((phase, step))

    def _sort_key(item: tuple[Phase, Step]) -> tuple[int, int, str]:
        _phase, step = item
        status_rank = 0 if step.status == StepStatus.REJECTED else 1
        if agent is None or step.agent is None:
            agent_rank = 1
        elif step.agent == agent:
            agent_rank = 0
        else:
            agent_rank = 2
        return (status_rank, agent_rank, step.id)

    result.sort(key=_sort_key)
    return result
