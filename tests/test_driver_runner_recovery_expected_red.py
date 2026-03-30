"""Expected-RED tests for advanced post-bootstrap runner recovery.

Source:
- step ``driver-enhancement-runner-recovery.design-and-test``
- docs/DRIVER-CONTINUITY-FOUNDATION.md Section 4, Section 6, and Section 7
- docs/DRIVER-ARCHITECTURE.md Section 2.1 (RunnerStatus runtime facts)

These tests intentionally fail until implementation owner
``driver-enhancement-runner-recovery.impl`` wires runtime matrix logic.
"""

from __future__ import annotations

from src.vectl.driver.loop import RUNNER_RECOVERY_TAXONOMY, evaluate_runner_recovery_boundary
from src.vectl.driver.types import (
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    ReplaySafetyEnvelope,
    RunnerRecoveryBoundaryInput,
    RunnerRecoverySignal,
)


def _ledger_entry(*, step_id: str) -> ContinuityLedgerEntry:
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
        recorded_at="2026-03-31T00:00:00Z",
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
    taxonomy: str,
    heartbeat_age_seconds: float | None,
    watchdog_timeout_seconds: float,
    process_alive: bool,
    exit_code: int | None,
) -> RunnerRecoveryBoundaryInput:
    step_id = "core.impl"
    ledger_entry = _ledger_entry(step_id=step_id)
    handoff = ContinuityHandoff(
        step_id=step_id,
        resume_from_session_id=ledger_entry.last_session_id,
        replay_envelope=ledger_entry.replay_envelope,
        ledger_entry=ledger_entry,
        capability_snapshot_id="opencode|resume=1|persist=1|replay=0|fresh_on_mismatch=1",
        reason="resume_safe_from_ledger_and_capability",
    )
    return RunnerRecoveryBoundaryInput(
        signal=RunnerRecoverySignal(
            step_id=step_id,
            attempt_key=ledger_entry.latest_attempt_key,
            runner_name=ledger_entry.runner_name,
            taxonomy=taxonomy,
            heartbeat_age_seconds=heartbeat_age_seconds,
            watchdog_timeout_seconds=watchdog_timeout_seconds,
            process_alive=process_alive,
            exit_code=exit_code,
        ),
        continuity_handoff=handoff,
        ledger_entry=ledger_entry,
    )


def test_runner_recovery_taxonomy_is_pinned() -> None:
    """Post-bootstrap taxonomy must expose crash/fail/stall/no-progress branches."""

    assert RUNNER_RECOVERY_TAXONOMY == (
        "clean_fail",
        "crash",
        "stall",
        "no_progress",
    )


def test_runner_recovery_distinguishes_crash_from_clean_fail_expected_red() -> None:
    """Crash and clean-fail must not collapse into one restart branch."""

    clean_fail = evaluate_runner_recovery_boundary(
        boundary_input=_boundary_input(
            taxonomy="clean_fail",
            heartbeat_age_seconds=2.0,
            watchdog_timeout_seconds=60.0,
            process_alive=False,
            exit_code=1,
        )
    )
    crash = evaluate_runner_recovery_boundary(
        boundary_input=_boundary_input(
            taxonomy="crash",
            heartbeat_age_seconds=2.0,
            watchdog_timeout_seconds=60.0,
            process_alive=False,
            exit_code=137,
        )
    )

    clean_fail_decision = clean_fail.decisions[0]
    crash_decision = crash.decisions[0]

    assert clean_fail_decision.disposition == "restart"
    assert clean_fail_decision.reason == "clean_fail_restart_with_replay_guard"
    assert crash_decision.disposition == "halt"
    assert crash_decision.reason == "crash_requires_controller_review"


def test_runner_recovery_uses_heartbeat_gap_for_watchdog_decision_expected_red() -> None:
    """No-progress watchdog gaps must force restart when heartbeat is stale."""

    observed = evaluate_runner_recovery_boundary(
        boundary_input=_boundary_input(
            taxonomy="no_progress",
            heartbeat_age_seconds=180.0,
            watchdog_timeout_seconds=45.0,
            process_alive=True,
            exit_code=None,
        )
    )

    decision = observed.decisions[0]
    assert decision.disposition == "restart"
    assert decision.reason == "watchdog_heartbeat_gap_no_progress"


def test_runner_recovery_holds_when_heartbeat_is_fresh_expected_red() -> None:
    """Fresh heartbeat on stall must halt auto-restart as watchdog inconclusive."""

    observed = evaluate_runner_recovery_boundary(
        boundary_input=_boundary_input(
            taxonomy="stall",
            heartbeat_age_seconds=5.0,
            watchdog_timeout_seconds=45.0,
            process_alive=True,
            exit_code=None,
        )
    )

    decision = observed.decisions[0]
    assert decision.disposition == "halt"
    assert decision.reason == "stall_with_fresh_heartbeat_requires_manual_review"


def test_runner_recovery_matrix_preserves_restart_controller_ownership_expected_red() -> None:
    """Watchdog matrix must attribute decisions to continuity restart controller."""

    observed = evaluate_runner_recovery_boundary(
        boundary_input=_boundary_input(
            taxonomy="clean_fail",
            heartbeat_age_seconds=3.0,
            watchdog_timeout_seconds=45.0,
            process_alive=False,
            exit_code=1,
        )
    )

    decision = observed.decisions[0]
    assert decision.restart_controller_owner == "startup_recovery_controller"
    assert "restart:core.impl:clean_fail_restart_with_replay_guard" in observed.repair_actions
