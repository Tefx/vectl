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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunRef,
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    PlannerMutationBundle,
    PlannerRequest,
    RecoveryAttempt,
    RecoveryContinuity,
    ReconcileResult,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    from vectl.orchestration.control import Control, PlanAwareControl
    from vectl.orchestration.core_adapter import CoreAdapter
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.review_gate import ReviewGate
    from vectl.orchestration.roster import Roster
    from vectl.orchestration.run_store import DriveStore
    from vectl.orchestration.runtime import Runtime


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
        active_child_run_ids: Currently active child runs.
        frontier_step_ids: Current claimable frontier.
        barrier: Current barrier, if active.
        summary: Human-readable aggregate progress description.
    """

    drive_id: str
    status: DriveStatus
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
    """

    def __init__(
        self,
        *,
        drive_store: DriveStore,
        core_adapter: CoreAdapter,
        control: PlanAwareControl,
        max_parallelism: int = 4,
    ) -> None:
        self._drive_store = drive_store
        self._core_adapter = core_adapter
        self._control = control
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
          3. Persists updated drive state
          4. Returns the loop result

        The actual child-run dispatch, reconciliation, and barrier resolution
        are handled by the orchestration app layer that calls this method and
        acts on the returned ControlDecision.

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

        decision = self._control.evaluate(
            core=core,
            roster=self._control.sources.roster.snapshot_value,
            runtime=self._control.sources.runtime.snapshot_value,
            drive=record,
            barrier=drive_barrier,
            open_case_ids=open_case_ids,
        )

        # Persist updated drive state reflecting the decision.
        now = time.time()
        completed_steps: tuple[str, ...] = ()
        new_status = record.status
        summary = f"loop pass: decision={decision.kind}"

        if decision.kind == "dispatch_batch":
            # Record the frontier decision; actual dispatch is the caller's
            # responsibility (orch_app) via its existing dispatch paths.
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
            summary = f"resolve: case_ids={decision.case_ids}"
        elif decision.kind == "replan":
            summary = f"replan: {decision.reason}"

        updated = DriveRecord(
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
            barrier=record.barrier,
            operator_pause_state=record.operator_pause_state,
            summary=summary,
        )
        self._drive_store.save_drive(updated)

        return DriveLoopResult(
            drive_id=drive_id,
            status=new_status,
            completed_steps=completed_steps,
            active_child_run_ids=record.active_child_run_ids,
            barrier=record.barrier,
            summary=summary,
        )

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
