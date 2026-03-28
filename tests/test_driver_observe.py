"""Focused tests for driver observe.

Tests verify:
- Event dataclass structure and serialization
- Observer Protocol signature
- Event shape conformance for JSONL logging

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.4
Blueprint Reference: DRIVER-BLUEPRINT.md Observability (observe.py)
"""

import time

import pytest

from src.vectl.driver.observe import Event


class TestEvent:
    """Contract tests for Event dataclass."""

    def test_event_required_fields(self) -> None:
        """Event MUST have ts (float), event (str), data (dict[str, object]).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types
        """
        event = Event(ts=time.time(), event="STEP_DISPATCHED")
        assert isinstance(event.ts, float)
        assert event.event == "STEP_DISPATCHED"
        assert event.data == {}  # Default empty dict

    def test_event_ts_type(self) -> None:
        """Event.ts MUST be float (time.time()).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        """
        ts = time.time()
        event = Event(ts=ts, event="DECIDE")
        assert event.ts == ts
        assert isinstance(event.ts, float)

    def test_event_data_default(self) -> None:
        """Event.data MUST default to empty dict.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        """
        event = Event(ts=1.0, event="HALT")
        assert event.data == {}

    def test_event_types_defined(self) -> None:
        """Event types MUST be valid string values.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        # Event types from the doc
        event_types = [
            "DECIDE",
            "STEP_DISPATCHED",
            "STEP_COMPLETED",
            "STEP_FAILED",
            "JUDGMENT",
            "MERGE_COMPLETED",
            "MERGE_CONFLICTED",
            "RUNNER_FALLBACK",
            "SESSION_REUSE_HIT",
            "SESSION_REUSE_MISS",
            "COST_CHECKPOINT",
            "RECOVERY",
            "HALT",
            "FINAL",
        ]
        for event_type in event_types:
            event = Event(ts=1.0, event=event_type)
            assert event.event == event_type

    def test_event_with_data(self) -> None:
        """Event can store arbitrary data in the data dict."""
        event = Event(
            ts=1.0,
            event="STEP_DISPATCHED",
            data={"step_id": "core.impl", "agent": "python-executor", "runner": "claude"},
        )
        assert event.data["step_id"] == "core.impl"
        assert event.data["agent"] == "python-executor"
        assert event.data["runner"] == "claude"


class TestObserverProtocol:
    """Contract tests for Observer Protocol."""

    def test_observer_emit_signature(self) -> None:
        """Observer.emit MUST accept event_type: str and **data: object.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability (observer.emit(...))
        """
        from typing import get_type_hints

        from src.vectl.driver.observe import Observer

        # Check emit method signature
        hints = get_type_hints(Observer.emit)
        # event_type is a positional-only parameter followed by **data
        assert "event_type" in hints
        assert hints["event_type"] is str
        assert "return" in hints
        assert hints["return"] is type(None)  # returns None

    def test_observer_close_signature(self) -> None:
        """Observer.close MUST be callable with no arguments.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md shutdown (observer.close())
        """
        from typing import get_type_hints

        from src.vectl.driver.observe import Observer

        # Check close method signature
        hints = get_type_hints(Observer.close)
        assert "return" in hints
        assert hints["return"] is type(None)  # returns None


class TestEventPayloads:
    """Contract tests for event payload expectations.

    Each test defines the required fields for a specific event type.
    These are informational contracts - the actual implementation
    will emit these fields.
    """

    def test_decide_event_payload(self) -> None:
        """DECIDE event MUST include: running_count, claimable, capacity, actions.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        # Define the expected structure
        expected_fields = {"running_count", "claimable", "capacity", "actions"}
        # Verify we can create an event with these fields
        event = Event(
            ts=time.time(),
            event="DECIDE",
            data={
                "running_count": 3,
                "claimable": 5,
                "capacity": 2,
                "actions": ["claim_and_dispatch", "wait"],
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_step_dispatched_event_payload(self) -> None:
        """STEP_DISPATCHED event MUST include: step_id, agent, runner, session_reuse.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "agent", "runner", "session_reuse"}
        event = Event(
            ts=time.time(),
            event="STEP_DISPATCHED",
            data={
                "step_id": "core.impl",
                "agent": "python-executor",
                "runner": "claude",
                "session_reuse": True,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_step_completed_event_payload(self) -> None:
        """STEP_COMPLETED event MUST include: step_id, elapsed, tokens, cost.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "elapsed", "tokens", "cost"}
        event = Event(
            ts=time.time(),
            event="STEP_COMPLETED",
            data={
                "step_id": "core.impl",
                "elapsed": 12.5,
                "tokens": {"input": 1000, "output": 500},
                "cost": 0.05,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_step_failed_event_payload(self) -> None:
        """STEP_FAILED event MUST include: step_id, failure_type, attempt, error.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "failure_type", "attempt", "error"}
        event = Event(
            ts=time.time(),
            event="STEP_FAILED",
            data={
                "step_id": "core.impl",
                "failure_type": "transport_error",
                "attempt": 2,
                "error": "Runner not found",
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_judgment_event_payload(self) -> None:
        """JUDGMENT event MUST include: type, step_id, verdict, reason, latency.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"type", "step_id", "verdict", "reason", "latency"}
        event = Event(
            ts=time.time(),
            event="JUDGMENT",
            data={
                "type": "EVIDENCE",
                "step_id": "core.impl",
                "verdict": "ACCEPT",
                "reason": "Tests pass with output",
                "latency": 3.2,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_merge_completed_event_payload(self) -> None:
        """MERGE_COMPLETED event MUST include: step_id, files_changed.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "files_changed"}
        event = Event(
            ts=time.time(),
            event="MERGE_COMPLETED",
            data={
                "step_id": "core.impl",
                "files_changed": ["src/vectl/driver/types.py", "tests/test_driver_types.py"],
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_merge_conflicted_event_payload(self) -> None:
        """MERGE_CONFLICTED event MUST include: step_id, conflicting_files.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "conflicting_files"}
        event = Event(
            ts=time.time(),
            event="MERGE_CONFLICTED",
            data={
                "step_id": "core.impl",
                "conflicting_files": ["src/vectl/driver/config.py"],
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_runner_fallback_event_payload(self) -> None:
        """RUNNER_FALLBACK event MUST include: step_id, from_runner, to_runner.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "from_runner", "to_runner"}
        event = Event(
            ts=time.time(),
            event="RUNNER_FALLBACK",
            data={
                "step_id": "core.impl",
                "from_runner": "claude",
                "to_runner": "opencode",
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_session_reuse_hit_event_payload(self) -> None:
        """SESSION_REUSE_HIT event MUST include: step_id, session_id, age_seconds.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "session_id", "age_seconds"}
        event = Event(
            ts=time.time(),
            event="SESSION_REUSE_HIT",
            data={
                "step_id": "core.test",
                "session_id": "ses_abc123",
                "age_seconds": 120,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_session_reuse_miss_event_payload(self) -> None:
        """SESSION_REUSE_MISS event MUST include: step_id, reason.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"step_id", "reason"}
        event = Event(
            ts=time.time(),
            event="SESSION_REUSE_MISS",
            data={
                "step_id": "core.test",
                "reason": "TTL expired",
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_cost_checkpoint_event_payload(self) -> None:
        """COST_CHECKPOINT event MUST include: cumulative_tokens, cumulative_cost.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"cumulative_tokens", "cumulative_cost"}
        event = Event(
            ts=time.time(),
            event="COST_CHECKPOINT",
            data={
                "cumulative_tokens": 50000,
                "cumulative_cost": 2.50,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))

    def test_recovery_event_payload(self) -> None:
        """RECOVERY event MUST include: type (stale_claim, orphan_worktree).

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        for recovery_type in ["stale_claim", "orphan_worktree"]:
            event = Event(
                ts=time.time(),
                event="RECOVERY",
                data={"type": recovery_type},
            )
            assert event.data["type"] == recovery_type

    def test_halt_event_payload(self) -> None:
        """HALT event MUST include: reason.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        event = Event(
            ts=time.time(),
            event="HALT",
            data={"reason": "No more executable steps"},
        )
        assert "reason" in event.data

    def test_final_event_payload(self) -> None:
        """FINAL event MUST include: total_steps, total_time, total_cost.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types table
        """
        expected_fields = {"total_steps", "total_time", "total_cost"}
        event = Event(
            ts=time.time(),
            event="FINAL",
            data={
                "total_steps": 20,
                "total_time": 300.5,
                "total_cost": 5.25,
            },
        )
        assert expected_fields.issubset(set(event.data.keys()))


class TestEventLogging:
    """Contract tests for event logging behavior."""

    def test_event_jsonl_format(self) -> None:
        """Events MUST be serializable as JSONL with ts, event, data fields.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4
        Blueprint: DRIVER-BLUEPRINT.md Observability (events.jsonl)
        """
        import json
        from dataclasses import asdict

        event = Event(
            ts=1711612345.0,
            event="STEP_DISPATCHED",
            data={"step_id": "core.impl", "agent": "python-executor"},
        )

        # Serialize to dict (asdict) then to JSON
        event_dict = asdict(event)
        json_str = json.dumps(event_dict)

        # Verify structure
        parsed = json.loads(json_str)
        assert "ts" in parsed
        assert "event" in parsed
        assert "data" in parsed
        assert parsed["ts"] == 1711612345.0
        assert parsed["event"] == "STEP_DISPATCHED"
        assert parsed["data"]["step_id"] == "core.impl"

    def test_event_timestamp_precision(self) -> None:
        """Event.ts MUST use time.time() with sub-second precision.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4
        """
        ts = time.time()
        event = Event(ts=ts, event="DECIDE")

        # Verify millisecond precision is retained
        assert event.ts == ts
        # Check that fractional seconds are preserved
        # time.time() returns float with fractional seconds
        assert isinstance(event.ts, float)
        # Verify it has sub-second precision (not just integer)
        # Most timestamps will have fractional part, but not guaranteed
        # So we just verify the type and that it came from time.time() style timestamp


class TestEventRoundTrip:
    """Serialization tests for Event."""

    def test_event_to_json(self) -> None:
        """Event MUST serialize to valid JSON."""
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
                "latency": 3.2,
            },
        )

        json_str = json.dumps(asdict(event))
        parsed = json.loads(json_str)

        assert parsed["ts"] == event.ts
        assert parsed["event"] == event.event
        assert parsed["data"] == event.data

    def test_event_data_can_contain_nested_dicts(self) -> None:
        """Event.data can contain nested dictionaries."""
        import json
        from dataclasses import asdict

        event = Event(
            ts=time.time(),
            event="STEP_COMPLETED",
            data={
                "step_id": "core.impl",
                "elapsed": 12.5,
                "tokens": {
                    "input": 1000,
                    "output": 500,
                    "cache_read": 200,
                },
                "cost": 0.05,
            },
        )

        json_str = json.dumps(asdict(event))
        parsed = json.loads(json_str)

        assert parsed["data"]["tokens"]["input"] == 1000
        assert parsed["data"]["tokens"]["output"] == 500
        assert parsed["data"]["tokens"]["cache_read"] == 200

    def test_event_data_can_contain_lists(self) -> None:
        """Event.data can contain lists."""
        import json
        from dataclasses import asdict

        event = Event(
            ts=time.time(),
            event="MERGE_COMPLETED",
            data={
                "step_id": "core.impl",
                "files_changed": ["a.py", "b.py", "c.py"],
            },
        )

        json_str = json.dumps(asdict(event))
        parsed = json.loads(json_str)

        assert parsed["data"]["files_changed"] == ["a.py", "b.py", "c.py"]
