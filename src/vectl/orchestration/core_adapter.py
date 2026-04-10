"""
Thin adapter over vectl core for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

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

    def claim_step(
        self,
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: Literal["normal"] = "normal",
    ) -> None:
        """Execute claim through official core claim surface.

        Contract lock:
            ``flow`` is pinned to ``"normal"`` for orchestration runtime use.
            Resolver and other exceptional paths must not treat this bridge as a
            second claim authority.
        """
        ...

    def complete_step(
        self,
        step_id: str,
        evidence: str,
        *,
        reconcile_disposition: Literal["merged", "noop"],
    ) -> None:
        """Execute completion through official core completion surface.

        Contract lock:
            Completion is allowed only after runtime reconcile closed as
            ``"merged"`` or ``"noop"``. The disposition is passed explicitly so
            orchestration call sites cannot hide that prerequisite.
        """
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

    def claim_step(
        self,
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: Literal["normal"] = "normal",
    ) -> None:
        """Execute claim through official core claim surface.

        Args:
            step_id: Step selector to claim.
            agent: Claiming agent name.
            force: Whether to force claim for exclusive-affinity mismatches.
            flow: Orchestration flow class. Pinned to ``"normal"`` for this
                bridge so resolver/escalation paths do not claim through the
                lifecycle facade.
        """
        if flow != "normal":
            raise ValueError("orchestration core adapter claim flow is pinned to 'normal'")
        plan, file_hash = load_plan_definition(self._plan_path)
        updated_plan, _ = core.claim_step(
            plan,
            step_id,
            agent,
            force=force,
            claims_path=self._claims_path,
        )
        save_plan(updated_plan, self._plan_path, expected_hash=file_hash)

    def complete_step(
        self,
        step_id: str,
        evidence: str,
        *,
        reconcile_disposition: Literal["merged", "noop"],
    ) -> None:
        """Execute completion through official core completion surface.

        Args:
            step_id: Step selector to complete.
            evidence: Completion evidence string.
            reconcile_disposition: Accepted reconcile closure proving runtime has
                already merged or determined noop before lifecycle completion.
        """
        # Preserve reconcile_disposition proof in the evidence surface.
        # Per ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 4,
        # completion is allowed only after reconcile returns 'merged' or 'noop'.
        # The reconcile_disposition parameter is the runtime proof that
        # this prerequisite was met. Dropping it (as the prior implementation
        # did with `_ = reconcile_disposition`) broke the proof chain.
        enriched_evidence = f"[reconcile_disposition={reconcile_disposition}] {evidence}"
        plan, file_hash = load_plan_definition(self._plan_path)
        updated_plan = core.complete_step(
            plan,
            step_id,
            enriched_evidence,
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

    # ---------------------------------------------------------------------
    # Step data loading (dispatch boundary)
    # ---------------------------------------------------------------------

    def load_step_data_for_dispatch(self, step_id: str):
        """Load step data for dispatch coordination through the official core seam.

        This is the stable public boundary for the dispatch coordinator's
        step-data loading needs. Callers must not access _plan_path or other
        internal state to obtain plan data.

        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md §3.6

        Args:
            step_id: Step identifier.

        Returns:
            StepData if found, else None.
        """
        # Late import to avoid circular dependency with dispatch_policy.py
        from vectl.orchestration.dispatch_policy import StepData

        plan, _ = load_plan_definition(self._plan_path)
        found = plan.find_step(step_id)
        if found is None:
            return None
        _, step = found
        return StepData(
            step_id=step.id,
            description=step.description,
            verification=step.verification,
            refs=tuple(step.refs),
            evidence_template=step.evidence_template,
            verify=step.verify,
            agent=step.agent,
        )


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
