"""
Plan-aware orchestration flow.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2
Authority: docs/RFC-orch-drive.md sections 9, 10
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from vectl.orchestration._control_decisions import (
    blocked_steps_case_id as _blocked_steps_case_id,
)
from vectl.orchestration._control_decisions import (
    count_active_step_runs as _count_active_step_runs,
)
from vectl.orchestration._control_decisions import (
    deterministic_frontier_order as _deterministic_frontier_order,
)
from vectl.orchestration._control_decisions import (
    format_unresolved_reason as _format_unresolved_reason,
)
from vectl.orchestration._control_decisions import (
    has_active_work as _has_active_work,
)
from vectl.orchestration._control_decisions import (
    has_plan_complete_conflict as _has_plan_complete_conflict,
)
from vectl.orchestration._control_decisions import (
    is_runtime_idle as _is_runtime_idle,
)
from vectl.orchestration._control_decisions import (
    plan_conflict_case_id as _plan_conflict_case_id,
)
from vectl.orchestration._control_decisions import (
    synthetic_resolve_case_id as _synthetic_resolve_case_id,
)
from vectl.orchestration._control_decisions import (
    validate_decision_invariants,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    PlannerMutationBundle,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.interfaces import Control


class SamePlanRunRejectedError(Exception):
    """Raised when orch run is rejected because an active drive owns the plan.

    Authority: docs/RFC-orch-drive.md section 7.1, 14.1

    This error is distinct from DriveActiveRunBlockedError (which is a
    RunStoreError for the store-level check). This error is raised at the
    orchestration app / control layer when the admission authority determines
    that a standalone run cannot proceed because a drive is active for the
    same plan.

    The canonical contract requires:
        - exit code 2
        - no --force override is permitted
        - error text must include the active drive_id
        - error text must instruct the operator to use drive-scoped commands

    Attributes:
        drive_id: The active drive that blocks the run.
        plan_path: The plan path that the run was targeting.
        message: Human-readable explanation.
    """

    def __init__(self, message: str, *, drive_id: str, plan_path: str) -> None:
        super().__init__(message)
        self.drive_id = drive_id
        self.plan_path = plan_path
        self.message = message


DEFAULT_DISPATCH_ROLE = "python-executor"


class RosterSnapshotSource(Protocol):
    """Read-only roster snapshot source consumed by control.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.2 and 5.1
    """

    def snapshot(self) -> RosterSnapshot:
        """Return component snapshot for control evaluation.

        Returns:
            Current roster snapshot for plan-aware control evaluation.
        """
        ...


class RuntimeSnapshotSource(Protocol):
    """Read-only runtime snapshot source consumed by control.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.3 and 5.2
    """

    def snapshot(self) -> RuntimeSnapshot:
        """Return component snapshot for control evaluation.

        Returns:
            Current runtime snapshot for plan-aware control evaluation.
        """
        ...


@dataclass(frozen=True)
class ControlInputSources:
    """Required authoritative/control-adjacent snapshot sources.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1 and 5

    This contract pins that control consumes:
        - authoritative core snapshots via ``core_adapter``
        - component snapshots from ``roster`` and ``runtime``
    """

    core_adapter: CoreAdapter
    roster: RosterSnapshotSource
    runtime: RuntimeSnapshotSource


@dataclass(frozen=True)
class PlanAwareControl:
    """Deterministic orchestration-flow implementation.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1, 5.1, 5.2, 5.3
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.2, 3.6, 5
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5, 6
        docs/RFC-orch-drive.md sections 9, 10

    Decision invariant enforcement (RFC-orch-drive.md section 9.2.1):
        - ``dispatch_batch``: ``step_ids`` non-empty, ``role_bindings`` covers
          every ``step_id``
        - ``resolve``: ``case_ids`` non-empty
        - ``replan``: ``planner_request`` present
        - ``wait``: ``step_ids`` and ``case_ids`` empty
        - ``done``: no frontier and no active child runs remain
        - ``halt``: barrier is terminal, no new work may be admitted
    """

    sources: ControlInputSources
    agent: str | None = None
    dispatch_role: str = DEFAULT_DISPATCH_ROLE

    def evaluate_current(self) -> ControlDecision:
        """Evaluate current snapshots read from authoritative sources.

        Returns:
            Next control decision from current core/roster/runtime snapshots.
        """
        core = self.sources.core_adapter.snapshot(agent=self.agent)
        roster = self.sources.roster.snapshot()
        runtime = self.sources.runtime.snapshot()
        return self.evaluate(core=core, roster=roster, runtime=runtime)

    def apply_resolution_current(self, report: ResolutionReport) -> ControlDecision:
        """Apply resolver report against refreshed authoritative snapshots.

        Args:
            report: Resolver report to apply.

        Returns:
            Follow-up decision computed from refreshed state.
        """
        core = self.sources.core_adapter.snapshot(agent=self.agent)
        roster = self.sources.roster.snapshot()
        runtime = self.sources.runtime.snapshot()
        return self.apply_resolution(report=report, core=core, roster=roster, runtime=runtime)

    # @invar:allow function_size: Decision ladder preserves RFC priority order and public control semantics in one auditable method.
    # @invar:allow dead_param: roster is retained for Control interface compatibility and future roster-aware scheduling.
    def evaluate(
        self,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
        open_case_ids: tuple[str, ...] = (),
        recovery_gate_blocked: bool = False,
    ) -> ControlDecision:
        """Evaluate authoritative/component snapshots into one control decision.

        Decision logic follows RFC-orch-drive.md section 9 in priority order:
            1. Barrier active → wait (barrier_required=True)
            2. Operator pause active → halt
            3. Recovery gate blocked → wait (barrier_required=True)
            4. Open cases with drive → resolve (case_ids populated)
            5. Unresolved core state → resolve (with synthetic case_ids)
            6. Plan-complete conflict → resolve (with synthetic case_ids)
            7. Plan complete + idle runtime → done
            8. Claimable steps → dispatch_batch or dispatch
            9. Active work → wait
            10. Blocked steps → resolve (with synthetic case_ids)
            11. Default → wait

        Args:
            core: Authoritative core snapshot produced through ``CoreAdapter``.
            roster: Current roster component snapshot.
            runtime: Current runtime component snapshot.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.
            open_case_ids: Open resolution case identifiers.
            recovery_gate_blocked: Whether the recovery gate is currently
                blocking dispatch.

        Returns:
            Next orchestration-plane control decision with all invariants met.
        """
        # --- Tier 1: Barrier gate (RFC-orch-drive.md section 5.4, 9.2) ---
        # If a barrier is active, drive scheduling must not dispatch.
        if barrier is not None:
            # Authority: RFC-orch-drive.md section 8.4.1 (recovery_gate)
            if barrier.reason == "recovery_gate":
                return ControlDecision(
                    kind="wait",
                    reason=f"Barrier active (recovery gate): {barrier.reason}",
                    barrier_required=True,
                )
            # Authority: RFC-orch-drive.md section 7.3 (operator pause)
            if barrier.reason == "operator_pause":
                return ControlDecision(
                    kind="halt",
                    reason=f"Barrier active (operator pause): {barrier.reason}",
                    barrier_required=True,
                )
            # Authority: RFC-orch-drive.md section 5.4
            # All other barrier reasons route to wait with barrier_required.
            return ControlDecision(
                kind="wait",
                reason=f"Barrier active: {barrier.reason}",
                barrier_required=True,
            )

        # --- Tier 2: Operator pause (without barrier) ---
        # Authority: RFC-orch-drive.md section 7.3, 16
        if drive is not None and drive.operator_pause_state == "paused":
            return ControlDecision(
                kind="halt",
                reason="Drive is operator-paused, no new work admitted",
                barrier_required=True,
            )

        # --- Tier 3: Recovery gate ---
        # Authority: RFC-orch-drive.md section 8.4.1
        if recovery_gate_blocked:
            return ControlDecision(
                kind="wait",
                reason="Recovery gate blocks dispatch",
                barrier_required=True,
            )

        # --- Tier 4: Open cases with drive ---
        # Authority: RFC-orch-drive.md section 9.2
        if open_case_ids and drive is not None:
            return ControlDecision(
                kind="resolve",
                reason=f"Open cases require resolution: {', '.join(open_case_ids)}",
                case_ids=open_case_ids,
            )

        # --- Tier 5: Unresolved core state ---
        unresolved_reason = _format_unresolved_reason(core)
        if unresolved_reason is not None:
            # Decision invariant: resolve must have non-empty case_ids.
            # When no drive context provides explicit case_ids, use a
            # synthetic case identifier derived from the core state.
            synthetic_id = _synthetic_resolve_case_id(core)
            return ControlDecision(
                kind="resolve",
                reason=unresolved_reason,
                case_ids=(synthetic_id,),
            )

        # --- Tier 6: Plan-complete conflict ---
        if _has_plan_complete_conflict(core):
            conflict_id = _plan_conflict_case_id(core)
            return ControlDecision(
                kind="resolve",
                reason=(
                    "Authoritative core snapshot conflict: plan_complete=True while "
                    "claimable/in_progress/blocked still present"
                ),
                case_ids=(conflict_id,),
            )

        # --- Tier 7: Plan complete + idle ---
        # Authority: RFC-orch-drive.md section 9.2.1 (done invariant)
        # "done: no frontier and no active child runs remain"
        if core.plan_complete and _is_runtime_idle(runtime):
            # In drive context, both conditions must hold: no frontier AND
            # no active step child runs.
            if drive is not None and drive.active_child_run_ids:
                return ControlDecision(
                    kind="wait",
                    reason="Plan complete but active child runs remain",
                )
            return ControlDecision(kind="done", reason="Plan complete and runtime idle")

        # --- Tier 8: Claimable work → dispatch ---
        # Authority: RFC-orch-drive.md section 9.3, 9.4
        if core.claimable_step_ids:
            # Deterministic frontier ordering (RFC 9.4)
            ordered_frontier = _deterministic_frontier_order(core.claimable_step_ids)

            # Drive-aware batch dispatch: when a drive is active,
            # return dispatch_batch with capacity-aware frontier.
            if drive is not None:
                active_step_count = _count_active_step_runs(drive)
                available_capacity = max(0, drive.max_parallelism - active_step_count)
                capacity_used = min(len(ordered_frontier), available_capacity)
                capacity_remaining = available_capacity - capacity_used
                batch_ids = ordered_frontier[:capacity_used] if capacity_used > 0 else ()
                role_bindings = {sid: self.dispatch_role for sid in batch_ids}

                # Invariant: dispatch_batch must have non-empty step_ids and
                # role_bindings covering every step_id.
                if not batch_ids:
                    # Capacity fully consumed — must wait.
                    return ControlDecision(
                        kind="wait",
                        reason="All capacity consumed by active child runs",
                    )

                return ControlDecision(
                    kind="dispatch_batch",
                    reason="Claimable work available (drive-aware)",
                    step_ids=batch_ids,
                    role_bindings=role_bindings,
                    capacity_used=capacity_used,
                    capacity_remaining=capacity_remaining,
                )

            # No drive: single-step legacy dispatch
            step_id = ordered_frontier[0]
            return ControlDecision(
                kind="dispatch",
                reason="Claimable work available",
                step_ids=(step_id,),
                role_bindings={step_id: self.dispatch_role},
            )

        # --- Tier 9: Active work → wait ---
        if _has_active_work(core=core, runtime=runtime):
            return ControlDecision(kind="wait", reason="Execution already in progress")

        # --- Tier 10: Blocked steps → resolve ---
        if core.blocked_step_ids:
            blocked_steps = ", ".join(core.blocked_step_ids)
            # Decision invariant: resolve must have non-empty case_ids.
            blocked_id = _blocked_steps_case_id(core)
            return ControlDecision(
                kind="resolve",
                reason=f"Blocked steps require resolution: {blocked_steps}",
                case_ids=(blocked_id,),
            )

        # --- Tier 11: Default wait ---
        return ControlDecision(
            kind="wait",
            reason="No claimable work yet, waiting for authoritative state changes",
        )

    def apply_resolution(
        self,
        report: ResolutionReport,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
    ) -> ControlDecision:
        """Apply resolver report and return a follow-up control decision.

        Continuation rules (RFC-orch-drive.md section 12.3):
            - ``unblocked`` without planner_request: re-evaluate from refreshed state
            - ``unblocked`` with planner_request: transition to replan barrier
            - ``waiting``: hold (wait)
            - ``operator_required``: hold (wait)
            - ``halt``: terminal halt — no further work may be admitted

        Args:
            report: Resolver output for a previously unresolved case.
            core: Refreshed authoritative core snapshot.
            roster: Refreshed roster component snapshot.
            runtime: Refreshed runtime component snapshot.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.

        Returns:
            Follow-up control decision after applying resolver outcome.
        """
        if report.status == "unblocked":
            # If resolver also requests planner, prefer replan transition.
            # Authority: RFC-orch-drive.md section 12.3
            if report.planner_request is not None:
                return ControlDecision(
                    kind="replan",
                    reason=f"Resolver requests planner: {report.planner_request.reason}",
                    planner_request=report.planner_request,
                    barrier_required=True,
                )
            return self.evaluate(
                core=core,
                roster=roster,
                runtime=runtime,
                drive=drive,
                barrier=barrier,
            )

        if report.status == "waiting":
            return ControlDecision(kind="wait", reason=f"Resolver waiting: {report.summary}")

        if report.status == "operator_required":
            operator_suffix = ""
            if report.operator_message:
                operator_suffix = f" (operator_message={report.operator_message})"
            return ControlDecision(
                kind="wait",
                reason=f"Resolver requires operator input: {report.summary}{operator_suffix}",
            )

        # report.status == "halt"
        # Authority: RFC-orch-drive.md section 12.2, 10.3
        # halt is a terminal decision: barrier is terminal, no new work admitted.
        return ControlDecision(
            kind="halt",
            reason=f"Resolver halted orchestration: {report.summary}",
            barrier_required=True,
        )

    def apply_planner_result(
        self,
        bundle: PlannerMutationBundle,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
    ) -> ControlDecision:
        """Apply planner mutation bundle and return a follow-up control decision.

        Authority: docs/RFC-orch-drive.md section 13.5

        Continuation rules:
            - ``applyable``: re-evaluate from refreshed state (may produce
              dispatch_batch, wait, or done)
            - ``operator_required``: transition to wait with barrier_required
            - ``halt``: terminal halt, no new work may be admitted

        Args:
            bundle: Machine-readable planner output contract.
            core: Refreshed authoritative core snapshot.
            roster: Refreshed roster component snapshot.
            runtime: Refreshed runtime component snapshot.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.

        Returns:
            Follow-up control decision after applying planner outcome.
        """
        if bundle.status == "applyable":
            # Authority: RFC-orch-drive.md section 13.5
            # After successful apply, re-evaluate to determine next action.
            # The planner may have changed the frontier, so we must re-evaluate
            # rather than assume dispatch_batch.
            return self.evaluate(
                core=core,
                roster=roster,
                runtime=runtime,
                drive=drive,
                barrier=barrier,
            )

        if bundle.status == "operator_required":
            return ControlDecision(
                kind="wait",
                reason=f"Planner requires operator intervention: {bundle.summary}",
                barrier_required=True,
            )

        # bundle.status == "halt"
        # Authority: RFC-orch-drive.md section 10.3 (terminal states)
        return ControlDecision(
            kind="halt",
            reason=f"Planner halted: {bundle.summary}",
            barrier_required=True,
        )


@dataclass(frozen=True)
class LegacyLoopSurfaceSplit:
    """Historical decomposition of orchestration loop surfaces by responsibility.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 4
    """

    control_surfaces: tuple[str, ...]
    runtime_surfaces: tuple[str, ...]
    core_adapter_surfaces: tuple[str, ...]
    resolver_surfaces: tuple[str, ...]


LEGACY_LOOP_SURFACE_SPLIT: Final[LegacyLoopSurfaceSplit] = LegacyLoopSurfaceSplit(
    control_surfaces=(
        "run main-loop orchestration branch ownership",
        "decide-dispatch-wait-reconcile flow decisions",
        "blocked/unresolved branch routing to resolver",
    ),
    runtime_surfaces=(
        "handle_dispatch mechanical launch wiring",
        "wait_for_any completion intake",
        "shutdown runner/worktree cleanup choreography",
    ),
    core_adapter_surfaces=(
        "plan load/validate read snapshots",
        "claim/complete/defer lifecycle mutations",
        "step isolation lookup",
    ),
    resolver_surfaces=(
        "resolver invocation for unresolved cases",
        "resolution report parsing and outcome mapping",
    ),
)


__all__ = [
    "Control",
    "ControlInputSources",
    "DEFAULT_DISPATCH_ROLE",
    "LegacyLoopSurfaceSplit",
    "LEGACY_LOOP_SURFACE_SPLIT",
    "PlanAwareControl",
    "RosterSnapshotSource",
    "RuntimeSnapshotSource",
    "SamePlanRunRejectedError",
    "validate_decision_invariants",
]
