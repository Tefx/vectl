from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

from vectl.core.orchestration.event_schema import (
    canonical_json as _core_canonical_json,
    denormalize_step_key as _core_denormalize_step_key,
    is_hex_sha256 as _core_is_hex_sha256,
    normalize_step_key as _core_normalize_step_key,
    validate_payload_shape,
)


@dataclass(frozen=True)
class EventSchema:

    required_payload_keys: tuple[str, ...] = ()
    optional_payload_keys: tuple[str, ...] = ()
    version: int = 1


OrchestrationEventKind = Literal[
    "run_started",
    "run_status_changed",
    "run_final",
    "control_dispatch",
    "control_wait",
    "control_done",
    "control_resolve",
    "control_pause",
    "control_unpause",
    "control_stop",
    "roster_claim",
    "roster_release",
    "roster_register",
    "runtime_prepare",
    "runtime_start",
    "runtime_collect",
    "runtime_cleanup",
    "resolver_invoked",
    "resolver_returned",
    "projection_latest_updated",
    "projection_summary_updated",
    "projection_metrics_updated",
    "operator_case_opened",
    "operator_case_resolved",
    "operator_action_requested",
    # Drive event family (Authority: §10.4, RFC-orch-drive §16.3)
    "drive_started",
    "drive_status_changed",
    "drive_barrier_entered",
    "drive_barrier_cleared",
    "planner_invoked",
    "planner_applied",
    "child_run_admitted",
    "child_run_final",
    "drive_final",
]


DriveEventKind = Literal[
    "drive_started",
    "drive_status_changed",
    "drive_barrier_entered",
    "drive_barrier_cleared",
    "planner_invoked",
    "planner_applied",
    "resolver_invoked",
    "resolver_returned",
    "child_run_admitted",
    "child_run_final",
    "drive_final",
]

DRIVE_EVENT_KINDS: Final[frozenset[str]] = frozenset(
    [
        "drive_started",
        "drive_status_changed",
        "drive_barrier_entered",
        "drive_barrier_cleared",
        "planner_invoked",
        "planner_applied",
        "resolver_invoked",
        "resolver_returned",
        "child_run_admitted",
        "child_run_final",
        "drive_final",
    ]
)


CANONICAL_EVENT_REGISTRY: Final[dict[str, EventSchema]] = {
    "run_started": EventSchema(required_payload_keys=("run_id", "plan_path")),
    "run_status_changed": EventSchema(required_payload_keys=("run_id", "status")),
    "run_final": EventSchema(required_payload_keys=("run_id", "final_status")),
    "control_dispatch": EventSchema(required_payload_keys=("step_id", "agent")),
    "control_wait": EventSchema(required_payload_keys=("step_id", "reason")),
    "control_done": EventSchema(required_payload_keys=("step_id", "status")),
    "control_resolve": EventSchema(required_payload_keys=("step_id", "case_id")),
    "roster_claim": EventSchema(required_payload_keys=("step_id", "agent")),
    "roster_release": EventSchema(required_payload_keys=("step_id", "agent")),
    "roster_register": EventSchema(required_payload_keys=("agent",)),
    "runtime_prepare": EventSchema(required_payload_keys=("step_id", "workspace")),
    "runtime_start": EventSchema(required_payload_keys=("step_id", "task_id")),
    "runtime_collect": EventSchema(required_payload_keys=("step_id", "task_id")),
    "runtime_cleanup": EventSchema(required_payload_keys=("step_id", "workspace")),
    "resolver_invoked": EventSchema(required_payload_keys=("step_id", "case_id")),
    "resolver_returned": EventSchema(required_payload_keys=("step_id", "status")),
    "projection_latest_updated": EventSchema(required_payload_keys=("run_id", "event_seq")),
    "projection_summary_updated": EventSchema(required_payload_keys=("run_id", "event_seq")),
    "projection_metrics_updated": EventSchema(required_payload_keys=("run_id", "event_seq")),
    "operator_case_opened": EventSchema(
        required_payload_keys=("case_id", "step_id"),
        optional_payload_keys=(
            "resolution_status",
            "summary",
            "operator_message",
            "evidence_refs",
            "open_case_delta",
        ),
    ),
    "operator_case_resolved": EventSchema(
        required_payload_keys=("case_id", "resolution"),
        optional_payload_keys=("open_case_delta",),
    ),
    "operator_action_requested": EventSchema(required_payload_keys=("case_id", "action")),
    # Drive event family — Authority: §10.4, RFC-orch-drive §16.3
    "drive_started": EventSchema(
        required_payload_keys=("drive_id", "plan_path"),
        optional_payload_keys=("agent", "max_parallelism"),
    ),
    "drive_status_changed": EventSchema(
        required_payload_keys=("drive_id", "status"),
        optional_payload_keys=("previous_status", "reason"),
    ),
    "drive_barrier_entered": EventSchema(
        required_payload_keys=("drive_id", "reason"),
        optional_payload_keys=("case_ids", "active_child_run_ids"),
    ),
    "drive_barrier_cleared": EventSchema(
        required_payload_keys=("drive_id", "resolution"),
        optional_payload_keys=("resolver_status", "planner_status"),
    ),
    "planner_invoked": EventSchema(
        required_payload_keys=("drive_id", "step_id"),
        optional_payload_keys=("planner_request_id", "affected_steps"),
    ),
    "planner_applied": EventSchema(
        required_payload_keys=("drive_id", "status"),
        optional_payload_keys=("mutations_count", "affected_steps"),
    ),
    "child_run_admitted": EventSchema(
        required_payload_keys=("drive_id", "run_id", "kind"),
        optional_payload_keys=("step_id", "case_id"),
    ),
    "child_run_final": EventSchema(
        required_payload_keys=("drive_id", "run_id", "status"),
        optional_payload_keys=("step_id", "case_id"),
    ),
    "drive_final": EventSchema(
        required_payload_keys=("drive_id", "final_status"),
        optional_payload_keys=("summary", "child_run_count"),
    ),
}


class EventValidationError(ValueError):
    ...


class EventCorruptionError(ValueError):

    def __init__(self, line_no: int, reason: str, raw_line: str = "") -> None:
        self.line_no = line_no
        self.reason = reason
        self.raw_line = raw_line
        super().__init__(f"event log corruption at line {line_no}: {reason}; raw={raw_line!r}")


normalize_step_key = _core_normalize_step_key
denormalize_step_key = _core_denormalize_step_key
_canonical_json = _core_canonical_json
_is_hex_sha256 = _core_is_hex_sha256


# @shell_orchestration: Payload validation remains adjacent to the canonical event registry it enforces.
def validate_payload(kind: str, payload: Mapping[str, Any]) -> None:

    if kind not in CANONICAL_EVENT_REGISTRY:
        raise EventValidationError(
            f"unknown event kind {kind!r}; expected one of {tuple(CANONICAL_EVENT_REGISTRY.keys())}"
        )
    diagnostics = validate_payload_shape(kind, payload)
    if diagnostics:
        raise EventValidationError(diagnostics[0])


@dataclass(frozen=True)
class OrchestrationEventEnvelope:

    kind: OrchestrationEventKind
    timestamp: datetime
    step_id: str | None = None
    payload: Mapping[str, Any] | None = None
    agent: str | None = None
    seq: int | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None
    run_id: str | None = None
    drive_id: str | None = None

    def __post_init__(self) -> None:
        payload_value: dict[str, Any] = dict(self.payload or {})
        validate_payload(self.kind, payload_value)

        if self.timestamp.tzinfo is None:
            raise EventValidationError("timestamp must be timezone-aware (UTC)")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))
        object.__setattr__(self, "payload", payload_value)

        if self.seq is not None and self.seq <= 0:
            raise EventValidationError(f"seq must be > 0, got {self.seq}")
        if self.prev_hash is not None and not _is_hex_sha256(self.prev_hash):
            raise EventValidationError(
                f"prev_hash must be lowercase 64-char SHA-256, got {self.prev_hash!r}"
            )

        computed = self.compute_entry_hash()
        if self.entry_hash is None:
            object.__setattr__(self, "entry_hash", computed)
        elif self.entry_hash != computed:
            raise EventValidationError(
                f"entry_hash mismatch: expected {computed!r}, got {self.entry_hash!r}"
            )

    @property
    def version(self) -> int:

        return CANONICAL_EVENT_REGISTRY[self.kind].version

    def _hash_payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agent": self.agent,
            "event": self.kind,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "seq": self.seq,
            "step_id": self.step_id,
            "ts": self.timestamp.isoformat(),
            "version": self.version,
        }
        if self.run_id is not None:
            result["run_id"] = self.run_id
        if self.drive_id is not None:
            result["drive_id"] = self.drive_id
        return result

    def compute_entry_hash(self) -> str:

        canonical = _canonical_json(self._hash_payload())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, Any]:

        record: dict[str, Any] = {
            "seq": self.seq,
            "ts": self.timestamp.isoformat(),
            "event": self.kind,
            "version": self.version,
            "step_id": self.step_id,
            "agent": self.agent,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }
        if self.run_id is not None:
            record["run_id"] = self.run_id
        if self.drive_id is not None:
            record["drive_id"] = self.drive_id
        return record

    def to_jsonl_line(self) -> str:

        return _canonical_json(self.to_record()) + "\n"

    def with_integrity(self, *, seq: int, prev_hash: str | None) -> OrchestrationEventEnvelope:

        return replace(self, seq=seq, prev_hash=prev_hash, entry_hash=None)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> OrchestrationEventEnvelope:

        required = {
            "seq",
            "ts",
            "event",
            "version",
            "step_id",
            "agent",
            "payload",
            "prev_hash",
            "entry_hash",
        }
        # Note: run_id and drive_id are optional for backward-compatible
        # records; they are not in the required set.
        missing = sorted(required - set(record.keys()))
        if missing:
            raise EventValidationError(f"record missing required keys: {missing}")

        seq_raw = record["seq"]
        if not isinstance(seq_raw, int):
            raise EventValidationError(f"record seq must be int, got {type(seq_raw).__name__}")

        payload_raw = record["payload"]
        if not isinstance(payload_raw, Mapping):
            raise EventValidationError(
                f"record payload must be object, got {type(payload_raw).__name__}"
            )

        ts_raw = record["ts"]
        if not isinstance(ts_raw, str):
            raise EventValidationError(f"record ts must be str, got {type(ts_raw).__name__}")

        ts = datetime.fromisoformat(ts_raw)

        event_raw = record["event"]
        if not isinstance(event_raw, str):
            raise EventValidationError(f"record event must be str, got {type(event_raw).__name__}")

        prev_hash_raw = record["prev_hash"]
        if prev_hash_raw is not None and not isinstance(prev_hash_raw, str):
            raise EventValidationError(
                f"record prev_hash must be str|null, got {type(prev_hash_raw).__name__}"
            )

        entry_hash_raw = record["entry_hash"]
        if not isinstance(entry_hash_raw, str):
            raise EventValidationError(
                f"record entry_hash must be str, got {type(entry_hash_raw).__name__}"
            )

        step_id_raw = record["step_id"]
        if step_id_raw is not None and not isinstance(step_id_raw, str):
            raise EventValidationError(
                f"record step_id must be str|null, got {type(step_id_raw).__name__}"
            )

        agent_raw = record["agent"]
        if agent_raw is not None and not isinstance(agent_raw, str):
            raise EventValidationError(
                f"record agent must be str|null, got {type(agent_raw).__name__}"
            )

        run_id_raw = record.get("run_id")
        if run_id_raw is not None and not isinstance(run_id_raw, str):
            raise EventValidationError(
                f"record run_id must be str|null, got {type(run_id_raw).__name__}"
            )

        drive_id_raw = record.get("drive_id")
        if drive_id_raw is not None and not isinstance(drive_id_raw, str):
            raise EventValidationError(
                f"record drive_id must be str|null, got {type(drive_id_raw).__name__}"
            )

        validate_payload(event_raw, dict(payload_raw))

        return cls(
            kind=cast(OrchestrationEventKind, event_raw),
            timestamp=ts,
            step_id=step_id_raw,
            payload=dict(payload_raw),
            agent=agent_raw,
            seq=seq_raw,
            prev_hash=prev_hash_raw,
            entry_hash=entry_hash_raw,
            run_id=run_id_raw,
            drive_id=drive_id_raw,
        )

    @classmethod
    def validate_chain(cls, envelopes: Sequence[OrchestrationEventEnvelope]) -> None:

        expected_seq = 1
        previous_hash: str | None = None
        for index, envelope in enumerate(envelopes, start=1):
            if envelope.seq is None:
                raise EventCorruptionError(index, "missing seq")
            if envelope.seq != expected_seq:
                raise EventCorruptionError(
                    index,
                    f"non-monotonic seq: expected {expected_seq}, got {envelope.seq}",
                )
            if envelope.prev_hash != previous_hash:
                raise EventCorruptionError(
                    index,
                    f"prev_hash mismatch: expected {previous_hash!r}, got {envelope.prev_hash!r}",
                )
            expected_seq += 1
            previous_hash = envelope.entry_hash


class EventSink(Protocol):

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        ...


@dataclass(frozen=True)
class DriveEventEnvelope:

    kind: DriveEventKind
    timestamp: datetime
    drive_id: str
    child_run_id: str | None = None
    step_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    seq: int | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.drive_id:
            raise EventValidationError("drive_id is required for drive event envelopes")
        if self.timestamp.tzinfo is None:
            raise EventValidationError("timestamp must be timezone-aware (UTC)")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))

        # Validate payload against canonical registry (all drive events are
        # also in the canonical registry)
        validate_payload(self.kind, dict(self.payload))

        if self.seq is not None and self.seq <= 0:
            raise EventValidationError(f"seq must be > 0, got {self.seq}")
        if self.prev_hash is not None and not _is_hex_sha256(self.prev_hash):
            raise EventValidationError(
                f"prev_hash must be lowercase 64-char SHA-256, got {self.prev_hash!r}"
            )

        computed = self._compute_entry_hash()
        if self.entry_hash is None:
            object.__setattr__(self, "entry_hash", computed)
        elif self.entry_hash != computed:
            raise EventValidationError(
                f"entry_hash mismatch: expected {computed!r}, got {self.entry_hash!r}"
            )

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "child_run_id": self.child_run_id,
            "drive_id": self.drive_id,
            "event": self.kind,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "seq": self.seq,
            "step_id": self.step_id,
            "ts": self.timestamp.isoformat(),
        }

    def _compute_entry_hash(self) -> str:
        canonical = _canonical_json(self._canonical_payload())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_orchestration_envelope(self) -> OrchestrationEventEnvelope:
        return OrchestrationEventEnvelope(
            kind=cast(OrchestrationEventKind, self.kind),
            timestamp=self.timestamp,
            step_id=self.step_id,
            payload=dict(self.payload),
            seq=self.seq,
            prev_hash=self.prev_hash,
            entry_hash=None,
            drive_id=self.drive_id,
        )

    def with_integrity(self, *, seq: int, prev_hash: str | None) -> DriveEventEnvelope:
        return replace(self, seq=seq, prev_hash=prev_hash, entry_hash=None)


class _DriveEventBuilder:
    """Callable compatibility constructor for drive event envelopes."""

    # @shell_orchestration: Drive-event constructor stays with envelope schema validation to preserve public import surface.
    def __call__(
        self,
        kind: DriveEventKind,
        drive_id: str,
        payload: Mapping[str, Any],
        *,
        child_run_id: str | None = None,
        step_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> DriveEventEnvelope:
        return DriveEventEnvelope(
            kind=kind,
            timestamp=timestamp or datetime.now(timezone.utc),
            drive_id=drive_id,
            child_run_id=child_run_id,
            step_id=step_id,
            payload=payload,
        )


build_drive_event = _DriveEventBuilder()


# @shell_complexity: Chain validation branches separately diagnose missing seq, seq drift, and hash drift.
# @shell_orchestration: Drive chain validation stays with envelope hashing to preserve corruption diagnostics.
def validate_drive_event_chain(
    envelopes: Sequence[DriveEventEnvelope],
) -> None:
    expected_seq = 1
    previous_hash: str | None = None
    for index, envelope in enumerate(envelopes, start=1):
        if envelope.seq is None:
            raise EventCorruptionError(index, "missing seq")
        if envelope.seq != expected_seq:
            raise EventCorruptionError(
                index,
                f"non-monotonic seq: expected {expected_seq}, got {envelope.seq}",
            )
        if envelope.prev_hash != previous_hash:
            raise EventCorruptionError(
                index,
                f"prev_hash mismatch: expected {previous_hash!r}, got {envelope.prev_hash!r}",
            )
        expected_seq += 1
        previous_hash = envelope.entry_hash


class JsonlEventSink:

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._thread_lock = threading.Lock()

    def _authoritative_tail(self) -> tuple[int, str | None]:
        existing = load_event_jsonl(self._path)
        if not existing:
            return (0, None)
        tail = existing[-1]
        return ((tail.seq or 0), tail.entry_hash)

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:

        with self._thread_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+", encoding="utf-8") as lock_handle:
                flock(lock_handle.fileno(), LOCK_EX)
                try:
                    current_seq, current_hash = self._authoritative_tail()
                    next_seq = current_seq + 1
                    if envelope.seq is not None and envelope.seq != next_seq:
                        raise EventValidationError(
                            "event seq hook violation: "
                            f"expected next seq {next_seq}, got {envelope.seq}"
                        )
                    if envelope.prev_hash is not None and envelope.prev_hash != current_hash:
                        raise EventValidationError(
                            "event prev_hash hook violation: "
                            f"expected {current_hash!r}, got {envelope.prev_hash!r}"
                        )

                    normalized = envelope.with_integrity(seq=next_seq, prev_hash=current_hash)
                    with self._path.open("a", encoding="utf-8") as handle:
                        handle.write(normalized.to_jsonl_line())
                finally:
                    flock(lock_handle.fileno(), LOCK_UN)


# @invar:allow shell_result: Loader raises EventCorruptionError per established JSONL integrity contract.
# @shell_complexity: Loader distinguishes missing file, truncation, JSON, object-shape, and chain corruption.
def load_event_jsonl(path: str | Path) -> tuple[OrchestrationEventEnvelope, ...]:

    records: list[OrchestrationEventEnvelope] = []
    file_path = Path(path)
    if not file_path.exists():
        return ()

    with file_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                raise EventCorruptionError(line_no, "truncated trailing record", line)
            if not line.strip():
                raise EventCorruptionError(line_no, "empty record", line)
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventCorruptionError(line_no, f"malformed json: {exc.msg}", line) from exc
            if not isinstance(data, Mapping):
                raise EventCorruptionError(
                    line_no,
                    f"record must be object, got {type(data).__name__}",
                    line,
                )
            try:
                envelope = OrchestrationEventEnvelope.from_record(data)
            except EventValidationError as exc:
                raise EventCorruptionError(line_no, f"invalid record: {exc}", line) from exc
            records.append(envelope)

    OrchestrationEventEnvelope.validate_chain(records)
    return tuple(records)


class EventRegistry:

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventSink]] = {
            kind: [] for kind in CANONICAL_EVENT_REGISTRY.keys()
        }

    def register(self, kind: OrchestrationEventKind, handler: EventSink) -> None:

        if kind not in self._handlers:
            raise EventValidationError(f"cannot register unknown event kind {kind!r}")
        self._handlers[kind].append(handler)

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:

        validate_payload(envelope.kind, envelope.payload or {})
        for handler in self._handlers[envelope.kind]:
            handler.emit(envelope)


def emit(envelope: OrchestrationEventEnvelope, *, registry: EventRegistry) -> None:

    registry.emit(envelope)


# @shell_orchestration: Registry helper preserves explicit registry public API while delegating mutation.
def register_sink(
    kind: OrchestrationEventKind,
    handler: EventSink,
    *,
    registry: EventRegistry,
) -> None:

    registry.register(kind, handler)


__all__ = [
    "EventCorruptionError",
    "EventSchema",
    "EventSink",
    "EventValidationError",
    "EventRegistry",
    "JsonlEventSink",
    "OrchestrationEventEnvelope",
    "OrchestrationEventKind",
    "DriveEventEnvelope",
    "DriveEventKind",
    "DRIVE_EVENT_KINDS",
    "CANONICAL_EVENT_REGISTRY",
    "build_drive_event",
    "denormalize_step_key",
    "emit",
    "load_event_jsonl",
    "normalize_step_key",
    "register_sink",
    "validate_drive_event_chain",
    "validate_payload",
]
