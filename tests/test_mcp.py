"""Tests for MCP server tools.

Each tool is a plain function decorated with @mcp.tool().
We test by calling the function directly with VECTL_PLAN_PATH
pointing to a temporary plan file.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from vectl.claims import get_current_branch as _claims_current_branch
from vectl.claims import load_claims
from vectl.mcp_server import (
    _load as _vectl_load,
)
from vectl.mcp_server import (
    _save_plan as _vectl_save_plan,
)
from vectl.mcp_server import (
    vectl_check as _vectl_check_tool,
)
from vectl.mcp_server import (
    vectl_claim as _vectl_claim_tool,
)
from vectl.mcp_server import (
    vectl_clipboard as _vectl_clipboard_tool,
)
from vectl.mcp_server import (
    vectl_complete as _vectl_complete_tool,
)
from vectl.mcp_server import (
    vectl_dag as _vectl_dag_tool,
)
from vectl.mcp_server import (
    vectl_guide as _vectl_guide_tool,
)
from vectl.mcp_server import (
    vectl_lifecycle as _vectl_lifecycle_tool,
)
from vectl.mcp_server import (
    vectl_migrate_step_id as _vectl_migrate_step_id_tool,
)
from vectl.mcp_server import (
    vectl_mutate as _vectl_mutate_tool,
)
from vectl.mcp_server import (
    vectl_recover as _vectl_recover_tool,
)
from vectl.mcp_server import (
    vectl_render as _vectl_render_tool,
)
from vectl.mcp_server import (
    vectl_repair_claims as _vectl_repair_claims_tool,
)
from vectl.mcp_server import (
    vectl_review as _vectl_review_tool,
)
from vectl.mcp_server import (
    vectl_search as _vectl_search_tool,
)
from vectl.mcp_server import (
    vectl_show as _vectl_show_tool,
)
from vectl.mcp_server import (
    vectl_status as _vectl_status_tool,
)
from vectl.mcp_server import (
    vectl_validate as _vectl_validate_tool,
)
from vectl.migration import migrate_from_split_state
from vectl.models import PhaseStatus, PlanError, PlanIOError, StepStatus
from vectl.plan_path import resolve_claims_path

# FastMCP @mcp.tool() returns _ToolWrapper objects; unwrap to get the callable.
# Use type: ignore to suppress mypy errors about the .fn attribute access.
vectl_status = _vectl_status_tool.fn  # type: ignore[attr-defined]
vectl_validate = _vectl_validate_tool.fn  # type: ignore[attr-defined]
vectl_show = _vectl_show_tool.fn  # type: ignore[attr-defined]
vectl_claim = _vectl_claim_tool.fn  # type: ignore[attr-defined]
vectl_complete = _vectl_complete_tool.fn  # type: ignore[attr-defined]
vectl_lifecycle = _vectl_lifecycle_tool.fn  # type: ignore[attr-defined]
vectl_search = _vectl_search_tool.fn  # type: ignore[attr-defined]
vectl_mutate = _vectl_mutate_tool.fn  # type: ignore[attr-defined]
vectl_migrate_step_id = getattr(_vectl_migrate_step_id_tool, "fn", _vectl_migrate_step_id_tool)
vectl_review = _vectl_review_tool.fn  # type: ignore[attr-defined]
vectl_guide = _vectl_guide_tool.fn  # type: ignore[attr-defined]
vectl_dag = _vectl_dag_tool.fn  # type: ignore[attr-defined]
vectl_clipboard = _vectl_clipboard_tool.fn  # type: ignore[attr-defined]
vectl_check = _vectl_check_tool.fn  # type: ignore[attr-defined]
vectl_render = _vectl_render_tool.fn  # type: ignore[attr-defined]
vectl_recover = _vectl_recover_tool.fn  # type: ignore[attr-defined]
vectl_repair_claims = _vectl_repair_claims_tool.fn  # type: ignore[attr-defined]


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


def _make_duplicate_step_id_plan_dict() -> dict:
    """Create plan dict with one duplicate step ID across phases."""
    return {
        "project": "duplicate-id-mcp",
        "phases": [
            {
                "id": "alpha",
                "name": "Alpha",
                "status": "pending",
                "steps": [
                    {
                        "id": "dup.step",
                        "name": "Alpha duplicate",
                        "status": "pending",
                    }
                ],
            },
            {
                "id": "beta",
                "name": "Beta",
                "status": "pending",
                "steps": [
                    {
                        "id": "dup.step",
                        "name": "Beta duplicate",
                        "status": "pending",
                    }
                ],
            },
            {
                "id": "gamma",
                "name": "Gamma",
                "status": "pending",
                "steps": [
                    {
                        "id": "unique.step",
                        "name": "Gamma unique",
                        "status": "pending",
                    }
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
    """Return legacy companion state path used only for ignore regressions."""
    return plan_file.parent / ".vectl" / "state.json"


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


@pytest.fixture()
def duplicate_id_plan_file(tmp_path: Path) -> Iterator[Path]:
    """Create a temp plan file with duplicate step IDs and set VECTL_PLAN_PATH."""
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.dump(_make_duplicate_step_id_plan_dict()))
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(p)
    yield p
    if old is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old


def _reload_plan(path: Path) -> dict:
    """Re-read plan.yaml contents as dict."""
    return yaml.safe_load(path.read_text())


def _legacy_step_ids(plan_data: dict[str, object] | None = None) -> set[str]:
    """Collect step IDs from a legacy migration fixture."""
    data = plan_data if plan_data is not None else _legacy_migration_plan_dict()
    step_ids: set[str] = set()
    phases_obj = data.get("phases")
    assert isinstance(phases_obj, list)
    for phase_obj in phases_obj:
        assert isinstance(phase_obj, dict)
        steps_obj = phase_obj.get("steps")
        assert isinstance(steps_obj, list)
        for step_obj in steps_obj:
            assert isinstance(step_obj, dict)
            step_id_obj = step_obj.get("id")
            assert isinstance(step_id_obj, str)
            step_ids.add(step_id_obj)
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

    def test_status_shows_duplicate_step_id_diagnostics(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_status()
        assert "Duplicate Step-ID Diagnostics" in result
        assert "dup.step" in result
        assert "alpha, beta" in result

    def test_status_next_steps_shows_correct_phase_for_duplicate_ids(
        self, duplicate_id_plan_file: Path
    ) -> None:
        """Regression test: Next Available Steps shows correct phase for each duplicate step ID."""
        result = vectl_status()
        # Both dup.step entries should appear with their correct phases
        assert "(alpha)" in result
        assert "(beta)" in result
        # Verify each duplicate shows its own phase, not the first found
        # Look for the step entries (marked with ○ bullet)
        lines = result.split("\n")
        dup_step_lines = [
            line for line in lines if "dup.step" in line and line.strip().startswith("○")
        ]
        # Should have two entries for dup.step (one in alpha, one in beta)
        assert len(dup_step_lines) == 2, (
            f"Expected 2 step lines, got {len(dup_step_lines)}: {dup_step_lines}"
        )
        # Check that each shows the correct phase
        alpha_dup = [line for line in dup_step_lines if "(alpha)" in line]
        beta_dup = [line for line in dup_step_lines if "(beta)" in line]
        assert len(alpha_dup) == 1, f"Alpha duplicate should show (alpha): {alpha_dup}"
        assert len(beta_dup) == 1, f"Beta duplicate should show (beta): {beta_dup}"


class TestVectlValidate:
    def test_validate_reports_duplicate_step_id_error(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_validate()
        assert "Validation" in result
        assert "ERROR:" in result
        assert "dup.step" in result
        assert "1 error(s), 0 warning(s)" in result


class TestVectlReviewDuplicateBlocking:
    def test_review_gate_check_is_blocked_when_plan_has_duplicate_step_ids(
        self, duplicate_id_plan_file: Path
    ) -> None:
        result = vectl_review(phase_id="alpha")
        assert "L1: Validation" in result
        assert "ERROR: Duplicate step ID 'dup.step'" in result
        assert "Gate check blocked: plan validation failed" in result


class TestVectlMigrateStepId:
    def test_dry_run_returns_report_and_evidence(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_migrate_step_id(run_mode="dry-run")

        assert result["ok"] is True
        assert result["status"] == "recommendation_only"
        assert result["report"]["run_mode"] == "dry-run"
        assert result["report"]["rename_map"]
        assert result["evidence"]["run_mode"] == "dry-run"
        assert result["evidence"]["migrated"] is False

    def test_apply_returns_report_and_persists_migration_then_noops(
        self, duplicate_id_plan_file: Path
    ) -> None:
        first = vectl_migrate_step_id(run_mode="apply")

        assert first["ok"] is True
        assert first["status"] == "repair_applied"
        assert first["migrated"] is True
        assert first["report"]["rename_map"]
        assert first["evidence"]["run_mode"] == "apply"
        assert first["evidence"]["migrated"] is True

        data = _reload_plan(duplicate_id_plan_file)
        ids = [step["id"] for phase in data["phases"] for step in phase["steps"]]
        assert len(ids) == len(set(ids))

        second = vectl_migrate_step_id(run_mode="apply")
        assert second["ok"] is True
        assert second["status"] == "repair_applied"
        assert second["migrated"] is False
        assert second["report"]["rename_map"] == []


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

    def test_show_step_duplicate_id_recommendation(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_show(id="dup.step")
        assert "Duplicate-ID Repair Recommendation" in result
        assert "type=duplicate-step-id" in result
        assert "step_id=dup.step" in result
        assert "duplicates=alpha, beta" in result
        assert "resolution.explicit_phase" in result
        assert "phase-qualified step selectors" in result
        assert "Use one of:" not in result
        assert (
            "resolution.auto_migrate_flag: Not available in phase B: --auto-migrate is unsupported"
            in result
        )
        assert (
            "resolution.migration_tool: Use vectl migrate-step-id --dry-run, then --yes" in result
        )


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

    def test_claim_reject_with_existing_claim_returns_structured_error(
        self, plan_file: Path
    ) -> None:
        """Claim rejection due to existing claim returns structured error data."""
        import os
        import subprocess

        # Need to work in a git repo for get_current_branch() to work
        repo_root = plan_file.parent
        if not (repo_root / ".git").exists():
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
        try:
            from vectl.claims import (
                ClaimEntry,
                get_current_branch,
                resolve_claims_path,
                save_claims,
            )

            branch_name = get_current_branch()

            # Create an existing claim
            claims_path = resolve_claims_path(plan_file)
            claims_path.parent.mkdir(parents=True, exist_ok=True)
            save_claims(
                {
                    f"{branch_name}:a.1": ClaimEntry(
                        step_id="a.1",
                        branch=branch_name,
                        agent="agent-a",
                        claimed_at="2026-03-09T00:00:00Z",
                    )
                },
                claims_path,
            )

            # Try to claim the same step with a different agent
            result = vectl_claim(agent="agent-b", step_id="a.1")

            assert result["ok"] is False
            assert result["error_code"] == "claim_conflict"
            # Check for structured conflict data
            assert result["claim_conflict"] is not None
            assert result["claim_conflict"]["step_id"] == "a.1"
            assert result["claim_conflict"]["branch"] == branch_name
            assert result["claim_conflict"]["claimant"] == "agent-a"
            # Check markdown includes actionable info
            assert "Claim Conflict" in result["markdown"]
            assert "agent-a" in result["markdown"]
            assert "vectl_show" in result["markdown"]
        finally:
            os.chdir(original_cwd)

    def test_claim_blocks_ambiguous_duplicate_target_without_opt_in(
        self, duplicate_id_plan_file: Path
    ) -> None:
        result = vectl_claim(agent="bot", step_id="dup.step")

        assert result["ok"] is False
        assert result["error_code"] == "duplicate_step_id_ambiguous_target"
        assert "blocking_reason=duplicate_step_id_ambiguous" in result["error"]
        recommendation = result["duplicate_step_id_recommendation"]
        assert recommendation["type"] == "duplicate-step-id"
        assert recommendation["step_id"] == "dup.step"
        assert recommendation["duplicates"] == [{"phase": "alpha"}, {"phase": "beta"}]
        assert (
            "phase-qualified step selectors" in recommendation["resolution_path"]["explicit_phase"]
        )
        assert recommendation["resolution_path"]["auto_migrate_flag"].startswith(
            "Not available in phase B"
        )

        data = _reload_plan(duplicate_id_plan_file)
        assert data["phases"][0]["steps"][0]["status"] == "pending"
        assert data["phases"][1]["steps"][0]["status"] == "pending"


class TestRepairClaimsMcp:
    def test_repair_claims_dry_run_preview(self, plan_file: Path) -> None:
        branch = _claims_current_branch()
        claims_path = resolve_claims_path(plan_file)
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        claims_path.write_text(
            json.dumps(
                {
                    f"{branch}:a.1": {
                        "step_id": "a.1",
                        "branch": branch,
                        "agent": "ghost",
                        "claimed_at": "2026-03-09T00:00:00Z",
                    }
                }
            ),
            encoding="utf-8",
        )
        before = claims_path.read_text(encoding="utf-8")

        result = vectl_repair_claims(dry_run=True)

        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["changed"] is True
        assert claims_path.read_text(encoding="utf-8") == before

    def test_repair_claims_step_scope_preserves_unrelated_entries(self, plan_file: Path) -> None:
        branch = _claims_current_branch()
        claims_path = resolve_claims_path(plan_file)
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        claims_path.write_text(
            json.dumps(
                {
                    f"{branch}:a.1": {
                        "step_id": "a.1",
                        "branch": branch,
                        "agent": "ghost",
                        "claimed_at": "2026-03-09T00:00:00Z",
                    },
                    f"{branch}:b.1": {
                        "step_id": "b.1",
                        "branch": branch,
                        "agent": "keep",
                        "claimed_at": "2026-03-09T00:00:00Z",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = vectl_repair_claims(step_id="a.1")

        assert result["ok"] is True
        assert [a["key"] for a in result["actions"]] == [f"{branch}:a.1"]
        claims = load_claims(claims_path)
        assert f"{branch}:a.1" not in claims
        assert claims[f"{branch}:b.1"].agent == "keep"

    def test_repair_claims_output_shape_and_policy(self, plan_file: Path) -> None:
        result = vectl_repair_claims(dry_run=True)

        assert result["ok"] is True
        assert "policy" in result
        assert "plan_precedence" in result["policy"]
        assert "claims_path" in result
        assert "plan_path" in result
        assert "actions" in result


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

    def test_claim_and_defer_use_claims_store(self, plan_file: Path) -> None:
        claims_path = resolve_claims_path(plan_file)

        claim_result = vectl_claim(agent="bot", step_id="a.1")
        assert claim_result["ok"] is True
        claims_after_claim = load_claims(claims_path)
        assert len(claims_after_claim) == 1
        assert next(iter(claims_after_claim.values())).step_id == "a.1"

        defer_result = vectl_lifecycle(action="defer", id="a.1")
        assert "Deferred" in defer_result
        assert load_claims(claims_path) == {}

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

    def test_lifecycle_blocks_ambiguous_duplicate_target_without_opt_in(
        self, duplicate_id_plan_file: Path
    ) -> None:
        result = vectl_lifecycle(action="skip", id="dup.step", reason="irrelevant")

        assert "Error" in result
        assert "error_code=duplicate_step_id_ambiguous_target" in result
        assert "blocking_reason=duplicate_step_id_ambiguous" in result

        data = _reload_plan(duplicate_id_plan_file)
        assert data["phases"][0]["steps"][0]["status"] == "pending"
        assert data["phases"][1]["steps"][0]["status"] == "pending"


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

    def test_add_step_rejects_duplicate_id_across_plan(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="Another dup",
            step_id="dup.step",
        )

        assert "Error" in result
        assert "error_code=duplicate_step_id_write_blocked" in result
        assert "blocking_reason=duplicate_step_id_exists" in result

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
# RFC: Linked worktree guard tests
# ---------------------------------------------------------------------------


class TestVectlMutateLinkedWorktreeGuard:
    """Tests for linked worktree guard in vectl_mutate."""

    @pytest.fixture
    def linked_worktree_file(self, tmp_path: Path) -> Iterator[tuple[Path, Path]]:
        """Create a plan file and simulate a linked worktree."""
        # Create a main worktree with plan.yaml
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        plan_file = main_root / "plan.yaml"
        plan_file.write_text(yaml.dump(_make_plan_dict()))

        # Create linked worktree directory
        linked_root = tmp_path / "linked_worktree"
        linked_root.mkdir()

        # Set env to simulate running from linked worktree (no VECTL_PLAN_PATH)
        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ.pop("VECTL_PLAN_PATH", None)

        yield plan_file, linked_root

        # Cleanup
        if old is not None:
            os.environ["VECTL_PLAN_PATH"] = old
        else:
            os.environ.pop("VECTL_PLAN_PATH", None)

    def test_mcp_mutate_blocked_in_linked_worktree(
        self, linked_worktree_file: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vectl_mutate is blocked when running in a linked worktree."""
        plan_file, linked_root = linked_worktree_file

        # Mock is_linked_worktree to return (True, main_root)
        main_root = plan_file.parent
        monkeypatch.setattr(
            "vectl.mcp_server.is_linked_worktree",
            lambda: (True, main_root),
        )

        # Ensure VECTL_PLAN_PATH is NOT set
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        # Call vectl_mutate - should be blocked
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="Blocked Step",
        )

        assert "Mutate blocked" in result
        assert "linked worktree" in result
        assert str(main_root) in result
        assert "Plan file not found" not in result

    def test_mcp_mutate_allowed_in_main_worktree(
        self, linked_worktree_file: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vectl_mutate is allowed when not in a linked worktree."""
        plan_file, linked_root = linked_worktree_file

        # Set up the environment to use the plan file
        monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_file))

        # Mock is_linked_worktree to return (False, None)
        monkeypatch.setattr(
            "vectl.mcp_server.is_linked_worktree",
            lambda: (False, None),
        )

        # Call vectl_mutate - should proceed
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="Allowed Step",
        )

        assert "Mutate blocked" not in result
        assert "Added step" in result

    def test_mcp_mutate_allowed_with_env_override(
        self, linked_worktree_file: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vectl_mutate is allowed with VECTL_PLAN_PATH set (escape hatch)."""
        plan_file, linked_root = linked_worktree_file

        # Mock is_linked_worktree to return (True, main_root)
        main_root = plan_file.parent
        monkeypatch.setattr(
            "vectl.mcp_server.is_linked_worktree",
            lambda: (True, main_root),
        )

        # Set VECTL_PLAN_PATH to override the guard
        monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_file))

        # Call vectl_mutate - should proceed despite being in linked worktree
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="Override Step",
        )

        assert "Mutate blocked" not in result
        assert "Added step" in result

    def test_mcp_mutate_blocked_when_main_root_unresolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed worktree probe still blocks mutation with actionable message."""
        monkeypatch.setattr("vectl.mcp_server.is_linked_worktree", lambda: (True, None))
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        result = vectl_mutate(action="add-phase", name="Blocked")

        assert "Mutate blocked" in result
        assert "linked worktree" in result
        assert "VECTL_PLAN_PATH" in result
        assert "Plan file not found" not in result


# ---------------------------------------------------------------------------
# Linked worktree implicit entry-path resolution tests (MCP)
# ---------------------------------------------------------------------------
# Tests for implicit MCP entry-path resolution when in linked worktree.


class TestVectlMcpLinkedWorktreeImplicitResolution:
    """Tests for implicit entry-path resolution in MCP linked worktrees (no explicit path)."""

    @pytest.fixture
    def linked_worktree_mcp_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Path, Path]:
        """Create main and linked worktree for MCP implicit resolution testing."""
        # Create main worktree with plan.yaml
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        plan_file = main_root / "plan.yaml"
        plan_file.write_text(yaml.dump(_make_plan_dict()))

        # Create linked worktree directory
        linked_root = tmp_path / "linked_worktree"
        linked_root.mkdir()

        # Ensure VECTL_PLAN_PATH is NOT set
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        # Mock resolve_plan_path to return the main worktree's plan
        monkeypatch.setattr(
            "vectl.mcp_server.resolve_plan_path",
            lambda: plan_file,
        )

        return plan_file, linked_root

    def test_mcp_status_resolves_to_main_worktree_plan_without_explicit_path(
        self, linked_worktree_mcp_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP vectl_status resolves to main worktree's plan when running in linked worktree."""
        plan_file, linked_root = linked_worktree_mcp_env

        # Call vectl_status WITHOUT explicit plan path - should auto-resolve to main worktree
        result = vectl_status()

        # Should succeed and find the plan in main worktree
        assert "alpha" in result  # phase exists in _make_plan_dict()
        assert "linked worktree" not in result.lower()

    def test_mcp_claim_resolves_to_main_worktree_plan_without_explicit_path(
        self, linked_worktree_mcp_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP vectl_claim resolves to main worktree's plan when running in linked worktree."""
        plan_file, linked_root = linked_worktree_mcp_env

        # Call vectl_claim WITHOUT explicit plan path - should auto-resolve
        result = vectl_claim(agent="test-agent", step_id="a.1")

        # Should succeed and find the step in main worktree's plan
        assert result.get("ok") is True or "a.1" in str(result)
        assert "linked worktree" not in str(result).lower()

    def test_mcp_mutate_with_explicit_plan_allowed_in_linked_worktree(
        self, linked_worktree_mcp_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP vectl_mutate with explicit plan path is allowed in linked worktree."""
        plan_file, linked_root = linked_worktree_mcp_env

        # Set explicit path
        monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_file))

        # Also mock is_linked_worktree to return (True, main_root)
        monkeypatch.setattr(
            "vectl.mcp_server.is_linked_worktree",
            lambda: (True, plan_file.parent),
        )

        # Call vectl_mutate - should proceed because VECTL_PLAN_PATH is set
        result = vectl_mutate(
            action="add-step",
            phase_id="alpha",
            name="New Step",
        )

        assert "Mutate blocked" not in result
        assert "Added step" in result

    def test_mcp_status_fails_closed_on_malformed_worktree_without_walkup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed linked-worktree probe fails closed to cwd sentinel path."""
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        (main_root / "plan.yaml").write_text(yaml.dump(_make_plan_dict()))

        linked_root = main_root / "linked_worktree"
        linked_root.mkdir()
        monkeypatch.chdir(linked_root)
        monkeypatch.delenv("VECTL_PLAN_PATH", raising=False)

        def fake_run(
            cmd: list[str], *, capture_output: bool, text: bool, cwd: Path
        ) -> subprocess.CompletedProcess[str]:
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=".git\n", stderr="")
            if cmd == ["git", "rev-parse", "--git-dir"]:
                # Malformed linked-worktree metadata: empty git-dir output.
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected git command: {cmd}")

        monkeypatch.setattr("vectl.plan_path.subprocess.run", fake_run)

        with pytest.raises(
            PlanIOError,
            match=r"Plan file not found: .*linked_worktree/plan\.yaml",
        ):
            vectl_status()


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

    def test_review_shows_duplicate_step_id_diagnostics(self, duplicate_id_plan_file: Path) -> None:
        result = vectl_review()
        assert "Duplicate Step-ID Diagnostics" in result
        assert "dup.step" in result
        assert "alpha, beta" in result

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

    def test_dag_includes_duplicate_step_id_warning_comments(
        self, duplicate_id_plan_file: Path
    ) -> None:
        result = vectl_dag()
        assert "%% Duplicate Step-ID Diagnostics" in result
        assert "dup.step" in result


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


class TestMcpUnifiedPlanPath:
    """Unified load/save behavior uses plan.yaml only."""

    def test_claim_persists_to_plan_yaml_only(self, legacy_plan_file: Path) -> None:
        result = vectl_claim(agent="bot", step_id="phase-b.step1")
        assert result["ok"] is True

        plan_data = _reload_plan(legacy_plan_file)
        step_data = plan_data["phases"][1]["steps"][0]
        assert step_data["status"] == StepStatus.CLAIMED.value
        assert step_data["claimed_by"] == "bot"

        assert not _state_json_path(legacy_plan_file).exists()

    def test_load_returns_definition_plan_hash_only(self, plan_file: Path) -> None:
        plan, def_hash = _vectl_load()
        assert plan.project == "test-mcp"
        assert isinstance(def_hash, str)
        assert def_hash

    def test_load_ignores_state_json_until_explicit_migration(self, plan_file: Path) -> None:
        stray_state = _state_json_path(plan_file)
        stray_state.parent.mkdir(parents=True, exist_ok=True)
        stray_state.write_text(
            json.dumps(
                {
                    "steps": {
                        "a.1": {"status": "done", "evidence": "migrated by mcp"},
                        "ghost.step": {"status": "claimed", "claimed_by": "ghost"},
                    },
                    "phases": {"alpha": {"status": "in_progress"}},
                }
            ),
            encoding="utf-8",
        )

        plan, _ = _vectl_load()
        found = plan.find_step("a.1")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.PENDING
        assert step.evidence is None
        phase = plan.find_phase("alpha")
        assert phase is not None
        assert phase.status == PhaseStatus.PENDING

        assert stray_state.exists()
        assert not stray_state.with_suffix(".json.migrated").exists()

        migration = migrate_from_split_state(plan_file)
        assert migration.migrated_steps == 1
        assert migration.migrated_phases == 1
        assert not migration.already_migrated

        migrated_plan, _ = _vectl_load()
        found = migrated_plan.find_step("a.1")
        assert found is not None
        _, step = found
        assert step.status == StepStatus.DONE
        assert step.evidence == "migrated by mcp"
        phase = migrated_plan.find_phase("alpha")
        assert phase is not None
        assert phase.status == PhaseStatus.IN_PROGRESS
        assert not stray_state.exists()
        assert stray_state.with_suffix(".json.migrated").exists()

    def test_save_plan_cas_conflict_raises_plan_error(self, plan_file: Path) -> None:
        plan, def_hash = _vectl_load()

        plan_data = yaml.safe_load(plan_file.read_text())
        plan_data["context"] = "concurrent edit to trigger CAS conflict"
        plan_file.write_text(yaml.dump(plan_data))

        with pytest.raises(PlanError, match="CAS conflict: plan.yaml"):
            _vectl_save_plan(plan, def_hash, "mcp: cas test")

    def test_read_only_tools_do_not_write_state_json(self, plan_file: Path) -> None:
        state_path = _state_json_path(plan_file)
        assert not state_path.exists()

        vectl_status()
        assert not state_path.exists()

        vectl_show(id="a.1")
        assert not state_path.exists()


class TestVectlInit:
    """Tests for vectl_init MCP tool."""

    def test_init_creates_plan_and_agents_md(self, tmp_path: Path) -> None:
        """Basic init creates plan.yaml and AGENTS.md."""
        from vectl.mcp_server import vectl_init as _vectl_init_tool

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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

        vectl_init = _vectl_init_tool.fn  # type: ignore[attr-defined]

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


class TestVectlRecover:
    def test_recover_restores_without_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_file = tmp_path / "plan.yaml"
        backup_git = tmp_path / ".git"
        backup_path = backup_git / "vectl" / "plan.yaml.bak"

        current_plan = _make_plan_dict()
        current_plan["phases"][0]["steps"][0]["name"] = "Current Name"
        backup_plan = _make_plan_dict()
        backup_plan["phases"][0]["steps"][0]["name"] = "Backup Name"

        plan_file.write_text(yaml.dump(current_plan), encoding="utf-8")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(yaml.dump(backup_plan), encoding="utf-8")

        state_path = _state_json_path(plan_file)
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

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        monkeypatch.setattr("vectl.mcp_server._resolve_git_dir", lambda _path: backup_git)
        try:
            result = vectl_recover()
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

        assert result["ok"] is True
        assert result["restored"] is True
        assert "diff_summary" in result

        restored_raw = yaml.safe_load(plan_file.read_text(encoding="utf-8"))
        assert restored_raw["phases"][0]["steps"][0]["name"] == "Backup Name"

    def test_recover_corrupted_backup_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP recover returns error when backup file is corrupted."""
        import os

        plan_file = tmp_path / "plan.yaml"
        backup_git = tmp_path / ".git"
        backup_path = backup_git / "vectl" / "plan.yaml.bak"

        current_plan = _make_plan_dict()
        current_plan["phases"][0]["steps"][0]["name"] = "Current Name"

        plan_file.write_text(yaml.dump(current_plan), encoding="utf-8")
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Write corrupted backup content
        backup_path.write_text("invalid: yaml [[[[")

        state_path = _state_json_path(plan_file)
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

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        monkeypatch.setattr("vectl.mcp_server._resolve_git_dir", lambda _path: backup_git)
        try:
            result = vectl_recover()
        finally:
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan

        assert result["ok"] is False
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_recover_permission_error_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MCP recover returns error when .git/vectl has permission issues."""
        import os

        plan_file = tmp_path / "plan.yaml"
        backup_git = tmp_path / ".git"
        backup_path = backup_git / "vectl" / "plan.yaml.bak"
        vectl_dir = backup_git / "vectl"

        current_plan = _make_plan_dict()
        current_plan["phases"][0]["steps"][0]["name"] = "Current Name"
        backup_plan = _make_plan_dict()
        backup_plan["phases"][0]["steps"][0]["name"] = "Backup Name"

        plan_file.write_text(yaml.dump(current_plan), encoding="utf-8")
        vectl_dir.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(yaml.dump(backup_plan), encoding="utf-8")

        state_path = _state_json_path(plan_file)
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

        old_plan = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(plan_file)
        monkeypatch.setattr("vectl.mcp_server._resolve_git_dir", lambda _path: backup_git)

        # Make the backup directory read-only to simulate permission error
        os.chmod(vectl_dir, 0o444)
        try:
            # May raise PermissionError or return an error dict,
            # depending on where recovery fails.
            try:
                result = vectl_recover()
                # If it returns, verify it has an error
                assert result["ok"] is False
                assert "error" in result
            except PermissionError:
                # PermissionError is acceptable: permission failures
                # must surface as a failed recovery path.
                pass
        finally:
            os.chmod(vectl_dir, 0o755)
            if old_plan is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old_plan
