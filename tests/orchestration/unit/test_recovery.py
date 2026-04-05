from __future__ import annotations

from vectl.orchestration.recovery import CutoverValidator
from vectl.orchestration.run_store import RunRegistry


def _continuity(step_id: str) -> dict[str, object]:
    return {
        "ledger": {
            "step_id": step_id,
            "session_id": "sess-1",
            "runner": "python",
            "status": "active",
        },
        "journal": {
            "event_id": "evt-1",
            "step_id": step_id,
            "session_id": "sess-1",
            "runner": "python",
            "event_type": "resume_attempt",
        },
    }


def test_cutover_validator_passes_when_no_imported_runs_exist(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)

    result = CutoverValidator(registry=registry).validate_cutover_readiness()

    assert result.can_cutover is True
    assert result.blocking_items == ()
    assert len(result.criteria_results) == 4
    assert all(item.startswith("criterion.") for item in result.criteria_results)


def test_cutover_validator_blocks_non_retired_imports(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    registry.import_legacy_run(
        legacy_run_id="legacy-10",
        step_id="core.ready",
        plan_path="plan.yaml",
        status="running",
        migration_state="preferred",
        continuity_artifacts=_continuity("core.ready"),
    )

    result = CutoverValidator(registry=registry).validate_cutover_readiness()

    assert result.can_cutover is False
    assert any(
        "criterion.3_docs_no_longer_primary_legacy_reference: blocked" in item
        for item in result.criteria_results
    )
    assert any("migration_state=preferred" in item for item in result.blocking_items)
    assert any("status=running" in item for item in result.blocking_items)


def test_cutover_validator_blocks_open_recovery_cases(tmp_path) -> None:
    registry = RunRegistry(store_root=tmp_path)
    imported = registry.import_legacy_run(
        legacy_run_id="legacy-11",
        step_id="core.ready",
        plan_path="plan.yaml",
        status="running",
        migration_state="retired",
        continuity_artifacts={
            "ledger": {
                "step_id": "core.ready",
                "session_id": "sess-2",
                "runner": "python",
                "status": "active",
            }
        },
    )

    result = CutoverValidator(registry=registry).validate_cutover_readiness()

    assert result.can_cutover is False
    assert imported.legacy_run_id is not None
    assert any(imported.legacy_run_id in item for item in result.blocking_items)
    assert any("open migration/recovery case" in item for item in result.blocking_items)
