from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_contract() -> dict[str, Any]:
    contract_path = Path("docs/contracts/duplicate-id-migration-contract.yaml")
    payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_contract_has_required_sections() -> None:
    contract = _load_contract()

    required_sections = {
        "metadata",
        "rename_algorithm",
        "depends_on_rewrite_rules",
        "dry_run_schema",
        "half_automatic_retry_contract",
        "compatibility_policy",
        "claimed_step_policy",
        "migration_evidence_schema",
        "idempotence_and_interruption",
    }
    assert required_sections.issubset(contract)


def test_contract_pins_claimed_step_and_alias_policy() -> None:
    contract = _load_contract()

    claimed_policy = contract["claimed_step_policy"]
    assert claimed_policy["mode"] == "block-with-guidance"

    compatibility = contract["compatibility_policy"]
    assert compatibility["old_id_aliases_supported"] is False


def test_dry_run_schema_includes_mapping_and_follow_up_fields() -> None:
    contract = _load_contract()

    required_fields = set(contract["dry_run_schema"]["required_fields"])
    assert "rename_map" in required_fields
    assert "depends_on_rewrites" in required_fields
    assert "claimed_step_conflicts" in required_fields
    assert "requires_manual_follow_up" in required_fields


def test_retry_contract_is_exactly_once() -> None:
    contract = _load_contract()

    retry_constraints = contract["half_automatic_retry_contract"]["retry_constraints"]
    assert retry_constraints["max_retries"] == 1
    assert retry_constraints["additional_auto_retry"] is False
