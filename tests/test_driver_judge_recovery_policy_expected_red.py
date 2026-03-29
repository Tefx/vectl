"""Expected-RED tests for judge recovery-policy hardening.

Source:
- docs/DRIVER-ARCHITECTURE.md Section 2.10 (verdict extraction hard-fail
  boundary and REPLAN planner-routing commitments)
- docs/JUDGE-AGENT-PROMPT.md TYPE: failure and TYPE: escalation
  (retry/fallback/halt/remediation decision semantics)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4 and Section 7
  (judge outcomes inform continuity handoff; continuity consumes policy fields)

These tests intentionally fail until implementation owner
`driver-judge-hardening-recovery-policy.impl` wires runtime policy logic.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from src.vectl.driver.judge import (
    JudgeOutcomeKind,
    JudgeRecoveryPolicyInput,
    decide_judge_recovery_policy,
)
from src.vectl.driver.judgments import JudgmentType, JudgmentVerdict
from src.vectl.driver.types import JudgeContinuityPolicyOutput


def _policy_input(
    *,
    outcome_kind: JudgeOutcomeKind,
    failure_count: int,
    retry_budget: int,
    fallback_runner_name: str | None,
    verdict: JudgmentVerdict | None = None,
) -> JudgeRecoveryPolicyInput:
    return JudgeRecoveryPolicyInput(
        step_id="core.impl",
        judgment_type=JudgmentType.FAILURE,
        outcome_kind=outcome_kind,
        attempt_key="core.impl:opencode:session-1",
        failure_count=failure_count,
        retry_budget=retry_budget,
        fallback_runner_name=fallback_runner_name,
        verdict=verdict,
    )


def test_contract_exposes_exact_continuity_policy_fields() -> None:
    """Continuity handoff MUST expose exact policy fields (shape contract)."""

    expected_fields = {
        "step_id",
        "judgment_type",
        "judge_outcome",
        "action",
        "provenance",
        "continuity_recovery_reason",
        "attempt_key",
        "planner_instruction",
        "fallback_runner_name",
        "halt_reason",
    }
    actual_fields = {field.name for field in fields(JudgeContinuityPolicyOutput)}
    assert actual_fields == expected_fields


@pytest.mark.parametrize(
    ("policy_input", "expected_action"),
    [
        (
            _policy_input(
                outcome_kind="timeout",
                failure_count=1,
                retry_budget=2,
                fallback_runner_name="codex",
            ),
            "retry",
        ),
        (
            _policy_input(
                outcome_kind="timeout",
                failure_count=3,
                retry_budget=2,
                fallback_runner_name="codex",
            ),
            "fallback",
        ),
        (
            _policy_input(
                outcome_kind="parse_error",
                failure_count=1,
                retry_budget=1,
                fallback_runner_name="codex",
            ),
            "fallback",
        ),
        (
            _policy_input(
                outcome_kind="malformed_output",
                failure_count=2,
                retry_budget=1,
                fallback_runner_name=None,
            ),
            "halt",
        ),
        (
            _policy_input(
                outcome_kind="empty_output",
                failure_count=2,
                retry_budget=1,
                fallback_runner_name=None,
            ),
            "halt",
        ),
    ],
)
def test_judge_transport_and_parse_failures_route_retry_fallback_or_halt(
    policy_input: JudgeRecoveryPolicyInput,
    expected_action: str,
) -> None:
    """Policy MUST route timeout/parse failures into retry/fallback/halt branches."""

    decision = decide_judge_recovery_policy(policy_input)
    assert decision.action == expected_action


@pytest.mark.parametrize(
    ("verdict", "expected_action"),
    [
        (
            JudgmentVerdict(
                verdict="REPLAN",
                reason="provenance=introduced_now, disposition=downstream_blocker",
                planner_instruction="Create remediation chain and retest gates",
            ),
            "planner_remediation",
        ),
        (
            JudgmentVerdict(
                verdict="RETRY",
                reason="transient transport failure",
            ),
            "retry",
        ),
        (
            JudgmentVerdict(
                verdict="HALT",
                reason="requires human intervention",
            ),
            "halt",
        ),
    ],
)
def test_verdict_driven_branching_routes_planner_remediation_retry_or_halt(
    verdict: JudgmentVerdict,
    expected_action: str,
) -> None:
    """Policy MUST branch from judge verdict semantics, including planner remediation."""

    decision = decide_judge_recovery_policy(
        _policy_input(
            outcome_kind="verdict",
            failure_count=3,
            retry_budget=2,
            fallback_runner_name="codex",
            verdict=verdict,
        )
    )
    assert decision.action == expected_action
