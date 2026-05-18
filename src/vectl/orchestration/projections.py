"""Projection replay from canonical events into derived state artifacts.

Authority:
    - tests/repro/test_orch_observability_red.py section "Projection Replay Tests (§9.2)"
    - task contract: implement replay into state/latest.json, state/summary.json,
      state/metrics.json with projection stale diagnosability.

This module implements deterministic replay of canonical orchestration events
into read-model artifacts. The projection outputs are derived artifacts and are
not authoritative source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from typing_extensions import TypeAliasType

from vectl.orchestration.events import OrchestrationEventEnvelope

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)
Result = TypeAliasType("Result", Any, type_params=(_T, _E))

PROJECTION_STALE = "PROJECTION_STALE"
_STATE_DIR = "state"
_LATEST_PATH = "state/latest.json"
_SUMMARY_PATH = "state/summary.json"
_METRICS_PATH = "state/metrics.json"


# ---------------------------------------------------------------------
# RunStateView — joined read-query DTO
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RunStateView:
    """
    Joined read-query DTO for run state inspection.

    Authority:
        tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_state_latest_json_schema

    Projection state is derived and replayable from event logs; this view is a
    snapshot for inspection surfaces.

    Attributes:
        step_id: The step this run is associated with.
        run_id: Unique run identifier.
        agent: Agent that executed or is executing this run.
        status: Execution status of the run.
        started_at: Timestamp when the run started (if applicable).
        finished_at: Timestamp when the run finished (if applicable).
        active_step_id: Current active step from replay.
        open_case_count: Count of unresolved cases.
        active_execution_count: Current active execution count.
        active_lease_count: Current active lease count.
        last_event_seq: Sequence number of latest replayed event.
        dispatch_count: Total dispatch events.
        resolution_count: Total resolution events.
        operator_required_case_count: Cases requiring operator action.
        transport_error_count: Runtime transport errors observed.
        active_execution_peak: High-water mark of active executions.
        average_step_runtime_seconds: Average step runtime in seconds.
        total_resolver_tokens: Total resolver tokens consumed.
        total_estimated_cost_usd: Total estimated resolver cost.
        output_summary: Human-readable summary of run output.
        derived_artifacts: Tuple of derived artifact references produced by this run.
        projection_health: "fresh" unless persistence fails.
        projection_code: Projection failure code for diagnostics.
        projection_detail: Failure detail for diagnostics.
    """

    step_id: str
    run_id: str = ""
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall", "transport_error"] | None = (
        None
    )
    started_at: float | None = None
    finished_at: float | None = None
    active_step_id: str | None = None
    open_case_count: int = 0
    active_execution_count: int = 0
    active_lease_count: int = 0
    last_event_seq: int = 0
    dispatch_count: int = 0
    resolution_count: int = 0
    operator_required_case_count: int = 0
    transport_error_count: int = 0
    active_execution_peak: int = 0
    average_step_runtime_seconds: float = 0.0
    total_resolver_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    output_summary: str = ""
    derived_artifacts: tuple[str, ...] = ()
    projection_health: Literal["fresh", "stale"] = "fresh"
    projection_code: str | None = None
    projection_detail: str | None = None


# ---------------------------------------------------------------------
# Derived Artifact
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedArtifact:
    """
    Schema for a derived artifact produced from a projection.

    Authority:
        task contract: step/case artifact metadata hooks for inspection surfaces.

    Attributes:
        artifact_ref: Reference identifier for the artifact.
        artifact_type: Type/classifier for the artifact.
        produced_by_step: Step that produced this artifact.
        produced_at: Timestamp when the artifact was produced.
        content_ref: Reference to the artifact content (file path, URI, etc.).
        scope: Inspection scope for metadata lookup.
        case_id: Case identifier when artifact belongs to a case surface.
        metadata: Stable key/value metadata for inspection surfaces.
    """

    artifact_ref: str
    artifact_type: str
    produced_by_step: str
    produced_at: float
    content_ref: str
    scope: Literal["run", "step", "case"] = "run"
    case_id: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProjectionReplayDiagnostics:
    """Diagnosability payload for projection replay outcomes.

    Args:
        code: Stable machine-readable replay code.
        message: Human-readable failure summary.
        artifact_path: Path that failed during persistence.
        last_event_seq: Last replayed event sequence.
    """

    code: str
    message: str
    artifact_path: str | None = None
    last_event_seq: int | None = None


class ProjectionStaleError(RuntimeError):
    """Projection persistence failure surfaced as PROJECTION_STALE."""

    code: str = PROJECTION_STALE

    def __init__(
        self,
        message: str,
        *,
        artifact_path: str | None = None,
        last_event_seq: int | None = None,
    ) -> None:
        super().__init__(message)
        self.artifact_path = artifact_path
        self.last_event_seq = last_event_seq


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

    Authority:
        task contract: replay emits derived artifacts and inspection metadata.

    Attributes:
        run_states: Tuple of run states produced by the replay.
        last_event_id: Last event ID processed in this replay.
        is_incremental: True if this was an incremental (not full) replay.
        artifact_paths: Paths of persisted projection artifacts.
        step_artifacts: Step-scoped artifact metadata hooks.
        case_artifacts: Case-scoped artifact metadata hooks.
        diagnostics: Projection diagnostics when replay is stale.
    """

    run_states: tuple[RunStateView, ...]
    last_event_id: str | None = None
    is_incremental: bool = False
    artifact_paths: tuple[str, ...] = (_LATEST_PATH, _SUMMARY_PATH, _METRICS_PATH)
    step_artifacts: tuple[tuple[str, tuple[DerivedArtifact, ...]], ...] = ()
    case_artifacts: tuple[tuple[str, tuple[DerivedArtifact, ...]], ...] = ()
    diagnostics: ProjectionReplayDiagnostics | None = None


@dataclass(frozen=True)
class FileProjectionReplay:
    """Concrete projection replay that persists derived state artifacts.

    Args:
        events: Canonical orchestration events used as replay input.
        artifact_root: Root directory where ``state/*.json`` will be written.
        run_id: Optional run identifier override.
    """

    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...]
    artifact_root: Path
    run_id: str | None = None
    _latest: RunStateView | None = field(default=None, init=False, repr=False)
    _step_index: dict[str, list[DerivedArtifact]] = field(
        default_factory=dict, init=False, repr=False
    )
    _case_index: dict[str, list[DerivedArtifact]] = field(
        default_factory=dict, init=False, repr=False
    )

    def replay(self, from_event_id: str | None = None) -> tuple[RunStateView, ...]:
        """Replay events and persist latest/summary/metrics artifacts.

        Args:
            from_event_id: Optional event sequence token for incremental replay.

        Returns:
            Tuple containing one latest run state view.

        Raises:
            ProjectionStaleError: If writing derived artifacts fails.
        """

        normalized = _normalize_events(self.events)
        selected = _select_events(normalized, from_event_id)
        latest, step_index, case_index = _derive_state(selected, run_id_override=self.run_id)
        object.__setattr__(self, "_latest", latest)
        object.__setattr__(self, "_step_index", step_index)
        object.__setattr__(self, "_case_index", case_index)

        try:
            _persist_projection(self.artifact_root, latest)
        except OSError as error:
            stale = replace(
                latest,
                projection_health="stale",
                projection_code=PROJECTION_STALE,
                projection_detail=str(error),
            )
            object.__setattr__(self, "_latest", stale)
            raise ProjectionStaleError(
                "Projection persistence failed; derived state is stale",
                artifact_path=str(self.artifact_root / _STATE_DIR),
                last_event_seq=latest.last_event_seq,
            ) from error

        return (latest,)

    def latest_state(self) -> RunStateView | None:
        """Return latest replayed state if available."""

        return self._latest

    def replay_result(self, from_event_id: str | None = None) -> ReplayResult:
        """Replay and return run state with artifact metadata hooks.

        Args:
            from_event_id: Optional event sequence token for incremental replay.

        Returns:
            ReplayResult including step/case artifact hooks and diagnostics.
        """

        try:
            run_states = self.replay(from_event_id=from_event_id)
            diagnostics: ProjectionReplayDiagnostics | None = None
        except ProjectionStaleError as error:
            if self._latest is None:
                raise
            run_states = (self._latest,)
            diagnostics = ProjectionReplayDiagnostics(
                code=error.code,
                message=str(error),
                artifact_path=error.artifact_path,
                last_event_seq=error.last_event_seq,
            )

        return ReplayResult(
            run_states=run_states,
            last_event_id=str(run_states[-1].last_event_seq) if run_states else None,
            is_incremental=from_event_id is not None,
            artifact_paths=(_LATEST_PATH, _SUMMARY_PATH, _METRICS_PATH),
            step_artifacts=tuple(
                (step_id, tuple(artifacts))
                for step_id, artifacts in sorted(self._step_index.items())
            ),
            case_artifacts=tuple(
                (case_id, tuple(artifacts))
                for case_id, artifacts in sorted(self._case_index.items())
            ),
            diagnostics=diagnostics,
        )

    def step_artifacts(self, step_id: str) -> tuple[DerivedArtifact, ...]:
        """Return step-scoped artifact metadata for inspection surfaces."""

        return tuple(self._step_index.get(step_id, ()))

    def case_artifacts(self, case_id: str) -> tuple[DerivedArtifact, ...]:
        """Return case-scoped artifact metadata for inspection surfaces."""

        return tuple(self._case_index.get(case_id, ()))


# @shell_orchestration: Replay boundary coordinates projection reducer and artifact persistence surfaces.
def replay_events_to_artifacts(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
    artifact_root: Path,
    run_id: str | None = None,
    from_event_id: str | None = None,
) -> Result[ReplayResult, Exception]:
    """Convenience replay boundary for canonical events.

    Args:
        events: Canonical events to replay.
        artifact_root: Directory where projection artifacts are persisted.
        run_id: Optional run identifier override.
        from_event_id: Optional incremental replay token.

    Returns:
        ReplayResult with projection state and artifact metadata.
    """

    replay = FileProjectionReplay(events=events, artifact_root=artifact_root, run_id=run_id)
    return replay.replay_result(from_event_id=from_event_id)


# @shell_complexity: Branches preserve full replay, numeric cursor, and event-id cursor selection semantics.
# @shell_orchestration: Event selection is coupled to projection replay cursor compatibility.
from vectl.orchestration.projection_helpers import _artifact_from_event, _as_float, _as_int, _as_scope, _as_status, _coerce_scalar, _derive_state, _latest_payload, _metrics_payload, _normalize_events, _optional_str, _parse_payload, _persist_projection, _select_events, _summary_payload, _write_json
from vectl.orchestration.drive_projections import DriveProjection, rebuild_drive_projection, replay_drive_events, _rebuild_drive_projection_from_records

__all__ = [
    "PROJECTION_STALE",
    "RunStateView",
    "DerivedArtifact",
    "ProjectionReplayDiagnostics",
    "ProjectionStaleError",
    "ProjectionReplay",
    "ReplayResult",
    "FileProjectionReplay",
    "replay_events_to_artifacts",
    "DriveProjection",
    "rebuild_drive_projection",
    "_rebuild_drive_projection_from_records",
    "replay_drive_events",
]
