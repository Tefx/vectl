"""Tests for diff_plans (ho.2: plan change detection)."""

import pytest

from vectl.core import diff_plans
from vectl.models import (
    DiffResult,
    Phase,
    PhaseChange,
    PhaseStatus,
    Plan,
    Step,
    StepChange,
    StepStatus,
)


def _base_plan() -> Plan:
    return Plan(
        project="test",
        phases=[
            Phase(
                id="p1",
                name="Phase One",
                status=PhaseStatus.IN_PROGRESS,
                steps=[
                    Step(id="s1", name="Step A", status=StepStatus.DONE),
                    Step(id="s2", name="Step B", status=StepStatus.PENDING),
                ],
            ),
            Phase(
                id="p2",
                name="Phase Two",
                status=PhaseStatus.LOCKED,
                steps=[
                    Step(id="s3", name="Step C", status=StepStatus.PENDING),
                ],
            ),
        ],
    )


class TestDiffPlans:
    """Core diff_plans function tests."""

    def test_no_changes(self) -> None:
        p = _base_plan()
        diff = diff_plans(p, p)
        assert not diff.has_changes
        assert diff.phase_changes == []
        assert diff.step_changes == []

    def test_step_status_change(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps[1].status = StepStatus.DONE
        diff = diff_plans(old, new)
        assert diff.has_changes
        sc = [c for c in diff.step_changes if c.step_id == "s2"]
        assert len(sc) == 1
        assert sc[0].kind == "status_changed"
        assert sc[0].old_status == StepStatus.PENDING
        assert sc[0].new_status == StepStatus.DONE

    def test_phase_status_change(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].status = PhaseStatus.DONE
        diff = diff_plans(old, new)
        pc = [c for c in diff.phase_changes if c.phase_id == "p1"]
        assert len(pc) == 1
        assert pc[0].kind == "status_changed"
        assert pc[0].old_status == PhaseStatus.IN_PROGRESS
        assert pc[0].new_status == PhaseStatus.DONE

    def test_phase_added(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases.append(Phase(id="p3", name="Phase Three", status=PhaseStatus.PENDING))
        diff = diff_plans(old, new)
        pc = [c for c in diff.phase_changes if c.phase_id == "p3"]
        assert len(pc) == 1
        assert pc[0].kind == "added"
        assert pc[0].phase_name == "Phase Three"

    def test_phase_removed(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases = [p for p in new.phases if p.id != "p2"]
        diff = diff_plans(old, new)
        pc = [c for c in diff.phase_changes if c.phase_id == "p2"]
        assert len(pc) == 1
        assert pc[0].kind == "removed"

    def test_step_added(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps.append(Step(id="s4", name="Step D", status=StepStatus.PENDING))
        diff = diff_plans(old, new)
        sc = [c for c in diff.step_changes if c.step_id == "s4"]
        assert len(sc) == 1
        assert sc[0].kind == "added"
        assert sc[0].phase_id == "p1"

    def test_step_removed(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps = [s for s in new.phases[0].steps if s.id != "s1"]
        diff = diff_plans(old, new)
        sc = [c for c in diff.step_changes if c.step_id == "s1"]
        assert len(sc) == 1
        assert sc[0].kind == "removed"

    def test_step_name_modified(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps[0].name = "Renamed Step"
        diff = diff_plans(old, new)
        sc = [c for c in diff.step_changes if c.step_id == "s1"]
        assert len(sc) == 1
        assert sc[0].kind == "modified"
        assert "name:" in sc[0].detail

    def test_step_description_modified(self) -> None:
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps[0].description = "New description"
        diff = diff_plans(old, new)
        sc = [c for c in diff.step_changes if c.step_id == "s1"]
        assert len(sc) == 1
        assert sc[0].kind == "modified"
        assert "description changed" in sc[0].detail

    def test_empty_to_populated(self) -> None:
        old = Plan(project="test")
        new = _base_plan()
        diff = diff_plans(old, new)
        assert len(diff.phase_changes) == 2
        assert all(pc.kind == "added" for pc in diff.phase_changes)
        assert len(diff.step_changes) == 3
        assert all(sc.kind == "added" for sc in diff.step_changes)

    def test_populated_to_empty(self) -> None:
        old = _base_plan()
        new = Plan(project="test")
        diff = diff_plans(old, new)
        assert len(diff.phase_changes) == 2
        assert all(pc.kind == "removed" for pc in diff.phase_changes)
        assert len(diff.step_changes) == 3
        assert all(sc.kind == "removed" for sc in diff.step_changes)

    def test_has_changes_property(self) -> None:
        assert not DiffResult(phase_changes=[], step_changes=[]).has_changes
        assert DiffResult(
            phase_changes=[PhaseChange(phase_id="x", phase_name="X", kind="added")],
            step_changes=[],
        ).has_changes
        assert DiffResult(
            phase_changes=[],
            step_changes=[StepChange(step_id="y", step_name="Y", phase_id="p", kind="added")],
        ).has_changes

    def test_status_change_takes_precedence_over_modification(self) -> None:
        """When both status and name change, report as status_changed."""
        old = _base_plan()
        new = _base_plan()
        new.phases[0].steps[1].status = StepStatus.DONE
        new.phases[0].steps[1].name = "Renamed"
        diff = diff_plans(old, new)
        sc = [c for c in diff.step_changes if c.step_id == "s2"]
        assert len(sc) == 1
        assert sc[0].kind == "status_changed"
