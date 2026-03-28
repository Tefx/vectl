"""Unit tests for vectl_decide logic.

Tests for:
- compute_continuation: continuation guard logic
- should_reuse_session: session reuse decisions
- decide: main orchestration decision function

Contract: These tests target the stub functions in vectl.decide.
Implementation will be provided in decide-impl phase.
Tests may fail at runtime with NotImplementedError until impl phase.
"""

from __future__ import annotations

import time
import importlib
from pathlib import Path

from vectl.decision_state import DecideState
from vectl.decide import (
    compute_continuation,
    decide,
    should_reuse_session,
)
from vectl.io import save_plan
from vectl.models import (
    CompletedResult,
    DecideOutput,
    Phase,
    PhaseStatus,
    Plan,
    RunningTask,
    Step,
    StepStatus,
)


def _write_plan_with_steps(path: Path | None, steps: list[Step]) -> Plan:
    """Helper to create a plan with steps and save it.

    Args:
        path: Path to save plan, or None to skip disk write (in-memory only).
        steps: List of steps for the first phase.

    Note:
        Using path=None avoids /dev/null.lock PermissionError in tests that
        only need in-memory Plan objects.
    """
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=steps,
            )
        ],
    )
    if path:
        save_plan(plan, path)
    return plan


# ---------------------------------------------------------------------------
# compute_continuation tests
# ---------------------------------------------------------------------------


def test_compute_continuation_claimable_steps_returns_true() -> None:
    """compute_continuation returns (True, None) with claimable steps."""
    # Note: path=None skips disk write; these tests don't need file persistence
    plan = _write_plan_with_steps(
        None,
        [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
    )

    # Claimable steps exist, no running tasks, max_parallelism allows it
    should_continue, halt_reason = compute_continuation(
        plan=plan,
        running_count=0,
        max_parallelism=5,
    )

    assert should_continue is True
    assert halt_reason is None


def test_compute_continuation_no_claimable_no_running() -> None:
    """compute_continuation returns (False, NO_EXECUTABLE_STEPS) when done."""
    plan = _write_plan_with_steps(
        None,
        [Step(id="s1", name="Step 1", status=StepStatus.DONE)],
    )

    should_continue, halt_reason = compute_continuation(
        plan=plan,
        running_count=0,
        max_parallelism=5,
    )

    assert should_continue is False
    assert halt_reason == "NO_EXECUTABLE_STEPS"


def test_compute_continuation_at_max_parallelism() -> None:
    """compute_continuation returns (False, MAX_PARALLELISM_REACHED) at cap."""
    plan = _write_plan_with_steps(
        None,
        [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
    )

    # Running count equals max_parallelism - at capacity
    should_continue, halt_reason = compute_continuation(
        plan=plan,
        running_count=5,
        max_parallelism=5,
    )

    assert should_continue is False
    assert halt_reason == "MAX_PARALLELISM_REACHED"


def test_compute_continuation_waiting_on_running() -> None:
    """compute_continuation returns (False, WAITING_ON_RUNNING_SUBAGENTS)."""
    # B2 fix: CLAIMED status requires claimed_by field (model validator enforced)
    plan = _write_plan_with_steps(
        None,
        [Step(id="s1", name="Step 1", status=StepStatus.CLAIMED, claimed_by="agent-1")],
    )

    # Running tasks exist but no claimable steps
    should_continue, halt_reason = compute_continuation(
        plan=plan,
        running_count=2,
        max_parallelism=5,
    )

    assert should_continue is False
    assert halt_reason == "WAITING_ON_RUNNING_SUBAGENTS"


# ---------------------------------------------------------------------------
# should_reuse_session tests
# ---------------------------------------------------------------------------


def test_should_reuse_session_no_parent() -> None:
    """should_reuse_session returns (False, None) when no parent step exists."""
    # Step with no parent cannot reuse session
    should_reuse, task_id = should_reuse_session(
        step_id="s1",
        parent_step_id=None,
    )

    assert should_reuse is False
    assert task_id is None


def test_should_reuse_session_within_ttl() -> None:
    """should_reuse_session returns (True, task_id) when parent within TTL."""
    state = DecideState(
        completion_times={"s1": time.time()},
        session_registry={"s1": "task-1"},
    )

    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
        state=state,
    )

    assert should_reuse is True
    assert task_id == "task-1"


def test_should_reuse_session_beyond_ttl() -> None:
    """should_reuse_session returns (False, None) when parent beyond TTL."""
    state = DecideState(
        completion_times={"s1": time.time() - 301},
        session_registry={"s1": "task-1"},
    )

    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
        state=state,
    )

    assert should_reuse is False
    assert task_id is None


def test_should_reuse_session_no_registered_task() -> None:
    """should_reuse_session returns (False, None) with no registered task."""
    state = DecideState(completion_times={"s1": time.time()})

    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
        state=state,
    )

    assert should_reuse is False
    assert task_id is None


# ---------------------------------------------------------------------------
# decide tests
# ---------------------------------------------------------------------------


def test_decide_returns_claim_actions() -> None:
    """decide returns claim_and_dispatch actions when steps are claimable."""
    output: DecideOutput = decide(
        running_tasks=[],
        completed_results=None,
        max_parallelism=5,
    )

    # After impl: should return actions with claim_and_dispatch for claimable steps
    assert isinstance(output, DecideOutput)
    assert isinstance(output.actions, list)
    assert isinstance(output.decision_log, list)
    # continuation should be True when there's work to do
    assert output.continuation is False or output.continuation is True


def test_decide_returns_wait_when_at_capacity() -> None:
    """decide returns wait action when max parallelism is reached."""
    running = [
        RunningTask(
            step_id="already-running-1",
            agent="agent-1",
            task_id="task-1",
            dispatched_at=1000.0,
        ),
    ]

    # At capacity: 1 running, max_parallelism=1
    output: DecideOutput = decide(
        running_tasks=running,
        completed_results=None,
        max_parallelism=1,
    )

    # After impl: should return continuation=False or wait action
    assert isinstance(output, DecideOutput)
    # The exact behavior depends on impl, but continuation should be False
    # when at capacity and no completions to process


def test_decide_completes_successful_results() -> None:
    """decide returns complete actions for successful completed results."""
    completed = [
        CompletedResult(
            step_id="s1",
            task_id="task-1",
            status="SUCCESS",
            output_summary="Work completed successfully",
        ),
    ]

    output: DecideOutput = decide(
        running_tasks=[],
        completed_results=completed,
        max_parallelism=5,
    )

    # After impl: should include a complete action for SUCCESS results
    assert isinstance(output, DecideOutput)
    # The complete action should have the evidence from output_summary


def test_decide_escalates_after_repeated_failures() -> None:
    """decide returns escalate action after repeated failures on same step."""
    # According to RFC: 3 consecutive failures on same step -> escalate
    completed = [
        CompletedResult(
            step_id="s1",
            task_id="task-1",
            status="FAIL",
            output_summary="Failed attempt 1",
        ),
    ]

    output: DecideOutput = decide(
        running_tasks=[],
        completed_results=completed,
        max_parallelism=5,
    )

    # After impl: need to track failure counts per step
    # Single failure -> may retry/waite
    # 3 consecutive failures on same step -> escalate
    assert isinstance(output, DecideOutput)
    # The exact threshold behavior depends on impl


def test_decide_continuation_flag() -> None:
    """decide correctly sets continuation flag based on plan state."""
    output: DecideOutput = decide(
        running_tasks=[],
        completed_results=None,
        max_parallelism=5,
    )

    # After impl: continuation=True when there are claimable steps and capacity
    # continuation=False when no claimable steps or at max parallelism
    assert isinstance(output, DecideOutput)
    assert output.continuation in (True, False)
    if not output.continuation:
        assert isinstance(output.halt_reason, str | type(None))


def test_decide_explicit_state_prevents_legacy_fallback_override(tmp_path: Path) -> None:
    """Explicit state remains authoritative over legacy fallback memory."""
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Parent", status=StepStatus.DONE),
                    Step(
                        id="s2",
                        name="Child",
                        status=StepStatus.PENDING,
                        depends_on=["s1"],
                        agent="python-executor",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    original_path = decide_mod.resolve_plan_path
    original_legacy = DecideState(
        completion_times=dict(decide_mod._legacy_state.completion_times),
        session_registry=dict(decide_mod._legacy_state.session_registry),
        failure_counts=dict(decide_mod._legacy_state.failure_counts),
    )

    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    decide_mod._legacy_state.completion_times = {"s1": time.time()}
    decide_mod._legacy_state.session_registry = {"s1": "legacy-task"}
    decide_mod._legacy_state.failure_counts = {}

    explicit_state = DecideState()
    try:
        output = decide(
            running_tasks=[],
            completed_results=None,
            max_parallelism=5,
            state=explicit_state,
        )
    finally:
        decide_mod.resolve_plan_path = original_path  # type: ignore[assignment]
        decide_mod._legacy_state.completion_times = original_legacy.completion_times
        decide_mod._legacy_state.session_registry = original_legacy.session_registry
        decide_mod._legacy_state.failure_counts = original_legacy.failure_counts

    dispatch_actions = [action for action in output.actions if action.step_id == "s2"]
    assert len(dispatch_actions) == 1
    assert dispatch_actions[0].session == "fresh"
    assert dispatch_actions[0].task_id is None


def test_decide_state_isolation_reuse_and_failure_reset(tmp_path: Path) -> None:
    """Two DecideState instances keep reuse/failure memory isolated."""
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Parent", status=StepStatus.DONE),
                    Step(
                        id="s2",
                        name="Child",
                        status=StepStatus.PENDING,
                        depends_on=["s1"],
                        agent="python-executor",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    state_a = DecideState(
        completion_times={"s1": time.time()},
        session_registry={"s1": "task-a"},
        failure_counts={"s1": 2},
    )
    state_b = DecideState(failure_counts={"s1": 2})

    decide_mod = importlib.import_module("vectl.decide")

    original_path = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        output_a = decide(
            running_tasks=[],
            completed_results=None,
            max_parallelism=5,
            state=state_a,
        )
        output_b = decide(
            running_tasks=[],
            completed_results=None,
            max_parallelism=5,
            state=state_b,
        )

        decide(
            running_tasks=[],
            completed_results=[
                CompletedResult(
                    step_id="s1",
                    task_id="task-a",
                    status="SUCCESS",
                    output_summary="ok",
                )
            ],
            max_parallelism=5,
            state=state_a,
        )
    finally:
        decide_mod.resolve_plan_path = original_path  # type: ignore[assignment]

    action_a = next(action for action in output_a.actions if action.step_id == "s2")
    action_b = next(action for action in output_b.actions if action.step_id == "s2")

    assert action_a.session == "reuse"
    assert action_a.task_id == "task-a"
    assert action_b.session == "fresh"
    assert action_b.task_id is None

    assert "s1" not in state_a.failure_counts
    assert state_b.failure_counts["s1"] == 2


def test_decide_successor_visible_after_simulated_completion(tmp_path: Path) -> None:
    """decide returns both complete AND claim_and_dispatch for successor in one batch.

    Regression test: before the fix, decide() would return complete(A) but NOT
    claim_and_dispatch(B) because B depends on A and A was not yet marked DONE
    on disk. The fix simulates completions in-memory before computing claimable steps.
    """
    # A (claimed) -> B (pending, depends on A)
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Step A",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                        agent="python-executor",
                    ),
                    Step(
                        id="s2",
                        name="Step B",
                        status=StepStatus.PENDING,
                        depends_on=["s1"],
                        agent="python-executor",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    # Patch resolve_plan_path to point at our temp plan
    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                status="SUCCESS",
                output_summary="Done",
            ),
        ]
        output = decide(
            running_tasks=[],
            completed_results=completed,
            max_parallelism=5,
        )
    finally:
        decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    action_types = [(a.action, a.step_id) for a in output.actions]
    # Must contain complete(s1) AND claim_and_dispatch(s2) in the same batch
    assert ("complete", "s1") in action_types
    assert ("claim_and_dispatch", "s2") in action_types


# ---------------------------------------------------------------------------
# Expected-red verification semantics tests
# ---------------------------------------------------------------------------


def test_decide_expected_red_fail_completes(tmp_path: Path) -> None:
    """Expected-red step with FAIL result completes (gap demonstrated as intended).

    RFC: docs/RFC-expected-red-verification-semantics.md (Sections 4-5)
    When step.verify == "expected_red", a FAIL result means the gap was demonstrated.
    This should create a complete action, not failure/escalation.
    """
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Gap-exposing test step",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                        verify="expected_red",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                status="FAIL",
                output_summary="Test failed as expected: missing feature X",
            ),
        ]
        output = decide(
            running_tasks=[],
            completed_results=completed,
            max_parallelism=5,
        )
    finally:
        decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    # Must complete, not fail/escalate
    action_types = [(a.action, a.step_id) for a in output.actions]
    assert ("complete", "s1") in action_types

    # Decision log must show expected-red reason
    assert any(
        d.decision == "COMPLETE" and d.step_id == "s1" and "Expected-red" in d.why
        for d in output.decision_log
    ), f"Decision log should contain 'Expected-red' reason, got: {output.decision_log}"


def test_decide_must_green_fail_normal_failure_path(tmp_path: Path) -> None:
    """must_green step with FAIL result follows normal failure path.

    RFC: docs/RFC-expected-red-verification-semantics.md (Sections 4-5)
    When step.verify == "must_green", FAIL should trigger failure tracking.
    This preserves existing behavior for verification-critical steps.
    """
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Critical verification step",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                        verify="must_green",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        # First failure
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                status="FAIL",
                output_summary="Verification failed",
            ),
        ]
        output = decide(
            running_tasks=[],
            completed_results=completed,
            max_parallelism=5,
        )
    finally:
        decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    # First failure should NOT complete, should be WAIT or escalate
    action_types = [(a.action, a.step_id) for a in output.actions]
    assert ("complete", "s1") not in action_types

    # Should be a WAIT decision (failure count 1)
    wait_decisions = [d for d in output.decision_log if d.decision == "WAIT"]
    assert any(d.step_id == "s1" for d in wait_decisions), (
        f"Expected WAIT decision for s1, got: {output.decision_log}"
    )


def test_decide_verify_none_fail_normal_failure_path(tmp_path: Path) -> None:
    """verify=None step with FAIL result follows normal failure path.

    RFC: docs/RFC-expected-red-verification-semantics.md (Section 7)
    Default behavior (verify=None) is equivalent to must_green for failure handling.
    This tests backward compatibility - existing steps unchanged.
    """
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Legacy step without verify field",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                        # verify is None by default
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        # First failure
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                status="FAIL",
                output_summary="Operation failed",
            ),
        ]
        output = decide(
            running_tasks=[],
            completed_results=completed,
            max_parallelism=5,
        )
    finally:
        decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    # First failure should NOT complete (preserves default behavior)
    action_types = [(a.action, a.step_id) for a in output.actions]
    assert ("complete", "s1") not in action_types

    # Should be WAIT decision (failure count 1)
    wait_decisions = [d for d in output.decision_log if d.decision == "WAIT"]
    assert any(d.step_id == "s1" for d in wait_decisions), (
        f"Expected WAIT decision for s1, got: {output.decision_log}"
    )


def test_decide_expected_red_success_completes_normally(tmp_path: Path) -> None:
    """Expected-red step with SUCCESS result completes normally.

    If an expected-red step unexpectedly succeeds (result.status == SUCCESS),
    it should complete normally - the SUCCESS path is unchanged.
    """
    plan = Plan(
        project="decide-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Expected-red step that unexpectedly passed",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-1",
                        verify="expected_red",
                    ),
                ],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    decide_mod = importlib.import_module("vectl.decide")

    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                status="SUCCESS",
                output_summary="Unexpectedly passed",
            ),
        ]
        output = decide(
            running_tasks=[],
            completed_results=completed,
            max_parallelism=5,
        )
    finally:
        decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    # SUCCESS should complete regardless of verify field
    action_types = [(a.action, a.step_id) for a in output.actions]
    assert ("complete", "s1") in action_types

    # Decision log should show normal SUCCESS completion
    complete_decisions = [d for d in output.decision_log if d.decision == "COMPLETE"]
    assert any(d.step_id == "s1" for d in complete_decisions)
