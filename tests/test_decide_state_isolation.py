"""Tests exposing module-global coupling in decide.py.

These tests document isolation behavior for decide-state handling:
callers using state=None must not share mutable module-global state between
independent decide() calls.

Required red cases:
1. Two DecideState() instances remain fully isolated: completion times,
   session registry, and failure counts do not leak across instances.
2. Driver runtime path can call decide() with an explicit state object
   and does not need module-global mutable dictionaries.
3. should_reuse_session() and failure escalation still behave correctly
   when fed one explicit instance.

All cases should pass with isolated fallback semantics.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from vectl.decision_state import DecideState
from vectl.decide import (
    decide,
    should_reuse_session,
    REUSE_TTL,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plan(tmp_path: Path | None = None) -> tuple[Plan, Path | None]:
    """Helper to create a simple plan for testing.

    Returns (plan, plan_path). If tmp_path is None, plan_path is None
    (no disk write).
    """
    plan = Plan(
        project="decide-isolation-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(
                        id="s1",
                        name="Step 1",
                        status=StepStatus.PENDING,
                    ),
                    Step(
                        id="s2",
                        name="Step 2",
                        status=StepStatus.PENDING,
                        depends_on=["s1"],
                    ),
                ],
            )
        ],
    )
    plan_path = None
    if tmp_path:
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)
    return plan, plan_path


# ---------------------------------------------------------------------------
# ISOLATION TESTS
# ---------------------------------------------------------------------------


class TestDecideStateIsolation:
    """Test that two DecideState instances do not leak state.

    Gap exposed: Before proper implementation, state must be explicitly passed
    to avoid falling back to module-global _legacy_state.
    """

    def test_two_decide_state_instances_are_isolated(self) -> None:
        """Two DecideState instances must have independent completion_times.

        This test passes because DecideState class itself is properly isolated.
        The gap is in the legacy fallback mechanism, not the class.
        """
        state_a = DecideState()
        state_b = DecideState()

        # Add data to state_a
        state_a.completion_times["s1"] = time.time()
        state_a.session_registry["s1"] = "task-1"
        state_a.failure_counts["s1"] = 1

        # state_b must NOT see state_a's data
        assert "s1" not in state_b.completion_times
        assert "s1" not in state_b.session_registry
        assert "s1" not in state_b.failure_counts

    def test_should_reuse_session_isolated_with_explicit_state(self) -> None:
        """should_reuse_session must use explicit state, not module global.

        This test verifies the happy path: when an explicit state is passed,
        the function behaves correctly (within that state).
        """
        state = DecideState()

        # Pre-populate state with a completed parent step
        state.completion_times["s1"] = time.time()
        state.session_registry["s1"] = "task-1"

        # Query for s2 with s1 as parent
        should_reuse, task_id = should_reuse_session(
            step_id="s2",
            parent_step_id="s1",
            state=state,
        )

        assert should_reuse is True
        assert task_id == "task-1"

    def test_should_reuse_session_no_leak_between_explicit_states(self) -> None:
        """Two explicit states must not leak to each other.

        This is the core isolation test: call should_reuse_session with
        two different explicit states and verify no cross-contamination.
        """
        state_a = DecideState()
        state_b = DecideState()

        # Populate state_a only
        state_a.completion_times["s1"] = time.time()
        state_a.session_registry["s1"] = "task-a"

        # state_b should NOT see s1 at all
        should_reuse_b, task_id_b = should_reuse_session(
            step_id="s2",
            parent_step_id="s1",
            state=state_b,
        )

        assert should_reuse_b is False
        assert task_id_b is None


# ---------------------------------------------------------------------------
# LEGACY/GLOBAL STATE LEAKAGE TESTS - THE CORE GAP
# ---------------------------------------------------------------------------


class TestLegacyFallbackIsolation:
    """Tests for state=None fallback isolation behavior.

    state=None should create isolated, call-local decide state and must not
    couple callers through ``vectl.decide._legacy_state``.
    """

    def test_resolve_state_returns_isolated_state_when_none(self) -> None:
        """_resolve_state(None) must not return shared module-global state."""
        import vectl.decide as decide_mod

        # When state=None, each resolve must produce a fresh object
        resolved1 = decide_mod._resolve_state(None)
        resolved2 = decide_mod._resolve_state(None)

        assert resolved1 is not resolved2
        assert resolved1 is not decide_mod._legacy_state
        assert resolved2 is not decide_mod._legacy_state

    def test_state_none_callers_do_not_mutate_legacy_state(self, tmp_path: Path) -> None:
        """decide(..., state=None) must not mutate module-global legacy state."""
        plan, plan_path = _make_plan(tmp_path)
        import vectl.decide as decide_mod

        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]

        try:
            decide_mod._legacy_state.completion_times = {}
            decide_mod._legacy_state.session_registry = {}

            completed1 = [
                CompletedResult(
                    step_id="s1",
                    task_id="session-1",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            decide(
                running_tasks=[],
                completed_results=completed1,
                max_parallelism=5,
                state=None,
            )
            assert "s1" not in decide_mod._legacy_state.completion_times, (
                "state=None fallback must be isolated; _legacy_state cannot be "
                "mutated by decide() calls that omit explicit state"
            )

        finally:
            decide_mod.resolve_plan_path = original  # type: ignore[assignment]

    def test_should_reuse_session_with_none_ignores_shared_legacy(self, tmp_path: Path) -> None:
        """should_reuse_session(state=None) must ignore shared legacy state."""
        import vectl.decide as decide_mod

        # Pre-populate _legacy_state as if a previous caller had written data.
        # state=None lookup must remain isolated from this shared object.
        decide_mod._legacy_state.completion_times["parent-step"] = time.time()
        decide_mod._legacy_state.session_registry["parent-step"] = "parent-session"

        should_reuse, task_id = should_reuse_session(
            step_id="child-step",
            parent_step_id="parent-step",
            state=None,
        )

        assert should_reuse is False, (
            "state=None lookup must not reuse task_id from shared _legacy_state"
        )
        assert task_id is None


# ---------------------------------------------------------------------------
# DRIVER RUNTIME PATH TESTS - EXPLICIT STATE BEHAVIOR
# ---------------------------------------------------------------------------


class TestDriverRuntimePathIsolation:
    """Verify driver runtime path can use explicit state.

    Required red case: Driver runtime must be able to call decide() with
    an explicit state object and not depend on module-global mutable dicts.

    These tests PASS (green) because explicit state works correctly.
    They verify the correct runtime path exists.
    """

    def test_decide_accepts_explicit_state(self, tmp_path: Path) -> None:
        """decide() must work correctly when passed an explicit state.

        This is the runtime path the driver uses. It should not need
        module-global mutable state.
        """
        plan, plan_path = _make_plan(tmp_path)

        # DriverState.decide_state is the intended runtime pattern
        driver_decide_state = DecideState()

        # Pre-populate with a completed parent
        driver_decide_state.completion_times["s1"] = time.time()
        driver_decide_state.session_registry["s1"] = "session-for-s1"

        import vectl.decide as decide_mod

        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                state=driver_decide_state,
            )
        finally:
            decide_mod.resolve_plan_path = original  # type: ignore[assignment]

        # Should have actions
        assert isinstance(output, DecideOutput)

    def test_failure_counts_isolated_in_explicit_state(self, tmp_path: Path) -> None:
        """Failure counts in explicit state must not leak to module global.

        When driver uses explicit state for failure tracking, the data must
        stay in that state object, not contaminate _legacy_state.
        """
        plan, plan_path = _make_plan(tmp_path)

        explicit_state = DecideState()

        # Simulate a failure being recorded
        explicit_state.failure_counts["s1"] = 2

        # Check that module global is NOT contaminated
        import vectl.decide as decide_mod

        assert "s1" not in decide_mod._legacy_state.failure_counts, (
            "Gap: Failure counts from explicit state leaked into module global "
            "_legacy_state. This indicates the impl is using the legacy fallback "
            "instead of properly isolating explicit state."
        )

    def test_should_reuse_session_uses_explicit_state_not_global(self, tmp_path: Path) -> None:
        """should_reuse_session must use passed state, not module global.

        This verifies the correct runtime behavior for session reuse.
        """
        explicit_state = DecideState()

        # Populate explicit state
        explicit_state.completion_times["parent-step"] = time.time()
        explicit_state.session_registry["parent-step"] = "parent-session"

        # Query using explicit state
        should_reuse, task_id = should_reuse_session(
            step_id="child-step",
            parent_step_id="parent-step",
            state=explicit_state,
        )

        # Should succeed with explicit state
        assert should_reuse is True
        assert task_id == "parent-session"


# ---------------------------------------------------------------------------
# INTEGRATION TEST: Two decide() calls with explicit states remain isolated
# ---------------------------------------------------------------------------


class TestDecideIntegrationIsolation:
    """Integration test: two decide() calls with separate states stay isolated.

    These tests PASS (green) - they verify the correct behavior when explicit
    state is used. The gap is only when state=None is used.
    """

    def test_two_decide_calls_with_separate_states_isolated(self, tmp_path: Path) -> None:
        """Two decide() calls with different explicit states must not leak.

        This is the end-to-end test for the isolation requirement.
        When explicit states are used, there should be no coupling.
        """
        plan, plan_path = _make_plan(tmp_path)

        state_a = DecideState()
        state_b = DecideState()

        import vectl.decide as decide_mod

        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]

        try:
            # Call decide with state_a
            completed_a = [
                CompletedResult(
                    step_id="s1",
                    task_id="session-a",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            decide(
                running_tasks=[],
                completed_results=completed_a,
                max_parallelism=5,
                state=state_a,
            )

            # state_b should NOT see s1 from state_a
            assert "s1" not in state_b.completion_times
            assert "s1" not in state_b.session_registry

            # Now call decide with state_b
            completed_b = [
                CompletedResult(
                    step_id="s2",
                    task_id="session-b",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            decide(
                running_tasks=[],
                completed_results=completed_b,
                max_parallelism=5,
                state=state_b,
            )

            # state_a should NOT see any changes from state_b's call
            assert "s2" not in state_a.completion_times

        finally:
            decide_mod.resolve_plan_path = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# FAILURE ESCALATION WITH EXPLICIT STATE
# ---------------------------------------------------------------------------


class TestFailureEscalationWithExplicitState:
    """Verify failure escalation works correctly with explicit state.

    Required red case: should_reuse_session() and failure escalation still
    behave correctly when fed one explicit instance.
    """

    def test_failure_count_tracking_in_explicit_state(self, tmp_path: Path) -> None:
        """Failure counts must be tracked in explicit state, not module global."""
        plan, plan_path = _make_plan(tmp_path)
        import vectl.decide as decide_mod

        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path  # type: ignore[assignment]

        explicit_state = DecideState()

        try:
            # Record 2 failures for s1 in explicit state
            for i in range(2):
                completed = [
                    CompletedResult(
                        step_id="s1",
                        task_id=f"task-{i}",
                        status="FAIL",
                        output_summary=f"Failure {i}",
                    ),
                ]
                decide(
                    running_tasks=[],
                    completed_results=completed,
                    max_parallelism=5,
                    state=explicit_state,
                )

            # Explicit state should have failure count
            assert explicit_state.failure_counts.get("s1") == 2, (
                "Gap: explicit state should track 2 failures for s1"
            )

            # Module global should NOT have s1
            assert "s1" not in decide_mod._legacy_state.failure_counts, (
                "Gap: failure counts leaked into module global _legacy_state"
            )

            # Third failure should trigger escalation
            completed = [
                CompletedResult(
                    step_id="s1",
                    task_id="task-2",
                    status="FAIL",
                    output_summary="Third failure",
                ),
            ]
            output = decide(
                running_tasks=[],
                completed_results=completed,
                max_parallelism=5,
                state=explicit_state,
            )

            # Should have escalate action
            action_types = [(a.action, a.step_id) for a in output.actions]
            assert ("escalate", "s1") in action_types, (
                "Gap: expected escalate action after 3 failures in explicit state"
            )

            # Module global should still NOT have s1
            assert "s1" not in decide_mod._legacy_state.failure_counts, (
                "Gap: failure counts leaked into module global"
            )

        finally:
            decide_mod.resolve_plan_path = original  # type: ignore[assignment]
