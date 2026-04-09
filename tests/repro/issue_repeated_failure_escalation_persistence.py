"""Reproduction: Issue - repeated-failure pending-escalation persistence.

Expected: Per RFC-vectl-decide-advisor-refresh.md:
  1. A trigger call hitting escalation_threshold produces
     status=blocked, reason_code=repeated_failures, and an escalate action.
     The next_state must carry pending_escalations with the blocked step.
  2. A follow-up call with the carried next_state (containing unresolved
     pending_escalations) must remain in blocked/repeated_failures — it must
     NOT regress to done/no_executable_steps.

Actual: (to be proven by running this script — the previous implementation
         regressed on follow-up calls, producing done/no_executable_steps
         instead of maintaining the blocked state.)

Spec refs:
  - RFC section 6.3: repeated_failures -> blocked status mapping
  - RFC section 6.4: preserve failure_count, allow escalation recommendation,
    but keep escalation as a non-terminal blocker
  - RFC section 7.2.1: next_state includes pending_escalations as full replacement
  - DecideState docstring: pending_escalations "remain until the caller clears
    them via reconciliation or a later success closes the escalation"
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

from vectl.decide import ESCALATION_THRESHOLD, decide
from vectl.io import save_plan
from vectl.models import (
    CompletedResult,
    Phase,
    PhaseStatus,
    Plan,
    Step,
    StepStatus,
)


# ---------------------------------------------------------------------------
# Helpers (spec-derived fixtures only)
# ---------------------------------------------------------------------------


def _fresh_advisor_state() -> dict[str, object]:
    """RFC 7.1: minimal advisor_state shape."""
    return {
        "completion_times": {},
        "session_registry": {},
        "session_runner_registry": {},
        "failure_counts": {},
    }


def _patch_resolve(plan_path: Path):
    """Patch decide module to use our plan file."""
    mod = importlib.import_module("vectl.decide")
    original = mod.resolve_plan_path
    mod.resolve_plan_path = lambda: plan_path
    return original, mod


def _unpatch(mod, original):
    mod.resolve_plan_path = original


# ---------------------------------------------------------------------------
# PROOF 1: Trigger call => blocked/repeated_failures + escalate action
# ---------------------------------------------------------------------------


def test_trigger_call_produces_blocked_and_escalate():
    """Hitting escalation threshold must produce blocked + escalate action.

    RFC 6.3: repeated_failures -> status=blocked
    RFC 6.4: preserve failure memory, expose escalation as action/recommendation
    RFC 7.2.1: next_state carries pending_escalations
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"

        # Plan with one CLAIMED step — it's in-flight but about to fail
        plan = Plan(
            project="escalation-persistence-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Failing verification step",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                            verify="must_green",
                        ),
                    ],
                )
            ],
        )
        save_plan(plan, plan_path)

        original, mod = _patch_resolve(plan_path)
        try:
            state = _fresh_advisor_state()

            # Drive failures until we hit the escalation threshold
            for i in range(ESCALATION_THRESHOLD):
                completed = [
                    CompletedResult(
                        step_id="s1",
                        task_id=f"exec-{i}",
                        runner="claude",
                        status="FAIL",
                        output_summary=f"Failure {i}",
                    ),
                ]
                output = decide(
                    running_tasks=[],
                    completed_results=completed,
                    max_parallelism=5,
                    advisor_state=state,
                )
                state = output.next_state

            # PROOF 1a: status must be blocked
            assert output.status == "blocked", (
                f"ISSUE: After {ESCALATION_THRESHOLD} failures, status must be 'blocked', "
                f"got '{output.status}'. RFC 6.3 requires repeated_failures -> blocked."
            )

            # PROOF 1b: reason_code must be repeated_failures
            assert output.reason_code == "repeated_failures", (
                f"ISSUE: After {ESCALATION_THRESHOLD} failures, reason_code must be "
                f"'repeated_failures', got '{output.reason_code}'. RFC 6.3 mapping."
            )

            # PROOF 1c: An escalate action must be emitted for the failing step
            escalate_actions = [
                a for a in output.actions if a.action == "escalate" and a.step_id == "s1"
            ]
            assert escalate_actions, (
                f"ISSUE: No escalate action for 's1' after {ESCALATION_THRESHOLD} failures. "
                f"RFC 6.4 requires escalation recommendation. Actions: "
                f"{[(a.action, a.step_id) for a in output.actions]}"
            )

            # PROOF 1d: next_state must carry pending_escalations for the blocked step
            pending_esc = output.next_state.get("pending_escalations", {})
            assert "s1" in pending_esc, (
                f"ISSUE: next_state.pending_escalations must contain 's1' after escalation, "
                f"got {pending_esc}. DecideState docstring says entries remain until cleared."
            )
            assert pending_esc["s1"] == "repeated_failures", (
                f"ISSUE: pending_escalations['s1'] must be 'repeated_failures', "
                f"got '{pending_esc['s1']}'"
            )

            # All trigger proofs verified
            pass

        finally:
            _unpatch(mod, original)


# ---------------------------------------------------------------------------
# PROOF 2: Follow-up call with carried next_state stays blocked (does not regress)
# ---------------------------------------------------------------------------


def test_followup_call_stays_blocked():
    """Carried next_state with unresolved escalation must keep status=blocked.

    RFC 6.3: blocked/repeated_failures is non-terminal — not done/no_executable_steps.
    DecideState docstring: pending_escalations "remain until the caller clears
    them via reconciliation or a later success closes the escalation."

    The bug was: follow-up call with pending_escalations in advisor_state would
    regress to done/no_executable_steps instead of staying blocked/repeated_failures.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"

        # Same plan — step s1 is still CLAIMED (escalation not resolved)
        plan = Plan(
            project="escalation-persistence-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Failing verification step",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                            verify="must_green",
                        ),
                    ],
                )
            ],
        )
        save_plan(plan, plan_path)

        original, mod = _patch_resolve(plan_path)
        try:
            # === TRIGGER: First build up the escalation ===
            state = _fresh_advisor_state()
            for i in range(ESCALATION_THRESHOLD):
                completed = [
                    CompletedResult(
                        step_id="s1",
                        task_id=f"exec-{i}",
                        runner="claude",
                        status="FAIL",
                        output_summary=f"Failure {i}",
                    ),
                ]
                trigger = decide(
                    running_tasks=[],
                    completed_results=completed,
                    max_parallelism=5,
                    advisor_state=state,
                )
                state = trigger.next_state

            # Now use the trigger's next_state as our carried state
            carried_state = trigger.next_state

            # Follow-up call: no new completions, carry forward next_state from trigger
            followup = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=carried_state,
            )

            # PROOF 2a: follow-up must NOT regress to done
            assert followup.status != "done", (
                f"ISSUE: Follow-up call regressed to status='done' with "
                f"reason_code='{followup.reason_code}'. "
                f"Unresolved escalation must keep status='blocked', not regress to 'done'. "
                f"RFC 6.3 requires blocked/repeated_failures to persist until reconciliation."
            )

            # PROOF 2b: follow-up must NOT regress to no_executable_steps
            assert followup.reason_code != "no_executable_steps", (
                f"ISSUE: Follow-up call regressed to reason_code='no_executable_steps'. "
                f"Unresolved escalation must keep reason_code='repeated_failures'. "
                f"DecideState pending_escalations must persist until explicitly cleared."
            )

            # PROOF 2c: follow-up status must remain blocked
            assert followup.status == "blocked", (
                f"ISSUE: Follow-up must maintain status='blocked' when carrying unresolved "
                f"escalation. Got status='{followup.status}'. "
                f"RFC 6.3: repeated_failures -> blocked, and escalation is non-terminal."
            )

            # PROOF 2d: follow-up reason_code must remain repeated_failures
            assert followup.reason_code == "repeated_failures", (
                f"ISSUE: Follow-up must maintain reason_code='repeated_failures' when carrying "
                f"unresolved escalation. Got reason_code='{followup.reason_code}'."
            )

            # PROOF 2e: no duplicate escalate action on follow-up
            escalate_actions = [a for a in followup.actions if a.action == "escalate"]
            assert len(escalate_actions) == 0, (
                f"ISSUE: Follow-up call should NOT re-emit escalate action. "
                f"The escalation was already signalled in the trigger call. "
                f"Got {len(escalate_actions)} escalate actions. "
                f"Actions: {[(a.action, a.step_id) for a in followup.actions]}"
            )

            # PROOF 2f: next_state must still carry pending_escalations
            pending_esc = followup.next_state.get("pending_escalations", {})
            assert "s1" in pending_esc, (
                f"ISSUE: next_state.pending_escalations must carry 's1' in follow-up. "
                f"Got {pending_esc}. Escalation must persist until reconciliation."
            )

        finally:
            _unpatch(mod, original)


# ---------------------------------------------------------------------------
# PROOF 3: One-shot verification (trigger + follow-up in single assertion chain)
# ---------------------------------------------------------------------------


def test_end_to_end_escalation_persistence():
    """Full trigger -> follow-up chain in one test for clarity.

    This combines proof 1 and proof 2 for a single-command verification.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plan_path = Path(td) / "plan.yaml"

        plan = Plan(
            project="escalation-persistence-e2e",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Critical verification step",
                            status=StepStatus.CLAIMED,
                            claimed_by="agent-1",
                            verify="must_green",
                        ),
                    ],
                )
            ],
        )
        save_plan(plan, plan_path)

        original, mod = _patch_resolve(plan_path)
        try:
            state = _fresh_advisor_state()

            # === TRIGGER: Drive failures up to escalation threshold ===
            for i in range(ESCALATION_THRESHOLD):
                completed = [
                    CompletedResult(
                        step_id="s1",
                        task_id=f"exec-{i}",
                        runner="claude",
                        status="FAIL",
                        output_summary=f"Failure {i}",
                    ),
                ]
                trigger = decide(
                    running_tasks=[],
                    completed_results=completed,
                    max_parallelism=5,
                    advisor_state=state,
                )
                state = trigger.next_state

            # Assertions on trigger output
            assert trigger.status == "blocked", (
                f"TRIGGER: status must be 'blocked', got '{trigger.status}'"
            )
            assert trigger.reason_code == "repeated_failures", (
                f"TRIGGER: reason_code must be 'repeated_failures', got '{trigger.reason_code}'"
            )
            assert any(a.action == "escalate" and a.step_id == "s1" for a in trigger.actions), (
                f"TRIGGER: must have escalate action for s1. "
                f"Actions: {[(a.action, a.step_id) for a in trigger.actions]}"
            )
            assert (
                trigger.next_state.get("pending_escalations", {}).get("s1") == "repeated_failures"
            ), (
                f"TRIGGER: next_state.pending_escalations['s1'] must be 'repeated_failures'. "
                f"Got: {trigger.next_state.get('pending_escalations', {})}"
            )

            # === FOLLOW-UP: Carry next_state, no new completions ===
            followup = decide(
                running_tasks=[],
                completed_results=None,
                max_parallelism=5,
                advisor_state=trigger.next_state,
            )

            # Assertions on follow-up output
            assert followup.status == "blocked", (
                f"FOLLOW-UP: status must remain 'blocked' with unresolved escalation, "
                f"got '{followup.status}'. "
                f"This means the escalation did NOT persist — the system regressed."
            )
            assert followup.reason_code == "repeated_failures", (
                f"FOLLOW-UP: reason_code must remain 'repeated_failures', "
                f"got '{followup.reason_code}'. "
                f"This means the escalation did NOT persist — the system regressed."
            )
            assert (
                followup.next_state.get("pending_escalations", {}).get("s1") == "repeated_failures"
            ), (
                f"FOLLOW-UP: next_state.pending_escalations['s1'] must remain "
                f"'repeated_failures'. Got: {followup.next_state.get('pending_escalations', {})}"
            )
            # Follow-up should NOT re-emit escalate (already signalled)
            followup_escalate = [a for a in followup.actions if a.action == "escalate"]
            assert len(followup_escalate) == 0, (
                f"FOLLOW-UP: should NOT re-emit escalate action. "
                f"Got {len(followup_escalate)} escalate actions."
            )

            print(
                "✓ TRIGGER:  status=blocked, reason_code=repeated_failures, escalate action present"
            )
            print("✓ TRIGGER:  next_state.pending_escalations = {'s1': 'repeated_failures'}")
            print("✓ FOLLOWUP: status=blocked, reason_code=repeated_failures (no regression!)")
            print(
                "✓ FOLLOWUP: next_state.pending_escalations = {'s1': 'repeated_failures'} (persisted!)"
            )
            print("✓ FOLLOWUP: No duplicate escalate action (idempotent)")

        finally:
            _unpatch(mod, original)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main():
    print("=" * 70)
    print("REPRODUCTION: repeated-failure pending-escalation persistence")
    print("=" * 70)
    print()

    failures = 0

    # Proof 1: Trigger call
    print("PROOF 1: Trigger call => blocked/repeated_failures + escalate action")
    print("-" * 70)
    try:
        test_trigger_call_produces_blocked_and_escalate()
        print(
            "  PASS: Trigger call produces blocked/repeated_failures + escalate + pending_escalations"
        )
    except AssertionError as e:
        failures += 1
        print(f"  FAIL: {e}")

    # Proof 2: Follow-up call
    print()
    print("PROOF 2: Follow-up call with carried next_state stays blocked")
    print("-" * 70)
    try:
        test_followup_call_stays_blocked()
        print("  PASS: Follow-up call stays blocked/repeated_failures")
    except AssertionError as e:
        failures += 1
        print(f"  FAIL: {e}")

    # Proof 3: End-to-end
    print()
    print("PROOF 3: End-to-end trigger + follow-up chain")
    print("-" * 70)
    try:
        test_end_to_end_escalation_persistence()
    except AssertionError as e:
        failures += 1
        print(f"  FAIL: {e}")

    print()
    print("=" * 70)
    if failures > 0:
        print(f"RESULT: {failures} FAILURES — repeated-failure escalation persistence is broken")
        raise SystemExit(1)
    else:
        print("RESULT: ALL PASSED — repeated-failure escalation persistence is verified")


if __name__ == "__main__":
    main()
