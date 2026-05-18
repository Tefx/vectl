"""Prompt artifact contract tests for checklist receipts.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

import pytest

from vectl.core_checklist import ChecklistItem, OrchestratorChecklistReceipt, ChecklistReceiptItem
from vectl.orchestration.contracts import DispatchSpec
from vectl.orchestration.dispatch_policy import ConfigPromptRegistry
from vectl.orchestration.evidence import (
    ChecklistReceiptValidationError,
    parse_checklist_receipt,
)

def test_orchestrator_receipt_uses_item_id_and_revision() -> None:
    """Orchestrator receipt tests prove receipts use item_id/revision and no natural-language fuzzy mapping when orchestration surfaces are touched.

    This expected-red test shows that if orchestration surfaces are touched (i.e., we parse evidence into this object),
    it expects deterministic IDs.
    Since we only have the contract stub, we just verify the dataclass instantiation.
    """
    item = ChecklistReceiptItem(
        item_id="det-123",
        revision="rev-abc",
        checked=True
    )
    receipt = OrchestratorChecklistReceipt(
        step_id="step-1",
        items=[item]
    )

    assert receipt.step_id == "step-1"
    assert receipt.items[0].item_id == "det-123"
    assert receipt.items[0].revision == "rev-abc"
    assert receipt.items[0].checked is True

    # If the orchestrator parser was implemented, we would test that here.
    # We simulate the missing surface by asserting a function exists, which fails:

    parsed = parse_checklist_receipt('{"step_id": "step-1", "items": [{"field": "description", "item_id": "det-123", "revision": "rev-abc", "checked": true}]}')
    assert parsed is not None
    assert parsed.items[0].item_id == "det-123"


def test_prompt_injects_deterministic_checklist_inventory() -> None:
    spec = DispatchSpec(
        source_kind="step",
        source_id="step-1",
        role_id="python-executor",
        role_source="step.agent",
        execution_context="linked_worktree",
        runner="opencode",
        session_mode="fresh",
        step_id="step-1",
        description="Implement",
        verification="Verify",
        prompt_family="coder",
        output_contract="freeform_evidence",
        mutation_policy="worktree_changes",
        checklist_inventory_revision="rev-abc",
        checklist_inventory=(
            ChecklistItem(
                item_id="description:0:abc123",
                field="description",
                index=0,
                text="Prompt materialization can include checklist inventory",
                checked=False,
            ),
        ),
    )

    bundle = ConfigPromptRegistry().render(spec)

    assert "checklist_inventory_revision: rev-abc" in bundle.task_prompt
    assert "item_id: description:0:abc123" in bundle.task_prompt
    assert "field: description" in bundle.task_prompt
    assert "index: 0" in bundle.task_prompt
    assert "state: unchecked" in bundle.task_prompt
    assert "Do not perform natural-language fuzzy matching" in bundle.task_prompt


def test_parse_checklist_receipt_requires_item_id_and_revision() -> None:
    missing_revision = """
step_id: step-1
checklist_receipt:
  - step_id: step-1
    field: description
    item_id: description:0:abc123
    checked: true
"""
    with pytest.raises(ChecklistReceiptValidationError, match="checklist_inventory_revision"):
        parse_checklist_receipt(missing_revision)

    missing_item_id = """
step_id: step-1
checklist_receipt:
  - step_id: step-1
    field: description
    checklist_inventory_revision: rev-abc
    checked: true
"""
    with pytest.raises(ChecklistReceiptValidationError, match="item_id"):
        parse_checklist_receipt(missing_item_id)


def test_parse_checklist_receipt_records_field_revision_and_final_state() -> None:
    parsed = parse_checklist_receipt(
        """
step_id: step-1
checklist_receipt:
  - step_id: step-1
    field: verification
    item_id: verification:0:def456
    checklist_inventory_revision: rev-def
    checked: false
"""
    )

    assert parsed is not None
    assert parsed.step_id == "step-1"
    assert parsed.items[0].field == "verification"
    assert parsed.items[0].item_id == "verification:0:def456"
    assert parsed.items[0].revision == "rev-def"
    assert parsed.items[0].checked is False
