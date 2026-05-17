"""Resolver barrier mixin for DriveDriver.

Authority: docs/RFC-orch-drive.md section 12.3.
"""

from __future__ import annotations

import time
from dataclasses import replace

from vectl.orchestration.contracts import ControlDecision, CoreSnapshot, DriveBarrier, DriveRecord, DriveStatus, ResolutionCase, RosterSnapshot, RuntimeSnapshot
from vectl.orchestration.driver_types import DriveLoopResult
from vectl.orchestration.driver_resolution_helpers import (
    _apply_resolution_report_to_drive,
    _auto_unblock_stale_runtime_case,
    _barrier_reason_for_case_source,
    _infer_case_source,
    _update_drive_record,
)
from vectl.orchestration.driver_transitions import InvalidDriveTransitionError, validate_drive_transition


class DriverResolverMixin:
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
            blocked_step_ids=tuple(
                dict.fromkeys(
                    (
                        *core.blocked_step_ids,
                        *(
                            sid
                            for sid in core.in_progress_step_ids
                            if sid in record.frontier_step_ids
                        ),
                    )
                )
            ),
            artifact_refs=(),
        )

        # Step 3: Invoke resolver unless the case is a provably stale runtime
        # barrier that can be cleared without human or agent intervention.
        # Authority: ORCHESTRATION-PLANE-INTERFACES.md section 4.4
        report = _auto_unblock_stale_runtime_case(case)
        if report is None:
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
            _post_decision=post_decision,
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

        if (
            report.status == "unblocked"
            and report.planner_request is not None
            and self._planner_mutation_applier is not None
        ):
            planner_decision = ControlDecision(
                kind="replan",
                reason=report.planner_request.reason,
                planner_request=report.planner_request,
                case_ids=resolved_barrier.case_ids if resolved_barrier is not None else (),
            )
            return self._handle_replan_decision(
                drive_id=drive_id,
                record=updated,
                _core=refreshed_core,
                _roster=refreshed_roster,
                _runtime=refreshed_runtime,
                decision=planner_decision,
            )

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
