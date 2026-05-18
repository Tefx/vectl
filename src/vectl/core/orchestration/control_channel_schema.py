"""Pure control-channel schema extraction.

Decision row: ``src/vectl/orchestration/control_channel.py`` structural Core extraction.

>>> serialize_receipt({"action_id": "act-1", "status": "acknowledged", "acknowledged_at": "2026-01-01T00:00:00Z"})
{'action_id': 'act-1', 'status': 'acknowledged', 'acknowledged_at': '2026-01-01T00:00:00Z'}

>>> deserialize_request({"action_id": ""})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping

from deal import post, pre


_SAFE_COMPONENT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-%"


@pre(lambda value, safe: isinstance(value, str) and bool(safe))
@post(lambda result: isinstance(result, str))
def _percent_encode(value: str, safe: str) -> str:
    safe_bytes = {ord(char) for char in safe}
    parts: list[str] = []
    for byte in value.encode("utf-8"):
        if byte in safe_bytes:
            parts.append(chr(byte))
        else:
            parts.append(f"%{byte:02X}")
    return "".join(parts)


_ALLOWED_CONTROL_MESSAGE_TYPES = frozenset(
    {
        "pause",
        "unpause",
        "stop",
        "case.respond",
        "case_response",
        "control.pause",
        "control.unpause",
        "control.stop",
        "drive.pause",
        "drive.unpause",
        "drive.stop",
    }
)


@pre(lambda raw: bool(raw.strip()))
@post(lambda result: bool(result.strip()) and "/" not in result)
def normalize_component(raw: str) -> str:
    """Normalize a non-empty control-channel path component.
    
    >>> normalize_component("component")
    'component'
    >>> normalize_component("run/id")
    'run%2Fid'
    """
    normalized = _percent_encode(raw, _SAFE_COMPONENT_CHARS)
    if not normalized:
        raise ValueError("path component cannot be empty")
    return normalized


@pre(lambda request: isinstance(request, Mapping) and bool(str(request.get("action_id", "")).strip()) and (bool(str(request.get("kind", "")).strip()) or bool(str(request.get("msg_type", "")).strip())))
@post(lambda result: isinstance(result, Mapping) and "action_id" in result and "payload" in result and ("kind" in result or "msg_type" in result))
def serialize_request(request: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize action request DTO data to persisted JSON object shape.
    
    >>> serialize_request({"action_id": "act-1", "kind": "stop", "payload": {}})
    {'action_id': 'act-1', 'kind': 'stop', 'payload': {}}
    """
    result = dict(request)
    if "payload" not in result:
        result["payload"] = ()
    return result


@pre(lambda data: isinstance(data, Mapping) and bool(str(data.get("action_id", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("action_id")))
def deserialize_request(data: Mapping[str, object]) -> Mapping[str, object]:
    """Deserialize persisted action request data with stable diagnostics.
    
    >>> deserialize_request({"action_id": "act-1"})
    {'action_id': 'act-1'}
    """
    result = dict(data)
    if "run_id" in result:
        result["run_id"] = normalize_component(str(result["run_id"]))
    if "action_id" in result:
        result["action_id"] = normalize_component(str(result["action_id"]))
    msg_type = result.get("msg_type")
    if isinstance(msg_type, str) and msg_type not in _ALLOWED_CONTROL_MESSAGE_TYPES:
        raise ValueError(f"unsupported msg_type {msg_type!r}")
    payload = result.get("payload")
    if payload is not None:
        if not isinstance(payload, (list, tuple)) or not all(isinstance(item, str) for item in payload):
            raise ValueError("payload must be a sequence of strings")
        result["payload"] = tuple(payload)
    return result


@pre(lambda receipt: isinstance(receipt, Mapping) and bool(str(receipt.get("action_id", "")).strip()) and bool(str(receipt.get("status", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and {"action_id", "status"}.issubset(result.keys()))
def serialize_receipt(receipt: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize action receipt DTO data to persisted JSON object shape.
    
    >>> serialize_receipt({"action_id": "act-1", "status": "acknowledged"})
    {'action_id': 'act-1', 'status': 'acknowledged'}
    """
    return dict(receipt)


@pre(lambda data: isinstance(data, Mapping) and bool(str(data.get("action_id", "")).strip()) and bool(str(data.get("status", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("action_id")) and bool(result.get("status")))
def deserialize_receipt(data: Mapping[str, object]) -> Mapping[str, object]:
    """Deserialize persisted action receipt data with stable diagnostics.
    
    >>> deserialize_receipt({"action_id": "act-1", "status": "acknowledged"})
    {'action_id': 'act-1', 'status': 'acknowledged'}
    """
    result = dict(data)
    result["action_id"] = normalize_component(str(result["action_id"]))
    if "run_id" in result:
        result["run_id"] = normalize_component(str(result["run_id"]))
    return result
