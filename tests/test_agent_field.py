"""Tests for the agent advisory field on Step.

Covers:
  - Model: Step.agent default + serialization
  - Core: get_next_steps agent prioritization, add_step with agent, edit_step
    with agent, add_steps_bulk
  - CLI: next --agent, show displays agent, add-step --agent, edit-step --agent
  - MCP: vectl_status, vectl_show, vectl_mutate (add-step/edit-step), vectl_claim auto-select
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from vectl.cli import app
from vectl.core import add_step, add_steps_bulk, edit_step, get_next_steps
from vectl.io import load_plan, save_plan
from vectl.mcp_server import (
    vectl_claim as _vectl_claim_tool,
)
from vectl.mcp_server import (
    vectl_mutate as _vectl_mutate_tool,
)
from vectl.mcp_server import (
    vectl_show as _vectl_show_tool,
)
from vectl.mcp_server import (
    vectl_status as _vectl_status_tool,
)
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus

# FastMCP tools are plain functions in FastMCP 3.x
vectl_status = _vectl_status_tool
vectl_show = _vectl_show_tool
vectl_claim = _vectl_claim_tool
vectl_mutate = _vectl_mutate_tool

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_with_agents() -> Plan:
    """Plan with steps assigned to different agents."""
    return Plan(
        project="test",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Step 1", agent="alice"),
                    Step(id="s2", name="Step 2", agent="bob"),
                    Step(id="s3", name="Step 3"),  # unassigned
                    Step(id="s4", name="Step 4", agent="alice"),
                ],
            ),
        ],
    )


@pytest.fixture()
def plan_file(tmp_path: Path) -> Path:
    """Create a plan.yaml for CLI testing."""
    plan = Plan(
        project="test-project",
        phases=[
            Phase(
                id="p1",
                name="Phase 1",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="s1", name="Step 1", agent="alice"),
                    Step(id="s2", name="Step 2", agent="bob"),
                    Step(id="s3", name="Step 3"),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


@pytest.fixture()
def mcp_plan_file(tmp_path: Path) -> Iterator[Path]:
    """Create a temp plan file and set VECTL_PLAN_PATH for MCP tests."""
    data = {
        "project": "test-mcp",
        "phases": [
            {
                "id": "alpha",
                "name": "Alpha Phase",
                "status": "pending",
                "steps": [
                    {"id": "a.1", "name": "Alpha Step One", "agent": "alice"},
                    {"id": "a.2", "name": "Alpha Step Two", "agent": "bob"},
                    {"id": "a.3", "name": "Alpha Step Three"},  # unassigned
                ],
            },
        ],
    }
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.dump(data))
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(p)
    yield p
    if old is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old


# ===========================================================================
# MODEL TESTS
# ===========================================================================


class TestStepAgentModel:
    def test_default_is_none(self):
        s = Step(id="x", name="X")
        assert s.agent is None

    def test_set_agent(self):
        s = Step(id="x", name="X", agent="bob")
        assert s.agent == "bob"

    def test_roundtrip_yaml(self, tmp_path: Path):
        plan = Plan(
            project="t",
            phases=[
                Phase(
                    id="p1",
                    name="P1",
                    steps=[
                        Step(id="s1", name="S1", agent="alice"),
                        Step(id="s2", name="S2"),
                    ],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        loaded, _ = load_plan(path)
        assert loaded.phases[0].steps[0].agent == "alice"
        assert loaded.phases[0].steps[1].agent is None

    def test_none_not_serialized(self, tmp_path: Path):
        """Steps with agent=None should not have an 'agent' key in YAML."""
        plan = Plan(
            project="t",
            phases=[
                Phase(id="p1", name="P1", steps=[Step(id="s1", name="S1")]),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(plan, path)
        raw = path.read_text()
        # Pydantic excludes None defaults; verify agent key is absent
        assert "agent:" not in raw or "agent: alice" not in raw


# ===========================================================================
# CORE LOGIC TESTS
# ===========================================================================


class TestGetNextStepsAgent:
    def test_no_agent_returns_all(self):
        plan = _plan_with_agents()
        nexts = get_next_steps(plan)
        assert len(nexts) == 4

    def test_agent_prioritization(self):
        plan = _plan_with_agents()
        nexts = get_next_steps(plan, agent="alice")
        ids = [s.id for s in nexts]
        # alice's steps should come first
        assert ids[0] in ("s1", "s4")
        assert ids[1] in ("s1", "s4")
        # unassigned step (s3) comes next
        assert ids[2] == "s3"
        # bob's step comes last
        assert ids[3] == "s2"

    def test_agent_bob(self):
        plan = _plan_with_agents()
        nexts = get_next_steps(plan, agent="bob")
        ids = [s.id for s in nexts]
        # bob's step first, then unassigned, then alice's
        assert ids[0] == "s2"
        assert ids[1] == "s3"  # unassigned
        # alice's steps last
        assert set(ids[2:]) == {"s1", "s4"}

    def test_rejected_still_first(self):
        """Rejected steps take priority even with agent filtering."""
        plan = _plan_with_agents()
        plan.phases[0].steps[1].status = StepStatus.REJECTED  # s2 (bob)
        plan.phases[0].steps[1].rejection_reason = "Bad"
        nexts = get_next_steps(plan, agent="alice")
        # Rejected s2 should be first, even though it's assigned to bob
        assert nexts[0].id == "s2"
        assert nexts[0].status == StepStatus.REJECTED

    def test_unknown_agent_deprioritizes_all_assigned(self):
        plan = _plan_with_agents()
        nexts = get_next_steps(plan, agent="charlie")
        ids = [s.id for s in nexts]
        # Unassigned step (s3) first, then all assigned steps
        assert ids[0] == "s3"


class TestAddStepAgent:
    def test_add_step_with_agent(self):
        plan = Plan(
            project="t",
            phases=[Phase(id="p1", name="P1")],
        )
        plan, sid = add_step(plan, "p1", "New Step", agent="alice")
        found = plan.find_step(sid)
        assert found is not None
        assert found[1].agent == "alice"

    def test_add_step_without_agent(self):
        plan = Plan(
            project="t",
            phases=[Phase(id="p1", name="P1")],
        )
        plan, sid = add_step(plan, "p1", "New Step")
        found = plan.find_step(sid)
        assert found is not None
        assert found[1].agent is None


class TestAddStepsBulkAgent:
    def test_bulk_with_agent(self):
        plan = Plan(
            project="t",
            phases=[Phase(id="p1", name="P1")],
        )
        steps: list[dict[str, object]] = [
            {"name": "Step A", "agent": "alice"},
            {"name": "Step B", "agent": "bob"},
            {"name": "Step C"},
        ]
        plan, ids = add_steps_bulk(plan, "p1", steps)
        phase = plan.find_phase("p1")
        assert phase is not None
        agents = {s.id: s.agent for s in phase.steps}
        assert agents[ids[0]] == "alice"
        assert agents[ids[1]] == "bob"
        assert agents[ids[2]] is None


class TestEditStepAgent:
    def test_edit_set_agent(self):
        plan = _plan_with_agents()
        plan = edit_step(plan, "s3", agent="charlie")
        found = plan.find_step("s3")
        assert found is not None
        assert found[1].agent == "charlie"

    def test_edit_clear_agent(self):
        plan = _plan_with_agents()
        plan = edit_step(plan, "s1", agent=None)
        found = plan.find_step("s1")
        assert found is not None
        assert found[1].agent is None

    def test_edit_no_agent_change(self):
        """Passing _SENTINEL leaves agent unchanged."""
        plan = _plan_with_agents()
        plan = edit_step(plan, "s1", name="Renamed")
        found = plan.find_step("s1")
        assert found is not None
        assert found[1].agent == "alice"  # unchanged
        assert found[1].name == "Renamed"


# ===========================================================================
# CLI TESTS
# ===========================================================================


class TestCliNextAgent:
    def test_next_shows_agent(self, plan_file: Path):
        result = runner.invoke(app, ["next", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "suggested: alice" in result.output

    def test_next_agent_flag(self, plan_file: Path):
        result = runner.invoke(app, ["next", "--agent", "alice", "--all", "--plan", str(plan_file)])
        assert result.exit_code == 0
        # alice's step should appear first (before bob's)
        alice_pos = result.output.find("suggested: alice")
        bob_pos = result.output.find("suggested: bob")
        assert alice_pos < bob_pos


class TestCliShowAgent:
    def test_show_displays_agent(self, plan_file: Path):
        result = runner.invoke(app, ["show", "s1", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Suggested agent:" in result.output
        assert "alice" in result.output

    def test_show_no_agent(self, plan_file: Path):
        result = runner.invoke(app, ["show", "s3", "--plan", str(plan_file)])
        assert result.exit_code == 0
        assert "Suggested agent:" not in result.output


class TestCliAddStepAgent:
    def test_add_step_with_agent(self, plan_file: Path):
        result = runner.invoke(
            app,
            [
                "add-step",
                "--phase",
                "p1",
                "--name",
                "New Step",
                "--agent",
                "charlie",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Added step" in result.output

        # Verify it was persisted
        plan, _ = load_plan(plan_file)
        new_steps = [s for s in plan.phases[0].steps if s.agent == "charlie"]
        assert len(new_steps) == 1


class TestCliEditStepAgent:
    def test_edit_step_set_agent(self, plan_file: Path):
        result = runner.invoke(
            app,
            [
                "edit-step",
                "s3",
                "--agent",
                "charlie",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0
        assert "Edited" in result.output

        plan, _ = load_plan(plan_file)
        found = plan.find_step("s3")
        assert found is not None
        assert found[1].agent == "charlie"

    def test_edit_step_clear_agent(self, plan_file: Path):
        result = runner.invoke(
            app,
            [
                "edit-step",
                "s1",
                "--agent",
                "",
                "--plan",
                str(plan_file),
            ],
        )
        assert result.exit_code == 0

        plan, _ = load_plan(plan_file)
        found = plan.find_step("s1")
        assert found is not None
        assert found[1].agent is None


# ===========================================================================
# MCP TESTS
# ===========================================================================


class TestMcpStatusAgent:
    def test_status_shows_agent(self, mcp_plan_file: Path):
        result = vectl_status()
        assert "suggested: alice" in result

    def test_status_agent_prioritization(self, mcp_plan_file: Path):
        result = vectl_status(agent="alice")
        # alice's step should appear before bob's in next steps
        alice_pos = result.find("suggested: alice")
        bob_pos = result.find("suggested: bob")
        assert alice_pos < bob_pos


class TestMcpShowAgent:
    def test_show_step_agent(self, mcp_plan_file: Path):
        result = vectl_show(id="a.1")
        assert "**Suggested agent:** alice" in result

    def test_show_step_no_agent(self, mcp_plan_file: Path):
        result = vectl_show(id="a.3")
        assert "**Suggested agent:**" not in result


class TestMcpMutateAgent:
    def test_add_step_with_agent(self, mcp_plan_file: Path):
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="New Step",
            agent="charlie",
        )
        assert "Added step" in result

        # Verify persisted
        plan, _ = load_plan(mcp_plan_file)
        new_steps = [s for s in plan.phases[0].steps if s.agent == "charlie"]
        assert len(new_steps) == 1

    def test_edit_step_agent(self, mcp_plan_file: Path):
        result = vectl_mutate(
            action="edit-step",
            step_id="a.3",
            agent="charlie",
        )
        assert "Updated step" in result

        plan, _ = load_plan(mcp_plan_file)
        found = plan.find_step("a.3")
        assert found is not None
        assert found[1].agent == "charlie"


class TestMcpClaimAutoSelectAgent:
    def test_claim_auto_select_prioritizes_agent(self, mcp_plan_file: Path):
        """Auto-claim should prioritize steps assigned to the claiming agent."""
        result = vectl_claim(agent="bob")
        # bob's step (a.2) should be auto-selected over alice's (a.1) and unassigned (a.3)
        assert result["ok"] is True
        assert "a.2" in result["markdown"]
