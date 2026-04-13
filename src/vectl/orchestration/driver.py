"""
Drive-level orchestration loop and barrier management.

Authority: docs/RFC-orch-drive.md sections 10, 14, 15, 17
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 4, 5

This module pins the authoritative public API for start_drive, run_drive_loop,
resume_drive, and recover_drive. Contract-only: signatures, result types, and
docstrings. No loop/runtime logic bodies beyond placeholders.
"""

from __future__ import annotations

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
    from vectl.orchestration.control import Control
    from vectl.orchestration.core_adapter import CoreAdapter
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.review_gate import ReviewGate
    from vectl.orchestration.roster import Roster
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
    "MaxParallelismError",
    "TERMINAL_DRIVE_STATUSES",
]
