"""Typed contract helpers for canonical driver event emission.

Authoritative source:
- docs/ADR-driver-evolution-foundation.md#58-final-becomes-canonical-summary-event
- docs/ADR-driver-evolution-foundation.md#169-implementation-guidance
- docs/ADR-driver-evolution-foundation.md#188-resolved-decisions

CONTRACT PURITY:
- signatures/type definitions only
- runtime helper bodies remain intentionally deferred
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
    raise NotImplementedError(f"{DECIDE} typed helper is contract-only in this phase")


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
    raise NotImplementedError(f"{FINAL} typed helper is contract-only in this phase")


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
    raise NotImplementedError(f"{STEP_COMPLETED} typed helper is contract-only in this phase")
