"""Pure projection replay reducer extraction.

Decision row: ``src/vectl/orchestration/projections.py`` structural Core extraction.

>>> derive_state(({"sequence": 1, "kind": "run_started", "payload": {}},))["event_count"]
1

>>> select_events(({"sequence": -1},), start_sequence=0)  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda events, start_sequence=0, end_sequence=None: start_sequence >= 0 and (end_sequence is None or end_sequence >= start_sequence) and all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, tuple) and all(int(event.get("sequence", -1)) >= 0 for event in result))
def select_events(
    events: Sequence[Mapping[str, object]],
    start_sequence: int = 0,
    end_sequence: int | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Select a monotonic event sequence from already-loaded records.
    
    >>> select_events(({"sequence": 1}, {"sequence": 2}), start_sequence=2)
    ({'sequence': 2},)
    """
    selected = tuple(
        dict(event)
        for event in events
        if int(event.get("sequence", event.get("seq", 0))) >= start_sequence
        and (end_sequence is None or int(event.get("sequence", event.get("seq", 0))) <= end_sequence)
    )
    return selected


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, tuple) and all("payload" in event and "kind" in event for event in result))
def normalize_events(events: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Normalize mapping/envelope event records without Shell replay.
    
    >>> normalize_events(({"sequence": 1, "kind": "run", "payload": {"run_id": "r"}},))[0]["run_id"]
    'r'
    """
    normalized: list[dict[str, object]] = []
    for index, event in enumerate(events, start=1):
        data = dict(event)
        payload = data.pop("payload", None)
        data.update(parse_payload(payload if isinstance(payload, Mapping) else None))
        data["payload"] = parse_payload(payload if isinstance(payload, Mapping) else None)
        if not str(data.get("kind", "")).strip():
            data["kind"] = str(data.get("type", "event"))
        data.setdefault("sequence", data.get("seq", index))
        data.setdefault("seq", data.get("sequence", index))
        data.setdefault("event_id", str(data.get("seq", index)))
        data.setdefault("timestamp", 0.0)
        normalized.append(data)
    return tuple(normalized)


@pre(lambda payload: payload is None or isinstance(payload, Mapping))
@post(lambda result: isinstance(result, Mapping))
def parse_payload(payload: Mapping[str, object] | None) -> Mapping[str, object]:
    """Coerce optional event payload data to a mapping.
    
    >>> parse_payload({"a": 1})
    {'a': 1}
    """
    return dict(payload or {})


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or isinstance(result, str | int | float | bool))
def coerce_scalar(value: object, field_name: str) -> str | int | float | bool | None:
    """Coerce a projection scalar for a named field.
    
    >>> coerce_scalar("true", "flag")
    True
    """
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def derive_state(events: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    """Derive deterministic latest run state from normalized events.
    
    >>> derive_state(({"sequence": 1, "kind": "control_dispatch", "payload": {}},))["dispatch_count"]
    1
    """
    state = _initial_projection_state(len(events))
    active_count = 0
    artifacts: list[str] = []
    for event in normalize_events(events):
        active_count = _apply_projection_event(state, event, active_count)
        artifact = artifact_from_event(event)
        if artifact is not None:
            artifacts.append(str(artifact["artifact_ref"]))
    state["derived_artifacts"] = tuple(artifacts)
    return state


@pre(lambda event_count: event_count >= 0)
@post(lambda result: isinstance(result, dict) and int(result.get("event_count", -1)) >= 0)
def _initial_projection_state(event_count: int) -> dict[str, object]:
    return {
        "step_id": "",
        "run_id": "",
        "agent": None,
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "active_step_id": None,
        "open_case_count": 0,
        "active_execution_count": 0,
        "active_lease_count": 0,
        "last_event_seq": 0,
        "dispatch_count": 0,
        "resolution_count": 0,
        "operator_required_case_count": 0,
        "transport_error_count": 0,
        "active_execution_peak": 0,
        "average_step_runtime_seconds": 0.0,
        "total_resolver_tokens": 0,
        "total_estimated_cost_usd": 0.0,
        "output_summary": "",
        "derived_artifacts": (),
        "projection_health": "fresh",
        "projection_code": None,
        "projection_detail": None,
        "event_count": event_count,
    }


@pre(lambda state, event, active_count: isinstance(state, dict) and isinstance(event, Mapping) and active_count >= 0)
@post(lambda result: isinstance(result, int) and result >= 0)
def _apply_projection_event(
    state: dict[str, object], event: Mapping[str, object], active_count: int
) -> int:
    kind = str(event.get("kind", ""))
    state["last_event_seq"] = as_int(event.get("seq", event.get("sequence", 0)), "sequence") or 0
    if event.get("step_id") is not None:
        state["step_id"] = str(event["step_id"])
        state["active_step_id"] = str(event["step_id"])
    if event.get("run_id") is not None and not state["run_id"]:
        state["run_id"] = str(event["run_id"])
    if event.get("agent") is not None and state["agent"] is None:
        state["agent"] = str(event["agent"])
    status = as_status(event.get("status"))
    if status is not None:
        state["status"] = status
    active_count = _apply_lifecycle_counts(state, event, kind, active_count)
    _apply_metric_fields(state, event)
    state["active_execution_count"] = active_count
    state["active_execution_peak"] = max(int(state["active_execution_peak"]), active_count)
    state["finished_at"] = as_float(event.get("timestamp"), "timestamp")
    return active_count


@pre(lambda state, event, kind, active_count: isinstance(state, dict) and isinstance(event, Mapping) and bool(kind) and active_count >= 0)
@post(lambda result: isinstance(result, int) and result >= 0)
def _apply_lifecycle_counts(
    state: dict[str, object], event: Mapping[str, object], kind: str, active_count: int
) -> int:
    if kind in {"runtime_start", "child_run_admitted"}:
        active_count += 1
        state["status"] = "running"
        state["started_at"] = state["started_at"] or as_float(event.get("timestamp"), "timestamp")
    if kind in {"runtime_collect", "child_run_final"}:
        active_count = max(0, active_count - 1)
    if kind == "control_dispatch":
        state["dispatch_count"] = int(state["dispatch_count"]) + 1
    if kind in {"control_resolve", "drive_barrier_cleared", "planner_applied"}:
        state["resolution_count"] = int(state["resolution_count"]) + 1
    if str(event.get("resolution_status", "")) == "operator_required":
        state["operator_required_case_count"] = int(state["operator_required_case_count"]) + 1
    if str(event.get("status", "")) == "transport_error":
        state["transport_error_count"] = int(state["transport_error_count"]) + 1
        state["status"] = "transport_error"
    return active_count


@pre(lambda state, event: isinstance(state, dict) and len(state) > 0 and isinstance(event, Mapping))
@post(lambda result: result is None)
def _apply_metric_fields(state: dict[str, object], event: Mapping[str, object]) -> None:
    if "open_case_count" in event:
        state["open_case_count"] = as_int(event["open_case_count"], "open_case_count") or 0
    if "open_case_delta" in event:
        state["open_case_count"] = int(state["open_case_count"]) + (as_int(event["open_case_delta"], "open_case_delta") or 0)
    if "resolver_tokens" in event:
        state["total_resolver_tokens"] = int(state["total_resolver_tokens"]) + (as_int(event["resolver_tokens"], "resolver_tokens") or 0)
    if "estimated_cost_usd" in event:
        state["total_estimated_cost_usd"] = float(state["total_estimated_cost_usd"]) + (as_float(event["estimated_cost_usd"], "estimated_cost_usd") or 0.0)
    if "output_summary" in event:
        state["output_summary"] = str(event["output_summary"])


@pre(lambda event: isinstance(event, Mapping) and bool(str(event.get("kind", "")).strip()))
@post(lambda result: result is None or (isinstance(result, Mapping) and bool(result.get("path"))))
def artifact_from_event(event: Mapping[str, object]) -> Mapping[str, object] | None:
    """Extract a projection artifact reference from one event.
    
    >>> artifact_from_event({"kind": "artifact", "artifact_ref": "a", "content_ref": "x"})["path"]
    'x'
    """
    if "artifact_ref" not in event:
        return None
    return {
        "artifact_ref": str(event["artifact_ref"]),
        "artifact_type": str(event.get("artifact_type", "generic")),
        "produced_by_step": str(event.get("artifact_step_id", event.get("step_id", ""))),
        "produced_at": as_float(event.get("timestamp", 0.0), "timestamp") or 0.0,
        "content_ref": str(event.get("content_ref", "")),
        "path": str(event.get("content_ref", "")),
        "scope": as_scope(event.get("artifact_scope")) or "run",
        "case_id": str(event["case_id"]) if event.get("case_id") is not None else None,
    }


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or result >= 0)
def as_int(value: object, field_name: str) -> int | None:
    """Coerce a non-negative projection integer.
    
    >>> as_int("1", "count")
    1
    """
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or result >= 0.0)
def as_float(value: object, field_name: str) -> float | None:
    """Coerce a non-negative projection float.
    
    >>> as_float("1.5", "seconds")
    1.5
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0.0 else None


@pre(lambda value: value is None or bool(str(value).strip()))
@post(lambda result: result is None or bool(result.strip()))
def as_status(value: object) -> str | None:
    """Coerce a non-empty status label.
    
    >>> as_status("running")
    'running'
    """
    text = str(value).strip() if value is not None else ""
    return text if text in {"pending", "running", "success", "fail", "stall", "transport_error"} else None


@pre(lambda value: value is None or bool(str(value).strip()))
@post(lambda result: result is None or bool(result.strip()))
def as_scope(value: object) -> str | None:
    """Coerce a non-empty scope label.
    
    >>> as_scope("case")
    'case'
    """
    text = str(value).strip() if value is not None else ""
    return text if text in {"run", "step", "case"} else "run"


@pre(lambda records: all(isinstance(record, Mapping) and int(record.get("sequence", -1)) >= 0 for record in records))
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def rebuild_drive_projection_from_records(records: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    """Rebuild drive projection data from already-loaded store records.
    
    >>> rebuild_drive_projection_from_records(({"sequence": 1, "status": "running"},))["event_count"]
    1
    """
    return derive_state(normalize_events(records))
