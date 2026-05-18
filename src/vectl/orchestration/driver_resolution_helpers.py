"""Pure-ish helpers for driver resolution and review projections.

Authority: docs/RFC-orch-drive.md sections 8.4.1, 12.3, 15.3, 17.1.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunRef,
    ControlDecision,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    ExecutionResult,
    ReconcileDisposition,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
)
from vectl.orchestration.driver_transitions import TERMINAL_DRIVE_STATUSES, validate_drive_transition, InvalidDriveTransitionError

if TYPE_CHECKING:
    from vectl.orchestration.run_store import RunRecord

def _infer_case_source(decision: ControlDecision) -> ResolutionCaseSource:
    """Infer the ResolutionCaseSource from a control decision.

    Authority: ORCHESTRATION-PLANE-INTERFACES.md section 3.8
    Authority: RFC-orch-drive.md section 8.4.1

    Maps control decision context to the appropriate case source tag.
    This is a heuristic mapping since the decision doesn't carry a
    case_source directly — the reason text is used as a signal.

    Args:
        decision: Control decision with kind='resolve'.

    Returns:
        The inferred ResolutionCaseSource tag.
    """
    reason_lower = decision.reason.lower()
    if "merge conflict" in reason_lower:
        return "merge_conflict"
    if "review" in reason_lower:
        return "review_failed"
    if "runtime" in reason_lower or "stall" in reason_lower or "fail" in reason_lower:
        return "runtime_failure"
    if "continuity" in reason_lower or "recovery" in reason_lower:
        return "continuity_block"
    if "ambiguit" in reason_lower or "authorit" in reason_lower:
        return "authority_ambiguity"
    return "unknown"


def _barrier_reason_for_case_source(source: ResolutionCaseSource) -> BarrierReason:
    """Map a ResolutionCaseSource to the corresponding BarrierReason.

    Authority: RFC-orch-drive.md section 8.4.1

    The barrier reason must match the canonical mapping defined in
    the RFC. Not all case sources have a direct barrier reason;
    unknown sources map to ``runtime_failure`` as the safest default.

    Args:
        source: Case source identifier.

    Returns:
        The corresponding barrier reason for drive barrier entry.
    """
    mapping: dict[ResolutionCaseSource, BarrierReason] = {
        "runtime_failure": "runtime_failure",
        "merge_conflict": "merge_conflict",
        "review_failed": "review_failed",
        "continuity_block": "recovery_gate",
        "authority_ambiguity": "runtime_failure",
        "unknown": "runtime_failure",
    }
    return mapping.get(source, "runtime_failure")


def _resolver_should_handle_active_barrier(record: DriveRecord) -> bool:
    """Return whether a persisted drive barrier should invoke resolver now.

    Foreground drive supervision may enter ``resolving`` after a child run
    creates a runtime/reconcile case.  That persisted barrier must not be a
    terminal operator boundary by itself: when a resolver is wired, the next
    loop pass should attempt resolver handling before giving up to the user.
    """

    barrier = record.barrier
    if barrier is None:
        return False
    if not record.blocked_case_ids:
        return False
    if barrier.pending_resolver_run_id is not None:
        return False
    return barrier.reason in {"runtime_failure", "merge_conflict", "review_failed"}


def _auto_unblock_stale_runtime_case(case: ResolutionCase) -> ResolutionReport | None:
    """Return a safe automatic unblock report for stale runtime barriers.

    This is intentionally narrow.  It only clears runtime/unknown cases when
    the drive already has runnable frontier work and the case has no blocked or
    in-progress step references intersecting that frontier.  In that state the
    barrier is stale orchestration bookkeeping, not an actionable operator
    decision, so keeping it would deadlock otherwise runnable work.
    """

    drive = case.drive
    if case.case_source not in {"runtime_failure", "unknown"}:
        return None
    if drive is None or not drive.frontier_step_ids:
        return None
    if drive.status != "blocked_operator":
        return None
    if drive.operator_pause_state == "paused":
        return None
    reason_text = f"{case.reason}\n{drive.summary}".lower()
    if any(
        token in reason_text
        for token in ("retry limit", "operator_required", "operator required", "requires operator")
    ):
        return None
    frontier = set(drive.frontier_step_ids)
    if frontier.intersection(case.core.in_progress_step_ids):
        return None
    if frontier.intersection(case.blocked_step_ids):
        return None
    return ResolutionReport(
        status="unblocked",
        summary=(
            "auto-unblocked stale runtime barrier: blocked refs do not "
            "intersect runnable drive frontier"
        ),
        evidence_refs=(
            f"case_id={case.case_id}",
            f"case_source={case.case_source}",
            f"frontier_step_ids={drive.frontier_step_ids}",
            "auto_resolution=stale_runtime_frontier_safe",
        ),
    )


def _agent_recovery_case_id(record: DriveRecord) -> str:
    """Build a stable-enough case identifier for agent-assisted recovery."""

    return f"agent_recovery_{record.drive_id}_{uuid.uuid4().hex}"


def _should_skip_agent_assisted_recovery(record: DriveRecord) -> str | None:
    """Return a skip reason when agent-assisted recovery must not run."""

    if record.status in TERMINAL_DRIVE_STATUSES:
        return f"drive is terminal: {record.status}"
    if record.operator_pause_state == "paused" or record.status == "paused":
        return "drive is explicitly operator-paused"
    if record.barrier is not None and record.barrier.reason == "operator_pause":
        return "drive barrier is an explicit operator pause"
    return None


def _execution_result_from_terminal_child_run(
    *,
    child_run: ChildRunRef,
    run_record: RunRecord,
) -> ExecutionResult:
    """Build review input from durable terminal child-run facts.

    Authority: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5,
    required change 7. The review gate consumes terminal ``ExecutionResult``
    facts; drive recovery stores those facts across ``ChildRunRef`` and
    ``RunRecord`` surfaces.

    Args:
        child_run: Terminal child-run reference from the drive store.
        run_record: Durable run registry record carrying output summary.

    Returns:
        ``ExecutionResult`` suitable for post-execution review evaluation.
    """
    status: Literal["success", "fail", "stall", "transport_error"]
    if child_run.status == "success":
        status = "success"
    elif child_run.status == "stall":
        status = "stall"
    elif child_run.status == "transport_error":
        status = "transport_error"
    else:
        status = "fail"

    return ExecutionResult(
        step_id=child_run.step_id or run_record.step_id,
        status=status,
        output_summary=run_record.output_summary,
        session_id=child_run.session_id,
    )


def _artifact_refs_for_review(
    *,
    child_run: ChildRunRef,
    run_record: RunRecord,
) -> tuple[str, ...]:
    """Collect artifact references for post-execution review.

    Authority: docs/RFC-orch-drive.md section 17.1 and
    docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5 required change 7.
    Review evaluation accepts artifact references alongside terminal execution
    output.

    Args:
        child_run: Terminal child-run reference.
        run_record: Durable run record with persisted artifact metadata.

    Returns:
        Stable artifact reference tuple for review evaluation.
    """
    refs: list[str] = []
    if child_run.artifact_root:
        refs.append(child_run.artifact_root)
    if run_record.artifact_root and run_record.artifact_root not in refs:
        refs.append(run_record.artifact_root)
    for artifact in run_record.artifacts:
        if artifact.artifact_ref:
            refs.append(artifact.artifact_ref)
    return tuple(dict.fromkeys(refs))


def _reconcile_disposition_from_run_record(
    run_record: RunRecord,
) -> ReconcileDisposition | None:
    """Return accepted reconcile proof for authoritative completion.

    Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md Rule 4
    (completion only after reconcile merged/noop) and contracts.py
    ``ReconcileDisposition`` contract.

    Args:
        run_record: Durable run record with runtime recovery state.

    Returns:
        ``"merged"`` or ``"noop"`` when terminal reconcile proof exists;
        otherwise ``None``.
    """
    runtime_state = run_record.runtime_state
    if runtime_state is None or runtime_state.reconcile_state is None:
        return None
    if runtime_state.reconcile_state.status == "merged":
        return "merged"
    if runtime_state.reconcile_state.status == "noop":
        return "noop"
    return None


def _apply_resolution_report_to_drive(
    record: DriveRecord,
    barrier: DriveBarrier,
    report: ResolutionReport,
    _post_decision: ControlDecision,
) -> tuple[DriveStatus, DriveBarrier | None, str]:
    """Resolve a ResolutionReport into drive status transition.

    Authority: RFC-orch-drive.md section 12.3
    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 6

    Implements the resolver continuation rules:
        - ``unblocked`` without planner_request: clear barrier → running
        - ``unblocked`` with planner_request: clear barrier → replanning
        - ``waiting``: keep resolving status, keep barrier
        - ``operator_required``: transition to blocked_operator
        - ``halt``: transition to halted

    Args:
        record: Current drive record (before report application).
        barrier: Current barrier state.
        report: Resolver report to apply.
        post_decision: Control decision after applying the report.

    Returns:
        Tuple of (new_status, new_barrier, summary).
    """
    if report.status == "unblocked":
        if report.planner_request is not None:
            # Authority: RFC-orch-drive.md section 12.3
            # If planner_request is present alongside status=unblocked,
            # drive must transition directly from resolving to replanning
            # without reopening the frontier in between.
            new_status: DriveStatus = "replanning"
            try:
                validate_drive_transition(record.status, new_status)
            except InvalidDriveTransitionError:
                # If already in a valid state, keep it
                new_status = record.status
            new_barrier = DriveBarrier(
                reason="planner_needed",
                entered_at=barrier.entered_at,
                case_ids=barrier.case_ids,
                pending_resolver_run_id=None,
                pending_planner_run_id=barrier.pending_planner_run_id,
                active_child_run_ids_at_entry=barrier.active_child_run_ids_at_entry,
            )
            summary = f"resolver unblocked (planner requested): {report.summary}"
            return new_status, new_barrier, summary

        # Authority: RFC-orch-drive.md section 12.3
        # unblocked without planner_request → clear barrier, continue
        new_status = "running"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            new_status = record.status
        summary = f"resolver unblocked: {report.summary}"
        return new_status, None, summary

    if report.status == "waiting":
        # Authority: RFC-orch-drive.md section 12.3
        # Stay in resolving/waiting; do not dispatch new work.
        new_status = "resolving"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            # If already resolving, stay there
            new_status = record.status
        summary = f"resolver waiting: {report.summary}"
        return new_status, barrier, summary

    if report.status == "operator_required":
        # Authority: RFC-orch-drive.md section 12.3
        new_status = "blocked_operator"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            new_status = record.status
        summary = f"resolver requires operator: {report.summary}"
        return new_status, barrier, summary

    # report.status == "halt"
    # Authority: RFC-orch-drive.md section 12.3, 10.3
    new_status = "halted"
    summary = f"resolver halt: {report.summary}"
    try:
        validate_drive_transition(record.status, new_status)
    except InvalidDriveTransitionError:
        new_status = record.status
    return new_status, barrier, summary


def _update_drive_record(
    record: DriveRecord,
    new_status: DriveStatus,
    barrier: DriveBarrier | None,
    summary: str,
) -> DriveRecord:
    """Create an updated DriveRecord with new status, barrier, and summary.

    Helper to avoid repeated DriveRecord reconstruction across
    the driver loop's branching paths.

    Args:
        record: Base drive record to update from.
        new_status: New drive status.
        barrier: New barrier state (may be None to clear barrier).
        summary: New summary text.

    Returns:
        New DriveRecord instance with updated fields.
    """
    now = time.time()
    return DriveRecord(
        drive_id=record.drive_id,
        plan_path=record.plan_path,
        status=new_status,
        started_at=record.started_at,
        updated_at=now,
        finished_at=now if new_status in TERMINAL_DRIVE_STATUSES else None,
        agent=record.agent,
        max_parallelism=record.max_parallelism,
        active_child_run_ids=record.active_child_run_ids,
        frontier_step_ids=record.frontier_step_ids,
        blocked_case_ids=record.blocked_case_ids,
        barrier=barrier,
        operator_pause_state=record.operator_pause_state,
        summary=summary,
    )
