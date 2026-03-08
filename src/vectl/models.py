"""Data models and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

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
    refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
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


class StepState(BaseModel):
    """Mutable runtime state for a single step, stored in state.json.

    .. deprecated::
        Will be removed after unified-state migration.
        See docs/ADR-unified-state.md.
    """

    status: StepStatus = StepStatus.PENDING
    claimed_by: str | None = None
    claimed_at: str | None = None
    done_at: str | None = None
    evidence: str | None = None
    skipped_reason: str | None = None
    rejection_reason: str | None = None
    rejection_history: list[RejectionEntry] = Field(default_factory=list)
    affinity_override: bool = False
    affinity_override_by: str | None = None
    affinity_override_at: str | None = None


class PhaseState(BaseModel):
    """Mutable runtime state for a phase, stored in state.json.

    .. deprecated::
        Will be removed after unified-state migration.
        See docs/ADR-unified-state.md.
    """

    status: PhaseStatus = PhaseStatus.PENDING
    evidence: str | None = None


class PlanState(BaseModel):
    """Top-level state document stored in .git/vectl/state.json.

    Contains all mutable runtime state separated from the plan definition.
    The plan_id field links this state to a specific plan definition.

    .. deprecated::
        Will be removed after unified-state migration.
        See docs/ADR-unified-state.md.
    """

    plan_id: str
    steps: dict[str, StepState] = Field(default_factory=dict)
    phases: dict[str, PhaseState] = Field(default_factory=dict)
    clipboard: Clipboard | None = None


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
        """Find a step by ID across all phases."""
        for phase in self.phases:
            for step in phase.steps:
                if step.id == step_id:
                    return phase, step
        return None

    def find_phase(self, phase_id: str) -> Phase | None:
        """Find a phase by ID."""
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        return None


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
# Orphan Detection
# ---------------------------------------------------------------------------


@dataclass
class OrphanEntry:
    """An orphan state entry that exists in state.json but has no matching
    definition in plan.yaml.

    .. deprecated::
        Will be removed after unified-state migration.
        See docs/ADR-unified-state.md.
    """

    kind: str  # "phase" or "step"
    id: str
    phase_id: str | None = None  # Only set for step orphans
