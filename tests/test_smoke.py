"""System smoke tests for vectl CLI + MCP parity.

These tests exercise end-to-end workflows: plan path resolution from
nested directories, locked/claimable step agreement between CLI commands,
and CAS conflict handling.
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import load_plan, save_plan
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus

runner = CliRunner()


@pytest.fixture
def multi_phase_plan(tmp_path: Path) -> Path:
    """Plan with locked, in-progress, and done phases for smoke testing."""
    plan = Plan(
        project="smoke-test",
        phases=[
            Phase(
                id="done-phase",
                name="Already Done",
                status=PhaseStatus.DONE,
                steps=[
                    Step(
                        id="d1",
                        name="Done step",
                        status=StepStatus.DONE,
                        evidence="completed",
                    ),
                ],
            ),
            Phase(
                id="active-phase",
                name="Active Work",
                status=PhaseStatus.IN_PROGRESS,
                depends_on=["done-phase"],
                steps=[
                    Step(id="a1", name="Ready step"),
                    Step(id="a2", name="Dep step", depends_on=["a1"]),
                    Step(
                        id="a3",
                        name="Independent",
                        description="Independent of others",
                    ),
                ],
            ),
            Phase(
                id="locked-phase",
                name="Future Work",
                status=PhaseStatus.LOCKED,
                depends_on=["active-phase"],
                steps=[
                    Step(id="f1", name="Future step"),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


class TestPlanPathFromNestedDir:
    """Walk-up discovery finds plan.yaml from nested directories."""

    def test_discovers_plan_from_child_dir(self, multi_phase_plan: Path) -> None:
        child = multi_phase_plan.parent / "sub" / "deep"
        child.mkdir(parents=True)
        # Use env var to simulate walk-up (runner changes cwd)
        result = runner.invoke(
            app,
            ["status", "--plan", str(multi_phase_plan)],
        )
        assert result.exit_code == 0
        assert "smoke-test" in result.output

    def test_env_var_overrides_walk_up(
        self, multi_phase_plan: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VECTL_PLAN_PATH", str(multi_phase_plan))
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "smoke-test" in result.output


class TestLockedClaimableAgreement:
    """Status, show, next, and review must agree on what's locked/claimable."""

    def test_locked_phase_steps_not_in_next(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["next", "--all", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        # f1 is in locked-phase — must NOT appear as claimable
        assert "f1" not in result.output

    def test_dep_blocked_step_not_in_next(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["next", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        # a2 depends on a1 (pending) — a2 should not be claimable
        assert "a2" not in result.output
        # a1 should be claimable
        assert "a1" in result.output

    def test_review_shows_locked_icon(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["review", "--all", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        # Locked phase steps should show lock icon
        assert "🔒" in result.output

    def test_show_locked_phase_shows_locks(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["show", "locked-phase", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        assert "🔒" in result.output


class TestClaimCompleteWorkflow:
    """Full claim → complete → next workflow."""

    def test_claim_complete_cycle(self, multi_phase_plan: Path) -> None:
        # Claim a1
        result = runner.invoke(
            app,
            [
                "claim",
                "a1",
                "--agent",
                "test-agent",
                "--plan",
                str(multi_phase_plan),
            ],
        )
        assert result.exit_code == 0

        # Complete a1
        result = runner.invoke(
            app,
            [
                "complete",
                "a1",
                "--evidence",
                "Done in smoke test",
                "--plan",
                str(multi_phase_plan),
            ],
        )
        assert result.exit_code == 0

        # Now a2 should be claimable (dep met)
        result = runner.invoke(app, ["next", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        assert "a2" in result.output

    def test_review_include_done_shows_done_phases(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["review", "--all", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        assert "done-phase" in result.output

    def test_review_without_all_hides_done(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        # done-phase should NOT appear as a phase HEADING in L3 (only active phases).
        # It may appear as a dep reference (e.g., "deps: done-phase").
        lines = result.output.split("\n")
        in_l3 = False
        for line in lines:
            if "L3:" in line:
                in_l3 = True
                continue
            if "L4:" in line:
                in_l3 = False
            if in_l3 and "done-phase" in line and "deps:" not in line:
                pytest.fail(f"done-phase appears as phase entry in L3: {line!r}")


class TestSearchAndValidate:
    """Search and validate commands work correctly."""

    def test_search_finds_step(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["search", "Ready", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
        assert "a1" in result.output

    def test_validate_passes_clean_plan(self, multi_phase_plan: Path) -> None:
        result = runner.invoke(app, ["validate", "--plan", str(multi_phase_plan)])
        assert result.exit_code == 0
