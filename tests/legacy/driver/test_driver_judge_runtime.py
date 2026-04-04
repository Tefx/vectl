"""Runtime behavior tests for Judge invocation (EXPECTED-RED).

These tests verify implementation behavior:
- Request validation (missing required context raises ValueError)
- Structured output parsing (valid JSON accepted, malformed rejected)
- Timeout handling (raises JudgmentTimeoutError)
- Observer emission (JUDGMENT event emitted)
- Disabled judgment types (is_enabled returns False when configured off)

NOTE: These tests WILL FAIL until implementation is complete.
They are EXPECTED-RED tests that document required behavior.
The downstream implementation step is: driver-judgment-runtime-minimal.impl-judge

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md
Blueprint Reference: DRIVER-BLUEPRINT.md lines 241-264
"""

import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.vectl.driver.config import JudgeConfig
from src.vectl.driver.errors import JudgmentParseError, JudgmentTimeoutError
from src.vectl.driver.judge import Judge, VERDICT_SCHEMA
from src.vectl.driver.judgments import (
    CONTEXT_SCHEMAS,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
)


# =============================================================================
# MOCK FIXTURES FOR TESTING
# =============================================================================


class MockObserver:
    """Mock Observer for testing Judge event emission."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, **data: object) -> None:
        """Record emitted events."""
        self.events.append({"type": event_type, "data": data})

    def close(self) -> None:
        """No-op close."""
        pass


@pytest.fixture
def mock_observer() -> MockObserver:
    """Fixture for mock observer."""
    return MockObserver()


@pytest.fixture
def judge_config() -> JudgeConfig:
    """Fixture for judge config with all judgment types enabled."""
    return JudgeConfig(
        runner="opencode",
        model=None,
        structured_output=True,
        timeout=60,
        preflight=True,
        evidence_validation=True,
        failure_classification=True,
        escalation=True,
        gate_assessment=True,
        cold_context=True,
        anomaly=True,
        skip_preflight_for=[],
    )


@pytest.fixture
def judge_config_disabled() -> JudgeConfig:
    """Fixture for judge config with some judgment types disabled."""
    return JudgeConfig(
        runner="opencode",
        model=None,
        structured_output=True,
        timeout=60,
        preflight=False,  # Disabled
        evidence_validation=True,
        failure_classification=False,  # Disabled
        escalation=True,
        gate_assessment=False,  # Disabled
        cold_context=True,
        anomaly=False,  # Disabled
        skip_preflight_for=[],
    )


# =============================================================================
# REQUEST VALIDATION TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeRequestValidation:
    """Tests for Judge request validation.

    EXPECTED-RED: These tests will fail until Judge._validate_request is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge.__init__ not implemented (expected-red)")
    async def test_init_creates_judge_instance(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.__init__ MUST create a Judge instance with config and observer."""
        judge = Judge(judge_config, mock_observer)
        assert judge is not None

    @pytest.mark.xfail(reason="Judge._validate_request not implemented (expected-red)")
    def test_validate_request_missing_required_field_raises_value_error(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._validate_request MUST raise ValueError if required context fields are missing."""
        judge = Judge(judge_config, mock_observer)

        # Create request missing required field "step_description"
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                # Missing "step_description" (required)
                "step_id": "core.impl",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        with pytest.raises(ValueError, match="step_description"):
            judge._validate_request(request)

    @pytest.mark.xfail(reason="Judge._validate_request not implemented (expected-red)")
    def test_validate_request_all_required_fields_passes(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._validate_request MUST pass when all required fields are present."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement feature",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        # Should NOT raise
        judge._validate_request(request)

    @pytest.mark.xfail(reason="Judge._validate_request not implemented (expected-red)")
    def test_validate_request_missing_multiple_required_fields(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._validate_request MUST report all missing required fields."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={
                # Only has step_id, missing step_verification and evidence
                "step_id": "test.step",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        with pytest.raises(ValueError):  # Should mention both missing fields
            judge._validate_request(request)

    @pytest.mark.xfail(reason="Judge._validate_request not implemented (expected-red)")
    def test_validate_request_extra_optional_fields_allowed(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._validate_request MUST allow optional fields that aren't in required."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement feature",
                "step_verification": "Tests pass",
                "step_refs": "[]",
                # Optional fields
                "risk_signals": "migration,CAS",
                "phase_context": "Core implementation phase",
                "extra_unknown_field": "should be allowed",  # Extra field OK
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        # Should NOT raise
        judge._validate_request(request)


# =============================================================================
# IS_ENABLED TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeIsEnabled:
    """Tests for Judge.is_enabled configuration checks.

    EXPECTED-RED: These tests will fail until Judge.is_enabled is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge.is_enabled not implemented (expected-red)")
    def test_is_enabled_returns_true_for_enabled_type(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.is_enabled MUST return True when judgment type is enabled in config."""
        judge = Judge(judge_config, mock_observer)

        assert judge.is_enabled(JudgmentType.PREFLIGHT) is True
        assert judge.is_enabled(JudgmentType.EVIDENCE) is True
        assert judge.is_enabled(JudgmentType.ESCALATION) is True

    @pytest.mark.xfail(reason="Judge.is_enabled not implemented (expected-red)")
    def test_is_enabled_returns_false_for_disabled_type(
        self, judge_config_disabled: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.is_enabled MUST return False when judgment type is disabled in config."""
        judge = Judge(judge_config_disabled, mock_observer)

        assert judge.is_enabled(JudgmentType.PREFLIGHT) is False
        assert judge.is_enabled(JudgmentType.FAILURE) is False
        assert judge.is_enabled(JudgmentType.GATE) is False
        assert judge.is_enabled(JudgmentType.ANOMALY) is False

    @pytest.mark.xfail(reason="Judge.is_enabled not implemented (expected-red)")
    def test_is_enabled_all_types_respect_config(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.is_enabled MUST respect config for all judgment types."""
        judge = Judge(judge_config, mock_observer)

        # All enabled with config where all flags are True
        for jtype in JudgmentType:
            assert judge.is_enabled(jtype) is True


# =============================================================================
# PARSE VERDICT TESTS (EXPECTED-RED)
# =============================================================================


class TestParseVerdict:
    """Tests for Judge._parse_verdict structured output parsing.

    EXPECTED-RED: These tests will fail until Judge._parse_verdict is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_valid_json_accept(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST parse valid JSON verdict with ACCEPT."""
        judge = Judge(judge_config, mock_observer)

        valid_json = '{"verdict": "ACCEPT", "reason": "Tests pass"}'
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        verdict = judge._parse_verdict(valid_json, request)
        assert verdict.verdict == "ACCEPT"
        assert verdict.reason == "Tests pass"
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_valid_json_switch_agent(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST parse SWITCH_AGENT verdict with suggested_action."""
        judge = Judge(judge_config, mock_observer)

        valid_json = '{"verdict": "SWITCH_AGENT", "reason": "Need frontend expert", "suggested_action": "frontend-engineer"}'
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "errors",
                "step_description": "Failing step",
                "available_agents": "python-senior,frontend-engineer",
            },
            failure_history=["error1", "error2"],
            plan_summary="Phase 1",
        )

        verdict = judge._parse_verdict(valid_json, request)
        assert verdict.verdict == "SWITCH_AGENT"
        assert verdict.suggested_action == "frontend-engineer"

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_valid_json_replan(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST parse REPLAN verdict with planner_instruction."""
        judge = Judge(judge_config, mock_observer)

        valid_json = '{"verdict": "REPLAN", "reason": "Spec needs CAS handling", "planner_instruction": "Add CAS conflict resolution"}'
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        verdict = judge._parse_verdict(valid_json, request)
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction == "Add CAS conflict resolution"

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_malformed_json_raises_parse_error(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST raise JudgmentParseError on malformed JSON."""
        judge = Judge(judge_config, mock_observer)

        malformed = '{"verdict": "ACCEPT", "reason": incomplete'
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        with pytest.raises(JudgmentParseError):
            judge._parse_verdict(malformed, request)

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_invalid_verdict_value_raises_parse_error(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST raise JudgmentParseError on invalid verdict value."""
        judge = Judge(judge_config, mock_observer)

        invalid_value = '{"verdict": "INVALID", "reason": "Bad"}'
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        with pytest.raises(JudgmentParseError):
            judge._parse_verdict(invalid_value, request)

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_missing_required_field_raises_parse_error(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST raise JudgmentParseError when required fields missing."""
        judge = Judge(judge_config, mock_observer)

        missing_verdict = '{"reason": "No verdict field"}'
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        with pytest.raises(JudgmentParseError):
            judge._parse_verdict(missing_verdict, request)

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_switch_agent_without_suggested_action_raises(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST raise error if SWITCH_AGENT lacks suggested_action."""
        judge = Judge(judge_config, mock_observer)

        invalid_switch = '{"verdict": "SWITCH_AGENT", "reason": "Need switch"}'
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "errors",
                "step_description": "Failing step",
                "available_agents": "python-senior",
            },
            failure_history=["error1"],
            plan_summary="Phase 1",
        )

        # SWITCH_AGENT MUST have suggested_action
        with pytest.raises((JudgmentParseError, ValueError)):
            judge._parse_verdict(invalid_switch, request)

    @pytest.mark.xfail(reason="Judge._parse_verdict not implemented (expected-red)")
    def test_parse_verdict_replan_without_planner_instruction_raises(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._parse_verdict MUST raise error if REPLAN lacks planner_instruction."""
        judge = Judge(judge_config, mock_observer)

        invalid_replan = '{"verdict": "REPLAN", "reason": "Need to replan"}'
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        # REPLAN MUST have planner_instruction
        with pytest.raises((JudgmentParseError, ValueError)):
            judge._parse_verdict(invalid_replan, request)


# =============================================================================
# TIMEOUT HANDLING TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeTimeout:
    """Tests for Judge timeout handling.

    EXPECTED-RED: These tests will fail until Judge._invoke_runner timeout is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge._invoke_runner not implemented (expected-red)")
    async def test_invoke_runner_raises_timeout_on_timeout(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._invoke_runner MUST raise JudgmentTimeoutError on timeout."""
        judge = Judge(judge_config, mock_observer)

        with pytest.raises(JudgmentTimeoutError) as exc_info:
            await judge._invoke_runner("system prompt", "user prompt")

        # Verify timeout seconds matches config
        assert exc_info.value.timeout_seconds == judge_config.timeout

    @pytest.mark.xfail(reason="Judge.judge not implemented (expected-red)")
    async def test_judge_raises_timeout_on_runner_timeout(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.judge MUST raise JudgmentTimeoutError when runner times out."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        with pytest.raises(JudgmentTimeoutError):
            await judge.judge(request)


# =============================================================================
# OBSERVER EMISSION TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeObserverEmission:
    """Tests for Judge observer event emission.

    EXPECTED-RED: These tests will fail until Judge._emit_judgment_event is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge._emit_judgment_event not implemented (expected-red)")
    def test_emit_judgment_event_records_event(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._emit_judgment_event MUST emit JUDGMENT event to observer."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )
        verdict = JudgmentVerdict(verdict="ACCEPT", reason="Evidence accepted")

        judge._emit_judgment_event(request, verdict, latency_ms=150.5)

        # Check event was emitted
        assert len(mock_observer.events) == 1
        event = mock_observer.events[0]
        assert event["type"] == "JUDGMENT"

    @pytest.mark.xfail(reason="Judge._emit_judgment_event not implemented (expected-red)")
    def test_emit_judgment_event_includes_all_fields(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._emit_judgment_event MUST include type, step_id, verdict, reason, latency_ms."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "errors",
                "step_description": "Failing step",
                "available_agents": "python-senior",
            },
            failure_history=["error"],
            plan_summary="Phase 1",
        )
        verdict = JudgmentVerdict(verdict="RETRY", reason="Transient error")

        judge._emit_judgment_event(request, verdict, latency_ms=320.0)

        event = mock_observer.events[0]
        data = event["data"]

        assert data.get("type") == "escalation"
        assert data.get("step_id") == "failing.step"
        assert data.get("verdict") == "RETRY"
        assert data.get("reason") == "Transient error"
        assert data.get("latency_ms") == 320.0

    @pytest.mark.xfail(reason="Judge.judge not implemented (expected-red)")
    async def test_judge_emits_event_on_success(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.judge MUST emit JUDGMENT event after successful invocation.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
        Blueprint: DRIVER-BLUEPRINT.md line 248
        """
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={"step_id": "test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        # After implementation, this will emit an event
        # For now, expect it to fail because not implemented
        verdict = await judge.judge(request)

        # Verify event was emitted
        assert len(mock_observer.events) >= 1
        assert mock_observer.events[0]["type"] == "JUDGMENT"


# =============================================================================
# RENDER REQUEST TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeRenderRequest:
    """Tests for Judge._render_request.

    EXPECTED-RED: These tests will fail until Judge._render_request is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge._render_request not implemented (expected-red)")
    def test_render_request_returns_string(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._render_request MUST return a string prompt."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement feature",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        prompt = judge._render_request(request)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    @pytest.mark.xfail(reason="Judge._render_request not implemented (expected-red)")
    def test_render_request_includes_step_id(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._render_request MUST include step_id in the prompt."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="my.test.step",
            context={"step_id": "my.test.step", "step_verification": "Tests", "evidence": "OK"},
            failure_history=[],
            plan_summary="Test",
        )

        prompt = judge._render_request(request)
        assert "my.test.step" in prompt

    @pytest.mark.xfail(reason="Judge._render_request not implemented (expected-red)")
    def test_render_request_includes_judgment_type(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge._render_request MUST include judgment type in the prompt."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "errors",
                "step_description": "Failing step",
                "available_agents": "python-senior",
            },
            failure_history=["error1", "error2"],
            plan_summary="Phase 1",
        )

        prompt = judge._render_request(request)
        # Type should be present (either as "ESCALATION" or "escalation")
        assert "escalation" in prompt.lower() or "ESCALATION" in prompt


# =============================================================================
# JUDGE INTEGRATION TESTS (EXPECTED-RED)
# =============================================================================


class TestJudgeIntegration:
    """Integration tests for Judge.judge full flow.

    EXPECTED-RED: These tests will fail until Judge.judge is implemented.
    Implementation owner: driver-judgment-runtime-minimal.impl-judge
    """

    @pytest.mark.xfail(reason="Judge.judge not implemented (expected-red)")
    async def test_judge_valid_request_returns_verdict(
        self, judge_config: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.judge MUST return JudgmentVerdict for valid request."""
        judge = Judge(judge_config, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={
                "step_id": "test.step",
                "step_verification": "Tests",
                "evidence": "All tests pass",
            },
            failure_history=[],
            plan_summary="Test phase",
        )

        verdict = await judge.judge(request)

        assert isinstance(verdict, JudgmentVerdict)
        assert verdict.verdict in {
            "ACCEPT",
            "REJECT",
            "RETRY",
            "SWITCH_AGENT",
            "REPLAN",
            "DEFER",
            "HALT",
        }
        assert len(verdict.reason) > 0

    @pytest.mark.xfail(reason="Judge.judge not implemented (expected-red)")
    async def test_judge_disabled_type_raises_error(
        self, judge_config_disabled: JudgeConfig, mock_observer: MockObserver
    ) -> None:
        """Judge.judge MUST raise error or skip silently when judgment type is disabled.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
        When is_enabled returns False, judge should not invoke LLM.
        """
        judge = Judge(judge_config_disabled, mock_observer)

        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,  # Disabled in config
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        # Should either raise or return a default verdict
        # Implementation choice: may raise ValueError or return early
        with pytest.raises((ValueError, RuntimeError)):
            await judge.judge(request)


# =============================================================================
# EXPOSED GAPS (FOR DOWNSTREAM IMPLEMENTATION)
# =============================================================================

# GAPS documented for implementation step: driver-judgment-runtime-minimal.impl-judge
#
# 1. Judge.__init__: Need to initialize with JudgeConfig and Observer
# 2. Judge.is_enabled: Need to check config flags for each JudgmentType
# 3. Judge._validate_request: Need to validate context against CONTEXT_SCHEMAS
# 4. Judge._render_request: Need to render JudgmentRequest to prompt string
# 5. Judge._invoke_runner: Need to call runner with timeout handling
# 6. Judge._parse_verdict: Need to parse JSON and verify verdict constraints
# 7. Judge._emit_judgment_event: Need to emit JUDGMENT event to observer
# 8. Judge.judge: Need to wire all methods together
#
# Mock runner for testing implementation:
# - Requires JudgeRunner protocol implementation (dispatch_structured/dispatch_text)
# - Need mock runner fixture that returns predetermined verdicts
# - Need mock runner fixture that simulates timeout
# - Need mock runner fixture that returns malformed JSON
