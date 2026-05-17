"""Contracts for pure event schema/hash/key extraction.

Decision row: ``src/vectl/orchestration/events.py`` structural Core extraction.

>>> normalize_step_key("phase/step with space")
Traceback (most recent call last):
...
NotImplementedError: contract stub: normalize_step_key

>>> validate_payload_shape("", {})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda step_id: bool(step_id.strip()))
@post(lambda result: bool(result.strip()) and "/" not in result and " " not in result)
def normalize_step_key(step_id: str) -> str:
    """Encode a non-empty step ID into a filesystem-safe key.
    
    >>> normalize_step_key("phase/step")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: normalize_step_key
    """
    raise NotImplementedError("contract stub: normalize_step_key")


@pre(lambda encoded_step_key: bool(encoded_step_key.strip()))
@post(lambda result: bool(result.strip()))
def denormalize_step_key(encoded_step_key: str) -> str:
    """Decode a non-empty filesystem-safe step key.
    
    >>> denormalize_step_key("phase%2Fstep")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: denormalize_step_key
    """
    raise NotImplementedError("contract stub: denormalize_step_key")


@pre(lambda payload: isinstance(payload, Mapping) and all(isinstance(key, str) and key for key in payload.keys()))
@post(lambda result: result.startswith("{") and result.endswith("}"))
def canonical_json(payload: Mapping[str, object]) -> str:
    """Return deterministic canonical JSON for a mapping payload.
    
    >>> canonical_json({"kind": "run"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: canonical_json
    """
    raise NotImplementedError("contract stub: canonical_json")


@pre(lambda value: bool(value.strip()))
@post(lambda result: isinstance(result, bool))
def is_hex_sha256(value: str) -> bool:
    """Classify a non-empty string as a SHA-256 hex digest or not.
    
    >>> is_hex_sha256("0" * 64)
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: is_hex_sha256
    """
    raise NotImplementedError("contract stub: is_hex_sha256")


@pre(lambda event_kind, payload: bool(event_kind.strip()) and isinstance(payload, Mapping))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_payload_shape(event_kind: str, payload: Mapping[str, object]) -> list[str]:
    """Return stable payload-shape diagnostics for a declared event kind.
    
    >>> validate_payload_shape("run_started", {})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: validate_payload_shape
    """
    raise NotImplementedError("contract stub: validate_payload_shape")


@pre(lambda sequence, kind, payload, previous_hash=None: sequence >= 0 and bool(kind.strip()) and isinstance(payload, Mapping) and (previous_hash is None or bool(previous_hash.strip())))
@post(lambda result: isinstance(result, Mapping) and result.get("sequence") is not None and bool(result.get("event_hash")))
def build_drive_event_data(
    sequence: int,
    kind: str,
    payload: Mapping[str, object],
    previous_hash: str | None = None,
) -> Mapping[str, object]:
    """Build drive-event envelope data with stable hash-chain fields.
    
    >>> build_drive_event_data(1, "run_started", {})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_drive_event_data
    """
    raise NotImplementedError("contract stub: build_drive_event_data")


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_drive_event_chain_data(events: Sequence[Mapping[str, object]]) -> list[str]:
    """Return stable diagnostics for drive-event chain corruption.
    
    >>> validate_drive_event_chain_data(({"sequence": 1},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: validate_drive_event_chain_data
    """
    raise NotImplementedError("contract stub: validate_drive_event_chain_data")
