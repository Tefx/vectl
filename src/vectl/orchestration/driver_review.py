"""Post-execution review mixin for DriveDriver.

Authority: docs/RFC-orch-drive.md section 17.1.
"""

from __future__ import annotations

import time
from dataclasses import replace

from vectl.orchestration.contracts import BarrierReason, ChildRunRef, DriveBarrier, DriveRecord, DriveStatus, ExecutionResult
from vectl.orchestration.driver_types import DriveLoopResult
from vectl.orchestration.review_gate import ReviewGateResult
from vectl.orchestration.driver_resolution_helpers import (
    _artifact_refs_for_review,
    _execution_result_from_terminal_child_run,
    _reconcile_disposition_from_run_record,
)


class DriverReviewMixin:
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

            if (
                review_result.status == "needs_replan"
                and review_result.planner_request is not None
                and self._planner_mutation_applier is not None
            ):
                active_child_run_ids = tuple(
                    run_id for run_id in record.active_child_run_ids if run_id != child_run.run_id
                )
                case_id = f"review_{child_run.run_id}"
                replan_record = replace(
                    record,
                    updated_at=time.time(),
                    active_child_run_ids=active_child_run_ids,
                    blocked_case_ids=(case_id,),
                    summary=(
                        f"post-execution review needs_replan for step={child_run.step_id}: "
                        f"{review_result.summary}"
                    ),
                )
                decision = ControlDecision(
                    kind="replan",
                    reason=review_result.summary,
                    planner_request=review_result.planner_request,
                    case_ids=(case_id,),
                    barrier_required=True,
                )
                return self._handle_replan_decision(
                    drive_id=record.drive_id,
                    record=replan_record,
                    _core=self._core_adapter.snapshot(agent=record.agent or "default"),
                    _roster=self._control.sources.roster.snapshot(),
                    _runtime=self._control.sources.runtime.snapshot(),
                    decision=decision,
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

