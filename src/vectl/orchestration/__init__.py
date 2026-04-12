"""
Orchestration plane package.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 2
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3-5
"""

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    DispatchSpec,
    ExecutionRequest,
    ExecutionResult,
    IsolationMode,
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    PromptBundle,
    PromptRegistry,
    ReconcileResult,
    RecoveredVia,
    RecoveryAttempt,
    RecoveryContinuity,
    RequestMode,
    ResolutionCase,
    ResolutionCaseSource,
    ResolutionReport,
    RoleProfile,
    RoleProfileRegistry,
    RosterSnapshot,
    RunnerHandoffEnv,
    RuntimeSnapshot,
    SessionPolicy,
    StructuredReviewResult,
    WorkLease,
    WorktreeBinding,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    CoreStepDataAdapter,
    DispatchCoordinator,
    ReviewResultParseError,
    StepData,
    StepDataAdapter,
    UnknownRoleError,
    normalize_parse_failure,
    normalize_review_result,
    step_verify_to_verify_mode,
)
from vectl.orchestration.interfaces import (
    Control,
    Resolver,
    Roster,
    Runtime,
)
from vectl.orchestration.recovery_fallback import (
    RecoveryFallbackResult,
    SessionValidationResult,
    persist_recovery_attempt,
    persist_recovery_continuity,
    persist_recovery_fallback_result,
    read_recovery_attempt,
    read_recovery_continuity,
    recover_with_fallback,
    validate_session_for_resume,
)
from vectl.orchestration.resolution_reports import (
    parse_resolution_report_payload,
    validate_resolution_report_payload,
)

__all__ = [
    # Shared boundary types (contracts.py)
    "CoreSnapshot",
    "RosterSnapshot",
    "RuntimeSnapshot",
    "ControlDecision",
    "DispatchSpec",
    "IsolationMode",
    "WorkLease",
    "ExecutionRequest",
    "ExecutionResult",
    "RecoveryAttempt",
    "RecoveryContinuity",
    "RecoveredVia",
    "RequestMode",
    "SessionPolicy",
    "RoleProfile",
    "RoleProfileRegistry",
    "PromptArtifactPaths",
    "PromptBundle",
    "PromptRegistry",
    "RunnerHandoffEnv",
    "OpenCodeLaunchConfig",
    "StructuredReviewResult",
    "ReconcileResult",
    "ResolutionCase",
    "ResolutionCaseSource",
    "ResolutionReport",
    "WorktreeBinding",
    # Core authority bridge contract (core_adapter.py)
    "CoreAdapter",
    # Component interfaces (interfaces.py)
    "Control",
    "Roster",
    "Runtime",
    "Resolver",
    # Dispatch policy (dispatch_policy.py)
    "ConfigRoleProfileRegistry",
    "ConfigPromptRegistry",
    "DispatchCoordinator",
    "StepDataAdapter",
    "StepData",
    "UnknownRoleError",
    "ReviewResultParseError",
    "normalize_review_result",
    "normalize_parse_failure",
    "step_verify_to_verify_mode",
    "CoreStepDataAdapter",
    "parse_resolution_report_payload",
    "validate_resolution_report_payload",
    # Recovery fallback (recovery_fallback.py)
    "RecoveryFallbackResult",
    "SessionValidationResult",
    "persist_recovery_attempt",
    "persist_recovery_continuity",
    "persist_recovery_fallback_result",
    "read_recovery_attempt",
    "read_recovery_continuity",
    "recover_with_fallback",
    "validate_session_for_resume",
]
