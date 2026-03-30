"""Test fixtures and helpers for continuity hygiene expected-red tests.

This module provides test utilities for the continuity hygiene helper classification
and quarantine manifest behavior tests.

Downstream Owner: driver-continuity-hygiene-core.impl-hygiene-storage
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.vectl.driver.types import (
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    ReplaySafetyEnvelope,
)


# =============================================================================
# REPLAY SAFETY ENVELOPE FIXTURES (MINIMAL EXTERNAL SHAPE)
# =============================================================================


def make_replay_envelope(
    step_id: str = "test.step",
    attempt_key: str = "attempt-1",
    runner_name: str = "claude",
    session_id: str = "ses_123",
    idempotency_scope: str = "test",
    tool_call_fingerprint: str | None = None,
) -> ReplaySafetyEnvelope:
    """Factory for ReplaySafetyEnvelope with sensible defaults.

    This matches the minimal external shape persisted in continuity artifacts.
    """
    return ReplaySafetyEnvelope(
        step_id=step_id,
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        idempotency_scope=idempotency_scope,
        tool_call_fingerprint=tool_call_fingerprint,
    )


# =============================================================================
# CONTINUITY JOURNAL ENTRY FIXTURES (MINIMAL EXTERNAL SHAPE)
# =============================================================================


def make_journal_entry(
    step_id: str = "test.step",
    event_kind: str = "dispatch",
    attempt_key: str = "attempt-1",
    runner_name: str = "claude",
    session_id: str = "ses_123",
    summary: str = "Test event",
    recorded_at: str | None = None,
) -> ContinuityJournalEntry:
    """Factory for ContinuityJournalEntry with minimal external shape.

    The recorded_at field is stored as an ISO8601 string in persisted artifacts,
    not as a float from time.monotonic().
    """
    return ContinuityJournalEntry(
        step_id=step_id,
        event_kind=event_kind,
        recorded_at=recorded_at or "2026-03-29T00:00:00+00:00",
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        summary=summary,
        replay_envelope=make_replay_envelope(
            step_id=step_id,
            attempt_key=attempt_key,
            runner_name=runner_name,
            session_id=session_id,
        ),
    )


# =============================================================================
# CONTINUITY LEDGER ENTRY FIXTURES (MINIMAL EXTERNAL SHAPE)
# =============================================================================


def make_ledger_entry(
    step_id: str = "test.step",
    status: str = "pending",
    attempt_key: str = "attempt-1",
    runner_name: str = "claude",
    session_id: str = "ses_123",
    recovery_cursor: str | None = None,
    event_kind: str = "created",
    summary: str = "Step created",
    judge_policy: Any = None,
) -> ContinuityLedgerEntry:
    """Factory for ContinuityLedgerEntry with minimal external shape.

    This matches the exact shape persisted in .vectl/continuity/ledger/*.json files.
    """
    return ContinuityLedgerEntry(
        step_id=step_id,
        latest_attempt_key=attempt_key,
        status=status,
        runner_name=runner_name,
        last_session_id=session_id,
        replay_envelope=make_replay_envelope(
            step_id=step_id,
            attempt_key=attempt_key,
            runner_name=runner_name,
            session_id=session_id,
        ),
        last_journal_event=make_journal_entry(
            step_id=step_id,
            event_kind=event_kind,
            attempt_key=attempt_key,
            runner_name=runner_name,
            session_id=session_id,
            summary=summary,
        ),
        recovery_cursor=recovery_cursor,
        judge_policy=judge_policy,
    )


def make_ledger_entry_success(
    step_id: str = "test.step",
    attempt_key: str = "attempt-1",
    runner_name: str = "claude",
    session_id: str = "ses_123",
    recovery_cursor: str = "cursor-after-success",
) -> ContinuityLedgerEntry:
    """Factory for a successful ContinuityLedgerEntry."""
    return make_ledger_entry(
        step_id=step_id,
        status="success",
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        recovery_cursor=recovery_cursor,
        event_kind="success",
        summary="Step completed successfully",
    )


def make_ledger_entry_failure(
    step_id: str = "test.step",
    attempt_key: str = "attempt-1",
    runner_name: str = "claude",
    session_id: str = "ses_123",
    error_summary: str = "Step failed with error",
) -> ContinuityLedgerEntry:
    """Factory for a failed ContinuityLedgerEntry."""
    return make_ledger_entry(
        step_id=step_id,
        status="failed",
        attempt_key=attempt_key,
        runner_name=runner_name,
        session_id=session_id,
        recovery_cursor=None,
        event_kind="failed",
        summary=error_summary,
    )


# =============================================================================
# ARTIFACT CLASSIFICATION FIXTURES
# =============================================================================


class ArtifactClassification:
    """Classification result for a continuity artifact.

    This represents how the hygiene helper classifies a ledger or journal artifact
    during startup recovery analysis.
    """

    SAFE_STALE = "safe_stale"
    BLOCKING = "blocking"
    AMBIGUOUS = "ambiguous"


@dataclass
class HygieneArtifact:
    """A continuity artifact (ledger or journal) for hygiene classification."""

    artifact_type: str  # "ledger" or "journal"
    step_id: str
    source_path: Path
    content: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HygieneClassificationResult:
    """Result of classifying a hygiene artifact."""

    artifact: HygieneArtifact
    classification: str
    reason: str
    blocking_repair_actions: tuple[str, ...] = ()


@dataclass
class QuarantineManifestEntry:
    """Entry in the quarantine manifest.

    Records the quarantine action taken on an artifact.
    """

    source_path: str
    destination_path: str
    reason: str
    audit_timestamp: str
    artifact_type: str
    step_id: str


@dataclass
class QuarantineResult:
    """Result of a quarantine operation."""

    manifest_entry: QuarantineManifestEntry
    original_preserved: bool  # True if original was preserved, not deleted


# =============================================================================
# RECOVERY SCENARIO FIXTURES FOR HYGIENE
# =============================================================================


class HygieneRecoveryScenario:
    """Fixture for modeling hygiene-related recovery scenarios.

    Usage:
        scenario = HygieneRecoveryScenario.lost_step_ledger_safe_stale()
        assert scenario.ledger_step_ids == {"lost.step"}
        assert scenario.plan_step_ids == set()
        assert scenario.is_safe_stale_ledger is True
    """

    def __init__(
        self,
        plan_step_ids: set[str],
        ledger_step_ids: set[str],
        claims_step_ids: set[str],
        corrupt_ledger_files: set[str] | None = None,
    ):
        self.plan_step_ids = plan_step_ids
        self.ledger_step_ids = ledger_step_ids
        self.claims_step_ids = claims_step_ids
        self.corrupt_ledger_files = corrupt_ledger_files or set()

    @property
    def orphaned_ledger_steps(self) -> set[str]:
        """Steps in ledger but not in plan."""
        return self.ledger_step_ids - self.plan_step_ids

    @property
    def stale_claims(self) -> set[str]:
        """Claims in claims.json but not in current plan."""
        return self.claims_step_ids - self.plan_step_ids

    @property
    def is_safe_stale_ledger(self) -> bool:
        """Ledger artifact absent from plan and repaired claims is safe stale.

        An orphaned ledger step is safe stale if:
        1. The step is not in the current plan
        2. The step's claims have been repaired (no active claims)
        3. The artifact is not corrupt
        """
        if not self.orphaned_ledger_steps:
            return False
        if self.stale_claims:
            return False  # Claims not fully repaired
        if self.corrupt_ledger_files:
            return False  # Corrupt files are blocking
        return True

    @property
    def is_blocking_orphan(self) -> bool:
        """Ledger step absent from plan but present in active claims is blocking."""
        orphan_in_active_claims = self.orphaned_ledger_steps & self.claims_step_ids
        return len(orphan_in_active_claims) > 0


def make_safe_stale_ledger_scenario() -> HygieneRecoveryScenario:
    """Scenario: Ledger artifact absent from plan and repaired current-branch claims.

    This represents a ledger for a step that:
    - Is not in the current plan
    - Has no active claims (claims were repaired)
    - Is not corrupt

    This should be classified as SAFE_STALE by the hygiene helper.
    """
    return HygieneRecoveryScenario(
        plan_step_ids={"current.step"},  # Only current step in plan
        ledger_step_ids={"current.step", "abandoned.step"},  # Abandoned step has ledger but no plan
        claims_step_ids={"current.step"},  # Abandoned step's claims were repaired
    )


def make_blocking_orphan_ledger_scenario() -> HygieneRecoveryScenario:
    """Scenario: Ledger step absent from plan but present in active claims.

    This represents a ledger for a step that:
    - Is not in the current plan
    - Still has an active claim

    This should remain BLOCKING and NOT be quarantined as safe stale.
    """
    return HygieneRecoveryScenario(
        plan_step_ids={"current.step"},
        ledger_step_ids={"current.step", "blocking.orphan"},
        claims_step_ids={"current.step", "blocking.orphan"},  # Still actively claimed!
    )


def make_corrupt_ledger_scenario() -> HygieneRecoveryScenario:
    """Scenario: Corrupt ledger files that must remain blocking.

    Corrupt ledger files should never be classified as safe stale
    and must remain blocking until manually resolved.
    """
    return HygieneRecoveryScenario(
        plan_step_ids={"current.step"},
        ledger_step_ids={"current.step", "corrupt.step"},
        claims_step_ids={"current.step"},
        corrupt_ledger_files={"corrupt.step"},
    )


def make_journal_only_safe_stale_scenario() -> HygieneRecoveryScenario:
    """Scenario: Journal-only artifact absent from plan and repaired claims.

    A journal-only artifact (no corresponding ledger) for an abandoned
    step should be classified as safe stale after claims repair.
    """
    return HygieneRecoveryScenario(
        plan_step_ids={"current.step"},
        ledger_step_ids={"current.step"},  # No ledger for abandoned.step
        claims_step_ids={"current.step"},  # Claims repaired
    )


# =============================================================================
# QUARANTINE MANIFEST FIXTURES
# =============================================================================


def make_quarantine_manifest_entry(
    source_path: str = ".vectl/continuity/ledger/abandoned.step.json",
    destination_path: str = ".vectl/continuity/quarantine/abandoned.step.json",
    reason: str = "safe_stale_orphan",
    audit_timestamp: str | None = None,
    artifact_type: str = "ledger",
    step_id: str = "abandoned.step",
) -> QuarantineManifestEntry:
    """Factory for QuarantineManifestEntry with required fields.

    Required fields per specification:
    - source_path: Original location of the artifact
    - destination_path: Quarantine destination
    - reason: Classification reason
    - audit_timestamp: When the quarantine occurred
    """
    return QuarantineManifestEntry(
        source_path=source_path,
        destination_path=destination_path,
        reason=reason,
        audit_timestamp=audit_timestamp or "2026-03-29T00:00:00+00:00",
        artifact_type=artifact_type,
        step_id=step_id,
    )


# =============================================================================
# EXPECTED GAP SUMMARY
# =============================================================================


HYGIENE_EXPECTED_GAPS = {
    "helper_classification": [
        "No helper function to classify artifact as safe_stale vs blocking",
        "No classification logic for orphaned ledger without active claims",
        "No distinction between ledger and journal-only artifacts",
        "No handling of ambiguous migration identity cases",
    ],
    "quarantine_manifest": [
        "No quarantine_manifest data structure defined",
        "No quarantine() function that preserves original files",
        "No API to record source_path, destination_path, reason, audit_timestamp",
        "No manifest persistence to .vectl/continuity/quarantine/manifest.jsonl",
    ],
    "corrupt_file_handling": [
        "No corrupt file detection in hygiene helper",
        "Corrupt files are blocking but not explicitly handled",
    ],
    "ambiguous_migration": [
        "No handling for ambiguous migration identity",
        "No classification logic for step ID patterns suggesting migration",
    ],
}


def get_hygiene_expected_gaps_summary() -> str:
    """Get summary of all expected-RED gaps for downstream owner."""
    return """
# Expected-RED Gaps for driver-continuity-hygiene-core.impl-hygiene-storage

## Helper Classification
{gaps_classification}

## Quarantine Manifest
{gaps_quarantine}

## Corrupt File Handling
{gaps_corrupt}

## Ambiguous Migration
{gaps_migration}

## Test Files
- test_driver_continuity_hygiene_expected_red.py: Hygiene helper classification tests
- tests/fixtures/helpers/continuity_hygiene.py: Hygiene fixtures and helpers

All tests are expected-RED before implementation.
""".format(
        gaps_classification="\n".join(
            f"- {gap}" for gap in HYGIENE_EXPECTED_GAPS["helper_classification"]
        ),
        gaps_quarantine="\n".join(
            f"- {gap}" for gap in HYGIENE_EXPECTED_GAPS["quarantine_manifest"]
        ),
        gaps_corrupt="\n".join(
            f"- {gap}" for gap in HYGIENE_EXPECTED_GAPS["corrupt_file_handling"]
        ),
        gaps_migration="\n".join(
            f"- {gap}" for gap in HYGIENE_EXPECTED_GAPS["ambiguous_migration"]
        ),
    )
