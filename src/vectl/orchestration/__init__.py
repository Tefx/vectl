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
    ResolutionCase,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
    WorkLease,
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
    "WorkLease",
    "ExecutionRequest",
    "ExecutionResult",
    "ResolutionCase",
    "ResolutionReport",
    # Component interfaces (interfaces.py)
    "Control",
    "Roster",
    "Runtime",
    "Resolver",
]
