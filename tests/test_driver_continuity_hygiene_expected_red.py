"""Expected-RED tests for continuity hygiene helper behavior.

Source authority:
- docs/DRIVER-CONTINUITY-FOUNDATION.md (hygiene helper classification)
- docs/DRIVER-ARCHITECTURE.md Section 2.11 (startup recovery)

These tests verify the hygiene helper classification and quarantine manifest
behavior that is not yet implemented. They must fail red until
`driver-continuity-hygiene-core.impl-hygiene-storage` lands.

Expected-red cases:
1. Ledger artifact absent from plan and repaired current-branch claims = safe stale
2. Journal-only artifact absent from plan and repaired claims = safe stale
3. Ledger step absent from plan but present in active claims = blocking
4. Corrupt ledger files = blocking
5. Ambiguous migration identity = blocking
6. Quarantine manifest records source_path, destination_path, reason, audit_timestamp
   while preserving original files via quarantine (not deletion)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.vectl.driver.types import (
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    ReplaySafetyEnvelope,
)
from tests.fixtures.helpers.continuity_hygiene import (
    ArtifactClassification,
    HygieneArtifact,
    HygieneClassificationResult,
    HygieneRecoveryScenario,
    QuarantineManifestEntry,
    QuarantineResult,
    make_ledger_entry,
    make_ledger_entry_success,
    make_quarantine_manifest_entry,
    make_safe_stale_ledger_scenario,
    make_blocking_orphan_ledger_scenario,
    make_corrupt_ledger_scenario,
    make_journal_only_safe_stale_scenario,
)


# =============================================================================
# HELPERS THAT DO NOT YET EXIST (will be implemented by downstream)
# =============================================================================

# These imports represent the functions that need to be implemented by
# driver-continuity-hygiene-core.impl-hygiene-storage. They currently do not
# exist, so these tests will fail red.


def classify_artifact(
    artifact: HygieneArtifact,
    plan_step_ids: set[str],
    claims_step_ids: set[str],
    corrupt_files: set[str] | None = None,
) -> HygieneClassificationResult:
    """Classify a continuity artifact as safe_stale or blocking.

    This function does not yet exist. It should be implemented to:
    1. Check if artifact's step_id is in plan
    2. Check if artifact's step_id is in active claims
    3. Check if artifact is corrupt
    4. Return appropriate classification

    Currently raises AttributeError (function does not exist).
    """
    raise AttributeError(
        "classify_artifact is not yet implemented. "
        "See driver-continuity-hygiene-core.impl-hygiene-storage"
    )


def quarantine_artifact(
    artifact: HygieneArtifact,
    reason: str,
    quarantine_dir: Path,
) -> QuarantineResult:
    """Quarantine an artifact by moving it to quarantine_dir, preserving original.

    This function does not yet exist. It should:
    1. Create destination path in quarantine_dir
    2. Copy (not move) the artifact to preserve original
    3. Record manifest entry with source_path, destination_path, reason, audit_timestamp
    4. Return QuarantineResult with manifest entry and original_preserved=True

    Currently raises AttributeError (function does not exist).
    """
    raise AttributeError(
        "quarantine_artifact is not yet implemented. "
        "See driver-continuity-hygiene-core.impl-hygiene-storage"
    )


def get_quarantine_manifest(quarantine_dir: Path) -> list[QuarantineManifestEntry]:
    """Load quarantine manifest entries from quarantine directory.

    This function does not yet exist.

    Currently raises AttributeError (function does not exist).
    """
    raise AttributeError(
        "get_quarantine_manifest is not yet implemented. "
        "See driver-continuity-hygiene-core.impl-hygiene-storage"
    )


def is_ambiguous_migration(step_id: str) -> bool:
    """Check if a step_id pattern suggests ambiguous migration identity.

    This function does not yet exist.

    Currently raises AttributeError (function does not exist).
    """
    raise AttributeError(
        "is_ambiguous_migration is not yet implemented. "
        "See driver-continuity-hygiene-core.impl-hygiene-storage"
    )


# =============================================================================
# ARTIFACT FACTORIES FOR MINIMAL EXTERNAL SHAPE
# =============================================================================


def _make_ledger_artifact(
    step_id: str,
    status: str = "success",
    runner_name: str = "opencode",
    session_id: str | None = "session-1",
) -> HygieneArtifact:
    """Create a minimal ledger artifact matching persisted artifact shape."""
    content = {
        "step_id": step_id,
        "latest_attempt_key": f"{step_id}:{runner_name}:{session_id}",
        "status": status,
        "runner_name": runner_name,
        "last_session_id": session_id,
        "recovery_cursor": "cursor-after-success" if status == "success" else None,
        "judge_policy": None,
        "replay_envelope": {
            "step_id": step_id,
            "attempt_key": f"{step_id}:{runner_name}:{session_id}",
            "runner_name": runner_name,
            "session_id": session_id,
            "idempotency_scope": "step",
            "tool_call_fingerprint": None,
        },
        "last_journal_event": {
            "step_id": step_id,
            "event_kind": "success" if status == "success" else "failed",
            "recorded_at": "2026-03-29T00:00:00+00:00",
            "attempt_key": f"{step_id}:{runner_name}:{session_id}",
            "runner_name": runner_name,
            "session_id": session_id,
            "summary": "completed",
            "replay_envelope": {
                "step_id": step_id,
                "attempt_key": f"{step_id}:{runner_name}:{session_id}",
                "runner_name": runner_name,
                "session_id": session_id,
                "idempotency_scope": "step",
                "tool_call_fingerprint": None,
            },
        },
    }
    return HygieneArtifact(
        artifact_type="ledger",
        step_id=step_id,
        source_path=Path(f".vectl/continuity/ledger/{step_id.replace('.', '_')}.json"),
        content=content,
    )


# =============================================================================
# TEST: LEDGER ARTIFACT ABSENT FROM PLAN = SAFE STALE
# =============================================================================


class TestLedgerArtifactAbsentFromPlanSafeStale:
    """Tests for ledger artifact absent from plan and repaired claims.

    A ledger artifact for a step not in the current plan, with no active
    claims, should be classified as SAFE_STALE (not blocking).
    """

    def test_orphan_ledger_absent_from_plan_is_safe_stale(self) -> None:
        """Orphaned ledger (not in plan) with repaired claims = SAFE_STALE.

        Source: Hygiene helper classification rules.
        This test MUST fail red before implementation.
        """
        scenario = make_safe_stale_ledger_scenario()

        # The abandoned step's ledger should be safe stale
        artifact = _make_ledger_artifact("abandoned.step")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification == ArtifactClassification.SAFE_STALE, (
            f"Expected SAFE_STALE for orphan ledger, got {result.classification}. "
            "The hygiene helper should classify orphaned ledger (not in plan, "
            "no active claims) as safe stale."
        )
        assert "safe_stale" in result.reason.lower()

    def test_safe_stale_ledger_not_in_blocking_repair_actions(self) -> None:
        """Safe stale classification should not generate blocking repair actions."""
        scenario = make_safe_stale_ledger_scenario()
        artifact = _make_ledger_artifact("abandoned.step")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        # Safe stale artifacts should not require blocking repair actions
        blocking_actions = [a for a in result.blocking_repair_actions if "halt" in a.lower()]
        assert len(blocking_actions) == 0, (
            f"Safe stale ledger should not generate blocking actions, got: {result.blocking_repair_actions}"
        )


# =============================================================================
# TEST: JOURNAL-ONLY ARTIFACT ABSENT FROM PLAN = SAFE STALE
# =============================================================================


class TestJournalOnlyArtifactAbsentFromPlanSafeStale:
    """Tests for journal-only artifact absent from plan and repaired claims.

    A journal-only artifact (no ledger) for a step not in the current plan,
    with no active claims, should be classified as SAFE_STALE.
    """

    def test_journal_only_absent_from_plan_is_safe_stale(self) -> None:
        """Journal-only artifact (no ledger) absent from plan = SAFE_STALE.

        Source: Hygiene helper classification rules.
        This test MUST fail red before implementation.
        """
        scenario = make_journal_only_safe_stale_scenario()

        # Create a journal-only artifact (no ledger)
        artifact = HygieneArtifact(
            artifact_type="journal",
            step_id="journal.only.step",
            source_path=Path(".vectl/continuity/journal/journal.only.step.jsonl"),
            content={
                "step_id": "journal.only.step",
                "event_kind": "completed",
                "recorded_at": "2026-03-29T00:00:00+00:00",
                "attempt_key": "journal.only.step:opencode:session-1",
                "runner_name": "opencode",
                "session_id": "session-1",
                "summary": "completed",
            },
        )

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification == ArtifactClassification.SAFE_STALE, (
            f"Expected SAFE_STALE for journal-only orphan, got {result.classification}. "
            "Journal-only artifacts for steps not in plan should be safe stale."
        )


# =============================================================================
# TEST: LEDGER STEP ABSENT FROM PLAN BUT IN ACTIVE CLAIMS = BLOCKING
# =============================================================================


class TestLedgerStepAbsentFromPlanButActiveClaimsBlocking:
    """Tests for blocking classification when ledger is orphaned but still claimed.

    A ledger step that is absent from plan but still has an active claim
    should remain BLOCKING (not safe stale).
    """

    def test_orphan_ledger_with_active_claims_is_blocking(self) -> None:
        """Orphaned ledger with active claims = BLOCKING.

        Source: Hygiene helper classification rules.
        This test MUST fail red before implementation.
        """
        scenario = make_blocking_orphan_ledger_scenario()

        artifact = _make_ledger_artifact("blocking.orphan")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification == ArtifactClassification.BLOCKING, (
            f"Expected BLOCKING for orphan ledger with active claims, got {result.classification}. "
            "A ledger step absent from plan but still actively claimed must remain blocking."
        )
        assert len(result.blocking_repair_actions) > 0, (
            "Blocking classification should generate repair actions"
        )

    def test_active_claim_orphan_not_safe_stale(self) -> None:
        """Orphan ledger with active claims must NOT be classified as safe stale."""
        scenario = make_blocking_orphan_ledger_scenario()
        artifact = _make_ledger_artifact("blocking.orphan")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification != ArtifactClassification.SAFE_STALE, (
            "Orphan ledger with active claims must NOT be safe stale"
        )


# =============================================================================
# TEST: CORRUPT LEDGER FILES = BLOCKING
# =============================================================================


class TestCorruptLedgerFilesBlocking:
    """Tests for corrupt ledger file handling.

    Corrupt ledger files must remain blocking and not be classified as safe stale.
    """

    def test_corrupt_ledger_file_is_blocking(self) -> None:
        """Corrupt ledger file = BLOCKING.

        Source: Hygiene helper classification rules.
        This test MUST fail red before implementation.
        """
        scenario = make_corrupt_ledger_scenario()

        artifact = _make_ledger_artifact("corrupt.step")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
            corrupt_files=scenario.corrupt_ledger_files,
        )

        assert result.classification == ArtifactClassification.BLOCKING, (
            f"Expected BLOCKING for corrupt ledger, got {result.classification}. "
            "Corrupt ledger files must remain blocking."
        )

    def test_corrupt_ledger_not_safe_stale(self) -> None:
        """Corrupt ledger must NOT be classified as safe stale."""
        scenario = make_corrupt_ledger_scenario()
        artifact = _make_ledger_artifact("corrupt.step")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
            corrupt_files=scenario.corrupt_ledger_files,
        )

        assert result.classification != ArtifactClassification.SAFE_STALE, (
            "Corrupt ledger must NOT be classified as safe stale"
        )


# =============================================================================
# TEST: AMBIGUOUS MIGRATION IDENTITY = BLOCKING
# =============================================================================


class TestAmbiguousMigrationIdentityBlocking:
    """Tests for ambiguous migration identity handling.

    Step IDs that suggest ambiguous migration identity should remain blocking.
    """

    def test_ambiguous_migration_step_id_is_blocking(self) -> None:
        """Ambiguous migration identity = BLOCKING.

        Source: Hygiene helper classification rules.
        This test MUST fail red before implementation.
        """
        # This step ID pattern suggests ambiguous migration
        ambiguous_step_id = "core.migrated.duplicate"

        assert is_ambiguous_migration(ambiguous_step_id) is True, (
            f"Step ID {ambiguous_step_id} should be detected as ambiguous migration"
        )

    def test_normal_step_id_not_ambiguous(self) -> None:
        """Normal step IDs should not be flagged as ambiguous migration."""
        normal_step_ids = [
            "core.impl",
            "core.test",
            "driver.feature.contract",
        ]

        for step_id in normal_step_ids:
            assert is_ambiguous_migration(step_id) is False, (
                f"Step ID {step_id} should NOT be flagged as ambiguous migration"
            )


# =============================================================================
# TEST: QUARANTINE MANIFEST BEHAVIOR
# =============================================================================


class TestQuarantineManifestBehavior:
    """Tests for quarantine manifest recording and file preservation.

    The quarantine operation must:
    1. Record source_path, destination_path, reason, audit_timestamp
    2. Preserve original files via quarantine (not deletion)
    3. Support manifest loading
    """

    def test_quarantine_manifest_records_required_fields(self, tmp_path: Path) -> None:
        """Quarantine manifest must record source, destination, reason, timestamp.

        Source: Hygiene quarantine specification.
        This test MUST fail red before implementation.
        """
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("safe.stale.step")

        result = quarantine_artifact(
            artifact=artifact,
            reason="safe_stale_orphan",
            quarantine_dir=quarantine_dir,
        )

        # Check manifest entry has all required fields
        entry = result.manifest_entry
        assert entry.source_path is not None, "source_path must be recorded"
        assert entry.destination_path is not None, "destination_path must be recorded"
        assert entry.reason == "safe_stale_orphan", "reason must be recorded"
        assert entry.audit_timestamp is not None, "audit_timestamp must be recorded"

    def test_quarantine_preserves_original_files(self, tmp_path: Path) -> None:
        """Quarantine must preserve original files, not delete them.

        Source: Hygiene quarantine specification.
        This test MUST fail red before implementation.
        """
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("safe.stale.step")

        result = quarantine_artifact(
            artifact=artifact,
            reason="safe_stale_orphan",
            quarantine_dir=quarantine_dir,
        )

        assert result.original_preserved is True, (
            "Quarantine must preserve original files, not delete them"
        )

        # Original file should still exist
        assert artifact.source_path.exists(), (
            f"Original file {artifact.source_path} should still exist after quarantine"
        )

    def test_quarantine_creates_copy_not_move(self, tmp_path: Path) -> None:
        """Quarantine should copy to destination, not move from source.

        Source: Hygiene quarantine specification.
        """
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("safe.stale.step")

        result = quarantine_artifact(
            artifact=artifact,
            reason="safe_stale_orphan",
            quarantine_dir=quarantine_dir,
        )

        # Destination file should exist (copy created)
        dest_path = Path(result.manifest_entry.destination_path)
        assert dest_path.exists(), f"Destination file {dest_path} should exist after quarantine"

        # Source file should also still exist (preserved, not moved)
        assert artifact.source_path.exists(), (
            "Source file should still exist (quarantine is copy, not move)"
        )

    def test_get_quarantine_manifest_loads_entries(self, tmp_path: Path) -> None:
        """Quarantine manifest loading must return recorded entries.

        Source: Hygiene quarantine specification.
        This test MUST fail red before implementation.
        """
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("safe.stale.step")

        # First quarantine an artifact
        quarantine_artifact(
            artifact=artifact,
            reason="safe_stale_orphan",
            quarantine_dir=quarantine_dir,
        )

        # Then load the manifest
        entries = get_quarantine_manifest(quarantine_dir)

        assert len(entries) >= 1, "Quarantine manifest should have at least one entry"

        entry = entries[0]
        assert entry.source_path is not None
        assert entry.destination_path is not None
        assert entry.reason is not None
        assert entry.audit_timestamp is not None


# =============================================================================
# TEST: QUARANTINE RESULT STRUCTURE
# =============================================================================


class TestQuarantineResultStructure:
    """Tests for quarantine result data structure."""

    def test_quarantine_result_has_manifest_entry(self, tmp_path: Path) -> None:
        """Quarantine result must contain a manifest entry."""
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("test.step")

        result = quarantine_artifact(
            artifact=artifact,
            reason="test",
            quarantine_dir=quarantine_dir,
        )

        assert hasattr(result, "manifest_entry"), (
            "Quarantine result must have manifest_entry attribute"
        )
        assert isinstance(result.manifest_entry, QuarantineManifestEntry), (
            "manifest_entry must be a QuarantineManifestEntry"
        )

    def test_quarantine_result_has_original_preserved_flag(self, tmp_path: Path) -> None:
        """Quarantine result must indicate if original was preserved."""
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("test.step")

        result = quarantine_artifact(
            artifact=artifact,
            reason="test",
            quarantine_dir=quarantine_dir,
        )

        assert hasattr(result, "original_preserved"), (
            "Quarantine result must have original_preserved attribute"
        )


# =============================================================================
# TEST: SAFE STALE CLASSIFICATION REQUIRES CLAIMS REPAIR
# =============================================================================


class TestSafeStaleRequiresClaimsRepair:
    """Tests that safe stale classification requires claims to be repaired."""

    def test_orphan_ledger_without_claims_repair_is_blocking(self) -> None:
        """Orphan ledger without repaired claims = BLOCKING.

        The claims repair step must complete before an orphaned ledger
        can be classified as safe stale.
        """
        # Scenario: orphan ledger but claims NOT repaired
        plan_step_ids = {"current.step"}
        ledger_step_ids = {"current.step", "orphan.step"}
        claims_step_ids = {"current.step", "orphan.step"}  # NOT repaired!

        artifact = _make_ledger_artifact("orphan.step")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=plan_step_ids,
            claims_step_ids=claims_step_ids,
        )

        assert result.classification == ArtifactClassification.BLOCKING, (
            "Orphan ledger with unrepaired claims must remain blocking"
        )


# =============================================================================
# INTEGRATION: FULL HYGIENE CLASSIFICATION FLOW
# =============================================================================


class TestFullHygieneClassificationFlow:
    """Integration tests for full hygiene classification flow."""

    def test_safe_stale_flow(self, tmp_path: Path) -> None:
        """Full flow: safe stale orphan -> classify -> quarantine -> manifest.

        This is the expected happy path for safe stale artifacts:
        1. Detect orphaned ledger (not in plan)
        2. Verify claims repaired (no active claims)
        3. Classify as safe stale
        4. Quarantine (preserve original)
        5. Verify manifest entry
        """
        scenario = make_safe_stale_ledger_scenario()
        quarantine_dir = tmp_path / ".vectl" / "continuity" / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        artifact = _make_ledger_artifact("abandoned.step")

        # Step 1: Classify
        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification == ArtifactClassification.SAFE_STALE

        # Step 2: Quarantine
        quarantine_result = quarantine_artifact(
            artifact=artifact,
            reason=result.reason,
            quarantine_dir=quarantine_dir,
        )

        assert quarantine_result.original_preserved is True

        # Step 3: Verify manifest
        entries = get_quarantine_manifest(quarantine_dir)
        assert len(entries) >= 1
        assert entries[0].source_path is not None
        assert entries[0].destination_path is not None
        assert entries[0].reason is not None
        assert entries[0].audit_timestamp is not None

    def test_blocking_flow(self, tmp_path: Path) -> None:
        """Full flow: blocking orphan -> classify -> NOT quarantined.

        Blocking artifacts should NOT be quarantined automatically.
        """
        scenario = make_blocking_orphan_ledger_scenario()

        artifact = _make_ledger_artifact("blocking.orphan")

        result = classify_artifact(
            artifact=artifact,
            plan_step_ids=scenario.plan_step_ids,
            claims_step_ids=scenario.claims_step_ids,
        )

        assert result.classification == ArtifactClassification.BLOCKING

        # Blocking artifacts should have blocking repair actions
        assert len(result.blocking_repair_actions) > 0
