"""Primary drive-loop dispatch mixin.

Authority: docs/RFC-orch-drive.md section 10.
"""

from __future__ import annotations

import uuid
import time

from dataclasses import replace

from vectl.orchestration.contracts import ChildRunRef, ControlDecision, DriveBarrier, DriveRecord, DriveStatus
from vectl.orchestration.driver_types import DriveChildLaunchError, DriveLoopResult
from vectl.orchestration.driver_resolution_helpers import (
    _barrier_reason_for_case_source,
    _infer_case_source,
    _resolver_should_handle_active_barrier,
    _update_drive_record,
)
from vectl.orchestration.driver_transitions import (
    TERMINAL_DRIVE_STATUSES,
    _MAX_STALE_CHILD_FAILURES_PER_STEP,
    _merge_ids,
)


class DriverLoopMixin:
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
            barrier_reason = (
                record.barrier.reason if record.barrier is not None else "runtime_failure"
            )
            reason = f"Open {barrier_reason} cases require resolution: {', '.join(open_case_ids)}"
            decision = ControlDecision(
                kind="resolve",
                reason=reason,
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
        blocked_case_ids = record.blocked_case_ids

        if decision.kind == "dispatch_batch":
            try:
                launched_refs = self._launch_child_runs_for_decision(record, decision)
                summary = (
                    f"dispatch_batch: steps={decision.step_ids} "
                    f"capacity_used={decision.capacity_used} "
                    f"capacity_remaining={decision.capacity_remaining}"
                )
                if launched_refs:
                    child_ids = tuple(ref.run_id for ref in launched_refs)
                    summary += f" child_runs={child_ids}"
            except DriveChildLaunchError as exc:
                launched_refs = exc.launched_refs
                new_status, barrier, summary = self._launch_failure_barrier(record, exc)
                blocked_case_ids = _merge_ids(record.blocked_case_ids, barrier.case_ids)
        elif decision.kind == "dispatch":
            try:
                launched_refs = self._launch_child_runs_for_decision(record, decision)
                summary = f"dispatch: step={decision.step_ids}"
                if launched_refs:
                    child_ids = tuple(ref.run_id for ref in launched_refs)
                    summary += f" child_runs={child_ids}"
            except DriveChildLaunchError as exc:
                launched_refs = exc.launched_refs
                new_status, barrier, summary = self._launch_failure_barrier(record, exc)
                blocked_case_ids = _merge_ids(record.blocked_case_ids, barrier.case_ids)
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
        if blocked_case_ids != updated.blocked_case_ids:
            updated = replace(updated, blocked_case_ids=blocked_case_ids)
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
            try:
                ref = self._child_run_launcher(record.drive_id, step_id, role_id)
            except Exception as exc:
                raise DriveChildLaunchError(
                    step_id=step_id,
                    launched_refs=tuple(launched),
                    original_error=exc,
                ) from exc
            self._drive_store.save_child_run(ref)
            launched.append(ref)
        return tuple(launched)

    def _launch_failure_barrier(
        self,
        record: DriveRecord,
        exc: DriveChildLaunchError,
    ) -> tuple[DriveStatus, DriveBarrier, str]:
        case_id = f"runtime_{uuid.uuid4().hex}"
        active_child_run_ids = _merge_ids(
            record.active_child_run_ids,
            tuple(ref.run_id for ref in exc.launched_refs),
        )
        barrier = DriveBarrier(
            reason="runtime_failure",
            entered_at=time.time(),
            case_ids=(case_id,),
            active_child_run_ids_at_entry=active_child_run_ids,
        )
        self._drive_store.record_retry_attempt(
            drive_id=record.drive_id,
            step_id=exc.step_id,
            run_id=f"launch_{uuid.uuid4().hex}",
            failure_class="launch_failure",
            summary=str(exc.original_error),
        )
        attempt_count = self._drive_store.retry_attempt_count(
            drive_id=record.drive_id,
            step_id=exc.step_id,
            failure_class="launch_failure",
        )
        release_note = ""
        try:
            self._core_adapter.defer_step(exc.step_id)
            release_note = "; claim released for retry"
        except Exception as release_exc:
            release_note = f"; claim release failed: {release_exc}"
        limit_note = ""
        new_status: DriveStatus = "resolving"
        if attempt_count >= _MAX_STALE_CHILD_FAILURES_PER_STEP:
            limit_note = "; retry limit reached"
            new_status = "blocked_operator"
        return (
            new_status,
            barrier,
            f"child launch failed for step={exc.step_id} attempt={attempt_count}: "
            f"{exc.original_error}; case_id={case_id}{release_note}{limit_note}",
        )
