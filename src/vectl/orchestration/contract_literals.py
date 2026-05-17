"""Literal and alias contracts for orchestration-plane boundaries.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
Authority: docs/DRIVER-ARCHITECTURE.md section 4
Authority: docs/RFC-orch-drive.md sections 8, 9, 12, 13
"""

from typing import Literal, TypeAlias

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

DriveStatus: TypeAlias = Literal[
    "running",
    "paused",
    "resolving",
    "replanning",
    "blocked_operator",
    "recovering",
    "completed",
    "halted",
    "failed_unrecoverable",
    "stopped",
]
"""Allowed drive-level lifecycle statuses.

Authority: docs/RFC-orch-drive.md section 8.2

Terminal drive statuses (no further transitions valid): ``completed``,
``halted``, ``failed_unrecoverable``, ``stopped``.
"""

ChildRunKind: TypeAlias = Literal["step", "resolver", "planner"]
"""Kind discriminator for child runs within a drive.

Authority: docs/RFC-orch-drive.md section 8.3
"""

ChildRunStatus: TypeAlias = Literal[
    "pending",
    "running",
    "success",
    "fail",
    "stall",
    "transport_error",
    "cancelled",
]
"""Allowed child-run lifecycle statuses.

Authority: docs/RFC-orch-drive.md section 8.3
"""

BarrierReason: TypeAlias = Literal[
    "runtime_failure",
    "merge_conflict",
    "review_failed",
    "planner_needed",
    "recovery_gate",
    "operator_pause",
]
"""Reason a drive enters barrier mode.

Authority: docs/RFC-orch-drive.md section 8.4.1
"""

PlannerMutationAction: TypeAlias = Literal[
    "add-step",
    "edit-step",
    "remove-step",
    "move-step",
    "add-phase",
    "edit-phase",
    "skip-step",
    "complete-phase",
]
"""Supported planner mutation actions, one-to-one with vectl facade surface.

Authority: docs/RFC-orch-drive.md section 8.5.1
"""

PlannerBundleStatus: TypeAlias = Literal[
    "applyable",
    "operator_required",
    "halt",
]
"""Allowed statuses for a PlannerMutationBundle.

Authority: docs/RFC-orch-drive.md section 13.5
"""


ReconcileDisposition: TypeAlias = Literal["merged", "noop"]
"""Allowed reconcile dispositions before orchestration completion.

Completion authority is pinned to the post-reconcile path only. A step may be
completed through orchestration contracts only after reconcile reached one of
these acceptance states.
"""
