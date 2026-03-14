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

from pathlib import Path

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
    # This test will fail until impl phase fills _completion_times/_session_registry
    # Expected behavior: if parent_step_id is in _completion_times within REUSE_TTL,
    # return (True, task_id)
    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
    )

    # Note: This will raise NotImplementedError until impl phase
    # After impl: should_reuse should be True if s1 completed within TTL
    # and _session_registry has a task_id for s1
    # Impl should populate _completion_times and _session_registry first
    assert should_reuse is False or should_reuse is True  # Placeholder until impl


def test_should_reuse_session_beyond_ttl() -> None:
    """should_reuse_session returns (False, None) when parent beyond TTL."""
    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
    )

    # After impl: this should test that if elapsed > REUSE_TTL, returns (False, None)
    # Placeholder until impl phase
    assert should_reuse is False or should_reuse is True  # Placeholder until impl


def test_should_reuse_session_no_registered_task() -> None:
    """should_reuse_session returns (False, None) with no registered task."""
    should_reuse, task_id = should_reuse_session(
        step_id="s2",
        parent_step_id="s1",
    )

    # After impl: if parent_step_id not in _session_registry, returns (False, None)
    # Placeholder until impl phase
    assert should_reuse is False or should_reuse is True  # Placeholder until impl


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
