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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)

if TYPE_CHECKING:
    pass


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
        NotImplementedError: Until default channel semantics are specified.
    """
    raise NotImplementedError("send_to_control: default channel not yet specified in design docs")


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
    "send_to_control",
    "InspectView",
    "CaseView",
]
