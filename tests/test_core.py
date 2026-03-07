"""Focused core tests for recovery behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from vectl.core import recover_from_backup, validate_plan
from vectl.io import load_plan_definition, save_plan
from vectl.models import (
    Phase,
    PhaseState,
    PhaseStatus,
    Plan,
    PlanError,
    PlanState,
    Step,
    StepState,
    StepStatus,
)


def _write_plan(path: Path, step_name: str) -> None:
    plan = Plan(
        project="recover-test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                steps=[Step(id="p1.s1", name=step_name)],
            )
        ],
    )
    save_plan(plan, path)


def test_recover_restores_from_backup(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    backup_path = tmp_path / "plan.yaml.bak"

    _write_plan(plan_path, "Current Name")
    _write_plan(backup_path, "Backup Name")

    result = recover_from_backup(plan_path, backup_path)

    restored_plan, _ = load_plan_definition(plan_path)
    restored = restored_plan.find_step("p1.s1")
    assert restored is not None
    assert restored[1].name == "Backup Name"
    assert result.restored is True
    assert len(result.diff.step_changes) == 1
    assert "Total changes:" in result.diff_summary


def test_recover_no_backup_error(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    missing_backup = tmp_path / "missing-plan.yaml.bak"
    _write_plan(plan_path, "Current Name")

    with pytest.raises(PlanError, match="Backup file not found"):
        recover_from_backup(plan_path, missing_backup)


# ---------------------------------------------------------------------------
# Orphan detection tests for validate_plan
# ---------------------------------------------------------------------------


def test_validate_detects_orphans() -> None:
    """Validate detects orphan state entries and reports them as warnings."""
    plan = Plan(
        project="orphan-test",
        phases=[
            Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")]),
        ],
    )
    state = PlanState(
        plan_id="orphan-test",
        phases={
            "core": PhaseState(status=PhaseStatus.PENDING),
            # This phase doesn't exist in plan definition
            "orphan_phase": PhaseState(status=PhaseStatus.DONE),
        },
        steps={
            "s1": StepState(status=StepStatus.PENDING),
            # This step doesn't exist in plan definition
            "orphan_step": StepState(status=StepStatus.CLAIMED, claimed_by="agent-x"),
        },
    )

    errors = validate_plan(plan, state=state)

    # Should have one warning with orphan detection info
    assert len(errors) == 1
    assert errors[0].is_warning is True
    assert "orphan" in errors[0].message.lower()
    assert "orphan_phase" in errors[0].message
    assert "orphan_step" in errors[0].message
    assert "vectl recover" in errors[0].message


def test_validate_clean_plan_no_orphan_warning() -> None:
    """Validate on clean plan (no orphans) produces no orphan warning."""
    plan = Plan(
        project="clean-test",
        phases=[
            Phase(
                id="core",
                name="Core",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2"),
                ],
            ),
            Phase(id="qa", name="QA", steps=[Step(id="s3", name="Step 3")]),
        ],
    )
    state = PlanState(
        plan_id="clean-test",
        phases={
            "core": PhaseState(status=PhaseStatus.PENDING),
            "qa": PhaseState(status=PhaseStatus.LOCKED),
        },
        steps={
            "s1": StepState(status=StepStatus.PENDING),
            "s2": StepState(status=StepStatus.CLAIMED, claimed_by="agent-a"),
            "s3": StepState(status=StepStatus.PENDING),
        },
    )

    errors = validate_plan(plan, state=state)

    # No orphan warnings should be present
    orphan_warnings = [e for e in errors if "orphan" in e.message.lower()]
    assert len(orphan_warnings) == 0


def test_validate_without_state_param() -> None:
    """Validate works when state parameter is not provided (None)."""
    plan = Plan(
        project="no-state-test",
        phases=[
            Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")]),
        ],
    )

    # Call without state parameter - should not raise
    errors = validate_plan(plan)

    assert len(errors) == 0
    assert errors == []
