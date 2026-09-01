"""Tests for core.1: Pydantic Data Models."""

import pytest
from pydantic import ValidationError

from vectl.core.diagnostics import normalize_model_validation_issues
from vectl.models import (
    AffinityMode,
    Clipboard,
    Phase,
    PhaseStatus,
    Plan,
    RejectionEntry,
    Step,
    StepStatus,
)


def test_canonical_error_normalization_drops_messages_and_dynamic_locations() -> None:
    raw = "candidate-value\nrendered_issue_count: 999"

    issues = normalize_model_validation_issues(
        [
            {
                "loc": ("phases", raw),
                "msg": f"raw value was {raw}",
                "type": f"value_error.{raw}",
            }
        ]
    )

    assert issues[0].message == (
        "canonical_model.phases.<field>: canonical model constraint violated "
        "[validation_error]"
    )
    assert raw not in issues[0].message


def test_canonical_error_normalization_allowlists_schema_paths_and_builtin_types() -> None:
    issues = normalize_model_validation_issues(
        [
            {"loc": ("clipboard", "author"), "type": "missing"},
            {
                "loc": (
                    "phases",
                    0,
                    "steps",
                    1,
                    "rejection_history",
                    2,
                    "reviewer",
                ),
                "type": "string_type",
            },
            {
                "loc": ("phases", 0, "steps", 0, "RAW_LOCATION_SECRET"),
                "type": "RAW_TYPE_SECRET",
                "msg": "RAW_MESSAGE_SECRET",
                "input": "RAW_INPUT_SECRET",
            },
        ]
    )

    assert [issue.message for issue in issues] == [
        "canonical_model.clipboard.author: canonical model constraint violated [missing]",
        (
            "canonical_model.phases[0].steps[1].rejection_history[2].reviewer: "
            "canonical model constraint violated [string_type]"
        ),
        (
            "canonical_model.phases[0].steps[0].<field>: "
            "canonical model constraint violated [validation_error]"
        ),
    ]
    rendered = "\n".join(issue.message for issue in issues)
    for secret in (
        "RAW_LOCATION_SECRET",
        "RAW_TYPE_SECRET",
        "RAW_MESSAGE_SECRET",
        "RAW_INPUT_SECRET",
    ):
        assert secret not in rendered


class TestStepStatus:
    def test_enum_values(self):
        assert StepStatus.PENDING == "pending"
        assert StepStatus.CLAIMED == "claimed"
        assert StepStatus.DONE == "done"
        assert StepStatus.SKIPPED == "skipped"
        assert StepStatus.REJECTED == "rejected"

    def test_all_values(self):
        assert len(StepStatus) == 5


class TestPhaseStatus:
    def test_enum_values(self):
        assert PhaseStatus.LOCKED == "locked"
        assert PhaseStatus.PENDING == "pending"
        assert PhaseStatus.IN_PROGRESS == "in_progress"
        assert PhaseStatus.DONE == "done"

    def test_all_values(self):
        assert len(PhaseStatus) == 4


class TestRejectionEntry:
    def test_basic(self):
        entry = RejectionEntry(reason="Bad code", timestamp="2026-01-01T00:00:00Z")
        assert entry.reason == "Bad code"
        assert entry.reviewer == ""

    def test_with_reviewer(self):
        entry = RejectionEntry(
            reason="Missing tests", timestamp="2026-01-01T00:00:00Z", reviewer="alice"
        )
        assert entry.reviewer == "alice"


class TestStep:
    def test_minimal(self):
        step = Step(id="s1", name="Do thing")
        assert step.status == StepStatus.PENDING
        assert step.depends_on == []
        assert step.refs == []
        assert "isolation" not in Step.model_fields

    def test_unknown_step_field_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            Step.model_validate({"id": "s1", "name": "Do thing", "isolation": "workspace"})

        assert exc_info.value.errors()[0]["loc"] == ("isolation",)
        assert exc_info.value.errors()[0]["type"] == "extra_forbidden"

    def test_skipped_requires_reason(self):
        with pytest.raises(ValueError, match="skipped_reason is required"):
            Step(id="s1", name="X", status=StepStatus.SKIPPED)

    def test_skipped_with_reason_ok(self):
        step = Step(id="s1", name="X", status=StepStatus.SKIPPED, skipped_reason="Not needed")
        assert step.skipped_reason == "Not needed"

    def test_rejected_requires_reason(self):
        with pytest.raises(ValueError, match="rejection_reason is required"):
            Step(id="s1", name="X", status=StepStatus.REJECTED)

    def test_rejected_with_reason_ok(self):
        step = Step(id="s1", name="X", status=StepStatus.REJECTED, rejection_reason="Bad quality")
        assert step.rejection_reason == "Bad quality"

    def test_claimed_requires_claimed_by(self):
        with pytest.raises(ValueError, match="claimed_by is required"):
            Step(id="s1", name="X", status=StepStatus.CLAIMED)

    def test_claimed_with_claimed_by_ok(self):
        step = Step(id="s1", name="X", status=StepStatus.CLAIMED, claimed_by="agent-1")
        assert step.claimed_by == "agent-1"

    def test_full_step(self):
        step = Step(
            id="auth.1",
            name="User Model",
            status=StepStatus.DONE,
            description="Define user schema",
            verification="pytest test_user.py",
            refs=["docs/auth.md#user"],
            depends_on=["auth.0"],
            evidence="commit abc123",
        )
        assert step.id == "auth.1"
        assert step.refs == ["docs/auth.md#user"]
        assert step.depends_on == ["auth.0"]

    def test_done_at_default_none(self):
        """Step default done_at=None."""
        step = Step(id="s1", name="Do thing")
        assert step.done_at is None

    def test_done_at_with_timestamp(self):
        """Step done_at with timestamp string accepted."""
        step = Step(id="s1", name="Do thing", done_at="2026-03-08T10:00:00Z")
        assert step.done_at == "2026-03-08T10:00:00Z"

    def test_done_at_none_with_status_done(self):
        """Step with status=done and done_at=None remains valid (backward compatibility)."""
        step = Step(id="s1", name="Do thing", status=StepStatus.DONE)
        assert step.status == StepStatus.DONE
        assert step.done_at is None


class TestPhase:
    def test_minimal(self):
        phase = Phase(id="p1", name="Phase One")
        assert phase.status == PhaseStatus.PENDING
        assert phase.steps == []
        assert phase.depends_on == []

    def test_with_steps(self):
        phase = Phase(
            id="core",
            name="Core",
            steps=[
                Step(id="c1", name="Models"),
                Step(id="c2", name="IO", depends_on=["c1"]),
            ],
        )
        assert len(phase.steps) == 2
        assert phase.steps[1].depends_on == ["c1"]

    def test_locked_phase(self):
        phase = Phase(id="p2", name="Phase Two", status=PhaseStatus.LOCKED, depends_on=["p1"])
        assert phase.status == PhaseStatus.LOCKED


class TestPersistedModelExtras:
    @pytest.mark.parametrize(
        ("model", "payload", "location"),
        [
            (Plan, {"project": "test", "unknown": "reject"}, "unknown"),
            (Phase, {"id": "phase", "name": "Phase", "unknown": "reject"}, "unknown"),
            (
                Clipboard,
                {
                    "author": "a",
                    "summary": "s",
                    "content": "c",
                    "written_at": "2025-01-01T00:00:00Z",
                    "expires_at": "2025-01-01T01:00:00Z",
                    "unknown": "reject",
                },
                "unknown",
            ),
            (
                RejectionEntry,
                {"reason": "r", "timestamp": "2025-01-01T00:00:00Z", "unknown": "reject"},
                "unknown",
            ),
        ],
    )
    def test_unknown_persisted_field_is_rejected(self, model, payload, location: str):
        with pytest.raises(ValidationError) as exc_info:
            model(**payload)

        assert exc_info.value.errors()[0]["loc"] == (location,)
        assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


class TestPlan:
    def test_minimal(self):
        plan = Plan(project="test")
        assert plan.version == 1
        assert plan.phases == []

    def test_find_step(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="s1", name="Step 1"), Step(id="s2", name="Step 2")],
                ),
                Phase(id="p2", name="Phase 2", steps=[Step(id="s3", name="Step 3")]),
            ],
        )
        result = plan.find_step("s2")
        assert result is not None
        phase, step = result
        assert phase.id == "p1"
        assert step.id == "s2"

    def test_find_step_not_found(self):
        plan = Plan(project="test")
        assert plan.find_step("nope") is None

    def test_find_step_qualified_lookup_exact_match_priority(self):
        """Exact match should take priority over qualified lookup."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="s1", name="Step 1"), Step(id="p1.s1", name="Qualified S1")],
                ),
            ],
        )
        # Exact match "s1" should be found first (in p1), not "p1.s1"
        result = plan.find_step("s1")
        assert result is not None
        phase, step = result
        assert phase.id == "p1"
        assert step.id == "s1"

    def test_find_step_qualified_lookup_fallback(self):
        """Qualified lookup should work when exact match fails."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="s1", name="Step 1"), Step(id="s2", name="Step 2")],
                ),
                Phase(
                    id="p2",
                    name="Phase 2",
                    steps=[Step(id="s3", name="Step 3")],
                ),
            ],
        )
        # Exact match "s1" should work
        result = plan.find_step("s1")
        assert result is not None
        phase, step = result
        assert phase.id == "p1"
        assert step.id == "s1"

        # Exact match "p1.s1" should work (qualified form matches exact step id)
        result = plan.find_step("p1.s1")
        assert result is not None
        phase, step = result
        assert phase.id == "p1"
        assert step.id == "s1"

        # Exact match "p2.s3" should work
        result = plan.find_step("p2.s3")
        assert result is not None
        phase, step = result
        assert phase.id == "p2"
        assert step.id == "s3"

        # Non-existent qualified step should return None
        result = plan.find_step("p1.s3")
        assert result is None

        # Non-existent phase in qualified lookup should return None
        result = plan.find_step("nonexistent.s1")
        assert result is None

    def test_find_step_qualified_lookup_partition_behavior(self):
        """Qualified lookup should use partition('.') - only first dot splits."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="p1.s1", name="Step 1")],
                ),
            ],
        )
        # "p1.s1" has phase_prefix="p1", step_suffix="s1" - should find
        result = plan.find_step("p1.s1")
        assert result is not None
        assert result[1].id == "p1.s1"

    def test_find_step_qualified_lookup_empty_parts(self):
        """Qualified lookup returns None if phase prefix or step suffix is empty."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="s1", name="Step 1")],
                ),
            ],
        )
        # Empty step suffix (no dot) - falls through to exact match
        result = plan.find_step("p1.")
        # "p1." has phase_prefix="p1", step_suffix="" - empty suffix should not match
        # The function checks "if phase_prefix and step_suffix" so empty suffix returns None
        assert result is None

    def test_find_step_qualified_lookup_with_exact_step_id_in_specific_phase(self):
        """Qualified lookup should find step by phase prefix + step suffix."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="phase-a",
                    name="Phase A",
                    steps=[Step(id="step-1", name="Step 1")],
                ),
                Phase(
                    id="phase-b",
                    name="Phase B",
                    steps=[Step(id="step-1", name="Step 1 in B")],
                ),
            ],
        )
        # Exact match should find first match
        result = plan.find_step("step-1")
        assert result is not None
        assert result[0].id == "phase-a"
        assert result[1].id == "step-1"

        # Qualified lookup for phase-b should find the one in phase-b
        result = plan.find_step("phase-b.step-1")
        assert result is not None
        assert result[0].id == "phase-b"
        assert result[1].id == "step-1"

        # Qualified lookup for phase-a should find the one in phase-a
        result = plan.find_step("phase-a.step-1")
        assert result is not None
        assert result[0].id == "phase-a"
        assert result[1].id == "step-1"

    def test_find_step_matches_exposes_ambiguity_without_changing_first_match(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="phase-a",
                    name="Phase A",
                    steps=[Step(id="dup", name="First")],
                ),
                Phase(
                    id="phase-b",
                    name="Phase B",
                    steps=[Step(id="dup", name="Second")],
                ),
            ],
        )

        assert [phase.id for phase, _ in plan.find_step_matches("dup")] == [
            "phase-a",
            "phase-b",
        ]
        assert [phase.id for phase, _ in plan.find_step_matches("phase-b.dup")] == [
            "phase-b"
        ]
        first = plan.find_step("dup")
        assert first is not None
        assert first[0].id == "phase-a"

    def test_find_step_matches_combines_exact_and_qualified_interpretations(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="other",
                    name="Other",
                    steps=[Step(id="target.review", name="Exact stored ID")],
                ),
                Phase(
                    id="target",
                    name="Target",
                    steps=[Step(id="review", name="Qualified fallback")],
                ),
            ],
        )

        assert [
            (phase.id, step.id)
            for phase, step in plan.find_step_matches("target.review")
        ] == [("other", "target.review"), ("target", "review")]
        first = plan.find_step("target.review")
        assert first is not None
        assert (first[0].id, first[1].id) == ("other", "target.review")

    def test_find_step_no_match_returns_none(self):
        """find_step returns None when no step matches."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="s1", name="Step 1")],
                ),
            ],
        )
        # Non-existent step
        assert plan.find_step("nonexistent") is None
        # Non-existent qualified step
        assert plan.find_step("p1.nonexistent") is None
        # Non-existent phase
        assert plan.find_step("nonexistent.s1") is None

    def test_find_step_case_sensitivity(self):
        """find_step lookup is case-sensitive."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="Phase1",
                    name="Phase 1",
                    steps=[Step(id="Step1", name="Step 1")],
                ),
            ],
        )
        # Case matters - exact match
        assert plan.find_step("Step1") is not None
        # Case mismatch - should not match
        assert plan.find_step("step1") is None
        assert plan.find_step("STEP1") is None
        # Phase case mismatch
        assert plan.find_step("phase1.Step1") is None

    def test_find_phase(self):
        plan = Plan(
            project="test",
            phases=[Phase(id="p1", name="Phase 1"), Phase(id="p2", name="Phase 2")],
        )
        phase = plan.find_phase("p2")
        assert phase is not None
        assert phase.name == "Phase 2"
        assert plan.find_phase("nope") is None


class TestClipboard:
    def test_minimal(self):
        cb = Clipboard(
            author="agent-1",
            summary="Test summary",
            content="Test content",
            written_at="2026-02-16T10:00:00Z",
            expires_at="2026-02-17T10:00:00Z",
        )
        assert cb.author == "agent-1"
        assert cb.summary == "Test summary"
        assert cb.content == "Test content"

    def test_empty_author_rejected(self):
        with pytest.raises(ValueError, match="author cannot be empty"):
            Clipboard(
                author="",
                summary="Test",
                content="Content",
                written_at="2026-02-16T10:00:00Z",
                expires_at="2026-02-17T10:00:00Z",
            )

    def test_whitespace_only_author_rejected(self):
        with pytest.raises(ValueError, match="author cannot be empty"):
            Clipboard(
                author="   ",
                summary="Test",
                content="Content",
                written_at="2026-02-16T10:00:00Z",
                expires_at="2026-02-17T10:00:00Z",
            )

    def test_empty_content_rejected(self):
        with pytest.raises(ValueError, match="content cannot be empty"):
            Clipboard(
                author="agent-1",
                summary="Test",
                content="",
                written_at="2026-02-16T10:00:00Z",
                expires_at="2026-02-17T10:00:00Z",
            )

    def test_whitespace_only_content_rejected(self):
        with pytest.raises(ValueError, match="content cannot be empty"):
            Clipboard(
                author="agent-1",
                summary="Test",
                content="   \n\t  ",
                written_at="2026-02-16T10:00:00Z",
                expires_at="2026-02-17T10:00:00Z",
            )


class TestPlanClipboard:
    def test_plan_without_clipboard(self):
        plan = Plan(project="test")
        assert plan.clipboard is None

    def test_plan_with_clipboard(self):
        cb = Clipboard(
            author="agent-1",
            summary="Handoff note",
            content="Here's the design for phase 2",
            written_at="2026-02-16T10:00:00Z",
            expires_at="2026-02-17T10:00:00Z",
        )
        plan = Plan(project="test", clipboard=cb)
        assert plan.clipboard is not None
        assert plan.clipboard.author == "agent-1"

    def test_backward_compat_plan_without_clipboard_field(self):
        """Plan without clipboard field loads correctly (backward compatibility)."""
        plan = Plan(project="test", phases=[Phase(id="p1", name="Phase 1")])
        assert plan.clipboard is None
        assert len(plan.phases) == 1


# ---------------------------------------------------------------------------
# RFC: docs/RFC-affinity.md — Affinity Model Tests
# ---------------------------------------------------------------------------


class TestAffinityMode:
    def test_enum_values(self):
        from vectl.models import AffinityMode

        assert AffinityMode.SUGGESTED.value == "suggested"
        assert AffinityMode.EXCLUSIVE.value == "exclusive"

    def test_all_values(self):
        from vectl.models import AffinityMode

        values = [e.value for e in AffinityMode]
        assert "suggested" in values
        assert "exclusive" in values


class TestStepAffinity:
    def test_step_affinity_none_default(self):
        """Step with affinity=None loads correctly."""
        step = Step(id="s1", name="Step 1")
        assert step.affinity is None

    def test_step_affinity_exclusive(self):
        """Step with affinity=exclusive."""
        step = Step(id="s1", name="Step 1", affinity=AffinityMode.EXCLUSIVE)
        assert step.affinity == AffinityMode.EXCLUSIVE

    def test_step_affinity_override_fields_default(self):
        """Step affinity_override fields default to False/None."""
        step = Step(id="s1", name="Step 1")
        assert step.affinity_override is False
        assert step.affinity_override_by is None
        assert step.affinity_override_at is None

    def test_step_affinity_override_set(self):
        """Step affinity_override can be set."""
        step = Step(
            id="s1",
            name="Step 1",
            affinity_override=True,
            affinity_override_by="human",
            affinity_override_at="2026-02-18T10:00:00Z",
        )
        assert step.affinity_override is True
        assert step.affinity_override_by == "human"


class TestPlanAffinity:
    def test_plan_default_affinity_suggested(self):
        """Plan default_affinity defaults to SUGGESTED."""
        plan = Plan(project="test")
        assert plan.default_affinity == AffinityMode.SUGGESTED

    def test_plan_default_affinity_exclusive(self):
        """Plan default_affinity can be set to EXCLUSIVE."""
        plan = Plan(project="test", default_affinity=AffinityMode.EXCLUSIVE)
        assert plan.default_affinity == AffinityMode.EXCLUSIVE


class TestAffinityBackwardCompat:
    def test_existing_plan_without_affinity_loads(self):
        """Existing plan.yaml without affinity fields loads without error."""
        plan = Plan(
            project="test", phases=[Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")])]
        )
        assert plan.default_affinity == AffinityMode.SUGGESTED
        assert plan.phases[0].steps[0].affinity is None
        assert plan.phases[0].steps[0].affinity_override is False


class TestPlanPlanId:
    def test_default_none(self):
        plan = Plan(project="test")
        assert plan.plan_id is None

    def test_explicit_value(self):
        plan = Plan(project="test", plan_id="plan-xyz")
        assert plan.plan_id == "plan-xyz"

    def test_backward_compat_parse_without_plan_id(self):
        payload = {
            "version": 1,
            "project": "test",
            "phases": [
                {
                    "id": "core",
                    "name": "Core",
                    "steps": [{"id": "core.1", "name": "Implement models"}],
                }
            ],
        }
        plan = Plan.model_validate(payload)
        assert plan.project == "test"
        assert plan.plan_id is None
        assert len(plan.phases) == 1
