"""Reproduction: vectl_decide advisor contract — RFC conformance black-box.

Verifies the refreshed contract from docs/RFC-vectl-decide-advisor-refresh.md:
1. Refreshed output fields: status/reason_code/message/actions/next_state/policy
2. Removed fields: no continuation, no halt_reason, no Action.session
3. Standalone deterministic advisor behavior with minimal fixtures
4. Runner provenance on RunningTask and CompletedResult
5. Caller-owned advisor_state and next_state full replacement
6. Reuse semantics: reuse_token/reuse_runner instead of overloaded task_id

Expected: All assertions pass against the refreshed contract.
Actual:   Any failure = contract drift or implementation gap.

Spec citation: docs/RFC-vectl-decide-advisor-refresh.md sections 6.1-6.6, 7.1-7.5
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

from vectl.decide import ESCALATION_THRESHOLD, REUSE_TTL, decide, should_reuse_session
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
# Fixtures constructed from RFC contract only (no implementation knowledge)
# ---------------------------------------------------------------------------


def _fresh_advisor_state() -> dict[str, object]:
    """RFC section 7.1: advisor_state shape with completion_times, session_registry, failure_counts."""
    return {
        "completion_times": {},
        "session_registry": {},
        "session_runner_registry": {},
        "failure_counts": {},
    }


def _write_plan_with_claimable_step(path: Path) -> Plan:
    """RFC section 7.5 example: minimal plan with one PENDING step."""
    plan = Plan(
        project="decide-contract-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Step 1", status=StepStatus.PENDING),
                ],
            )
        ],
    )
    save_plan(plan, path)
    return plan


def _write_plan_with_dependency_chain(path: Path) -> Plan:
    """RFC section 6.1: parent->child dependency for reuse test."""
    plan = Plan(
        project="decide-contract-test",
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
    save_plan(plan, path)
    return plan


def _patch_resolve(plan_path: Path):
    """Patch resolve_plan_path to point at test plan (test pattern from existing tests)."""
    decide_mod = importlib.import_module("vectl.decide")
    original = decide_mod.resolve_plan_path
    decide_mod.resolve_plan_path = lambda: plan_path
    return original, decide_mod


def _unpatch(decide_mod, original):
    decide_mod.resolve_plan_path = original


# ===========================================================================
# TEST 1: Refreshed output fields exist per RFC section 7.2
# ===========================================================================


def test_refreshed_output_fields_present():
    """DecideOutput must contain status, reason_code, message, actions, next_state, policy.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 7.2
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    # Verify all refreshed output fields exist
    assert hasattr(output, "status"), (
        "RFC 7.2: DecideOutput must have 'status' field. "
        "Got fields: " + str(list(DecideOutput.model_fields.keys()))
    )
    assert hasattr(output, "reason_code"), (
        "RFC 7.2: DecideOutput must have 'reason_code' field."
    )
    assert hasattr(output, "message"), (
        "RFC 7.2: DecideOutput must have 'message' field."
    )
    assert hasattr(output, "actions"), (
        "RFC 7.2: DecideOutput must have 'actions' field."
    )
    assert hasattr(output, "next_state"), (
        "RFC 7.2: DecideOutput must have 'next_state' field."
    )
    assert hasattr(output, "policy"), (
        "RFC 7.2: DecideOutput must have 'policy' field."
    )
    assert hasattr(output, "decision_log"), (
        "RFC 7.2: DecideOutput must have 'decision_log' field."
    )
    print("PASS: All refreshed output fields present on DecideOutput")


# ===========================================================================
# TEST 2: Removed fields are absent/not-required per RFC section 6.3
# ===========================================================================


def test_removed_fields_absent():
    """continuation and halt_reason must NOT be present on DecideOutput.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
    'continuation / halt_reason are removed from the refreshed contract'
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    # continuation removed per RFC 6.3
    has_continuation = hasattr(output, "continuation") and output.continuation is not None
    assert not has_continuation, (
        "RFC 6.3: 'continuation' has been removed from refreshed contract. "
        "Use status/reason_code instead. Got continuation="
        + str(getattr(output, "continuation", "NOT_PRESENT"))
    )

    # halt_reason removed per RFC 6.3
    has_halt_reason = hasattr(output, "halt_reason") and output.halt_reason is not None
    assert not has_halt_reason, (
        "RFC 6.3: 'halt_reason' has been removed from refreshed contract. "
        "Use reason_code instead. Got halt_reason="
        + str(getattr(output, "halt_reason", "NOT_PRESENT"))
    )
    print("PASS: Removed fields (continuation, halt_reason) absent from DecideOutput")


# ===========================================================================
# TEST 3: Action.session field removed per RFC section 6.1
# ===========================================================================


def test_action_session_field_removed():
    """Action must NOT have 'session' field. Use reuse_token/reuse_runner instead.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    'do not rely on session="fresh"|"reuse" as part of the final contract shape'
    """
    from vectl.models import Action

    action_fields = set(Action.model_fields.keys())
    assert "session" not in action_fields, (
        "RFC 6.1: Action.session has been removed from refreshed contract. "
        "Use reuse_token/reuse_runner instead. Got Action fields: "
        + str(action_fields)
    )
    # Verify replacement fields present
    assert "reuse_token" in action_fields, "RFC 6.1: Action must have reuse_token field"
    assert "reuse_runner" in action_fields, "RFC 6.1: Action must have reuse_runner field"
    print("PASS: Action.session removed, reuse_token/reuse_runner present")


# ===========================================================================
# TEST 4: status/reason_code follow RFC mapping table (RFC section 6.3)
# ===========================================================================


def test_status_reason_code_mapping_dispatch():
    """Work available + capacity open -> status=dispatch, reason_code=dispatch_available.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    assert output.status == "dispatch", (
        f"RFC 6.3: With claimable step and capacity, status must be 'dispatch', "
        f"got '{output.status}'"
    )
    assert output.reason_code == "dispatch_available", (
        f"RFC 6.3: With claimable step and capacity, reason_code must be "
        f"'dispatch_available', got '{output.reason_code}'"
    )
    print("PASS: status=dispatch, reason_code=dispatch_available when work available")


# ===========================================================================
# TEST 5: capacity_full -> status=wait (RFC section 6.3)
# ===========================================================================


def test_status_reason_code_mapping_capacity_full():
    """At parallelism limit -> status=wait, reason_code=capacity_full.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[
                    RunningTask(
                        step_id="s0",
                        agent="agent-1",
                        task_id="exec-0",
                        runner="task",
                        dispatched_at=time.time(),
                    )
                ],
                completed_results=None,
                max_parallelism=1,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    assert output.status == "wait", (
        f"RFC 6.3: At parallelism limit, status must be 'wait', got '{output.status}'"
    )
    assert output.reason_code == "capacity_full", (
        f"RFC 6.3: At parallelism limit, reason_code must be 'capacity_full', "
        f"got '{output.reason_code}'"
    )
    print("PASS: status=wait, reason_code=capacity_full at capacity")


# ===========================================================================
# TEST 6: no_executable_steps -> status=done (RFC section 6.3)
# ===========================================================================


def test_status_reason_code_mapping_done():
    """No executable steps -> status=done, reason_code=no_executable_steps.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        # All steps DONE - no executable work
        plan = Plan(
            project="decide-contract-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1", status=StepStatus.DONE)],
                )
            ],
        )
        save_plan(plan, plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    assert output.status == "done", (
        f"RFC 6.3: With no executable steps, status must be 'done', got '{output.status}'"
    )
    assert output.reason_code == "no_executable_steps", (
        f"RFC 6.3: With no executable steps, reason_code must be 'no_executable_steps', "
        f"got '{output.reason_code}'"
    )
    print("PASS: status=done, reason_code=no_executable_steps when no work")


# ===========================================================================
# TEST 7: Runner provenance on RunningTask and CompletedResult (RFC 6.1)
# ===========================================================================


def test_runner_provenance_required():
    """RunningTask and CompletedResult require runner field. Unknown runners rejected.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    'runner is required; allowed values at rollout are "claude" and "task"'
    """
    from pydantic import ValidationError

    # Valid runners
    task = RunningTask(
        step_id="s1", agent="a", task_id="t1", runner="claude", dispatched_at=time.time()
    )
    assert task.runner == "claude"

    task2 = RunningTask(
        step_id="s2", agent="a", task_id="t2", runner="task", dispatched_at=time.time()
    )
    assert task2.runner == "task"

    result = CompletedResult(
        step_id="s1", task_id="t1", runner="task", status="SUCCESS", output_summary="ok"
    )
    assert result.runner == "task"

    # Unknown runner must be rejected
    try:
        RunningTask(
            step_id="s1",
            agent="a",
            task_id="t1",
            runner="unknown-runner",
            dispatched_at=time.time(),
        )
        assert False, "RFC 6.1: Unknown runner must be rejected by validation"
    except ValidationError:
        pass  # Expected

    # Missing runner on CompletedResult must be rejected
    try:
        CompletedResult(step_id="s1", task_id="t1", status="SUCCESS", output_summary="ok")
        assert False, "RFC 6.1: Missing runner must be rejected by validation"
    except ValidationError:
        pass  # Expected

    print("PASS: Runner provenance required and validated")


# ===========================================================================
# TEST 8: caller-owned advisor_state round-trips correctly (RFC 6.2)
# ===========================================================================


def test_advisor_state_round_trip():
    """advisor_state in -> next_state out; next_state is full replacement.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.2, 7.2.1
    'next_state is a full replacement object, not a merge patch'
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_dependency_chain(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            # First call: complete parent s1
            initial_state: dict[str, object] = {
                "completion_times": {},
                "session_registry": {},
                "session_runner_registry": {},
                "failure_counts": {},
            }
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

            # next_state must be a dict (JSON-serializable per RFC 6.2.1)
            assert isinstance(output1.next_state, dict), (
                f"RFC 6.2.1: next_state must be dict, got {type(output1.next_state)}"
            )

            # Verify completion was recorded
            next_ct = output1.next_state.get("completion_times", {})
            assert "s1" in next_ct, (
                f"RFC 6.2: next_state must record s1 completion. Got: {next_ct}"
            )

            # Second call: pass next_state as advisor_state
            output2 = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=output1.next_state,
            )

            # Child s2 should be claimable now
            claim_actions = [
                a
                for a in output2.actions
                if a.action == "claim_and_dispatch" and a.step_id == "s2"
            ]
            assert claim_actions, (
                f"RFC 6.2: After parent completion via next_state, child s2 must be "
                f"claimable. Got actions: {[(a.action, a.step_id) for a in output2.actions]}"
            )

        finally:
            _unpatch(decide_mod, original)

    print("PASS: advisor_state round-trips correctly; next_state is full replacement")


# ===========================================================================
# TEST 9: next_state is full replacement, NOT merge (RFC 6.2.1)
# ===========================================================================


def test_next_state_is_replacement_not_merge():
    """next_state must NOT preserve stale keys from input advisor_state.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 7.2.1
    'next_state is a full replacement object, not a merge patch'
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            # Advisor state with a stale key that should NOT survive
            advisor_state: dict[str, object] = {
                "completion_times": {},
                "session_registry": {},
                "session_runner_registry": {},
                "failure_counts": {"ghost-step": 99},  # Should be replaced, not preserved
            }
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=advisor_state,
            )
        finally:
            _unpatch(decide_mod, original)

    # The ghost-step failure count must NOT appear in next_state
    # (since no completions happened for it in this cycle)
    next_fc = output.next_state.get("failure_counts", {})
    assert "ghost-step" not in next_fc, (
        f"RFC 7.2.1: next_state is FULL replacement, not merge. "
        f"'ghost-step' should NOT appear in next_state failure_counts. "
        f"Got: {next_fc}"
    )
    print("PASS: next_state is full replacement, not merge patch")


# ===========================================================================
# TEST 10: Reuse token semantics - reuse_token/reuse_runner, not task_id (RFC 6.1)
# ===========================================================================


def test_reuse_token_not_task_id():
    """Reuse advice uses reuse_token/reuse_runner, not overloaded task_id.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    'remove overloaded reuse semantics from Action.task_id; add reuse_token; add reuse_runner'
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_dependency_chain(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            # Complete s1 and pass state showing recent s1 completion
            state: dict[str, object] = {
                "completion_times": {"s1": time.time()},
                "session_registry": {"s1": "ses-token-XYZ"},
                "session_runner_registry": {"s1": "claude"},
                "failure_counts": {},
            }
            completed = [
                CompletedResult(
                    step_id="s1",
                    task_id="exec-1",
                    runner="claude",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            output = decide(
                running_tasks=[],
                completed_results=completed,
                max_parallelism=5,
                advisor_state=state,
            )

            # Find child s2 claim action
            claim_actions = [
                a
                for a in output.actions
                if a.action == "claim_and_dispatch" and a.step_id == "s2"
            ]
            assert claim_actions, (
                f"RFC 6.1: Expected claim_and_dispatch for s2 after s1 completion. "
                f"Actions: {[(a.action, a.step_id) for a in output.actions]}"
            )

            action = claim_actions[0]

            # reuse_token must carry the session token, not task_id
            assert action.reuse_token == "ses-token-XYZ", (
                f"RFC 6.1: reuse_token must be 'ses-token-XYZ', "
                f"got '{action.reuse_token}'. Old contract incorrectly used task_id."
            )

            # reuse_runner must reflect parent's runner provenance
            assert action.reuse_runner == "claude", (
                f"RFC 6.1: reuse_runner must be 'claude' (parent's runner), "
                f"got '{action.reuse_runner}'"
            )

            # task_id on Action must be None (task_id is execution identity, not reuse handle)
            assert action.task_id is None, (
                f"RFC 6.1: Fresh dispatch should have task_id=None "
                f"(task_id is execution identity, NOT a reuse handle). "
                f"Got task_id='{action.task_id}'"
            )

        finally:
            _unpatch(decide_mod, original)

    print("PASS: Reuse semantics use reuse_token/reuse_runner, not overloaded task_id")


# ===========================================================================
# TEST 11: Policy metadata exposed (RFC 7.3)
# ===========================================================================


def test_policy_metadata_exposed():
    """Output must include policy with reuse_ttl_s and escalation_threshold.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 7.3
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            output = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=_fresh_advisor_state(),
            )
        finally:
            _unpatch(decide_mod, original)

    assert isinstance(output.policy, dict), (
        f"RFC 7.3: policy must be dict, got {type(output.policy)}"
    )
    assert "reuse_ttl_s" in output.policy, (
        "RFC 7.3: policy must include 'reuse_ttl_s'"
    )
    assert "escalation_threshold" in output.policy, (
        "RFC 7.3: policy must include 'escalation_threshold'"
    )
    # Verify values match constants
    assert output.policy["reuse_ttl_s"] == REUSE_TTL, (
        f"RFC 7.3: policy.reuse_ttl_s must equal REUSE_TTL ({REUSE_TTL}), "
        f"got {output.policy['reuse_ttl_s']}"
    )
    assert output.policy["escalation_threshold"] == ESCALATION_THRESHOLD, (
        f"RFC 7.3: policy.escalation_threshold must equal ESCALATION_THRESHOLD "
        f"({ESCALATION_THRESHOLD}), got {output.policy['escalation_threshold']}"
    )
    print("PASS: Policy metadata exposed with reuse_ttl_s and escalation_threshold")


# ===========================================================================
# TEST 12: Deterministic behavior (RFC 3.2)
# ===========================================================================


def test_deterministic_same_inputs_same_outputs():
    """vectl_decide must be deterministic: same inputs produce same outputs.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 3.2
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_claimable_step(plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            state1 = _fresh_advisor_state()
            state2 = _fresh_advisor_state()

            output1 = decide(
                running_tasks=[], completed_results=None,
                max_parallelism=5, advisor_state=state1,
            )
            output2 = decide(
                running_tasks=[], completed_results=None,
                max_parallelism=5, advisor_state=state2,
            )

        finally:
            _unpatch(decide_mod, original)

    # Same status/reason_code
    assert output1.status == output2.status, (
        f"Determinism violation: status differs: {output1.status} vs {output2.status}"
    )
    assert output1.reason_code == output2.reason_code, (
        f"Determinism violation: reason_code differs: "
        f"{output1.reason_code} vs {output2.reason_code}"
    )
    # Same number of actions
    assert len(output1.actions) == len(output2.actions), (
        f"Determinism violation: action count differs: "
        f"{len(output1.actions)} vs {len(output2.actions)}"
    )
    # Same action step_ids
    ids1 = [a.step_id for a in output1.actions]
    ids2 = [a.step_id for a in output2.actions]
    assert ids1 == ids2, (
        f"Determinism violation: action step_ids differ: {ids1} vs {ids2}"
    )
    print("PASS: Deterministic behavior -- same inputs, same outputs")


# ===========================================================================
# TEST 13: repeated_failures -> status=blocked (RFC section 6.3, 6.4)
# ===========================================================================


def test_repeated_failures_status_blocked():
    """Repeated failures beyond escalation_threshold -> status=blocked.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.3, 6.4
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        plan = Plan(
            project="decide-contract-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Failing Step",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                        ),
                    ],
                )
            ],
        )
        save_plan(plan, plan_path)

        original, decide_mod = _patch_resolve(plan_path)
        try:
            state = _fresh_advisor_state()
            # Drive failures until threshold reached
            for i in range(ESCALATION_THRESHOLD):
                completed = [
                    CompletedResult(
                        step_id="s1",
                        task_id=f"exec-{i}",
                        runner="claude",
                        status="FAIL",
                        output_summary=f"Failure {i}",
                    ),
                ]
                output = decide(
                    running_tasks=[],
                    completed_results=completed,
                    max_parallelism=5,
                    advisor_state=state,
                )
                state = output.next_state

            # After ESCALATION_THRESHOLD failures, must be blocked
            assert output.status == "blocked", (
                f"RFC 6.3: After {ESCALATION_THRESHOLD} failures, status must be "
                f"'blocked', got '{output.status}'"
            )
            assert output.reason_code == "repeated_failures", (
                f"RFC 6.3: After {ESCALATION_THRESHOLD} failures, reason_code must be "
                f"'repeated_failures', got '{output.reason_code}'"
            )

        finally:
            _unpatch(decide_mod, original)

    print("PASS: repeated_failures -> status=blocked, reason_code=repeated_failures")


# ===========================================================================
# TEST 14: should_reuse_session returns (bool, token) per RFC 6.1
# ===========================================================================


def test_should_reuse_session_contract():
    """should_reuse_session returns (should_reuse, reuse_token) per refreshed contract.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.1
    """
    # Fresh eligible - should reuse
    state = DecideState(
        completion_times={"s1": time.time()},
        session_registry={"s1": "ses-ok"},
    )
    should, token = should_reuse_session(step_id="s2", parent_step_id="s1", state=state)
    assert should is True, f"Expected should=True for recently completed parent"
    assert token == "ses-ok", f"Expected token='ses-ok', got '{token}'"

    # Expired - should NOT reuse
    state_stale = DecideState(
        completion_times={"s1": time.time() - REUSE_TTL - 100},
        session_registry={"s1": "ses-stale"},
    )
    should2, token2 = should_reuse_session(
        step_id="s2", parent_step_id="s1", state=state_stale
    )
    assert should2 is False, "Expected should=False for stale parent"
    assert token2 is None, f"Expected token=None for stale, got '{token2}'"

    print("PASS: should_reuse_session returns (bool, reuse_token) correctly")


# ===========================================================================
# TEST 15: advisor_state=None creates isolated ephemeral state (RFC 6.2.1)
# ===========================================================================


def test_advisor_state_none_isolated():
    """advisor_state=None must not mutate module-global _legacy_state.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md section 6.2.1
    'vectl does not persist advisor state automatically'
    """
    import tempfile
    import vectl.decide as decide_mod

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"
        _write_plan_with_dependency_chain(plan_path)

        # Snapshot legacy state BEFORE
        decide_mod._legacy_state.completion_times = {}
        decide_mod._legacy_state.session_registry = {}

        original = decide_mod.resolve_plan_path
        decide_mod.resolve_plan_path = lambda: plan_path
        try:
            completed = [
                CompletedResult(
                    step_id="s1",
                    task_id="exec-1",
                    runner="claude",
                    status="SUCCESS",
                    output_summary="Done",
                ),
            ]
            decide(
                running_tasks=[],
                completed_results=completed,
                max_parallelism=5,
                advisor_state=None,  # Isolation mode
            )
        finally:
            decide_mod.resolve_plan_path = original

    # Legacy state must remain clean
    assert "s1" not in decide_mod._legacy_state.completion_times, (
        "RFC 6.2.1: advisor_state=None must be isolated from _legacy_state. "
        "s1 leaked into _legacy_state.completion_times"
    )
    print("PASS: advisor_state=None creates isolated ephemeral state")


# ===========================================================================
# Runner: execute all tests
# ===========================================================================


def main():
    tests = [
        test_refreshed_output_fields_present,
        test_removed_fields_absent,
        test_action_session_field_removed,
        test_status_reason_code_mapping_dispatch,
        test_status_reason_code_mapping_capacity_full,
        test_status_reason_code_mapping_done,
        test_runner_provenance_required,
        test_advisor_state_round_trip,
        test_next_state_is_replacement_not_merge,
        test_reuse_token_not_task_id,
        test_policy_metadata_exposed,
        test_deterministic_same_inputs_same_outputs,
        test_repeated_failures_status_blocked,
        test_should_reuse_session_contract,
        test_advisor_state_none_isolated,
    ]

    failures = 0
    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            failures += 1
            print(f"FAIL: {test_fn.__name__}")
            print(f"  Error: {e}")

    print(f"\n{'='*70}")
    print(f"Results: {len(tests) - failures}/{len(tests)} passed, {failures} failed")
    if failures > 0:
        print("VERDICT: FAILED -- See individual test failures above")
        raise SystemExit(1)
    else:
        print("VERDICT: ALL PASSED -- vectl_decide contract conforms to RFC")


if __name__ == "__main__":
    main()
