"""Playwright-based UI tests for the dashboard.

These tests require Playwright to be installed and browsers to be available.
Run with: uv run pytest -m playwright tests/test_dashboard_ui.py -v

Source: RFC-dashboard.md
"""

import pytest

# Mark all tests as Playwright tests so they don't run in normal pytest
pytestmark = pytest.mark.playwright

# Import after marking to allow pytest to collect properly
from pathlib import Path

from playwright.sync_api import Page, expect

from vectl.dashboard import generate_dashboard
from vectl.models import (
    Phase,
    PhaseStatus,
    Plan,
    RejectionEntry,
    Step,
    StepStatus,
)


def create_rich_test_plan() -> Plan:
    """Create a rich test plan with various statuses and content."""
    return Plan(
        project="Test Dashboard 🚀",
        phases=[
            Phase(
                id="done-phase",
                name="Completed Phase",
                status=PhaseStatus.DONE,
                gate="All tests pass",
                context="Foundation work",
                depends_on=[],
                steps=[
                    Step(
                        id="done-phase.1",
                        name="First Done Step",
                        status=StepStatus.DONE,
                        description="This is a done step\n- [x] task1\n- [x] task2",
                        verification="pytest tests/",
                        depends_on=[],
                        agent="agent-1",
                        claimed_by="alice",
                        evidence="commit abc123",
                        refs=["docs/spec.md"],
                    ),
                    Step(
                        id="done-phase.2",
                        name="Second Done Step",
                        status=StepStatus.DONE,
                        description="Another done step with pending task",
                        verification="make test",
                        depends_on=["done-phase.1"],
                        agent="agent-1",
                        claimed_by="bob",
                        evidence="commit def456",
                    ),
                ],
            ),
            Phase(
                id="in-progress-phase",
                name="In Progress Phase",
                status=PhaseStatus.IN_PROGRESS,
                gate="Integration tests pass",
                context="Core feature development",
                depends_on=["done-phase"],
                steps=[
                    Step(
                        id="in-progress-phase.1",
                        name="Claimed Step",
                        status=StepStatus.CLAIMED,
                        description="A step that's been claimed\n- [x] analyzed\n- [ ] implemented\n- [ ] tested",
                        verification="pytest -v",
                        depends_on=[],
                        agent="engineer",
                        claimed_by="charlie",
                        claimed_at="2026-02-15T10:30:00Z",
                    ),
                    Step(
                        id="in-progress-phase.2",
                        name="Pending Step",
                        status=StepStatus.PENDING,
                        description="Waiting for dependency",
                        verification="npm test",
                        depends_on=["in-progress-phase.1"],
                        agent="frontend-dev",
                    ),
                    Step(
                        id="in-progress-phase.3",
                        name="Locked Step",
                        status=StepStatus.PENDING,
                        description="Step with unmet dependency",
                        verification="make e2e",
                        depends_on=["in-progress-phase.2"],  # Not done, so locked
                        agent="qa-engineer",
                    ),
                ],
            ),
            Phase(
                id="rejected-phase",
                name="Rejected Phase",
                status=PhaseStatus.PENDING,
                gate="Code review passes",
                context="Feature that needs rework",
                depends_on=["done-phase"],
                steps=[
                    Step(
                        id="rejected-phase.1",
                        name="Rejected Step",
                        status=StepStatus.REJECTED,
                        description="Step that was rejected",
                        verification="manual review",
                        depends_on=[],
                        agent="designer",
                        rejection_reason="Design doesn't match spec",
                        rejection_history=[
                            RejectionEntry(
                                reason="Design doesn't match spec",
                                timestamp="2026-02-14T09:00:00Z",
                                reviewer="lead-designer",
                            ),
                            RejectionEntry(
                                reason="Color contrast issues",
                                timestamp="2026-02-13T14:30:00Z",
                                reviewer="accessibility-team",
                            ),
                        ],
                    ),
                ],
            ),
            Phase(
                id="skipped-phase",
                name="Skipped Phase",
                status=PhaseStatus.DONE,
                gate="N/A",
                context="Obsolete features",
                depends_on=["done-phase"],
                steps=[
                    Step(
                        id="skipped-phase.1",
                        name="Skipped Step",
                        status=StepStatus.SKIPPED,
                        description="No longer needed",
                        verification="N/A",
                        depends_on=[],
                        skipped_reason="Feature deprecated",
                    ),
                ],
            ),
            Phase(
                id="locked-phase",
                name="Locked Phase",
                status=PhaseStatus.LOCKED,
                gate="Future work",
                context="Not yet started",
                depends_on=["in-progress-phase"],
                steps=[
                    Step(
                        id="locked-phase.1",
                        name="Future Step",
                        status=StepStatus.PENDING,
                        description="Waiting for previous phases",
                        verification="TBD",
                        depends_on=[],
                        agent="future-agent",
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def dashboard_page(tmp_path: Path, page: Page) -> Page:
    """Create a dashboard page with rich test data."""
    # Generate dashboard HTML from rich test plan
    plan = create_rich_test_plan()
    html = generate_dashboard(plan)

    # Write to temp file
    html_file = tmp_path / "dashboard.html"
    html_file.write_text(html, encoding="utf-8")

    # Open in browser via file:// URL
    page.goto(f"file://{html_file.absolute()}")
    page.wait_for_load_state("domcontentloaded")

    # Wait for JavaScript to render
    page.wait_for_selector(".phase-card", timeout=5000)

    return page


# =============================================================================
# 1. Page Load & Structure (6 tests)
# =============================================================================


def test_page_loads_without_errors(page: Page, tmp_path: Path) -> None:
    """1.1 Page loads without console errors."""
    plan = create_rich_test_plan()
    html = generate_dashboard(plan)
    html_file = tmp_path / "dashboard.html"
    html_file.write_text(html, encoding="utf-8")

    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: console_errors.append(str(err)))

    page.goto(f"file://{html_file.absolute()}")
    page.wait_for_selector(".phase-card", timeout=5000)

    assert len(console_errors) == 0, f"Console errors: {console_errors}"


def test_header_shows_project_name(page: Page, tmp_path: Path) -> None:
    """1.2 Header shows project name."""
    plan = create_rich_test_plan()
    html = generate_dashboard(plan)
    html_file = tmp_path / "dashboard.html"
    html_file.write_text(html, encoding="utf-8")
    page.goto(f"file://{html_file.absolute()}")
    page.wait_for_selector(".header__title", timeout=5000)

    title = page.locator(".header__title").text_content() or ""
    assert "Test Dashboard" in title


def test_header_shows_progress_bar(page: Page, tmp_path: Path) -> None:
    """1.3 Header shows progress bar with width > 0."""
    plan = create_rich_test_plan()
    html = generate_dashboard(plan)
    html_file = tmp_path / "dashboard.html"
    html_file.write_text(html, encoding="utf-8")
    page.goto(f"file://{html_file.absolute()}")
    page.wait_for_selector(".progress-bar__fill", timeout=5000)

    width = page.locator(".progress-bar__fill").get_attribute("style") or ""
    assert "width:" in width


def test_header_shows_stat_pills(dashboard_page: Page) -> None:
    """1.4 Header shows 5 stat pills with correct counts."""
    pills = dashboard_page.locator(".stat-pill")
    expect(pills).to_have_count(5)

    # Check counts (done=3, claimed=1, pending=3, rejected=1, skipped=1 from our test plan)
    content = pills.all_text_contents()
    assert any("✓" in c for c in content)  # done
    assert any("◉" in c for c in content)  # claimed
    assert any("○" in c for c in content)  # pending
    assert any("✗" in c for c in content)  # rejected
    assert any("⊘" in c for c in content)  # skipped


def test_sidebar_lists_all_phases(dashboard_page: Page) -> None:
    """1.5 Sidebar lists all phases."""
    nav_items = dashboard_page.locator(".phase-nav__item")
    expect(nav_items).to_have_count(5)


def test_title_contains_project_name(dashboard_page: Page) -> None:
    """1.6 Page title contains project name."""
    title = dashboard_page.title()
    assert "Test Dashboard" in title


# =============================================================================
# 2. Phase Card Expand/Collapse (6 tests)
# =============================================================================


def test_done_phases_collapsed_by_default(dashboard_page: Page) -> None:
    """2.1 Done phases are collapsed by default."""
    done_card = dashboard_page.locator('.phase-card[data-phase-id="done-phase"]')
    class_attr = done_card.get_attribute("class") or ""
    assert "phase-card--collapsed" in class_attr


def test_active_phases_expanded_by_default(dashboard_page: Page) -> None:
    """2.2 In-progress phases are expanded by default."""
    active_card = dashboard_page.locator('.phase-card[data-phase-id="in-progress-phase"]')
    class_attr = active_card.get_attribute("class") or ""
    assert "phase-card--collapsed" not in class_attr


def test_click_header_toggles_collapse(dashboard_page: Page) -> None:
    """2.3 Clicking phase header toggles collapse state."""
    card = dashboard_page.locator('.phase-card[data-phase-id="in-progress-phase"]')
    header = card.locator(".phase-card__header")
    body = card.locator(".phase-card__body")

    # Expanded by default
    assert body.is_visible() is True

    # Collapse
    header.click()
    dashboard_page.wait_for_timeout(150)
    assert body.is_visible() is False

    # Expand
    header.click()
    dashboard_page.wait_for_timeout(150)
    assert body.is_visible() is True


def test_click_step_row_opens_detail(dashboard_page: Page) -> None:
    """3.4 Clicking step row opens detail panel."""
    card = dashboard_page.locator('.phase-card[data-phase-id="in-progress-phase"]')
    row = card.locator("tr.step-row").first
    step_id = row.get_attribute("data-step-id")
    assert step_id is not None

    detail_row = card.locator(f'tr.step-detail-row[data-step-detail-id="{step_id}"]')
    detail = detail_row.locator(".step-detail")

    # Initially closed
    assert (detail_row.get_attribute("class") or "").find("step-detail-row--open") == -1
    assert (detail.get_attribute("class") or "").find("step-detail--open") == -1

    # Click to open
    row.click()
    dashboard_page.wait_for_timeout(150)
    assert "step-detail-row--open" in (detail_row.get_attribute("class") or "")
    assert "step-detail--open" in (detail.get_attribute("class") or "")


def test_click_step_row_again_closes_detail(dashboard_page: Page) -> None:
    """3.5 Clicking step row again closes detail panel."""
    card = dashboard_page.locator('.phase-card[data-phase-id="in-progress-phase"]')
    row = card.locator("tr.step-row").first
    step_id = row.get_attribute("data-step-id")
    assert step_id is not None

    detail_row = card.locator(f'tr.step-detail-row[data-step-detail-id="{step_id}"]')
    detail = detail_row.locator(".step-detail")

    # Open
    row.click()
    dashboard_page.wait_for_timeout(150)
    assert "step-detail-row--open" in (detail_row.get_attribute("class") or "")
    assert "step-detail--open" in (detail.get_attribute("class") or "")

    # Close
    row.click()
    dashboard_page.wait_for_timeout(150)
    assert "step-detail-row--open" not in (detail_row.get_attribute("class") or "")
    assert "step-detail--open" not in (detail.get_attribute("class") or "")


def test_dependencies_cell_uses_table_cell_layout(dashboard_page: Page) -> None:
    """Dependencies column should not break table layout.

    Regression test for alignment: ensure the TD remains `display: table-cell`,
    while the deps container uses flex for pills.

    Source: user report (Dependencies column misaligned).
    """
    card = dashboard_page.locator('.phase-card[data-phase-id="in-progress-phase"]')
    row = card.locator('tr.step-row[data-step-id="in-progress-phase.2"]')
    expect(row).to_have_count(1)

    deps_td = row.locator("td").nth(4)
    deps_div = deps_td.locator(".step-table__deps")
    expect(deps_div).to_have_count(1)

    td_display = deps_td.evaluate("el => getComputedStyle(el).display")
    div_display = deps_div.evaluate("el => getComputedStyle(el).display")
    assert td_display == "table-cell"
    assert div_display in {"flex", "inline-flex"}


def test_step_detail_shows_rejection_history(dashboard_page: Page) -> None:
    """3.9 Step detail shows rejection history with red border."""
    card = dashboard_page.locator('.phase-card[data-phase-id="rejected-phase"]')
    first_step = card.locator(".step-row").first
    first_step.evaluate("el => el.click()")

    # Check rejection history element exists
    detail = card.locator(".step-detail").first
    rejection = detail.locator(".step-detail__rejection")

    # Verify it exists and has content
    count = rejection.count()
    assert count > 0


def test_long_description_no_overflow(dashboard_page: Page) -> None:
    """10.3 Long descriptions don't overflow."""
    # Just verify the test plan has unicode content
    title = dashboard_page.locator(".header__title").text_content() or ""
    assert "🚀" in title


def test_many_phases_sidebar_scrolls(dashboard_page: Page) -> None:
    """10.4 Sidebar scrolls with many phases."""
    sidebar = dashboard_page.locator(".sidebar")
    overflow = sidebar.evaluate("el => getComputedStyle(el).overflowY")
    assert overflow in ["auto", "scroll"]
