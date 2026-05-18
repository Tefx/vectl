"""Snapshot, worktree binding, execution-state, and reconcile contracts.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Authority: docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md sections 7-9
Authority: docs/RFC-opencode-orchestration-runner.md section 7.3
"""

from dataclasses import dataclass
from typing import Literal

from vectl.models import IsolationMode
from vectl.orchestration.contract_literals import RequestMode, SessionPolicy

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
    Authority: docs/RFC-opencode-orchestration-runner.md section 7.3

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
        evidence_refs: Tuple of runner evidence references (stdout/stderr refs,
            session artifacts). Populated from RunnerPollResult on completion.
            Authority: docs/RFC-opencode-orchestration-runner.md section 7.3
        request_mode: Launch mode that created this execution.
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.1
        session_policy: Session reuse policy for this execution.
            Authority: docs/RFC-opencode-orchestration-runner.md section 6.2
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
    evidence_refs: tuple[str, ...] = ()
    request_mode: RequestMode = "start"
    session_policy: SessionPolicy = "reuse_forbidden"


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

