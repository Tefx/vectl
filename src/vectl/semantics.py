"""Centralized derived semantics for step lock/claim state.

Single source of truth for whether a step is locked, claimable,
or blocked. CLI, MCP, and core render all use this module.

Source: p0-parity.3-derived-semantics step — expert P0 finding:
display vs claimability diverge across surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    Step,
    StepStatus,
)


class BlockReason(Enum):
    """Why a step is blocked from being claimed."""

    UNMET_DEP = "unmet_dep"
    MISSING_DEP = "missing_dep"
    PHASE_INACTIVE = "phase_inactive"
    STATUS = "status"


@dataclass(frozen=True)
class StepDerived:
    """Derived state for a step (never persisted, computed on read).

    Attributes:
        claimable: True if this step can be claimed right now.
        blocked: True if this step is blocked for any reason.
        reasons: Set of BlockReason values explaining why blocked.
        unmet_deps: Step IDs that are not yet done/skipped.
    """

    claimable: bool
    blocked: bool
    reasons: frozenset[BlockReason] = field(default_factory=frozenset)
    unmet_deps: tuple[str, ...] = ()

    @property
    def locked(self) -> bool:
        """True if a PENDING step has unmet deps — the '🔒' icon case."""
        return self.blocked and BlockReason.UNMET_DEP in self.reasons


def _get_active_phase_ids(plan: Plan) -> set[str]:
    """Get IDs of phases where work can happen.

    A phase is active when:
    - Status is PENDING or IN_PROGRESS, OR
    - Status is LOCKED but all dependencies are DONE (eligible for auto-unlock).
    """
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    active: set[str] = set()
    for phase in plan.phases:
        if phase.status == PhaseStatus.LOCKED:
            if phase.depends_on and all(dep in done_phase_ids for dep in phase.depends_on):
                active.add(phase.id)
        elif phase.status in (PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS):
            active.add(phase.id)
    return active


def derive_step(plan: Plan, phase: Phase, step: Step) -> StepDerived:
    """Compute the derived state for a single step.

    This is the SINGLE algorithm that all surfaces (CLI, MCP, render)
    MUST use. Do not duplicate this logic.

    Args:
        plan: The full plan (needed for phase DAG).
        phase: The phase containing this step.
        step: The step to derive state for.

    Returns:
        StepDerived with claimable/blocked/reasons/unmet_deps.
    """
    # Non-actionable statuses are never claimable
    if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
        return StepDerived(
            claimable=False,
            blocked=(step.status not in (StepStatus.DONE, StepStatus.SKIPPED)),
            reasons=frozenset({BlockReason.STATUS})
            if step.status == StepStatus.CLAIMED
            else frozenset(),
        )

    reasons: set[BlockReason] = set()
    unmet: list[str] = []

    # Check phase is active
    active_phase_ids = _get_active_phase_ids(plan)
    if phase.id not in active_phase_ids:
        reasons.add(BlockReason.PHASE_INACTIVE)

    # Check step dependencies (phase-local)
    done_step_ids = {s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)}
    all_step_ids = {s.id for s in phase.steps}

    for dep_id in step.depends_on:
        if dep_id not in all_step_ids:
            reasons.add(BlockReason.MISSING_DEP)
            unmet.append(dep_id)
        elif dep_id not in done_step_ids:
            reasons.add(BlockReason.UNMET_DEP)
            unmet.append(dep_id)

    blocked = bool(reasons)
    claimable = not blocked

    return StepDerived(
        claimable=claimable,
        blocked=blocked,
        reasons=frozenset(reasons),
        unmet_deps=tuple(unmet),
    )


def is_step_locked(plan: Plan, phase: Phase, step: Step) -> bool:
    """Convenience: check if a step should show the 🔒 icon.

    A step is 'locked' when it is PENDING but has unmet dependencies
    within its phase.
    """
    if step.status != StepStatus.PENDING:
        return False
    derived = derive_step(plan, phase, step)
    return derived.locked
