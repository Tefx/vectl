"""Resume and recovery mixin for DriveDriver.

Authority: docs/RFC-orch-drive.md sections 15.1, 15.2, 15.3.
"""

from __future__ import annotations

import time
from dataclasses import replace

from vectl.orchestration.contracts import ChildRunRef, DriveBarrier, DriveRecord, DriveStatus
from vectl.orchestration.driver_types import DriveRecoverResult, DriveResumeResult
from vectl.orchestration.driver_transitions import (
    InvalidDriveTransitionError,
    TERMINAL_DRIVE_STATUSES,
    _MAX_STALE_CHILD_FAILURES_PER_STEP,
    _barrier_status,
    validate_drive_transition,
)


class DriverRecoveryMixin:
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

        Conflict resolution follows RFC-orch-drive.md section 15.3.1:
            - persisted child run=running, live process missing, no terminal
              artifact → live runtime fact wins; classify as stale
            - persisted child run=running, live terminal artifact exists →
              committed-but-unobserved completion beats stale in-memory status
            - persisted step lease exists, core marks step done → core
              authority wins
            - persisted drive frontier includes step, core no longer claimable
              → core authority wins; frontier recomputed
            - persisted barrier absent, open case exists → open case state
              wins; enter barrier

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

        # Step 1: Restore persisted drive state.
        # (The `record` variable already holds the replayed drive state.)

        # Step 2: Restore child-run facts and reconcile.
        # Authority: RFC-orch-drive.md section 15.3.1
        all_child_runs = self._drive_store.child_runs_for_drive(drive_id)
        active_runs = self._drive_store.active_child_runs_for_drive(drive_id)

        recovered_ids: list[str] = []
        failed_ids: list[str] = []
        retry_limited_step_ids: list[str] = []
        conflict_notes: list[str] = []

        for ref in all_child_runs:
            if ref.status in ("pending", "running"):
                # Step 3: Reconcile live/runtime facts to persisted records.
                # For each persisted non-terminal child run, check if a
                # durable terminal artifact exists in the run store —
                # this is the "committed-but-unobserved completion" path.
                #
                # Authority: RFC-orch-drive.md section 15.3.1
                # "persisted child run=running, live terminal artifact exists
                #  → persisted + terminal artifact"
                terminal_artifact = self._find_terminal_artifact(ref.run_id)
                if terminal_artifact is not None:
                    # Durable terminal artifact beats stale in-memory status.
                    conflict_notes.append(
                        f"child run {ref.run_id}: persisted status={ref.status} but "
                        f"durable terminal artifact found ({terminal_artifact}); "
                        f"classifying as completed fact"
                    )
                    # This child run is a completed fact; it should not be in
                    # the active set. We do NOT add it to recovered_ids.
                    continue

                # No durable terminal artifact.  Check liveness.
                # Authority: RFC-orch-drive.md section 15.3.1
                # "persisted child run=running, live process missing, no
                #  terminal artifact → live runtime fact"
                liveness = self._check_child_run_liveness(ref.run_id)
                if liveness == "stale":
                    # Stale persisted running state with no live process.
                    # The process is gone; persisted running state is stale.
                    # Classify as failed rather than recovered.  Recovery will
                    # persist this terminal fact below so replay does not
                    # resurrect the dead run, then release the core claim so
                    # normal scheduling can retry the step without bypassing
                    # control authority.
                    conflict_notes.append(
                        f"child run {ref.run_id}: persisted status={ref.status} but "
                        f"live process missing and no terminal artifact; "
                        f"classifying as stale (failed); scheduler may retry after claim release"
                    )
                    step_label = ref.step_id or ref.run_id
                    historical_failures_for_step = sum(
                        1
                        for prior in all_child_runs
                        if prior.run_id != ref.run_id
                        and prior.step_id == ref.step_id
                        and prior.status in ("fail", "stall", "transport_error")
                    )
                    prior_retry_count = self._drive_store.retry_attempt_count(
                        drive_id=drive_id,
                        step_id=step_label,
                        failure_class="stale_child",
                    )
                    failed_count_for_step = max(
                        historical_failures_for_step,
                        prior_retry_count,
                    ) + 1
                    if failed_count_for_step >= _MAX_STALE_CHILD_FAILURES_PER_STEP:
                        retry_limited_step_ids.append(step_label)
                        conflict_notes.append(
                            f"operator required: retry limit reached for {step_label} "
                            f"after {failed_count_for_step} stale child failures"
                        )
                    failed_ids.append(ref.run_id)
                    continue

                if liveness == "unknown" and ref.status == "pending":
                    # Pending with unknown liveness — may still be queued.
                    # Recovery-conservative: include in recovered set.
                    recovered_ids.append(ref.run_id)
                    continue

                # Liveness is "alive" or unknown-but-running: assume recoverable.
                recovered_ids.append(ref.run_id)
            else:
                # Terminal entries are completed facts; not recovered.
                pass

        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted active child set differs from live child set →
        #  union, then normalize by liveness checks"
        # We use the union of persisted active runs and freshly recovered
        # runs, excluding failed/stale ones.
        recovered_active_ids = tuple(
            dict.fromkeys(
                list(ref.run_id for ref in active_runs if ref.run_id not in failed_ids)
                + [rid for rid in recovered_ids if rid not in {r.run_id for r in active_runs}]
            )
        )

        # If a previous supervisor died after a child run reached a terminal
        # state but before authoritative completion/defer, the plan can be left
        # with a claimed step and no active drive child.  In drive recovery that
        # claim is an orchestration-owned orphan: keeping it makes control see
        # "Execution already in progress" forever while the drive has no live
        # child to collect.  Release only steps owned by this drive (current
        # frontier, historical child-run refs, or the drive agent's live
        # in-progress snapshot when there are no recoverable active child runs)
        # whose child facts are absent from the recovered active set.
        core = self._core_adapter.snapshot(agent=record.agent or "default")
        recovered_active_set = set(recovered_active_ids)
        refs_by_step: dict[str, list[ChildRunRef]] = {}
        for ref in all_child_runs:
            if ref.step_id is None:
                continue
            refs_by_step.setdefault(ref.step_id, []).append(ref)
        drive_owned_step_ids = set(record.frontier_step_ids) | set(refs_by_step)
        if not recovered_active_set:
            # A crash can happen after core claim succeeds but before the child
            # run ref is persisted.  In that state the step is absent from both
            # the stale frontier cache and child-run history, yet control will
            # keep returning "Execution already in progress" forever.  The
            # snapshot is already scoped to ``record.agent`` by CoreAdapter, so
            # these in-progress IDs are drive-agent-owned candidates.
            drive_owned_step_ids.update(core.in_progress_step_ids)
        orphaned_claim_step_ids: list[str] = []
        for step_id in core.in_progress_step_ids:
            if step_id not in drive_owned_step_ids:
                continue
            step_refs = refs_by_step.get(step_id, [])
            if any(ref.run_id in recovered_active_set for ref in step_refs):
                continue
            orphaned_claim_step_ids.append(step_id)

        for step_id in orphaned_claim_step_ids:
            conflict_notes.append(
                f"released orphaned drive claim for {step_id}: no active child run remains"
            )
            if not dry_run:
                self._core_adapter.defer_step(step_id)

        # Step 4: Re-enter barrier if persisted state requires it.
        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted barrier absent, open case exists → open case state"
        barrier = record.barrier
        if barrier is None and record.blocked_case_ids:
            # No persisted barrier but open cases exist — safety requires
            # entering barrier.
            conflict_notes.append(
                f"no persisted barrier but {len(record.blocked_case_ids)} open case(s) "
                f"require barrier re-entry"
            )
            barrier = DriveBarrier(
                reason="recovery_gate",
                entered_at=time.time(),
                case_ids=record.blocked_case_ids,
                pending_resolver_run_id=None,
                pending_planner_run_id=None,
                active_child_run_ids_at_entry=recovered_active_ids,
            )

        # Determine recovery target status.
        # Authority: RFC-orch-drive.md section 8.2.1 (transition table)
        # Authority: RFC-orch-drive.md section 15.3
        #   - If no barrier remains: recovering → running
        #   - If barrier persists: recovering → resolving or replanning
        if record.operator_pause_state == "paused":
            new_status: DriveStatus = "blocked_operator"
        elif barrier is not None:
            new_status = _barrier_status(barrier.reason)
        else:
            new_status = "running"
        if retry_limited_step_ids:
            new_status = "blocked_operator"

        # Validate the transition through "recovering" if needed.
        # Authority: RFC-orch-drive.md section 8.2.1
        # All non-terminal statuses can transition to "recovering".
        if record.status != new_status:
            try:
                # Try direct transition first.
                validate_drive_transition(record.status, new_status)
            except InvalidDriveTransitionError:
                try:
                    # Try via recovering intermediate.
                    validate_drive_transition(record.status, "recovering")
                    validate_drive_transition("recovering", new_status)
                except InvalidDriveTransitionError:
                    # Last resort: keep current status.
                    new_status = record.status

        # Recompute frontier from live core authority.
        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted drive frontier includes step, core no longer
        #  claimable → core authority wins"
        frontier_step_ids = self._recompute_frontier(record)

        # Authority: RFC-orch-drive.md section 15.3.1
        # "persisted step lease exists, core marks step done → core authority"
        # Any active leases for steps now marked done by core should be
        # released. We record this as a conflict note.
        released_lease_steps = self._release_superseded_leases(drive_id)
        if released_lease_steps:
            conflict_notes.append(
                f"released {len(released_lease_steps)} lease(s) for core-done steps: "
                f"{', '.join(released_lease_steps)}"
            )

        # Preserve operator pause state from record.
        # Authority: RFC-orch-drive.md section 15.1
        operator_pause_state = record.operator_pause_state

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
                    f"fail {len(failed_ids)} stale runs, "
                    f"transition to {new_status}"
                ),
            )

        # Apply recovery: first persist failed child-run terminal facts.
        # Without this, replay_drive_state() would rebuild active_child_run_ids
        # from stale pending/running child refs and resurrect the dead run.
        failed_id_set = set(failed_ids)
        for ref in all_child_runs:
            if ref.run_id not in failed_id_set or ref.status not in ("pending", "running"):
                continue
            step_label = ref.step_id or ref.run_id
            self._drive_store.record_retry_attempt(
                drive_id=drive_id,
                step_id=step_label,
                run_id=ref.run_id,
                failure_class="stale_child",
                summary="live process missing and no terminal artifact",
            )
            self._drive_store.save_child_run(replace(ref, status="fail"))
            released_lease = self._drive_store.invalidate_lease(ref.run_id, reason="invalidated")
            if released_lease is not None:
                conflict_notes.append(f"released stale child lease for {ref.step_id}: {ref.run_id}")

        # Persist the updated drive state.
        now = time.time()
        updated = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=new_status,
            started_at=record.started_at,
            updated_at=now,
            finished_at=None,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=recovered_active_ids,
            frontier_step_ids=frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=barrier,
            operator_pause_state=operator_pause_state,
            summary=(
                f"drive recovered; {len(recovered_ids)} child runs restored; "
                f"{len(failed_ids)} stale runs classified as failed; "
                f"status={new_status}"
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
                f"drive recovered; {len(recovered_ids)} child runs restored; "
                f"{len(failed_ids)} stale runs classified as failed; "
                f"status={new_status}"
            ),
        )

    # ------------------------------------------------------------------
    # Recovery helper methods
    # ------------------------------------------------------------------

    def _recompute_frontier(self, record: DriveRecord) -> tuple[str, ...]:
        """Recompute frontier step IDs from live core authority.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted drive frontier includes step, core no longer claimable
        → core authority wins"

        Falls back to the persisted frontier if core snapshot is unavailable
        or empty.

        Args:
            record: The current drive record with persisted frontier.

        Returns:
            Tuple of frontier step IDs derived from live core authority.
        """
        try:
            core = self._core_adapter.snapshot(agent=record.agent or "default")
            live_frontier = core.claimable_step_ids
            if live_frontier:
                # Authority: RFC-orch-drive.md section 15.3.1
                # Core authority beats cached frontier.
                return live_frontier
        except Exception:
            # Core adapter failure during recompute: fall through to
            # persisted frontier.  This is conservative — we don't discard
            # the persisted frontier just because core is unreachable.
            pass
        return record.frontier_step_ids

    def _find_terminal_artifact(self, run_id: str) -> str | None:
        """Check whether a durable terminal artifact exists for a child run.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted child run=running, live terminal artifact exists
        → persisted + terminal artifact"

        Uses the DriveStore's child run index and, if available, the
        RunRegistry for durable terminal evidence.

        Args:
            run_id: The child run identifier to check.

        Returns:
            A string description of the terminal artifact, or None.
        """
        # First, check the child-run index for terminal status.
        # If the child-run ref shows a terminal status, that's durable
        # evidence of completion regardless of whether a live process exists.
        child_ref = self._drive_store.child_run_by_id(run_id)
        if child_ref is not None and child_ref.status in (
            "success",
            "fail",
            "cancelled",
        ):
            return f"child_run status={child_ref.status}"

        # Check via RunRegistry if we have one available.
        # Terminal run records in the registry serve as durable proof.
        if self._run_registry is not None:
            run_record = self._run_registry.by_id(run_id)
            if run_record is not None and run_record.status in ("success", "fail"):
                return f"run_record status={run_record.status}"

        return None

    def _check_child_run_liveness(
        self, run_id: str, *, stale_threshold_seconds: float = 90.0
    ) -> str:
        """Check liveness of a child run using heartbeat artifacts.

        Authority: RFC-orch-drive.md section 10.4
        Default heartbeat stale threshold is 90 seconds.

        Args:
            run_id: The child run identifier to check.
            stale_threshold_seconds: Maximum heartbeat age before stale.

        Returns:
            "alive" if the run heartbeat is recent,
            "stale" if heartbeat is old or missing,
            "unknown" if no heartbeat artifact exists.
        """
        if self._run_registry is None:
            return "unknown"

        try:
            liveness = self._run_registry.liveness_for_run(
                run_id,
                stale_after_seconds=stale_threshold_seconds,
            )
            if liveness == "alive":
                return "alive"
            if liveness == "stale":
                return "stale"
        except Exception:
            pass
        return "unknown"

    def _release_superseded_leases(self, drive_id: str) -> list[str]:
        """Release active leases for steps that core authority marks as done.

        Authority: RFC-orch-drive.md section 15.3.1
        "persisted step lease exists, core marks step done → core authority"

        Uses core snapshot to check which steps are no longer claimable
        because they've been completed, and releases their active leases.

        Args:
            drive_id: The drive ID whose leases to check.

        Returns:
            List of step IDs whose leases were released.
        """
        try:
            record = self._drive_store.replay_drive_state(drive_id)
            if record is None:
                return []
            core = self._core_adapter.snapshot(agent=record.agent or "default")
            claimable = set(core.claimable_step_ids)
            active_leases = self._drive_store.active_leases_for_drive(drive_id)
            released_steps: list[str] = []
            for lease in active_leases:
                # If the step is not claimable and not in-progress, it's
                # been completed by core authority. Release the lease.
                if (
                    lease.step_id not in claimable
                    and lease.step_id not in core.in_progress_step_ids
                ):
                    self._drive_store.invalidate_lease(lease.run_id, reason="superseded")
                    released_steps.append(lease.step_id)
            return released_steps
        except Exception:
            # Core adapter failure: don't release leases speculatively.
            return []
