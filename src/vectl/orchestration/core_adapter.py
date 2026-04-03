"""
Thin adapter over vectl core for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from vectl import core
from vectl.io import load_plan_definition, save_plan
from vectl.models import IsolationMode, Plan, PlanError, StepStatus
from vectl.orchestration.contracts import CoreSnapshot
from vectl.plan_path import resolve_claims_path


class CoreAdapter(Protocol):
    """Authoritative bridge from orchestration-plane to vectl core.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
        docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 7

    The adapter is an orchestration-plane bridge over official core surfaces,
    not a shadow authority.
    """

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        """Build orchestration-facing snapshot from authoritative core state."""
        ...

    def step_isolation(self, step_id: str) -> IsolationMode:
        """Read authoritative step isolation semantics from core state."""
        ...

    def claim_step(self, step_id: str, agent: str, force: bool = False) -> None:
        """Execute claim through official core claim surface."""
        ...

    def complete_step(self, step_id: str, evidence: str) -> None:
        """Execute completion through official core completion surface."""
        ...

    def defer_step(self, step_id: str) -> None:
        """Execute defer through official core defer surface."""
        ...


class PlanCoreAdapter:
    """File-backed orchestration bridge over official vectl core surfaces.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
        docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 2.1 and 3 (G1)
        docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md section 10

    This adapter keeps vectl core as the sole authority for plan semantics and
    lifecycle mutations. The orchestration plane only reads/writes through this
    boundary.
    """

    def __init__(self, plan_path: Path) -> None:
        """Initialize adapter with authoritative plan path.

        Args:
            plan_path: Path to the authoritative plan definition file.
        """
        self._plan_path = plan_path
        self._claims_path = resolve_claims_path(plan_path)

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        """Build orchestration-facing snapshot from authoritative core state.

        Args:
            agent: Optional agent filter used by core claimable/in-progress
                queries.

        Returns:
            CoreSnapshot derived from official vectl core read surfaces.
        """
        plan, _ = load_plan_definition(self._plan_path)

        claimable_step_ids = tuple(step.id for step in core.get_next_steps(plan, agent=agent))
        in_progress_step_ids = tuple(
            step.id for _, step in core.get_claimed_steps(plan, agent=agent)
        )

        claimable_set = set(claimable_step_ids)
        in_progress_set = set(in_progress_step_ids)
        blocked_step_ids = tuple(
            step.id
            for phase in plan.phases
            for step in phase.steps
            if step.status in (StepStatus.PENDING, StepStatus.REJECTED)
            and step.id not in claimable_set
            and step.id not in in_progress_set
        )

        unresolved_reasons = tuple(
            issue.message for issue in core.validate_plan(plan) if not issue.is_warning
        )

        return CoreSnapshot(
            plan_complete=_is_plan_complete(plan),
            claimable_step_ids=claimable_step_ids,
            in_progress_step_ids=in_progress_step_ids,
            blocked_step_ids=blocked_step_ids,
            unresolved_reasons=unresolved_reasons,
        )

    def step_isolation(self, step_id: str) -> IsolationMode:
        """Read authoritative step isolation semantics from core state.

        Args:
            step_id: Step selector accepted by authoritative plan lookup.

        Returns:
            Isolation mode recorded in authoritative step model.

        Raises:
            PlanError: If the step cannot be resolved unambiguously.
        """
        plan, _ = load_plan_definition(self._plan_path)
        core.require_unambiguous_target_step_id(
            plan,
            step_id,
            operation="core-adapter-step-isolation",
        )
        found = plan.find_step(step_id)
        if found is None:
            raise PlanError(f"Step '{step_id}' not found")
        _, step = found
        return step.isolation

    def claim_step(self, step_id: str, agent: str, force: bool = False) -> None:
        """Execute claim through official core claim surface.

        Args:
            step_id: Step selector to claim.
            agent: Claiming agent name.
            force: Whether to force claim for exclusive-affinity mismatches.
        """
        plan, file_hash = load_plan_definition(self._plan_path)
        updated_plan, _ = core.claim_step(
            plan,
            step_id,
            agent,
            force=force,
            claims_path=self._claims_path,
        )
        save_plan(updated_plan, self._plan_path, expected_hash=file_hash)

    def complete_step(self, step_id: str, evidence: str) -> None:
        """Execute completion through official core completion surface.

        Args:
            step_id: Step selector to complete.
            evidence: Completion evidence string.
        """
        plan, file_hash = load_plan_definition(self._plan_path)
        updated_plan = core.complete_step(
            plan,
            step_id,
            evidence,
            claims_path=self._claims_path,
        )
        save_plan(updated_plan, self._plan_path, expected_hash=file_hash)

    def defer_step(self, step_id: str) -> None:
        """Execute defer through official core defer surface.

        Args:
            step_id: Step selector to defer.
        """
        plan, file_hash = load_plan_definition(self._plan_path)
        updated_plan = core.defer_step(plan, step_id, claims_path=self._claims_path)
        save_plan(updated_plan, self._plan_path, expected_hash=file_hash)


def _is_plan_complete(plan: Plan) -> bool:
    """Return whether all steps are terminal in authoritative plan state.

    Args:
        plan: Authoritative plan model.

    Returns:
        True when each step is either done or skipped.
    """
    return all(
        step.status in (StepStatus.DONE, StepStatus.SKIPPED)
        for phase in plan.phases
        for step in phase.steps
    )
