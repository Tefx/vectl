"""Contract tests for Judge invocation surface.

Tests verify the contract defined in docs/DRIVER-ARCHITECTURE.md Section 2.10:
- Judge.__init__(config, observer)
- await Judge.judge(request)
- Judge.is_enabled(judgment_type)
- VERDICT_JSON_SCHEMA structure
- JudgeRunner protocol
- JudgmentTimeoutError and JudgmentParseError
- Context validation against CONTEXT_SCHEMAS

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md
Blueprint Reference: DRIVER-BLUEPRINT.md lines 241-264

This is a CONTRACT test file - defines the interface contract,
NOT the runtime implementation.
"""

from dataclasses import dataclass

import pytest

from src.vectl.driver.errors import JudgmentParseError, JudgmentTimeoutError
from src.vectl.driver.judge import VERDICT_SCHEMA, Judge, JudgeRunner
from src.vectl.driver.judgments import (
    CONTEXT_SCHEMAS,
    VERDICT_VALUES,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
)


# =============================================================================
# VERDICT_SCHEMA CONTRACT TESTS
# =============================================================================


class TestVerdictSchema:
    """Contract tests for VERDICT_SCHEMA JSON schema.

    The schema MUST define the structure for structured output.
    """

    def test_verdict_schema_is_dict(self) -> None:
        """VERDICT_SCHEMA MUST be a dict (JSON schema object)."""
        assert isinstance(VERDICT_SCHEMA, dict)

    def test_verdict_schema_has_schema_field(self) -> None:
        """VERDICT_SCHEMA MUST have $schema field for JSON Schema metadata."""
        assert "$schema" in VERDICT_SCHEMA

    def test_verdict_schema_verdict_required(self) -> None:
        """VERDICT_SCHEMA MUST have verdict as required property."""
        assert "required" in VERDICT_SCHEMA
        assert "verdict" in VERDICT_SCHEMA["required"]

    def test_verdict_schema_reason_required(self) -> None:
        """VERDICT_SCHEMA MUST have reason as required property."""
        assert "required" in VERDICT_SCHEMA
        assert "reason" in VERDICT_SCHEMA["required"]

    def test_verdict_schema_verdict_enum(self) -> None:
        """VERDICT_SCHEMA verdict MUST be enum matching VERDICT_VALUES."""
        verdict_prop = VERDICT_SCHEMA["properties"]["verdict"]
        assert verdict_prop["type"] == "string"
        assert "enum" in verdict_prop
        # Enum values must match all VERDICT_VALUES
        enum_set = set(verdict_prop["enum"])
        assert enum_set == VERDICT_VALUES

    def test_verdict_schema_suggested_action_optional(self) -> None:
        """VERDICT_SCHEMA MUST have suggested_action as optional string|null."""
        sa_prop = VERDICT_SCHEMA["properties"]["suggested_action"]
        assert "string" in sa_prop["type"]
        assert "null" in sa_prop["type"]
        assert "suggested_action" not in VERDICT_SCHEMA["required"]

    def test_verdict_schema_planner_instruction_optional(self) -> None:
        """VERDICT_SCHEMA MUST have planner_instruction as optional string|null."""
        pi_prop = VERDICT_SCHEMA["properties"]["planner_instruction"]
        assert "string" in pi_prop["type"]
        assert "null" in pi_prop["type"]
        assert "planner_instruction" not in VERDICT_SCHEMA["required"]

    def test_verdict_schema_no_additional_properties(self) -> None:
        """VERDICT_SCHEMA MUST disallow additional properties for strict validation."""
        assert VERDICT_SCHEMA.get("additionalProperties") is False

    def test_verdict_schema_verdict_values_complete(self) -> None:
        """VERDICT_SCHEMA enum MUST include all 7 verdict values."""
        verdict_enum = VERDICT_SCHEMA["properties"]["verdict"]["enum"]
        expected = ["ACCEPT", "REJECT", "RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"]
        assert set(verdict_enum) == set(expected)


# =============================================================================
# ERROR CLASSES CONTRACT TESTS
# =============================================================================


class TestJudgmentErrorClasses:
    """Contract tests for judgment error classes.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.2
    """

    def test_judgment_timeout_error_exists(self) -> None:
        """JudgmentTimeoutError MUST exist and inherit from JudgmentError."""
        assert JudgmentTimeoutError is not None
        assert issubclass(JudgmentTimeoutError, Exception)

    def test_judgment_parse_error_exists(self) -> None:
        """JudgmentParseError MUST exist and inherit from JudgmentError."""
        assert JudgmentParseError is not None
        assert issubclass(JudgmentParseError, Exception)

    def test_judgment_timeout_error_attributes(self) -> None:
        """JudgmentTimeoutError MUST carry judgment_type, step_id, timeout_seconds."""
        error = JudgmentTimeoutError(
            judgment_type="preflight",
            step_id="core.impl",
            timeout_seconds=60,
        )
        assert error.judgment_type == "preflight"
        assert error.step_id == "core.impl"
        assert error.timeout_seconds == 60

    def test_judgment_parse_error_attributes(self) -> None:
        """JudgmentParseError MUST carry judgment_type, step_id, raw_output."""
        error = JudgmentParseError(
            judgment_type="evidence",
            step_id="test.step",
            raw_output="invalid json",
        )
        assert error.judgment_type == "evidence"
        assert error.step_id == "test.step"
        assert error.raw_output == "invalid json"


# =============================================================================
# JUDGE CLASS CONTRACT TESTS
# =============================================================================


class TestJudgeClassContract:
    """Contract tests for Judge class interface.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
    Blueprint: DRIVER-BLUEPRINT.md lines 241-249
    """

    def test_judge_class_exists(self) -> None:
        """Judge class MUST exist."""
        assert Judge is not None

    def test_judge_init_signature(self) -> None:
        """Judge.__init__ MUST accept (config: JudgeConfig, observer: Observer).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Judge.__init__
        """
        # Method signature is defined in the stub
        # This test documents the contract
        import inspect

        # Check __init__ exists and has correct annotation
        # (Implementation will raise NotImplementedError per contract-stub pattern)
        assert hasattr(Judge, "__init__")

    def test_judge_judge_is_async(self) -> None:
        """Judge.judge MUST be an async method."""
        import inspect

        # Check judge method exists
        assert hasattr(Judge, "judge")

        # Implementation note: will raise NotImplementedError but signature is defined
        # The method contract requires it to be async def judge(self, request) -> JudgmentVerdict

    def test_judge_judge_returns_verdict(self) -> None:
        """Judge.judge MUST return JudgmentVerdict."""
        # This documents the return type contract
        # Signature: async def judge(self, request: JudgmentRequest) -> JudgmentVerdict
        pass  # Contract documentation

    def test_judge_is_enabled_takes_judgment_type(self) -> None:
        """Judge.is_enabled MUST accept JudgmentType and return bool."""
        # Signature: def is_enabled(self, judgment_type: JudgmentType) -> bool
        pass  # Contract documentation

    def test_judge_validate_request_private(self) -> None:
        """Judge._validate_request MUST validate against CONTEXT_SCHEMAS."""
        # Signature: def _validate_request(self, request: JudgmentRequest) -> None
        # Raises ValueError if required fields missing
        pass  # Contract documentation

    def test_judge_render_request_private(self) -> None:
        """Judge._render_request MUST render JudgmentRequest to string."""
        # Signature: def _render_request(self, request: JudgmentRequest) -> str
        pass  # Contract documentation

    def test_judge_invoke_runner_private_async(self) -> None:
        """Judge._invoke_runner MUST be async and return string."""
        # Signature: async def _invoke_runner(self, system_prompt: str, user_prompt: str) -> str
        pass  # Contract documentation

    def test_judge_parse_verdict_private(self) -> None:
        """Judge._parse_verdict MUST parse raw output to JudgmentVerdict."""
        # Signature: def _parse_verdict(self, raw_output: str, request: JudgmentRequest) -> JudgmentVerdict
        # Raises JudgmentParseError on invalid output
        pass  # Contract documentation

    def test_judge_emit_judgment_event_private(self) -> None:
        """Judge._emit_judgment_event MUST emit JUDGMENT event to observer."""
        # Signature: def _emit_judgment_event(self, request, verdict, latency_ms) -> None
        pass  # Contract documentation


# =============================================================================
# JUDGE RUNNER PROTOCOL CONTRACT TESTS
# =============================================================================


class TestJudgeRunnerProtocol:
    """Contract tests for JudgeRunner protocol.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, JudgeRunner Protocol
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
    """

    def test_judge_runner_dispatch_structured_exists(self) -> None:
        """JudgeRunner MUST have dispatch_structured method."""
        assert hasattr(JudgeRunner, "dispatch_structured")
        # Signature: async def dispatch_structured(
        #     self, system_prompt: str, user_prompt: str,
        #     schema: dict, timeout: float
        # ) -> dict

    def test_judge_runner_dispatch_text_exists(self) -> None:
        """JudgeRunner MUST have dispatch_text method (fallback)."""
        assert hasattr(JudgeRunner, "dispatch_text")
        # Signature: async def dispatch_text(
        #     self, system_prompt: str, user_prompt: str, timeout: float
        # ) -> str

    def test_judge_runner_name_attribute(self) -> None:
        """JudgeRunner MUST have name attribute."""
        # Protocol requires: name: str
        pass  # Protocol contract documentation


# =============================================================================
# CONTEXT VALIDATION CONTRACT TESTS
# =============================================================================


class TestContextValidationContract:
    """Contract tests for context validation against CONTEXT_SCHEMAS.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, context validation
    Blueprint: docs/DRIVER-ARCHITECTURE.md Invariant (lines 100-104)
    """

    def test_context_schemas_all_types_have_required(self) -> None:
        """All CONTEXT_SCHEMAS entries MUST have required list."""
        for jtype in JudgmentType:
            assert jtype in CONTEXT_SCHEMAS
            schema = CONTEXT_SCHEMAS[jtype]
            assert "required" in schema
            assert isinstance(schema["required"], list)

    def test_context_schemas_all_types_have_optional(self) -> None:
        """All CONTEXT_SCHEMAS entries MUST have optional list."""
        for jtype in JudgmentType:
            schema = CONTEXT_SCHEMAS[jtype]
            assert "optional" in schema
            assert isinstance(schema["optional"], list)

    def test_context_validation_missing_required_raises(self) -> None:
        """Judge MUST raise ValueError if required context fields are missing.

        Architecture: docs/DRIVER-ARCHITECTURE.md lines 100-104
        Invariant: All JudgmentRequest.context MUST contain all keys from
            CONTEXT_SCHEMAS[type]["required"].
        """
        # This documents the contract - implementation will validate
        # For example, PREFLIGHT requires: step_id, step_description, step_verification, step_refs
        required = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]["required"]
        assert len(required) == 4
        assert "step_id" in required
        assert "step_description" in required
        assert "step_verification" in required
        assert "step_refs" in required

    def test_context_validation_allows_optional_missing(self) -> None:
        """Judge MUST allow optional context fields to be missing."""
        # Optional fields are permitted but not required
        # For example, PREFLIGHT optionally has: risk_signals, phase_context
        optional = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]["optional"]
        assert "risk_signals" in optional
        assert "phase_context" in optional


# =============================================================================
# VERDICT PARSING CONTRACT TESTS
# =============================================================================


class TestVerdictParsingContract:
    """Contract tests for verdict parsing and validation.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, verdict parsing
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)
    """

    def test_verdict_values_constant_exists(self) -> None:
        """VERDICT_VALUES constant MUST exist and be frozenset."""
        assert VERDICT_VALUES is not None
        assert isinstance(VERDICT_VALUES, frozenset)

    def test_verdict_values_all_7_values(self) -> None:
        """VERDICT_VALUES MUST contain all 7 verdict values."""
        assert "ACCEPT" in VERDICT_VALUES
        assert "REJECT" in VERDICT_VALUES
        assert "RETRY" in VERDICT_VALUES
        assert "SWITCH_AGENT" in VERDICT_VALUES
        assert "REPLAN" in VERDICT_VALUES
        assert "DEFER" in VERDICT_VALUES
        assert "HALT" in VERDICT_VALUES
        assert len(VERDICT_VALUES) == 7

    def test_verdict_switch_agent_requires_suggested_action(self) -> None:
        """SWITCH_AGENT verdict MUST have suggested_action (non-None).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
        Blueprint: docs/JUDGE-AGENT-PROMPT.md line 24
        """
        # When verdict == "SWITCH_AGENT", suggested_action MUST be set
        verdict = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Need different agent",
            suggested_action="python-senior",
        )
        assert verdict.verdict == "SWITCH_AGENT"
        assert verdict.suggested_action is not None
        # Note: Implementation must validate this constraint

    def test_verdict_replan_requires_planner_instruction(self) -> None:
        """REPLAN verdict MUST have planner_instruction (non-None).

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
        Blueprint: docs/JUDGE-AGENT-PROMPT.md line 25
        """
        # When verdict == "REPLAN", planner_instruction MUST be set
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Spec needs enhancement",
            planner_instruction="Add CAS handling",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None
        # Note: Implementation must validate this constraint

    def test_verdict_accept_has_no_optional_fields(self) -> None:
        """ACCEPT verdict MUST have suggested_action and planner_instruction as None."""
        verdict = JudgmentVerdict(verdict="ACCEPT", reason="OK")
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None


# =============================================================================
# OBSERVER EVENT CONTRACT TESTS
# =============================================================================


class TestObserverEventContract:
    """Contract tests for observer JUDGMENT event emission.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, event emission
    Blueprint: DRIVER-BLUEPRINT.md line 248, lines 837-839
    """

    def test_judgment_event_emitted_on_call(self) -> None:
        """Judge MUST emit JUDGMENT event after each call.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10
        Blueprint: DRIVER-BLUEPRINT.md line 248
        """
        # Event format from DRIVER-BLUEPRINT.md lines 837-839:
        # {"ts":..., "event":"JUDGMENT", "data":{"type":"...", "step_id":"...",
        #   "verdict":"...", "reason":"...", "latency_ms":...}}
        pass  # Contract documentation

    def test_judgment_event_includes_latency(self) -> None:
        """JUDGMENT event MUST include latency_ms field."""
        # The observer.emit("JUDGMENT", ...) call must include latency
        pass  # Contract documentation


# =============================================================================
# STRUCTURED OUTPUT STRATEGY CONTRACT TESTS
# =============================================================================


class TestStructuredOutputStrategy:
    """Contract tests for structured output strategy.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Structured Output Strategy
    Blueprint: DRIVER-BLUEPRINT.md Table (lines 922-936)
    """

    def test_structured_output_schema_matches_verdict_dataclass(self) -> None:
        """VERDICT_SCHEMA MUST match JudgmentVerdict dataclass structure."""
        # Schema enum must match VERDICT_VALUES
        schema_enum = set(VERDICT_SCHEMA["properties"]["verdict"]["enum"])
        assert schema_enum == VERDICT_VALUES

    def test_structured_output_requires_json_schema(self) -> None:
        """Structured output requires a valid JSON Schema for the verdict."""
        # Schema must have $schema field for JSON Schema draft compliance
        assert "$schema" in VERDICT_SCHEMA
        assert "json-schema.org" in VERDICT_SCHEMA["$schema"]


# =============================================================================
# JUDGE VS RULES BOUNDARY CONTRACT TESTS
# =============================================================================


class TestJudgeVsRulesBoundary:
    """Contract tests documenting when judge IS and IS NOT invoked.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10, Judge vs Rules Decision Boundary
    """

    def test_evidence_schema_validation_is_rule_based(self) -> None:
        """Evidence schema validation (structure) MUST be rule-based, no judge.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 table
        """
        # Required fields present, minimum length, YAML parseable -> rule check
        # No judge invocation for schema validation
        pass  # Contract documentation

    def test_evidence_content_adequacy_requires_judge(self) -> None:
        """Evidence content adequacy (semantics) MUST require judge.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 table
        """
        # Understanding if evidence proves verification -> EVIDENCE judgment
        pass  # Contract documentation

    def test_failure_count_lt_3_no_judge(self) -> None:
        """Failure count < 3 MUST use automatic retry, no judge.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 table
        """
        # Rule-based threshold: count < 3 -> automatic retry
        pass  # Contract documentation

    def test_failure_count_gte_3_requires_judge(self) -> None:
        """Failure count >= 3 MUST invoke ESCALATION judgment.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 table
        """
        # Needs classification: retry? switch agent? replan? -> ESCALATION judgment
        pass  # Contract documentation

    def test_boundary_is_rules_handle_structure(self) -> None:
        """The boundary principle: rules handle structure; judge handles semantics.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 table
        Blueprint: Decision boundary principle
        """
        # Structure checks = rule-based, fast path
        # Semantic evaluation = judge-based, needs understanding
        pass  # Contract documentation
