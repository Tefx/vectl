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

from vectl.core_checklist import (
    get_inventory as _get_inventory,
    mutate_checklist as _mutate_checklist,
    ItemIdSelector,
    FieldIndexSelector,
    LegacyKeywordSelector,
    MutationRequest,
    ChecklistInventoryRevision,
    ChecklistItem,
    StaleRevisionError,
    ItemNotFoundError,
    InvalidSelectorError,
)

# Unwrap to bypass contract stub errors in expected-red state
get_inventory = getattr(_get_inventory, "__wrapped__", _get_inventory)
mutate_checklist = getattr(_mutate_checklist, "__wrapped__", _mutate_checklist)

class TestDeterministicInventory:
    def test_inventory_discovers_items_from_both_fields(self):
        step = Step(
            id="s1", name="S1",
            description="Desc:\n- [ ] Desc item 1\n- [x] Desc item 2\n",
            verification="Verif:\n- [ ] Verif item 1\n"
        )
        rev, items = get_inventory(step)
        assert len(items) == 3
        assert items[0].field == "description"
        assert items[0].index == 0
        assert items[0].checked is False
        assert items[0].text.strip() == "Desc item 1"

        assert items[1].field == "description"
        assert items[1].index == 1
        assert items[1].checked is True
        assert items[1].text.strip() == "Desc item 2"

        assert items[2].field == "verification"
        assert items[2].index == 0
        assert items[2].checked is False
        assert items[2].text.strip() == "Verif item 1"

    def test_missing_and_empty_fields_produce_empty_inventories(self):
        step1 = Step(id="s1", name="S1")
        rev1, items1 = get_inventory(step1)
        assert len(items1) == 0

        step2 = Step(id="s2", name="S2", description="", verification="  \n ")
        rev2, items2 = get_inventory(step2)
        assert len(items2) == 0

    def test_item_id_values_are_snapshot_scoped(self):
        step = Step(id="s1", name="S1", description="- [ ] Item A\n")
        rev1, items1 = get_inventory(step)

        step_same = Step(id="s1", name="S1", description="- [ ] Item A\n")
        rev2, items2 = get_inventory(step_same)

        assert items1[0].item_id == items2[0].item_id
        assert rev1 == rev2

        step_diff = Step(id="s1", name="S1", description="- [ ] Item B\n")
        rev3, items3 = get_inventory(step_diff)
        assert items1[0].item_id != items3[0].item_id
        assert rev1 != rev3

class TestDeterministicMutation:
    def test_stale_revision_rejects_mutation(self):
        step = Step(id="s1", name="S1", description="- [ ] Item A\n")
        req = MutationRequest(selector=FieldIndexSelector("description", 0), checked=True)
        with pytest.raises(StaleRevisionError):
            mutate_checklist(step, "stale_rev_123", [req])

    def test_deterministic_single_mutation_is_idempotent(self):
        step = Step(id="s1", name="S1", description="- [x] Item A\n")
        rev = "rev1"
        req = MutationRequest(selector=FieldIndexSelector("description", 0), checked=True)
        res = mutate_checklist(step, rev, [req])
        assert res.step == step
        assert res.diagnostics.changed_items == 0
        assert res.diagnostics.matched_items == 1

    def test_legacy_keyword_toggle_compatibility(self):
        step = Step(id="s1", name="S1", description="- [ ] mY iTeM\n")
        rev = "rev1"
        req = MutationRequest(selector=LegacyKeywordSelector("my item"), checked=None)
        res = mutate_checklist(step, rev, [req])
        assert "- [x] mY iTeM" in res.step.description

    def test_invalid_selector_mode_combinations(self):
        step = Step(id="s1", name="S1", description="- [ ] Item\n")
        rev = "rev1"

        req1 = MutationRequest(selector=LegacyKeywordSelector("Item"), checked=True)
        with pytest.raises(InvalidSelectorError):
            mutate_checklist(step, rev, [req1])

        req2 = MutationRequest(selector=FieldIndexSelector("description", 0), checked=None)
        with pytest.raises(InvalidSelectorError):
            mutate_checklist(step, rev, [req2])

    def test_marker_only_preservation(self):
        desc = "Intro\n\n  - [ ]  Item A  \n\nOutro"
        step = Step(id="s1", name="S1", description=desc)
        rev = "rev1"
        req = MutationRequest(selector=FieldIndexSelector("description", 0), checked=True)

        res = mutate_checklist(step, rev, [req])

        expected_desc = "Intro\n\n  - [x]  Item A  \n\nOutro"
        assert res.step.description == expected_desc

    def test_atomic_batch_mutation_diagnostics(self):
        step = Step(id="s1", name="S1", description="- [ ] Item A\n- [ ] Item B\n")
        rev = "rev1"
        req1 = MutationRequest(selector=FieldIndexSelector("description", 0), checked=True)
        req2 = MutationRequest(selector=FieldIndexSelector("description", 1), checked=True)

        res = mutate_checklist(step, rev, [req1, req2])
        assert res.diagnostics.total_requested == 2
        assert res.diagnostics.matched_items == 2
        assert res.diagnostics.changed_items == 2
        assert "- [x] Item A" in res.step.description
        assert "- [x] Item B" in res.step.description
