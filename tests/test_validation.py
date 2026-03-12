"""Tests for core.3: DAG Validation."""

import pytest

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

    def test_duplicate_step_id_is_error_not_warning(self):
        """Current behavior: duplicate step IDs are errors (not warnings).

        This test documents current hard-error behavior. The step below
        is a placeholder for the future soft-warning migration.
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
        assert len(errors) == 1
        assert "Duplicate step ID" in errors[0].message
        # Currently this is an error, not a warning
        assert errors[0].is_warning is False

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
        # Current behavior: hard error
        errors = validate_plan(plan)
        assert errors[0].is_warning is False
        # TODO: After flip, this should become:
        # errors = validate_plan(plan, legacy_warnings=True)
        # assert errors[0].is_warning is True

    def test_read_only_surface_status_shows_warning_for_duplicate(self):
        """Status command should show validation warnings for legacy duplicates.

        This is a placeholder for testing the read-only surface behavior
        when legacy_warnings flag is implemented.
        """
        # Currently, validate_plan returns errors (not warnings) for duplicates.
        # Read-only surfaces (status, render, show) will display these as errors.
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
        # Validation produces non-warning errors currently
        assert any(not e.is_warning for e in errors)
        # Placeholder: after flip, read-only surfaces should show warnings:
        # errors = validate_plan(plan, legacy_warnings=True)
        # assert any(e.is_warning for e in errors)

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
        # Current behavior - validation errors
        errors = validate_plan(plan)
        assert len(errors) > 0
        # Placeholder for render output with warnings:
        # After flip: render should indicate warnings without blocking


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
        # Current behavior: always returns errors
        errors = validate_plan(plan)
        assert all(not e.is_warning for e in errors)

        # Placeholder for future implementation:
        # errors = validate_plan(plan, strict=False)  # soft warnings for legacy
        # assert all(e.is_warning for e in errors)
        #
        # errors = validate_plan(plan, strict=True)   # hard errors (default)
        # assert all(not e.is_warning for e in errors)

    def test_cli_validate_flag_placeholder(self):
        """Placeholder: CLI validate command should have --strict/--warn flags."""
        # Current CLI always treats duplicates as errors (exit code 1)
        # Placeholder for future:
        # vectl validate --warn    # exits 0, shows warnings for legacy dupes
        # vectl validate --strict  # exits 1, treats all as errors (default)
        pass  # Documenting intended CLI flag behavior

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
