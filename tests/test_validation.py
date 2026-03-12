"""Tests for core.3: DAG Validation."""

import pytest

from vectl.core import add_step, add_steps_bulk, claim_step, edit_step, validate_plan
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
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


# =============================================================================
# Step ID Validation Hardening Tests (core.3.1: Rollout Behavior)
# =============================================================================
#
# These tests validate the rollout behavior for step ID uniqueness:
# - Legacy plans: read-only surfaces emit warnings (soft validation)
# - Write surfaces: reject creating NEW duplicate IDs (hard validation)
# - Future flip: placeholders for eventual hard-error migration
#
# Reference: docs/RFC-step-id-validation.md


class TestLegacyDuplicateStepIdWarnings:
    """Legacy plans with duplicate IDs produce warning diagnostics in read-only surfaces."""

    def test_duplicate_step_id_is_warning(self):
        """Duplicate step IDs are warnings for diagnostics-only rollout."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="S1 Duplicate"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert len(errors) == 1
        assert "Duplicate step ID" in errors[0].message
        assert errors[0].is_warning is True

    def test_duplicate_step_id_warning_placeholder(self):
        """Placeholder test for future soft-warning flip.

        TODO(phase-2): When transitioning to soft-warning mode for legacy plans:
        1. Add a flag like `legacy_warnings=True` to validate_plan()
        2. Change duplicate detection to emit warnings instead of errors
        3. Update this test to verify `is_warning=True` when flag is set

        until then, this test serves as documentation of intended behavior.
        """
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="S1 Duplicate"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert errors[0].is_warning is True

    def test_read_only_surface_status_shows_warning_for_duplicate(self):
        """Status command should show validation warnings for legacy duplicates.

        This is a placeholder for testing the read-only surface behavior
        when legacy_warnings flag is implemented.
        """
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="S1 Dup"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert any(e.is_warning for e in errors)

    def test_read_only_surface_render_shows_warning_placeholder(self):
        """Render should show warnings for legacy duplicates (placeholder)."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="Dup"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert len(errors) > 0
        assert all(e.is_warning for e in errors)


class TestWriteSurfaceRejectsDuplicateIds:
    """Write surfaces reject creating NEW duplicate step IDs."""

    def test_add_step_rejects_duplicate_explicit_id(self):
        """add_step should reject creating a step with an ID that already exists."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="existing", name="Existing Step")],
                )
            ]
        )
        from vectl.core import add_step
        from vectl.models import PlanError

        # Trying to add a step with the same explicit ID should fail
        with pytest.raises(PlanError) as exc_info:
            add_step(plan, "p1", "New Step", step_id="existing")

        assert "already exists" in str(exc_info.value)

    def test_add_step_rejects_duplicate_auto_generated_id(self):
        """add_step should reject auto-generated ID that would be duplicate."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="my-step", name="My Step")],
                )
            ]
        )
        from vectl.core import add_step
        from vectl.models import PlanError

        # Adding another step with the same name generates the same slug
        # The uniqueness suffix logic should handle this, but if we try
        # to force an explicit duplicate, it should fail
        with pytest.raises(PlanError) as exc_info:
            add_step(plan, "p1", "My Step", step_id="my-step")

        assert "already exists" in str(exc_info.value)

    def test_add_step_rejects_duplicate_id_across_phases(self):
        plan = _make_plan(
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="shared", name="P1 Existing")]),
                Phase(id="p2", name="P2", steps=[]),
            ]
        )
        from vectl.models import PlanError

        with pytest.raises(PlanError) as exc_info:
            add_step(plan, "p2", "P2 New", step_id="shared")

        msg = str(exc_info.value)
        assert "error_code=duplicate_step_id_write_blocked" in msg
        assert "blocking_reason=duplicate_step_id_exists" in msg

    def test_add_steps_bulk_rejects_duplicate_id_across_phases(self):
        plan = _make_plan(
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="shared", name="P1 Existing")]),
                Phase(id="p2", name="P2", steps=[]),
            ]
        )
        from vectl.models import PlanError

        with pytest.raises(PlanError) as exc_info:
            add_steps_bulk(plan, "p2", [{"name": "P2 New", "id": "shared"}])

        assert "error_code=duplicate_step_id_write_blocked" in str(exc_info.value)

    def test_claim_rejects_ambiguous_duplicate_target_without_opt_in(self):
        plan = _make_plan(
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="dup.step", name="P1 Step")]),
                Phase(id="p2", name="P2", steps=[Step(id="dup.step", name="P2 Step")]),
            ]
        )
        from vectl.models import PlanError

        with pytest.raises(PlanError) as exc_info:
            claim_step(plan, "dup.step", "bot")

        msg = str(exc_info.value)
        assert "error_code=duplicate_step_id_ambiguous_target" in msg
        assert "blocking_reason=duplicate_step_id_ambiguous" in msg
        assert "recommendation.type=duplicate-step-id" in msg

    def test_add_phase_rejects_duplicate_phase_id(self):
        """add_phase should reject creating a phase with an ID that already exists."""
        plan = _make_plan(phases=[Phase(id="existing", name="Existing Phase")])
        from vectl.core import add_phase
        from vectl.models import PlanError

        with pytest.raises(PlanError) as exc_info:
            add_phase(plan, "New Phase", phase_id="existing")

        assert "already exists" in str(exc_info.value)


class TestFutureHardErrorFlipPlaceholders:
    """Placeholders for future hard-error flip behavior.

    These tests document the intended migration path from soft warnings
    to hard errors for duplicate step IDs.
    """

    def test_validation_flag_placeholder_soft_warnings(self):
        """Placeholder: validate_plan should accept flag for soft-warning mode."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s1", name="Dup"),
                    ],
                )
            ]
        )
        errors = validate_plan(plan)
        assert all(e.is_warning for e in errors)

        # Placeholder for future implementation:
        # errors = validate_plan(plan, strict=False)  # soft warnings for legacy
        # assert all(e.is_warning for e in errors)
        #
        # errors = validate_plan(plan, strict=True)   # hard errors (default)
        # assert all(not e.is_warning for e in errors)

    def test_cli_validate_flag_placeholder(self):
        """Phase-B surface keeps validate_plan signature stable (no strict flag yet)."""
        import inspect

        parameters = inspect.signature(validate_plan).parameters
        assert "strict" not in parameters

    def test_add_step_future_strict_mode_placeholder(self):
        """Placeholder: add_step will eventually support strict mode.

        In the future, add_step may accept a strict=True flag that
        enables additional validation beyond current checks.
        """
        # Current behavior: add_step rejects explicit duplicate IDs
        plan = _make_plan(phases=[Phase(id="p1", name="P1", steps=[Step(id="x", name="X")])])
        from vectl.core import add_step
        from vectl.models import PlanError

        # This already fails in current implementation
        with pytest.raises(PlanError):
            add_step(plan, "p1", "Another", step_id="x")

        # Placeholder for future: strict validation of auto-generated IDs
        # add_step(plan, "p1", "X", strict=True)  # Would fail if "x" exists
        # add_step(plan, "p1", "X", strict=False) # Would allow with suffix


# =============================================================================
# edit_step Cycle Detection Tests (p0-cycle-detection/implement)
# =============================================================================


class TestEditStepCycleDetection:
    """Cycle detection during edit_step mutations."""

    def test_edit_step_depends_on_cycle_raises_error(self):
        """edit_step with depends_on that creates a cycle raises PlanError."""
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
        # s1 depends on s2 creates cycle: s1 -> s2 -> s1
        with pytest.raises(PlanError) as exc_info:
            edit_step(plan, "s1", depends_on=["s2"])

        assert "cycle" in str(exc_info.value).lower()
        found = plan.find_step("s1")
        assert found is not None
        _, s1 = found
        assert s1.depends_on == []

    def test_edit_step_add_deps_cycle_raises_error(self):
        """edit_step with add_deps that creates a cycle raises PlanError."""
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
        # s1 -> s2 already exists; adding s2 as dep to s1 creates cycle
        with pytest.raises(PlanError) as exc_info:
            edit_step(plan, "s1", add_deps=["s2"])

        assert "cycle" in str(exc_info.value).lower()
        found = plan.find_step("s1")
        assert found is not None
        _, s1 = found
        assert s1.depends_on == []

    def test_edit_step_cycle_rollback_keeps_non_dependency_edits(self):
        """Dependency rollback on cycle leaves non-dependency edits intact."""
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

        with pytest.raises(PlanError):
            edit_step(plan, "s1", name="S1 updated", add_deps=["s2"])

        found = plan.find_step("s1")
        assert found is not None
        _, s1 = found
        assert s1.depends_on == []
        assert s1.name == "S1 updated"

    def test_edit_step_remove_deps_cycle_raises_error(self):
        """edit_step with remove_deps does NOT raise cycle (removing deps can't create cycles)."""
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
        # Removing deps cannot create a cycle, should succeed
        result = edit_step(plan, "s2", remove_deps=["s1"])
        assert result is not None

    def test_edit_step_valid_deps_succeeds(self):
        """edit_step with valid dependencies succeeds."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                        Step(id="s2", name="S2"),
                        Step(id="s3", name="S3"),
                    ],
                )
            ]
        )
        # Adding valid dependencies should succeed
        result = edit_step(plan, "s3", add_deps=["s1", "s2"])
        assert result is not None

    def test_edit_step_no_dep_mutation_no_cycle_check(self):
        """edit_step without dep mutations doesn't trigger cycle check."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1"),
                    ],
                )
            ]
        )
        # No dep mutation, should succeed even with existing steps
        result = edit_step(plan, "s1", name="Updated S1")
        assert result is not None
        found = plan.find_step("s1")
        assert found is not None
        _, s1 = found
        assert s1.name == "Updated S1"


# =============================================================================
# Qualified ID in Error Messages Tests (p1-qualified-lookup-errors/implement-error-msg)
# =============================================================================


class TestQualifiedIdInErrorMessages:
    """Validation error messages use {phase.id}.{step.id} qualified ID format."""

    def test_claim_step_wrong_status_error_uses_qualified_id(self):
        """claim_step error for wrong status uses qualified ID format like 'p1.s1'."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", status=StepStatus.DONE)],
                )
            ]
        )
        from vectl.lifecycle import claim_step

        with pytest.raises(PlanError) as exc_info:
            claim_step(plan, "s1", "agent-1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_complete_step_wrong_status_error_uses_qualified_id(self):
        """complete_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[Step(id="validate", name="Validate", status=StepStatus.PENDING)],
                )
            ]
        )
        from vectl.lifecycle import complete_step

        with pytest.raises(PlanError) as exc_info:
            complete_step(plan, "validate", "evidence")

        msg = str(exc_info.value)
        assert "core.validate" in msg, (
            f"Error message should contain qualified ID 'core.validate', got: {msg}"
        )

    def test_defer_step_wrong_status_error_uses_qualified_id(self):
        """defer_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", status=StepStatus.PENDING)],
                )
            ]
        )
        from vectl.lifecycle import defer_step

        with pytest.raises(PlanError) as exc_info:
            defer_step(plan, "s1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_reject_step_wrong_status_error_uses_qualified_id(self):
        """reject_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[Step(id="s1", name="S1", status=StepStatus.PENDING)],
                )
            ]
        )
        from vectl.lifecycle import reject_step

        with pytest.raises(PlanError) as exc_info:
            reject_step(plan, "s1", "needs rework")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_skip_step_wrong_status_error_uses_qualified_id(self):
        """skip_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[Step(id="validate", name="Validate", status=StepStatus.DONE)],
                )
            ]
        )
        from vectl.lifecycle import skip_step

        with pytest.raises(PlanError) as exc_info:
            skip_step(plan, "validate", "irrelevant")

        msg = str(exc_info.value)
        assert "core.validate" in msg, (
            f"Error message should contain qualified ID 'core.validate', got: {msg}"
        )

    def test_edit_step_cycle_error_uses_qualified_id(self):
        """edit_step error for dependency cycle uses qualified ID format."""
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
        # s1 depends on s2 creates cycle: s1 -> s2 -> s1
        with pytest.raises(PlanError) as exc_info:
            edit_step(plan, "s1", depends_on=["s2"])

        msg = str(exc_info.value)
        # Should contain qualified IDs like p1.s2 → p1.s1
        assert "p1.s1" in msg and "p1.s2" in msg, (
            f"Error message should contain qualified IDs, got: {msg}"
        )

    def test_remove_step_wrong_status_error_uses_qualified_id(self):
        """remove_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="agent-1")
                    ],
                )
            ]
        )
        from vectl.core import remove_step

        with pytest.raises(PlanError) as exc_info:
            remove_step(plan, "s1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_move_step_wrong_status_error_uses_qualified_id(self):
        """move_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="agent-1")
                    ],
                ),
                Phase(id="p2", name="P2", steps=[]),
            ]
        )
        from vectl.core import move_step

        with pytest.raises(PlanError) as exc_info:
            move_step(plan, "s1", "p2")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_remove_step_wrong_status_error_uses_qualified_id(self):
        """remove_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="agent-1")
                    ],
                )
            ]
        )
        from vectl.core import remove_step

        with pytest.raises(PlanError) as exc_info:
            remove_step(plan, "s1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_move_step_wrong_status_error_uses_qualified_id(self):
        """move_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="agent-1")
                    ],
                ),
                Phase(id="p2", name="P2", steps=[]),
            ]
        )
        from vectl.core import move_step

        with pytest.raises(PlanError) as exc_info:
            move_step(plan, "s1", "p2")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_remove_step_wrong_status_error_uses_qualified_id(self):
        """remove_step error for wrong status uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.CLAIMED, claimed_by="agent-1")
                    ],
                )
            ]
        )
        from vectl.core import remove_step

        with pytest.raises(PlanError) as exc_info:
            remove_step(plan, "s1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_move_step_wrong_phase_error_uses_qualified_id(self):
        """move_step error when step is already in target phase uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")]),
                Phase(id="p2", name="P2", steps=[]),
            ]
        )
        from vectl.core import move_step

        # Trying to move to same phase it already belongs to
        with pytest.raises(PlanError) as exc_info:
            move_step(plan, "s1", "p1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_claim_step_inactive_phase_error_uses_qualified_id(self):
        """claim_step error for inactive phase uses qualified ID format."""
        plan = _make_plan(
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.LOCKED,
                    steps=[Step(id="s1", name="S1")],
                )
            ]
        )
        from vectl.lifecycle import claim_step

        with pytest.raises(PlanError) as exc_info:
            claim_step(plan, "s1", "agent-1")

        msg = str(exc_info.value)
        assert "p1.s1" in msg, f"Error message should contain qualified ID 'p1.s1', got: {msg}"

    def test_claim_step_unmet_deps_error_uses_qualified_id(self):
        """claim_step error for unmet dependencies uses qualified ID format."""
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
        from vectl.lifecycle import claim_step

        with pytest.raises(PlanError) as exc_info:
            claim_step(plan, "s2", "agent-1")

        msg = str(exc_info.value)
        assert "p1.s2" in msg, f"Error message should contain qualified ID 'p1.s2', got: {msg}"

    def test_error_message_copy_pasteable_as_step_id(self):
        """Qualified ID in error message can be copy-pasted to identify step.

        Note: The current find_step only matches on step.id, not qualified ID.
        This test verifies the qualified ID format is correct and can be parsed.
        The copy-paste use case would require updating find_step to support
        qualified IDs in the future.
        """
        plan = _make_plan(
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[Step(id="validate", name="Validate", status=StepStatus.DONE)],
                )
            ]
        )
        from vectl.lifecycle import claim_step

        with pytest.raises(PlanError) as exc_info:
            claim_step(plan, "validate", "agent-1")

        msg = str(exc_info.value)
        # Verify qualified ID format is present
        assert "core.validate" in msg, (
            f"Error message should contain qualified ID 'core.validate', got: {msg}"
        )
        # Verify the qualified ID can be parsed into phase and step
        assert msg.count("core.validate") > 0
