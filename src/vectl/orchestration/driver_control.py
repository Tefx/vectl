"""Drive-scoped control consumption mixin.

Authority: docs/RFC-orch-drive.md sections 7.3, 10, 16.
"""

from __future__ import annotations

import time
from dataclasses import replace

from vectl.orchestration.contracts import DriveRecord, DriveStatus
from vectl.orchestration.driver_transitions import InvalidDriveTransitionError, TERMINAL_DRIVE_STATUSES, _barrier_status, validate_drive_transition


class DriverControlMixin:
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
