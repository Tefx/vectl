"""
Drive-level orchestration loop and barrier management.

Authority: docs/RFC-orch-drive.md sections 10, 14, 15, 17
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.11

This module provides:
  - result types for the four drive-surface methods
  - the DriveDriver Protocol (contract interface)
  - ConcreteDriveDriver (wired implementation using existing orchestration
    building blocks: DriveStore, PlanAwareControl, core adapter)
  - drive status transition validation
  - drive admission and parallelism validation
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from vectl.orchestration.contracts import (
    BarrierReason,
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    PlannerMutationBundle,
    PlannerRequest,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    from vectl.orchestration.control import PlanAwareControl
    from vectl.orchestration.core_adapter import CoreAdapter, PlannerMutationApplier
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.review_gate import ReviewGate
    from vectl.orchestration.run_store import DriveStore


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
        barrier: Current barrier, if active.
        summary: Human-readable aggregate progress description.
    """

    drive_id: str
    status: DriveStatus
    scope_kind: str = "drive"
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
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
# Drive Driver Protocol
# ---------------------------------------------------------------------


class DriveDriver(Protocol):
    """Authoritative public API for drive-level orchestration.

    Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15, 17
    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4, 5

    The driver owns:
        - drive session creation and admission
        - the main scheduling loop (run_drive_loop)
        - barrier entry, stabilization, and resolution
        - phase/plan auto-close
        - drive-level recovery and resume

    The driver does NOT own:
        - plan graph authority (delegates to vectl core)
        - individual runner mechanics (delegates to runtime)
        - resolver reasoning (delegates to resolver)
        - control decisions (delegates to control)

    Implementation note:
        Methods are contract-only. Loop behavior and runtime integration
        are deferred to implementation steps.
    """

    def start_drive(
        self,
        *,
        plan_path: str,
        agent: str = "",
        max_parallelism: int = 4,
    ) -> DriveStartResult:
        """Create or resolve a drive for the given plan.

        Authority: docs/RFC-orch-drive.md section 7.2, 14.1

        If an active drive already exists for the same plan identity
        (canonical realpath-resolved absolute path to ``plan.yaml``), this
        method MUST raise ``DriveAdmissionError`` with the existing
        ``active_drive_id``.

        ``max_parallelism`` constraints:
            - minimum: 1
            - default: 4
            - hard maximum: 32
            - invalid values must fail fast with ``MaxParallelismError``

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.
            agent: Agent role that owns this drive.
            max_parallelism: Maximum concurrent step child runs.

        Returns:
            ``DriveStartResult`` with the new or resolved drive state.

        Raises:
            DriveAdmissionError: If an active drive exists for this plan.
            MaxParallelismError: If ``max_parallelism`` is outside [1, 32].
        """
        ...  # contract-only

    def run_drive_loop(
        self,
        drive_id: str,
    ) -> DriveLoopResult:
        """Execute one drive scheduling loop pass.

        Authority: docs/RFC-orch-drive.md section 10

        The loop MUST:
            1. Refresh authoritative snapshots (core, roster, runtime, drive)
            2. Evaluate control decision
            3. If ``dispatch_batch``: admit child runs up to capacity
            4. Collect terminal child runs
            5. Route terminal outputs through reconcile/review gates
            6. If non-closure exists: enter barrier
            7. If barrier active: stabilize, resolve via resolver/planner/operator
            8. Auto-close completed phases
            9. If all phases complete: mark drive completed

        The loop MUST always run from the repository root for
        integration-context checks.

        Args:
            drive_id: The drive to run one loop pass for.

        Returns:
            ``DriveLoopResult`` capturing the terminal or paused state.
        """
        ...  # contract-only

    def resume_drive(
        self,
        drive_id: str,
    ) -> DriveResumeResult:
        """Resume an interrupted drive session.

        Authority: docs/RFC-orch-drive.md section 15.1

        Resume MUST restore:
            - active child run set
            - barrier state
            - operator pause state
            - resolver/planner pending state
            - aggregate progress summary

        Args:
            drive_id: The drive to resume.

        Returns:
            ``DriveResumeResult`` with restored state.
        """
        ...  # contract-only

    def recover_drive(
        self,
        drive_id: str,
        *,
        dry_run: bool = False,
    ) -> DriveRecoverResult:
        """Recover a drive from interrupted state.

        Authority: docs/RFC-orch-drive.md section 15.2, 15.3

        Recovery MUST prefer truthful continuation:
            1. Restore persisted drive state
            2. Restore child-run facts
            3. Reconcile live/runtime facts to persisted records
            4. Re-enter barrier if needed
            5. Only reopen scheduling after state is coherent

        Conflict resolution policy follows RFC-orch-drive.md section 15.3.1.

        Args:
            drive_id: The drive to recover.
            dry_run: If True, compute recovery plan without applying changes.

        Returns:
            ``DriveRecoverResult`` with recovery outcomes.
        """
        ...  # contract-only


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
# Concrete Drive Driver
# ---------------------------------------------------------------------


class ConcreteDriveDriver:
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
        review_gate: ReviewGate | None = None,
        planner_mutation_applier: PlannerMutationApplier | None = None,
        max_parallelism: int = 4,
    ) -> None:
        self._drive_store = drive_store
        self._core_adapter = core_adapter
        self._control = control
        self._resolver = resolver
        self._review_gate = review_gate
        self._planner_mutation_applier = planner_mutation_applier
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

        # Refresh authoritative snapshot.
        core = self._core_adapter.snapshot(agent=record.agent or "default")

        # Evaluate control decision with drive context.
        drive_barrier = record.barrier
        open_case_ids = record.blocked_case_ids
        roster_snap = self._control.sources.roster.snapshot()
        runtime_snap = self._control.sources.runtime.snapshot()

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
        now = time.time()
        completed_steps: tuple[str, ...] = ()
        new_status = record.status
        barrier = record.barrier
        summary = f"loop pass: decision={decision.kind}"

        if decision.kind == "dispatch_batch":
            summary = (
                f"dispatch_batch: steps={decision.step_ids} "
                f"capacity_used={decision.capacity_used} "
                f"capacity_remaining={decision.capacity_remaining}"
            )
        elif decision.kind == "dispatch":
            summary = f"dispatch: step={decision.step_ids}"
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
        self._drive_store.save_drive(updated)

        return DriveLoopResult(
            drive_id=drive_id,
            status=new_status,
            completed_steps=completed_steps,
            active_child_run_ids=record.active_child_run_ids,
            barrier=barrier,
            summary=summary,
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
            - active child run set
            - barrier state
            - operator pause state
            - resolver/planner pending state
            - aggregate progress summary

        The actual resumption of individual child runs is handled by the
        orchestration app layer. This method reconstructs the authoritative
        drive state from persistence and validates transition legality.

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

        # Validate that we can transition from current status to running.
        current_status = record.status
        if current_status != "running":
            try:
                validate_drive_transition(current_status, "running")
            except InvalidDriveTransitionError:
                # Some statuses can only resume to their current status or
                # specific targets. For pause/resolve/etc, we try the
                # unpause transition.
                pass

        # Rebuild active child run set from persistent child-run index.
        active_runs = self._drive_store.active_child_runs_for_drive(drive_id)
        restored_ids = tuple(ref.run_id for ref in active_runs)
        barrier = record.barrier

        # Transition the drive back to running.
        now = time.time()
        updated = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status="running",
            started_at=record.started_at,
            updated_at=now,
            finished_at=None,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=restored_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=barrier,
            operator_pause_state="active",
            summary=f"drive resumed; {len(restored_ids)} active child runs restored",
        )
        self._drive_store.save_drive(updated)

        return DriveResumeResult(
            drive_id=drive_id,
            status="running",
            restored_child_run_ids=restored_ids,
            barrier=barrier,
            summary=f"drive resumed; {len(restored_ids)} active child runs restored",
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

        Conflict resolution follows RFC-orch-drive.md section 15.3.1.

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

        # Gather all child runs (both active and terminal).
        all_child_runs = self._drive_store.child_runs_for_drive(drive_id)
        active_runs = self._drive_store.active_child_runs_for_drive(drive_id)

        # Conflict resolution: identify potentially stale running entries.
        # Per RFC-orch-drive.md section 15.3.1:
        # - persisted child run=running, live process gone, no terminal artifact → stale
        # - persisted step lease exists but core marks step done → core authority wins
        recovered_ids: list[str] = []
        failed_ids: list[str] = []
        conflict_notes: list[str] = []

        for ref in all_child_runs:
            if ref.status in ("pending", "running"):
                # Assume recovery possible for pending/running entries.
                # The orchestration app layer will validate liveness.
                recovered_ids.append(ref.run_id)
            else:
                # Terminal entries are not recovered; they are completed facts.
                pass

        # Re-enter barrier if persisted state requires it.
        barrier = record.barrier

        # Determine recovery status.
        # Per RFC-orch-drive.md section 15.3:
        #   - If no barrier remains: recovering -> running
        #   - If barrier persists: recovering -> resolving or replanning
        new_status: DriveStatus = "running"
        if barrier is not None:
            # Re-enter appropriate barrier state based on reason.
            barrier_reason = barrier.reason
            if barrier_reason in ("operator_pause",):
                new_status = "blocked_operator"
            elif barrier_reason in (
                "runtime_failure",
                "merge_conflict",
                "review_failed",
                "recovery_gate",
            ):
                new_status = "resolving"
            elif barrier_reason == "planner_needed":
                new_status = "replanning"
            conflict_notes.append(
                f"barrier restored: reason={barrier_reason}; status transitioned to {new_status}"
            )

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
                    f"transition to {new_status}"
                ),
            )

        # Apply recovery: persist the updated drive state.
        now = time.time()
        recovered_run_ids = tuple(ref.run_id for ref in active_runs)
        updated = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=new_status,
            started_at=record.started_at,
            updated_at=now,
            finished_at=None,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=recovered_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=barrier,
            operator_pause_state="active",
            summary=(
                f"drive recovered; {len(recovered_ids)} child runs restored; status={new_status}"
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
                f"drive recovered; {len(recovered_ids)} child runs restored; status={new_status}"
            ),
        )


__all__ = [
    "ConcreteDriveDriver",
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
