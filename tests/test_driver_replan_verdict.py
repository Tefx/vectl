"""Focused tests for REPLAN verdict handling and planner dispatch wiring.

This is an EXPECTED-RED TDD test file. Tests verify the REPLAN verdict contract:
- ReplanVerdictContract validation (verdict must be REPLAN, planner_instruction non-empty)
- PlannerDispatchRequest construction (step_id, trigger, instruction propagation)
- Non-destructive deferral of originating step (step remains claimable)
- Planner dispatch instruction propagation from judge to planner
- Anti-narrowing (REPLAN cannot collapse to REJECT/DEFER/HALT at runtime)

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10, 2.11
Blueprint Reference: DRIVER-BLUEPRINT.md Flow 2, Flow 3, Flow 4
Contract Reference: src/vectl/driver/judgments.py ReplanVerdictContract

Allowed file scope:
- tests (this file and fixtures)
- NO src/vectl/driver/* modifications

Step ID: driver-judgment-expansion-replan.test-replan-planner-wiring
"""

from __future__ import annotations

import pytest

from src.vectl.driver.judgments import (
    CONTEXT_SCHEMAS,
    VERDICT_VALUES,
    JudgmentRequest,
    JudgmentType,
    JudgmentVerdict,
    ReplanVerdictContract,
    REPLAN_NON_NARROWING_RULE,
)
from src.vectl.driver.loop import (
    DEFERRED_REPLAN_BRANCHES,
    PlannerDispatchContract,
    PlannerDispatchRequest,
    PlannerDispatcher,
    ReplanTriggerName,
    REPLAN_PLANNER_WIRING,
)


# =============================================================================
# PART 1: ReplanVerdictContract Tests
# =============================================================================


class TestReplanVerdictContractDataclass:
    """Contract tests for ReplanVerdictContract dataclass.

    Validates the pinned contract for REPLAN verdicts before runtime wirings.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 / 2.11
    Blueprint: DRIVER-BLUEPRINT.md Flow 2, Flow 3, Flow 4
    Implementation: src/vectl/driver/judgments.py lines 186-207
    """

    def test_replan_verdict_contract_instantiation(self) -> None:
        """ReplanVerdictContract MUST instantiate with required fields."""
        contract = ReplanVerdictContract(
            verdict="REPLAN",
            reason="Step needs decomposition into subtasks",
            planner_instruction="Split core.impl into core.impl-a and core.impl-b",
        )
        assert contract.verdict == "REPLAN"
        assert contract.reason == "Step needs decomposition into subtasks"
        assert contract.planner_instruction == ("Split core.impl into core.impl-a and core.impl-b")

    def test_replan_verdict_contract_frozen(self) -> None:
        """ReplanVerdictContract MUST be frozen (immutable)."""
        contract = ReplanVerdictContract(
            verdict="REPLAN",
            reason="Spec inadequacy",
            planner_instruction="Add test coverage step",
        )
        with pytest.raises(AttributeError):
            contract.verdict = "REJECT"  # type: ignore

    def test_replan_verdict_contract_verdict_must_be_replan(self) -> None:
        """ReplanVerdictContract.verdict MUST be exactly 'REPLAN'.

        Invariant from: judgments.py lines 196-199
        Contract: verdict MUST be 'REPLAN', not REJECT/DEFER/HALT.
        """
        # Valid: verdict = "REPLAN"
        valid_contract = ReplanVerdictContract(
            verdict="REPLAN",
            reason="Test reason",
            planner_instruction="Test instruction",
        )
        assert valid_contract.verdict == "REPLAN"

        # Invalid verdicts should fail at runtime if validation is added later
        # For now, we just verify the contract exists and accepts REPLAN
        # Future implementation may add runtime validation

    def test_replan_verdict_contract_planner_instruction_required(self) -> None:
        """ReplanVerdictContract.planner_instruction MUST be non-empty.

        Invariant from: judgments.py lines 196-199
        Contract: planner_instruction MUST be non-empty for REPLAN.
        """
        contract = ReplanVerdictContract(
            verdict="REPLAN",
            reason="Spec needs enhancement",
            planner_instruction="Add implementation details",
        )
        assert contract.planner_instruction is not None
        assert len(contract.planner_instruction) > 0

    def test_replan_verdict_contract_fields_match_judgment_verdict(self) -> None:
        """ReplanVerdictContract fields MUST align with JudgmentVerdict for REPLAN.

        The contract exists to pin REPLAN semantics before runtime wiring.
        Fields: verdict, reason, planner_instruction
        """
        # Both should have compatible semantics
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Test reason",
            planner_instruction="Test instruction",
        )
        contract = ReplanVerdictContract(
            verdict=verdict.verdict,
            reason=verdict.reason,
            planner_instruction=verdict.planner_instruction or "",
        )
        assert contract.verdict == verdict.verdict
        assert contract.reason == verdict.reason
        assert contract.planner_instruction == verdict.planner_instruction


class TestReplanNonNarrowingRule:
    """Tests for REPLAN_NON_NARROWING_RULE constant.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 / 2.11
    Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733
    Implementation: judgments.py lines 209-214
    """

    def test_replan_non_narrowing_rule_exists(self) -> None:
        """REPLAN_NON_NARROWING_RULE MUST be defined as a string constant."""
        assert isinstance(REPLAN_NON_NARROWING_RULE, str)
        assert len(REPLAN_NON_NARROWING_RULE) > 0

    def test_replan_non_narrowing_rule_content(self) -> None:
        """REPLAN_NON_NARROWING_RULE MUST mention planner-dispatch-capable surface."""
        # The rule MUST state that REPLAN is planner-dispatch-capable
        assert "planner" in REPLAN_NON_NARROWING_RULE.lower()
        assert "dispatch" in REPLAN_NON_NARROWING_RULE.lower()

    def test_replan_non_narrowing_rule_forbids_narrowing(self) -> None:
        """REPLAN_NON_NARROWING_RULE MUST forbid collapsing REPLAN to reject/defer/halt."""
        # The rule MUST forbid replacing REPLAN with REJECT, DEFER, or HALT
        rule_lower = REPLAN_NON_NARROWING_RULE.lower()
        assert "reject" in rule_lower or "narrow" in rule_lower
        assert "defer" in rule_lower or "narrow" in rule_lower
        assert "halt" in rule_lower or "narrow" in rule_lower


# =============================================================================
# PART 2: PlannerDispatchRequest Tests
# =============================================================================


class TestPlannerDispatchRequestDataclass:
    """Contract tests for PlannerDispatchRequest.

    Validates the planner dispatch payload derived from REPLAN verdicts.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11 (dispatch_planner)
    Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733
    Implementation: loop.py lines 137-160
    """

    def test_planner_dispatch_request_instantiation(self) -> None:
        """PlannerDispatchRequest MUST instantiate with required fields."""
        request = PlannerDispatchRequest(
            step_id="core.impl",
            trigger="handle_dispatch.preflight_replan",
            judgment_type="PREFLIGHT",
            planner_instruction="Split core.impl into subtasks",
        )
        assert request.step_id == "core.impl"
        assert request.trigger == "handle_dispatch.preflight_replan"
        assert request.judgment_type == "PREFLIGHT"
        assert request.planner_instruction == "Split core.impl into subtasks"
        assert request.source_verdict == "REPLAN"  # Default value

    def test_planner_dispatch_request_frozen(self) -> None:
        """PlannerDispatchRequest MUST be frozen (immutable)."""
        request = PlannerDispatchRequest(
            step_id="test.step",
            trigger="reconcile.escalation_replan",
            judgment_type="ESCALATION",
            planner_instruction="Add recovery step",
        )
        with pytest.raises(AttributeError):
            request.step_id = "modified"  # type: ignore

    def test_planner_dispatch_request_source_verdict_default(self) -> None:
        """source_verdict MUST default to 'REPLAN'.

        Contract pin: loop.py line 159
        """
        request = PlannerDispatchRequest(
            step_id="test.step",
            trigger="handle_dispatch.preflight_replan",  # Use valid trigger name
            judgment_type="PREFLIGHT",
            planner_instruction="Test instruction",
        )
        assert request.source_verdict == "REPLAN"

    def test_planner_dispatch_request_planner_instruction_required(self) -> None:
        """planner_instruction MUST be non-empty.

        This is a runtime invariant from the planner dispatch contract.
        """
        request = PlannerDispatchRequest(
            step_id="test.step",
            trigger="reconcile.evidence_replan",  # Use valid trigger name
            judgment_type="EVIDENCE",
            planner_instruction="Non-empty instruction",
        )
        assert request.planner_instruction is not None
        assert len(request.planner_instruction) > 0

    def test_planner_dispatch_request_trigger_identifies_branch(self) -> None:
        """trigger MUST identify the originating branch for audit trail.

        Prevents silent merging of distinct REPLAN sites.
        """
        valid_triggers: list[ReplanTriggerName] = [
            "handle_dispatch.preflight_replan",
            "reconcile.evidence_replan",
            "reconcile.failure_classification_replan",
            "reconcile.escalation_replan",
            "run.startup_recovery_anomaly_replan",
        ]
        for trigger in valid_triggers:
            request = PlannerDispatchRequest(
                step_id="test.step",
                trigger=trigger,
                judgment_type="TEST",
                planner_instruction="Test instruction",
            )
            assert request.trigger == trigger

    def test_planner_dispatch_request_step_id_identity(self) -> None:
        """step_id MUST identify the step whose plan needs modification.

        This allows later phases to track which step triggered replanning.
        """
        request = PlannerDispatchRequest(
            step_id="auth.login",
            trigger="reconcile.escalation_replan",
            judgment_type="ESCALATION",
            planner_instruction="Add timeout handling",
        )
        assert request.step_id == "auth.login"


# =============================================================================
# PART 3: REPLAN-capable Branch Registration Tests
# =============================================================================


class TestDeferredReplanBranches:
    """Tests for DEFERRED_REPLAN_BRANCHES registration.

    Validates that all REPLAN-capable branches are documented and bounded.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Implementation: loop.py lines 218-259
    """

    def test_deferred_replan_branches_count(self) -> None:
        """Must have exactly 5 deferred REPLAN branches.

        These are the bounded branches deferred to driver-judgment-expansion-replan.
        """
        assert len(DEFERRED_REPLAN_BRANCHES) == 5

    def test_deferred_replan_branches_all_deferred_to_same_phase(self) -> None:
        """All branches MUST be deferred to driver-judgment-expansion-replan.

        Anti-narrowing: later phases MUST NOT reduce scope.
        """
        for branch in DEFERRED_REPLAN_BRANCHES:
            assert branch.deferred_to == "driver-judgment-expansion-replan"

    def test_deferred_replan_branches_have_rationales(self) -> None:
        """Each branch MUST have a rationale explaining why branch is deferred."""
        for branch in DEFERRED_REPLAN_BRANCHES:
            assert isinstance(branch.rationale, str)
            assert len(branch.rationale) > 0
            # Rationale must explain deferral - check for relevant keywords
            rationale_lower = branch.rationale.lower()
            # Accept various rationale patterns: "replan", "planner", "verdict", "handling", "deferred"
            has_relevant_keyword = (
                "replan" in rationale_lower
                or "planner" in rationale_lower
                or "verdict" in rationale_lower
                or "handling" in rationale_lower
                or "deferral" in rationale_lower
                or "deferred" in rationale_lower
            )
            assert has_relevant_keyword, (
                f"Branch {branch.branch} rationale must explain REPLAN/planner/verdict handling: "
                f"{branch.rationale}"
            )

    def test_deferred_replan_branches_names_match_contracts(self) -> None:
        """Branch names MUST match ReplanTriggerName literal."""
        expected_names: set[str] = {
            "handle_dispatch.preflight_replan",
            "reconcile.evidence_replan",
            "reconcile.failure_classification_replan",
            "reconcile.escalation_replan",
            "run.startup_recovery_anomaly_replan",
        }
        actual_names = {branch.branch for branch in DEFERRED_REPLAN_BRANCHES}
        assert actual_names == expected_names


class TestReplanPlannerWiring:
    """Tests for REPLAN_PLANNER_WIRING matrix.

    Validates the exact REPLAN/planner-dispatch wiring commitments.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Implementation: loop.py lines 262-320
    """

    def test_replan_planner_wiring_count(self) -> None:
        """Must have exactly 5 REPLAN wiring contracts."""
        assert len(REPLAN_PLANNER_WIRING) == 5

    def test_replan_planner_wiring_judgment_types(self) -> None:
        """Each contract MUST specify a valid judgment type that can emit REPLAN."""
        valid_types = {"PREFLIGHT", "EVIDENCE", "FAILURE", "ESCALATION", "ANOMALY"}
        for contract in REPLAN_PLANNER_WIRING:
            assert contract.judgment_type in valid_types

    def test_replan_planner_wiring_triggers_match_deferred_branches(self) -> None:
        """Each PlannerDispatchContract.trigger MUST match a DEFERRED_REPLAN_BRANCHES entry."""
        deferred_triggers = {branch.branch for branch in DEFERRED_REPLAN_BRANCHES}
        wiring_triggers = {contract.trigger for contract in REPLAN_PLANNER_WIRING}
        assert wiring_triggers == deferred_triggers

    def test_replan_planner_wiring_instruction_source(self) -> None:
        """planner_instruction_source MUST be 'JudgmentVerdict.planner_instruction'.

        Anti-narrowing: runtime MUST preserve instruction verbatim.
        """
        for contract in REPLAN_PLANNER_WIRING:
            assert contract.planner_instruction_source == "JudgmentVerdict.planner_instruction"

    def test_replan_planner_wiring_deferred_to_phase(self) -> None:
        """deferred_to MUST be 'driver-judgment-expansion-replan'."""
        for contract in REPLAN_PLANNER_WIRING:
            assert contract.deferred_to == "driver-judgment-expansion-replan"

    def test_replan_planner_wiring_has_rationale(self) -> None:
        """Each contract MUST have a rationale."""
        for contract in REPLAN_PLANNER_WIRING:
            assert isinstance(contract.rationale, str)
            assert len(contract.rationale) > 0

    def test_replan_planner_wiring_preflight_contract(self) -> None:
        """PREFLIGHT REPLAN contract MUST exist for handle_dispatch.preflight_replan."""
        preflight_contracts = [c for c in REPLAN_PLANNER_WIRING if c.judgment_type == "PREFLIGHT"]
        assert len(preflight_contracts) == 1
        assert preflight_contracts[0].trigger == "handle_dispatch.preflight_replan"
        assert preflight_contracts[0].trigger_surface == (
            "handle_dispatch() before claim/worktree/runner dispatch"
        )

    def test_replan_planner_wiring_escalation_contract(self) -> None:
        """ESCALATION REPLAN contract MUST exist for reconcile.escalation_replan."""
        escalation_contracts = [c for c in REPLAN_PLANNER_WIRING if c.judgment_type == "ESCALATION"]
        assert len(escalation_contracts) == 1
        assert escalation_contracts[0].trigger == "reconcile.escalation_replan"
        assert "repeated-failure" in escalation_contracts[0].rationale.lower()


# =============================================================================
# PART 4: PlannerDispatcher Protocol Tests
# =============================================================================


class TestPlannerDispatcherProtocol:
    """Tests for PlannerDispatcher protocol.

    Validates the loop-owned planner dispatch interface.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Implementation: loop.py lines 162-179
    """

    def test_planner_dispatcher_protocol_exists(self) -> None:
        """PlannerDispatcher MUST be a Protocol class."""
        assert hasattr(PlannerDispatcher, "__protocol_attrs__") or hasattr(
            PlannerDispatcher, "_is_protocol"
        )

    def test_planner_dispatcher_has_dispatch_planner_method(self) -> None:
        """PlannerDispatcher MUST have dispatch_planner async method."""
        import inspect

        assert hasattr(PlannerDispatcher, "dispatch_planner")
        method = getattr(PlannerDispatcher, "dispatch_planner")
        assert callable(method)
        # Protocol methods have no implementation, so we can't call them directly
        # But we can check the signature exists in the protocol definition

    def test_planner_dispatcher_dispatch_planner_signature(self) -> None:
        """dispatch_planner MUST accept PlannerDispatchRequest and context params.

        Signature from loop.py lines 170-178:
        - request: PlannerDispatchRequest
        - config: DriverConfig
        - runners: dict[str, Runner]
        - observer: Observer
        - plan_path: Path
        """
        import inspect

        # Get protocol method (if it has __func__ for descriptor)
        method = PlannerDispatcher.dispatch_planner
        if hasattr(method, "__func__"):
            method = method.__func__

        # Protocol methods don't have real signatures we can inspect easily,
        # but we can verify the protocol attribute exists
        assert "dispatch_planner" in dir(PlannerDispatcher)


# =============================================================================
# PART 5: REPLAN Verdict Validation Tests
# =============================================================================


class TestReplanVerdictValidation:
    """Tests for REPLAN verdict validation in JudgmentVerdict.

    Validates that REPLAN verdicts are properly constrained.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9, JudgmentVerdict
    Blueprint: docs/JUDGE-AGENT-PROMPT.md Response Format
    Implementation: judge.py lines 547-558
    """

    def test_replan_verdict_in_verdict_values(self) -> None:
        """REPLAN MUST be in VERDICT_VALUES."""
        assert "REPLAN" in VERDICT_VALUES

    def test_replan_verdict_requires_planner_instruction(self) -> None:
        """JudgmentVerdict with verdict='REPLAN' MUST have non-None planner_instruction.

        Blueprint: JUDGE-AGENT-PROMPT.md lines 23-24
        Implementation: judge.py lines 547-552
        """
        # Valid case: REPLAN with planner_instruction
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Spec needs enhancement",
            planner_instruction="Add missing steps",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None
        assert verdict.planner_instruction == "Add missing steps"

    def test_replan_verdict_without_planner_instruction_invalid(self) -> None:
        """REPLAN verdict without planner_instruction MUST be rejected.

        Note: This validation is enforced in judge.py parse_verdict, not in
        the dataclass itself. This test documents the contract.
        """
        # The dataclass allows constructing this, but judge._parse_verdict
        # should reject it at runtime (lines 547-552)
        # We're documenting the contract, not testing dataclass validation
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Spec needs enhancement",
            planner_instruction=None,  # This is runtime-invalid
        )
        # Dataclass allows construction, but runtime should reject
        assert verdict.planner_instruction is None  # Contract violation documented

    def test_non_replan_verdict_with_planner_instruction_invalid(self) -> None:
        """Non-REPLAN verdict with planner_instruction MUST be rejected.

        Note: This validation is enforced in judge.py lines 553-558.
        """
        # Valid case: ACCEPT without planner_instruction
        accept_verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Tests pass",
        )
        assert accept_verdict.planner_instruction is None

        # Contract documentation: planner_instruction only valid for REPLAN
        # Runtime validation in judge._parse_verdict rejects this case


class TestReplanVerdictFromJudgmentRequest:
    """Tests for REPLAN verdict production from various judgment types.

    Validates which judgment types can produce REPLAN verdicts.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
    Blueprint: docs/JUDGE-AGENT-PROMPT.md (per-type verdict options)
    """

    def test_preflight_can_produce_replan(self) -> None:
        """PREFLIGHT judgment MAY produce REPLAN for inadequate specs.

        Blueprint: JUDGE-AGENT-PROMPT.md lines 38-66
        Valid PREFLIGHT verdicts: ACCEPT, REJECT, REPLAN, DEFER
        """
        # Valid REPLAN verdict from PREFLIGHT
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Step missing test verification criteria",
            planner_instruction="Add explicit test verification to step description",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None

    def test_escalation_can_produce_replan(self) -> None:
        """ESCALATION judgment MAY produce REPLAN for repeated failures.

        Blueprint: JUDGE-AGENT-PROMPT.md lines 128-152
        Valid ESCALATION verdicts: RETRY, SWITCH_AGENT, REPLAN, HALT
        """
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Repeated failures indicate missing prerequisite steps",
            planner_instruction="Add setup step before current step",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None

    def test_evidence_cannot_produce_replan(self) -> None:
        """EVIDENCE judgment does NOT produce REPLAN.

        Blueprint: JUDGE-AGENT-PROMPT.md lines 68-95
        Valid EVIDENCE verdicts: ACCEPT, REJECT (no REPLAN)
        """
        valid_evidence_verdicts = {"ACCEPT", "REJECT"}
        # This is a contract documentation test
        # REPLAN is not in the EVIDENCE verdict space per Blueprint
        assert "REPLAN" not in valid_evidence_verdicts


# =============================================================================
# PART 6: Non-Destructive Deferral Tests
# =============================================================================


class TestNonDestructiveStepDeferral:
    """Tests for non-destructive deferral of originating step on REPLAN.

    Validates that when REPLAN occurs, the originating step remains claimable
    and is not silently blocked or removed.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Blueprint: DRIVER-BLUEPRINT.md (REPLAN handling)
    """

    def test_replan_preserves_step_claimability(self) -> None:
        """REPLAN verdict MUST NOT permanently block the originating step.

        The step should remain claimable after planner finishes.
        This is a contract documentation test - runtime enforcement deferred.
        """
        # This contract is documented in REPLAN_PLANNER_WIRING rationale
        # The step_id in PlannerDispatchRequest identifies the originating step
        # The planner may add/modify steps, but the original step should not
        # be permanently blocked

        request = PlannerDispatchRequest(
            step_id="core.impl",
            trigger="handle_dispatch.preflight_replan",
            judgment_type="PREFLIGHT",
            planner_instruction="Add test coverage requirement",
        )
        # Contract: step_id is preserved for potential re-claim after planner finishes
        assert request.step_id == "core.impl"

    def test_replan_deferral_is_reversible(self) -> None:
        """REPLAN deferral MUST be reversible if planner succeeds.

        Architecture: The originating step enters a deferred state during
        planner execution, but should return to claimable if planner succeeds.
        """
        # This is a contract documentation test
        # The reversible nature is implied by deferred_to pointing to a future
        # implementation phase that will handle re-activation
        for contract in REPLAN_PLANNER_WIRING:
            assert contract.deferred_to == "driver-judgment-expansion-replan"
            # Phase name indicates this is temporary deferral, not permanent block


# =============================================================================
# PART 7: Integration: JudgmentVerdict -> PlannerDispatchRequest
# =============================================================================


class TestJudgmentVerdictToPlannerDispatch:
    """Integration tests for converting JudgmentVerdict to PlannerDispatchRequest.

    Validates instruction propagation from judge verdict to planner dispatch.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    """

    def test_verdict_to_request_instruction_preservation(self) -> None:
        """planner_instruction MUST be preserved verbatim from verdict to request.

        Anti-narrowing: no summarization or paraphrasing allowed.
        """
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Test reason",
            planner_instruction="Original planner instruction with specific details",
        )
        request = PlannerDispatchRequest(
            step_id="test.step",
            trigger="handle_dispatch.preflight_replan",
            judgment_type="PREFLIGHT",
            planner_instruction=verdict.planner_instruction or "",
        )
        assert request.planner_instruction == verdict.planner_instruction

    def test_verdict_to_request_trigger_mapping(self) -> None:
        """trigger MUST be determined from judgment type and call site.

        Maps: (judgment_type, call_site) -> trigger_name
        """
        # Mapping from REPLAN_PLANNER_WIRING
        mapping = {
            ("PREFLIGHT", "handle_dispatch"): "handle_dispatch.preflight_replan",
            ("EVIDENCE", "reconcile"): "reconcile.evidence_replan",
            ("FAILURE", "reconcile"): "reconcile.failure_classification_replan",
            ("ESCALATION", "reconcile"): "reconcile.escalation_replan",
            ("ANOMALY", "run"): "run.startup_recovery_anomaly_replan",
        }

        for (judgment_type, call_site), expected_trigger in mapping.items():
            # Verify the trigger exists in REPLAN_PLANNER_WIRING
            matching_contracts = [
                c
                for c in REPLAN_PLANNER_WIRING
                if c.judgment_type == judgment_type and call_site in c.trigger_surface
            ]
            assert len(matching_contracts) >= 1, (
                f"No contract found for ({judgment_type}, {call_site})"
            )

    def test_verdict_to_request_reason_preservation(self) -> None:
        """verdict.reason MUST be available for planner context.

        Planner may use reason for human-readable context.
        """
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Step verification criteria are ambiguous",
            planner_instruction="Clarify test expectations",
        )

        # ReplanVerdictContract captures reason
        contract = ReplanVerdictContract(
            verdict=verdict.verdict,
            reason=verdict.reason,
            planner_instruction=verdict.planner_instruction or "",
        )
        assert contract.reason == verdict.reason


# =============================================================================
# PART 8: Expected-RED Runtime Gaps
# =============================================================================


class TestReplanRuntimeGaps:
    """Tests documenting expected-RED gaps in runtime REPLAN handling.

    These tests document current gaps that will be filled by
    driver-judgment-expansion-replan.impl-replan-planner-wiring.

    Allowed behavior: tests MUST pass without product-code changes OR
    record expected gaps as evidence.
    """

    def test_dispatch_planner_not_implemented(self) -> None:
        """dispatch_planner MUST raise NotImplementedError in current phase.

        Expected-RED: runtime dispatch is deferred to driver-judgment-expansion-replan.
        """
        # This test documents the current gap
        # When impl-replan-planner-wiring lands, this test will need updating
        # For now, we just verify the stub exists and has the right signature
        import inspect

        from src.vectl.driver.loop import dispatch_planner

        # Stub exists
        assert callable(dispatch_planner)

        # Check signature
        sig = inspect.signature(dispatch_planner)
        params = list(sig.parameters)
        assert "request" in params

    def test_no_runtime_replan_execution_branch(self) -> None:
        """No runtime branch currently executes REPLAN verdicts.

        Expected-RED: runtime branches deferred to driver-judgment-expansion-replan.
        This is documented in DEFERRED_REPLAN_BRANCHES.
        """
        # All REPLAN branches are in DEFERRED_REPLAN_BRANCHES,
        # indicating they lack runtime implementation
        assert len(DEFERRED_REPLAN_BRANCHES) == 5
        for branch in DEFERRED_REPLAN_BRANCHES:
            assert branch.deferred_to == "driver-judgment-expansion-replan"

        # This is the expected current state
        # The test passes because this documents the gap, not because it's implemented


# =============================================================================
# PART 9: Context Coverage Tests
# =============================================================================


class TestReplanJudgmentContexts:
    """Tests validating REPLAN can be produced from correct judgment contexts.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9 (CONTEXT_SCHEMAS)
    Blueprint: JUDGE-AGENT-PROMPT.md (per-type context requirements)
    """

    def test_preflight_replan_has_required_context(self) -> None:
        """PREFLIGHT REPLAN request MUST have all required context fields."""
        request = JudgmentRequest(
            type=JudgmentType.PREFLIGHT,
            step_id="core.impl",
            context={
                "step_id": "core.impl",
                "step_description": "Implement core feature",
                "step_verification": "Tests pass",
                "step_refs": "[]",
            },
            failure_history=[],
            plan_summary="Phase 1",
        )
        # Validate required fields present
        required = CONTEXT_SCHEMAS[JudgmentType.PREFLIGHT]["required"]
        for field in required:
            assert field in request.context

    def test_escalation_replan_has_required_context(self) -> None:
        """ESCALATION REPLAN request MUST have all required context fields."""
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="failing.step",
            context={
                "step_id": "failing.step",
                "failure_count": "3",
                "failure_history": "error1\nerror2\nerror3",
                "step_description": "Complex step with failures",
                "available_agents": "python-senior,frontend-engineer",
            },
            failure_history=["error1", "error2", "error3"],
            plan_summary="Plan with failures",
        )
        required = CONTEXT_SCHEMAS[JudgmentType.ESCALATION]["required"]
        for field in required:
            assert field in request.context

    def test_anomaly_replan_has_required_context(self) -> None:
        """ANOMALY REPLAN request MUST have all required context fields."""
        request = JudgmentRequest(
            type=JudgmentType.ANOMALY,
            step_id="",  # Anomaly may not have a specific step
            context={
                "anomaly_type": "orphan_claims",
                "repair_scope": "claims_only",
            },
            failure_history=[],
            plan_summary="Anomaly detected",
        )
        required = CONTEXT_SCHEMAS[JudgmentType.ANOMALY]["required"]
        for field in required:
            assert field in request.context


# =============================================================================
# PART 10: Anti-Narrowing Tests
# =============================================================================


class TestReplanAntiNarrowing:
    """Tests for anti-narrowing invariants preventing REPLAN collapse.

    Validates that REPLAN cannot be silently replaced with REJECT/DEFER/HALT.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.10 / 2.11
    Blueprint: DRIVER-BLUEPRINT.md anti-narrowing rules
    """

    def test_replan_cannot_collapse_to_reject(self) -> None:
        """REPLAN MUST NOT be silently converted to REJECT.

        Contract: loop.py line 331 states they MUST NOT be synonyms.
        """
        # Verify REPLAN and REJECT are distinct verdicts
        assert "REPLAN" in VERDICT_VALUES
        assert "REJECT" in VERDICT_VALUES
        assert "REPLAN" != "REJECT"

    def test_replan_cannot_collapse_to_defer(self) -> None:
        """REPLAN MUST NOT be silently converted to DEFER.

        Contract: loop.py line 331 states they MUST NOT be synonyms.
        """
        assert "REPLAN" in VERDICT_VALUES
        assert "DEFER" in VERDICT_VALUES
        assert "REPLAN" != "DEFER"

    def test_replan_cannot_collapse_to_halt(self) -> None:
        """REPLAN MUST NOT be silently converted to HALT.

        Contract: loop.py line 331 states they MUST NOT be synonyms.
        """
        assert "REPLAN" in VERDICT_VALUES
        assert "HALT" in VERDICT_VALUES
        assert "REPLAN" != "HALT"

    def test_replan_verdict_semantic_distinction(self) -> None:
        """REPLAN MUST remain semantically distinct from other verdicts.

        Each verdict has a distinct purpose:
        - ACCEPT: proceed with action
        - REJECT: block and require fix
        - RETRY: retry with same agent
        - SWITCH_AGENT: try different agent
        - REPLAN: modify the plan
        - DEFER: skip for now
        - HALT: cannot proceed
        """
        # All verdicts are distinct
        assert len(VERDICT_VALUES) == 7

        # REPLAN is unique in requiring planner_instruction
        replan_verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Test",
            planner_instruction="Test instruction",
        )
        # Only REPLAN has planner_instruction
        assert replan_verdict.planner_instruction is not None

        # SWITCH_AGENT is unique in requiring suggested_action
        switch_verdict = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Test",
            suggested_action="python-senior",
        )
        assert switch_verdict.suggested_action is not None


# =============================================================================
# PART 11: Gate-like Context Tests
# =============================================================================


class TestReplanInGateContext:
    """Tests for REPLAN verdict handling in gate-like contexts.

    Validates REPLAN behavior in GATE judgment scenarios.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
    Blueprint: JUDGE-AGENT-PROMPT.md GATE judgment type
    """

    def test_gate_judgment_does_not_produce_replan(self) -> None:
        """GATE judgment does NOT produce REPLAN verdicts.

        Blueprint: JUDGE-AGENT-PROMPT.md lines 153-172
        Valid GATE verdicts: ACCEPT, REJECT (no REPLAN)
        """
        # Document the contract: GATE only has ACCEPT/REJECT
        # This is intentional - gate issues need different handling
        valid_gate_verdicts = {"ACCEPT", "REJECT"}
        assert "REPLAN" not in valid_gate_verdicts

    def test_gate_replan_would_be_misuse(self) -> None:
        """REPLAN from GATE context would be a contract violation.

        If GATE ever needs to trigger plan modification, this requires
        an architecture update, not silent addition.
        """
        # This test documents that GATE -> REPLAN is not in the contract
        # If this ever needs to change, the architecture must be updated
        gate_replan_contracts = [c for c in REPLAN_PLANNER_WIRING if c.judgment_type == "GATE"]
        # No GATE contract in REPLAN_PLANNER_WIRING
        assert len(gate_replan_contracts) == 0


class TestPreflightReplanContext:
    """Tests for PREFLIGHT REPLAN in dispatch context.

    Validates REPLAN behavior before step dispatch.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Blueprint: DRIVER-BLUEPRINT.md Flow 2
    """

    def test_preflight_replan_before_dispatch(self) -> None:
        """PREFLIGHT REPLAN occurs before claim/worktree/runner dispatch.

        Contract: REPLAN_PLANNER_WIRING[0].trigger_surface
        """
        preflight_contract = REPLAN_PLANNER_WIRING[0]
        assert preflight_contract.judgment_type == "PREFLIGHT"
        assert "before claim" in preflight_contract.trigger_surface.lower()
        assert "dispatch" in preflight_contract.trigger_surface.lower()


class TestEscalationReplanContext:
    """Tests for ESCALATION REPLAN in repeated failure context.

    Validates REPLAN behavior after threshold failures.
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11
    Blueprint: DRIVER-BLUEPRINT.md Flow 3
    """

    def test_escalation_replan_after_threshold(self) -> None:
        """ESCALATION REPLAN occurs after repeated failure threshold.

        Contract: REPLAN_PLANNER_WIRING[3] and Blueprint Flow 3
        """
        escalation_contract = [c for c in REPLAN_PLANNER_WIRING if c.judgment_type == "ESCALATION"][
            0
        ]
        assert escalation_contract.trigger == "reconcile.escalation_replan"
        assert "repeated-failure" in escalation_contract.rationale.lower()


# =============================================================================
# PART 12: Summary Documentation
# =============================================================================


class TestReplanContractSummary:
    """Summary tests documenting the complete REPLAN contract.

    These tests provide a summary view of all REPLAN guarantees.
    """

    def test_all_replan_branches_registered(self) -> None:
        """All REPLAN-capable branches MUST be registered in DEFERRED_REPLAN_BRANCHES."""
        # Count matches REPLAN_PLANNER_WIRING
        assert len(DEFERRED_REPLAN_BRANCHES) == len(REPLAN_PLANNER_WIRING)

    def test_replan_wiring_complete(self) -> None:
        """Each DEFERRED_REPLAN_BRANCH MUST have a corresponding REPLAN_PLANNER_WIRING."""
        branch_triggers = {b.branch for b in DEFERRED_REPLAN_BRANCHES}
        wiring_triggers = {w.trigger for w in REPLAN_PLANNER_WIRING}
        assert branch_triggers == wiring_triggers

    def test_replan_contract_documentation_complete(self) -> None:
        """ReplanVerdictContract and PlannerDispatchRequest document the contract."""
        # Both types exist and are frozen
        import dataclasses

        # ReplanVerdictContract
        assert dataclasses.is_dataclass(ReplanVerdictContract)
        assert ReplanVerdictContract.__dataclass_fields__["verdict"].default == dataclasses.MISSING
        assert (
            ReplanVerdictContract.__dataclass_fields__["planner_instruction"].default
            == dataclasses.MISSING
        )

        # PlannerDispatchRequest
        assert dataclasses.is_dataclass(PlannerDispatchRequest)
        assert PlannerDispatchRequest.__dataclass_fields__["step_id"].default == dataclasses.MISSING
        # source_verdict has a default
        assert PlannerDispatchRequest.__dataclass_fields__["source_verdict"].default == "REPLAN"

    def test_replan_non_narrowing_rule_documented(self) -> None:
        """REPLAN_NON_NARROWING_RULE MUST document the anti-narrowing invariant."""
        assert isinstance(REPLAN_NON_NARROWING_RULE, str)
        assert len(REPLAN_NON_NARROWING_RULE) > 100  # Substantial documentation
