"""Tests for the plan.yaml merge driver."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import load_plan_definition, save_plan
from vectl.merge_driver import merge_plans
from vectl.models import Phase, Plan, Step, StepStatus

runner = CliRunner()


def _write_plan(path: Path, plan: Plan) -> None:
    save_plan(plan, path)


def test_merge_non_conflicting_step_changes(tmp_path: Path) -> None:
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2"),
                ],
            )
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE
    ours.phases[0].steps[0].evidence = "ours complete"

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[1].status = StepStatus.DONE
    theirs.phases[0].steps[1].evidence = "theirs complete"

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    merged_steps = {step.id: step for step in merged.phases[0].steps}
    assert merged_steps["s1"].status == StepStatus.DONE
    assert merged_steps["s1"].evidence == "ours complete"
    assert merged_steps["s2"].status == StepStatus.DONE
    assert merged_steps["s2"].evidence == "theirs complete"


def test_merge_same_step_conflict_returns_1_and_marks_ours(tmp_path: Path) -> None:
    base = Plan(
        project="merge-test",
        phases=[Phase(id="phase-1", name="Phase 1", steps=[Step(id="s1", name="Step 1")])],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE
    ours.phases[0].steps[0].evidence = "ours evidence"

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[0].status = StepStatus.DONE
    theirs.phases[0].steps[0].evidence = "theirs evidence"

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    result = runner.invoke(
        app,
        ["merge-driver", str(base_path), str(ours_path), str(theirs_path)],
    )
    assert result.exit_code == 1

    ours_content = ours_path.read_text(encoding="utf-8")
    assert "<<<<<<< ours" in ours_content
    assert "=======" in ours_content
    assert ">>>>>>> theirs" in ours_content


def test_merge_non_conflicting_phase_additions(tmp_path: Path) -> None:
    """Test that new phases added on different sides merge cleanly."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(id="phase-1", name="Phase 1", steps=[Step(id="s1", name="Step 1")]),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases.append(Phase(id="phase-2", name="Phase 2", steps=[Step(id="s2", name="Step 2")]))

    theirs = base.model_copy(deep=True)
    theirs.phases.append(Phase(id="phase-3", name="Phase 3", steps=[Step(id="s3", name="Step 3")]))

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    merged_phase_ids = [p.id for p in merged.phases]
    assert "phase-1" in merged_phase_ids
    assert "phase-2" in merged_phase_ids
    assert "phase-3" in merged_phase_ids


def test_merge_non_conflicting_step_additions(tmp_path: Path) -> None:
    """Test that new steps added in the same phase on different sides merge cleanly."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[Step(id="s1", name="Step 1")],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps.append(Step(id="s2", name="Step 2"))

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps.append(Step(id="s3", name="Step 3"))

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    merged_step_ids = [step.id for step in merged.phases[0].steps]
    assert "s1" in merged_step_ids
    assert "s2" in merged_step_ids
    assert "s3" in merged_step_ids


def test_merge_non_conflicting_property_changes(tmp_path: Path) -> None:
    """Test that different properties changed on the same step merge cleanly."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[Step(id="s1", name="Step 1", description="")],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE
    ours.phases[0].steps[0].evidence = "our evidence"

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[0].description = "Added description"
    theirs.phases[0].steps[0].refs = ["file.py"]

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    # Full step payload is compared - different properties = conflict
    assert exit_code == 1

    ours_content = ours_path.read_text(encoding="utf-8")
    assert "<<<<<<< ours" in ours_content


def test_merge_step_status_and_different_property_conflict(tmp_path: Path) -> None:
    """Test conflict when same step status changed AND different property changed."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[Step(id="s1", name="Step 1", description="")],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[0].description = "Changed description"

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    # Since the full step payload is compared, different properties = conflict
    assert exit_code == 1

    ours_content = ours_path.read_text(encoding="utf-8")
    assert "<<<<<<< ours" in ours_content


def test_merge_phase_conflict(tmp_path: Path) -> None:
    """Test conflict when same phase is modified on both sides."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[Step(id="s1", name="Step 1")],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].name = "Phase 1 - Modified"

    theirs = base.model_copy(deep=True)
    theirs.phases[0].name = "Phase 1 - Changed"

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 1

    ours_content = ours_path.read_text(encoding="utf-8")
    assert "<<<<<<< ours" in ours_content


def test_merge_step_deleted_in_theirs(tmp_path: Path) -> None:
    """Test merge when step is deleted in theirs but modified in ours - our changes preserved."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2"),
                ],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE  # ours modified s1

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps = [Step(id="s1", name="Step 1")]  # s2 deleted in theirs

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    # Ours modified s1, theirs deleted s2 - both should merge cleanly
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    merged_step_ids = [step.id for step in merged.phases[0].steps]
    # s1 was modified in ours (should be kept)
    # s2 was deleted in theirs and unchanged in ours (theirs deletion wins)
    assert "s1" in merged_step_ids
    assert merged.phases[0].steps[0].status == StepStatus.DONE
    # s2 is deleted because ours didn't modify it (base==ours, so theirs wins)


def test_merge_step_deleted_in_ours(tmp_path: Path) -> None:
    """Test merge when step is deleted in ours but modified in theirs - their changes preserved."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2"),
                ],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps = [Step(id="s2", name="Step 2")]  # s1 deleted in ours

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[1].status = StepStatus.DONE  # theirs modified s2

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    merged_step_ids = [step.id for step in merged.phases[0].steps]
    # s1 deleted in ours, unchanged in theirs - ours deletion wins
    # s2 modified in theirs - should be kept with DONE status
    assert "s2" in merged_step_ids
    assert merged.phases[0].steps[0].status == StepStatus.DONE


def test_merge_step_moved_between_phases(tmp_path: Path) -> None:
    """Test step moved from one phase to another - ours wins when theirs unchanged."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(id="phase-1", name="Phase 1", steps=[Step(id="s1", name="Step 1")]),
            Phase(id="phase-2", name="Phase 2", steps=[]),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps = []
    ours.phases[1].steps = [Step(id="s1", name="Step 1")]

    theirs = base.model_copy(deep=True)
    # theirs unchanged - step still in phase-1

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    # ours changed (moved to phase-2), theirs == base, so ours wins
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    # Step should be in phase-2 (ours won)
    assert merged.phases[1].steps[0].id == "s1"


def test_merge_identical_changes_no_conflict(tmp_path: Path) -> None:
    """Test that identical changes on both sides don't cause conflict."""
    base = Plan(
        project="merge-test",
        phases=[
            Phase(
                id="phase-1",
                name="Phase 1",
                steps=[Step(id="s1", name="Step 1")],
            ),
        ],
    )

    ours = base.model_copy(deep=True)
    ours.phases[0].steps[0].status = StepStatus.DONE

    theirs = base.model_copy(deep=True)
    theirs.phases[0].steps[0].status = StepStatus.DONE

    base_path = tmp_path / "base.yaml"
    ours_path = tmp_path / "ours.yaml"
    theirs_path = tmp_path / "theirs.yaml"
    _write_plan(base_path, base)
    _write_plan(ours_path, ours)
    _write_plan(theirs_path, theirs)

    exit_code = merge_plans(str(base_path), str(ours_path), str(theirs_path))
    assert exit_code == 0

    merged, _ = load_plan_definition(ours_path)
    assert merged.phases[0].steps[0].status == StepStatus.DONE
