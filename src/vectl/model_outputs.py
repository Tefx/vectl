"""Reporting, diff, init, and decision output DTOs.

Extracted from vectl.models while preserving compatibility re-exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from vectl.models import Phase, PhaseStatus, PlanValidationIssue, Step, StepStatus

class SearchMatch:
    """A single search result with location and context."""

    def __init__(
        self,
        phase_id: str,
        step_id: str | None,
        field: str,
        snippet: str,
    ) -> None:
        self.phase_id = phase_id
        self.step_id = step_id
        self.field = field
        self.snippet = snippet

    def __repr__(self) -> str:
        loc = self.step_id or self.phase_id
        return f"SearchMatch({loc}.{self.field}: {self.snippet!r})"


# ---------------------------------------------------------------------------
# Review & Gate Check Data Types
# ---------------------------------------------------------------------------


@dataclass
class PhaseProgress:
    """Progress summary for a single phase."""

    phase_id: str
    name: str
    status: PhaseStatus
    done: int
    total: int

    @property
    def pct(self) -> float:
        return (self.done / self.total * 100) if self.total > 0 else 0.0


@dataclass
class ReviewResult:
    """Structured result of a plan review (all 4 layers)."""

    validation_issues: list[PlanValidationIssue]
    phase_progress: list[PhaseProgress]
    active_phases: list[Phase]
    ref_index: dict[str, list[str]]
    total_done: int
    total_steps: int

    @property
    def errors(self) -> list[PlanValidationIssue]:
        return [i for i in self.validation_issues if not i.is_warning]

    @property
    def warnings(self) -> list[PlanValidationIssue]:
        return [i for i in self.validation_issues if i.is_warning]

    @property
    def overall_pct(self) -> float:
        return (self.total_done / self.total_steps * 100) if self.total_steps > 0 else 0.0


@dataclass
class GateCheckResult:
    """Structured result of a phase gate readiness check."""

    phase_id: str
    phase_name: str
    steps_complete: bool
    done_count: int
    total_count: int
    pending_steps: list[Step]
    gate_criterion: str | None
    gate_script: str | None
    downstream_locked: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Diff Data Types
# ---------------------------------------------------------------------------


@dataclass
class StepChange:
    """A single change to a step."""

    step_id: str
    step_name: str
    phase_id: str
    kind: str  # "added", "removed", "status_changed", "modified"
    old_status: StepStatus | None = None
    new_status: StepStatus | None = None
    detail: str = ""


@dataclass
class PhaseChange:
    """A single change to a phase."""

    phase_id: str
    phase_name: str
    kind: str  # "added", "removed", "status_changed"
    old_status: PhaseStatus | None = None
    new_status: PhaseStatus | None = None


@dataclass
class DiffResult:
    """Structured result of comparing two plan states."""

    phase_changes: list[PhaseChange]
    step_changes: list[StepChange]

    @property
    def has_changes(self) -> bool:
        return bool(self.phase_changes or self.step_changes)


# ---------------------------------------------------------------------------
# Init Result
# ---------------------------------------------------------------------------


class InitResult(BaseModel):
    """Result of initializing a new vectl project.

    Source: Task mcp-parity.add-vectl-init-mcp-tool
    Returned by vectl_init MCP tool for structured client handling.
    """

    ok: bool
    plan_path: str
    agents_target: str | None = None  # Which file was updated (AGENTS.md or CLAUDE.md)
    message: str
    error: str | None = None


# ---------------------------------------------------------------------------
# vectl_decide Models
# ---------------------------------------------------------------------------


class RunningTask(BaseModel):
    """A running task tracked by caller-owned automation.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Used by vectl_decide to track in-flight work for session reuse decisions.

    Attributes:
        step_id: Step identifier.
        agent: Agent name.
        task_id: Caller-visible execution/task identifier (NOT a reuse handle).
        runner: Runner namespace/source that owns any reuse semantics attached
            to that task. Required; allowed values at rollout are "claude" and "task".
        dispatched_at: time.time() when dispatched.
    """

    step_id: str
    agent: str
    task_id: str
    runner: str
    dispatched_at: float

    @field_validator("runner")
    @classmethod
    def validate_runner(cls, value: str) -> str:
        """Validate runner provenance against current contract rollout values."""
        allowed = {"claude", "task"}
        if value not in allowed:
            allowed_display = ", ".join(sorted(allowed))
            raise ValueError(f"runner must be one of: {allowed_display}")
        return value


class CompletedResult(BaseModel):
    """A completed task result from a sub-agent.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Used by vectl_decide to process completion events.

    Attributes:
        step_id: Step identifier.
        task_id: Task/execution identifier associated with the completed result.
        runner: Runner namespace/source for this result.
        status: SUCCESS or FAIL.
        output_summary: Brief text from sub-agent (for evidence).
    """

    step_id: str
    task_id: str
    runner: str
    status: str
    output_summary: str

    @field_validator("runner")
    @classmethod
    def validate_runner(cls, value: str) -> str:
        """Validate runner provenance against current contract rollout values."""
        allowed = {"claude", "task"}
        if value not in allowed:
            allowed_display = ", ".join(sorted(allowed))
            raise ValueError(f"runner must be one of: {allowed_display}")
        return value


class Decision(BaseModel):
    """A single decision made by vectl_decide.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Logged for debugging and audit trail. Callers should not have to parse
    prose to act correctly; machine-relevant signals are in structured fields.
    """

    decision: str  # SESSION_FRESH | SESSION_REUSE | CLAIM | COMPLETE | WAIT
    step_id: str | None = None
    why: str


class Action(BaseModel):
    """A single action to be applied by the caller.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Deterministic action output from vectl_decide. Action payloads should behave
    like a discriminated union.

    For claim_and_dispatch:
        - task_id is the execution identity only (NOT a reuse handle)
        - reuse_token: opaque runner-specific reuse token from parent context
        - reuse_runner: runner namespace for the reuse token

    For complete:
        - evidence: completion evidence

    For escalate/attention:
        - reason: failure context
        - context: recommendation
    """

    action: Literal["claim_and_dispatch", "complete", "wait", "escalate"]
    # For claim_and_dispatch:
    step_id: str | None = None
    agent: str | None = None
    task_id: str | None = None  # Execution identity only; NOT a reuse handle
    reuse_token: str | None = None  # Opaque runner-specific reuse token
    reuse_runner: str | None = None  # Runner namespace for reuse_token
    step_description: str | None = None
    step_verification: str | None = None
    step_refs: list[str] | None = None
    # For complete:
    evidence: str | None = None
    # For wait/escalate:
    reason: str | None = None
    context: str | None = None


class DecideOutput(BaseModel):
    """Structured output from vectl_decide.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Contains deterministic automation/dispatch advisor output: top-level status summary,
    actions to execute, next caller-owned state, and policy metadata.

    Attributes:
        status: Primary top-level control summary. One of: dispatch | wait | blocked | done.
        reason_code: Machine-relevant reason code. One of: dispatch_available |
            waiting_on_running | capacity_full | no_executable_steps | repeated_failures.
            Complete set; unknown values should be treated as contract errors.
        message: Optional human-readable summary string.
        actions: List of action payloads (discriminated union).
        next_state: Full replacement object for caller-owned advisor state.
            Callers should replace their prior advisor state with this value.
        policy: Explicit policy metadata with reuse_ttl_s and escalation_threshold.
        decision_log: Optional debug/explanatory array. Callers should not need
            to parse prose to act correctly; machine-relevant signals are in
            structured fields above.
    """

    status: Literal["dispatch", "wait", "blocked", "done"]
    reason_code: Literal[
        "dispatch_available",
        "waiting_on_running",
        "capacity_full",
        "no_executable_steps",
        "repeated_failures",
    ]
    message: str | None = None
    actions: list[Action] = Field(default_factory=list)
    next_state: dict[str, object] = Field(default_factory=dict)
    policy: dict[str, object] = Field(default_factory=dict)
    decision_log: list[Decision] = Field(default_factory=list)
