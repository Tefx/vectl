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

if TYPE_CHECKING:
    pass


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
RetryObserver = Callable[[Path, int, Exception], None]

_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({"success", "fail"})
_NON_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset({"pending", "running", "stall", "paused"})
_DEFAULT_APPEND_RETRIES = 3
_DEFAULT_APPEND_RETRY_DELAY_SECONDS = 0.02
_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ADMISSION_BLOCKING_LEGACY_STATES: frozenset[LegacyMigrationState] = frozenset(
    {"parallel", "preferred", "deprecated"}
)


def _encode_crockford_32(value: int, length: int) -> str:
    """Encode an integer into fixed-width Crockford Base32 text."""
    encoded_chars: list[str] = []
    for _ in range(length):
        encoded_chars.append(_CROCKFORD32[value & 31])
        value >>= 5
    encoded_chars.reverse()
    return "".join(encoded_chars)


def _current_timestamp(now: datetime | None = None) -> float:
    if now is None:
        return time.time()
    return now.timestamp()


def generate_run_id(now: datetime | None = None) -> str:
    """Generate lexicographically sortable ULID text for run identities."""
    timestamp_seconds = _current_timestamp(now)
    millis = int(timestamp_seconds * 1000)
    if millis < 0:
        raise ValueError("ULID timestamp must be >= 0")
    timestamp_component = _encode_crockford_32(millis, 10)
    random_component = _encode_crockford_32(random.getrandbits(80), 16)
    return f"{timestamp_component}{random_component}"


def generate_case_id(now: datetime | None = None) -> str:
    """Generate globally unique ``case-<ULID>`` identifier."""
    return f"case-{generate_run_id(now=now)}"


def run_artifact_root_path(runs_root: Path | str, run_id: str) -> Path:
    """Resolve artifact root directory for a run ID."""
    return Path(runs_root) / run_id


def heartbeat_path(runs_root: Path | str, run_id: str) -> Path:
    """Resolve heartbeat artifact path for a run ID."""
    return run_artifact_root_path(runs_root=runs_root, run_id=run_id) / "heartbeat.json"


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


def _status_is_non_terminal(status: RunStatus | None) -> bool:
    if status is None:
        return False
    return status not in _TERMINAL_STATUSES


def _run_sort_key(record: RunRecord) -> tuple[float, str]:
    updated_at = record.updated_at
    if updated_at is None:
        updated_at = record.started_at
    if updated_at is None:
        updated_at = record.created_at
    if updated_at is None:
        updated_at = 0.0
    return (updated_at, record.run_id)


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


def _append_jsonl_once(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


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


def _deserialize_run_record(payload: dict[str, object]) -> RunRecord:
    return RunRecord(
        run_id=str(payload.get("run_id", "")),
        step_id=str(payload.get("step_id", "")),
        plan_path=(None if payload.get("plan_path") is None else str(payload.get("plan_path"))),
        agent=(None if payload.get("agent") is None else str(payload.get("agent"))),
        status=cast(RunStatus | None, payload.get("status")),
        created_at=cast(float | None, payload.get("created_at")),
        started_at=cast(float | None, payload.get("started_at")),
        updated_at=cast(float | None, payload.get("updated_at")),
        finished_at=cast(float | None, payload.get("finished_at")),
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


def _deserialize_run_artifacts(payload: object) -> tuple[RunArtifact, ...]:
    if not isinstance(payload, list):
        return ()
    artifacts: list[RunArtifact] = []
    for item in payload:
        artifact = _deserialize_run_artifact(item)
        if artifact is not None:
            artifacts.append(artifact)
    return tuple(artifacts)


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


def _deserialize_operator_notifications(payload: object) -> tuple[OperatorNotificationRecord, ...]:
    if not isinstance(payload, list):
        return ()
    records: list[OperatorNotificationRecord] = []
    for item in payload:
        record = _deserialize_operator_notification(item)
        if record is not None:
            records.append(record)
    return tuple(records)


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


def _coerce_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip() != "":
        return float(value)
    return 0.0


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip() != "":
        return int(float(value))
    return 0


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _legacy_journal_entry(continuity_artifacts: dict[str, object]) -> dict[str, object] | None:
    journal_payload = continuity_artifacts.get("journal")
    if isinstance(journal_payload, dict):
        return cast(dict[str, object], journal_payload)
    if isinstance(journal_payload, list) and journal_payload:
        first = journal_payload[0]
        if isinstance(first, dict):
            return cast(dict[str, object], first)
    return None


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


def _legacy_state_blocks_same_plan(record: RunRecord) -> bool:
    if record.source != "legacy_imported":
        return _status_is_non_terminal(record.status)
    if record.legacy_migration_state not in _ADMISSION_BLOCKING_LEGACY_STATES:
        return False
    return _status_is_non_terminal(record.status)


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


__all__ = [
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
]
