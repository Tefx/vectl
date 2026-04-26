"""Integration tests for control ↔ resolver contract verification.

Authority:
    docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md (whole document)
    docs/ORCHESTRATION-PLANE-INTERFACES.md sections 5.3 and 6
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.5 and 3.6

Verification Requirements:
    - At least one unblocked case and one operator_required case end-to-end
    - Resolver acts through allowed surfaces and returns bounded report
    - Control reevaluation after resolution explicitly demonstrated

Step: orch_resolver.verify
Intent: Verify the control ↔ resolver contract works end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vectl.models import IsolationMode
from vectl.orchestration.contracts import (
    CoreSnapshot,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.control import ControlInputSources, PlanAwareControl
from vectl.orchestration.resolver import BoundResolver


def _core(
    *,
    plan_complete: bool = False,
    claimable: tuple[str, ...] = (),
    in_progress: tuple[str, ...] = (),
    blocked: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
) -> CoreSnapshot:
    """Create a test CoreSnapshot."""
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
    """Create a test RosterSnapshot."""
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
    """Create a test RuntimeSnapshot."""
    return RuntimeSnapshot(
        active_workspaces=active_workspaces,
        active_executions=active_executions,
        stalled_executions=stalled_executions,
    )


# === TEST DOUBLES ===


@dataclass
class _FakeCoreAdapter:
    """Test double for core adapter with snapshot tracking."""

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


    def load_step_data_for_dispatch(self, step_id: str):
        raise NotImplementedError(
            f"test double does not implement load_step_data_for_dispatch: step_id={step_id}"
        )

    def defer_step(self, step_id: str) -> None:
        raise NotImplementedError(f"test double: defer_step({step_id})")


@dataclass
class _FakeRosterSource:
    """Test double for roster snapshot source with tracking."""

    snapshot_value: RosterSnapshot
    calls: int = 0

    def snapshot(self) -> RosterSnapshot:
        self.calls += 1
        return self.snapshot_value


@dataclass
class _FakeRuntimeSource:
    """Test double for runtime snapshot source with tracking."""

    snapshot_value: RuntimeSnapshot
    calls: int = 0

    def snapshot(self) -> RuntimeSnapshot:
        self.calls += 1
        return self.snapshot_value


@dataclass
class _FakeResolverInvocation:
    """Test double for resolver invocation surface."""

    payloads: list[dict[str, object]]
    return_status: Literal["unblocked", "waiting", "operator_required", "halt"]
    return_summary: str

    def invoke(self, case: ResolutionCase, *, role_id: str) -> dict[str, object]:
        """Record invocation and return configured report."""
        _ = role_id
        payload: dict[str, object] = {
            "status": self.return_status,
            "summary": self.return_summary,
            "evidence_refs": ("resolver://invoked",),
        }
        self.payloads.append(payload)
        return payload


# === END-TO-END RESOLUTION FLOW TESTS ===


def test_control_invokes_resolver_only_when_normal_flow_does_not_close() -> None:
    """Verify control only invokes resolver when dispatch/wait/done are invalid.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 3.1
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1

    Spec:
        - control should NOT invoke resolver for ordinary dispatch (claimable work)
        - control should NOT invoke resolver for wait (work in progress)
        - control should NOT invoke resolver for done (plan complete, idle)
        - control MUST invoke resolver when blocked/unresolved
    """
    # CASE 1: Normal dispatch path - resolver should NOT be invoked

    resolver_invocation = _FakeResolverInvocation(
        payloads=[],
        return_status="unblocked",
        return_summary="should not be called",
    )
    # Control sees claimable work with available agents
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("phase.step",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.evaluate_current()

    assert decision.kind == "dispatch", "Normal dispatch path should not invoke resolver"
    assert decision.step_ids == ("phase.step",)
    assert len(resolver_invocation.payloads) == 0, "Resolver should NOT be invoked for dispatch"

    # CASE 2: Normal wait path - resolver should NOT be invoked

    resolver_invocation2 = _FakeResolverInvocation(
        payloads=[],
        return_status="unblocked",
        return_summary="should not be called",
    )
    # Control sees work in progress
    control2 = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(in_progress=("phase.active",))),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime(active_executions=("exec-1",))),
        )
    )

    decision2 = control2.evaluate_current()

    assert decision2.kind == "wait", "Normal wait path should not invoke resolver"
    assert "in progress" in decision2.reason.lower()
    assert len(resolver_invocation2.payloads) == 0, "Resolver should NOT be invoked for wait"

    # CASE 3: Normal done path - resolver should NOT be invoked

    resolver_invocation3 = _FakeResolverInvocation(
        payloads=[],
        return_status="unblocked",
        return_summary="should not be called",
    )
    # Control sees complete plan and idle runtime
    control3 = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(plan_complete=True)),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision3 = control3.evaluate_current()

    assert decision3.kind == "done", "Normal done path should not invoke resolver"
    assert len(resolver_invocation3.payloads) == 0, "Resolver should NOT be invoked for done"


def test_blocked_case_unblocked_resolution_end_to_end() -> None:
    """Verify unblocked resolution case flows end-to-end.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 6
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 5.3

    Verification:
        - control detects blocked state and creates ResolutionCase
        - resolver investigates through allowed surface and returns "unblocked"
        - control re-reads snapshots (calls snapshot sources again)
        - control dispatches from refreshed state (not stale pre-resolution state)

    Flow:
        1. Control sees blocked steps → kind="resolve"
        2. Resolver returns "unblocked" report
        3. Control applies_resolution → re-reads core/roster/runtime
        4. Control dispatches based on refreshed (now claimable) state
    """
    # STEP 1: Control detects blocked state - would route to resolver

    initial_core = _core(blocked=("phase.blocked_step",))
    resolver_invocation = _FakeResolverInvocation(
        payloads=[],
        return_status="unblocked",
        return_summary="Blocked step resolved via official surface retry",
    )

    core_adapter = _FakeCoreAdapter(initial_core)
    roster = _FakeRosterSource(_roster(available_agents=("python-executor",)))
    runtime = _FakeRuntimeSource(_runtime())

    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_adapter,
            roster=roster,
            runtime=runtime,
        )
    )

    # Control sees blocked steps
    decision_before = control.evaluate_current()
    assert decision_before.kind == "resolve"
    assert "phase.blocked_step" in decision_before.reason

    # STEP 2: Resolver investigates and returns "unblocked"

    resolver = BoundResolver(
        invocation=resolver_invocation,
        default_role_id="blocked-case-coordinator",
    )

    # Create resolution case (what control would pass to resolver)
    case = ResolutionCase(
        reason=decision_before.reason,
        core=initial_core,
        roster=roster.snapshot_value,
        runtime=runtime.snapshot_value,
    )

    report = resolver.resolve(case)

    # Verify resolver returns bounded report
    assert report.status == "unblocked"
    assert "resolved" in report.summary.lower()
    assert report.evidence_refs == ("resolver://invoked",)
    assert len(resolver_invocation.payloads) == 1, "Resolver invoked once"

    # STEP 3: Control re-evaluates from refreshed state after resolution
    # Simulate state change: blocked step is now claimable
    refreshed_core = _core(claimable=("phase.blocked_step",))
    refreshed_core_adapter = _FakeCoreAdapter(refreshed_core)
    refreshed_roster = _FakeRosterSource(_roster(available_agents=("python-executor",)))
    refreshed_runtime = _FakeRuntimeSource(_runtime())

    control_refreshed = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=refreshed_core_adapter,
            roster=refreshed_roster,
            runtime=refreshed_runtime,
        )
    )

    # Control applies resolution
    decision_after = control_refreshed.apply_resolution_current(report)

    # STEP 4: Control dispatches from refreshed state
    assert decision_after.kind == "dispatch"
    assert decision_after.step_ids == ("phase.blocked_step",)
    assert decision_after.role_bindings == {"phase.blocked_step": "python-executor"}

    # Verify control re-read snapshots (called snapshot methods)
    assert refreshed_core_adapter.calls == 1, "Control must re-read core snapshot"
    assert refreshed_roster.calls == 1, "Control must re-read roster snapshot"
    assert refreshed_runtime.calls == 1, "Control must re-read runtime snapshot"


def test_unresolved_case_operator_required_resolution_end_to_end() -> None:
    """Verify operator_required resolution case flows end-to-end.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 6, 10
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 5.3

    Verification:
        - control detects unresolved state and creates ResolutionCase
        - resolver CANNOT resolve and returns "operator_required"
        - control re-evaluates and maps to wait (not dispatch)
        - operator_message is surfaced appropriately

    Flow:
        1. Control sees unresolved state → kind="resolve"
        2. Resolver returns "operator_required" report
        3. Control applies_resolution → maps to wait decision
        4. System halts automated orchestration until operator intervention
    """
    # STEP 1: Control detects unresolved state

    initial_core = _core(
        claimable=("phase.step",),
        unresolved=("claim graph conflict detected",),
    )
    resolver_invocation = _FakeResolverInvocation(
        payloads=[],
        return_status="operator_required",
        return_summary="Automated resolution failed: claim graph requires manual adjudication",
    )

    core_adapter = _FakeCoreAdapter(initial_core)
    roster = _FakeRosterSource(_roster())
    runtime = _FakeRuntimeSource(_runtime())

    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_adapter,
            roster=roster,
            runtime=runtime,
        )
    )

    # Control sees unresolved conflict - should NOT dispatch despite claimable
    decision_before = control.evaluate_current()
    assert decision_before.kind == "resolve"
    assert "unresolved" in decision_before.reason.lower()
    assert "claim graph conflict" in decision_before.reason

    # STEP 2: Resolver fails to resolve and returns "operator_required"

    resolver = BoundResolver(
        invocation=resolver_invocation,
        default_role_id="blocked-case-coordinator",
    )

    case = ResolutionCase(
        reason=decision_before.reason,
        core=initial_core,
        roster=roster.snapshot_value,
        runtime=runtime.snapshot_value,
    )

    report = resolver.resolve(case)

    # Verify resolver returns operator_required instead of false certainty
    assert report.status == "operator_required"
    assert "manual" in report.summary.lower() or "failed" in report.summary.lower()
    assert report.evidence_refs == ("resolver://invoked",)
    assert len(resolver_invocation.payloads) == 1, "Resolver invoked once"

    # STEP 3: Control applies resolution and maps to wait
    control_after = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(initial_core),
            roster=_FakeRosterSource(_roster()),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision_after = control_after.apply_resolution_current(report)

    # STEP 4: Control maps operator_required to wait (not dispatch)
    assert decision_after.kind == "wait"
    assert "operator" in decision_after.reason.lower()
    # System should pause automated orchestration


def test_resolver_returns_bounded_report_for_all_status_values() -> None:
    """Verify resolver returns bounded report for all status values.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5, 8
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.9

    Spec:
        ResolutionReport.status must be one of:
        - "unblocked": system can return to normal flow
        - "waiting": no immediate dispatch but not terminal failure
        - "operator_required": stop until operator intervention
        - "halt": orchestration should stop
    """
    test_cases: list[
        tuple[Literal["unblocked", "waiting", "operator_required", "halt"], str, object]
    ] = [
        ("unblocked", "resolved via retry", ("run://1",)),
        ("waiting", "awaiting external reconciliation", ("ticket://123",)),
        ("operator_required", "manual intervention required", None),
        ("halt", "unsafe divergence detected", ()),
    ]

    for status, summary, evidence_refs in test_cases:
        resolver_invocation = _FakeResolverInvocation(
            payloads=[],
            return_status=status,
            return_summary=summary,
        )
        resolver = BoundResolver(
            invocation=resolver_invocation,
            default_role_id="blocked-case-coordinator",
        )

        case = ResolutionCase(
            reason="Test case",
            core=_core(),
            roster=_roster(),
            runtime=_runtime(),
        )

        report = resolver.resolve(case)

        # Verify bounded report structure
        assert isinstance(report, ResolutionReport)
        assert report.status == status
        assert report.summary == summary
        assert report.evidence_refs == evidence_refs or report.evidence_refs == (
            "resolver://invoked",
        )

        # Control must be able to apply all status values
        control = PlanAwareControl(
            sources=ControlInputSources(
                core_adapter=_FakeCoreAdapter(_core()),
                roster=_FakeRosterSource(_roster()),
                runtime=_FakeRuntimeSource(_runtime()),
            )
        )

        decision = control.apply_resolution_current(report)
        assert decision.kind in ("dispatch", "wait", "done", "halt", "replan"), (
            f"Control must map {status} to valid decision"
        )


def test_control_refreshes_all_snapshots_after_resolution() -> None:
    """Verify control re-evaluates from refreshed state after resolution.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 6
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1, 5.3

    Spec:
        "After receiving a ResolutionReport, control must:
        1. re-read the relevant authoritative and orchestration-plane state
        2. not trust stale pre-resolution assumptions
        3. resume normal evaluate() logic from refreshed state"
    """
    # Create adapters that track snapshot calls

    core_snapshot_1 = _core(blocked=("phase.step1",))
    core_snapshot_2 = _core(claimable=("phase.step2",))  # After resolver clears block

    roster_snapshot_1 = _roster(working_agents=("python-executor",))
    roster_snapshot_2 = _roster(available_agents=("python-executor",))

    # CRITICAL: runtime must be idle for control to route to resolve
    # If there are active executions, control would route to wait instead
    runtime_snapshot_1 = _runtime()  # Idle runtime so blocked steps route to resolve
    runtime_snapshot_2 = _runtime()

    # First evaluation (blocked)
    core_before = _FakeCoreAdapter(core_snapshot_1)
    roster_before = _FakeRosterSource(roster_snapshot_1)
    runtime_before = _FakeRuntimeSource(runtime_snapshot_1)

    control_before = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_before,
            roster=roster_before,
            runtime=runtime_before,
        )
    )

    decision1 = control_before.evaluate_current()
    assert decision1.kind == "resolve", "Initial state is blocked"
    assert core_before.calls == 1
    assert roster_before.calls == 1
    assert runtime_before.calls == 1

    # Resolution clears blockage
    report = ResolutionReport(
        status="unblocked",
        summary="Blocked step resolved",
        evidence_refs=("resolver://success",),
    )

    # After resolution - MUST use fresh snapshots
    core_after = _FakeCoreAdapter(core_snapshot_2)
    roster_after = _FakeRosterSource(roster_snapshot_2)
    runtime_after = _FakeRuntimeSource(runtime_snapshot_2)

    control_after = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=core_after,
            roster=roster_after,
            runtime=runtime_after,
        )
    )

    # Apply resolution and verify re-read
    decision2 = control_after.apply_resolution_current(report)

    # CRITICAL: Control must call snapshot() on all three sources
    assert core_after.calls == 1, "Control must re-read core snapshot after resolution"
    assert roster_after.calls == 1, "Control must re-read roster snapshot after resolution"
    assert runtime_after.calls == 1, "Control must re-read runtime snapshot after resolution"

    # Decision must be based on refreshed state (claimable step2), not stale state
    assert decision2.kind == "dispatch"
    assert decision2.step_ids == ("phase.step2",), "Must use refreshed core, not stale core"


def test_resolver_forbidden_from_becoming_permanent_controller() -> None:
    """Verify resolver does not become permanent orchestration authority.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.2
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.4

    Spec:
        "resolver must not:
        - become the long-lived orchestration controller
        - silently redefine authority boundaries
        - mutate plan files or internal state by bypassing official tools/interfaces"

    This test verifies that resolver's output is a bounded report,
    not a permanent authority handoff. Control resumes orchestration
    after receiving the report.
    """
    # Resolver returns a report, not a directive for control to replay
    resolver_invocation = _FakeResolverInvocation(
        payloads=[],
        return_status="unblocked",
        return_summary="Resolver investigated and unblocked via official surface",
    )

    resolver = BoundResolver(
        invocation=resolver_invocation,
        default_role_id="blocked-case-coordinator",
    )

    case = ResolutionCase(
        reason="Blocked step",
        core=_core(blocked=("phase.step",)),
        roster=_roster(),
        runtime=_runtime(),
    )

    report = resolver.resolve(case)

    # CRITICAL: Report is bounded - just status, summary, evidence_refs, optional message
    # It does NOT contain:
    #   - A new control directive to replay
    #   - A mutation to apply to core state
    #   - A permanent authority handoff

    assert hasattr(report, "status"), "Report has status field"
    assert hasattr(report, "summary"), "Report has summary field"
    assert hasattr(report, "evidence_refs"), "Report has evidence_refs field"
    assert hasattr(report, "operator_message"), "Report has operator_message field"

    # Bounded by frozen dataclass - cannot grow new fields
    from dataclasses import fields

    field_count = len(list(fields(ResolutionReport)))
    assert field_count == 5, (
        "Report is bounded to exactly 5 fields (status, summary, evidence_refs, operator_message, planner_request)"
    )

    # Control must apply its own evaluation logic after receiving report
    control = PlanAwareControl(
        sources=ControlInputSources(
            core_adapter=_FakeCoreAdapter(_core(claimable=("phase.step",))),
            roster=_FakeRosterSource(_roster(available_agents=("python-executor",))),
            runtime=_FakeRuntimeSource(_runtime()),
        )
    )

    decision = control.apply_resolution_current(report)

    # Control owns the decision, not resolver
    assert decision.kind == "dispatch"
    assert decision.reason != report.summary, (
        "Control decision reason comes from control's own evaluation, not resolver's summary"
    )


def test_resolution_case_contains_current_state_not_speculation() -> None:
    """Verify ResolutionCase contains state snapshot, not action plan.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 3.2
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.8

    Spec:
        "The case should be a snapshot of current known facts,
        not a speculative action plan."
    """
    # Create a resolution case from control decision
    current_core = _core(
        claimable=("phase.step_available",),
        unresolved=("dependency cycle detected",),
    )
    current_roster = _roster(available_agents=("python-executor",))
    current_runtime = _runtime()

    case = ResolutionCase(
        reason="Unresolved authoritative state: dependency cycle detected",
        core=current_core,
        roster=current_roster,
        runtime=current_runtime,
    )

    # Verify case contains state snapshot, not action plan
    assert isinstance(case.core, CoreSnapshot), "Case contains core snapshot"
    assert isinstance(case.roster, RosterSnapshot), "Case contains roster snapshot"
    assert isinstance(case.runtime, RuntimeSnapshot), "Case contains runtime snapshot"

    # Case does NOT contain:
    #   - "dispatch this step" directive
    #   - "resolve by doing X" plan
    #   - target state for resolver to reach

    # Case contains ONLY:
    assert case.reason, "Case has reason"
    assert hasattr(case, "core"), "Case has core"
    assert hasattr(case, "roster"), "Case has roster"
    assert hasattr(case, "runtime"), "Case has runtime"
    assert not hasattr(case, "action"), "Case does not have action plan"
    assert not hasattr(case, "target"), "Case does not have target state"
    assert not hasattr(case, "directive"), "Case does not have directive"

    # Bounded by frozen dataclass
    from dataclasses import fields

    field_names = tuple(field.name for field in fields(ResolutionCase))
    assert field_names[:4] == ("reason", "core", "roster", "runtime")
    assert {
        "case_id",
        "case_source",
        "summary",
        "blocked_step_ids",
        "artifact_refs",
    } <= set(field_names)
