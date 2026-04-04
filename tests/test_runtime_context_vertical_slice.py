"""Vertical-slice checks for current orchestration surfaces.

Authority: driver RuntimeContext/loop surfaces were removed; this test validates
the active control+runtime boundary in ``vectl.orchestration``.
"""

from __future__ import annotations

from dataclasses import dataclass

from vectl.models import IsolationMode
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.contracts import CoreSnapshot, RosterSnapshot, RuntimeSnapshot


@dataclass
class _CoreSource:
    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        del agent
        return CoreSnapshot(
            plan_complete=False,
            claimable_step_ids=("phase.step",),
            in_progress_step_ids=(),
            blocked_step_ids=(),
            unresolved_reasons=(),
        )

    def step_isolation(self, step_id: str) -> IsolationMode:
        raise NotImplementedError(f"test source does not implement step_isolation: {step_id}")

    def claim_step(self, step_id: str, agent: str, force: bool = False) -> None:
        raise NotImplementedError(
            f"test source does not implement claim_step: step_id={step_id}, agent={agent}, force={force}"
        )

    def complete_step(self, step_id: str, evidence: str) -> None:
        raise NotImplementedError(
            f"test source does not implement complete_step: step_id={step_id}, evidence={evidence}"
        )

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test source does not implement defer_step: {step_id}")


@dataclass
class _RosterSource:
    def snapshot(self) -> RosterSnapshot:
        return RosterSnapshot(
            available_agents=("python-executor",),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        )


@dataclass
class _RuntimeSource:
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(active_workspaces=(), active_executions=(), stalled_executions=())


def test_plan_aware_control_evaluates_dispatch_from_current_sources() -> None:
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_CoreSource(),
            roster=_RosterSource(),
            runtime=_RuntimeSource(),
        )
    )

    decision = control.evaluate_current()
    assert decision.kind == "dispatch"
    assert decision.step_id == "phase.step"
