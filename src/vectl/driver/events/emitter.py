"""Typed helpers for canonical driver event emission.

Authoritative source:
- docs/ADR-driver-evolution-foundation.md#58-final-becomes-canonical-summary-event
- docs/ADR-driver-evolution-foundation.md#169-implementation-guidance
- docs/ADR-driver-evolution-foundation.md#188-resolved-decisions

Implementation notes:
- registry remains the sole schema/version source of truth
- helpers enforce exact event names and forward to observer sinks
"""

from __future__ import annotations

from typing import Final, Protocol, TypedDict

from .registry import DECIDE_EVENT_RECORD, FINAL_EVENT_RECORD, STEP_COMPLETED_EVENT_RECORD
from .types import (
    DECIDE,
    FINAL,
    STEP_COMPLETED,
    DecideEventName,
    FinalEventName,
    StepCompletedEventName,
)


class SupportsEventEmit(Protocol):
    def emit(self, event_type: str, /, **data: object) -> None: ...


class _DecideEventPayloadRequired(TypedDict):
    running_count: int
    actions: list[str]


class DecideEventPayload(_DecideEventPayloadRequired, total=False):
    claimable: int
    capacity: int


class _FinalEventPayloadRequired(TypedDict):
    completed_summary: dict[str, object]
    total_duration_seconds: float


class FinalEventPayload(_FinalEventPayloadRequired, total=False):
    halt_reason: str | None
    total_cost_usd: float
    total_tokens: int


class _StepCompletedEventPayloadRequired(TypedDict):
    step_id: str
    elapsed_seconds: float


class StepCompletedEventPayload(_StepCompletedEventPayloadRequired, total=False):
    cost: float
    evidence_len: int
    tokens: int


DECIDE_EVENT_DEF: Final = DECIDE_EVENT_RECORD
FINAL_EVENT_DEF: Final = FINAL_EVENT_RECORD
STEP_COMPLETED_EVENT_DEF: Final = STEP_COMPLETED_EVENT_RECORD


def emit_decide(
    observer: SupportsEventEmit,
    /,
    *,
    running_count: int,
    actions: list[str],
    claimable: int | None = None,
    capacity: int | None = None,
) -> DecideEventName:
    payload: DecideEventPayload = {
        "running_count": running_count,
        "actions": list(actions),
    }
    if claimable is not None:
        payload["claimable"] = claimable
    if capacity is not None:
        payload["capacity"] = capacity

    observer.emit(DECIDE_EVENT_DEF.event, **payload)
    return DECIDE


def emit_final(
    observer: SupportsEventEmit,
    /,
    *,
    completed_summary: dict[str, object],
    total_duration_seconds: float,
    halt_reason: str | None = None,
    total_cost_usd: float | None = None,
    total_tokens: int | None = None,
) -> FinalEventName:
    payload: FinalEventPayload = {
        "completed_summary": completed_summary,
        "total_duration_seconds": total_duration_seconds,
    }
    if halt_reason is not None:
        payload["halt_reason"] = halt_reason
    if total_cost_usd is not None:
        payload["total_cost_usd"] = total_cost_usd
    if total_tokens is not None:
        payload["total_tokens"] = total_tokens

    observer.emit(FINAL_EVENT_DEF.event, **payload)
    return FINAL


def emit_step_completed(
    observer: SupportsEventEmit,
    /,
    *,
    step_id: str,
    elapsed_seconds: float,
    cost: float | None = None,
    evidence_len: int | None = None,
    tokens: int | None = None,
) -> StepCompletedEventName:
    payload: StepCompletedEventPayload = {
        "step_id": step_id,
        "elapsed_seconds": elapsed_seconds,
    }
    if cost is not None:
        payload["cost"] = cost
    if evidence_len is not None:
        payload["evidence_len"] = evidence_len
    if tokens is not None:
        payload["tokens"] = tokens

    observer.emit(STEP_COMPLETED_EVENT_DEF.event, **payload)
    return STEP_COMPLETED
