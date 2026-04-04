"""
Joined read-query DTOs for runs, inspect, and case surfaces.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 5
(no dedicated section; uses contracts.py shared types)

Public surfaces (this module):
    - ControlChannelMessage   (message schema for control-channel communication)
    - ControlChannel          (protocol for control-channel send/receive)
    - send_to_control()      (convenience surface for sending to control channel)
    - InspectView             (joined read-query DTO for inspect surfaces)
    - CaseView               (joined read-query DTO for case/unresolved surfaces)

Note: This module addresses the "joined read-query DTOs for runs, inspect, and
case surfaces". The exact channel transport and query model are not yet
specified; this module records interface anchors with documented gaps.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import quote

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
    }
)
_DEFAULT_RUNS_ROOT = Path(".vectl/runs")
_DEFAULT_MAX_PENDING_ACTIONS = 100
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05


class ControlChannelError(RuntimeError):
    """Base class for control-channel persistence errors."""


class InvalidControlChannelMessageError(ControlChannelError):
    """Raised when the message cannot be persisted as an operator action."""


class DuplicateActionError(ControlChannelError):
    """Raised when action ID already exists in channel storage."""


class PendingActionLimitExceededError(ControlChannelError):
    """Raised when pending-action queue is already at configured capacity."""


class MalformedActionFileError(ControlChannelError):
    """Raised when action file payload is malformed or undecodable."""


@dataclass(frozen=True)
class ActionRequest:
    """Persisted operator action request."""

    action_id: str
    run_id: str
    msg_type: str
    sender: str
    payload: tuple[str, ...]
    timestamp: float


@dataclass(frozen=True)
class ActionReceipt:
    """Persisted operator action acknowledgement receipt."""

    action_id: str
    run_id: str
    status: Literal["applied", "rejected"]
    timestamp: float
    reason: str | None = None


@dataclass(frozen=True)
class ActionAcknowledgement:
    """Result of acknowledgement lookup including timeout status."""

    action_id: str
    status: AcknowledgementStatus
    receipt: ActionReceipt | None = None


# ---------------------------------------------------------------------
# Control Channel Message Schema
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ControlChannelMessage:
    """
    Message schema for control-channel communication.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 5

    GAP: The exact message taxonomy and required fields are not yet fully
    specified. The fields below represent the known minimum anchor.

    Attributes:
        msg_type: Message type identifier.
        sender: Identifier of the sending component.
        payload: Message payload (content depends on msg_type).
        timestamp: Message emission timestamp.
    """

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
    """
    Protocol for control-channel send/receive between orchestration components.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3

    GAP: The exact transport mechanism (in-memory, file-based, async MQ) and
    the full message protocol are not yet specified. No concrete implementation
    should be added in this contract step.

    This protocol records the expected boundary role for inter-component
    control communication.
    """

    def send(self, message: ControlChannelMessage) -> None:
        """
        Send a message through the control channel.

        Args:
            message: The message to send.

        Raises:
            NotImplementedError: Until channel transport is specified.
        """
        ...


@dataclass
class FilesystemControlChannel:
    """Filesystem-backed operator control channel implementation.

    Authority: user task for ``orch_operator_control_surface.impl_control_channel``
    requiring pending/applied/rejected persistence, acknowledgement lookup,
    bounded pending queue behavior, and run-local path normalization.
    """

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
        """Persist operator action request as pending file."""

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
        """Read oldest pending message across runs without mutating queue."""

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
        """List persisted requests for ``run_id`` and status."""

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
        """Persist applied receipt and remove pending request if present."""

        return self._acknowledge(
            run_id=run_id, action_id=action_id, status="applied", reason=reason
        )

    def acknowledge_rejected(self, run_id: str, action_id: str, *, reason: str) -> ActionReceipt:
        """Persist rejected receipt and remove pending request if present."""

        return self._acknowledge(
            run_id=run_id, action_id=action_id, status="rejected", reason=reason
        )

    def lookup_acknowledgement(self, run_id: str, action_id: str) -> ActionAcknowledgement | None:
        """Return applied/rejected acknowledgement if already present."""

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
        """Poll for acknowledgement and return explicit timeout status."""

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
        """Return canonical control-channel file layout for ``run_id``."""

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


_DEFAULT_CONTROL_CHANNEL: ContextVar[ControlChannel | None] = ContextVar(
    "vectl_orch_control_channel",
    default=None,
)


def set_default_control_channel(channel: ControlChannel) -> Token[ControlChannel | None]:
    """Bind default control-channel implementation for current execution context."""

    return _DEFAULT_CONTROL_CHANNEL.set(channel)


def reset_default_control_channel(token: Token[ControlChannel | None]) -> None:
    """Reset context-bound control channel to the previous value."""

    _DEFAULT_CONTROL_CHANNEL.reset(token)


def get_default_control_channel() -> ControlChannel:
    """Get context-bound default control-channel implementation."""

    existing = _DEFAULT_CONTROL_CHANNEL.get()
    if existing is None:
        return FilesystemControlChannel()
    return existing


def _normalize_component(raw: str) -> str:
    normalized = quote(
        raw, safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-%"
    )
    if not normalized:
        raise ValueError("path component cannot be empty")
    return normalized


def _build_action_id(message: ControlChannelMessage) -> str:
    payload = {
        "msg_type": message.msg_type,
        "sender": message.sender,
        "payload": list(message.payload),
        "timestamp": message.timestamp,
        "time_ns": time.time_ns(),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:24]


def _request_from_message(message: ControlChannelMessage) -> ActionRequest:
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


def _serialize_request(request: ActionRequest) -> dict[str, object]:
    return {
        "action_id": request.action_id,
        "run_id": request.run_id,
        "msg_type": request.msg_type,
        "sender": request.sender,
        "payload": list(request.payload),
        "timestamp": request.timestamp,
    }


def _deserialize_request(payload: dict[str, object]) -> ActionRequest:
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

    return ActionRequest(
        action_id=_normalize_component(action_id),
        run_id=_normalize_component(run_id),
        msg_type=msg_type,
        sender=sender,
        payload=tuple(raw_payload),
        timestamp=float(timestamp),
    )


def _serialize_receipt(receipt: ActionReceipt) -> dict[str, object]:
    return {
        "action_id": receipt.action_id,
        "run_id": receipt.run_id,
        "status": receipt.status,
        "timestamp": receipt.timestamp,
        "reason": receipt.reason,
    }


def _deserialize_receipt(payload: dict[str, object]) -> ActionReceipt:
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

    return ActionReceipt(
        action_id=_normalize_component(action_id),
        run_id=_normalize_component(run_id),
        status=cast(Literal["applied", "rejected"], status),
        timestamp=float(timestamp),
        reason=reason,
    )


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

    def receive(self, timeout_seconds: float | None = None) -> ControlChannelMessage | None:
        """
        Receive a message from the control channel.

        Args:
            timeout_seconds: Optional timeout. None means block indefinitely.

        Returns:
            The next message, or None if timeout expired.

        Raises:
            NotImplementedError: Until channel transport is specified.
        """
        ...


# ---------------------------------------------------------------------
# Send-to-Control Convenience Function
# ---------------------------------------------------------------------


def send_to_control(
    message: ControlChannelMessage,
    channel: ControlChannel | None = None,
) -> None:
    """
    Convenience surface for sending a message to the control channel.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3

    GAP: The default channel instance when channel=None is not yet specified.

    Args:
        message: The message to send to control.
        channel: Optional explicit channel. If None, a default channel
            must be globally available.

    Raises:
        InvalidControlChannelMessageError: If message cannot be persisted.
        ControlChannelError: If persistence fails.
    """
    resolved_channel = channel if channel is not None else get_default_control_channel()
    resolved_channel.send(message)


# ---------------------------------------------------------------------
# Inspect View — joined read-query DTO
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class InspectView:
    """
    Joined read-query DTO for inspection surfaces.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact inspect surface shape and query model are not yet
    specified. The fields below represent the known minimum anchor.

    Attributes:
        plan_id: Identifier of the plan being inspected.
        core: Current core snapshot view.
        roster: Current roster snapshot view.
        runtime: Current runtime snapshot view.
        active_runs: Tuple of currently active run identifiers.
        recent_decisions: Tuple of recent control decisions.
    """

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
    """
    Joined read-query DTO for case / unresolved-state inspection.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact case view shape and query model are not yet specified.
    The fields below represent the known minimum anchor.

    Attributes:
        case_id: Unique identifier for this case.
        reason: Human-readable explanation of why normal flow did not close.
        core: Core snapshot at time of case creation.
        roster: Roster snapshot at time of case creation.
        runtime: Runtime snapshot at time of case creation.
        resolution_report: Resolution report if the case has been resolved.
        status: Current case status.
    """

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
    "DuplicateActionError",
    "PendingActionLimitExceededError",
    "MalformedActionFileError",
    "ActionRequest",
    "ActionReceipt",
    "ActionAcknowledgement",
    "FilesystemControlChannel",
    "set_default_control_channel",
    "reset_default_control_channel",
    "get_default_control_channel",
    "send_to_control",
    "InspectView",
    "CaseView",
]
