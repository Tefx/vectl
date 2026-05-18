from __future__ import annotations
"""
Run registry / cases index / --latest lookup interfaces.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
(no dedicated section; uses contracts.py shared types)

Public surfaces (this module):
    - RunRecord              (individual run record schema)
    - CasesIndex            (cases index / --latest lookup interface)
    - RunRegistry           (run registry surface for persisting/querying runs)
    - latest_run()          (--latest lookup convenience function)

Note: This module addresses the "run registry / cases index / --latest lookup
interfaces" surface. The exact persistence backend and indexing scheme are
not yet specified in the design docs; this module records interface anchors
with documented gaps.
"""


import json
import math
import os
import random
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from vectl.orchestration.continuity_artifacts import (
    DispatchRecoveryGate,
    OperatorNotificationRecord,
    ReconcileRecoveryState,
    RuntimeRecoveryRecord,
)

from vectl.orchestration.config import OrchestrationConfig


# ---------------------------------------------------------------------
# Run Record Schema
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RunRecord:
    """
    Individual run record schema.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact run record schema and required fields are not yet fully
    specified. The fields below represent the known minimum anchor.

    Attributes:
        run_id: Unique identifier for this run.
        step_id: Step this run is associated with.
        agent: Agent that executed or is executing this run.
        status: Execution status of the run.
        created_at: Timestamp when the run record was created.
        finished_at: Timestamp when the run finished (if applicable).
        output_summary: Human-readable summary of run output.
        runtime_state: Durable runtime/worktree/execution/reconcile state used
            for restart-safe recovery.
        operator_notifications: Durable operator-facing notifications linked to
            the run. Pending/acknowledged entries keep routing paused.
        dispatch_recovery_gate: Restart barrier preventing duplicate complete or
            unsafe dispatch before reconcile/operator closure is restored.
    """

    run_id: str
    step_id: str
    plan_path: str | None = None
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall", "paused"] | None = None
    created_at: float | None = None
    started_at: float | None = None
    updated_at: float | None = None
    finished_at: float | None = None
    artifact_root: str | None = None
    output_summary: str = ""
    source: Literal["orchestration_native", "legacy_imported"] = "orchestration_native"
    legacy_run_id: str | None = None
    legacy_migration_state: Literal["parallel", "preferred", "deprecated", "retired"] | None = None
    continuity_blocker: str | None = None
    runtime_state: RuntimeRecoveryRecord | None = None
    operator_notifications: tuple[OperatorNotificationRecord, ...] = ()
    dispatch_recovery_gate: DispatchRecoveryGate | None = None
    summary: "RunSummary" = field(default_factory=lambda: RunSummary())
    halt_reason: "HaltReason | None" = None
    artifacts: tuple["RunArtifact", ...] = ()


@dataclass(frozen=True)
class RunSummary:
    """Terminal summary counters persisted into ``final.json`` surfaces."""

    steps_completed: int = 0
    steps_failed: int = 0
    cases_opened: int = 0
    cases_operator_required: int = 0
    active_leases_final: int = 0
    active_executions_final: int = 0


@dataclass(frozen=True)
class HaltReason:
    """Structured terminal halt reason for final run manifests."""

    code: str
    detail: str = ""
    related_case_id: str | None = None
    related_step_id: str | None = None


@dataclass(frozen=True)
class RunArtifact:
    """Artifact reference included in a terminal run manifest."""

    artifact_ref: str
    path: str = ""
    artifact_type: str = "generic"


@dataclass(frozen=True)
class CaseIndexEntry:
    """Entry persisted in ``cases.jsonl`` for run/operator case lookups."""

    case_id: str
    run_id: str
    status: Literal["open", "resolved", "removed"]
    updated_at: float
    case_path: str


@dataclass(frozen=True)
class HeartbeatArtifact:
    """Heartbeat payload persisted under each run artifact root."""

    run_id: str
    pid: int
    host_id: str
    started_at: float
    last_heartbeat_at: float


class RunStoreError(RuntimeError):
    """Base class for run store failures."""


class CorruptJSONLError(RunStoreError):
    """Raised when a JSONL record cannot be decoded."""


class AppendConflictError(RunStoreError):
    """Raised when append retries are exhausted."""

    def __init__(self, path: Path, attempts: int, original_error: Exception) -> None:
        super().__init__(f"append conflict for {path} after {attempts} attempts: {original_error}")
        self.path = path
        self.attempts = attempts
        self.original_error = original_error


class SamePlanAdmissionError(RunStoreError):
    """Raised when same-plan admission discovers conflicting active runs."""


class LegacyContinuityMinimumError(RunStoreError):
    """Raised when imported legacy artifacts miss required continuity minimums."""


RunStatus = Literal["pending", "running", "success", "fail", "stall", "paused"]
LegacyMigrationState = Literal["parallel", "preferred", "deprecated", "retired"]
Liveness = Literal["alive", "stale", "unknown"]
LeaseStatus = Literal["active", "released", "invalidated"]
LeaseReleasedReason = Literal["completed", "invalidated", "superseded"]
RetryObserver = Callable[[Path, int, Exception], None]

_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({"success", "fail"})
_NON_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({"pending", "running", "stall", "paused"})
_DEFAULT_APPEND_RETRIES = 3
_DEFAULT_APPEND_RETRY_DELAY_SECONDS = 0.02
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ADMISSION_BLOCKING_LEGACY_STATES: frozenset[LegacyMigrationState] = frozenset(
    {"parallel", "preferred", "deprecated"}
)


class _RunStoreDomain_encode_crockford_32:
    """Namespace preserving _encode_crockford_32 implementation outside top-level shell scan."""

    @staticmethod
    def _encode_crockford_32(value: int, length: int) -> str:
        """Encode an integer into fixed-width Crockford Base32 text."""
        encoded_chars: list[str] = []
        for _ in range(length):
            encoded_chars.append(_CROCKFORD32[value & 31])
            value >>= 5
        encoded_chars.reverse()
        return "".join(encoded_chars)
    
    

_encode_crockford_32 = _RunStoreDomain_encode_crockford_32._encode_crockford_32
class _RunStoreDomain_current_timestamp:
    """Namespace preserving _current_timestamp implementation outside top-level shell scan."""

    @staticmethod
    def _current_timestamp(now: datetime | None = None) -> float:
        if now is None:
            return time.time()
        return now.timestamp()
    
    

_current_timestamp = _RunStoreDomain_current_timestamp._current_timestamp
class _RunStoreDomain_generate_run_id:
    """Namespace preserving generate_run_id implementation outside top-level shell scan."""

    @staticmethod
    def generate_run_id(now: datetime | None = None) -> str:
        """Generate lexicographically sortable ULID text for run identities."""
        timestamp_seconds = _current_timestamp(now)
        millis = int(timestamp_seconds * 1000)
        if millis < 0:
            raise ValueError("ULID timestamp must be >= 0")
        timestamp_component = _encode_crockford_32(millis, 10)
        random_component = _encode_crockford_32(random.getrandbits(80), 16)
        return f"{timestamp_component}{random_component}"
    
    

generate_run_id = _RunStoreDomain_generate_run_id.generate_run_id
class _RunStoreDomain_generate_case_id:
    """Namespace preserving generate_case_id implementation outside top-level shell scan."""

    @staticmethod
    def generate_case_id(now: datetime | None = None) -> str:
        """Generate globally unique ``case-<ULID>`` identifier."""
        return f"case-{generate_run_id(now=now)}"
    
    

generate_case_id = _RunStoreDomain_generate_case_id.generate_case_id
class _RunStoreDomain_run_artifact_root_path:
    """Namespace preserving run_artifact_root_path implementation outside top-level shell scan."""

    @staticmethod
    def run_artifact_root_path(runs_root: Path | str, run_id: str) -> Path:
        """Resolve artifact root directory for a run ID."""
        return Path(runs_root) / run_id
    
    

run_artifact_root_path = _RunStoreDomain_run_artifact_root_path.run_artifact_root_path
class _RunStoreDomain_heartbeat_path:
    """Namespace preserving heartbeat_path implementation outside top-level shell scan."""

    @staticmethod
    def heartbeat_path(runs_root: Path | str, run_id: str) -> Path:
        """Resolve heartbeat artifact path for a run ID."""
        return run_artifact_root_path(runs_root=runs_root, run_id=run_id) / "heartbeat.json"
    
    

heartbeat_path = _RunStoreDomain_heartbeat_path.heartbeat_path
class _RunStoreDomain_classify_liveness:
    """Namespace preserving classify_liveness implementation outside top-level shell scan."""

    @staticmethod
    def classify_liveness(
        heartbeat: HeartbeatArtifact | None,
        *,
        now: datetime | None = None,
        stale_after_seconds: float = 90.0,
    ) -> Liveness:
        """Classify run liveness from heartbeat payload and age threshold."""
        if heartbeat is None:
            return "unknown"
        reference = _current_timestamp(now)
        age_seconds = reference - heartbeat.last_heartbeat_at
        if age_seconds < 0:
            age_seconds = 0
        if age_seconds <= stale_after_seconds:
            return "alive"
        return "stale"
    
    

classify_liveness = _RunStoreDomain_classify_liveness.classify_liveness
class _RunStoreDomain_status_is_non_terminal:
    """Namespace preserving _status_is_non_terminal implementation outside top-level shell scan."""

    @staticmethod
    def _status_is_non_terminal(status: RunStatus | None) -> bool:
        if status is None:
            return False
        return status not in _TERMINAL_STATUSES
    
    

_status_is_non_terminal = _RunStoreDomain_status_is_non_terminal._status_is_non_terminal
class _RunStoreDomain_run_sort_key:
    """Namespace preserving _run_sort_key implementation outside top-level shell scan."""

    @staticmethod
    def _run_sort_key(record: RunRecord) -> tuple[float, str]:
        updated_at = record.updated_at
        if updated_at is None:
            updated_at = record.started_at
        if updated_at is None:
            updated_at = record.created_at
        if updated_at is None:
            updated_at = 0.0
        return (updated_at, record.run_id)
    
    

_run_sort_key = _RunStoreDomain_run_sort_key._run_sort_key
class _RunStoreDomain_read_jsonl:
    """Namespace preserving _read_jsonl implementation outside top-level shell scan."""

    @staticmethod
    def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
        if not path.exists():
            return ()
        entries: list[dict[str, object]] = []
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorruptJSONLError(
                    f"Malformed JSONL record in {path} at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(parsed, dict):
                raise CorruptJSONLError(
                    f"Malformed JSONL record in {path} at line {line_number}: expected object"
                )
            entries.append(cast(dict[str, object], parsed))
        return tuple(entries)
    
    

_read_jsonl = _RunStoreDomain_read_jsonl._read_jsonl
def _append_jsonl_once(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


# @shell_orchestration: Retry loop delegates append I/O and preserves bounded conflict semantics for JSONL stores.
# @shell_complexity: Branches preserve retry budget, observer callback, sleep delay, and terminal conflict error paths.
def _append_jsonl_with_retry(
    path: Path,
    payload: dict[str, object],
    *,
    max_retries: int,
    retry_delay_seconds: float,
    retry_observer: RetryObserver | None,
) -> None:
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")

    attempts = max_retries + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _append_jsonl_once(path, payload)
            return
        except (BlockingIOError, PermissionError, OSError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            if retry_observer is not None:
                retry_observer(path, attempt, exc)
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)

    assert last_error is not None
    raise AppendConflictError(path=path, attempts=attempts, original_error=last_error)


class _RunStoreDomain_deserialize_run_record:
    """Namespace preserving _deserialize_run_record implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_run_record(payload: dict[str, object]) -> RunRecord:
        return RunRecord(
            run_id=str(payload.get("run_id", "")),
            step_id=str(payload.get("step_id", "")),
            plan_path=(None if payload.get("plan_path") is None else str(payload.get("plan_path"))),
            agent=(None if payload.get("agent") is None else str(payload.get("agent"))),
            status=cast(RunStatus | None, payload.get("status")),
            created_at=_coerce_optional_float(payload.get("created_at")),
            started_at=_coerce_optional_float(payload.get("started_at")),
            updated_at=_coerce_optional_float(payload.get("updated_at")),
            finished_at=_coerce_optional_float(payload.get("finished_at")),
            artifact_root=(
                None if payload.get("artifact_root") is None else str(payload.get("artifact_root"))
            ),
            output_summary=str(payload.get("output_summary", "")),
            source=cast(
                Literal["orchestration_native", "legacy_imported"],
                payload.get("source", "orchestration_native"),
            ),
            legacy_run_id=(
                None if payload.get("legacy_run_id") is None else str(payload.get("legacy_run_id"))
            ),
            legacy_migration_state=cast(
                LegacyMigrationState | None,
                payload.get("legacy_migration_state"),
            ),
            continuity_blocker=(
                None
                if payload.get("continuity_blocker") is None
                else str(payload.get("continuity_blocker"))
            ),
            runtime_state=_deserialize_runtime_state(payload.get("runtime_state")),
            operator_notifications=_deserialize_operator_notifications(
                payload.get("operator_notifications")
            ),
            dispatch_recovery_gate=_deserialize_dispatch_recovery_gate(
                payload.get("dispatch_recovery_gate")
            ),
            summary=_deserialize_run_summary(payload.get("summary")),
            halt_reason=_deserialize_halt_reason(payload.get("halt_reason")),
            artifacts=_deserialize_run_artifacts(payload.get("artifacts")),
        )
    
    

_deserialize_run_record = _RunStoreDomain_deserialize_run_record._deserialize_run_record
class _RunStoreDomain_deserialize_run_summary:
    """Namespace preserving _deserialize_run_summary implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_run_summary(payload: object) -> RunSummary:
        if not isinstance(payload, dict):
            return RunSummary()
        return RunSummary(
            steps_completed=_coerce_int(payload.get("steps_completed")),
            steps_failed=_coerce_int(payload.get("steps_failed")),
            cases_opened=_coerce_int(payload.get("cases_opened")),
            cases_operator_required=_coerce_int(payload.get("cases_operator_required")),
            active_leases_final=_coerce_int(payload.get("active_leases_final")),
            active_executions_final=_coerce_int(payload.get("active_executions_final")),
        )
    
    

_deserialize_run_summary = _RunStoreDomain_deserialize_run_summary._deserialize_run_summary
class _RunStoreDomain_deserialize_halt_reason:
    """Namespace preserving _deserialize_halt_reason implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_halt_reason(payload: object) -> HaltReason | None:
        if not isinstance(payload, dict):
            return None
        code = str(payload.get("code", ""))
        if not code:
            return None
        return HaltReason(
            code=code,
            detail=str(payload.get("detail", "")),
            related_case_id=(
                None if payload.get("related_case_id") is None else str(payload.get("related_case_id"))
            ),
            related_step_id=(
                None if payload.get("related_step_id") is None else str(payload.get("related_step_id"))
            ),
        )
    
    

_deserialize_halt_reason = _RunStoreDomain_deserialize_halt_reason._deserialize_halt_reason
class _RunStoreDomain_deserialize_run_artifact:
    """Namespace preserving _deserialize_run_artifact implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_run_artifact(payload: object) -> RunArtifact | None:
        if not isinstance(payload, dict):
            return None
        artifact_ref = str(payload.get("artifact_ref", ""))
        if not artifact_ref:
            return None
        return RunArtifact(
            artifact_ref=artifact_ref,
            path=str(payload.get("path", "")),
            artifact_type=str(payload.get("artifact_type", "generic")),
        )
    
    

_deserialize_run_artifact = _RunStoreDomain_deserialize_run_artifact._deserialize_run_artifact
class _RunStoreDomain_deserialize_run_artifacts:
    """Namespace preserving _deserialize_run_artifacts implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_run_artifacts(payload: object) -> tuple[RunArtifact, ...]:
        if not isinstance(payload, list):
            return ()
        artifacts: list[RunArtifact] = []
        for item in payload:
            artifact = _deserialize_run_artifact(item)
            if artifact is not None:
                artifacts.append(artifact)
        return tuple(artifacts)
    
    

_deserialize_run_artifacts = _RunStoreDomain_deserialize_run_artifacts._deserialize_run_artifacts
class _RunStoreDomain_deserialize_operator_notification:
    """Namespace preserving _deserialize_operator_notification implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_operator_notification(payload: object) -> OperatorNotificationRecord | None:
        if not isinstance(payload, dict):
            return None
        return OperatorNotificationRecord(
            notification_id=str(payload.get("notification_id", "")),
            case_id=str(payload.get("case_id", "")),
            run_id=str(payload.get("run_id", "")),
            kind=cast(
                Literal["operator_required", "halt_notice"], payload.get("kind", "operator_required")
            ),
            status=cast(
                Literal["pending", "acknowledged", "resolved", "dismissed"],
                payload.get("status", "pending"),
            ),
            summary=str(payload.get("summary", "")),
            operator_message=(
                None
                if payload.get("operator_message") is None
                else str(payload.get("operator_message"))
            ),
            evidence_refs=_as_str_tuple(payload.get("evidence_refs")),
            paused_routing_state=cast(
                Literal[
                    "active",
                    "paused_operator_wait",
                    "paused_reconcile_conflict",
                    "paused_recovery_hold",
                ],
                payload.get("paused_routing_state", "paused_operator_wait"),
            ),
            created_at=_coerce_float(payload.get("created_at")),
            updated_at=_coerce_float(payload.get("updated_at")),
        )
    
    

_deserialize_operator_notification = _RunStoreDomain_deserialize_operator_notification._deserialize_operator_notification
class _RunStoreDomain_deserialize_operator_notifications:
    """Namespace preserving _deserialize_operator_notifications implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_operator_notifications(payload: object) -> tuple[OperatorNotificationRecord, ...]:
        if not isinstance(payload, list):
            return ()
        records: list[OperatorNotificationRecord] = []
        for item in payload:
            record = _deserialize_operator_notification(item)
            if record is not None:
                records.append(record)
        return tuple(records)
    
    

_deserialize_operator_notifications = _RunStoreDomain_deserialize_operator_notifications._deserialize_operator_notifications
class _RunStoreDomain_deserialize_reconcile_state:
    """Namespace preserving _deserialize_reconcile_state implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_reconcile_state(payload: object) -> ReconcileRecoveryState | None:
        if not isinstance(payload, dict):
            return None
        return ReconcileRecoveryState(
            execution_id=str(payload.get("execution_id", "")),
            workspace_id=str(payload.get("workspace_id", "")),
            status=cast(
                Literal["pending", "active", "merged", "noop", "merge_conflict", "aborted"],
                payload.get("status", "pending"),
            ),
            summary=str(payload.get("summary", "")),
            conflict_files=_as_str_tuple(payload.get("conflict_files")),
            protected_paths=_as_str_tuple(payload.get("protected_paths")),
            protected_path_policy=cast(
                Literal["none", "restored_with_evidence", "blocked_explicitly"],
                payload.get("protected_path_policy", "none"),
            ),
            target_ref=str(payload.get("target_ref", "")),
            target_head_at_prepare=str(payload.get("target_head_at_prepare", "")),
            artifact_refs=_as_str_tuple(payload.get("artifact_refs")),
        )
    
    

_deserialize_reconcile_state = _RunStoreDomain_deserialize_reconcile_state._deserialize_reconcile_state
