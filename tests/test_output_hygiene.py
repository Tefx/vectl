"""Regression tests for Rich markup escaping in CLI output.

Ensures that user-supplied text containing Rich markup-like characters
(square brackets, etc.) is displayed literally and not swallowed by Rich.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vectl.cli import app
from vectl.io import save_plan
from vectl.models import Phase, PhaseStatus, Plan, Step

runner = CliRunner()


@pytest.fixture
def bracket_plan(tmp_path: Path) -> Path:
    """Plan with bracket-heavy content that would be swallowed by Rich markup."""
    plan = Plan(
        project="[red]test-project[/]",
        phases=[
            Phase(
                id="p1",
                name="Phase [bold]One[/bold]",
                status=PhaseStatus.IN_PROGRESS,
                context="Context with [dim]brackets[/]",
                gate="Check [yellow]gate[/]",
                steps=[
                    Step(
                        id="s1",
                        name="Step [mig.1]",
                        description="Checklist:\n- [ ] Item A\n- [x] Item B\n",
                        depends_on=[],
                    ),
                    Step(
                        id="s2",
                        name="Step [red]Two[/]",
                        depends_on=["s1"],
                        verification="Run [bold]tests[/]",
                    ),
                ],
            ),
        ],
    )
    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    return path


class TestMarkupNotSwallowed:
    """Brackets in user text must appear literally in CLI output."""

    def test_status_preserves_brackets_in_phase_name(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["status", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        # Phase name should contain literal brackets — not be interpreted as markup
        assert "[bold]One[/bold]" in result.output

    def test_show_phase_preserves_brackets(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["show", "p1", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[bold]One[/bold]" in result.output
        assert "[dim]brackets[/]" in result.output
        assert "[yellow]gate[/]" in result.output

    def test_show_step_preserves_brackets_in_name(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["show", "s1", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[mig.1]" in result.output

    def test_show_step_preserves_checklist_checkboxes(self, bracket_plan: Path) -> None:
        """Markdown checklists like '- [ ] Item' must not be eaten by Rich."""
        result = runner.invoke(app, ["show", "s1", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "- [ ] Item A" in result.output
        assert "- [x] Item B" in result.output

    def test_show_step_preserves_verification_brackets(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["show", "s2", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[bold]tests[/]" in result.output

    def test_review_preserves_brackets_in_phase_name(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[bold]One[/bold]" in result.output

    def test_review_preserves_brackets_in_step_name(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["review", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[mig.1]" in result.output

    def test_next_preserves_brackets(self, bracket_plan: Path) -> None:
        result = runner.invoke(app, ["next", "--plan", str(bracket_plan)])
        assert result.exit_code == 0
        assert "[mig.1]" in result.output
