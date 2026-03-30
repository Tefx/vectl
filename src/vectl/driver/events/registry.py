"""Static canonical event declaration table.

Authoritative source:
- docs/ADR-driver-evolution-foundation.md#45-event-registry-becomes-source-of-truth
- docs/ADR-driver-evolution-foundation.md#104-event-registry-is-a-static-declaration-table
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from .types import (
    ALL_EVENT_TYPES,
    DECIDE,
    FINAL,
    STEP_COMPLETED,
    STARTUP_HYGIENE_BLOCKED,
    STARTUP_HYGIENE_CLASSIFY,
    STARTUP_HYGIENE_QUARANTINE,
    STARTUP_HYGIENE_SCAN,
)


@dataclass(frozen=True)
class EventRegistryRecord:
    event: str
    version: int
    required: tuple[str, ...]
    optional: tuple[str, ...]
    owner: str
    compatibility: str


def _record(
    event: str,
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    owner: str,
    compatibility: str,
) -> EventRegistryRecord:
    return EventRegistryRecord(
        event=event,
        version=1,
        required=required,
        optional=optional,
        owner=owner,
        compatibility=compatibility,
    )


_TABLE: dict[str, EventRegistryRecord] = {
    "ANOMALY_VERDICT": _record(
        "ANOMALY_VERDICT",
        required=("anomaly_type", "reason", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "COLD_CONTEXT_VERDICT": _record(
        "COLD_CONTEXT_VERDICT",
        required=("reason", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "COMPLETE_ACTION_IGNORED": _record(
        "COMPLETE_ACTION_IGNORED",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "CONTINUITY_DOUBLE_TRUTH_DETECTED": _record(
        "CONTINUITY_DOUBLE_TRUTH_DETECTED",
        required=("action_task_id", "authority", "pool_session_id", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "DECIDE": _record(
        "DECIDE",
        required=("running_count", "actions"),
        optional=("claimable", "capacity"),
        owner="vectl.driver.loop",
        compatibility=(
            "Version 1 baseline for registry-backed decision emission; preserve "
            "required fields and add only optional fields within v1."
        ),
    ),
    "ESCALATION_DEFERRED": _record(
        "ESCALATION_DEFERRED",
        required=("attempt", "deferred_branches", "reason", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "ESCALATION_VERDICT": _record(
        "ESCALATION_VERDICT",
        required=("attempt", "reason", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "FAILURE_REMEDIATION_DEFERRED": _record(
        "FAILURE_REMEDIATION_DEFERRED",
        required=("disposition", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "FAILURE_VERDICT": _record(
        "FAILURE_VERDICT",
        required=("disposition", "reason", "remaining_checks", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "FINAL": _record(
        "FINAL",
        required=("completed_summary", "total_duration_seconds"),
        optional=("halt_reason", "total_cost_usd", "total_tokens"),
        owner="vectl.driver.loop",
        compatibility=(
            "Canonical summary event in v1; FINAL alone determines terminal outcome "
            "for end-of-run consumers while HALT remains separate stop-causality. "
            "Preserve envelope shape and add only optional fields within v1."
        ),
    ),
    "GATE_REMEDIATION_DEFERRED": _record(
        "GATE_REMEDIATION_DEFERRED",
        required=("batched_issue_count", "blocker_count", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "GATE_VERDICT": _record(
        "GATE_VERDICT",
        required=("issue_count", "reason", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "HALT": _record(
        "HALT",
        required=("reason",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; first-class stop-causality event.",
    ),
    "HALT_REQUESTED": _record(
        "HALT_REQUESTED",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "JUDGE_FAILURE_POLICY": _record(
        "JUDGE_FAILURE_POLICY",
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; payload fields defined by policy output contract.",
    ),
    "JUDGMENT": _record(
        "JUDGMENT",
        required=("type", "step_id", "verdict", "reason"),
        optional=("latency_ms", "planner_instruction", "suggested_action"),
        owner="vectl.driver.judge",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "MERGE_COMPLETED": _record(
        "MERGE_COMPLETED",
        required=("step_id", "elapsed_seconds"),
        optional=("auto_resolved", "conflicting_files"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "MERGE_CONFLICTED": _record(
        "MERGE_CONFLICTED",
        required=("step_id", "conflicting_files"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PLANNER_DISPATCH_COMPLETED": _record(
        "PLANNER_DISPATCH_COMPLETED",
        required=("elapsed_seconds", "judgment_type", "runner", "session_id", "step_id", "trigger"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PLANNER_DISPATCH_FAILED": _record(
        "PLANNER_DISPATCH_FAILED",
        required=("judgment_type", "output", "runner", "status", "step_id", "trigger"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PLANNER_DISPATCH_STARTED": _record(
        "PLANNER_DISPATCH_STARTED",
        required=("judgment_type", "runner", "step_id", "trigger"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PREFLIGHT_DEFER": _record(
        "PREFLIGHT_DEFER",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PREFLIGHT_REJECT": _record(
        "PREFLIGHT_REJECT",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PREFLIGHT_SKIPPED": _record(
        "PREFLIGHT_SKIPPED",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "PREFLIGHT_VERDICT": _record(
        "PREFLIGHT_VERDICT",
        required=("reason", "risk_signals", "step_id", "verdict"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "RECOVERY": _record(
        "RECOVERY",
        required=("type",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "RUNNER_FALLBACK": _record(
        "RUNNER_FALLBACK",
        required=("from_runner", "reason", "step_id", "to_runner"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "SESSION_REUSE_HIT": _record(
        "SESSION_REUSE_HIT",
        required=("session_id", "source", "step_id"),
        optional=("age_seconds",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "SESSION_REUSE_MISS": _record(
        "SESSION_REUSE_MISS",
        required=("reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "STARTUP_HYGIENE_BLOCKED": _record(
        "STARTUP_HYGIENE_BLOCKED",
        required=("classification", "reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility=(
            "Version 1 baseline; hygiene-stage blocking telemetry that distinguishes "
            "corrupt_blocking, blocking_divergence (active-claim), and ambiguous_blocking "
            "from active recovery decisions."
        ),
    ),
    "STARTUP_HYGIENE_CLASSIFY": _record(
        "STARTUP_HYGIENE_CLASSIFY",
        required=("artifact_kind", "classification", "original_path", "step_id"),
        optional=("quarantine_destination", "safe_stale_rule_satisfied"),
        owner="vectl.driver.loop",
        compatibility=(
            "Version 1 baseline; per-artifact classification telemetry from hygiene stage. "
            "Distinguishes hygiene-stage quarantine from active recovery decisions."
        ),
    ),
    "STARTUP_HYGIENE_QUARANTINE": _record(
        "STARTUP_HYGIENE_QUARANTINE",
        required=(
            "artifact_kind",
            "classification",
            "destination_path",
            "original_path",
            "reason",
            "step_id",
        ),
        optional=("audit_timestamp",),
        owner="vectl.driver.loop",
        compatibility=(
            "Version 1 baseline; quarantine write-path telemetry with stable "
            "reason/classification fields and manifest/log linkage."
        ),
    ),
    "STARTUP_HYGIENE_SCAN": _record(
        "STARTUP_HYGIENE_SCAN",
        required=("artifact_count", "corrupt_count", "current_branch"),
        optional=("corrupt_paths",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; hygiene scan summary before classification.",
    ),
    "STARTUP_RECOVERY_DECISION": _record(
        "STARTUP_RECOVERY_DECISION",
        required=("attempt_key", "disposition", "reason", "step_id"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "STEP_COMPLETED": _record(
        "STEP_COMPLETED",
        required=("step_id", "elapsed_seconds"),
        optional=("cost", "evidence_len", "tokens"),
        owner="vectl.driver.loop",
        compatibility=(
            "Version 1 baseline for terminal step-success emission; preserve "
            "required fields and add only optional fields within v1."
        ),
    ),
    "STEP_DISPATCHED": _record(
        "STEP_DISPATCHED",
        required=("step_id", "agent", "runner", "session_reuse"),
        optional=("preflight_enabled",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "STEP_FAILED": _record(
        "STEP_FAILED",
        required=("step_id", "failure_type", "attempt", "error"),
        optional=("judge_policy_action", "judge_recovery_reason"),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
    "WAIT": _record(
        "WAIT",
        required=("reason",),
        owner="vectl.driver.loop",
        compatibility="Version 1 baseline; additive optional fields only within v1.",
    ),
}

DECIDE_EVENT_RECORD: Final[EventRegistryRecord] = _TABLE[DECIDE]
FINAL_EVENT_RECORD: Final[EventRegistryRecord] = _TABLE[FINAL]
STEP_COMPLETED_EVENT_RECORD: Final[EventRegistryRecord] = _TABLE[STEP_COMPLETED]

EVENT_REGISTRY: Final[Mapping[str, EventRegistryRecord]] = MappingProxyType(_TABLE)

if set(EVENT_REGISTRY) != set(ALL_EVENT_TYPES):
    missing = set(ALL_EVENT_TYPES) - set(EVENT_REGISTRY)
    extra = set(EVENT_REGISTRY) - set(ALL_EVENT_TYPES)
    raise RuntimeError(f"Event registry mismatch: missing={sorted(missing)} extra={sorted(extra)}")


def get_event_record(event: str) -> EventRegistryRecord:
    return EVENT_REGISTRY[event]


def validate_event_payload(record: EventRegistryRecord, payload: Mapping[str, object]) -> None:
    """Validate emitted payload against canonical event-envelope rules.

    Args:
        record: Canonical registry record for the emitted event.
        payload: Event payload kwargs destined for the envelope ``data`` field.

    Raises:
        ValueError: If payload includes a nested ``version`` shim.
    """

    if "version" in payload:
        raise ValueError(
            f"{record.event} payload must not include 'version'; use top-level envelope version"
        )
