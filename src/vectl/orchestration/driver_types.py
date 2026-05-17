"""Public drive result and error types.

Authority: docs/RFC-orch-drive.md sections 7.2, 10, 14, 15.
"""

from __future__ import annotations

from dataclasses import dataclass

from vectl.orchestration.contracts import ChildRunRef, DriveBarrier, DriveStatus

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


class DriveChildLaunchError(RuntimeError):
    """Raised when a child launch fails after zero or more admissions."""

    def __init__(
        self,
        *,
        step_id: str,
        launched_refs: tuple[ChildRunRef, ...],
        original_error: Exception,
    ) -> None:
        super().__init__(f"child launch failed for {step_id}: {original_error}")
        self.step_id = step_id
        self.launched_refs = launched_refs
        self.original_error = original_error
