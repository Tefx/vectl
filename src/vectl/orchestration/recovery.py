"""
Recovery and cutover contracts for orchestration run recovery and legacy migration.

Authority: docs/DRIVER-CONTINUITY-FOUNDATION.md sections 4, 7a
Authority: docs/ORCHESTRATION-PLANE-MIGRATION.md sections 3, 5
Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md sections 9, 10

This module provides typed contracts for:
    - Recovery/report DTOs and outcome enums for ``vectl orch recover``
    - Recovery report schemas and hygiene classification results
    - Startup recovery controller input/output contracts
    - Legacy-run import/bridge surfaces for migration cutover states

Note: These are typed contracts only. Concrete recovery and import behavior
is deferred to the recovery_cutover implementation phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.continuity_artifacts import (
    ArtifactClassification,
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Recovery Outcome Enums
# ---------------------------------------------------------------------


class RecoveryOutcome(str, Enum):
    """
    Outcome classification for recovery operations.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a (guardrails)

    Values:
        RECOVERED: Recovery succeeded; orchestration can continue.
        BLOCKED: Recovery blocked by divergence, corruption, or ambiguity.
        QUARANTINED: Artifact was safe-stale and has been quarantined.
        NO_ARTIFACTS: No continuity artifacts found for the target.
        OPERATOR_REQUIRED: Operator intervention is required to proceed.
        HALT: Recovery concluded that orchestration should stop.
    """

    RECOVERED = "recovered"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    NO_ARTIFACTS = "no_artifacts"
    OPERATOR_REQUIRED = "operator_required"
    HALT = "halt"


class RecoveryHygieneResult(str, Enum):
    """
    Outcome for startup hygiene classification of a single artifact.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a, table

    Values:
        SAFE_STALE_QUARANTINE: Safe to quarantine; does not block startup.
        BLOCKING_DIVERGENCE: Blocks startup; requires operator action.
        BLOCKING_CORRUPT: Blocks startup; requires operator action.
        BLOCKING_AMBIGUOUS: Blocks startup; requires operator action.
    """

    SAFE_STALE_QUARANTINE = "safe_stale_quarantine"
    BLOCKING_DIVERGENCE = "blocking_divergence"
    BLOCKING_CORRUPT = "corrupt_blocking"
    BLOCKING_AMBIGUOUS = "ambiguous_blocking"


# ---------------------------------------------------------------------
# Recovery Report DTOs
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryAction:
    """
    A single action recommended or taken during recovery.

    Attributes:
        action_type: Type of action (e.g., "resume", "reclaim", "quarantine", "halt").
        step_id: Associated step ID if applicable.
        description: Human-readable description of the action.
        artifacts: Tuple of artifact references involved.
    """

    action_type: str
    step_id: str | None = None
    description: str = ""
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class HygieneClassificationResult:
    """
    Result of classifying a single artifact during startup hygiene.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 7a, table

    Attributes:
        artifact_path: Path to the classified artifact.
        classification: The hygiene classification result.
        step_id: Step ID extracted from the artifact (if any).
        reason: Human-readable explanation of the classification.
        quarantine_applied: True if the artifact was quarantined.
    """

    artifact_path: str
    classification: RecoveryHygieneResult
    step_id: str | None = None
    reason: str = ""
    quarantine_applied: bool = False


@dataclass(frozen=True)
class RecoveryReport:
    """
    Report produced by a recovery operation.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md sections 4, 7a

    This is the canonical recovery outcome DTO returned by ``OrchestrationApp.recover()``
    and consumed by the startup recovery controller.

    Attributes:
        outcome: High-level recovery outcome classification.
        message: Human-readable summary of the recovery operation.
        step_id: Step ID targeted by recovery (if any).
        run_id: Run ID affected by recovery (if any).
        actions: Tuple of recovery actions taken or recommended.
        hygiene_results: Tuple of hygiene classification results for all artifacts.
        blocked_artifact_paths: Tuple of artifact paths that are blocking recovery.
        quarantined_artifact_paths: Tuple of artifact paths that were quarantined.
        operator_message: Message to surface to the operator if intervention is needed.
    """

    outcome: RecoveryOutcome
    message: str
    step_id: str | None = None
    run_id: str | None = None
    actions: tuple[RecoveryAction, ...] = ()
    hygiene_results: tuple[HygieneClassificationResult, ...] = ()
    blocked_artifact_paths: tuple[str, ...] = ()
    quarantined_artifact_paths: tuple[str, ...] = ()
    operator_message: str | None = None


# ---------------------------------------------------------------------
# Startup Recovery Controller Contracts
# ---------------------------------------------------------------------
# Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4
# (StartupRecoveryControllerInput/Output)


@dataclass(frozen=True)
class StartupRecoveryControllerInput:
    """
    Input to the startup recovery controller.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4
    (StartupRecoveryControllerInput)

    Attributes:
        continuity_ledger: Current continuity ledger entries.
        continuity_journal: Current continuity journal entries.
        active_runs: Tuple of active run identifiers known to the system.
        blocked_hygiene_artifacts: Tuple of artifact paths that hygiene classified
            as blocking.
    """

    continuity_ledger: tuple[ContinuityLedgerEntry, ...] = ()
    continuity_journal: tuple[ContinuityJournalEntry, ...] = ()
    active_runs: tuple[str, ...] = ()
    blocked_hygiene_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class StartupRecoveryControllerOutput:
    """
    Output from the startup recovery controller.

    Authority: DRIVER-CONTINUITY-FOUNDATION.md section 4
    (StartupRecoveryControllerOutput)

    Attributes:
        startup_safe: True if startup hygiene passed and recovery can proceed.
        resume_candidates: Step IDs that are candidates for automatic resume.
        repair_actions: Actions recommended to resolve blocking artifacts.
        halt_requested: True if the controller concluded orchestration should halt.
        halt_reason: Reason for halt if halt_requested is True.
        operator_messages: Tuple of messages to surface to the operator.
    """

    startup_safe: bool
    resume_candidates: tuple[str, ...] = ()
    repair_actions: tuple[RecoveryAction, ...] = ()
    halt_requested: bool = False
    halt_reason: str = ""
    operator_messages: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Legacy Run Bridge / Import Contracts
# ---------------------------------------------------------------------
# Authority: ORCHESTRATION-PLANE-MIGRATION.md sections 3, 5


class LegacyRunStatus(str, Enum):
    """
    Migration status of a legacy run.

    Authority: ORCHESTRATION-PLANE-MIGRATION.md section 5
    (retirement criteria)

    Values:
        PARALLEL: Legacy and new orchestration are running in parallel.
        PREFERRED: New orchestration is preferred over legacy.
        DEPRECATED: Legacy is deprecated; new orchestration is the target.
        RETIRED: Legacy has been retired; only new orchestration remains.
    """

    PARALLEL = "parallel"
    PREFERRED = "preferred"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


@dataclass(frozen=True)
class LegacyRunImport:
    """
    Contract for importing a legacy run into the new orchestration surface.

    Authority: ORCHESTRATION-PLANE-MIGRATION.md sections 3, 5

    GAP: The exact import mapping and compatibility translation are deferred
    to the recovery_cutover implementation phases.

    Attributes:
        legacy_run_id: Original legacy run identifier.
        target_step_id: Target step ID in the new orchestration plane.
        imported_at: Unix timestamp of import.
        status: Migration status of this import.
        compatibility_notes: Tuple of compatibility notes about the import.
    """

    legacy_run_id: str
    target_step_id: str
    imported_at: float
    status: LegacyRunStatus
    compatibility_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyRunBridge:
    """
    Surface for bridging legacy run state into the new orchestration plane.

    Authority: ORCHESTRATION-PLANE-MIGRATION.md sections 3, 5

    This is the composition boundary for translating legacy continuity artifacts
    into new orchestration plane surfaces.

    GAP: Concrete bridge implementation is deferred to recovery_cutover
    implementation phases.
    """

    def import_legacy_run(
        self,
        legacy_run_id: str,
        target_step_id: str,
    ) -> LegacyRunImport:
        """
        Import a legacy run into the new orchestration surface.

        Args:
            legacy_run_id: Original legacy run identifier.
            target_step_id: Target step ID in the new plane.

        Returns:
            LegacyRunImport record of the import.

        Raises:
            NotImplementedError: Until bridge semantics are specified.
        """
        raise NotImplementedError(
            "LegacyRunBridge.import_legacy_run: bridge semantics not yet specified"
        )

    def get_migration_status(
        self,
        legacy_run_id: str | None = None,
    ) -> LegacyRunStatus:
        """
        Get the current migration status for a legacy run or the system overall.

        Args:
            legacy_run_id: Optional legacy run ID. None means system-wide status.

        Returns:
            Current LegacyRunStatus.

        Raises:
            NotImplementedError: Until bridge semantics are specified.
        """
        raise NotImplementedError(
            "LegacyRunBridge.get_migration_status: bridge semantics not yet specified"
        )

    def set_migration_status(
        self,
        status: LegacyRunStatus,
        legacy_run_id: str | None = None,
    ) -> None:
        """
        Set the migration status for a legacy run or system-wide.

        Args:
            status: The migration status to set.
            legacy_run_id: Optional legacy run ID. None means system-wide.

        Raises:
            NotImplementedError: Until bridge semantics are specified.
        """
        raise NotImplementedError(
            "LegacyRunBridge.set_migration_status: bridge semantics not yet specified"
        )


# ---------------------------------------------------------------------
# Cutover Validation Contracts
# ---------------------------------------------------------------------
# Authority: ORCHESTRATION-PLANE-MIGRATION.md section 5
# (retirement criteria)


@dataclass(frozen=True)
class CutoverValidationResult:
    """
    Result of validating readiness for legacy-driver retirement.

    Authority: ORCHESTRATION-PLANE-MIGRATION.md section 5
    (retirement criteria)

    Attributes:
        can_cutover: True if all retirement criteria are met.
        criteria_results: Tuple of per-criterion results.
        blocking_items: Tuple of items blocking cutover.
        recommendations: Tuple of recommendations for addressing blocking items.
    """

    can_cutover: bool
    criteria_results: tuple[str, ...] = ()
    blocking_items: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


class CutoverValidator:
    """
    Surface for validating legacy-driver retirement readiness.

    Authority: ORCHESTRATION-PLANE-MIGRATION.md section 5
    (retirement criteria)

    Retirement criteria (per ORCHESTRATION-PLANE-MIGRATION.md section 5):
        1. An equivalent target component implementation exists
        2. Behavior is covered by tests in the new location
        3. Docs no longer rely on the legacy path as the primary executable reference
        4. The migration does not erase currently known-good behavior

    GAP: Concrete validation logic is deferred to recovery_cutover
    implementation phases.
    """

    def validate_cutover_readiness(self) -> CutoverValidationResult:
        """
        Validate whether legacy-driver retirement criteria are met.

        Returns:
            CutoverValidationResult with per-criterion results.

        Raises:
            NotImplementedError: Until validator semantics are specified.
        """
        raise NotImplementedError("CutoverValidator.validate_cutover_readiness: not yet specified")


__all__ = [
    "CutoverValidationResult",
    "CutoverValidator",
    "HygieneClassificationResult",
    "LegacyRunBridge",
    "LegacyRunImport",
    "LegacyRunStatus",
    "RecoveryAction",
    "RecoveryHygieneResult",
    "RecoveryOutcome",
    "RecoveryReport",
    "StartupRecoveryControllerInput",
    "StartupRecoveryControllerOutput",
]
