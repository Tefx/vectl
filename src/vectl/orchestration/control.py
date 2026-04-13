"""
Plan-aware orchestration flow.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2
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


@dataclass(frozen=True)
class PlanAwareControl:
    """Deterministic orchestration-flow implementation.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1, 5.1, 5.2, 5.3
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.2, 3.6, 5
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5, 6

    This implementation owns only flow decisions (dispatch/resolve/wait/done).
    It does not perform plan mutations, runtime chores, or resolver reasoning.
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
        # If a barrier is active, drive scheduling must not dispatch.
        # Authority: RFC-orch-drive.md section 5.4, 9.2
        if barrier is not None:
            return ControlDecision(
                kind="wait",
                reason=f"Barrier active: {barrier.reason}",
                barrier_required=True,
            )

        # If recovery gate blocks dispatch, no new work admitted.
        # Authority: RFC-orch-drive.md section 8.4.1
        if recovery_gate_blocked:
            return ControlDecision(
                kind="wait",
                reason="Recovery gate blocks dispatch",
                barrier_required=True,
            )

        # If open cases exist and drive is active, route to resolve.
        # Authority: RFC-orch-drive.md section 9.2
        if open_case_ids and drive is not None:
            return ControlDecision(
                kind="resolve",
                reason=f"Open cases require resolution: {', '.join(open_case_ids)}",
                case_ids=open_case_ids,
            )

        unresolved_reason = _format_unresolved_reason(core)
        if unresolved_reason is not None:
            return ControlDecision(kind="resolve", reason=unresolved_reason)

        if _has_plan_complete_conflict(core):
            return ControlDecision(
                kind="resolve",
                reason=(
                    "Authoritative core snapshot conflict: plan_complete=True while "
                    "claimable/in_progress/blocked still present"
                ),
            )

        if core.plan_complete and _is_runtime_idle(runtime):
            return ControlDecision(kind="done", reason="Plan complete and runtime idle")

        if core.claimable_step_ids:
            # Drive-aware batch dispatch: when a drive is active,
            # return dispatch_batch with the full claimable frontier.
            # Authority: RFC-orch-drive.md section 9.3, 9.4
            if drive is not None:
                step_ids = core.claimable_step_ids
                role_bindings = {step_id: self.dispatch_role for step_id in step_ids}
                active_step_count = len(drive.active_child_run_ids)
                capacity_used = min(len(step_ids), drive.max_parallelism - active_step_count)
                capacity_remaining = drive.max_parallelism - active_step_count - capacity_used
                batch_ids = step_ids[:capacity_used] if capacity_used > 0 else ()
                batch_bindings = {sid: role_bindings[sid] for sid in batch_ids}
                return ControlDecision(
                    kind="dispatch_batch",
                    reason="Claimable work available (drive-aware)",
                    step_ids=batch_ids,
                    role_bindings=batch_bindings,
                    capacity_used=capacity_used,
                    capacity_remaining=max(0, capacity_remaining),
                )
            step_id = core.claimable_step_ids[0]
            return ControlDecision(
                kind="dispatch",
                reason="Claimable work available",
                step_ids=(step_id,),
                role_bindings={step_id: self.dispatch_role},
            )

        if _has_active_work(core=core, runtime=runtime):
            return ControlDecision(kind="wait", reason="Execution already in progress")

        if core.blocked_step_ids:
            blocked_steps = ", ".join(core.blocked_step_ids)
            return ControlDecision(
                kind="resolve",
                reason=f"Blocked steps require resolution: {blocked_steps}",
            )

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

        return ControlDecision(
            kind="done", reason=f"Resolver halted orchestration: {report.summary}"
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
            return ControlDecision(
                kind="dispatch_batch",
                reason=f"Planner mutations applied: {bundle.summary}",
                step_ids=(),
            )

        if bundle.status == "operator_required":
            return ControlDecision(
                kind="wait",
                reason=f"Planner requires operator intervention: {bundle.summary}",
                barrier_required=True,
            )

        # bundle.status == "halt"
        return ControlDecision(
            kind="halt",
            reason=f"Planner halted: {bundle.summary}",
            barrier_required=True,
        )


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
]
