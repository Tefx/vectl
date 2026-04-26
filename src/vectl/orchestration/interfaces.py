"""
Component interface Protocols for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4
Authority: docs/RFC-orch-drive.md sections 12, 13 (planner/resolver integration)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DriveBarrier,
    DriveRecord,
    ExecutionRequest,
    ExecutionResult,
    PlannerMutationBundle,
    PlannerRequest,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
    WorkLease,
)

if TYPE_CHECKING:
    pass


@runtime_checkable
class RunnerBackend(Protocol):
    """Mechanical runner backend surface.

    Responsibility:
        Runner-process/session start and collection mechanics only.

    Does Not Own:
        - workspace/worktree lifecycle
        - plan/lifecycle mutation authority
        - blocked/unresolved interpretation

    This split exists to keep runner mechanics from absorbing runtime lifecycle
    policy. It is an ownership contract, not a new subsystem.
    """

    def start(self, request: ExecutionRequest, workspace: str) -> str:
        """Start mechanical execution in a prepared workspace."""
        ...

    def collect(self, execution_id: str) -> ExecutionResult | None:
        """Collect mechanical execution results without reinterpreting policy."""
        ...


@runtime_checkable
class RuntimeLifecycle(Protocol):
    """Mechanical runtime lifecycle surface.

    Responsibility:
        Workspace/worktree preparation, cleanup, and runtime state snapshots.

    Does Not Own:
        - runner backend execution internals
        - plan/lifecycle mutation authority
        - resolver reasoning

    This split keeps runtime lifecycle ownership explicit and non-overlapping
    with the runner backend.
    """

    def snapshot(self) -> RuntimeSnapshot:
        """Capture current runtime lifecycle state."""
        ...

    def prepare(self, request: ExecutionRequest) -> str:
        """Prepare workspace/worktree state for a future runner start."""
        ...

    def cleanup(self, workspace: str) -> None:
        """Clean up lifecycle-owned workspace/worktree state."""
        ...


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
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
        open_case_ids: tuple[str, ...] = (),
        recovery_gate_blocked: bool = False,
    ) -> ControlDecision:
        """
        Evaluate the current state and produce a control decision.

        Args:
            core: Current view of authoritative core state.
            roster: Current view of reusable resource state.
            runtime: Current view of mechanical execution state.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.
            open_case_ids: Open resolution case identifiers.
            recovery_gate_blocked: Whether recovery currently blocks dispatch.

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
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
    ) -> ControlDecision:
        """
        Apply a resolution report and re-evaluate.

        Args:
            report: ResolutionReport from resolver.
            core: Refreshed core snapshot.
            roster: Refreshed roster snapshot.
            runtime: Refreshed runtime snapshot.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.

        Returns:
            ControlDecision following application of the resolution.
        """
        ...

    def apply_planner_result(
        self,
        bundle: PlannerMutationBundle,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
        drive: DriveRecord | None = None,
        barrier: DriveBarrier | None = None,
    ) -> ControlDecision:
        """
        Apply planner mutation bundle and return a follow-up control decision.

        Args:
            bundle: Machine-readable planner output contract.
            core: Refreshed authoritative core snapshot.
            roster: Refreshed roster component snapshot.
            runtime: Refreshed runtime component snapshot.
            drive: Current drive record, if a drive session is active.
            barrier: Current barrier state, if the drive is in barrier mode.

        Returns:
            Follow-up control decision after applying planner outcome.
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


class Runtime(RuntimeLifecycle, RunnerBackend, Protocol):
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

    Ownership Split:
        ``Runtime`` composes two non-overlapping responsibilities:
        ``RuntimeLifecycle`` owns workspace/worktree lifecycle and snapshots;
        ``RunnerBackend`` owns start/collect mechanics. This contract pins the
        split so backend execution logic does not absorb runtime lifecycle policy.

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

    Contract Locks:
        - resolver executes from the canonical main worktree
        - resolver mutates only through the approved vectl facade
        - resolver never becomes an alternate claim path

    Selection:
        The orchestration app selects the resolver entry from
        ``resolver.default_role_id`` and still expects the bounded
        ``ResolutionCase -> ResolutionReport`` contract at this seam.

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


class LifecycleMutationPort(Protocol):
    """Approved orchestration mutation facade.

    This is the narrow lifecycle-mutation port that runtime/control/resolver
    seams are allowed to target. It pins two hard rules:

    - claim is normal-flow only
    - completion requires post-reconcile acceptance (``merged`` or ``noop``)
    """

    def claim_step(
        self,
        step_id: str,
        agent: str,
        *,
        force: bool = False,
        flow: Literal["normal"] = "normal",
    ) -> None:
        """Claim a step only from normal orchestration flow."""
        ...

    def complete_step(
        self,
        step_id: str,
        evidence: str,
        *,
        reconcile_disposition: Literal["merged", "noop"],
    ) -> None:
        """Complete a step only after reconcile closed as ``merged`` or ``noop``."""
        ...


class PlannerAdapter(Protocol):
    """Approved planner facade boundary for drive scheduling.

    Authority: docs/RFC-orch-drive.md sections 13, 13.4

    The planner adapter defines the contract for invoking a planner child
    run and consuming its machine-readable mutation output.

    Does Not Own:
        - plan mutation semantics beyond the approved vectl facade surface
        - scheduling or barrier policy
        - resolver reasoning

    Contract Locks:
        - All mutations must be applied through approved vectl facade only
        - Direct ``plan.yaml`` edits remain forbidden
    """

    def invoke(self, request: PlannerRequest) -> PlannerMutationBundle:
        """Invoke the planner with a mutation request.

        Args:
            request: Machine-readable planner request from resolver.

        Returns:
            PlannerMutationBundle with structured mutation output.
        """
        ...

    def apply(self, bundle: PlannerMutationBundle) -> None:
        """Apply a validated planner mutation bundle through the vectl facade.

        Args:
            bundle: A bundle with status ``applyable`` to apply.

        Raises:
            ValueError: If bundle status is not ``applyable``.
        """
        ...
