"""Contract tests for continuity-foundation repair artifacts.

These tests pin only reviewable contract surfaces and exact artifact paths.
They intentionally avoid runtime continuity implementation behavior.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path

from src.vectl.driver import runner_continuity
from src.vectl.driver.session import SESSION_CONTINUITY_AUTHORITIES
from src.vectl.driver.types import (
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    ContinuityResultFields,
    ReplaySafetyEnvelope,
    ReplayTokenSemantics,
    RunnerResult,
    StartupRecoveryController,
    StartupRecoveryControllerInput,
    StartupRecoveryControllerOutput,
)


def test_continuity_foundation_doc_exists() -> None:
    assert Path("docs/DRIVER-CONTINUITY-FOUNDATION.md").exists()


def test_session_continuity_authorities_are_explicit() -> None:
    assert SESSION_CONTINUITY_AUTHORITIES == {
        "dispatch_reuse_intent": "DecideState in vectl.decide via Action.session/task_id",
        "runner_compatible_session_cache": "SessionPool in src/vectl/driver/session.py",
        "durable_resume_state": (
            "ContinuityLedgerEntry contract in src/vectl/driver/types.py; runtime persistence "
            "deferred to driver-continuity-authority-ledger"
        ),
        "replay_safety_envelope": "ReplaySafetyEnvelope contract in src/vectl/driver/types.py",
        "startup_recovery_decision_input": (
            "StartupRecoveryControllerInput/Output contract in src/vectl/driver/types.py"
        ),
        "startup_recovery_boundary_matrix": (
            "evaluate_startup_recovery_boundary + StartupRecoveryBoundaryInput/Output in "
            "src/vectl/driver/loop.py and src/vectl/driver/types.py"
        ),
    }


def test_continuity_types_exist_with_pinned_fields() -> None:
    assert is_dataclass(ReplaySafetyEnvelope)
    assert [f.name for f in fields(ReplaySafetyEnvelope)] == [
        "step_id",
        "attempt_key",
        "runner_name",
        "session_id",
        "idempotency_scope",
        "tool_call_fingerprint",
    ]

    assert [f.name for f in fields(ReplayTokenSemantics)] == [
        "token",
        "token_kind",
        "semantics_version",
        "bound_runner_name",
        "bound_step_id",
        "capability_snapshot_id",
    ]

    assert [f.name for f in fields(ContinuityResultFields)] == [
        "attempt_key",
        "replay_token",
        "replay_safe",
        "duplicate_replay",
        "recovery_reason",
    ]

    assert [f.name for f in fields(ContinuityJournalEntry)] == [
        "step_id",
        "event_kind",
        "recorded_at",
        "attempt_key",
        "runner_name",
        "session_id",
        "summary",
        "replay_envelope",
    ]

    assert [f.name for f in fields(ContinuityLedgerEntry)] == [
        "step_id",
        "latest_attempt_key",
        "status",
        "runner_name",
        "last_session_id",
        "replay_envelope",
        "last_journal_event",
        "recovery_cursor",
    ]

    assert [f.name for f in fields(ContinuityHandoff)] == [
        "step_id",
        "resume_from_session_id",
        "replay_envelope",
        "ledger_entry",
        "capability_snapshot_id",
        "reason",
    ]

    assert [f.name for f in fields(RunnerResult)] == [
        "status",
        "session_id",
        "output",
        "elapsed_seconds",
        "exit_code",
        "cost_usd",
        "tokens",
        "continuity",
    ]


def test_startup_recovery_controller_contract_is_present() -> None:
    assert is_dataclass(StartupRecoveryControllerInput)
    assert is_dataclass(StartupRecoveryControllerOutput)
    assert hasattr(StartupRecoveryController, "plan_recovery")


def test_runner_continuity_capabilities_are_repo_present_and_explicit() -> None:
    capabilities = {
        capability.runner_name: capability
        for capability in runner_continuity.RUNNER_CONTINUITY_CAPABILITIES
    }

    assert set(capabilities) == {"claude", "opencode", "codex", "gemini"}
    assert capabilities["claude"].resume_supported is True
    assert capabilities["opencode"].persistent_session_identity is True
    assert capabilities["codex"].replay_safe_after_partial_output is False
    assert capabilities["gemini"].resume_supported is False
    assert all(
        capability.minimum_recovery_telemetry
        == runner_continuity.DEFAULT_MINIMUM_RECOVERY_TELEMETRY
        for capability in capabilities.values()
    )
