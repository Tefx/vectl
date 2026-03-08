"""Tests verifying the auto-fix informational message format.

The _save() helpers in cli.py and mcp_server.py emit an informational
message when recalc_lock_status() changes any phase's lock state.

Good format  : "[vectl] Lock status updated: phase-a (pending)"
Bad format   : "Warning: ..." or "WARNING ..." — must NOT appear.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import save_plan
from vectl.mcp_server import (
    vectl_claim as _vectl_claim_tool,
)
from vectl.mcp_server import (
    vectl_complete as _vectl_complete_tool,
)
from vectl.models import Phase, PhaseStatus, Plan, Step

runner = CliRunner()

# Unwrap FastMCP FunctionTool wrappers.
vectl_claim = _vectl_claim_tool.fn  # pyright: ignore[reportFunctionMemberAccess]
vectl_complete = _vectl_complete_tool.fn  # pyright: ignore[reportFunctionMemberAccess]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inconsistent_plan() -> Plan:
    """Return a plan whose lock status is inconsistent.

    phase-a: PENDING, no dependencies — correct.
    phase-b: LOCKED, depends_on=["phase-a"] — but phase-a is not DONE,
             so LOCKED is correct.
    phase-c: LOCKED, no depends_on — WRONG: should be PENDING.
             recalc_lock_status() will fix this and return ["phase-c"].
    """
    return Plan(
        project="autofix-test",
        phases=[
            Phase(
                id="phase-a",
                name="Phase A",
                status=PhaseStatus.PENDING,
                steps=[Step(id="a.1", name="Step A1")],
            ),
            Phase(
                id="phase-b",
                name="Phase B",
                status=PhaseStatus.LOCKED,
                depends_on=["phase-a"],
                steps=[Step(id="b.1", name="Step B1")],
            ),
            Phase(
                id="phase-c",
                name="Phase C",
                status=PhaseStatus.LOCKED,
                # No depends_on — recalc will flip this to PENDING.
                steps=[Step(id="c.1", name="Step C1")],
            ),
        ],
    )


@pytest.fixture()
def inconsistent_plan_file(tmp_path: Path) -> Path:
    """Write an inconsistent plan to a temp file; return its path."""
    plan = _make_inconsistent_plan()
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


@pytest.fixture()
def mcp_inconsistent_plan_file(tmp_path: Path) -> Iterator[Path]:
    """Write an inconsistent plan and set VECTL_PLAN_PATH for MCP tools."""
    plan = _make_inconsistent_plan()
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    old = os.environ.get("VECTL_PLAN_PATH")
    os.environ["VECTL_PLAN_PATH"] = str(path)
    yield path
    if old is None:
        os.environ.pop("VECTL_PLAN_PATH", None)
    else:
        os.environ["VECTL_PLAN_PATH"] = old


# ---------------------------------------------------------------------------
# CLI _save() — autofix message format
# ---------------------------------------------------------------------------


class TestCliAutofixMessage:
    """CLI _save() emits an informational [vectl] message, never a warning."""

    def test_autofix_message_present(self, inconsistent_plan_file: Path) -> None:
        """[vectl] Lock status updated: … appears when status is corrected."""
        # Claiming a.1 triggers _save(), which calls recalc_lock_status().
        # phase-c is LOCKED with no deps → will be changed to PENDING.
        result = runner.invoke(
            app,
            ["claim", "a.1", "--agent", "bot", "--plan", str(inconsistent_plan_file)],
        )
        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        assert "[vectl] Lock status updated:" in result.output

    def test_autofix_message_contains_phase_and_status(
        self, inconsistent_plan_file: Path
    ) -> None:
        """Message includes the phase ID and its new status in parentheses."""
        result = runner.invoke(
            app,
            ["claim", "a.1", "--agent", "bot", "--plan", str(inconsistent_plan_file)],
        )
        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        # phase-c will be flipped to pending
        assert "phase-c" in result.output
        assert "(pending)" in result.output

    def test_autofix_message_not_a_warning(self, inconsistent_plan_file: Path) -> None:
        """Output must NOT contain any warning-style prefix."""
        result = runner.invoke(
            app,
            ["claim", "a.1", "--agent", "bot", "--plan", str(inconsistent_plan_file)],
        )
        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        output_lower = result.output.lower()
        assert "warning:" not in output_lower
        assert "warning " not in output_lower

    def test_no_autofix_message_when_consistent(self, tmp_path: Path) -> None:
        """When the plan is already consistent, no autofix message is emitted."""
        from vectl.io import save_plan
        from vectl.models import Phase, PhaseStatus, Plan, Step

        consistent = Plan(
            project="consistent",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1")],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(consistent, path)

        result = runner.invoke(
            app, ["claim", "s1", "--agent", "bot", "--plan", str(path)]
        )
        assert result.exit_code == 0, f"Unexpected exit: {result.output}"
        assert "[vectl] Lock status updated:" not in result.output


# ---------------------------------------------------------------------------
# MCP _save() — autofix message format
# ---------------------------------------------------------------------------


class TestMcpAutofixMessage:
    """MCP _save() returns an informational [vectl] string, never a warning."""

    def test_autofix_message_returned(
        self, mcp_inconsistent_plan_file: Path
    ) -> None:
        """_save() return value contains [vectl] Lock status updated: …"""
        # Claiming a.1 triggers MCP _save().
        claim = vectl_claim(agent="bot", step_id="a.1")
        assert claim["ok"] is True, f"Claim failed: {claim}"
        # The autofix notice is embedded in the markdown summary.
        markdown = claim.get("markdown", "")
        assert "[vectl] Lock status updated:" in markdown

    def test_autofix_message_contains_phase_and_status(
        self, mcp_inconsistent_plan_file: Path
    ) -> None:
        """MCP return value includes phase ID and new status."""
        claim = vectl_claim(agent="bot", step_id="a.1")
        assert claim["ok"] is True, f"Claim failed: {claim}"
        markdown = claim.get("markdown", "")
        assert "phase-c" in markdown
        assert "(pending)" in markdown

    def test_autofix_message_not_a_warning(
        self, mcp_inconsistent_plan_file: Path
    ) -> None:
        """MCP return value must NOT contain any warning-style prefix."""
        claim = vectl_claim(agent="bot", step_id="a.1")
        assert claim["ok"] is True, f"Claim failed: {claim}"
        markdown = claim.get("markdown", "")
        markdown_lower = markdown.lower()
        assert "warning:" not in markdown_lower
        assert "warning " not in markdown_lower

    def test_no_autofix_message_when_consistent(self, tmp_path: Path) -> None:
        """When plan is consistent, _save() returns empty string for autofix."""
        from vectl.io import save_plan
        from vectl.models import Phase, PhaseStatus, Plan, Step

        consistent = Plan(
            project="consistent-mcp",
            phases=[
                Phase(
                    id="p1",
                    name="Phase 1",
                    status=PhaseStatus.PENDING,
                    steps=[Step(id="s1", name="Step 1")],
                ),
            ],
        )
        path = tmp_path / "plan.yaml"
        save_plan(consistent, path)

        old = os.environ.get("VECTL_PLAN_PATH")
        os.environ["VECTL_PLAN_PATH"] = str(path)
        try:
            claim = vectl_claim(agent="bot", step_id="s1")
            assert claim["ok"] is True, f"Claim failed: {claim}"
            markdown = claim.get("markdown", "")
            assert "[vectl] Lock status updated:" not in markdown
        finally:
            if old is None:
                os.environ.pop("VECTL_PLAN_PATH", None)
            else:
                os.environ["VECTL_PLAN_PATH"] = old
