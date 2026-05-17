"""Planner barrier projection helpers for the drive driver.

Authority: docs/RFC-orch-drive.md section 13.
"""

from __future__ import annotations

from vectl.orchestration.contracts import (
    ControlDecision,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    PlannerMutationBundle,
    PlannerRequest,
)
from vectl.orchestration.driver_transitions import InvalidDriveTransitionError, validate_drive_transition

def _construct_bundle_from_request(
    planner_request: PlannerRequest,
) -> PlannerMutationBundle:
    """Construct a PlannerMutationBundle from a planner request for barrier entry.

    Authority: RFC-orch-drive.md section 13.2, 13.3

    When the planner loop is triggered by a control decision with kind='replan',
    the driver must enter the replan barrier. The bundle is synthesized from
    the planner_request to establish the barrier context.

    In a full integration with a planner child run, this bundle would come
    from the planner child run output. For the single-pass integration, the
    driver uses the planner_request to construct a placeholder bundle that
    records the replan intent and affected steps.

    Args:
        planner_request: The planner request from the control decision.

    Returns:
        A PlannerMutationBundle capturing the replan intent.
    """
    return PlannerMutationBundle(
        status="applyable",
        summary=f"Planner request: {planner_request.reason}",
        mutations=planner_request.mutations,
        affected_steps=planner_request.affected_steps,
        evidence_refs=planner_request.evidence_refs,
        safety_notes=planner_request.constraints,
    )


def _apply_planner_bundle_to_drive(
    record: DriveRecord,
    barrier: DriveBarrier,
    bundle: PlannerMutationBundle,
    post_decision: ControlDecision,
    invalidated_lease_count: int = 0,
) -> tuple[DriveStatus, DriveBarrier | None, str]:
    """Resolve a PlannerMutationBundle into drive status transition.

    Authority: RFC-orch-drive.md section 13.5
    Authority: ORCHESTRATION-PLANE-INTERFACES.md section 4.1 (apply_planner_result)

    Implements the planner continuation rules:
        - ``applyable``: clear barrier → running (re-evaluate from refreshed state)
        - ``operator_required``: transition to blocked_operator
        - ``halt``: transition to halted

    Args:
        record: Current drive record (before bundle application).
        barrier: Current barrier state.
        bundle: Planner mutation bundle that was applied.
        post_decision: Control decision after applying the bundle.
        invalidated_lease_count: Number of leases that were invalidated.

    Returns:
        Tuple of (new_status, new_barrier, summary).
    """
    lease_note = (
        f" ({invalidated_lease_count} leases invalidated)" if invalidated_lease_count > 0 else ""
    )

    if bundle.status == "applyable":
        # Authority: RFC-orch-drive.md section 13.5
        # "valid applyable bundle: apply, refresh, reopen scheduling"
        new_status: DriveStatus = "running"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            new_status = record.status
        summary = f"planner mutations applied; barrier cleared{lease_note}"
        if post_decision.kind == "dispatch_batch":
            summary += f"; next: dispatch_batch steps={post_decision.step_ids}"
        elif post_decision.kind == "wait":
            summary += f"; next: wait ({post_decision.reason})"
        elif post_decision.kind == "done":
            summary += "; next: done"
        return new_status, None, summary

    if bundle.status == "operator_required":
        # Authority: RFC-orch-drive.md section 13.5
        new_status = "blocked_operator"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            new_status = record.status
        summary = f"planner requires operator intervention: {bundle.summary}"
        return new_status, barrier, summary

    # bundle.status == "halt"
    # Authority: RFC-orch-drive.md section 13.5, 10.3
    new_status = "halted"
    try:
        validate_drive_transition(record.status, new_status)
    except InvalidDriveTransitionError:
        new_status = record.status
    summary = f"planner halted: {bundle.summary}"
    return new_status, barrier, summary
