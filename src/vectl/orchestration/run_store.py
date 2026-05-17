# @invar:allow file_size: Run and drive JSONL persistence remain co-located to preserve public run-store import compatibility during scoped remediation.
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

from __future__ import annotations

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
class _RunStoreDomain_deserialize_runtime_state:
    """Namespace preserving _deserialize_runtime_state implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_runtime_state(payload: object) -> RuntimeRecoveryRecord | None:
        if not isinstance(payload, dict):
            return None
        return RuntimeRecoveryRecord(
            workspace_id=str(payload.get("workspace_id", "")),
            step_id=str(payload.get("step_id", "")),
            worktree_path=str(payload.get("worktree_path", "")),
            scratch_branch=str(payload.get("scratch_branch", "")),
            target_ref=str(payload.get("target_ref", "")),
            target_head_at_prepare=str(payload.get("target_head_at_prepare", "")),
            execution_id=str(payload.get("execution_id", "")),
            runner=str(payload.get("runner", "")),
            runner_handle=str(payload.get("runner_handle", "")),
            session_id=(None if payload.get("session_id") is None else str(payload.get("session_id"))),
            execution_status=cast(
                Literal[
                    "starting",
                    "running",
                    "stall",
                    "success",
                    "fail",
                    "transport_error",
                    "cancelled",
                    "unknown",
                ],
                payload.get("execution_status", "unknown"),
            ),
            started_at=_coerce_float(payload.get("started_at")),
            last_update_at=_coerce_float(payload.get("last_update_at")),
            execution_artifact_refs=_as_str_tuple(payload.get("execution_artifact_refs")),
            evidence_refs=_as_str_tuple(payload.get("evidence_refs")),
            request_mode=cast(
                Literal["start", "resume", "recover"],
                payload.get("request_mode", "start"),
            ),
            session_policy=cast(
                Literal["reuse_allowed", "reuse_forbidden"],
                payload.get("session_policy", "reuse_forbidden"),
            ),
            reconcile_state=_deserialize_reconcile_state(payload.get("reconcile_state")),
            paused_routing_state=cast(
                Literal[
                    "active",
                    "paused_operator_wait",
                    "paused_reconcile_conflict",
                    "paused_recovery_hold",
                ],
                payload.get("paused_routing_state", "active"),
            ),
        )
    
    

_deserialize_runtime_state = _RunStoreDomain_deserialize_runtime_state._deserialize_runtime_state
class _RunStoreDomain_deserialize_dispatch_recovery_gate:
    """Namespace preserving _deserialize_dispatch_recovery_gate implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_dispatch_recovery_gate(payload: object) -> DispatchRecoveryGate | None:
        if not isinstance(payload, dict):
            return None
        return DispatchRecoveryGate(
            status=cast(
                Literal[
                    "dispatch_allowed",
                    "blocked_pending_reconcile",
                    "blocked_pending_operator",
                    "blocked_recovery_reentry",
                ],
                payload.get("status", "blocked_recovery_reentry"),
            ),
            reason=str(payload.get("reason", "")),
            duplicate_complete_blocked=bool(payload.get("duplicate_complete_blocked", True)),
            unsafe_dispatch_blocked=bool(payload.get("unsafe_dispatch_blocked", True)),
            blocked_on_execution_id=(
                None
                if payload.get("blocked_on_execution_id") is None
                else str(payload.get("blocked_on_execution_id"))
            ),
            blocked_on_case_id=(
                None
                if payload.get("blocked_on_case_id") is None
                else str(payload.get("blocked_on_case_id"))
            ),
        )
    
    

_deserialize_dispatch_recovery_gate = _RunStoreDomain_deserialize_dispatch_recovery_gate._deserialize_dispatch_recovery_gate
class _RunStoreDomain_coerce_float:
    """Namespace preserving _coerce_float implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_float(value: object) -> float:
        if isinstance(value, (int, float)):
            coerced = float(value)
            if not math.isfinite(coerced):
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}")
            return coerced
        if isinstance(value, str) and value.strip() != "":
            try:
                coerced = float(value)
            except ValueError as exc:
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}") from exc
            if not math.isfinite(coerced):
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}")
            return coerced
        return 0.0
    
    

_coerce_float = _RunStoreDomain_coerce_float._coerce_float
class _RunStoreDomain_coerce_int:
    """Namespace preserving _coerce_int implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip() != "":
            try:
                return int(float(value))
            except ValueError as exc:
                raise CorruptJSONLError(f"Malformed integer payload value: {value!r}") from exc
        return 0
    
    

_coerce_int = _RunStoreDomain_coerce_int._coerce_int
class _RunStoreDomain_coerce_optional_float:
    """Namespace preserving _coerce_optional_float implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_optional_float(value: object) -> float | None:
        if value is None:
            return None
        return _coerce_float(value)
    
    

_coerce_optional_float = _RunStoreDomain_coerce_optional_float._coerce_optional_float
class _RunStoreDomain_as_str_tuple:
    """Namespace preserving _as_str_tuple implementation outside top-level shell scan."""

    @staticmethod
    def _as_str_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value)
    
    

_as_str_tuple = _RunStoreDomain_as_str_tuple._as_str_tuple
class _RunStoreDomain_legacy_journal_entry:
    """Namespace preserving _legacy_journal_entry implementation outside top-level shell scan."""

    @staticmethod
    def _legacy_journal_entry(continuity_artifacts: dict[str, object]) -> dict[str, object] | None:
        journal_payload = continuity_artifacts.get("journal")
        if isinstance(journal_payload, dict):
            return cast(dict[str, object], journal_payload)
        if isinstance(journal_payload, list) and journal_payload:
            first = journal_payload[0]
            if isinstance(first, dict):
                return cast(dict[str, object], first)
        return None
    
    

_legacy_journal_entry = _RunStoreDomain_legacy_journal_entry._legacy_journal_entry
class _RunStoreDomain_validate_legacy_continuity_minimums:
    """Namespace preserving _validate_legacy_continuity_minimums implementation outside top-level shell scan."""

    @staticmethod
    def _validate_legacy_continuity_minimums(
        *,
        continuity_artifacts: dict[str, object] | None,
        expected_step_id: str,
    ) -> str | None:
        if continuity_artifacts is None:
            return "missing continuity artifacts: ledger and journal are required"
    
        ledger_payload = continuity_artifacts.get("ledger")
        if not isinstance(ledger_payload, dict):
            return "missing continuity minimums: ledger object is required"
    
        journal_entry = _legacy_journal_entry(continuity_artifacts)
        if journal_entry is None:
            return "missing continuity minimums: journal entry is required"
    
        required_ledger_fields = ("step_id", "session_id", "runner", "status")
        missing_ledger_fields = [
            field
            for field in required_ledger_fields
            if str(ledger_payload.get(field, "")).strip() == ""
        ]
        if missing_ledger_fields:
            joined = ", ".join(sorted(missing_ledger_fields))
            return f"missing continuity minimums: ledger fields [{joined}]"
    
        required_journal_fields = ("event_id", "step_id", "session_id", "runner", "event_type")
        missing_journal_fields = [
            field
            for field in required_journal_fields
            if str(journal_entry.get(field, "")).strip() == ""
        ]
        if missing_journal_fields:
            joined = ", ".join(sorted(missing_journal_fields))
            return f"missing continuity minimums: journal fields [{joined}]"
    
        ledger_step_id = str(ledger_payload.get("step_id", "")).strip()
        journal_step_id = str(journal_entry.get("step_id", "")).strip()
        if ledger_step_id != expected_step_id or journal_step_id != expected_step_id:
            return (
                "blocking_divergence: continuity step identity does not match imported "
                f"target_step_id={expected_step_id}"
            )
        return None
    
    

_validate_legacy_continuity_minimums = _RunStoreDomain_validate_legacy_continuity_minimums._validate_legacy_continuity_minimums
class _RunStoreDomain_legacy_state_blocks_same_plan:
    """Namespace preserving _legacy_state_blocks_same_plan implementation outside top-level shell scan."""

    @staticmethod
    def _legacy_state_blocks_same_plan(record: RunRecord) -> bool:
        if record.source != "legacy_imported":
            return _status_is_non_terminal(record.status)
        if record.legacy_migration_state not in _ADMISSION_BLOCKING_LEGACY_STATES:
            return False
        return _status_is_non_terminal(record.status)
    
    

_legacy_state_blocks_same_plan = _RunStoreDomain_legacy_state_blocks_same_plan._legacy_state_blocks_same_plan
class _RunStoreDomain_deserialize_case_entry:
    """Namespace preserving _deserialize_case_entry implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_case_entry(payload: dict[str, object]) -> CaseIndexEntry:
        raw_updated_at = payload.get("updated_at", 0.0)
        updated_at = float(raw_updated_at) if isinstance(raw_updated_at, (int, float, str)) else 0.0
        return CaseIndexEntry(
            case_id=str(payload.get("case_id", "")),
            run_id=str(payload.get("run_id", "")),
            status=cast(Literal["open", "resolved", "removed"], payload.get("status", "open")),
            updated_at=updated_at,
            case_path=str(payload.get("case_path", "")),
        )
    
    

_deserialize_case_entry = _RunStoreDomain_deserialize_case_entry._deserialize_case_entry
# ---------------------------------------------------------------------
# Cases Index Interface
# ---------------------------------------------------------------------
# GAP: The cases index (--latest lookup semantics, index schema) is not
# yet specified. This is a forward contract stub.


class CasesIndex(Protocol):
    """
    Protocol for cases index / --latest lookup interface.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact index schema, query model, and --latest lookup semantics
    are not yet specified. No concrete implementation should be added
    in this contract step.

    This protocol records the expected boundary role for index lookups.
    """

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        """
        Return the latest run record for a given step.

        Args:
            step_id: The step to look up.

        Returns:
            The most recent RunRecord for the step, or None if no runs exist.

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        """
        Return all run records for a given step.

        Args:
            step_id: The step to look up.

        Returns:
            All RunRecords for the step, ordered by creation time (newest first).

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...

    def by_status(
        self,
        status: Literal["pending", "running", "success", "fail", "stall", "paused"],
    ) -> tuple[RunRecord, ...]:
        """
        Return all run records with a given status.

        Args:
            status: The status to filter by.

        Returns:
            All RunRecords with the given status.

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...


# ---------------------------------------------------------------------
# Run Registry Surface
# ---------------------------------------------------------------------
# GAP: The file-based run registry persistence/query boundary is not
# yet specified. This is a forward contract stub.


class RunRegistry:
    """
    Run registry surface for persisting and querying run records.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact persistence backend (file-based, SQLite, etc.) and
    query API are not yet specified. No concrete implementation should
    be added in this contract step.
    """

    def __init__(
        self,
        store_root: Path | str = ".vectl/runs",
        *,
        max_append_retries: int = _DEFAULT_APPEND_RETRIES,
        append_retry_delay_seconds: float = _DEFAULT_APPEND_RETRY_DELAY_SECONDS,
        append_retry_observer: RetryObserver | None = None,
    ) -> None:
        self._store_root = Path(store_root)
        self._index_path = self._store_root / "index.jsonl"
        self._cases_path = self._store_root / "cases.jsonl"
        self._max_append_retries = max_append_retries
        self._append_retry_delay_seconds = append_retry_delay_seconds
        self._append_retry_observer = append_retry_observer

    def save(self, record: RunRecord) -> None:
        """
        Persist a run record.

        Args:
            record: The run record to persist.

        Raises:
            NotImplementedError: Until registry persistence is specified.
        """
        normalized_updated_at = record.updated_at
        if normalized_updated_at is None:
            normalized_updated_at = _current_timestamp()

        normalized_started_at = record.started_at
        if normalized_started_at is None:
            normalized_started_at = record.created_at

        normalized_record = RunRecord(
            run_id=record.run_id,
            step_id=record.step_id,
            plan_path=record.plan_path,
            agent=record.agent,
            status=record.status,
            created_at=record.created_at,
            started_at=normalized_started_at,
            updated_at=normalized_updated_at,
            finished_at=record.finished_at,
            artifact_root=(
                record.artifact_root
                if record.artifact_root is not None
                else str(run_artifact_root_path(self._store_root, record.run_id))
            ),
            output_summary=record.output_summary,
            source=record.source,
            legacy_run_id=record.legacy_run_id,
            legacy_migration_state=record.legacy_migration_state,
            continuity_blocker=record.continuity_blocker,
            runtime_state=record.runtime_state,
            operator_notifications=record.operator_notifications,
            dispatch_recovery_gate=record.dispatch_recovery_gate,
            summary=record.summary,
            halt_reason=record.halt_reason,
            artifacts=record.artifacts,
        )

        _append_jsonl_with_retry(
            self._index_path,
            asdict(normalized_record),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    @property
    def store_root(self) -> Path:
        """Return authoritative run-store root path."""

        return self._store_root

    def latest_records(self) -> tuple[RunRecord, ...]:
        """Return latest run record per run ID sorted by recency."""

        records = list(self._latest_records_by_run_id().values())
        records.sort(key=_run_sort_key, reverse=True)
        return tuple(records)

    def latest_cases(self, *, include_removed: bool = True) -> tuple[CaseIndexEntry, ...]:
        """Return latest case entry per case ID sorted by recency."""

        entries = list(self._latest_cases_by_case_id().values())
        if not include_removed:
            entries = [entry for entry in entries if entry.status != "removed"]
        entries.sort(key=lambda entry: (entry.updated_at, entry.case_id), reverse=True)
        return tuple(entries)

    def case_by_id(self, case_id: str) -> CaseIndexEntry | None:
        """Look up latest case entry by case identifier."""

        return self._latest_cases_by_case_id().get(case_id)

    @contextmanager
    def admission_guard(self):
        """Hold cross-process lock for same-plan admission and first durable save."""

        self._store_root.mkdir(parents=True, exist_ok=True)
        lock_path = self._store_root / ".admission.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            flock(handle.fileno(), LOCK_EX)
            try:
                yield
            finally:
                flock(handle.fileno(), LOCK_UN)

    def admit_for_start(
        self,
        *,
        run_id: str,
        step_id: str,
        plan_path: str,
        agent: str | None,
        artifact_root: str,
        output_summary: str,
        current_run_id: str | None = None,
    ) -> RunRecord:
        """Atomically validate same-plan admission and persist pending run record."""

        with self.admission_guard():
            self.assert_can_admit_same_plan(plan_path=plan_path, current_run_id=current_run_id)
            admitted = RunRecord(
                run_id=run_id,
                step_id=step_id,
                plan_path=plan_path,
                agent=agent,
                status="pending",
                artifact_root=artifact_root,
                output_summary=output_summary,
            )
            self.save(admitted)
            return admitted

    def by_id(self, run_id: str) -> RunRecord | None:
        """
        Look up a run record by run ID.

        Args:
            run_id: The run ID to look up.

        Returns:
            The RunRecord if found, else None.

        Raises:
            NotImplementedError: Until registry query semantics are specified.
        """
        for record in self._latest_records_by_run_id().values():
            if record.run_id == run_id:
                return record
        return None

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        """
        Return the latest run record for a step (--latest lookup).

        Args:
            step_id: The step to look up.

        Returns:
            The most recent RunRecord for the step, or None.

        Raises:
            NotImplementedError: Until --latest lookup semantics are specified.
        """
        records_for_step = self.all_for_step(step_id)
        if not records_for_step:
            return None
        non_terminal = tuple(r for r in records_for_step if _status_is_non_terminal(r.status))
        if non_terminal:
            return max(non_terminal, key=_run_sort_key)
        return max(records_for_step, key=_run_sort_key)

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        """
        Return all run records for a step.

        Args:
            step_id: The step to look up.

        Returns:
            All RunRecords for the step.

        Raises:
            NotImplementedError: Until registry query semantics are specified.
        """
        records = [
            record
            for record in self._latest_records_by_run_id().values()
            if record.step_id == step_id
        ]
        records.sort(key=_run_sort_key, reverse=True)
        return tuple(records)

    def by_status(
        self,
        status: Literal["pending", "running", "success", "fail", "stall", "paused"],
    ) -> tuple[RunRecord, ...]:
        """Return all latest run records with ``status``."""
        records = [
            record
            for record in self._latest_records_by_run_id().values()
            if record.status == status
        ]
        records.sort(key=_run_sort_key, reverse=True)
        return tuple(records)

    def append_case(self, entry: CaseIndexEntry) -> None:
        """Append case index entry to durable ``cases.jsonl``."""
        _append_jsonl_with_retry(
            self._cases_path,
            asdict(entry),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    def cases_for_run(self, run_id: str) -> tuple[CaseIndexEntry, ...]:
        """Return latest non-removed case entries associated with ``run_id``."""
        records = [
            entry
            for entry in self.latest_cases(include_removed=False)
            if entry.run_id == run_id and entry.status != "removed"
        ]
        records.sort(key=lambda entry: (entry.updated_at, entry.case_id), reverse=True)
        return tuple(records)

    def remove_cases_for_run(self, run_id: str, *, updated_at: float | None = None) -> int:
        """Tombstone all active case-index entries for ``run_id``."""
        latest_cases = self.latest_cases(include_removed=False)
        candidates = [
            entry for entry in latest_cases if entry.run_id == run_id and entry.status != "removed"
        ]
        tombstone_at = _current_timestamp() if updated_at is None else updated_at
        for entry in candidates:
            self.append_case(
                CaseIndexEntry(
                    case_id=entry.case_id,
                    run_id=entry.run_id,
                    status="removed",
                    updated_at=tombstone_at,
                    case_path=entry.case_path,
                )
            )
        return len(candidates)

    def prune_run(self, run_id: str) -> int:
        """Prune/remove run-linked cases from the case index."""
        return self.remove_cases_for_run(run_id)

    def import_legacy_run(
        self,
        *,
        legacy_run_id: str,
        step_id: str,
        plan_path: str,
        status: RunStatus,
        migration_state: LegacyMigrationState,
        continuity_artifacts: dict[str, object] | None,
        run_id: str | None = None,
        imported_at: float | None = None,
    ) -> RunRecord:
        """Import a legacy run into the canonical orchestration run index."""
        continuity_blocker = _validate_legacy_continuity_minimums(
            continuity_artifacts=continuity_artifacts,
            expected_step_id=step_id,
        )
        effective_status = "stall" if continuity_blocker is not None else status
        effective_run_id = run_id or generate_run_id()
        imported_timestamp = _current_timestamp() if imported_at is None else imported_at

        output_summary = (
            "legacy import registered via run-store canonical index "
            f"legacy_run_id={legacy_run_id} migration_state={migration_state}"
        )
        if continuity_blocker is not None:
            output_summary = f"{output_summary}; migration/recovery blocker={continuity_blocker}"

        imported_record = RunRecord(
            run_id=effective_run_id,
            step_id=step_id,
            plan_path=plan_path,
            status=effective_status,
            created_at=imported_timestamp,
            started_at=imported_timestamp,
            updated_at=imported_timestamp,
            output_summary=output_summary,
            source="legacy_imported",
            legacy_run_id=legacy_run_id,
            legacy_migration_state=migration_state,
            continuity_blocker=continuity_blocker,
        )
        self.save(imported_record)

        if continuity_blocker is not None:
            case_id = generate_case_id()
            case_path = f"cases/{case_id}.json"
            self.append_case(
                CaseIndexEntry(
                    case_id=case_id,
                    run_id=effective_run_id,
                    status="open",
                    updated_at=imported_timestamp,
                    case_path=case_path,
                )
            )

        return imported_record

    def imported_legacy_runs(
        self,
        *,
        step_id: str | None = None,
        legacy_run_id: str | None = None,
    ) -> tuple[RunRecord, ...]:
        """Return imported legacy runs from canonical run-store index."""
        records = [
            record
            for record in self._latest_records_by_run_id().values()
            if record.source == "legacy_imported"
            and (step_id is None or record.step_id == step_id)
            and (legacy_run_id is None or record.legacy_run_id == legacy_run_id)
        ]
        records.sort(key=_run_sort_key, reverse=True)
        return tuple(records)

    def can_admit_same_plan(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> tuple[bool, tuple[RunRecord, ...]]:
        """Check same-plan admission against existing active non-terminal runs."""
        conflicts = [
            record
            for record in self._latest_records_by_run_id().values()
            if record.plan_path == plan_path
            and _legacy_state_blocks_same_plan(record)
            and record.run_id != current_run_id
        ]
        conflicts.sort(key=_run_sort_key, reverse=True)
        if conflicts:
            return (False, tuple(conflicts))
        return (True, ())

    def assert_can_admit_same_plan(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> None:
        """Raise if same-plan admission would conflict with active runs."""
        allowed, conflicts = self.can_admit_same_plan(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )
        if allowed:
            return
        conflict_ids = ", ".join(record.run_id for record in conflicts)
        raise SamePlanAdmissionError(
            f"conflicting active runs for plan '{plan_path}': {conflict_ids}"
        )

    def run_artifact_root(self, run_id: str) -> Path:
        """Return artifact root for a run, preferring index metadata."""
        record = self.by_id(run_id)
        if record is not None and record.artifact_root:
            return Path(record.artifact_root)
        return run_artifact_root_path(self._store_root, run_id)

    def read_heartbeat(self, run_id: str) -> HeartbeatArtifact | None:
        """Read heartbeat artifact for a run, returning ``None`` when missing."""
        path = self.run_artifact_root(run_id) / "heartbeat.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CorruptJSONLError(f"Malformed heartbeat payload in {path}: expected object")
        return HeartbeatArtifact(
            run_id=str(payload.get("run_id", "")),
            pid=int(payload.get("pid", 0)),
            host_id=str(payload.get("host_id", "")),
            started_at=float(payload.get("started_at", 0.0)),
            last_heartbeat_at=float(payload.get("last_heartbeat_at", 0.0)),
        )

    def liveness_for_run(
        self,
        run_id: str,
        *,
        stale_after_seconds: float = 90.0,
        now: datetime | None = None,
    ) -> Liveness:
        """Return canonical liveness value from persisted heartbeat."""
        heartbeat = self.read_heartbeat(run_id)
        return classify_liveness(
            heartbeat,
            stale_after_seconds=stale_after_seconds,
            now=now,
        )

    def _latest_records_by_run_id(self) -> dict[str, RunRecord]:
        entries = _read_jsonl(self._index_path)
        latest: dict[str, RunRecord] = {}
        for payload in entries:
            record = _deserialize_run_record(payload)
            previous = latest.get(record.run_id)
            if previous is None or _run_sort_key(record) >= _run_sort_key(previous):
                latest[record.run_id] = record
        return latest

    def _latest_cases_by_case_id(self) -> dict[str, CaseIndexEntry]:
        entries = _read_jsonl(self._cases_path)
        latest: dict[str, CaseIndexEntry] = {}
        for payload in entries:
            entry = _deserialize_case_entry(payload)
            previous = latest.get(entry.case_id)
            if previous is None or (entry.updated_at, entry.case_id) >= (
                previous.updated_at,
                previous.case_id,
            ):
                latest[entry.case_id] = entry
        return latest


class RunInspectionBoundary(Protocol):
    """Public read boundary consumed by inspection/query surfaces."""

    def latest_records(self) -> tuple[RunRecord, ...]: ...

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]: ...

    def by_status(
        self,
        status: Literal["pending", "running", "success", "fail", "stall", "paused"],
    ) -> tuple[RunRecord, ...]: ...

    def latest_for_step(self, step_id: str) -> RunRecord | None: ...

    def latest_cases(self, *, include_removed: bool = True) -> tuple[CaseIndexEntry, ...]: ...

    def cases_for_run(self, run_id: str) -> tuple[CaseIndexEntry, ...]: ...


@dataclass(frozen=True)
class RunRegistryInspectionView:
    """Adapter exposing a stable inspection read boundary over ``RunRegistry``."""

    registry: RunRegistry

    def latest_records(self) -> tuple[RunRecord, ...]:
        return self.registry.latest_records()

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        return self.registry.all_for_step(step_id)

    def by_status(
        self,
        status: Literal["pending", "running", "success", "fail", "stall", "paused"],
    ) -> tuple[RunRecord, ...]:
        return self.registry.by_status(status)

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        return self.registry.latest_for_step(step_id)

    def latest_cases(self, *, include_removed: bool = True) -> tuple[CaseIndexEntry, ...]:
        return self.registry.latest_cases(include_removed=include_removed)

    def cases_for_run(self, run_id: str) -> tuple[CaseIndexEntry, ...]:
        return self.registry.cases_for_run(run_id)


# ---------------------------------------------------------------------
# --latest Lookup Convenience Function
# ---------------------------------------------------------------------


class _RunStoreDomain_latest_run:
    """Namespace preserving latest_run implementation outside top-level shell scan."""

    @staticmethod
    def latest_run(step_id: str, registry: RunRegistry | None = None) -> RunRecord | None:
        """
        Return the latest run record for a step (--latest lookup convenience surface).
    
        Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)
    
        GAP: The default registry instance and global lookup behavior are not
        yet specified.
    
        Args:
            step_id: The step to look up.
            registry: Optional explicit registry instance. If None, a default
                registry must be globally available.
    
        Returns:
            The most recent RunRecord for the step, or None.
    
        Raises:
            NotImplementedError: Until --latest lookup and default registry
                semantics are specified.
        """
        resolved_registry = registry if registry is not None else RunRegistry()
        return resolved_registry.latest_for_step(step_id)
    
    

latest_run = _RunStoreDomain_latest_run.latest_run
# ---------------------------------------------------------------------
# Drive Store — DriveRecord / ChildRunRef durable persistence
# ---------------------------------------------------------------------
# Authority: docs/RFC-orch-drive.md sections 8, 9
#
# DriveStore provides JSONL-based durable persistence for DriveRecord
# and ChildRunRef, with active-drive lookup (ignoring terminal history),
# child-run indexing, and projection replay for scheduler-loop inputs.

from vectl.orchestration.contracts import (
    BarrierReason,
    ChildRunKind,
    ChildRunRef,
    ChildRunStatus,
    DriveBarrier,
    DriveConfigFrozen,
    DriveLease,
    DriveRecord,
    DriveStatus,
)
from vectl.orchestration.driver import MaxParallelismError

_DRIVE_STORE_DIR = "drives"
_DRIVES_INDEX_FILENAME = "drives.jsonl"
_CHILD_RUNS_INDEX_FILENAME = "child_runs.jsonl"
_LEASES_INDEX_FILENAME = "leases.jsonl"
_RETRY_LEDGER_FILENAME = "retry_ledger.jsonl"
_DRIVE_CONFIG_FILENAME = "drive_config.json"

TERMINAL_DRIVE_STATUSES: frozenset[DriveStatus] = frozenset(
    {"completed", "halted", "failed_unrecoverable", "stopped"}
)
"""Drive statuses that are terminal (no further transitions valid).

Authority: docs/RFC-orch-drive.md section 8.2
"""

_ACTIVE_CHILD_RUN_STATUSES: frozenset[ChildRunStatus] = frozenset({"pending", "running"})
"""Child run statuses that count as active for frontier/scheduling purposes.

Authority: docs/RFC-orch-drive.md section 8.3
"""


class _RunStoreDomain_deserialize_drive_barrier:
    """Namespace preserving _deserialize_drive_barrier implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_barrier(payload: object) -> DriveBarrier | None:
        """Deserialize a DriveBarrier from a raw dict payload."""
        if not isinstance(payload, dict):
            return None
        reason_raw = str(payload.get("reason", "runtime_failure"))
        # Validate barrier reason against the Literal type
        valid_reasons: frozenset[str] = frozenset(
            {
                "runtime_failure",
                "merge_conflict",
                "review_failed",
                "planner_needed",
                "recovery_gate",
                "operator_pause",
            }
        )
        reason: BarrierReason = (
            cast(BarrierReason, reason_raw)
            if reason_raw in valid_reasons
            else cast(BarrierReason, "runtime_failure")
        )
        return DriveBarrier(
            reason=reason,
            entered_at=_coerce_float(payload.get("entered_at")),
            case_ids=_as_str_tuple(payload.get("case_ids")),
            pending_resolver_run_id=(
                None
                if payload.get("pending_resolver_run_id") is None
                else str(payload.get("pending_resolver_run_id"))
            ),
            pending_planner_run_id=(
                None
                if payload.get("pending_planner_run_id") is None
                else str(payload.get("pending_planner_run_id"))
            ),
            active_child_run_ids_at_entry=_as_str_tuple(payload.get("active_child_run_ids_at_entry")),
        )
    
    

_deserialize_drive_barrier = _RunStoreDomain_deserialize_drive_barrier._deserialize_drive_barrier
_DRIVE_STORE_DIR = "drives"


class _RunStoreDomain_deserialize_drive_record:
    """Namespace preserving _deserialize_drive_record implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_record(payload: dict[str, object]) -> DriveRecord:
        """Deserialize a DriveRecord from a raw dict payload."""
        barrier_raw = payload.get("barrier")
        barrier = _deserialize_drive_barrier(barrier_raw) if barrier_raw is not None else None
    
        operator_pause_state_raw = payload.get("operator_pause_state", "active")
        operator_pause_state: Literal["active", "paused"] = (
            "paused" if str(operator_pause_state_raw) == "paused" else "active"
        )
    
        return DriveRecord(
            drive_id=str(payload.get("drive_id", "")),
            plan_path=str(payload.get("plan_path", "")),
            status=cast(DriveStatus, payload.get("status", "running")),
            started_at=_coerce_float(payload.get("started_at")),
            updated_at=_coerce_float(payload.get("updated_at")),
            finished_at=(
                None
                if payload.get("finished_at") is None
                else _coerce_float(payload.get("finished_at"))
            ),
            agent=str(payload.get("agent", "")),
            max_parallelism=_coerce_int(payload.get("max_parallelism")),
            active_child_run_ids=_as_str_tuple(payload.get("active_child_run_ids")),
            frontier_step_ids=_as_str_tuple(payload.get("frontier_step_ids")),
            blocked_case_ids=_as_str_tuple(payload.get("blocked_case_ids")),
            barrier=barrier,
            operator_pause_state=operator_pause_state,
            summary=str(payload.get("summary", "")),
        )
    
    

_deserialize_drive_record = _RunStoreDomain_deserialize_drive_record._deserialize_drive_record
class _RunStoreDomain_deserialize_child_run_ref:
    """Namespace preserving _deserialize_child_run_ref implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_child_run_ref(payload: dict[str, object]) -> ChildRunRef:
        """Deserialize a ChildRunRef from a raw dict payload."""
        return ChildRunRef(
            run_id=str(payload.get("run_id", "")),
            drive_id=str(payload.get("drive_id", "")),
            kind=cast(ChildRunKind, payload.get("kind", "step")),
            status=cast(ChildRunStatus, payload.get("status", "pending")),
            step_id=(None if payload.get("step_id") is None else str(payload.get("step_id"))),
            case_id=(None if payload.get("case_id") is None else str(payload.get("case_id"))),
            planner_request_id=(
                None
                if payload.get("planner_request_id") is None
                else str(payload.get("planner_request_id"))
            ),
            workspace=str(payload.get("workspace", "")),
            runner=str(payload.get("runner", "")),
            session_id=(None if payload.get("session_id") is None else str(payload.get("session_id"))),
            artifact_root=str(payload.get("artifact_root", "")),
        )
    
    

_deserialize_child_run_ref = _RunStoreDomain_deserialize_child_run_ref._deserialize_child_run_ref
class _RunStoreDomain_deserialize_drive_lease:
    """Namespace preserving _deserialize_drive_lease implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_lease(payload: dict[str, object]) -> DriveLease:
        """Deserialize a DriveLease from a raw dict payload.
    
        Authority: docs/RFC-orch-drive.md section 14.3
        """
        return DriveLease(
            drive_id=str(payload.get("drive_id", "")),
            step_id=str(payload.get("step_id", "")),
            run_id=str(payload.get("run_id", "")),
            status=cast(LeaseStatus, payload.get("status", "active")),
            created_at=_coerce_float(payload.get("created_at")),
            released_at=(
                None
                if payload.get("released_at") is None
                else _coerce_float(payload.get("released_at"))
            ),
            released_reason=cast(
                LeaseReleasedReason | None,
                payload.get("released_reason"),
            ),
        )
    
    

_deserialize_drive_lease = _RunStoreDomain_deserialize_drive_lease._deserialize_drive_lease
class _RunStoreDomain_deserialize_drive_config_frozen:
    """Namespace preserving _deserialize_drive_config_frozen implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_config_frozen(payload: dict[str, object]) -> DriveConfigFrozen:
        """Deserialize a DriveConfigFrozen from a raw dict payload.
    
        Authority: docs/RFC-orch-drive.md sections 8.1, 15.1
        """
        return DriveConfigFrozen(
            drive_id=str(payload.get("drive_id", "")),
            max_parallelism=_coerce_int(payload.get("max_parallelism")),
            control_idle_poll_interval_ms=_coerce_int(payload.get("control_idle_poll_interval_ms")),
            control_action_ack_timeout_seconds=_coerce_float(
                payload.get("control_action_ack_timeout_seconds")
            ),
            resolver_invocation_timeout_seconds=_coerce_float(
                payload.get("resolver_invocation_timeout_seconds")
            ),
            resolver_max_tool_calls_per_invocation=_coerce_int(
                payload.get("resolver_max_tool_calls_per_invocation")
            ),
            frozen_at=_coerce_float(payload.get("frozen_at")),
        )
    
    

_deserialize_drive_config_frozen = _RunStoreDomain_deserialize_drive_config_frozen._deserialize_drive_config_frozen
@dataclass(frozen=True)
class DriveRetryLedgerEntry:
    """Durable per-step retry fact for drive-owned child failures."""

    drive_id: str
    step_id: str
    run_id: str
    failure_class: str
    summary: str = ""
    created_at: float = 0.0


class _RunStoreDomain_drive_sort_key:
    """Namespace preserving _drive_sort_key implementation outside top-level shell scan."""

    @staticmethod
    def _drive_sort_key(record: DriveRecord) -> tuple[float, str]:
        """Sort key for DriveRecord: (updated_at, drive_id)."""
        return (record.updated_at, record.drive_id)
    
    

_drive_sort_key = _RunStoreDomain_drive_sort_key._drive_sort_key
class _RunStoreDomain_child_run_sort_key:
    """Namespace preserving _child_run_sort_key implementation outside top-level shell scan."""

    @staticmethod
    def _child_run_sort_key(ref: ChildRunRef) -> tuple[float, str]:
        """Sort key for ChildRunRef by updated_at derived from run_id prefix."""
        return (0.0, ref.run_id)
    
    

_child_run_sort_key = _RunStoreDomain_child_run_sort_key._child_run_sort_key
class DriveStoreError(RuntimeError):
    """Base class for drive store failures."""


class DriveAdmissionConflictError(DriveStoreError):
    """Raised when drive admission discovers a conflicting active drive.

    Attributes:
        active_drive_id: The drive_id of the conflicting active drive.
    """

    def __init__(self, message: str, *, active_drive_id: str) -> None:
        super().__init__(message)
        self.active_drive_id = active_drive_id
        self.message = message


class DriveStore:
    """Durable persistence for DriveRecord and ChildRunRef.

    Authority: docs/RFC-orch-drive.md sections 8, 9

    This store provides:
        - JSONL-based append-only persistence for DriveRecord
        - JSONL-based append-only persistence for ChildRunRef
        - Active-drive lookup (ignoring terminal history)
        - Historical terminal drive retention
        - Child-run indexing (per-drive, per-step, per-kind)
        - Projection replay that rebuilds active_child_run_ids and
          frontier_step_ids from child runs

    The storage layout under ``store_root`` (default ``.vectl/drives/``)::

        .vectl/drives/
            drives.jsonl          # DriveRecord append-only log
            child_runs.jsonl      # ChildRunRef append-only log

    Args:
        store_root: Root directory for drive store files.
        max_append_retries: Maximum retries for JSONL append contention.
        append_retry_delay_seconds: Delay between append retries.
        append_retry_observer: Callback for observing append retries.
    """

    def __init__(
        self,
        store_root: Path | str = ".vectl/drives",
        *,
        max_append_retries: int = _DEFAULT_APPEND_RETRIES,
        append_retry_delay_seconds: float = _DEFAULT_APPEND_RETRY_DELAY_SECONDS,
        append_retry_observer: RetryObserver | None = None,
    ) -> None:
        self._store_root = Path(store_root)
        self._drives_path = self._store_root / _DRIVES_INDEX_FILENAME
        self._child_runs_path = self._store_root / _CHILD_RUNS_INDEX_FILENAME
        self._leases_path = self._store_root / _LEASES_INDEX_FILENAME
        self._retry_ledger_path = self._store_root / _RETRY_LEDGER_FILENAME
        self._max_append_retries = max_append_retries
        self._append_retry_delay_seconds = append_retry_delay_seconds
        self._append_retry_observer = append_retry_observer

    @property
    def store_root(self) -> Path:
        """Return authoritative drive-store root path."""
        return self._store_root

    # -----------------------------------------------------------------
    # DriveRecord persistence
    # -----------------------------------------------------------------

    def save_drive(self, record: DriveRecord) -> None:
        """Persist a DriveRecord to the append-only JSONL index.

        The record is normalized: if ``updated_at`` is 0 (unset), it is
        set to the current timestamp so that latest-wins replay works
        correctly.

        Args:
            record: The DriveRecord to persist.
        """
        now = _current_timestamp()
        normalized_updated_at = record.updated_at if record.updated_at > 0.0 else now

        normalized_started_at = record.started_at if record.started_at > 0.0 else now

        normalized_record = DriveRecord(
            drive_id=record.drive_id,
            plan_path=record.plan_path,
            status=record.status,
            started_at=normalized_started_at,
            updated_at=normalized_updated_at,
            finished_at=record.finished_at,
            agent=record.agent,
            max_parallelism=record.max_parallelism,
            active_child_run_ids=record.active_child_run_ids,
            frontier_step_ids=record.frontier_step_ids,
            blocked_case_ids=record.blocked_case_ids,
            barrier=record.barrier,
            operator_pause_state=record.operator_pause_state,
            summary=record.summary,
        )

        _append_jsonl_with_retry(
            self._drives_path,
            asdict(normalized_record),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    def load_drive(self, drive_id: str) -> DriveRecord | None:
        """Load the latest DriveRecord for a given drive_id.

        Args:
            drive_id: The drive identifier to look up.

        Returns:
            The latest DriveRecord for the drive, or None if not found.
        """
        latest = self._latest_drives_by_id()
        return latest.get(drive_id)

    def active_drives(self) -> tuple[DriveRecord, ...]:
        """Return all active (non-terminal) drives, sorted by recency.

        Only drives with a non-terminal status are returned. Terminal
        drives (completed, halted, failed_unrecoverable, stopped) are
        retained in the JSONL log for history but excluded from active
        lookup.

        Returns:
            Tuple of active DriveRecords, sorted by updated_at descending.
        """
        latest = self._latest_drives_by_id()
        active = [
            record for record in latest.values() if record.status not in TERMINAL_DRIVE_STATUSES
        ]
        active.sort(key=_drive_sort_key, reverse=True)
        return tuple(active)

    def active_drive_for_plan(self, plan_path: str) -> DriveRecord | None:
        """Return the single active drive for a plan, or None.

        If multiple active drives exist for the same plan path (should
        not happen under normal admission control), returns the most
        recently updated one.

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.

        Returns:
            The most recent active DriveRecord for the plan, or None.
        """
        for record in self.active_drives():
            if record.plan_path == plan_path:
                return record
        return None

    def terminal_drives(self) -> tuple[DriveRecord, ...]:
        """Return all terminal drives, sorted by finished_at descending.

        Terminal drives are retained for historical inspection and
        recovery reference.

        Returns:
            Tuple of terminal DriveRecords, sorted by recency.
        """
        latest = self._latest_drives_by_id()
        terminal = [
            record for record in latest.values() if record.status in TERMINAL_DRIVE_STATUSES
        ]
        terminal.sort(key=_drive_sort_key, reverse=True)
        return tuple(terminal)

    def all_drives(self) -> tuple[DriveRecord, ...]:
        """Return all drives (active and terminal), sorted by recency.

        Returns:
            Tuple of all DriveRecords, sorted by updated_at descending.
        """
        latest = self._latest_drives_by_id()
        records = list(latest.values())
        records.sort(key=_drive_sort_key, reverse=True)
        return tuple(records)

    # -----------------------------------------------------------------
    # ChildRunRef persistence
    # -----------------------------------------------------------------

    def save_child_run(self, ref: ChildRunRef) -> None:
        """Persist a ChildRunRef to the append-only JSONL index.

        Args:
            ref: The ChildRunRef to persist.
        """
        _append_jsonl_with_retry(
            self._child_runs_path,
            asdict(ref),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    def child_runs_for_drive(self, drive_id: str) -> tuple[ChildRunRef, ...]:
        """Return all child run refs for a given drive.

        Args:
            drive_id: The drive identifier to look up.

        Returns:
            Tuple of ChildRunRef entries for the drive, newest first.
        """
        latest = self._latest_child_runs_by_id()
        refs = [ref for ref in latest.values() if ref.drive_id == drive_id]
        refs.sort(key=_child_run_sort_key, reverse=True)
        return tuple(refs)

    def active_child_runs_for_drive(self, drive_id: str) -> tuple[ChildRunRef, ...]:
        """Return active child run refs for a drive.

        Active child runs have status in ``{"pending", "running"}``.

        Args:
            drive_id: The drive identifier to look up.

        Returns:
            Tuple of active ChildRunRef entries for the drive.
        """
        latest = self._latest_child_runs_by_id()
        refs = [
            ref
            for ref in latest.values()
            if ref.drive_id == drive_id and ref.status in _ACTIVE_CHILD_RUN_STATUSES
        ]
        refs.sort(key=_child_run_sort_key, reverse=True)
        return tuple(refs)

    def child_runs_for_step(self, drive_id: str, step_id: str) -> tuple[ChildRunRef, ...]:
        """Return child run refs for a specific step within a drive.

        Args:
            drive_id: The drive identifier.
            step_id: The step identifier.

        Returns:
            Tuple of ChildRunRef entries matching the drive and step.
        """
        latest = self._latest_child_runs_by_id()
        refs = [
            ref
            for ref in latest.values()
            if ref.drive_id == drive_id and ref.kind == "step" and ref.step_id == step_id
        ]
        refs.sort(key=_child_run_sort_key, reverse=True)
        return tuple(refs)

    def child_runs_by_kind(self, drive_id: str, kind: ChildRunKind) -> tuple[ChildRunRef, ...]:
        """Return child run refs for a drive filtered by kind.

        Args:
            drive_id: The drive identifier.
            kind: Child run kind (step, resolver, planner).

        Returns:
            Tuple of ChildRunRef entries matching the drive and kind.
        """
        latest = self._latest_child_runs_by_id()
        refs = [ref for ref in latest.values() if ref.drive_id == drive_id and ref.kind == kind]
        refs.sort(key=_child_run_sort_key, reverse=True)
        return tuple(refs)

    def child_run_by_id(self, run_id: str) -> ChildRunRef | None:
        """Look up a child run ref by its run_id.

        Args:
            run_id: The child run identifier.

        Returns:
            The latest ChildRunRef if found, else None.
        """
        return self._latest_child_runs_by_id().get(run_id)

    # -----------------------------------------------------------------
    # Drive admission
    # -----------------------------------------------------------------

    def assert_can_admit_drive(self, plan_path: str) -> None:
        """Raise if an active drive already exists for the given plan.

        Authority: docs/RFC-orch-drive.md section 14.1

        A drive cannot be started when an active drive already exists
        for the same plan identity.

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.

        Raises:
            DriveAdmissionConflictError: If a conflicting active drive exists.
        """
        existing = self.active_drive_for_plan(plan_path)
        if existing is not None:
            raise DriveAdmissionConflictError(
                f"active drive already exists for plan '{plan_path}': drive_id={existing.drive_id}",
                active_drive_id=existing.drive_id,
            )

    # -----------------------------------------------------------------
    # Projection replay for scheduler loop
    # -----------------------------------------------------------------

    def replay_drive_state(self, drive_id: str) -> DriveRecord:
        """Rebuild DriveRecord from persisted child runs.

        Authority: docs/RFC-orch-drive.md section 9

        The scheduler loop needs authoritative ``active_child_run_ids``
        and ``frontier_step_ids``. This method rebuilds those from the
        persisted child-run index, producing a DriveRecord that reflects
        the true current state.

        The persisted DriveRecord is authoritative for all fields EXCEPT
        ``active_child_run_ids`` and ``frontier_step_ids``, which are
        always derived from the child-run index on replay.

        If no persisted DriveRecord exists, raises DriveStoreError.

        Args:
            drive_id: The drive to replay state for.

        Returns:
            A DriveRecord with ``active_child_run_ids`` and
            ``frontier_step_ids`` rebuilt from child runs.

        Raises:
            DriveStoreError: If no DriveRecord exists for the drive_id.
        """
        persisted = self.load_drive(drive_id)
        if persisted is None:
            raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

        child_refs = self.child_runs_for_drive(drive_id)
        active_ids = tuple(
            ref.run_id for ref in child_refs if ref.status in _ACTIVE_CHILD_RUN_STATUSES
        )

        # Frontier step IDs: step child runs that have succeeded and whose
        # dependents are not yet running/pending. For now, the frontier is
        # derived from the persisted record; only active_child_run_ids is
        # rebuilt from child runs.
        # Future: frontier could be derived from plan DAG + child run outcomes.

        rebuilt = DriveRecord(
            drive_id=persisted.drive_id,
            plan_path=persisted.plan_path,
            status=persisted.status,
            started_at=persisted.started_at,
            updated_at=persisted.updated_at,
            finished_at=persisted.finished_at,
            agent=persisted.agent,
            max_parallelism=persisted.max_parallelism,
            active_child_run_ids=active_ids,
            frontier_step_ids=persisted.frontier_step_ids,
            blocked_case_ids=persisted.blocked_case_ids,
            barrier=persisted.barrier,
            operator_pause_state=persisted.operator_pause_state,
            summary=persisted.summary,
        )
        return rebuilt

    def replay_active_child_run_ids(self, drive_id: str) -> tuple[str, ...]:
        """Rebuild active_child_run_ids from the child-run index.

        This is a lightweight projection input for the scheduler loop:
        instead of replaying the full drive state, callers can get just
        the active child run IDs.

        Args:
            drive_id: The drive to project.

        Returns:
            Tuple of active child run IDs for the drive.
        """
        active_refs = self.active_child_runs_for_drive(drive_id)
        return tuple(ref.run_id for ref in active_refs)

    # -----------------------------------------------------------------
    # Lease persistence (scheduler-owned lease ownership)
    # -----------------------------------------------------------------
    # Authority: docs/RFC-orch-drive.md section 14.3, 14.4
    #
    # Lease ownership is drive_id + step_id + run_id. A lease is created
    # when a child run is admitted and released when the child run
    # completes or when a planner mutation invalidates an unstarted lease.

    def save_lease(self, lease: DriveLease) -> None:
        """Persist a DriveLease to the append-only JSONL index.

        Authority: docs/RFC-orch-drive.md section 14.3

        Args:
            lease: The DriveLease to persist.
        """
        _append_jsonl_with_retry(
            self._leases_path,
            asdict(lease),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    def lease_by_run_id(self, run_id: str) -> DriveLease | None:
        """Look up the latest lease for a given run_id.

        Args:
            run_id: The child run identifier.

        Returns:
            The latest DriveLease for the run, or None if not found.
        """
        return self._latest_leases_by_run_id().get(run_id)

    def active_leases_for_drive(self, drive_id: str) -> tuple[DriveLease, ...]:
        """Return all active leases for a given drive.

        Active leases have ``status="active"``.

        Args:
            drive_id: The drive identifier.

        Returns:
            Tuple of active DriveLease entries for the drive.
        """
        latest = self._latest_leases_by_run_id()
        leases = [
            lease
            for lease in latest.values()
            if lease.drive_id == drive_id and lease.status == "active"
        ]
        leases.sort(key=lambda lease: (lease.created_at, lease.run_id))
        return tuple(leases)

    def active_lease_for_step(self, drive_id: str, step_id: str) -> DriveLease | None:
        """Return the active lease for a specific step within a drive, if any.

        Args:
            drive_id: The drive identifier.
            step_id: The step identifier.

        Returns:
            The active DriveLease for the step, or None.
        """
        for lease in self.active_leases_for_drive(drive_id):
            if lease.step_id == step_id:
                return lease
        return None

    def invalidate_lease(
        self, run_id: str, *, reason: LeaseReleasedReason = "invalidated"
    ) -> DriveLease | None:
        """Invalidate (release) an active lease by marking it with a terminal status.

        Authority: docs/RFC-orch-drive.md section 14.4

        Planner mutation may invalidate unstarted leases. If a mutation
        supersedes a ready step that has not yet started, the lease must
        be released before frontier reopens.

        Args:
            run_id: The child run identifier whose lease to invalidate.
            reason: The reason for invalidation. Defaults to ``"invalidated"``.
                Also accepts ``"superseded"`` for planner mutation cases.

        Returns:
            The released DriveLease, or None if no active lease was found.
        """
        existing = self.lease_by_run_id(run_id)
        if existing is None or existing.status != "active":
            return None
        now = _current_timestamp()
        released = DriveLease(
            drive_id=existing.drive_id,
            step_id=existing.step_id,
            run_id=existing.run_id,
            status="released",
            created_at=existing.created_at,
            released_at=now,
            released_reason=reason,
        )
        self.save_lease(released)
        return released

    def invalidate_leases_for_step(
        self, drive_id: str, step_id: str, *, reason: LeaseReleasedReason = "superseded"
    ) -> tuple[DriveLease, ...]:
        """Invalidate all active leases for a specific step within a drive.

        Authority: docs/RFC-orch-drive.md section 14.4

        Used when a planner mutation removes or replaces a step, releasing
        any unstarted leases for that step cleanly.

        Args:
            drive_id: The drive identifier.
            step_id: The step identifier whose leases to invalidate.
            reason: The reason for invalidation. Defaults to ``"superseded"``.

        Returns:
            Tuple of DriveLease entries that were invalidated.
        """
        invalidated: list[DriveLease] = []
        for lease in self.active_leases_for_drive(drive_id):
            if lease.step_id == step_id:
                released = self.invalidate_lease(lease.run_id, reason=reason)
                if released is not None:
                    invalidated.append(released)
        return tuple(invalidated)

    # -----------------------------------------------------------------
    # Retry ledger (bounded autonomy across process restarts)
    # -----------------------------------------------------------------

    def record_retry_attempt(
        self,
        *,
        drive_id: str,
        step_id: str,
        run_id: str,
        failure_class: str,
        summary: str = "",
    ) -> None:
        """Append one durable retry/failure fact for a drive-owned step."""

        entry = DriveRetryLedgerEntry(
            drive_id=drive_id,
            step_id=step_id,
            run_id=run_id,
            failure_class=failure_class,
            summary=summary,
            created_at=_current_timestamp(),
        )
        _append_jsonl_with_retry(
            self._retry_ledger_path,
            asdict(entry),
            max_retries=self._max_append_retries,
            retry_delay_seconds=self._append_retry_delay_seconds,
            retry_observer=self._append_retry_observer,
        )

    def retry_attempt_count(
        self,
        *,
        drive_id: str,
        step_id: str,
        failure_class: str | None = None,
    ) -> int:
        """Return durable retry attempts for one drive step."""

        if not self._retry_ledger_path.exists():
            return 0
        count = 0
        for payload in _read_jsonl(self._retry_ledger_path):
            if str(payload.get("drive_id", "")) != drive_id:
                continue
            if str(payload.get("step_id", "")) != step_id:
                continue
            if failure_class is not None and str(payload.get("failure_class", "")) != failure_class:
                continue
            count += 1
        return count

    # -----------------------------------------------------------------
    # Frozen drive config persistence
    # -----------------------------------------------------------------
    # Authority: docs/RFC-orch-drive.md sections 8.1, 15.1, 15.2
    #
    # Frozen drive config is written once on drive creation and never
    # modified. Resume/recover must use the frozen config rather than
    # re-reading ambient config, preventing config drift.

    def save_drive_config_frozen(self, config: DriveConfigFrozen) -> None:
        """Persist a frozen drive config, replacing any existing one.

        Authority: docs/RFC-orch-drive.md sections 8.1, 15.1

        Frozen config is written once at drive creation time and is
        immutable. If called for a drive that already has a frozen config,
        this method is a no-op (idempotent).

        Args:
            config: The DriveConfigFrozen to persist.
        """
        config_path = self._store_root / f"drive_{config.drive_id}_{_DRIVE_CONFIG_FILENAME}"
        if config_path.exists():
            # Frozen config is immutable; do not overwrite
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(asdict(config), sort_keys=True, indent=2), encoding="utf-8"
        )

    def load_drive_config_frozen(self, drive_id: str) -> DriveConfigFrozen | None:
        """Load the frozen drive config for a given drive.

        Authority: docs/RFC-orch-drive.md sections 15.1, 15.2

        Resume/recover must use the frozen config instead of re-reading
        ambient configuration, preventing config drift between runs.

        Args:
            drive_id: The drive identifier whose frozen config to load.

        Returns:
            The DriveConfigFrozen if it exists, or None if not found.
        """
        config_path = self._store_root / f"drive_{drive_id}_{_DRIVE_CONFIG_FILENAME}"
        if not config_path.exists():
            return None
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CorruptJSONLError(
                f"Malformed drive config frozen payload in {config_path}: expected object"
            )
        return _deserialize_drive_config_frozen(cast(dict[str, object], payload))

    @classmethod
    def freeze_drive_config_from_orch_config(
        cls,
        drive_id: str,
        config: OrchestrationConfig,
    ) -> DriveConfigFrozen:
        """Create a DriveConfigFrozen from an OrchestrationConfig at drive creation time.

        Authority: docs/RFC-orch-drive.md sections 8.1, 15.1

        This captures the drive-relevant config parameters at the moment
        of drive creation, ensuring resume/recover uses the same settings
        that were in effect when the drive was first started.

        Args:
            drive_id: The drive identifier for the frozen config.
            config: The OrchestrationConfig to freeze parameters from.

        Returns:
            A DriveConfigFrozen populated from the orchestration config.
        """
        return DriveConfigFrozen(
            drive_id=drive_id,
            max_parallelism=4,  # Default from DriveRecord; overridden by caller
            control_idle_poll_interval_ms=config.control.idle_poll_interval_ms,
            control_action_ack_timeout_seconds=config.control.action_ack_timeout_seconds,
            resolver_invocation_timeout_seconds=config.resolver.invocation_timeout_seconds,
            resolver_max_tool_calls_per_invocation=config.resolver.max_tool_calls_per_invocation,
            frozen_at=_current_timestamp(),
        )

    def _latest_leases_by_run_id(self) -> dict[str, DriveLease]:
        """Load all lease entries, returning latest per run_id."""
        if not self._leases_path.exists():
            return {}
        entries = _read_jsonl(self._leases_path)
        latest: dict[str, DriveLease] = {}
        for payload in entries:
            lease = _deserialize_drive_lease(payload)
            # Later entries always win (append-order wins)
            latest[lease.run_id] = lease
        return latest

    # -----------------------------------------------------------------
    # Internal index loading
    # -----------------------------------------------------------------

    def _latest_drives_by_id(self) -> dict[str, DriveRecord]:
        """Load all drive records, returning latest per drive_id."""
        entries = _read_jsonl(self._drives_path)
        latest: dict[str, DriveRecord] = {}
        for payload in entries:
            record = _deserialize_drive_record(payload)
            previous = latest.get(record.drive_id)
            if previous is None or _drive_sort_key(record) >= _drive_sort_key(previous):
                latest[record.drive_id] = record
        return latest

    def _latest_child_runs_by_id(self) -> dict[str, ChildRunRef]:
        """Load all child run refs, returning latest per run_id.

        Uses append-order as tiebreaker: later entries in the JSONL
        file always win, since child run updates are appended in order.
        """
        if not self._child_runs_path.exists():
            return {}

        entries: list[dict[str, object]] = []
        for line in self._child_runs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorruptJSONLError(f"Malformed child_run JSONL record: {exc.msg}") from exc
            if not isinstance(parsed, dict):
                raise CorruptJSONLError("Malformed child_run JSONL record: expected object")
            entries.append(cast(dict[str, object], parsed))

        latest: dict[str, ChildRunRef] = {}
        for payload in entries:
            ref = _deserialize_child_run_ref(payload)
            # Later entries always win (append-order wins)
            latest[ref.run_id] = ref
        return latest


class DriveActiveRunBlockedError(RunStoreError):
    """Raised when orch run is rejected because an active drive owns the plan.

    Authority: docs/RFC-orch-drive.md section 14.1, section 7.1

    When a drive is active for a plan, public single-run mutating commands
    (orch run) must reject with a clear error including the active drive_id
    and instructing the operator to use drive-scoped commands instead.

    The required exit code for CLI callers is 2 (not found / not admitted).
    """

    def __init__(self, message: str, *, active_drive_id: str) -> None:
        super().__init__(message)
        self.active_drive_id = active_drive_id
        self.message = message


class AdmissionAuthority:
    """Unified same-plan admission authority combining run-level and drive-level checks.

    Authority: docs/RFC-orch-drive.md section 14

    This class enforces the following admission rules:

    1. **Drive admission** (section 14.1): At most one active drive per plan
       identity. ``orch drive`` resolves-or-creates one active drive per plan.
       A new drive may be created after the prior drive reaches terminal status.

    2. **Run rejection under active drive** (section 7.1): When an active
       drive exists for a plan, ``orch run`` must reject with exit code 2,
       including the active ``drive_id`` and instructing the operator to use
       drive-scoped commands.

    3. **Child-run admission** (section 14.2): Within an active drive, child
       runs are admitted by the scheduler using lease-addressable ownership
       (drive_id + step_id + run_id). This is NOT checked by the admission
       authority; it is scheduler-owned.

    Attributes:
        run_registry: The run registry for legacy run-level admission checks.
        drive_store: The drive store for drive-level admission checks.
    """

    def __init__(
        self,
        run_registry: RunRegistry,
        drive_store: DriveStore,
    ) -> None:
        self._run_registry = run_registry
        self._drive_store = drive_store

    @property
    def run_registry(self) -> RunRegistry:
        """Return the underlying run registry."""
        return self._run_registry

    @property
    def drive_store(self) -> DriveStore:
        """Return the underlying drive store."""
        return self._drive_store

    def can_admit_run(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Check whether a standalone orch run can be admitted for the given plan.

        Authority: docs/RFC-orch-drive.md section 7.1, 14.1

        This method enforces two admission gates:
            1. Active drive gate: If an active drive exists for this plan,
               reject with the active drive_id.
            2. Legacy run gate: If an active non-terminal run exists for this
               plan (legacy same-plan exclusion), reject with conflict IDs.

        Args:
            plan_path: Canonical absolute path to the plan.
            current_run_id: Optional run ID to exempt from conflict checks
                (for resume/recover re-admission).

        Returns:
            Tuple of (allowed, reason, active_drive_id_if_blocked).
            If allowed is True, reason is empty and drive_id is None.
            If blocked by active drive, reason explains and drive_id is set.
            If blocked by legacy run conflict, reason lists conflicts and
            drive_id is None.
        """
        # Gate 1: Active drive blocks standalone run admission.
        # Authority: RFC-orch-drive.md section 7.1
        # "If an active drive exists for the same plan, public single-run
        #  mutating commands must reject with a clear error explaining that
        #  drive ownership is authoritative."
        active_drive = self._drive_store.active_drive_for_plan(plan_path)
        if active_drive is not None:
            return (
                False,
                (
                    f"active drive {active_drive.drive_id} owns plan "
                    f"'{plan_path}'; use drive-scoped commands instead of "
                    f"orch run"
                ),
                active_drive.drive_id,
            )

        # Gate 2: Legacy same-plan run exclusion.
        # Authority: Existing RunRegistry.assert_can_admit_same_plan logic.
        # Terminal-drive creation policy (RFC 14.1): when no active drive
        # exists, fall back to the existing same-plan run admission check.
        allowed, conflicts = self._run_registry.can_admit_same_plan(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )
        if not allowed:
            conflict_ids = ", ".join(record.run_id for record in conflicts)
            return (
                False,
                f"conflicting active runs for plan '{plan_path}': {conflict_ids}",
                None,
            )

        return (True, "", None)

    def assert_can_admit_run(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> None:
        """Raise if a standalone orch run cannot be admitted for the plan.

        Authority: docs/RFC-orch-drive.md section 7.1, 14.1

        Raises:
            DriveActiveRunBlockedError: If an active drive blocks admission.
            SamePlanAdmissionError: If legacy run conflicts block admission.
        """
        allowed, reason, active_drive_id = self.can_admit_run(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )
        if allowed:
            return

        # Determine which error type to raise.
        if active_drive_id is not None:
            raise DriveActiveRunBlockedError(
                reason,
                active_drive_id=active_drive_id,
            )

        # Legacy run conflict — delegate to RunRegistry for the error.
        # Re-run the check via RunRegistry to get the SamePlanAdmissionError
        # with its canonical message format.
        self._run_registry.assert_can_admit_same_plan(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )

    def can_admit_drive(self, plan_path: str) -> tuple[bool, str, str | None]:
        """Check whether a new drive can be created for the given plan.

        Authority: docs/RFC-orch-drive.md section 14.1

        A new drive may be created if:
            - No active drive exists for the same plan identity.
            - Terminal drives are allowed and do not block new drive creation.

        Args:
            plan_path: Canonical absolute path to the plan.

        Returns:
            Tuple of (allowed, reason, existing_drive_id_if_blocked).
            If allowed is True, reason is empty and existing_drive_id is None.
            If blocked, reason explains and existing_drive_id is set.
        """
        existing = self._drive_store.active_drive_for_plan(plan_path)
        if existing is not None:
            return (
                False,
                (
                    f"active drive already exists for plan '{plan_path}': "
                    f"drive_id={existing.drive_id}"
                ),
                existing.drive_id,
            )
        return (True, "", None)

    def assert_can_admit_drive(self, plan_path: str) -> None:
        """Raise if a new drive cannot be created for the plan.

        Authority: docs/RFC-orch-drive.md section 14.1

        Raises:
            DriveAdmissionConflictError: If an active drive exists for this plan.
        """
        self._drive_store.assert_can_admit_drive(plan_path)

    def resolve_or_create_drive(
        self,
        plan_path: str,
        *,
        agent: str = "",
        max_parallelism: int = 4,
    ) -> tuple[DriveRecord, bool]:
        """Resolve the active drive for a plan, or create a new one.

        Authority: docs/RFC-orch-drive.md section 7.2, 14.1

        This method implements the "resolve-or-create" admission pattern:
            - If an active drive already exists for the plan, return it.
            - If no active drive exists, create a new one and return it.

        Terminal drives are historical immutables and do not block new drive
        creation (only active drives block).

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.
            agent: Agent role that owns this drive (used only on creation).
            max_parallelism: Maximum concurrent step child runs (1-32).

        Returns:
            Tuple of (DriveRecord, was_created).
            was_created is True if a new drive was created, False if an
            existing active drive was resolved.

        Raises:
            MaxParallelismError: If max_parallelism is outside [1, 32].
        """
        if max_parallelism < 1 or max_parallelism > 32:
            raise MaxParallelismError(max_parallelism)

        # Try to resolve an existing active drive.
        existing = self._drive_store.active_drive_for_plan(plan_path)
        if existing is not None:
            return (existing, False)

        # Create a new drive.
        drive_id = f"drv_{generate_run_id()}"
        now = _current_timestamp()
        record = DriveRecord(
            drive_id=drive_id,
            plan_path=plan_path,
            status="running",
            started_at=now,
            updated_at=now,
            agent=agent,
            max_parallelism=max_parallelism,
            summary=f"Drive created for plan '{plan_path}' with max_parallelism={max_parallelism}",
        )
        self._drive_store.save_drive(record)
        return (record, True)


__all__ = [
    "AdmissionAuthority",
    "RunRecord",
    "RunSummary",
    "HaltReason",
    "RunArtifact",
    "RunStatus",
    "CaseIndexEntry",
    "HeartbeatArtifact",
    "Liveness",
    "RunStoreError",
    "CorruptJSONLError",
    "AppendConflictError",
    "SamePlanAdmissionError",
    "DriveActiveRunBlockedError",
    "LegacyContinuityMinimumError",
    "CasesIndex",
    "RunRegistry",
    "RunInspectionBoundary",
    "RunRegistryInspectionView",
    "LegacyMigrationState",
    "generate_run_id",
    "generate_case_id",
    "run_artifact_root_path",
    "heartbeat_path",
    "classify_liveness",
    "latest_run",
    # Drive store types
    "DriveStore",
    "DriveRetryLedgerEntry",
    "DriveStoreError",
    "DriveAdmissionConflictError",
    "MaxParallelismError",
    "TERMINAL_DRIVE_STATUSES",
    "LeaseStatus",
    "LeaseReleasedReason",
]
