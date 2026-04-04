"""
Component interface Protocols for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ExecutionRequest,
    ExecutionResult,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
    WorkLease,
)

if TYPE_CHECKING:
    pass


class Control(Protocol):
    """
    Plan-aware orchestration flow.

    Responsibility:
        Plan-aware orchestration flow decisions.

    Owns:
        - plan-aware flow decisions
        - blocked/unresolved determination
        - when to invoke resolver
        - when to dispatch work using available resources

    Does Not Own:
        - authoritative lifecycle mutation semantics
        - reusable session internals
        - workspace/runner chores
        - long-running reasoning itself

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
    """

    def evaluate(
        self,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision:
        """
        Evaluate the current state and produce a control decision.

        Args:
            core: Current view of authoritative core state.
            roster: Current view of reusable resource state.
            runtime: Current view of mechanical execution state.

        Returns:
            ControlDecision indicating next action (dispatch/resolve/wait/done).
        """
        ...

    def apply_resolution(
        self,
        report: ResolutionReport,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision:
        """
        Apply a resolution report and re-evaluate.

        Args:
            report: ResolutionReport from resolver.
            core: Refreshed core snapshot.
            roster: Refreshed roster snapshot.
            runtime: Refreshed runtime snapshot.

        Returns:
            ControlDecision following application of the resolution.
        """
        ...


class Roster(Protocol):
    """
    Reusable resource registry.

    Responsibility:
        Reusable agent/session resource tracking.

    Owns:
        - reusable agent/session metadata
        - TTL / reuse-window bookkeeping
        - compatibility/capability matching for resource reuse

    Does Not Own:
        - plan semantics
        - blocked-state meaning
        - next-step orchestration decisions

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.2
    """

    def snapshot(self) -> RosterSnapshot:
        """
        Capture current roster state.

        Returns:
            RosterSnapshot representing available reusable resources.
        """
        ...

    def claim(self, role: str) -> WorkLease | None:
        """
        Claim a compatible reusable resource.

        Args:
            role: The role being sought.

        Returns:
            WorkLease if a compatible resource is available, else None.
        """
        ...

    def release(self, lease: WorkLease) -> None:
        """
        Release a previously claimed lease.

        Args:
            lease: The WorkLease to release.
        """
        ...

    def register(
        self,
        lease: WorkLease,
        expires_at: float,
    ) -> None:
        """
        Register a new resource into the roster.

        Args:
            lease: The WorkLease to register.
            expires_at: Unix timestamp when this lease expires.
        """
        ...


class Runtime(Protocol):
    """
    Mechanical execution chores.

    Responsibility:
        Worktree/workspace preparation and execution support.

    Owns:
        - workspace/worktree preparation and cleanup
        - runner startup / resume / shutdown helpers
        - mechanical execution handles and collection

    Does Not Own:
        - plan-aware dispatch decisions
        - blocked-state reasoning
        - authority mutation semantics

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.3
    """

    def snapshot(self) -> RuntimeSnapshot:
        """
        Capture current runtime state.

        Returns:
            RuntimeSnapshot representing active mechanical state.
        """
        ...

    def prepare(self, request: ExecutionRequest) -> str:
        """
        Prepare workspace for execution.

        Args:
            request: The execution request.

        Returns:
            Workspace identifier.
        """
        ...

    def start(self, request: ExecutionRequest, workspace: str) -> str:
        """
        Start execution in prepared workspace.

        Args:
            request: The execution request.
            workspace: Workspace identifier from prepare().

        Returns:
            Execution identifier.
        """
        ...

    def collect(self, execution_id: str) -> ExecutionResult | None:
        """
        Collect results from an execution.

        Args:
            execution_id: The execution identifier.

        Returns:
            ExecutionResult if complete, else None.
        """
        ...

    def cleanup(self, workspace: str) -> None:
        """
        Clean up a workspace after execution.

        Args:
            workspace: Workspace identifier to clean up.
        """
        ...


class Resolver(Protocol):
    """
    Reasoning over blocked or unresolved cases.

    Responsibility:
        Blocker investigation and unblock path finding.

    Owns:
        - blocker investigation
        - use of allowed tools to find an unblock path
        - returning a bounded outcome to control

    Does Not Own:
        - permanent plan/control authority
        - reusable resource bookkeeping
        - mechanical startup/dispatch chores

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.4
    """

    def resolve(self, case: ResolutionCase) -> ResolutionReport:
        """
        Investigate a blocked/unresolved case.

        Args:
            case: The ResolutionCase describing the problem.

        Returns:
            ResolutionReport with findings and recommended action.
        """
        ...
