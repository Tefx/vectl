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
    PromptBundle,
    PromptRegistry,
    ResolutionCase,
    ResolutionReport,
    RoleProfile,
    RoleProfileRegistry,
    RosterSnapshot,
    RuntimeSnapshot,
    StructuredReviewResult,
    WorkLease,
)
from vectl.orchestration.core_adapter import CoreAdapter
from vectl.orchestration.dispatch_policy import (
    ConfigPromptRegistry,
    ConfigRoleProfileRegistry,
    CoreStepDataAdapter,
    DispatchCoordinator,
    ReviewResultParseError,
    StepDataAdapter,
    StepData,
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
    "RoleProfile",
    "RoleProfileRegistry",
    "PromptBundle",
    "PromptRegistry",
    "StructuredReviewResult",
    "ResolutionCase",
    "ResolutionReport",
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
]
