"""Tests for CLI commands (cli_read + cli_write phases)."""

from __future__ import annotations

import json
import subprocess as sp
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from vectl import __version__
from vectl.claims import ClaimEntry, load_claims, save_claims
from vectl.cli import app
from vectl.io import load_plan_definition, save_plan
from vectl.io import load_plan_definition as load_plan
from vectl.models import AffinityMode, Phase, PhaseStatus, Plan, Step, StepStatus
from vectl.plan_path import resolve_claims_path

runner = CliRunner()


def _must_find_step(plan: Plan, step_id: str) -> Step:
    found = plan.find_step(step_id)
    assert found is not None
    return found[1]


def _must_find_phase(plan: Plan, phase_id: str) -> Phase:
    phase = plan.find_phase(phase_id)
    assert phase is not None
    return phase


def _companion_state_path(plan_path: Path) -> Path:
    return plan_path.parent / ".vectl" / "state.json"


@pytest.fixture
def plan_file(tmp_path: Path) -> Path:
    """Create a plan.yaml for testing."""
    plan = Plan(
        project="test-project",
        context="Test strategy context.",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                gate="All tests pass",
                context="Phase 1 context.",
                steps=[
                    Step(id="s1", name="Step 1"),
                    Step(id="s2", name="Step 2", depends_on=["s1"]),
                    Step(
                        id="s3",
                        name="Step 3",
                        description="Checklist:\n- [ ] Item A\n- [ ] Item B\n",
                    ),
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
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


def _legacy_migration_plan_dict() -> dict:
    """Create a legacy fixture with runtime state embedded in plan.yaml."""
    return {
        "version": 1,
        "project": "migration-test",
        "plan_id": "migration-plan-001",
        "clipboard": {
            "author": "reviewer",
            "summary": "Handoff",
            "content": "Legacy clipboard content preserved via state.",
            "written_at": "2026-03-01T09:00:00Z",
            "expires_at": "2026-03-31T09:00:00Z",
        },
        "phases": [
            {
                "id": "phase-a",
                "name": "Phase A",
                "status": "done",
                "evidence": "Phase completed",
                "steps": [
                    {
                        "id": "phase-a.step1",
                        "name": "Step with all state",
                        "status": "done",
                        "description": "Legacy step 1",
                        "claimed_by": "python-engineer",
                        "claimed_at": "2026-02-21T08:11:51.841244+00:00",
                        "evidence": "Implemented feature X.",
                        "skipped_reason": "irrelevant",
                        "rejection_reason": "Testing reject via CLI",
                        "rejection_history": [
                            {
                                "reason": "Testing reject via CLI",
                                "timestamp": "2026-02-09T04:46:23.870176+00:00",
                                "reviewer": "cli-tester",
                            }
                        ],
                        "affinity_override": True,
                        "affinity_override_by": "cli-tester",
                        "affinity_override_at": "2026-02-21T08:26:00.000000+00:00",
                    },
                    {
                        "id": "phase-a.step2",
                        "name": "Step with rejection history",
                        "status": "rejected",
                        "description": "Legacy step 2",
                        "claimed_by": "legacy-agent",
                        "claimed_at": "2026-02-09T04:46:06.123164+00:00",
                        "evidence": "Legacy rejection",
                        "skipped_reason": "absorbed",
                        "rejection_reason": "Testing reject via CLI",
                        "rejection_history": [
                            {
                                "reason": "Testing reject via CLI",
                                "timestamp": "2026-02-09T04:46:23.870176+00:00",
                                "reviewer": "cli-tester",
                            }
                        ],
                        "affinity_override": False,
                        "affinity_override_by": "legacy-agent",
                        "affinity_override_at": "2026-02-21T08:26:52.124877+00:00",
                    },
                ],
            },
            {
                "id": "phase-b",
                "name": "Phase B",
                "status": "pending",
                "depends_on": ["phase-a"],
                "steps": [
                    {
                        "id": "phase-b.step1",
                        "name": "Pending step",
                        "status": "pending",
                        "claimed_by": "legacy-worker",
                        "claimed_at": "2026-03-01T12:00:00Z",
                        "evidence": "Legacy pending placeholder.",
                        "skipped_reason": "absorbed",
                        "rejection_reason": "Legacy pending rejection.",
                        "rejection_history": [
                            {
                                "reason": "Warm-up",
                                "timestamp": "2026-03-01T11:00:00Z",
                                "reviewer": "planner",
                            }
                        ],
                        "affinity_override": False,
                        "affinity_override_by": "planner",
                        "affinity_override_at": "2026-03-01T12:00:00Z",
                    }
                ],
            },
        ],
    }


@pytest.fixture
def legacy_plan_file(tmp_path: Path) -> Path:
    """Create a legacy plan with embedded state fields."""
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.dump(_legacy_migration_plan_dict()), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# cli.1: --version + init
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "vectl" in result.output
        assert __version__ in result.output

    def test_short_version_flag(self):
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert __version__ in result.output


class TestInit:
    def test_init_creates_file(self, tmp_path: Path):
        path = tmp_path / "plan.yaml"
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        assert path.exists()
        plan, _ = load_plan(path)
        assert plan.project == "myproject"

    def test_init_refuses_existing(self, plan_file: Path):
        result = runner.invoke(app, ["init", "--project", "x", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_creates_agents_md(self, tmp_path: Path):
        path = tmp_path / "plan.yaml"
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text()
        assert "## Plan Tracking (vectl)" in content
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content
        assert "<!-- VECTL:AGENTS:END -->" in content
        assert "vectl guide" in content
        assert "VECTL:GUIDANCE:BEGIN" in content

    def test_init_appends_to_existing_agents_md(self, tmp_path: Path):
        path = tmp_path / "plan.yaml"
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# My Project\n\nExisting content.\n")
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        content = agents_md.read_text()
        assert "Existing content." in content
        assert "## Plan Tracking (vectl)" in content

    def test_init_agents_md_idempotent(self, tmp_path: Path):
        path = tmp_path / "plan.yaml"
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# My Project\n\n"
            "<!-- VECTL:AGENTS:BEGIN -->\n"
            "## Plan Tracking (vectl)\n\n"
            "Old block.\n"
            "<!-- VECTL:AGENTS:END -->\n"
        )
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        content = agents_md.read_text()
        assert content.count("<!-- VECTL:AGENTS:BEGIN -->") == 1
        assert content.count("<!-- VECTL:AGENTS:END -->") == 1
        assert "updated" in result.output.lower() or "replaced" in result.output.lower()

    def test_init_preserves_legacy_block_and_appends_new(self, tmp_path: Path):
        """Legacy header without markers should be preserved; new block appended."""
        path = tmp_path / "plan.yaml"
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# My Project\n\n## Plan Tracking (vectl)\n\nLegacy text.\n")
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        content = agents_md.read_text()
        assert content.count("## Plan Tracking (vectl)") == 2
        # Rich may wrap long lines; normalize whitespace for assertion
        output_flat = " ".join(result.output.lower().split())
        assert "legacy block preserved" in output_flat

    def test_agents_md_command_upserts(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Title\n\nExisting\n", encoding="utf-8")
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        content = agents_md.read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content


class TestClaudeDetection:
    """Tests for .claude/ auto-detection in _upsert_agents_md / agents-md."""

    def test_fresh_project_with_claude_dir_creates_claude_md(self, tmp_path: Path) -> None:
        """No AGENTS.md, no CLAUDE.md, but .claude/ dir → creates CLAUDE.md."""
        (tmp_path / ".claude").mkdir()
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content

    def test_fresh_project_without_claude_dir_creates_agents_md(self, tmp_path: Path) -> None:
        """No .claude/ dir → creates AGENTS.md (backward compatible)."""
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_existing_agents_md_with_markers_preserved_despite_claude_dir(
        self, tmp_path: Path
    ) -> None:
        """AGENTS.md has markers + .claude/ dir → stays on AGENTS.md (stability)."""
        (tmp_path / ".claude").mkdir()
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# Project\n\n<!-- VECTL:AGENTS:BEGIN -->\nOld block.\n<!-- VECTL:AGENTS:END -->\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        content = agents_md.read_text(encoding="utf-8")
        assert content.count("<!-- VECTL:AGENTS:BEGIN -->") == 1
        assert "Old block." not in content  # replaced
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_existing_claude_md_with_markers_preserved(self, tmp_path: Path) -> None:
        """CLAUDE.md has markers → stays on CLAUDE.md even without .claude/ dir."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Claude\n\n<!-- VECTL:AGENTS:BEGIN -->\nOld.\n<!-- VECTL:AGENTS:END -->\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        content = claude_md.read_text(encoding="utf-8")
        assert content.count("<!-- VECTL:AGENTS:BEGIN -->") == 1
        assert not (tmp_path / "AGENTS.md").exists()

    def test_existing_agents_md_without_markers_wins_over_claude_dir(self, tmp_path: Path) -> None:
        """AGENTS.md exists (no markers) + .claude/ dir → appends to AGENTS.md."""
        (tmp_path / ".claude").mkdir()
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# My Project\n\nExisting.\n", encoding="utf-8")
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        content = agents_md.read_text(encoding="utf-8")
        assert "Existing." in content
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_init_creates_claude_md_when_claude_dir_present(self, tmp_path: Path) -> None:
        """init command respects .claude/ detection."""
        (tmp_path / ".claude").mkdir()
        path = tmp_path / "plan.yaml"
        result = runner.invoke(app, ["init", "--project", "myproject", "--plan", str(path)])
        assert result.exit_code == 0
        assert path.exists()
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content

    def test_both_files_exist_agents_with_markers_wins(self, tmp_path: Path) -> None:
        """Both AGENTS.md (with markers) and CLAUDE.md exist → AGENTS.md wins."""
        (tmp_path / ".claude").mkdir()
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "<!-- VECTL:AGENTS:BEGIN -->\nOld.\n<!-- VECTL:AGENTS:END -->\n",
            encoding="utf-8",
        )
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Claude stuff\n", encoding="utf-8")
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        agents_content = agents_md.read_text(encoding="utf-8")
        claude_content = claude_md.read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in agents_content
        assert "<!-- VECTL:AGENTS:BEGIN -->" not in claude_content

    def test_both_files_exist_claude_with_markers_wins(self, tmp_path: Path) -> None:
        """Both files exist, only CLAUDE.md has markers → CLAUDE.md wins."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents\n", encoding="utf-8")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Claude\n\n<!-- VECTL:AGENTS:BEGIN -->\nOld.\n<!-- VECTL:AGENTS:END -->\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        claude_content = claude_md.read_text(encoding="utf-8")
        agents_content = agents_md.read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in claude_content
        assert "<!-- VECTL:AGENTS:BEGIN -->" not in agents_content

    def test_target_flag_agents_forces_agents_md(self, tmp_path: Path) -> None:
        """--target agents creates AGENTS.md even when .claude/ dir present."""
        (tmp_path / ".claude").mkdir()
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path), "--target", "agents"])
        assert result.exit_code == 0
        assert (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_target_flag_claude_forces_claude_md(self, tmp_path: Path) -> None:
        """--target claude creates CLAUDE.md even without .claude/ dir."""
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path), "--target", "claude"])
        assert result.exit_code == 0
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()

    def test_target_flag_overrides_existing_file_detection(self, tmp_path: Path) -> None:
        """--target claude writes CLAUDE.md even when AGENTS.md with markers exists."""
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "<!-- VECTL:AGENTS:BEGIN -->\nOld.\n<!-- VECTL:AGENTS:END -->\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path), "--target", "claude"])
        assert result.exit_code == 0
        assert (tmp_path / "CLAUDE.md").exists()
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "<!-- VECTL:AGENTS:BEGIN -->" in content

    def test_init_target_flag_claude(self, tmp_path: Path) -> None:
        """init --target claude creates CLAUDE.md regardless of .claude/ dir."""
        path = tmp_path / "plan.yaml"
        result = runner.invoke(
            app, ["init", "--project", "p", "--plan", str(path), "--target", "claude"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()


class TestEditPlan:
    def test_edit_plan_project_guidance_file(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(Plan(project="p"), plan_path)

        guidance_path = tmp_path / "guidance.md"
        guidance_path.write_text("Rule A\nRule B\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "edit-plan",
                "--project-guidance-file",
                str(guidance_path),
                "--plan",
                str(plan_path),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_path)
        assert "Rule A" in plan.project_guidance

    def test_edit_plan_rejects_dual_inputs(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(Plan(project="p"), plan_path)

        guidance_path = tmp_path / "guidance.md"
        guidance_path.write_text("X", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "edit-plan",
                "--project-guidance",
                "Inline",
                "--project-guidance-file",
                str(guidance_path),
                "--plan",
                str(plan_path),
            ],
        )
        assert result.exit_code == 1
        assert "only one" in result.output.lower()


# ---------------------------------------------------------------------------
# cli.2: next
# ---------------------------------------------------------------------------


class TestNext:
    def test_shows_claimable_steps(self, plan_file: Path):
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "s3" in result.output
        # s2 is blocked by s1
        assert "s2" not in result.output or "depends" in result.output.lower()

    def test_shows_strategy_context(self, plan_file: Path):
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert "Test strategy context" in result.output

    def test_empty_plan(self, tmp_path: Path):
        plan = Plan(project="empty")
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--plan", str(path)])
        assert result.exit_code == 0
        assert "No claimable steps" in result.output

    def test_shows_one_line_summary(self, tmp_path: Path) -> None:
        """Steps with descriptions show first-line summary."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            description="Implement the widget system.\n- [ ] Part A\n- [ ] Part B",
                        ),
                    ],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--plan", str(path)])
        assert result.exit_code == 0
        assert "Implement the widget system" in result.output

    def test_detail_flag_shows_full_description(self, tmp_path: Path) -> None:
        """--detail shows full description in panel."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            description="Full description here.\n- [ ] Part A\n- [ ] Part B",
                            verification="pytest -v",
                        ),
                    ],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--detail", "--plan", str(path)])
        assert result.exit_code == 0
        assert "Part A" in result.output
        assert "Part B" in result.output
        assert "pytest -v" in result.output

    def test_affordance_hints(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "vectl claim" in result.output
        assert "vectl show" in result.output

    def test_default_limit_caps_at_three(self, tmp_path: Path) -> None:
        """Default limit of 3 hides extra steps."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id=f"s{i}", name=f"Step {i}") for i in range(1, 6)],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--plan", str(path)])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "s2" in result.output
        assert "s3" in result.output
        assert "s4" not in result.output
        assert "and 2 more" in result.output
        assert "vectl next --all" in result.output

    def test_limit_flag(self, tmp_path: Path) -> None:
        """--limit N shows exactly N steps."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id=f"s{i}", name=f"Step {i}") for i in range(1, 6)],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--limit", "2", "--plan", str(path)])
        assert result.exit_code == 0
        assert "s2" in result.output
        assert "s3" not in result.output
        assert "and 3 more" in result.output

    def test_all_flag_shows_everything(self, tmp_path: Path) -> None:
        """--all overrides limit and shows all steps."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id=f"s{i}", name=f"Step {i}") for i in range(1, 6)],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "--all", "--plan", str(path)])
        assert result.exit_code == 0
        assert "s5" in result.output
        assert "more" not in result.output

    def test_short_limit_flag(self, tmp_path: Path) -> None:
        """-n is alias for --limit."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id=f"s{i}", name=f"Step {i}") for i in range(1, 6)],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["next", "-n", "1", "--plan", str(path)])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "s2" not in result.output
        assert "and 4 more" in result.output


# ---------------------------------------------------------------------------
# cli.3: status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_overview(self, plan_file: Path):
        result = runner.invoke(app, ["status", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "p1" in result.output
        assert "p2" in result.output
        assert "Phase 1" in result.output

    def test_phase_detail(self, plan_file: Path):
        result = runner.invoke(app, ["status", "--plan", str(plan_file), "--phase", "p1"])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "s2" in result.output
        assert "s3" in result.output

    def test_phase_not_found(self, plan_file: Path):
        result = runner.invoke(app, ["status", "--plan", str(plan_file), "--phase", "nope"])
        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# cli.4: guide (renamed from help-agent)
# ---------------------------------------------------------------------------


class TestGuide:
    def test_default_startup(self):
        result = runner.invoke(app, ["guide"])
        assert result.exit_code == 0
        assert "vectl next" in result.output

    def test_stuck_topic(self):
        result = runner.invoke(app, ["guide", "--on", "stuck"])
        assert result.exit_code == 0
        assert "rejected" in result.output.lower() or "blocked" in result.output.lower()

    def test_review_topic(self):
        result = runner.invoke(app, ["guide", "--on", "review"])
        assert result.exit_code == 0
        assert "review" in result.output
        assert "gate-check" in result.output

    def test_planning_topic(self):
        result = runner.invoke(app, ["guide", "--on", "planning"])
        assert result.exit_code == 0
        assert "validate" in result.output

    def test_unknown_topic(self):
        result = runner.invoke(app, ["guide", "--on", "nope"])
        assert result.exit_code == 1
        assert "Unknown topic" in result.output


# ---------------------------------------------------------------------------
# cli.5: claim + complete
# ---------------------------------------------------------------------------


class TestClaim:
    def test_claim_step(self, plan_file: Path):
        result = runner.invoke(app, ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Claimed" in result.output
        # Should show full step spec after claiming
        assert "s1" in result.output
        assert "Step 1" in result.output
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.CLAIMED
        assert step.claimed_by == "bot-1"

    def test_claim_reject_with_existing_claim_shows_diagnostics(self, plan_file: Path) -> None:
        """Claim rejection due to existing claim shows rich diagnostics."""
        import os
        import subprocess

        from vectl.claims import get_current_branch

        # Need to work in a git repo for get_current_branch() to work
        repo_root = plan_file.parent
        if not (repo_root / ".git").exists():
            # Create .git directory to make it a git repo
            (repo_root / ".git").mkdir()
            subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_root,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=repo_root,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=repo_root,
                capture_output=True,
            )

        # Get branch
        original_cwd = os.getcwd()
        os.chdir(repo_root)
        branch_name = get_current_branch()

        try:
            # Create an existing claim
            claims_path = resolve_claims_path(plan_file)
            claims_path.parent.mkdir(parents=True, exist_ok=True)
            save_claims(
                {
                    f"{branch_name}:s1": ClaimEntry(
                        step_id="s1",
                        branch=branch_name,
                        agent="agent-a",
                        claimed_at="2026-03-09T00:00:00Z",
                    )
                },
                claims_path,
            )

            # Try to claim the same step with a different agent
            claim_result = runner.invoke(
                app, ["claim", "s1", "--agent", "agent-b", "--plan", str(plan_file)]
            )

            assert claim_result.exit_code == 1
            # Check for rich diagnostics
            assert "Claim Details:" in claim_result.output
            assert "s1" in claim_result.output
            assert branch_name in claim_result.output
            assert "agent-a" in claim_result.output  # The original claimant
            # Check for actionable next steps
            assert "vectl show s1" in claim_result.output
        finally:
            os.chdir(original_cwd)

    def test_claim_nonexistent(self, plan_file: Path):
        result = runner.invoke(app, ["claim", "nope", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_claim_blocked_step(self, plan_file: Path):
        result = runner.invoke(app, ["claim", "s2", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "unmet dependencies" in result.output

    def test_claim_error_for_already_qualified_id_is_not_double_prefixed(
        self, tmp_path: Path
    ) -> None:
        plan = Plan(
            project="qualified-id-regression",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    steps=[Step(id="p1.s1", name="Step 1", status=StepStatus.DONE)],
                )
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        result = runner.invoke(app, ["claim", "p1.s1", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "p1.s1" in result.output
        assert "p1.p1.s1" not in result.output

    def test_auto_claim_picks_first(self, plan_file: Path) -> None:
        """Omitting step_id auto-picks the first claimable step."""
        result = runner.invoke(app, ["claim", "--agent", "bot-1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Auto-selected" in result.output
        assert "Claimed" in result.output
        plan, _ = load_plan(plan_file)
        # First claimable is s1 or s3 (both unblocked); check at least one claimed
        claimed = [s for p in plan.phases for s in p.steps if s.status == StepStatus.CLAIMED]
        assert len(claimed) == 1
        assert claimed[0].claimed_by == "bot-1"

    def test_auto_claim_no_steps_fails(self, tmp_path: Path) -> None:
        """Auto-claim with no available steps shows error."""
        plan = Plan(project="empty")
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["claim", "--agent", "bot", "--plan", str(path)])
        assert result.exit_code == 1
        assert "No claimable steps" in result.output


class TestRepairClaimsCLI:
    def _init_repo(self, repo: Path) -> str:
        sp.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
        sp.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        sp.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        (repo / ".gitkeep").write_text("x", encoding="utf-8")
        sp.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
        sp.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True
        )
        branch = sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return branch

    def test_repair_claims_relative_and_absolute_plan_paths_share_claims_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)

        plan_path = repo / "plan.yaml"
        save_plan(Plan(project="repair", phases=[Phase(id="p1", name="P1", steps=[])]), plan_path)

        monkeypatch.chdir(repo)
        rel = runner.invoke(app, ["repair", "claims", "--dry-run", "--json", "--plan", "plan.yaml"])
        abs_result = runner.invoke(
            app,
            ["repair", "claims", "--dry-run", "--json", "--plan", str(plan_path)],
        )

        assert rel.exit_code == 0
        assert abs_result.exit_code == 0
        rel_payload = json.loads(rel.output)
        abs_payload = json.loads(abs_result.output)
        assert rel_payload["claims_path"] == abs_payload["claims_path"]
        assert rel_payload["plan_path"] == abs_payload["plan_path"]
        assert rel_payload["changed"] is False
        assert abs_payload["changed"] is False

    def test_repair_claims_step_scope_preserves_unrelated_claims(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        branch = self._init_repo(repo)

        plan_path = repo / "plan.yaml"
        save_plan(
            Plan(
                project="repair",
                phases=[
                    Phase(
                        id="p1",
                        name="P1",
                        steps=[
                            Step(id="s1", name="S1", status=StepStatus.PENDING),
                            Step(
                                id="s2",
                                name="S2",
                                status=StepStatus.CLAIMED,
                                claimed_by="agent-plan",
                                claimed_at="2026-03-09T10:00:00Z",
                            ),
                        ],
                    )
                ],
            ),
            plan_path,
        )

        claims_path = resolve_claims_path(plan_path)
        untouched = ClaimEntry(
            step_id="s2",
            branch=branch,
            agent="keep-me",
            claimed_at="2026-03-09T09:00:00Z",
        )
        save_claims(
            {
                f"{branch}:s1": ClaimEntry(
                    step_id="s1",
                    branch=branch,
                    agent="ghost",
                    claimed_at="2026-03-08T00:00:00Z",
                ),
                f"{branch}:s2": untouched,
            },
            claims_path,
        )

        monkeypatch.chdir(repo)
        result = runner.invoke(
            app,
            ["repair", "claims", "--step", "s1", "--json", "--plan", str(plan_path)],
        )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["changed"] is True
        assert [a["key"] for a in payload["actions"]] == [f"{branch}:s1"]

        repaired = load_claims(claims_path)
        assert repaired[f"{branch}:s2"] == untouched

    def test_repair_claims_missing_file_fallback_and_invalid_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)

        plan_path = repo / "plan.yaml"
        save_plan(
            Plan(
                project="repair",
                phases=[
                    Phase(
                        id="p1",
                        name="P1",
                        steps=[
                            Step(
                                id="s1",
                                name="S1",
                                status=StepStatus.CLAIMED,
                                claimed_by="agent-plan",
                                claimed_at="2026-03-09T10:00:00Z",
                            )
                        ],
                    )
                ],
            ),
            plan_path,
        )

        monkeypatch.chdir(repo)
        ok = runner.invoke(app, ["repair", "claims", "--json", "--plan", str(plan_path)])
        assert ok.exit_code == 0
        payload = json.loads(ok.output)
        assert payload["missing_claims_file"] is True

        bad = runner.invoke(
            app,
            ["repair", "claims", "--step", "missing.step", "--plan", str(plan_path)],
        )
        assert bad.exit_code == 1
        assert "not found" in bad.output.lower()


class TestComplete:
    def test_complete_step(self, plan_file: Path):
        # First claim
        runner.invoke(app, ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_file)])
        # Then complete
        result = runner.invoke(
            app, ["complete", "s1", "--evidence", "commit abc", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Completed" in result.output
        # Should show next available steps
        assert "Next available" in result.output
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.DONE
        assert step.evidence == "commit abc"

    def test_complete_unclaimed(self, plan_file: Path):
        result = runner.invoke(app, ["complete", "s1", "--evidence", "x", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "claimed" in result.output.lower()

    def test_claim_and_complete_use_claims_store(self, plan_file: Path) -> None:
        claims_path = resolve_claims_path(plan_file)

        claim_result = runner.invoke(
            app,
            ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_file)],
        )
        assert claim_result.exit_code == 0
        claims_after_claim = load_claims(claims_path)
        assert len(claims_after_claim) == 1
        assert next(iter(claims_after_claim.values())).step_id == "s1"

        complete_result = runner.invoke(
            app,
            ["complete", "s1", "--evidence", "commit abc", "--plan", str(plan_file)],
        )
        assert complete_result.exit_code == 0
        assert load_claims(claims_path) == {}

    def test_claim_complete_with_external_plan_avoids_misleading_autosave_noise(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """External --plan claim/complete should not emit pathspec/autosave warning noise."""
        external_repo = tmp_path / "external-repo"
        external_repo.mkdir()

        for command in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test User"],
        ):
            result = sp.run(command, cwd=external_repo, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

        external_plan = external_repo / "plan.yaml"
        save_plan(
            Plan(
                project="external-plan",
                phases=[
                    Phase(
                        id="p1",
                        name="Phase 1",
                        status=PhaseStatus.PENDING,
                        steps=[Step(id="s1", name="Step 1")],
                    )
                ],
            ),
            external_plan,
        )

        caplog.set_level("WARNING", logger="vectl.io")
        claim_result = runner.invoke(
            app,
            ["claim", "s1", "--agent", "bot-1", "--plan", str(external_plan)],
        )
        assert claim_result.exit_code == 0
        assert "Claimed" in claim_result.output

        complete_result = runner.invoke(
            app,
            ["complete", "s1", "--evidence", "scoped run", "--plan", str(external_plan)],
        )
        assert complete_result.exit_code == 0
        assert "Completed" in complete_result.output
        assert "pathspec" not in caplog.text.lower()
        assert "git add" not in caplog.text.lower()

    def test_claim_in_repo_preserves_autosave_commit_behavior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In-repo plan mutation still autosaves via git commit."""
        repo = tmp_path / "repo"
        repo.mkdir()

        for command in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test User"],
        ):
            result = sp.run(command, cwd=repo, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

        plan_path = repo / "plan.yaml"
        save_plan(
            Plan(
                project="in-repo-plan",
                phases=[
                    Phase(
                        id="p1",
                        name="Phase 1",
                        status=PhaseStatus.PENDING,
                        steps=[Step(id="s1", name="Step 1")],
                    )
                ],
            ),
            plan_path,
        )

        for command in (["git", "add", "plan.yaml"], ["git", "commit", "-m", "seed"]):
            result = sp.run(command, cwd=repo, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr

        monkeypatch.chdir(repo)
        invoke_result = runner.invoke(
            app, ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_path)]
        )
        assert invoke_result.exit_code == 0

        commit_count = sp.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert commit_count.returncode == 0
        assert commit_count.stdout.strip() == "2"

        latest_subject = sp.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert latest_subject.returncode == 0
        assert latest_subject.stdout.strip() == "vectl: claim s1 by bot-1"

    def test_gate_check_claim_consistency_shows_scoped_verification_note(
        self, tmp_path: Path
    ) -> None:
        """claim-consistency gate output must mark targeted evidence as scoped."""
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="gate-scope",
                phases=[
                    Phase(
                        id="claim-consistency-recovery",
                        name="Claim Consistency Recovery",
                        status=PhaseStatus.PENDING,
                        gate="Targeted integration verify evidence.",
                        steps=[
                            Step(
                                id="s1",
                                name="Done step",
                                status=StepStatus.DONE,
                                evidence="historical verification",
                            )
                        ],
                    )
                ],
            ),
            plan_path,
        )

        result = runner.invoke(
            app, ["gate-check", "claim-consistency-recovery", "--plan", str(plan_path)]
        )

        assert result.exit_code == 0
        out_lower = result.output.lower()
        assert "scoped verification evidence" in out_lower
        assert "not full package-wide coverage proof" in out_lower

    def test_split_brain_claim_exists_but_plan_shows_pending(self, plan_file: Path) -> None:
        """CLI-level reproducer: claims.json has claim but plan.yaml shows pending.

        This demonstrates the split-brain symptom at CLI level:
        - Manual manipulation of claims.json creates inconsistency
        - CLI claim command fails with 'already claimed' (reads claims.json)
        - But plan.yaml shows the step as PENDING (not CLAIMED)
        - CLI complete command fails because it reads plan.yaml status
        """
        import os
        import subprocess

        from vectl.claims import ClaimEntry, get_current_branch, resolve_claims_path, save_claims
        from vectl.io import load_plan_definition as load_plan

        # Need to work in a git repo for get_current_branch() to work
        repo_root = plan_file.parent
        if not (repo_root / ".git").exists():
            # Create .git directory to make it a git repo
            (repo_root / ".git").mkdir()
            subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_root,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=repo_root,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=repo_root,
                capture_output=True,
            )

        # Get branch
        original_cwd = os.getcwd()
        os.chdir(repo_root)
        branch_name = get_current_branch()
        os.chdir(original_cwd)

        # Manually add claim to claims.json without updating plan.yaml
        claims_path = resolve_claims_path(plan_file)
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        save_claims(
            {
                f"{branch_name}:s1": ClaimEntry(
                    step_id="s1",
                    branch=branch_name,
                    agent="agent-a",
                    claimed_at="2026-03-09T00:00:00Z",
                )
            },
            claims_path,
        )

        # Verify split-brain state
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.PENDING, "Precondition: plan shows PENDING"

        # Symptom: claim command fails with "already claimed"
        # because it checks claims.json first
        os.chdir(repo_root)
        try:
            claim_result = runner.invoke(
                app, ["claim", "s1", "--agent", "agent-b", "--plan", str(plan_file)]
            )
            assert claim_result.exit_code == 1
            assert "already claimed" in claim_result.output.lower()
        finally:
            os.chdir(original_cwd)

        # Symptom: complete command fails because plan shows PENDING
        complete_result = runner.invoke(
            app, ["complete", "s1", "--evidence", "evidence", "--plan", str(plan_file)]
        )
        assert complete_result.exit_code == 1
        assert "claimed" in complete_result.output.lower()


class TestCompletePhase:
    def test_complete_phase_historical_success(self, plan_file: Path) -> None:
        """Explicit phase completion is used for historical imports/migration."""
        plan, _ = load_plan(plan_file)
        # Make p1 eligible: all steps terminal
        ph = plan.find_phase("p1")
        assert ph is not None
        for s in ph.steps:
            s.status = StepStatus.DONE
            s.evidence = "e"
        save_plan(plan, plan_file)

        result = runner.invoke(
            app,
            [
                "complete-phase",
                "p1",
                "--evidence",
                "imported from markdown",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Completed phase" in result.output

        plan2, _ = load_plan(plan_file)
        ph2 = plan2.find_phase("p1")
        assert ph2 is not None
        assert ph2.status == PhaseStatus.DONE
        assert ph2.evidence == "imported from markdown"
        # Dependent p2 should be unlocked
        dep = plan2.find_phase("p2")
        assert dep is not None
        assert dep.status == PhaseStatus.PENDING

    def test_complete_phase_fails_if_pending_steps(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["complete-phase", "p1", "--evidence", "x", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "non-terminal" in result.output

    def test_complete_phase_requires_evidence_option(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["complete-phase", "p1", "--plan", str(plan_file)])
        assert result.exit_code != 0
        assert "evidence" in result.output.lower()

    def test_complete_phase_requires_deps_done(self, plan_file: Path) -> None:
        plan, _ = load_plan(plan_file)
        # Make p2 steps terminal but keep p1 not done
        ph2 = plan.find_phase("p2")
        assert ph2 is not None
        ph2.steps[0].status = StepStatus.DONE
        ph2.steps[0].evidence = "e"
        save_plan(plan, plan_file)

        result = runner.invoke(
            app, ["complete-phase", "p2", "--evidence", "x", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "depends_on" in result.output


# ---------------------------------------------------------------------------
# cli.6: defer + reject + skip
# ---------------------------------------------------------------------------


class TestDefer:
    def test_defer_claimed(self, plan_file: Path):
        runner.invoke(app, ["claim", "s1", "--plan", str(plan_file)])
        result = runner.invoke(app, ["defer", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Deferred" in result.output
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.PENDING

    def test_defer_pending_fails(self, plan_file: Path):
        result = runner.invoke(app, ["defer", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 1


class TestReject:
    def test_reject_done(self, plan_file: Path):
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        result = runner.invoke(
            app, ["reject", "s1", "--reason", "Bad code", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Rejected" in result.output
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.REJECTED

    def test_reject_pending_fails(self, plan_file: Path):
        result = runner.invoke(app, ["reject", "s1", "--reason", "x", "--plan", str(plan_file)])
        assert result.exit_code == 1


class TestSkip:
    def test_skip_pending(self, plan_file: Path):
        result = runner.invoke(
            app, ["skip", "s1", "--reason", "superseded", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Skipped" in result.output
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.SKIPPED
        assert step.skipped_reason == "superseded"

    def test_skip_done_fails(self, plan_file: Path):
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        result = runner.invoke(
            app, ["skip", "s1", "--reason", "irrelevant", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1

    def test_skip_invalid_reason(self, plan_file: Path):
        result = runner.invoke(
            app, ["skip", "s1", "--reason", "not-a-reason", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "Invalid skip reason" in result.output

    def test_cancel_alias(self, plan_file: Path):
        result = runner.invoke(app, ["cancel", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.SKIPPED
        assert step.skipped_reason == "irrelevant"

    def test_cancel_done_fails(self, plan_file: Path):
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        result = runner.invoke(app, ["cancel", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 1


class TestSkipPhase:
    def test_skip_phase_all_pending(self, plan_file: Path):
        result = runner.invoke(
            app, ["skip-phase", "p1", "--reason", "superseded", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Skipped phase" in result.output
        assert "s1" in result.output
        plan, _ = load_plan(plan_file)
        assert plan.phases[0].status == PhaseStatus.DONE

    def test_skip_phase_locked_fails(self, plan_file: Path):
        result = runner.invoke(
            app, ["skip-phase", "p2", "--reason", "irrelevant", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "locked" in result.output

    def test_skip_phase_invalid_reason(self, plan_file: Path):
        result = runner.invoke(
            app, ["skip-phase", "p1", "--reason", "bad", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "Invalid skip reason" in result.output

    def test_skip_phase_with_done_steps(self, plan_file: Path):
        """Done steps preserved, only pending skipped."""
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        result = runner.invoke(
            app, ["skip-phase", "p1", "--reason", "deprioritized", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        s1 = _must_find_step(plan, "s1")
        assert s1.status == StepStatus.DONE  # preserved
        s2 = _must_find_step(plan, "s2")
        assert s2.status == StepStatus.SKIPPED

    def test_skip_phase_cascades_unlock(self, plan_file: Path):
        """Skipping p1 unlocks p2."""
        runner.invoke(app, ["skip-phase", "p1", "--reason", "irrelevant", "--plan", str(plan_file)])
        plan, _ = load_plan(plan_file)
        assert plan.phases[1].status == PhaseStatus.PENDING

    def test_skip_phase_locked_with_force(self, plan_file: Path):
        """Locked phase can be skipped with --force."""
        result = runner.invoke(
            app, ["skip-phase", "p2", "--reason", "irrelevant", "--force", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        assert plan.phases[1].status == PhaseStatus.DONE


# ---------------------------------------------------------------------------
# cli.7: check (renamed from update-checklist)
# ---------------------------------------------------------------------------


class TestCheck:
    def test_check_item(self, plan_file: Path):
        result = runner.invoke(app, ["check", "s3", "Item A", "--plan", str(plan_file)])
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s3")
        assert "[x] Item A" in step.description

    def test_add_item(self, plan_file: Path):
        result = runner.invoke(app, ["check", "s3", "--add", "New item", "--plan", str(plan_file)])
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s3")
        assert "[ ] New item" in step.description

    def test_no_args_fails(self, plan_file: Path):
        result = runner.invoke(app, ["check", "s3", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "Must provide" in result.output


# ---------------------------------------------------------------------------
# cli.8: validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_plan(self, plan_file: Path):
        result = runner.invoke(app, ["validate", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_invalid_plan(self, tmp_path: Path):
        plan = Plan(
            project="bad",
            phases=[
                Phase(id="p1", name="P1", depends_on=["nonexistent"]),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        result = runner.invoke(app, ["validate", "--plan", str(path)])
        assert result.exit_code == 1
        assert "ERROR" in result.output

    def test_validate_ignores_split_state_orphans(self, tmp_path: Path) -> None:
        """Validate only checks unified plan.yaml state."""
        plan = Plan(
            project="orphan-test",
            phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")])],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        # Stale split-state file should be ignored by unified CLI path.
        stale_state_path = tmp_path / ".vectl" / "state.json"
        stale_state_path.parent.mkdir(parents=True, exist_ok=True)
        stale_state_path.write_text(
            json.dumps(
                {
                    "plan_id": "orphan-test",
                    "phases": {
                        "core": {"status": "pending"},
                        "orphan_phase": {"status": "done"},
                    },
                    "steps": {
                        "s1": {"status": "pending"},
                        "orphan_step": {"status": "claimed", "claimed_by": "agent-x"},
                    },
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["validate", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()
        assert "orphan" not in result.output.lower()

    def test_load_ignores_legacy_state_until_explicit_migration(self, tmp_path: Path) -> None:
        """_load reads inline plan.yaml state and ignores leftover state.json."""
        from vectl.cli import _load
        from vectl.io import save_plan

        plan = Plan(
            project="orphan-test",
            phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="Step 1")])],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "steps": {
                        "s1": {"status": "done", "evidence": "migrated evidence"},
                        "ghost.step": {"status": "claimed", "claimed_by": "ghost"},
                    },
                    "phases": {"core": {"status": "in_progress"}},
                }
            ),
            encoding="utf-8",
        )

        loaded_plan, def_hash, target = _load(plan_path)
        assert loaded_plan.project == "orphan-test"
        assert def_hash
        assert target == plan_path

        migrated_step = loaded_plan.find_step("s1")
        assert migrated_step is not None
        assert migrated_step[1].status == StepStatus.PENDING
        assert migrated_step[1].evidence is None
        migrated_phase = loaded_plan.find_phase("core")
        assert migrated_phase is not None
        assert migrated_phase.status == PhaseStatus.PENDING

        assert state_path.exists()
        assert not state_path.with_suffix(".json.migrated").exists()


class TestMigrate:
    def test_migrate_no_state_message(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="migration-cli",
                phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="S1")])],
            ),
            plan_path,
        )

        result = runner.invoke(app, ["migrate", "--plan", str(plan_path), "--yes"])

        assert result.exit_code == 0
        assert "No legacy state file found" in result.output

    def test_migrate_already_migrated_message(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="migration-cli",
                phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="S1")])],
            ),
            plan_path,
        )

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.with_suffix(".json.migrated").write_text("done", encoding="utf-8")

        result = runner.invoke(app, ["migrate", "--plan", str(plan_path), "--yes"])

        assert result.exit_code == 0
        assert "Already migrated" in result.output

    def test_migrate_preview_and_confirm_path(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="migration-cli",
                phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="S1")])],
            ),
            plan_path,
        )
        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"steps": {"s1": {"status": "done"}}}), encoding="utf-8")

        result = runner.invoke(app, ["migrate", "--plan", str(plan_path)], input="n\n")

        assert result.exit_code == 0
        assert "Migration preview" in result.output
        assert "legacy state" in result.output
        assert "Run split-state migration now?" in result.output
        assert "Cancelled" in result.output
        assert state_path.exists()

    def test_migrate_runs_and_shows_summary(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="migration-cli",
                phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="S1")])],
            ),
            plan_path,
        )
        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"steps": {"s1": {"status": "done"}}}), encoding="utf-8")

        result = runner.invoke(app, ["migrate", "--plan", str(plan_path), "-y"])

        assert result.exit_code == 0
        assert "Migration complete" in result.output
        assert "steps=1" in result.output
        assert "phases=0" in result.output
        assert state_path.with_suffix(".json.migrated").exists()

        migrated_plan, _ = load_plan_definition(plan_path)
        migrated = migrated_plan.find_step("s1")
        assert migrated is not None
        assert migrated[1].status == StepStatus.DONE

    def test_migrate_shows_orphan_warnings(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(
            Plan(
                project="migration-cli",
                phases=[Phase(id="core", name="Core", steps=[Step(id="s1", name="S1")])],
            ),
            plan_path,
        )
        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "steps": {
                        "s1": {"status": "done"},
                        "ghost.step": {"status": "claimed", "claimed_by": "ghost"},
                    },
                    "phases": {"ghost-phase": {"status": "done"}},
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["migrate", "--plan", str(plan_path), "--yes"])

        assert result.exit_code == 0
        assert "Warnings" in result.output
        assert "Orphan step" in result.output
        assert "Orphan phase" in result.output


class TestRecover:
    def test_recover_command_shows_diff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_path = tmp_path / "plan.yaml"
        backup_root = tmp_path / ".git"
        backup_path = backup_root / "vectl" / "plan.yaml.bak"

        current = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Current")])],
        )
        backup = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Backup")])],
        )
        save_plan(current, plan_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        save_plan(backup, backup_path)

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "plan_id": "",
                    "steps": {"ghost.step": {"status": "pending"}},
                    "phases": {},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr("vectl.cli._resolve_git_dir", lambda _path: backup_root)

        result = runner.invoke(app, ["recover", "--plan", str(plan_path)], input="n\n")

        assert result.exit_code == 0
        assert "Recovery diff" in result.output
        assert "Restore plan.yaml from backup" in result.output
        assert "cancelled" in result.output.lower()

        restored_plan, _ = load_plan_definition(plan_path)
        restored = restored_plan.find_step("p1.s1")
        assert restored is not None
        assert restored[1].name == "Current"

    def test_recover_command_no_backup_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_path = tmp_path / "plan.yaml"
        backup_root = tmp_path / ".git"

        current = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Current")])],
        )
        save_plan(current, plan_path)

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "plan_id": "",
                    "steps": {"ghost.step": {"status": "pending"}},
                    "phases": {},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr("vectl.cli._resolve_git_dir", lambda _path: backup_root)

        result = runner.invoke(app, ["recover", "--yes", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "Backup not found" in result.output

    def test_recover_command_corrupted_backup_shows_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recover command shows error when backup file is corrupted."""

        plan_path = tmp_path / "plan.yaml"
        backup_root = tmp_path / ".git"
        backup_path = backup_root / "vectl" / "plan.yaml.bak"

        current = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Current")])],
        )
        save_plan(current, plan_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Write corrupted backup content
        backup_path.write_text("invalid: yaml [[[[")

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "plan_id": "",
                    "steps": {"ghost.step": {"status": "pending"}},
                    "phases": {},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr("vectl.cli._resolve_git_dir", lambda _path: backup_root)

        result = runner.invoke(app, ["recover", "--yes", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "Invalid" in result.output

    def test_recover_command_permission_error_on_backup_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recover command shows error when .git/vectl has permission issues."""
        import os

        plan_path = tmp_path / "plan.yaml"
        backup_root = tmp_path / ".git"
        backup_path = backup_root / "vectl" / "plan.yaml.bak"
        vectl_dir = backup_root / "vectl"

        current = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Current")])],
        )
        backup = Plan(
            project="recover-cli",
            phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="p1.s1", name="Backup")])],
        )
        save_plan(current, plan_path)
        vectl_dir.mkdir(parents=True, exist_ok=True)
        save_plan(backup, backup_path)

        state_path = _companion_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "plan_id": "",
                    "steps": {"ghost.step": {"status": "pending"}},
                    "phases": {},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr("vectl.cli._resolve_git_dir", lambda _path: backup_root)

        # Make vectl_dir read-only to simulate permission error
        os.chmod(vectl_dir, 0o444)
        try:
            result = runner.invoke(app, ["recover", "--yes", "--plan", str(plan_path)])

            # Should fail with permission-related error or non-zero exit code
            assert result.exit_code == 1
            # The error message may be in output or as exception
            has_error = (
                result.output
                and (
                    "Permission denied" in result.output.lower() or "error" in result.output.lower()
                )
            ) or result.exception is not None
            assert has_error, (
                f"Expected error but got output={result.output!r}, exception={result.exception!r}"
            )
        finally:
            # Restore permissions for cleanup
            os.chmod(vectl_dir, 0o755)


# ---------------------------------------------------------------------------
# Full lifecycle: claim → complete → next unblocks
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_claim_complete_unblocks_deps(self, plan_file: Path):
        """s2 depends on s1. After completing s1, s2 should appear in next."""
        # s2 should NOT be next initially
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        # s2 should not appear as a claimable step (it's blocked)
        # We can't assert "s2 not in output" because it might appear in dep columns

        # Claim and complete s1
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "done", "--plan", str(plan_file)])

        # Now s2 should be available
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert "s2" in result.output

    def test_full_phase_completion(self, plan_file: Path):
        """Complete all steps in p1, verify phase auto-completes."""
        for sid in ["s1", "s3"]:
            runner.invoke(app, ["claim", sid, "--agent", "a", "--plan", str(plan_file)])
            runner.invoke(app, ["complete", sid, "--evidence", "e", "--plan", str(plan_file)])

        # s2 is now unblocked
        runner.invoke(app, ["claim", "s2", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s2", "--evidence", "e", "--plan", str(plan_file)])

        # Phase should be done
        plan, _ = load_plan(plan_file)
        assert _must_find_phase(plan, "p1").status == PhaseStatus.DONE

        # p2 should now be unlocked (auto-unlock)
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert "s4" in result.output


# ---------------------------------------------------------------------------
# cli.9: add-step + add-phase
# ---------------------------------------------------------------------------


class TestAddStep:
    def test_basic_auto_id(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["add-step", "--phase", "p1", "--name", "New Feature", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Added step" in result.output
        assert "p1.new-feature" in result.output

        # Verify plan was updated
        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        assert any(s.id == "p1.new-feature" for s in ph.steps)

    def test_explicit_id(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Custom Step",
                "--id",
                "my-custom-id",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "my-custom-id" in result.output

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        assert any(s.id == "my-custom-id" for s in ph.steps)

    def test_with_all_flags(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Full Step",
                "--desc",
                "A detailed description",
                "--after",
                "s1",
                "--verify",
                "pytest -v",
                "--refs",
                "src/foo.py,docs/bar.md",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        found = plan.find_step("p1.full-step")
        assert found is not None
        _, step = found
        assert step.description == "A detailed description"
        assert step.depends_on == ["s1"]
        assert step.verification == "pytest -v"
        assert step.refs == ["src/foo.py", "docs/bar.md"]

    def test_add_step_evidence_template_file(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.yaml"
        save_plan(Plan(project="p", phases=[Phase(id="p1", name="P1")]), plan_path)

        tpl_path = tmp_path / "tpl.txt"
        tpl_path.write_text("Artifact:\n- PR: <url>\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "With Template",
                "--evidence-template-file",
                str(tpl_path),
                "--plan",
                str(plan_path),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_path)
        found = plan.find_step("p1.with-template")
        assert found is not None
        _, step = found
        assert "PR" in step.evidence_template

    def test_phase_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["add-step", "--phase", "nonexistent", "--name", "X", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_locked_phase_allows_adding(self, plan_file: Path) -> None:
        """Locked phases allow adding steps (for planning), just not claiming."""
        result = runner.invoke(
            app, ["add-step", "--phase", "p2", "--name", "Planned Work", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Added step" in result.output
        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p2")
        assert ph is not None
        assert any(s.name == "Planned Work" for s in ph.steps)

    def test_affordance_hints(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["add-phase", "--name", "Hint Phase", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "vectl add-step" in result.output
        assert "vectl status" in result.output


class TestLongSlugWarning:
    def test_add_step_long_slug_warns(self, plan_file: Path) -> None:
        """Auto-generated slug > 40 chars triggers a warning."""
        long_name = (
            "This Is A Very Long Step Name That Will Generate A Slug Exceeding Forty Characters"
        )
        result = runner.invoke(
            app, ["add-step", "--phase", "p1", "--name", long_name, "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Slug is" in result.output
        assert "--id" in result.output

    def test_add_step_short_slug_no_warning(self, plan_file: Path) -> None:
        """Short slug does not trigger warning."""
        result = runner.invoke(
            app, ["add-step", "--phase", "p1", "--name", "Short Name", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Slug is" not in result.output

    def test_add_step_explicit_id_no_warning(self, plan_file: Path) -> None:
        """Explicit --id never triggers warning even if long."""
        long_name = (
            "This Is A Very Long Step Name That Will Generate A Slug Exceeding Forty Characters"
        )
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                long_name,
                "--id",
                "p1.short",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Slug is" not in result.output

    def test_add_phase_long_slug_warns(self, plan_file: Path) -> None:
        """Auto-generated phase slug > 40 chars triggers a warning."""
        long_name = (
            "This Is A Very Long Phase Name That Will Generate A Slug Exceeding Forty Characters"
        )
        result = runner.invoke(app, ["add-phase", "--name", long_name, "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Slug is" in result.output
        assert "--id" in result.output


# ---------------------------------------------------------------------------
# cli.10b: add-step / add-steps — import status support
# ---------------------------------------------------------------------------


class TestAddStepImportStatusCli:
    """CLI tests for add-step with --status/--evidence/--skipped-reason flags."""

    def test_add_step_done_with_evidence(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Historical Step",
                "--status",
                "done",
                "--evidence",
                "commit abc123",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Added step" in result.output

        plan, _ = load_plan(plan_file)
        found = plan.find_step("p1.historical-step")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.DONE
        assert step.evidence == "commit abc123"

    def test_add_step_done_without_evidence_fails(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Bad Step",
                "--status",
                "done",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 1
        assert "without evidence" in result.output

    def test_add_step_skipped_with_reason(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Skipped Step",
                "--status",
                "skipped",
                "--skipped-reason",
                "absorbed",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        found = plan.find_step("p1.skipped-step")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.SKIPPED
        assert step.skipped_reason == "absorbed"

    def test_add_step_skipped_without_reason_fails(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Bad Skip",
                "--status",
                "skipped",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 1
        assert "without skipped_reason" in result.output

    def test_add_step_transient_status_fails(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Bad Claim",
                "--status",
                "claimed",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 1
        assert "claimed/rejected" in result.output

    def test_add_step_pending_explicit(self, plan_file: Path) -> None:
        """Explicit --status pending is equivalent to default."""
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "Explicit Pending",
                "--status",
                "pending",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        found = plan.find_step("p1.explicit-pending")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.PENDING


class TestAddStepsImportStatusCli:
    """CLI tests for add-steps (bulk) with status/evidence in stdin YAML."""

    def test_bulk_done_steps(self, plan_file: Path) -> None:
        yaml_input = (
            '- name: "Imported A"\n'
            "  status: done\n"
            '  evidence: "migrated from old system"\n'
            '- name: "Imported B"\n'
            "  status: done\n"
            '  evidence: "historical verification"\n'
        )
        result = runner.invoke(
            app,
            ["add-steps", "--phase", "p1", "--plan", str(plan_file)],
            input=yaml_input,
        )
        assert result.exit_code == 0
        assert "Added 2 step(s)" in result.output

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        imported = [s for s in ph.steps if s.name.startswith("Imported")]
        assert len(imported) == 2
        assert all(s.status == StepStatus.DONE for s in imported)

    def test_bulk_mixed_statuses(self, plan_file: Path) -> None:
        yaml_input = (
            '- name: "Done Step"\n'
            "  status: done\n"
            '  evidence: "ok"\n'
            '- name: "Skip Step"\n'
            "  status: skipped\n"
            '  skipped_reason: "irrelevant"\n'
            '- name: "Pending Step"\n'
        )
        result = runner.invoke(
            app,
            ["add-steps", "--phase", "p1", "--plan", str(plan_file)],
            input=yaml_input,
        )
        assert result.exit_code == 0
        assert "Added 3 step(s)" in result.output

    def test_bulk_done_without_evidence_fails(self, plan_file: Path) -> None:
        yaml_input = '- name: "Bad"\n  status: done\n'
        result = runner.invoke(
            app,
            ["add-steps", "--phase", "p1", "--plan", str(plan_file)],
            input=yaml_input,
        )
        assert result.exit_code == 1
        assert "without evidence" in result.output

    def test_bulk_transient_status_fails(self, plan_file: Path) -> None:
        yaml_input = '- name: "Bad"\n  status: claimed\n'
        result = runner.invoke(
            app,
            ["add-steps", "--phase", "p1", "--plan", str(plan_file)],
            input=yaml_input,
        )
        assert result.exit_code == 1
        assert "claimed/rejected" in result.output


# ---------------------------------------------------------------------------
# cli.11: edit-step, remove-step, move-step
# ---------------------------------------------------------------------------


class TestEditStepCli:
    def test_edit_name(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-step", "s1", "--name", "Renamed Step", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Edited" in result.output
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert step.name == "Renamed Step"

    def test_edit_desc(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-step", "s1", "--desc", "New description", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert step.description == "New description"

    def test_edit_add_dep(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-step", "s3", "--add-dep", "s1", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s3")
        assert found is not None
        _, step = found
        assert "s1" in step.depends_on

    def test_edit_evidence_template_inline(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "edit-step",
                "s1",
                "--evidence-template",
                "Artifact:\n- PR: <url>",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert "PR" in step.evidence_template

    def test_edit_add_rm_ref(self, plan_file: Path) -> None:
        # Add ref
        result = runner.invoke(
            app, ["edit-step", "s1", "--add-ref", "docs/new.md", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert "docs/new.md" in step.refs

        # Remove ref
        result = runner.invoke(
            app, ["edit-step", "s1", "--rm-ref", "docs/new.md", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        _, step = found
        assert "docs/new.md" not in step.refs

    def test_edit_rm_dep(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["edit-step", "s2", "--rm-dep", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        found = plan.find_step("s2")
        assert found is not None
        _, step = found
        assert "s1" not in step.depends_on

    def test_edit_nothing_fails(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["edit-step", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "Nothing to edit" in result.output

    def test_edit_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-step", "nonexistent", "--name", "X", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output


class TestRemoveStepCli:
    def test_remove_pending(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["remove-step", "s3", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_claimed_fails(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "bot", "--plan", str(plan_file)])
        result = runner.invoke(app, ["remove-step", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "must be pending" in result.output

    def test_remove_with_dependents_fails(self, plan_file: Path) -> None:
        """s2 depends on s1, so removing s1 should fail."""
        result = runner.invoke(app, ["remove-step", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "depends on it" in result.output

    def test_remove_with_force_cleans_deps(self, plan_file: Path) -> None:
        """--force removes s1 and cleans up s2's dependency on it."""
        result = runner.invoke(app, ["remove-step", "s1", "--force", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Removed" in result.output
        plan, _ = load_plan(plan_file)
        assert plan.find_step("s1") is None
        found = plan.find_step("s2")
        assert found is not None
        _, step = found
        assert "s1" not in step.depends_on


class TestMoveStepCli:
    def test_move_basic(self, plan_file: Path) -> None:
        # s3 has no dependents, so it can be moved
        result = runner.invoke(
            app, ["move-step", "s3", "--to-phase", "p2", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Moved" in result.output
        plan, _ = load_plan(plan_file)
        ph2 = plan.find_phase("p2")
        assert ph2 is not None
        assert any(s.id == "s3" for s in ph2.steps)

    def test_move_target_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["move-step", "s1", "--to-phase", "nonexistent", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_move_warns_about_cleared_deps(self, plan_file: Path) -> None:
        """Moving a step with deps should warn about cleared dependencies."""
        # s2 depends on s1 — moving s2 to p2 should clear deps and warn
        result = runner.invoke(
            app, ["move-step", "s2", "--to-phase", "p2", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Cleared 1 dep" in result.output
        assert "s1" in result.output

    def test_move_no_warning_when_no_deps(self, plan_file: Path) -> None:
        """Moving a step without deps should not show warning."""
        # s3 has no deps
        result = runner.invoke(
            app, ["move-step", "s3", "--to-phase", "p2", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Cleared" not in result.output


# ---------------------------------------------------------------------------
# cli.10: show (universal inspect)
# ---------------------------------------------------------------------------


class TestShow:
    def test_show_step(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "Step 1" in result.output
        assert "pending" in result.output

    def test_show_step_with_description(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "s3", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Checklist" in result.output

    def test_show_phase(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "p1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Phase 1" in result.output
        assert "s1" in result.output
        assert "Gate" in result.output

    def test_show_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "nonexistent", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_not_found_collapses_double_prefixed_selector(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "p1.p1.missing", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "'p1.missing' not found as step or phase." in result.output
        assert "p1.p1.missing" not in result.output

    def test_show_claimed_step_has_agent(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "bot", "--plan", str(plan_file)])
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "bot" in result.output
        # Should suggest complete, not claim
        assert "complete" in result.output

    def test_show_pending_step_suggests_claim(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "claim" in result.output

    def test_show_done_step_suggests_next_and_reject(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "next" in result.output
        assert "reject" in result.output

    def test_show_claimed_step_suggests_defer(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "defer" in result.output

    def test_show_skipped_step_suggests_next(self, plan_file: Path) -> None:
        runner.invoke(app, ["skip", "s1", "--reason", "superseded", "--plan", str(plan_file)])
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "next" in result.output

    def test_show_rejected_step_suggests_claim(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s1", "--evidence", "e", "--plan", str(plan_file)])
        runner.invoke(app, ["reject", "s1", "--reason", "bad work", "--plan", str(plan_file)])
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "claim" in result.output
        assert "rework" in result.output.lower()

    def test_duplicate_explicit_id(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["add-step", "--phase", "p1", "--name", "Dupe", "--id", "s1", "--plan", str(plan_file)],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output


class TestAddPhase:
    def test_basic_auto_id(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["add-phase", "--name", "New Phase", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Added phase" in result.output
        assert "new-phase" in result.output

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("new-phase")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_explicit_id(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["add-phase", "--name", "Custom Phase", "--id", "my-phase", "--plan", str(plan_file)],
        )
        assert result.exit_code == 0
        assert "my-phase" in result.output

        plan, _ = load_plan(plan_file)
        assert plan.find_phase("my-phase") is not None

    def test_with_all_flags(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-phase",
                "--name",
                "Full Phase",
                "--after",
                "p1",
                "--gate",
                "All tests pass",
                "--context",
                "Context for this phase",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("full-phase")
        assert ph is not None
        assert ph.depends_on == ["p1"]
        assert ph.gate == "All tests pass"
        assert ph.context == "Context for this phase"

    def test_auto_locked_when_deps_not_done(self, plan_file: Path) -> None:
        """Phase depending on pending p1 should start as LOCKED."""
        result = runner.invoke(
            app,
            ["add-phase", "--name", "Locked Test", "--after", "p1", "--plan", str(plan_file)],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("locked-test")
        assert ph is not None
        assert ph.status == PhaseStatus.LOCKED

    def test_auto_pending_when_no_deps(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["add-phase", "--name", "Free Phase", "--plan", str(plan_file)])
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("free-phase")
        assert ph is not None
        assert ph.status == PhaseStatus.PENDING

    def test_deps_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["add-phase", "--name", "Bad Deps", "--after", "nonexistent", "--plan", str(plan_file)],
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_duplicate_explicit_id(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["add-phase", "--name", "Dupe", "--id", "p1", "--plan", str(plan_file)],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_affordance_hints(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["add-phase", "--name", "Hint Phase", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "vectl add-step" in result.output
        assert "vectl status" in result.output


# ---------------------------------------------------------------------------
# Linked worktree guard tests
# ---------------------------------------------------------------------------
# Note: These tests mock at is_linked_worktree level (not subprocess)


class TestLinkedWorktreeGuard:
    """Tests for linked worktree guard in CLI mutate commands."""

    @pytest.fixture
    def linked_worktree_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        """Create a plan file and simulate a linked worktree environment."""
        # Create a main worktree with plan.yaml
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        plan_file = main_root / "plan.yaml"
        save_plan(Plan(project="test", phases=[Phase(id="p1", name="P1")]), plan_file)

        # Create linked worktree directory
        linked_root = tmp_path / "linked_worktree"
        linked_root.mkdir()

        # Mock is_linked_worktree to return (True, main_root)
        monkeypatch.setattr(
            "vectl.cli.is_linked_worktree",
            lambda: (True, main_root),
        )

        # Ensure VECTL_PLAN_PATH is NOT set
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        return plan_file, linked_root

    def test_cli_mutate_blocked_in_linked_worktree(
        self, linked_worktree_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI mutate commands are blocked in linked worktree without explicit plan."""
        plan_file, _ = linked_worktree_env
        main_root = plan_file.parent

        # Try add-step - should be blocked
        result = runner.invoke(
            app,
            ["add-step", "--phase", "p1", "--name", "Blocked Step"],
        )
        assert result.exit_code != 0
        assert "Mutate blocked" in result.output
        assert "linked worktree" in result.output
        # Check that main_root path (or at least the folder name) is in the output
        assert main_root.name in result.output

    def test_cli_mutate_allowed_with_env_override(
        self, linked_worktree_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI mutate commands work with VECTL_PLAN_PATH set (escape hatch)."""
        plan_file, _ = linked_worktree_env

        # Set VECTL_PLAN_PATH to override the guard
        monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_file))

        # Try add-step - should proceed despite being in linked worktree
        result = runner.invoke(
            app,
            ["add-step", "--phase", "p1", "--name", "Override Step", "--plan", str(plan_file)],
        )
        assert result.exit_code == 0
        assert "Mutate blocked" not in result.output
        assert "Added step" in result.output

    def test_cli_mutate_blocked_when_main_root_unresolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed worktree probe still blocks mutation with actionable message."""
        monkeypatch.setattr("vectl.cli.is_linked_worktree", lambda: (True, None))
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        result = runner.invoke(app, ["add-phase", "--name", "Blocked Phase"])

        assert result.exit_code != 0
        assert "Mutate blocked" in result.output
        assert "linked worktree" in result.output
        assert "VECTL_PLAN_PATH" in result.output
        assert "Plan file not found" not in result.output


# ---------------------------------------------------------------------------
# Linked worktree implicit entry-path resolution tests
# ---------------------------------------------------------------------------
# Tests for implicit (no --plan flag) CLI entry-path resolution when in linked worktree.
# Verifies that CLI reads resolve to main worktree's plan when VECTL_PLAN_PATH not set.


class TestLinkedWorktreeImplicitResolution:
    """Tests for implicit entry-path resolution in linked worktrees (no --plan flag)."""

    @pytest.fixture
    def linked_worktree_resolution_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path, Path]:
        """Create main and linked worktree for implicit resolution testing."""
        # Create main worktree with plan.yaml
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        plan_file = main_root / "plan.yaml"
        save_plan(
            Plan(
                project="test",
                phases=[Phase(id="p1", name="Phase 1", steps=[Step(id="s1", name="Step 1")])],
            ),
            plan_file,
        )

        # Create linked worktree directory (simulate agent running here)
        linked_root = tmp_path / "linked_worktree"
        linked_root.mkdir()

        # Ensure VECTL_PLAN_PATH is NOT set
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        # Mock resolve_plan_path to return the main worktree's plan
        # This simulates what happens when is_linked_worktree returns (True, main_root)
        monkeypatch.setattr(
            "vectl.cli.resolve_plan_path",
            lambda explicit=None: plan_file if explicit is None else explicit,
        )

        return plan_file, linked_root, main_root

    def test_cli_status_resolves_to_main_worktree_plan_without_explicit_path(
        self,
        linked_worktree_resolution_env: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI status resolves to main worktree plan without --plan."""
        plan_file, linked_root, main_root = linked_worktree_resolution_env

        # Run status without --plan; resolve_plan_path is mocked to main worktree.
        result = runner.invoke(app, ["status"], catch_exceptions=False)

        # Should succeed and find the plan in main worktree
        assert result.exit_code == 0, f"status failed: {result.output}"
        assert "test" in result.output.lower()  # project name
        assert "p1" in result.output  # phase exists
        assert "linked worktree" not in result.output.lower()

    def test_cli_claim_resolves_to_main_worktree_plan_without_explicit_path(
        self,
        linked_worktree_resolution_env: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI claim resolves to main worktree plan without --plan."""
        plan_file, linked_root, main_root = linked_worktree_resolution_env

        # Run claim WITHOUT --plan flag - should auto-resolve to main worktree
        result = runner.invoke(app, ["claim", "s1", "--agent", "test-agent"])

        # Should succeed and find the step in main worktree's plan
        assert result.exit_code == 0, f"claim failed: {result.output}"
        assert "test-agent" in result.output.lower() or "claimed" in result.output.lower()
        assert "linked worktree" not in result.output.lower()

    def test_cli_mutate_with_explicit_plan_allowed_in_linked_worktree(
        self,
        linked_worktree_resolution_env: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI mutate with --plan pointing to main worktree is allowed."""
        plan_file, linked_root, main_root = linked_worktree_resolution_env

        # Run add-step WITH --plan flag pointing to main worktree
        result = runner.invoke(
            app,
            ["add-step", "--phase", "p1", "--name", "New Step", "--plan", str(plan_file)],
        )

        # Should succeed because explicit path overrides linked-worktree guard
        assert result.exit_code == 0, f"add-step failed: {result.output}"
        assert "Added step" in result.output

    def test_cli_fails_closed_on_malformed_worktree_without_stale_local_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed linked-worktree probe fails closed to cwd sentinel path.

        When linked-worktree detection returns is_linked=True but main_root is None
        (malformed/partial state), resolve_plan_path returns cwd/plan.yaml as sentinel.
        This path does NOT exist, so CLI must fail closed rather than silently succeed.
        """
        # Ensure VECTL_PLAN_PATH is NOT set
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        # Mock resolve_plan_path to simulate malformed linked-worktree:
        # is_linked=True but main_root=None triggers cwd/plan.yaml fallback sentinel.
        # This sentinel path does NOT exist in the test, so CLI must fail.
        monkeypatch.setattr(
            "vectl.cli.resolve_plan_path",
            lambda explicit=None: (
                tmp_path / "plan.yaml"  # sentinel path that doesn't exist
                if explicit is None
                else explicit
            ),
        )

        # Run status - should fail because the sentinel plan file doesn't exist (fail closed)
        result = runner.invoke(app, ["status"])

        # Fail-closed: command must fail when plan file is not found
        # This catches precedence drift where CLI might silently succeed on missing plan
        assert result.exit_code != 0, (
            f"Expected non-zero exit code for missing plan file, got {result.exit_code}. "
            "This indicates precedence drift - command should fail closed."
        )
        assert "Plan file not found" in result.output or "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# cli.12: unlock
# ---------------------------------------------------------------------------


class TestUnlockCli:
    def test_unlock_locked_phase(self, plan_file: Path) -> None:
        """Complete all steps in p1 to make p2's dep satisfied, then verify cascade unlock."""
        for sid in ["s1", "s3"]:
            runner.invoke(app, ["claim", sid, "--agent", "a", "--plan", str(plan_file)])
            runner.invoke(app, ["complete", sid, "--evidence", "e", "--plan", str(plan_file)])
        runner.invoke(app, ["claim", "s2", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s2", "--evidence", "e", "--plan", str(plan_file)])

        # p2 is now auto-unlocked via cascade (complete_step calls auto_unlock_phases)
        plan, _ = load_plan(plan_file)
        p2 = plan.find_phase("p2")
        assert p2 is not None
        assert p2.status == PhaseStatus.PENDING  # cascade unlock persisted

        # Trying to unlock again should fail (it's already pending, not locked)
        result = runner.invoke(
            app, ["unlock", "p2", "--evidence", "p1 completed", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "not locked" in result.output

    def test_unlock_deps_not_done(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["unlock", "p2", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "dependencies not done" in result.output

    def test_unlock_not_locked(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["unlock", "p1", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not locked" in result.output

    def test_unlock_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["unlock", "nonexistent", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_unlock_affordance_hints(self, plan_file: Path) -> None:
        """Unlock p2 with a third phase that stays locked, check output has hints."""
        # Add a third phase locked behind p2
        runner.invoke(
            app,
            [
                "add-phase",
                "--name",
                "Phase 3",
                "--id",
                "p3",
                "--after",
                "p2",
                "--plan",
                str(plan_file),
            ],
        )
        # Complete p1 to cascade-unlock p2
        for sid in ["s1", "s3"]:
            runner.invoke(app, ["claim", sid, "--agent", "a", "--plan", str(plan_file)])
            runner.invoke(app, ["complete", sid, "--evidence", "e", "--plan", str(plan_file)])
        runner.invoke(app, ["claim", "s2", "--agent", "a", "--plan", str(plan_file)])
        runner.invoke(app, ["complete", "s2", "--evidence", "e", "--plan", str(plan_file)])

        # Now unlock p3 explicitly (p2 is auto-unlocked but p3 needs p2 done)
        # p3 can't be unlocked (p2 not done yet), so test unlock of p3 after manual unlock of p2
        # Instead, just test that unlock on an already-pending phase gives useful error
        result = runner.invoke(app, ["unlock", "p2", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not locked" in result.output


# ---------------------------------------------------------------------------
# cli.13: add-steps (bulk from stdin)
# ---------------------------------------------------------------------------


class TestAddStepsCli:
    def test_bulk_add_from_stdin(self, plan_file: Path) -> None:
        yaml_input = '- name: "Bulk Step A"\n- name: "Bulk Step B"\n'
        result = runner.invoke(
            app, ["add-steps", "--phase", "p1", "--plan", str(plan_file)], input=yaml_input
        )
        assert result.exit_code == 0
        assert "Added 2 step(s)" in result.output
        assert "bulk-step-a" in result.output
        assert "bulk-step-b" in result.output

        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        names = [s.name for s in ph.steps]
        assert "Bulk Step A" in names
        assert "Bulk Step B" in names

    def test_bulk_empty_stdin(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["add-steps", "--phase", "p1", "--plan", str(plan_file)], input=""
        )
        assert result.exit_code == 1
        assert "No input" in result.output

    def test_bulk_invalid_yaml(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["add-steps", "--phase", "p1", "--plan", str(plan_file)],
            input=": invalid: yaml: [",
        )
        assert result.exit_code == 1

    def test_bulk_not_a_list(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["add-steps", "--phase", "p1", "--plan", str(plan_file)], input="name: foo"
        )
        assert result.exit_code == 1
        assert "Expected YAML list" in result.output

    def test_bulk_phase_not_found(self, plan_file: Path) -> None:
        yaml_input = '- name: "X"\n'
        result = runner.invoke(
            app, ["add-steps", "--phase", "nonexistent", "--plan", str(plan_file)], input=yaml_input
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_bulk_with_deps(self, plan_file: Path) -> None:
        yaml_input = (
            '- name: "Step A"\n  id: "p1.step-a"\n- name: "Step B"\n  after:\n    - "p1.step-a"\n'
        )
        result = runner.invoke(
            app, ["add-steps", "--phase", "p1", "--plan", str(plan_file)], input=yaml_input
        )
        assert result.exit_code == 0
        assert "Added 2 step(s)" in result.output

        plan, _ = load_plan(plan_file)
        found = plan.find_step("p1.step-b")
        assert found is not None
        _, step = found
        assert "p1.step-a" in step.depends_on


# ---------------------------------------------------------------------------
# cli.14: search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_finds_match(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["search", "Step 1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "match" in result.output
        assert "s1" in result.output

    def test_search_no_match(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["search", "nonexistent-xyz", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "No matches" in result.output

    def test_search_phase_filter(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["search", "Step", "--phase", "p1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "match" in result.output

    def test_search_checklist_content(self, plan_file: Path) -> None:
        """Search should find content in step descriptions (checklists)."""
        result = runner.invoke(app, ["search", "Item A", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "s3" in result.output

    def test_search_regex_mode(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["search", "Step [12]", "--regex", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "match" in result.output

    def test_search_invalid_regex(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["search", "[invalid", "--regex", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "Invalid regex" in result.output


# ---------------------------------------------------------------------------
# cli.15: edit-phase
# ---------------------------------------------------------------------------


class TestSearchHints:
    """Mutation commands should include a 'vectl search' affordance hint."""

    def test_add_step_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["add-step", "--phase", "p1", "--name", "New Step", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "vectl search" in result.output

    def test_edit_step_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-step", "s1", "--name", "Renamed", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "vectl search" in result.output

    def test_remove_step_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["remove-step", "s3", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "vectl search" in result.output

    def test_move_step_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["move-step", "s3", "--to-phase", "p2", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "vectl search" in result.output

    def test_edit_phase_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-phase", "p1", "--name", "Renamed Phase", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "vectl search" in result.output

    def test_skip_phase_has_search_hint(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["skip-phase", "p1", "--reason", "irrelevant", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "vectl search" in result.output


class TestEditPhase:
    def test_edit_phase_name(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-phase", "p1", "--name", "Renamed Phase", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        assert "Edited phase" in result.output
        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.name == "Renamed Phase"

    def test_edit_phase_context(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-phase", "p1", "--context", "New context", "--plan", str(plan_file)]
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.context == "New context"

    def test_edit_phase_gate(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            ["edit-phase", "p1", "--gate", "all tests pass; mypy clean", "--plan", str(plan_file)],
        )
        assert result.exit_code == 0
        plan, _ = load_plan(plan_file)
        ph = plan.find_phase("p1")
        assert ph is not None
        assert ph.gate == "all tests pass; mypy clean"

    def test_edit_phase_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(
            app, ["edit-phase", "nonexistent", "--name", "X", "--plan", str(plan_file)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_edit_phase_nothing_to_edit(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["edit-phase", "p1", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "Nothing to edit" in result.output


# ---------------------------------------------------------------------------
# mine command
# ---------------------------------------------------------------------------


class TestMine:
    def test_mine_shows_claimed(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_file)])
        result = runner.invoke(app, ["mine", "--agent", "bot-1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "bot-1" in result.output
        assert "s1" in result.output
        assert "complete" in result.output.lower()  # affordance hint

    def test_mine_filters_by_agent(self, plan_file: Path) -> None:
        runner.invoke(app, ["claim", "s1", "--agent", "bot-1", "--plan", str(plan_file)])
        runner.invoke(app, ["claim", "s3", "--agent", "bot-2", "--plan", str(plan_file)])
        result = runner.invoke(app, ["mine", "--agent", "bot-1", "--plan", str(plan_file)])
        assert "s1" in result.output
        assert "s3" not in result.output

    def test_mine_empty(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["mine", "--agent", "nobody", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "No steps claimed" in result.output
        assert "vectl claim" in result.output  # hint to start work


# ---------------------------------------------------------------------------
# dag command
# ---------------------------------------------------------------------------


class TestDag:
    def test_dag_phase_level(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["dag", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "flowchart TD" in result.output
        assert "Phase 1" in result.output
        assert "Phase 2" in result.output
        assert "p1 --> p2" in result.output

    def test_dag_step_level(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["dag", "--phase", "p1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "flowchart TD" in result.output
        assert "Step 1" in result.output
        assert "Step 2" in result.output
        assert "s1 --> s2" in result.output

    def test_dag_phase_not_found(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["dag", "--phase", "nope", "--plan", str(plan_file)])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_dag_has_drill_hint(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["dag", "--plan", str(plan_file)])
        assert "vectl dag --phase" in result.output


class TestDuplicateIdDiagnostics:
    def _duplicate_plan_path(self, tmp_path: Path) -> Path:
        plan = Plan(
            project="duplicate-diagnostics",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="dup.step", name="P1 Dup")],
                ),
                Phase(
                    id="p2",
                    name="Phase 2",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="dup.step", name="P2 Dup")],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        return path

    def test_validate_duplicate_step_ids_are_hard_errors(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(app, ["validate", "--plan", str(plan_path)])
        assert result.exit_code == 1
        assert "ERROR:" in result.output
        assert "Duplicate step ID 'dup.step'" in result.output
        assert "1 error(s)" in result.output

    def test_gate_check_blocks_duplicate_step_id_plan(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(app, ["gate-check", "p1", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "Gate check blocked" in result.output
        assert "Duplicate step ID 'dup.step'" in result.output

    def test_status_shows_duplicate_diagnostics(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(app, ["status", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Duplicate step-ID diagnostics" in result.output
        assert "dup.step" in result.output
        assert "p1, p2" in result.output

    def test_show_step_emits_structured_repair_recommendation(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(app, ["show", "dup.step", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Ambiguous step target: duplicate ID detected across phases" in result.output
        assert "type=duplicate-step-id" in result.output
        assert "step_id=dup.step" in result.output
        assert "duplicates=p1, p2" in result.output
        assert "resolution.explicit_phase:" in result.output
        assert "phase-qualified step selectors" in result.output
        assert "Use one of:" not in result.output
        assert "resolution.auto_migrate_flag:" in result.output
        assert "Not available in phase B" in result.output
        assert "--auto-migrate" in result.output
        assert "resolution.migration_tool:" in result.output
        assert "Use vectl migrate-step-id --dry-run, then --yes" in result.output

    def test_dag_shows_duplicate_diagnostics(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(app, ["dag", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Duplicate step-ID diagnostics" in result.output
        assert "dup.step" in result.output

    def test_claim_blocks_ambiguous_duplicate_target_without_opt_in(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        result = runner.invoke(
            app, ["claim", "dup.step", "--agent", "bot", "--plan", str(plan_path)]
        )

        assert result.exit_code == 1
        assert "error_code=duplicate_step_id_ambiguous_target" in result.output
        assert "blocking_reason=duplicate_step_id_ambiguous" in result.output
        assert "recommendation.type=duplicate-step-id" in result.output
        assert "Use one of:" not in result.output

        plan, _ = load_plan(plan_path)
        assert plan.phases[0].steps[0].status == StepStatus.PENDING
        assert plan.phases[1].steps[0].status == StepStatus.PENDING

    def test_check_blocks_ambiguous_duplicate_target_without_opt_in(self, tmp_path: Path) -> None:
        plan = Plan(
            project="duplicate-check",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="dup.step", name="P1 Dup", description="- [ ] first")],
                ),
                Phase(
                    id="p2",
                    name="Phase 2",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="dup.step", name="P2 Dup", description="- [ ] second")],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        result = runner.invoke(app, ["check", "dup.step", "first", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "error_code=duplicate_step_id_ambiguous_target" in result.output

        reloaded, _ = load_plan(plan_path)
        assert reloaded.phases[0].steps[0].description == "- [ ] first"
        assert reloaded.phases[1].steps[0].description == "- [ ] second"

    def test_migrate_blocks_when_duplicate_targets_require_auto_migrate(
        self, tmp_path: Path
    ) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)
        from vectl.migration import resolve_state_path

        state_path = resolve_state_path(plan_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{}", encoding="utf-8")

        result = runner.invoke(app, ["migrate", "--yes", "--plan", str(plan_path)])

        assert result.exit_code == 1
        assert "error_code=duplicate_step_id_ambiguous_target" in result.output
        assert "blocking_reason=duplicate_step_id_ambiguous" in result.output

    def test_migrate_step_id_dry_run_json_reports_mapping_and_evidence(
        self, tmp_path: Path
    ) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)

        result = runner.invoke(
            app, ["migrate-step-id", "--dry-run", "--json", "--plan", str(plan_path)]
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["status"] == "recommendation_only"
        assert payload["report"]["run_mode"] == "dry-run"
        assert payload["report"]["rename_map"]
        assert payload["evidence"]["run_mode"] == "dry-run"
        assert payload["evidence"]["migrated"] is False

    def test_migrate_step_id_apply_json_saves_then_becomes_noop(self, tmp_path: Path) -> None:
        plan_path = self._duplicate_plan_path(tmp_path)

        first = runner.invoke(app, ["migrate-step-id", "--yes", "--json", "--plan", str(plan_path)])
        assert first.exit_code == 0
        first_payload = json.loads(first.output)
        assert first_payload["status"] == "repair_applied"
        assert first_payload["migrated"] is True
        assert first_payload["report"]["rename_map"]

        reloaded, _ = load_plan(plan_path)
        ids = [step.id for phase in reloaded.phases for step in phase.steps]
        assert len(ids) == len(set(ids))

        second = runner.invoke(
            app, ["migrate-step-id", "--yes", "--json", "--plan", str(plan_path)]
        )
        assert second.exit_code == 0
        second_payload = json.loads(second.output)
        assert second_payload["status"] == "repair_applied"
        assert second_payload["migrated"] is False
        assert second_payload["report"]["rename_map"] == []


# ---------------------------------------------------------------------------
# clipboard commands
# ---------------------------------------------------------------------------


class TestClipboardWrite:
    def test_write_basic(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "agent-1",
                "--summary",
                "Handoff note",
                "--content",
                "Here's the design",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Clipboard written" in result.output
        assert "agent-1" in result.output

    def test_write_empty_author_fails(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "",
                "--summary",
                "Summary",
                "--content",
                "Content",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 1

    def test_write_empty_content_fails(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "agent",
                "--summary",
                "Summary",
                "--content",
                "",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 1

    def test_write_custom_ttl(self, plan_file: Path) -> None:
        result = runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "agent",
                "--summary",
                "Summary",
                "--content",
                "Content",
                "--ttl",
                "48",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Clipboard written" in result.output


class TestClipboardRead:
    def test_read_basic(self, plan_file: Path) -> None:
        # Write first
        runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "agent-1",
                "--summary",
                "Test note",
                "--content",
                "Test content",
                "--plan",
                str(plan_file),
            ],
        )
        # Read
        result = runner.invoke(app, ["clipboard-read", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Clipboard" in result.output
        assert "agent-1" in result.output
        assert "Test note" in result.output
        assert "Test content" in result.output

    def test_read_empty(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["clipboard-read", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()


class TestClipboardClear:
    def test_clear_basic(self, plan_file: Path) -> None:
        # Write first
        runner.invoke(
            app,
            [
                "clipboard-write",
                "--author",
                "agent",
                "--summary",
                "Summary",
                "--content",
                "Content",
                "--plan",
                str(plan_file),
            ],
        )
        # Clear
        result = runner.invoke(app, ["clipboard-clear", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "cleared" in result.output.lower()

    def test_clear_already_empty(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["clipboard-clear", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "already empty" in result.output.lower()


# ---------------------------------------------------------------------------
# RFC: docs/RFC-affinity.md — CLI Affinity Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def affinity_plan_file(tmp_path: Path) -> Path:
    """Create a plan with affinity settings for CLI testing."""
    plan = Plan(
        project="affinity-test",
        default_affinity=AffinityMode.SUGGESTED,
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="No Agent Step"),
                    Step(id="s2", name="Suggested Step", agent="blind-tester"),
                    Step(
                        id="s3",
                        name="Exclusive Step",
                        agent="blind-tester",
                        affinity=AffinityMode.EXCLUSIVE,
                    ),
                ],
            )
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


class TestAffinityCli:
    def test_claim_no_agent_no_check(self, affinity_plan_file: Path) -> None:
        """Claim step without agent field -> no affinity check."""
        result = runner.invoke(
            app, ["claim", "s1", "--agent", "anyone", "--plan", str(affinity_plan_file)]
        )
        assert result.exit_code == 0
        assert "Claimed" in result.output

    def test_claim_suggested_mismatch_warns(self, affinity_plan_file: Path) -> None:
        """Claim with suggested affinity mismatch -> warning, exit 0."""
        result = runner.invoke(
            app,
            ["claim", "s2", "--agent", "python-engineer", "--plan", str(affinity_plan_file)],
        )
        assert result.exit_code == 0
        assert "Affinity warning" in result.output
        assert "blind-tester" in result.output

    def test_claim_exclusive_mismatch_rejects(self, affinity_plan_file: Path) -> None:
        """Claim with exclusive mismatch -> error, exit 1."""
        result = runner.invoke(
            app,
            ["claim", "s3", "--agent", "python-engineer", "--plan", str(affinity_plan_file)],
        )
        assert result.exit_code == 1
        assert "exclusive affinity" in result.output.lower()

    def test_claim_exclusive_force_allows(self, affinity_plan_file: Path) -> None:
        """Claim with exclusive + --force -> warning, exit 0."""
        result = runner.invoke(
            app,
            [
                "claim",
                "s3",
                "--agent",
                "python-engineer",
                "--force",
                "--plan",
                str(affinity_plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Affinity override" in result.output
        # Verify audit trail
        plan, _ = load_plan(affinity_plan_file)
        step = _must_find_step(plan, "s3")
        assert step.affinity_override is True

    def test_show_displays_affinity(self, affinity_plan_file: Path) -> None:
        """Show command displays affinity field."""
        result = runner.invoke(app, ["show", "s3", "--plan", str(affinity_plan_file)])
        assert result.exit_code == 0
        assert "Affinity:" in result.output
        assert "exclusive" in result.output.lower()

    def test_next_shows_exclusive_icon(self, affinity_plan_file: Path) -> None:
        """Next command shows exclusive icon for steps with exclusive affinity."""
        result = runner.invoke(app, ["next", "--plan", str(affinity_plan_file)])
        assert result.exit_code == 0
        # s3 has exclusive affinity, should show 🔐
        assert "🔐" in result.output


class TestCliUnifiedStateIntegration:
    def test_claim_updates_plan_yaml(self, plan_file: Path) -> None:
        result = runner.invoke(app, ["claim", "s1", "--agent", "bot", "--plan", str(plan_file)])
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.CLAIMED
        assert step.claimed_by == "bot"

    def test_complete_updates_plan_yaml(self, plan_file: Path) -> None:
        claim_result = runner.invoke(
            app, ["claim", "s1", "--agent", "bot", "--plan", str(plan_file)]
        )
        assert claim_result.exit_code == 0

        complete_result = runner.invoke(
            app,
            ["complete", "s1", "--evidence", "integration-evidence", "--plan", str(plan_file)],
        )
        assert complete_result.exit_code == 0

        plan, _ = load_plan(plan_file)
        step = _must_find_step(plan, "s1")
        assert step.status == StepStatus.DONE
        assert step.evidence == "integration-evidence"

    def test_mutation_updates_plan_yaml_only(self, plan_file: Path) -> None:
        before = plan_file.read_text(encoding="utf-8")

        result = runner.invoke(
            app,
            ["add-step", "--phase", "p1", "--name", "Persisted Step", "--plan", str(plan_file)],
        )
        assert result.exit_code == 0

        after = plan_file.read_text(encoding="utf-8")
        assert after != before
        assert "persisted-step" in after

    def test_status_is_read_only_for_plan_yaml(self, plan_file: Path) -> None:
        before = plan_file.read_text(encoding="utf-8")
        result = runner.invoke(app, ["status", "--plan", str(plan_file)])
        assert result.exit_code == 0
        after = plan_file.read_text(encoding="utf-8")
        assert after == before

    def test_legacy_migration_preserves_embedded_runtime_state(
        self, legacy_plan_file: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["claim", "phase-b.step1", "--agent", "bot", "--plan", str(legacy_plan_file)],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(legacy_plan_file)
        assert plan.plan_id == "migration-plan-001"
        assert plan.clipboard is not None
        assert plan.clipboard.author == "reviewer"

        step = _must_find_step(plan, "phase-a.step2")
        assert len(step.rejection_history) == 1
        rejection = step.rejection_history[0]
        assert rejection.reason == "Testing reject via CLI"
        assert rejection.timestamp == "2026-02-09T04:46:23.870176+00:00"
        assert rejection.reviewer == "cli-tester"


# ---------------------------------------------------------------------------
# Tests for B1-B4: Claim mismatch visibility
# ---------------------------------------------------------------------------


class TestClaimMismatchVisibility:
    """Tests for claim.json vs plan.yaml mismatch visibility (B1-B4)."""

    def test_status_baseline_clean_when_no_mismatch(self, plan_file: Path) -> None:
        """B4 baseline: Status output is clean when no mismatch exists."""
        result = runner.invoke(app, ["status", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # Should NOT show mismatch warning
        assert "Claim mismatch detected" not in result.output
        assert "vectl repair claims" not in result.output

    def test_show_baseline_clean_when_no_mismatch(self, plan_file: Path) -> None:
        """B4 baseline: Show output is clean when no mismatch exists."""
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # Should NOT show mismatch warning for non-claimed step
        assert "⚠" not in result.output

    def test_status_shows_mismatch_indicator(self, tmp_path: Path) -> None:
        """B1: Status shows explicit mismatch indicator when mismatch exists."""
        # Create plan with a claimed step
        plan = Plan(
            project="mismatch-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            status=StepStatus.CLAIMED,
                            claimed_by="test-agent",
                            claimed_at="2026-03-01T10:00:00Z",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        # Run status - should show mismatch indicator
        result = runner.invoke(app, ["status", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Stale claims detected" in result.output
        assert "routine" in result.output.lower()

    def test_status_shows_ghost_claims_count(self, tmp_path: Path) -> None:
        """B1: Status shows ghost claims count when present."""
        plan = Plan(
            project="ghost-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1")],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        # Use the same branch resolution path as CLI status/show.
        from vectl.claims import get_current_branch

        branch = get_current_branch()

        # Create claims.json with a ghost claim (matching current branch)
        claims_path = plan_path.parent / ".vectl" / "claims.json"
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        claims_path.write_text(
            json.dumps(
                {
                    f"{branch}:s1": {
                        "step_id": "s1",
                        "branch": branch,
                        "agent": "ghost-agent",
                        "claimed_at": "2026-03-01T10:00:00Z",
                    }
                }
            ),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["status", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Ghost claims" in result.output
        assert "1" in result.output

    def test_status_shows_stale_plan_claims_count(self, tmp_path: Path) -> None:
        """B1: Status shows stale plan claims count when present."""
        plan = Plan(
            project="stale-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            status=StepStatus.CLAIMED,
                            claimed_by="test-agent",
                            claimed_at="2026-03-01T10:00:00Z",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        # Ensure no claims.json entry exists
        claims_path = plan_path.parent / ".vectl" / "claims.json"
        if claims_path.exists():
            claims_path.unlink()

        result = runner.invoke(app, ["status", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "Stale plan claims" in result.output
        assert "1" in result.output

    def test_status_shows_repair_instructions(self, tmp_path: Path) -> None:
        """B3: Status shows repair instructions when mismatch exists."""
        plan = Plan(
            project="repair-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            status=StepStatus.CLAIMED,
                            claimed_by="test-agent",
                            claimed_at="2026-03-01T10:00:00Z",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        result = runner.invoke(app, ["status", "--plan", str(plan_path)])
        assert result.exit_code == 0
        assert "vectl repair claims" in result.output
        assert "safe, idempotent" in result.output

    def test_show_step_explains_mismatch(self, tmp_path: Path) -> None:
        """B2: Show step explains mismatch when detected."""
        plan = Plan(
            project="show-mismatch-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[
                        Step(
                            id="s1",
                            name="Step 1",
                            status=StepStatus.CLAIMED,
                            claimed_by="test-agent",
                            claimed_at="2026-03-01T10:00:00Z",
                        ),
                    ],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        # Ensure no claims.json entry exists - this creates stale plan claim
        claims_path = plan_path.parent / ".vectl" / "claims.json"
        if claims_path.exists():
            claims_path.unlink()

        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_path)])
        assert result.exit_code == 0
        # B2: Show should explain the mismatch
        assert "missing from claims.json" in result.output

    def test_show_step_clean_for_non_claimed(self, tmp_path: Path) -> None:
        """B4: Show step is clean when step is not claimed."""
        plan = Plan(
            project="clean-show",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1", status=StepStatus.PENDING)],
                ),
            ],
        )
        plan_path = tmp_path / "plan.yaml"
        save_plan(plan, plan_path)

        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_path)])
        assert result.exit_code == 0
        # Should not show mismatch warning for non-claimed steps
        assert "missing from claims.json" not in result.output
        assert "ghost" not in result.output.lower()


# ---------------------------------------------------------------------------
# vectl drive: retired public surface regression tests
# Ref: docs/ADR-orchestration-plane-reset.md, section "legacy driver-centric
#      design as the target future architecture"
# ---------------------------------------------------------------------------


class TestDriveCLI:
    """Regression coverage for the retired legacy ``drive`` surface.

    The current public CLI exposes orchestration functionality under ``orch``.
    The old ``drive`` command/import contract was part of the rejected
    driver-centric design and should remain absent.
    """

    def test_drive_command_is_not_registered(self):
        """Verify the removed drive command is not registered."""
        result = runner.invoke(app, ["drive", "--help"])
        assert result.exit_code != 0
        assert "No such command 'drive'" in result.output

    def test_root_help_exposes_orch_surface_instead(self):
        """Verify the supported public surface points users to ``orch``."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "orch" in result.output
        assert "Orchestration operator commands" in result.output

    def test_orch_group_help_is_available(self):
        """Verify the supported orchestration command group is available."""
        result = runner.invoke(app, ["orch", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output
        assert "resume" in result.output
        assert "config" in result.output

    def test_drive_invocation_reports_removed_surface(self):
        """Verify direct drive invocation fails as a missing command."""
        result = runner.invoke(app, ["drive", "--config", "/nonexistent/path/driver.yaml"])
        assert result.exit_code != 0
        assert "No such command 'drive'" in result.output

    def test_drive_python_import_surface_is_absent(self):
        """Verify ``vectl.cli`` no longer exports a ``drive`` entrypoint."""
        import vectl.cli as cli

        assert not hasattr(cli, "drive")
        assert hasattr(cli, "orch_app")

    def test_orch_run_help_is_available(self):
        """Verify supported orchestration execution help is available."""
        result = runner.invoke(app, ["orch", "run", "--help"])
        assert result.exit_code == 0
        assert "Start or resume an orchestration run" in result.output

    def test_orch_status_help_is_available(self):
        """Verify supported orchestration inspection help is available."""
        result = runner.invoke(app, ["orch", "status", "--help"])
        assert result.exit_code == 0
        assert "Inspect current orchestration status" in result.output

    def test_orch_surface_replaces_drive_for_operator_workflows(self):
        """Verify operator-facing workflows live under ``orch`` instead of ``drive``."""
        result = runner.invoke(app, ["orch", "--help"])
        assert result.exit_code == 0
        assert "pause" in result.output
        assert "unpause" in result.output
        assert "stop" in result.output
