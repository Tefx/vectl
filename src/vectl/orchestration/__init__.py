"""
Orchestration plane package.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 2
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3-5
"""

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ExecutionRequest,
    ExecutionResult,
    IsolationMode,
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
    WorkLease,
)
from vectl.orchestration.core_adapter import CoreAdapter
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
    "IsolationMode",
    "WorkLease",
    "ExecutionRequest",
    "ExecutionResult",
    "ResolutionCase",
    "ResolutionReport",
    # Core authority bridge contract (core_adapter.py)
    "CoreAdapter",
    # Component interfaces (interfaces.py)
    "Control",
    "Roster",
    "Runtime",
    "Resolver",
]
