# @invar:allow file_size: projection DTOs, replay persistence, and drive projection helpers stay co-located to preserve public artifact formats.
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
def _select_events(
    events: tuple[dict[str, object], ...],
    from_event_id: str | None,
) -> Result[tuple[dict[str, object], ...], Exception]:
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


# @shell_complexity: Branches preserve envelope versus mapping inputs, payload flattening, and default sequence/timestamp fields.
# @shell_orchestration: Event normalization is coupled to canonical envelope and legacy mapping replay inputs.
def _normalize_events(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
) -> Result[tuple[dict[str, object], ...], Exception]:
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


# @shell_complexity: Branches preserve Mapping, tuple key=value, and unknown payload behavior.
# @shell_orchestration: Payload parsing preserves legacy projection payload forms consumed by shell replay.
def _parse_payload(payload: object) -> Result[dict[str, object], Exception]:
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


# @shell_orchestration: Scalar coercion is coupled to legacy tuple payload replay compatibility.
def _coerce_scalar(value: str) -> Result[object, Exception]:
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


# @shell_complexity: Branches encode canonical event families and preserve projection replay outputs.
# @shell_orchestration: Projection reducer coordinates canonical event families into persisted read-model outputs.
def _derive_state(
    events: tuple[dict[str, object], ...],
    *,
    run_id_override: str | None,
) -> Result[tuple[RunStateView, dict[str, list[DerivedArtifact]], dict[str, list[DerivedArtifact]]], Exception]:
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

        # Drive event family projection handling
        if kind == "drive_status_changed":
            drive_status = str(event.get("status", ""))
            if drive_status in ("completed", "halted", "failed_unrecoverable", "stopped"):
                finished_at = timestamp
        elif kind == "child_run_admitted":
            active_execution_count += 1
        elif kind == "child_run_final":
            active_execution_count = max(0, active_execution_count - 1)
            child_status = str(event.get("status", ""))
            if child_status == "success" and status == "running":
                status = "success"
            elif child_status in ("fail", "stall", "transport_error"):
                transport_error_count += 1
                if child_status == "transport_error":
                    status = "transport_error"
        elif kind == "drive_barrier_entered":
            if "case_ids" in event:
                case_ids_val = event.get("case_ids", [])
                if isinstance(case_ids_val, (list, tuple)):
                    open_case_count += len(case_ids_val)
        elif kind == "drive_barrier_cleared":
            resolution = str(event.get("resolution", ""))
            if resolution in ("unblocked", "applied"):
                resolution_count += 1
        elif kind == "planner_applied":
            resolution_count += 1

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


def _optional_str(value: object) -> Result[str | None, Exception]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


# @shell_orchestration: Artifact extraction is coupled to projection replay metadata indexes.
def _artifact_from_event(
    *,
    event: Mapping[str, object],
    timestamp: float,
    default_step_id: str | None,
) -> Result[DerivedArtifact | None, Exception]:
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


# @shell_complexity: Branches preserve bool/int/float/string/fallback coercion behavior for event payloads.
# @shell_orchestration: Integer coercion preserves tolerant projection replay of persisted event payloads.
def _as_int(value: object, *, default: int) -> Result[int, Exception]:
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


# @shell_complexity: Branches preserve bool/numeric/string/fallback coercion behavior for event payloads.
# @shell_orchestration: Float coercion preserves tolerant projection replay of persisted event payloads.
def _as_float(value: object, *, default: float) -> Result[float, Exception]:
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


# @shell_orchestration: Status coercion is coupled to projection status compatibility values.
def _as_status(
    value: object,
) -> Result[Literal["pending", "running", "success", "fail", "stall", "transport_error"] | None, Exception]:
    normalized = _optional_str(value)
    if normalized in {"pending", "running", "success", "fail", "stall", "transport_error"}:
        return cast(
            Literal["pending", "running", "success", "fail", "stall", "transport_error"],
            normalized,
        )
    return None


# @shell_orchestration: Scope coercion is coupled to derived artifact projection defaults.
def _as_scope(value: object) -> Result[Literal["run", "step", "case"], Exception]:
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


# @shell_orchestration: latest.json payload shape is a shell projection artifact contract.
def _latest_payload(latest: RunStateView) -> Result[dict[str, object], Exception]:
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


# @shell_orchestration: summary.json payload shape is a shell projection artifact contract.
def _summary_payload(latest: RunStateView) -> Result[dict[str, object], Exception]:
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


# @shell_orchestration: metrics.json payload shape is a shell projection artifact contract.
def _metrics_payload(latest: RunStateView) -> Result[dict[str, object], Exception]:
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


# ---------------------------------------------------------------------
# Drive State Projection — scheduler-loop inputs from persisted state
# ---------------------------------------------------------------------
# Authority: docs/RFC-orch-drive.md sections 9, 10
#
# These functions rebuild drive scheduling facts (active_child_run_ids,
# frontier_step_ids, barrier state) from the persisted DriveStore for
# the scheduler loop to consume on resume/recover.

from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class DriveProjection:
    """Rebuilt drive scheduling facts for the scheduler loop.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    This projection is derived from the persisted DriveRecord and
    ChildRunRef entries. It is NOT authoritative by itself — it
    reflects the truth of what was persisted.

    Attributes:
        drive_id: Drive identifier this projection belongs to.
        status: Drive lifecycle status at projection time.
        active_child_run_ids: Rebuilt from child-run index: all
            child runs with status in ``{"pending", "running"}``.
        frontier_step_ids: Step IDs that have active child runs or
            are on the claimable frontier. Derived from persisted
            record + child run activity.
        active_step_child_runs: Per-step tuple of active child run refs.
        terminal_step_child_runs: Per-step tuple of terminal child run refs.
        barrier: Barrier state, if any, from the persisted drive record.
        operator_pause_state: Whether the drive is operator-paused.
        max_parallelism: Maximum concurrent step child runs allowed.
        summary: Human-readable summary from the persisted drive record.
    """

    drive_id: str
    status: str
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    active_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = ()
    terminal_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = ()
    barrier: object | None = None
    operator_pause_state: str = "active"
    max_parallelism: int = 4
    summary: str = ""


# @shell_orchestration: Drive projection coordinates persisted store reads into scheduler resume state.
def rebuild_drive_projection(
    store: object,
    drive_id: str,
) -> Result[DriveProjection, Exception]:
    """Rebuild drive scheduling facts from persisted store for the scheduler loop.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    This function takes a ``DriveStore`` and a ``drive_id``, reads the
    latest persisted ``DriveRecord`` and all ``ChildRunRef`` entries,
    and produces a ``DriveProjection`` that the scheduler loop can use
    as its initial state on resume/recover.

    The ``active_child_run_ids`` and per-step breakdown are always
    derived from the child-run index (the store of truth), NOT from
    the ``DriveRecord.active_child_run_ids`` field, which may be stale
    if the drive was interrupted.

    ``frontier_step_ids`` is taken from the persisted ``DriveRecord``
    because computing the DAG frontier requires plan graph authority
    (vectl core), which the projection layer does not access.

    Args:
        store: A ``DriveStore`` instance providing ``load_drive``,
            ``active_child_runs_for_drive``, and ``child_runs_for_drive``.
        drive_id: Drive identifier to project.

    Returns:
        ``DriveProjection`` with rebuilt scheduling facts.

    Raises:
        DriveStoreError: If no DriveRecord exists for the drive_id.
    """
    # Import here to avoid circular imports at module level
    from vectl.orchestration.run_store import DriveStore

    if not isinstance(store, DriveStore):
        raise TypeError(f"store must be a DriveStore instance, got {type(store).__name__}")

    persisted = store.load_drive(drive_id)
    if persisted is None:
        from vectl.orchestration.run_store import DriveStoreError

        raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

    return _rebuild_drive_projection_from_records(
        persisted, store.child_runs_for_drive(drive_id), store.active_child_runs_for_drive(drive_id)
    )


# @shell_complexity: Branches partition active/terminal child-run refs while preserving persisted DriveRecord authority.
# @shell_orchestration: Drive record projection preserves scheduler-facing active/terminal child-run indexes.
def _rebuild_drive_projection_from_records(
    persisted: object,
    all_refs: tuple[object, ...],
    active_refs: tuple[object, ...],
) -> Result[DriveProjection, Exception]:
    """Core projection logic, separated for testability without DriveStore.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    Args:
        persisted: The DriveRecord.
        all_refs: All ChildRunRef entries for the drive.
        active_refs: Active (pending/running) ChildRunRef entries for the drive.

    Returns:
        ``DriveProjection`` with rebuilt scheduling facts.
    """
    from vectl.orchestration.contracts import ChildRunRef, DriveBarrier, DriveRecord

    if not isinstance(persisted, DriveRecord):
        raise TypeError(f"persisted must be a DriveRecord instance, got {type(persisted).__name__}")

    active_ids = tuple(ref.run_id for ref in active_refs if isinstance(ref, ChildRunRef))

    # Partition active child runs by step_id
    active_by_step: dict[str, list] = {}
    for ref in active_refs:
        if isinstance(ref, ChildRunRef) and ref.kind == "step" and ref.step_id:
            active_by_step.setdefault(ref.step_id, []).append(ref)

    # Partition terminal (success/fail/cancelled) child runs by step_id
    terminal_by_step: dict[str, list] = {}
    for ref in all_refs:
        if (
            isinstance(ref, ChildRunRef)
            and ref.kind == "step"
            and ref.step_id
            and ref.status in ("success", "fail", "cancelled")
        ):
            terminal_by_step.setdefault(ref.step_id, []).append(ref)

    active_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = tuple(
        (step_id, tuple(refs)) for step_id, refs in sorted(active_by_step.items())
    )
    terminal_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = tuple(
        (step_id, tuple(refs)) for step_id, refs in sorted(terminal_by_step.items())
    )

    barrier_value: DriveBarrier | None = persisted.barrier

    return DriveProjection(
        drive_id=persisted.drive_id,
        status=persisted.status,
        active_child_run_ids=active_ids,
        frontier_step_ids=persisted.frontier_step_ids,
        active_step_child_runs=active_step_child_runs,
        terminal_step_child_runs=terminal_step_child_runs,
        barrier=barrier_value,
        operator_pause_state=persisted.operator_pause_state,
        max_parallelism=persisted.max_parallelism,
        summary=persisted.summary,
    )


def replay_drive_events(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
    artifact_root: Path,
    drive_id: str,
) -> Result[ReplayResult, Exception]:
    """Replay drive-scoped events into drive projection state artifacts.

    Authority: docs/RFC-orch-drive.md §16.3, §16.4
              docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §9.2

    This function replays drive events and persists drive-level state
    artifacts under ``<artifact_root>/drive/``. It extends the existing
    FileProjectionReplay semantics to handle drive-scoped event envelopes
    (those with a ``drive_id`` field).

    Args:
        events: Canonical drive events to replay.
        artifact_root: Directory where projection artifacts are persisted.
        drive_id: Drive identifier for scoping.

    Returns:
        ReplayResult with drive projection state and artifact metadata.
    """
    replay = FileProjectionReplay(events=events, artifact_root=artifact_root, run_id=drive_id)
    result = replay.replay_result()

    # Also persist drive-level summary artifacts per §16.4
    drive_dir = artifact_root / "drive"
    drive_dir.mkdir(parents=True, exist_ok=True)

    # Build and write drive summary from last projected state
    if replay._latest is not None:
        latest = replay._latest
        summary = {
            "drive_id": drive_id,
            "status": latest.status,
            "active_execution_count": latest.active_execution_count,
            "dispatch_count": latest.dispatch_count,
            "resolution_count": latest.resolution_count,
            "last_event_seq": latest.last_event_seq,
        }
        _write_json(drive_dir / "summary.json", summary)

        frontier = {
            "drive_id": drive_id,
            "active_step_id": latest.active_step_id,
            "open_case_count": latest.open_case_count,
        }
        _write_json(drive_dir / "frontier.json", frontier)

    return result


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
    "replay_drive_events",
]
