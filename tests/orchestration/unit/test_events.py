"""Unit tests for orchestration observability event contracts.

Authority:
    - User dispatch for step orch_operator_observability.impl_event_envelope_registry_sinks
    - protected tests from orch_operator_tests.observability_red
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from vectl.orchestration.events import (
    EventCorruptionError,
    EventRegistry,
    EventValidationError,
    JsonlEventSink,
    OrchestrationEventEnvelope,
    load_event_jsonl,
    register_sink,
)


def test_canonical_jsonl_framing_and_entry_hash_are_deterministic() -> None:
    timestamp = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)
    envelope = OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=timestamp,
        step_id="core.impl",
        payload={"step_id": "core.impl", "agent": "python-executor"},
        agent="python-executor",
        seq=1,
    )

    expected_hash_payload = {
        "agent": "python-executor",
        "event": "control_dispatch",
        "payload": {"agent": "python-executor", "step_id": "core.impl"},
        "prev_hash": None,
        "seq": 1,
        "step_id": "core.impl",
        "ts": timestamp.isoformat(),
        "version": 1,
    }
    expected_hash = hashlib.sha256(
        json.dumps(expected_hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert envelope.compute_entry_hash() == expected_hash
    assert envelope.entry_hash == expected_hash

    line = envelope.to_jsonl_line()
    decoded = json.loads(line)
    assert decoded["seq"] == 1
    assert decoded["prev_hash"] is None
    assert decoded["entry_hash"] == expected_hash
    assert decoded["event"] == "control_dispatch"


def test_jsonl_sink_appends_and_enforces_seq_prev_hash_hooks(tmp_path: Any) -> None:
    events_path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(events_path)

    first = OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        step_id="core.a",
        payload={"step_id": "core.a", "agent": "python-executor"},
        agent="python-executor",
    )
    sink.emit(first)
    size_after_first = events_path.stat().st_size

    second = OrchestrationEventEnvelope(
        kind="control_done",
        timestamp=datetime(2026, 4, 4, 12, 0, 1, tzinfo=timezone.utc),
        step_id="core.a",
        payload={"step_id": "core.a", "status": "ok"},
        agent="python-executor",
    )
    sink.emit(second)
    size_after_second = events_path.stat().st_size

    assert size_after_second > size_after_first
    loaded = load_event_jsonl(events_path)
    assert len(loaded) == 2
    assert loaded[0].seq == 1
    assert loaded[1].seq == 2
    assert loaded[1].prev_hash == loaded[0].entry_hash


def test_event_seq_hook_violation_is_rejected(tmp_path: Any) -> None:
    sink = JsonlEventSink(tmp_path / "events.jsonl")

    envelope = OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        step_id="core.a",
        payload={"step_id": "core.a", "agent": "python-executor"},
        agent="python-executor",
        seq=4,
    )
    with pytest.raises(EventValidationError, match="event seq hook violation"):
        sink.emit(envelope)


def test_unknown_event_name_and_payload_drift_are_rejected() -> None:
    with pytest.raises(EventValidationError, match="unknown event kind"):
        OrchestrationEventEnvelope(
            kind=cast(Any, "unknown_event"),
            timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
            payload={},
        )

    with pytest.raises(EventValidationError, match="payload schema drift"):
        OrchestrationEventEnvelope(
            kind="control_dispatch",
            timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
            payload={"step_id": "core.a"},
        )

    with pytest.raises(EventValidationError, match="payload schema drift"):
        OrchestrationEventEnvelope(
            kind="control_dispatch",
            timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
            payload={
                "step_id": "core.a",
                "agent": "python-executor",
                "unexpected": "drift",
            },
        )


def test_truncated_or_malformed_trailing_records_surface_corruption(tmp_path: Any) -> None:
    events_path = tmp_path / "events.jsonl"
    valid = OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        step_id="core.a",
        payload={"step_id": "core.a", "agent": "python-executor"},
        agent="python-executor",
        seq=1,
        prev_hash=None,
    )

    events_path.write_text(valid.to_jsonl_line() + '{"seq":2', encoding="utf-8")
    with pytest.raises(EventCorruptionError, match="truncated trailing record"):
        load_event_jsonl(events_path)

    events_path.write_text(valid.to_jsonl_line() + "{not-json}\n", encoding="utf-8")
    with pytest.raises(EventCorruptionError, match="malformed json"):
        load_event_jsonl(events_path)


def test_jsonl_sink_reloads_authoritative_tail_across_instances(tmp_path: Any) -> None:
    events_path = tmp_path / "events.jsonl"
    sink_a = JsonlEventSink(events_path)
    sink_b = JsonlEventSink(events_path)

    sink_a.emit(
        OrchestrationEventEnvelope(
            kind="control_dispatch",
            timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
            step_id="core.a",
            payload={"step_id": "core.a", "agent": "python-executor"},
            agent="python-executor",
        )
    )
    sink_b.emit(
        OrchestrationEventEnvelope(
            kind="control_done",
            timestamp=datetime(2026, 4, 4, 12, 0, 1, tzinfo=timezone.utc),
            step_id="core.a",
            payload={"step_id": "core.a", "status": "ok"},
            agent="python-executor",
        )
    )
    sink_a.emit(
        OrchestrationEventEnvelope(
            kind="control_wait",
            timestamp=datetime(2026, 4, 4, 12, 0, 2, tzinfo=timezone.utc),
            step_id="core.a",
            payload={"step_id": "core.a", "reason": "waiting"},
            agent="python-executor",
        )
    )

    loaded = load_event_jsonl(events_path)
    assert [event.seq for event in loaded] == [1, 2, 3]


def test_jsonl_sink_parallel_writers_preserve_append_only_chain(tmp_path: Any) -> None:
    events_path = tmp_path / "events.jsonl"
    sink_a = JsonlEventSink(events_path)
    sink_b = JsonlEventSink(events_path)

    def _emit_batch(sink: JsonlEventSink, start: int) -> None:
        for i in range(5):
            sink.emit(
                OrchestrationEventEnvelope(
                    kind="control_dispatch",
                    timestamp=datetime(2026, 4, 4, 12, 0, start + i, tzinfo=timezone.utc),
                    step_id="core.parallel",
                    payload={"step_id": "core.parallel", "agent": "python-executor"},
                    agent="python-executor",
                )
            )

    t1 = threading.Thread(target=_emit_batch, args=(sink_a, 0))
    t2 = threading.Thread(target=_emit_batch, args=(sink_b, 10))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    loaded = load_event_jsonl(events_path)
    assert len(loaded) == 10
    assert [event.seq for event in loaded] == list(range(1, 11))


def test_register_sink_requires_explicit_registry_instance(tmp_path: Any) -> None:
    registry = EventRegistry()
    sink = JsonlEventSink(tmp_path / "events-explicit-registry.jsonl")

    register_sink("control_dispatch", sink, registry=registry)
    envelope = OrchestrationEventEnvelope(
        kind="control_dispatch",
        timestamp=datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc),
        step_id="core.a",
        payload={"step_id": "core.a", "agent": "python-executor"},
        agent="python-executor",
    )
    registry.emit(envelope)
