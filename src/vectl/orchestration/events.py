"""Orchestration-plane canonical event envelopes and append-only sinks.

Authority:
    - User dispatch for step orch_operator_observability.impl_event_envelope_registry_sinks
      (requires canonical JSONL framing, prev/entry hash chain, seq hooks,
      registry validation, append-only sink behavior, corruption diagnostics)
    - tests from orch_operator_tests.observability_red (protected)
    - docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.8
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast


@dataclass(frozen=True)
class EventSchema:
    """Schema contract for one canonical event kind.

    Attributes:
        required_payload_keys: Keys that must be present in payload.
        optional_payload_keys: Keys allowed in payload in addition to required keys.
        version: Canonical event schema version.
    """

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
]


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
    "operator_case_opened": EventSchema(required_payload_keys=("case_id", "step_id")),
    "operator_case_resolved": EventSchema(required_payload_keys=("case_id", "resolution")),
    "operator_action_requested": EventSchema(required_payload_keys=("case_id", "action")),
}


class EventValidationError(ValueError):
    """Raised when event kind, payload, or integrity metadata is invalid."""


class EventCorruptionError(ValueError):
    """Raised when an existing JSONL stream is truncated or malformed."""

    def __init__(self, line_no: int, reason: str, raw_line: str = "") -> None:
        self.line_no = line_no
        self.reason = reason
        self.raw_line = raw_line
        super().__init__(f"event log corruption at line {line_no}: {reason}; raw={raw_line!r}")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_hex_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    """Validate payload against static canonical registry schema.

    Args:
        kind: Canonical event kind name.
        payload: Event payload object.

    Raises:
        EventValidationError: If kind is unknown or payload shape drifts.
    """

    schema = CANONICAL_EVENT_REGISTRY.get(kind)
    if schema is None:
        raise EventValidationError(
            f"unknown event kind {kind!r}; expected one of {tuple(CANONICAL_EVENT_REGISTRY.keys())}"
        )

    keys = set(payload.keys())
    required = set(schema.required_payload_keys)
    allowed = required | set(schema.optional_payload_keys)
    missing = sorted(required - keys)
    extras = sorted(keys - allowed)
    if missing or extras:
        raise EventValidationError(
            f"payload schema drift for event {kind!r}: missing={missing}, unexpected={extras}"
        )


@dataclass(frozen=True)
class OrchestrationEventEnvelope:
    """Canonical orchestration event envelope for append-only JSONL logs.

    Attributes:
        kind: Canonical event name from the static registry.
        timestamp: UTC-aware timestamp for the event.
        step_id: Optional associated step identifier.
        payload: Event payload validated against event registry schema.
        agent: Optional agent identifier.
        seq: Monotonic append-order sequence value.
        prev_hash: Previous record entry hash (or None for first record).
        entry_hash: SHA-256 over canonical JSON of envelope without entry_hash.
    """

    kind: OrchestrationEventKind
    timestamp: datetime
    step_id: str | None = None
    payload: Mapping[str, Any] | None = None
    agent: str | None = None
    seq: int | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None

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
        """Return canonical schema version for this event kind."""

        return CANONICAL_EVENT_REGISTRY[self.kind].version

    def _hash_payload(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "event": self.kind,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "seq": self.seq,
            "step_id": self.step_id,
            "ts": self.timestamp.isoformat(),
            "version": self.version,
        }

    def compute_entry_hash(self) -> str:
        """Compute deterministic SHA-256 over canonical event JSON payload."""

        canonical = _canonical_json(self._hash_payload())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, Any]:
        """Convert envelope to canonical JSONL record payload."""

        return {
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

    def to_jsonl_line(self) -> str:
        """Return canonical single-line JSONL framing for this envelope."""

        return _canonical_json(self.to_record()) + "\n"

    def with_integrity(self, *, seq: int, prev_hash: str | None) -> OrchestrationEventEnvelope:
        """Return envelope with seq/hash-chain metadata recomputed.

        Args:
            seq: Assigned append sequence.
            prev_hash: Previous entry hash in chain.

        Returns:
            New immutable envelope with deterministic entry_hash.
        """

        return replace(self, seq=seq, prev_hash=prev_hash, entry_hash=None)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> OrchestrationEventEnvelope:
        """Build and validate an envelope from JSONL record data.

        Args:
            record: Decoded JSON object from one JSONL line.

        Returns:
            Fully validated envelope.

        Raises:
            EventValidationError: If record is missing required fields.
        """

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
        )

    @classmethod
    def validate_chain(cls, envelopes: Sequence[OrchestrationEventEnvelope]) -> None:
        """Validate monotonic seq and prev_hash -> entry_hash chain integrity.

        Args:
            envelopes: Ordered sequence of envelopes as they appear in JSONL.

        Raises:
            EventCorruptionError: If sequence or hash chain integrity is broken.
        """

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
    """Protocol for durable event sink implementations."""

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        """Emit one validated event envelope to sink."""


class JsonlEventSink:
    """Append-only events JSONL sink with hash-chain continuity checks."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_seq = 0
        self._last_hash: str | None = None

        if self._path.exists():
            existing = load_event_jsonl(self._path)
            if existing:
                self._last_seq = existing[-1].seq or 0
                self._last_hash = existing[-1].entry_hash

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        """Append one event preserving seq monotonicity and hash chain integrity."""

        next_seq = self._last_seq + 1
        if envelope.seq is not None and envelope.seq != next_seq:
            raise EventValidationError(
                f"event seq hook violation: expected next seq {next_seq}, got {envelope.seq}"
            )
        if envelope.prev_hash is not None and envelope.prev_hash != self._last_hash:
            raise EventValidationError(
                "event prev_hash hook violation: "
                f"expected {self._last_hash!r}, got {envelope.prev_hash!r}"
            )

        normalized = envelope.with_integrity(seq=next_seq, prev_hash=self._last_hash)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(normalized.to_jsonl_line())
        self._last_seq = next_seq
        self._last_hash = normalized.entry_hash


def load_event_jsonl(path: str | Path) -> tuple[OrchestrationEventEnvelope, ...]:
    """Load and validate canonical events JSONL stream.

    Args:
        path: Path to events JSONL file.

    Returns:
        Ordered tuple of envelopes.

    Raises:
        EventCorruptionError: For truncated trailing lines, malformed JSON, or chain drift.
    """

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
    """Event dispatch registry with canonical taxonomy and schema validation."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventSink]] = {
            kind: [] for kind in CANONICAL_EVENT_REGISTRY.keys()
        }

    def register(self, kind: OrchestrationEventKind, handler: EventSink) -> None:
        """Register a sink handler for one canonical event kind."""

        if kind not in self._handlers:
            raise EventValidationError(f"cannot register unknown event kind {kind!r}")
        self._handlers[kind].append(handler)

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        """Validate and dispatch an envelope to handlers of its event kind."""

        validate_payload(envelope.kind, envelope.payload or {})
        for handler in self._handlers[envelope.kind]:
            handler.emit(envelope)


_global_registry = EventRegistry()


def emit(envelope: OrchestrationEventEnvelope) -> None:
    """Emit one orchestration event through the module-global registry."""

    _global_registry.emit(envelope)


def register_sink(kind: OrchestrationEventKind, handler: EventSink) -> None:
    """Register a module-global sink for one canonical event kind."""

    _global_registry.register(kind, handler)


__all__ = [
    "EventCorruptionError",
    "EventSchema",
    "EventSink",
    "EventValidationError",
    "EventRegistry",
    "JsonlEventSink",
    "OrchestrationEventEnvelope",
    "OrchestrationEventKind",
    "CANONICAL_EVENT_REGISTRY",
    "emit",
    "load_event_jsonl",
    "register_sink",
    "validate_payload",
]
