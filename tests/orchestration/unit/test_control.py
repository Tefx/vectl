"""Runtime behavior tests for orchestration control decisions.

Authority:
    docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1, 5, 6
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.2, 3.6, 5
    docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3, 5, 6
"""

from __future__ import annotations

from dataclasses import dataclass

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import (
    DEFAULT_DISPATCH_ROLE,
    ControlInputSources,
    PlanAwareControl,
)
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
    snapshot_value: CoreSnapshot
    calls: int = 0

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        _ = agent
        self.calls += 1
        return self.snapshot_value

    def step_isolation(self, step_id: str) -> IsolationMode:
        raise NotImplementedError(f"test double does not implement step_isolation: {step_id}")

    def claim_step(
        self,
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: str = "normal",
    ) -> None:
        _ = force
        _ = flow
        raise NotImplementedError(
            f"test double does not implement claim_step: step_id={step_id}, agent={agent}"
        )

    def complete_step(
        self,
        step_id: str,
        evidence: str,
        *,
        reconcile_disposition: str,
    ) -> None:
        _ = reconcile_disposition
        raise NotImplementedError(
            f"test double does not implement complete_step: step_id={step_id}, evidence={evidence}"
        )


    def load_step_data_for_dispatch(self, step_id: str):
        raise NotImplementedError(
            f"test double does not implement load_step_data_for_dispatch: step_id={step_id}"
        )

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double does not implement defer_step: step_id={step_id}")


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


def test_evaluate_dispatches_claimable_step_with_configured_role() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("core.impl",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("core.impl",)
    assert decision.role_bindings == {"core.impl": "python-executor"}


def test_roster_none_claim_is_not_interpreted_as_plan_blockage() -> None:
    """R24 behavioral proof: no reusable resource does not force resolve path."""
    roster_component = Roster()
    claim = roster_component.claim("python-executor")
    assert claim is None

    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("core.impl",))),
            roster=_FakeRosterSource(roster_component.snapshot()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("core.impl",)
    assert decision.role_bindings == {"core.impl": DEFAULT_DISPATCH_ROLE}


def test_evaluate_ignores_roster_available_agent_order_for_dispatch_role() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("core.impl",))),
            roster=_FakeRosterSource(
                _roster(available_agents=("gate-reviewer", "python-executor"))
            ),
            runtime=_FakeRuntimeSource(_runtime()),
        ),
        dispatch_role="python-executor",
    )

    decision = control.evaluate_current()

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("core.impl",)
    assert decision.role_bindings == {"core.impl": "python-executor"}


def test_evaluate_waits_when_execution_is_already_active() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(in_progress=("core.impl",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime(active_executions=("exec-1",))),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "wait"
    assert "in progress" in decision.reason.lower()


def test_evaluate_returns_done_when_plan_complete_and_runtime_idle() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(plan_complete=True)),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "done"


def test_evaluate_unresolved_reasons_yield_resolve_not_dispatch_regression() -> None:
    """Regression: unresolved truth must route to resolve instead of guessing."""
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(
                _core(
                    claimable=("core.impl",),
                    unresolved=("claim graph conflict",),
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


def test_evaluate_blocked_without_dispatch_path_yields_resolve() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(blocked=("core.blocked",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "resolve"
    assert "core.blocked" in decision.reason


def test_apply_resolution_unblocked_re_evaluates_from_refreshed_state() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("core.impl",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="unblocked", summary="retry normal flow")
    )

    assert decision.kind == "dispatch"
    assert decision.step_ids == ("core.impl",)


def test_apply_resolution_waiting_maps_to_wait() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="waiting", summary="awaiting asynchronous repair")
    )

    assert decision.kind == "wait"
    assert "awaiting asynchronous repair" in decision.reason


def test_apply_resolution_operator_required_maps_to_wait() -> None:
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
            summary="manual adjudication needed",
            operator_message="please inspect claim divergence",
        )
    )

    assert decision.kind == "wait"
    assert "operator" in decision.reason.lower()


def test_apply_resolution_halt_maps_to_halt() -> None:
    """Resolver halt produces kind=halt with barrier_required=True.

    Authority: RFC-orch-drive.md section 9.2.1 (halt invariant),
    section 10.3 (terminal states), section 12.2 (resolver output contract).

    halt means the barrier is terminal and no new work may be admitted.
    The prior mapping to kind='done' was incorrect.
    """
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core()),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(
        ResolutionReport(status="halt", summary="unsafe divergence")
    )

    assert decision.kind == "halt"
    assert "halted" in decision.reason.lower()
    assert decision.barrier_required is True


def test_evaluate_current_reads_all_sources_once() -> None:
    fake_core = _FakeCoreAdapter(_core(claimable=("core.impl",)))
    fake_roster = _FakeRosterSource(_roster(available_agents=("python-executor",)))
    fake_runtime = _FakeRuntimeSource(_runtime())
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=fake_core,
            roster=fake_roster,
            runtime=fake_runtime,
        )
    )

    _ = control.evaluate_current()

    assert fake_core.calls == 1
    assert fake_roster.calls == 1
    assert fake_runtime.calls == 1
