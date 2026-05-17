"""Planner barrier mixin for DriveDriver.

Authority: docs/RFC-orch-drive.md section 13.
"""

from __future__ import annotations

import time

from vectl.orchestration.contracts import ControlDecision, CoreSnapshot, DriveBarrier, DriveRecord, DriveStatus, RosterSnapshot, RuntimeSnapshot
from vectl.orchestration.driver_types import DriveLoopResult
from vectl.orchestration.driver_planning_helpers import _apply_planner_bundle_to_drive, _construct_bundle_from_request
from vectl.orchestration.driver_resolution_helpers import _update_drive_record
from vectl.orchestration.driver_transitions import validate_drive_transition


class DriverPlannerMixin:
    def _handle_replan_decision(
        self,
        drive_id: str,
        record: DriveRecord,
        _core: CoreSnapshot,
        _roster: RosterSnapshot,
        _runtime: RuntimeSnapshot,
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
