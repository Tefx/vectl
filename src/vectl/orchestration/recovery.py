"""
Recovery and cutover contracts for orchestration run recovery and legacy migration.

Authority: docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md
Authority: docs/ADR-orchestration-plane-reset.md
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

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from vectl.orchestration.continuity_artifacts import (
    ContinuityJournalEntry,
    ContinuityLedgerEntry,
    DispatchRecoveryGate,
    OperatorNotificationRecord,
    RuntimeRecoveryRecord,
)
from vectl.orchestration.run_store import RunRecord, RunRegistry

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Recovery Outcome Enums
# ---------------------------------------------------------------------


class RecoveryOutcome(str, Enum):
    """
    Outcome classification for recovery operations.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (guardrails)

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

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md

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

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md

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

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md

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
        gate_open_allowed: Whether startup/recovery gate may proceed after this report.
        no_silent_deletion_preserved: Whether quarantine behavior preserved source artifacts.
        runtime_state: Authoritative recovered runtime/worktree/execution/
            reconcile state, when available.
        operator_notifications: Durable operator notifications still governing
            paused routing after recovery.
        dispatch_recovery_gate: Restart barrier proving duplicate-complete and
            unsafe-dispatch prevention remains active until closure is restored.
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
    gate_open_allowed: bool = True
    no_silent_deletion_preserved: bool = True
    runtime_state: RuntimeRecoveryRecord | None = None
    operator_notifications: tuple[OperatorNotificationRecord, ...] = ()
    dispatch_recovery_gate: DispatchRecoveryGate | None = None


def recovery_gate_open_allowed(outcome: RecoveryOutcome) -> bool:
    """Return whether recovery outcome permits opening the gate.

    Authority: step repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
    blocker family B1/B2 (shared semantics and gate_open_allowed behavior).

    QUARANTINED is NOT gate-open because quarantine terminalizes the run for a
    fresh start; the gate must stay closed until re-dispatch. This aligns the
    helper with the actual RecoveryReport field produced by OrchestratonApp.recover(),
    which sets gate_open_allowed=False when fresh_start_terminalized is non-empty
    (the only path that produces QUARANTINED).

    Args:
        outcome: Recovery outcome to classify.

    Returns:
        True when recovery allows orchestration to continue without operator block.
    """

    return outcome in {
        RecoveryOutcome.RECOVERED,
        RecoveryOutcome.NO_ARTIFACTS,
    }


def recovery_case_status(outcome: RecoveryOutcome) -> Literal["open", "resolved", "halt"]:
    """Map recovery outcome to case-surface status without reinterpretation.

    Authority: step repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
    blocker family B1 (status/case/recover/report seam alignment).

    QUARANTINED maps to "open" because quarantine terminalizes the run for a
    fresh start; the gate stays closed and the case remains unresolved until
    re-dispatch completes.

    Args:
        outcome: Recovery outcome to map.

    Returns:
        Case status for consumer-facing case surfaces.
    """

    if outcome is RecoveryOutcome.HALT:
        return "halt"
    if recovery_gate_open_allowed(outcome):
        return "resolved"
    return "open"


def recovery_action_status(
    outcome: RecoveryOutcome,
) -> Literal["applied", "pending", "rejected"]:
    """Map recovery outcome to action-surface status.

    Authority: step repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
    blocker family B1 (status/actions/recover/report seam alignment).

    QUARANTINED maps to "rejected" because the gate is not open (fresh start
    required); pending actions cannot proceed until re-dispatch.

    Args:
        outcome: Recovery outcome to map.

    Returns:
        Action status for consumer-facing inspect-actions surfaces.
    """

    if outcome is RecoveryOutcome.OPERATOR_REQUIRED:
        return "pending"
    if recovery_gate_open_allowed(outcome):
        return "applied"
    return "rejected"


# ---------------------------------------------------------------------
# Startup Recovery Controller Contracts
# ---------------------------------------------------------------------
# Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md
# (StartupRecoveryControllerInput/Output)


@dataclass(frozen=True)
class StartupRecoveryControllerInput:
    """
    Input to the startup recovery controller.

    Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md
    (StartupRecoveryControllerInput)

    Attributes:
        continuity_ledger: Current continuity ledger entries.
        continuity_journal: Current continuity journal entries.
        active_runs: Tuple of active run identifiers known to the system.
        blocked_hygiene_artifacts: Tuple of artifact paths that hygiene classified
            as blocking.
        run_records: Authoritative durable run records available for recovery.
        runtime_states: Restart-sensitive runtime states reconstructed from the
            durable store before dispatch can resume.
        operator_notifications: Pending or acknowledged operator notifications
            that must keep routing paused across restart.
    """

    continuity_ledger: tuple[ContinuityLedgerEntry, ...] = ()
    continuity_journal: tuple[ContinuityJournalEntry, ...] = ()
    active_runs: tuple[str, ...] = ()
    blocked_hygiene_artifacts: tuple[str, ...] = ()
    run_records: tuple[RunRecord, ...] = ()
    runtime_states: tuple[RuntimeRecoveryRecord, ...] = ()
    operator_notifications: tuple[OperatorNotificationRecord, ...] = ()


@dataclass(frozen=True)
class StartupRecoveryControllerOutput:
    """
    Output from the startup recovery controller.

    Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md
    (StartupRecoveryControllerOutput)

    Attributes:
        startup_safe: True if startup hygiene passed and recovery can proceed.
        resume_candidates: Step IDs that are candidates for automatic resume.
        repair_actions: Actions recommended to resolve blocking artifacts.
        halt_requested: True if the controller concluded orchestration should halt.
        halt_reason: Reason for halt if halt_requested is True.
        operator_messages: Tuple of messages to surface to the operator.
        recovered_runtime_states: Runtime states restored into restart-safe
            orchestration view.
        operator_notifications: Notifications that must remain open after
            restart because operator closure has not been durably recorded.
        dispatch_recovery_gates: Dispatch/completion barriers that remain in
            force until reconcile/operator closure is re-established.
    """

    startup_safe: bool
    resume_candidates: tuple[str, ...] = ()
    repair_actions: tuple[RecoveryAction, ...] = ()
    halt_requested: bool = False
    halt_reason: str = ""
    operator_messages: tuple[str, ...] = ()
    recovered_runtime_states: tuple[RuntimeRecoveryRecord, ...] = ()
    operator_notifications: tuple[OperatorNotificationRecord, ...] = ()
    dispatch_recovery_gates: tuple[DispatchRecoveryGate, ...] = ()


# ---------------------------------------------------------------------
# Legacy Run Bridge / Import Contracts
# ---------------------------------------------------------------------
# Authority: ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md


class LegacyRunStatus(str, Enum):
    """
    Migration status of a legacy run.

    Authority: ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md
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

    Authority: ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md

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

    Authority: ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md

    This is the composition boundary for translating legacy continuity artifacts
    into new orchestration plane surfaces.

    GAP: Concrete bridge implementation is deferred to recovery_cutover
    implementation phases.
    """

    def import_legacy_run(
        self,
        _legacy_run_id: str,
        _target_step_id: str,
    ) -> LegacyRunImport:
        """
        Import a legacy run into the new orchestration surface.

        Args:
            _legacy_run_id: Original legacy run identifier.
            _target_step_id: Target step ID in the new plane.

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
        _legacy_run_id: str | None = None,
    ) -> LegacyRunStatus:
        """
        Get the current migration status for a legacy run or the system overall.

        Args:
            _legacy_run_id: Optional legacy run ID. None means system-wide status.

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
        _status: LegacyRunStatus,
        _legacy_run_id: str | None = None,
    ) -> None:
        """
        Set the migration status for a legacy run or system-wide.

        Args:
            _status: The migration status to set.
            _legacy_run_id: Optional legacy run ID. None means system-wide.

        Raises:
            NotImplementedError: Until bridge semantics are specified.
        """
        raise NotImplementedError(
            "LegacyRunBridge.set_migration_status: bridge semantics not yet specified"
        )


class RunStoreLegacyRunBridge(LegacyRunBridge):
    """Run-store backed legacy bridge without introducing a second authority path."""

    def __init__(
        self,
        registry: RunRegistry,
        *,
        plan_path: str,
    ) -> None:
        self._registry = registry
        self._plan_path = plan_path

    def import_legacy_run(
        self,
        legacy_run_id: str,
        target_step_id: str,
    ) -> LegacyRunImport:
        imported = self._registry.import_legacy_run(
            legacy_run_id=legacy_run_id,
            step_id=target_step_id,
            plan_path=self._plan_path,
            status="running",
            migration_state="parallel",
            continuity_artifacts=None,
        )
        state = imported.legacy_migration_state or "parallel"
        return LegacyRunImport(
            legacy_run_id=legacy_run_id,
            target_step_id=target_step_id,
            imported_at=imported.updated_at or imported.created_at or 0.0,
            status=LegacyRunStatus(state),
            compatibility_notes=("run_store_authority=canonical",),
        )

    def get_migration_status(
        self,
        legacy_run_id: str | None = None,
    ) -> LegacyRunStatus:
        if legacy_run_id is not None:
            records = self._registry.imported_legacy_runs(legacy_run_id=legacy_run_id)
            if records:
                status = records[0].legacy_migration_state or "parallel"
                return LegacyRunStatus(status)
            return LegacyRunStatus.PARALLEL

        records = self._registry.imported_legacy_runs()
        if not records:
            return LegacyRunStatus.PARALLEL
        statuses = {
            LegacyRunStatus(record.legacy_migration_state or "parallel") for record in records
        }
        for ordered in (
            LegacyRunStatus.PARALLEL,
            LegacyRunStatus.PREFERRED,
            LegacyRunStatus.DEPRECATED,
            LegacyRunStatus.RETIRED,
        ):
            if ordered in statuses:
                return ordered
        return LegacyRunStatus.PARALLEL

    def set_migration_status(
        self,
        status: LegacyRunStatus,
        legacy_run_id: str | None = None,
    ) -> None:
        mapped_status: Literal["parallel", "preferred", "deprecated", "retired"]
        if status is LegacyRunStatus.PARALLEL:
            mapped_status = "parallel"
        elif status is LegacyRunStatus.PREFERRED:
            mapped_status = "preferred"
        elif status is LegacyRunStatus.DEPRECATED:
            mapped_status = "deprecated"
        else:
            mapped_status = "retired"

        if legacy_run_id is None:
            records = self._registry.imported_legacy_runs()
        else:
            records = self._registry.imported_legacy_runs(legacy_run_id=legacy_run_id)

        for record in records:
            self._registry.save(
                record.__class__(
                    run_id=record.run_id,
                    step_id=record.step_id,
                    plan_path=record.plan_path,
                    agent=record.agent,
                    status=record.status,
                    created_at=record.created_at,
                    started_at=record.started_at,
                    updated_at=record.updated_at,
                    finished_at=record.finished_at,
                    artifact_root=record.artifact_root,
                    output_summary=record.output_summary,
                    source=record.source,
                    legacy_run_id=record.legacy_run_id,
                    legacy_migration_state=mapped_status,
                    continuity_blocker=record.continuity_blocker,
                )
            )


# ---------------------------------------------------------------------
# Cutover Validation Contracts
# ---------------------------------------------------------------------
# Authority: ORCH-OPERATOR-CUTOVER-VALIDATION-MIGRATION-STATE-AUDIT.md
# (retirement criteria)


@dataclass(frozen=True)
class CutoverValidationResult:
    """
    Result of validating readiness for orchestration-plane cutover.

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



from vectl.orchestration.recovery_cutover import CutoverValidator

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
    "recovery_action_status",
    "recovery_case_status",
    "recovery_gate_open_allowed",
    "RunStoreLegacyRunBridge",
    "StartupRecoveryControllerInput",
    "StartupRecoveryControllerOutput",
]
