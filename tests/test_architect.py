"""Tests for architect functions — plan mutation."""

import pytest

from vectl.core import (
    _slugify,
    _unique_phase_id,
    _unique_step_id,
    add_phase,
    add_step,
    add_steps_bulk,
    edit_phase,
    edit_step,
    move_step,
    remove_step,
    unlock_phase,
)
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Show Command") == "show-command"

    def test_special_chars(self) -> None:
        assert _slugify("Add Step & Add Steps (Bulk)") == "add-step-add-steps-bulk"

    def test_whitespace(self) -> None:
        assert _slugify("  hello  world  ") == "hello-world"

    def test_mixed_case(self) -> None:
        assert _slugify("YAML IO with CAS") == "yaml-io-with-cas"

    def test_already_slug(self) -> None:
        assert _slugify("show-command") == "show-command"

    def test_numbers(self) -> None:
        assert _slugify("Phase 3 Setup") == "phase-3-setup"

    def test_empty_string(self) -> None:
        assert _slugify("") == ""

    def test_only_special_chars(self) -> None:
        assert _slugify("---!!!---") == ""


# ---------------------------------------------------------------------------
# _unique_step_id / _unique_phase_id
# ---------------------------------------------------------------------------


class TestUniqueIds:
    def test_step_id_no_collision(self) -> None:
        phase = Phase(id="core", name="Core")
        assert _unique_step_id(phase, "show-command") == "core.show-command"

    def test_step_id_collision(self) -> None:
        phase = Phase(
            id="core",
            name="Core",
            steps=[Step(id="core.show-command", name="Show Command")],
        )
        assert _unique_step_id(phase, "show-command") == "core.show-command-2"

    def test_step_id_multiple_collisions(self) -> None:
        phase = Phase(
            id="core",
            name="Core",
            steps=[
                Step(id="core.foo", name="Foo"),
                Step(id="core.foo-2", name="Foo 2"),
                Step(id="core.foo-3", name="Foo 3"),
            ],
        )
        assert _unique_step_id(phase, "foo") == "core.foo-4"

    def test_phase_id_no_collision(self) -> None:
        plan = Plan(project="test")
        assert _unique_phase_id(plan, "cli-ux") == "cli-ux"

    def test_phase_id_collision(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="cli-ux", name="CLI UX")],
        )
        assert _unique_phase_id(plan, "cli-ux") == "cli-ux-2"


# ---------------------------------------------------------------------------
# add_step
# ---------------------------------------------------------------------------


def _plan_with_phase(
    phase_id: str = "p1",
    phase_status: PhaseStatus = PhaseStatus.PENDING,
    steps: list[Step] | None = None,
    extra_phases: list[Phase] | None = None,
) -> Plan:
    """Helper to create a plan with one phase."""
    phases = [
        Phase(
            id=phase_id,
            name="Phase 1",
            status=phase_status,
            steps=steps or [],
        )
    ]
    if extra_phases:
        phases.extend(extra_phases)
    return Plan(project="test", phases=phases)


class TestAddStep:
    def test_basic_auto_id(self) -> None:
        plan = _plan_with_phase()
        plan, step_id = add_step(plan, "p1", "Show Command")
        assert step_id == "p1.show-command"
        assert len(plan.phases[0].steps) == 1
        assert plan.phases[0].steps[0].id == "p1.show-command"
        assert plan.phases[0].steps[0].name == "Show Command"
        assert plan.phases[0].steps[0].status == StepStatus.PENDING

    def test_explicit_id(self) -> None:
        plan = _plan_with_phase()
        plan, step_id = add_step(plan, "p1", "Show Command", step_id="custom.id")
        assert step_id == "custom.id"
        assert plan.phases[0].steps[0].id == "custom.id"

    def test_with_all_fields(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="Step 1")])
        plan, step_id = add_step(
            plan,
            "p1",
            "Show Command",
            description="Show the step spec",
            depends_on=["s1"],
            verification="uv run pytest -v",
            refs=["tools/vectl/README.md"],
        )
        step = plan.phases[0].steps[1]
        assert step.description == "Show the step spec"
        assert step.depends_on == ["s1"]
        assert step.verification == "uv run pytest -v"
        assert step.refs == ["tools/vectl/README.md"]

    def test_auto_id_collision(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="p1.show-command", name="Show Command")])
        plan, step_id = add_step(plan, "p1", "Show Command")
        assert step_id == "p1.show-command-2"

    def test_phase_not_found(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            add_step(plan, "nonexistent", "Foo")

    def test_locked_phase_allows_adding(self) -> None:
        """Locked phases allow adding steps (for planning), just not claiming."""
        plan = _plan_with_phase(phase_status=PhaseStatus.LOCKED)
        plan, step_id = add_step(plan, "p1", "Foo")
        assert step_id == "p1.foo"
        assert plan.phases[0].steps[0].status == StepStatus.PENDING

    def test_duplicate_explicit_id(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="Step 1")])
        with pytest.raises(PlanError, match="already exists"):
            add_step(plan, "p1", "Foo", step_id="s1")

    def test_invalid_depends_on(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="depends_on"):
            add_step(plan, "p1", "Foo", depends_on=["nonexistent"])

    def test_done_phase_reopens(self) -> None:
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Done Step", status=StepStatus.DONE, evidence="done")],
        )
        plan, _ = add_step(plan, "p1", "New Step")
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS

    def test_in_progress_phase_ok(self) -> None:
        plan = _plan_with_phase(phase_status=PhaseStatus.IN_PROGRESS)
        plan, step_id = add_step(plan, "p1", "New Step")
        assert step_id == "p1.new-step"
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS

    def test_pending_phase_stays_pending(self) -> None:
        plan = _plan_with_phase(phase_status=PhaseStatus.PENDING)
        plan, _ = add_step(plan, "p1", "New Step")
        assert plan.phases[0].status == PhaseStatus.PENDING


# ---------------------------------------------------------------------------
# add_step — import status support
# ---------------------------------------------------------------------------


class TestAddStepImportStatus:
    """Tests for add_step() with status/evidence/skipped_reason kwargs."""

    def test_done_with_evidence(self) -> None:
        plan = _plan_with_phase()
        plan, sid = add_step(
            plan,
            "p1",
            "Historical Step",
            status=StepStatus.DONE,
            evidence="commit abc123",
        )
        step = plan.phases[0].steps[0]
        assert step.id == sid
        assert step.status == StepStatus.DONE
        assert step.evidence == "commit abc123"

    def test_done_without_evidence_raises(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="without evidence"):
            add_step(plan, "p1", "Bad", status=StepStatus.DONE)

    def test_skipped_with_reason(self) -> None:
        plan = _plan_with_phase()
        plan, sid = add_step(
            plan,
            "p1",
            "Skipped Step",
            status=StepStatus.SKIPPED,
            skipped_reason="absorbed",
        )
        step = plan.phases[0].steps[0]
        assert step.status == StepStatus.SKIPPED
        assert step.skipped_reason == "absorbed"

    def test_skipped_without_reason_raises(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="without skipped_reason"):
            add_step(plan, "p1", "Bad", status=StepStatus.SKIPPED)

    def test_claimed_rejected_as_transient(self) -> None:
        """Transient states (claimed, rejected) are not allowed on import."""
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="claimed/rejected require runtime"):
            add_step(plan, "p1", "Bad", status=StepStatus.CLAIMED)

        plan2 = _plan_with_phase()
        with pytest.raises(PlanError, match="claimed/rejected require runtime"):
            add_step(plan2, "p1", "Bad", status=StepStatus.REJECTED)

    def test_pending_explicit_is_default(self) -> None:
        """Explicit status=PENDING behaves identically to omitting status."""
        plan = _plan_with_phase()
        plan, sid = add_step(plan, "p1", "Normal Step", status=StepStatus.PENDING)
        step = plan.phases[0].steps[0]
        assert step.status == StepStatus.PENDING
        assert step.evidence is None

    def test_done_step_does_not_reopen_done_phase(self) -> None:
        """Adding done/skipped steps to a DONE phase does NOT reopen it."""
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Existing", status=StepStatus.DONE, evidence="ok")],
        )
        plan, _ = add_step(
            plan,
            "p1",
            "Imported Done",
            status=StepStatus.DONE,
            evidence="migrated from X",
        )
        assert plan.phases[0].status == PhaseStatus.DONE  # stays DONE

    def test_skipped_step_does_not_reopen_done_phase(self) -> None:
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Existing", status=StepStatus.DONE, evidence="ok")],
        )
        plan, _ = add_step(
            plan,
            "p1",
            "Imported Skip",
            status=StepStatus.SKIPPED,
            skipped_reason="irrelevant",
        )
        assert plan.phases[0].status == PhaseStatus.DONE  # stays DONE

    def test_pending_step_reopens_done_phase(self) -> None:
        """Adding a pending step to a DONE phase reopens it."""
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Existing", status=StepStatus.DONE, evidence="ok")],
        )
        plan, _ = add_step(plan, "p1", "New Pending Work")
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# add_phase
# ---------------------------------------------------------------------------


class TestAddPhase:
    def test_basic_auto_id(self) -> None:
        plan = Plan(project="test")
        plan, phase_id = add_phase(plan, "CLI UX Enhancements")
        assert phase_id == "cli-ux-enhancements"
        assert len(plan.phases) == 1
        assert plan.phases[0].id == "cli-ux-enhancements"
        assert plan.phases[0].name == "CLI UX Enhancements"
        assert plan.phases[0].status == PhaseStatus.PENDING

    def test_explicit_id(self) -> None:
        plan = Plan(project="test")
        plan, phase_id = add_phase(plan, "CLI UX", phase_id="cli_ux")
        assert phase_id == "cli_ux"
        assert plan.phases[0].id == "cli_ux"

    def test_with_all_fields(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="core", name="Core", status=PhaseStatus.DONE)],
        )
        plan, phase_id = add_phase(
            plan,
            "CLI UX",
            depends_on=["core"],
            gate="All UX tests pass",
            context="Improve CLI for agents",
        )
        phase = plan.phases[1]
        assert phase.gate == "All UX tests pass"
        assert phase.context == "Improve CLI for agents"
        assert phase.depends_on == ["core"]

    def test_auto_id_collision(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="cli-ux", name="CLI UX")],
        )
        plan, phase_id = add_phase(plan, "CLI UX")
        assert phase_id == "cli-ux-2"

    def test_duplicate_explicit_id(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="core", name="Core")],
        )
        with pytest.raises(PlanError, match="already exists"):
            add_phase(plan, "Core 2", phase_id="core")

    def test_invalid_depends_on(self) -> None:
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="depends_on.*not found"):
            add_phase(plan, "CLI UX", depends_on=["nonexistent"])

    def test_status_pending_no_deps(self) -> None:
        plan = Plan(project="test")
        plan, _ = add_phase(plan, "Core")
        assert plan.phases[0].status == PhaseStatus.PENDING

    def test_status_pending_when_deps_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="core", name="Core", status=PhaseStatus.DONE)],
        )
        plan, _ = add_phase(plan, "CLI UX", depends_on=["core"])
        assert plan.phases[1].status == PhaseStatus.PENDING

    def test_status_locked_when_deps_not_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="core", name="Core", status=PhaseStatus.IN_PROGRESS)],
        )
        plan, _ = add_phase(plan, "CLI UX", depends_on=["core"])
        assert plan.phases[1].status == PhaseStatus.LOCKED

    def test_multiple_deps_all_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="a", name="A", status=PhaseStatus.DONE),
                Phase(id="b", name="B", status=PhaseStatus.DONE),
            ],
        )
        plan, _ = add_phase(plan, "C", depends_on=["a", "b"])
        assert plan.phases[2].status == PhaseStatus.PENDING

    def test_multiple_deps_one_not_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="a", name="A", status=PhaseStatus.DONE),
                Phase(id="b", name="B", status=PhaseStatus.IN_PROGRESS),
            ],
        )
        plan, _ = add_phase(plan, "C", depends_on=["a", "b"])
        assert plan.phases[2].status == PhaseStatus.LOCKED


# ---------------------------------------------------------------------------
# edit_step
# ---------------------------------------------------------------------------


class TestEditStep:
    def test_edit_name(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="Old Name")])
        plan = edit_step(plan, "s1", name="New Name")
        _, step = plan.find_step("s1")
        assert step.name == "New Name"

    def test_edit_description(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S")])
        plan = edit_step(plan, "s1", description="Updated desc")
        _, step = plan.find_step("s1")
        assert step.description == "Updated desc"

    def test_edit_verification(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S")])
        plan = edit_step(plan, "s1", verification="pytest -v")
        _, step = plan.find_step("s1")
        assert step.verification == "pytest -v"

    def test_add_dep(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1"),
                Step(id="s2", name="S2"),
            ]
        )
        plan = edit_step(plan, "s2", add_deps=["s1"])
        _, step = plan.find_step("s2")
        assert step.depends_on == ["s1"]

    def test_add_dep_invalid(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S")])
        with pytest.raises(PlanError, match="not found"):
            edit_step(plan, "s1", add_deps=["nonexistent"])

    def test_add_dep_idempotent(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1"),
                Step(id="s2", name="S2", depends_on=["s1"]),
            ]
        )
        plan = edit_step(plan, "s2", add_deps=["s1"])
        _, step = plan.find_step("s2")
        assert step.depends_on == ["s1"]  # no duplicates

    def test_remove_dep(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1"),
                Step(id="s2", name="S2", depends_on=["s1"]),
            ]
        )
        plan = edit_step(plan, "s2", remove_deps=["s1"])
        _, step = plan.find_step("s2")
        assert step.depends_on == []

    def test_remove_dep_nonexistent_is_noop(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S")])
        plan = edit_step(plan, "s1", remove_deps=["nonexistent"])
        _, step = plan.find_step("s1")
        assert step.depends_on == []

    def test_step_not_found(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            edit_step(plan, "nonexistent", name="X")

    def test_multiple_edits(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="Old", description="old")])
        plan = edit_step(plan, "s1", name="New", description="new", verification="check")
        _, step = plan.find_step("s1")
        assert step.name == "New"
        assert step.description == "new"
        assert step.verification == "check"


# ---------------------------------------------------------------------------
# remove_step
# ---------------------------------------------------------------------------


class TestRemoveStep:
    def test_remove_pending(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S1")])
        plan = remove_step(plan, "s1")
        assert len(plan.phases[0].steps) == 0

    def test_remove_claimed_fails(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="bot"),
            ]
        )
        with pytest.raises(PlanError, match="must be pending"):
            remove_step(plan, "s1")

    def test_remove_not_found(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            remove_step(plan, "nonexistent")

    def test_remove_with_dependents_fails(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1"),
                Step(id="s2", name="S2", depends_on=["s1"]),
            ]
        )
        with pytest.raises(PlanError, match="depends on it"):
            remove_step(plan, "s1")

    def test_remove_force_cleans_deps(self) -> None:
        plan = _plan_with_phase(
            steps=[
                Step(id="s1", name="S1"),
                Step(id="s2", name="S2", depends_on=["s1"]),
                Step(id="s3", name="S3", depends_on=["s1"]),
            ]
        )
        plan = remove_step(plan, "s1", force=True)
        assert len(plan.phases[0].steps) == 2
        assert "s1" not in plan.phases[0].steps[0].depends_on
        assert "s1" not in plan.phases[0].steps[1].depends_on


# ---------------------------------------------------------------------------
# move_step
# ---------------------------------------------------------------------------


class TestMoveStep:
    def test_move_basic(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")]),
                Phase(id="p2", name="P2"),
            ],
        )
        plan = move_step(plan, "s1", "p2")
        assert len(plan.phases[0].steps) == 0
        assert len(plan.phases[1].steps) == 1
        assert plan.phases[1].steps[0].id == "s1"

    def test_move_clears_deps(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s0", name="S0"),
                        Step(id="s1", name="S1", depends_on=["s0"]),
                    ],
                ),
                Phase(id="p2", name="P2"),
            ],
        )
        plan = move_step(plan, "s1", "p2")
        assert plan.phases[1].steps[0].depends_on == []

    def test_move_claimed_fails(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="bot"),
                    ],
                ),
                Phase(id="p2", name="P2"),
            ],
        )
        with pytest.raises(PlanError, match="must be pending"):
            move_step(plan, "s1", "p2")

    def test_move_same_phase_fails(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S1")])
        with pytest.raises(PlanError, match="already in phase"):
            move_step(plan, "s1", "p1")

    def test_move_target_not_found(self) -> None:
        plan = _plan_with_phase(steps=[Step(id="s1", name="S1")])
        with pytest.raises(PlanError, match="not found"):
            move_step(plan, "s1", "nonexistent")

    def test_move_with_dependents_fails(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s2", name="S2", depends_on=["s1"]),
                    ],
                ),
                Phase(id="p2", name="P2"),
            ],
        )
        with pytest.raises(PlanError, match="depends on it"):
            move_step(plan, "s1", "p2")


# ---------------------------------------------------------------------------
# unlock_phase
# ---------------------------------------------------------------------------


class TestUnlockPhase:
    def test_unlock_locked_deps_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(id="p2", name="P2", status=PhaseStatus.LOCKED, depends_on=["p1"]),
            ],
        )
        plan = unlock_phase(plan, "p2")
        assert plan.phases[1].status == PhaseStatus.PENDING

    def test_unlock_deps_not_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.IN_PROGRESS),
                Phase(id="p2", name="P2", status=PhaseStatus.LOCKED, depends_on=["p1"]),
            ],
        )
        with pytest.raises(PlanError, match="dependencies not done"):
            unlock_phase(plan, "p2")

    def test_unlock_not_locked_fails(self) -> None:
        plan = Plan(
            project="test",
            phases=[Phase(id="p1", name="P1", status=PhaseStatus.PENDING)],
        )
        with pytest.raises(PlanError, match="not locked"):
            unlock_phase(plan, "p1")

    def test_unlock_not_found(self) -> None:
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="not found"):
            unlock_phase(plan, "nonexistent")

    def test_unlock_multiple_deps_all_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="a", name="A", status=PhaseStatus.DONE),
                Phase(id="b", name="B", status=PhaseStatus.DONE),
                Phase(id="c", name="C", status=PhaseStatus.LOCKED, depends_on=["a", "b"]),
            ],
        )
        plan = unlock_phase(plan, "c")
        assert plan.phases[2].status == PhaseStatus.PENDING

    def test_unlock_multiple_deps_one_not_done(self) -> None:
        plan = Plan(
            project="test",
            phases=[
                Phase(id="a", name="A", status=PhaseStatus.DONE),
                Phase(id="b", name="B", status=PhaseStatus.IN_PROGRESS),
                Phase(id="c", name="C", status=PhaseStatus.LOCKED, depends_on=["a", "b"]),
            ],
        )
        with pytest.raises(PlanError, match="dependencies not done"):
            unlock_phase(plan, "c")


# ---------------------------------------------------------------------------
# add_steps_bulk
# ---------------------------------------------------------------------------


class TestAddStepsBulk:
    def test_basic_bulk(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Step A"},
                {"name": "Step B"},
            ],
        )
        assert len(ids) == 2
        assert len(plan.phases[0].steps) == 2
        assert plan.phases[0].steps[0].name == "Step A"
        assert plan.phases[0].steps[1].name == "Step B"

    def test_bulk_with_all_fields(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {
                    "name": "Step A",
                    "desc": "Description A",
                    "verify": "pytest -v",
                    "refs": ["docs/a.md"],
                },
            ],
        )
        assert len(ids) == 1
        _, step = plan.find_step(ids[0])
        assert step.name == "Step A"
        assert step.description == "Description A"
        assert step.verification == "pytest -v"
        assert step.refs == ["docs/a.md"]

    def test_bulk_sequential_deps(self) -> None:
        """Later entries can depend on earlier ones (added in sequence)."""
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Step A"},
                {"name": "Step B", "after": [ids[0]] if False else []},
            ],
        )
        # The above doesn't test deps since we don't know the ID ahead of time.
        # Let's use explicit IDs instead.
        plan2 = _plan_with_phase()
        plan2, ids2 = add_steps_bulk(
            plan2,
            "p1",
            [
                {"name": "Step A", "id": "p1.step-a"},
                {"name": "Step B", "after": ["p1.step-a"]},
            ],
        )
        _, step_b = plan2.find_step(ids2[1])
        assert "p1.step-a" in step_b.depends_on

    def test_bulk_explicit_ids(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Step A", "id": "p1.custom-a"},
                {"name": "Step B", "id": "p1.custom-b"},
            ],
        )
        assert ids == ["p1.custom-a", "p1.custom-b"]

    def test_bulk_missing_name_fails(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="'name' is required"):
            add_steps_bulk(plan, "p1", [{"desc": "no name"}])

    def test_bulk_phase_not_found(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            add_steps_bulk(plan, "nonexistent", [{"name": "X"}])

    def test_bulk_empty_list(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(plan, "p1", [])
        assert ids == []
        assert len(plan.phases[0].steps) == 0

    def test_bulk_after_as_comma_string(self) -> None:
        """The 'after' field can be a comma-separated string."""
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "A", "id": "p1.a"},
                {"name": "B", "after": "p1.a"},
            ],
        )
        _, step_b = plan.find_step(ids[1])
        assert "p1.a" in step_b.depends_on

    def test_bulk_intra_batch_short_slug_ref(self) -> None:
        """Step B references Step A by short slug 'step-a' (not full 'p1.step-a')."""
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Step A"},
                {"name": "Step B", "after": ["step-a"]},
                {"name": "Step C", "after": ["step-a", "step-b"]},
            ],
        )
        assert len(ids) == 3
        _, step_b = plan.find_step(ids[1])
        assert "p1.step-a" in step_b.depends_on
        _, step_c = plan.find_step(ids[2])
        assert "p1.step-a" in step_c.depends_on
        assert "p1.step-b" in step_c.depends_on

    def test_bulk_cycle_detection_rollback(self) -> None:
        """Circular deps in batch → PlanError, all batch steps removed."""
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="cycle"):
            add_steps_bulk(
                plan,
                "p1",
                [
                    {"name": "Step A", "id": "p1.step-a", "after": ["p1.step-b"]},
                    {"name": "Step B", "id": "p1.step-b", "after": ["p1.step-a"]},
                ],
            )
        # Atomic rollback: no steps left in phase
        assert len(plan.phases[0].steps) == 0

    def test_bulk_mixed_existing_and_batch_deps(self) -> None:
        """Batch step depends on pre-existing step + another batch step."""
        existing = Step(id="p1.existing", name="Existing Step")
        plan = _plan_with_phase(steps=[existing])
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "New A"},
                {"name": "New B", "after": ["p1.existing", "new-a"]},
            ],
        )
        assert len(ids) == 2
        _, step_b = plan.find_step(ids[1])
        assert "p1.existing" in step_b.depends_on
        assert "p1.new-a" in step_b.depends_on

    def test_bulk_unknown_dep_in_batch_fails(self) -> None:
        """Reference to non-existent step fails cleanly."""
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            add_steps_bulk(
                plan,
                "p1",
                [
                    {"name": "Step A", "after": ["nonexistent-step"]},
                ],
            )


# ---------------------------------------------------------------------------
# add_steps_bulk — import status support
# ---------------------------------------------------------------------------


class TestAddStepsBulkImportStatus:
    """Tests for add_steps_bulk() with status/evidence/skipped_reason in dicts."""

    def test_bulk_done_with_evidence(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [{"name": "Done A", "status": "done", "evidence": "commit abc"}],
        )
        assert len(ids) == 1
        step = plan.phases[0].steps[0]
        assert step.status == StepStatus.DONE
        assert step.evidence == "commit abc"

    def test_bulk_skipped_with_reason(self) -> None:
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [{"name": "Skip A", "status": "skipped", "skipped_reason": "absorbed"}],
        )
        step = plan.phases[0].steps[0]
        assert step.status == StepStatus.SKIPPED
        assert step.skipped_reason == "absorbed"

    def test_bulk_done_without_evidence_raises(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="without evidence"):
            add_steps_bulk(
                plan,
                "p1",
                [{"name": "Bad", "status": "done"}],
            )

    def test_bulk_skipped_without_reason_raises(self) -> None:
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="without skipped_reason"):
            add_steps_bulk(
                plan,
                "p1",
                [{"name": "Bad", "status": "skipped"}],
            )

    def test_bulk_transient_status_rejected(self) -> None:
        """Claimed/rejected are transient states — not allowed in bulk import."""
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="claimed/rejected require runtime"):
            add_steps_bulk(
                plan,
                "p1",
                [{"name": "Bad", "status": "claimed"}],
            )

    def test_bulk_mixed_statuses(self) -> None:
        """Batch with mixed pending/done/skipped steps."""
        plan = _plan_with_phase()
        plan, ids = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "A", "status": "done", "evidence": "migrated"},
                {"name": "B", "status": "skipped", "skipped_reason": "irrelevant"},
                {"name": "C"},  # default: pending
            ],
        )
        assert len(ids) == 3
        steps = plan.phases[0].steps
        assert steps[0].status == StepStatus.DONE
        assert steps[1].status == StepStatus.SKIPPED
        assert steps[2].status == StepStatus.PENDING

    def test_bulk_all_done_does_not_complete_phase(self) -> None:
        """Phase does NOT auto-complete when all imported steps are done."""
        plan = _plan_with_phase()
        plan, _ = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "A", "status": "done", "evidence": "ok"},
                {"name": "B", "status": "done", "evidence": "ok"},
            ],
        )
        # Import ≠ workflow advancement — phase stays PENDING
        assert plan.phases[0].status == PhaseStatus.PENDING

    def test_bulk_done_steps_do_not_reopen_done_phase(self) -> None:
        """Adding only done/skipped steps to DONE phase does NOT reopen it."""
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Existing", status=StepStatus.DONE, evidence="ok")],
        )
        plan, _ = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Imported A", "status": "done", "evidence": "migrated"},
                {"name": "Imported B", "status": "skipped", "skipped_reason": "absorbed"},
            ],
        )
        assert plan.phases[0].status == PhaseStatus.DONE  # stays DONE

    def test_bulk_pending_step_reopens_done_phase(self) -> None:
        """Adding a pending step in a mixed batch to DONE phase reopens it."""
        plan = _plan_with_phase(
            phase_status=PhaseStatus.DONE,
            steps=[Step(id="s1", name="Existing", status=StepStatus.DONE, evidence="ok")],
        )
        plan, _ = add_steps_bulk(
            plan,
            "p1",
            [
                {"name": "Done Import", "status": "done", "evidence": "ok"},
                {"name": "Pending Work"},  # triggers reopen
            ],
        )
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS

    def test_bulk_invalid_status_string(self) -> None:
        """Invalid status value raises PlanError."""
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="invalid status"):
            add_steps_bulk(
                plan,
                "p1",
                [{"name": "Bad", "status": "in_progress"}],
            )


# ---------------------------------------------------------------------------
# edit_phase
# ---------------------------------------------------------------------------


class TestEditPhase:
    def test_edit_name(self):
        plan = _plan_with_phase()
        plan = edit_phase(plan, "p1", name="Renamed Phase")
        assert plan.find_phase("p1").name == "Renamed Phase"

    def test_edit_context(self):
        plan = _plan_with_phase()
        plan = edit_phase(plan, "p1", context="New context info")
        assert plan.find_phase("p1").context == "New context info"

    def test_edit_gate(self):
        plan = _plan_with_phase()
        plan = edit_phase(plan, "p1", gate="mypy + pytest pass")
        assert plan.find_phase("p1").gate == "mypy + pytest pass"

    def test_add_dep(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p0", name="Phase 0", status=PhaseStatus.DONE),
                Phase(id="p1", name="Phase 1"),
            ],
        )
        plan = edit_phase(plan, "p1", add_deps=["p0"])
        assert "p0" in plan.find_phase("p1").depends_on

    def test_add_dep_invalid(self):
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            edit_phase(plan, "p1", add_deps=["nonexistent"])

    def test_add_dep_self(self):
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="cannot depend on itself"):
            edit_phase(plan, "p1", add_deps=["p1"])

    def test_rm_dep(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p0", name="Phase 0", status=PhaseStatus.DONE),
                Phase(id="p1", name="Phase 1", depends_on=["p0"]),
            ],
        )
        plan = edit_phase(plan, "p1", remove_deps=["p0"])
        assert "p0" not in plan.find_phase("p1").depends_on

    def test_phase_not_found(self):
        plan = _plan_with_phase()
        with pytest.raises(PlanError, match="not found"):
            edit_phase(plan, "nonexistent", name="X")
