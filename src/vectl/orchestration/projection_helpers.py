"""Projection reducer and payload helpers.

Extracted from projections compatibility module.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, cast

from vectl.orchestration.events import OrchestrationEventEnvelope
from vectl.orchestration.projections import DerivedArtifact, RunStateView

_STATE_DIR = "state"
_LATEST_PATH = "state/latest.json"
_SUMMARY_PATH = "state/summary.json"
_METRICS_PATH = "state/metrics.json"

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
