"""
Shared orchestration-plane boundary types.

These types are owned by the orchestration plane and represent the contracts
between control, roster, runtime, and resolver components.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
"""

from dataclasses import dataclass
from typing import Literal

from vectl.models import IsolationMode


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
    """

    active_workspaces: tuple[str, ...]
    active_executions: tuple[str, ...]
    stalled_executions: tuple[str, ...]


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

    Attributes:
        step_id: Step being executed.
        role: Role performing the execution.
        runner: Runner identifier.
        work_refs: References to work artifacts.
        session_id: Session to use (if any).
    """

    step_id: str
    role: str
    runner: str
    work_refs: tuple[str, ...]
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
    """

    step_id: str
    status: Literal["success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None


@dataclass(frozen=True)
class ResolutionCase:
    """
    Problem handed from control to resolver when normal flow does not close.

    Attributes:
        reason: Why normal flow is blocked.
        core: Current core snapshot.
        roster: Current roster snapshot.
        runtime: Current runtime snapshot.
    """

    reason: str
    core: CoreSnapshot
    roster: RosterSnapshot
    runtime: RuntimeSnapshot


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
