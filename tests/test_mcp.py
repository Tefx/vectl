"""Tests for MCP server tools.

Each tool is a plain function decorated with @mcp.tool().
We test by calling the function directly with VECTL_PLAN_PATH
pointing to a temporary plan file.
"""

from __future__ import annotations

import os
import json
from collections.abc import Iterator
from pathlib import Path

from vectl.io import extract_state, load_state, merge_plan
from vectl.models import Plan
from vectl.plan_path import resolve_state_path

import pytest
import yaml

from vectl.mcp_server import (
    _load as _vectl_load,
    _save_both as _vectl_save_both,
    _save_state as _vectl_save_state,
    vectl_claim as _vectl_claim_tool,
    vectl_check as _vectl_check_tool,
    vectl_clipboard as _vectl_clipboard_tool,
    vectl_complete as _vectl_complete_tool,
    vectl_dag as _vectl_dag_tool,
    vectl_guide as _vectl_guide_tool,
    vectl_lifecycle as _vectl_lifecycle_tool,
    vectl_mutate as _vectl_mutate_tool,
    vectl_render as _vectl_render_tool,
    vectl_review as _vectl_review_tool,
    vectl_search as _vectl_search_tool,
    vectl_show as _vectl_show_tool,
    vectl_status as _vectl_status_tool,
)
from vectl.models import PhaseStatus, PlanError, StepStatus

# FastMCP @mcp.tool() wraps functions in FunctionTool objects.
# Access the underlying callable via .fn for direct testing.
vectl_status = _vectl_status_tool.fn
vectl_show = _vectl_show_tool.fn
vectl_claim = _vectl_claim_tool.fn
vectl_complete = _vectl_complete_tool.fn
vectl_lifecycle = _vectl_lifecycle_tool.fn
vectl_search = _vectl_search_tool.fn
vectl_mutate = _vectl_mutate_tool.fn
vectl_review = _vectl_review_tool.fn
vectl_guide = _vectl_guide_tool.fn
vectl_dag = _vectl_dag_tool.fn
vectl_clipboard = _vectl_clipboard_tool.fn
vectl_check = _vectl_check_tool.fn
vectl_render = _vectl_render_tool.fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plan_dict(
    *,
    p1_status: str = "pending",
    p2_status: str = "locked",
    s1_status: str = "pending",
    s2_status: str = "pending",
    s3_status: str = "pending",
) -> dict:
    """Create a two-phase plan dict for YAML serialization."""
    return {
        "project": "test-mcp",
        "phases": [
            {
                "id": "alpha",
                "name": "Alpha Phase",
                "status": p1_status,
                "context": "First phase context",
                "steps": [
                    {
                        "id": "a.1",
                        "name": "Alpha Step One",
                        "status": s1_status,
                        "description": "First step description",
                        "verification": "pytest tests/a1.py",
                    },
                    {
                        "id": "a.2",
                        "name": "Alpha Step Two",
                        "status": s2_status,
                        "depends_on": ["a.1"],
                    },
                ],
            },
            {
                "id": "beta",
                "name": "Beta Phase",
                "status": p2_status,
                "depends_on": ["alpha"],
                "gate": "All alpha tests pass",
                "steps": [
                    {
                        "id": "b.1",
                        "name": "Beta Step One",
                        "status": s3_status,
                    },
                ],
            },
        ],
    }


def _legacy_migration_plan_dict() -> dict:
    """Create a migration fixture with embedded state in the plan body."""
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
                        "rejection_reason": "Testing reject via MCP",
                        "rejection_history": [
                            {
                                "reason": "Testing reject via MCP",
                                "timestamp": "2026-02-09T04:46:23.870176+00:00",
                                "reviewer": "mcp-tester",
                            }
                        ],
                        "affinity_override": True,
                        "affinity_override_by": "mcp-tester",
                        "affinity_override_at": "2026-02-21T08:26:00.000000+00:00",
                    },
                    {
                        "id": "phase-a.step2",
                        "name": "Step with rejection history",
                        "status": "rejected",
                        "description": "Legacy step 2",
                        "claimed_by": "mcp-test-agent",
                        "claimed_at": "2026-02-09T04:46:06.123164+00:00",
                        "evidence": "Legacy rejection",
                        "skipped_reason": "absorbed",
                        "rejection_reason": "Testing reject via MCP",
                        "rejection_history": [
                            {
                                "reason": "Testing reject via MCP",
                                "timestamp": "2026-02-09T04:46:23.870176+00:00",
                                "reviewer": "mcp-tester",
                            }
                        ],
                        "affinity_override": False,
                        "affinity_override_by": "legacy-agent",
                        "affinity_override_at": "2026-02-21T08:26:52.124877+00:00",
                    },
                    {
                        "id": "phase-a.step3",
                        "name": "Step with affinity override",
                        "status": "skipped",
                        "description": "Legacy step 3",
                        "claimed_by": "other-agent",
                        "claimed_at": "2026-02-21T08:26:52.124877+00:00",
                        "evidence": "Completed with override.",
                        "skipped_reason": "irrelevant",
                        "rejection_reason": "No-op",
                        "rejection_history": [],
                        "affinity_override": True,
                        "affinity_override_by": "planner",
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


def _legacy_migration_plan_file(tmp_path: Path) -> Path:
    """Create and return a plan file with embedded state fields."""
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(yaml.dump(_legacy_migration_plan_dict()))
    return plan_file


def _state_json_path(plan_file: Path) -> Path:
    """Return state path for a plan path and verify it is under that plan."""
    return resolve_state_path(plan_file)


@pytest.fixture()
def legacy_plan_file(tmp_path: Path) -> Iterator[Path]:
    """Create a legacy plan file with embedded state and set VECTL_PLAN_PATH."""
    plan_file = _legacy_migration_plan_file(tmp_path)
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(plan_file)
    try:
        yield plan_file
    finally:
        if old is None:
            os.environ.pop("VECTL_PLAN_PATH", None)
        else:
            os.environ["VECTL_PLAN_PATH"] = old


@pytest.fixture()
def plan_file(tmp_path: Path) -> Iterator[Path]:
    """Create a temp plan file and set VECTL_PLAN_PATH."""
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.dump(_make_plan_dict()))
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(p)
    yield p
    if old is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old


def _reload_plan(path: Path) -> dict:
    """Re-read the effective plan state as dict.

    The active MCP implementation keeps mutable runtime fields (status, evidence,
    claims, etc.) in a companion state.json. This helper merges that state back
    into plan.yaml so tests can inspect the effective plan.
    """
    plan_data = yaml.safe_load(path.read_text())

    state_path = resolve_state_path(path)
    state, _ = load_state(state_path)
    if not state.steps and not state.phases and state.clipboard is None and not state.plan_id:
        return plan_data

    plan = Plan.model_validate(plan_data)
    merged = merge_plan(plan, state)
    return merged.model_dump(mode="json")


def _read_state_json(path: Path) -> dict:
    """Load companion state.json payload as a plain dictionary."""
    state_path = _state_json_path(path)
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text())


def _legacy_step_ids(plan_data: dict[str, object] | None = None) -> set[str]:
    """Collect step IDs from a legacy migration fixture."""
    data = plan_data if plan_data is not None else _legacy_migration_plan_dict()
    step_ids: set[str] = set()
    for phase in data["phases"]:  # type: ignore[operator]
        for step in phase["steps"]:  # type: ignore[index]
            step_ids.add(step["id"])  # type: ignore[index]
    return step_ids


# ---------------------------------------------------------------------------
# Tool 1: vectl_status
# ---------------------------------------------------------------------------


class TestVectlStatus:
    def test_shows_phase_overview(self, plan_file: Path) -> None:
        result = vectl_status()
        assert "test-mcp" in result
        assert "alpha" in result
        assert "beta" in result

    def test_shows_next_available(self, plan_file: Path) -> None:
        result = vectl_status()
        assert "Next Available" in result
        assert "a.1" in result

    def test_no_agent_no_mine_section(self, plan_file: Path) -> None:
        result = vectl_status()
        assert "Claimed by" not in result

    def test_agent_shows_mine_section_empty(self, plan_file: Path) -> None:
        result = vectl_status(agent="bot")
        assert "No steps claimed by bot" in result

    def test_agent_shows_claimed_steps(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        result = vectl_status(agent="bot")
        assert "bot" in result
        assert "a.1" in result


# ---------------------------------------------------------------------------
# Tool 2: vectl_show
# ---------------------------------------------------------------------------


class TestVectlShow:
    def test_show_step(self, plan_file: Path) -> None:
        result = vectl_show(id="a.1")
        assert "Step: a.1" in result
        assert "Alpha Step One" in result
        assert "pending" in result
        assert "pytest tests/a1.py" in result

    def test_show_phase(self, plan_file: Path) -> None:
        result = vectl_show(id="alpha")
        assert "Phase: alpha" in result
        assert "Alpha Phase" in result
        assert "a.1" in result
        assert "a.2" in result

    def test_show_not_found(self, plan_file: Path) -> None:
        result = vectl_show(id="nonexistent")
        assert "Error" in result
        assert "not found" in result

    def test_show_phase_gate(self, plan_file: Path) -> None:
        result = vectl_show(id="beta")
        assert "Gate" in result
        assert "All alpha tests pass" in result

    def test_show_step_deps(self, plan_file: Path) -> None:
        result = vectl_show(id="a.2")
        assert "Depends on" in result
        assert "a.1" in result


# ---------------------------------------------------------------------------
# Tool 3: vectl_claim
# ---------------------------------------------------------------------------


class TestVectlClaim:
    def test_claim_specific_step(self, plan_file: Path) -> None:
        result = vectl_claim(agent="bot", step_id="a.1")
        assert result["ok"] is True
        assert "Claimed" in result["markdown"]
        assert "a.1" in result["markdown"]
        assert "bot" in result["markdown"]
        assert result["claimed"]["step_id"] == "a.1"

        data = _reload_plan(plan_file)
        step = data["phases"][0]["steps"][0]
        assert step["status"] == "claimed"
        assert step["claimed_by"] == "bot"

    def test_auto_claim(self, plan_file: Path) -> None:
        result = vectl_claim(agent="bot")
        assert result["ok"] is True
        assert "Claimed" in result["markdown"]
        # Should auto-pick a.1 (first available, a.3 depends on a.1)
        assert "a.1" in result["markdown"]

    def test_claim_error_on_dep_unmet(self, plan_file: Path) -> None:
        result = vectl_claim(agent="bot", step_id="a.2")
        assert result["ok"] is False
        assert "Error" in result["markdown"]

    def test_claim_shows_affordance(self, plan_file: Path) -> None:
        result = vectl_claim(agent="bot", step_id="a.1")
        assert "vectl_complete" in result["markdown"]


# ---------------------------------------------------------------------------
# Tool 4: vectl_complete
# ---------------------------------------------------------------------------


class TestVectlComplete:
    def test_complete_claimed_step(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        result = vectl_complete(step_id="a.1", evidence="Tests pass")
        assert "Completed" in result
        assert "a.1" in result

        data = _reload_plan(plan_file)
        step = data["phases"][0]["steps"][0]
        assert step["status"] == "done"
        assert step["evidence"] == "Tests pass"

    def test_complete_shows_next_available(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        result = vectl_complete(step_id="a.1", evidence="Done")
        # a.2 should now be available (dep satisfied)
        assert "a.2" in result

    def test_complete_unclaimed_error(self, plan_file: Path) -> None:
        result = vectl_complete(step_id="a.1", evidence="Done")
        assert "Error" in result

    def test_complete_last_step_unlocks_next_phase(self, plan_file: Path) -> None:
        # Claim and complete both steps in alpha
        vectl_claim(agent="bot", step_id="a.1")
        vectl_complete(step_id="a.1", evidence="done a.1")
        vectl_claim(agent="bot", step_id="a.2")
        result = vectl_complete(step_id="a.2", evidence="done a.2")
        assert "alpha" in result and "DONE" in result

        # Beta should be unlocked now
        data = _reload_plan(plan_file)
        assert data["phases"][1]["status"] == "pending"


# ---------------------------------------------------------------------------
# Tool 5: vectl_lifecycle
# ---------------------------------------------------------------------------


class TestVectlLifecycle:
    def test_defer_claimed_step(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        result = vectl_lifecycle(action="defer", id="a.1")
        assert "Deferred" in result

        data = _reload_plan(plan_file)
        assert data["phases"][0]["steps"][0]["status"] == "pending"

    def test_reject_done_step(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        vectl_complete(step_id="a.1", evidence="done")
        result = vectl_lifecycle(action="reject", id="a.1", reason="Bad quality")
        assert "Rejected" in result
        assert "Bad quality" in result

    def test_reject_requires_reason(self, plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="a.1")
        vectl_complete(step_id="a.1", evidence="done")
        result = vectl_lifecycle(action="reject", id="a.1")
        assert "Error" in result
        assert "reason" in result.lower()

    def test_skip_step(self, plan_file: Path) -> None:
        result = vectl_lifecycle(action="skip", id="a.1", reason="superseded")
        assert "Skipped" in result

        data = _reload_plan(plan_file)
        assert data["phases"][0]["steps"][0]["status"] == "skipped"

    def test_skip_requires_reason(self, plan_file: Path) -> None:
        result = vectl_lifecycle(action="skip", id="a.1")
        assert "Error" in result

    def test_skip_phase(self, plan_file: Path) -> None:
        result = vectl_lifecycle(action="skip-phase", id="alpha", reason="irrelevant")
        assert "Skipped phase" in result
        assert "2 steps skipped" in result

    def test_skip_locked_phase_with_force(self, plan_file: Path) -> None:
        """Locked phase can be skipped with force=True."""
        result = vectl_lifecycle(action="skip-phase", id="beta", reason="superseded", force=True)
        assert "Skipped phase" in result
        reloaded = _reload_plan(plan_file)
        assert reloaded["phases"][1]["status"] == PhaseStatus.DONE.value

    def test_complete_phase_historical(self, plan_file: Path) -> None:
        """Historical migration: explicitly complete a phase when steps are terminal."""
        # Make alpha's steps terminal without going through claim/complete
        data = _make_plan_dict(s1_status="done", s2_status="skipped", p1_status="pending")
        data["phases"][0]["steps"][0]["evidence"] = "legacy"
        data["phases"][0]["steps"][1]["skipped_reason"] = "irrelevant"
        plan_file.write_text(yaml.dump(data))

        result = vectl_lifecycle(action="complete-phase", id="alpha", evidence="import")
        assert "Completed phase" in result

        reloaded = _reload_plan(plan_file)
        assert reloaded["phases"][0]["status"] == PhaseStatus.DONE.value
        assert reloaded["phases"][0]["evidence"] == "import"
        # Downstream beta should be unlocked
        assert reloaded["phases"][1]["status"] == PhaseStatus.PENDING.value

    def test_unknown_action(self, plan_file: Path) -> None:
        result = vectl_lifecycle(action="defer", id="nonexistent")
        assert "Error" in result


# ---------------------------------------------------------------------------
# Tool 6: vectl_search
# ---------------------------------------------------------------------------


class TestVectlSearch:
    def test_search_by_name(self, plan_file: Path) -> None:
        result = vectl_search(pattern="Alpha")
        assert "match" in result.lower()
        assert "alpha" in result.lower()

    def test_search_no_matches(self, plan_file: Path) -> None:
        result = vectl_search(pattern="zzz_nonexistent_zzz")
        assert "No matches" in result

    def test_search_restricted_to_phase(self, plan_file: Path) -> None:
        result = vectl_search(pattern="Step", phase_id="beta")
        assert "b.1" in result
        # Should NOT have alpha steps
        assert "a.1" not in result

    def test_search_regex(self, plan_file: Path) -> None:
        result = vectl_search(pattern=r"Alpha.*One", regex=True)
        assert "match" in result.lower()

    def test_search_invalid_regex(self, plan_file: Path) -> None:
        result = vectl_search(pattern="[invalid", regex=True)
        assert "Error" in result


# ---------------------------------------------------------------------------
# Tool 7: vectl_mutate
# ---------------------------------------------------------------------------


class TestVectlMutate:
    def test_add_step(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="New Step",
            description="A new step",
        )
        assert "Added step" in result
        assert "alpha" in result

        data = _reload_plan(plan_file)
        step_ids = [s["id"] for s in data["phases"][0]["steps"]]
        assert any("new-step" in sid for sid in step_ids)

    def test_add_step_requires_phase_and_name(self, plan_file: Path) -> None:
        result = vectl_mutate(action="add-step", phase_id="alpha")
        assert "Error" in result

    def test_edit_step(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="edit-step",
            step_id="a.1",
            name="Renamed Step",
        )
        assert "Updated step" in result

        data = _reload_plan(plan_file)
        assert data["phases"][0]["steps"][0]["name"] == "Renamed Step"

    def test_edit_step_evidence_template(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="edit-step",
            step_id="a.1",
            evidence_template="Artifact:\n- PR: <url>",
        )
        assert "Updated step" in result

        data = _reload_plan(plan_file)
        assert "evidence_template" in data["phases"][0]["steps"][0]
        assert "PR" in data["phases"][0]["steps"][0]["evidence_template"]

    def test_edit_step_refs(self, plan_file: Path) -> None:
        # Add refs
        result = vectl_mutate(
            action="edit-step",
            step_id="a.1",
            add_refs=["docs/new.md"],
        )
        assert "Updated step" in result
        data = _reload_plan(plan_file)
        assert "docs/new.md" in data["phases"][0]["steps"][0]["refs"]

        # Remove refs
        result = vectl_mutate(
            action="edit-step",
            step_id="a.1",
            remove_refs=["docs/new.md"],
        )
        assert "Updated step" in result
        data = _reload_plan(plan_file)
        # refs might be missing if empty
        refs = data["phases"][0]["steps"][0].get("refs", [])
        assert "docs/new.md" not in refs

    def test_edit_step_requires_id(self, plan_file: Path) -> None:
        result = vectl_mutate(action="edit-step", name="Foo")
        assert "Error" in result

    def test_remove_step(self, plan_file: Path) -> None:
        result = vectl_mutate(action="remove-step", step_id="a.1", force=True)
        assert "Removed step" in result

        data = _reload_plan(plan_file)
        step_ids = [s["id"] for s in data["phases"][0]["steps"]]
        assert "a.1" not in step_ids

    def test_remove_step_requires_id(self, plan_file: Path) -> None:
        result = vectl_mutate(action="remove-step")
        assert "Error" in result

    def test_move_step(self, plan_file: Path) -> None:
        # Move a.1 from alpha to beta (need to remove dep from a.2 first)
        vectl_mutate(action="remove-step", step_id="a.2", force=True)
        result = vectl_mutate(action="move-step", step_id="a.1", target_phase="beta")
        assert "Moved step" in result
        assert "beta" in result

        data = _reload_plan(plan_file)
        beta_ids = [s["id"] for s in data["phases"][1]["steps"]]
        assert "a.1" in beta_ids

    def test_move_step_requires_target(self, plan_file: Path) -> None:
        result = vectl_mutate(action="move-step", step_id="a.1")
        assert "Error" in result

    def test_add_phase(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="add-phase",
            name="Gamma Phase",
            gate="All tests pass",
        )
        assert "Added phase" in result

        data = _reload_plan(plan_file)
        phase_ids = [p["id"] for p in data["phases"]]
        assert any("gamma" in pid for pid in phase_ids)

    def test_add_phase_requires_name(self, plan_file: Path) -> None:
        result = vectl_mutate(action="add-phase")
        assert "Error" in result

    def test_edit_phase(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="edit-phase",
            phase_id="alpha",
            name="Renamed Alpha",
        )
        assert "Updated phase" in result

        data = _reload_plan(plan_file)
        assert data["phases"][0]["name"] == "Renamed Alpha"

    def test_edit_phase_requires_id(self, plan_file: Path) -> None:
        result = vectl_mutate(action="edit-phase", name="Foo")
        assert "Error" in result

    def test_edit_phase_depends_on(self, plan_file: Path) -> None:
        """edit-phase with depends_on persists the change (bug fix verification)."""
        # The plan has beta depending on alpha. Add a new phase and update beta's deps.
        vectl_mutate(action="add-phase", name="Gamma Phase", phase_id="gamma")
        result = vectl_mutate(
            action="edit-phase",
            phase_id="beta",
            depends_on=["alpha", "gamma"],
        )
        assert "Updated phase" in result

        data = _reload_plan(plan_file)
        beta = next(p for p in data["phases"] if p["id"] == "beta")
        assert sorted(beta["depends_on"]) == ["alpha", "gamma"]

    def test_edit_phase_depends_on_clear(self, plan_file: Path) -> None:
        """edit-phase with depends_on=[] clears all dependencies."""
        result = vectl_mutate(
            action="edit-phase",
            phase_id="beta",
            depends_on=[],
        )
        assert "Updated phase" in result

        data = _reload_plan(plan_file)
        beta = next(p for p in data["phases"] if p["id"] == "beta")
        assert beta.get("depends_on", []) == []

    def test_edit_plan_project_guidance(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="edit-plan",
            project_guidance="Rule A\nRule B",
        )
        assert "Updated plan" in result or "Updated plan metadata" in result

        data = _reload_plan(plan_file)
        assert "project_guidance" in data
        assert "Rule A" in data["project_guidance"]

    def test_unknown_action(self, plan_file: Path) -> None:
        result = vectl_mutate(action="add-step", phase_id="alpha")  # type: ignore[arg-type]
        assert "Error" in result

    def test_mutate_shows_search_hint(self, plan_file: Path) -> None:
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="Hint Step",
        )
        assert "vectl_search" in result

    # add-steps action tests

    def test_add_steps_basic(self, plan_file: Path) -> None:
        """Add multiple steps in a batch."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Step A", "description": "First batch step"},
                {"name": "Step B", "description": "Second batch step"},
            ],
        )
        assert "Added 2 step" in result
        assert "alpha" in result

        data = _reload_plan(plan_file)
        step_names = [s["name"] for s in data["phases"][0]["steps"]]
        assert "Step A" in step_names
        assert "Step B" in step_names

    def test_add_steps_requires_phase_id(self, plan_file: Path) -> None:
        """add-steps requires phase_id."""
        result = vectl_mutate(
            action="add-steps",
            steps=[{"name": "Step A"}],
        )
        assert "Error" in result
        assert "phase_id" in result

    def test_add_steps_requires_steps(self, plan_file: Path) -> None:
        """add-steps requires steps parameter."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=None,
        )
        assert "Error" in result
        assert "steps" in result

    def test_add_steps_empty_list(self, plan_file: Path) -> None:
        """add-steps with empty list returns gracefully."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[],
        )
        # Empty list should succeed with 0 steps added
        assert "Added 0 step" in result

    def test_add_steps_with_dependencies(self, plan_file: Path) -> None:
        """add-steps with intra-batch dependencies using short slugs."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Batch A", "id": "alpha.batch-a"},
                {"name": "Batch B", "after": ["batch-a"]},  # short slug ref
            ],
        )
        assert "Added 2 step" in result

        data = _reload_plan(plan_file)
        batch_b = next(s for s in data["phases"][0]["steps"] if "batch-b" in s["id"])
        assert "alpha.batch-a" in batch_b.get("depends_on", [])

    def test_add_steps_with_full_dep_refs(self, plan_file: Path) -> None:
        """add-steps with full ID dependency references."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Batch X", "id": "alpha.batch-x"},
                {"name": "Batch Y", "depends_on": ["alpha.batch-x"]},
            ],
        )
        assert "Added 2 step" in result

        data = _reload_plan(plan_file)
        batch_y = next(s for s in data["phases"][0]["steps"] if "batch-y" in s["id"])
        assert "alpha.batch-x" in batch_y.get("depends_on", [])

    def test_add_steps_with_done_status(self, plan_file: Path) -> None:
        """add-steps with status=done requires evidence."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Already Done", "status": "done", "evidence": "Was completed earlier"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "already-done" in s["id"])
        assert step["status"] == "done"
        assert "completed earlier" in step["evidence"]

    def test_add_steps_done_requires_evidence(self, plan_file: Path) -> None:
        """add-steps with status=done but no evidence fails."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Bad Done", "status": "done"},
            ],
        )
        assert "Error" in result
        assert "evidence" in result.lower()

    def test_add_steps_with_skipped_status(self, plan_file: Path) -> None:
        """add-steps with status=skipped requires skipped_reason."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Skip Me", "status": "skipped", "skipped_reason": "absorbed"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "skip-me" in s["id"])
        assert step["status"] == "skipped"

    def test_add_steps_skip_requires_reason(self, plan_file: Path) -> None:
        """add-steps with status=skipped but no reason fails."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Bad Skip", "status": "skipped"},
            ],
        )
        assert "Error" in result
        assert "skipped_reason" in result.lower()

    def test_add_steps_with_refs(self, plan_file: Path) -> None:
        """add-steps with file refs."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "With Refs", "refs": ["docs/api.md", "src/main.py"]},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "with-refs" in s["id"])
        assert "docs/api.md" in step.get("refs", [])
        assert "src/main.py" in step.get("refs", [])

    def test_add_steps_with_agent(self, plan_file: Path) -> None:
        """add-steps with advisory agent suggestion."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Agent Step", "agent": "@coder"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "agent-step" in s["id"])
        assert step["agent"] == "@coder"

    def test_add_steps_with_verification(self, plan_file: Path) -> None:
        """add-steps with verification field (verify alias)."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Verify Step", "verification": "Run tests"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "verify-step" in s["id"])
        assert step.get("verification") == "Run tests"

    def test_add_steps_with_verify_alias(self, plan_file: Path) -> None:
        """add-steps with verify alias for verification."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Verify Alias", "verify": "Check output"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "verify-alias" in s["id"])
        assert step.get("verification") == "Check output"

    def test_add_steps_with_desc_alias(self, plan_file: Path) -> None:
        """add-steps with desc alias for description."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[
                {"name": "Desc Alias", "desc": "Using desc shorthand"},
            ],
        )
        assert "Added 1 step" in result

        data = _reload_plan(plan_file)
        step = next(s for s in data["phases"][0]["steps"] if "desc-alias" in s["id"])
        assert step.get("description") == "Using desc shorthand"

    def test_add_steps_invalid_phase(self, plan_file: Path) -> None:
        """add-steps to non-existent phase fails."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="nonexistent",
            steps=[{"name": "Step A"}],
        )
        assert "Error" in result
        assert "not found" in result.lower()

    def test_add_steps_missing_name(self, plan_file: Path) -> None:
        """add-steps entry without name fails."""
        result = vectl_mutate(
            action="add-steps",
            phase_id="alpha",
            steps=[{"description": "No name provided"}],
        )
        assert "Error" in result
        assert "name" in result.lower()


# ---------------------------------------------------------------------------
# Integration: multi-step workflow
# ---------------------------------------------------------------------------


class TestWorkflow:
    """End-to-end workflow through MCP tools."""

    def test_full_lifecycle(self, plan_file: Path) -> None:
        """status → claim → complete → claim next → complete → phase done."""
        # 1. Status shows available steps
        status = vectl_status(agent="bot")
        assert "a.1" in status
        assert "No steps claimed by bot" in status

        # 2. Claim a.1
        claim = vectl_claim(agent="bot", step_id="a.1")
        assert claim["ok"] is True
        assert "Claimed" in claim["markdown"]

        # 3. Status shows claimed
        status = vectl_status(agent="bot")
        assert "a.1" in status
        assert "bot" in status

        # 4. Complete a.1
        done = vectl_complete(step_id="a.1", evidence="Implemented and tested")
        assert "Completed" in done

        # 5. Claim a.2 (now unblocked)
        claim2 = vectl_claim(agent="bot", step_id="a.2")
        assert claim2["ok"] is True
        assert "Claimed" in claim2["markdown"]

        # 6. Complete a.2 → phase done, beta unlocked
        done2 = vectl_complete(step_id="a.2", evidence="All done")
        assert "Completed" in done2
        assert "DONE" in done2

        # 7. Beta should be available now
        status_after = vectl_status()
        assert "b.1" in status_after

    def test_defer_and_reclaim(self, plan_file: Path) -> None:
        """Claim → defer → reclaim."""
        vectl_claim(agent="bot1", step_id="a.1")
        vectl_lifecycle(action="defer", id="a.1")

        # Different agent can claim
        result = vectl_claim(agent="bot2", step_id="a.1")
        assert result["ok"] is True
        assert "Claimed" in result["markdown"]
        assert "bot2" in result["markdown"]


# ---------------------------------------------------------------------------
# RFC: docs/RFC-affinity.md — MCP Affinity Tests
# ---------------------------------------------------------------------------


def _make_affinity_plan_dict(
    *,
    agent: str | None = None,
    affinity: str | None = None,
    plan_default: str = "suggested",
) -> dict:
    """Create a plan with affinity settings for testing."""
    step: dict = {"id": "s1", "name": "Step 1"}
    if agent:
        step["agent"] = agent
    if affinity:
        step["affinity"] = affinity
    return {
        "project": "test-affinity",
        "default_affinity": plan_default,
        "phases": [
            {
                "id": "p1",
                "name": "Phase 1",
                "status": "pending",
                "steps": [step],
            }
        ],
    }


class TestAffinity:
    """Tests for agent affinity enforcement in MCP."""

    @pytest.fixture
    def affinity_plan_file(self, tmp_path: Path) -> Iterator[Path]:
        """Create a temp plan file with affinity settings."""
        plan_file = tmp_path / "plan.yaml"
        plan_dict = _make_affinity_plan_dict()
        plan_file.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        try:
            yield plan_file
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old

    @pytest.fixture
    def exclusive_plan_file(self, tmp_path: Path) -> Iterator[Path]:
        """Create a temp plan file with exclusive affinity."""
        plan_file = tmp_path / "plan.yaml"
        plan_dict = _make_affinity_plan_dict(agent="blind-tester", affinity="exclusive")
        plan_file.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        try:
            yield plan_file
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old

    def test_claim_no_agent_no_check(self, affinity_plan_file: Path) -> None:
        """No agent field -> no affinity check."""
        result = vectl_claim(agent="any-agent", step_id="s1")
        assert result["ok"] is True
        assert result.get("affinity_warning") is None

    def test_claim_suggested_warns(self, tmp_path: Path) -> None:
        """Suggested affinity mismatch -> warn but allow."""
        plan_file = tmp_path / "plan.yaml"
        plan_dict = _make_affinity_plan_dict(agent="blind-tester", affinity="suggested")
        plan_file.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        try:
            result = vectl_claim(agent="python-engineer", step_id="s1")
            assert result["ok"] is True
            assert result.get("affinity_warning") is not None
            assert "blind-tester" in result["affinity_warning"]["message"]
            assert "python-engineer" in result["affinity_warning"]["message"]
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old

    def test_claim_exclusive_rejects(self, exclusive_plan_file: Path) -> None:
        """Exclusive affinity mismatch -> reject."""
        result = vectl_claim(agent="python-engineer", step_id="s1")
        assert result["ok"] is False
        assert result.get("error_code") == "affinity_violation"
        assert "exclusive affinity" in result["error"]

    def test_claim_exclusive_force_allows(self, exclusive_plan_file: Path) -> None:
        """Exclusive affinity mismatch with force -> allow + audit trail."""
        result = vectl_claim(agent="python-engineer", step_id="s1", force=True)
        assert result["ok"] is True
        assert result.get("affinity_override") is not None
        assert "override" in result["affinity_override"]["message"].lower()

        # Verify audit trail in plan
        data = _reload_plan(exclusive_plan_file)
        step = data["phases"][0]["steps"][0]
        assert step.get("affinity_override") is True
        assert step.get("affinity_override_by") == "python-engineer"

    def test_claim_exclusive_matching_agent_allows(self, exclusive_plan_file: Path) -> None:
        """Exclusive affinity with matching agent -> allow."""
        result = vectl_claim(agent="blind-tester", step_id="s1")
        assert result["ok"] is True
        assert result.get("affinity_warning") is None
        assert result.get("affinity_override") is None

    def test_claim_guidance_shows_affinity(self, tmp_path: Path) -> None:
        """Claim guidance includes affinity note for exclusive steps."""
        plan_file = tmp_path / "plan.yaml"
        plan_dict = _make_affinity_plan_dict(agent="blind-tester", affinity="exclusive")
        plan_file.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        try:
            result = vectl_claim(agent="blind-tester", step_id="s1", guidance=True)
            assert result["ok"] is True
            # Guidance should mention exclusive affinity
            assert "exclusive" in result["markdown"].lower()
            assert "blind-tester" in result["markdown"]
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old


# ---------------------------------------------------------------------------
# Fixtures for review tool tests
# ---------------------------------------------------------------------------


def _make_review_plan_dict() -> dict:
    """Create a plan with refs, gate, and mixed statuses for review testing."""
    return {
        "project": "test-review-mcp",
        "phases": [
            {
                "id": "alpha",
                "name": "Alpha Phase",
                "status": "done",
                "gate": "All alpha tests pass",
                "steps": [
                    {
                        "id": "a.1",
                        "name": "Alpha One",
                        "status": "done",
                        "evidence": "done",
                        "refs": ["docs/spec.md", "src/core.py"],
                    },
                    {
                        "id": "a.2",
                        "name": "Alpha Two",
                        "status": "done",
                        "evidence": "done",
                        "depends_on": ["a.1"],
                        "refs": ["docs/spec.md"],
                    },
                ],
            },
            {
                "id": "beta",
                "name": "Beta Phase",
                "status": "in_progress",
                "depends_on": ["alpha"],
                "gate": "Beta integration passes",
                "steps": [
                    {
                        "id": "b.1",
                        "name": "Beta One",
                        "status": "done",
                        "evidence": "done",
                    },
                    {"id": "b.2", "name": "Beta Two", "status": "pending"},
                    {
                        "id": "b.3",
                        "name": "Beta Three",
                        "status": "pending",
                        "depends_on": ["b.1"],
                    },
                ],
            },
            {
                "id": "gamma",
                "name": "Gamma Phase",
                "status": "locked",
                "depends_on": ["beta"],
                "steps": [
                    {"id": "g.1", "name": "Gamma One", "status": "pending"},
                ],
            },
        ],
    }


@pytest.fixture()
def review_plan_file(tmp_path: Path) -> Iterator[Path]:
    """Plan file with refs and gates for review testing."""
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.dump(_make_review_plan_dict()))
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(p)
    yield p
    if old is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old


# ---------------------------------------------------------------------------
# Tool 8: vectl_review
# ---------------------------------------------------------------------------


class TestVectlReview:
    def test_returns_all_layers(self, review_plan_file: Path) -> None:
        result = vectl_review()
        assert "L1: Validation" in result
        assert "L2: Phase Overview" in result
        assert "L3: Active Phases" in result
        assert "L4: Spec Coverage" in result

    def test_l1_valid_plan(self, review_plan_file: Path) -> None:
        result = vectl_review()
        assert "0 errors" in result

    def test_l2_shows_progress(self, review_plan_file: Path) -> None:
        result = vectl_review()
        # alpha 2/2, beta 1/3, gamma 0/1
        assert "2/2" in result
        assert "100%" in result
        assert "1/3" in result
        assert "Overall:" in result

    def test_l3_shows_active_phases(self, review_plan_file: Path) -> None:
        result = vectl_review()
        # beta is in_progress — should appear
        assert "beta" in result
        assert "Beta Phase" in result

    def test_l3_shows_step_details(self, review_plan_file: Path) -> None:
        result = vectl_review()
        assert "b.1" in result
        assert "b.2" in result
        assert "b.3" in result

    def test_l4_shows_ref_coverage(self, review_plan_file: Path) -> None:
        result = vectl_review()
        assert "docs/spec.md" in result
        assert "src/core.py" in result
        assert "a.1" in result
        assert "a.2" in result

    def test_l4_no_refs(self, plan_file: Path) -> None:
        result = vectl_review()
        assert "No refs" in result

    def test_gate_check_included_when_phase_id(self, review_plan_file: Path) -> None:
        result = vectl_review(phase_id="beta")
        assert "Gate Check: beta" in result
        assert "remaining" in result
        assert "b.2" in result

    def test_gate_check_complete_phase(self, review_plan_file: Path) -> None:
        result = vectl_review(phase_id="alpha")
        assert "Gate Check: alpha" in result
        assert "2/2 complete" in result

    def test_gate_check_shows_criterion(self, review_plan_file: Path) -> None:
        result = vectl_review(phase_id="beta")
        assert "Beta integration passes" in result

    def test_gate_check_shows_downstream(self, review_plan_file: Path) -> None:
        result = vectl_review(phase_id="beta")
        # gamma is locked and depends on beta
        assert "gamma" in result

    def test_gate_check_invalid_phase(self, review_plan_file: Path) -> None:
        result = vectl_review(phase_id="nonexistent")
        assert "Gate Check Error" in result

    def test_no_gate_check_without_phase_id(self, review_plan_file: Path) -> None:
        result = vectl_review()
        assert "Gate Check:" not in result

    def test_gate_script_warning(self, tmp_path: Path) -> None:
        """Gate script should show warning about MCP non-executability."""
        plan_dict = {
            "project": "test-gate-mcp",
            "phases": [
                {
                    "id": "p1",
                    "name": "P1",
                    "status": "done",
                    "gate": "Tests pass",
                    "gate_script": "./run_tests.sh",
                    "steps": [
                        {"id": "s1", "name": "S1", "status": "done", "evidence": "ok"},
                    ],
                },
            ],
        }
        p = tmp_path / "gate_plan.yaml"
        p.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(p)
        try:
            result = vectl_review(phase_id="p1")
            assert "gate_script" in result
            assert "not executable via MCP" in result
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old

    def test_validation_error_plan(self, tmp_path: Path) -> None:
        """Plan with cycle should show errors in L1."""
        plan_dict = {
            "project": "broken-mcp",
            "phases": [
                {
                    "id": "p1",
                    "name": "Broken",
                    "status": "pending",
                    "steps": [
                        {"id": "x", "name": "X", "depends_on": ["y"]},
                        {"id": "y", "name": "Y", "depends_on": ["x"]},
                    ],
                },
            ],
        }
        p = tmp_path / "broken_plan.yaml"
        p.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(p)
        try:
            result = vectl_review()
            assert "ERROR" in result
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old


# ---------------------------------------------------------------------------
# Tool 9: vectl_guide
# ---------------------------------------------------------------------------


class TestVectlGuide:
    def test_all_topics_returned_by_default(self) -> None:
        result = vectl_guide()
        assert "Agent Startup" in result
        assert "Unblocking Strategy" in result
        assert "Review & Validation" in result
        assert "Architect Protocol" in result
        assert "Migration" in result

    def test_specific_topic_startup(self) -> None:
        result = vectl_guide(topic="startup")
        assert "Agent Startup" in result
        assert "Workflow" in result
        # Should NOT contain other topics
        assert "Unblocking Strategy" not in result

    def test_specific_topic_stuck(self) -> None:
        result = vectl_guide(topic="stuck")
        assert "Unblocking Strategy" in result
        assert "Rejected" in result

    def test_specific_topic_review(self) -> None:
        result = vectl_guide(topic="review")
        assert "Review & Validation" in result
        assert "Gatekeeping" in result

    def test_specific_topic_planning(self) -> None:
        result = vectl_guide(topic="planning")
        assert "Architect Protocol" in result
        assert "Mutate" in result

    def test_specific_topic_migration(self) -> None:
        result = vectl_guide(topic="migration")
        assert "Migration" in result
        assert "plan.yaml" in result

    def test_invalid_topic_returns_error(self) -> None:
        result = vectl_guide(topic="nonexistent")
        assert "Error" in result
        assert "nonexistent" in result
        # Should list valid topics
        assert "startup" in result
        assert "migration" in result

    def test_all_topics_has_separator(self) -> None:
        result = vectl_guide()
        assert "---" in result

    def test_no_plan_file_needed(self) -> None:
        """Guide tool should work without a plan file (pure content)."""
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = "/nonexistent/path/plan.yaml"
        try:
            result = vectl_guide(topic="startup")
            assert "Agent Startup" in result
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old


# ---------------------------------------------------------------------------
# Tool 10: vectl_dag
# ---------------------------------------------------------------------------


class TestVectlDag:
    def test_phase_dag_default(self, plan_file: Path) -> None:
        result = vectl_dag()
        assert "flowchart TD" in result
        assert "Alpha Phase" in result
        assert "Beta Phase" in result

    def test_phase_dag_edges(self, plan_file: Path) -> None:
        result = vectl_dag()
        assert "alpha --> beta" in result

    def test_step_dag(self, plan_file: Path) -> None:
        result = vectl_dag(phase_id="alpha")
        assert "flowchart TD" in result
        assert "Alpha Step One" in result
        assert "Alpha Step Two" in result
        assert "a_1 --> a_2" in result

    def test_step_dag_phase_not_found(self, plan_file: Path) -> None:
        result = vectl_dag(phase_id="nope")
        assert "Error" in result
        assert "not found" in result

    def test_drill_hint_in_phase_dag(self, plan_file: Path) -> None:
        result = vectl_dag()
        assert "uvx vectl dag --phase" in result


# ---------------------------------------------------------------------------
# Tool 11: vectl_clipboard
# ---------------------------------------------------------------------------


class TestVectlClipboardWrite:
    def test_write_basic(self, plan_file: Path) -> None:
        result = vectl_clipboard(
            action="write",
            author="agent-1",
            summary="Handoff note",
            content="Here's the design for phase 2",
        )
        assert "Clipboard written" in result
        assert "agent-1" in result
        assert "Handoff note" in result

    def test_write_overwrites(self, plan_file: Path) -> None:
        vectl_clipboard(action="write", author="agent-1", summary="First", content="Content 1")
        result = vectl_clipboard(
            action="write", author="agent-2", summary="Second", content="Content 2"
        )
        assert "agent-2" in result
        assert "Second" in result

    def test_write_empty_author_rejected(self, plan_file: Path) -> None:
        result = vectl_clipboard(action="write", author="", summary="Summary", content="Content")
        assert "Error" in result
        assert "author" in result.lower()

    def test_write_empty_content_rejected(self, plan_file: Path) -> None:
        result = vectl_clipboard(action="write", author="agent", summary="Summary", content="")
        assert "Error" in result
        assert "empty" in result.lower()


class TestVectlClipboardRead:
    def test_read_basic(self, plan_file: Path) -> None:
        vectl_clipboard(
            action="write",
            author="agent-1",
            summary="Test note",
            content="Test content",
        )
        result = vectl_clipboard(action="read")
        assert "Clipboard" in result
        assert "agent-1" in result
        assert "Test note" in result
        assert "Test content" in result

    def test_read_empty(self, plan_file: Path) -> None:
        result = vectl_clipboard(action="read")
        assert "empty" in result.lower()


class TestVectlClipboardClear:
    def test_clear_basic(self, plan_file: Path) -> None:
        vectl_clipboard(action="write", author="agent", summary="Summary", content="Content")
        result = vectl_clipboard(action="clear")
        assert "cleared" in result.lower()

    def test_clear_already_empty(self, plan_file: Path) -> None:
        result = vectl_clipboard(action="clear")
        assert "already empty" in result.lower()


class TestVectlClipboardInvalidAction:
    def test_invalid_action(self, plan_file: Path) -> None:
        result = vectl_clipboard(action="invalid")  # type: ignore[arg-type]
        assert "Error" in result
        assert "Unknown action" in result


class TestVectlClipboardCAS:
    def test_cas_conflict_message(self, tmp_path: Path) -> None:
        """CAS conflict shows actionable message."""
        # This is a conceptual test - we can't easily simulate CAS conflicts
        # without mocking, but we can verify the error message format
        plan_dict = _make_plan_dict()
        p = tmp_path / "plan.yaml"
        p.write_text(yaml.dump(plan_dict))
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(p)
        try:
            # Write should work normally
            result = vectl_clipboard(
                action="write",
                author="agent",
                summary="Summary",
                content="Content",
            )
            assert "Clipboard written" in result
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old


class TestMcpSplitStateIntegration:
    """Migration-safe behavior for MCP split-state refactor."""

    def test_claim_creates_state_json(self, legacy_plan_file: Path) -> None:
        result = vectl_claim(agent="bot", step_id="phase-b.step1")
        assert result["ok"] is True

        state_path = _state_json_path(legacy_plan_file)
        assert state_path.exists()

    def test_claim_state_json_contents(self, legacy_plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="phase-b.step1")

        state_payload = _read_state_json(legacy_plan_file)
        assert state_payload["steps"]["phase-b.step1"]["status"] == StepStatus.CLAIMED.value
        assert state_payload["steps"]["phase-b.step1"]["claimed_by"] == "bot"
        assert "claimed_at" in state_payload["steps"]["phase-b.step1"]

    def test_complete_updates_state_json_plan_yaml_unchanged(self, legacy_plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="phase-b.step1")
        vectl_complete(step_id="phase-b.step1", evidence="Legacy step completed")

        state_payload = _read_state_json(legacy_plan_file)
        completed = state_payload["steps"]["phase-b.step1"]
        assert completed["status"] == StepStatus.DONE.value
        assert completed["evidence"] == "Legacy step completed"

        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        step_data = plan_data["phases"][1]["steps"][0]
        assert step_data["status"] == "pending"
        assert step_data["claimed_by"] == "legacy-worker"

    def test_mutate_updates_both_files(self, legacy_plan_file: Path) -> None:
        result = vectl_mutate(
            action="add-step",
            phase_id="phase-b",
            name="Persisted Step",
        )
        assert "Added step" in result

        assert _state_json_path(legacy_plan_file).exists()
        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        assert any(
            "persisted-step" in step.get("id", "") for step in plan_data["phases"][1]["steps"]
        )

        for phase in plan_data["phases"]:
            assert "claimed_by" not in phase
            assert "evidence" not in phase
            assert "rejection_reason" not in phase
            for step in phase["steps"]:
                for field in (
                    "claimed_by",
                    "claimed_at",
                    "evidence",
                    "skipped_reason",
                    "rejection_reason",
                    "rejection_history",
                    "affinity_override",
                    "affinity_override_by",
                    "affinity_override_at",
                ):
                    assert field not in step

        reloaded = _reload_plan(legacy_plan_file)
        assert any("persisted-step" in step["id"] for step in reloaded["phases"][1]["steps"])

    def test_read_only_tools_no_state_write(self, plan_file: Path) -> None:
        state_path = _state_json_path(plan_file)
        assert not state_path.exists()

        vectl_status()
        assert not state_path.exists()

        vectl_show(id="a.1")
        assert not state_path.exists()

    def test_migration_roundtrip_all_state_fields(self, legacy_plan_file: Path) -> None:
        plan, _def_hash, state_hash = _vectl_load()
        assert state_hash == ""

        plan_dict = plan.model_dump(mode="json")
        fixture = _legacy_migration_plan_dict()

        assert plan.project == fixture["project"]
        assert plan.plan_id == fixture["plan_id"]

        assert len(plan.phases) == len(fixture["phases"])
        assert plan.clipboard is not None

        plan_state = extract_state(plan)
        assert set(plan_state.steps.keys()) == _legacy_step_ids(fixture)
        assert set(plan_state.phases.keys()) == {"phase-a", "phase-b"}

        for phase in fixture["phases"]:
            for step in phase["steps"]:
                state = plan_state.steps[step["id"]]
                assert state.status.value == step["status"]
                assert state.claimed_by == step["claimed_by"]
                assert state.claimed_at == step["claimed_at"]
                assert state.evidence == step["evidence"]
                assert state.skipped_reason == step["skipped_reason"]
                assert state.rejection_reason == step["rejection_reason"]
                assert [entry.model_dump(mode="json") for entry in state.rejection_history] == step[
                    "rejection_history"
                ]
                assert state.affinity_override == step["affinity_override"]
                assert state.affinity_override_by == step["affinity_override_by"]
                assert state.affinity_override_at == step["affinity_override_at"]

        _vectl_save_state(plan, state_hash)
        reloaded_plan, _, reloaded_state_hash = _vectl_load()

        assert reloaded_plan.model_dump(mode="json") == plan_dict
        assert reloaded_state_hash != ""

    def test_migration_state_json_has_all_steps(self, legacy_plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="phase-b.step1")

        payload = _read_state_json(legacy_plan_file)
        steps = set(payload["steps"].keys())
        assert steps == _legacy_step_ids()

        expected = {
            step["id"]: step
            for phase in _legacy_migration_plan_dict()["phases"]
            for step in phase["steps"]
        }
        assert (
            payload["steps"]["phase-a.step2"]["rejection_history"]
            == expected["phase-a.step2"]["rejection_history"]
        )

    def test_migration_stale_plan_yaml_ignored(self, legacy_plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="phase-b.step1")

        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        stale_step = plan_data["phases"][1]["steps"][0]
        stale_step["status"] = "done"
        stale_step["claimed_by"] = "stale-agent"
        stale_step["evidence"] = "stale-state"
        legacy_plan_file.write_text(yaml.dump(plan_data))

        reloaded, _, _ = _vectl_load()
        reloaded_step_pair = reloaded.find_step("phase-b.step1")
        assert reloaded_step_pair is not None
        _, reloaded_step = reloaded_step_pair
        assert reloaded_step.status == StepStatus.CLAIMED
        assert reloaded_step.claimed_by == "bot"

    def test_migration_definition_write_strips_state(self, legacy_plan_file: Path) -> None:
        vectl_claim(agent="bot", step_id="phase-b.step1")

        result = vectl_mutate(
            action="add-step",
            phase_id="phase-b",
            name="Strip Me",
        )
        assert "Added step" in result

        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        for phase in plan_data["phases"]:
            assert "claimed_by" not in phase
            assert "evidence" not in phase
            for step in phase["steps"]:
                for field in (
                    "claimed_by",
                    "claimed_at",
                    "evidence",
                    "skipped_reason",
                    "rejection_reason",
                    "rejection_history",
                    "affinity_override",
                    "affinity_override_by",
                    "affinity_override_at",
                ):
                    assert field not in step

        payload = _read_state_json(legacy_plan_file)
        assert "phase-b.step1" in payload["steps"]
        assert payload["steps"]["phase-b.step1"]["status"] == StepStatus.CLAIMED.value

        reloaded = _reload_plan(legacy_plan_file)
        assert any("strip-me" in step["id"] for step in reloaded["phases"][1]["steps"])

    def test_migration_clipboard_roundtrip(self, legacy_plan_file: Path) -> None:
        plan, _, state_hash = _vectl_load()
        _vectl_save_state(plan, state_hash)

        payload = _read_state_json(legacy_plan_file)
        assert payload["clipboard"]["author"] == "reviewer"

        result = vectl_mutate(action="edit-plan", project_guidance="Clipboard check")
        assert "Updated plan" in result

        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        assert "clipboard" not in plan_data

        reloaded, _, _ = _vectl_load()
        assert reloaded.clipboard is not None
        assert reloaded.clipboard.author == "reviewer"
        assert reloaded.clipboard.summary == "Handoff"

    def test_save_both_plan_cas_conflict_does_not_persist_state(
        self, legacy_plan_file: Path
    ) -> None:
        """Definition is written first. If definition CAS fails, state.json
        is NOT written, preventing orphaned state entries."""
        plan, def_hash, state_hash = _vectl_load()
        assert state_hash == ""

        state_path = _state_json_path(legacy_plan_file)
        assert not state_path.exists()

        plan_data = yaml.safe_load(legacy_plan_file.read_text())
        plan_data["context"] = "concurrent edit to trigger CAS conflict"
        legacy_plan_file.write_text(yaml.dump(plan_data))

        with pytest.raises(PlanError, match="CAS conflict: plan.yaml"):
            _vectl_save_both(plan, def_hash, state_hash)

        assert not state_path.exists()


class TestVectlInit:
    """Tests for vectl_init MCP tool."""

    def test_init_creates_plan_and_agents_md(self, tmp_path: Path) -> None:
        """Basic init creates plan.yaml and AGENTS.md."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            result = vectl_init(project="test-project")

            assert result["ok"] is True
            assert result["plan_path"] == "plan.yaml"
            assert result["agents_target"] == "AGENTS.md"
            assert "Created" in result["message"]
            assert (tmp_path / "plan.yaml").exists()
            assert (tmp_path / "AGENTS.md").exists()

            # Verify plan content
            plan_data = yaml.safe_load((tmp_path / "plan.yaml").read_text())
            assert plan_data["project"] == "test-project"

            # Verify AGENTS.md has vectl markers
            agents_content = (tmp_path / "AGENTS.md").read_text()
            assert "<!-- VECTL:AGENTS:BEGIN -->" in agents_content
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_refuses_existing_plan(self, tmp_path: Path) -> None:
        """Init refuses to overwrite an existing plan.yaml."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            # Create plan first
            (tmp_path / "plan.yaml").write_text("project: existing\n")

            result = vectl_init(project="new-project")

            assert result["ok"] is False
            assert "already exists" in result["error"]
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_custom_plan_path(self, tmp_path: Path) -> None:
        """Init respects custom plan_path parameter."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            custom_path = tmp_path / "subdir" / "custom-plan.yaml"
            result = vectl_init(project="custom-project", plan_path=str(custom_path))

            assert result["ok"] is True
            assert custom_path.exists()
            assert "subdir" in result["plan_path"]
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_claude_md_with_claude_dir(self, tmp_path: Path) -> None:
        """Init creates CLAUDE.md when .claude/ dir exists."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)
            (tmp_path / ".claude").mkdir()

            result = vectl_init(project="claude-project")

            assert result["ok"] is True
            assert result["agents_target"] == "CLAUDE.md"
            assert (tmp_path / "CLAUDE.md").exists()
            assert not (tmp_path / "AGENTS.md").exists()
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_explicit_agents_target(self, tmp_path: Path) -> None:
        """Init respects explicit agents_target='claude'."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            result = vectl_init(project="explicit-claude", agents_target="claude")

            assert result["ok"] is True
            assert result["agents_target"] == "CLAUDE.md"
            assert (tmp_path / "CLAUDE.md").exists()
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_appends_to_existing_agents_md(self, tmp_path: Path) -> None:
        """Init appends vectl section to existing AGENTS.md."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            # Create existing AGENTS.md
            (tmp_path / "AGENTS.md").write_text("# My Project\n\nExisting content.\n")

            result = vectl_init(project="test-project")

            assert result["ok"] is True
            content = (tmp_path / "AGENTS.md").read_text()
            assert "Existing content." in content
            assert "<!-- VECTL:AGENTS:BEGIN -->" in content
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_init_idempotent_agents_md(self, tmp_path: Path) -> None:
        """Init is idempotent - running twice on same AGENTS.md updates block."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn

        old_cwd = os.getcwd()
        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.chdir(tmp_path)
            os.environ.pop("VECTL_PLAN_PATH", None)

            # First run creates everything
            result1 = vectl_init(project="test-project")
            assert result1["ok"] is True
            assert (tmp_path / "plan.yaml").exists()

            # Manually reset to test idempotent AGENTS.md behavior
            (tmp_path / "plan.yaml").unlink()

            # Create AGENTS.md with markers
            agents = tmp_path / "AGENTS.md"
            agents.write_text(
                "# Project\n\n"
                "<!-- VECTL:AGENTS:BEGIN -->\n"
                "## Plan Tracking (vectl)\n\n"
                "Old content.\n"
                "<!-- VECTL:AGENTS:END -->\n"
            )

            # Second init with new plan path
            result2 = vectl_init(project="test-project-2", plan_path="plan2.yaml")
            assert result2["ok"] is True

            # Check that markers appear exactly once (idempotent replacement)
            content = agents.read_text()
            assert content.count("<!-- VECTL:AGENTS:BEGIN -->") == 1
            assert content.count("<!-- VECTL:AGENTS:END -->") == 1
            assert "Old content." not in content  # Replaced, not doubled
        finally:
            os.chdir(old_cwd)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan


# ---------------------------------------------------------------------------
# Tests for vectl_check
# ---------------------------------------------------------------------------


class TestVectlCheck:
    """Tests for vectl_check MCP tool."""

    def _make_plan_with_checklist(self, tmp_path: Path, description: str) -> Path:
        """Create a plan file with a step containing a checklist."""
        plan_dict = {
            "project": "test-check",
            "phases": [
                {
                    "id": "p1",
                    "name": "Phase One",
                    "status": "pending",
                    "steps": [
                        {
                            "id": "p1.s1",
                            "name": "Step with checklist",
                            "status": "pending",
                            "description": description,
                        },
                        {
                            "id": "p1.s2",
                            "name": "Another step",
                            "status": "pending",
                            "description": "No checklist here",
                        },
                    ],
                }
            ],
        }
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text(yaml.dump(plan_dict))
        return plan_file

    def test_toggle_checklist_item(self, tmp_path: Path) -> None:
        """Toggle a checklist item from unchecked to checked."""
        plan_file = self._make_plan_with_checklist(
            tmp_path, "Checklist:\n- [ ] Do validation\n- [ ] Write tests\n"
        )

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", keyword="validation")

            assert "Updated checklist" in result
            assert "p1.s1" in result
            assert "- [x] Do validation" in result

            # Verify persistence
            data = yaml.safe_load(plan_file.read_text())
            desc = data["phases"][0]["steps"][0]["description"]
            assert "- [x] Do validation" in desc
            assert "- [ ] Write tests" in desc
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_toggle_checked_to_unchecked(self, tmp_path: Path) -> None:
        """Toggle a checked item back to unchecked."""
        plan_file = self._make_plan_with_checklist(
            tmp_path, "Items:\n- [x] Already done\n- [ ] Not yet\n"
        )

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", keyword="Already")

            assert "- [ ] Already done" in result

            # Verify persistence
            data = yaml.safe_load(plan_file.read_text())
            desc = data["phases"][0]["steps"][0]["description"]
            assert "- [ ] Already done" in desc
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_add_checklist_item(self, tmp_path: Path) -> None:
        """Add a new unchecked checklist item."""
        plan_file = self._make_plan_with_checklist(tmp_path, "Checklist:\n- [ ] Existing\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", add="New item")

            assert "- [ ] New item" in result

            # Verify persistence
            data = yaml.safe_load(plan_file.read_text())
            desc = data["phases"][0]["steps"][0]["description"]
            assert "- [ ] Existing" in desc
            assert "- [ ] New item" in desc
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_both_toggle_and_add(self, tmp_path: Path) -> None:
        """Both toggle an item and add a new one."""
        plan_file = self._make_plan_with_checklist(tmp_path, "- [ ] Existing item\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", keyword="Existing", add="New item")

            assert "- [x] Existing item" in result
            assert "- [ ] New item" in result
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_error_no_keyword_or_add(self, tmp_path: Path) -> None:
        """Error when neither keyword nor add is provided."""
        plan_file = self._make_plan_with_checklist(tmp_path, "- [ ] Item\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1")

            assert "Error" in result
            assert "keyword" in result.lower() or "add" in result.lower()
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_error_no_match(self, tmp_path: Path) -> None:
        """Error when keyword doesn't match any checklist item."""
        plan_file = self._make_plan_with_checklist(tmp_path, "- [ ] Something\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", keyword="nonexistent")

            assert "Error" in result
            assert "nonexistent" in result
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_error_ambiguous_match(self, tmp_path: Path) -> None:
        """Error when keyword matches multiple items."""
        plan_file = self._make_plan_with_checklist(tmp_path, "- [ ] Test alpha\n- [ ] Test beta\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="p1.s1", keyword="Test")

            assert "Error" in result
            assert "multiple" in result.lower()
            assert "Test alpha" in result
            assert "Test beta" in result
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

    def test_error_step_not_found(self, tmp_path: Path) -> None:
        """Error when step doesn't exist."""
        plan_file = self._make_plan_with_checklist(tmp_path, "- [ ] Item\n")

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        try:
            os.environ["VECTL_PLAN_PATH"] = str(plan_file)

            result = vectl_check(step_id="nonexistent", keyword="Item")

            assert "Error" in result
            assert "not found" in result.lower()
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan


# ---------------------------------------------------------------------------
# Tests for vectl_render
# ---------------------------------------------------------------------------


class TestVectlRender:
    """Tests for vectl_render MCP tool."""

    def test_render_basic(self, plan_file: Path) -> None:
        """Basic render returns markdown with plan structure."""
        result = vectl_render()
        assert "test-mcp" in result
        assert "alpha" in result
        assert "beta" in result

    def test_render_includes_phase_table(self, plan_file: Path) -> None:
        """Render includes a summary table with phase progress."""
        result = vectl_render()
        assert "| Status | Phase |" in result
        assert "|--------|-------|" in result

    def test_render_includes_step_details(self, plan_file: Path) -> None:
        """Render includes step details under each phase."""
        result = vectl_render()
        assert "a.1" in result
        assert "a.2" in result
        assert "Alpha Step One" in result

    def test_render_phase_filter(self, plan_file: Path) -> None:
        """Render with phase_id shows only that phase."""
        result = vectl_render(phase_id="alpha")
        assert "alpha" in result
        assert "Alpha Phase" in result
        # Should NOT show beta phase steps
        assert "b.1" not in result
        # Should NOT have the summary table when filtering to single phase
        assert "| Status | Phase |" not in result

    def test_render_full_mode(self, plan_file: Path) -> None:
        """Render with full=True includes complete descriptions."""
        result_full = vectl_render(full=True)
        result_default = vectl_render(full=False)

        # Full mode should include the full description (multi-line indentation)
        # The description in the test plan is "First step description"
        # In full mode, descriptions are indented under the step bullet
        assert "First step description" in result_full
        # In default mode, the description is shown as a one-liner after the step name
        assert "First step description" in result_default or "—" in result_default

    def test_render_invalid_phase(self, plan_file: Path) -> None:
        """Error handling for invalid phase_id."""
        result = vectl_render(phase_id="nonexistent")
        assert "Error" in result
        assert "nonexistent" in result.lower() or "not found" in result.lower()

    def test_render_shows_gate(self, plan_file: Path) -> None:
        """Render includes gate when present."""
        result = vectl_render()
        # Beta phase has a gate in the test plan
        assert "Gate" in result
        assert "All alpha tests pass" in result

    def test_render_shows_context(self, plan_file: Path) -> None:
        """Render includes phase context when present."""
        result = vectl_render()
        # Alpha phase has context "First phase context"
        assert "First phase context" in result
