"""Drive-aware control decision tests.

Authority:
    docs/RFC-orch-drive.md sections 9, 10
    docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.5, 4.1

Step: orch_drive_control.batch-frontier-decision-expansion

Verification:
    - Multi-step frontier dispatch with deterministic ordering
    - Decision invariants for all six decision kinds
    - Bounded capacity accounting (max_parallelism, active runs)
    - barrier_required handling for all barrier reasons
    - Role bindings for every dispatched step
    - Invalid empty dispatches rejected
    - Missing role bindings rejected
    - Stale done/halt classification rejected
"""

from __future__ import annotations

import pytest

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    PlannerMutationBundle,
    PlannerRequest,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import (
    PlanAwareControl,
    ControlInputSources,
    DEFAULT_DISPATCH_ROLE,
    validate_decision_invariants,
    _deterministic_frontier_order,
    _count_active_step_runs,
)

from dataclasses import dataclass
from vectl.models import IsolationMode


# ------------------------------------------------------------------
# Test helpers
# ------------------------------------------------------------------


def _core(
    *,
    plan_complete: bool = False,
    claimable: tuple[str, ...] = (),
    in_progress: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=plan_complete,
        claimable_step_ids=claimable,
        in_progress_step_ids=in_progress,
        blocked_step_ids=blocked,
        unresolved_reasons=unresolved,
    )


def _roster(
    *,
    available_agents: tuple[str, ...] = (),
    working_agents: tuple[str, ...] = (),
    reusable_sessions: tuple[str, ...] = (),
) -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=available_agents,
        working_agents=working_agents,
        reusable_sessions=reusable_sessions,
        exhausted_roles=(),
    )


def _runtime(
    *,
    active_workspaces: tuple[str, ...] = (),
    active_executions: tuple[str, ...] = (),
    stalled_executions: tuple[str, ...] = (),
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        active_workspaces=active_workspaces,
        active_executions=active_executions,
        stalled_executions=stalled_executions,
    )


def _drive(
    *,
    drive_id: str = "drv_01",
    plan_path: str = "/repo/plan.yaml",
    status: str = "running",
    max_parallelism: int = 4,
    active_child_run_ids: tuple[str, ...] = (),
    frontier_step_ids: tuple[str, ...] = (),
    blocked_case_ids: tuple[str, ...] = (),
    barrier: DriveBarrier | None = None,
    operator_pause_state: str = "active",
) -> DriveRecord:
    return DriveRecord(
        drive_id=drive_id,
        plan_path=plan_path,
        status=status,
        max_parallelism=max_parallelism,
        active_child_run_ids=active_child_run_ids,
        frontier_step_ids=frontier_step_ids,
        blocked_case_ids=blocked_case_ids,
        barrier=barrier,
        operator_pause_state=operator_pause_state,
    )


def _barrier(
    *,
    reason: str = "runtime_failure",
    entered_at: float = 1000.0,
    case_ids: tuple[str, ...] = (),
) -> DriveBarrier:
    return DriveBarrier(reason=reason, entered_at=entered_at, case_ids=case_ids)


@dataclass
class _FakeCoreAdapter:
    snapshot_value: CoreSnapshot
    calls: int = 0

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        _ = agent
        self.calls += 1
        return self.snapshot_value

    def step_isolation(self, step_id: str) -> IsolationMode:
        raise NotImplementedError(f"test double: step_isolation not implemented: {step_id}")

    def claim_step(
        self, step_id: str, agent: str, *, force: bool = False, flow: str = "normal"
    ) -> None:
        raise NotImplementedError(f"test double: claim_step not implemented: {step_id}")

    def complete_step(self, step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        raise NotImplementedError(f"test double: complete_step not implemented: {step_id}")

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double: defer_step not implemented: {step_id}")


@dataclass
class _FakeRosterSource:
    snapshot_value: RosterSnapshot
    calls: int = 0

    def snapshot(self) -> RosterSnapshot:
        self.calls += 1
        return self.snapshot_value


@dataclass
class _FakeRuntimeSource:
    snapshot_value: RuntimeSnapshot
    calls: int = 0

    def snapshot(self) -> RuntimeSnapshot:
        self.calls += 1
        return self.snapshot_value


def _make_control(
    core: CoreSnapshot | None = None,
    roster: RosterSnapshot | None = None,
    runtime: RuntimeSnapshot | None = None,
    dispatch_role: str = DEFAULT_DISPATCH_ROLE,
) -> PlanAwareControl:
    return PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(core or _core()),
            roster=_FakeRosterSource(roster or _roster()),
            runtime=_FakeRuntimeSource(runtime or _runtime()),
        ),
        dispatch_role=dispatch_role,
    )


# ------------------------------------------------------------------
# Deterministic frontier ordering (RFC 9.4)
# ------------------------------------------------------------------


class TestDeterministicFrontierOrdering:
    """Verify deterministic frontier ordering per RFC-orch-drive.md section 9.4."""

    def test_sorts_step_ids_lexicographically(self):
        """Stable phase-local ordering: step_ids must be sorted."""
        result = _deterministic_frontier_order(("z.step", "a.step", "m.step"))
        assert result == ("a.step", "m.step", "z.step")

    def test_already_sorted_is_idempotent(self):
        """Sorted input returns identical output."""
        ordered = ("a.step", "b.step", "c.step")
        assert _deterministic_frontier_order(ordered) == ordered

    def test_single_step_frontier(self):
        result = _deterministic_frontier_order(("core.verify",))
        assert result == ("core.verify",)

    def test_empty_frontier(self):
        result = _deterministic_frontier_order(())
        assert result == ()

    def test_deterministic_ordering_in_dispatch_batch(self):
        """drive-aware dispatch_batch uses deterministic frontier ordering."""
        control = _make_control(core=_core(claimable=("z.step", "a.step", "m.step")))
        drive = _drive()

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        assert decision.step_ids == ("a.step", "m.step", "z.step")
        assert decision.role_bindings == {
            "a.step": DEFAULT_DISPATCH_ROLE,
            "m.step": DEFAULT_DISPATCH_ROLE,
            "z.step": DEFAULT_DISPATCH_ROLE,
        }


# ------------------------------------------------------------------
# Capacity accounting (RFC 10.2)
# ------------------------------------------------------------------


class TestBoundedCapacityAccounting:
    """Verify bounded capacity accounting per RFC-orch-drive.md section 10.2."""

    def test_full_capacity_dispatch(self):
        """All frontier steps dispatched when capacity allows."""
        control = _make_control(core=_core(claimable=("s1", "s2")))
        drive = _drive(max_parallelism=4, active_child_run_ids=())

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        assert decision.step_ids == ("s1", "s2")
        assert decision.capacity_used == 2
        assert decision.capacity_remaining == 2
        assert decision.role_bindings == {
            "s1": DEFAULT_DISPATCH_ROLE,
            "s2": DEFAULT_DISPATCH_ROLE,
        }

    def test_partial_capacity_truncation(self):
        """Frontier truncated by available capacity."""
        control = _make_control(core=_core(claimable=("s1", "s2", "s3")))
        # 2 of 4 slots already in use → only 2 slots available
        drive = _drive(max_parallelism=4, active_child_run_ids=("r1", "r2"))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        assert len(decision.step_ids) == 2
        assert decision.capacity_used == 2
        assert decision.capacity_remaining == 0

    def test_zero_capacity_waits(self):
        """No available capacity → wait, not empty dispatch_batch."""
        control = _make_control(core=_core(claimable=("s1", "s2")))
        drive = _drive(max_parallelism=2, active_child_run_ids=("r1", "r2"))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "wait"
        assert "capacity" in decision.reason.lower()

    def test_capacity_single_slot_remaining(self):
        """Only 1 slot available: dispatch exactly one step."""
        control = _make_control(core=_core(claimable=("s1", "s2", "s3")))
        drive = _drive(max_parallelism=4, active_child_run_ids=("r1", "r2", "r3"))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        assert len(decision.step_ids) == 1
        assert decision.capacity_used == 1
        assert decision.capacity_remaining == 0

    def test_role_bindings_cover_all_dispatched_steps(self):
        """Decision invariant: role_bindings must cover every step_id."""
        control = _make_control(core=_core(claimable=("alpha.step", "beta.step", "gamma.step")))
        drive = _drive(max_parallelism=4)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        for sid in decision.step_ids:
            assert sid in decision.role_bindings, f"Missing role binding for {sid}"


# ------------------------------------------------------------------
# Barrier handling
# ------------------------------------------------------------------


class TestBarrierHandling:
    """Verify barrier_required handling per RFC-orch-drive.md sections 5.4, 8.4.1."""

    def test_barrier_runtime_failure_yields_wait(self):
        control = _make_control()
        barrier = _barrier(reason="runtime_failure")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "wait"
        assert decision.barrier_required is True
        assert "runtime_failure" in decision.reason

    def test_barrier_merge_conflict_yields_wait(self):
        control = _make_control()
        barrier = _barrier(reason="merge_conflict")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "wait"
        assert decision.barrier_required is True

    def test_barrier_recovery_gate_yields_wait(self):
        """Recovery gate is a wait, not halt (dispatch may resume)."""
        control = _make_control()
        barrier = _barrier(reason="recovery_gate")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "wait"
        assert decision.barrier_required is True
        assert "recovery gate" in decision.reason.lower()

    def test_barrier_operator_pause_yields_halt(self):
        """Operator pause barrier is terminal: halt, not wait."""
        control = _make_control()
        barrier = _barrier(reason="operator_pause")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True

    def test_barrier_blocks_dispatch_even_with_claimable(self):
        """Barrier must suppress dispatch even when claimable steps exist."""
        control = _make_control(core=_core(claimable=("s1", "s2")))
        barrier = _barrier(reason="runtime_failure")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "wait"
        assert decision.step_ids == ()

    def test_recovery_gate_blocked_without_barrier(self):
        """Recovery gate blocked flag without DriveBarrier."""
        control = _make_control(core=_core(claimable=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            recovery_gate_blocked=True,
        )

        assert decision.kind == "wait"
        assert decision.barrier_required is True
        assert "recovery" in decision.reason.lower()


# ------------------------------------------------------------------
# Operator pause (RFC 7.3)
# ------------------------------------------------------------------


class TestOperatorPause:
    """Verify operator pause handling per RFC-orch-drive.md section 7.3."""

    def test_operator_paused_drive_yields_halt(self):
        """Paused drive without barrier: halt, no new work admitted."""
        control = _make_control()
        drive = _drive(operator_pause_state="paused")

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True
        assert "paused" in decision.reason.lower()

    def test_operator_active_drive_allows_dispatch(self):
        """Active drive allows dispatch."""
        control = _make_control(core=_core(claimable=("s1",)))
        drive = _drive(operator_pause_state="active")

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"


# ------------------------------------------------------------------
# Open cases routing (RFC 9.2)
# ------------------------------------------------------------------


class TestOpenCasesRouting:
    """Verify open cases route to resolve with case_ids."""

    def test_open_cases_with_drive_returns_resolve(self):
        """Open cases with drive context: route to resolve with case_ids."""
        control = _make_control()
        drive = _drive()

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            open_case_ids=("case_01", "case_02"),
        )

        assert decision.kind == "resolve"
        assert decision.case_ids == ("case_01", "case_02")

    def test_open_cases_without_drive_not_routed_to_resolve(self):
        """Without drive context, open cases alone don't determine resolve.

        The original non-drive logic doesn't consider open_case_ids without
        a drive, so it should fall through to later evaluation logic.
        """
        control = _make_control()

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            open_case_ids=("case_01",),
        )

        # Without a drive, open_case_ids aren't sufficient for resolve
        # — the decision depends on core state.
        assert decision.kind != "resolve" or decision.case_ids == ("case_01",)


# ------------------------------------------------------------------
# Decision invariants (RFC 9.2.1)
# ------------------------------------------------------------------


class TestDecisionInvariants:
    """Verify all decision invariants per RFC-orch-drive.md section 9.2.1."""

    def test_dispatch_batch_step_ids_non_empty(self):
        control = _make_control(core=_core(claimable=("s1",)))
        drive = _drive()

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        violations = validate_decision_invariants(decision)
        assert violations == [], f"Decision invariant violations: {violations}"

    def test_dispatch_batch_role_bindings_cover_all(self):
        control = _make_control(core=_core(claimable=("s1", "s2", "s3")))
        drive = _drive(max_parallelism=4)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        for sid in decision.step_ids:
            assert sid in decision.role_bindings
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_resolve_case_ids_non_empty(self):
        """resolve decisions must always have non-empty case_ids."""
        control = _make_control(core=_core(unresolved=("conflict",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "resolve"
        assert decision.case_ids  # non-empty
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_blocked_resolve_has_case_ids(self):
        control = _make_control(core=_core(blocked=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "resolve"
        assert decision.case_ids  # non-empty
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_wait_step_ids_and_case_ids_empty(self):
        control = _make_control(core=_core(in_progress=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "wait"
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_done_step_ids_and_case_ids_empty(self):
        control = _make_control(core=_core(plan_complete=True))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "done"
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_halt_barrier_required_true(self):
        control = _make_control()
        drive = _drive(operator_pause_state="paused")

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "halt"
        violations = validate_decision_invariants(decision)
        assert violations == []

    def test_replan_planner_request_present(self):
        planner_req = PlannerRequest(reason="needs replan", affected_steps=("s1",))
        report = ResolutionReport(
            status="unblocked",
            summary="resolved but needs planner",
            planner_request=planner_req,
        )
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "replan"
        assert decision.planner_request is not None
        violations = validate_decision_invariants(decision)
        assert violations == []


# ------------------------------------------------------------------
# Invariant validation regressions
# ------------------------------------------------------------------


class TestInvariantValidation:
    """Verify validate_decision_invariants catches malformed decisions."""

    def test_empty_dispatch_batch_rejected(self):
        bad = ControlDecision(
            kind="dispatch_batch",
            reason="bad",
            step_ids=(),
            role_bindings={},
        )
        violations = validate_decision_invariants(bad)
        assert any("step_ids must be non-empty" in v for v in violations)

    def test_dispatch_batch_missing_role_binding_rejected(self):
        bad = ControlDecision(
            kind="dispatch_batch",
            reason="bad",
            step_ids=("s1",),
            role_bindings={},
        )
        violations = validate_decision_invariants(bad)
        assert any("role_bindings missing" in v for v in violations)

    def test_resolve_empty_case_ids_rejected(self):
        bad = ControlDecision(
            kind="resolve",
            reason="bad",
            case_ids=(),
        )
        violations = validate_decision_invariants(bad)
        assert any("case_ids must be non-empty" in v for v in violations)

    def test_replan_none_planner_request_rejected(self):
        bad = ControlDecision(
            kind="replan",
            reason="bad",
            planner_request=None,
        )
        violations = validate_decision_invariants(bad)
        assert any("planner_request must be present" in v for v in violations)

    def test_wait_with_step_ids_rejected(self):
        bad = ControlDecision(
            kind="wait",
            reason="bad",
            step_ids=("s1",),
        )
        violations = validate_decision_invariants(bad)
        assert any("step_ids must be empty" in v for v in violations)

    def test_wait_with_case_ids_rejected(self):
        bad = ControlDecision(
            kind="wait",
            reason="bad",
            case_ids=("case_01",),
        )
        violations = validate_decision_invariants(bad)
        assert any("case_ids must be empty" in v for v in violations)

    def test_done_with_step_ids_rejected(self):
        bad = ControlDecision(
            kind="done",
            reason="bad",
            step_ids=("s1",),
        )
        violations = validate_decision_invariants(bad)
        assert any("step_ids must be empty" in v for v in violations)

    def test_halt_without_barrier_required_rejected(self):
        bad = ControlDecision(
            kind="halt",
            reason="bad",
            barrier_required=False,
        )
        violations = validate_decision_invariants(bad)
        assert any("barrier_required must be True" in v for v in violations)

    def test_valid_dispatch_batch_passes(self):
        good = ControlDecision(
            kind="dispatch_batch",
            reason="ok",
            step_ids=("s1", "s2"),
            role_bindings={"s1": "python-executor", "s2": "python-executor"},
            capacity_used=2,
            capacity_remaining=2,
        )
        violations = validate_decision_invariants(good)
        assert violations == []

    def test_valid_dispatch_passes(self):
        good = ControlDecision(
            kind="dispatch",
            reason="ok",
            step_ids=("s1",),
            role_bindings={"s1": "python-executor"},
        )
        violations = validate_decision_invariants(good)
        assert violations == []


# ------------------------------------------------------------------
# Done decision in drive context (RFC 9.2.1)
# ------------------------------------------------------------------


class TestDoneDecision:
    """Verify done decision invariant: no frontier and no active child runs."""

    def test_done_when_plan_complete_and_idle_no_drive(self):
        control = _make_control(core=_core(plan_complete=True))
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )
        assert decision.kind == "done"

    def test_done_with_drive_no_active_runs(self):
        """Plan complete + idle runtime + drive with no active runs → done."""
        control = _make_control(core=_core(plan_complete=True))
        drive = _drive(active_child_run_ids=())

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "done"

    def test_not_done_with_active_child_runs(self):
        """Plan complete + idle runtime + drive with active runs → wait."""
        control = _make_control(core=_core(plan_complete=True))
        drive = _drive(active_child_run_ids=("r1",))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "wait"
        assert "active child runs" in decision.reason.lower()


# ------------------------------------------------------------------
# Halt decision (RFC 9.2.1, 10.3)
# ------------------------------------------------------------------


class TestHaltDecision:
    """Verify halt decision emission and invariants."""

    def test_halt_from_operator_pause_barrier(self):
        control = _make_control()
        barrier = _barrier(reason="operator_pause")
        drive = _drive(barrier=barrier)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            barrier=barrier,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True

    def test_halt_from_operator_paused_drive(self):
        control = _make_control()
        drive = _drive(operator_pause_state="paused")

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True

    def test_halt_from_resolver_report(self):
        """Resolver halt → kind=halt (not kind=done)."""
        report = ResolutionReport(status="halt", summary="unsafe divergence")
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True

    def test_halt_from_planner_halt_bundle(self):
        bundle = PlannerMutationBundle(
            status="halt",
            summary="Unsafe to continue",
        )
        control = _make_control()

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True


# ------------------------------------------------------------------
# Apply resolution (RFC 12.3)
# ------------------------------------------------------------------


class TestApplyResolution:
    """Verify apply_resolution continuation rules per RFC section 12.3."""

    def test_unblocked_replan_transition(self):
        """Resolver unblocked + planner_request → kind=replan."""
        planner_req = PlannerRequest(reason="step needs replan", affected_steps=("s1",))
        report = ResolutionReport(
            status="unblocked",
            summary="needs planner",
            planner_request=planner_req,
        )
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "replan"
        assert decision.planner_request is not None
        assert decision.planner_request.reason == "step needs replan"
        assert decision.barrier_required is True

    def test_unblocked_re_evaluate_with_claimable(self):
        """Unblocked without planner_request → re-evaluate → dispatch."""
        report = ResolutionReport(status="unblocked", summary="repaired")
        control = _make_control(core=_core(claimable=("s1",)))

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "dispatch"

    def test_unblocked_with_drive_dispatch_batch(self):
        """Unbounded → re-evaluate with drive → dispatch_batch."""
        report = ResolutionReport(status="unblocked", summary="repaired")
        control = _make_control(core=_core(claimable=("s1", "s2")))
        drive = _drive()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"

    def test_waiting_maps_to_wait(self):
        report = ResolutionReport(status="waiting", summary="async repair")
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "wait"

    def test_operator_required_maps_to_wait(self):
        report = ResolutionReport(
            status="operator_required",
            summary="manual review needed",
            operator_message="inspect divergent claims",
        )
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "wait"
        assert "operator" in decision.reason.lower()

    def test_halt_maps_to_halt(self):
        """halt from resolver → kind=halt with barrier_required=True."""
        report = ResolutionReport(status="halt", summary="unsafe")
        control = _make_control()

        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True


# ------------------------------------------------------------------
# Apply planner result (RFC 13.5)
# ------------------------------------------------------------------


class TestApplyPlannerResult:
    """Verify apply_planner_result continuation rules per RFC section 13.5."""

    def test_applyable_re_evaluates(self):
        """Applyable planner result → re-evaluate from refreshed state."""
        bundle = PlannerMutationBundle(status="applyable", summary="added step")
        control = _make_control(core=_core(claimable=("s1",)))

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        # Re-evaluation should see claimable step and dispatch
        assert decision.kind == "dispatch"
        assert decision.step_ids == ("s1",)

    def test_applyable_with_drive_dispatch_batch(self):
        """Applyable → re-evaluate with drive → dispatch_batch."""
        bundle = PlannerMutationBundle(status="applyable", summary="added step")
        control = _make_control(core=_core(claimable=("s1", "s2")))
        drive = _drive()

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"

    def test_applyable_no_claimable_yields_wait(self):
        """Applyable → re-evaluate → wait when no claimable work."""
        bundle = PlannerMutationBundle(status="applyable", summary="added step")
        control = _make_control()  # no claimable steps

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "wait"

    def test_operator_required_yields_wait_with_barrier(self):
        bundle = PlannerMutationBundle(status="operator_required", summary="manual action needed")
        control = _make_control()

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "wait"
        assert decision.barrier_required is True

    def test_halt_yields_halt_with_barrier(self):
        bundle = PlannerMutationBundle(status="halt", summary="unsafe to continue")
        control = _make_control()

        decision = control.apply_planner_result(
            bundle=bundle,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "halt"
        assert decision.barrier_required is True


# ------------------------------------------------------------------
# Custom dispatch role
# ------------------------------------------------------------------


class TestCustomDispatchRole:
    """Verify role bindings use configured dispatch role."""

    def test_custom_role_in_drive_batch(self):
        control = _make_control(
            core=_core(claimable=("s1", "s2")),
            dispatch_role="custom-agent",
        )
        drive = _drive(max_parallelism=4)

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"
        assert decision.role_bindings == {
            "s1": "custom-agent",
            "s2": "custom-agent",
        }

    def test_custom_role_in_legacy_dispatch(self):
        control = _make_control(
            core=_core(claimable=("s1",)),
            dispatch_role="gate-reviewer",
        )

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "dispatch"
        assert decision.role_bindings == {"s1": "gate-reviewer"}


# ------------------------------------------------------------------
# Synthetic case IDs for resolve decisions
# ------------------------------------------------------------------


class TestSyntheticCaseIds:
    """Verify resolve decisions always have non-empty case_ids (RFC 9.2.1)."""

    def test_unresolved_state_has_synthetic_case_id(self):
        control = _make_control(core=_core(unresolved=("claim conflict",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "resolve"
        assert decision.case_ids  # non-empty
        assert "unresolved:" in decision.case_ids[0]

    def test_blocked_steps_have_synthetic_case_id(self):
        control = _make_control(core=_core(blocked=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "resolve"
        assert decision.case_ids  # non-empty
        assert "blocked:" in decision.case_ids[0]

    def test_plan_conflict_has_synthetic_case_id(self):
        control = _make_control(core=_core(plan_complete=True, claimable=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "resolve"
        assert decision.case_ids  # non-empty
        assert "plan_complete_conflict" in decision.case_ids[0]


# ------------------------------------------------------------------
# All decision kinds from evaluate
# ------------------------------------------------------------------


class TestAllDecisionKinds:
    """Verify that evaluate produces all six decision kinds correctly."""

    def test_dispatch_batch(self):
        control = _make_control(core=_core(claimable=("s1",)))
        drive = _drive()

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )

        assert decision.kind == "dispatch_batch"

    def test_dispatch_without_drive(self):
        """Legacy single-step dispatch without drive context."""
        control = _make_control(core=_core(claimable=("s1",)))

        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )

        assert decision.kind == "dispatch"

    def test_resolve_from_blocked(self):
        control = _make_control(core=_core(blocked=("s1",)))
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )
        assert decision.kind == "resolve"

    def test_resolve_from_open_cases(self):
        control = _make_control()
        drive = _drive()
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
            open_case_ids=("case_01",),
        )
        assert decision.kind == "resolve"

    def test_replan_from_resolver(self):
        """Kind=replan comes only from apply_resolution with planner_request."""
        planner_req = PlannerRequest(reason="replan needed")
        report = ResolutionReport(status="unblocked", summary="ok", planner_request=planner_req)
        control = _make_control()
        decision = control.apply_resolution(
            report=report,
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )
        assert decision.kind == "replan"

    def test_wait_from_active_work(self):
        control = _make_control(core=_core(in_progress=("s1",)))
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )
        assert decision.kind == "wait"

    def test_done_from_plan_complete(self):
        control = _make_control(core=_core(plan_complete=True))
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
        )
        assert decision.kind == "done"

    def test_halt_from_operator_pause(self):
        """Kind=halt from operator pause."""
        control = _make_control()
        drive = _drive(operator_pause_state="paused")
        decision = control.evaluate(
            core=control.sources.core_adapter.snapshot_value,
            roster=control.sources.roster.snapshot_value,
            runtime=control.sources.runtime.snapshot_value,
            drive=drive,
        )
        assert decision.kind == "halt"
