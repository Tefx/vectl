"""Continuity artifact reader/writer and quarantine protocols."""

from __future__ import annotations

from typing import Protocol

from vectl.orchestration.continuity_artifacts import (
    ArtifactClassification,
    ContinuityHandoff,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    QuarantineManifestEntry,
    ReplaySafetyEnvelope,
)

class ArtifactReader(Protocol):
    """
    Protocol for reading continuity artifacts.

    Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md

    GAP: Concrete persistence implementation is deferred.
    """

    def read_ledger(self, step_id: str) -> ContinuityLedgerEntry | None:
        """
        Read the ledger entry for a step.

        Args:
            step_id: The step ID to read.

        Returns:
            ContinuityLedgerEntry if found, else None.

        Raises:
            NotImplementedError: Until reader persistence is specified.
        """
        ...

    def read_journal(
        self,
        step_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[ContinuityJournalEntry, ...]:
        """
        Read journal entries, optionally filtered.

        Args:
            step_id: Optional step ID filter.
            limit: Maximum entries to return.

        Returns:
            Tuple of matching journal entries.

        Raises:
            NotImplementedError: Until reader persistence is specified.
        """
        ...

    def read_handoff(
        self,
        from_session: str | None = None,
    ) -> ContinuityHandoff | None:
        """
        Read a handoff record.

        Args:
            from_session: Session ID that produced the handoff.

        Returns:
            ContinuityHandoff if found, else None.

        Raises:
            NotImplementedError: Until reader persistence is specified.
        """
        ...

    def read_replay_envelope(self, step_id: str) -> ReplaySafetyEnvelope | None:
        """
        Read the replay safety envelope for a step.

        Args:
            step_id: The step ID to read.

        Returns:
            ReplaySafetyEnvelope if found, else None.

        Raises:
            NotImplementedError: Until reader persistence is specified.
        """
        ...


class ArtifactWriter(Protocol):
    """
    Protocol for writing continuity artifacts.

    Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md

    GAP: Concrete persistence implementation is deferred.
    """

    def write_ledger(self, entry: ContinuityLedgerEntry) -> None:
        """
        Write a ledger entry.

        Args:
            entry: The ledger entry to persist.

        Raises:
            NotImplementedError: Until writer persistence is specified.
        """
        ...

    def write_journal(self, entry: ContinuityJournalEntry) -> None:
        """
        Write a journal entry.

        Args:
            entry: The journal entry to persist.

        Raises:
            NotImplementedError: Until writer persistence is specified.
        """
        ...

    def write_handoff(self, handoff: ContinuityHandoff) -> None:
        """
        Write a handoff record.

        Args:
            handoff: The handoff to persist.

        Raises:
            NotImplementedError: Until writer persistence is specified.
        """
        ...

    def write_replay_envelope(self, envelope: ReplaySafetyEnvelope) -> None:
        """
        Write a replay safety envelope.

        Args:
            envelope: The envelope to persist.

        Raises:
            NotImplementedError: Until writer persistence is specified.
        """
        ...


# ---------------------------------------------------------------------
# Quarantine Manager Protocol
# ---------------------------------------------------------------------
# Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md


class QuarantineManager:
    """
    Run-local quarantine and startup hygiene manager.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md

    GAP: Concrete quarantine implementation is deferred to recovery_cutover
    implementation phases. This contract pins the classification surface,
    quarantine structure, and audit requirements only.

    Guardrails (non-negotiable, per section 7a):
        1. No silent deletion — artifacts are copied to quarantine, not deleted
        2. No auto-remap of renamed step IDs — blocks rather than infers
        3. Corrupt files remain blocking — halt until operator intervention
        4. Current-claim disagreement remains blocking
        5. Ambiguous identity remains blocking
    """

    # @invar:allow dead_param: Quarantine contract preserves public parameter names for external implementations.
    def classify_artifact(
        self,
        artifact_path: str,
        step_id: str | None = None,
    ) -> ArtifactClassification:
        """
        Classify an artifact at startup hygiene time.

        Args:
            artifact_path: Path to the artifact to classify.
            step_id: Optional step ID extracted from the artifact.

        Returns:
            ArtifactClassification for this artifact.

        Raises:
            NotImplementedError: Until hygiene classification is specified.
        """
        raise NotImplementedError(
            "QuarantineManager.classify_artifact: hygiene semantics not yet specified"
        )

    # @invar:allow dead_param: Quarantine contract preserves public parameter names for external implementations.
    def quarantine(
        self,
        artifact_path: str,
        classification: ArtifactClassification,
        reason: str,
    ) -> QuarantineManifestEntry:
        """
        Quarantine an artifact (copy to quarantine, preserve original).

        Args:
            artifact_path: Path to the artifact to quarantine.
            classification: Classification of the artifact.
            reason: Human-readable reason for quarantine.

        Returns:
            QuarantineManifestEntry for the quarantine operation.

        Raises:
            NotImplementedError: Until quarantine semantics are specified.
        """
        raise NotImplementedError(
            "QuarantineManager.quarantine: quarantine semantics not yet specified"
        )

    def list_quarantined(self) -> tuple[QuarantineManifestEntry, ...]:
        """
        List all quarantined artifacts from the manifest.

        Returns:
            Tuple of quarantine manifest entries.

        Raises:
            NotImplementedError: Until quarantine semantics are specified.
        """
        raise NotImplementedError(
            "QuarantineManager.list_quarantined: quarantine semantics not yet specified"
        )
