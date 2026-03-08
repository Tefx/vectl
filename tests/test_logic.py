"""Tests for core.4: State Machine & Query Logic."""

import datetime

import pytest

from vectl.core import (
    CLIPBOARD_CONTENT_MAX,
    CLIPBOARD_SUMMARY_MAX,
    auto_unlock_phases,
    claim_step,
    clipboard_clear,
    _clipboard_expired,
    clipboard_write,
    complete_phase,
    complete_step,
    defer_step,
    get_claimed_steps,
    get_next_steps,
    recalc_lock_status,
    reject_step,
    search_plan,
    skip_phase,
    skip_step,
)
from vectl.models import (
    AffinityError,
    AffinityMode,
    Clipboard,
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)


def _simple_plan() -> Plan:
    """Plan with two phases: p1 (pending), p2 (locked, depends on p1)."""
    return Plan(
        project="test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2", depends_on=["s1"]),
                    Step(id="s3", name="Step 3"),
                ],
            ),
            Phase(
                id="p2",
                name="Phase 2",
                status=PhaseStatus.LOCKED,
                depends_on=["p1"],
                steps=[
                    Step(id="s4", name="Step 4"),
                ],
            ),
        ],
    )


class TestGetNextSteps:
    def test_returns_unblocked_steps(self):
        plan = _simple_plan()
        nexts = get_next_steps(plan)
        ids = [s.id for s in nexts]
        assert "s1" in ids  # no deps
        assert "s3" in ids  # no deps
        assert "s2" not in ids  # blocked by s1

    def test_locked_phase_excluded(self):
        plan = _simple_plan()
        nexts = get_next_steps(plan)
        ids = [s.id for s in nexts]
        assert "s4" not in ids  # phase p2 is locked

    def test_rejected_steps_first(self):
        plan = _simple_plan()
        plan.phases[0].steps[2].status = StepStatus.REJECTED
        plan.phases[0].steps[2].rejection_reason = "Bad"
        nexts = get_next_steps(plan)
        assert nexts[0].id == "s3"  # rejected → priority
        assert nexts[0].status == StepStatus.REJECTED

    def test_step_deps_satisfied(self):
        plan = _simple_plan()
        plan.phases[0].steps[0].status = StepStatus.DONE
        plan.phases[0].steps[0].evidence = "done"
        nexts = get_next_steps(plan)
        ids = [s.id for s in nexts]
        assert "s2" in ids  # s1 is done, s2 unblocked

    def test_phase_auto_unlock(self):
        """When all deps of a locked phase are done, it auto-unlocks."""
        plan = _simple_plan()
        plan.phases[0].status = PhaseStatus.DONE
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "done"
        nexts = get_next_steps(plan)
        ids = [s.id for s in nexts]
        assert "s4" in ids  # p2 auto-unlocked because p1 is done

    def test_get_next_steps_does_not_mutate_phase_status(self):
        """CQS: get_next_steps is a query — must not mutate phase status.

        Reproduction for CQS violation: _get_active_phase_ids() was silently
        changing LOCKED → PENDING as a side effect of querying.
        """
        plan = _simple_plan()
        # Make p1 done so p2 becomes eligible
        plan.phases[0].status = PhaseStatus.DONE
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "done"

        # p2 is LOCKED but eligible (all deps done)
        assert plan.phases[1].status == PhaseStatus.LOCKED

        # Query should return p2's steps but NOT mutate p2's status
        nexts = get_next_steps(plan)
        assert any(s.id == "s4" for s in nexts), "Should return steps from eligible phase"

        # THE BUG: _get_active_phase_ids mutated phase status as side effect
        assert plan.phases[1].status == PhaseStatus.LOCKED, (
            "CQS violation: get_next_steps must not mutate phase status"
        )

    def test_empty_plan(self):
        plan = Plan(project="test")
        assert get_next_steps(plan) == []


class TestAutoUnlockPhases:
    def test_unlocks_eligible_phases(self):
        """auto_unlock_phases explicitly transitions LOCKED → PENDING."""
        plan = _simple_plan()
        plan.phases[0].status = PhaseStatus.DONE
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "done"

        assert plan.phases[1].status == PhaseStatus.LOCKED
        unlocked = auto_unlock_phases(plan)
        assert unlocked == ["p2"]
        assert plan.phases[1].status == PhaseStatus.PENDING

    def test_does_not_unlock_when_deps_unmet(self):
        """Locked phases with unmet deps remain locked."""
        plan = _simple_plan()
        # p1 is PENDING, not DONE → p2 should stay locked
        unlocked = auto_unlock_phases(plan)
        assert unlocked == []
        assert plan.phases[1].status == PhaseStatus.LOCKED

    def test_idempotent(self):
        """Calling twice does not re-unlock already pending phases."""
        plan = _simple_plan()
        plan.phases[0].status = PhaseStatus.DONE
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "done"

        auto_unlock_phases(plan)
        assert plan.phases[1].status == PhaseStatus.PENDING
        # Second call: p2 is now PENDING, not LOCKED → nothing to unlock
        unlocked = auto_unlock_phases(plan)
        assert unlocked == []


class TestClaimStep:
    def test_claim_pending(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        assert plan.phases[0].steps[0].status == StepStatus.CLAIMED
        assert plan.phases[0].steps[0].claimed_by == "agent-1"
        assert plan.phases[0].steps[0].claimed_at is not None

    def test_claim_updates_phase_to_in_progress(self):
        plan = _simple_plan()
        assert plan.phases[0].status == PhaseStatus.PENDING
        plan, _ = claim_step(plan, "s1", "agent-1")
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS

    def test_claim_rejected_step(self):
        plan = _simple_plan()
        plan.phases[0].steps[0].status = StepStatus.REJECTED
        plan.phases[0].steps[0].rejection_reason = "Bad"
        plan, _ = claim_step(plan, "s1", "agent-2")
        assert plan.phases[0].steps[0].status == StepStatus.CLAIMED

    def test_claim_already_claimed_fails(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        with pytest.raises(PlanError, match="cannot be claimed"):
            claim_step(plan, "s1", "agent-2")

    def test_claim_done_step_fails(self):
        plan = _simple_plan()
        plan.phases[0].steps[0].status = StepStatus.DONE
        plan.phases[0].steps[0].evidence = "done"
        with pytest.raises(PlanError, match="cannot be claimed"):
            claim_step(plan, "s1", "agent-1")

    def test_claim_blocked_step_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="unmet dependencies"):
            claim_step(plan, "s2", "agent-1")  # s2 depends on s1

    def test_claim_in_locked_phase_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="inactive phase"):
            claim_step(plan, "s4", "agent-1")  # p2 is locked

    def test_claim_nonexistent_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="not found"):
            claim_step(plan, "nope", "agent-1")


class TestCompleteStep:
    def test_complete_claimed(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan = complete_step(plan, "s1", "commit abc")
        assert plan.phases[0].steps[0].status == StepStatus.DONE
        assert plan.phases[0].steps[0].evidence == "commit abc"

    def test_complete_unclaimed_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="must be claimed"):
            complete_step(plan, "s1", "evidence")

    def test_complete_all_steps_completes_phase(self):
        plan = _simple_plan()
        # Complete s1 and s3 (no deps), then s2 (depends on s1)
        plan, _ = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        plan, _ = claim_step(plan, "s3", "a")
        plan = complete_step(plan, "s3", "e")
        plan, _ = claim_step(plan, "s2", "a")
        plan = complete_step(plan, "s2", "e")
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_complete_cascades_unlock_to_downstream(self):
        """Completing last step in a phase should auto-unlock dependent phases."""
        plan = _simple_plan()
        # p2 depends on p1. Complete all steps in p1.
        plan, _ = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        plan, _ = claim_step(plan, "s3", "a")
        plan = complete_step(plan, "s3", "e")
        plan, _ = claim_step(plan, "s2", "a")
        plan = complete_step(plan, "s2", "e")

        assert plan.phases[0].status == PhaseStatus.DONE
        # BUG: p2 should be auto-unlocked to PENDING, not stay LOCKED
        assert plan.phases[1].status == PhaseStatus.PENDING, (
            "complete_step should cascade unlock downstream phases"
        )


class TestDeferStep:
    def test_defer_claimed(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan = defer_step(plan, "s1")
        assert plan.phases[0].steps[0].status == StepStatus.PENDING
        assert plan.phases[0].steps[0].claimed_by is None
        assert plan.phases[0].steps[0].claimed_at is None

    def test_defer_pending_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="cannot be deferred"):
            defer_step(plan, "s1")


class TestRejectStep:
    def test_reject_done(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan = complete_step(plan, "s1", "evidence")
        plan = reject_step(plan, "s1", "Missing tests", "reviewer-1")
        assert plan.phases[0].steps[0].status == StepStatus.REJECTED
        assert plan.phases[0].steps[0].rejection_reason == "Missing tests"
        assert len(plan.phases[0].steps[0].rejection_history) == 1
        assert plan.phases[0].steps[0].evidence is None

    def test_reject_pending_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="must be done"):
            reject_step(plan, "s1", "reason")

    def test_reject_reverts_phase_done(self):
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(id="s1", name="S1", status=StepStatus.DONE, evidence="e"),
                    ],
                )
            ],
        )
        plan = reject_step(plan, "s1", "Bad")
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS


class TestSkipStep:
    def test_skip_pending(self):
        plan = _simple_plan()
        plan = skip_step(plan, "s1", "superseded")
        assert plan.phases[0].steps[0].status == StepStatus.SKIPPED
        assert plan.phases[0].steps[0].skipped_reason == "superseded"

    def test_skip_claimed(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan = skip_step(plan, "s1", "deprioritized")
        assert plan.phases[0].steps[0].status == StepStatus.SKIPPED

    def test_skip_done_fails(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        with pytest.raises(PlanError, match="cannot be skipped"):
            skip_step(plan, "s1", "irrelevant")

    def test_skip_invalid_reason(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="Invalid skip reason"):
            skip_step(plan, "s1", "not a valid reason")

    def test_skip_all_valid_reasons(self):
        """All four enum values are accepted."""
        from vectl.models import SkipReason

        for reason in SkipReason:
            plan = _simple_plan()
            plan = skip_step(plan, "s1", reason.value)
            assert plan.phases[0].steps[0].skipped_reason == reason.value

    def test_skip_all_completes_phase(self):
        plan = _simple_plan()
        for step in plan.phases[0].steps:
            plan = skip_step(plan, step.id, "irrelevant")
        assert plan.phases[0].status == PhaseStatus.DONE


# ---------------------------------------------------------------------------
# Get Claimed Steps (mine)
# ---------------------------------------------------------------------------


class TestGetClaimedSteps:
    def test_returns_claimed_by_agent(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan, _ = claim_step(plan, "s3", "agent-2")
        results = get_claimed_steps(plan, agent="agent-1")
        assert len(results) == 1
        assert results[0][1].id == "s1"

    def test_returns_all_claimed_when_no_agent(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan, _ = claim_step(plan, "s3", "agent-2")
        results = get_claimed_steps(plan)
        assert len(results) == 2

    def test_empty_when_none_claimed(self):
        plan = _simple_plan()
        results = get_claimed_steps(plan, agent="anyone")
        assert results == []

    def test_includes_phase_id(self):
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "a")
        results = get_claimed_steps(plan, agent="a")
        phase_id, step = results[0]
        assert phase_id == "p1"
        assert step.id == "s1"


# ---------------------------------------------------------------------------
# Skip Phase
# ---------------------------------------------------------------------------


class TestSkipPhase:
    def test_skip_all_pending(self):
        """All pending steps become skipped, phase becomes DONE."""
        plan = _simple_plan()
        plan, skipped = skip_phase(plan, "p1", "superseded")
        assert set(skipped) == {"s1", "s2", "s3"}
        for step in plan.phases[0].steps:
            assert step.status == StepStatus.SKIPPED
            assert step.skipped_reason == "superseded"
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_skip_defers_claimed_first(self):
        """Claimed steps are deferred then skipped."""
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "agent-1")
        plan, skipped = skip_phase(plan, "p1", "deprioritized")
        assert "s1" in skipped
        step_s1 = plan.phases[0].steps[0]
        assert step_s1.status == StepStatus.SKIPPED
        assert step_s1.claimed_by is None  # deferred

    def test_skip_preserves_done_steps(self):
        """Already-done steps are left unchanged."""
        plan = _simple_plan()
        plan, _ = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "evidence")
        plan, skipped = skip_phase(plan, "p1", "irrelevant")
        assert "s1" not in skipped
        assert plan.phases[0].steps[0].status == StepStatus.DONE
        assert set(skipped) == {"s2", "s3"}

    def test_skip_mixed_states(self):
        """Phase with done + claimed + pending steps."""
        plan = _simple_plan()
        # s1: done
        plan, _ = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        # s3: claimed
        plan, _ = claim_step(plan, "s3", "b")
        # s2: pending (blocked by s1, but skip doesn't care about deps)
        plan, skipped = skip_phase(plan, "p1", "absorbed")
        assert set(skipped) == {"s2", "s3"}
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_skip_cascades_unlock(self):
        """Skipping all steps in p1 unlocks p2."""
        plan = _simple_plan()
        plan, _ = skip_phase(plan, "p1", "irrelevant")
        assert plan.phases[1].status == PhaseStatus.PENDING  # p2 unlocked

    def test_skip_locked_phase_with_steps_fails(self):
        """Locked phase with steps cannot be skipped without --force."""
        plan = _simple_plan()
        with pytest.raises(PlanError, match="locked"):
            skip_phase(plan, "p2", "irrelevant")

    def test_skip_locked_phase_with_steps_force_allowed(self):
        """Locked phase with steps can be skipped with force=True."""
        plan = _simple_plan()
        plan, skipped = skip_phase(plan, "p2", "irrelevant", force=True)
        assert skipped == ["s4"]
        assert plan.phases[1].status == PhaseStatus.DONE

    def test_skip_empty_locked_phase_allowed(self):
        """Empty locked phase can be skipped (lock protects nothing)."""
        plan = _simple_plan()
        # Add an empty locked phase
        plan.phases.append(
            Phase(
                id="p3",
                name="Empty Locked Phase",
                status=PhaseStatus.LOCKED,
                depends_on=["p1"],
                steps=[],
            )
        )
        # Should succeed without force
        plan, skipped = skip_phase(plan, "p3", "superseded")
        assert skipped == []
        p3 = plan.find_phase("p3")
        assert p3 is not None
        assert p3.status == PhaseStatus.DONE

    def test_skip_done_phase_fails(self):
        plan = _simple_plan()
        plan, _ = skip_phase(plan, "p1", "irrelevant")
        with pytest.raises(PlanError, match="already done"):
            skip_phase(plan, "p1", "irrelevant")

    def test_skip_invalid_reason(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="Invalid skip reason"):
            skip_phase(plan, "p1", "bad-reason")

    def test_skip_phase_not_found(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="not found"):
            skip_phase(plan, "nonexistent", "irrelevant")


# ---------------------------------------------------------------------------
# Complete Phase (explicit terminal transition)
# ---------------------------------------------------------------------------


class TestCompletePhase:
    def test_complete_fails_if_any_step_pending(self) -> None:
        plan = _simple_plan()
        with pytest.raises(PlanError, match="non-terminal"):
            complete_phase(plan, "p1", evidence="historical import")

    def test_complete_sets_phase_done_and_unlocks_downstream(self) -> None:
        plan = _simple_plan()
        # Make p1 eligible: all steps terminal
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "e"

        plan, unlocked = complete_phase(plan, "p1", evidence="imported from markdown")
        assert plan.phases[0].status == PhaseStatus.DONE
        assert plan.phases[0].evidence == "imported from markdown"
        assert "p2" in unlocked
        assert plan.phases[1].status == PhaseStatus.PENDING

    def test_complete_requires_deps_done(self) -> None:
        plan = _simple_plan()
        # Make p2 steps terminal but deps not done
        plan.phases[1].steps[0].status = StepStatus.DONE
        plan.phases[1].steps[0].evidence = "e"

        with pytest.raises(PlanError, match="depends_on"):
            complete_phase(plan, "p2", evidence="x")

    def test_complete_locked_phase_when_deps_done(self) -> None:
        """LOCKED status does not block completion when deps are DONE."""
        plan = _simple_plan()
        # p1: done + terminal steps
        plan.phases[0].status = PhaseStatus.DONE
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "e"

        # p2 remains LOCKED in state machine until auto_unlock is run
        assert plan.phases[1].status == PhaseStatus.LOCKED
        plan.phases[1].steps[0].status = StepStatus.SKIPPED
        plan.phases[1].steps[0].skipped_reason = "irrelevant"

        plan, _ = complete_phase(plan, "p2", evidence="import")
        assert plan.phases[1].status == PhaseStatus.DONE

    def test_complete_empty_evidence_rejected(self) -> None:
        plan = _simple_plan()
        for s in plan.phases[0].steps:
            s.status = StepStatus.DONE
            s.evidence = "e"
        with pytest.raises(PlanError, match="evidence"):
            complete_phase(plan, "p1", evidence="")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _search_plan() -> Plan:
    """Plan with searchable content across phases and steps."""
    return Plan(
        project="test",
        phases=[
            Phase(
                id="core",
                name="Core Logic",
                context="Build the DAG validation engine",
                gate="all tests pass",
                steps=[
                    Step(
                        id="core.models",
                        name="Pydantic Models",
                        description="Define Step and Phase models",
                    ),
                    Step(
                        id="core.dag", name="DAG Validation", description="Cycle detection with DFS"
                    ),
                ],
            ),
            Phase(
                id="cli",
                name="CLI Commands",
                context="Typer-based CLI",
                depends_on=["core"],
                status=PhaseStatus.LOCKED,
                steps=[
                    Step(id="cli.next", name="Next Command", description="Show claimable steps"),
                    Step(
                        id="cli.claim",
                        name="Claim Command",
                        description="Claim a step for DAG work",
                    ),
                ],
            ),
        ],
    )


class TestSearchPlan:
    def test_basic_substring_match(self):
        plan = _search_plan()
        results = search_plan(plan, "DAG")
        assert len(results) == 3  # core.context, core.dag desc, cli.claim desc

    def test_case_insensitive(self):
        plan = _search_plan()
        results = search_plan(plan, "dag")
        assert len(results) == 3

    def test_phase_filter(self):
        plan = _search_plan()
        results = search_plan(plan, "DAG", phase_id="core")
        assert len(results) == 2  # core.context, core.dag desc
        assert all(m.phase_id == "core" for m in results)

    def test_regex_mode(self):
        plan = _search_plan()
        results = search_plan(plan, r"D[AF]G", use_regex=True)
        assert len(results) == 3

    def test_invalid_regex(self):
        plan = _search_plan()
        with pytest.raises(PlanError, match="Invalid regex"):
            search_plan(plan, "[invalid", use_regex=True)

    def test_no_matches(self):
        plan = _search_plan()
        results = search_plan(plan, "nonexistent-term-xyz")
        assert len(results) == 0

    def test_phase_level_match(self):
        plan = _search_plan()
        results = search_plan(plan, "validation engine")
        assert len(results) == 1
        assert results[0].step_id is None
        assert results[0].field == "context"

    def test_match_in_gate(self):
        plan = _search_plan()
        results = search_plan(plan, "all tests")
        assert len(results) == 1
        assert results[0].field == "gate"

    def test_match_snippet_extraction(self):
        plan = _search_plan()
        results = search_plan(plan, "Pydantic")
        assert len(results) == 1
        assert "Pydantic" in results[0].snippet


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


class TestClipboardWrite:
    def test_basic_write(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent-1", "Handoff note", "Here's the design")
        assert plan.clipboard is not None
        assert plan.clipboard.author == "agent-1"
        assert plan.clipboard.summary == "Handoff note"
        assert plan.clipboard.content == "Here's the design"

    def test_overwrite_existing(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent-1", "First", "Content 1")
        plan = clipboard_write(plan, "agent-2", "Second", "Content 2")
        cb = plan.clipboard
        assert cb is not None
        assert cb.author == "agent-2"
        assert cb.summary == "Second"

    def test_summary_truncation(self):
        plan = Plan(project="test")
        long_summary = "x" * 100
        plan = clipboard_write(plan, "agent", long_summary, "content")
        cb = plan.clipboard
        assert cb is not None
        assert len(cb.summary) == CLIPBOARD_SUMMARY_MAX
        assert cb.summary.endswith("…")

    def test_content_limit_exceeded(self):
        plan = Plan(project="test")
        long_content = "x" * (CLIPBOARD_CONTENT_MAX + 1)
        with pytest.raises(PlanError, match="exceeds.*char limit"):
            clipboard_write(plan, "agent", "Summary", long_content)

    def test_empty_content_rejected(self):
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="cannot be empty"):
            clipboard_write(plan, "agent", "Summary", "")

    def test_whitespace_only_content_rejected(self):
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="whitespace-only"):
            clipboard_write(plan, "agent", "Summary", "   \n\t  ")

    def test_empty_author_rejected(self):
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="author cannot be empty"):
            clipboard_write(plan, "", "Summary", "Content")

    def test_whitespace_only_author_rejected(self):
        plan = Plan(project="test")
        with pytest.raises(PlanError, match="author cannot be empty"):
            clipboard_write(plan, "   ", "Summary", "Content")

    def test_custom_ttl(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent", "Summary", "Content", ttl=48)
        # Verify expires_at is roughly 48 hours in the future
        from vectl.core import _clipboard_expired

        cb = plan.clipboard
        assert cb is not None
        assert not _clipboard_expired(cb)

    def test_default_ttl_is_24h(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent", "Summary", "Content")
        # Default TTL is 24 hours
        from vectl.core import CLIPBOARD_TTL_DEFAULT_HOURS

        assert CLIPBOARD_TTL_DEFAULT_HOURS == 24


class TestClipboardRead:
    def test_read_basic(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent-1", "Summary", "Content")
        assert plan.clipboard is not None
        assert not _clipboard_expired(plan.clipboard)
        assert plan.clipboard.author == "agent-1"

    def test_read_empty_clipboard(self):
        plan = Plan(project="test")
        assert plan.clipboard is None

    def test_read_expired_clipboard(self):
        plan = Plan(project="test")
        # Create expired clipboard manually
        past = (
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        plan.clipboard = Clipboard(
            author="agent",
            summary="Old",
            content="Expired content",
            written_at=past,
            expires_at=past,
        )
        assert _clipboard_expired(plan.clipboard)  # Expired


class TestClipboardClear:
    def test_clear_basic(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent", "Summary", "Content")
        assert plan.clipboard is not None
        plan = clipboard_clear(plan)
        assert plan.clipboard is None

    def test_clear_already_empty(self):
        plan = Plan(project="test")
        plan = clipboard_clear(plan)
        assert plan.clipboard is None


class TestClipboardExpiry:
    def test_not_expired(self):
        plan = Plan(project="test")
        plan = clipboard_write(plan, "agent", "Summary", "Content", ttl=24)
        from vectl.core import _clipboard_expired

        cb = plan.clipboard
        assert cb is not None
        assert not _clipboard_expired(cb)

    def test_expired(self):
        plan = Plan(project="test")
        # Create expired clipboard
        past = (
            (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        plan.clipboard = Clipboard(
            author="agent",
            summary="Old",
            content="Expired",
            written_at=past,
            expires_at=past,
        )
        from vectl.core import _clipboard_expired

        cb = plan.clipboard
        assert cb is not None
        assert _clipboard_expired(cb)

    def test_expiry_boundary(self):
        """Test clipboard just expired vs not yet expired."""
        from vectl.core import _clipboard_expired

        now = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now.isoformat().replace("+00:00", "Z")

        # Just expired (1 second ago)
        just_expired = (now - datetime.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        cb_expired = Clipboard(
            author="a", summary="s", content="c", written_at=now_iso, expires_at=just_expired
        )
        assert _clipboard_expired(cb_expired)

        # Not yet expired (1 second in future)
        not_yet = (now + datetime.timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        cb_valid = Clipboard(
            author="a", summary="s", content="c", written_at=now_iso, expires_at=not_yet
        )
        assert not _clipboard_expired(cb_valid)


# ---------------------------------------------------------------------------
# RFC: docs/RFC-affinity.md — Affinity Tests
# ---------------------------------------------------------------------------


class TestAffinityClaim:
    """Tests for agent affinity enforcement during claim."""

    def _plan_with_step(
        self,
        agent: str | None = None,
        affinity: AffinityMode | None = None,
        plan_default: AffinityMode = AffinityMode.SUGGESTED,
    ) -> Plan:
        """Helper to create a plan with a single step."""
        step = Step(id="s1", name="Step 1", agent=agent, affinity=affinity)
        phase = Phase(id="p1", name="Phase 1", status=PhaseStatus.PENDING, steps=[step])
        return Plan(project="test", default_affinity=plan_default, phases=[phase])

    def test_claim_no_agent_field_no_check(self):
        """No agent field on step -> no affinity check."""
        plan = self._plan_with_step(agent=None)
        plan, result = claim_step(plan, "s1", "any-agent")
        assert result.affinity_warning is False
        assert result.warning_message is None

    def test_claim_agent_matches_no_warning(self):
        """Agent matches step.agent -> no warning."""
        plan = self._plan_with_step(agent="python-engineer")
        plan, result = claim_step(plan, "s1", "python-engineer")
        assert result.affinity_warning is False
        assert result.warning_message is None

    def test_claim_suggested_mismatch_warns(self):
        """Suggested affinity mismatch -> warn but allow."""
        plan = self._plan_with_step(agent="blind-tester", affinity=AffinityMode.SUGGESTED)
        plan, result = claim_step(plan, "s1", "python-engineer")
        assert result.affinity_warning is True
        msg = result.warning_message
        assert msg is not None
        assert "blind-tester" in msg
        assert "python-engineer" in msg

    def test_claim_exclusive_mismatch_rejects(self):
        """Exclusive affinity mismatch -> reject."""
        plan = self._plan_with_step(agent="blind-tester", affinity=AffinityMode.EXCLUSIVE)
        with pytest.raises(AffinityError, match="exclusive affinity"):
            claim_step(plan, "s1", "python-engineer")

    def test_claim_exclusive_mismatch_force_allows(self):
        """Exclusive affinity mismatch with force -> allow + audit trail."""
        plan = self._plan_with_step(agent="blind-tester", affinity=AffinityMode.EXCLUSIVE)
        plan, result = claim_step(plan, "s1", "python-engineer", force=True)
        assert result.affinity_override is True
        msg = result.warning_message
        assert msg is not None
        assert "override" in msg.lower()
        # Check step has override fields
        step = plan.phases[0].steps[0]
        assert step.affinity_override is True
        assert step.affinity_override_by == "python-engineer"
        assert step.affinity_override_at is not None

    def test_claim_uses_plan_default_affinity(self):
        """Step without affinity uses plan.default_affinity."""
        # Plan default is EXCLUSIVE
        plan = self._plan_with_step(
            agent="gate-reviewer", affinity=None, plan_default=AffinityMode.EXCLUSIVE
        )
        # Step has no affinity set, should use plan default -> EXCLUSIVE
        with pytest.raises(AffinityError, match="exclusive affinity"):
            claim_step(plan, "s1", "python-engineer")

    def test_claim_step_affinity_overrides_plan_default(self):
        """Step affinity overrides plan default."""
        # Plan default is EXCLUSIVE, but step is SUGGESTED
        plan = self._plan_with_step(
            agent="blind-tester",
            affinity=AffinityMode.SUGGESTED,
            plan_default=AffinityMode.EXCLUSIVE,
        )
        # Should warn, not reject (step affinity wins)
        plan, result = claim_step(plan, "s1", "python-engineer")
        assert result.affinity_warning is True
        assert result.affinity_override is False

    def test_claim_exclusive_matches_allows(self):
        """Exclusive affinity with matching agent -> allow."""
        plan = self._plan_with_step(agent="blind-tester", affinity=AffinityMode.EXCLUSIVE)
        plan, result = claim_step(plan, "s1", "blind-tester")
        assert result.affinity_warning is False
        assert result.warning_message is None


# ---------------------------------------------------------------------------
# recalc_lock_status
# ---------------------------------------------------------------------------


class TestRecalcLockStatus:
    """Unit tests for recalc_lock_status — all 4 status transition rules."""

    # ------------------------------------------------------------------
    # Rule 1: DONE phases stay DONE
    # ------------------------------------------------------------------

    def test_rule1_done_phase_not_changed(self) -> None:
        """DONE phase with unmet deps is never regressed."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.DONE,
                    depends_on=["p1"],  # dep is PENDING, not DONE
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" not in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.DONE

    def test_rule1_done_phase_without_deps_not_changed(self) -> None:
        """DONE phase with no dependencies is untouched."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.status == PhaseStatus.DONE

    def test_rule1_done_phase_with_done_deps_stays_done(self) -> None:
        """DONE phase whose dependencies are also DONE is never regressed."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.DONE,
                    depends_on=["p1"],  # dep is DONE
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" not in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.DONE

    # ------------------------------------------------------------------
    # Rule 2: IN_PROGRESS phases stay IN_PROGRESS
    # ------------------------------------------------------------------

    def test_rule2_in_progress_with_unmet_deps_not_locked(self) -> None:
        """IN_PROGRESS phase with unsatisfied deps is not interrupted."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.IN_PROGRESS,
                    depends_on=["p1"],  # dep not done
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" not in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.IN_PROGRESS

    def test_rule2_in_progress_with_met_deps_not_changed(self) -> None:
        """IN_PROGRESS phase with satisfied deps is also left alone."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.IN_PROGRESS,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.IN_PROGRESS

    # ------------------------------------------------------------------
    # Rule 3: PENDING with unmet deps → LOCKED
    # ------------------------------------------------------------------

    def test_rule3_pending_with_unmet_dep_becomes_locked(self) -> None:
        """PENDING phase whose dependency is not DONE is locked."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    def test_rule3_pending_with_in_progress_dep_becomes_locked(self) -> None:
        """PENDING phase whose dependency is IN_PROGRESS (not DONE) is locked."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.IN_PROGRESS),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    def test_rule3_pending_with_all_deps_done_not_locked(self) -> None:
        """PENDING phase with all deps DONE is left PENDING (rule 3 does not fire)."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_rule3_pending_without_deps_not_locked(self) -> None:
        """PENDING phase with no dependencies is never locked by this function."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_rule3_partially_unmet_deps_becomes_locked(self) -> None:
        """PENDING phase is locked even when only one of multiple deps is unmet."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(id="p2", name="P2", status=PhaseStatus.PENDING),
                Phase(
                    id="p3",
                    name="P3",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1", "p2"],  # p2 is not DONE
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p3" in changed
        ph = plan.find_phase("p3")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    # ------------------------------------------------------------------
    # Rule 4: LOCKED with all deps DONE → PENDING
    # ------------------------------------------------------------------

    def test_rule4_locked_with_all_deps_done_becomes_pending(self) -> None:
        """LOCKED phase whose deps are all DONE is unlocked to PENDING."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p2" in changed
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_rule4_locked_with_unmet_deps_stays_locked(self) -> None:
        """LOCKED phase with unmet deps stays LOCKED."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    def test_rule4_locked_with_multiple_deps_all_done_becomes_pending(self) -> None:
        """LOCKED phase unlocks only when ALL multiple deps are DONE."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(id="p2", name="P2", status=PhaseStatus.DONE),
                Phase(
                    id="p3",
                    name="P3",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1", "p2"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert "p3" in changed
        ph = plan.find_phase("p3")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_rule4_locked_with_one_unmet_of_multiple_deps_stays_locked(self) -> None:
        """LOCKED phase with at least one unmet dep stays LOCKED."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(id="p2", name="P2", status=PhaseStatus.PENDING),
                Phase(
                    id="p3",
                    name="P3",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1", "p2"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []
        ph = plan.find_phase("p3")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    # ------------------------------------------------------------------
    # Return value: list of changed phase IDs
    # ------------------------------------------------------------------

    def test_returns_empty_list_when_no_changes(self) -> None:
        """Returns an empty list when no phase statuses are changed."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.LOCKED,
                    depends_on=["p1"],
                ),
            ],
        )
        # p2 should unlock — call once to apply
        recalc_lock_status(plan)
        # Second call: state is already consistent, no changes
        changed_again = recalc_lock_status(plan)
        assert changed_again == []

    def test_returns_all_changed_phase_ids_in_plan_order(self) -> None:
        """All changed phases are reported, in plan order."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.PENDING),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
                Phase(
                    id="p3",
                    name="P3",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        # p2 and p3 should both be locked; p1 has no deps so stays PENDING
        assert changed == ["p2", "p3"]
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING
        ph = plan.find_phase("p2")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED
        ph = plan.find_phase("p3")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    def test_idempotent_on_already_consistent_plan(self) -> None:
        """A plan already in consistent state produces no changes."""
        plan = Plan(
            project="test",
            phases=[
                Phase(id="p1", name="P1", status=PhaseStatus.DONE),
                Phase(
                    id="p2",
                    name="P2",
                    status=PhaseStatus.PENDING,
                    depends_on=["p1"],
                ),
            ],
        )
        changed = recalc_lock_status(plan)
        assert changed == []

    # ------------------------------------------------------------------
    # Circular dependency: must not hang or raise
    # ------------------------------------------------------------------

    def test_circular_dep_does_not_loop(self) -> None:
        """Mutual dependency (A depends on B, B depends on A) must not cause
        an infinite loop.  recalc_lock_status iterates the phase list once
        using a static snapshot of DONE ids, so it terminates in O(N).
        Both phases have unmet deps (neither is DONE) so each PENDING phase
        becomes LOCKED; the function returns a list (never hangs).
        """
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="phA",
                    name="Phase A",
                    status=PhaseStatus.PENDING,
                    depends_on=["phB"],
                ),
                Phase(
                    id="phB",
                    name="Phase B",
                    status=PhaseStatus.PENDING,
                    depends_on=["phA"],
                ),
            ],
        )
        # Must complete (no hang) and return a list
        changed = recalc_lock_status(plan)
        assert isinstance(changed, list)
        # Both phases have unmet deps → both are locked
        ph_a = plan.find_phase("phA")
        ph_b = plan.find_phase("phB")
        assert ph_a is not None and ph_a.status == PhaseStatus.LOCKED
        assert ph_b is not None and ph_b.status == PhaseStatus.LOCKED
        assert set(changed) == {"phA", "phB"}
