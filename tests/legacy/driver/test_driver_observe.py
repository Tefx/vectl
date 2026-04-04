"""Focused tests for driver event-contract foundation."""

from __future__ import annotations

import inspect
import time
from typing import get_type_hints

from src.vectl.driver.events import emitter as emitter_module
from src.vectl.driver.events.emitter import (
    DECIDE_EVENT_DEF,
    FINAL_EVENT_DEF,
    STEP_COMPLETED_EVENT_DEF,
    emit_decide,
    emit_final,
    emit_step_completed,
)
from src.vectl.driver.events.registry import EVENT_REGISTRY, EventRegistryRecord
from src.vectl.driver.events.sinks import FileObserver, NullObserver
from src.vectl.driver.events.types import (
    ALL_EVENT_TYPES,
    DECIDE,
    FINAL,
    STEP_COMPLETED,
    STEP_DISPATCHED,
)
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
        assert "terminal outcome" in record.compatibility

    def test_decide_registry_contract_matches_adr(self) -> None:
        record = EVENT_REGISTRY[DECIDE]
        assert record.required == ("running_count", "actions")
        assert record.optional == ("claimable", "capacity")
        assert record.version == 1
        assert record.owner == "vectl.driver.loop"

    def test_step_completed_registry_contract_matches_adr(self) -> None:
        record = EVENT_REGISTRY[STEP_COMPLETED]
        assert record.required == ("step_id", "elapsed_seconds")
        assert record.optional == ("cost", "evidence_len", "tokens")
        assert record.version == 1
        assert record.owner == "vectl.driver.loop"

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


class TestTypedEmitterContracts:
    def test_registry_backed_defs_use_exact_event_names(self) -> None:
        assert DECIDE_EVENT_DEF is EVENT_REGISTRY[DECIDE]
        assert FINAL_EVENT_DEF is EVENT_REGISTRY[FINAL]
        assert STEP_COMPLETED_EVENT_DEF is EVENT_REGISTRY[STEP_COMPLETED]
        assert (DECIDE_EVENT_DEF.event, FINAL_EVENT_DEF.event, STEP_COMPLETED_EVENT_DEF.event) == (
            "DECIDE",
            "FINAL",
            "STEP_COMPLETED",
        )

    def test_typed_emitter_signatures_are_pinned(self) -> None:
        assert list(inspect.signature(emit_decide).parameters) == [
            "observer",
            "running_count",
            "actions",
            "claimable",
            "capacity",
        ]
        assert list(inspect.signature(emit_final).parameters) == [
            "observer",
            "completed_summary",
            "total_duration_seconds",
            "halt_reason",
            "total_cost_usd",
            "total_tokens",
        ]
        assert list(inspect.signature(emit_step_completed).parameters) == [
            "observer",
            "step_id",
            "elapsed_seconds",
            "cost",
            "evidence_len",
            "tokens",
        ]

    def test_typed_emitters_return_exact_event_literals(self) -> None:
        assert str(get_type_hints(emit_decide)["return"]) == "typing.Literal['DECIDE']"
        assert str(get_type_hints(emit_final)["return"]) == "typing.Literal['FINAL']"
        assert (
            str(get_type_hints(emit_step_completed)["return"]) == "typing.Literal['STEP_COMPLETED']"
        )


class TestTypedEmitterRuntimeBehavior:
    class _CaptureObserver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def emit(self, event_type: str, /, **data: object) -> None:
            self.calls.append((event_type, data))

    def test_emit_decide_emits_exact_event_and_payload(self) -> None:
        observer = self._CaptureObserver()

        result = emit_decide(observer, running_count=3, actions=["claim_and_dispatch"])

        assert result == "DECIDE"
        assert observer.calls == [
            (
                "DECIDE",
                {"running_count": 3, "actions": ["claim_and_dispatch"]},
            )
        ]

    def test_emit_step_completed_emits_required_elapsed_seconds(self) -> None:
        observer = self._CaptureObserver()

        result = emit_step_completed(
            observer,
            step_id="core.impl",
            elapsed_seconds=1.25,
            evidence_len=8,
        )

        assert result == "STEP_COMPLETED"
        assert observer.calls == [
            (
                "STEP_COMPLETED",
                {"step_id": "core.impl", "elapsed_seconds": 1.25, "evidence_len": 8},
            )
        ]

    def test_emit_final_omits_absent_optional_fields(self) -> None:
        observer = self._CaptureObserver()

        result = emit_final(
            observer,
            completed_summary={"terminal_outcome": "completed"},
            total_duration_seconds=12.5,
        )

        assert result == "FINAL"
        assert observer.calls == [
            (
                "FINAL",
                {
                    "completed_summary": {"terminal_outcome": "completed"},
                    "total_duration_seconds": 12.5,
                },
            )
        ]


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

    def test_file_observer_rejects_nested_data_version(self, tmp_path) -> None:
        from src.vectl.driver.config import ObservabilityConfig

        observer = FileObserver(ObservabilityConfig(events_file=str(tmp_path / "events.jsonl")))
        try:
            observer.emit("WAIT", reason="idle", version=99)
        except ValueError as exc:
            assert "top-level envelope version" in str(exc)
        else:
            raise AssertionError("expected ValueError for nested payload version")

    def test_null_observer_rejects_nested_data_version(self) -> None:
        observer = NullObserver()
        try:
            observer.emit("WAIT", reason="idle", version=99)
        except ValueError as exc:
            assert "top-level envelope version" in str(exc)
        else:
            raise AssertionError("expected ValueError for nested payload version")

    def test_file_observer_rejects_missing_required_fields(self, tmp_path) -> None:
        from src.vectl.driver.config import ObservabilityConfig

        observer = FileObserver(ObservabilityConfig(events_file=str(tmp_path / "events.jsonl")))
        try:
            observer.emit(
                STEP_DISPATCHED,
                step_id="core.impl",
                agent="python-executor",
                session_reuse=False,
            )
        except ValueError as exc:
            assert "missing required fields" in str(exc)
            assert "runner" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing required fields")

    def test_null_observer_rejects_missing_required_fields(self) -> None:
        observer = NullObserver()
        try:
            observer.emit("DRIVER_LIFECYCLE", phase="startup")
        except ValueError as exc:
            assert "missing required fields" in str(exc)
            assert "run_id" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing required fields")


class TestAdvancedObservabilitySchemaDriftGuard:
    def test_schema_alignment_reports_field_level_required_mismatch(self, monkeypatch) -> None:
        from src.vectl.driver.events.registry import EVENT_REGISTRY

        drifted_registry = dict(EVENT_REGISTRY)
        lifecycle_record = drifted_registry["DRIVER_LIFECYCLE"]
        drifted_registry["DRIVER_LIFECYCLE"] = EventRegistryRecord(
            event=lifecycle_record.event,
            version=lifecycle_record.version,
            required=("phase",),
            optional=lifecycle_record.optional,
            owner=lifecycle_record.owner,
            compatibility=lifecycle_record.compatibility,
        )
        monkeypatch.setattr(emitter_module, "EVENT_REGISTRY", drifted_registry)

        drift = emitter_module.assert_advanced_observability_schema_alignment()

        assert "required_mismatch:DRIVER_LIFECYCLE" in drift
        assert "required_missing:DRIVER_LIFECYCLE:run_id" in drift
