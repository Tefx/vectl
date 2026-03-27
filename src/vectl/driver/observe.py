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

from dataclasses import dataclass, field
from typing import Protocol


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
