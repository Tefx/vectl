"""Resume mixin for DriveDriver.

Authority: docs/RFC-orch-drive.md sections 15.1, 15.3.
"""

from __future__ import annotations

import time

from vectl.orchestration.contracts import DriveRecord, DriveStatus
from vectl.orchestration.driver_types import DriveResumeResult
from vectl.orchestration.driver_transitions import (
    InvalidDriveTransitionError,
    TERMINAL_DRIVE_STATUSES,
    _barrier_status,
    validate_drive_transition,
)


class DriverResumeMixin:
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
