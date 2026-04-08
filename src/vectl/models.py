"""Data models and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    """Status of a single step."""

    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class SkipReason(str, Enum):
    """Reason for skipping a step."""

    SUPERSEDED = "superseded"
    IRRELEVANT = "irrelevant"
    ABSORBED = "absorbed"
    DEPRIORITIZED = "deprioritized"


class PhaseStatus(str, Enum):
    """Status of a phase."""

    LOCKED = "locked"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class AffinityMode(str, Enum):
    """Agent affinity enforcement mode for steps.

    RFC: docs/RFC-affinity.md
    Controls whether agent suggestions are enforced during claim.
    """

    SUGGESTED = "suggested"  # Warn on non-matching agent, allow claim
    EXCLUSIVE = "exclusive"  # Reject non-matching agent unless --force


class IsolationMode(str, Enum):
    """Authoritative step-level execution isolation semantic.

    Authority:
        docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md section 2
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 7

    This is intentionally a single authoritative field value set for step
    semantics. It does not introduce architecture-level continuity concepts.
    """

    DEFAULT = "default"
    WORKSPACE = "workspace"
    INDEPENDENT = "independent"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RejectionEntry(BaseModel):
    """A single rejection event in a step's history."""

    reason: str
    timestamp: str
    reviewer: str = ""


class Step(BaseModel):
    """A single actionable work item inside a phase."""

    id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    description: str = ""
    verification: str = ""
    # Feature request (2026-02-12): claim-time guidance.
    # Source: user feature request "Output guidance when running vectl claim".
    # R2: step-level evidence template needs a place to store short copy/paste text.
    evidence_template: str = ""
    # RFC: docs/RFC-expected-red-verification-semantics.md
    # Verification mode: None=red is failure, "expected_red"=red output acceptable for missing behavior
    verify: Literal["expected_red", "must_green"] | None = None
    refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    # Authority:
    #   docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 2 and 3
    #   docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 7
    # Single authoritative task isolation semantic.
    isolation: IsolationMode = IsolationMode.DEFAULT
    agent: str | None = None
    # RFC: docs/RFC-affinity.md
    # Agent affinity enforcement mode. None inherits from plan.default_affinity.
    affinity: AffinityMode | None = None
    claimed_by: str | None = None
    claimed_at: str | None = None
    done_at: str | None = None
    evidence: str | None = None
    skipped_reason: str | None = None
    rejection_reason: str | None = None
    rejection_history: list[RejectionEntry] = Field(default_factory=list)
    # RFC: docs/RFC-affinity.md
    # Audit trail for --force override of exclusive affinity.
    affinity_override: bool = False
    affinity_override_by: str | None = None
    affinity_override_at: str | None = None

    @model_validator(mode="after")
    def _validate_status_fields(self) -> Step:
        if self.status == StepStatus.SKIPPED and not self.skipped_reason:
            raise ValueError(f"Step '{self.id}': skipped_reason is required when status is skipped")
        if self.status == StepStatus.REJECTED and not self.rejection_reason:
            raise ValueError(
                f"Step '{self.id}': rejection_reason is required when status is rejected"
            )
        if self.status == StepStatus.CLAIMED and not self.claimed_by:
            raise ValueError(f"Step '{self.id}': claimed_by is required when status is claimed")
        return self


class Phase(BaseModel):
    """A group of steps with a gate criterion and DAG dependencies."""

    id: str
    name: str
    status: PhaseStatus = PhaseStatus.PENDING
    gate: str = ""
    gate_script: str | None = None
    context: str = ""
    depends_on: list[str] = Field(default_factory=list)
    evidence: str | None = None
    steps: list[Step] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


class Clipboard(BaseModel):
    """Single-slot clipboard for cross-agent communication.

    RFC: docs/RFC-clipboard.md
    Used for ad-hoc information that doesn't follow DAG edges:
    cross-tool handoff, cross-phase broadcasts, reviewer notes.
    """

    author: str
    summary: str
    content: str
    written_at: str  # ISO 8601 timestamp
    expires_at: str  # ISO 8601 timestamp, server-computed from TTL

    @model_validator(mode="after")
    def _validate_fields(self) -> Clipboard:
        if not self.author or not self.author.strip():
            raise ValueError("Clipboard author cannot be empty")
        if not self.content or not self.content.strip():
            raise ValueError("Clipboard content cannot be empty or whitespace-only")
        return self


class Plan(BaseModel):
    """Top-level plan document."""

    version: int = 1
    project: str
    plan_id: str | None = None
    strategy_ref: str = ""
    context: str = ""
    # Feature request (2026-02-12): claim-time guidance.
    # Source: user feature request "Output guidance when running vectl claim".
    # R3: project-level guidance should be available at claim time.
    project_guidance: str = ""
    # RFC: docs/RFC-clipboard.md
    # Single-slot clipboard for cross-agent handoff/broadcast.
    # Placed before phases so it appears first in YAML output.
    clipboard: Clipboard | None = None
    # RFC: docs/RFC-affinity.md
    # Plan-level default affinity mode for steps without explicit affinity.
    default_affinity: AffinityMode = AffinityMode.SUGGESTED
    phases: list[Phase] = Field(default_factory=list)

    # ---- helpers ----

    def find_step(self, step_id: str) -> tuple[Phase, Step] | None:
        """Find a step by ID across all phases.

        Supports qualified lookup (phase.step format) and unqualified lookup.

        Priority:
        1. Exact match: try to find step with ID == step_id across all phases
        2. Qualified fallback: if step_id contains '.', split by partition('.')
           to get (phase_id, step_suffix) and look up step with ID == step_suffix
           within that specific phase
        """
        # Priority 1: exact match
        for phase in self.phases:
            for step in phase.steps:
                if step.id == step_id:
                    return phase, step

        # Priority 2: qualified lookup (phase.step format)
        if "." in step_id:
            phase_prefix, _, step_suffix = step_id.partition(".")
            if phase_prefix and step_suffix:
                found_phase = self.find_phase(phase_prefix)
                if found_phase is not None:
                    for found_step in found_phase.steps:
                        # Look for the step_suffix within the specified phase
                        if found_step.id == step_suffix:
                            return found_phase, found_step

        return None

    def find_phase(self, phase_id: str) -> Phase | None:
        """Find a phase by ID."""
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        return None


def format_step_selector(phase_id: str, step_id: str) -> str:
    """Return canonical selector text for step-oriented diagnostics.

    If a stored step ID is already phase-qualified (contains ``.``), emit it
    as-is to avoid duplicating the phase prefix in user-facing error messages.
    """
    if "." in step_id:
        return step_id
    return f"{phase_id}.{step_id}"


# ---------------------------------------------------------------------------
# Exceptions & Validation Types
# ---------------------------------------------------------------------------


class CASConflictError(Exception):
    """Raised when a CAS (Compare-And-Swap) conflict is detected."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"CAS conflict: {path} was modified by another process")


class PlanIOError(Exception):
    """Raised when plan IO fails."""


class PlanValidationIssue:
    """A single validation error or warning."""

    def __init__(self, message: str, *, is_warning: bool = False) -> None:
        self.message = message
        self.is_warning = is_warning

    def __repr__(self) -> str:
        kind = "WARNING" if self.is_warning else "ERROR"
        return f"PlanValidationIssue({kind}: {self.message})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PlanValidationIssue):
            return NotImplemented
        return self.message == other.message and self.is_warning == other.is_warning


@dataclass(frozen=True)
class DuplicateStepIdDiagnostic:
    """Plan-wide duplicate step-ID conflict details.

    Used by read-only diagnostic surfaces (status/review/show/dag/validate)
    and by targeted-command repair recommendations.
    """

    step_id: str
    phase_ids: list[str]
    occurrences: int


@dataclass(frozen=True)
class DuplicateStepIdPhaseRef:
    """One conflicting phase for a duplicate step ID."""

    phase: str


@dataclass(frozen=True)
class DuplicateStepIdResolutionPath:
    """Stable resolution metadata for duplicate step-ID automation."""

    explicit_phase: str
    auto_migrate_flag: str
    migration_tool: str


@dataclass(frozen=True)
class DuplicateStepIdRepairRecommendation:
    """Structured next-step recommendation for duplicate step IDs."""

    type: str
    step_id: str
    duplicates: list[DuplicateStepIdPhaseRef]
    resolution_path: DuplicateStepIdResolutionPath


@dataclass(frozen=True)
class DuplicateStepIdDiagnostics:
    """Aggregate duplicate step-ID diagnostics and repair guidance."""

    conflicts: list[DuplicateStepIdDiagnostic]
    recommendations: list[DuplicateStepIdRepairRecommendation]


@dataclass(frozen=True)
class DuplicateStepIdGroup:
    """Duplicate step-ID group summary for migration reports."""

    step_id: str
    phases: list[str]
    occurrences: int


@dataclass(frozen=True)
class DuplicateStepIdRenameEntry:
    """One deterministic step-ID rename entry."""

    phase_id: str
    old_step_id: str
    new_step_id: str
    reason: str


@dataclass(frozen=True)
class DuplicateStepIdDependsOnRewrite:
    """One depends_on rewrite record for migration reports."""

    phase_id: str
    step_id: str
    old_depends_on: list[str]
    new_depends_on: list[str]


@dataclass(frozen=True)
class DuplicateStepIdClaimConflict:
    """One claimed-step conflict blocking duplicate-ID migration apply."""

    phase_id: str
    step_id: str
    claimed_by: str


@dataclass(frozen=True)
class DuplicateStepIdRetryEvidence:
    """Retry outcome schema for auto-migrate wrapper flows."""

    attempted: bool
    succeeded: bool
    error: str | None


@dataclass(frozen=True)
class DuplicateStepIdDryRunReport:
    """Machine-readable duplicate-ID migration dry-run output."""

    run_mode: str
    duplicate_groups: list[DuplicateStepIdGroup]
    rename_map: list[DuplicateStepIdRenameEntry]
    depends_on_rewrites: list[DuplicateStepIdDependsOnRewrite]
    affected_phases: list[str]
    claimed_step_conflicts: list[DuplicateStepIdClaimConflict]
    compatibility_notes: list[str]
    requires_manual_follow_up: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "run_mode": self.run_mode,
            "duplicate_groups": [
                {
                    "step_id": group.step_id,
                    "phases": list(group.phases),
                    "occurrences": group.occurrences,
                }
                for group in self.duplicate_groups
            ],
            "rename_map": [
                {
                    "phase_id": entry.phase_id,
                    "old_step_id": entry.old_step_id,
                    "new_step_id": entry.new_step_id,
                    "reason": entry.reason,
                }
                for entry in self.rename_map
            ],
            "depends_on_rewrites": [
                {
                    "phase_id": rewrite.phase_id,
                    "step_id": rewrite.step_id,
                    "old_depends_on": list(rewrite.old_depends_on),
                    "new_depends_on": list(rewrite.new_depends_on),
                }
                for rewrite in self.depends_on_rewrites
            ],
            "affected_phases": list(self.affected_phases),
            "claimed_step_conflicts": [
                {
                    "phase_id": conflict.phase_id,
                    "step_id": conflict.step_id,
                    "claimed_by": conflict.claimed_by,
                }
                for conflict in self.claimed_step_conflicts
            ],
            "compatibility_notes": list(self.compatibility_notes),
            "requires_manual_follow_up": self.requires_manual_follow_up,
        }


@dataclass(frozen=True)
class DuplicateStepIdMigrationEvidence:
    """Structured duplicate-ID migration evidence payload."""

    command: str
    command_args: list[str]
    run_mode: str
    migrated: bool
    rename_map: list[DuplicateStepIdRenameEntry]
    depends_on_rewrites: list[DuplicateStepIdDependsOnRewrite]
    affected_phases: list[str]
    claimed_step_conflicts: list[DuplicateStepIdClaimConflict]
    retry: DuplicateStepIdRetryEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "command_args": list(self.command_args),
            "run_mode": self.run_mode,
            "migrated": self.migrated,
            "rename_map": [
                {
                    "phase_id": entry.phase_id,
                    "old_step_id": entry.old_step_id,
                    "new_step_id": entry.new_step_id,
                }
                for entry in self.rename_map
            ],
            "depends_on_rewrites": [
                {
                    "phase_id": rewrite.phase_id,
                    "step_id": rewrite.step_id,
                    "old_depends_on": list(rewrite.old_depends_on),
                    "new_depends_on": list(rewrite.new_depends_on),
                }
                for rewrite in self.depends_on_rewrites
            ],
            "affected_phases": list(self.affected_phases),
            "claimed_step_conflicts": [
                {
                    "phase_id": conflict.phase_id,
                    "step_id": conflict.step_id,
                    "claimed_by": conflict.claimed_by,
                }
                for conflict in self.claimed_step_conflicts
            ],
            "retry": {
                "attempted": self.retry.attempted,
                "succeeded": self.retry.succeeded,
                "error": self.retry.error,
            },
        }


@dataclass(frozen=True)
class DuplicateStepIdApplyResult:
    """Apply-mode result for duplicate-ID migration engine."""

    report: DuplicateStepIdDryRunReport
    evidence: DuplicateStepIdMigrationEvidence
    migrated: bool
    new_plan_hash: str | None


class PlanError(Exception):
    """Raised when a plan operation fails."""


class AffinityError(PlanError):
    """Raised when an exclusive affinity violation occurs during claim.

    RFC: docs/RFC-affinity.md
    """

    def __init__(self, step_id: str, expected_agent: str, claiming_agent: str) -> None:
        self.step_id = step_id
        self.expected_agent = expected_agent
        self.claiming_agent = claiming_agent
        super().__init__(
            f"Step '{step_id}' has exclusive affinity for '{expected_agent}'. "
            f"Use --force to override."
        )


class AmbiguousMatchError(PlanError):
    """Raised when a fuzzy match finds multiple candidates."""

    def __init__(self, keyword: str, candidates: list[str]) -> None:
        self.keyword = keyword
        self.candidates = candidates
        super().__init__(f"Ambiguous match for '{keyword}': {candidates}. Be more specific.")


class NoMatchError(PlanError):
    """Raised when a fuzzy match finds no candidates."""

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword
        super().__init__(f"No checklist item matches '{keyword}'. Use --append to add a new item.")


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
    """A running task tracked by the orchestrator.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md
    Used by vectl_decide to track in-flight work for session reuse decisions.

    Attributes:
        step_id: Step identifier.
        agent: Agent name.
        task_id: Orchestrator-visible execution/task identifier (NOT a reuse handle).
        runner: Runner namespace/source that owns any reuse semantics attached
            to that task. Required; allowed values at rollout are "claude" and "task".
        dispatched_at: time.time() when dispatched.
    """

    step_id: str
    agent: str
    task_id: str
    runner: str
    dispatched_at: float


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
    """A single action to be executed by the orchestrator.

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
    Contains deterministic orchestration advisor output: top-level status summary,
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
