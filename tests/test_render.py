"""Tests for render_plan (ho.1: yaml→markdown export)."""

import pytest

from vectl.core import _first_line, generate_mermaid_dag, render_plan
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    PlanError,
    Step,
    StepStatus,
)


def _plan_with_mixed_statuses() -> Plan:
    return Plan(
        project="myproject",
        phases=[
            Phase(
                id="alpha",
                name="Alpha Phase",
                status=PhaseStatus.DONE,
                gate="All alpha tests pass",
                context="Foundation layer.",
                steps=[
                    Step(
                        id="a.1",
                        name="First",
                        status=StepStatus.DONE,
                        description="Build the thing",
                    ),
                    Step(
                        id="a.2",
                        name="Second",
                        status=StepStatus.SKIPPED,
                        skipped_reason="superseded",
                    ),
                ],
            ),
            Phase(
                id="beta",
                name="Beta Phase",
                status=PhaseStatus.IN_PROGRESS,
                depends_on=["alpha"],
                steps=[
                    Step(id="b.1", name="Third", status=StepStatus.DONE),
                    Step(
                        id="b.2",
                        name="Fourth",
                        status=StepStatus.CLAIMED,
                        claimed_by="agent-x",
                        claimed_at="2026-01-01T00:00:00Z",
                    ),
                    Step(id="b.3", name="Fifth", status=StepStatus.PENDING),
                ],
            ),
            Phase(
                id="gamma",
                name="Gamma Phase",
                status=PhaseStatus.LOCKED,
                depends_on=["beta"],
                steps=[
                    Step(id="g.1", name="Sixth", status=StepStatus.PENDING),
                ],
            ),
        ],
    )


class TestRenderPlan:
    """Core render_plan function tests."""

    def test_full_plan_has_title(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert md.startswith("# myproject")

    def test_full_plan_has_summary_table(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "| Status | Phase | Name | Progress |" in md
        assert "| ✓ done | alpha | Alpha Phase | 2/2 (100%) |" in md
        assert "| ▶ in_progress | beta | Beta Phase | 1/3 (33%) |" in md
        assert "| 🔒 locked | gamma | Gamma Phase | 0/1 (0%) |" in md

    def test_full_plan_has_phase_sections(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "## ✓ alpha — Alpha Phase (2/2, 100%)" in md
        assert "## ▶ beta — Beta Phase (1/3, 33%)" in md
        assert "## 🔒 gamma — Gamma Phase (0/1, 0%)" in md

    def test_full_plan_has_step_bullets(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "- ✓ **a.1** First" in md
        assert "- ⊘ **a.2** Second" in md
        assert "- ◉ **b.2** Fourth" in md
        assert "- ○ **b.3** Fifth" in md

    def test_omits_claimed_by(self) -> None:
        """Stakeholder report omits operational detail."""
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "agent-x" not in md
        assert "claimed_by" not in md
        assert "2026-01-01" not in md

    def test_includes_gate(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "**Gate:** All alpha tests pass" in md

    def test_includes_context(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "> Foundation layer." in md

    def test_step_description_summary(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p)
        assert "Build the thing" in md

    def test_single_phase_filter(self) -> None:
        p = _plan_with_mixed_statuses()
        md = render_plan(p, phase_id="beta")
        assert "## ▶ beta — Beta Phase" in md
        assert "alpha" not in md.lower().split("beta")[0]  # no alpha before beta
        assert "gamma" not in md

    def test_single_phase_not_found(self) -> None:
        p = _plan_with_mixed_statuses()
        with pytest.raises(PlanError, match="not found"):
            render_plan(p, phase_id="nonexistent")

    def test_empty_plan(self) -> None:
        p = Plan(project="empty")
        md = render_plan(p)
        assert "# empty" in md
        assert "| Status |" in md

    def test_phase_with_no_steps(self) -> None:
        p = Plan(
            project="t",
            phases=[Phase(id="x", name="Empty Phase", status=PhaseStatus.PENDING)],
        )
        md = render_plan(p)
        assert "0/0 (0%)" in md


class TestFirstLine:
    """Tests for _first_line helper."""

    def test_simple(self) -> None:
        assert _first_line("hello world") == "hello world"

    def test_multiline(self) -> None:
        assert _first_line("line one\nline two") == "line one"

    def test_skips_empty_lines(self) -> None:
        assert _first_line("\n\n  \nhello") == "hello"

    def test_skips_checklist(self) -> None:
        assert _first_line("- [ ] task\nreal summary") == "real summary"

    def test_truncates(self) -> None:
        long = "a" * 100
        result = _first_line(long, max_len=20)
        assert len(result) == 20
        assert result.endswith("…")

    def test_empty_string(self) -> None:
        assert _first_line("") == ""

    def test_whitespace_only(self) -> None:
        assert _first_line("   \n  \n") == ""


class TestRenderFull:
    """Tests for render_plan with full=True."""

    def _plan_with_long_description(self) -> Plan:
        long_desc = (
            "Implement vectl add-step --phase <id> --name <n> --description <d> "
            "[--depends-on dep1,dep2] [--verification cmd] [--refs a,b]"
        )
        multi_line_desc = "First line of description.\nSecond line with details.\nThird line."
        return Plan(
            project="fulltest",
            phases=[
                Phase(
                    id="p1",
                    name="Phase One",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(
                            id="p1.long",
                            name="Long Step",
                            status=StepStatus.PENDING,
                            description=long_desc,
                        ),
                        Step(
                            id="p1.multi",
                            name="Multi Line",
                            status=StepStatus.PENDING,
                            description=multi_line_desc,
                        ),
                        Step(
                            id="p1.empty",
                            name="No Desc",
                            status=StepStatus.PENDING,
                        ),
                    ],
                ),
            ],
        )

    def test_default_truncates_long_description(self) -> None:
        p = self._plan_with_long_description()
        md = render_plan(p)
        assert "…" in md  # truncation marker present

    def test_full_no_truncation(self) -> None:
        p = self._plan_with_long_description()
        md = render_plan(p, full=True)
        assert "…" not in md  # no truncation
        assert "[--refs a,b]" in md  # full content preserved

    def test_full_multiline_indented(self) -> None:
        p = self._plan_with_long_description()
        md = render_plan(p, full=True)
        assert "  First line of description." in md
        assert "  Second line with details." in md
        assert "  Third line." in md

    def test_full_step_without_description(self) -> None:
        p = self._plan_with_long_description()
        md = render_plan(p, full=True)
        # Step with no description should still render fine
        assert "**p1.empty** No Desc" in md

    def test_full_with_phase_filter(self) -> None:
        p = self._plan_with_long_description()
        md = render_plan(p, phase_id="p1", full=True)
        assert "…" not in md
        assert "[--refs a,b]" in md


# ---------------------------------------------------------------------------
# generate_mermaid_dag tests
# ---------------------------------------------------------------------------


class TestMermaidDag:
    """Tests for generate_mermaid_dag()."""

    @staticmethod
    def _dag_plan() -> Plan:
        return Plan(
            project="dagtest",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(id="core.a", name="A", status=StepStatus.DONE),
                        Step(id="core.b", name="B", status=StepStatus.DONE, depends_on=["core.a"]),
                    ],
                ),
                Phase(
                    id="cli",
                    name="CLI",
                    status=PhaseStatus.IN_PROGRESS,
                    depends_on=["core"],
                    steps=[
                        Step(id="cli.x", name="X", status=StepStatus.PENDING),
                        Step(id="cli.y", name="Y", status=StepStatus.PENDING, depends_on=["cli.x"]),
                    ],
                ),
                Phase(
                    id="mcp",
                    name="MCP",
                    status=PhaseStatus.LOCKED,
                    depends_on=["core", "cli"],
                    steps=[
                        Step(id="mcp.z", name="Z", status=StepStatus.PENDING),
                    ],
                ),
            ],
        )

    def test_phase_dag_has_flowchart_header(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan())
        assert mmd.startswith("flowchart TD")

    def test_phase_dag_contains_all_phases(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan())
        assert 'core["✓ Core (2/2)"]' in mmd
        assert 'cli["▶ CLI (0/2)"]' in mmd
        assert 'mcp["🔒 MCP (0/1)"]' in mmd

    def test_phase_dag_contains_edges(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan())
        assert "core --> cli" in mmd
        assert "core --> mcp" in mmd
        assert "cli --> mcp" in mmd

    def test_phase_dag_has_hint(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan())
        assert "vectl dag --phase" in mmd

    def test_step_dag_has_flowchart_header(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan(), phase_id="core")
        assert mmd.startswith("flowchart TD")

    def test_step_dag_contains_steps(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan(), phase_id="core")
        assert '["✓ A"]' in mmd
        assert '["✓ B"]' in mmd

    def test_step_dag_contains_edges(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan(), phase_id="core")
        assert "core_a --> core_b" in mmd

    def test_step_dag_no_cross_phase_edges(self) -> None:
        mmd = generate_mermaid_dag(self._dag_plan(), phase_id="cli")
        assert "cli_x --> cli_y" in mmd
        # No reference to core steps
        assert "core" not in mmd

    def test_step_dag_phase_not_found(self) -> None:
        with pytest.raises(PlanError, match="not found"):
            generate_mermaid_dag(self._dag_plan(), phase_id="nope")

    def test_empty_plan(self) -> None:
        p = Plan(project="empty", phases=[])
        mmd = generate_mermaid_dag(p)
        assert "flowchart TD" in mmd

    def test_no_deps_no_edges(self) -> None:
        p = Plan(
            project="nodeps",
            phases=[
                Phase(id="a", name="A", status=PhaseStatus.PENDING, steps=[]),
                Phase(id="b", name="B", status=PhaseStatus.PENDING, steps=[]),
            ],
        )
        mmd = generate_mermaid_dag(p)
        assert "-->" not in mmd

    def test_mermaid_node_id_sanitization(self) -> None:
        from vectl.core import _mermaid_node_id

        assert _mermaid_node_id("core.validate-plan") == "core_validate_plan"
        assert _mermaid_node_id("simple") == "simple"
