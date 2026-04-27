from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    CoreSnapshot,
    DriveBarrier,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.driver import DriveDriver
from vectl.orchestration.run_store import DriveStore


def _core(
    *,
    claimable: tuple[str, ...] = (),
    in_progress: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
) -> CoreSnapshot:
    return CoreSnapshot(
        plan_complete=False,
        claimable_step_ids=claimable,
        in_progress_step_ids=in_progress,
        blocked_step_ids=blocked,
        unresolved_reasons=(),
    )


@dataclass
class _FakeCoreAdapter:
    snapshot_value: CoreSnapshot

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        del agent
        return self.snapshot_value

    def step_isolation(self, step_id: str) -> IsolationMode:
        del step_id
        return "shared"

    def claim_step(
        self, step_id: str, agent: str, *, force: bool = False, flow: str = "normal"
    ) -> None:
        del step_id, agent, force, flow

    def complete_step(self, step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        del step_id, evidence, reconcile_disposition

    def load_step_data_for_dispatch(self, step_id: str):
        raise NotImplementedError(f"test double does not dispatch: {step_id}")

    def defer_step(self, step_id: str) -> None:
        del step_id


@dataclass
class _FakeRosterSource:
    def snapshot(self) -> RosterSnapshot:
        return RosterSnapshot(
            available_agents=(),
            working_agents=(),
            reusable_sessions=(),
            exhausted_roles=(),
        )


@dataclass
class _FakeRuntimeSource:
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            active_workspaces=(),
            active_executions=(),
            stalled_executions=(),
        )


@dataclass
class _FakeResolver:
    cases: list[ResolutionCase]
    report: ResolutionReport | None = None

    def resolve(self, case: ResolutionCase) -> ResolutionReport:
        self.cases.append(case)
        if self.report is not None:
            return self.report
        return ResolutionReport(
            status="operator_required",
            summary="manual resolver fallback was invoked",
            operator_message="manual attention required",
        )


def _make_driver(
    tmp_path: Path,
    *,
    core: CoreSnapshot,
    resolver: _FakeResolver,
) -> DriveDriver:
    core_adapter = _FakeCoreAdapter(core)
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_adapter,
            roster=_FakeRosterSource(),
            runtime=_FakeRuntimeSource(),
        )
    )
    return DriveDriver(
        drive_store=DriveStore(store_root=tmp_path / "drives"),
        core_adapter=core_adapter,
        control=control,
        resolver=resolver,
    )


def test_stale_runtime_barrier_auto_unblocks_without_resolver(tmp_path: Path) -> None:
    resolver = _FakeResolver(cases=[])
    driver = _make_driver(
        tmp_path,
        core=_core(claimable=("step.next",)),
        resolver=resolver,
    )
    start = driver.start_drive(plan_path="/repo/plan.yaml")
    record = driver._drive_store.load_drive(start.drive_id)
    assert record is not None
    driver._drive_store.save_drive(
        replace(
            record,
            status="blocked_operator",
            updated_at=time.time(),
            frontier_step_ids=("step.next",),
            blocked_case_ids=("case-stale",),
            barrier=DriveBarrier(reason="runtime_failure", case_ids=("case-stale",)),
            operator_pause_state="active",
            summary="operator unpause consumed: ready to continue",
        )
    )

    result = driver.run_drive_loop(start.drive_id)

    assert result.status == "running"
    assert result.barrier is None
    assert "auto-unblocked stale runtime barrier" in result.summary
    assert resolver.cases == []
    updated = driver._drive_store.load_drive(start.drive_id)
    assert updated is not None
    assert updated.blocked_case_ids == ()
    assert updated.barrier is None


def test_stale_runtime_barrier_does_not_auto_unblock_paused_drive(tmp_path: Path) -> None:
    resolver = _FakeResolver(cases=[])
    driver = _make_driver(
        tmp_path,
        core=_core(claimable=("step.next",)),
        resolver=resolver,
    )
    start = driver.start_drive(plan_path="/repo/plan.yaml")
    record = driver._drive_store.load_drive(start.drive_id)
    assert record is not None
    driver._drive_store.save_drive(
        replace(
            record,
            status="blocked_operator",
            updated_at=time.time(),
            frontier_step_ids=("step.next",),
            blocked_case_ids=("case-paused",),
            barrier=DriveBarrier(reason="runtime_failure", case_ids=("case-paused",)),
            operator_pause_state="paused",
            summary="operator pause consumed",
        )
    )

    result = driver.run_drive_loop(start.drive_id)

    assert result.status == "blocked_operator"
    assert "resolver requires operator" in result.summary
    assert [case.case_id for case in resolver.cases] == ["case-paused"]


def test_stale_runtime_barrier_does_not_auto_unblock_retry_limit(tmp_path: Path) -> None:
    resolver = _FakeResolver(cases=[])
    driver = _make_driver(
        tmp_path,
        core=_core(claimable=("step.next",)),
        resolver=resolver,
    )
    start = driver.start_drive(plan_path="/repo/plan.yaml")
    record = driver._drive_store.load_drive(start.drive_id)
    assert record is not None
    driver._drive_store.save_drive(
        replace(
            record,
            status="blocked_operator",
            updated_at=time.time(),
            frontier_step_ids=("step.next",),
            blocked_case_ids=("case-retry-limit",),
            barrier=DriveBarrier(reason="runtime_failure", case_ids=("case-retry-limit",)),
            operator_pause_state="active",
            summary="child launch failed for step=step.next; retry limit reached",
        )
    )

    result = driver.run_drive_loop(start.drive_id)

    assert result.status == "blocked_operator"
    assert "resolver requires operator" in result.summary
    assert [case.case_id for case in resolver.cases] == ["case-retry-limit"]


def test_agent_assisted_recovery_opens_case_and_invokes_resolver(tmp_path: Path) -> None:
    resolver = _FakeResolver(
        cases=[],
        report=ResolutionReport(
            status="unblocked",
            summary="agent resolved retry-limit blocker",
            evidence_refs=("resolver:test",),
        ),
    )
    driver = _make_driver(
        tmp_path,
        core=_core(),
        resolver=resolver,
    )
    start = driver.start_drive(plan_path="/repo/plan.yaml")
    record = driver._drive_store.load_drive(start.drive_id)
    assert record is not None
    driver._drive_store.save_drive(
        replace(
            record,
            status="blocked_operator",
            updated_at=time.time(),
            blocked_case_ids=(),
            barrier=None,
            summary="retry limit reached without an existing case",
        )
    )

    result = driver.attempt_agent_assisted_recovery(
        start.drive_id,
        reason="retry limit reached for step.next",
    )

    assert result.status == "running"
    assert "resolver unblocked" in result.summary
    assert len(resolver.cases) == 1
    assert resolver.cases[0].case_source == "runtime_failure"
    updated = driver._drive_store.load_drive(start.drive_id)
    assert updated is not None
    assert updated.blocked_case_ids == ()
    assert updated.barrier is None


def test_agent_assisted_recovery_preserves_operator_pause(tmp_path: Path) -> None:
    resolver = _FakeResolver(cases=[])
    driver = _make_driver(
        tmp_path,
        core=_core(claimable=("step.next",)),
        resolver=resolver,
    )
    start = driver.start_drive(plan_path="/repo/plan.yaml")
    record = driver._drive_store.load_drive(start.drive_id)
    assert record is not None
    driver._drive_store.save_drive(
        replace(
            record,
            status="paused",
            updated_at=time.time(),
            operator_pause_state="paused",
            summary="operator pause consumed",
        )
    )

    result = driver.attempt_agent_assisted_recovery(
        start.drive_id,
        reason="operator paused",
    )

    assert result.status == "paused"
    assert "explicitly operator-paused" in result.summary
    assert resolver.cases == []
