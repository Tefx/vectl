"""Expected-RED tests for startup restart-recovery boundary.

Source:
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (startup recovery ordering)
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 3, Section 4, and Section 7
  (ledger/plan/claims reconciliation, restart matrix, judge-policy input)

These tests intentionally require behavior deferred to
``driver-continuity-restart-recovery.impl``.
"""

from __future__ import annotations

from src.vectl.driver.loop import (
    STARTUP_RECOVERY_SCAN_ORDER,
    evaluate_startup_recovery_boundary,
)
from src.vectl.driver.types import (
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    JudgeContinuityPolicyOutput,
    ReplaySafetyEnvelope,
    StartupRecoveryBoundaryInput,
    StartupRecoveryDecision,
    StartupRecoveryJudgeInput,
    StartupRecoveryReconciliationFacts,
)


def _ledger_entry(step_id: str) -> ContinuityLedgerEntry:
    envelope = ReplaySafetyEnvelope(
        step_id=step_id,
        attempt_key=f"{step_id}:opencode:session-1",
        runner_name="opencode",
        session_id="session-1",
        idempotency_scope="step-attempt",
    )
    journal = ContinuityJournalEntry(
        step_id=step_id,
        event_kind="failure",
        recorded_at="2026-03-29T00:00:00Z",
        attempt_key=envelope.attempt_key,
        runner_name="opencode",
        session_id="session-1",
        summary="runner failed",
        replay_envelope=envelope,
    )
    return ContinuityLedgerEntry(
        step_id=step_id,
        latest_attempt_key=envelope.attempt_key,
        status="failed",
        runner_name="opencode",
        last_session_id="session-1",
        replay_envelope=envelope,
        last_journal_event=journal,
    )


def _boundary_input(
    *,
    plan_step_ids: tuple[str, ...],
    claim_step_ids: tuple[str, ...],
    ledger_step_ids: tuple[str, ...],
    capability_snapshot_ids: tuple[str, ...] = ("opencode|resume=1|persist=1",),
    judge_inputs: tuple[StartupRecoveryJudgeInput, ...] = (),
) -> StartupRecoveryBoundaryInput:
    ledger_entries = tuple(_ledger_entry(step_id) for step_id in ledger_step_ids)
    return StartupRecoveryBoundaryInput(
        reconciliation=StartupRecoveryReconciliationFacts(
            plan_step_ids=plan_step_ids,
            claim_step_ids=claim_step_ids,
            ledger_step_ids=ledger_step_ids,
        ),
        capability_snapshot_ids=capability_snapshot_ids,
        ledger_entries=ledger_entries,
        judge_failure_inputs=judge_inputs,
    )


def test_startup_recovery_scan_order_is_pinned() -> None:
    """Startup scan order contract must remain explicit and stable."""

    assert STARTUP_RECOVERY_SCAN_ORDER == (
        "load_plan",
        "repair_claims",
        "cleanup_orphan_worktrees",
        "scan_continuity_ledger",
        "reconcile_plan_claims_ledger",
        "consume_judge_failure_policy_inputs",
        "classify_resume_restart_halt",
    )


def test_startup_recovery_halts_on_plan_claims_ledger_divergence_expected_red() -> None:
    """Divergent ledger/plan/claims must classify to explicit HALT."""

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl", "orphan.step"),
        )
    )

    assert (
        StartupRecoveryDecision(
            step_id="orphan.step",
            disposition="halt",
            reason="ledger_plan_claims_divergence",
        )
        in boundary_output.decisions
    )


def test_startup_recovery_consumes_judge_failure_halt_input_expected_red() -> None:
    """Judge failure policy HALT outcome must feed startup HALT matrix path."""

    judge_input = StartupRecoveryJudgeInput(
        step_id="core.impl",
        attempt_key="core.impl:opencode:session-1",
        policy=JudgeContinuityPolicyOutput(
            step_id="core.impl",
            judgment_type="FAILURE",
            judge_outcome="timeout",
            action="halt",
            provenance="judge_transport_timeout",
            continuity_recovery_reason="judge_timeout_no_fallback_runner",
            attempt_key="core.impl:opencode:session-1",
            halt_reason="judge instructed halt",
        ),
    )

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl",),
            judge_inputs=(judge_input,),
        )
    )

    assert (
        StartupRecoveryDecision(
            step_id="core.impl",
            disposition="halt",
            reason="judge_policy_halt",
            source_attempt_key="core.impl:opencode:session-1",
        )
        in boundary_output.decisions
    )


def test_startup_recovery_selects_restart_when_capability_snapshot_missing_expected_red() -> None:
    """Missing capability snapshot must force restart, not resume."""

    boundary_output = evaluate_startup_recovery_boundary(
        boundary_input=_boundary_input(
            plan_step_ids=("core.impl",),
            claim_step_ids=("core.impl",),
            ledger_step_ids=("core.impl",),
            capability_snapshot_ids=(),
        )
    )

    assert any(
        decision.step_id == "core.impl"
        and decision.disposition == "restart"
        and decision.reason == "capability_snapshot_missing"
        for decision in boundary_output.decisions
    )

    assert all(
        isinstance(handoff, ContinuityHandoff) for handoff in boundary_output.resumable_handoffs
    )
