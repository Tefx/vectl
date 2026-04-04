"""Contract tests for deferred judgment expansion semantics.

These tests pin the remaining prompt-defined runtime surfaces that the
``driver-judgment-expansion-replan`` phase must implement without narrowing.

Authority:
- docs/JUDGE-AGENT-PROMPT.md
- DRIVER-BLUEPRINT.md Flow 3 / Flow 4
- docs/DRIVER-ARCHITECTURE.md Section 2.11
"""

from src.vectl.driver.loop import (
    CONFLICT_RESOLVER_READINESS_CONTRACT,
    GATE_REMEDIATION_CHAIN_CONTRACT,
    REMAINING_JUDGMENT_RUNTIME_CONTRACTS,
)


class TestRemainingJudgmentRuntimeContracts:
    def test_names_each_remaining_judgment_type_with_runtime_trigger(self) -> None:
        mapping = {
            contract.judgment_type: contract.trigger
            for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS
        }

        assert mapping == {
            "FAILURE": "reconcile.failure_classification",
            "ESCALATION": "reconcile.escalation_threshold",
            "GATE": "reconcile.gate_assessment",
            "ANOMALY": "run.startup_recovery_anomaly",
            "COLD_CONTEXT": "handle_dispatch.gate_cold_context",
        }

    def test_runtime_contracts_pin_deferred_owner(self) -> None:
        assert REMAINING_JUDGMENT_RUNTIME_CONTRACTS
        for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS:
            assert contract.runtime_owner == "loop.py"
            assert contract.deferred_to == "driver-judgment-expansion-replan"
            assert contract.allowed_verdicts

    def test_gate_and_replan_instruction_requirements_are_explicit(self) -> None:
        by_type = {
            contract.judgment_type: contract.planner_instruction_required_for
            for contract in REMAINING_JUDGMENT_RUNTIME_CONTRACTS
        }

        assert by_type["FAILURE"] == ("REPLAN",)
        assert by_type["ESCALATION"] == ("REPLAN",)
        assert by_type["GATE"] == ("REJECT",)
        assert by_type["ANOMALY"] == ()
        assert by_type["COLD_CONTEXT"] == ()


class TestGateRemediationChainContract:
    def test_batched_remediation_ownership_is_explicit(self) -> None:
        assert GATE_REMEDIATION_CHAIN_CONTRACT.batched_remediation_owner == (
            "planner dispatch from loop.py"
        )
        assert GATE_REMEDIATION_CHAIN_CONTRACT.retest_step_owner == (
            "loop.py gate rerun after planner-created fix batch lands"
        )

    def test_gate_failure_chain_keeps_prompt_severity_grouping(self) -> None:
        assert GATE_REMEDIATION_CHAIN_CONTRACT.blocker_severities == ("blocker",)
        assert GATE_REMEDIATION_CHAIN_CONTRACT.included_same_batch == (
            "blocker",
            "should_fix",
        )
        assert GATE_REMEDIATION_CHAIN_CONTRACT.record_only_severities == (
            "suggestion",
            "tech_debt",
        )
        assert GATE_REMEDIATION_CHAIN_CONTRACT.planner_instruction_source == (
            "JudgmentVerdict.planner_instruction"
        )


class TestConflictResolverReadinessContract:
    def test_conflict_resolver_dispatch_readiness_is_pinned(self) -> None:
        assert CONFLICT_RESOLVER_READINESS_CONTRACT.trigger == (
            "reconcile.merge_conflict_resolver_dispatch"
        )
        assert CONFLICT_RESOLVER_READINESS_CONTRACT.payload_source == (
            "MergeResult.resolver_dispatch"
        )
        assert "merge outcome is non_trivial_conflict" in (
            CONFLICT_RESOLVER_READINESS_CONTRACT.dispatch_ready_when
        )
        assert "resolver_dispatch payload is present" in (
            CONFLICT_RESOLVER_READINESS_CONTRACT.dispatch_ready_when
        )
        assert CONFLICT_RESOLVER_READINESS_CONTRACT.runtime_owner == "loop.py"
        assert CONFLICT_RESOLVER_READINESS_CONTRACT.deferred_to == (
            "driver-judgment-expansion-replan"
        )
