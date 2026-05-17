"""
Continuity artifact reader/writer contracts for recovery and resume decisions.

Authority: docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md
Authority: docs/ORCHESTRATION-PLANE-ARCHITECTURE.md

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
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Artifact Classification (startup hygiene / quarantine)
# ---------------------------------------------------------------------
# Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md


class ArtifactClassification(str, Enum):
    """
    Startup hygiene artifact classification.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md

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


NotificationKind = Literal["operator_required", "halt_notice"]
NotificationStatus = Literal["pending", "acknowledged", "resolved", "dismissed"]
PausedRoutingState = Literal[
    "active", "paused_operator_wait", "paused_reconcile_conflict", "paused_recovery_hold"
]
ReconcileClosureStatus = Literal["pending", "active", "merged", "noop", "merge_conflict", "aborted"]
DispatchSafetyStatus = Literal[
    "dispatch_allowed",
    "blocked_pending_reconcile",
    "blocked_pending_operator",
    "blocked_recovery_reentry",
]


# ---------------------------------------------------------------------
# Continuity Artifact Schemas
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuityLedgerEntry:
    """
    Durable restart/resume fact record.

    Authority: ORCHESTRATION-PLANE-ARCHITECTURE.md (durable continuity contract)

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
        runtime_state: Authoritative runtime/worktree/execution/reconcile state.
        operator_notifications: Durable notifications governing paused routing.
        dispatch_recovery_gate: Restart barrier for complete/dispatch safety.
    """

    step_id: str
    session_id: str
    runner: str
    status: Literal["active", "completed", "failed", "aborted"]
    created_at: float
    updated_at: float
    capability_snapshot: str = ""
    replay_envelope: str = ""
    runtime_state: RuntimeRecoveryRecord | None = None
    operator_notifications: tuple[OperatorNotificationRecord, ...] = ()
    dispatch_recovery_gate: DispatchRecoveryGate | None = None


@dataclass(frozen=True)
class OperatorNotificationRecord:
    """Durable operator notification state.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 4, 6.2
        docs/ORCHESTRATION-PLANE-ORCH-APP-ROUTING.md sections 8, 10

    Invariants:
        - ``status`` is authoritative receipt state and must survive restart.
        - ``paused_routing_state`` remains non-``active`` until the
          notification path is explicitly resolved or dismissed.
        - ``notification_id`` is stable across restart/recovery re-entry.
    """

    notification_id: str
    case_id: str
    run_id: str
    kind: NotificationKind
    status: NotificationStatus = "pending"
    summary: str = ""
    operator_message: str | None = None
    evidence_refs: tuple[str, ...] = ()
    paused_routing_state: PausedRoutingState = "paused_operator_wait"
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True)
class ReconcileRecoveryState:
    """Durable reconcile evidence preserved for restart-safe recovery.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 5, 6.2, 6.4

    Invariants:
        - ``status`` preserves ``merge_conflict`` and ``aborted`` explicitly.
        - ``protected_paths`` and ``protected_path_policy`` make protected-path
          handling auditable and non-silent.
        - ``artifact_refs`` preserve conflict or abort evidence until recovery
          classifies the run as safely closed.
    """

    execution_id: str
    workspace_id: str
    status: ReconcileClosureStatus
    summary: str = ""
    conflict_files: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    protected_path_policy: Literal["none", "restored_with_evidence", "blocked_explicitly"] = "none"
    target_ref: str = ""
    target_head_at_prepare: str = ""
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DispatchRecoveryGate:
    """Durable dispatch/completion barrier used during restart re-entry.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.3, 6.4, 9

    Invariants:
        - ``duplicate_complete_blocked`` stays true until reconcile closure is
          durably re-established as ``merged`` or ``noop``.
        - ``unsafe_dispatch_blocked`` stays true while restart recovery has not
          yet reconstituted pending reconcile/operator state coherently.
    """

    status: DispatchSafetyStatus
    reason: str = ""
    duplicate_complete_blocked: bool = True
    unsafe_dispatch_blocked: bool = True
    blocked_on_execution_id: str | None = None
    blocked_on_case_id: str | None = None


@dataclass(frozen=True)
class RuntimeRecoveryRecord:
    """Authoritative runtime state required to reconstruct restart position.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 6.2, 6.3, 6.4, 7
        docs/RFC-opencode-orchestration-runner.md section 7 (session/evidence fields)

    This record is intentionally broader than ``RuntimeSnapshot``. The snapshot
    is loop-time observability; this record is restart authority.
    """

    workspace_id: str
    step_id: str
    worktree_path: str
    scratch_branch: str
    target_ref: str
    target_head_at_prepare: str
    execution_id: str = ""
    runner: str = ""
    runner_handle: str = ""
    session_id: str | None = None
    execution_status: Literal[
        "starting",
        "running",
        "stall",
        "success",
        "fail",
        "transport_error",
        "cancelled",
        "unknown",
    ] = "unknown"
    started_at: float = 0.0
    last_update_at: float = 0.0
    execution_artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    request_mode: Literal["start", "resume", "recover"] = "start"
    session_policy: Literal["reuse_allowed", "reuse_forbidden"] = "reuse_forbidden"
    reconcile_state: ReconcileRecoveryState | None = None
    paused_routing_state: PausedRoutingState = "active"


@dataclass(frozen=True)
class ContinuityJournalEntry:
    """
    Minimum recovery telemetry / bootstrap journal entry.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (minimum recovery telemetry)

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
        runtime_state: Optional recovery snapshot for restart-sensitive events.
    """

    event_id: str
    step_id: str
    session_id: str
    runner: str
    event_type: str
    timestamp: float
    outcome: Literal["success", "failure", "abort", "crash_adjacent"] | None = None
    details: tuple[str, ...] = ()
    runtime_state: RuntimeRecoveryRecord | None = None


@dataclass(frozen=True)
class ContinuityHandoff:
    """
    Resume handoff between orchestration sessions.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (continuity handoff)

    Attributes:
        from_session: Session ID being handed off from.
        to_session: Session ID being handed off to.
        step_id: Step ID this handoff covers.
        runner: Runner name.
        artifacts: Tuple of artifact references included in the handoff.
        timestamp: Unix timestamp of the handoff.
        runtime_state: Restart-safe runtime state handed to the next session.
        dispatch_recovery_gate: Dispatch/completion barrier preserved across handoff.
    """

    from_session: str
    to_session: str
    step_id: str
    runner: str
    artifacts: tuple[str, ...] = ()
    timestamp: float | None = None
    runtime_state: RuntimeRecoveryRecord | None = None
    dispatch_recovery_gate: DispatchRecoveryGate | None = None


@dataclass(frozen=True)
class ReplaySafetyEnvelope:
    """
    Replay/idempotency safety boundary.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (replay/idempotency identity)

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
# Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (quarantine manifest format)


@dataclass(frozen=True)
class QuarantineManifestEntry:
    """
    Quarantine operation manifest entry.

    Authority: ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md (manifest format)

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


from vectl.orchestration.continuity_protocols import (
    ArtifactReader,
    ArtifactWriter,
    QuarantineManager,
)


# ---------------------------------------------------------------------
# Artifact Reader / Writer Protocols
# ---------------------------------------------------------------------
# GAP: The exact persistence backend (file-based, SQLite, etc.) is not
# specified. These protocols define the boundary role only.




def build_reconcile_recovery_state(
    *,
    execution_id: str,
    workspace_id: str,
    status: ReconcileClosureStatus,
    summary: str = "",
    conflict_files: tuple[str, ...] = (),
    protected_paths: tuple[str, ...] = (),
    protected_path_policy: Literal["none", "restored_with_evidence", "blocked_explicitly"] = "none",
    target_ref: str = "",
    target_head_at_prepare: str = "",
    artifact_refs: tuple[str, ...] = (),
) -> ReconcileRecoveryState:
    """Build a ReconcileRecoveryState from runtime reconcile data.

    Authority:
        docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md sections 5, 6.2, 6.4
        docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 12

    This function bridges runtime reconcile outcomes to durable recovery artifacts.
    It must be called when reconcile reaches a terminal disposition so that the
    evidence is available for restart/recovery re-entry.

    Invariants:
        - ``merge_conflict`` and ``aborted`` statuses preserve conflict_files and
          artifact_refs explicitly. No silent loss of evidence.
        - ``protected_path_policy`` is set to ``restored_with_evidence`` when
          protected paths (plan.yaml) were restored during reconcile, and
          ``blocked_explicitly`` when protected-path restoration failed.
        - ``merged`` and ``noop`` statuses carry their artifact_refs for audit
          trail but do not require conflict resolution evidence.

    Args:
        execution_id: Execution that produced this reconcile.
        workspace_id: Workspace being reconciled.
        status: Terminal reconcile disposition.
        summary: Human-readable summary.
        conflict_files: Conflicted file paths (populated for merge_conflict).
        protected_paths: Paths that were subject to protected-path policy.
        protected_path_policy: How protected paths were handled.
        target_ref: Target ref for merge-back.
        target_head_at_prepare: HEAD commit at prepare time.
        artifact_refs: Artifact references for reconciliation evidence.

    Returns:
        ReconcileRecoveryState with truthful evidence preservation.
    """
    return ReconcileRecoveryState(
        execution_id=execution_id,
        workspace_id=workspace_id,
        status=status,
        summary=summary,
        conflict_files=conflict_files,
        protected_paths=protected_paths,
        protected_path_policy=protected_path_policy,
        target_ref=target_ref,
        target_head_at_prepare=target_head_at_prepare,
        artifact_refs=artifact_refs,
    )


__all__ = [
    "ArtifactClassification",
    "ArtifactReader",
    "ArtifactWriter",
    "ContinuityHandoff",
    "ContinuityJournalEntry",
    "ContinuityLedgerEntry",
    "DispatchRecoveryGate",
    "DispatchSafetyStatus",
    "NotificationKind",
    "NotificationStatus",
    "OperatorNotificationRecord",
    "PausedRoutingState",
    "QuarantineManager",
    "QuarantineManifestEntry",
    "ReconcileClosureStatus",
    "ReconcileRecoveryState",
    "ReplaySafetyEnvelope",
    "RuntimeRecoveryRecord",
    "build_reconcile_recovery_state",
]
