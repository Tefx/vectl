"""Tests for core.4: State Machine & Query Logic."""

import pytest

from vectl.core import (
    auto_unlock_phases,
    claim_step,
    complete_phase,
    complete_step,
    defer_step,
    get_claimed_steps,
    get_next_steps,
    reject_step,
    search_plan,
    skip_phase,
    skip_step,
)
from vectl.models import (
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
        plan = claim_step(plan, "s1", "agent-1")
        assert plan.phases[0].steps[0].status == StepStatus.CLAIMED
        assert plan.phases[0].steps[0].claimed_by == "agent-1"
        assert plan.phases[0].steps[0].claimed_at is not None

    def test_claim_updates_phase_to_in_progress(self):
        plan = _simple_plan()
        assert plan.phases[0].status == PhaseStatus.PENDING
        plan = claim_step(plan, "s1", "agent-1")
        assert plan.phases[0].status == PhaseStatus.IN_PROGRESS

    def test_claim_rejected_step(self):
        plan = _simple_plan()
        plan.phases[0].steps[0].status = StepStatus.REJECTED
        plan.phases[0].steps[0].rejection_reason = "Bad"
        plan = claim_step(plan, "s1", "agent-2")
        assert plan.phases[0].steps[0].status == StepStatus.CLAIMED

    def test_claim_already_claimed_fails(self):
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "agent-1")
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
        plan = claim_step(plan, "s1", "agent-1")
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
        plan = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        plan = claim_step(plan, "s3", "a")
        plan = complete_step(plan, "s3", "e")
        plan = claim_step(plan, "s2", "a")
        plan = complete_step(plan, "s2", "e")
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_complete_cascades_unlock_to_downstream(self):
        """Completing last step in a phase should auto-unlock dependent phases."""
        plan = _simple_plan()
        # p2 depends on p1. Complete all steps in p1.
        plan = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        plan = claim_step(plan, "s3", "a")
        plan = complete_step(plan, "s3", "e")
        plan = claim_step(plan, "s2", "a")
        plan = complete_step(plan, "s2", "e")

        assert plan.phases[0].status == PhaseStatus.DONE
        # BUG: p2 should be auto-unlocked to PENDING, not stay LOCKED
        assert plan.phases[1].status == PhaseStatus.PENDING, (
            "complete_step should cascade unlock downstream phases"
        )


class TestDeferStep:
    def test_defer_claimed(self):
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "agent-1")
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
        plan = claim_step(plan, "s1", "agent-1")
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
        plan = claim_step(plan, "s1", "agent-1")
        plan = skip_step(plan, "s1", "deprioritized")
        assert plan.phases[0].steps[0].status == StepStatus.SKIPPED

    def test_skip_done_fails(self):
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "a")
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
        plan = claim_step(plan, "s1", "agent-1")
        plan = claim_step(plan, "s3", "agent-2")
        results = get_claimed_steps(plan, agent="agent-1")
        assert len(results) == 1
        assert results[0][1].id == "s1"

    def test_returns_all_claimed_when_no_agent(self):
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "agent-1")
        plan = claim_step(plan, "s3", "agent-2")
        results = get_claimed_steps(plan)
        assert len(results) == 2

    def test_empty_when_none_claimed(self):
        plan = _simple_plan()
        results = get_claimed_steps(plan, agent="anyone")
        assert results == []

    def test_includes_phase_id(self):
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "a")
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
        plan = claim_step(plan, "s1", "agent-1")
        plan, skipped = skip_phase(plan, "p1", "deprioritized")
        assert "s1" in skipped
        step_s1 = plan.phases[0].steps[0]
        assert step_s1.status == StepStatus.SKIPPED
        assert step_s1.claimed_by is None  # deferred

    def test_skip_preserves_done_steps(self):
        """Already-done steps are left unchanged."""
        plan = _simple_plan()
        plan = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "evidence")
        plan, skipped = skip_phase(plan, "p1", "irrelevant")
        assert "s1" not in skipped
        assert plan.phases[0].steps[0].status == StepStatus.DONE
        assert set(skipped) == {"s2", "s3"}

    def test_skip_mixed_states(self):
        """Phase with done + claimed + pending steps."""
        plan = _simple_plan()
        # s1: done
        plan = claim_step(plan, "s1", "a")
        plan = complete_step(plan, "s1", "e")
        # s3: claimed
        plan = claim_step(plan, "s3", "b")
        # s2: pending (blocked by s1, but skip doesn't care about deps)
        plan, skipped = skip_phase(plan, "p1", "absorbed")
        assert set(skipped) == {"s2", "s3"}
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_skip_cascades_unlock(self):
        """Skipping all steps in p1 unlocks p2."""
        plan = _simple_plan()
        plan, _ = skip_phase(plan, "p1", "irrelevant")
        assert plan.phases[1].status == PhaseStatus.PENDING  # p2 unlocked

    def test_skip_locked_phase_fails(self):
        plan = _simple_plan()
        with pytest.raises(PlanError, match="locked"):
            skip_phase(plan, "p2", "irrelevant")

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
