"""Tests for core.5: Checklist Operations."""

import pytest

from vectl.core import update_checklist
from vectl.models import (
    AmbiguousMatchError,
    NoMatchError,
    Phase,
    Plan,
    PlanError,
    Step,
)


def _plan_with_checklist(description: str) -> Plan:
    return Plan(
        project="test",
        phases=[
            Phase(
                id="p1",
                name="P1",
                steps=[Step(id="s1", name="S1", description=description)],
            )
        ],
    )


class TestCheckItem:
    def test_toggle_unchecked_to_checked(self):
        plan = _plan_with_checklist("Checklist:\n- [ ] Do validation\n- [ ] Write tests\n")
        plan = update_checklist(plan, "s1", check="validation")
        desc = plan.phases[0].steps[0].description
        assert "- [x] Do validation" in desc
        assert "- [ ] Write tests" in desc

    def test_toggle_checked_to_unchecked(self):
        plan = _plan_with_checklist("Items:\n- [x] Already done\n- [ ] Not yet\n")
        plan = update_checklist(plan, "s1", check="Already")
        desc = plan.phases[0].steps[0].description
        assert "- [ ] Already done" in desc

    def test_case_insensitive(self):
        plan = _plan_with_checklist("- [ ] UPPER case item\n")
        plan = update_checklist(plan, "s1", check="upper")
        desc = plan.phases[0].steps[0].description
        assert "- [x] UPPER case item" in desc

    def test_no_match_raises(self):
        plan = _plan_with_checklist("- [ ] Something\n")
        with pytest.raises(NoMatchError, match="nonexistent"):
            update_checklist(plan, "s1", check="nonexistent")

    def test_ambiguous_match_raises(self):
        plan = _plan_with_checklist("- [ ] Test alpha\n- [ ] Test beta\n")
        with pytest.raises(AmbiguousMatchError, match="Test"):
            update_checklist(plan, "s1", check="Test")

    def test_exact_enough_match(self):
        plan = _plan_with_checklist("- [ ] Test alpha\n- [ ] Test beta\n")
        plan = update_checklist(plan, "s1", check="alpha")
        desc = plan.phases[0].steps[0].description
        assert "- [x] Test alpha" in desc
        assert "- [ ] Test beta" in desc


class TestAppendItem:
    def test_append_new_item(self):
        plan = _plan_with_checklist("Checklist:\n- [ ] Existing\n")
        plan = update_checklist(plan, "s1", append="New item")
        desc = plan.phases[0].steps[0].description
        assert "- [ ] New item" in desc
        assert "- [ ] Existing" in desc

    def test_append_to_empty_description(self):
        plan = _plan_with_checklist("")
        plan = update_checklist(plan, "s1", append="First item")
        desc = plan.phases[0].steps[0].description
        assert "- [ ] First item" in desc


class TestCheckAndAppend:
    def test_both_check_and_append(self):
        plan = _plan_with_checklist("- [ ] Existing item\n")
        plan = update_checklist(plan, "s1", check="Existing", append="New item")
        desc = plan.phases[0].steps[0].description
        assert "- [x] Existing item" in desc
        assert "- [ ] New item" in desc


class TestEdgeCases:
    def test_no_check_no_append_raises(self):
        plan = _plan_with_checklist("- [ ] Item\n")
        with pytest.raises(PlanError, match="Must provide"):
            update_checklist(plan, "s1")

    def test_step_not_found(self):
        plan = _plan_with_checklist("- [ ] Item\n")
        with pytest.raises(PlanError, match="not found"):
            update_checklist(plan, "nonexistent", check="Item")

    def test_multiple_toggles(self):
        plan = _plan_with_checklist("- [ ] A\n- [ ] B\n- [ ] C\n")
        plan = update_checklist(plan, "s1", check="A")
        plan = update_checklist(plan, "s1", check="C")
        desc = plan.phases[0].steps[0].description
        assert "- [x] A" in desc
        assert "- [ ] B" in desc
        assert "- [x] C" in desc
