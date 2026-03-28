"""Event emitter and JSONL writer.

Responsibility: Append structured events to a JSONL file. Optionally print
human-readable progress lines to stderr.

Non-responsibility: Does NOT interpret events. Does NOT aggregate metrics
(that is the replay tool's job).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.4
Blueprint Reference: DRIVER-BLUEPRINT.md Observability (observe.py)

All judgment calls are logged with full input/output for audit trail.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .config import ObservabilityConfig


@dataclass
class Event:
    """Event envelope for JSONL logging.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Event schema
    Blueprint: DRIVER-BLUEPRINT.md Observability Event Types

    Event types and their key fields:
        DECIDE:           running_count, claimable, capacity, actions
        STEP_DISPATCHED: step_id, agent, runner, session_reuse
        STEP_COMPLETED:  step_id, elapsed, tokens, cost
        STEP_FAILED:     step_id, failure_type, attempt, error
        JUDGMENT:        type, step_id, verdict, reason, latency
        MERGE_COMPLETED: step_id, files_changed
        MERGE_CONFLICTED: step_id, conflicting_files
        RUNNER_FALLBACK: step_id, from_runner, to_runner
        SESSION_REUSE_HIT: step_id, session_id, age_seconds
        SESSION_REUSE_MISS: step_id, reason
        COST_CHECKPOINT: cumulative_tokens, cumulative_cost
        RECOVERY:        type (stale_claim, orphan_worktree)
        HALT:            reason
        FINAL:           total_steps, total_time, total_cost
    """

    ts: float  # time.time()
    event: str  # Event type name (see event table above)
    data: dict[str, object] = field(default_factory=dict)


class Observer(Protocol):
    """Protocol for emitting structured events.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer Protocol
    Blueprint: DRIVER-BLUEPRINT.md Observability (observer.emit(...))
    """

    def emit(self, event_type: str, /, **data: object) -> None:
        """Append a timestamped event. Thread-safe (called from asyncio, single-threaded).

        Args:
            event_type: Event type name (e.g., "DECIDE", "STEP_DISPATCHED", etc.)
            **data: Event-specific key-value data.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer.emit
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types
        """
        ...

    def close(self) -> None:
        """Flush and close the JSONL file handle.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer.close
        Blueprint: DRIVER-BLUEPRINT.md shutdown (observer.close())
        """
        ...


class FileObserver:
    """Concrete Observer implementation that writes events to a JSONL file.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer implementation
    Blueprint: DRIVER-BLUEPRINT.md Observability (events.jsonl)

    This is the primary observer implementation used by the driver.
    Events are appended as JSON lines to the configured file.
    """

    def __init__(
        self,
        config: ObservabilityConfig,
        *,
        file_handle: IO[str] | None = None,
    ) -> None:
        """Initialize the file observer.

        Args:
            config: Observability configuration with events_file path.
            file_handle: Optional pre-opened file handle (for testing).
                If not provided, opens config.events_file for appending.
        """
        self._config = config
        self._file: IO[str] | None = file_handle
        self._owns_file = file_handle is None

    def _ensure_file(self) -> IO[str]:
        """Lazily open the file handle on first use."""
        if self._file is None:
            path = Path(self._config.events_file)
            # Create parent directories if needed
            path.parent.mkdir(parents=True, exist_ok=True)
            # Open in append mode for JSONL logging
            self._file = open(path, "a", encoding="utf-8")
        return self._file

    def emit(self, event_type: str, /, **data: object) -> None:
        """Append a timestamped event to the JSONL file.

        Thread-safe for single-threaded async usage (no concurrent writes
        expected from a single driver loop).

        Args:
            event_type: Event type name (e.g., "DECIDE", "STEP_DISPATCHED", etc.)
            **data: Event-specific key-value data.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer.emit
        Blueprint: DRIVER-BLUEPRINT.md Observability Event Types
        """
        event = Event(
            ts=time.time(),
            event=event_type,
            data={k: self._serialize_value(v) for k, v in data.items()},
        )
        line = json.dumps(
            {
                "ts": event.ts,
                "event": event.event,
                "data": event.data,
            }
        )
        f = self._ensure_file()
        f.write(line + "\n")
        f.flush()  # Ensure immediate write for audit trail

    def _serialize_value(self, value: object) -> object:
        """Serialize a value to JSON-compatible format."""
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]
        # For other types, convert to string
        return str(value)

    def close(self) -> None:
        """Flush and close the JSONL file handle.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4, Observer.close
        Blueprint: DRIVER-BLUEPRINT.md shutdown (observer.close())
        """
        if self._file is not None and self._owns_file:
            self._file.flush()
            self._file.close()
            self._file = None


class NullObserver:
    """Observer implementation that discards all events.

    Useful for testing or when observability is disabled.
    """

    def emit(self, event_type: str, /, **data: object) -> None:
        """Discard the event."""
        pass

    def close(self) -> None:
        """Nothing to close."""
        pass


def create_observer(config: ObservabilityConfig) -> Observer:
    """Create an observer instance based on configuration.

    Args:
        config: Observability configuration.

    Returns:
        FileObserver instance configured with the events_file path.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.4
    """
    return FileObserver(config)
