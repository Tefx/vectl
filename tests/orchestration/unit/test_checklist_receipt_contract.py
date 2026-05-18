"""Prompt artifact contract tests for checklist receipts.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

import json
from dataclasses import asdict
import pytest

from vectl.core_checklist import OrchestratorChecklistReceipt, ChecklistReceiptItem

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

    from vectl.orchestration.evidence import parse_checklist_receipt
    parse_checklist_receipt('{"step_id": "step-1", "items": [{"item_id": "det-123", "revision": "rev-abc", "checked": true}]}')
