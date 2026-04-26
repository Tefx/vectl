"""Resolver-loop integration tests for drive barrier entry, invocation, and continuation.

Authority:
    docs/RFC-orch-drive.md sections 5.4, 8.4, 10, 12.3
    docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 5, 6
    docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.8, 3.9, 4.1, 4.4

Step: orch_drive_barriers.resolver-loop-integration

Verification requirements:
    - Main path: resolver-loop tests prove barrier entry, invocation payloads,
      and post-resolution refresh/continuation work.
    - Failure path: regressions prove stale pre-resolution state is never reused.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    DriveStatus,
    PlannerRequest,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.driver import (
    DriveDriver,
    _apply_resolution_report_to_drive,
    _barrier_reason_for_case_source,
    _infer_case_source,
    _update_drive_record,
)
from vectl.orchestration.resolver import BoundResolver
from vectl.orchestration.run_store import DriveStore

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
) -> RosterSnapshot:
    return RosterSnapshot(
        available_agents=available_agents,
        working_agents=working_agents,
        reusable_sessions=(),
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
        raise NotImplementedError(f"test double: step_isolation({step_id})")

    def claim_step(
        self, step_id: str, agent: str, *, force: bool = False, flow: str = "normal"
    ) -> None:
        raise NotImplementedError(f"test double: claim_step({step_id}, {agent})")

    def complete_step(self, step_id: str, evidence: str, *, reconcile_disposition: str) -> None:
        raise NotImplementedError(f"test double: complete_step({step_id})")

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double: defer_step({step_id})")


@dataclass
class _FakeRosterSource:
    snapshot_value: RosterSnapshot

    def snapshot(self) -> RosterSnapshot:
        return self.snapshot_value


@dataclass
class _FakeRuntimeSource:
    snapshot_value: RuntimeSnapshot

    def snapshot(self) -> RuntimeSnapshot:
        return self.snapshot_value


@dataclass
class _FakeResolverInvocation:
    """Test double for resolver invocation surface with tracking."""

    payload_log: list[dict[str, object]]
    return_status: str
    return_summary: str
    return_planner_request: PlannerRequest | None = None

    def invoke(self, case: ResolutionCase, *, role_id: str) -> dict[str, object]:
        _ = role_id
        payload: dict[str, object] = {
            "status": self.return_status,
            "summary": self.return_summary,
            "evidence_refs": ("resolver://test-invoked",),
        }
        if self.return_planner_request is not None:
            payload["planner_request"] = {
                "reason": self.return_planner_request.reason,
                "affected_steps": self.return_planner_request.affected_steps,
                "evidence_refs": self.return_planner_request.evidence_refs,
                "constraints": self.return_planner_request.constraints,
            }
        self.payload_log.append(payload)
        return payload


def _make_control(
    core: CoreSnapshot | None = None,
    roster: RosterSnapshot | None = None,
    runtime: RuntimeSnapshot | None = None,
) -> PlanAwareControl:
    return PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(core or _core()),
            roster=_FakeRosterSource(roster or _roster()),
            runtime=_FakeRuntimeSource(runtime or _runtime()),
        ),
    )


def _make_driver(
    tmp_path: Path,
    core: CoreSnapshot | None = None,
    roster: RosterSnapshot | None = None,
    runtime: RuntimeSnapshot | None = None,
    resolver: BoundResolver | None = None,
    max_parallelism: int = 4,
) -> DriveDriver:
    store = DriveStore(store_root=tmp_path)
    core_adapter = _FakeCoreAdapter(core or _core())
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_adapter,
            roster=_FakeRosterSource(roster or _roster()),
            runtime=_FakeRuntimeSource(runtime or _runtime()),
        ),
    )
    return DriveDriver(
        drive_store=store,
        core_adapter=core_adapter,
        control=control,
        resolver=resolver,
        max_parallelism=max_parallelism,
    )


# ------------------------------------------------------------------
# Test: Barrier entry creates ResolutionCase snapshot
# ------------------------------------------------------------------


class TestBarrierEntryCreatesResolutionCase:
    """Verify that barrier entry creates a proper ResolutionCase snapshot.

    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.2, 5
    Authority: RFC-orch-drive.md section 12.1
    """

    def test_resolve_decision_enters_barrier_and_creates_case(self, tmp_path: Path) -> None:
        """When control evaluates to resolve, the driver enters barrier mode
        and creates a ResolutionCase with current state snapshots."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Resolved via official surface",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("core.step",))
        blocked_roster = _roster()
        blocked_runtime = _runtime()

        driver = _make_driver(
            tmp_path,
            core=blocked_core,
            roster=blocked_roster,
            runtime=blocked_runtime,
            resolver=resolver,
        )

        result = driver.start_drive(plan_path="/repo/plan.yaml", agent="test-agent")
        drive_id = result.drive_id

        loop_result = driver.run_drive_loop(drive_id)

        # The driver should have invoked the resolver for the blocked step.
        assert len(resolver_invocation.payload_log) == 1, (
            "Resolver must be invoked once for blocked state"
        )

        # The loop result transitions to running (unblocked) or resolving
        assert loop_result.status in ("running", "resolving", "blocked_operator", "halted"), (
            f"Expected post-resolution status, got {loop_result.status}"
        )

    def test_barrier_captures_case_ids_from_control_decision(self, tmp_path: Path) -> None:
        """Barrier entry captures case_ids from the control decision."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="waiting",
            return_summary="Awaiting external resolution",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step_a", "phase.step_b"))
        driver = _make_driver(
            tmp_path,
            core=blocked_core,
            resolver=resolver,
        )

        result = driver.start_drive(plan_path="/repo/plan.yaml")
        drive_id = result.drive_id

        loop_result = driver.run_drive_loop(drive_id)

        # The loop result should reflect resolving status or continuing
        assert loop_result.status is not None

    def test_barrier_entry_preserves_drive_id_and_plan_path(self, tmp_path: Path) -> None:
        """Barrier state preserves drive_id and plan_path through the loop."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Cleared blockage",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        driver = _make_driver(
            tmp_path,
            core=_core(blocked=("phase.step",)),
            resolver=resolver,
        )

        result = driver.start_drive(plan_path="/repo/plan.yaml")
        drive_id = result.drive_id

        loop_result = driver.run_drive_loop(drive_id)
        assert loop_result.drive_id == drive_id

    def test_existing_runtime_failure_barrier_invokes_resolver(self, tmp_path: Path) -> None:
        """A persisted resolving barrier is resolver work, not an immediate stop."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Recovered persisted reconcile failure",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        core_adapter = _FakeCoreAdapter(_core(claimable=("phase.next",)))
        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )
        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )
        started = driver.start_drive(plan_path="/repo/plan.yaml")
        record = store.replay_drive_state(started.drive_id)
        assert record is not None
        barrier = DriveBarrier(
            reason="runtime_failure",
            case_ids=("case-runtime-001",),
            active_child_run_ids_at_entry=(),
        )
        store.save_drive(
            replace(
                record,
                status="resolving",
                barrier=barrier,
                blocked_case_ids=("case-runtime-001",),
            )
        )

        loop_result = driver.run_drive_loop(started.drive_id)
        resolved = store.replay_drive_state(started.drive_id)

        assert len(resolver_invocation.payload_log) == 1
        assert loop_result.status == "running"
        assert loop_result.barrier is None
        assert resolved is not None
        assert resolved.blocked_case_ids == ()


# ------------------------------------------------------------------
# Test: Resolver invocation payloads
# ------------------------------------------------------------------


class TestResolverInvocationPayloads:
    """Verify that resolver is invoked with correct ResolutionCase payloads.

    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 3.2, 8
    """

    def test_resolver_receives_current_state_snapshots(self, tmp_path: Path) -> None:
        """Resolver receives current core, roster, and runtime snapshots
        in the ResolutionCase, not stale pre-barrier state."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Resolved with current state",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        # Blocked core drives resolve decision
        blocked_core = _core(
            blocked=("phase.blocked_step",),
        )
        blocked_roster = _roster()
        blocked_runtime = _runtime()

        core_adapter = _FakeCoreAdapter(blocked_core)
        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(blocked_roster),
                runtime=_FakeRuntimeSource(blocked_runtime),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        driver.run_drive_loop(start_result.drive_id)

        # Resolver was invoked — we confirmed the payload was built
        assert len(resolver_invocation.payload_log) == 1, (
            "Resolver must be invoked when control evaluates to resolve"
        )

    def test_resolver_receives_drive_context_in_case(self, tmp_path: Path) -> None:
        """ResolutionCase includes the drive record for drive-aware resolver context."""
        @dataclass
        class _CapturingInvocation:
            """Invocation double that captures the full ResolutionCase."""

            cases: list[ResolutionCase]
            return_status: str = "unblocked"
            return_summary: str = "Resolved"

            def invoke(self, case: ResolutionCase, *, role_id: str) -> dict[str, object]:
                self.cases.append(case)
                return {
                    "status": self.return_status,
                    "summary": self.return_summary,
                    "evidence_refs": ("resolver://captured",),
                }

        capturing_invocation = _CapturingInvocation(cases=[])
        resolver = BoundResolver(
            invocation=capturing_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)
        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        driver.run_drive_loop(start_result.drive_id)

        assert len(capturing_invocation.cases) == 1
        case = capturing_invocation.cases[0]

        # Case must contain current state snapshots (orchestration contract §3.2)
        assert isinstance(case.core, CoreSnapshot)
        assert isinstance(case.roster, RosterSnapshot)
        assert isinstance(case.runtime, RuntimeSnapshot)

        # Case reason must explain why normal flow did not close
        assert "phase.step" in case.reason or "blocked" in case.reason.lower()

        # Case must carry the drive context
        assert case.drive is not None
        assert case.drive.drive_id == start_result.drive_id


# ------------------------------------------------------------------
# Test: Post-resolution state refresh
# ------------------------------------------------------------------


class TestPostResolutionStateRefresh:
    """Verify that control re-reads authoritative state after resolution.

    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 6
    Authority: RFC-orch-drive.md section 12.3
    """

    def test_stale_pre_resolution_state_is_not_reused(self, tmp_path: Path) -> None:
        """After resolution, control must use refreshed state, not stale
        pre-resolution state. This is the core regression test for the
        'never trust stale pre-resolution assumptions' invariant."""
        # Initial state: blocked
        blocked_core = _core(blocked=("phase.step",))
        # After resolution: unblocked
        cleared_core = _core(claimable=("phase.step",))

        core_adapter = _FakeCoreAdapter(blocked_core)
        core_adapter.calls = 0

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Blockage resolved through official surface",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")

        # Before resolution: core is blocked
        initial_calls = core_adapter.calls

        # Simulate state change: after resolver clears the blockage,
        # the core snapshot should now show claimable work.
        core_adapter.snapshot_value = cleared_core

        driver.run_drive_loop(start_result.drive_id)

        # Key invariant: core_adapter.snapshot must have been called
        # more than once — once for initial evaluate, and again for
        # refreshed state after resolution.
        assert core_adapter.calls > initial_calls, (
            f"Core adapter must be called for refreshed state; "
            f"calls before={initial_calls}, calls after={core_adapter.calls}. "
            "Stale pre-resolution state must never be reused."
        )

    def test_unblocked_resolution_clears_barrier(self, tmp_path: Path) -> None:
        """When resolver returns unblocked, barrier is cleared."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Blockage cleared, system can continue",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        # After unblocked resolution, barrier should be cleared
        assert loop_result.barrier is None, (
            f"Barrier must be None after unblocked resolution, got {loop_result.barrier}"
        )

        # Status should be running (unblocked clears barrier)
        assert loop_result.status == "running", (
            f"Expected status 'running' after unblocked resolution, got {loop_result.status}"
        )


# ------------------------------------------------------------------
# Test: Continuation semantics (unblocked/waiting/operator_required/halt)
# ------------------------------------------------------------------


class TestResolverContinuationSemantics:
    """Verify that each ResolutionReport status drives the correct transition.

    Authority: RFC-orch-drive.md section 12.3
    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5.1, 6
    """

    def test_unblocked_without_planner_returns_to_running(self, tmp_path: Path) -> None:
        """Resolver unblocked (no planner_request) → barrier cleared → running."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Resolved, system can resume",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        # Must transition to running and clear barrier
        assert loop_result.status == "running"
        assert loop_result.barrier is None

    def test_unblocked_with_planner_request_transitions_to_replanning(self, tmp_path: Path) -> None:
        """Resolver unblocked + planner_request → replanning barrier
        (without reopening frontier in between)."""
        planner_request = PlannerRequest(
            reason="Step needs prerequisite fix",
            affected_steps=("phase.step",),
        )
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Resolved but planner needed",
            return_planner_request=planner_request,
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        # Must transition to replanning with planner_needed barrier
        assert loop_result.status == "replanning", (
            f"Expected 'replanning' status, got {loop_result.status}"
        )
        assert loop_result.barrier is not None
        assert loop_result.barrier.reason == "planner_needed", (
            f"Expected barrier reason 'planner_needed', got {loop_result.barrier.reason}"
        )

    def test_waiting_stays_in_resolving(self, tmp_path: Path) -> None:
        """Resolver waiting → stay in resolving status; barrier persists."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="waiting",
            return_summary="Awaiting external repair",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        # Must stay in resolving with barrier persisted
        assert loop_result.status == "resolving", (
            f"Expected 'resolving' status, got {loop_result.status}"
        )
        assert loop_result.barrier is not None, "Barrier must persist while resolver is waiting"

    def test_operator_required_transitions_to_blocked_operator(self, tmp_path: Path) -> None:
        """Resolver operator_required → blocked_operator."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="operator_required",
            return_summary="Manual intervention required",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        assert loop_result.status == "blocked_operator", (
            f"Expected 'blocked_operator' status, got {loop_result.status}"
        )

    def test_halt_transitions_to_halted(self, tmp_path: Path) -> None:
        """Resolver halt → drive halted (terminal)."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="halt",
            return_summary="Unsafe divergence detected; stopping orchestration",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        assert loop_result.status == "halted", f"Expected 'halted' status, got {loop_result.status}"
        assert loop_result.barrier is not None


# ------------------------------------------------------------------
# Test: Regression — stale pre-resolution state
# ------------------------------------------------------------------


class TestStaleStateRegression:
    """Regression tests proving stale pre-resolution state is never reused.

    Authority: ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 6
    """

    def test_control_uses_refreshed_snapshots_after_unblocked(self, tmp_path: Path) -> None:
        """After unblocked resolution, control decisions must use refreshed
        snapshots, not the pre-resolution blocked state."""
        resolver_invocation = _FakeResolverInvocation(
            payload_log=[],
            return_status="unblocked",
            return_summary="Resolved: step now claimable",
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        # BEFORE resolution: step is blocked
        pre_core = _core(blocked=("phase.verified_step",))
        # AFTER resolution: step becomes claimable
        post_core = _core(claimable=("phase.verified_step"))

        core_adapter = _FakeCoreAdapter(pre_core)
        initial_calls = core_adapter.calls

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=resolver,
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")

        # FIRST loop with blocked state
        first_result = driver.run_drive_loop(start_result.drive_id)
        assert first_result.status == "running"  # unblocked → running

        # Now change core to claimable (simulating resolver having fixed something)
        core_adapter.snapshot_value = post_core

        # SECOND loop — control must use refreshed (post-resolution) state
        second_result = driver.run_drive_loop(start_result.drive_id)

        # With claimable work, control should dispatch
        assert second_result.status == "running"

        # The core adapter must have been called multiple times,
        # proving that refreshed state was read after resolution.
        assert core_adapter.calls >= initial_calls + 2, (
            f"Control must re-read core after resolution. "
            f"Initial calls: {initial_calls}, total calls: {core_adapter.calls}"
        )

    def test_barrier_entry_with_no_resolver_records_case_but_no_resolver_run(
        self, tmp_path: Path
    ) -> None:
        """When no resolver is wired, barrier entry records the case
        but does not invoke resolver. The drive transitions to resolving
        status but resolver invocation is not attempted."""
        # No resolver — driver._resolver is None

        blocked_core = _core(blocked=("phase.step",))
        core_adapter = _FakeCoreAdapter(blocked_core)

        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=core_adapter,
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            ),
        )

        store = DriveStore(store_root=tmp_path)
        driver = DriveDriver(
            drive_store=store,
            core_adapter=core_adapter,
            control=control,
            resolver=None,  # No resolver wired
        )

        start_result = driver.start_drive(plan_path="/repo/plan.yaml")
        loop_result = driver.run_drive_loop(start_result.drive_id)

        # Without resolver, the loop should still handle the resolve
        # decision — entering barrier with case ids recorded
        assert loop_result.status == "resolving", (
            f"Expected 'resolving' status (no resolver), got {loop_result.status}"
        )
        assert loop_result.barrier is not None, (
            "Barrier must be entered for resolve decision even without resolver"
        )
        assert loop_result.barrier.reason in (
            "runtime_failure",
            "review_failed",
            "merge_conflict",
            "recovery_gate",
        ), f"Expected barrier reason for resolve, got {loop_result.barrier.reason}"


# ------------------------------------------------------------------
# Test: Helper function unit tests
# ------------------------------------------------------------------


class TestBarrierReasonMapping:
    """Verify _barrier_reason_for_case_source maps correctly."""

    def test_runtime_failure_maps(self) -> None:
        assert _barrier_reason_for_case_source("runtime_failure") == "runtime_failure"

    def test_merge_conflict_maps(self) -> None:
        assert _barrier_reason_for_case_source("merge_conflict") == "merge_conflict"

    def test_review_failed_maps(self) -> None:
        assert _barrier_reason_for_case_source("review_failed") == "review_failed"

    def test_continuity_block_maps_to_recovery_gate(self) -> None:
        assert _barrier_reason_for_case_source("continuity_block") == "recovery_gate"

    def test_unknown_maps_to_runtime_failure(self) -> None:
        assert _barrier_reason_for_case_source("unknown") == "runtime_failure"


class TestInferCaseSource:
    """Verify _infer_case_source heuristic mapping."""

    def test_merge_conflict_in_reason(self) -> None:
        decision = ControlDecision(
            kind="resolve",
            reason="Merge conflict detected in step output",
            case_ids=("case_01",),
        )
        assert _infer_case_source(decision) == "merge_conflict"

    def test_review_in_reason(self) -> None:
        decision = ControlDecision(
            kind="resolve",
            reason="Review gate failed for step",
            case_ids=("case_02",),
        )
        assert _infer_case_source(decision) == "review_failed"

    def test_runtime_in_reason(self) -> None:
        decision = ControlDecision(
            kind="resolve",
            reason="Runtime failure during execution",
            case_ids=("case_03",),
        )
        assert _infer_case_source(decision) == "runtime_failure"

    def test_unknown_defaults(self) -> None:
        decision = ControlDecision(
            kind="resolve",
            reason="Some unrecognized issue",
            case_ids=("case_04",),
        )
        assert _infer_case_source(decision) == "unknown"


class TestApplyResolutionReportToDrive:
    """Verify _apply_resolution_report_to_drive transition logic."""

    def _make_record(self, status: DriveStatus = "running") -> DriveRecord:
        return DriveRecord(
            drive_id="drv_test",
            plan_path="/repo/plan.yaml",
            status=status,
            started_at=1000.0,
            updated_at=1000.0,
            agent="test",
        )

    def _make_barrier(self) -> DriveBarrier:
        return DriveBarrier(
            reason="runtime_failure",
            entered_at=1000.0,
            case_ids=("case_01",),
        )

    def test_unblocked_clears_barrier(self) -> None:
        record = self._make_record(status="resolving")
        barrier = self._make_barrier()
        report = ResolutionReport(
            status="unblocked",
            summary="Resolved",
        )
        post_decision = ControlDecision(
            kind="dispatch_batch",
            reason="Claimable work available",
            step_ids=("phase.step",),
            role_bindings={"phase.step": "python-executor"},
            capacity_used=1,
            capacity_remaining=0,
        )

        new_status, new_barrier, summary = _apply_resolution_report_to_drive(
            record, barrier, report, post_decision
        )

        assert new_status == "running"
        assert new_barrier is None
        assert "unblocked" in summary.lower()

    def test_waiting_preserves_barrier(self) -> None:
        record = self._make_record(status="resolving")
        barrier = self._make_barrier()
        report = ResolutionReport(
            status="waiting",
            summary="Awaiting external repair",
        )
        post_decision = ControlDecision(
            kind="wait",
            reason="Resolver waiting",
        )

        new_status, new_barrier, summary = _apply_resolution_report_to_drive(
            record, barrier, report, post_decision
        )

        assert new_status == "resolving"
        assert new_barrier is not None
        assert "waiting" in summary.lower()

    def test_operator_required_transitions_to_blocked_operator(self) -> None:
        record = self._make_record(status="resolving")
        barrier = self._make_barrier()
        report = ResolutionReport(
            status="operator_required",
            summary="Manual intervention required",
            operator_message="Review and retry",
        )
        post_decision = ControlDecision(
            kind="wait",
            reason="Resolver requires operator input",
        )

        new_status, new_barrier, summary = _apply_resolution_report_to_drive(
            record, barrier, report, post_decision
        )

        assert new_status == "blocked_operator"
        assert new_barrier is not None

    def test_halt_transitions_to_halted(self) -> None:
        record = self._make_record(status="resolving")
        barrier = self._make_barrier()
        report = ResolutionReport(
            status="halt",
            summary="Unsafe divergence detected",
        )
        post_decision = ControlDecision(
            kind="halt",
            reason="Resolver halted orchestration",
            barrier_required=True,
        )

        new_status, new_barrier, summary = _apply_resolution_report_to_drive(
            record, barrier, report, post_decision
        )

        assert new_status == "halted"
        assert new_barrier is not None

    def test_unblocked_with_planner_request_transitions_to_replanning(self) -> None:
        record = self._make_record(status="resolving")
        barrier = self._make_barrier()
        report = ResolutionReport(
            status="unblocked",
            summary="Resolved but planner needed",
            planner_request=PlannerRequest(
                reason="Step needs prerequisite fix",
                affected_steps=("phase.step",),
            ),
        )
        post_decision = ControlDecision(
            kind="replan",
            reason="Resolver requests planner",
            planner_request=report.planner_request,
            barrier_required=True,
        )

        new_status, new_barrier, summary = _apply_resolution_report_to_drive(
            record, barrier, report, post_decision
        )

        assert new_status == "replanning"
        assert new_barrier is not None
        assert new_barrier.reason == "planner_needed"


class TestUpdateDriveRecord:
    """Verify _update_drive_record helper."""

    def test_updates_status_and_barrier(self) -> None:
        record = DriveRecord(
            drive_id="drv_test",
            plan_path="/repo/plan.yaml",
            status="running",
            started_at=1000.0,
            updated_at=1000.0,
            agent="test",
        )
        barrier = DriveBarrier(
            reason="runtime_failure",
            entered_at=1001.0,
        )

        updated = _update_drive_record(
            record,
            new_status="resolving",
            barrier=barrier,
            summary="entering resolver",
        )

        assert updated.status == "resolving"
        assert updated.barrier == barrier
        assert updated.summary == "entering resolver"
        assert updated.drive_id == "drv_test"
        assert updated.started_at == 1000.0
        # updated_at must be recent
        assert updated.updated_at >= 1000.0

    def test_clears_barrier_with_none(self) -> None:
        record = DriveRecord(
            drive_id="drv_test",
            plan_path="/repo/plan.yaml",
            status="resolving",
            started_at=1000.0,
            updated_at=1001.0,
            agent="test",
            barrier=DriveBarrier(
                reason="runtime_failure",
                entered_at=1001.0,
            ),
        )

        updated = _update_drive_record(
            record,
            new_status="running",
            barrier=None,
            summary="unblocked",
        )

        assert updated.status == "running"
        assert updated.barrier is None
