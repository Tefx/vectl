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

from .registry import (
    DECIDE_EVENT_RECORD,
    FINAL_EVENT_RECORD,
    STEP_COMPLETED_EVENT_RECORD,
    get_event_record,
)
from .types import (
    DECIDE,
    FINAL,
    STEP_COMPLETED,
    STARTUP_HYGIENE_BLOCKED,
    STARTUP_HYGIENE_CLASSIFY,
    STARTUP_HYGIENE_QUARANTINE,
    STARTUP_HYGIENE_SCAN,
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


# --- Startup Hygiene Event Payloads ---


class _StartupHygieneScanPayloadRequired(TypedDict):
    artifact_count: int
    corrupt_count: int
    current_branch: str


class StartupHygieneScanPayload(_StartupHygieneScanPayloadRequired, total=False):
    corrupt_paths: list[str]


class _StartupHygieneClassifyPayloadRequired(TypedDict):
    artifact_kind: str
    classification: str
    original_path: str
    step_id: str


class StartupHygieneClassifyPayload(_StartupHygieneClassifyPayloadRequired, total=False):
    quarantine_destination: str
    safe_stale_rule_satisfied: bool


class _StartupHygieneQuarantinePayloadRequired(TypedDict):
    artifact_kind: str
    classification: str
    destination_path: str
    original_path: str
    reason: str
    step_id: str


class StartupHygieneQuarantinePayload(_StartupHygieneQuarantinePayloadRequired, total=False):
    audit_timestamp: str


class _StartupHygieneBlockedPayloadRequired(TypedDict):
    classification: str
    reason: str
    step_id: str


class StartupHygieneBlockedPayload(_StartupHygieneBlockedPayloadRequired, total=False):
    pass


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


# --- Startup Hygiene Emit Helpers ---


def emit_startup_hygiene_scan(
    observer: SupportsEventEmit,
    /,
    *,
    artifact_count: int,
    corrupt_count: int,
    current_branch: str,
    corrupt_paths: list[str] | None = None,
) -> str:
    """Emit hygiene scan summary event before classification."""
    payload: StartupHygieneScanPayload = {
        "artifact_count": artifact_count,
        "corrupt_count": corrupt_count,
        "current_branch": current_branch,
    }
    if corrupt_paths is not None:
        payload["corrupt_paths"] = corrupt_paths

    observer.emit(STARTUP_HYGIENE_SCAN, **payload)
    return STARTUP_HYGIENE_SCAN


def emit_startup_hygiene_classify(
    observer: SupportsEventEmit,
    /,
    *,
    artifact_kind: str,
    classification: str,
    original_path: str,
    step_id: str,
    quarantine_destination: str | None = None,
    safe_stale_rule_satisfied: bool | None = None,
) -> str:
    """Emit per-artifact classification telemetry from hygiene stage."""
    payload: StartupHygieneClassifyPayload = {
        "artifact_kind": artifact_kind,
        "classification": classification,
        "original_path": original_path,
        "step_id": step_id,
    }
    if quarantine_destination is not None:
        payload["quarantine_destination"] = quarantine_destination
    if safe_stale_rule_satisfied is not None:
        payload["safe_stale_rule_satisfied"] = safe_stale_rule_satisfied

    observer.emit(STARTUP_HYGIENE_CLASSIFY, **payload)
    return STARTUP_HYGIENE_CLASSIFY


def emit_startup_hygiene_quarantine(
    observer: SupportsEventEmit,
    /,
    *,
    artifact_kind: str,
    classification: str,
    destination_path: str,
    original_path: str,
    reason: str,
    step_id: str,
    audit_timestamp: str | None = None,
) -> str:
    """Emit quarantine write-path telemetry with stable reason/classification fields."""
    payload: StartupHygieneQuarantinePayload = {
        "artifact_kind": artifact_kind,
        "classification": classification,
        "destination_path": destination_path,
        "original_path": original_path,
        "reason": reason,
        "step_id": step_id,
    }
    if audit_timestamp is not None:
        payload["audit_timestamp"] = audit_timestamp

    observer.emit(STARTUP_HYGIENE_QUARANTINE, **payload)
    return STARTUP_HYGIENE_QUARANTINE


def emit_startup_hygiene_blocked(
    observer: SupportsEventEmit,
    /,
    *,
    classification: str,
    reason: str,
    step_id: str,
) -> str:
    """Emit hygiene-stage blocking telemetry for corrupt, active-claim, and ambiguous cases."""
    payload: StartupHygieneBlockedPayload = {
        "classification": classification,
        "reason": reason,
        "step_id": step_id,
    }

    observer.emit(STARTUP_HYGIENE_BLOCKED, **payload)
    return STARTUP_HYGIENE_BLOCKED
