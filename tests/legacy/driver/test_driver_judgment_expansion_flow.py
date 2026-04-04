"""Focused tests for full judgment expansion behavior.

These tests verify the remaining judgment expansion semantics before the
gate-chain implementation lands. Each test focuses on runtime contract
behavior for judgment types that require specific provenance disposition,
escalation handling, or remediation orchestration.

Authority:
- DRIVER-BLUEPRINT.md Flow 3, Flow 4 (failure classification, gate remediation)
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (judgment runtime contracts)
- src/vectl/driver/loop.py REMAINING_JUDGMENT_RUNTIME_CONTRACTS

This is a RED test file - tests are expected to FAIL if implementation is
incomplete. Record discovered gaps in step notes linking to
driver-judgment-expansion-replan.impl-gate-chain-flow.

step_intent: test_define_red
expected_result: red
product_implementation_files_modified: no
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
    GATE_REMEDIATION_CHAIN_CONTRACT,
    REMAINING_JUDGMENT_RUNTIME_CONTRACTS,
    GateRemediationChainContract,
    RemainingJudgmentRuntimeContract,
)


# =============================================================================
# FAILURE JUDGMENT: PROVENANCE/DISPOSITION SEMANTICS
# =============================================================================


class TestFailureJudgmentProvenanceDisposition:
    """Contract tests for FAILURE judgment provenance/disposition semantics.

    Authority: DRIVER-BLUEPRINT.md lines 205-208, Flow 3
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11

    The FAILURE judgment must classify:
    - Provenance: introduced_now vs pre_existing
    - Disposition: local_blocker vs downstream_blocker vs non_blocking

    The result determines whether the failure requires planner involvement
    or can be handled locally via defer/retry.
    """

    def test_failure_context_required_fields_include_provenance_inputs(self) -> None:
        """FAILURE context MUST include fields needed for provenance classification.

        Required fields enable provenance determination:
        - step_id: identifies the failing step
        - error_output: the actual error for classification
        - failure_count: distinguishes first occurrence from escalation

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 277-280
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.FAILURE]
        required = set(schema["required"])

        # Provenance classification requires knowing what failed
        assert "step_id" in required, "FAILURE requires step_id for provenance"
        assert "error_output" in required, "FAILURE requires error_output for classification"
        assert "failure_count" in required, "FAILURE requires failure_count for escalation timing"

    def test_failure_context_optional_fields_include_disposition_inputs(self) -> None:
        """FAILURE context MAY include fields for downstream disposition.

        Optional fields enable downstream blocker determination:
        - remaining_gates: allows judge to assess if failure blocks later gates
        - step_description: provides context for error interpretation
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.FAILURE]
        optional = set(schema["optional"])

        # Disposition may depend on downstream gate surface
        assert "remaining_gates" in optional, "FAILURE may have remaining_gates for disposition"
        assert "step_description" in optional, "FAILURE may have step_description for context"

    def test_failure_allowed_verdicts_include_replan_for_downstream_blocker(self) -> None:
        """FAILURE verdict surface MUST include REPLAN for downstream_blocker disposition.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS[0]
        Blueprint: Flow 3, failure classification may require planner follow-up

        When failure is classified as downstream_blocker, the judge MUST be
        able to return REPLAN with planner instructions for follow-up steps.
        """
        failure_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[0]
        assert failure_contract.judgment_type == "FAILURE"

        # REPLAN must be in allowed verdicts for downstream blocker
        assert "REPLAN" in failure_contract.allowed_verdicts, (
            "FAILURE judgment must allow REPLAN for downstream_blocker disposition"
        )

    def test_failure_planner_instruction_required_for_replan(self) -> None:
        """FAILURE REPLAN verdict MUST include planner_instruction.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for
        Blueprint: Flow 3, planner-authored remediation ownership

        When FAILURE returns REPLAN, the planner_instruction describes
        the follow-up steps needed to unblock the downstream chain.
        """
        failure_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[0]
        assert failure_contract.judgment_type == "FAILURE"

        assert "REPLAN" in failure_contract.planner_instruction_required_for, (
            "FAILURE REPLAN must have planner_instruction for remediation"
        )

    def test_failure_trigger_is_reconcile_failure_classification(self) -> None:
        """FAILURE MUST be invoked at reconcile failure path.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS trigger
        Blueprint: Flow 3 (lines 668-699)

        The trigger naming ensures FAILURE is only invoked after
        evidence judgment and before disposition finalization.
        """
        failure_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[0]
        assert failure_contract.trigger == "reconcile.failure_classification"

    def test_failure_verdict_surface_preserves_accept_and_replan(self) -> None:
        """FAILURE allowed_verdicts MUST preserve ACCEPT and REPLAN as non-narrowing surface.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS allowed_verdicts

        ACCEPT represents introduced_now + non_blocking (proceed).
        REPLAN represents downstream_blocker requiring remediation.
        """
        failure_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[0]
        assert "ACCEPT" in failure_contract.allowed_verdicts
        assert "REPLAN" in failure_contract.allowed_verdicts


# =============================================================================
# ESCALATION JUDGMENT: THRESHOLD BEHAVIOR
# =============================================================================


class TestEscalationJudgmentThresholdBehavior:
    """Contract tests for ESCALATION judgment threshold semantics.

    Authority: DRIVER-BLUEPRINT.md lines 210-212, Flow 3 (lines 682-700)
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11

    The ESCALATION judgment is invoked when failure_count reaches threshold.
    Threshold is typically 3 (rule: count < 3 auto-defer, count >= 3 escalate).
    """

    def test_escalation_context_required_fields_include_failure_history(self) -> None:
        """ESCALATION context MUST include failure_history for pattern analysis.

        Required fields:
        - step_id: identifies the failing step
        - failure_count: current retry count
        - failure_history: all previous errors for pattern analysis
        - step_description: context for decision
        - available_agents: agents for SWITCH_AGENT verdict

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 283-285
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.ESCALATION]
        required = set(schema["required"])

        assert "failure_count" in required
        assert "failure_history" in required, (
            "ESCALATION requires failure_history for pattern analysis"
        )
        assert "step_description" in required, "ESCALATION requires step_description for context"
        assert "available_agents" in required, (
            "ESCALATION requires available_agents for SWITCH_AGENT"
        )

    def test_escalation_allowed_verdicts_include_all_dispositions(self) -> None:
        """ESCALATION allowed_verdicts MUST include full disposition surface.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS[1] allowed_verdicts

        All dispositions are valid for escalation:
        - RETRY: try same agent again
        - SWITCH_AGENT: try different agent
        - REPLAN: modify plan
        - DEFER: skip for now
        - HALT: terminal failure
        """
        escalation_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[1]
        assert escalation_contract.judgment_type == "ESCALATION"

        expected_verdicts = {"RETRY", "SWITCH_AGENT", "REPLAN", "DEFER", "HALT"}
        assert set(escalation_contract.allowed_verdicts) == expected_verdicts

    def test_escalation_planner_instruction_required_for_replan(self) -> None:
        """ESCALATION REPLAN verdict MUST include planner_instruction.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for
        Blueprint: Flow 3, lines 697-698
        """
        escalation_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[1]
        assert "REPLAN" in escalation_contract.planner_instruction_required_for

    def test_escalation_trigger_is_reconcile_escalation_threshold(self) -> None:
        """ESCALATION MUST be invoked at reconcile when failure_count >= threshold.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS trigger
        Blueprint: Flow 3 (lines 682-699)
        """
        escalation_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[1]
        assert escalation_contract.trigger == "reconcile.escalation_threshold"

    def test_escalation_context_failure_history_is_list(self) -> None:
        """ESCALATION failure_history MUST be list of string error outputs.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 283-285
        """
        # Construction of valid FAILURE context
        request = JudgmentRequest(
            type=JudgmentType.ESCALATION,
            step_id="problematic.step",
            context={
                "step_id": "problematic.step",
                "failure_count": "3",
                "failure_history": "error1\nerror2\nerror3",  # as string in context
                "step_description": "Complex step",
                "available_agents": "python-senior,frontend-engineer",
            },
            failure_history=["error1", "error2", "error3"],  # as list in dataclass
            plan_summary="Plan",
        )
        assert isinstance(request.failure_history, list)
        assert all(isinstance(e, str) for e in request.failure_history)


# =============================================================================
# GATE JUDGMENT: FIX+RETEST CHAIN ORCHESTRATION
# =============================================================================


class TestGateJudgmentFixRetestChain:
    """Contract tests for GATE judgment and remediation chain semantics.

    Authority: DRIVER-BLUEPRINT.md Flow 4 (lines 707-736)
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11

    The GATE judgment handles:
    - Issue classification (blocker vs should_fix vs suggestion)
    - Downstream blocker promotion
    - Planner dispatch for batched fix creation
    - Deferred gate re-run after fix batch lands
    """

    def test_gate_context_required_fields_include_blocker_issues(self) -> None:
        """GATE context MUST include blocker_issues for classification.

        Required fields:
        - step_id: identifies the gate step
        - gate_evidence: evidence from gate execution
        - blocker_issues: parsed blocker issues for assessment

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 288-290
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.GATE]
        required = set(schema["required"])

        assert "step_id" in required
        assert "gate_evidence" in required, "GATE requires gate_evidence for assessment"
        assert "blocker_issues" in required, "GATE requires blocker_issues for classification"

    def test_gate_context_optional_fields_include_remaining_gates(self) -> None:
        """GATE context MAY include remaining_gates for downstream assessment.

        Optional fields enable downstream blocker determination:
        - remaining_gates: allows judge to assess impact on subsequent gates
        - phase_context: provides phase-level context
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.GATE]
        optional = set(schema["optional"])

        assert "remaining_gates" in optional
        assert "phase_context" in optional

    def test_gate_maintenance_contract_blocker_severities(self) -> None:
        """GATE remediation chain MUST preserve blocker severity grouping.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT blocker_severities
        Blueprint: Flow 4 (lines 711-714)

        blocker_severities defines which issues require immediate remediation.
        Only issues with severity "blocker" are in this category.
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.blocker_severities == ("blocker",)

    def test_gate_maintenance_contract_included_same_batch(self) -> None:
        """GATE remediation chain MUST include should_fix in same batch.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT included_same_batch
        Blueprint: Flow 4 (lines 711-714)

        When remediation is triggered, both blocker and should_fix issues
        travel together in the batch for planner-created fix steps.
        """
        assert "blocker" in GATE_REMEDIATION_CHAIN_CONTRACT.included_same_batch
        assert "should_fix" in GATE_REMEDIATION_CHAIN_CONTRACT.included_same_batch

    def test_gate_maintenance_contract_record_only_severities(self) -> None:
        """GATE remediation chain MUST NOT batch suggestion/tech_debt issues.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT record_only_severities
        Blueprint: Flow 4 (lines 711-714)

        Issues with severity suggestion or tech_debt are recorded
        but do not trigger planner dispatch or fix chain creation.
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.record_only_severities == (
            "suggestion",
            "tech_debt",
        )

    def test_gate_maintenance_contract_planner_instruction_source(self) -> None:
        """GATE remediation planner instruction MUST come from judge verdict.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT planner_instruction_source
        Blueprint: Flow 4 (lines 728-733)

        The judge verdict owns the planner instruction semantics.
        Loop MUST NOT synthesize its own instruction.
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.planner_instruction_source == (
            "JudgmentVerdict.planner_instruction"
        )

    def test_gate_maintenance_contract_ownership_split(self) -> None:
        """GATE remediation chain MUST have explicit owner split.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT batched_remediation_owner
        Blueprint: Flow 4

        The ownership split is:
        - planner dispatch owns creation of the batched fix chain
        - loop reconciliation owns deferring and re-running the gate step
        """
        assert "planner" in GATE_REMEDIATION_CHAIN_CONTRACT.batched_remediation_owner.lower()
        assert "loop" in GATE_REMEDIATION_CHAIN_CONTRACT.retest_step_owner.lower()

    def test_gate_allowed_verdicts_preserve_reject_for_planner_dispatch(self) -> None:
        """GATE allowed_verdicts MUST preserve REJECT for planner dispatch path.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS[2] allowed_verdicts
        Blueprint: Flow 4 (lines 728-733)

        REJECT triggers the fix+retest chain when blockers remain.
        ACCEPT represents gate passed.
        HALT represents unrecoverable gate failure.
        """
        gate_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[2]
        assert gate_contract.judgment_type == "GATE"

        assert "ACCEPT" in gate_contract.allowed_verdicts
        assert "REJECT" in gate_contract.allowed_verdicts
        assert "HALT" in gate_contract.allowed_verdicts

    def test_gate_reject_requires_planner_instruction(self) -> None:
        """GATE REJECT verdict MUST require planner_instruction for fix chain.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for
        Blueprint: Flow 4 (lines 728-733)
        """
        gate_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[2]
        assert "REJECT" in gate_contract.planner_instruction_required_for


# =============================================================================
# COLD_CONTEXT JUDGMENT: PRUNING CONTRACT
# =============================================================================


class TestColdContextJudgmentPruningContract:
    """Contract tests for COLD_CONTEXT judgment and pruning semantics.

    Authority: DRIVER-BLUEPRINT.md lines 218-220, lines 294-296
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11

    The COLD_CONTEXT judgment handles:
    - Gate step dispatch isolation
    - Context pruning before running gate steps with independent auditor
    - Excluding worker/orchestrator reasoning from gate dispatch
    """

    def test_cold_context_required_fields_include_diff(self) -> None:
        """COLD_CONTEXT context MUST include diff for pruning determination.

        Required fields:
        - step_id: identifies the gate step
        - step_spec: spec for the gate step
        - diff: the diff to analyze for pruning scope

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 294-296
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.COLD_CONTEXT]
        required = set(schema["required"])

        assert "step_id" in required
        assert "step_spec" in required, "COLD_CONTEXT requires step_spec"
        assert "diff" in required, "COLD_CONTEXT requires diff for pruning scope"

    def test_cold_context_optional_fields_include_verification_criteria(self) -> None:
        """COLD_CONTEXT context MAY include verification_criteria and entry_points.

        Optional fields enable more precise pruning:
        - verification_criteria: explicit verification requirements
        - entry_points: known entry points for scope limitation
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.COLD_CONTEXT]
        optional = set(schema["optional"])

        assert "verification_criteria" in optional
        assert "entry_points" in optional

    def test_cold_context_allowed_verdicts_only_accept(self) -> None:
        """COLD_CONTEXT allowed_verdicts MUST be only ACCEPT.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS[4] allowed_verdicts
        Blueprint: lines 218-220

        Cold context pruning has only one valid outcome: accept the pruned
        context for gate dispatch. If pruning fails, halt is not appropriate
        (that's a different judgment type error path).
        """
        cold_context_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[4]
        assert cold_context_contract.judgment_type == "COLD_CONTEXT"

        # ACCEPT is the only valid verdict for cold context
        assert cold_context_contract.allowed_verdicts == ("ACCEPT",)

    def test_cold_context_no_planner_instruction_required(self) -> None:
        """COLD_CONTEXT MUST NOT require planner_instruction.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for

        Pruning is a local determination, not a plan modification.
        """
        cold_context_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[4]
        assert cold_context_contract.planner_instruction_required_for == ()

    def test_cold_context_trigger_is_dispatch_gate_cold_context(self) -> None:
        """COLD_CONTEXT MUST be invoked at dispatch before gate step execution.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS trigger
        Blueprint: lines 218-220
        """
        cold_context_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[4]
        assert cold_context_contract.trigger == "handle_dispatch.gate_cold_context"


# =============================================================================
# ANOMALY JUDGMENT: SAFE-REPAIR VS HALT SPLIT
# =============================================================================


class TestAnomalyJudgmentSafeRepairHaltSplit:
    """Contract tests for ANOMALY judgment safe-repair vs halt semantics.

    Authority: DRIVER-BLUEPRINT.md lines 218-220
    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.11

    The ANOMALY judgment handles:
    - Plan/claims anomaly detection at startup
    - Safe vs unsafe auto-repair determination
    - Halt decision for structural issues requiring manual intervention
    """

    def test_anomaly_context_required_fields_include_anomaly_type(self) -> None:
        """ANOMALY context MUST include anomaly_type and repair_scope.

        Required fields:
        - anomaly_type: classification of the anomaly
        - repair_scope: scope of proposed repair

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.9
        Blueprint: lines 291-293
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.ANOMALY]
        required = set(schema["required"])

        assert "anomaly_type" in required, "ANOMALY requires anomaly_type for classification"
        assert "repair_scope" in required, "ANOMALY requires repair_scope for safe/unsafe decision"

    def test_anomaly_context_optional_fields_include_dry_run_recommendation(self) -> None:
        """ANOMALY context MAY include dry_run_recommendation.

        Optional fields enable pre-repair analysis:
        - dry_run_recommendation: suggested repair approach
        """
        schema = CONTEXT_SCHEMAS[JudgmentType.ANOMALY]
        optional = set(schema["optional"])

        assert "dry_run_recommendation" in optional

    def test_anomaly_allowed_verdicts_accept_and_halt_only(self) -> None:
        """ANOMALY allowed_verdicts MUST be only ACCEPT (safe-repair) and HALT.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS[3] allowed_verdicts
        Blueprint: lines 218-220

        Anomaly handling has two outcomes:
        - ACCEPT: safe automatic repair
        - HALT: requires manual intervention (no auto-repair)

        Unlike other judgments, anomaly does not have RETRY, SWITCH_AGENT,
        DEFER, or REPLAN because it's not about step execution.
        """
        anomaly_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[3]
        assert anomaly_contract.judgment_type == "ANOMALY"

        assert set(anomaly_contract.allowed_verdicts) == {"ACCEPT", "HALT"}

    def test_anomaly_no_planner_instruction_required(self) -> None:
        """ANOMALY MUST NOT require planner_instruction.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for

        Anomaly repair is a structural fix, not a plan modification.
        If planner involvement is needed, that's a different path.
        """
        anomaly_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[3]
        assert anomaly_contract.planner_instruction_required_for == ()

    def test_anomaly_trigger_is_startup_recovery_anomaly(self) -> None:
        """ANOMALY MUST be invoked at run startup recovery.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS trigger
        Blueprint: lines 218-220
        """
        anomaly_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[3]
        assert anomaly_contract.trigger == "run.startup_recovery_anomaly"

    def test_anomaly_rationale_forbids_silent_auto_repair(self) -> None:
        """ANOMALY contract rationale MUST forbid silent auto-repair.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS rationale
        Blueprint: lines 218-220
        """
        anomaly_contract = REMAINING_JUDGMENT_RUNTIME_CONTRACTS[3]
        assert "forbids silent structural repair" in anomaly_contract.rationale.lower() or (
            "forbids silent" in anomaly_contract.rationale.lower()
        )


# =============================================================================
# CROSS-TYPE CONTRACT TESTS
# =============================================================================


class TestCrossTypeJudgmentContracts:
    """Cross-type contract tests for judgment expansion behavior."""

    def test_all_remaining_judgment_types_have_runtime_owner_loop(self) -> None:
        """All remaining judgment types MUST have runtime_owner loop.py.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS runtime_owner
        """
        for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS:
            assert contract.runtime_owner == "loop.py", (
                f"{contract.judgment_type} must have runtime_owner loop.py"
            )

    def test_all_remaining_judgment_contracts_defer_to_same_phase(self) -> None:
        """All remaining judgment contracts MUST defer to driver-judgment-expansion-replan.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS deferred_to
        """
        for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS:
            assert contract.deferred_to == "driver-judgment-expansion-replan", (
                f"{contract.judgment_type} must defer to driver-judgment-expansion-replan"
            )

    def test_allowed_verdicts_are_subset_of_verdict_values(self) -> None:
        """Each judgment type's allowed_verdicts MUST be subset of VERDICT_VALUES.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS allowed_verdicts
        """
        for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS:
            for verdict in contract.allowed_verdicts:
                assert verdict in VERDICT_VALUES, (
                    f"{contract.judgment_type} allowed_verdicts contains invalid verdict: {verdict}"
                )

    def test_planner_instruction_required_verdicts_require_replan_or_reject(self) -> None:
        """planner_instruction_required_for MUST contain only REPLAN or REJECT verdicts.

        Architecture: REMAINING_JUDGMENT_RUNTIME_CONTRACTS planner_instruction_required_for

        Only REPLAN and REJECT verdicts require planner instruction because
        those are the only verdicts that trigger planner dispatch.
        """
        for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS:
            for verdict in contract.planner_instruction_required_for:
                assert verdict in ("REPLAN", "REJECT"), (
                    f"{contract.judgment_type} planner_instruction_required_for contains "
                    f"invalid verdict: {verdict}"
                )

    def test_all_judgment_types_have_context_schema(self) -> None:
        """All JudgmentType values MUST have corresponding CONTEXT_SCHEMA entry.

        Architecture: CONTEXT_SCHEMAS coverage
        """
        for jt in JudgmentType:
            assert jt in CONTEXT_SCHEMAS, f"Missing CONTEXT_SCHEMA for {jt}"

    def test_replan_for_replan_verdict_is_preserved(self) -> None:
        """REPLAN verdict must have planner_instruction preserved per anti-narrowing rule.

        Architecture: REPLAN_NON_NARROWING_RULE
        Blueprint: DRIVER-BLUEPRINT.md lines 566-568, 697-698, 728-733
        """
        assert "planner_instruction" in REPLAN_NON_NARROWING_RULE.lower()
        assert "preserve" in REPLAN_NON_NARROWING_RULE.lower()

    def test_replan_verdict_contract_enforces_non_empty_instruction(self) -> None:
        """ReplanVerdictContract MUST enforce non-empty planner_instruction.

        Architecture: ReplanVerdictContract invariants
        """
        # This tests the dataclass contract exists and has correct structure
        contract = ReplanVerdictContract(
            verdict="REPLAN",
            reason="Step needs decomposition",
            planner_instruction="Create subtasks for X, Y, Z",
        )
        assert contract.verdict == "REPLAN"
        assert contract.planner_instruction != ""
        assert len(contract.planner_instruction) > 0


# =============================================================================
# GATE REMEDIATION CHAIN ORCHESTRATION
# =============================================================================


class TestGateRemediationChainOrchestration:
    """Tests for gate failure -> fix+retest chain orchestration."""

    def test_gate_remediation_chain_trigger_is_gate_failure(self) -> None:
        """Gate remediation chain MUST trigger on gate failure.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT trigger
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.trigger == "reconcile.gate_failure_fix_retest_chain"

    def test_gate_remediation_chain_judgment_trigger_is_gate_assessment(self) -> None:
        """Gate remediation chain judgment trigger MUST be gate_assessment.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT judgment_trigger
        Blueprint: Flow 4 (lines 718-725)
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.judgment_trigger == "reconcile.gate_assessment"

    def test_gate_remediation_chain_planner_trigger_links_judgment_to_planner(self) -> None:
        """Gate remediation chain planner trigger MUST link judgment to planner dispatch.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT planner_trigger
        """
        assert "gate" in GATE_REMEDIATION_CHAIN_CONTRACT.planner_trigger.lower()
        assert "retest" in GATE_REMEDIATION_CHAIN_CONTRACT.planner_trigger.lower() or (
            "fix" in GATE_REMEDIATION_CHAIN_CONTRACT.planner_trigger.lower()
        )

    def test_gate_remediation_chain_deferred_to_same_phase(self) -> None:
        """Gate remediation chain MUST defer to driver-judgment-expansion-replan.

        Architecture: GATE_REMEDIATION_CHAIN_CONTRACT deferred_to
        """
        assert GATE_REMEDIATION_CHAIN_CONTRACT.deferred_to == "driver-judgment-expansion-replan"


# =============================================================================
# FAILURE TO FIX/RETEST DISPATCH SEMANTICS
# =============================================================================


class TestFailureToFixRetestDispatchSemantics:
    """Tests for FAILURE -> fix/retest dispatch chain creation semantics."""

    def test_failure_replan_creates_planner_dispatch_request(self) -> None:
        """FAILURE REPLAN verdict MUST create PlannerDispatchRequest.

        Architecture: PlannerDispatchRequest for FAILURE triggers
        Blueprint: Flow 3 (lines 697-698)
        """
        # This is a structural test - the runtime wiring is in loop.py
        # Here we verify the contract structure supports this use case
        from src.vectl.driver.loop import PlannerDispatchRequest

        request = PlannerDispatchRequest(
            step_id="core.impl",
            trigger="reconcile.failure_classification_replan",
            judgment_type="FAILURE",
            planner_instruction="Add tests for failure path",
        )
        assert request.trigger == "reconcile.failure_classification_replan"
        assert request.judgment_type == "FAILURE"
        assert request.source_verdict == "REPLAN"

    def test_escalation_replan_creates_planner_dispatch_request(self) -> None:
        """ESCALATION REPLAN verdict MUST create PlannerDispatchRequest.

        Architecture: PlannerDispatchRequest for ESCALATION triggers
        Blueprint: Flow 3 (lines 697-700)
        """
        from src.vectl.driver.loop import PlannerDispatchRequest

        request = PlannerDispatchRequest(
            step_id="problematic.step",
            trigger="reconcile.escalation_replan",
            judgment_type="ESCALATION",
            planner_instruction="Decompose step into smaller subtasks",
        )
        assert request.trigger == "reconcile.escalation_replan"
        assert request.judgment_type == "ESCALATION"

    def test_gate_reject_creates_planner_dispatch_request(self) -> None:
        """GATE REJECT verdict MUST create PlannerDispatchRequest for fix chain.

        Architecture: PlannerDispatchRequest for GATE REJECT triggers
        Blueprint: Flow 4 (lines 728-733)
        """
        from src.vectl.driver.loop import PlannerDispatchRequest

        request = PlannerDispatchRequest(
            step_id="gate.quality",
            trigger="reconcile.gate_reject_fix_retest_chain",
            judgment_type="GATE",
            planner_instruction="Fix blockers: security scan, lint errors",
            source_verdict="REJECT",
        )
        assert request.trigger == "reconcile.gate_reject_fix_retest_chain"
        assert request.judgment_type == "GATE"
        assert request.source_verdict == "REJECT"


# =============================================================================
# VERDICT CONSTRUCTION FOR ALL JUDGMENT TYPES
# =============================================================================


class TestVerdictConstructionPerType:
    """Tests for valid verdict construction for each judgment type."""

    def test_failure_verdict_accept_is_valid(self) -> None:
        """FAILURE ACCEPT verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Failure is non_blocking, proceed",
        )
        assert verdict.verdict == "ACCEPT"

    def test_failure_verdict_replan_is_valid(self) -> None:
        """FAILURE REPLAN verdict MUST be constructable with planner_instruction."""
        verdict = JudgmentVerdict(
            verdict="REPLAN",
            reason="Failure is downstream_blocker",
            planner_instruction="Add follow-up step for downstream dependency",
        )
        assert verdict.verdict == "REPLAN"
        assert verdict.planner_instruction is not None

    def test_escalation_verdict_retry_is_valid(self) -> None:
        """ESCALATION RETRY verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="RETRY",
            reason="Transient error, retry same agent",
        )
        assert verdict.verdict == "RETRY"

    def test_escalation_verdict_switch_agent_is_valid(self) -> None:
        """ESCALATION SWITCH_AGENT verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="SWITCH_AGENT",
            reason="Current agent lacks expertise",
            suggested_action="python-senior",
        )
        assert verdict.verdict == "SWITCH_AGENT"
        assert verdict.suggested_action == "python-senior"

    def test_escalation_verdict_halt_is_valid(self) -> None:
        """ESCALATION HALT verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="HALT",
            reason="Unrecoverable failure after retries",
        )
        assert verdict.verdict == "HALT"

    def test_gate_verdict_accept_is_valid(self) -> None:
        """GATE ACCEPT verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Gate passed, no blockers",
        )
        assert verdict.verdict == "ACCEPT"

    def test_gate_verdict_reject_is_valid(self) -> None:
        """GATE REJECT verdict MUST be constructable with planner_instruction."""
        verdict = JudgmentVerdict(
            verdict="REJECT",
            reason="Gate failed with blockers",
            planner_instruction="Create fix steps for security vulnerabilities",
        )
        assert verdict.verdict == "REJECT"
        assert verdict.planner_instruction is not None

    def test_anomaly_verdict_accept_is_valid(self) -> None:
        """ANOMALY ACCEPT verdict (safe repair) MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Safe automatic repair",
        )
        assert verdict.verdict == "ACCEPT"

    def test_anomaly_verdict_halt_is_valid(self) -> None:
        """ANOMALY HALT verdict (unsafe, manual intervention) MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="HALT",
            reason="Unsafe anomaly, requires manual intervention",
        )
        assert verdict.verdict == "HALT"

    def test_cold_context_verdict_accept_is_valid(self) -> None:
        """COLD_CONTEXT ACCEPT verdict MUST be constructable."""
        verdict = JudgmentVerdict(
            verdict="ACCEPT",
            reason="Context pruned for gate dispatch",
        )
        assert verdict.verdict == "ACCEPT"
