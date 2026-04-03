"""
Thin adapter over vectl core for the orchestration plane.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
"""

from __future__ import annotations

from typing import Protocol

from vectl.models import IsolationMode
from vectl.orchestration.contracts import CoreSnapshot


class CoreAdapter(Protocol):
    """Authoritative bridge from orchestration-plane to vectl core.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
        docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 7

    The adapter is an orchestration-plane bridge over official core surfaces,
    not a shadow authority.
    """

    def snapshot(self, agent: str | None = None) -> CoreSnapshot:
        """Build orchestration-facing snapshot from authoritative core state."""
        ...

    def step_isolation(self, step_id: str) -> IsolationMode:
        """Read authoritative step isolation semantics from core state."""
        ...

    def claim_step(self, step_id: str, agent: str, force: bool = False) -> None:
        """Execute claim through official core claim surface."""
        ...

    def complete_step(self, step_id: str, evidence: str) -> None:
        """Execute completion through official core completion surface."""
        ...

    def defer_step(self, step_id: str) -> None:
        """Execute defer through official core defer surface."""
        ...
