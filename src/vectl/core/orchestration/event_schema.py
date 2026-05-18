"""Pure event schema/hash/key extraction.

Decision row: ``src/vectl/orchestration/events.py`` structural Core extraction.

>>> normalize_step_key("phase/step with space")
'phase%2Fstep%20with%20space'

>>> validate_payload_shape("", {})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence
import hashlib
import json

from deal import post, pre


_SAFE_COMPONENT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"


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


@pre(lambda value: "\x00" not in value and "%00" not in value.lower())
@post(lambda result: "\x00" not in result)
def _percent_decode(value: str) -> str:
    output = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%" and index + 2 < len(value):
            try:
                output.append(int(value[index + 1 : index + 3], 16))
                index += 3
                continue
            except ValueError:
                pass
        output.extend(char.encode("utf-8"))
        index += 1
    return output.decode("utf-8", errors="replace")

_EVENT_SCHEMA: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "run_started": (("run_id", "plan_path"), ()),
    "run_status_changed": (("run_id", "status"), ()),
    "run_final": (("run_id", "final_status"), ()),
    "control_dispatch": (("step_id", "agent"), ()),
    "control_wait": (("step_id", "reason"), ()),
    "control_done": (("step_id", "status"), ()),
    "control_resolve": (("step_id", "case_id"), ()),
    "control_pause": ((), ()),
    "control_unpause": ((), ()),
    "control_stop": ((), ()),
    "roster_claim": (("step_id", "agent"), ()),
    "roster_release": (("step_id", "agent"), ()),
    "roster_register": (("agent",), ()),
    "runtime_prepare": (("step_id", "workspace"), ()),
    "runtime_start": (("step_id", "task_id"), ()),
    "runtime_collect": (("step_id", "task_id"), ()),
    "runtime_cleanup": (("step_id", "workspace"), ()),
    "resolver_invoked": (("step_id", "case_id"), ()),
    "resolver_returned": (("step_id", "status"), ()),
    "projection_latest_updated": (("run_id", "event_seq"), ()),
    "projection_summary_updated": (("run_id", "event_seq"), ()),
    "projection_metrics_updated": (("run_id", "event_seq"), ()),
    "operator_case_opened": (("case_id", "step_id"), ("resolution_status", "summary", "operator_message", "evidence_refs", "open_case_delta")),
    "operator_case_resolved": (("case_id", "resolution"), ("open_case_delta",)),
    "operator_action_requested": (("case_id", "action"), ()),
    "drive_started": (("drive_id", "plan_path"), ("agent", "max_parallelism")),
    "drive_status_changed": (("drive_id", "status"), ("previous_status", "reason")),
    "drive_barrier_entered": (("drive_id", "reason"), ("case_ids", "active_child_run_ids")),
    "drive_barrier_cleared": (("drive_id", "resolution"), ("resolver_status", "planner_status")),
    "planner_invoked": (("drive_id", "step_id"), ("planner_request_id", "affected_steps")),
    "planner_applied": (("drive_id", "status"), ("mutations_count", "affected_steps")),
    "child_run_admitted": (("drive_id", "run_id", "kind"), ("step_id", "case_id")),
    "child_run_final": (("drive_id", "run_id", "status"), ("step_id", "case_id")),
    "drive_final": (("drive_id", "final_status"), ("summary", "child_run_count")),
}


@pre(lambda step_id: bool(step_id.strip()))
@post(lambda result: bool(result.strip()) and "/" not in result and " " not in result)
def normalize_step_key(step_id: str) -> str:
    """Encode a non-empty step ID into a filesystem-safe key.
    
    >>> normalize_step_key("phase/step")
    'phase%2Fstep'
    """
    return _percent_encode(step_id, _SAFE_COMPONENT_CHARS)


@pre(lambda encoded_step_key: bool(encoded_step_key.strip()))
@post(lambda result: bool(result.strip()))
def denormalize_step_key(encoded_step_key: str) -> str:
    """Decode a non-empty filesystem-safe step key.
    
    >>> denormalize_step_key("phase%2Fstep")
    'phase/step'
    """
    return _percent_decode(encoded_step_key)


@pre(lambda payload: isinstance(payload, Mapping) and all(isinstance(key, str) and key for key in payload.keys()))
@post(lambda result: result.startswith("{") and result.endswith("}"))
def canonical_json(payload: Mapping[str, object]) -> str:
    """Return deterministic canonical JSON for a mapping payload.
    
    >>> canonical_json({"kind": "run"})
    '{"kind":"run"}'
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pre(lambda value: bool(value.strip()))
@post(lambda result: isinstance(result, bool))
def is_hex_sha256(value: str) -> bool:
    """Classify a non-empty string as a SHA-256 hex digest or not.
    
    >>> is_hex_sha256("0" * 64)
    True
    """
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


@pre(lambda event_kind, payload: bool(event_kind.strip()) and isinstance(payload, Mapping))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_payload_shape(event_kind: str, payload: Mapping[str, object]) -> list[str]:
    """Return stable payload-shape diagnostics for a declared event kind.
    
    >>> validate_payload_shape("run_started", {})
    ["payload schema drift for event 'run_started': missing=['plan_path', 'run_id'], unexpected=[]"]
    """
    schema = _EVENT_SCHEMA.get(event_kind)
    if schema is None:
        return [f"unknown event kind {event_kind!r}; expected one of {tuple(_EVENT_SCHEMA.keys())}"]
    required_keys, optional_keys = schema
    keys = set(payload.keys())
    required = set(required_keys)
    allowed = required | set(optional_keys)
    missing = sorted(required - keys)
    extras = sorted(keys - allowed)
    if missing or extras:
        return [f"payload schema drift for event {event_kind!r}: missing={missing}, unexpected={extras}"]
    return []


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
    {'sequence': 1, 'event': 'run_started', 'payload': {}, 'previous_hash': None, 'event_hash': '2dc22d0a7b61259861ae37d241f23e022c25a0ea62cbd8880e979f588067ddb9'}
    """
    event_data = {
        "sequence": sequence,
        "event": kind,
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }
    digest = hashlib.sha256(canonical_json(event_data).encode("utf-8")).hexdigest()
    return {**event_data, "event_hash": digest}


@pre(lambda events: all(isinstance(event, Mapping) and int(event.get("sequence", -1)) >= 0 for event in events))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_drive_event_chain_data(events: Sequence[Mapping[str, object]]) -> list[str]:
    """Return stable diagnostics for drive-event chain corruption.
    
    >>> validate_drive_event_chain_data(({"sequence": 1},))
    ['event 1 missing event_hash']
    """
    diagnostics: list[str] = []
    expected_sequence = 1
    previous_hash: str | None = None
    for index, event in enumerate(events, start=1):
        sequence = int(event.get("sequence", -1))
        if sequence != expected_sequence:
            diagnostics.append(f"event {index} non-monotonic sequence: expected {expected_sequence}, got {sequence}")
        observed_previous = event.get("previous_hash")
        if observed_previous != previous_hash:
            diagnostics.append(
                f"event {index} previous_hash mismatch: expected {previous_hash!r}, got {observed_previous!r}"
            )
        event_hash = event.get("event_hash")
        if not isinstance(event_hash, str) or not is_hex_sha256(event_hash):
            diagnostics.append(f"event {index} missing event_hash")
            previous_hash = None
        else:
            previous_hash = event_hash
        expected_sequence += 1
    return diagnostics
