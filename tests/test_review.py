"""Tests for review and gate-check CLI commands and core functions."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.core import gate_check, review_plan
from vectl.io import load_plan, save_plan
from vectl.models import (
    GateCheckResult,
    Phase,
    PhaseProgress,
    PhaseStatus,
    Plan,
    PlanError,
    ReviewResult,
    Step,
    StepStatus,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def plan_file(tmp_path: Path) -> Path:
    """Plan with mixed-status phases for review testing."""
    plan = Plan(
        project="test-review",
        phases=[
            Phase(
                id="alpha",
                name="Alpha Phase",
                status=PhaseStatus.DONE,
                gate="All alpha tests pass",
                steps=[
                    Step(
                        id="a.1",
                        name="Alpha One",
                        status=StepStatus.DONE,
                        evidence="done",
                        refs=["docs/spec.md", "src/core.py"],
                    ),
                    Step(
                        id="a.2",
                        name="Alpha Two",
                        status=StepStatus.DONE,
                        evidence="done",
                        depends_on=["a.1"],
                        refs=["docs/spec.md"],
                    ),
                ],
            ),
            Phase(
                id="beta",
                name="Beta Phase",
                status=PhaseStatus.IN_PROGRESS,
                depends_on=["alpha"],
                gate="Beta integration passes",
                steps=[
                    Step(
                        id="b.1",
                        name="Beta One",
                        status=StepStatus.DONE,
                        evidence="done",
                    ),
                    Step(id="b.2", name="Beta Two"),
                    Step(id="b.3", name="Beta Three", depends_on=["b.1"]),
                ],
            ),
            Phase(
                id="gamma",
                name="Gamma Phase",
                status=PhaseStatus.LOCKED,
                depends_on=["beta"],
                steps=[
                    Step(id="g.1", name="Gamma One"),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


@pytest.fixture()
def done_plan_file(tmp_path: Path) -> Path:
    """Plan where a phase is fully complete (for gate-check)."""
    plan = Plan(
        project="test-gate",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.DONE,
                gate="All tests pass",
                steps=[
                    Step(id="s1", name="S1", status=StepStatus.DONE, evidence="done"),
                    Step(
                        id="s2",
                        name="S2",
                        status=StepStatus.SKIPPED,
                        skipped_reason="superseded",
                    ),
                ],
            ),
            Phase(
                id="p2",
                name="Phase 2",
                status=PhaseStatus.LOCKED,
                depends_on=["p1"],
                steps=[
                    Step(id="s3", name="S3"),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


@pytest.fixture()
def gate_script_plan(tmp_path: Path) -> Path:
    """Plan with a gate_script that can be tested."""
    # Create a simple gate script
    script = tmp_path / "check.sh"
    script.write_text("#!/bin/sh\necho 'gate check ok'\nexit 0\n")
    script.chmod(0o755)

    # Create a failing script too
    fail_script = tmp_path / "fail.sh"
    fail_script.write_text("#!/bin/sh\necho 'FAIL: criteria not met' >&2\nexit 1\n")
    fail_script.chmod(0o755)

    plan = Plan(
        project="test-gate-script",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.DONE,
                gate="Automated checks pass",
                gate_script="./check.sh",
                steps=[
                    Step(id="s1", name="S1", status=StepStatus.DONE, evidence="done"),
                ],
            ),
            Phase(
                id="p2",
                name="Phase 2",
                status=PhaseStatus.PENDING,
                gate_script="./fail.sh",
                steps=[
                    Step(id="s2", name="S2", status=StepStatus.DONE, evidence="done"),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------


class TestReview:
    def test_review_shows_all_layers(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "L1: Validation" in result.output
        assert "L2: Phase Overview" in result.output
        assert "L3: Active Phase Detail" in result.output
        assert "L4: Spec Coverage" in result.output

    def test_l1_valid_plan(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "0 errors" in result.output

    def test_l2_shows_progress_bars(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # Alpha is 100% done
        assert "2/2" in result.output
        assert "100%" in result.output
        # Beta is 1/3 done
        assert "1/3" in result.output
        assert "Overall:" in result.output

    def test_l3_shows_active_phases(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # Beta is in_progress, should appear
        assert "beta" in result.output
        assert "Beta Phase" in result.output
        # Gamma is locked, should NOT appear without --all
        assert "Gamma Phase" not in result.output

    def test_l3_shows_all_with_flag(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--all", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # Now gamma should appear
        assert "gamma" in result.output
        assert "Gamma Phase" in result.output
        # Alpha (done) should also appear
        assert "Alpha Phase" in result.output

    def test_l3_shows_deps_and_gate(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "deps:" in result.output
        assert "gate:" in result.output

    def test_l4_shows_ref_coverage(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # refs are docs/spec.md and src/core.py
        assert "docs/spec.md" in result.output
        assert "src/core.py" in result.output
        # spec.md is referenced by a.1 and a.2
        assert "a.1" in result.output
        assert "a.2" in result.output

    def test_l4_no_refs(self, done_plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(done_plan_file)])
        assert result.exit_code == 0
        assert "No refs" in result.output

    def test_review_shows_affordance_hints(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "gate-check" in result.output

    def test_review_exits_1_on_validation_errors(self, tmp_path: Path) -> None:
        """Plan with a cycle should fail validation and exit 1."""
        plan = Plan(
            project="broken",
            phases=[
                Phase(
                    id="p1",
                    name="Broken",
                    steps=[
                        Step(id="x", name="X", depends_on=["y"]),
                        Step(id="y", name="Y", depends_on=["x"]),
                    ],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["review", "--plan", str(path)])
        assert result.exit_code == 1
        assert "ERROR" in result.output


# ---------------------------------------------------------------------------
# gate-check command
# ---------------------------------------------------------------------------


class TestGateCheck:
    def test_gate_ready(self, done_plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "p1", "--plan", str(done_plan_file)])
        assert result.exit_code == 0
        assert "gate-ready" in result.output
        assert "2/2 complete" in result.output

    def test_gate_not_ready(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "beta", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "NOT gate-ready" in result.output
        assert "remaining" in result.output
        assert "b.2" in result.output
        assert "b.3" in result.output

    def test_gate_check_shows_criteria(self, done_plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "p1", "--plan", str(done_plan_file)])
        assert result.exit_code == 0
        assert "All tests pass" in result.output

    def test_gate_check_phase_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "nonexistent", "--plan", str(plan_file)])
        assert result.exit_code == 1

    def test_gate_check_shows_downstream(self, done_plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "p1", "--plan", str(done_plan_file)])
        assert result.exit_code == 0
        # p2 is locked and depends on p1
        assert "p2" in result.output

    def test_gate_script_pass(self, gate_script_plan: Path) -> None:
        result = runner.invoke(app, ["gate-check", "p1", "--plan", str(gate_script_plan)])
        assert result.exit_code == 0
        assert "Gate script passed" in result.output

    def test_gate_script_fail(self, gate_script_plan: Path) -> None:
        result = runner.invoke(app, ["gate-check", "p2", "--plan", str(gate_script_plan)])
        assert result.exit_code == 0
        assert "Gate script failed" in result.output
        assert "NOT gate-ready" in result.output

    def test_gate_check_no_gate_script(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["gate-check", "beta", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "No gate_script defined" in result.output


# ---------------------------------------------------------------------------
# Core: review_plan()
# ---------------------------------------------------------------------------


class TestReviewPlan:
    """Unit tests for review_plan() core function."""

    def test_returns_review_result(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        assert isinstance(result, ReviewResult)

    def test_phase_progress_counts(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        # alpha: 2 done / 2 total, beta: 1 done / 3 total, gamma: 0/1
        assert len(result.phase_progress) == 3
        alpha_pp = result.phase_progress[0]
        assert alpha_pp.phase_id == "alpha"
        assert alpha_pp.done == 2
        assert alpha_pp.total == 2
        assert alpha_pp.pct == 100.0

        beta_pp = result.phase_progress[1]
        assert beta_pp.phase_id == "beta"
        assert beta_pp.done == 1
        assert beta_pp.total == 3
        assert abs(beta_pp.pct - 33.3) < 1.0

    def test_total_counts(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        assert result.total_steps == 6  # 2 + 3 + 1
        assert result.total_done == 3  # 2 alpha + 1 beta
        assert abs(result.overall_pct - 50.0) < 1.0

    def test_active_phases_excludes_done_and_locked(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        active_ids = [ph.id for ph in result.active_phases]
        # beta is in_progress (active), alpha is done (excluded), gamma is locked (excluded)
        assert "beta" in active_ids
        assert "alpha" not in active_ids
        assert "gamma" not in active_ids

    def test_include_done_shows_all(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan, include_done=True)
        active_ids = [ph.id for ph in result.active_phases]
        assert "alpha" in active_ids
        assert "beta" in active_ids
        assert "gamma" in active_ids

    def test_ref_index(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        # a.1 refs docs/spec.md and src/core.py; a.2 refs docs/spec.md
        assert "docs/spec.md" in result.ref_index
        assert "src/core.py" in result.ref_index
        assert "a.1" in result.ref_index["docs/spec.md"]
        assert "a.2" in result.ref_index["docs/spec.md"]
        assert result.ref_index["src/core.py"] == ["a.1"]

    def test_ref_index_empty_when_no_refs(self, done_plan_file: Path) -> None:
        plan = load_plan(done_plan_file)[0]
        result = review_plan(plan)
        assert result.ref_index == {}

    def test_validation_issues_on_valid_plan(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = review_plan(plan)
        assert result.errors == []

    def test_validation_issues_on_broken_plan(self) -> None:
        plan = Plan(
            project="broken",
            phases=[
                Phase(
                    id="p1",
                    name="Broken",
                    steps=[
                        Step(id="x", name="X", depends_on=["y"]),
                        Step(id="y", name="Y", depends_on=["x"]),
                    ],
                ),
            ],
        )
        result = review_plan(plan)
        assert len(result.errors) > 0

    def test_skipped_steps_count_as_done(self) -> None:
        plan = Plan(
            project="test",
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
                            skipped_reason="not needed",
                        ),
                    ],
                ),
            ],
        )
        result = review_plan(plan)
        assert result.total_done == 2
        assert result.total_steps == 2
        assert result.overall_pct == 100.0

    def test_empty_plan(self) -> None:
        plan = Plan(project="empty", phases=[])
        result = review_plan(plan)
        assert result.phase_progress == []
        assert result.total_steps == 0
        assert result.total_done == 0
        assert result.overall_pct == 0.0
        assert result.active_phases == []
        assert result.ref_index == {}

    def test_errors_and_warnings_properties(self) -> None:
        plan = Plan(
            project="mixed",
            phases=[
                Phase(
                    id="p1",
                    name="Phase One",
                    steps=[
                        Step(id="s1", name="S1", depends_on=["nonexistent"]),
                    ],
                ),
            ],
        )
        result = review_plan(plan)
        # depends_on referencing nonexistent step is an error
        all_issues = result.validation_issues
        assert len(all_issues) > 0
        # errors + warnings should cover all issues
        assert len(result.errors) + len(result.warnings) == len(all_issues)


# ---------------------------------------------------------------------------
# Core: gate_check()
# ---------------------------------------------------------------------------


class TestGateCheckCore:
    """Unit tests for gate_check() core function."""

    def test_returns_gate_check_result(self, done_plan_file: Path) -> None:
        plan = load_plan(done_plan_file)[0]
        result = gate_check(plan, "p1")
        assert isinstance(result, GateCheckResult)

    def test_complete_phase_is_gate_ready(self, done_plan_file: Path) -> None:
        plan = load_plan(done_plan_file)[0]
        result = gate_check(plan, "p1")
        assert result.steps_complete is True
        assert result.done_count == 2
        assert result.total_count == 2
        assert result.pending_steps == []

    def test_incomplete_phase_is_not_gate_ready(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = gate_check(plan, "beta")
        assert result.steps_complete is False
        assert result.done_count == 1
        assert result.total_count == 3
        pending_ids = [s.id for s in result.pending_steps]
        assert "b.2" in pending_ids
        assert "b.3" in pending_ids

    def test_gate_criterion(self, done_plan_file: Path) -> None:
        plan = load_plan(done_plan_file)[0]
        result = gate_check(plan, "p1")
        assert result.gate_criterion == "All tests pass"

    def test_gate_script(self, gate_script_plan: Path) -> None:
        plan = load_plan(gate_script_plan)[0]
        result = gate_check(plan, "p1")
        assert result.gate_script == "./check.sh"

    def test_no_gate_script(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = gate_check(plan, "beta")
        assert result.gate_script is None

    def test_downstream_locked(self, done_plan_file: Path) -> None:
        plan = load_plan(done_plan_file)[0]
        result = gate_check(plan, "p1")
        assert "p2" in result.downstream_locked

    def test_no_downstream_when_unlocked(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        # gamma is locked and depends on beta
        result = gate_check(plan, "beta")
        assert "gamma" in result.downstream_locked

    def test_phase_not_found_raises(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        with pytest.raises(PlanError, match="not found"):
            gate_check(plan, "nonexistent")

    def test_phase_id_and_name(self, plan_file: Path) -> None:
        plan = load_plan(plan_file)[0]
        result = gate_check(plan, "alpha")
        assert result.phase_id == "alpha"
        assert result.phase_name == "Alpha Phase"
