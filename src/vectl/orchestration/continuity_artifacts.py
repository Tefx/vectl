"""
Continuity artifact reader/writer contracts for recovery and resume decisions.

Authority: docs/DRIVER-CONTINUITY-FOUNDATION.md sections 4, 7a
Authority: docs/ORCHESTRATION-PLANE-MIGRATION.md section 4

This module provides typed contracts for the durability/read/write surfaces
used by resume and recovery decisions:
    - ContinuityLedgerEntry     (durable restart/resume fact record)
    - ContinuityJournalEntry    (minimum recovery telemetry / bootstrap journal)
    - ContinuityHandoff         (resume handoff between orchestration sessions)
    - ReplaySafetyEnvelope     (replay/idempotency safety boundary)
    - ArtifactReader            (protocol for reading continuity artifacts)
    - ArtifactWriter            (protocol for writing continuity artifacts)
    - QuarantineManager         (run-local quarantine and startup hygiene)

Note: These are typed contracts only. Concrete persistence implementation
is deferred to the recovery_cutover implementation phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Artifact Classification (startup hygiene / quarantine)
# ---------------------------------------------------------------------
# Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a


class ArtifactClassification(str, Enum):
    """
    Startup hygiene artifact classification.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a, table

    Values:
        SAFE_STALE_QUARANTINE: Step absent from plan and repaired claims;
            artifact may be quarantined (does not block startup).
        BLOCKING_DIVERGENCE: Conflicts with current plan or repaired claims;
            requires operator action (blocks startup).
        CORRUPT_BLOCKING: Unparsable or missing step identity;
            requires operator action (blocks startup).
        AMBIGUOUS_BLOCKING: Migration/rename suspicion detected;
            requires operator action (blocks startup).
    """

    SAFE_STALE_QUARANTINE = "safe_stale_quarantine"
    BLOCKING_DIVERGENCE = "blocking_divergence"
    CORRUPT_BLOCKING = "corrupt_blocking"
    AMBIGUOUS_BLOCKING = "ambiguous_blocking"


# ---------------------------------------------------------------------
# Continuity Artifact Schemas
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuityLedgerEntry:
    """
    Durable restart/resume fact record.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4 (Durable continuity contract)

    This is the authoritative durable record of a continuity fact.
    It must survive process death and be consumable at startup.

    Attributes:
        step_id: Step identifier this entry is about.
        session_id: Session identifier associated with this entry.
        runner: Runner name used for this step.
        status: Status of the continuity entry.
        created_at: Unix timestamp when this entry was created.
        updated_at: Unix timestamp when this entry was last updated.
        capability_snapshot: Capability snapshot at time of entry creation.
        replay_envelope: Replay safety envelope for this entry.
    """

    step_id: str
    session_id: str
    runner: str
    status: Literal["active", "completed", "failed", "aborted"]
    created_at: float
    updated_at: float
    capability_snapshot: str = ""
    replay_envelope: str = ""


@dataclass(frozen=True)
class ContinuityJournalEntry:
    """
    Minimum recovery telemetry / bootstrap journal entry.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4 (Minimum recovery telemetry)

    This is the narrow bootstrap journal for restart decisions only.

    Attributes:
        event_id: Unique identifier for this journal event.
        step_id: Step identifier this entry is about.
        session_id: Session identifier.
        runner: Runner name.
        event_type: Type of journal event.
        timestamp: Unix timestamp of this event.
        outcome: Outcome classification (success, failure, abort, crash_adjacent).
        details: Additional event details.
    """

    event_id: str
    step_id: str
    session_id: str
    runner: str
    event_type: str
    timestamp: float
    outcome: Literal["success", "failure", "abort", "crash_adjacent"] | None = None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContinuityHandoff:
    """
    Resume handoff between orchestration sessions.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4 (ContinuityHandoff)

    Attributes:
        from_session: Session ID being handed off from.
        to_session: Session ID being handed off to.
        step_id: Step ID this handoff covers.
        runner: Runner name.
        artifacts: Tuple of artifact references included in the handoff.
        timestamp: Unix timestamp of the handoff.
    """

    from_session: str
    to_session: str
    step_id: str
    runner: str
    artifacts: tuple[str, ...] = ()
    timestamp: float | None = None


@dataclass(frozen=True)
class ReplaySafetyEnvelope:
    """
    Replay/idempotency safety boundary.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4 (Replay/idempotency identity)

    This envelope gates replay safety. No downstream continuity phase may infer
    replay safety solely from runner support for resume.

    Attributes:
        step_id: Step identifier this envelope is for.
        replay_safe: True only when replay is explicitly safe per this envelope.
        scope: Tuple of safe replay scope identifiers.
        constraints: Tuple of replay constraint strings.
        created_at: Unix timestamp when this envelope was created.
    """

    step_id: str
    replay_safe: bool
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    created_at: float | None = None


# ---------------------------------------------------------------------
# Quarantine Manifest Entry
# ---------------------------------------------------------------------
# Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a, quarantine manifest format


@dataclass(frozen=True)
class QuarantineManifestEntry:
    """
    Quarantine operation manifest entry.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a (manifest format)

    Attributes:
        artifact_kind: Kind of artifact (e.g., "ledger", "journal").
        original_path: Original path of the artifact before quarantine.
        reason: Classification reason string.
        classification: The artifact classification.
        quarantine_destination: Destination path in quarantine.
        audit_timestamp: UTC ISO timestamp of the quarantine operation.
        step_id: Associated step ID if applicable.
    """

    artifact_kind: str
    original_path: str
    reason: str
    classification: ArtifactClassification
    quarantine_destination: str
    audit_timestamp: str
    step_id: str | None = None


# ---------------------------------------------------------------------
# Artifact Reader / Writer Protocols
# ---------------------------------------------------------------------
# GAP: The exact persistence backend (file-based, SQLite, etc.) is not
# specified. These protocols define the boundary role only.


class ArtifactReader(Protocol):
    """
    Protocol for reading continuity artifacts.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4

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

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4

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
# Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a


class QuarantineManager:
    """
    Run-local quarantine and startup hygiene manager.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a

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


__all__ = [
    "ArtifactClassification",
    "ArtifactReader",
    "ArtifactWriter",
    "ContinuityHandoff",
    "ContinuityJournalEntry",
    "ContinuityLedgerEntry",
    "QuarantineManager",
    "QuarantineManifestEntry",
    "ReplaySafetyEnvelope",
]
