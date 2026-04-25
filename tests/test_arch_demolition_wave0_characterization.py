"""Wave 0 characterization tests for architectural demolition safety rails."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.claims import ClaimEntry, get_current_branch, save_claims
from vectl.cli import _get_next_steps_with_phase as cli_next_steps_with_phase
from vectl.cli import app
from vectl.io import save_plan
from vectl.mcp_server import _get_next_steps_with_phase as mcp_next_steps_with_phase
from vectl.mcp_server import vectl_claim as _vectl_claim_tool
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus
from vectl.plan_path import resolve_claims_path

runner = CliRunner()
vectl_claim = _vectl_claim_tool.fn  # type: ignore[attr-defined]


def _branch_claim_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def test_wave0_agents_md_preserves_legacy_header_and_appends_single_managed_block(
    tmp_path: Path,
) -> None:
    """Pin legacy AGENTS.md upsert behavior before shell extraction work."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Project\n\n## Plan Tracking (vectl)\n\nLegacy text.\n")

    result = runner.invoke(app, ["agents-md", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    content = agents_md.read_text(encoding="utf-8")
    assert content.startswith("# Project\n\n## Plan Tracking (vectl)\n\nLegacy text.\n")
    assert content.count("## Plan Tracking (vectl)") == 2
    assert content.count("<!-- VECTL:AGENTS:BEGIN -->") == 1
    assert content.count("<!-- VECTL:AGENTS:END -->") == 1
    assert "legacy block preserved" in result.output.lower()


def test_wave0_cli_and_mcp_next_step_ordering_filtering_match() -> None:
    """Pin duplicated next-step helper semantics before deduplication."""
    plan = Plan(
        project="wave0-next-steps",
        phases=[
            Phase(
                id="alpha",
                name="Alpha",
                status=PhaseStatus.PENDING,
                steps=[
                    Step(id="alpha.done", name="Done", status=StepStatus.DONE),
                    Step(
                        id="fix.rejected",
                        name="Fix",
                        status=StepStatus.REJECTED,
                        rejection_reason="Needs rework",
                    ),
                    Step(id="match.step", name="Match", agent="bot"),
                    Step(id="open.step", name="Open"),
                    Step(id="other.step", name="Other", agent="other"),
                    Step(id="blocked.step", name="Blocked", depends_on=["missing"]),
                    Step(
                        id="claimed.step",
                        name="Claimed",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-a",
                    ),
                ],
            ),
            Phase(
                id="locked",
                name="Locked",
                status=PhaseStatus.LOCKED,
                steps=[Step(id="locked.step", name="Locked")],
            ),
        ],
    )

    cli_result = [(phase.id, step.id) for phase, step in cli_next_steps_with_phase(plan, "bot")]
    mcp_result = [(phase.id, step.id) for phase, step in mcp_next_steps_with_phase(plan, "bot")]

    assert cli_result == mcp_result
    assert cli_result == [
        ("alpha", "fix.rejected"),
        ("alpha", "match.step"),
        ("alpha", "open.step"),
        ("alpha", "other.step"),
    ]


def test_wave0_duplicate_step_id_diagnostics_are_stable_on_cli_status(tmp_path: Path) -> None:
    """Pin duplicate-step-ID warning text before formatter extraction."""
    plan = Plan(
        project="wave0-duplicate-diagnostics",
        phases=[
            Phase(
                id="alpha",
                name="Alpha",
                steps=[Step(id="dup.step", name="First duplicate")],
            ),
            Phase(
                id="beta",
                name="Beta",
                steps=[Step(id="dup.step", name="Second duplicate")],
            ),
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)

    status = runner.invoke(app, ["status", "--plan", str(plan_path)])
    show = runner.invoke(app, ["show", "dup.step", "--plan", str(plan_path)])

    assert status.exit_code == 0
    assert "Duplicate step-ID diagnostics" in status.output
    assert "duplicate step ID 'dup.step' appears 2 time(s)" in status.output
    assert "alpha" in status.output
    assert "beta" in status.output
    assert show.exit_code == 0
    assert "Ambiguous step target: duplicate ID detected across phases" in show.output
    assert "type=duplicate-step-id" in show.output
    assert "duplicates=alpha, beta" in show.output
    assert "resolution.migration_tool:" in show.output


def test_wave0_claim_conflict_shape_matches_cli_and_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin lifecycle-owned claim conflict metadata across CLI and MCP shells."""
    plan = Plan(
        project="wave0-claim-conflict",
        phases=[
            Phase(
                id="alpha",
                name="Alpha",
                steps=[Step(id="alpha.step", name="Claim target")],
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    save_plan(plan, plan_path)
    branch = get_current_branch()
    claims_path = resolve_claims_path(plan_path)
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    save_claims(
        {
            f"{branch}:alpha.step": ClaimEntry(
                step_id="alpha.step",
                branch=branch,
                agent="agent-a",
                claimed_at=_branch_claim_timestamp(),
            )
        },
        claims_path,
    )

    cli_result = runner.invoke(
        app,
        ["claim", "alpha.step", "--agent", "agent-b", "--plan", str(plan_path)],
    )
    monkeypatch.setenv("VECTL_PLAN_PATH", str(plan_path))
    mcp_result = vectl_claim(agent="agent-b", step_id="alpha.step")

    assert cli_result.exit_code == 1
    assert "Claim Details:" in cli_result.output
    assert "alpha.step" in cli_result.output
    assert branch in cli_result.output
    assert "agent-a" in cli_result.output
    assert "vectl show alpha.step" in cli_result.output

    assert mcp_result["ok"] is False
    assert mcp_result["error_code"] == "claim_conflict"
    assert mcp_result["claim_conflict"] == {
        "step_id": "alpha.step",
        "branch": branch,
        "claimant": "agent-a",
        "claimed_at": mcp_result["claim_conflict"]["claimed_at"],
    }
    assert mcp_result["claim_conflict"]["claimed_at"]
    assert "Claim Conflict" in mcp_result["markdown"]
    assert "agent-a" in mcp_result["markdown"]
    assert "vectl_show" in mcp_result["markdown"]
