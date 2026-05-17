"""Contracts for pure control-channel schema extraction.

Decision row: ``src/vectl/orchestration/control_channel.py`` structural Core extraction.

>>> serialize_receipt({"action_id": "act-1", "status": "acknowledged", "acknowledged_at": "2026-01-01T00:00:00Z"})
Traceback (most recent call last):
...
NotImplementedError: contract stub: serialize_receipt

>>> deserialize_request({"action_id": ""})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping

from deal import post, pre


@pre(lambda raw: bool(raw.strip()))
@post(lambda result: bool(result.strip()) and "/" not in result)
def normalize_component(raw: str) -> str:
    """Normalize a non-empty control-channel path component.
    
    >>> normalize_component("component")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: normalize_component
    """
    raise NotImplementedError("contract stub: normalize_component")


@pre(lambda request: isinstance(request, Mapping) and bool(str(request.get("action_id", "")).strip()) and bool(str(request.get("kind", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"action_id", "kind", "payload"}.issubset(result.keys()))
def serialize_request(request: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize action request DTO data to persisted JSON object shape.
    
    >>> serialize_request({"action_id": "act-1", "kind": "stop", "payload": {}})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: serialize_request
    """
    raise NotImplementedError("contract stub: serialize_request")


@pre(lambda data: isinstance(data, Mapping) and bool(str(data.get("action_id", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("action_id")))
def deserialize_request(data: Mapping[str, object]) -> Mapping[str, object]:
    """Deserialize persisted action request data with stable diagnostics.
    
    >>> deserialize_request({"action_id": "act-1"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: deserialize_request
    """
    raise NotImplementedError("contract stub: deserialize_request")


@pre(lambda receipt: isinstance(receipt, Mapping) and bool(str(receipt.get("action_id", "")).strip()) and bool(str(receipt.get("status", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"action_id", "status"}.issubset(result.keys()))
def serialize_receipt(receipt: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize action receipt DTO data to persisted JSON object shape.
    
    >>> serialize_receipt({"action_id": "act-1", "status": "acknowledged"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: serialize_receipt
    """
    raise NotImplementedError("contract stub: serialize_receipt")


@pre(lambda data: isinstance(data, Mapping) and bool(str(data.get("action_id", "")).strip()) and bool(str(data.get("status", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("action_id")) and bool(result.get("status")))
def deserialize_receipt(data: Mapping[str, object]) -> Mapping[str, object]:
    """Deserialize persisted action receipt data with stable diagnostics.
    
    >>> deserialize_receipt({"action_id": "act-1", "status": "acknowledged"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: deserialize_receipt
    """
    raise NotImplementedError("contract stub: deserialize_receipt")
