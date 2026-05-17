"""Contracts for pure projection replay reducer extraction.

Decision row: ``src/vectl/orchestration/projections.py`` structural Core extraction.

>>> derive_state(({"sequence": 1, "kind": "run_started", "payload": {}},))
Traceback (most recent call last):
...
NotImplementedError: contract stub: derive_state

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
    
    >>> select_events(({"sequence": 1},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: select_events
    """
    raise NotImplementedError("contract stub: select_events")


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, tuple) and all("payload" in event and "kind" in event for event in result))
def normalize_events(events: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Normalize mapping/envelope event records without Shell replay.
    
    >>> normalize_events(({"sequence": 1, "kind": "run", "payload": {}},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: normalize_events
    """
    raise NotImplementedError("contract stub: normalize_events")


@pre(lambda payload: payload is None or isinstance(payload, Mapping))
@post(lambda result: isinstance(result, Mapping))
def parse_payload(payload: Mapping[str, object] | None) -> Mapping[str, object]:
    """Coerce optional event payload data to a mapping.
    
    >>> parse_payload({})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: parse_payload
    """
    raise NotImplementedError("contract stub: parse_payload")


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or isinstance(result, str | int | float | bool))
def coerce_scalar(value: object, field_name: str) -> str | int | float | bool | None:
    """Coerce a projection scalar for a named field.
    
    >>> coerce_scalar("value", "field")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: coerce_scalar
    """
    raise NotImplementedError("contract stub: coerce_scalar")


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def derive_state(events: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    """Derive deterministic latest run state from normalized events.
    
    >>> derive_state(({"sequence": 1, "kind": "run", "payload": {}},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: derive_state
    """
    raise NotImplementedError("contract stub: derive_state")


@pre(lambda event: isinstance(event, Mapping) and bool(str(event.get("kind", "")).strip()))
@post(lambda result: result is None or (isinstance(result, Mapping) and bool(result.get("path"))))
def artifact_from_event(event: Mapping[str, object]) -> Mapping[str, object] | None:
    """Extract a projection artifact reference from one event.
    
    >>> artifact_from_event({"kind": "artifact", "payload": {}})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: artifact_from_event
    """
    raise NotImplementedError("contract stub: artifact_from_event")


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or result >= 0)
def as_int(value: object, field_name: str) -> int | None:
    """Coerce a non-negative projection integer.
    
    >>> as_int(1, "count")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: as_int
    """
    raise NotImplementedError("contract stub: as_int")


@pre(lambda value, field_name: value is not None and bool(field_name.strip()))
@post(lambda result: result is None or result >= 0.0)
def as_float(value: object, field_name: str) -> float | None:
    """Coerce a non-negative projection float.
    
    >>> as_float(1.0, "seconds")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: as_float
    """
    raise NotImplementedError("contract stub: as_float")


@pre(lambda value: value is None or bool(str(value).strip()))
@post(lambda result: result is None or bool(result.strip()))
def as_status(value: object) -> str | None:
    """Coerce a non-empty status label.
    
    >>> as_status("running")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: as_status
    """
    raise NotImplementedError("contract stub: as_status")


@pre(lambda value: value is None or bool(str(value).strip()))
@post(lambda result: result is None or bool(result.strip()))
def as_scope(value: object) -> str | None:
    """Coerce a non-empty scope label.
    
    >>> as_scope("drive")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: as_scope
    """
    raise NotImplementedError("contract stub: as_scope")


@pre(lambda records: all(isinstance(record, Mapping) and int(record.get("sequence", -1)) >= 0 for record in records))
@post(lambda result: isinstance(result, Mapping) and int(result.get("event_count", 0)) >= 0)
def rebuild_drive_projection_from_records(records: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    """Rebuild drive projection data from already-loaded store records.
    
    >>> rebuild_drive_projection_from_records(({"sequence": 1},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: rebuild_drive_projection_from_records
    """
    raise NotImplementedError("contract stub: rebuild_drive_projection_from_records")
