"""
RunStateView / projection replay and derived artifact contracts.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
(no dedicated section; uses contracts.py shared types)

Public surfaces (this module):
    - RunStateView               (joined read-query DTO for run state)
    - ProjectionReplay           (protocol for projection replay from event log)
    - DerivedArtifact           (schema for derived artifact produced from projection)
    - ReplayResult              (result of a projection replay operation)

Note: This module addresses the "projection replay and derived artifact contracts"
surface. The exact projection model (event-sourced vs. snapshot-based) is not
yet specified in the design docs; this module records the interface anchors
with documented gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# RunStateView — joined read-query DTO
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RunStateView:
    """
    Joined read-query DTO for run state inspection.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact shape and population sources for RunStateView are not
    yet specified. The fields below represent the known minimum anchor.

    Attributes:
        step_id: The step this run is associated with.
        agent: Agent that executed or is executing this run.
        status: Execution status of the run.
        started_at: Timestamp when the run started (if applicable).
        finished_at: Timestamp when the run finished (if applicable).
        output_summary: Human-readable summary of run output.
        derived_artifacts: Tuple of derived artifact references produced by this run.
    """

    step_id: str
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall"] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    output_summary: str = ""
    derived_artifacts: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Derived Artifact
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedArtifact:
    """
    Schema for a derived artifact produced from a projection.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact artifact taxonomy and metadata fields are not yet
    specified in the design docs.

    Attributes:
        artifact_ref: Reference identifier for the artifact.
        artifact_type: Type/classifier for the artifact.
        produced_by_step: Step that produced this artifact.
        produced_at: Timestamp when the artifact was produced.
        content_ref: Reference to the artifact content (file path, URI, etc.).
    """

    artifact_ref: str
    artifact_type: str
    produced_by_step: str
    produced_at: float
    content_ref: str


# ---------------------------------------------------------------------
# Projection Replay Protocol
# ---------------------------------------------------------------------
# GAP: The projection replay contract (event-sourced vs. snapshot-based)
# is not yet specified. This protocol is a forward contract stub.


class ProjectionReplay(Protocol):
    """
    Protocol for replaying projections from an event log.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact replay semantics (from-event-log, from-snapshot,
    incremental vs. full) are not yet specified. No concrete implementation
    should be added in this contract step.

    This protocol records the expected boundary role for projection replay.
    """

    def replay(self, from_event_id: str | None = None) -> tuple[RunStateView, ...]:
        """
        Replay a projection from the event log.

        Args:
            from_event_id: Optional event sequence/token to replay from
                (enables incremental replay). None means full replay.

        Returns:
            Tuple of RunStateView produced by the replay.

        Raises:
            NotImplementedError: Until replay semantics are specified.
        """
        ...

    def latest_state(self) -> RunStateView | None:
        """
        Return the latest derived state without full replay.

        Returns:
            The most recent RunStateView if available, else None.

        Raises:
            NotImplementedError: Until snapshot semantics are specified.
        """
        ...


# ---------------------------------------------------------------------
# Replay Result
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    """
    Result of a projection replay operation.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact result schema for replay operations is not yet
    specified. This schema represents the known minimum anchor.

    Attributes:
        run_states: Tuple of run states produced by the replay.
        last_event_id: Last event ID processed in this replay.
        is_incremental: True if this was an incremental (not full) replay.
    """

    run_states: tuple[RunStateView, ...]
    last_event_id: str | None = None
    is_incremental: bool = False


__all__ = [
    "RunStateView",
    "DerivedArtifact",
    "ProjectionReplay",
    "ReplayResult",
]
