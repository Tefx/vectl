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
from typing import Literal, Protocol, cast

from vectl.orchestration.events import OrchestrationEventEnvelope

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


def replay_events_to_artifacts(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
    artifact_root: Path,
    run_id: str | None = None,
    from_event_id: str | None = None,
) -> ReplayResult:
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


def _select_events(
    events: tuple[dict[str, object], ...],
    from_event_id: str | None,
) -> tuple[dict[str, object], ...]:
    if from_event_id is None:
        return events

    try:
        threshold = int(from_event_id)
    except ValueError:
        threshold = -1

    if threshold >= 0:
        return tuple(event for event in events if _as_int(event.get("seq"), default=0) > threshold)

    start_index = 0
    for index, event in enumerate(events):
        if str(event.get("event_id", "")) == from_event_id:
            start_index = index + 1
            break
    return events[start_index:]


def _normalize_events(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
) -> tuple[dict[str, object], ...]:
    normalized: list[dict[str, object]] = []
    for index, event in enumerate(events, start=1):
        if isinstance(event, OrchestrationEventEnvelope):
            data = {
                "seq": index,
                "event_id": str(index),
                "kind": event.kind,
                "timestamp": event.timestamp.timestamp(),
                "step_id": event.step_id,
                "agent": event.agent,
            }
            data.update(_parse_payload(event.payload))
            normalized.append(data)
            continue

        data = dict(event)
        payload = data.pop("payload", None)
        if payload is not None:
            data.update(_parse_payload(payload))
        if "seq" not in data:
            data["seq"] = index
        if "event_id" not in data:
            data["event_id"] = str(data["seq"])
        if "timestamp" not in data:
            data["timestamp"] = 0.0
        normalized.append(data)
    return tuple(normalized)


def _parse_payload(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, tuple):
        parsed: dict[str, object] = {}
        for token in payload:
            if not isinstance(token, str) or "=" not in token:
                continue
            key, value = token.split("=", 1)
            parsed[key] = _coerce_scalar(value)
        return parsed
    return {}


def _coerce_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _derive_state(
    events: tuple[dict[str, object], ...],
    *,
    run_id_override: str | None,
) -> tuple[RunStateView, dict[str, list[DerivedArtifact]], dict[str, list[DerivedArtifact]]]:
    run_id = run_id_override or ""
    step_id = ""
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall", "transport_error"] | None = (
        "pending"
    )
    started_at: float | None = None
    finished_at: float | None = None
    active_step_id: str | None = None
    open_case_count = 0
    active_execution_count = 0
    active_lease_count = 0
    dispatch_count = 0
    resolution_count = 0
    operator_required_case_count = 0
    transport_error_count = 0
    active_execution_peak = 0
    total_resolver_tokens = 0
    total_estimated_cost_usd = 0.0
    output_summary = ""
    artifacts: list[DerivedArtifact] = []
    step_index: dict[str, list[DerivedArtifact]] = {}
    case_index: dict[str, list[DerivedArtifact]] = {}
    runtimes: list[float] = []
    started_by_step: dict[str, float] = {}

    for event in events:
        kind = str(event.get("kind", ""))
        timestamp = _as_float(event.get("timestamp"), default=0.0)
        event_step_id = _optional_str(event.get("step_id"))
        if event_step_id:
            step_id = event_step_id
            active_step_id = event_step_id
        if not run_id and "run_id" in event:
            run_id = str(event["run_id"])
        if agent is None and event.get("agent") is not None:
            agent = str(event["agent"])
        next_status = _as_status(event.get("status"))
        if next_status is not None:
            status = next_status

        if kind == "runtime_start":
            started_at = started_at if started_at is not None else timestamp
            status = "running"
            if event_step_id:
                started_by_step[event_step_id] = timestamp
            active_execution_count += 1
        elif kind == "runtime_collect":
            active_execution_count = max(0, active_execution_count - 1)
            if status == "running":
                status = "success"
            if event_step_id and event_step_id in started_by_step:
                runtimes.append(max(0.0, timestamp - started_by_step.pop(event_step_id)))
        elif kind == "control_dispatch":
            dispatch_count += 1
        elif kind == "control_resolve":
            resolution_count += 1

        if kind == "roster_claim":
            active_lease_count += 1
        elif kind == "roster_release":
            active_lease_count = max(0, active_lease_count - 1)

        if str(event.get("status", "")) == "transport_error":
            transport_error_count += 1
            status = "transport_error"

        if str(event.get("resolution_status", "")) == "operator_required":
            operator_required_case_count += 1

        if "open_case_count" in event:
            open_case_count = _as_int(event.get("open_case_count"), default=open_case_count)
        if "open_case_delta" in event:
            open_case_count += _as_int(event.get("open_case_delta"), default=0)

        if "resolver_tokens" in event:
            total_resolver_tokens += _as_int(event.get("resolver_tokens"), default=0)
        if "estimated_cost_usd" in event:
            total_estimated_cost_usd += _as_float(event.get("estimated_cost_usd"), default=0.0)

        if "output_summary" in event:
            output_summary = str(event["output_summary"])

        active_execution_peak = max(active_execution_peak, active_execution_count)
        finished_at = timestamp

        artifact = _artifact_from_event(
            event=event, timestamp=timestamp, default_step_id=event_step_id
        )
        if artifact is None:
            continue

        artifacts.append(artifact)
        if artifact.produced_by_step:
            step_index.setdefault(artifact.produced_by_step, []).append(artifact)
        if artifact.case_id:
            case_index.setdefault(artifact.case_id, []).append(artifact)

    average_runtime = sum(runtimes) / len(runtimes) if runtimes else 0.0
    last_seq = _as_int(events[-1].get("seq"), default=0) if events else 0

    return (
        RunStateView(
            step_id=step_id,
            run_id=run_id,
            agent=agent,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            active_step_id=active_step_id,
            open_case_count=open_case_count,
            active_execution_count=active_execution_count,
            active_lease_count=active_lease_count,
            last_event_seq=last_seq,
            dispatch_count=dispatch_count,
            resolution_count=resolution_count,
            operator_required_case_count=operator_required_case_count,
            transport_error_count=transport_error_count,
            active_execution_peak=active_execution_peak,
            average_step_runtime_seconds=average_runtime,
            total_resolver_tokens=total_resolver_tokens,
            total_estimated_cost_usd=total_estimated_cost_usd,
            output_summary=output_summary,
            derived_artifacts=tuple(artifact.artifact_ref for artifact in artifacts),
        ),
        step_index,
        case_index,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _artifact_from_event(
    *,
    event: Mapping[str, object],
    timestamp: float,
    default_step_id: str | None,
) -> DerivedArtifact | None:
    if "artifact_ref" not in event:
        return None

    metadata_entries: list[tuple[str, str]] = []
    raw_metadata = event.get("artifact_metadata")
    if isinstance(raw_metadata, Mapping):
        metadata_entries = [(str(key), str(value)) for key, value in raw_metadata.items()]

    case_id = _optional_str(event.get("case_id"))
    produced_by_step = _optional_str(event.get("artifact_step_id")) or default_step_id or ""
    artifact_scope = _as_scope(event.get("artifact_scope"))

    return DerivedArtifact(
        artifact_ref=str(event["artifact_ref"]),
        artifact_type=str(event.get("artifact_type", "generic")),
        produced_by_step=produced_by_step,
        produced_at=timestamp,
        content_ref=str(event.get("content_ref", "")),
        scope=artifact_scope,
        case_id=case_id,
        metadata=tuple(metadata_entries),
    )


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _as_status(
    value: object,
) -> Literal["pending", "running", "success", "fail", "stall", "transport_error"] | None:
    normalized = _optional_str(value)
    if normalized in {"pending", "running", "success", "fail", "stall", "transport_error"}:
        return cast(
            Literal["pending", "running", "success", "fail", "stall", "transport_error"],
            normalized,
        )
    return None


def _as_scope(value: object) -> Literal["run", "step", "case"]:
    normalized = _optional_str(value)
    if normalized == "step":
        return "step"
    if normalized == "case":
        return "case"
    return "run"


def _persist_projection(artifact_root: Path, latest: RunStateView) -> None:
    state_dir = artifact_root / _STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    latest_payload = _latest_payload(latest)
    summary_payload = _summary_payload(latest)
    metrics_payload = _metrics_payload(latest)
    _write_json(artifact_root / _LATEST_PATH, latest_payload)
    _write_json(artifact_root / _SUMMARY_PATH, summary_payload)
    _write_json(artifact_root / _METRICS_PATH, metrics_payload)


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _latest_payload(latest: RunStateView) -> dict[str, object]:
    return {
        "version": 1,
        "run_id": latest.run_id,
        "status": latest.status,
        "started_at": latest.started_at,
        "finished_at": latest.finished_at,
        "active_step_id": latest.active_step_id,
        "open_case_count": latest.open_case_count,
        "active_execution_count": latest.active_execution_count,
        "active_lease_count": latest.active_lease_count,
        "last_event_seq": latest.last_event_seq,
        "projection_health": latest.projection_health,
        "projection_code": latest.projection_code,
        "projection_detail": latest.projection_detail,
    }


def _summary_payload(latest: RunStateView) -> dict[str, object]:
    return {
        "version": 1,
        "run_id": latest.run_id,
        "status": latest.status,
        "active_step_id": latest.active_step_id,
        "open_case_count": latest.open_case_count,
        "active_execution_count": latest.active_execution_count,
        "active_lease_count": latest.active_lease_count,
        "last_event_seq": latest.last_event_seq,
    }


def _metrics_payload(latest: RunStateView) -> dict[str, object]:
    return {
        "version": 1,
        "run_id": latest.run_id,
        "dispatch_count": latest.dispatch_count,
        "resolution_count": latest.resolution_count,
        "operator_required_case_count": latest.operator_required_case_count,
        "transport_error_count": latest.transport_error_count,
        "active_execution_peak": latest.active_execution_peak,
        "average_step_runtime_seconds": latest.average_step_runtime_seconds,
        "total_resolver_tokens": latest.total_resolver_tokens,
        "total_estimated_cost_usd": latest.total_estimated_cost_usd,
    }


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
]
