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
