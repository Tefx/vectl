"""
Shared orchestration-plane boundary types.

These types are owned by the orchestration plane and represent the contracts
between control, roster, runtime, resolver, and the upper-layer dispatch/prompt
policy surfaces.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
Authority: docs/ADR-worktree-support.md section "Core Design"
Authority: docs/DRIVER-ARCHITECTURE.md section 4 (completion authority)
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias

from vectl.models import IsolationMode

RequestMode: TypeAlias = Literal["start", "resume", "recover"]
"""Execution request launch mode.

Authority: docs/RFC-opencode-orchestration-runner.md section 6.1

- ``start``: Begin a fresh execution; create a new session if needed.
- ``resume``: Continue an existing session-backed execution.
- ``recover``: Reconstruct execution from durable artifacts, preferring native
  session continuation and falling back to fresh relaunch.
"""

SessionPolicy: TypeAlias = Literal["reuse_allowed", "reuse_forbidden"]
"""Session reuse policy for execution requests.

Authority: docs/RFC-opencode-orchestration-runner.md section 6.2

- ``reuse_allowed``: Orchestration may provide an existing session for
  continued work.
- ``reuse_forbidden``: Orchestration must force fresh execution semantics.
"""

RecoveredVia: TypeAlias = Literal["native_session_resume", "fresh_relaunch"]
"""Recovery path truth label.

Authority: docs/RFC-opencode-orchestration-runner.md section 10.3

The system must not collapse these two recovery paths into the same label.
They are persisted in ``continuity.json`` and mirrored into recovery event
payloads and human-readable summaries.
"""

__all__ = [
    "AgentExecutionState",
    "DispatchRoleSource",
    "DispatchSourceKind",
    "DispatchSpec",
    "ExecutionContext",
    "ExecutionRequest",
    "ExecutionResult",
    "IsolationMode",
    "MutationPolicy",
    "OpenCodeLaunchConfig",
    "PromptArtifactPaths",
    "PromptBundle",
    "PromptRegistry",
    "ReconcileDisposition",
    "ReconcileResult",
    "RecoveryAttempt",
    "RecoveryContinuity",
    "RecoveredVia",
    "RequestMode",
    "ResolutionCaseSource",
    "ResolverAuthorityContract",
    "ResolverClaimFlow",
    "ResolverExecutionSite",
    "ResolverMutationSurface",
    "RunnerHandoffEnv",
    "SessionPolicy",
    "ReviewOutcome",
    "RoleOutputContract",
    "RoleProfile",
    "RoleProfileRegistry",
    "SessionMode",
    "StructuredReviewResult",
    "WorktreeBinding",
]


DispatchSourceKind: TypeAlias = Literal["step", "resolution_subtask"]
DispatchRoleSource: TypeAlias = Literal[
    "step.agent",
    "default",
    "resolver",
]
ExecutionContext: TypeAlias = Literal["linked_worktree", "main_worktree"]
SessionMode: TypeAlias = Literal["fresh", "reuse"]
MutationPolicy: TypeAlias = Literal[
    "read_only",
    "worktree_changes",
    "vectl_facade_only",
]
RoleOutputContract: TypeAlias = Literal[
    "freeform_evidence",
    "structured_review_result",
    "vectl_facade_mutation",
    "resolution_report",
]
ReviewOutcome: TypeAlias = Literal[
    "pass",
    "needs_fix",
    "needs_replan",
    "operator_required",
]
ResolutionCaseSource: TypeAlias = Literal[
    "runtime_failure",
    "merge_conflict",
    "review_failed",
    "continuity_block",
    "authority_ambiguity",
    "unknown",
]


ResolverExecutionSite: TypeAlias = Literal["main_worktree"]
"""Resolver execution location contract.

The resolver contract is pinned to the main worktree. Resolver reasoning may
inspect per-step runtime snapshots, but its own execution context must remain
anchored to the canonical main worktree so plan reads/mutations do not drift to
isolated scratch branches.
"""


ResolverMutationSurface: TypeAlias = Literal["approved_vectl_facade_only"]
"""Resolver mutation authority contract.

Resolver-side writes are limited to the approved vectl facade boundary. Direct
mutation of plan/lifecycle state outside that facade is out of contract.
"""


ResolverClaimFlow: TypeAlias = Literal["normal_flow_only"]
"""Resolver claim authority contract.

Step claiming remains a normal-flow control/runtime activity. Resolver paths may
diagnose or recommend, but must not turn blocked handling into an alternate
claim authority.
"""


ReconcileDisposition: TypeAlias = Literal["merged", "noop"]
"""Allowed reconcile dispositions before orchestration completion.

Completion authority is pinned to the post-reconcile path only. A step may be
completed through orchestration contracts only after reconcile reached one of
these acceptance states.
"""


@dataclass(frozen=True)
class ResolverAuthorityContract:
    """Pinned resolver authority boundary for blocked/unresolved handling.

    Attributes:
        execution_site: Resolver runs from the canonical main worktree.
        mutation_surface: Resolver mutations must go through the approved vectl
            facade only.
        claim_flow: Claiming remains reserved for normal flow, not resolver flow.
    """

    execution_site: ResolverExecutionSite = "main_worktree"
    mutation_surface: ResolverMutationSurface = "approved_vectl_facade_only"
    claim_flow: ResolverClaimFlow = "normal_flow_only"


@dataclass(frozen=True)
class DispatchSpec:
    """Unified dispatch-layer semantic contract.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 7.1

    This contract bridges control output, authoritative step semantics,
    role/profile policy, prompt rendering input, and runtime execution input.
    It does not replace ``ExecutionRequest``; it precedes and informs it.
    """

    source_kind: DispatchSourceKind
    source_id: str
    role_id: str
    role_source: DispatchRoleSource
    execution_context: ExecutionContext
    runner: str
    session_mode: SessionMode
    reuse_token: str | None = None
    reuse_runner: str | None = None
    step_id: str | None = None
    description: str = ""
    verification: str | None = None
    refs: tuple[str, ...] = ()
    evidence_template: str | None = None
    verify_mode: Literal["must_green", "expected_red", "none"] = "none"
    prompt_family: str = ""
    output_contract: str = ""
    mutation_policy: MutationPolicy = "read_only"


@dataclass(frozen=True)
class RoleProfile:
    """Open role/profile contract for dispatch and prompt policy.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 8.2

    ``execution_context`` stays open to both ``linked_worktree`` and
    ``main_worktree`` so planner, reviewer, and resolver-family roles can run in
    the main worktree without redefining resolver as the only such actor.
    """

    role_id: str
    agent_id: str
    prompt_family: str
    execution_context: ExecutionContext
    mutation_policy: MutationPolicy
    session_policy: Literal["reuse_allowed", "reuse_forbidden"]
    output_contract: RoleOutputContract
    default_runner: str


class RoleProfileRegistry(Protocol):
    """Configuration-backed lookup for open-ended role IDs."""

    def get(self, role_id: str) -> RoleProfile: ...

    def has_role(self, role_id: str) -> bool: ...


@dataclass(frozen=True)
class PromptBundle:
    """Rendered prompt material for a dispatch spec.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 10.1
    """

    system_prompt: str
    task_prompt: str
    messages: tuple[dict[str, str], ...]


class PromptRegistry(Protocol):
    """Central prompt rendering boundary for role-aware dispatch."""

    def render(self, spec: DispatchSpec) -> PromptBundle: ...

    def has_role(self, role_id: str) -> bool: ...


@dataclass(frozen=True)
class StructuredReviewResult:
    """Machine-readable review/gate outcome contract.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md section 13.1

    Non-pass outcomes are intended to normalize into explicit resolution cases
    rather than prose-only control decisions.
    """

    review_outcome: ReviewOutcome
    summary: str
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreSnapshot:
    """
    Read-only view of authoritative core state needed by orchestration.

    Attributes:
        plan_complete: True if the plan has no remaining work.
        claimable_step_ids: Steps that may be claimed by this agent.
        in_progress_step_ids: Steps currently claimed by this agent.
        blocked_step_ids: Steps that cannot proceed normally.
        unresolved_reasons: Human-readable reasons why steps are blocked.
    """

    plan_complete: bool
    claimable_step_ids: tuple[str, ...]
    in_progress_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    unresolved_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RosterSnapshot:
    """
    Read-only view of reusable resource state.

    Attributes:
        available_agents: Agent roles with available capacity.
        working_agents: Agent roles currently in use.
        reusable_sessions: Sessions that may be reused.
        exhausted_roles: Roles that have exceeded reuse windows.
    """

    available_agents: tuple[str, ...]
    working_agents: tuple[str, ...]
    reusable_sessions: tuple[str, ...]
    exhausted_roles: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeSnapshot:
    """
    Read-only view of mechanical execution state.

    Attributes:
        active_workspaces: Workspaces currently in use.
        active_executions: Executions currently running.
        stalled_executions: Executions known to be stuck.
        pending_reconciles: Workspaces waiting to begin reconcile.
        active_reconciles: Workspaces actively reconciling.
        conflicted_reconciles: Workspaces with unresolved merge conflicts.
    """

    active_workspaces: tuple[str, ...]
    active_executions: tuple[str, ...]
    stalled_executions: tuple[str, ...]
    pending_reconciles: tuple[str, ...] = ()
    active_reconciles: tuple[str, ...] = ()
    conflicted_reconciles: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorktreeBinding:
    """
    Worktree binding metadata for isolated execution.

    Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 7.2

    Attributes:
        workspace_id: Unique workspace identifier.
        step_id: Step this worktree is bound to.
        worktree_path: Path to the worktree on disk.
        scratch_branch: Scratch branch name for this execution.
        target_ref: Target ref to merge back into.
        target_head_at_prepare: Commit SHA at prepare time.
        isolation: Isolation mode for this worktree.
    """

    workspace_id: str
    step_id: str
    worktree_path: str
    scratch_branch: str = ""
    target_ref: str = ""
    target_head_at_prepare: str = ""
    isolation: IsolationMode | None = None


@dataclass
class AgentExecutionState:
    """
    Execution tracking state for agent work.

    Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 8

    Attributes:
        execution_id: Unique execution identifier.
        step_id: Step being executed.
        workspace_id: Workspace hosting the execution.
        runner: Runner identifier.
        runner_handle: Runner-specific execution handle.
        session_id: Session identifier if reusing, else None.
        status: execution lifecycle status.
        started_at: Unix timestamp when execution started.
        last_update_at: Unix timestamp of last state update.
        artifact_refs: Tuple of artifact references produced.
    """

    execution_id: str
    step_id: str
    workspace_id: str
    runner: str
    runner_handle: str
    session_id: str | None = None
    status: Literal[
        "starting",
        "running",
        "stall",
        "success",
        "fail",
        "transport_error",
        "cancelled",
    ] = "starting"
    started_at: float = 0.0
    last_update_at: float = 0.0
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconcileResult:
    """
    Result of worktree reconciliation back to target.

    Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 9.2

    Attributes:
        execution_id: Execution that produced this reconcile.
        workspace_id: Workspace being reconciled.
        status: One of 'merged', 'noop', 'merge_conflict', 'aborted'.
        summary: Human-readable summary of reconcile outcome.
        conflict_files: Tuple of conflict file paths (if merge_conflict).
        artifact_refs: Tuple of artifact references for reconciliation evidence.
    """

    execution_id: str
    workspace_id: str
    status: Literal["merged", "noop", "merge_conflict", "aborted"]
    summary: str = ""
    conflict_files: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControlDecision:
    """
    Minimal next-step output from control.

    Attributes:
        kind: One of 'dispatch', 'resolve', 'wait', 'done'.
        reason: Human-readable explanation of the decision.
        step_id: Target step for dispatch (if kind='dispatch').
        role: Role to use for dispatch (if kind='dispatch').
    """

    kind: Literal["dispatch", "resolve", "wait", "done"]
    reason: str
    step_id: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class WorkLease:
    """
    Claimed reusable resource from roster.

    Attributes:
        role: The role this lease is for.
        runner: Runner identifier.
        agent_id: Assigned agent identifier.
        session_id: Session identifier if using reusable session.
    """

    role: str
    runner: str
    agent_id: str
    session_id: str | None


@dataclass(frozen=True)
class ExecutionRequest:
    """
    Mechanical execution request into runtime.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.6
    Authority: docs/RFC-opencode-orchestration-runner.md section 7.1

    Attributes:
        step_id: Step being executed.
        role: Role performing the execution.
        runner: Runner identifier.
        work_refs: References to work artifacts.
        agent_id: Agent identifier for the execution.
        prompt_bundle_path: Path to the rendered prompt bundle artifact.
        runner_prompt_path: Path to the runner-specific prompt artifact.
        request_mode: Launch mode semantics (``start``, ``resume``, ``recover``).
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.1
        session_policy: Whether an existing session may be reused.
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.2
        session_id: Session to use (if any).
    """

    step_id: str
    role: str
    runner: str
    work_refs: tuple[str, ...]
    agent_id: str = ""
    prompt_bundle_path: str = ""
    runner_prompt_path: str = ""
    request_mode: RequestMode = "start"
    session_policy: SessionPolicy = "reuse_forbidden"
    session_id: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """
    Mechanical execution result returned from runtime.

    Attributes:
        step_id: Step that was executed.
        status: One of 'success', 'fail', 'stall', 'transport_error'.
        output_summary: Human-readable summary of results.
        session_id: Session used (if any).
        operator_message: Operator/user-visible message required when runtime
            cannot close the problem mechanically. Unresolved runtime failures
            should surface this instead of silently collapsing into status only.
    """

    step_id: str
    status: Literal["success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None
    operator_message: str | None = None


@dataclass(frozen=True)
class RecoveryContinuity:
    """Recovery path truth label persisted at ``continuity.json``.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.3

    The system must not collapse native session resume and fresh relaunch
    into the same label. This contract enforces that distinction at the
    type level.

    Attributes:
        recovered_via: Which recovery path was actually taken.
        run_id: The run identifier this continuity record belongs to.
        step_id: The step identifier this continuity record belongs to.
        agent_id: The agent identifier for the recovered execution.
        runner: The runner used for the recovered execution.
        session_id: The session identifier, if a native session was reused.
        timestamp: ISO-8601 timestamp of when recovery was recorded.
    """

    recovered_via: RecoveredVia
    run_id: str = ""
    step_id: str = ""
    agent_id: str = ""
    runner: str = ""
    session_id: str | None = None
    timestamp: str = ""


@dataclass(frozen=True)
class RecoveryAttempt:
    """Individual resume or recover attempt record.

    Authority: docs/RFC-opencode-orchestration-runner.md section 10.4

    Records whether native session validation succeeded, why it may have
    failed, whether fallback relaunch was used, and resulting identifiers.

    Attributes:
        attempt_kind: Whether this was a resume attempt or a recover attempt.
        native_validation_ok: Whether native session validation passed.
        native_validation_failure_reason: Why validation failed, if it did.
        fallback_relaunch_used: Whether a fresh relaunch was used as fallback.
        resulting_run_id: The run identifier after the attempt.
        resulting_session_id: The session identifier after the attempt, if any.
    """

    attempt_kind: Literal["resume", "recover"]
    native_validation_ok: bool = False
    native_validation_failure_reason: str = ""
    fallback_relaunch_used: bool = False
    resulting_run_id: str = ""
    resulting_session_id: str | None = None


@dataclass(frozen=True)
class ResolutionCase:
    """
    Problem handed from control to resolver when normal flow does not close.

    Attributes:
        case_id: Explicit identifier for the resolution case.
        case_source: Coarse source tag for how the case arose.
        reason: Why normal flow is blocked.
        summary: Optional bounded human-readable case summary.
        core: Current core snapshot.
        roster: Current roster snapshot.
        runtime: Current runtime snapshot.
        blocked_step_ids: Optional blocked-step coordination context.
        artifact_refs: Optional evidence/artifact references preserved on the case.
    """

    reason: str
    core: CoreSnapshot
    roster: RosterSnapshot
    runtime: RuntimeSnapshot
    case_id: str = ""
    case_source: ResolutionCaseSource = "unknown"
    summary: str | None = None
    blocked_step_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolutionReport:
    """
    What resolver returns to control.

    Attributes:
        status: One of 'unblocked', 'waiting', 'operator_required', 'halt'.
        summary: Human-readable explanation of findings.
        evidence_refs: References to supporting evidence.
        operator_message: Message to surface to human operator.
    """

    status: Literal["unblocked", "waiting", "operator_required", "halt"]
    summary: str
    evidence_refs: tuple[str, ...] = ()
    operator_message: str | None = None


# ---------------------------------------------------------------------
# Prompt Artifact and Runner Handoff Contracts
# ---------------------------------------------------------------------

# Authority: docs/RFC-opencode-orchestration-runner.md sections 8, 8.5, 9.2
#
# These types pin the stable contract for prompt materialization and
# runner handoff *before* runner code is written. They freeze:
#   1. Artifact authority and workspace copy locations
#   2. Required VECTL_ORCH_* environment variables
#   3. OpenCode --file attachment and launch command contract


_RUNS_INPUT_DIR: Literal["input"] = "input"
"""Relative subdirectory under a run's artifact root for input artifacts.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.1

Every run's authoritative prompt artifacts must be materialized under::

    .vectl/runs/<run_id>/input/

This constant exists so that path resolution is derived from one frozen
definition rather than scattered string literals.
"""

_PROMPT_BUNDLE_FILENAME: Literal["prompt_bundle.json"] = "prompt_bundle.json"
"""Filename for the structured prompt bundle JSON artifact.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.3
"""

_RUNNER_PROMPT_FILENAME: Literal["runner_prompt.md"] = "runner_prompt.md"
"""Filename for the runner-consumable flattened prompt Markdown artifact.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.4
"""

_WORKSPACE_ORCH_DIR: Literal[".vectl/orch"] = ".vectl/orch"
"""Relative workspace directory for orchestration runtime artifacts.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.2
"""

_RUNNER_PROMPT_WORKSPACE_RELATIVE: Literal[".vectl/orch/runner_prompt.md"] = (
    ".vectl/orch/runner_prompt.md"
)
"""Workspace-relative path to the runner-consumable prompt copy.

Authority: docs/RFC-opencode-orchestration-runner.md section 8.2, 8.5

The workspace should receive a runner-readable prompt copy at this path,
allowing runner startup to use a stable file path inside the execution
workspace.
"""


@dataclass(frozen=True)
class PromptArtifactPaths:
    """Frozen contract pinning all prompt artifact location semantics.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8

    This dataclass pins the three required prompt artifact locations:

    1. **Authority prompt bundle** (structured JSON):
       ``<artifact_root>/<run_id>/input/prompt_bundle.json``
    2. **Authority runner prompt** (flattened Markdown):
       ``<artifact_root>/<run_id>/input/runner_prompt.md``
    3. **Workspace copy** (runner-consumable Markdown):
       ``<workspace>/.vectl/orch/runner_prompt.md``

    Attributes:
        prompt_bundle_path: Absolute path to the structured prompt bundle JSON
            under the run's authoritative artifact root.
        runner_prompt_path: Absolute path to the flattened runner prompt
            Markdown under the run's authoritative artifact root.
        workspace_prompt_path: Absolute path to the workspace copy of the
            runner prompt. This is the canonical location the runner reads
            at startup.
        workspace_prompt_relative: Workspace-relative path string for the
            runner prompt copy. Always equals ``.vectl/orch/runner_prompt.md``.
    """

    prompt_bundle_path: str
    runner_prompt_path: str
    workspace_prompt_path: str
    workspace_prompt_relative: str = _RUNNER_PROMPT_WORKSPACE_RELATIVE


@dataclass(frozen=True)
class RunnerHandoffEnv:
    """Frozen contract pinning the required VECTL_ORCH_* environment variables.

    Authority: docs/RFC-opencode-orchestration-runner.md section 8.5

    The process environment must include these variables for OpenCode runner
    handoff. They provide the runner with stable references to orchestration
    identity and prompt artifact locations.

    Attributes:
        VECTL_ORCH_RUN_ID: The run identifier for this orchestration execution.
        VECTL_ORCH_STEP_ID: The step being executed.
        VECTL_ORCH_AGENT_ID: The agent identifier for the execution.
        VECTL_ORCH_PROMPT_PATH: Path to the workspace-copy runner prompt file.
        VECTL_ORCH_PROMPT_BUNDLE_PATH: Path to the structured prompt bundle JSON.
    """

    VECTL_ORCH_RUN_ID: str
    VECTL_ORCH_STEP_ID: str
    VECTL_ORCH_AGENT_ID: str
    VECTL_ORCH_PROMPT_PATH: str
    VECTL_ORCH_PROMPT_BUNDLE_PATH: str

    def as_dict(self) -> dict[str, str]:
        """Render handoff environment as a plain dict suitable for ``subprocess`` env.

        Returns:
            Dictionary mapping each VECTL_ORCH_* variable name to its value.
        """
        return {
            "VECTL_ORCH_RUN_ID": self.VECTL_ORCH_RUN_ID,
            "VECTL_ORCH_STEP_ID": self.VECTL_ORCH_STEP_ID,
            "VECTL_ORCH_AGENT_ID": self.VECTL_ORCH_AGENT_ID,
            "VECTL_ORCH_PROMPT_PATH": self.VECTL_ORCH_PROMPT_PATH,
            "VECTL_ORCH_PROMPT_BUNDLE_PATH": self.VECTL_ORCH_PROMPT_BUNDLE_PATH,
        }


_OPENCODE_BOOTSTRAP_MESSAGE_START: Literal[
    "Read the attached runner prompt file, execute the requested task in the current workspace, and then exit."
] = "Read the attached runner prompt file, execute the requested task in the current workspace, and then exit."
"""Frozen one-shot bootstrap message for OpenCode start mode.

Authority: docs/RFC-opencode-orchestration-runner.md section 9.2
"""

_OPENCODE_BOOTSTRAP_MESSAGE_RESUME: Literal[
    "Continue this session by executing the attached runner prompt in the current workspace."
] = "Continue this session by executing the attached runner prompt in the current workspace."
"""Frozen one-shot bootstrap message for OpenCode resume mode.

Authority: docs/RFC-opencode-orchestration-runner.md section 9.3
"""


@dataclass(frozen=True)
class OpenCodeLaunchConfig:
    """Frozen contract pinning the OpenCode launch command surface and flags.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 9.2, 9.3

    This dataclass pins the complete OpenCode launch contract, including the
    required ``--file`` attachment. The handoff mechanism is frozen as:

        workspace prompt file + explicit env vars + ``--file`` attachment
        + bounded bootstrap message.

    Attributes:
        file_flag: The workspace-relative path passed via ``--file``.
            Always equals ``.vectl/orch/runner_prompt.md``.
        format_flag: The output format flag for OpenCode. Always ``json``.
        dir_flag_key: The directory flag name. Always ``--dir``.
        agent_flag_key: The agent selection flag name. Always ``--agent``.
        session_flag_key: The session continuation flag name. Always ``--session``.
        bootstrap_start: The bounded one-shot bootstrap message for start mode.
        bootstrap_resume: The bounded one-shot bootstrap message for resume mode.
    """

    file_flag: str = _RUNNER_PROMPT_WORKSPACE_RELATIVE
    format_flag: Literal["json"] = "json"
    dir_flag_key: Literal["--dir"] = "--dir"
    agent_flag_key: Literal["--agent"] = "--agent"
    session_flag_key: Literal["--session"] = "--session"
    bootstrap_start: str = _OPENCODE_BOOTSTRAP_MESSAGE_START
    bootstrap_resume: str = _OPENCODE_BOOTSTRAP_MESSAGE_RESUME
