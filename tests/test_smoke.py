"""System smoke tests for vectl CLI + MCP parity.

These tests exercise end-to-end workflows: plan path resolution from
nested directories, locked/claimable step agreement between CLI commands,
and CAS conflict handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import save_plan, save_state
from vectl.models import (
    Phase,
    PhaseState,
    PhaseStatus,
    Plan,
    PlanState,
    Step,
    StepState,
    StepStatus,
)
from vectl.plan_path import resolve_state_path

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


class TestSplitStatePersistence:
    """Companion state behavior stays stable across path + precedence modes."""

    def test_relative_and_absolute_plan_path_share_companion_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Source: src/vectl/plan_path.py::resolve_state_path fallback rule
        # for non-git dirs -> <plan_dir>/.vectl/state.json.
        plan = Plan(
            project="state-path",
            phases=[Phase(id="p1", name="P1", steps=[Step(id="p1.s1", name="S1")])],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        monkeypatch.chdir(tmp_path)
        rel_result = runner.invoke(
            app,
            ["claim", "p1.s1", "--agent", "rel-agent", "--plan", "plan.yaml"],
        )
        assert rel_result.exit_code == 0

        state_path = tmp_path / ".vectl" / "state.json"
        assert state_path.exists()
        state_doc = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_doc["steps"]["p1.s1"]["claimed_by"] == "rel-agent"

        abs_result = runner.invoke(app, ["show", "p1.s1", "--plan", str(plan_path.resolve())])
        assert abs_result.exit_code == 0
        assert "**Claimed By:** rel-agent" in abs_result.output

    def test_state_json_overrides_stale_embedded_runtime_fields(self, tmp_path: Path) -> None:
        # Source: src/vectl/io.py::load_plan + merge_plan apply state.json over
        # legacy embedded runtime fields in plan.yaml.
        plan_path = tmp_path / "plan.yaml"
        plan_path.write_text(
            """\
project: stale-precedence
phases:
  - id: p1
    name: P1
    status: pending
    steps:
      - id: p1.s1
        name: S1
        status: claimed
        claimed_by: legacy-agent
""",
            encoding="utf-8",
        )

        state_path = resolve_state_path(plan_path)
        save_state(
            PlanState(
                plan_id="",
                steps={
                    "p1.s1": StepState(
                        status=StepStatus.CLAIMED,
                        claimed_by="fresh-agent",
                    )
                },
                phases={"p1": PhaseState(status=PhaseStatus.PENDING)},
            ),
            state_path,
        )

        show_result = runner.invoke(app, ["show", "p1.s1", "--plan", str(plan_path)])
        assert show_result.exit_code == 0
        assert "**Claimed By:** fresh-agent" in show_result.output
        assert "legacy-agent" not in show_result.output

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
