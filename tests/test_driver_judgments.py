"""Focused tests for judgment type definitions and context schemas.

Tests verify the data contracts defined in judgments.py:
- JudgmentType enum (all 7 types represented)
- CONTEXT_SCHEMAS (required/optional fields per type)
- VERDICT_VALUES (all valid verdict strings)
- JudgmentRequest dataclass (input validation)
- JudgmentVerdict dataclass (output validation)

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.9
Blueprint Reference: DRIVER-BLUEPRINT.md (Judgment Agent Design)
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md (Judgment Types)

This is a RED test file - expected to FAIL if implementation is incomplete.
Record any discovered contract gaps in step notes.
"""

import json
from dataclasses import asdict

import pytest

from src.vectl.driver.judgments import (
    CONTEXT_SCHEMAS,
    VERDICT_VALUES,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
)


# =============================================================================
# JudgmentType Enum Contract Tests
# =============================================================================


class TestJudgmentTypeEnum:
    """Contract tests for JudgmentType enum.

    Validates all judgment types from the Blueprint are represented.
    Blueprint Reference: DRIVER-BLUEPRINT.md lines 157-172 (Judgment Agent Design)
    """

    def test_judgment_type_count(self) -> None:
        """JudgmentType MUST have exactly 7 types matching Blueprint Tier 3.

        Blueprint table shows 11 judgment points mapping to 7 JudgmentTypes:
        - PREFLIGHT (Blueprint #1, #3)
        - EVIDENCE (Blueprint #8c, #10)
        - FAILURE (Blueprint #5, #6, #7)
        - ESCALATION (Blueprint #15)
        - GATE (Blueprint #17, #19)
        - ANOMALY (Blueprint #11)
        - COLD_CONTEXT (Blueprint #18)

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        assert len(JudgmentType) == 7

    def test_judgment_type_values_are_strings(self) -> None:
        """Each JudgmentType value MUST be lowercase snake_case string.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        assert JudgmentType.PREFLIGHT.value == "preflight"
        assert JudgmentType.EVIDENCE.value == "evidence"
        assert JudgmentType.FAILURE.value == "failure"
        assert JudgmentType.ESCALATION.value == "escalation"
        assert JudgmentType.GATE.value == "gate"
        assert JudgmentType.ANOMALY.value == "anomaly"
        assert JudgmentType.COLD_CONTEXT.value == "cold_context"

    def test_judgment_type_is_str_enum(self) -> None:
        """JudgmentType MUST inherit from str, Enum for JSON serialization.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        assert isinstance(JudgmentType.PREFLIGHT, str)
        assert JudgmentType.PREFLIGHT == "preflight"  # str comparison works

    def test_judgment_type_all_values_serializable(self) -> None:
        """All JudgmentType values MUST be JSON-serializable.

        Required for event logging (observe.py) and API responses.
        """
        for jt in JudgmentType:
            data = {"judgment_type": jt.value}
            json_str = json.dumps(data)
            parsed = json.loads(json_str)
            assert parsed["judgment_type"] == jt.value

    def test_judgment_type_blueprint_mapping(self) -> None:
        """Each JudgmentType MUST map to Blueprint judgment points in docstring.

        Ensures traceability from code to specification.
        """
        # PREFLIGHT: Blueprint #1 (Pre-dispatch acceptance preflight), #3 (Spec review)
        assert hasattr(JudgmentType, "PREFLIGHT")

        # EVIDENCE: Blueprint #8c (Evidence content adequacy), #10 (Verification purity)
        assert hasattr(JudgmentType, "EVIDENCE")

        # FAILURE: Blueprint #5 (Failure provenance), #6 (Failure disposition), #7 (Gate intersection)
        assert hasattr(JudgmentType, "FAILURE")

        # ESCALATION: Blueprint #15 (Repeated remediation strategy)
        assert hasattr(JudgmentType, "ESCALATION")

        # GATE: Blueprint #17 (Runnable surface gate augmentation), #19 (Freeze hard-block)
        assert hasattr(JudgmentType, "GATE")

        # ANOMALY: Blueprint #11 (Plan anomaly repair decision)
        assert hasattr(JudgmentType, "ANOMALY")

        # COLD_CONTEXT: Blueprint #18 (Cold context dispatch isolation)
        assert hasattr(JudgmentType, "COLD_CONTEXT")


# =============================================================================
# CONTEXT_SCHEMAS Contract Tests
# =============================================================================


class TestContextSchemas:
    """Contract tests for context field schemas per judgment type.

    Each judgment type MUST define required and optional context fields.
    Required fields MUST be present in JudgmentRequest.context before judge invocation.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, CONTEXT_SCHEMAS
    Blueprint: docs/JUDGE-AGENT-PROMPT.md (per-type context descriptions)
    """

    def test_context_schemas_covers_all_judgment_types(self) -> None:
        """CONTEXT_SCHEMAS MUST have entries for all JudgmentType values.

        Invariant: Every JudgmentType has schema with required/optional fields.
        """
        for jt in JudgmentType:
            assert jt in CONTEXT_SCHEMAS, f"Missing schema for {jt}"

    def test_context_schemas_all_required_are_lists(self) -> None:
        """Each schema MUST have 'required' as list[str].

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        for jt, schema in CONTEXT_SCHEMAS.items():
            assert "required" in schema, f"Missing 'required' for {jt}"
            assert isinstance(schema["required"], list), f"'required' must be list for {jt}"
            for field in schema["required"]:
                assert isinstance(field, str), f"Required field must be str in {jt}"

    def test_context_schemas_all_optional_are_lists(self) -> None:
        """Each schema MUST have 'optional' as list[str].

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        for jt, schema in CONTEXT_SCHEMAS.items():
            assert "optional" in schema, f"Missing 'optional' for {jt}"
            assert isinstance(schema["optional"], list), f"'optional' must be list for {jt}"
            for field in schema["optional"]:
                assert isinstance(field, str), f"Optional field must be str in {jt}"

    def test_context_schemas_no_overlap_required_optional(self) -> None:
        """No field MUST appear in both required and optional for same type.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        for jt, schema in CONTEXT_SCHEMAS.items():
            required_set = set(schema["required"])
            optional_set = set(schema["optional"])
            overlap = required_set & optional_set
            assert overlap == set(), f"Overlap in {jt}: {overlap}"

    def test_context_schemas_preflight(self) -> None:
        """PREFLIGHT schema: required fields from Blueprint lines 194-197."""
        schema = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]
        # Required: step_id (target), step_description, step_verification, step_refs
        required_fields = {"step_id", "step_description", "step_verification", "step_refs"}
        assert required_fields <= set(schema["required"]), (
            f"PREFLIGHT missing required fields: {required_set - set(schema['required'])}. "
            f"Blueprint specifies: step_description, step_verification, risk_signals"
        )
        # Optional: risk_signals, phase_context
        assert "risk_signals" in schema["optional"] or "risk_signals" in schema["required"], (
            "PREFLIGHT must have risk_signals field"
        )

    def test_context_schemas_evidence(self) -> None:
        """EVIDENCE schema: required fields from Blueprint lines 200-203."""
        schema = CONTEXT_SCHEMAS[JudgmentType.EVIDENCE]
        # Required: step_id, step_verification, evidence
        required_fields = {"step_id", "step_verification", "evidence"}
        assert required_fields <= set(schema["required"]), (
            f"EVIDENCE missing required fields: {required_fields - set(schema['required'])}"
        )
        # Optional: step_type, gate_evidence
        optional_fields = {"step_type", "gate_evidence"}
        assert optional_fields <= set(schema["optional"]), (
            f"EVIDENCE missing optional fields: {optional_fields - set(schema['optional'])}"
        )

    def test_context_schemas_failure(self) -> None:
        """FAILURE schema: required fields from Blueprint lines 205-208."""
        schema = CONTEXT_SCHEMAS[JudgmentType.FAILURE]
        # Required: step_id, error_output, failure_count
        required_fields = {"step_id", "error_output", "failure_count"}
        assert required_fields <= set(schema["required"]), (
            f"FAILURE missing required fields: {required_fields - set(schema['required'])}"
        )

    def test_context_schemas_escalation(self) -> None:
        """ESCALATION schema: required fields from Blueprint lines 210-212."""
        schema = CONTEXT_SCHEMAS[JudgmentType.ESCALATION]
        # Required: step_id, failure_count, failure_history, step_description, available_agents
        required_fields = {
            "step_id",
            "failure_count",
            "failure_history",
            "step_description",
            "available_agents",
        }
        assert required_fields <= set(schema["required"]), (
            f"ESCALATION missing required fields: {required_fields - set(schema['required'])}"
        )

    def test_context_schemas_gate(self) -> None:
        """GATE schema: required fields from Blueprint lines 214-216."""
        schema = CONTEXT_SCHEMAS[JudgmentType.GATE]
        # Required: step_id, gate_evidence, blocker_issues
        required_fields = {"step_id", "gate_evidence", "blocker_issues"}
        assert required_fields <= set(schema["required"]), (
            f"GATE missing required fields: {required_fields - set(schema['required'])}"
        )

    def test_context_schemas_anomaly(self) -> None:
        """ANOMALY schema: required fields from Blueprint lines 218-220."""
        schema = CONTEXT_SCHEMAS[JudgmentType.ANOMALY]
        # Required: anomaly_type, repair_scope
        required_fields = {"anomaly_type", "repair_scope"}
        assert required_fields <= set(schema["required"]), (
            f"ANOMALY missing required fields: {required_fields - set(schema['required'])}"
        )

    def test_context_schemas_cold_context(self) -> None:
        """COLD_CONTEXT schema: required fields from Blueprint lines 294-296."""
        schema = CONTEXT_SCHEMAS[JudgmentType.COLD_CONTEXT]
        # Required: step_id, step_spec, diff
        required_fields = {"step_id", "step_spec", "diff"}
        assert required_fields <= set(schema["required"]), (
            f"COLD_CONTEXT missing required fields: {required_fields - set(schema['required'])}"
        )


# =============================================================================
# VERDICT_VALUES Contract Tests
# =============================================================================


class TestVerdictValues:
    """Contract tests for valid verdict strings.

    VERDICT_VALUES defines all valid values for JudgmentVerdict.verdict.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentVerdict
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)
    """

    def test_verdict_values_count(self) -> None:
        """VERDICT_VALUES MUST contain exactly 7 values.

        From Blueprint lines 111-118 and Blueprint lines 235-239.
        Each verdict type serves a distinct orchestration purpose:
        - ACCEPT: Proceed with the action
        - REJECT: Block and require fix
        - RETRY: Retry with same agent
        - SWITCH_AGENT: Try a different agent
        - REPLAN: Modify the plan
        - DEFER: Skip for now, revisit later
        - HALT: Cannot proceed without intervention
        """
        assert len(VERDICT_VALUES) == 7

    def test_verdict_values_are_strings(self) -> None:
        """Each verdict value MUST be UPPERCASE string."""
        expected = {"ACCEPT", "REJECT", "RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"}
        assert VERDICT_VALUES == expected

    def test_verdict_values_accept(self) -> None:
        """ACCEPT: Proceed with the action.

        Blueprint: Common positive outcome for PREFLIGHT, EVIDENCE, GATE.
        """
        assert "ACCEPT" in VERDICT_VALUES

    def test_verdict_values_reject(self) -> None:
        """REJECT: Block and require fix.

        Blueprint: Negative outcome for EVIDENCE validation failure.
        """
        assert "REJECT" in VERDICT_VALUES

    def test_verdict_values_retry(self) -> None:
        """RETRY: Retry with same agent.

        Blueprint: ESCALATION decision for transient failures.
        """
        assert "RETRY" in VERDICT_VALUES

    def test_verdict_values_switch_agent(self) -> None:
        """SWITCH_AGENT: Try a different agent.

        Blueprint: ESCALATION decision for agent-specific issues.
        """
        assert "SWITCH_AGENT" in VERDICT_VALUES

    def test_verdict_values_replan(self) -> None:
        """REPLAN: Modify the plan.

        Blueprint: Decision for spec inadequacy (PREFLIGHT) or ESCALATION.
        """
        assert "REPLAN" in VERDICT_VALUES

    def test_verdict_values_defer(self) -> None:
        """DEFER: Skip for now, revisit later.

        Blueprint: Decision for non-critical issues.
        """
        assert "DEFER" in VERDICT_VALUES

    def test_verdict_values_halt(self) -> None:
        """HALT: Cannot proceed without intervention.

        Blueprint: Decision for unrecoverable failures.
        """
        assert "HALT" in VERDICT_VALUES

    def test_verdict_values_serializable(self) -> None:
        """VERDICT_VALUES MUST be JSON-serializable."""
        data = {"verdicts": list(VERDICT_VALUES)}
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        assert set(parsed["verdicts"]) == VERDICT_VALUES


# =============================================================================
# JudgmentRequest Dataclass Contract Tests
# =============================================================================


class TestJudgmentRequest:
    """Contract tests for JudgmentRequest dataclass.

    Input to the judgment agent. Validates structure and invariant:
    context MUST contain all keys from CONTEXT_SCHEMAS[type]["required"].

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentRequest dataclass
    Blueprint: docs/JUDGE-AGENT-PROMPT.md (request structure)
    """

    def test_judgment_request_fields(self) -> None:
        """JudgmentRequest MUST have: type, step_id, context, failure_history, plan_summary.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 227-233
        """
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement core feature",
                "step_verification": "Tests pass",
                "step_refs": [],
            },
            failure_history=[],
            plan_summary="Phase 1 of core implementation",
        )
        assert request.type == JudgmentType.PREFLIGHT
        assert request.step_id == "core.impl"
        assert request.context["step_description"] == "Implement core feature"
        assert request.failure_history == []
        assert request.plan_summary == "Phase 1 of core implementation"

    def test_judgment_request_frozen(self) -> None:
        """JudgmentRequest MUST be frozen (immutable).

        Ensures judgment inputs cannot be modified after construction.
        """
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={
                "step_id": "test.step",
                "step_verification": "Verify",
                "evidence": "Test output",
            },
            failure_history=[],
            plan_summary="Test plan",
        )
        # Attempt to modify should raise FrozenInstanceError
        with pytest.raises(AttributeError):
            request.step_id = "modified"  # type: ignore

    def test_judgment_request_context_type(self) -> None:
        """JudgmentRequest.context MUST be dict[str, str].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="s1",
            context={
                "step_id": "s1",
                "step_description": "desc",
                "step_verification": "verify",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="summary",
        )
        assert isinstance(request.context, dict)
        for key, value in request.context.items():
            assert isinstance(key, str)
            assert isinstance(value, str)

    def test_judgment_request_failure_history_type(self) -> None:
        """JudgmentRequest.failure_history MUST be list[str].

        For ESCALATION type, contains previous error outputs.
        """
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "error1;error2",
                "step_description": "Failing step",
                "available_agents": "python-senior,frontend-engineer",
            },
            failure_history=["First error", "Second error"],
            plan_summary="Plan with failures",
        )
        assert isinstance(request.failure_history, list)
        for item in request.failure_history:
            assert isinstance(item, str)

    def test_judgment_request_preflight_required_context(self) -> None:
        """PREFLIGHT request MUST include all required context fields.

        Invariant validated by judge module before invocation.
        Required: step_id, step_description, step_verification, step_refs
        """
        # Valid PREFLIGHT request with all required fields
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement core",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )

        # Verify all required fields present
        required = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]["required"]
        for field in required:
            assert field in request.context, f"Missing required field: {field}"

    def test_judgment_request_evidence_required_context(self) -> None:
        """EVIDENCE request MUST include all required context fields.

        Required: step_id, step_verification, evidence
        """
        request = JudgmentRequest(
            type=JudgmentType.EVIDENCE,
            step_id="test.step",
            context={
                "step_id": "test.step",
                "step_verification": "Run pytest",
                "evidence": "All tests passed",
            },
            failure_history=[],
            plan_summary="Test phase",
        )

        required = CONTEXT_SCHEMAS[JudgmentType.EVIDENCE]["required"]
        for field in required:
            assert field in request.context

    def test_judgment_request_escalation_required_context(self) -> None:
        """ESCALATION request MUST include all required context fields.

        Required: step_id, failure_count, failure_history, step_description, available_agents
        """
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "error1\nerror2\nerror3",
                "step_description": "Complex step",
                "available_agents": "python-senior,frontend-engineer",
            },
            failure_history=["error1", "error2", "error3"],
            plan_summary="Plan",
        )

        required = CONTEXT_SCHEMAS[JudgmentType.ESCALATION]["required"]
        for field in required:
            assert field in request.context

    def test_judgment_request_round_trip_asdict(self) -> None:
        """JudgmentRequest MUST round-trip via dataclasses.asdict.

        Required for logging and serialization.
        """
        original = JudgmentRequest(
            type=JudgmentType.GATE,
            step_id="gate.step",
            context={
                "step_id": "gate.step",
                "gate_evidence": "All checks passed",
                "blocker_issues": "[]",
            },
            failure_history=[],
            plan_summary="Gate phase",
        )

        d = asdict(original)
        assert d["type"] == JudgmentType.GATE
        assert d["step_id"] == "gate.step"
        assert "step_id" in d["context"]

        # Round-trip reconstruction (not same object, but same values)
        reconstructed = JudgmentRequest(
            type=d["type"],
            step_id=d["step_id"],
            context=d["context"],
            failure_history=d["failure_history"],
            plan_summary=d["plan_summary"],
        )
        assert reconstructed.type == original.type
        assert reconstructed.step_id == original.step_id


# =============================================================================
# JudgmentVerdict Dataclass Contract Tests
# =============================================================================


class TestJudgmentVerdict:
    """Contract tests for JudgmentVerdict dataclass.

    Output from the judgment agent. Validates structure matches
    JUDGE-AGENT-PROMPT.md response format.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentVerdict dataclass
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format (MANDATORY)
    """

    def test_judgment_verdict_fields(self) -> None:
        """JudgmentVerdict MUST have: verdict, reason, suggested_action, planner_instruction.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 235-239
        """
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Tests pass with output",
        )
        assert verdict.verdict == "ACCEPT"
        assert verdict.reason == "Tests pass with output"
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_frozen(self) -> None:
        """JudgmentVerdict MUST be frozen (immutable).

        Ensures judgment outputs cannot be modified after construction.
        """
        verdict = JudgmentVerdict(
            verdict="REJECT",
            reason="Evidence incomplete",
        )
        with pytest.raises(AttributeError):
            verdict.verdict = "ACCEPT"  # type: ignore

    def test_judgment_verdict_accept(self) -> None:
        """ACCEPT verdict: proceed with action.

        Blueprint: Common positive outcome.
        """
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Spec is adequate for implementation",
        )
        assert verdict.verdict == "ACCEPT"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_reject(self) -> None:
        """REJECT verdict: block and require fix.

        Blueprint: Evidence validation failure.
        """
        verdict = JudgmentVerdict(
            verdict="REJECT",
            reason="Missing test output in evidence",
        )
        assert verdict.verdict == "REJECT"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_retry(self) -> None:
        """RETRY verdict: retry with same agent.

        Blueprint: ESCALATION for transient failures.
        """
        verdict = JudgmentVerdict(
            verdict="RETRY",
            reason="Transient network error, retry recommended",
        )
        assert verdict.verdict == "RETRY"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_switch_agent(self) -> None:
        """SWITCH_AGENT verdict: try different agent, suggested_action required.

        Blueprint: ESCALATION for agent-specific failures.
        """
        verdict = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Current agent lacks frontend expertise",
            suggested_action="frontend-engineer",
        )
        assert verdict.verdict == "SWITCH_AGENT"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action == "frontend-engineer"
        assert verdict.planner_instruction is None

    def test_judgment_verdict_replan(self) -> None:
        """REPLAN verdict: modify plan, planner_instruction required.

        Blueprint: PREFLIGHT for inadequate specs.
        """
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Step requires subtask decomposition",
            planner_instruction="Split core.impl into core.impl-a and core.impl-b",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction == "Split core.impl into core.impl-a and core.impl-b"

    def test_judgment_verdict_defer(self) -> None:
        """DEFER verdict: skip for now, revisit later.

        Blueprint: Non-critical issues.
        """
        verdict = JudgmentVerdict(
            verdict="DEFER",
            reason="Non-blocking suggestion, proceed with current work",
        )
        assert verdict.verdict == "DEFER"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_halt(self) -> None:
        """HALT verdict: cannot proceed without intervention.

        Blueprint: Unrecoverable failures.
        """
        verdict = JudgmentVerdict(
            verdict="HALT",
            reason="Critical dependency missing, requires manual intervention",
        )
        assert verdict.verdict == "HALT"
        assert verdict.verdict in VERDICT_VALUES
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_reason_required(self) -> None:
        """JudgmentVerdict.reason MUST always be provided.

        Blueprint: lines 165 states "1-3 sentence explanation for the verdict".
        """
        # This will fail at runtime if reason is empty - validation is in judge.py
        # Here we just verify the field exists
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Tests passed",
        )
        assert verdict.reason == "Tests passed"
        assert len(verdict.reason) > 0

    def test_judgment_verdict_round_trip_asdict(self) -> None:
        """JudgmentVerdict MUST round-trip via dataclasses.asdict.

        Required for event logging (observe.py) and JSON serialization.
        """
        original = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Need different expertise",
            suggested_action="python-senior",
        )

        d = asdict(original)
        assert d["verdict"] == "SWITCH_AGENT"
        assert d["reason"] == "Need different expertise"
        assert d["suggested_action"] == "python-senior"
        assert d["planner_instruction"] is None

        # JSON round-trip
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["verdict"] == "SWITCH_AGENT"

    def test_judgment_verdict_optional_fields_none_by_default(self) -> None:
        """Optional fields (suggested_action, planner_instruction) MUST default to None.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.9
        """
        verdict = JudgmentVerdict(verdict="ACCEPT", reason="OK")
        assert verdict.suggested_action is None
        assert verdict.planner_instruction is None

    def test_judgment_verdict_json_serializable(self) -> None:
        """JudgmentVerdict MUST be JSON-serializable for event logging.

        Required format matches Blueprint lines 169-175.
        """
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Step needs decomposition",
            planner_instruction="Create subtasks",
        )
        d = asdict(verdict)
        # Ensure all values are JSON-serializable
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["verdict"] == "REPLAN"
        assert parsed["reason"] == "Step needs decomposition"
        assert parsed["planner_instruction"] == "Create subtasks"


# =============================================================================
# Integration: Request-Verdict Flow Contract Tests
# =============================================================================


class TestJudgmentRequestVerdictIntegration:
    """Contract tests for request-to-verdict flow.

    Validates that valid verdicts are only produced in valid contexts.
    """

    def test_valid_verdict_per_type_preflight(self) -> None:
        """PREFLIGHT can produce: ACCEPT, REJECT, REPLAN, DEFER.

        Blueprint lines 194-197: Preflight evaluates step adequacy.
        Valid outcomes: proceed (ACCEPT), reject spec (REJECT), modify plan (REPLAN),
        or skip (DEFER for non-critical).
        """
        valid_for_preflight = {"ACCEPT", "REJECT", "REPLAN", "DEFER"}
        # Not assertion, just documentation - enforcement is in judge.py
        assert valid_for_preflight <= VERDICT_VALUES

    def test_valid_verdict_per_type_evidence(self) -> None:
        """EVIDENCE can produce: ACCEPT, REJECT.

        Blueprint lines 200-203: Evidence validation.
        Valid outcomes: accept evidence (ACCEPT) or reject (REJECT).
        """
        valid_for_evidence = {"ACCEPT", "REJECT"}
        assert valid_for_evidence <= VERDICT_VALUES

    def test_valid_verdict_per_type_escalation(self) -> None:
        """ESCALATION can produce: RETRY, SWITCH_AGENT, REPLAN, HALT.

        Blueprint lines 210-212: Repeated failure handling.
        Valid outcomes: retry same agent (RETRY), switch agent (SWITCH_AGENT),
        modify plan (REPLAN), or halt (HALT).
        """
        valid_for_escalation = {"RETRY", "SWITCH_AGENT", "REPLAN", "HALT"}
        assert valid_for_escalation <= VERDICT_VALUES

    def test_valid_verdict_per_type_gate(self) -> None:
        """GATE can produce: ACCEPT, REJECT.

        Blueprint lines 214-216: Gate evidence assessment.
        Valid outcomes: gate passed (ACCEPT) or gate failed (REJECT).
        """
        valid_for_gate = {"ACCEPT", "REJECT"}
        assert valid_for_gate <= VERDICT_VALUES

    def test_switch_agent_verdict_requires_suggested_action(self) -> None:
        """SWITCH_AGENT verdict MUST have suggested_action.

        Blueprint line 239: "suggested_action: optional agent name for SWITCH_AGENT".
        When verdict is SWITCH_AGENT, suggested_action MUST be non-None.
        """
        # Valid case: SWITCH_AGENT with suggested_action
        verdict = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Need different agent",
            suggested_action="python-senior",
        )
        assert verdict.verdict == "SWITCH_AGENT"
        assert verdict.suggested_action is not None

        # Note: Validation that SWITCH_AGENT MUST have suggested_action
        # is enforced in judge.py, not in the dataclass itself.

    def test_replan_verdict_requires_planner_instruction(self) -> None:
        """REPLAN verdict MUST have planner_instruction.

        Blueprint line 239: "planner_instruction: instruction for REPLAN".
        When verdict is REPLAN, planner_instruction MUST be non-None.
        """
        # Valid case: REPLAN with planner_instruction
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Spec needs enhancement",
            planner_instruction="Add test coverage step",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None

        # Note: Validation that REPLAN MUST have planner_instruction
        # is enforced in judge.py, not in the dataclass itself.


# =============================================================================
# Edge Cases and Error Conditions
# =============================================================================


class TestJudgmentEdgeCases:
    """Edge case and error condition tests.

    Validates behavior at boundaries and error conditions.
    """

    def test_empty_failure_history(self) -> None:
        """JudgmentRequest with empty failure_history MUST be valid.

        First invocation of judgment for a step has no failure history.
        """
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Initial step",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],  # Empty is valid
            plan_summary="Phase 1",
        )
        assert request.failure_history == []
        assert len(request.failure_history) == 0

    def test_long_failure_history(self) -> None:
        """JudgmentRequest.ESCALATION with multiple failures MUST be valid.

        Real escalation scenario has accumulated failure history.
        """
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="problematic.step",
            context={
                "step_id": "problematic.step",
                "failure_count": "5",
                "failure_history": "error1\nerror2\nerror3\nerror4\nerror5",
                "step_description": "Many failures",
                "available_agents": "python-senior",
            },
            failure_history=["error1", "error2", "error3", "error4", "error5"],
            plan_summary="Plan",
        )
        assert len(request.failure_history) == 5

    def test_context_with_optional_fields(self) -> None:
        """JudgmentRequest context MAY include optional fields.

        Optional fields extend context but are not required.
        """
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
                # Optional fields
                "risk_signals": "migration,state,backward-compat",
                "phase_context": "Core phase",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )
        # Optional fields are present
        assert "risk_signals" in request.context
        assert "phase_context" in request.context

    def test_context_without_optional_fields(self) -> None:
        """JudgmentRequest context with only required fields MUST be valid.

        Judge module validates required fields, not optional.
        """
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement",
                "step_verification": "Tests",
                "step_refs": "[]",
                # No optional fields
            },
            failure_history=[],
            plan_summary="Phase 1",
        )
        assert request.type == JudgmentType.PREFLIGHT

    def test_verdict_reason_length_bounds(self) -> None:
        """JudgmentVerdict.reason SHOULD be 1-3 sentences.

        Blueprint line 165: "1-3 sentence explanation for the verdict".
        This is a SHOULD, not MUST - no strict validation, just documentation.
        """
        # Short reason is valid
        short = JudgmentVerdict(verdict="ACCEPT", reason="OK")
        assert len(short.reason) > 0  # At least non-empty

        # Longer reason with multiple sentences is valid
        longer = JudgmentVerdict(
            verdict="REJECT",
            reason="Evidence incomplete. Missing test output. Please re-run with pytest.",
        )
        assert "." in longer.reason  # Multiple sentences allowed

    def test_judgment_type_string_equality(self) -> None:
        """JudgmentType values MUST compare equal to their string values.

        Required for dict key usage and JSON serialization.
        """
        assert JudgmentType.PREFLIGHT == "preflight"
        assert JudgmentType.EVIDENCE == "evidence"
        assert JudgmentType.FAILURE == "failure"

    def test_judgment_type_dict_lookup(self) -> None:
        """JudgmentType MUST work as dict key for CONTEXT_SCHEMAS lookup.

        Required pattern: CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]
        assert "required" in schema
        assert "optional" in schema

    def test_verdict_string_membership(self) -> None:
        """Verdict strings MUST be checked against VERDICT_VALUES.

        Required pattern: verdict.verdict in VERDICT_VALUES
        """
        for verdict_str in VERDICT_VALUES:
            assert verdict_str in VERDICT_VALUES
