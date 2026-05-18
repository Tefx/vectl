"""Drive scheduling and control decision contracts.

Authority: docs/RFC-orch-drive.md sections 8, 9, 12, 13, 14, 15
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.4
"""

from dataclasses import dataclass, field
from typing import Literal

from vectl.orchestration.contract_literals import (
    BarrierReason,
    ChildRunKind,
    ChildRunStatus,
    DriveStatus,
    PlannerBundleStatus,
    PlannerMutationAction,
)

@dataclass(frozen=True)
class PlannerRequest:
    """Machine-readable request from resolver for planner invocation.

    Authority: docs/RFC-orch-drive.md section 12.2

    When a resolver determines that plan mutation is required, it attaches
    a ``planner_request`` to its ``ResolutionReport`` rather than emitting
    prose-only instructions.

    Attributes:
        reason: Why replanning is required.
        affected_steps: Step identifiers that motivated the replan request.
        evidence_refs: Artifact references supporting this request.
        constraints: Bounded operational constraints the planner must respect.
        mutations: Optional approved facade mutations to apply. Empty means
            the planner request records intent only and must not imply a plan
            edit.
    """

    reason: str
    affected_steps: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    mutations: tuple["PlannerMutationItem", ...] = ()


@dataclass(frozen=True)
class PlannerMutationItem:
    """Single mutation item within a PlannerMutationBundle.

    Authority: docs/RFC-orch-drive.md section 8.5.1

    Each mutation item must map one-to-one to an approved vectl facade
    mutation. ``arguments`` contains the action-specific payload.

    Attributes:
        action: The vectl facade mutation action.
        arguments: Action-specific key-value payload.
        reason: Why this mutation is required.
        safety_notes: Bounded caveats or follow-up constraints.
    """

    action: PlannerMutationAction
    arguments: dict[str, object] = field(default_factory=dict)
    reason: str = ""
    safety_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannerMutationBundle:
    """Machine-readable planner output contract.

    Authority: docs/RFC-orch-drive.md sections 8.5, 13.3

    Planner output must be strict structured JSON; it must not rely on
    free-form prose to describe mutations. All mutations must be applied
    through approved vectl facade surfaces only.

    Attributes:
        status: Whether the bundle is applyable, requires operator, or halts.
        summary: Human-readable explanation of the planner output.
        mutations: Ordered list of mutation items.
        affected_steps: Step identifiers affected by this bundle.
        evidence_refs: Artifact references supporting the mutations.
        safety_notes: Bounded caveats or follow-up constraints for the bundle.
    """

    status: PlannerBundleStatus
    summary: str
    mutations: tuple[PlannerMutationItem, ...] = ()
    affected_steps: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    safety_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DriveBarrier:
    """Barrier state embedded within a DriveRecord.

    Authority: docs/RFC-orch-drive.md section 8.4

    Barrier captures exceptional-flow state: reason, timestamp, pending
    resolver/planner runs, and the set of active child runs at entry time.

    Persistence rule:
        ``DriveBarrier`` is embedded as ``DriveRecord.barrier``. Barrier
        subfields must not be duplicated as top-level sibling fields on
        the drive record.

    Attributes:
        reason: Why the barrier was entered.
        entered_at: Unix timestamp when barrier was entered.
        case_ids: Case identifiers associated with this barrier.
        pending_resolver_run_id: Resolver child run in progress (if any).
        pending_planner_run_id: Planner child run in progress (if any).
        active_child_run_ids_at_entry: Active child runs when barrier was entered.
    """

    reason: BarrierReason
    entered_at: float = 0.0
    case_ids: tuple[str, ...] = ()
    pending_resolver_run_id: str | None = None
    pending_planner_run_id: str | None = None
    active_child_run_ids_at_entry: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChildRunRef:
    """Lightweight reference to a child run owned by a drive.

    Authority: docs/RFC-orch-drive.md section 8.3

    Every child run (step, resolver, or planner) persisted within a drive
    must carry this shape.

    Attributes:
        run_id: Unique child run identifier.
        drive_id: Owning drive identifier.
        kind: Whether this is a step, resolver, or planner child run.
        status: Current lifecycle status of the child run.
        step_id: Step identifier (when kind='step').
        case_id: Case identifier (when kind='resolver').
        planner_request_id: Planner request identifier (when kind='planner').
        workspace: Workspace path for the child run.
        runner: Runner identifier for the child run.
        session_id: Session identifier, if session reused.
        artifact_root: Artifact directory root for the child run.
    """

    run_id: str
    drive_id: str
    kind: ChildRunKind
    status: ChildRunStatus = "pending"
    step_id: str | None = None
    case_id: str | None = None
    planner_request_id: str | None = None
    workspace: str = ""
    runner: str = ""
    session_id: str | None = None
    artifact_root: str = ""


@dataclass(frozen=True)
class DriveRecord:
    """Durable parent orchestration state for a plan-level scheduling session.

    Authority: docs/RFC-orch-drive.md section 8.1

    ``DriveRecord`` owns frontier scheduling, parallelism limits, barrier
    state, blocked/open case state, recovery cursor, and aggregate
    observability for one plan-level orchestration session.

    Persistence rule:
        ``DriveRecord`` embeds ``DriveBarrier | None`` as the authoritative
        barrier field. Barrier subfields must not be duplicated as top-level
        sibling fields on the drive record.

    Attributes:
        drive_id: Unique drive identifier.
        plan_path: Canonical absolute path to ``plan.yaml``.
        status: Current drive lifecycle status.
        started_at: Unix timestamp when drive was created.
        updated_at: Unix timestamp of last state mutation.
        finished_at: Unix timestamp when drive reached terminal status (if any).
        agent: Agent role that owns this drive.
        max_parallelism: Maximum concurrent step child runs allowed.
        active_child_run_ids: Currently active child run identifiers.
        frontier_step_ids: Ready DAG frontier step identifiers.
        blocked_case_ids: Open case identifiers blocking scheduling.
        barrier: Current barrier state, if any.
        operator_pause_state: Whether the drive is operator-paused.
        summary: Human-readable aggregate progress description.
    """

    drive_id: str
    plan_path: str
    status: DriveStatus = "running"
    started_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float | None = None
    agent: str = ""
    max_parallelism: int = 4
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    blocked_case_ids: tuple[str, ...] = ()
    barrier: DriveBarrier | None = None
    operator_pause_state: Literal["active", "paused"] = "active"
    summary: str = ""


@dataclass(frozen=True)
class DriveLease:
    """Scheduler-owned lease for a child run slot within a drive.

    Authority: docs/RFC-orch-drive.md section 14.3

    Lease ownership is ``drive_id + step_id + run_id``. A lease is created
    when a child run is admitted into a drive and released when the child
    run completes or when a planner mutation invalidates an unstarted lease.

    Persistence rule:
        ``DriveLease`` entries are persisted in the drive store's
        ``leases.jsonl`` and are authoritative for scheduler ownership.
        A lease with ``status="pending"`` that has no matching child run
        in the active set is considered invalidated and may be released.

    Attributes:
        drive_id: Owning drive identifier.
        step_id: Step this lease is for.
        run_id: Child run identifier this lease is bound to.
        status: Lease lifecycle status — ``active``, ``released``, or
            ``invalidated``.
        created_at: Unix timestamp when the lease was created.
        released_at: Unix timestamp when the lease was released (if any).
        released_reason: Why the lease was released (``completed``,
            ``invalidated``, ``superseded``).
    """

    drive_id: str
    step_id: str
    run_id: str
    status: Literal["active", "released", "invalidated"] = "active"
    created_at: float = 0.0
    released_at: float | None = None
    released_reason: Literal["completed", "invalidated", "superseded"] | None = None


@dataclass(frozen=True)
class DriveConfigFrozen:
    """Frozen drive creation parameters persisted on first drive creation.

    Authority: docs/RFC-orch-drive.md sections 8.1, 15.1, 15.2

    When a drive is first created, certain configuration parameters are
    frozen and persisted alongside the drive. Resume and recover must use
    these frozen parameters rather than re-reading ambient config, preventing
    config drift between the time the drive was created and when it is
    resumed or recovered.

    This ensures that a drive created with ``max_parallelism=2`` continues
    to enforce ``max_parallelism=2`` even if ambient config changes to
    ``max_parallelism=8`` between runs.

    Persistence rule:
        ``DriveConfigFrozen`` is persisted in the drive store as
        ``drive_config.json`` alongside the drive's JSONL indices. It is
        immutable once written — a drive's frozen config never changes.

    Attributes:
        drive_id: Drive identifier this config belongs to.
        max_parallelism: Maximum concurrent step child runs allowed.
            Frozen from config at drive creation time.
        control_idle_poll_interval_ms: Control idle poll interval in ms.
            Frozen from config at drive creation time.
        control_action_ack_timeout_seconds: Control action ack timeout in
            seconds. Frozen from config at drive creation time.
        resolver_invocation_timeout_seconds: Resolver invocation timeout in
            seconds. Frozen from config at drive creation time.
        resolver_max_tool_calls_per_invocation: Max resolver tool calls.
            Frozen from config at drive creation time.
        frozen_at: Unix timestamp when the config was frozen.
    """

    drive_id: str
    max_parallelism: int = 4
    control_idle_poll_interval_ms: int = 1000
    control_action_ack_timeout_seconds: float = 5.0
    resolver_invocation_timeout_seconds: float = 600.0
    resolver_max_tool_calls_per_invocation: int = 100
    frozen_at: float = 0.0


@dataclass(frozen=True)
class ControlDecision:
    """
    Next-step output from control for drive scheduling.

    Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3.4
    Authority: docs/RFC-orch-drive.md section 9.2.1

    The expanded drive-aware decision shape supersedes the prior single-step
    ``dispatch`` model. The old ``kind='dispatch'`` with ``step_id``/``role``
    is a transitional alias for a one-element ``dispatch_batch``.

    Migration rule:
        - existing implementation-local ``dispatch`` kind is a transitional alias
          for one-element ``dispatch_batch``
        - the target interface authority is the expanded form below

    Decision invariants:
        - ``dispatch_batch``: ``step_ids`` must be non-empty and ``role_bindings``
          must cover every ``step_id``
        - ``resolve``: ``case_ids`` must be non-empty
        - ``replan``: ``planner_request`` must be present
        - ``wait``: ``step_ids`` and ``case_ids`` must be empty
        - ``done``: no frontier and no active child runs remain
        - ``halt``: barrier is terminal and no new work may be admitted

    Attributes:
        kind: Decision discriminator.
        reason: Human-readable explanation.
        step_ids: Target steps for ``dispatch_batch`` (ordered by deterministic
            frontier ordering).
        role_bindings: Mapping from ``step_id`` to ``role_id`` for dispatch.
        case_ids: Resolution case identifiers for ``resolve``.
        planner_request: Planner mutation request for ``replan``.
        capacity_used: Number of step child runs consumed by this decision.
        capacity_remaining: Remaining step-run capacity after this decision.
        barrier_required: Whether this decision requires entering barrier mode.
    """

    kind: Literal[
        "dispatch_batch",
        "dispatch",
        "resolve",
        "replan",
        "wait",
        "done",
        "halt",
    ]
    reason: str
    step_ids: tuple[str, ...] = ()
    role_bindings: dict[str, str] = field(default_factory=dict)
    case_ids: tuple[str, ...] = ()
    planner_request: PlannerRequest | None = None
    capacity_used: int = 0
    capacity_remaining: int = 0
    barrier_required: bool = False

