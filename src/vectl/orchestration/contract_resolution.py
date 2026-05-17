"""Resolver case and report contracts.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.8, 3.9
Authority: docs/RFC-orch-drive.md section 12.2
"""

from dataclasses import dataclass
from typing import Literal

from vectl.orchestration.contract_drive import DriveRecord, PlannerRequest
from vectl.orchestration.contract_literals import ResolutionCaseSource
from vectl.orchestration.contract_snapshots import CoreSnapshot, RosterSnapshot, RuntimeSnapshot

@dataclass(frozen=True)
class ResolutionCase:
    """
    Problem handed from control to resolver when normal flow does not close.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.8

    Attributes:
        reason: Why normal flow is blocked.
        core: Current core snapshot.
        roster: Current roster snapshot.
        runtime: Current runtime snapshot.
        case_id: Explicit identifier for the resolution case.
        case_source: Coarse source tag for how the case arose.
        summary: Optional bounded human-readable case summary.
        drive: Optional drive record for drive-aware resolver context.
        blocked_step_ids: Optional blocked-step coordination context.
        artifact_refs: Optional evidence/artifact references preserved on the case.
    """

    reason: str
    core: CoreSnapshot
    roster: RosterSnapshot
    runtime: RuntimeSnapshot
    case_id: str = ""
    case_source: ResolutionCaseSource = "unknown"
    summary: str | None = None
    drive: DriveRecord | None = None
    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolutionReport:
    """
    What resolver returns to control.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.9
    Authority: docs/RFC-orch-drive.md section 12.2

    Optional extension field:
        ``planner_request``: when present, indicates the resolver recommends
        transition to a replan barrier. If ``planner_request`` is present
        alongside ``status='unblocked'``, drive policy must prefer the
        replanning transition.

    Attributes:
        status: One of 'unblocked', 'waiting', 'operator_required', 'halt'.
        summary: Human-readable explanation of findings.
        evidence_refs: References to supporting evidence.
        operator_message: Message to surface to human operator.
        planner_request: Optional planner mutation request for replan path.
    """

    status: Literal["unblocked", "waiting", "operator_required", "halt"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None
    planner_request: PlannerRequest | None = None

