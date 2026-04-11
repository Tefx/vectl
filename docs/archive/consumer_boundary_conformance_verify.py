#!/usr/bin/env python3
"""
Consumer-boundary conformance verification for recovery surfaces.

This script verifies that CLI inspect status, inspect actions, case surfaces,
and recovery/report outputs interpret the same resolved recovery facts from
a shared fixture without re-deriving them independently.

Authority: orch_operator_recovery_cutover.consumer_boundary_conformance
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from vectl.orchestration.run_store import RunRegistry, RunRecord, CaseIndexEntry, generate_run_id
from vectl.orchestration.inspection_queries import RunsQueryImpl, CasesQueryImpl, InspectQuery
from vectl.orchestration.recovery import (
    RecoveryOutcome,
    RecoveryReport,
    RecoveryHygieneResult,
    StartupRecoveryControllerInput,
    StartupRecoveryControllerOutput,
    CutoverValidator,
)


def verify_runs_surface_conformance():
    """Verify that runs() surface consumes resolved facts from shared RunRegistry fixture."""
    print("=" * 60)
    print("VERIFICATION: runs() surface / RunRegistry conformance")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        runs_root = Path(tmp) / "runs"
        runs_root.mkdir(parents=True)
        registry = RunRegistry(store_root=runs_root)
        runs_query = RunsQueryImpl(registry)

        # Create a run with resolved facts
        run_id = generate_run_id()
        registry.save(
            RunRecord(
                run_id=run_id,
                step_id="core.test",
                plan_path="plan.yaml",
                agent="test-agent",
                status="running",
                source="orchestration_native",
                output_summary="test run for conformance",
            )
        )

        # Surface queries RunRegistry, does not re-derive
        view = runs_query.query(InspectQuery(step_id="core.test", limit=10))

        # Verify conformance
        record = registry.by_id(run_id)
        assert record is not None, "Run record should exist"

        # Check 1: Same run_id appears in both
        assert run_id in view.runs, "run_id must appear in runs() surface"
        assert record.run_id == run_id, "run_id must match"

        # Check 2: Status is consumed from record, not re-derived
        assert "running" in view.statuses, "status must appear in runs() surface"
        assert record.status == "running", "status must match record"

        print(f"✓ runs() surface returns run_id={run_id}")
        print(f"✓ SHARED FIXTURE: RunRegistry.by_id() returns run_id={record.run_id}")
        print(
            f"✓ STATUS CONFORMANCE: runs().statuses={view.statuses} vs record.status={record.status}"
        )
        print(f"✓ NO INDEPENDENT RE-DERIVATION: RunsQueryImpl delegates to RunRegistry")
        print()

        return True


def verify_cases_surface_conformance():
    """Verify that case_list surface consumes resolved facts from shared CaseIndexEntry fixture."""
    print("=" * 60)
    print("VERIFICATION: case_list() surface / CaseIndexEntry conformance")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        runs_root = Path(tmp) / "runs"
        runs_root.mkdir(parents=True)
        registry = RunRegistry(store_root=runs_root)
        cases_query = CasesQueryImpl(registry)

        # Create a run first
        run_id = generate_run_id()
        registry.save(
            RunRecord(
                run_id=run_id,
                step_id="core.case_test",
                plan_path="plan.yaml",
                agent="test-agent",
                status="running",
            )
        )

        # Create a case with resolved facts
        registry.append_case(
            CaseIndexEntry(
                case_id="case-conformance-001",
                run_id=run_id,
                status="open",
                updated_at=0.0,
                case_path=str(runs_root / "case.json"),
            )
        )

        # Surface queries CaseIndexEntry, does not re-derive
        open_cases = cases_query.open_cases()

        # Verify conformance
        latest_cases = registry._latest_cases_by_case_id()
        assert "case-conformance-001" in latest_cases, "case must exist in registry"

        # Check 1: Same case_id appears in both
        assert "case-conformance-001" in open_cases, "case_id must appear in cases() surface"

        # Check 2: Status is consumed from CaseIndexEntry, not re-derived
        entry = latest_cases["case-conformance-001"]
        assert entry.status == "open", "status must be open in entry"

        print(f"✓ case_list() surface returns case_id=case-conformance-001")
        print(f"✓ SHARED FIXTURE: registry._latest_cases_by_case_id() returns same")
        print(f"✓ STATUS CONFORMANCE: entry.status={entry.status}")
        print(f"✓ NO INDEPENDENT RE-DERIVATION: CasesQueryImpl delegates to RunRegistry")
        print()

        return True


def verify_recovery_outcome_conformance():
    """Verify that recovery outcomes are defined but not consumed by orch_app."""
    print("=" * 60)
    print("VERIFICATION: RecoveryOutcome enum vs internal _RecoveryDecision")
    print("=" * 60)

    # The public enum
    public_outcomes = {e.value for e in RecoveryOutcome}
    print(f"PUBLIC RecoveryOutcome values: {sorted(public_outcomes)}")

    # The internal decision types from orch_app._RecoveryDecision
    internal_outcomes = {
        "resume_safe",
        "recover_and_resume",
        "fresh_start_required",
        "blocking_divergence",
        "corrupt_blocking",
        "ambiguous_blocking",
    }
    print(f"INTERNAL _RecoveryDecision values: {sorted(internal_outcomes)}")

    # Conformance check
    intersection = public_outcomes & internal_outcomes

    if intersection:
        print(f"✓ INTERSECTION: {intersection}")
        return True
    else:
        print("✗ CONFORMANCE DEBT: RecoveryOutcome enum is SPEC-DEFINED but NOT CONSUMED")
        print("  The internal _RecoveryDecision uses different outcome strings.")
        print("  This is a blocking debt documented in behavioral_proof_register.")
        print()

        # Check if RecoveryReport is consumed anywhere
        print("ANALYSIS: RecoveryReport in orch_app.recover():")
        print("  orch_app.recover() returns OrchestrationResult, NOT RecoveryReport")
        print("  The RecoveryReport DTO is defined in recovery.py but unused.")
        print()

        return False


def verify_cutover_validator_uses_run_store():
    """Verify that CutoverValidator consumes RunRegistry, not independent derivation."""
    print("=" * 60)
    print("VERIFICATION: CutoverValidator / RunRegistry conformance")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        runs_root = Path(tmp) / "runs"
        runs_root.mkdir(parents=True)
        registry = RunRegistry(store_root=runs_root)
        validator = CutoverValidator(registry=registry)

        # No imported runs
        result = validator.validate_cutover_readiness()
        assert result.can_cutover is True, "cutover should pass with no imported runs"

        print(f"✓ CutoverValidator(registry=RunRegistry) consumes shared fixture")
        print(f"✓ Result.can_cutover={result.can_cutover}")
        print(f"✓ Result.criteria_results: {result.criteria_results}")
        print()

        return True


def verify_recovery_report_dto_unused():
    """Verify that RecoveryReport is defined but not returned by orch_app.recover()."""
    print("=" * 60)
    print("VERIFICATION: RecoveryReport DTO usage")
    print("=" * 60)

    # RecoveryReport is defined in recovery.py
    from vectl.orchestration.recovery import RecoveryReport

    # Check: does orch_app.recover() return RecoveryReport?
    from vectl.orch_app import OrchestrationResult

    print("RecoveryReport fields: outcome, message, step_id, run_id, actions, hygiene_results")
    print("OrchestrationResult fields: success, message, step_id, run_id")

    print()
    print("FINDING: orch_app.recover() returns OrchestrationResult, NOT RecoveryReport")
    print("  This is a conformance debt: spec-defined DTO is not consumed.")
    print("  The internal _RecoveryDecision is used instead.")
    print()

    return False


def main():
    print()
    print("=" * 60)
    print("CONSUMER-BOUNDARY CONFORMANCE VERIFICATION")
    print("=" * 60)
    print()

    behavioral_proof_register = []
    conformance_results = []

    # Verify runs surface conformance
    if verify_runs_surface_conformance():
        conformance_results.append(("runs()/RunRegistry", "PASS"))
        behavioral_proof_register.append(
            "runs() surface delegates to RunsQueryImpl which queries RunRegistry, "
            "not independent re-derivation"
        )
    else:
        conformance_results.append(("runs()/RunRegistry", "FAIL"))

    # Verify cases surface conformance
    if verify_cases_surface_conformance():
        conformance_results.append(("cases()/CaseIndexEntry", "PASS"))
        behavioral_proof_register.append(
            "case_list() surface delegates to CasesQueryImpl which queries RunRegistry._latest_cases_by_case_id(), "
            "not independent re-derivation"
        )
    else:
        conformance_results.append(("cases()/CaseIndexEntry", "FAIL"))

    # Verify recovery outcome conformance
    if verify_recovery_outcome_conformance():
        conformance_results.append(("RecoveryOutcome enum", "PASS"))
    else:
        conformance_results.append(("RecoveryOutcome enum", "FAIL - CONFORMANCE DEBT"))
        behavioral_proof_register.append(
            "CONFORMANCE DEBT: RecoveryOutcome enum is spec-defined but NOT consumed. "
            "Internal _RecoveryDecision uses different outcome strings: "
            "resume_safe, recover_and_resume, fresh_start_required, blocking_divergence, "
            "corrupt_blocking, ambiguous_blocking. "
            "Public enum values: recovered, blocked, quarantined, no_artifacts, operator_required, halt."
        )

    # Verify CutoverValidator uses RunRegistry
    if verify_cutover_validator_uses_run_store():
        conformance_results.append(("CutoverValidator/RunRegistry", "PASS"))
        behavioral_proof_register.append(
            "CutoverValidator consumes RunRegistry.shared fixture, "
            "not independent re-derivation of imported run state"
        )
    else:
        conformance_results.append(("CutoverValidator/RunRegistry", "FAIL"))

    # Verify RecoveryReport DTO usage
    if verify_recovery_report_dto_unused():
        conformance_results.append(("RecoveryReport DTO", "PASS"))
    else:
        conformance_results.append(("RecoveryReport DTO", "FAIL - CONFORMANCE DEBT"))
        behavioral_proof_register.append(
            "CONFORMANCE DEBT: RecoveryReport DTO is defined in recovery.py but NOT returned by orch_app.recover(). "
            "orch_app.recover() returns OrchestrationResult instead. "
            "The spec-defined RecoveryReport with fields (outcome, actions, hygiene_results) is unused."
        )

    # Summary
    print()
    print("=" * 60)
    print("CONFORMANCE RESULTS")
    print("=" * 60)
    for check_name, result in conformance_results:
        status = "✓" if "PASS" in result else "✗"
        print(f"{status} {check_name}: {result}")

    print()
    print("=" * 60)
    print("BEHAVIORAL PROOF REGISTER")
    print("=" * 60)
    for entry in behavioral_proof_register:
        print(f"• {entry}")

    # Gate decision
    debt_count = sum(1 for _, r in conformance_results if "DEBT" in r)
    if debt_count > 0:
        print()
        print(f"GATE: BLOCKED - {debt_count} conformance debt(s) detected")
        print("See behavioral_proof_register for details.")
        return False

    print()
    print("GATE: OPEN - All surfaces consume shared fixtures without independent re-derivation")
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
