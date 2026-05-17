from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from vectl.core.orchestration.control_channel_schema import (
    deserialize_receipt as _core_deserialize_receipt,
    deserialize_request as _core_deserialize_request,
    normalize_component as _core_normalize_component,
    serialize_receipt as _core_serialize_receipt,
    serialize_request as _core_serialize_request,
)
from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    pass


ActionStatus = Literal["pending", "applied", "rejected"]
AcknowledgementStatus = Literal["applied", "rejected", "timeout"]

_ALLOWED_CONTROL_MESSAGE_TYPES: frozenset[str] = frozenset(
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
_DEFAULT_RUNS_ROOT = Path(".vectl/runs")
_DEFAULT_MAX_PENDING_ACTIONS = 100
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05


class ControlChannelError(RuntimeError):
    ...


class InvalidControlChannelMessageError(ControlChannelError):
    ...


class DuplicateActionError(ControlChannelError):
    ...


class PendingActionLimitExceededError(ControlChannelError):
    ...


class MalformedActionFileError(ControlChannelError):
    ...


@dataclass(frozen=True)
class ActionRequest:

    action_id: str
    run_id: str
    msg_type: str
    sender: str
    payload: tuple[str, ...]
    timestamp: float


@dataclass(frozen=True)
class ActionReceipt:

    action_id: str
    run_id: str
    status: Literal["applied", "rejected"]
    timestamp: float
    reason: str | None = None


@dataclass(frozen=True)
class ActionAcknowledgement:

    action_id: str
    status: AcknowledgementStatus
    receipt: ActionReceipt | None = None


# ---------------------------------------------------------------------
# Control Channel Message Schema
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ControlChannelMessage:

    msg_type: str
    sender: str
    payload: tuple[str, ...] = ()
    timestamp: float | None = None


# ---------------------------------------------------------------------
# Control Channel Protocol
# ---------------------------------------------------------------------
# GAP: The exact control channel transport (in-memory, file-based, async
# message queue) is not yet specified. This is a forward contract stub.


class ControlChannel(Protocol):

    def send(self, message: ControlChannelMessage) -> None:
        ...

    def receive(self, timeout_seconds: float | None = None) -> ControlChannelMessage | None:
        ...


@dataclass
class FilesystemControlChannel:

    runs_root: Path | str = _DEFAULT_RUNS_ROOT
    max_pending_actions: int = _DEFAULT_MAX_PENDING_ACTIONS
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        self._runs_root = Path(self.runs_root)
        if self.max_pending_actions <= 0:
            raise ValueError("max_pending_actions must be > 0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")

    def send(self, message: ControlChannelMessage) -> None:

        request = _request_from_message(message)
        self._reject_malformed_and_duplicate_pending(run_id=request.run_id)
        pending = self._pending_dir(request.run_id)
        pending.mkdir(parents=True, exist_ok=True)
        if len(tuple(pending.glob("*.json"))) >= self.max_pending_actions:
            raise PendingActionLimitExceededError(f"pending queue full for run '{request.run_id}'")
        if self._action_exists(run_id=request.run_id, action_id=request.action_id):
            raise DuplicateActionError(
                f"action_id '{request.action_id}' already exists for run '{request.run_id}'"
            )
        _write_json_atomic(
            self._pending_file_path(request.run_id, request.action_id), _serialize_request(request)
        )

    def receive(self, timeout_seconds: float | None = None) -> ControlChannelMessage | None:

        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while True:
            pending = self._oldest_pending_request()
            if pending is not None:
                return ControlChannelMessage(
                    msg_type=pending.msg_type,
                    sender=pending.sender,
                    payload=pending.payload,
                    timestamp=pending.timestamp,
                )
            if deadline is not None and time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval_seconds)

    def list_requests(
        self,
        run_id: str,
        *,
        status: ActionStatus = "pending",
    ) -> tuple[ActionRequest, ...]:

        if status == "pending":
            self._reject_malformed_and_duplicate_pending(run_id=run_id)
            directory = self._pending_dir(run_id)
        elif status == "applied":
            directory = self._applied_dir(run_id)
        else:
            directory = self._rejected_dir(run_id)

        if not directory.exists():
            return ()
        records: list[ActionRequest] = []
        for file_path in sorted(directory.glob("*.json")):
            payload = _read_json_object(file_path)
            records.append(_deserialize_request(payload))
        records.sort(key=lambda item: (item.timestamp, item.action_id))
        return tuple(records)

    def acknowledge_applied(
        self, run_id: str, action_id: str, *, reason: str | None = None
    ) -> ActionReceipt:

        return self._acknowledge(
            run_id=run_id, action_id=action_id, status="applied", reason=reason
        )

    def acknowledge_rejected(self, run_id: str, action_id: str, *, reason: str) -> ActionReceipt:

        return self._acknowledge(
            run_id=run_id, action_id=action_id, status="rejected", reason=reason
        )

    def lookup_acknowledgement(self, run_id: str, action_id: str) -> ActionAcknowledgement | None:

        for status in ("applied", "rejected"):
            receipt_path = self._receipt_file_path(
                run_id=run_id,
                action_id=action_id,
                status=status,
            )
            if receipt_path.exists():
                payload = _read_json_object(receipt_path)
                receipt = _deserialize_receipt(payload)
                return ActionAcknowledgement(
                    action_id=action_id,
                    status=receipt.status,
                    receipt=receipt,
                )
        return None

    def wait_for_acknowledgement(
        self,
        run_id: str,
        action_id: str,
        *,
        timeout_seconds: float,
    ) -> ActionAcknowledgement:

        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be >= 0")
        deadline = time.monotonic() + timeout_seconds
        while True:
            found = self.lookup_acknowledgement(run_id=run_id, action_id=action_id)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return ActionAcknowledgement(action_id=action_id, status="timeout")
            time.sleep(self.poll_interval_seconds)

    def control_layout(self, run_id: str) -> dict[str, Path]:

        root = self._control_root(run_id)
        return {
            "root": root,
            "actions": root / "actions",
            "pending": root / "actions" / "pending",
            "applied": root / "actions" / "applied",
            "rejected": root / "actions" / "rejected",
        }

    def _acknowledge(
        self,
        *,
        run_id: str,
        action_id: str,
        status: Literal["applied", "rejected"],
        reason: str | None,
    ) -> ActionReceipt:
        normalized_run = _normalize_component(run_id)
        normalized_action = _normalize_component(action_id)
        pending_file = self._pending_file_path(normalized_run, normalized_action)
        if pending_file.exists():
            pending_file.unlink()

        receipt = ActionReceipt(
            action_id=normalized_action,
            run_id=normalized_run,
            status=status,
            timestamp=time.time(),
            reason=reason,
        )
        _write_json_atomic(
            self._receipt_file_path(
                run_id=normalized_run, action_id=normalized_action, status=status
            ),
            _serialize_receipt(receipt),
        )
        return receipt

    def _oldest_pending_request(self) -> ActionRequest | None:
        runs_root = self._runs_root
        if not runs_root.exists():
            return None
        candidates: list[ActionRequest] = []
        for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
            run_id = run_dir.name
            self._reject_malformed_and_duplicate_pending(run_id=run_id)
            for file_path in sorted(self._pending_dir(run_id).glob("*.json")):
                payload = _read_json_object(file_path)
                candidates.append(_deserialize_request(payload))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.timestamp, item.action_id))
        return candidates[0]

    def _reject_malformed_and_duplicate_pending(self, *, run_id: str) -> None:
        pending = self._pending_dir(run_id)
        if not pending.exists():
            return
        seen: dict[str, Path] = {}
        for file_path in sorted(pending.glob("*.json")):
            try:
                payload = _read_json_object(file_path)
                request = _deserialize_request(payload)
            except (MalformedActionFileError, ValueError):
                self._mark_rejected_from_file(
                    run_id=run_id,
                    file_path=file_path,
                    reason="Malformed action payload",
                )
                continue
            first = seen.get(request.action_id)
            if first is None:
                seen[request.action_id] = file_path
                continue
            self._mark_rejected_from_file(
                run_id=run_id,
                file_path=file_path,
                reason=f"Duplicate action_id '{request.action_id}'",
            )

    def _mark_rejected_from_file(self, *, run_id: str, file_path: Path, reason: str) -> None:
        try:
            payload = _read_json_object(file_path)
            action_id_raw = str(payload.get("action_id", ""))
        except MalformedActionFileError:
            action_id_raw = file_path.stem
        normalized_action_id = (
            _normalize_component(action_id_raw)
            if action_id_raw
            else _normalize_component(file_path.stem)
        )
        receipt = ActionReceipt(
            action_id=normalized_action_id,
            run_id=_normalize_component(run_id),
            status="rejected",
            timestamp=time.time(),
            reason=reason,
        )
        _write_json_atomic(
            self._receipt_file_path(run_id=run_id, action_id=receipt.action_id, status="rejected"),
            _serialize_receipt(receipt),
        )
        file_path.unlink(missing_ok=True)

    def _control_root(self, run_id: str) -> Path:
        return self._runs_root / _normalize_component(run_id) / "control"

    def _pending_dir(self, run_id: str) -> Path:
        return self._control_root(run_id) / "actions" / "pending"

    def _applied_dir(self, run_id: str) -> Path:
        return self._control_root(run_id) / "actions" / "applied"

    def _rejected_dir(self, run_id: str) -> Path:
        return self._control_root(run_id) / "actions" / "rejected"

    def _pending_file_path(self, run_id: str, action_id: str) -> Path:
        return self._pending_dir(run_id) / f"{_normalize_component(action_id)}.json"

    def _receipt_file_path(
        self,
        *,
        run_id: str,
        action_id: str,
        status: Literal["applied", "rejected"],
    ) -> Path:
        if status == "applied":
            return self._applied_dir(run_id) / f"{_normalize_component(action_id)}.json"
        return self._rejected_dir(run_id) / f"{_normalize_component(action_id)}.json"

    def _action_exists(self, *, run_id: str, action_id: str) -> bool:
        return any(
            path.exists()
            for path in (
                self._pending_file_path(run_id, action_id),
                self._receipt_file_path(run_id=run_id, action_id=action_id, status="applied"),
                self._receipt_file_path(run_id=run_id, action_id=action_id, status="rejected"),
            )
        )


_normalize_component = _core_normalize_component


class _ActionIdBuilder:
    # @shell_orchestration: Action ID construction is coupled to persisted control-message deduplication.
    def __call__(self, message: ControlChannelMessage) -> str:
        payload = {
            "msg_type": message.msg_type,
            "sender": message.sender,
            "payload": list(message.payload),
            "timestamp": message.timestamp,
            "time_ns": time.time_ns(),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return digest[:24]


_build_action_id = _ActionIdBuilder()


class _RequestFromMessage:
    # @shell_orchestration: Message-to-request mapping is the control-channel persistence boundary validator.
    def __call__(self, message: ControlChannelMessage) -> ActionRequest:
        if message.msg_type not in _ALLOWED_CONTROL_MESSAGE_TYPES:
            raise InvalidControlChannelMessageError(
                f"unsupported control message type '{message.msg_type}'"
            )
        if not message.payload:
            raise InvalidControlChannelMessageError("control message payload must include run_id")
        run_id = _normalize_component(message.payload[0])
        timestamp = time.time() if message.timestamp is None else message.timestamp
        return ActionRequest(
            action_id=_build_action_id(message),
            run_id=run_id,
            msg_type=message.msg_type,
            sender=message.sender,
            payload=tuple(message.payload),
            timestamp=float(timestamp),
        )


_request_from_message = _RequestFromMessage()


class _RequestSerializer:
    # @shell_orchestration: Request serialization remains a public on-disk schema adapter over Core data.
    def __call__(self, request: ActionRequest) -> dict[str, object]:
        return dict(_core_serialize_request({
            "action_id": request.action_id,
            "run_id": request.run_id,
            "msg_type": request.msg_type,
            "sender": request.sender,
            "payload": list(request.payload),
            "timestamp": request.timestamp,
        }))


_serialize_request = _RequestSerializer()


class _RequestDeserializer:
    # @shell_complexity: Validation branches preserve per-field malformed-action diagnostics.
    # @shell_orchestration: Request deserialization feeds pending-file rejection and acknowledgement routing.
    def __call__(self, payload: dict[str, object]) -> ActionRequest:
        action_id = str(payload.get("action_id", "")).strip()
        run_id = str(payload.get("run_id", "")).strip()
        msg_type = str(payload.get("msg_type", "")).strip()
        sender = str(payload.get("sender", "")).strip()
        raw_payload = payload.get("payload", ())
        timestamp = payload.get("timestamp", 0.0)

        if not action_id or not run_id or not msg_type or not sender:
            raise MalformedActionFileError("missing required action fields")
        if msg_type not in _ALLOWED_CONTROL_MESSAGE_TYPES:
            raise MalformedActionFileError(f"unsupported msg_type '{msg_type}'")
        if not isinstance(raw_payload, list):
            raise MalformedActionFileError("payload must be JSON array")
        if not all(isinstance(item, str) for item in raw_payload):
            raise MalformedActionFileError("payload entries must be strings")
        if not isinstance(timestamp, (int, float)):
            raise MalformedActionFileError("timestamp must be numeric")

        normalized = _core_deserialize_request(
            {
                "action_id": action_id,
                "run_id": run_id,
                "msg_type": msg_type,
                "sender": sender,
                "payload": raw_payload,
                "timestamp": timestamp,
            }
        )
        return ActionRequest(
            action_id=str(normalized["action_id"]),
            run_id=str(normalized["run_id"]),
            msg_type=msg_type,
            sender=sender,
            payload=tuple(cast(tuple[str, ...], normalized["payload"])),
            timestamp=float(timestamp),
        )


_deserialize_request = _RequestDeserializer()


class _ReceiptSerializer:
    # @shell_orchestration: Receipt serialization remains a public acknowledgement-schema adapter over Core data.
    def __call__(self, receipt: ActionReceipt) -> dict[str, object]:
        return dict(_core_serialize_receipt({
            "action_id": receipt.action_id,
            "run_id": receipt.run_id,
            "status": receipt.status,
            "timestamp": receipt.timestamp,
            "reason": receipt.reason,
        }))


_serialize_receipt = _ReceiptSerializer()


class _ReceiptDeserializer:
    # @shell_complexity: Validation branches preserve identifier, status, and timestamp diagnostics.
    # @shell_orchestration: Receipt deserialization belongs with acknowledgement lookup semantics.
    def __call__(self, payload: dict[str, object]) -> ActionReceipt:
        action_id = str(payload.get("action_id", "")).strip()
        run_id = str(payload.get("run_id", "")).strip()
        status = str(payload.get("status", "")).strip()
        timestamp = payload.get("timestamp", 0.0)
        reason_raw = payload.get("reason")
        reason = str(reason_raw) if reason_raw is not None else None

        if not action_id or not run_id:
            raise MalformedActionFileError("missing receipt identifiers")
        if status not in {"applied", "rejected"}:
            raise MalformedActionFileError(f"unsupported receipt status '{status}'")
        if not isinstance(timestamp, (int, float)):
            raise MalformedActionFileError("receipt timestamp must be numeric")

        normalized = _core_deserialize_receipt(
            {
                "action_id": action_id,
                "run_id": run_id,
                "status": status,
                "timestamp": timestamp,
                "reason": reason,
            }
        )
        return ActionReceipt(
            action_id=str(normalized["action_id"]),
            run_id=str(normalized["run_id"]),
            status=cast(Literal["applied", "rejected"], status),
            timestamp=float(timestamp),
            reason=reason,
        )


_deserialize_receipt = _ReceiptDeserializer()


# @invar:allow shell_result: JSON reader raises MalformedActionFileError so callers can reject bad pending files.
def _read_json_object(path: Path) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedActionFileError(f"malformed JSON in {path}: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise MalformedActionFileError(f"malformed payload in {path}: expected object")
    return parsed


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, sort_keys=True)
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(path)

# ---------------------------------------------------------------------
# Send-to-Control Convenience Function
# ---------------------------------------------------------------------


# @shell_orchestration: Convenience helper belongs to control-channel shell API and delegates transport mutation.
def send_to_control(
    message: ControlChannelMessage,
    channel: ControlChannel,
) -> None:
    channel.send(message)


# ---------------------------------------------------------------------
# Drive-Scoped Control Convenience Function
# ---------------------------------------------------------------------
# Authority: docs/RFC-orch-drive.md section 7.3
#
# Drive-scoped control (pause/unpause/stop) targets the drive rather
# than a single run. The drive_id is carried in the payload instead of
# run_id, and stop --force appends a "force=true" marker.


_ALLOWED_DRIVE_CONTROL_MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "drive.pause",
        "drive.unpause",
        "drive.stop",
    }
)


class InvalidDriveControlMessageError(ControlChannelError):
    ...


class _DriveControlSender:
    # @shell_orchestration: Drive-control helper builds operator payloads before delegating filesystem persistence.
    def __call__(
        self,
        drive_id: str,
        action: Literal["pause", "unpause", "stop"],
        channel: FilesystemControlChannel,
        *,
        reason: str | None = None,
        force: bool = False,
    ) -> ActionRequest:
        msg_type = f"drive.{action}"
        payload_items: list[str] = [drive_id]
        if reason is not None:
            payload_items.append(reason)
        if force and action == "stop":
            payload_items.append("force=true")

        message = ControlChannelMessage(
            msg_type=msg_type,
            sender="operator",
            payload=tuple(payload_items),
        )
        channel.send(message)
        return _request_from_message(message)


send_drive_control = _DriveControlSender()


# ---------------------------------------------------------------------
# Inspect View — joined read-query DTO
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class InspectView:

    plan_id: str
    core: CoreSnapshot | None = None
    roster: RosterSnapshot | None = None
    runtime: RuntimeSnapshot | None = None
    active_runs: tuple[str, ...] = ()
    recent_decisions: tuple[ControlDecision, ...] = ()


# ---------------------------------------------------------------------
# Case View — joined read-query DTO for unresolved/blocked cases
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class CaseView:

    case_id: str
    reason: str
    core: CoreSnapshot | None = None
    roster: RosterSnapshot | None = None
    runtime: RuntimeSnapshot | None = None
    resolution_report: ResolutionReport | None = None
    status: Literal["open", "resolved", "halt"] | None = None


__all__ = [
    "ControlChannelMessage",
    "ControlChannel",
    "ActionStatus",
    "AcknowledgementStatus",
    "ControlChannelError",
    "InvalidControlChannelMessageError",
    "InvalidDriveControlMessageError",
    "DuplicateActionError",
    "PendingActionLimitExceededError",
    "MalformedActionFileError",
    "ActionRequest",
    "ActionReceipt",
    "ActionAcknowledgement",
    "FilesystemControlChannel",
    "send_to_control",
    "send_drive_control",
    "InspectView",
    "CaseView",
]
