"""Lifecycle claim metadata and compatibility result DTOs."""

from __future__ import annotations

from dataclasses import dataclass

from vectl.models import PlanError, Step

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
