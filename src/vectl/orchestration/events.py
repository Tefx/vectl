"""
Shared event/logging surface for orchestration-plane observability.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.8

Public surfaces (authority pinned to ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md):
    - OrchestrationEventEnvelope    (canonical event envelope schema)
    - EventSink                    (event sink protocol for event dispatch)
    - EventRegistry                 (event registry / topic subscription surface)
    - emit()                       (canonical event emission boundary)

Ownership clarification (per IMPLEMENTATION-DESIGN.md section 3.8):
    events.py is owned by the orchestration plane as shared support.
    It is NOT core-owned authority, and it is NOT a private submodule of
    any single orchestration component.

GAP: The exact event taxonomy, envelope schema, and sink API are not yet
specified in the design docs. This module records known interface anchors
and explicitly documents the gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Canonical Event Envelope
# ---------------------------------------------------------------------

OrchestrationEventKind = Literal[
    "control_dispatch",
    "control_wait",
    "control_done",
    "control_resolve",
    "roster_claim",
    "roster_release",
    "roster_register",
    "runtime_prepare",
    "runtime_start",
    "runtime_collect",
    "runtime_cleanup",
    "resolver_invoked",
    "resolver_returned",
]


@dataclass(frozen=True)
class OrchestrationEventEnvelope:
    """
    Canonical event envelope for orchestration-plane observability.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.8

    GAP: The exact event taxonomy and envelope fields are not fully specified.
    The fields below represent the known minimum anchor. Concrete fields
    must NOT be expanded beyond this without design-doc specification.

    Attributes:
        kind: Event kind identifier from the canonical taxonomy.
        timestamp: Event emission timestamp (UTC).
        step_id: Optional associated step identifier.
        payload: Optional event-specific payload data.
        agent: Optional agent identifier that triggered the event.
    """

    kind: OrchestrationEventKind
    timestamp: datetime
    step_id: str | None = None
    payload: tuple[str, ...] = ()
    agent: str | None = None


# ---------------------------------------------------------------------
# Event Sink Protocol
# ---------------------------------------------------------------------
# GAP: The event sink protocol (subscribe/emit/persist) is not yet
# specified in the design docs. This is a forward contract stub.


class EventSink(Protocol):
    """
    Protocol for event sink / event dispatch subscribers.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md.md section 3.8

    GAP: The exact sink API (subscribe(topic), emit(envelope), flush()) is
    not yet specified. This protocol records the expected boundary role.
    Concrete methods must NOT be invented here.
    """

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        """
        Emit one event envelope to the sink.

        Args:
            envelope: The canonical event envelope to emit.
        """
        ...


# ---------------------------------------------------------------------
# Event Registry Surface
# ---------------------------------------------------------------------
# GAP: The event registry (topic subscription, handler lookup) is not
# yet specified in the design docs.


class EventRegistry:
    """
    Event registry / topic subscription surface.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.8

    GAP: Concrete event registry behavior (registration, handler lookup,
    subscription lifecycle) is not yet specified. No implementation logic
    should be added to this contract step.
    """

    def register(
        self,
        kind: OrchestrationEventKind,
        handler: EventSink,
    ) -> None:
        """
        Register an event handler for a specific event kind.

        Args:
            kind: Event kind to subscribe to.
            handler: Event sink to invoke on matching events.

        Raises:
            NotImplementedError: Until registry semantics are specified.
        """
        raise NotImplementedError(
            "EventRegistry.register: semantics not yet specified in design docs"
        )

    def emit(self, envelope: OrchestrationEventEnvelope) -> None:
        """
        Emit an event to all registered handlers.

        Args:
            envelope: The event envelope to broadcast.

        Raises:
            NotImplementedError: Until registry semantics are specified.
        """
        raise NotImplementedError("EventRegistry.emit: semantics not yet specified in design docs")


# ---------------------------------------------------------------------
# Canonical Event Emission Boundary
# ---------------------------------------------------------------------
# GAP: The canonical emit() function and default global sink are not
# yet specified.


def emit(envelope: OrchestrationEventEnvelope) -> None:
    """
    Emit one orchestration event through the canonical emission boundary.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.8

    GAP: The global sink registration, default sink behavior, and thread-
    safety guarantees are not yet specified. No concrete implementation
    should be added in this contract step.

    Args:
        envelope: The canonical event envelope to emit.

    Raises:
        NotImplementedError: Until emission semantics are specified.
    """
    raise NotImplementedError("emit: canonical emission boundary not yet specified in design docs")


__all__ = [
    "OrchestrationEventEnvelope",
    "OrchestrationEventKind",
    "EventSink",
    "EventRegistry",
    "emit",
]
