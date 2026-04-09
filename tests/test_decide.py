"""Unit tests for vectl_decide logic — refreshed contract.

These tests assert against the RFC-vectl-decide-advisor-refresh.md contract:
- runner provenance on RunningTask and CompletedResult
- task_id as execution identity only (NOT a reuse handle)
- reuse_token / reuse_runner semantics on Action
- caller-owned advisor_state via next_state full replacement
- status / reason_code / message top-level control summary (no continuation/halt_reason)
- policy metadata (reuse_ttl_s, escalation_threshold)

Gap exposure: These tests will FAIL until the implementation is updated to
match the refreshed contract. This is intentional — tests drive implementation.

Contract spec: docs/RFC-vectl-decide-advisor-refresh.md
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

from vectl.decide import (
    ESCALATION_THRESHOLD,
    REUSE_TTL,
    decide,
    should_reuse_session,
)
from vectl.decision_state import DecideState
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
# Helpers
# ---------------------------------------------------------------------------


def _write_plan_with_steps(path: Path | None, steps: list[Step]) -> Plan:
    """Helper to create a plan with steps and save it.

    Args:
        path: Path to save plan, or None to skip disk write (in-memory only).
        steps: List of steps for the first phase.
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
# Runner provenance tests
# ---------------------------------------------------------------------------


class TestRunnerProvenance:
    """RunningTask and CompletedResult require runner field.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    Gap: Models have runner, but tests were using old structure without it.
    """

    def test_running_task_requires_runner(self) -> None:
        """RunningTask must have runner field per refreshed contract."""
        # This should work with the refreshed model
        task = RunningTask(
            step_id="s1",
            agent="python-executor",
            task_id="exec-1",
            runner="task",
            dispatched_at=time.time(),
        )
        assert task.runner == "task"
        assert task.runner in ("claude", "task")

    def test_running_task_rejects_unknown_runner(self) -> None:
        """Unknown runner values should be rejected per contract."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RunningTask(
                step_id="s1",
                agent="python-executor",
                task_id="exec-1",
                runner="unknown-runner",  # Invalid per contract
                dispatched_at=time.time(),
            )

    def test_completed_result_requires_runner(self) -> None:
        """CompletedResult must have runner field per refreshed contract."""
        result = CompletedResult(
            step_id="s1",
            task_id="exec-1",
            runner="claude",
            status="SUCCESS",
            output_summary="Done",
        )
        assert result.runner == "claude"


# ---------------------------------------------------------------------------
# task_id as execution identity only tests
# ---------------------------------------------------------------------------


class TestTaskIdSemantics:
    """task_id is orchestrator-visible execution identity, NOT a reuse handle.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    Gap: Old tests asserted action.task_id == reuse_token. That is wrong.
    """

    def test_claim_action_has_task_id_as_identity_only(self, tmp_path: Path) -> None:
        """claim_and_dispatch task_id is execution identity, NOT reuse token."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: tmp_path / "plan.yaml"
        save_plan(plan, tmp_path / "plan.yaml")
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
            )
        finally:
            decide_mod.resolve_plan_path = original

        # Find the claim action
        claim_actions = [a for a in output.actions if a.action == "claim_and_dispatch"]
        assert claim_actions, f"No claim actions found: {output.actions}"

        # task_id should be None for fresh dispatch (not a reuse handle)
        for action in claim_actions:
            # Per contract: task_id is execution identity only, NOT a reuse handle
            # For fresh dispatch, it should be None
            assert action.task_id is None, (
                f"Fresh dispatch should have task_id=None, got {action.task_id}. "
                "task_id is execution identity, NOT a reuse handle."
            )

    def test_reuse_action_has_reuse_token_not_task_id(self, tmp_path: Path) -> None:
        """Reuse semantics come via reuse_token/reuse_runner, not task_id."""
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
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # Parent completed recently - should get reuse hint
            state = DecideState(
                completion_times={"s1": time.time()},
                session_registry={"s1": "ses-abc123"},  # This is the reuse token
            )
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state={
                    "completion_times": state.completion_times,
                    "session_registry": state.session_registry,
                    "failure_counts": {},
                },
            )
        finally:
            decide_mod.resolve_plan_path = original

        # Find the reuse claim action
        claim_actions = [
            a for a in output.actions if a.action == "claim_and_dispatch" and a.step_id == "s2"
        ]
        assert claim_actions, f"No s2 claim actions: {output.actions}"

        action = claim_actions[0]

        # Per contract: reuse_token carries the session token, reuse_runner names the namespace
        assert action.reuse_token == "ses-abc123", (
            f"reuse_token should be 'ses-abc123', got {action.reuse_token}. "
            "Old contract incorrectly used task_id for reuse semantics."
        )
        assert action.reuse_runner == "task", (
            f"reuse_runner should be 'task', got {action.reuse_runner}"
        )
        # task_id should still be None (it's execution identity, not reuse handle)
        assert action.task_id is None

    def test_reuse_runner_follows_completed_result_runner(self, tmp_path: Path) -> None:
        """Same-cycle reuse preserves completion runner provenance.

        Source: step decide_refresh_impl.af1-r8-fix and RFC section 6.1 require
        reuse hints to carry authoritative runner provenance.
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
                            name="Parent",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                            agent="python-executor",
                        ),
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
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=[
                    CompletedResult(
                        step_id="s1",
                        task_id="exec-1",
                        runner="claude",
                        status="SUCCESS",
                        output_summary="Done",
                    )
                ],
                max_parallelism=5,
                advisor_state={
                    "completion_times": {"s1": time.time()},
                    "session_registry": {"s1": "ses-abc123"},
                    "failure_counts": {},
                },
            )
        finally:
            decide_mod.resolve_plan_path = original

        claim_actions = [
            a for a in output.actions if a.action == "claim_and_dispatch" and a.step_id == "s2"
        ]
        assert claim_actions, f"No s2 claim actions: {output.actions}"
        assert claim_actions[0].reuse_token == "ses-abc123"
        assert claim_actions[0].reuse_runner == "claude"

    def test_reuse_runner_round_trips_through_advisor_state(self, tmp_path: Path) -> None:
        """Persisted advisor_state preserves reusable-session provenance.

        Source: step decide_refresh_impl.af1-r8-fix R8 checklist requires
        reuse_runner provenance to survive caller-owned state reuse.
        """
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
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state={
                    "completion_times": {"s1": time.time()},
                    "session_registry": {"s1": "ses-abc123"},
                    "session_runner_registry": {"s1": "claude"},
                    "failure_counts": {},
                },
            )
        finally:
            decide_mod.resolve_plan_path = original

        claim_actions = [
            a for a in output.actions if a.action == "claim_and_dispatch" and a.step_id == "s2"
        ]
        assert claim_actions, f"No s2 claim actions: {output.actions}"
        assert claim_actions[0].reuse_token == "ses-abc123"
        assert claim_actions[0].reuse_runner == "claude"
        assert output.next_state.get("session_runner_registry") == {"s1": "claude"}


# ---------------------------------------------------------------------------
# Caller-owned advisor_state tests
# ---------------------------------------------------------------------------


class TestCallerOwnedState:
    """advisor_state is caller-owned; next_state is full replacement.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.2
    Gap: Old tests used state=DecideState directly; new contract uses dict.
    """

    def test_decide_accepts_advisor_state_dict(self, tmp_path: Path) -> None:
        """decide() must accept advisor_state as dict per refreshed contract."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # Fresh state
            advisor_state: dict[str, object] = {
                "completion_times": {},
                "session_registry": {},
                "failure_counts": {},
            }
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=advisor_state,
            )
        finally:
            decide_mod.resolve_plan_path = original

        assert isinstance(output, DecideOutput)
        assert "next_state" in output.model_fields

    def test_next_state_is_full_replacement_not_merge(self, tmp_path: Path) -> None:
        """next_state is full replacement object, not a merge patch.

        RFC: docs/RFC-vectl-decide-advisor-refresh.md section 7.2.1
        Callers must replace their state with next_state, not merge it.
        """
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # Start with some state
            advisor_state: dict[str, object] = {
                "completion_times": {},
                "session_registry": {},
                "failure_counts": {"other-step": 2},  # Should be replaced, not preserved
            }
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=advisor_state,
            )
        finally:
            decide_mod.resolve_plan_path = original

        # next_state must be a full replacement
        assert isinstance(output.next_state, dict)
        # If there are no completions, failure_counts should NOT contain other-step
        assert "other-step" not in output.next_state.get("failure_counts", {}), (
            "next_state is full replacement, not merge. "
            "Callers must replace their state with next_state, not merge."
        )

    def test_explicit_state_round_trip(self, tmp_path: Path) -> None:
        """Explicit state must round-trip correctly through next_state."""
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
                            name="Parent",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                        ),
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
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # Pre-populate with completed parent
            initial_state: dict[str, object] = {
                "completion_times": {"s1": time.time()},
                "session_registry": {"s1": "ses-xyz"},
                "failure_counts": {},
            }

            # First call: parent completes
            completed = [
                CompletedResult(
                    step_id="s1",
                    task_id="exec-1",
                    runner="claude",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            output1 = decide(
                running_tasks=[],
                completed_results=completed,
                max_parallelism=5,
                advisor_state=initial_state,
            )

            # Verify next_state has updated completion times
            assert "s1" in output1.next_state.get("completion_times", {}), (
                "next_state should include completion_times from result processing"
            )

            # Second call: child should get reuse hint from next_state
            output2 = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=output1.next_state,
            )

            # Child should be eligible for reuse
            reuse_actions = [
                a for a in output2.actions if a.action == "claim_and_dispatch" and a.step_id == "s2"
            ]
            assert reuse_actions, f"Expected reuse action for s2: {output2.actions}"
            assert reuse_actions[0].reuse_token == "ses-xyz", (
                f"Expected reuse_token='ses-xyz' from next_state, "
                f"got {reuse_actions[0].reuse_token}"
            )
        finally:
            decide_mod.resolve_plan_path = original


# ---------------------------------------------------------------------------
# status / reason_code / message tests (no continuation/halt_reason)
# ---------------------------------------------------------------------------


class TestStatusReasonCode:
    """Top-level control uses status / reason_code / message, not continuation/halt_reason.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
    Gap: Old tests asserted output.continuation and output.halt_reason.
    """

    def test_output_has_status_not_continuation(self, tmp_path: Path) -> None:
        """DecideOutput must have status field, not continuation."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
            )
        finally:
            decide_mod.resolve_plan_path = original

        # Per refreshed contract: use status, not continuation
        assert hasattr(output, "status"), "DecideOutput must have status field"
        assert output.status in ("dispatch", "wait", "blocked", "done"), (
            f"status must be one of dispatch/wait/blocked/done, got {output.status}"
        )

        # continuation must NOT exist per refreshed contract
        assert not hasattr(output, "continuation") or output.continuation is None, (
            "continuation field has been removed from refreshed contract. "
            "Use status/reason_code instead."
        )

    def test_output_has_reason_code_not_halt_reason(self, tmp_path: Path) -> None:
        """DecideOutput must have reason_code, not halt_reason."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
            )
        finally:
            decide_mod.resolve_plan_path = original

        # Per refreshed contract: use reason_code
        assert hasattr(output, "reason_code"), "DecideOutput must have reason_code field"
        assert output.reason_code in (
            "dispatch_available",
            "waiting_on_running",
            "capacity_full",
            "no_executable_steps",
            "repeated_failures",
        ), f"Invalid reason_code: {output.reason_code}"

        # halt_reason must NOT exist per refreshed contract
        assert not hasattr(output, "halt_reason") or output.halt_reason is None, (
            "halt_reason field has been removed from refreshed contract. "
            "Use status/reason_code instead."
        )

    def test_status_reason_code_mapping(self, tmp_path: Path) -> None:
        """status and reason_code must follow the mapping table.

        RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
        """
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # Case 1: Work available and capacity open -> dispatch/dispatch_available
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
            )
        finally:
            decide_mod.resolve_plan_path = original

        assert output.status == "dispatch"
        assert output.reason_code == "dispatch_available"

    def test_status_at_capacity(self, tmp_path: Path) -> None:
        """At capacity -> wait/capacity_full."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[
                    RunningTask(
                        step_id="s0",
                        agent="agent-1",
                        task_id="exec-1",
                        runner="task",
                        dispatched_at=time.time(),
                    )
                ],
                completed_results=None,
                max_parallelism=1,  # At capacity
            )
        finally:
            decide_mod.resolve_plan_path = original

        assert output.status == "wait"
        assert output.reason_code == "capacity_full"

    def test_repeated_failures_map_to_blocked_status(self, tmp_path: Path) -> None:
        """Repeated-failure threshold maps to blocked/repeated_failures.

        Source: docs/RFC-vectl-decide-advisor-refresh.md section 6.3 mapping
        table requires repeated_failures -> blocked.
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
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=[
                    CompletedResult(
                        step_id="s1",
                        task_id="task-1",
                        runner="claude",
                        status="FAIL",
                        output_summary="Verification failed again",
                    )
                ],
                max_parallelism=5,
                advisor_state={
                    "completion_times": {},
                    "session_registry": {},
                    "failure_counts": {"s1": ESCALATION_THRESHOLD - 1},
                },
            )
        finally:
            decide_mod.resolve_plan_path = original

        assert output.status == "blocked"
        assert output.reason_code == "repeated_failures"
        assert any(
            action.action == "escalate" and action.step_id == "s1" for action in output.actions
        )
        assert output.next_state.get("failure_counts", {}) == {}


# ---------------------------------------------------------------------------
# Policy metadata tests
# ---------------------------------------------------------------------------


class TestPolicyMetadata:
    """decide() output must include explicit policy metadata.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 7.3
    Gap: Old tests didn't assert on policy field.
    """

    def test_output_includes_policy_metadata(self, tmp_path: Path) -> None:
        """DecideOutput must include policy with reuse_ttl_s and escalation_threshold."""
        plan = _write_plan_with_steps(
            None,
            [Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        decide_mod = importlib.import_module("vectl.decide")
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
            )
        finally:
            decide_mod.resolve_plan_path = original

        assert hasattr(output, "policy"), "DecideOutput must have policy field"
        assert isinstance(output.policy, dict), "policy must be a dict"

        # Policy must contain required fields
        assert "reuse_ttl_s" in output.policy, "policy must include reuse_ttl_s"
        assert "escalation_threshold" in output.policy, "policy must include escalation_threshold"

        assert output.policy["reuse_ttl_s"] == REUSE_TTL
        assert output.policy["escalation_threshold"] == ESCALATION_THRESHOLD


# ---------------------------------------------------------------------------
# Removal of session field from Action
# ---------------------------------------------------------------------------


class TestSessionFieldRemoved:
    """Action.session field has been removed from refreshed contract.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.5
    Gap: Old tests asserted action.session == "reuse" or "fresh".
    """

    def test_action_has_no_session_field(self, tmp_path: Path) -> None:
        """Action must NOT have session field per refreshed contract."""
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
        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            # With parent completed, should get reuse
            state: dict[str, object] = {
                "completion_times": {"s1": time.time()},
                "session_registry": {"s1": "ses-abc"},
                "failure_counts": {},
            }
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=state,
            )
        finally:
            decide_mod.resolve_plan_path = original

        # Find the claim action for s2
        claim_actions = [
            a for a in output.actions if a.action == "claim_and_dispatch" and a.step_id == "s2"
        ]
        assert claim_actions, f"No s2 claim actions: {output.actions}"

        action = claim_actions[0]

        # session field must NOT exist
        assert not hasattr(action, "session") or action.session is None, (
            "Action.session field has been removed from refreshed contract. "
            "Use reuse_token/reuse_runner for reuse semantics."
        )


# ---------------------------------------------------------------------------
# should_reuse_session returns (bool, str|None) - token not task_id
# ---------------------------------------------------------------------------


class TestShouldReuseSessionReturns:
    """should_reuse_session returns (bool, reuse_token) per refreshed contract.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    Gap: Old tests called should_reuse_session with state=DecideState directly.
    """

    def test_should_reuse_returns_reuse_token(self) -> None:
        """should_reuse_session returns (True, reuse_token) when eligible."""
        state = DecideState(
            completion_times={"s1": time.time()},
            session_registry={"s1": "ses-token-123"},
        )

        should_reuse, token = should_reuse_session(
            step_id="s2",
            parent_step_id="s1",
            state=state,
        )

        assert should_reuse is True
        assert token == "ses-token-123", (
            f"Return value is reuse_token, not task_id. Expected 'ses-token-123', got {token}"
        )

    def test_should_reuse_returns_none_for_stale_parent(self) -> None:
        """should_reuse_session returns (False, None) when parent beyond TTL."""
        state = DecideState(
            completion_times={"s1": time.time() - 400},  # Beyond 300s TTL
            session_registry={"s1": "ses-old"},
        )

        should_reuse, token = should_reuse_session(
            step_id="s2",
            parent_step_id="s1",
            state=state,
        )

        assert should_reuse is False
        assert token is None


# ---------------------------------------------------------------------------
# Expected-red verification semantics
# ---------------------------------------------------------------------------


def test_decide_expected_red_fail_completes(tmp_path: Path) -> None:
    """Expected-red step with FAIL result completes (gap demonstrated as intended).

    RFC: docs/RFC-expected-red-verification-semantics.md (Sections 4-5)
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
    decide_mod.resolve_plan_path = lambda: plan_path
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                runner="claude",
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
        decide_mod.resolve_plan_path = original

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
    decide_mod.resolve_plan_path = lambda: plan_path
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                runner="claude",
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
        decide_mod.resolve_plan_path = original

    # First failure should NOT complete
    action_types = [(a.action, a.step_id) for a in output.actions]
    assert ("complete", "s1") not in action_types

    # Status should indicate blocked or wait due to failure
    assert output.status in ("blocked", "wait", "done")


# ---------------------------------------------------------------------------
# Successor visibility after simulated completion
# ---------------------------------------------------------------------------


def test_decide_successor_visible_after_simulated_completion(tmp_path: Path) -> None:
    """decide returns both complete AND claim_and_dispatch for successor in one batch.

    Regression test: successor steps must become visible in the same decide() call.
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

    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path
    try:
        completed = [
            CompletedResult(
                step_id="s1",
                task_id="task-1",
                runner="claude",
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
        decide_mod.resolve_plan_path = original

    action_types = [(a.action, a.step_id) for a in output.actions]
    # Must contain complete(s1) AND claim_and_dispatch(s2) in the same batch
    assert ("complete", "s1") in action_types
    assert ("claim_and_dispatch", "s2") in action_types
