"""Leaf step-data model for orchestration dispatch construction."""

from __future__ import annotations

from dataclasses import dataclass

from vectl.core_checklist import ChecklistInventoryRevision, ChecklistItem


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
        checklist_inventory_revision: Snapshot-scoped revision for deterministic
            checklist receipts, when the step has dispatchable checklist items.
        checklist_inventory: Deterministic checklist item inventory for worker
            prompt injection. Empty means no checklist receipt context is needed.
    """

    step_id: str
    description: str
    verification: str
    refs: tuple[str, ...]
    evidence_template: str
    verify: str | None
    agent: str | None
    checklist_inventory_revision: ChecklistInventoryRevision | None = None
    checklist_inventory: tuple[ChecklistItem, ...] = ()
