"""
Plan-aware orchestration flow.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2
Authority: docs/RFC-orch-drive.md sections 9, 10
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

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
from vectl.orchestration.run_store import AdmissionAuthority, DriveActiveRunBlockedError


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


class Control(Protocol):
    """Plan-aware orchestration flow contract.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2
        docs/RFC-orch-drive.md section 9

    Public surface exposes evaluate, apply_resolution, and apply_planner_result.
    The drive-aware signatures accept optional ``drive`` and ``barrier``
    parameters for full drive scheduling integration.
    """

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

        The drive-aware signature allows the control component to factor
        in drive state, barrier state, open cases, and recovery gate
        status when computing its decision.

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
            Next orchestration-plane control decision.
        """
        ...

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

        Authority: docs/RFC-orch-drive.md section 12.3
        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1

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
        ...

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
        Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1

        Continuation rules (RFC-orch-drive.md section 13.5):
            - ``applyable``: attempt facade apply; on success return to
              ``kind='dispatch_batch'`` or ``kind='wait'``
            - ``operator_required``: transition drive to ``blocked_operator``
            - ``halt``: transition drive to ``halted``

        Facade apply disposition rule:
            - ordinary facade apply failure enters ``blocked_operator``
            - only corruption-level or invariant-breaking apply failure
              may enter ``failed_unrecoverable``

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
        ...


def _deterministic_frontier_order(step_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Order claimable frontier steps deterministically.

    Authority: docs/RFC-orch-drive.md section 9.4

    Deterministic frontier ordering rules:
        1. Authoritative claimable frontier only
        2. Stable phase-local ordering by ``step_id``
        3. Truncated by available capacity (done by caller)

    This function implements rule 2: sort by step_id to ensure stable
    phase-local ordering regardless of plan insertion order.

    Args:
        step_ids: Claimable step IDs from authoritative core snapshot.

    Returns:
        Step IDs sorted by stable phase-local ordering.
    """
    return tuple(sorted(step_ids))


def _count_active_step_runs(drive: DriveRecord) -> int:
    """Count active step child runs in a drive (excluding resolver/planner).

    Authority: docs/RFC-orch-drive.md section 10.2

    Resolver and planner child runs are excluded from capacity accounting;
    they are barrier work, not ordinary plan work.

    Note: DriveRecord.active_child_run_ids is a flat tuple of run IDs.
    Since we lack per-run kind information at the ControlDecision level,
    we conservatively count all active child runs. The driver loop must
    refine this with ChildRunRef.kind when admitting.

    Args:
        drive: Current drive record.

    Returns:
        Number of active child runs considered for step capacity.
    """
    return len(drive.active_child_run_ids)


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


def _synthetic_resolve_case_id(core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier from unresolved core state.

    Decision invariant (RFC-orch-drive.md section 9.2.1) requires resolve
    decisions to have non-empty ``case_ids``. When control detects unresolved
    state without an externally-provided case ID, it synthesizes one from
    the unresolved reasons hash.

    Args:
        core: Authoritative core snapshot with unresolved reasons.

    Returns:
        A deterministic synthetic case identifier.
    """
    return f"unresolved:{','.join(core.unresolved_reasons)}"


def _plan_conflict_case_id(core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier for plan-complete conflicts.

    Args:
        core: Authoritative core snapshot with conflicting plan_complete flag.

    Returns:
        A deterministic synthetic case identifier.
    """
    return "plan_complete_conflict"


def _blocked_steps_case_id(core: CoreSnapshot) -> str:
    """Generate a synthetic case identifier for blocked steps.

    Args:
        core: Authoritative core snapshot with blocked steps.

    Returns:
        A deterministic synthetic case identifier.
    """
    return f"blocked:{','.join(core.blocked_step_ids)}"


def _is_runtime_idle(runtime: RuntimeSnapshot) -> bool:
    """Return whether runtime reports no active or stalled work."""

    return not (
        runtime.active_workspaces or runtime.active_executions or runtime.stalled_executions
    )


def _has_active_work(core: CoreSnapshot, runtime: RuntimeSnapshot) -> bool:
    """Return whether authoritative/runtime state shows active execution."""

    return bool(
        core.in_progress_step_ids
        or runtime.active_workspaces
        or runtime.active_executions
        or runtime.stalled_executions
    )


def _has_plan_complete_conflict(core: CoreSnapshot) -> bool:
    """Return True when plan-complete flag conflicts with remaining work surfaces."""

    if not core.plan_complete:
        return False
    return bool(core.claimable_step_ids or core.in_progress_step_ids or core.blocked_step_ids)


def _format_unresolved_reason(core: CoreSnapshot) -> str | None:
    """Build unresolved-state explanation for ``kind='resolve'`` decisions."""

    if not core.unresolved_reasons:
        return None

    if len(core.unresolved_reasons) == 1:
        return f"Unresolved authoritative state: {core.unresolved_reasons[0]}"

    joined = "; ".join(core.unresolved_reasons)
    return f"Unresolved authoritative state: {joined}"


def validate_decision_invariants(decision: ControlDecision) -> list[str]:
    """Validate ControlDecision invariants per RFC-orch-drive.md section 9.2.1.

    This function checks that every decision kind respects its declared
    invariants. It is intended for use in driver loop and test assertions.

    Invariants:
        - ``dispatch_batch``: ``step_ids`` non-empty, ``role_bindings`` covers
          every ``step_id``
        - ``dispatch``: ``step_ids`` non-empty (transitional alias)
        - ``resolve``: ``case_ids`` non-empty
        - ``replan``: ``planner_request`` present
        - ``wait``: ``step_ids`` and ``case_ids`` empty
        - ``done``: ``step_ids`` empty, ``case_ids`` empty, ``capacity_used`` == 0
        - ``halt``: ``barrier_required`` True

    Args:
        decision: The ControlDecision to validate.

    Returns:
        List of invariant violation descriptions. Empty list means all pass.
    """
    violations: list[str] = []

    if decision.kind in ("dispatch_batch", "dispatch"):
        if not decision.step_ids:
            violations.append(f"{decision.kind}: step_ids must be non-empty, got ()")
        for sid in decision.step_ids:
            if sid not in decision.role_bindings:
                violations.append(
                    f"{decision.kind}: role_bindings missing binding for step_id={sid!r}"
                )
        if decision.kind == "dispatch_batch" and decision.capacity_used <= 0:
            violations.append(
                f"dispatch_batch: capacity_used must be positive, got {decision.capacity_used}"
            )

    elif decision.kind == "resolve":
        if not decision.case_ids:
            violations.append("resolve: case_ids must be non-empty, got ()")

    elif decision.kind == "replan":
        if decision.planner_request is None:
            violations.append("replan: planner_request must be present, got None")

    elif decision.kind == "wait":
        if decision.step_ids:
            violations.append(f"wait: step_ids must be empty, got {decision.step_ids}")
        if decision.case_ids:
            violations.append(f"wait: case_ids must be empty, got {decision.case_ids}")

    elif decision.kind == "done":
        if decision.step_ids:
            violations.append(f"done: step_ids must be empty, got {decision.step_ids}")
        if decision.case_ids:
            violations.append(f"done: case_ids must be empty, got {decision.case_ids}")

    elif decision.kind == "halt":
        if not decision.barrier_required:
            violations.append("halt: barrier_required must be True, got False")

    return violations


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
