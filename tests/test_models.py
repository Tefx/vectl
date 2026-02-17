"""Tests for core.1: Pydantic Data Models."""

import pytest
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

    def test_find_phase(self):
        plan = Plan(
            project="test",
            phases=[Phase(id="p1", name="Phase 1"), Phase(id="p2", name="Phase 2")],
        )
        assert plan.find_phase("p2") is not None
        assert plan.find_phase("p2").name == "Phase 2"
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
