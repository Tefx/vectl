"""
Thin adapter over vectl core for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from vectl import core
from vectl.io import load_plan_definition, save_plan
from vectl.models import IsolationMode, Plan, PlanError, StepStatus
from vectl.orchestration.contracts import (
    CoreSnapshot,
    PlannerMutationBundle,
    PlannerMutationItem,
)
from vectl.orchestration.step_data import StepData
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

    def load_step_data_for_dispatch(self, step_id: str) -> StepData | None:
        """Load authoritative step data for dispatch construction."""
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
            issue.message
            for issue in core.validate_plan(plan, include_completed_evidence_guard=False)
            if not issue.is_warning
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

    def load_step_data_for_dispatch(self, step_id: str) -> StepData | None:
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


# ---------------------------------------------------------------------
# Planner mutation application through vectl facade only
# ---------------------------------------------------------------------
# Authority: docs/RFC-orch-drive.md section 13.4
# Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 5 (ownership matrix)
#
# Planner output must be applied through approved vectl facade surfaces only.
# Direct plan.yaml edits remain forbidden. This applier enforces that
# contract by mapping each PlannerMutationItem to its corresponding
# vectl core function, loading/saving through the official PlanCoreAdapter.


@dataclass(frozen=True)
class PlannerMutationResult:
    """Result of applying a planner mutation bundle through vectl facades.

    Authority: docs/RFC-orch-drive.md section 13.5

    Attributes:
        applied_count: Number of mutations successfully applied.
        failed_count: Number of mutations that failed to apply.
        applied_actions: Descriptions of successfully applied mutations.
        failed_actions: Descriptions of mutations that failed, with reasons.
        affected_step_ids: Step IDs affected by successfully applied mutations.
    """

    applied_count: int = 0
    failed_count: int = 0
    applied_actions: tuple[str, ...] = ()
    failed_actions: tuple[str, ...] = ()
    affected_step_ids: tuple[str, ...] = ()


class PlannerMutationApplier(Protocol):
    """Protocol for applying planner mutation bundles through vectl facades.

    Authority: docs/RFC-orch-drive.md section 13.4, 13.5

    The applier translates each PlannerMutationItem into one or more
    vectl core facade calls. It must never edit plan.yaml directly.
    """

    def apply_bundle(self, bundle: PlannerMutationBundle) -> PlannerMutationResult:
        """Apply a planner mutation bundle through vectl facade surfaces only.

        Authority: docs/RFC-orch-drive.md section 13.4

        All mutations must be applied through approved vectl facade surfaces.
        Direct plan.yaml edits remain forbidden.

        Args:
            bundle: Machine-readable planner mutation bundle to apply.

        Returns:
            PlannerMutationResult with apply/fail counts and affected steps.
        """
        ...


class PlanPlannerMutationApplier:
    """Concrete planner mutation applier using vectl core facades.

    Authority: docs/RFC-orch-drive.md section 13.4, 13.5
    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 5 (ownership matrix)

    This applier translates each PlannerMutationItem in a bundle to the
    corresponding vectl core function call, using load/save through the
    official plan definition I/O layer. It never edits plan.yaml directly.

    Supported actions (RFC-orch-drive.md section 8.5.1):
        - add-step: core.add_step
        - edit-step: core.edit_step
        - remove-step: core.remove_step
        - move-step: core.move_step
        - add-phase: core.add_phase
        - edit-phase: core.edit_phase
        - skip-step: core.skip_step (via lifecycle)
        - complete-phase: core.complete_phase (via lifecycle)

    Args:
        plan_path: Path to the authoritative plan definition file.
    """

    def __init__(self, plan_path: Path) -> None:
        self._plan_path = plan_path

    def apply_bundle(self, bundle: PlannerMutationBundle) -> PlannerMutationResult:
        """Apply a planner mutation bundle through vectl facade surfaces only.

        Authority: docs/RFC-orch-drive.md section 13.4

        Mutations are applied in order. If a mutation fails, it is recorded
        in failed_actions but subsequent mutations are still attempted
        (best-effort application). The calling driver decides whether to
        transition to blocked_operator or failed_unrecoverable based on the
        failure count and severity.

        Args:
            bundle: Machine-readable planner mutation bundle to apply.

        Returns:
            PlannerMutationResult with apply/fail counts and affected steps.
        """
        if bundle.status != "applyable":
            return PlannerMutationResult(
                failed_count=len(bundle.mutations),
                failed_actions=tuple(
                    f"bundle status={bundle.status}; cannot apply non-applyable bundle"
                    for _ in bundle.mutations
                ),
            )

        applied: list[str] = []
        failed: list[str] = []
        affected_steps: list[str] = []

        for mutation in bundle.mutations:
            try:
                action_desc = self._apply_single_mutation(mutation)
                applied.append(action_desc)
                # Collect affected step IDs from the mutation.
                step_id = self._extract_step_id(mutation)
                if step_id:
                    affected_steps.append(step_id)
            except Exception as exc:
                failed.append(f"action={mutation.action} reason={mutation.reason}: {exc}")

        return PlannerMutationResult(
            applied_count=len(applied),
            failed_count=len(failed),
            applied_actions=tuple(applied),
            failed_actions=tuple(failed),
            affected_step_ids=tuple(affected_steps),
        )

    def _apply_single_mutation(self, mutation: PlannerMutationItem) -> str:
        """Apply a single mutation item through the vectl core facade.

        Args:
            mutation: The mutation item to apply.

        Returns:
            Human-readable description of the applied mutation.

        Raises:
            PlanError: If the mutation cannot be applied through core.
            ValueError: If the mutation action is not recognized.
        """
        plan, file_hash = load_plan_definition(self._plan_path)
        args = mutation.arguments

        if mutation.action == "add-step":
            phase_id = str(args.get("phase_id", ""))
            step_id = args.get("step_id")
            name = str(args.get("name", ""))
            description = str(args.get("description", ""))
            depends_on = _as_str_list(args.get("depends_on"))
            refs = _as_str_list(args.get("refs"))
            verification = str(args.get("verification", ""))
            agent = args.get("agent")
            plan, generated_id = core.add_step(
                plan,
                phase_id,
                name,
                step_id=str(step_id) if step_id is not None else None,
                description=description,
                depends_on=depends_on,
                verification=verification,
                refs=refs,
                agent=str(agent) if agent is not None else None,
            )
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"add-step: phase={phase_id} step={generated_id} name={name}"

        if mutation.action == "edit-step":
            step_id = str(args.get("step_id", ""))
            changes = args.get("changes", {})
            if not isinstance(changes, dict):
                raise ValueError(f"edit-step changes must be dict, got {type(changes)}")
            edit_kwargs: dict[str, object] = {}
            if "name" in changes:
                edit_kwargs["name"] = str(changes["name"])
            if "description" in changes:
                edit_kwargs["description"] = str(changes["description"])
            if "verification" in changes:
                edit_kwargs["verification"] = str(changes["verification"])
            if "evidence_template" in changes:
                edit_kwargs["evidence_template"] = str(changes["evidence_template"])
            if "agent" in changes:
                edit_kwargs["agent"] = (
                    str(changes["agent"]) if changes["agent"] is not None else None
                )
            if "depends_on" in changes:
                edit_kwargs["depends_on"] = list(_as_str_list(changes["depends_on"]))
            if "refs" in changes:
                edit_kwargs["refs"] = list(_as_str_list(changes["refs"]))
            plan = core.edit_step(plan, step_id, **edit_kwargs)  # type: ignore[arg-type]
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"edit-step: step={step_id} fields={tuple(edit_kwargs.keys())}"

        if mutation.action == "remove-step":
            step_id = str(args.get("step_id", ""))
            plan = core.remove_step(plan, step_id, force=True)
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"remove-step: step={step_id}"

        if mutation.action == "move-step":
            step_id = str(args.get("step_id", ""))
            target_phase = str(args.get("target_phase", ""))
            plan = core.move_step(plan, step_id, target_phase)
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"move-step: step={step_id} to={target_phase}"

        if mutation.action == "add-phase":
            name = str(args.get("name", ""))
            raw_phase_id = args.get("phase_id")
            add_phase_id: str | None = str(raw_phase_id) if raw_phase_id is not None else None
            plan, generated_id = core.add_phase(
                plan,
                name,
                phase_id=add_phase_id,
            )
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"add-phase: phase={generated_id} name={name}"

        if mutation.action == "edit-phase":
            phase_id = str(args.get("phase_id", ""))
            changes = args.get("changes", {})
            if not isinstance(changes, dict):
                raise ValueError(f"edit-phase changes must be dict, got {type(changes)}")
            phase_kwargs: dict[str, object] = {}
            if "name" in changes:
                phase_kwargs["name"] = str(changes["name"])
            if "depends_on" in changes:
                phase_kwargs["depends_on"] = list(_as_str_list(changes["depends_on"]))
            if "context" in changes:
                phase_kwargs["context"] = str(changes["context"])
            plan = core.edit_phase(plan, phase_id, **phase_kwargs)  # type: ignore[arg-type]
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"edit-phase: phase={phase_id} fields={tuple(phase_kwargs.keys())}"

        if mutation.action == "skip-step":
            step_id = str(args.get("step_id", ""))
            reason = str(args.get("reason", ""))
            plan = core.skip_step(plan, step_id, reason)
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"skip-step: step={step_id} reason={reason}"

        if mutation.action == "complete-phase":
            phase_id = str(args.get("phase_id", ""))
            reason = str(args.get("reason", "planner auto-complete"))
            plan, completed_ids = core.complete_phase(plan, phase_id, reason)
            save_plan(plan, self._plan_path, expected_hash=file_hash)
            return f"complete-phase: phase={phase_id} completed_steps={completed_ids}"

        raise ValueError(f"Unsupported planner mutation action: {mutation.action}")

    def _extract_step_id(self, mutation: PlannerMutationItem) -> str | None:
        """Extract the primary step_id from a mutation's arguments.

        Args:
            mutation: The mutation item.

        Returns:
            The step_id if present in arguments, else None.
        """
        step_id = mutation.arguments.get("step_id")
        if step_id is not None:
            return str(step_id)
        return None


def _as_str_list(value: object) -> list[str]:
    """Coerce a value to a list of strings.

    Args:
        value: Value to coerce. Accepts list, tuple, or None.

    Returns:
        List of strings.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]
