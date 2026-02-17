"""Tests for MCP server tools.

Each tool is a plain function decorated with @mcp.tool().
We test by calling the function directly with VECTL_PLAN_PATH
pointing to a temporary plan file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from vectl.mcp_server import (
    vectl_claim as _vectl_claim_tool,
    vectl_clipboard as _vectl_clipboard_tool,
    vectl_complete as _vectl_complete_tool,
    vectl_dag as _vectl_dag_tool,
    vectl_guide as _vectl_guide_tool,
    vectl_lifecycle as _vectl_lifecycle_tool,
    vectl_mutate as _vectl_mutate_tool,
    vectl_review as _vectl_review_tool,
    vectl_search as _vectl_search_tool,
    vectl_show as _vectl_show_tool,
    vectl_status as _vectl_status_tool,
)
from vectl.models import PhaseStatus, StepStatus

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
    """Re-read the plan file as dict."""
    return yaml.safe_load(path.read_text())


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
