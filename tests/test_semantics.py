"""Tests for centralized derived semantics (p0-parity.3 + p0-parity.4).

Tests derive_step() and is_step_locked() across all state combinations,
and verifies cross-surface semantic agreement with core.get_next_steps().

Source: p0-parity.3-derived-semantics + p0-parity.4-semantic-parity-tests.
"""

from __future__ import annotations

from vectl.core import get_next_steps
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus
from vectl.semantics import BlockReason, StepDerived, derive_step, is_step_locked


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan(phases: list[Phase]) -> Plan:
    return Plan(project="test-semantics", phases=phases)


def _phase(
    id: str = "ph",
    *,
    status: PhaseStatus = PhaseStatus.PENDING,
    steps: list[Step] | None = None,
    depends_on: list[str] | None = None,
) -> Phase:
    return Phase(
        id=id,
        name=f"Phase {id}",
        status=status,
        steps=steps or [],
        depends_on=depends_on or [],
    )


def _step(
    id: str = "s1",
    *,
    status: StepStatus = StepStatus.PENDING,
    depends_on: list[str] | None = None,
) -> Step:
    kwargs: dict[str, object] = {
        "id": id,
        "name": f"Step {id}",
        "status": status,
        "depends_on": depends_on or [],
    }
    if status == StepStatus.SKIPPED:
        kwargs["skipped_reason"] = "test-skipped"
    elif status == StepStatus.CLAIMED:
        kwargs["claimed_by"] = "test-agent"
    elif status == StepStatus.REJECTED:
        kwargs["rejection_reason"] = "test-rejected"
    return Step(**kwargs)  # type: ignore[arg-type]  # JUSTIFIED: dynamic kwargs from dict


# ---------------------------------------------------------------------------
# derive_step: status-based blocking
# ---------------------------------------------------------------------------


class TestDeriveStepStatus:
    """Non-actionable statuses are never claimable."""

    def test_done_step_not_blocked(self) -> None:
        step = _step(status=StepStatus.DONE)
        phase = _phase(steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert not d.blocked
        assert d.reasons == frozenset()

    def test_skipped_step_not_blocked(self) -> None:
        step = _step(status=StepStatus.SKIPPED)
        phase = _phase(steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert not d.blocked

    def test_claimed_step_blocked_by_status(self) -> None:
        step = _step(status=StepStatus.CLAIMED)
        phase = _phase(steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert d.blocked
        assert BlockReason.STATUS in d.reasons


class TestDeriveStepDeps:
    """Dependency resolution within a phase."""

    def test_pending_no_deps_claimable(self) -> None:
        step = _step(status=StepStatus.PENDING)
        phase = _phase(steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert d.claimable
        assert not d.blocked

    def test_pending_with_met_deps_claimable(self) -> None:
        dep = _step("s1", status=StepStatus.DONE)
        step = _step("s2", depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert d.claimable
        assert not d.blocked

    def test_pending_with_unmet_deps_blocked(self) -> None:
        dep = _step("s1", status=StepStatus.PENDING)
        step = _step("s2", depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert d.blocked
        assert BlockReason.UNMET_DEP in d.reasons
        assert "s1" in d.unmet_deps

    def test_pending_with_missing_dep_blocked(self) -> None:
        step = _step("s1", depends_on=["nonexistent"])
        phase = _phase(steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert d.blocked
        assert BlockReason.MISSING_DEP in d.reasons
        assert "nonexistent" in d.unmet_deps

    def test_skipped_dep_counts_as_met(self) -> None:
        dep = _step("s1", status=StepStatus.SKIPPED)
        step = _step("s2", depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert d.claimable

    def test_rejected_step_with_met_deps_claimable(self) -> None:
        dep = _step("s1", status=StepStatus.DONE)
        step = _step("s2", status=StepStatus.REJECTED, depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert d.claimable

    def test_rejected_step_with_unmet_deps_blocked(self) -> None:
        dep = _step("s1", status=StepStatus.PENDING)
        step = _step("s2", status=StepStatus.REJECTED, depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert BlockReason.UNMET_DEP in d.reasons


class TestDeriveStepPhaseActive:
    """Phase must be active (pending/in_progress/auto-unlockable) for steps to be claimable."""

    def test_locked_phase_blocks_step(self) -> None:
        step = _step(status=StepStatus.PENDING)
        phase = _phase(status=PhaseStatus.LOCKED, steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert BlockReason.PHASE_INACTIVE in d.reasons

    def test_locked_phase_with_met_deps_unlocks(self) -> None:
        """Phase LOCKED with all depends_on DONE = auto-unlock eligible → active."""
        done_phase = _phase("done-ph", status=PhaseStatus.DONE)
        step = _step(status=StepStatus.PENDING)
        locked_phase = _phase(
            "locked-ph",
            status=PhaseStatus.LOCKED,
            steps=[step],
            depends_on=["done-ph"],
        )
        plan = _plan([done_phase, locked_phase])
        d = derive_step(plan, locked_phase, step)
        assert d.claimable

    def test_in_progress_phase_allows_step(self) -> None:
        step = _step(status=StepStatus.PENDING)
        phase = _phase(status=PhaseStatus.IN_PROGRESS, steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert d.claimable

    def test_done_phase_blocks_pending_step(self) -> None:
        """A DONE phase should block any remaining pending steps."""
        step = _step(status=StepStatus.PENDING)
        phase = _phase(status=PhaseStatus.DONE, steps=[step])
        plan = _plan([phase])
        d = derive_step(plan, phase, step)
        assert not d.claimable
        assert BlockReason.PHASE_INACTIVE in d.reasons


# ---------------------------------------------------------------------------
# is_step_locked: convenience wrapper
# ---------------------------------------------------------------------------


class TestIsStepLocked:
    """is_step_locked is the '🔒 icon' predicate."""

    def test_pending_with_unmet_deps_is_locked(self) -> None:
        dep = _step("s1", status=StepStatus.PENDING)
        step = _step("s2", depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        assert is_step_locked(plan, phase, step) is True

    def test_pending_without_deps_is_not_locked(self) -> None:
        step = _step(status=StepStatus.PENDING)
        phase = _phase(steps=[step])
        plan = _plan([phase])
        assert is_step_locked(plan, phase, step) is False

    def test_non_pending_is_never_locked(self) -> None:
        step = _step(status=StepStatus.CLAIMED, depends_on=["nonexistent"])
        phase = _phase(steps=[step])
        plan = _plan([phase])
        assert is_step_locked(plan, phase, step) is False

    def test_pending_with_met_deps_is_not_locked(self) -> None:
        dep = _step("s1", status=StepStatus.DONE)
        step = _step("s2", depends_on=["s1"])
        phase = _phase(steps=[dep, step])
        plan = _plan([phase])
        assert is_step_locked(plan, phase, step) is False


# ---------------------------------------------------------------------------
# StepDerived.locked property
# ---------------------------------------------------------------------------


class TestStepDerivedLocked:
    """locked property = blocked AND has UNMET_DEP reason."""

    def test_locked_when_unmet_dep(self) -> None:
        d = StepDerived(
            claimable=False,
            blocked=True,
            reasons=frozenset({BlockReason.UNMET_DEP}),
            unmet_deps=("s1",),
        )
        assert d.locked is True

    def test_not_locked_when_phase_inactive_only(self) -> None:
        d = StepDerived(
            claimable=False,
            blocked=True,
            reasons=frozenset({BlockReason.PHASE_INACTIVE}),
        )
        assert d.locked is False

    def test_not_locked_when_not_blocked(self) -> None:
        d = StepDerived(claimable=True, blocked=False)
        assert d.locked is False


# ---------------------------------------------------------------------------
# Semantic parity: derive_step vs get_next_steps (p0-parity.4)
# ---------------------------------------------------------------------------


class TestSemanticParityWithCore:
    """derive_step().claimable MUST agree with get_next_steps() membership."""

    def _check_parity(self, plan: Plan) -> None:
        """Assert derive_step claimable == in get_next_steps for all steps."""
        next_ids = {s.id for s in get_next_steps(plan)}
        for phase in plan.phases:
            for step in phase.steps:
                derived = derive_step(plan, phase, step)
                in_next = step.id in next_ids
                assert derived.claimable == in_next, (
                    f"Step {step.id}: derive_step.claimable={derived.claimable} "
                    f"but in get_next_steps={in_next} "
                    f"(status={step.status.value}, reasons={derived.reasons})"
                )

    def test_simple_linear_plan(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.PENDING)
        s2 = _step("ph.s2", depends_on=["ph.s1"])
        phase = _phase("ph", steps=[s1, s2])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_partially_done_plan(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.DONE)
        s2 = _step("ph.s2", depends_on=["ph.s1"])
        s3 = _step("ph.s3", depends_on=["ph.s2"])
        phase = _phase("ph", steps=[s1, s2, s3])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_all_done_plan(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.DONE)
        s2 = _step("ph.s2", status=StepStatus.DONE, depends_on=["ph.s1"])
        phase = _phase("ph", steps=[s1, s2])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_multi_phase_with_locks(self) -> None:
        s1 = _step("a.1", status=StepStatus.PENDING)
        phase_a = _phase("a", steps=[s1])
        s2 = _step("b.1", status=StepStatus.PENDING)
        phase_b = _phase("b", status=PhaseStatus.LOCKED, steps=[s2], depends_on=["a"])
        plan = _plan([phase_a, phase_b])
        self._check_parity(plan)

    def test_multi_phase_auto_unlock(self) -> None:
        phase_a = _phase("a", status=PhaseStatus.DONE, steps=[_step("a.1", status=StepStatus.DONE)])
        s2 = _step("b.1", status=StepStatus.PENDING)
        phase_b = _phase("b", status=PhaseStatus.LOCKED, steps=[s2], depends_on=["a"])
        plan = _plan([phase_a, phase_b])
        self._check_parity(plan)

    def test_rejected_with_deps(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.DONE)
        s2 = _step("ph.s2", status=StepStatus.REJECTED, depends_on=["ph.s1"])
        phase = _phase("ph", steps=[s1, s2])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_parallel_steps_no_deps(self) -> None:
        s1 = _step("ph.s1")
        s2 = _step("ph.s2")
        s3 = _step("ph.s3")
        phase = _phase("ph", steps=[s1, s2, s3])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_claimed_step_not_in_next(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.CLAIMED)
        s2 = _step("ph.s2", depends_on=["ph.s1"])
        phase = _phase("ph", steps=[s1, s2])
        plan = _plan([phase])
        self._check_parity(plan)

    def test_mixed_statuses(self) -> None:
        s1 = _step("ph.s1", status=StepStatus.DONE)
        s2 = _step("ph.s2", status=StepStatus.SKIPPED)
        s3 = _step("ph.s3", depends_on=["ph.s1", "ph.s2"])
        s4 = _step("ph.s4", status=StepStatus.CLAIMED)
        phase = _phase("ph", steps=[s1, s2, s3, s4])
        plan = _plan([phase])
        self._check_parity(plan)


class TestCLIMCPLockAgreement:
    """CLI and MCP MUST show same 🔒 for same step state.

    Both import is_step_locked from vectl.semantics, so they share
    the same algorithm. This test verifies the import identity.
    """

    def test_cli_mcp_share_lock_function(self) -> None:
        from vectl.cli import is_step_locked as cli_fn
        from vectl.mcp_server import is_step_locked as mcp_fn

        assert cli_fn is mcp_fn

    def test_core_render_shares_lock_function(self) -> None:
        from vectl.core import _is_step_locked_shared as core_fn

        assert core_fn is is_step_locked
