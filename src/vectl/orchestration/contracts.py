"""Shared orchestration-plane boundary types.

These public imports preserve the historical ``vectl.orchestration.contracts``
compatibility surface while the contract definitions live in cohesive domain
modules.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 3
Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md
Authority: docs/ADR-worktree-support.md section "Core Design"
Authority: docs/DRIVER-ARCHITECTURE.md section 4 (completion authority)
Authority: docs/RFC-orch-drive.md sections 8, 9, 12, 13
"""

from vectl.models import IsolationMode
from vectl.orchestration.contract_dispatch import (
    DispatchSpec,
    PromptBundle,
    ResolverAuthorityContract,
    RoleProfile,
    StructuredReviewResult,
)
from vectl.orchestration.contract_drive import (
    ChildRunRef,
    ControlDecision,
    DriveBarrier,
    DriveConfigFrozen,
    DriveLease,
    DriveRecord,
    PlannerMutationBundle,
    PlannerMutationItem,
    PlannerRequest,
)
from vectl.orchestration.contract_execution import (
    ExecutionRequest,
    ExecutionResult,
    RecoveryAttempt,
    RecoveryContinuity,
)
from vectl.orchestration.contract_handoff import (
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    RunnerHandoffEnv,
    _OPENCODE_BOOTSTRAP_MESSAGE_RESUME,
    _OPENCODE_BOOTSTRAP_MESSAGE_START,
    _PROMPT_BUNDLE_FILENAME,
    _RUNNER_PROMPT_FILENAME,
    _RUNNER_PROMPT_WORKSPACE_RELATIVE,
    _RUNS_INPUT_DIR,
    _WORKSPACE_ORCH_DIR,
)
from vectl.orchestration.contract_literals import (
    BarrierReason,
    ChildRunKind,
    ChildRunStatus,
    DispatchRoleSource,
    DispatchSourceKind,
    DriveStatus,
    ExecutionContext,
    MutationPolicy,
    PlannerBundleStatus,
    PlannerMutationAction,
    ReconcileDisposition,
    RecoveredVia,
    RequestMode,
    ResolutionCaseSource,
    ResolverClaimFlow,
    ResolverExecutionSite,
    ResolverMutationSurface,
    ReviewOutcome,
    RoleOutputContract,
    SessionMode,
    SessionPolicy,
)
from vectl.orchestration.contract_resolution import ResolutionCase, ResolutionReport
from vectl.orchestration.contract_snapshots import (
    AgentExecutionState,
    CoreSnapshot,
    ReconcileResult,
    RosterSnapshot,
    RuntimeSnapshot,
    WorkLease,
    WorktreeBinding,
)

__all__ = [
    "AgentExecutionState",
    "BarrierReason",
    "ChildRunKind",
    "ChildRunRef",
    "ChildRunStatus",
    "ControlDecision",
    "DispatchRoleSource",
    "DispatchSourceKind",
    "DispatchSpec",
    "DriveBarrier",
    "DriveConfigFrozen",
    "DriveLease",
    "DriveRecord",
    "DriveStatus",
    "ExecutionContext",
    "ExecutionRequest",
    "ExecutionResult",
    "IsolationMode",
    "MutationPolicy",
    "OpenCodeLaunchConfig",
    "PlannerBundleStatus",
    "PlannerMutationAction",
    "PlannerMutationBundle",
    "PlannerMutationItem",
    "PlannerRequest",
    "PromptArtifactPaths",
    "PromptBundle",
    "ReconcileDisposition",
    "ReconcileResult",
    "RecoveryAttempt",
    "RecoveryContinuity",
    "RecoveredVia",
    "RequestMode",
    "ResolutionCase",
    "ResolutionCaseSource",
    "ResolverAuthorityContract",
    "ResolverClaimFlow",
    "ResolverExecutionSite",
    "ResolverMutationSurface",
    "ResolutionReport",
    "RunnerHandoffEnv",
    "SessionPolicy",
    "ReviewOutcome",
    "RoleOutputContract",
    "RoleProfile",
    "SessionMode",
    "StructuredReviewResult",
    "WorkLease",
    "WorktreeBinding",
]
