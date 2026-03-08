"""Tests for core.3: DAG Validation."""


from vectl.core import validate_plan
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    Step,
    StepStatus,
)


def _make_plan(**kwargs) -> Plan:
    return Plan(project="test", **kwargs)


class TestPhaseIDUniqueness:
    def test_unique_ids_ok(self):
        plan = _make_plan(phases=[Phase(id="a", name="A"), Phase(id="b", name="B")])
        errors = validate_plan(plan)
        assert not errors

    def test_duplicate_phase_id(self):
        plan = _make_plan(phases=[Phase(id="a", name="A"), Phase(id="a", name="A2")])
        errors = validate_plan(plan)
        assert any("Duplicate phase ID" in e.message for e in errors)


class TestPhaseDAG:
    def test_valid_depends_on(self):
        plan = _make_plan(
            phases=[
                Phase(id="a", name="A"),
                Phase(id="b", name="B", depends_on=["a"]),
            ]
        )
        errors = validate_plan(plan)
        assert not errors

    def test_unknown_depends_on(self):
        plan = _make_plan(phases=[Phase(id="a", name="A", depends_on=["nonexistent"])])
        errors = validate_plan(plan)
        assert any("unknown phase 'nonexistent'" in e.message for e in errors)

    def test_cycle(self):
        plan = _make_plan(
            phases=[
                Phase(id="a", name="A", depends_on=["b"]),
                Phase(id="b", name="B", depends_on=["a"]),
            ]
        )
        errors = validate_plan(plan)
        assert any("cycle" in e.message.lower() for e in errors)

    def test_three_node_cycle(self):
        plan = _make_plan(
            phases=[
                Phase(id="a", name="A", depends_on=["c"]),
                Phase(id="b", name="B", depends_on=["a"]),
                Phase(id="c", name="C", depends_on=["b"]),
            ]
        )
        errors = validate_plan(plan)
        assert any("cycle" in e.message.lower() for e in errors)


class TestStepDAG:
    def test_valid_step_deps(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s2", name="S2", depends_on=["s1"]),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert not errors

    def test_unknown_step_dep(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", depends_on=["ghost"])],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any("unknown step 'ghost'" in e.message for e in errors)

    def test_step_cycle(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", depends_on=["s2"]),
                        Step(id="s2", name="S2", depends_on=["s1"]),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any("step dag cycle" in e.message.lower() for e in errors)

    def test_duplicate_step_id(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="S1 dup"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any("Duplicate step ID" in e.message for e in errors)


class TestStatusConsistency:
    def test_claimed_step_in_locked_phase(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.LOCKED,
                    steps=[Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="x")],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any("locked" in e.message.lower() for e in errors)

    def test_pending_step_in_locked_phase_ok(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.LOCKED,
                    steps=[Step(id="s1", name="S1", status=StepStatus.PENDING)],
                )
            ]
        )
        errors = validate_plan(plan)
        assert not errors

    def test_phase_done_but_step_pending(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.DONE,
                    steps=[Step(id="s1", name="S1", status=StepStatus.PENDING)],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any("done but step" in e.message.lower() for e in errors)

    def test_phase_done_all_steps_done_ok(self):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.DONE, evidence="ok"),
                        Step(
                            id="s2",
                            name="S2",
                            status=StepStatus.SKIPPED,
                            skipped_reason="na",
                        ),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert not errors


class TestRefsCheck:
    def test_refs_check_missing_file(self, tmp_path):
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", refs=["nonexistent.md"])],
                )
            ]
        )
        errors = validate_plan(plan, check_refs=True, base_path=tmp_path)
        assert any("file not found" in e.message.lower() for e in errors)
        assert errors[0].is_warning

    def test_refs_check_existing_file(self, tmp_path):
        (tmp_path / "exists.md").write_text("hi")
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", refs=["exists.md"])],
                )
            ]
        )
        errors = validate_plan(plan, check_refs=True, base_path=tmp_path)
        assert not errors

    def test_refs_with_fragment(self, tmp_path):
        (tmp_path / "doc.md").write_text("# Section")
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", refs=["doc.md#section"])],
                )
            ]
        )
        errors = validate_plan(plan, check_refs=True, base_path=tmp_path)
        assert not errors
