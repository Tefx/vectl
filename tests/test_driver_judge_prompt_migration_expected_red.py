"""Expected-RED tests for packaged judge prompt migration.

Source:
- step intent `driver-prompt-foundation.design-and-test`
- docs/DRIVER-ARCHITECTURE.md Section 2.10 (judge system prompt authority)
- docs/JUDGE-AGENT-PROMPT.md (semantic prompt payload authority)

These tests intentionally fail until downstream implementation steps land:
- driver-prompt-migration.package-resource-loader
- driver-prompt-migration.authority-reference-migration
"""

from __future__ import annotations

import inspect
from importlib.resources import files

from src.vectl.driver import judge


def test_prompt_authority_contract_exposes_downstream_owners_and_gaps() -> None:
    """Contract MUST pin owners and known migration gaps."""
    contract = judge.JUDGE_PROMPT_AUTHORITY_CONTRACT

    assert contract.source_step_id == "driver-prompt-foundation.design-and-test"
    assert contract.implementation_owner_step == "driver-prompt-migration.package-resource-loader"
    assert (
        contract.authority_migration_owner_step
        == "driver-prompt-migration.authority-reference-migration"
    )
    assert len(contract.exposed_gaps) >= 2


def test_packaged_prompt_resource_exists_at_declared_authority_path() -> None:
    """Declared package resource authority MUST resolve to a real file.

    Expected-red pre-implementation: resource does not exist yet.
    """
    contract = judge.JUDGE_PROMPT_AUTHORITY_CONTRACT

    resource_path = files(contract.package_name).joinpath(contract.package_resource)
    assert resource_path.is_file(), (
        "packaged judge prompt resource missing; "
        "expected migration owner: driver-prompt-migration.package-resource-loader"
    )


def test_runtime_loader_routes_through_packaged_loader_boundary() -> None:
    """Runtime loader MUST delegate to packaged prompt boundary.

    Expected-red pre-implementation: runtime still reads docs path directly.
    """
    source = inspect.getsource(judge._load_judge_system_prompt)

    assert "_load_packaged_judge_system_prompt" in source
    assert "docs/JUDGE-AGENT-PROMPT.md" not in source
