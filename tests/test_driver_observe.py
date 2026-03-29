"""Focused tests for driver event-contract foundation."""

from __future__ import annotations

import time

from src.vectl.driver.events.registry import EVENT_REGISTRY, EventRegistryRecord
from src.vectl.driver.events.sinks import FileObserver, NullObserver
from src.vectl.driver.events.types import ALL_EVENT_TYPES, FINAL, STEP_DISPATCHED
from src.vectl.driver.observe import Event, Observer


class TestEvent:
    def test_event_required_fields(self) -> None:
        event = Event(ts=time.time(), event=STEP_DISPATCHED)
        assert isinstance(event.ts, float)
        assert event.event == STEP_DISPATCHED
        assert event.version == 1
        assert event.data == {}

    def test_event_ts_type(self) -> None:
        ts = time.time()
        event = Event(ts=ts, event="DECIDE")
        assert event.ts == ts
        assert isinstance(event.ts, float)

    def test_event_data_default(self) -> None:
        event = Event(ts=1.0, event="HALT")
        assert event.data == {}

    def test_event_types_defined(self) -> None:
        for event_type in ALL_EVENT_TYPES:
            event = Event(ts=1.0, event=event_type)
            assert event.event == event_type


class TestObserverProtocol:
    def test_observer_emit_signature(self) -> None:
        from typing import get_type_hints

        hints = get_type_hints(Observer.emit)
        assert hints["event_type"] is str
        assert hints["return"] is type(None)

    def test_observer_close_signature(self) -> None:
        from typing import get_type_hints

        hints = get_type_hints(Observer.close)
        assert hints["return"] is type(None)


class TestEventRegistry:
    def test_registry_covers_all_declared_event_types(self) -> None:
        assert set(EVENT_REGISTRY) == set(ALL_EVENT_TYPES)

    def test_registry_record_shape(self) -> None:
        record = EVENT_REGISTRY[STEP_DISPATCHED]
        assert isinstance(record, EventRegistryRecord)
        assert record.event == STEP_DISPATCHED
        assert record.version == 1
        assert record.required == ("step_id", "agent", "runner", "session_reuse")
        assert record.optional == ("preflight_enabled",)
        assert record.owner == "vectl.driver.loop"
        assert record.compatibility

    def test_final_registry_contract_matches_adr(self) -> None:
        record = EVENT_REGISTRY[FINAL]
        assert record.required == ("completed_summary", "total_duration_seconds")
        assert set(record.optional) == {"halt_reason", "total_cost_usd", "total_tokens"}

    def test_registry_is_static_declaration_table(self) -> None:
        try:
            EVENT_REGISTRY["NEW_EVENT"] = EventRegistryRecord(  # type: ignore[index]
                event="NEW_EVENT",
                version=1,
                required=(),
                optional=(),
                owner="vectl.driver.loop",
                compatibility="forbidden",
            )
        except TypeError:
            pass
        else:
            raise AssertionError("EVENT_REGISTRY must be immutable")


class TestEventLogging:
    def test_event_jsonl_format(self) -> None:
        import json
        from dataclasses import asdict

        event = Event(
            ts=1711612345.0,
            event=STEP_DISPATCHED,
            data={"step_id": "core.impl", "agent": "python-executor"},
        )
        parsed = json.loads(json.dumps(asdict(event)))
        assert parsed["ts"] == 1711612345.0
        assert parsed["event"] == STEP_DISPATCHED
        assert parsed["version"] == 1
        assert parsed["data"]["step_id"] == "core.impl"


class TestEventRoundTrip:
    def test_event_to_json(self) -> None:
        import json
        from dataclasses import asdict

        event = Event(
            ts=1711612345.123,
            event="JUDGMENT",
            data={
                "type": "EVIDENCE",
                "step_id": "core.impl",
                "verdict": "ACCEPT",
                "reason": "Tests pass",
            },
        )
        parsed = json.loads(json.dumps(asdict(event)))
        assert parsed["ts"] == event.ts
        assert parsed["event"] == event.event
        assert parsed["version"] == event.version
        assert parsed["data"] == event.data


class TestSinkOwnershipSplit:
    def test_file_observer_uses_registry_versioned_envelope(self, tmp_path) -> None:
        import json

        from src.vectl.driver.config import ObservabilityConfig

        events_file = tmp_path / "events.jsonl"
        observer = FileObserver(ObservabilityConfig(events_file=str(events_file)))
        observer.emit(
            STEP_DISPATCHED,
            step_id="core.impl",
            agent="python-executor",
            runner="claude",
            session_reuse=False,
        )
        observer.close()

        parsed = json.loads(events_file.read_text().strip())
        assert parsed["event"] == STEP_DISPATCHED
        assert parsed["version"] == EVENT_REGISTRY[STEP_DISPATCHED].version
        assert parsed["data"]["step_id"] == "core.impl"

    def test_null_observer_accepts_declared_events(self) -> None:
        observer = NullObserver()
        observer.emit("WAIT", reason="idle")
        observer.close()
