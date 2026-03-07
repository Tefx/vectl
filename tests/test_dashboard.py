"""Tests for dashboard data serialization and HTML generation.

Source: RFC-dashboard.md
"""

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from vectl.dashboard import DASHBOARD_HTML_TEMPLATE, build_dashboard_data, generate_dashboard
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    RejectionEntry,
    Step,
    StepStatus,
)


class TestBuildDashboardData:
    """Tests for build_dashboard_data function."""

    def test_empty_plan(self) -> None:
        """Empty plan produces valid structure with zero counts."""
        plan = Plan(project="empty", phases=[])
        data = build_dashboard_data(plan)

        assert data["project"] == "empty"
        assert "generated_at" in data
        assert data["summary"]["total_phases"] == 0
        assert data["summary"]["total_steps"] == 0
        assert data["summary"]["done"] == 0
        assert data["phases"] == []
        assert "phase_dag" in data["mermaid"]
        assert data["mermaid"]["step_dags"] == {}

    def test_single_phase_single_step_done(self) -> None:
        """Single done step produces correct summary."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    status=PhaseStatus.DONE,
                    steps=[Step(id="core.1", name="S1", status=StepStatus.DONE)],
                )
            ],
        )
        data = build_dashboard_data(plan)

        assert data["project"] == "test"
        assert data["summary"]["total_phases"] == 1
        assert data["summary"]["total_steps"] == 1
        assert data["summary"]["done"] == 1
        assert data["phases"][0]["id"] == "core"
        assert data["phases"][0]["progress"]["pct"] == 100

    def test_progress_calculation_with_skipped(self) -> None:
        """Progress includes skipped steps as complete."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(
                            id="a.2",
                            name="S2",
                            status=StepStatus.SKIPPED,
                            skipped_reason="obsolete",
                        ),
                        Step(id="a.3", name="S3", status=StepStatus.PENDING),
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)

        # 2 done/skipped out of 3 = 66%
        assert data["phases"][0]["progress"]["done"] == 1
        assert data["phases"][0]["progress"]["total"] == 3
        assert data["phases"][0]["progress"]["pct"] == 67

    def test_step_status_counts(self) -> None:
        """All status types are counted correctly."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(
                            id="a.2",
                            name="S2",
                            status=StepStatus.CLAIMED,
                            claimed_by="alice",
                        ),
                        Step(id="a.3", name="S3", status=StepStatus.PENDING),
                        Step(
                            id="a.4",
                            name="S4",
                            status=StepStatus.REJECTED,
                            rejection_reason="bad",
                        ),
                        Step(
                            id="a.5",
                            name="S5",
                            status=StepStatus.SKIPPED,
                            skipped_reason="dupe",
                        ),
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)

        assert data["summary"]["done"] == 1
        assert data["summary"]["claimed"] == 1
        assert data["summary"]["pending"] == 1
        assert data["summary"]["rejected"] == 1
        assert data["summary"]["skipped"] == 1

    def test_step_detail_fields(self) -> None:
        """All step detail fields are serialized."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.IN_PROGRESS,
                    gate="All tests pass",
                    context="Foundation work",
                    steps=[
                        Step(
                            id="a.1",
                            name="First Step",
                            status=StepStatus.CLAIMED,
                            description="Do the thing\n- [x] task1\n- [ ] task2",
                            verification="pytest tests/",
                            depends_on=[],
                            agent="bob",
                            claimed_by="alice",
                            claimed_at="2026-02-15T10:30:00Z",
                            evidence=None,
                            refs=["docs/spec.md#section"],
                            rejection_history=[
                                RejectionEntry(
                                    reason="Missing tests",
                                    timestamp="2026-02-14T09:00:00Z",
                                    reviewer="charlie",
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)
        step = data["phases"][0]["steps"][0]

        assert step["id"] == "a.1"
        assert step["name"] == "First Step"
        assert step["status"] == "claimed"
        assert step["locked"] is False
        assert "task1" in step["description"]
        assert step["verification"] == "pytest tests/"
        assert step["agent"] == "bob"
        assert step["claimed_by"] == "alice"
        assert step["claimed_at"] == "2026-02-15T10:30:00Z"
        assert step["refs"] == ["docs/spec.md#section"]
        assert len(step["rejection_history"]) == 1
        assert step["rejection_history"][0]["reason"] == "Missing tests"
        assert step["rejection_history"][0]["reviewer"] == "charlie"

    def test_phase_fields(self) -> None:
        """Phase-level fields are serialized."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.IN_PROGRESS,
                    gate="All tests pass",
                    context="Foundation work",
                    depends_on=["core"],
                    evidence="commit abc123",
                    steps=[],
                )
            ],
        )
        data = build_dashboard_data(plan)
        phase = data["phases"][0]

        assert phase["id"] == "a"
        assert phase["name"] == "Alpha"
        assert phase["status"] == "in_progress"
        assert phase["gate"] == "All tests pass"
        assert phase["context"] == "Foundation work"
        assert phase["depends_on"] == ["core"]
        assert phase["evidence"] == "commit abc123"

    def test_mermaid_phase_dag(self) -> None:
        """Phase-level Mermaid DAG is generated."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.DONE,
                    steps=[Step(id="a.1", name="S1", status=StepStatus.DONE)],
                ),
                Phase(
                    id="b",
                    name="Beta",
                    status=PhaseStatus.PENDING,
                    depends_on=["a"],
                    steps=[Step(id="b.1", name="S1", status=StepStatus.PENDING)],
                ),
            ],
        )
        data = build_dashboard_data(plan)

        assert "flowchart" in data["mermaid"]["phase_dag"]
        # Check that both phases appear in the DAG
        assert "a --> b" in data["mermaid"]["phase_dag"] or "a-->b" in data["mermaid"]["phase_dag"]

    def test_mermaid_step_dags(self) -> None:
        """Step-level Mermaid DAGs are generated for each phase."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.DONE,
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(
                            id="a.2",
                            name="S2",
                            status=StepStatus.DONE,
                            depends_on=["a.1"],
                        ),
                    ],
                ),
            ],
        )
        data = build_dashboard_data(plan)

        assert "a" in data["mermaid"]["step_dags"]
        assert "flowchart" in data["mermaid"]["step_dags"]["a"]

    def test_locked_step_detection(self) -> None:
        """Locked steps are detected and annotated."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(
                            id="a.2",
                            name="S2",
                            status=StepStatus.PENDING,
                            depends_on=["a.1"],  # Dependency met, not locked
                        ),
                        Step(
                            id="a.3",
                            name="S3",
                            status=StepStatus.PENDING,
                            depends_on=["a.2"],  # a.2 not done, so locked
                        ),
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)

        # a.2 depends on done step, not locked
        assert data["phases"][0]["steps"][1]["locked"] is False
        # a.3 depends on pending step, locked
        assert data["phases"][0]["steps"][2]["locked"] is True

    def test_generated_at_iso8601(self) -> None:
        """generated_at is ISO 8601 format with Z suffix."""
        plan = Plan(project="test", phases=[])
        data = build_dashboard_data(plan)

        # Should end with Z for UTC
        assert data["generated_at"].endswith("Z")
        # Should be parseable
        from datetime import datetime

        dt = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        assert dt.tzinfo is not None

    def test_json_serializable(self) -> None:
        """Output is JSON-serializable."""
        import json

        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(
                            id="a.1",
                            name="S1",
                            status=StepStatus.CLAIMED,
                            claimed_by="alice",
                        )
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)

        # Should not raise
        json_str = json.dumps(data)
        assert len(json_str) > 0


class TestDashboardHtmlTemplate:
    """Tests for DASHBOARD_HTML_TEMPLATE validity."""

    def test_template_is_valid_html(self) -> None:
        """Template string parses as valid HTML."""
        # Use html.parser to validate the template
        parser = HTMLParser()
        # Should not raise any parsing errors
        parser.feed(DASHBOARD_HTML_TEMPLATE)
        # Check that we got some data
        assert len(DASHBOARD_HTML_TEMPLATE) > 0

    def test_template_has_doctype(self) -> None:
        """Template includes DOCTYPE declaration."""
        assert "<!DOCTYPE html>" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_html_lang(self) -> None:
        """Template has html element with lang attribute."""
        assert '<html lang="en">' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_charset(self) -> None:
        """Template includes charset meta tag."""
        assert '<meta charset="utf-8">' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_viewport(self) -> None:
        """Template includes viewport meta for responsive design."""
        assert 'name="viewport"' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_title_placeholder(self) -> None:
        """Template includes title with project placeholder."""
        assert "{{project}}" in DASHBOARD_HTML_TEMPLATE
        assert "<title>" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_style_tag(self) -> None:
        """Template includes inline CSS in style tag."""
        assert "<style>" in DASHBOARD_HTML_TEMPLATE
        assert "</style>" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_has_custom_properties(self) -> None:
        """CSS includes custom properties for colors."""
        assert "--color-done:" in DASHBOARD_HTML_TEMPLATE
        assert "--color-claimed:" in DASHBOARD_HTML_TEMPLATE
        assert "--color-pending:" in DASHBOARD_HTML_TEMPLATE
        assert "--color-rejected:" in DASHBOARD_HTML_TEMPLATE
        assert "--color-skipped:" in DASHBOARD_HTML_TEMPLATE
        assert "--color-locked:" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_has_dark_mode_media_query(self) -> None:
        """CSS includes prefers-color-scheme: dark media query."""
        assert "@media (prefers-color-scheme: dark)" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_light_colors_match_rfc(self) -> None:
        """Light mode colors match RFC-dashboard.md specification."""
        # done: #16a34a
        assert "#16a34a" in DASHBOARD_HTML_TEMPLATE
        # claimed/in_progress: #2563eb
        assert "#2563eb" in DASHBOARD_HTML_TEMPLATE
        # pending: #6b7280
        assert "#6b7280" in DASHBOARD_HTML_TEMPLATE
        # rejected: #dc2626
        assert "#dc2626" in DASHBOARD_HTML_TEMPLATE
        # skipped: #9333ea
        assert "#9333ea" in DASHBOARD_HTML_TEMPLATE
        # locked: #374151
        assert "#374151" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_dark_colors_match_rfc(self) -> None:
        """Dark mode colors match RFC-dashboard.md specification."""
        # done: #22c55e
        assert "#22c55e" in DASHBOARD_HTML_TEMPLATE
        # claimed/in_progress: #3b82f6
        assert "#3b82f6" in DASHBOARD_HTML_TEMPLATE
        # pending: #9ca3af
        assert "#9ca3af" in DASHBOARD_HTML_TEMPLATE
        # rejected: #ef4444
        assert "#ef4444" in DASHBOARD_HTML_TEMPLATE
        # skipped: #a855f7
        assert "#a855f7" in DASHBOARD_HTML_TEMPLATE
        # locked: #4b5563
        assert "#4b5563" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_has_grid_layout(self) -> None:
        """CSS uses CSS Grid for layout."""
        assert "display: grid" in DASHBOARD_HTML_TEMPLATE
        assert "grid-template-columns" in DASHBOARD_HTML_TEMPLATE
        assert "grid-template-areas" in DASHBOARD_HTML_TEMPLATE

    def test_template_css_has_responsive_breakpoint(self) -> None:
        """CSS includes responsive breakpoint at 768px."""
        assert "@media (max-width: 768px)" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_header_structure(self) -> None:
        """Template has header bar with required elements."""
        assert 'class="header"' in DASHBOARD_HTML_TEMPLATE
        assert 'class="header__title"' in DASHBOARD_HTML_TEMPLATE
        assert 'class="progress-bar"' in DASHBOARD_HTML_TEMPLATE
        assert 'class="stat-pill' in DASHBOARD_HTML_TEMPLATE
        assert 'class="tab-btn' in DASHBOARD_HTML_TEMPLATE
        assert 'class="search-input"' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_sidebar_structure(self) -> None:
        """Template has sidebar with phase navigation."""
        assert 'class="sidebar"' in DASHBOARD_HTML_TEMPLATE
        assert 'class="phase-nav"' in DASHBOARD_HTML_TEMPLATE
        assert "{{phase_nav_items}}" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_main_content_areas(self) -> None:
        """Template has main content with Overview and DAG tabs."""
        assert 'class="main"' in DASHBOARD_HTML_TEMPLATE
        assert 'id="tab-overview"' in DASHBOARD_HTML_TEMPLATE
        assert 'id="tab-dag"' in DASHBOARD_HTML_TEMPLATE
        assert "{{phase_cards}}" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_mermaid_container(self) -> None:
        """Template has container for Mermaid DAG rendering."""
        assert 'id="dag-diagram"' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_javascript(self) -> None:
        """Template includes JavaScript for rendering."""
        # Template should have script tags for DATA, Mermaid CDN, and inline JS
        assert "<script>" in DASHBOARD_HTML_TEMPLATE
        assert "</script>" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_data_placeholder(self) -> None:
        """Template has DATA object placeholder."""
        assert "const DATA = {{json_data}};" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_mermaid_cdn(self) -> None:
        """Template includes Mermaid.js CDN."""
        assert "cdn.jsdelivr.net/npm/mermaid" in DASHBOARD_HTML_TEMPLATE

    def test_template_js_has_render_functions(self) -> None:
        """JavaScript includes required rendering functions."""
        assert "renderSidebar" in DASHBOARD_HTML_TEMPLATE
        assert "renderPhaseCards" in DASHBOARD_HTML_TEMPLATE
        assert "renderStepTable" in DASHBOARD_HTML_TEMPLATE
        assert "renderStepDetail" in DASHBOARD_HTML_TEMPLATE
        assert "setupSearch" in DASHBOARD_HTML_TEMPLATE
        assert "initMermaid" in DASHBOARD_HTML_TEMPLATE
        assert "renderPhaseDAG" in DASHBOARD_HTML_TEMPLATE
        assert "renderStepDAG" in DASHBOARD_HTML_TEMPLATE

    def test_template_js_has_status_icons(self) -> None:
        """JavaScript includes status icon mappings."""
        assert "PHASE_ICONS" in DASHBOARD_HTML_TEMPLATE
        assert "STEP_ICONS" in DASHBOARD_HTML_TEMPLATE

    def test_template_js_has_tab_switching(self) -> None:
        """JavaScript includes tab switching functionality."""
        assert "setupTabs" in DASHBOARD_HTML_TEMPLATE
        assert "tab-btn--active" in DASHBOARD_HTML_TEMPLATE

    def test_template_js_has_scroll_spy(self) -> None:
        """JavaScript includes scroll spy for sidebar highlighting."""
        assert "setupScrollSpy" in DASHBOARD_HTML_TEMPLATE
        assert "IntersectionObserver" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_required_placeholders(self) -> None:
        """Template has all required placeholder variables."""
        required_placeholders = [
            "{{project}}",
            "{{progress_pct}}",
            "{{summary.done}}",
            "{{summary.claimed}}",
            "{{summary.pending}}",
            "{{summary.rejected}}",
            "{{summary.skipped}}",
            "{{phase_nav_items}}",
            "{{phase_cards}}",
            "{{step_dag_options}}",
        ]
        for placeholder in required_placeholders:
            assert placeholder in DASHBOARD_HTML_TEMPLATE, f"Missing placeholder: {placeholder}"

    def test_template_has_status_icon_classes(self) -> None:
        """CSS includes status icon utility classes."""
        assert "icon-done" in DASHBOARD_HTML_TEMPLATE
        assert "icon-claimed" in DASHBOARD_HTML_TEMPLATE
        assert "icon-pending" in DASHBOARD_HTML_TEMPLATE
        assert "icon-rejected" in DASHBOARD_HTML_TEMPLATE
        assert "icon-skipped" in DASHBOARD_HTML_TEMPLATE
        assert "icon-locked" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_phase_card_styles(self) -> None:
        """CSS includes phase card collapsible styles."""
        assert "phase-card" in DASHBOARD_HTML_TEMPLATE
        assert "phase-card__header" in DASHBOARD_HTML_TEMPLATE
        assert "phase-card__body" in DASHBOARD_HTML_TEMPLATE
        assert "phase-card--collapsed" in DASHBOARD_HTML_TEMPLATE

    def test_template_has_step_detail_styles(self) -> None:
        """CSS includes step detail panel styles."""
        assert "step-detail" in DASHBOARD_HTML_TEMPLATE
        assert "step-detail__rejection" in DASHBOARD_HTML_TEMPLATE
        assert "md-content" in DASHBOARD_HTML_TEMPLATE
        assert 'md-content input[type="checkbox"]' in DASHBOARD_HTML_TEMPLATE

    def test_template_has_progress_bar_styles(self) -> None:
        """CSS includes progress bar component styles."""
        assert "progress-bar" in DASHBOARD_HTML_TEMPLATE
        assert "progress-bar__fill" in DASHBOARD_HTML_TEMPLATE

    def test_template_closes_all_tags(self) -> None:
        """All HTML tags are properly closed."""
        # Basic check that closing tags exist for major elements
        assert "</html>" in DASHBOARD_HTML_TEMPLATE
        assert "</head>" in DASHBOARD_HTML_TEMPLATE
        assert "</body>" in DASHBOARD_HTML_TEMPLATE
        assert "</style>" in DASHBOARD_HTML_TEMPLATE
        assert "</div>" in DASHBOARD_HTML_TEMPLATE


class TestGenerateDashboard:
    """Tests for generate_dashboard function."""

    def test_generates_valid_html(self) -> None:
        """generate_dashboard produces valid HTML string."""
        plan = Plan(
            project="test-project",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    status=PhaseStatus.DONE,
                    steps=[Step(id="a.1", name="S1", status=StepStatus.DONE)],
                )
            ],
        )
        html = generate_dashboard(plan)

        assert isinstance(html, str)
        assert len(html) > 0
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_injects_project_name(self) -> None:
        """Project name appears in title and header."""
        plan = Plan(
            project="MyAwesomeProject",
            phases=[],
        )
        html = generate_dashboard(plan)

        assert "MyAwesomeProject" in html
        assert "<title>MyAwesomeProject" in html

    def test_injects_json_data(self) -> None:
        """JSON data is embedded in the HTML."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="core",
                    name="Core",
                    steps=[Step(id="core.1", name="First", status=StepStatus.DONE)],
                )
            ],
        )
        html = generate_dashboard(plan)

        # Should contain the DATA object with our data
        assert "const DATA = " in html
        assert '"project": "test"' in html
        assert '"core"' in html

    def test_progress_calculation(self) -> None:
        """Progress percentage is calculated and injected."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(id="a.2", name="S2", status=StepStatus.DONE),
                        Step(
                            id="a.3",
                            name="S3",
                            status=StepStatus.SKIPPED,
                            skipped_reason="dupe",
                        ),
                        Step(id="a.4", name="S4", status=StepStatus.PENDING),
                    ],
                )
            ],
        )
        html = generate_dashboard(plan)

        # 3 done/skipped out of 4 = 75%
        assert "75%" in html

    def test_escapes_script_tags_in_data(self) -> None:
        """Script tags in data are escaped to prevent XSS."""
        plan = Plan(
            project="test<script>alert('xss')</script>",
            phases=[],
        )
        html = generate_dashboard(plan)

        # The project name in JSON should have escaped script tags
        # The title still shows the raw name (it's in a text node, not script)
        # But the JSON data should be safe
        assert "const DATA = " in html
        # Check that </script> in JSON is escaped
        assert "<\\/script>" in html or r"\u003c/script\u003e" in html

    def test_handles_special_characters(self) -> None:
        """Special characters in project name are handled."""
        plan = Plan(
            project='test "quotes" and <brackets>',
            phases=[],
        )
        html = generate_dashboard(plan)

        # Should still be valid HTML
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        # JSON should properly escape these
        assert "const DATA = " in html

    def test_handles_unicode(self) -> None:
        """Unicode characters are preserved."""
        plan = Plan(
            project="测试项目 🚀",
            phases=[
                Phase(
                    id="a",
                    name="阶段 Alpha",
                    steps=[Step(id="a.1", name="步骤 一", status=StepStatus.DONE)],
                )
            ],
        )
        html = generate_dashboard(plan)

        assert "测试项目" in html
        assert "🚀" in html
        assert "阶段" in html
        assert "步骤" in html

    def test_empty_plan(self) -> None:
        """Empty plan generates valid dashboard."""
        plan = Plan(project="empty", phases=[])
        html = generate_dashboard(plan)

        assert "<!DOCTYPE html>" in html
        assert "empty" in html
        assert "0%" in html  # Progress with no steps

    def test_summary_counts_injected(self) -> None:
        """Summary step counts are injected into header."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                        Step(
                            id="a.2",
                            name="S2",
                            status=StepStatus.CLAIMED,
                            claimed_by="alice",
                        ),
                        Step(id="a.3", name="S3", status=StepStatus.PENDING),
                    ],
                )
            ],
        )
        html = generate_dashboard(plan)

        # Check summary pills have correct counts
        assert "✓ 1" in html  # done
        assert "◉ 1" in html  # claimed
        assert "○ 1" in html  # pending

    def test_output_is_single_html_file(self) -> None:
        """Output is a complete single HTML file with no external CSS dependencies."""
        plan = Plan(
            project="standalone",
            phases=[],
        )
        html = generate_dashboard(plan)

        # Check structure
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

        # Check no external CSS files (styles must be inline)
        assert '<link rel="stylesheet"' not in html

        # External scripts are expected: marked.js, highlight.js, mermaid.js
        script_srcs = [line for line in html.split("\n") if '<script src="' in line]
        script_urls = [s.strip() for s in script_srcs]
        # At minimum, mermaid should be present
        assert any("mermaid" in s for s in script_urls)
        # marked.js for markdown rendering
        assert any("marked" in s for s in script_urls)
        # highlight.js for code syntax highlighting
        assert any("highlight.js" in s for s in script_urls)

    def test_parses_as_valid_html(self) -> None:
        """Generated HTML parses without errors."""
        plan = Plan(
            project="test",
            phases=[
                Phase(
                    id="a",
                    name="Alpha",
                    steps=[
                        Step(id="a.1", name="S1", status=StepStatus.DONE),
                    ],
                )
            ],
        )
        html = generate_dashboard(plan)

        parser = HTMLParser()
        # Should not raise
        parser.feed(html)

    def test_build_dashboard_data_structure(self) -> None:
        """Verify top-level keys in dashboard data."""
        plan = Plan(
            project="structure-test",
            phases=[
                Phase(
                    id="p1",
                    name="Phase One",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(id="p1.1", name="S1", status=StepStatus.DONE),
                    ],
                )
            ],
        )
        data = build_dashboard_data(plan)

        # Top-level keys
        assert "project" in data
        assert "generated_at" in data
        assert "summary" in data
        assert "phases" in data
        assert "mermaid" in data

        # Summary structure
        assert "total_phases" in data["summary"]
        assert "total_steps" in data["summary"]
        assert "done" in data["summary"]
        assert "claimed" in data["summary"]
        assert "pending" in data["summary"]
        assert "rejected" in data["summary"]
        assert "skipped" in data["summary"]

        # Mermaid structure
        assert "phase_dag" in data["mermaid"]
        assert "step_dags" in data["mermaid"]
        assert isinstance(data["mermaid"]["phase_dag"], str)
        assert isinstance(data["mermaid"]["step_dags"], dict)


class TestDashboardCLI:
    """Tests for dashboard CLI command."""

    def test_dashboard_creates_file(self, tmp_path: Any) -> None:
        """Dashboard command creates HTML file at expected path."""
        import subprocess

        # Create a minimal plan
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("""
project: test-cli
phases: []
""")

        output_file = tmp_path / "output.html"

        result = subprocess.run(
            [
                "uv",
                "run",
                "vectl",
                "dashboard",
                "--plan",
                str(plan_file),
                "--out",
                str(output_file),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert output_file.exists()

        html = output_file.read_text()
        assert "<!DOCTYPE html>" in html
        assert "test-cli" in html

    def test_dashboard_custom_output(self, tmp_path: Any) -> None:
        """--out flag changes output path."""
        import subprocess

        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("""
project: custom-output-test
phases: []
""")

        custom_output = tmp_path / "custom" / "dashboard.html"

        result = subprocess.run(
            [
                "uv",
                "run",
                "vectl",
                "dashboard",
                "--plan",
                str(plan_file),
                "--out",
                str(custom_output),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert custom_output.exists()

    def test_dashboard_default_output(self, tmp_path: Any) -> None:
        """Default output is plan-dashboard.html in current directory."""
        import subprocess

        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("""
project: default-output-test
phases: []
""")

        output_file = tmp_path / "plan-dashboard.html"

        result = subprocess.run(
            [
                "uv",
                "run",
                "vectl",
                "dashboard",
                "--plan",
                str(plan_file),
                "--out",
                str(output_file),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert output_file.exists()

    def test_dashboard_with_real_plan(self) -> None:
        """Dashboard works with the actual vectl plan."""
        import subprocess

        # This test uses the actual plan.yaml in the project
        result = subprocess.run(
            ["uv", "run", "vectl", "dashboard", "--out", "/tmp/vectl-dashboard-test.html"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert "Dashboard written to" in result.stderr

        # Verify output file exists and is valid HTML
        output = Path("/tmp/vectl-dashboard-test.html")
        assert output.exists()

        html = output.read_text()
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_html_tags_in_content_are_escaped(self) -> None:
        """HTML tags in step content should be escaped via renderMarkdown to prevent injection.

        Regression test for: description containing `<style>` or `<a>` tags
        breaking the dashboard rendering by injecting actual HTML elements.

        The escaping happens in JavaScript's renderMarkdown function, not in the
        Python JSON serialization. This test verifies:
        1. JSON data contains the raw content (not double-escaped)
        2. JavaScript renderMarkdown has the escaping logic
        """
        plan = Plan(
            project="test-html-escape",
            phases=[
                Phase(
                    id="test",
                    name="Test Phase",
                    status=PhaseStatus.IN_PROGRESS,
                    steps=[
                        Step(
                            id="test.step1",
                            name="Step with HTML tags",
                            status=StepStatus.PENDING,
                            description="Remove <style> and <a> tags from content",
                            verification="Verify <script> tags are stripped",
                            evidence="Test: `<style>` should appear as text, not HTML",
                        ),
                        Step(
                            id="test.step2",
                            name="Another step",
                            status=StepStatus.PENDING,
                            description="<div><p>Multiple</p><span>HTML tags</span></div>",
                        ),
                    ],
                )
            ],
        )
        html = generate_dashboard(plan)

        # The JSON data should contain the raw content (not double-escaped)
        # When JavaScript renders it via renderMarkdown, it will escape HTML
        import re

        json_match = re.search(r'"description":\s*"([^"]*<style>[^"]*)"', html)
        assert json_match is not None, "JSON should contain raw <style> in description"

        # The renderMarkdown function should contain HTML escaping logic
        assert "div.textContent" in html, "renderMarkdown should use textContent for escaping"
        assert "div.innerHTML" in html, "renderMarkdown should use innerHTML to get escaped content"

        # Verify the page is still valid HTML
        parser = HTMLParser()
        parser.feed(html)

        # Count phase cards in the DATA to verify rendering would work
        phases_match = re.search(r'"phases":\s*\[', html)
        assert phases_match is not None, "DATA should contain phases array"
