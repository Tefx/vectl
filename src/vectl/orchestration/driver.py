"""
Drive-level orchestration loop and barrier management.

Authority: docs/RFC-orch-drive.md sections 10, 14, 15, 17
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.11

This module provides:
  - result types for the four drive-surface methods
  - DriveDriver (wired implementation using existing orchestration
    building blocks: DriveStore, PlanAwareControl, core adapter)
  - drive status transition validation
  - drive admission and parallelism validation
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunRef,
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    ExecutionResult,
    PlannerMutationBundle,
    PlannerRequest,
    ReconcileDisposition,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.review_gate import DefaultReviewGate, ReviewGateResult

if TYPE_CHECKING:
    from vectl.orchestration.control import PlanAwareControl
    from vectl.orchestration.control_channel import FilesystemControlChannel
    from vectl.orchestration.core_adapter import CoreAdapter, PlannerMutationApplier
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.run_store import DriveStore, RunRecord, RunRegistry


# ---------------------------------------------------------------------
# Drive result types
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DriveStartResult:
    """Result of ``start_drive``.

    Authority: docs/RFC-orch-drive.md section 7.2

    Attributes:
        drive_id: Unique identifier of the created/resolved drive.
        status: Drive status after start (typically ``running``).
        frontier_step_ids: Initial claimable frontier at drive start.
        summary: Human-readable drive start summary.
    """

    drive_id: str
    status: DriveStatus = "running"
    frontier_step_ids: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class DriveLoopResult:
    """Result of ``run_drive_loop``.

    Authority: docs/RFC-orch-drive.md section 10

    Captures the terminal state of a drive loop iteration or the final
    state when the loop exits.

    Attributes:
        drive_id: Drive identifier.
        status: Terminal or paused drive status.
        completed_steps: Steps completed during this loop invocation.
        active_child_run_ids: Child runs still active when loop exited.
        barrier: Barrier state at loop exit, if any.
        summary: Human-readable loop result summary.
    """

    drive_id: str
    status: DriveStatus
    completed_steps: tuple[str, ...] = ()
    active_child_run_ids: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    summary: str = ""


@dataclass(frozen=True)
class DriveResumeResult:
    """Result of ``resume_drive``.

    Authority: docs/RFC-orch-drive.md section 15.1

    Attributes:
        drive_id: Drive identifier.
        status: Drive status after resume.
        restored_child_run_ids: Child run identifiers that were restored.
        barrier: Barrier state after resume, if any.
        summary: Human-readable resume result summary.
    """

    drive_id: str
    status: DriveStatus
    restored_child_run_ids: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    summary: str = ""


@dataclass(frozen=True)
class DriveRecoverResult:
    """Result of ``recover_drive``.

    Authority: docs/RFC-orch-drive.md section 15.2

    Attributes:
        drive_id: Drive identifier.
        status: Drive status after recovery.
        recovered_child_run_ids: Child run identifiers that were recovered.
        failed_child_run_ids: Child run identifiers that could not be recovered.
        conflict_resolutions: Descriptions of conflict resolution outcomes.
        barrier: Barrier state after recovery, if any.
        summary: Human-readable recovery result summary.
    """

    drive_id: str
    status: DriveStatus
    recovered_child_run_ids: tuple[str, ...] = ()
    failed_child_run_ids: tuple[str, ...] = ()
    conflict_resolutions: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    summary: str = ""


@dataclass(frozen=True)
class DriveStatusResult:
    """Result of ``drive_status`` inspection.

    Authority: docs/RFC-orch-drive.md section 7.2

    Attributes:
        drive_id: Drive identifier.
        status: Current drive status.
        scope_kind: Discriminator identifying this as a drive-scoped result.
            Always ``"drive"`` for drive-level status results.
        active_child_run_ids: Currently active child runs.
        frontier_step_ids: Current claimable frontier.
        blocked_case_ids: Open case identifiers blocking scheduling.
        barrier: Current barrier, if active.
        summary: Human-readable aggregate progress description.
    """

    drive_id: str
    status: DriveStatus
    scope_kind: str = "drive"
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    blocked_case_ids: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    summary: str = ""


# ---------------------------------------------------------------------
# Drive admission error
# ---------------------------------------------------------------------


class DriveAdmissionError(Exception):
    """Raised when drive admission is rejected.

    Authority: docs/RFC-orch-drive.md section 14.1

    A drive cannot be started when an active drive already exists for the
    same plan identity. This error preserves the blocking ``drive_id`` so
    operators can identify the conflicting drive.
    """

    def __init__(self, message: str, *, active_drive_id: str) -> None:
        super().__init__(message)
        self.active_drive_id = active_drive_id
        self.message = message


class MaxParallelismError(ValueError):
    """Raised when ``max_parallelism`` is out of the valid range [1, 32].

    Authority: docs/RFC-orch-drive.md section 7.2
    """

    def __init__(self, value: int) -> None:
        super().__init__(f"max_parallelism must be between 1 and 32, got {value}")
        self.value = value


# ---------------------------------------------------------------------
# Valid transition enforcement
# ---------------------------------------------------------------------

# Authority: docs/RFC-orch-drive.md section 8.2.1
#
# This module owns the canonical transition table. Implementation steps
# will fill in the enforcement logic; this contract pins the shape.

DRIVE_TRANSITIONS: dict[tuple[DriveStatus, DriveStatus], str] = {
    # (from_status, to_status): reason_description
    ("running", "paused"): "operator pause",
    ("running", "resolving"): "barrier entered for non-closure",
    ("running", "replanning"): "barrier entered for replan",
    ("running", "recovering"): "recovery requested",
    ("running", "completed"): "all phases closed",
    ("running", "halted"): "hard halt decision",
    ("running", "failed_unrecoverable"): "unrecoverable invariant break",
    ("running", "stopped"): "operator stop",
    ("paused", "running"): "operator unpause",
    ("paused", "stopped"): "operator stop",
    ("paused", "recovering"): "recovery requested",
    ("resolving", "running"): "resolver returns unblocked and barrier clears",
    ("resolving", "resolving"): "resolver returns waiting",
    ("resolving", "blocked_operator"): "resolver returns operator_required",
    ("resolving", "halted"): "resolver returns halt",
    ("resolving", "replanning"): "resolver requests planner",
    ("replanning", "running"): "planner bundle applied and barrier clears",
    ("replanning", "blocked_operator"): "planner requests operator intervention",
    ("replanning", "halted"): "planner emits halt-worthy fatal result",
    ("replanning", "failed_unrecoverable"): "planner/adapter invariant break",
    ("blocked_operator", "running"): "operator resumes with barrier cleared",
    ("blocked_operator", "stopped"): "operator stop",
    ("blocked_operator", "recovering"): "recovery requested",
    ("recovering", "running"): "state restored and no barrier remains",
    ("recovering", "resolving"): "restored state requires resolver",
    ("recovering", "replanning"): "restored state requires planner",
    ("recovering", "blocked_operator"): "operator attention required",
    ("recovering", "failed_unrecoverable"): "unrecoverable corruption detected",
}
"""Canonical drive status transition table.

Authority: docs/RFC-orch-drive.md section 8.2.1

Any transition not in this table is invalid and MUST raise.
"""

TERMINAL_DRIVE_STATUSES: frozenset[DriveStatus] = frozenset(
    {"completed", "halted", "failed_unrecoverable", "stopped"}
)
"""Drive statuses that are terminal (no further transitions valid).

Authority: docs/RFC-orch-drive.md section 10.3
"""

DRIVE_DEFAULTS = {
    "max_parallelism": 4,
    "collect_poll_interval_seconds": 0.25,
    "resolver_child_run_timeout_seconds": 300,
    "planner_child_run_timeout_seconds": 300,
    "barrier_stabilization_wait_seconds": 120,
    "child_run_heartbeat_stale_threshold_seconds": 90,
}
"""Default configuration values for drive execution.

Authority: docs/RFC-orch-drive.md section 10.4
"""


# ---------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------


class InvalidDriveTransitionError(RuntimeError):
    """Raised when a drive status transition is not in the valid table.

    Authority: docs/RFC-orch-drive.md section 8.2.1
    """

    def __init__(
        self,
        *,
        from_status: DriveStatus,
        to_status: DriveStatus,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"invalid drive status transition: {from_status} -> {to_status}")


def validate_drive_transition(
    from_status: DriveStatus,
    to_status: DriveStatus,
) -> str:
    """Validate a drive status transition against the canonical table.

    Authority: docs/RFC-orch-drive.md section 8.2.1

    Args:
        from_status: Current drive status.
        to_status: Target drive status.

    Returns:
        The reason description for the valid transition.

    Raises:
        InvalidDriveTransitionError: If the transition is not valid.
    """
    key = (from_status, to_status)
    if key in DRIVE_TRANSITIONS:
        return DRIVE_TRANSITIONS[key]
    raise InvalidDriveTransitionError(from_status=from_status, to_status=to_status)


def _generate_drive_id() -> str:
    """Generate a unique drive identifier.

    Returns:
        A string of the form ``drv_<uuid4_hex>``.
    """
    return f"drv_{uuid.uuid4().hex}"


def _barrier_status(reason: BarrierReason) -> DriveStatus:
    """Map a barrier reason to the appropriate drive status for barrier re-entry.

    Authority: RFC-orch-drive.md section 8.2.1 (transition table)
    Authority: RFC-orch-drive.md section 15.3 (recovery order)

    When a barrier must be re-entered during resume or recovery, the
    target status is determined by the barrier reason. This function
    centralizes that mapping so both ``resume_drive`` and ``recover_drive``
    use the same authoritative mapping.

    Args:
        reason: The barrier reason to map.

    Returns:
        The drive status appropriate for re-entry with this barrier reason.
    """
    if reason == "operator_pause":
        return "blocked_operator"
    if reason == "planner_needed":
        return "replanning"
    # runtime_failure, merge_conflict, review_failed, recovery_gate
    # all map to "resolving" per the transition table.
    return "resolving"


# ---------------------------------------------------------------------
# Resolver-loop helper functions
# ---------------------------------------------------------------------


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
    post_decision: ControlDecision,
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


# ---------------------------------------------------------------------
# Planner-loop helper functions
# ---------------------------------------------------------------------


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
        mutations=(),
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


# ---------------------------------------------------------------------
# Drive Driver
# ---------------------------------------------------------------------


class DriveDriver:
    """Concrete implementation of drive-level orchestration wiring.

    Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15
    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.11

    This class composes existing orchestration building blocks (DriveStore,
    PlanAwareControl, core adapter) to implement the four drive-surface
    entry points: start_drive, run_drive_loop, resume_drive, recover_drive.

    It is a composition layer — not a fifth architecture component. The driver
    delegates plan-aware decisions to Control, persistence to DriveStore, and
    authority reads to CoreAdapter.

    Resolver-loop integration (RFC-orch-drive.md section 12.3):
        When control evaluates to ``kind='resolve'``, the driver creates a
        ``ResolutionCase`` snapshot, invokes the resolver, and continues based
        on the ``ResolutionReport`` status:

        - ``unblocked``: refresh state, clear barrier, continue normal scheduling
        - ``waiting``: stay in resolving/waiting; do not dispatch new work
        - ``operator_required``: transition drive to ``blocked_operator``
        - ``halt``: transition drive to ``halted``
        - ``planner_request`` present: transition from resolving to replanning

        Refresh-after-resolution is mandatory (ORCHESTRATION-PLANE-RESOLUTION-CONTRACT
        section 6): control must re-read authoritative state and never trust
        stale pre-resolution assumptions.
    """

    def __init__(
        self,
        *,
        drive_store: DriveStore,
        core_adapter: CoreAdapter,
        control: PlanAwareControl,
        resolver: Resolver | None = None,
        review_gate: DefaultReviewGate | None = None,
        planner_mutation_applier: PlannerMutationApplier | None = None,
        run_registry: RunRegistry | None = None,
        control_channel: FilesystemControlChannel | None = None,
        child_run_launcher: Callable[[str, str, str], ChildRunRef] | None = None,
        child_run_canceller: Callable[[str], bool] | None = None,
        max_parallelism: int = 4,
    ) -> None:
        self._drive_store = drive_store
        self._core_adapter = core_adapter
        self._control = control
        self._resolver = resolver
        self._review_gate = review_gate if review_gate is not None else DefaultReviewGate()
        self._planner_mutation_applier = planner_mutation_applier
        self._run_registry = run_registry
        self._control_channel = control_channel
        self._child_run_launcher = child_run_launcher
        self._child_run_canceller = child_run_canceller
        self._max_parallelism = max(1, min(32, max_parallelism))

    # ------------------------------------------------------------------
    # start_drive
    # ------------------------------------------------------------------

    def start_drive(
        self,
        *,
        plan_path: str,
        agent: str = "",
        max_parallelism: int = 4,
    ) -> DriveStartResult:
        """Create a new drive for the given plan.

        Authority: docs/RFC-orch-drive.md section 7.2, 14.1

        If an active drive already exists for the same plan identity,
        raises ``DriveAdmissionError``.

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.
            agent: Agent role that owns this drive.
            max_parallelism: Maximum concurrent step child runs (1–32).

        Returns:
            ``DriveStartResult`` with the new drive state.

        Raises:
            DriveAdmissionError: If an active drive exists for this plan.
            MaxParallelismError: If ``max_parallelism`` is outside [1, 32].
        """
        if max_parallelism < 1 or max_parallelism > 32:
            raise MaxParallelismError(max_parallelism)

        # Authority: RFC-orch-drive.md section 14.1
        # "At most one active drive may exist for one plan identity."
        existing = self._drive_store.active_drive_for_plan(plan_path)
        if existing is not None:
            raise DriveAdmissionError(
                f"active drive already exists for plan '{plan_path}': drive_id={existing.drive_id}",
                active_drive_id=existing.drive_id,
            )

        drive_id = _generate_drive_id()
        core_snapshot = self._core_adapter.snapshot(agent=agent or "default")
        now = time.time()

        frontier = core_snapshot.claimable_step_ids

        record = DriveRecord(
            drive_id=drive_id,
            plan_path=plan_path,
            status="running",
            started_at=now,
            updated_at=now,
            finished_at=None,
            agent=agent,
            max_parallelism=max_parallelism,
            active_child_run_ids=(),
            frontier_step_ids=frontier,
            blocked_case_ids=(),
            barrier=None,
            operator_pause_state="active",
            summary=f"drive started; frontier width={len(frontier)}",
        )
        self._drive_store.save_drive(record)

        return DriveStartResult(
            drive_id=drive_id,
            status="running",
            frontier_step_ids=frontier,
            summary=f"drive started; frontier width={len(frontier)}",
        )

    # ------------------------------------------------------------------
    # run_drive_loop
    # ------------------------------------------------------------------

    def run_drive_loop(self, drive_id: str) -> DriveLoopResult:
        """Execute one drive scheduling loop pass.

        Authority: docs/RFC-orch-drive.md section 10

        This method:
          1. Refreshes authoritative snapshots (core, roster, runtime, drive)
          2. Evaluates control decision
          3. If resolve: enter barrier, create ResolutionCase, invoke resolver,
             consume report, refresh state, continue with decision semantics
          4. If replan: enter barrier for planner transition
          5. Persists updated drive state reflecting the decision
          6. Returns the loop result

        Resolver-loop integration (RFC-orch-drive.md section 12.3):
            - ``unblocked``: refresh state, clear barrier, continue scheduling
            - ``waiting``: stay in resolving status; do not dispatch new work
            - ``operator_required``: transition drive to ``blocked_operator``
            - ``halt``: transition drive to ``halted``
            - ``planner_request`` present: transition from resolving to replanning

        Args:
            drive_id: The drive to run one loop pass for.

        Returns:
            ``DriveLoopResult`` capturing the drive state and decision outcome.

        Raises:
            DriveStoreError: If no DriveRecord exists for the drive_id.
        """
        from vectl.orchestration.run_store import DriveStoreError

        record = self._drive_store.replay_drive_state(drive_id)
        if record is None:
            raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

        # Terminal drives cannot be looped.
        if record.status in TERMINAL_DRIVE_STATUSES:
            return DriveLoopResult(
                drive_id=drive_id,
                status=record.status,
                completed_steps=(),
                active_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary=f"drive is terminal: {record.status}",
            )

        # --- Drive-scoped control consumption ---
        # Authority: RFC-orch-drive.md section 7.3, 10
        # Authority: ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
        #   'control requests must be consumed, not only queued'
        # Consume queued drive.pause/unpause/stop messages before evaluating
        # the control decision. This ensures operator commands are reflected
        # in the DriveRecord state before scheduling decisions are made.
        original_operator_pause_state = record.operator_pause_state
        record = self._consume_drive_control(record)

        # If control consumption transitioned the drive to a non-dispatching
        # state (stopped, paused, or status changed by a control message),
        # return immediately without further evaluation. The control message
        # has already been applied and persisted; we should not overwrite it
        # with a control decision.
        if record.status in TERMINAL_DRIVE_STATUSES or record.status == "paused":
            return DriveLoopResult(
                drive_id=drive_id,
                status=record.status,
                completed_steps=(),
                active_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary=record.summary,
            )

        # If control consumption was applied (operator_pause_state changed
        # from the original record), return early so the consumption result
        # is not overwritten by a subsequent control evaluation.
        if original_operator_pause_state != record.operator_pause_state:
            return DriveLoopResult(
                drive_id=drive_id,
                status=record.status,
                completed_steps=(),
                active_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary=record.summary,
            )

        reviewed_terminal_child = self._review_terminal_child_run(record)
        if reviewed_terminal_child is not None:
            return reviewed_terminal_child

        # Refresh authoritative snapshot.
        core = self._core_adapter.snapshot(agent=record.agent or "default")

        # Evaluate control decision with drive context.
        drive_barrier = record.barrier
        open_case_ids = record.blocked_case_ids
        roster_snap = self._control.sources.roster.snapshot()
        runtime_snap = self._control.sources.runtime.snapshot()

        if _resolver_should_handle_active_barrier(record) and self._resolver is not None:
            decision = ControlDecision(
                kind="resolve",
                reason=f"Open cases require resolution: {', '.join(open_case_ids)}",
                case_ids=open_case_ids,
            )
            return self._handle_resolve_decision(
                drive_id=drive_id,
                record=record,
                core=core,
                roster=roster_snap,
                runtime=runtime_snap,
                decision=decision,
            )

        decision = self._control.evaluate(
            core=core,
            roster=roster_snap,
            runtime=runtime_snap,
            drive=record,
            barrier=drive_barrier,
            open_case_ids=open_case_ids,
        )

        # --- Resolver-loop integration ---
        # Authority: RFC-orch-drive.md section 12.3, 5.4
        # Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 5, 6
        if decision.kind == "resolve" and self._resolver is not None:
            return self._handle_resolve_decision(
                drive_id=drive_id,
                record=record,
                core=core,
                roster=roster_snap,
                runtime=runtime_snap,
                decision=decision,
            )

        # --- Planner-loop integration ---
        # Authority: RFC-orch-drive.md section 13, 5.6
        # Authority: ORCHESTRATION-PLANE-INTERFACES.md section 4.1 (apply_planner_result)
        if decision.kind == "replan" and self._planner_mutation_applier is not None:
            return self._handle_replan_decision(
                drive_id=drive_id,
                record=record,
                core=core,
                roster=roster_snap,
                runtime=runtime_snap,
                decision=decision,
            )

        # Persist updated drive state reflecting the decision.
        completed_steps: tuple[str, ...] = ()
        new_status = record.status
        barrier = record.barrier
        summary = f"loop pass: decision={decision.kind}"
        launched_refs: tuple[ChildRunRef, ...] = ()

        if decision.kind == "dispatch_batch":
            launched_refs = self._launch_child_runs_for_decision(record, decision)
            summary = (
                f"dispatch_batch: steps={decision.step_ids} "
                f"capacity_used={decision.capacity_used} "
                f"capacity_remaining={decision.capacity_remaining}"
            )
            if launched_refs:
                child_ids = tuple(ref.run_id for ref in launched_refs)
                summary += f" child_runs={child_ids}"
        elif decision.kind == "dispatch":
            launched_refs = self._launch_child_runs_for_decision(record, decision)
            summary = f"dispatch: step={decision.step_ids}"
            if launched_refs:
                child_ids = tuple(ref.run_id for ref in launched_refs)
                summary += f" child_runs={child_ids}"
        elif decision.kind == "done":
            new_status = "completed"
            summary = "all phases closed; drive complete"
        elif decision.kind == "halt":
            new_status = "halted"
            summary = f"halt: {decision.reason}"
        elif decision.kind == "wait":
            summary = f"wait: {decision.reason}"
        elif decision.kind == "resolve":
            # No resolver available — enter barrier with case ids recorded
            # but do NOT invoke resolver. The barrier entry still captures
            # the case and transitions the drive to resolving status.
            new_status, barrier, summary = self._enter_barrier_for_resolve(record, decision)
        elif decision.kind == "replan":
            new_status, barrier, summary = self._enter_barrier_for_replan(record, decision)

        updated = _update_drive_record(
            record=record,
            new_status=new_status,
            barrier=barrier,
            summary=summary,
        )
        if launched_refs:
            active_child_run_ids = tuple(
                dict.fromkeys(
                    (*record.active_child_run_ids, *(ref.run_id for ref in launched_refs))
                )
            )
            updated = DriveRecord(
                drive_id=updated.drive_id,
                plan_path=updated.plan_path,
                status=updated.status,
                started_at=updated.started_at,
                updated_at=updated.updated_at,
                finished_at=updated.finished_at,
                agent=updated.agent,
                max_parallelism=updated.max_parallelism,
                active_child_run_ids=active_child_run_ids,
                frontier_step_ids=updated.frontier_step_ids,
                blocked_case_ids=updated.blocked_case_ids,
                barrier=updated.barrier,
                operator_pause_state=updated.operator_pause_state,
                summary=updated.summary,
            )
        self._drive_store.save_drive(updated)

        return DriveLoopResult(
            drive_id=drive_id,
            status=new_status,
            completed_steps=completed_steps,
            active_child_run_ids=updated.active_child_run_ids,
            barrier=barrier,
            summary=summary,
        )

    def _launch_child_runs_for_decision(
        self,
        record: DriveRecord,
        decision: ControlDecision,
    ) -> tuple[ChildRunRef, ...]:
        """Launch and persist child runs for dispatch decisions when wired.

        Structural tests can instantiate the driver without a launcher; in that
        mode dispatch decisions are still reflected in the summary but no
        external runner is started. Production app wiring supplies the launcher.
        """

        if self._child_run_launcher is None:
            return ()
        launched: list[ChildRunRef] = []
        for step_id in decision.step_ids:
            role_id = decision.role_bindings.get(step_id, record.agent or "default")
            ref = self._child_run_launcher(record.drive_id, step_id, role_id)
            self._drive_store.save_child_run(ref)
            launched.append(ref)
        return tuple(launched)

    def _review_terminal_child_run(self, record: DriveRecord) -> DriveLoopResult | None:
        """Route one terminal step child-run fact through post-execution review.

        Authority: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5,
        required changes 6-7. Terminal execution output must pass through the
        named post-execution review hook before the driver treats the step as
        completed or enters a review barrier.

        Args:
            record: Current drive record after drive-scoped control consumption.

        Returns:
            ``DriveLoopResult`` when a terminal child run was reviewed; otherwise
            ``None`` so normal scheduling can continue.
        """
        if self._run_registry is None:
            return None

        for run_id in record.active_child_run_ids:
            child_run = self._drive_store.child_run_by_id(run_id)
            if child_run is None or child_run.kind != "step" or child_run.step_id is None:
                continue
            if child_run.status in ("pending", "running", "cancelled"):
                continue

            run_record = self._run_registry.by_id(run_id)
            if run_record is None:
                continue

            execution_result = _execution_result_from_terminal_child_run(
                child_run=child_run,
                run_record=run_record,
            )
            review_result = self._handle_post_execution_review(
                step_id=child_run.step_id,
                execution_result=execution_result,
                artifact_refs=_artifact_refs_for_review(child_run=child_run, run_record=run_record),
            )
            authoritative_completion = False
            if review_result.status == "pass":
                reconcile_disposition = _reconcile_disposition_from_run_record(run_record)
                if reconcile_disposition is not None:
                    self._core_adapter.complete_step(
                        child_run.step_id,
                        evidence=(
                            f"post-execution review passed: {review_result.summary}; "
                            f"evidence_refs={review_result.evidence_refs}"
                        ),
                        reconcile_disposition=reconcile_disposition,
                    )
                    authoritative_completion = True

            updated = self._apply_post_execution_review_result(
                record=record,
                child_run=child_run,
                review_result=review_result,
                authoritative_completion=authoritative_completion,
            )
            self._drive_store.save_drive(updated)
            completed_steps = (child_run.step_id,) if authoritative_completion else ()
            return DriveLoopResult(
                drive_id=record.drive_id,
                status=updated.status,
                completed_steps=completed_steps,
                active_child_run_ids=updated.active_child_run_ids,
                barrier=updated.barrier,
                summary=updated.summary,
            )

        return None

    def _handle_post_execution_review(
        self,
        *,
        step_id: str,
        execution_result: ExecutionResult,
        artifact_refs: tuple[str, ...] = (),
    ) -> ReviewGateResult:
        """Evaluate terminal execution output through the configured review gate.

        Authority: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5,
        required changes 6-7. This method is the canonical driver hook between
        terminal execution facts and authoritative completion handling.

        Args:
            step_id: Step identifier for the completed child run.
            execution_result: Terminal execution result from the run registry.
            artifact_refs: Artifact references that support review evaluation.

        Returns:
            Bounded post-execution review result from the concrete review gate.
        """
        return self._review_gate.evaluate(
            step_id=step_id,
            execution_result=execution_result,
            artifact_refs=artifact_refs,
        )

    def _apply_post_execution_review_result(
        self,
        *,
        record: DriveRecord,
        child_run: ChildRunRef,
        review_result: ReviewGateResult,
        authoritative_completion: bool,
    ) -> DriveRecord:
        """Project a review outcome onto durable drive state.

        Authority: docs/RFC-orch-drive.md section 17.1.1 and
        docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md Wave 5 acceptance
        criteria 4-5. Non-pass review outcomes enter explicit barriers; pass
        outcomes remove the terminal child run from the active child set so the
        driver can proceed to later completion gates without re-reviewing it.

        Args:
            record: Current drive record.
            child_run: Terminal step child run being reviewed.
            review_result: Bounded review outcome.
            authoritative_completion: Whether core completion succeeded after
                accepted reconcile proof.

        Returns:
            Updated drive record reflecting the review outcome.
        """
        active_child_run_ids = record.active_child_run_ids
        if authoritative_completion or review_result.status != "pass":
            active_child_run_ids = tuple(
                run_id for run_id in record.active_child_run_ids if run_id != child_run.run_id
            )
        now = time.time()
        if review_result.status == "pass":
            if authoritative_completion:
                summary = (
                    f"post-execution review passed and step completed for "
                    f"step={child_run.step_id}: {review_result.summary}"
                )
            else:
                summary = (
                    f"post-execution review passed for step={child_run.step_id}; "
                    f"waiting for reconcile disposition: {review_result.summary}"
                )
            return replace(
                record,
                updated_at=now,
                active_child_run_ids=active_child_run_ids,
                summary=summary,
            )

        barrier_reason: BarrierReason = "review_failed"
        new_status: DriveStatus = "resolving"
        if review_result.status == "needs_replan":
            new_status = "replanning"
        elif review_result.status == "operator_required":
            new_status = "blocked_operator"

        case_id = f"review_{child_run.run_id}"
        barrier = DriveBarrier(
            reason=barrier_reason,
            entered_at=now,
            case_ids=(case_id,),
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=active_child_run_ids,
        )
        return replace(
            record,
            status=new_status,
            updated_at=now,
            active_child_run_ids=active_child_run_ids,
            blocked_case_ids=(case_id,),
            barrier=barrier,
            summary=(
                f"post-execution review {review_result.status} for "
                f"step={child_run.step_id}: {review_result.summary}"
            ),
        )

    # ------------------------------------------------------------------
    # Resolver-loop integration
    # ------------------------------------------------------------------

    def _handle_resolve_decision(
        self,
        drive_id: str,
        record: DriveRecord,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        decision: ControlDecision,
    ) -> DriveLoopResult:
        """Handle a resolve decision by invoking resolver and continuing.

        Authority: RFC-orch-drive.md section 12.3
        Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 5, 6

        This method:
          1. Enters barrier (running → resolving) if not already in barrier
          2. Creates ResolutionCase snapshot from current state
          3. Invokes resolver with the case
          4. Consumes the ResolutionReport
          5. Refreshes authoritative state (mandatory per RESOLUTION-CONTRACT §6)
          6. Applies the report through control.apply_resolution()
          7. Transitions drive status per continuation rules
          8. Preserves evidence/artifacts on the case and report

        Args:
            drive_id: Drive identifier.
            record: Current drive record.
            core: Core snapshot at barrier entry.
            roster: Roster snapshot at barrier entry.
            runtime: Runtime snapshot at barrier entry.
            decision: ControlDecision with kind='resolve'.

        Returns:
            ``DriveLoopResult`` reflecting the post-resolution drive state.
        """
        assert self._resolver is not None  # guarded by caller

        # Step 1: Enter barrier if not already in one.
        now = time.time()
        if record.barrier is None:
            barrier = DriveBarrier(
                reason=_barrier_reason_for_case_source(_infer_case_source(decision)),
                entered_at=now,
                case_ids=decision.case_ids,
                pending_resolver_run_id=None,
                pending_planner_run_id=None,
                active_child_run_ids_at_entry=record.active_child_run_ids,
            )
            validate_drive_transition(record.status, "resolving")
            # Create an intermediate record reflecting the resolving state
            # for correct transition validation in _apply_resolution_report_to_drive.
            resolving_record = _update_drive_record(
                record=record,
                new_status="resolving",
                barrier=barrier,
                summary="entering resolver",
            )
        else:
            barrier = record.barrier
            resolving_record = record

        # Step 2: Create ResolutionCase snapshot.
        # Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 3.2
        # "The case should be a snapshot of current known facts, not a
        #  speculative action plan."
        case = ResolutionCase(
            case_id=decision.case_ids[0] if decision.case_ids else "",
            case_source=_infer_case_source(decision),
            reason=decision.reason,
            summary=None,
            core=core,
            roster=roster,
            runtime=runtime,
            drive=record,
            blocked_step_ids=tuple(sid for sid in core.blocked_step_ids),
            artifact_refs=(),
        )

        # Step 3: Invoke resolver.
        # Authority: ORCHESTRATION-PLANE-INTERFACES.md section 4.4
        report = self._resolver.resolve(case)

        # Step 4: Refresh authoritative state (mandatory).
        # Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 6
        # "After receiving a ResolutionReport, control must:
        #  1. re-read the relevant authoritative and orchestration-plane state
        #  2. not trust stale pre-resolution assumptions
        #  3. resume normal evaluate() logic from refreshed state"
        refreshed_core = self._core_adapter.snapshot(agent=resolving_record.agent or "default")
        refreshed_roster = self._control.sources.roster.snapshot()
        refreshed_runtime = self._control.sources.runtime.snapshot()

        # Step 5: Apply resolution report through control.
        post_decision = self._control.apply_resolution(
            report=report,
            core=refreshed_core,
            roster=refreshed_roster,
            runtime=refreshed_runtime,
            drive=resolving_record,
            barrier=barrier,
        )

        # Step 6: Transition drive status per continuation rules.
        # Authority: RFC-orch-drive.md section 12.3
        new_status, resolved_barrier, summary = _apply_resolution_report_to_drive(
            record=resolving_record,
            barrier=barrier,
            report=report,
            post_decision=post_decision,
        )

        updated = _update_drive_record(
            record=resolving_record,
            new_status=new_status,
            barrier=resolved_barrier,
            summary=summary,
        )
        if resolved_barrier is None:
            updated = replace(updated, blocked_case_ids=())
        self._drive_store.save_drive(updated)

        return DriveLoopResult(
            drive_id=drive_id,
            status=new_status,
            completed_steps=(),
            active_child_run_ids=record.active_child_run_ids,
            barrier=resolved_barrier,
            summary=summary,
        )

    def _enter_barrier_for_resolve(
        self,
        record: DriveRecord,
        decision: ControlDecision,
    ) -> tuple[DriveStatus, DriveBarrier | None, str]:
        """Enter barrier for resolve decision without resolver available.

        Authority: RFC-orch-drive.md section 5.4, 8.4.1

        Args:
            record: Current drive record.
            decision: ControlDecision with kind='resolve'.

        Returns:
            Tuple of (new_status, barrier, summary).
        """
        now = time.time()
        barrier_reason = _barrier_reason_for_case_source(_infer_case_source(decision))
        barrier = DriveBarrier(
            reason=barrier_reason,
            entered_at=now,
            case_ids=decision.case_ids,
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=record.active_child_run_ids,
        )
        new_status: DriveStatus = "resolving"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            # If already in resolving or another barrier-compatible state,
            # keep the current status but still update the barrier.
            new_status = record.status
        summary = f"resolve: case_ids={decision.case_ids} (barrier entered; no resolver wired)"
        return new_status, barrier, summary

    def _enter_barrier_for_replan(
        self,
        record: DriveRecord,
        decision: ControlDecision,
    ) -> tuple[DriveStatus, DriveBarrier | None, str]:
        """Enter barrier for replan decision.

        Authority: RFC-orch-drive.md section 5.6, 13

        Args:
            record: Current drive record.
            decision: ControlDecision with kind='replan'.

        Returns:
            Tuple of (new_status, barrier, summary).
        """
        now = time.time()
        barrier = DriveBarrier(
            reason="planner_needed",
            entered_at=now,
            case_ids=decision.case_ids,
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=record.active_child_run_ids,
        )
        new_status: DriveStatus = "replanning"
        try:
            validate_drive_transition(record.status, new_status)
        except InvalidDriveTransitionError:
            new_status = record.status
        summary = f"replan: {decision.reason} (barrier entered)"
        return new_status, barrier, summary

    # ------------------------------------------------------------------
    # Drive-scoped control consumption
    # ------------------------------------------------------------------

    def _consume_drive_control(self, record: DriveRecord) -> DriveRecord:
        """Consume queued drive-scoped control messages and apply state transitions.

        Authority: docs/RFC-orch-drive.md section 7.3, 10
        Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4
            'control requests must be consumed, not only queued'

        This method reads pending ``drive.pause``, ``drive.unpause``, and
        ``drive.stop`` messages from the control channel (if wired), applies
        the corresponding state transitions to the DriveRecord, and
        acknowledges each consumed message.

        Processing order:
            1. ``drive.stop`` — terminal; transitions to ``stopped`` immediately.
            2. ``drive.pause`` — transitions to ``paused`` and sets
               ``operator_pause_state="paused"``.
            3. ``drive.unpause`` — clears pause, transitions to ``running``
               (or back to the pre-pause status) and sets
               ``operator_pause_state="active"``.

        Stop takes priority over pause/unpause. If multiple messages are
        queued, only the first applicable one is consumed per call (the
        driver loop calls this at the start of each iteration, so remaining
        messages are consumed on subsequent iterations).

        Args:
            record: Current drive record.

        Returns:
            Updated DriveRecord reflecting any consumed control message.
            If no control channel is wired or no pending messages exist,
            returns the record unchanged.
        """
        if self._control_channel is None:
            return record

        # Use drive_id as the run_id in the control channel, matching
        # send_drive_control's convention (drive_id is the first payload element).
        pending = self._control_channel.list_requests(
            record.drive_id, status="pending"
        )
        if not pending:
            return record

        # Authority: RFC-orch-drive.md section 7.3
        # "Drive-scoped control targets the drive rather than a single run."
        # Process the oldest pending request first (FIFO).
        # Stop has highest priority; pause and unpause follow.
        for request in pending:
            msg_type = request.msg_type
            action_id = request.action_id

            if msg_type == "drive.stop":
                # Authority: RFC-orch-drive.md section 7.3.1
                # Stop is terminal — no further transitions valid after this.
                force = "force=true" in request.payload
                cancelled_child_ids: list[str] = []
                if force:
                    for child_run_id in record.active_child_run_ids:
                        if self._child_run_canceller is not None:
                            self._child_run_canceller(child_run_id)
                        child_ref = self._drive_store.child_run_by_id(child_run_id)
                        if child_ref is not None and child_ref.status in ("pending", "running"):
                            self._drive_store.save_child_run(
                                replace(child_ref, status="cancelled")
                            )
                        cancelled_child_ids.append(child_run_id)

                new_status: DriveStatus = "stopped"
                try:
                    validate_drive_transition(record.status, new_status)
                except InvalidDriveTransitionError:
                    # If already terminal or transition not valid, keep current.
                    new_status = record.status

                now = time.time()
                finished_at = now if new_status in TERMINAL_DRIVE_STATUSES else None
                updated = DriveRecord(
                    drive_id=record.drive_id,
                    plan_path=record.plan_path,
                    status=new_status,
                    started_at=record.started_at,
                    updated_at=now,
                    finished_at=finished_at,
                    agent=record.agent,
                    max_parallelism=record.max_parallelism,
                    active_child_run_ids=(
                        () if force else record.active_child_run_ids
                    ),
                    frontier_step_ids=record.frontier_step_ids,
                    blocked_case_ids=record.blocked_case_ids,
                    barrier=record.barrier,
                    operator_pause_state=record.operator_pause_state,
                    summary=(
                        f"operator stop consumed"
                        f"{' (force)' if force else ''}: "
                        f"transition {record.status} → {new_status}"
                        + (
                            f"; cancelled_child_runs={tuple(cancelled_child_ids)}"
                            if cancelled_child_ids
                            else ""
                        )
                    ),
                )
                self._drive_store.save_drive(updated)
                self._control_channel.acknowledge_applied(
                    run_id=record.drive_id, action_id=action_id
                )
                return updated

            if msg_type == "drive.pause":
                # Authority: RFC-orch-drive.md section 7.3, 16
                # Pause sets operator_pause_state="paused" and transitions
                # drive status to "paused" per the transition table.
                new_status_pause: DriveStatus = "paused"
                try:
                    validate_drive_transition(record.status, new_status_pause)
                except InvalidDriveTransitionError:
                    # If already in a state that cannot transition to paused
                    # (e.g. already in a barrier), still update
                    # operator_pause_state so the next eligible transition
                    # recognizes the pause.
                    new_status_pause = record.status

                now = time.time()
                updated = DriveRecord(
                    drive_id=record.drive_id,
                    plan_path=record.plan_path,
                    status=new_status_pause,
                    started_at=record.started_at,
                    updated_at=now,
                    finished_at=None,
                    agent=record.agent,
                    max_parallelism=record.max_parallelism,
                    active_child_run_ids=record.active_child_run_ids,
                    frontier_step_ids=record.frontier_step_ids,
                    blocked_case_ids=record.blocked_case_ids,
                    barrier=record.barrier,
                    operator_pause_state="paused",
                    summary=(
                        f"operator pause consumed: "
                        f"transition {record.status} → {new_status_pause}, "
                        f"operator_pause_state=paused"
                    ),
                )
                self._drive_store.save_drive(updated)
                self._control_channel.acknowledge_applied(
                    run_id=record.drive_id, action_id=action_id
                )
                return updated

            if msg_type == "drive.unpause":
                # Authority: RFC-orch-drive.md section 7.3, 16
                # Unpause clears the operator pause state and transitions
                # back to running (or the appropriate pre-pause status).
                # If a barrier was active before the pause, the drive should
                # return to the barrier state, not running.
                # However, the unpause itself just clears the pause flag;
                # the next control evaluation picks up the correct status.
                pre_pause_status: DriveStatus = "running"
                # If there was a barrier before pause, resume into the
                # barrier-mapped status.
                if record.barrier is not None:
                    pre_pause_status = _barrier_status(record.barrier.reason)

                try:
                    validate_drive_transition(record.status, pre_pause_status)
                except InvalidDriveTransitionError:
                    # Keep current status if transition is invalid, but
                    # still clear the operator_pause_state.
                    pre_pause_status = record.status

                now = time.time()
                updated = DriveRecord(
                    drive_id=record.drive_id,
                    plan_path=record.plan_path,
                    status=pre_pause_status,
                    started_at=record.started_at,
                    updated_at=now,
                    finished_at=None,
                    agent=record.agent,
                    max_parallelism=record.max_parallelism,
                    active_child_run_ids=record.active_child_run_ids,
                    frontier_step_ids=record.frontier_step_ids,
                    blocked_case_ids=record.blocked_case_ids,
                    barrier=record.barrier,
                    operator_pause_state="active",
                    summary=(
                        f"operator unpause consumed: "
                        f"transition {record.status} → {pre_pause_status}, "
                        f"operator_pause_state=active"
                    ),
                )
                self._drive_store.save_drive(updated)
                self._control_channel.acknowledge_applied(
                    run_id=record.drive_id, action_id=action_id
                )
                return updated

            # Unknown drive-scoped message type — skip it.
            # (It may be a run-scoped message that was routed to this
            #  drive's queue; acknowledge as rejected.)
            self._control_channel.acknowledge_rejected(
                run_id=record.drive_id,
                action_id=action_id,
                reason=f"unknown drive-scoped msg_type: {msg_type}",
            )

        return record

    # ------------------------------------------------------------------
    # Planner-loop integration
    # ------------------------------------------------------------------

    def _handle_replan_decision(
        self,
        drive_id: str,
        record: DriveRecord,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        decision: ControlDecision,
    ) -> DriveLoopResult:
        """Handle a replan decision by entering barrier, applying planner mutations.

        Authority: RFC-orch-drive.md section 13, 5.6
        Authority: ORCHESTRATION-PLANE-INTERFACES.md section 4.1 (apply_planner_result)

        This method implements the planner continuation loop:
          1. Enter replan barrier (running → replanning) if not already in barrier
          2. Construct a PlannerMutationBundle from the planner_request
          3. Apply the bundle through vectl facade only
          4. Invalidate superseded unstarted leases
          5. Refresh authoritative state (mandatory after mutation)
          6. Apply the bundle result through control.apply_planner_result()
          7. Transition drive status per continuation rules
          8. Persist updated drive state

        The planner NEVER edits plan.yaml directly. All mutations flow
        through the approved vectl facade surface.

        Args:
            drive_id: Drive identifier.
            record: Current drive record.
            core: Core snapshot at barrier entry.
            roster: Roster snapshot at barrier entry.
            runtime: Runtime snapshot at barrier entry.
            decision: ControlDecision with kind='replan'.

        Returns:
            ``DriveLoopResult`` reflecting the post-planner drive state.
        """
        assert self._planner_mutation_applier is not None  # guarded by caller

        # Step 1: Enter replan barrier if not already in one.
        now = time.time()
        if record.barrier is None or record.barrier.reason != "planner_needed":
            barrier = DriveBarrier(
                reason="planner_needed",
                entered_at=now,
                case_ids=decision.case_ids,
                pending_resolver_run_id=None,
                pending_planner_run_id=None,
                active_child_run_ids_at_entry=record.active_child_run_ids,
            )
            validate_drive_transition(record.status, "replanning")
            replanning_record = _update_drive_record(
                record=record,
                new_status="replanning",
                barrier=barrier,
                summary="entering replan barrier",
            )
        else:
            barrier = record.barrier
            replanning_record = record

        # Step 2: Construct PlannerMutationBundle from the planner_request.
        # Authority: RFC-orch-drive.md section 13.2, 13.3
        planner_request = decision.planner_request
        assert planner_request is not None  # replan invariant: planner_request present

        bundle = _construct_bundle_from_request(planner_request)

        # Step 3: Apply the bundle through vectl facade only.
        # Authority: RFC-orch-drive.md section 13.4
        # "Direct plan.yaml edits remain forbidden."
        apply_result = self._planner_mutation_applier.apply_bundle(bundle)

        if apply_result.failed_count > 0:
            # Authority: RFC-orch-drive.md section 13.5
            # "ordinary facade apply failure enters blocked_operator"
            failed_desc = "; ".join(apply_result.failed_actions)
            new_status: DriveStatus = "blocked_operator"
            summary = (
                f"planner apply failed: {apply_result.failed_count}/{len(bundle.mutations)} "
                f"mutations failed: {failed_desc}"
            )
            updated = _update_drive_record(
                record=replanning_record,
                new_status=new_status,
                barrier=barrier,
                summary=summary,
            )
            self._drive_store.save_drive(updated)
            return DriveLoopResult(
                drive_id=drive_id,
                status=new_status,
                completed_steps=(),
                active_child_run_ids=record.active_child_run_ids,
                barrier=barrier,
                summary=summary,
            )

        # Step 4: Invalidate superseded unstarted leases.
        # Authority: RFC-orch-drive.md section 14.4
        invalidated_ids = self._invalidate_superseded_leases(
            drive_id=drive_id,
            affected_step_ids=apply_result.affected_step_ids,
        )

        # Step 5: Refresh authoritative state (mandatory).
        refreshed_core = self._core_adapter.snapshot(agent=replanning_record.agent or "default")
        refreshed_roster = self._control.sources.roster.snapshot()
        refreshed_runtime = self._control.sources.runtime.snapshot()

        # Step 6: Apply planner result through control.
        post_decision = self._control.apply_planner_result(
            bundle=bundle,
            core=refreshed_core,
            roster=refreshed_roster,
            runtime=refreshed_runtime,
            drive=replanning_record,
            barrier=barrier,
        )

        # Step 7: Transition drive status per continuation rules.
        new_status, resolved_barrier, summary = _apply_planner_bundle_to_drive(
            record=replanning_record,
            barrier=barrier,
            bundle=bundle,
            post_decision=post_decision,
            invalidated_lease_count=len(invalidated_ids),
        )

        updated = _update_drive_record(
            record=replanning_record,
            new_status=new_status,
            barrier=resolved_barrier,
            summary=summary,
        )
        self._drive_store.save_drive(updated)

        return DriveLoopResult(
            drive_id=drive_id,
            status=new_status,
            completed_steps=(),
            active_child_run_ids=record.active_child_run_ids,
            barrier=resolved_barrier,
            summary=summary,
        )

    def _invalidate_superseded_leases(
        self,
        drive_id: str,
        affected_step_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Invalidate leases for steps superseded by planner mutations.

        Authority: RFC-orch-drive.md section 14.4
        Authority: ORCHESTRATION-PLANE-INTERFACES.md section 5 (ownership matrix)

        When a planner mutation adds, removes, or edits steps, any existing
        active leases for affected steps that have not yet started (i.e. the
        child run is still pending) must be invalidated. This prevents the
        scheduler from dispatching work against a stale frontier.

        Only pending (unstarted) leases are invalidated; leases for running
        child runs are not touched (they are already in-flight).

        Args:
            drive_id: The drive whose leases to check.
            affected_step_ids: Step IDs affected by planner mutations.

        Returns:
            Tuple of invalidated lease run IDs.
        """
        if not affected_step_ids:
            return ()

        invalidated: list[str] = []
        for step_id in affected_step_ids:
            released = self._drive_store.invalidate_leases_for_step(
                drive_id=drive_id,
                step_id=step_id,
                reason="superseded",
            )
            invalidated.extend(lease.run_id for lease in released)

        return tuple(invalidated)

    # ------------------------------------------------------------------
    # resume_drive
    # ------------------------------------------------------------------

    def resume_drive(self, drive_id: str) -> DriveResumeResult:
        """Resume an interrupted drive session.

        Authority: docs/RFC-orch-drive.md section 15.1

        Resume restores:
            - active child run set (rebuilt from persistent child-run index)
            - barrier state (preserved from persisted drive record)
            - operator pause state (preserved from persisted drive record)
            - resolver/planner pending state (carried via barrier fields)
            - aggregate progress summary (recomputed)

        Truthful continuation rules (RFC-orch-drive.md section 15.3.1):
            - Live runtime facts beat stale persisted convenience when
              persisted child runs claim running but no live process exists.
            - Durable terminal artifacts beat stale in-memory running state.
            - Frontier is recomputed from live core authority rather than
              trusted from the persisted cache.

        Args:
            drive_id: The drive to resume.

        Returns:
            ``DriveResumeResult`` with restored state.

        Raises:
            DriveStoreError: If no DriveRecord exists for the drive_id.
        """
        from vectl.orchestration.run_store import DriveStoreError

        record = self._drive_store.replay_drive_state(drive_id)
        if record is None:
            raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

        # If terminal, resume is not meaningful — return current state.
        if record.status in TERMINAL_DRIVE_STATUSES:
            return DriveResumeResult(
                drive_id=drive_id,
                status=record.status,
                restored_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary=f"drive is terminal: {record.status}; nothing to resume",
            )

        # Rebuild active child run set from persistent child-run index.
        # Authority: RFC-orch-drive.md section 15.1
        # "Resume MUST restore: active child run set"
        active_runs = self._drive_store.active_child_runs_for_drive(drive_id)
        restored_ids = tuple(ref.run_id for ref in active_runs)
        barrier = record.barrier

        # Determine the target status based on persisted barrier and operator
        # pause state.  Resume must NOT unconditionally transition to
        # "running" — it must restore the correct status.
        # Authority: RFC-orch-drive.md section 15.1
        # "resolver/planner pending state" is carried via barrier fields;
        # "operator pause state" is carried on the drive record.
        resume_status: DriveStatus
        if record.operator_pause_state == "paused":
            resume_status = "paused"
        elif barrier is not None:
            # Re-enter the appropriate barrier state based on reason.
            # Authority: RFC-orch-drive.md section 15.3.1
            # "persisted barrier absent, open case exists → open case state"
            resume_status = _barrier_status(barrier.reason)
        else:
            resume_status = "running"

        # Validate the transition from current status to target status.
        # Authority: RFC-orch-drive.md section 8.2.1
        if record.status != resume_status:
            try:
                validate_drive_transition(record.status, resume_status)
            except InvalidDriveTransitionError:
                # If the direct transition is not valid, try via "recovering"
                # as an intermediate (RFC 8.2.1: recovering is the designated
                # transitional status for state restoration).
                try:
                    validate_drive_transition(record.status, "recovering")
                    validate_drive_transition("recovering", resume_status)
                except InvalidDriveTransitionError:
                    # Last resort: if no valid path exists, keep current
                    # status.  This preserves safety over forcing an invalid
                    # transition.
                    resume_status = record.status

        # Recompute frontier from live core authority.
        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted drive frontier includes step, core no longer claimable
        #  → core authority wins"
        frontier_step_ids = self._recompute_frontier(record)

        # Persist operator pause state from the record, not hard-code "active".
        # Authority: RFC-orch-drive.md section 15.1
        # "operator pause state" is a top-level drive field that must be
        # preserved across resume.
        operator_pause_state = record.operator_pause_state

        now = time.time()
        updated = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=resume_status,
            started_at=record.started_at,
            updated_at=now,
            finished_at=None,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=restored_ids,
            frontier_step_ids=frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=barrier,
            operator_pause_state=operator_pause_state,
            summary=(
                f"drive resumed; status={resume_status}; "
                f"{len(restored_ids)} active child runs restored"
            ),
        )
        self._drive_store.save_drive(updated)

        return DriveResumeResult(
            drive_id=drive_id,
            status=resume_status,
            restored_child_run_ids=restored_ids,
            barrier=barrier,
            summary=(
                f"drive resumed; status={resume_status}; "
                f"{len(restored_ids)} active child runs restored"
            ),
        )

    # ------------------------------------------------------------------
    # recover_drive
    # ------------------------------------------------------------------

    def recover_drive(
        self,
        drive_id: str,
        *,
        dry_run: bool = False,
    ) -> DriveRecoverResult:
        """Recover a drive from interrupted state.

        Authority: docs/RFC-orch-drive.md section 15.2, 15.3

        Recovery prefers truthful continuation:
            1. Restore persisted drive state
            2. Restore child-run facts
            3. Reconcile live/runtime facts to persisted records
            4. Re-enter barrier if needed
            5. Only reopen scheduling after state is coherent

        Conflict resolution follows RFC-orch-drive.md section 15.3.1:
            - persisted child run=running, live process missing, no terminal
              artifact → live runtime fact wins; classify as stale
            - persisted child run=running, live terminal artifact exists →
              committed-but-unobserved completion beats stale in-memory status
            - persisted step lease exists, core marks step done → core
              authority wins
            - persisted drive frontier includes step, core no longer claimable
              → core authority wins; frontier recomputed
            - persisted barrier absent, open case exists → open case state
              wins; enter barrier

        Args:
            drive_id: The drive to recover.
            dry_run: If True, compute recovery plan without applying changes.

        Returns:
            ``DriveRecoverResult`` with recovery outcomes.

        Raises:
            DriveStoreError: If no DriveRecord exists for the drive_id.
        """
        from vectl.orchestration.run_store import DriveStoreError

        record = self._drive_store.replay_drive_state(drive_id)
        if record is None:
            raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

        # Terminal drives don't need recovery.
        if record.status in TERMINAL_DRIVE_STATUSES:
            return DriveRecoverResult(
                drive_id=drive_id,
                status=record.status,
                recovered_child_run_ids=(),
                failed_child_run_ids=(),
                conflict_resolutions=(f"drive is terminal: {record.status}; nothing to recover",),
                barrier=record.barrier,
                summary=f"drive is terminal: {record.status}; nothing to recover",
            )

        # Step 1: Restore persisted drive state.
        # (The `record` variable already holds the replayed drive state.)

        # Step 2: Restore child-run facts and reconcile.
        # Authority: RFC-orch-drive.md section 15.3.1
        all_child_runs = self._drive_store.child_runs_for_drive(drive_id)
        active_runs = self._drive_store.active_child_runs_for_drive(drive_id)

        recovered_ids: list[str] = []
        failed_ids: list[str] = []
        conflict_notes: list[str] = []

        for ref in all_child_runs:
            if ref.status in ("pending", "running"):
                # Step 3: Reconcile live/runtime facts to persisted records.
                # For each persisted non-terminal child run, check if a
                # durable terminal artifact exists in the run store —
                # this is the "committed-but-unobserved completion" path.
                #
                # Authority: RFC-orch-drive.md section 15.3.1
                # "persisted child run=running, live terminal artifact exists
                #  → persisted + terminal artifact"
                terminal_artifact = self._find_terminal_artifact(ref.run_id)
                if terminal_artifact is not None:
                    # Durable terminal artifact beats stale in-memory status.
                    conflict_notes.append(
                        f"child run {ref.run_id}: persisted status={ref.status} but "
                        f"durable terminal artifact found ({terminal_artifact}); "
                        f"classifying as completed fact"
                    )
                    # This child run is a completed fact; it should not be in
                    # the active set. We do NOT add it to recovered_ids.
                    continue

                # No durable terminal artifact.  Check liveness.
                # Authority: RFC-orch-drive.md section 15.3.1
                # "persisted child run=running, live process missing, no
                #  terminal artifact → live runtime fact"
                liveness = self._check_child_run_liveness(ref.run_id)
                if liveness == "stale":
                    # Stale persisted running state with no live process.
                    # The process is gone; persisted running state is stale.
                    # Classify as failed rather than recovered.
                    conflict_notes.append(
                        f"child run {ref.run_id}: persisted status={ref.status} but "
                        f"live process missing and no terminal artifact; "
                        f"classifying as stale (failed)"
                    )
                    failed_ids.append(ref.run_id)
                    continue

                if liveness == "unknown" and ref.status == "pending":
                    # Pending with unknown liveness — may still be queued.
                    # Recovery-conservative: include in recovered set.
                    recovered_ids.append(ref.run_id)
                    continue

                # Liveness is "alive" or unknown-but-running: assume recoverable.
                recovered_ids.append(ref.run_id)
            else:
                # Terminal entries are completed facts; not recovered.
                pass

        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted active child set differs from live child set →
        #  union, then normalize by liveness checks"
        # We use the union of persisted active runs and freshly recovered
        # runs, excluding failed/stale ones.
        recovered_active_ids = tuple(
            dict.fromkeys(
                list(ref.run_id for ref in active_runs if ref.run_id not in failed_ids)
                + [rid for rid in recovered_ids if rid not in {r.run_id for r in active_runs}]
            )
        )

        # Step 4: Re-enter barrier if persisted state requires it.
        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted barrier absent, open case exists → open case state"
        barrier = record.barrier
        if barrier is None and record.blocked_case_ids:
            # No persisted barrier but open cases exist — safety requires
            # entering barrier.
            conflict_notes.append(
                f"no persisted barrier but {len(record.blocked_case_ids)} open case(s) "
                f"require barrier re-entry"
            )
            barrier = DriveBarrier(
                reason="recovery_gate",
                entered_at=time.time(),
                case_ids=record.blocked_case_ids,
                pending_resolver_run_id=None,
                pending_planner_run_id=None,
                active_child_run_ids_at_entry=recovered_active_ids,
            )

        # Determine recovery target status.
        # Authority: RFC-orch-drive.md section 8.2.1 (transition table)
        # Authority: RFC-orch-drive.md section 15.3
        #   - If no barrier remains: recovering → running
        #   - If barrier persists: recovering → resolving or replanning
        if record.operator_pause_state == "paused":
            new_status: DriveStatus = "blocked_operator"
        elif barrier is not None:
            new_status = _barrier_status(barrier.reason)
        else:
            new_status = "running"

        # Validate the transition through "recovering" if needed.
        # Authority: RFC-orch-drive.md section 8.2.1
        # All non-terminal statuses can transition to "recovering".
        if record.status != new_status:
            try:
                # Try direct transition first.
                validate_drive_transition(record.status, new_status)
            except InvalidDriveTransitionError:
                try:
                    # Try via recovering intermediate.
                    validate_drive_transition(record.status, "recovering")
                    validate_drive_transition("recovering", new_status)
                except InvalidDriveTransitionError:
                    # Last resort: keep current status.
                    new_status = record.status

        # Recompute frontier from live core authority.
        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted drive frontier includes step, core no longer
        #  claimable → core authority wins"
        frontier_step_ids = self._recompute_frontier(record)

        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted step lease exists, core marks step done → core authority"
        # Any active leases for steps now marked done by core should be
        # released. We record this as a conflict note.
        released_lease_steps = self._release_superseded_leases(drive_id)
        if released_lease_steps:
            conflict_notes.append(
                f"released {len(released_lease_steps)} lease(s) for core-done steps: "
                f"{', '.join(released_lease_steps)}"
            )

        # Preserve operator pause state from record.
        # Authority: RFC-orch-drive.md section 15.1
        operator_pause_state = record.operator_pause_state

        if dry_run:
            return DriveRecoverResult(
                drive_id=drive_id,
                status=record.status,  # keep current status in dry run
                recovered_child_run_ids=tuple(recovered_ids),
                failed_child_run_ids=tuple(failed_ids),
                conflict_resolutions=tuple(conflict_notes) or ("dry-run: no changes applied",),
                barrier=barrier,
                summary=(
                    f"dry-run: would recover {len(recovered_ids)} child runs, "
                    f"fail {len(failed_ids)} stale runs, "
                    f"transition to {new_status}"
                ),
            )

        # Apply recovery: persist the updated drive state.
        now = time.time()
        updated = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=new_status,
            started_at=record.started_at,
            updated_at=now,
            finished_at=None,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=recovered_active_ids,
            frontier_step_ids=frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=barrier,
            operator_pause_state=operator_pause_state,
            summary=(
                f"drive recovered; {len(recovered_ids)} child runs restored; "
                f"{len(failed_ids)} stale runs classified as failed; "
                f"status={new_status}"
            ),
        )
        self._drive_store.save_drive(updated)

        return DriveRecoverResult(
            drive_id=drive_id,
            status=new_status,
            recovered_child_run_ids=tuple(recovered_ids),
            failed_child_run_ids=tuple(failed_ids),
            conflict_resolutions=tuple(conflict_notes),
            barrier=barrier,
            summary=(
                f"drive recovered; {len(recovered_ids)} child runs restored; "
                f"{len(failed_ids)} stale runs classified as failed; "
                f"status={new_status}"
            ),
        )

    # ------------------------------------------------------------------
    # Recovery helper methods
    # ------------------------------------------------------------------

    def _recompute_frontier(self, record: DriveRecord) -> tuple[str, ...]:
        """Recompute frontier step IDs from live core authority.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted drive frontier includes step, core no longer claimable
        → core authority wins"

        Falls back to the persisted frontier if core snapshot is unavailable
        or empty.

        Args:
            record: The current drive record with persisted frontier.

        Returns:
            Tuple of frontier step IDs derived from live core authority.
        """
        try:
            core = self._core_adapter.snapshot(agent=record.agent or "default")
            live_frontier = core.claimable_step_ids
            if live_frontier:
                # Authority: RFC-orch-drive.md section 15.3.1
                # Core authority beats cached frontier.
                return live_frontier
        except Exception:
            # Core adapter failure during recompute: fall through to
            # persisted frontier.  This is conservative — we don't discard
            # the persisted frontier just because core is unreachable.
            pass
        return record.frontier_step_ids

    def _find_terminal_artifact(self, run_id: str) -> str | None:
        """Check whether a durable terminal artifact exists for a child run.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted child run=running, live terminal artifact exists
        → persisted + terminal artifact"

        Uses the DriveStore's child run index and, if available, the
        RunRegistry for durable terminal evidence.

        Args:
            run_id: The child run identifier to check.

        Returns:
            A string description of the terminal artifact, or None.
        """
        # First, check the child-run index for terminal status.
        # If the child-run ref shows a terminal status, that's durable
        # evidence of completion regardless of whether a live process exists.
        child_ref = self._drive_store.child_run_by_id(run_id)
        if child_ref is not None and child_ref.status in (
            "success",
            "fail",
            "cancelled",
        ):
            return f"child_run status={child_ref.status}"

        # Check via RunRegistry if we have one available.
        # Terminal run records in the registry serve as durable proof.
        if self._run_registry is not None:
            run_record = self._run_registry.by_id(run_id)
            if run_record is not None and run_record.status in ("success", "fail"):
                return f"run_record status={run_record.status}"

        return None

    def _check_child_run_liveness(
        self, run_id: str, *, stale_threshold_seconds: float = 90.0
    ) -> str:
        """Check liveness of a child run using heartbeat artifacts.

        Authority: RFC-orch-drive.md section 10.4
        Default heartbeat stale threshold is 90 seconds.

        Args:
            run_id: The child run identifier to check.
            stale_threshold_seconds: Maximum heartbeat age before stale.

        Returns:
            "alive" if the run heartbeat is recent,
            "stale" if heartbeat is old or missing,
            "unknown" if no heartbeat artifact exists.
        """
        if self._run_registry is None:
            return "unknown"

        try:
            liveness = self._run_registry.liveness_for_run(
                run_id,
                stale_after_seconds=stale_threshold_seconds,
            )
            if liveness == "alive":
                return "alive"
            if liveness == "stale":
                return "stale"
        except Exception:
            pass
        return "unknown"

    def _release_superseded_leases(self, drive_id: str) -> list[str]:
        """Release active leases for steps that core authority marks as done.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted step lease exists, core marks step done → core authority"

        Uses core snapshot to check which steps are no longer claimable
        because they've been completed, and releases their active leases.

        Args:
            drive_id: The drive ID whose leases to check.

        Returns:
            List of step IDs whose leases were released.
        """
        try:
            record = self._drive_store.replay_drive_state(drive_id)
            if record is None:
                return []
            core = self._core_adapter.snapshot(agent=record.agent or "default")
            claimable = set(core.claimable_step_ids)
            active_leases = self._drive_store.active_leases_for_drive(drive_id)
            released_steps: list[str] = []
            for lease in active_leases:
                # If the step is not claimable and not in-progress, it's
                # been completed by core authority. Release the lease.
                if (
                    lease.step_id not in claimable
                    and lease.step_id not in core.in_progress_step_ids
                ):
                    self._drive_store.invalidate_lease(lease.run_id, reason="superseded")
                    released_steps.append(lease.step_id)
            return released_steps
        except Exception:
            # Core adapter failure: don't release leases speculatively.
            return []

__all__ = [
    "DRIVE_DEFAULTS",
    "DRIVE_TRANSITIONS",
    "DriveAdmissionError",
    "DriveDriver",
    "DriveLoopResult",
    "DriveRecoverResult",
    "DriveResumeResult",
    "DriveStartResult",
    "DriveStatusResult",
    "InvalidDriveTransitionError",
    "MaxParallelismError",
    "TERMINAL_DRIVE_STATUSES",
    "validate_drive_transition",
]
