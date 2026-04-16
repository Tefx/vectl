"""Integration tests for control isolation propagation.

Authority:
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.3, 6, 10
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 7
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vectl.io import load_plan_definition, save_plan
from vectl.models import IsolationMode, Phase, Plan, Step
from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.core_adapter import PlanCoreAdapter
from vectl.orchestration.roster import Roster


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


@dataclass
class _FakeCoreAdapter:
    """Test double for core adapter with configurable snapshot."""

    snapshot_value: CoreSnapshot
    calls: int = 0

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        _ = agent
        self.calls += 1
        return self.snapshot_value

    def step_isolation(self, step_id: str) -> IsolationMode:
        raise NotImplementedError(f"test double: step_isolation({step_id})")

    def claim_step(
        self,
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: str = "normal",
    ) -> None:
        _ = flow
        raise NotImplementedError(f"test double: claim_step({step_id}, {agent})")

    def complete_step(
        self,
        step_id: str,
        evidence: str,
        *,
        reconcile_disposition: str,
    ) -> None:
        _ = reconcile_disposition
        raise NotImplementedError(f"test double: complete_step({step_id})")

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double: defer_step({step_id})")


@dataclass
class _FakeRosterSource:
    """Test double for roster snapshot source."""

    snapshot_value: RosterSnapshot
    calls: int = 0

    def snapshot(self) -> RosterSnapshot:
        self.calls += 1
        return self.snapshot_value


@dataclass
class _FakeRuntimeSource:
    """Test double for runtime snapshot source."""

    snapshot_value: RuntimeSnapshot
    calls: int = 0

    def snapshot(self) -> RuntimeSnapshot:
        self.calls += 1
        return self.snapshot_value


# === Dispatch/Resolve/Wait/Done Behavior Tests ===


def test_dispatch_decision_from_authoritative_core_snapshot() -> None:
    """Verify dispatch decisions flow from authoritative core data."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("phase.step",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("phase.step",)
    assert decision.role_bindings == {"phase.step": "python-executor"}
    assert "Claimable work available" in decision.reason


def test_wait_decision_when_in_progress_blocks_dispatch() -> None:
    """Verify wait decision when execution already active."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(in_progress=("phase.active",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime(active_executions=("exec-1",))),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "wait"
    assert "in progress" in decision.reason.lower()


def test_resolve_decision_for_blocked_steps_without_dispatch_path() -> None:
    """Verify resolve decision for blocked steps."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(blocked=("phase.blocked",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "resolve"
    assert "phase.blocked" in decision.reason


def test_resolve_decision_preserves_unresolved_truth_from_core() -> None:
    """Regression: unresolved truth must route to resolve instead of dispatch."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(
                _core(
                    claimable=("phase.step",),
                    unresolved=("dependency cycle detected",),
                )
            ),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "resolve"
    assert decision.step_ids == ()
    assert "unresolved" in decision.reason.lower()


def test_done_decision_when_plan_complete_and_runtime_idle() -> None:
    """Verify done decision when plan complete and no active work."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(plan_complete=True)),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "done"
    assert "complete" in decision.reason.lower()


def test_resolve_conflict_when_plan_complete_with_remaining_work() -> None:
    """Verify resolve decision for plan_complete conflict with remaining work."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(
                _core(
                    plan_complete=True,
                    claimable=("phase.straggler",),
                )
            ),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "resolve"
    assert "conflict" in decision.reason.lower()


# === Resolution Report Mapping Tests ===


def test_resolution_unblocked_re_evaluate_from_refreshed_state() -> None:
    """Verify resolution unblocked triggers re-evaluation."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("phase.step",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="unblocked", summary="blocked step resolved")
    )

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("phase.step",)


def test_resolution_waiting_maps_to_wait() -> None:
    """Verify resolution waiting status maps to wait decision."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="waiting", summary="awaiting external repair")
    )

    assert decision.kind == "wait"
    assert "awaiting external repair" in decision.reason


def test_resolution_operator_required_maps_to_wait() -> None:
    """Verify resolution operator_required status maps to wait."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(
            status="operator_required",
            summary="manual adjudication required",
            operator_message="Please review claim conflict",
        )
    )

    assert decision.kind == "wait"
    assert "operator" in decision.reason.lower()


def test_resolution_halt_maps_to_halt() -> None:
    """Verify resolution halt status maps to halt decision with barrier_required.

    Authority: RFC-orch-drive.md section 9.2.1 (halt invariant), section 10.3
    (terminal states), section 12.2 (resolver output contract).

    halt means the barrier is terminal and no new work may be admitted, which
    is distinct from done (normal completion).
    """
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="halt", summary="unsafe divergence detected")
    )

    assert decision.kind == "halt"
    assert "halted" in decision.reason.lower()
    assert decision.barrier_required is True


# === Isolation Semantics Propagation Tests ===


def test_authoritative_core_adapter_propagates_isolation_from_plan(
    tmp_path: Path,
) -> None:
    """Verify PlanCoreAdapter reads isolation mode from authoritative plan."""
    plan = Plan(
        project="isolation-test",
        phases=[
            Phase(
                id="phase",
                name="Phase",
                steps=[
                    Step(
                        id="phase.default",
                        name="Default",
                        isolation=IsolationMode.DEFAULT,
                    ),
                    Step(
                        id="phase.workspace",
                        name="Workspace",
                        isolation=IsolationMode.WORKSPACE,
                    ),
                    Step(
                        id="phase.independent",
                        name="Independent",
                        isolation=IsolationMode.INDEPENDENT,
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    adapter = PlanCoreAdapter(plan_path)

    assert adapter.step_isolation("phase.default") == IsolationMode.DEFAULT
    assert adapter.step_isolation("phase.workspace") == IsolationMode.WORKSPACE
    assert adapter.step_isolation("phase.independent") == IsolationMode.INDEPENDENT


def test_authoritative_core_snapshot_derived_from_official_surfaces(
    tmp_path: Path,
) -> None:
    """Verify CoreSnapshot is derived from official vectl core read surfaces."""
    plan = Plan(
        project="authority-test",
        phases=[
            Phase(
                id="phase",
                name="Phase",
                steps=[
                    Step(id="phase.ready", name="Ready"),
                    Step(
                        id="phase.blocked",
                        name="Blocked",
                        depends_on=["phase.ready"],
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    adapter = PlanCoreAdapter(plan_path)
    snapshot = adapter.snapshot()

    # Claims that claimable steps come from core.get_next_steps
    assert snapshot.claimable_step_ids == ("phase.ready",)

    # Claims that blocked steps are derived from plan semantics
    assert snapshot.blocked_step_ids == ("phase.blocked",)

    # Claims that plan_complete derives from step status
    assert snapshot.plan_complete is False


def test_roster_honors_independent_isolation_no_reuse() -> None:
    """Verify roster respects INDEPENDENT isolation (no session reuse)."""
    from vectl.orchestration.contracts import WorkLease

    roster = Roster()
    lease = WorkLease(
        role="python-executor",
        runner="claude",
        agent_id="agent-1",
        session_id="ses-123",
    )
    roster.register(lease, expires_at=9999999999.0)

    # DEFAULT isolation allows reuse
    claim_default = roster.claim("python-executor", isolation=IsolationMode.DEFAULT)
    assert claim_default == lease

    # Re-register for independent test
    roster.register(lease, expires_at=9999999999.0)

    # INDEPENDENT isolation forbids reuse
    claim_independent = roster.claim("python-executor", isolation=IsolationMode.INDEPENDENT)
    assert claim_independent is None


# === Legacy Behavior Equivalence Tests ===


def test_control_evaluates_same_decision_paths_as_legacy_loop_design() -> None:
    """Verify control decisions match legacy loop flow decisions.

    This tests the documented decomposition from LEGACY_LOOP_SURFACE_SPLIT.
    Control owns dispatch/resolve/wait/done flow decisions.
    """
    # Legacy behavior: when claimable work exists, dispatch it
    control_dispatch = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("work.item",))),
            roster=_FakeRosterSource(_roster(available_agents=("runner",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )
    assert control_dispatch.evaluate_current().kind == "dispatch"

    # Legacy behavior: when work in progress, wait for completion
    control_wait = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(in_progress=("work.active",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime(active_executions=("exec-1",))),
        )
    )
    assert control_wait.evaluate_current().kind == "wait"

    # Legacy behavior: when blocked/unresolved, route to resolver
    control_resolve = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(blocked=("work.blocked",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )
    assert control_resolve.evaluate_current().kind == "resolve"

    # Legacy behavior: when plan complete and idle, orchestration done
    control_done = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(plan_complete=True)),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )
    assert control_done.evaluate_current().kind == "done"


def test_all_authoritative_snapshots_read_once_per_evaluation() -> None:
    """Verify control reads each source exactly once per evaluate_current call."""
    fake_core = _FakeCoreAdapter(_core(claimable=("phase.step",)))
    fake_roster = _FakeRosterSource(_roster(available_agents=("executor",)))
    fake_runtime = _FakeRuntimeSource(_runtime())

    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=fake_core,
            roster=fake_roster,
            runtime=fake_runtime,
        )
    )

    _ = control.evaluate_current()

    assert fake_core.calls == 1, "Core snapshot read once"
    assert fake_roster.calls == 1, "Roster snapshot read once"
    assert fake_runtime.calls == 1, "Runtime snapshot read once"
