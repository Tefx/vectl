"""Property-based stateful tests for the plan state machine.

Golden property: any sequence of valid operations produces a plan
that passes validate_plan() — no matter the order.

Uses Hypothesis RuleBasedStateMachine to generate random sequences of
claim/complete/defer/reject/skip on a fixed multi-phase plan with step
dependencies.
"""

from __future__ import annotations

import copy

from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, rule

from vectl.core import (
    auto_unlock_phases,
    claim_step,
    complete_step,
    defer_step,
    get_claimed_steps,
    get_next_steps,
    reject_step,
    skip_step,
    validate_plan,
)
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    Step,
    StepStatus,
)


def _make_test_plan() -> Plan:
    """Create a fixed multi-phase plan with step deps for property testing.

    Structure:
        alpha (PENDING, no deps):
            a.s1 (no deps)
            a.s2 (depends on a.s1)
            a.s3 (no deps)
        beta (LOCKED, depends on alpha):
            b.s1 (no deps)
            b.s2 (no deps)
        gamma (LOCKED, depends on beta):
            g.s1 (no deps)
    """
    return Plan(
        project="property-test",
        phases=[
            Phase(
                id="alpha",
                name="Alpha",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="a.s1", name="Alpha Step 1"),
                    Step(id="a.s2", name="Alpha Step 2", depends_on=["a.s1"]),
                    Step(id="a.s3", name="Alpha Step 3"),
                ],
            ),
            Phase(
                id="beta",
                name="Beta",
                status=PhaseStatus.LOCKED,
                depends_on=["alpha"],
                steps=[
                    Step(id="b.s1", name="Beta Step 1"),
                    Step(id="b.s2", name="Beta Step 2"),
                ],
            ),
            Phase(
                id="gamma",
                name="Gamma",
                status=PhaseStatus.LOCKED,
                depends_on=["beta"],
                steps=[
                    Step(id="g.s1", name="Gamma Step 1"),
                ],
            ),
        ],
    )


class PlanStateMachine(RuleBasedStateMachine):
    """Stateful test: random valid operations always produce a valid plan."""

    def __init__(self) -> None:
        super().__init__()
        self.plan = _make_test_plan()
        # Verify starting state is valid
        errors = validate_plan(self.plan)
        assert not errors, f"Initial plan invalid: {errors}"

    # -- Bundles to track step IDs in specific states --
    claimed_steps = Bundle("claimed_steps")
    done_steps = Bundle("done_steps")

    def _check_invariants(self) -> None:
        """Assert the golden property + structural invariants."""
        errors = validate_plan(self.plan)
        assert not errors, f"Plan invalid after operation: {errors}"

        for phase in self.plan.phases:
            if phase.status == PhaseStatus.LOCKED:
                for step in phase.steps:
                    assert step.status in (
                        StepStatus.PENDING,
                        StepStatus.SKIPPED,
                    ), f"Step '{step.id}' is {step.status.value} in LOCKED phase '{phase.id}'"
            if phase.status == PhaseStatus.DONE:
                for step in phase.steps:
                    assert step.status in (
                        StepStatus.DONE,
                        StepStatus.SKIPPED,
                    ), f"Step '{step.id}' is {step.status.value} in DONE phase '{phase.id}'"

    @rule(target=claimed_steps)
    def claim_available(self) -> str:
        """Claim a random available step."""
        available = get_next_steps(self.plan)
        if not available:
            # Nothing claimable — return a sentinel that won't match bundles
            return "__none__"
        # Pick first available (Hypothesis explores order via other rules)
        step = available[0]
        self.plan = claim_step(self.plan, step.id, "test-agent")
        self._check_invariants()
        return step.id

    @rule(target=done_steps, step_id=claimed_steps)
    def complete_claimed(self, step_id: str) -> str:
        """Complete a previously claimed step."""
        if step_id == "__none__":
            return "__none__"
        found = self.plan.find_step(step_id)
        if found is None or found[1].status != StepStatus.CLAIMED:
            return "__none__"
        self.plan = complete_step(self.plan, step_id, "test evidence")
        self._check_invariants()
        return step_id

    @rule(step_id=claimed_steps)
    def defer_claimed(self, step_id: str) -> None:
        """Defer a previously claimed step back to pending."""
        if step_id == "__none__":
            return
        found = self.plan.find_step(step_id)
        if found is None or found[1].status != StepStatus.CLAIMED:
            return
        self.plan = defer_step(self.plan, step_id)
        self._check_invariants()

    @rule(step_id=done_steps)
    def reject_done(self, step_id: str) -> None:
        """Reject a previously completed step."""
        if step_id == "__none__":
            return
        found = self.plan.find_step(step_id)
        if found is None or found[1].status != StepStatus.DONE:
            return
        self.plan = reject_step(self.plan, step_id, "needs rework", "reviewer")
        self._check_invariants()

    @rule()
    def skip_available(self) -> None:
        """Skip a random available step."""
        available = get_next_steps(self.plan)
        if not available:
            return
        step = available[0]
        self.plan = skip_step(self.plan, step.id, "superseded")
        self._check_invariants()

    @rule()
    def claim_last_available(self) -> None:
        """Claim the last available step (different from first)."""
        available = get_next_steps(self.plan)
        if len(available) < 2:
            return
        step = available[-1]
        self.plan = claim_step(self.plan, step.id, "test-agent-2")
        self._check_invariants()

    @rule()
    def verify_claimed_steps_query(self) -> None:
        """Verify get_claimed_steps returns consistent results."""
        claimed = get_claimed_steps(self.plan)
        for phase_id, step in claimed:
            assert step.status == StepStatus.CLAIMED
            phase = self.plan.find_phase(phase_id)
            assert phase is not None
            assert step in phase.steps

    @rule()
    def verify_next_steps_deps_satisfied(self) -> None:
        """Verify all steps returned by get_next_steps have deps satisfied."""
        available = get_next_steps(self.plan)
        for step in available:
            assert step.status in (StepStatus.PENDING, StepStatus.REJECTED)
            # Find the phase containing this step
            found = self.plan.find_step(step.id)
            assert found is not None
            phase, _ = found
            done_ids = {
                s.id for s in phase.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)
            }
            for dep in step.depends_on:
                assert dep in done_ids, (
                    f"Step '{step.id}' returned by get_next_steps but dep '{dep}' not satisfied"
                )


# Hypothesis settings: enough examples to exercise the state machine thoroughly
TestPlanStateMachine = PlanStateMachine.TestCase
TestPlanStateMachine.settings = settings(
    max_examples=200,
    stateful_step_count=30,
)
