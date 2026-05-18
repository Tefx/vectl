"""DriveDriver composition root.

Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from vectl.orchestration.contracts import ChildRunRef, ControlDecision, DriveBarrier, DriveRecord
from vectl.orchestration.driver_control import DriverControlMixin
from vectl.orchestration.driver_loop import DriverLoopMixin
from vectl.orchestration.driver_planner import DriverPlannerMixin
from vectl.orchestration.driver_recovery import DriverRecoveryMixin
from vectl.orchestration.driver_resolver import DriverResolverMixin
from vectl.orchestration.driver_resume import DriverResumeMixin
from vectl.orchestration.driver_review import DriverReviewMixin
from vectl.orchestration.driver_types import (
    DriveAdmissionError,
    DriveLoopResult,
    DriveStartResult,
    MaxParallelismError,
)
from vectl.orchestration.driver_resolution_helpers import (
    _agent_recovery_case_id,
    _barrier_reason_for_case_source,
    _infer_case_source,
    _should_skip_agent_assisted_recovery,
)
from vectl.orchestration.driver_transitions import TERMINAL_DRIVE_STATUSES, _barrier_status, _generate_drive_id, validate_drive_transition, InvalidDriveTransitionError
from vectl.orchestration.review_gate import DefaultReviewGate

if TYPE_CHECKING:
    from vectl.orchestration.control import PlanAwareControl
    from vectl.orchestration.control_channel import FilesystemControlChannel
    from vectl.orchestration.core_adapter import CoreAdapter, PlannerMutationApplier
    from vectl.orchestration.resolver import Resolver
    from vectl.orchestration.run_store import DriveStore, RunRegistry


class DriveDriver(
    DriverLoopMixin,
    DriverReviewMixin,
    DriverResolverMixin,
    DriverControlMixin,
    DriverPlannerMixin,
    DriverResumeMixin,
    DriverRecoveryMixin,
):
    """Concrete implementation of drive-level orchestration wiring.

    Authority: docs/RFC-orch-drive.md sections 7, 10, 14, 15
    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.11

    This class composes existing orchestration building blocks (DriveStore,
    PlanAwareControl, core adapter) to implement the four drive-surface
    entry points: start_drive, run_drive_loop, resume_drive, recover_drive.

    It is a composition layer — not a fifth architecture component. The driver
    delegates plan-aware decisions to Control, persistence to DriveStore, and
    authority reads to CoreAdapter.

    Resolver-loop integration (RFC-orch-drive.md section 12.3):
        When control evaluates to ``kind='resolve'``, the driver creates a
        ``ResolutionCase`` snapshot, invokes the resolver, and continues based
        on the ``ResolutionReport`` status:

        - ``unblocked``: refresh state, clear barrier, continue normal scheduling
        - ``waiting``: stay in resolving/waiting; do not dispatch new work
        - ``operator_required``: transition drive to ``blocked_operator``
        - ``halt``: transition drive to ``halted``
        - ``planner_request`` present: transition from resolving to replanning

        Refresh-after-resolution is mandatory (ORCHESTRATION-PLANE-RESOLUTION-CONTRACT
        section 6): control must re-read authoritative state and never trust
        stale pre-resolution assumptions.
    """

    def __init__(
        self,
        *,
        drive_store: DriveStore,
        core_adapter: CoreAdapter,
        control: PlanAwareControl,
        resolver: Resolver | None = None,
        review_gate: DefaultReviewGate | None = None,
        planner_mutation_applier: PlannerMutationApplier | None = None,
        run_registry: RunRegistry | None = None,
        control_channel: FilesystemControlChannel | None = None,
        child_run_launcher: Callable[[str, str, str], ChildRunRef] | None = None,
        child_run_canceller: Callable[[str], bool] | None = None,
        max_parallelism: int = 4,
    ) -> None:
        self._drive_store = drive_store
        self._core_adapter = core_adapter
        self._control = control
        self._resolver = resolver
        self._review_gate = review_gate if review_gate is not None else DefaultReviewGate()
        self._planner_mutation_applier = planner_mutation_applier
        self._run_registry = run_registry
        self._control_channel = control_channel
        self._child_run_launcher = child_run_launcher
        self._child_run_canceller = child_run_canceller
        self._max_parallelism = max(1, min(32, max_parallelism))

    def attempt_agent_assisted_recovery(
        self,
        drive_id: str,
        *,
        reason: str = "",
        max_attempts: int = 2,
    ) -> DriveLoopResult:
        """Give the resolver agent a bounded chance before human handoff.

        This is the last automatic recovery tier for cases that are not
        mechanically safe to clear.  It preserves explicit human controls
        (pause/stop/terminal states), creates an auditable resolution case when
        needed, and then runs the normal drive loop so the existing resolver and
        planner contracts own any mutations.
        """

        from vectl.orchestration.run_store import DriveStoreError

        record = self._drive_store.replay_drive_state(drive_id)
        if record is None:
            raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

        skip_reason = _should_skip_agent_assisted_recovery(record)
        if skip_reason is not None:
            return DriveLoopResult(
                drive_id=drive_id,
                status=record.status,
                active_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary=f"agent-assisted recovery skipped: {skip_reason}",
            )

        if self._resolver is None:
            return DriveLoopResult(
                drive_id=drive_id,
                status=record.status,
                active_child_run_ids=record.active_child_run_ids,
                barrier=record.barrier,
                summary="agent-assisted recovery unavailable: resolver not wired",
            )

        self._ensure_agent_recovery_case(record=record, reason=reason)
        attempts = max(1, max_attempts)
        result: DriveLoopResult | None = None
        for _ in range(attempts):
            result = self.run_drive_loop(drive_id)
            if result.status not in {"resolving", "replanning"}:
                break
            if result.summary.startswith("automation stopped:"):
                break
        assert result is not None
        return result

    def _ensure_agent_recovery_case(self, *, record: DriveRecord, reason: str) -> None:
        if record.barrier is not None and record.blocked_case_ids:
            return

        decision = ControlDecision(
            kind="resolve",
            reason=reason or record.summary or "agent-assisted recovery requested",
            case_ids=record.blocked_case_ids or (_agent_recovery_case_id(record),),
        )
        source = _infer_case_source(decision)
        barrier_reason = _barrier_reason_for_case_source(source)
        case_ids = decision.case_ids
        barrier = DriveBarrier(
            reason=barrier_reason,
            entered_at=time.time(),
            case_ids=case_ids,
            pending_resolver_run_id=None,
            pending_planner_run_id=None,
            active_child_run_ids_at_entry=record.active_child_run_ids,
        )
        new_status = record.status
        if record.status in {"running", "recovering"}:
            candidate = _barrier_status(barrier_reason)
            try:
                validate_drive_transition(record.status, candidate)
                new_status = candidate
            except InvalidDriveTransitionError:
                new_status = record.status
        self._drive_store.save_drive(
            replace(
                record,
                status=new_status,
                updated_at=time.time(),
                blocked_case_ids=case_ids,
                barrier=barrier,
                summary=(
                    "agent-assisted recovery case opened: "
                    f"{decision.reason}; case_ids={case_ids}"
                ),
            )
        )

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

