"""Leaf step-data model for orchestration dispatch construction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepData:
    """Authoritative step data used by the dispatch coordinator.

    Attributes:
        step_id: The step identifier.
        description: Step description from the plan.
        verification: Step verification criteria.
        refs: Step references.
        evidence_template: Step evidence template.
        verify: Step verification mode (expected_red/must_green/None).
        agent: Step agent assignment (may be None).
    """

    step_id: str
    description: str
    verification: str
    refs: tuple[str, ...]
    evidence_template: str
    verify: str | None
    agent: str | None
