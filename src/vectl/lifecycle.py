"""Lifecycle mutators and claim/query helpers.

Extracted from ``vectl.core`` to reduce module size and isolate state
transition operations.
"""

# @invar:allow file_size: lifecycle mutators remain co-located to preserve the
# exported exception-based transition API; cross-module split is outside this
# step's caller-compatibility boundary.

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeAlias

# Path is kept as a local typing alias so postponed public annotations remain
# source-compatible without importing pathlib, which invar classifies as Shell I/O.
Path: TypeAlias = Any

from vectl.models import (
    AffinityError,
    AffinityMode,
    PhaseStatus,
    Plan,
    PlanError,
    RejectionEntry,
    SkipReason,
    Step,
    StepStatus,
    format_step_selector,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimStepMetadata:
    """Lifecycle-owned metadata for a successfully claimed step."""

    step_id: str
    step_name: str
    phase_id: str
    phase_name: str
    claimed_by: str
    suggested_agent: str | None
    affinity_override: bool


@dataclass(frozen=True)
class AffinityWarningMetadata:
    """Lifecycle-owned affinity warning details for claim consumers.

    Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-004
    requires claim/affinity/conflict metadata to be owned by this module while
    CLI and MCP only wrap or render it.
    """

    step_agent: str
    claiming_agent: str
    affinity: str
    message: str


@dataclass(frozen=True)
class AffinityOverrideMetadata:
    """Lifecycle-owned affinity override details for claim consumers.

    Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-004
    requires shared claim metadata to remain lifecycle-owned.
    """

    overridden_agent: str
    override_by: str
    message: str


@dataclass(frozen=True)
class ClaimConflictMetadata:
    """Lifecycle-owned structured details for an existing claim conflict.

    Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-004
    requires conflict metadata to be shared from lifecycle rather than
    redefined by MCP-only models.
    """

    step_id: str
    branch: str
    claimant: str
    claimed_at: str


class ClaimResult:
    """Result of a claim operation with affinity metadata.

    RFC: docs/RFC-affinity.md
    Used to communicate affinity warnings/errors to CLI and MCP layers.
    """

    def __init__(
        self,
        *,
        affinity_warning: bool = False,
        warning_message: str | None = None,
        affinity_override: bool = False,
        affinity_warning_metadata: AffinityWarningMetadata | None = None,
        affinity_override_metadata: AffinityOverrideMetadata | None = None,
    ) -> None:
        self.affinity_warning = affinity_warning
        self.warning_message = warning_message
        self.affinity_override = affinity_override
        self.affinity_warning_metadata = affinity_warning_metadata
        self.affinity_override_metadata = affinity_override_metadata

    def __repr__(self) -> str:
        return (
            f"ClaimResult(affinity_warning={self.affinity_warning}, "
            f"warning_message={self.warning_message!r}, "
            f"affinity_override={self.affinity_override}, "
            f"affinity_warning_metadata={self.affinity_warning_metadata!r}, "
            f"affinity_override_metadata={self.affinity_override_metadata!r})"
        )


class ClaimConflictError(PlanError):
    """Raised when a claim attempt fails due to an existing claim record.

    Contains structured information about the conflicting claim for diagnostic purposes.
    """

    def __init__(
        self,
        step_id: str,
        branch: str,
        claimant: str,
        claimed_at: str,
    ) -> None:
        self.step_id = step_id
        self.branch = branch
        self.claimant = claimant
        self.claimed_at = claimed_at
        self.metadata = ClaimConflictMetadata(
            step_id=step_id,
            branch=branch,
            claimant=claimant,
            claimed_at=claimed_at,
        )
        super().__init__(
            f"Step '{step_id}' is already claimed on branch '{branch}' "
            f"by '{claimant}' (claimed at {claimed_at})"
        )


def claim_step_metadata(
    *, phase_id: str, phase_name: str, step: Step, claimed_by: str
) -> ClaimStepMetadata:
    """Build lifecycle-owned metadata for transport surfaces."""

    return ClaimStepMetadata(
        step_id=step.id,
        step_name=step.name,
        phase_id=phase_id,
        phase_name=phase_name,
        claimed_by=claimed_by,
        suggested_agent=step.agent,
        affinity_override=step.affinity_override,
    )


def claim_conflict_metadata(error: ClaimConflictError) -> ClaimConflictMetadata:
    """Build lifecycle-owned conflict metadata from a claim conflict error."""

    return ClaimConflictMetadata(
        step_id=error.step_id,
        branch=error.branch,
        claimant=error.claimant,
        claimed_at=error.claimed_at,
    )


def claim_affinity_metadata(
    *, result: ClaimResult, step: Step, claiming_agent: str
) -> tuple[AffinityWarningMetadata | None, AffinityOverrideMetadata | None]:
    """Build lifecycle-owned affinity metadata for a claim result."""

    if result.warning_message is None:
        return None, None
    if result.affinity_override:
        return None, AffinityOverrideMetadata(
            overridden_agent=step.agent or "",
            override_by=claiming_agent,
            message=result.warning_message,
        )
    if result.affinity_warning:
        return AffinityWarningMetadata(
            step_agent=step.agent or "",
            claiming_agent=claiming_agent,
            affinity="suggested",
            message=result.warning_message,
        ), None
    return None, None


def get_claimed_steps(plan: Plan, agent: str | None = None) -> list[tuple[str, Step]]:
    """Get all currently claimed steps, optionally filtered by agent.

    Args:
        plan: The plan to query.
        agent: If provided, only return steps claimed by this agent.

    Returns:
        List of ``(phase_id, step)`` tuples for claimed steps.
    """
    results: list[tuple[str, Step]] = []
    for phase in plan.phases:
        for step in phase.steps:
            if step.status != StepStatus.CLAIMED:
                continue
            if agent is not None and step.claimed_by != agent:
                continue
            results.append((phase.id, step))
    return results


def claim_step(
    plan: Plan,
    step_id: str,
    agent_name: str,
    *,
    force: bool = False,
    claims_path: Path | None = None,
) -> tuple[Plan, ClaimResult]:
    """Claim a step for work.

    RFC: docs/RFC-affinity.md
    Enforces agent affinity when step.agent is set.

    Args:
        plan: The plan to modify.
        step_id: ID of the step to claim.
        agent_name: Name of the agent claiming the step.
        force: If True, override exclusive affinity violations.
        claims_path: Optional claims store path for branch-scoped coordination.

    Returns:
        Tuple of (modified plan, claim result with affinity metadata).

    Raises:
        PlanError: Step not found, wrong status, inactive phase, or unmet deps.
        AffinityError: Exclusive affinity violation when force=False.
    """
    result = ClaimResult()
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status not in (StepStatus.PENDING, StepStatus.REJECTED):
        raise PlanError(f"Step '{qualified_id}' cannot be claimed (status: {step.status.value})")

    active_ids = _get_active_phase_ids(plan)
    if phase.id not in active_ids:
        raise PlanError(f"Step '{qualified_id}' is in inactive phase '{phase.id}'")

    done_ids = {s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)}
    unmet = [dep for dep in step.depends_on if dep not in done_ids]
    if unmet:
        raise PlanError(f"Step '{qualified_id}' has unmet dependencies: {unmet}")

    _apply_claim_affinity(
        plan=plan,
        step=step,
        step_id=step_id,
        agent_name=agent_name,
        force=force,
        result=result,
    )

    if claims_path is not None:
        _acquire_claim_record(step_id=step_id, agent_name=agent_name, claims_path=claims_path)

    step.status = StepStatus.CLAIMED
    step.claimed_by = agent_name
    step.claimed_at = datetime.now(timezone.utc).isoformat()

    if phase.status in (PhaseStatus.PENDING, PhaseStatus.LOCKED):
        phase.status = PhaseStatus.IN_PROGRESS

    return plan, result


def _apply_claim_affinity(
    *,
    plan: Plan,
    step: Step,
    step_id: str,
    agent_name: str,
    force: bool,
    result: ClaimResult,
) -> None:
    if step.agent is None or step.agent == agent_name:
        return

    effective_affinity = step.affinity or plan.default_affinity
    if effective_affinity == AffinityMode.SUGGESTED:
        result.affinity_warning = True
        result.warning_message = (
            f"Step '{step_id}' suggests agent '{step.agent}' "
            f"but is being claimed by '{agent_name}'. "
            "Proceeding (affinity: suggested)"
        )
        result.affinity_warning_metadata = AffinityWarningMetadata(
            step_agent=step.agent,
            claiming_agent=agent_name,
            affinity=effective_affinity.value,
            message=result.warning_message,
        )
        return

    if effective_affinity != AffinityMode.EXCLUSIVE:
        return
    if not force:
        raise AffinityError(step_id, step.agent, agent_name)

    step.affinity_override = True
    step.affinity_override_by = agent_name
    step.affinity_override_at = datetime.now(timezone.utc).isoformat()
    result.affinity_override = True
    result.warning_message = (
        f"Affinity override: step '{step_id}' has exclusive affinity for "
        f"'{step.agent}'. Overridden by --force. Audit trail recorded."
    )
    result.affinity_override_metadata = AffinityOverrideMetadata(
        overridden_agent=step.agent,
        override_by=agent_name,
        message=result.warning_message,
    )


def _acquire_claim_record(*, step_id: str, agent_name: str, claims_path: Path) -> None:
    acquire_claim, cleanup_stale_claims, get_current_branch, _, get_claim_info = _claims_api()
    branch = get_current_branch()
    existing = get_claim_info(step_id, branch, claims_path)
    if existing is not None:
        raise ClaimConflictError(
            step_id=step_id,
            branch=branch,
            claimant=existing.agent,
            claimed_at=existing.claimed_at,
        )
    cleanup_stale_claims(claims_path)
    acquired = acquire_claim(step_id, branch, agent_name, claims_path)
    if not acquired:
        raise PlanError(f"Step '{step_id}' is already claimed on branch '{branch}'")


def complete_step(plan: Plan, step_id: str, evidence: str, claims_path: Path | None = None) -> Plan:
    """Mark a step as done with evidence."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status != StepStatus.CLAIMED:
        raise PlanError(
            f"Step '{qualified_id}' cannot be completed (status: {step.status.value}, "
            "must be claimed first)"
        )

    _release_claim_if_needed(step_id, claims_path, action="completing")

    step.status = StepStatus.DONE
    step.evidence = evidence
    step.done_at = datetime.now(timezone.utc).isoformat()

    if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
        phase.status = PhaseStatus.DONE
        _auto_unlock_phases(plan)

    return plan


def complete_phase(plan: Plan, phase_id: str, evidence: str) -> tuple[Plan, list[str]]:
    """Mark a phase as DONE with evidence.

    This is intended for historical migration/import workflows where steps may
    already be terminal (done/skipped) without having been claimed.

    Source: Eidos migrate phase needs explicit phase completion to avoid
    dependency deadlocks during import (user instruction: extend vectl first,
    then migrate; plus expert recommendation to keep phase completion explicit).

    Args:
        plan: The plan to modify.
        phase_id: Phase ID to complete.
        evidence: Evidence/audit note for why the phase is considered complete.

    Returns:
        Tuple of (updated plan, list of phase IDs unlocked by this completion).

    Raises:
        PlanError: If phase not found, has unmet dependencies, or contains
            any non-terminal steps.
    """
    if not evidence:
        raise PlanError("Phase evidence is required for completion")

    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")
    if phase.status == PhaseStatus.DONE:
        raise PlanError(f"Phase '{phase_id}' is already done")

    unmet_deps: list[str] = []
    for dep_id in phase.depends_on:
        dep = plan.find_phase(dep_id)
        if dep is None or dep.status != PhaseStatus.DONE:
            unmet_deps.append(dep_id)
    if unmet_deps:
        raise PlanError(f"Phase '{phase_id}' depends_on phases not done: {unmet_deps}")

    non_terminal = [s for s in phase.steps if s.status not in (StepStatus.DONE, StepStatus.SKIPPED)]
    if non_terminal:
        details = [f"{s.id}({s.status.value})" for s in non_terminal]
        raise PlanError(f"Phase '{phase_id}' has non-terminal steps: {details}")

    phase.status = PhaseStatus.DONE
    phase.evidence = evidence

    unlocked = _auto_unlock_phases(plan)
    return plan, unlocked


def defer_step(plan: Plan, step_id: str, claims_path: Path | None = None) -> Plan:
    """Return a claimed step to pending."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status != StepStatus.CLAIMED:
        raise PlanError(f"Step '{qualified_id}' cannot be deferred (status: {step.status.value})")

    _release_claim_if_needed(step_id, claims_path, action="deferring")

    step.status = StepStatus.PENDING
    step.claimed_by = None
    step.claimed_at = None
    return plan


def reject_step(plan: Plan, step_id: str, reason: str, reviewer: str = "") -> Plan:
    """Reject a completed step, moving it back for rework."""
    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status != StepStatus.DONE:
        raise PlanError(
            f"Step '{qualified_id}' cannot be rejected (status: {step.status.value}, must be done)"
        )

    step.status = StepStatus.REJECTED
    step.rejection_reason = reason
    step.rejection_history.append(
        RejectionEntry(
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer=reviewer,
        )
    )
    step.evidence = None

    if phase.status == PhaseStatus.DONE:
        phase.status = PhaseStatus.IN_PROGRESS

    return plan


def skip_step(plan: Plan, step_id: str, reason: str) -> Plan:
    """Skip a step with a reason.

    The reason must be a valid SkipReason value: superseded, irrelevant,
    absorbed, or deprioritized.
    """
    _validate_skip_reason(reason)

    found = plan.find_step(step_id)
    if found is None:
        raise PlanError(f"Step '{step_id}' not found")
    phase, step = found
    qualified_id = format_step_selector(phase.id, step.id)

    if step.status not in (StepStatus.PENDING, StepStatus.CLAIMED, StepStatus.REJECTED):
        raise PlanError(f"Step '{qualified_id}' cannot be skipped (status: {step.status.value})")

    step.status = StepStatus.SKIPPED
    step.skipped_reason = reason

    if all(s.status in (StepStatus.DONE, StepStatus.SKIPPED) for s in phase.steps):
        phase.status = PhaseStatus.DONE
        _auto_unlock_phases(plan)

    return plan


def skip_phase(
    plan: Plan, phase_id: str, reason: str, force: bool = False
) -> tuple[Plan, list[str]]:
    """Skip all remaining steps in a phase.

    For each step in the phase:
    - PENDING / REJECTED → SKIPPED with reason
    - CLAIMED → deferred to PENDING first, then SKIPPED
    - DONE / SKIPPED → left unchanged

    After processing, phase auto-completes if all steps are DONE/SKIPPED,
    triggering ``auto_unlock_phases`` for downstream phases.

    Args:
        plan: The plan to modify.
        phase_id: ID of the phase to skip.
        reason: Must be a valid SkipReason value.
        force: If True, allow skipping a locked phase with remaining steps.
            Empty phases (0 steps) are always allowed to skip regardless of lock.

    Returns:
        Tuple of (updated plan, list of step IDs that were skipped).

    Raises:
        PlanError: If phase not found, invalid reason, or phase is LOCKED
            (unless empty or force=True) or DONE.
    """
    _validate_skip_reason(reason)

    phase = plan.find_phase(phase_id)
    if phase is None:
        raise PlanError(f"Phase '{phase_id}' not found")

    if phase.status == PhaseStatus.LOCKED:
        if len(phase.steps) == 0:
            pass
        elif force:
            pass
        else:
            raise PlanError(
                f"Phase '{phase_id}' is locked — unlock it first, wait for dependencies, "
                "or use --force to override"
            )

    if phase.status == PhaseStatus.DONE:
        raise PlanError(f"Phase '{phase_id}' is already done")

    skipped_ids: list[str] = []
    for step in phase.steps:
        if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
            continue
        if step.status == StepStatus.CLAIMED:
            step.claimed_by = None
            step.claimed_at = None
        step.status = StepStatus.SKIPPED
        step.skipped_reason = reason
        skipped_ids.append(step.id)

    non_terminal = [s for s in phase.steps if s.status not in (StepStatus.DONE, StepStatus.SKIPPED)]
    if not non_terminal:
        phase.status = PhaseStatus.DONE
        _auto_unlock_phases(plan)

    return plan, skipped_ids


def _auto_unlock_phases(plan: Plan) -> list[str]:
    from vectl.core import auto_unlock_phases

    return auto_unlock_phases(plan)


def _validate_skip_reason(reason: str) -> None:
    valid_reasons = [r.value for r in SkipReason]
    if reason not in valid_reasons:
        raise PlanError(
            f"Invalid skip reason '{reason}'. Must be one of: {', '.join(valid_reasons)}"
        )


def _release_claim_if_needed(step_id: str, claims_path: Path | None, *, action: str) -> None:
    if claims_path is None:
        return
    _, _, get_current_branch, release_claim, _ = _claims_api()
    branch = get_current_branch()
    released = release_claim(step_id, branch, claims_path)
    if not released:
        _logger.warning(
            "Claim not found while %s step '%s' on branch '%s' (may have expired)",
            action,
            step_id,
            branch,
        )


def _get_active_phase_ids(plan: Plan) -> set[str]:
    """Get IDs of phases that are active or eligible (deps satisfied)."""
    done_phase_ids = {p.id for p in plan.phases if p.status == PhaseStatus.DONE}
    active: set[str] = set()
    for phase in plan.phases:
        if phase.status in (PhaseStatus.LOCKED,):
            if phase.depends_on and all(dep in done_phase_ids for dep in phase.depends_on):
                active.add(phase.id)
            continue
        if phase.status in (PhaseStatus.PENDING, PhaseStatus.IN_PROGRESS):
            active.add(phase.id)
    return active


def _claims_api():
    from vectl import core

    return (
        core.acquire_claim,
        core.cleanup_stale_claims,
        core.get_current_branch,
        core.release_claim,
        core.get_claim_info,
    )
