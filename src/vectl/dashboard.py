"""Dashboard data serialization and HTML generation.

This module provides static HTML dashboard generation for vectl plans.
See docs/RFC-dashboard.md for the full specification.

Source: RFC-dashboard.md, lines 196-242
"""

from __future__ import annotations

from datetime import datetime, timezone

from vectl.core import generate_mermaid_dag
from vectl.models import Phase, PhaseStatus, Plan, Step, StepStatus
from vectl.semantics import is_step_locked


def build_dashboard_data(plan: Plan) -> dict:
    """Serialize Plan into a JSON-safe dict for dashboard rendering.

    Pre-computes summary stats, per-phase progress, and Mermaid DAG strings.

    Args:
        plan: The plan to serialize.

    Returns:
        JSON-safe dict with structure per RFC-dashboard.md section "Data Serialization".

    Example:
        >>> from vectl.models import Plan, Phase, Step
        >>> p = Plan(project="test", phases=[
        ...     Phase(id="a", name="Alpha", status=PhaseStatus.DONE, steps=[
        ...         Step(id="a.1", name="S1", status=StepStatus.DONE),
        ...     ]),
        ... ])
        >>> data = build_dashboard_data(p)
        >>> data["project"]
        'test'
        >>> data["summary"]["total_phases"]
        1
        >>> data["phases"][0]["progress"]["pct"]
        100
    """
    # Compute summary stats
    total_phases = len(plan.phases)
    total_steps = sum(len(phase.steps) for phase in plan.phases)

    step_counts = {"done": 0, "claimed": 0, "pending": 0, "rejected": 0, "skipped": 0}
    for phase in plan.phases:
        for step in phase.steps:
            step_counts[step.status.value] += 1

    # Build phases data
    phases_data = []
    for phase in plan.phases:
        phase_data = _build_phase_data(plan, phase)
        phases_data.append(phase_data)

    # Generate Mermaid DAG strings
    phase_dag = generate_mermaid_dag(plan)
    step_dags = {}
    for phase in plan.phases:
        try:
            step_dags[phase.id] = generate_mermaid_dag(plan, phase_id=phase.id)
        except Exception:
            # Skip phases with no steps or other issues
            pass

    return {
        "project": plan.project,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "summary": {
            "total_phases": total_phases,
            "total_steps": total_steps,
            **step_counts,
        },
        "phases": phases_data,
        "mermaid": {
            "phase_dag": phase_dag,
            "step_dags": step_dags,
        },
    }


def _build_phase_data(plan: Plan, phase: Phase) -> dict:
    """Build data dict for a single phase.

    Args:
        plan: The parent plan (for locked state checks).
        phase: The phase to serialize.

    Returns:
        Dict with phase metadata, progress, and steps.
    """
    total = len(phase.steps)
    done = sum(1 for s in phase.steps if s.status == StepStatus.DONE)
    skipped = sum(1 for s in phase.steps if s.status == StepStatus.SKIPPED)
    # Progress considers done + skipped as "complete"
    complete = done + skipped
    pct = round(complete / total * 100) if total > 0 else 0

    steps_data = [_build_step_data(plan, phase, step) for step in phase.steps]

    return {
        "id": phase.id,
        "name": phase.name,
        "status": phase.status.value,
        "gate": phase.gate,
        "context": phase.context,
        "depends_on": phase.depends_on,
        "evidence": phase.evidence,
        "progress": {"done": done, "total": total, "pct": pct},
        "steps": steps_data,
    }


def _build_step_data(plan: Plan, phase: Phase, step: Step) -> dict:
    """Build data dict for a single step.

    Args:
        plan: The parent plan (for locked state checks).
        phase: The parent phase.
        step: The step to serialize.

    Returns:
        Dict with step metadata and detail fields.
    """
    locked = is_step_locked(plan, phase, step)

    # Serialize rejection_history as list of dicts
    rejection_history = [
        {
            "reason": entry.reason,
            "reviewer": entry.reviewer,
            "timestamp": entry.timestamp,
        }
        for entry in step.rejection_history
    ]

    return {
        "id": step.id,
        "name": step.name,
        "status": step.status.value,
        "locked": locked,
        "description": step.description,
        "verification": step.verification,
        "depends_on": step.depends_on,
        "agent": step.agent,
        "claimed_by": step.claimed_by,
        "claimed_at": step.claimed_at,
        "evidence": step.evidence,
        "refs": step.refs,
        "rejection_history": rejection_history,
        "skipped_reason": step.skipped_reason,
    }


# ---------------------------------------------------------------------------
# HTML Template (CSS only, no JavaScript)
# Source: RFC-dashboard.md, lines 249-274
# ---------------------------------------------------------------------------

DASHBOARD_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{project}} — vectl dashboard</title>
  <style>
    /* CSS Custom Properties - Color System per RFC-dashboard.md */
    :root {
      /* Light mode colors (default) */
      --color-done: #16a34a;
      --color-done-bg: #dcfce7;
      --color-claimed: #2563eb;
      --color-claimed-bg: #dbeafe;
      --color-pending: #6b7280;
      --color-pending-bg: #f3f4f6;
      --color-rejected: #dc2626;
      --color-rejected-bg: #fee2e2;
      --color-skipped: #9333ea;
      --color-skipped-bg: #f3e8ff;
      --color-locked: #374151;
      --color-locked-bg: #e5e7eb;

      /* UI colors */
      --bg-primary: #ffffff;
      --bg-secondary: #f9fafb;
      --bg-tertiary: #f3f4f6;
      --text-primary: #111827;
      --text-secondary: #4b5563;
      --text-muted: #9ca3af;
      --border-color: #e5e7eb;
      --shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
      --shadow-lg: 0 4px 6px rgba(0, 0, 0, 0.1);

      /* Spacing */
      --spacing-xs: 0.25rem;
      --spacing-sm: 0.5rem;
      --spacing-md: 1rem;
      --spacing-lg: 1.5rem;
      --spacing-xl: 2rem;

      /* Typography */
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Consolas, monospace;
    }

    /* Dark mode via prefers-color-scheme */
    @media (prefers-color-scheme: dark) {
      :root {
        --color-done: #22c55e;
        --color-done-bg: #14532d;
        --color-claimed: #3b82f6;
        --color-claimed-bg: #1e3a8a;
        --color-pending: #9ca3af;
        --color-pending-bg: #374151;
        --color-rejected: #ef4444;
        --color-rejected-bg: #7f1d1d;
        --color-skipped: #a855f7;
        --color-skipped-bg: #581c87;
        --color-locked: #4b5563;
        --color-locked-bg: #1f2937;

        --bg-primary: #111827;
        --bg-secondary: #1f2937;
        --bg-tertiary: #374151;
        --text-primary: #f9fafb;
        --text-secondary: #d1d5db;
        --text-muted: #6b7280;
        --border-color: #374151;
        --shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        --shadow-lg: 0 4px 6px rgba(0, 0, 0, 0.3);
      }
    }

    /* Reset & Base */
    *, *::before, *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    html {
      scroll-behavior: smooth;
    }

    body {
      font-family: var(--font-sans);
      background: var(--bg-secondary);
      color: var(--text-primary);
      line-height: 1.5;
      min-height: 100vh;
    }

    /* Layout: CSS Grid */
    .app-container {
      display: grid;
      grid-template-columns: 240px 1fr;
      grid-template-rows: auto 1fr;
      grid-template-areas:
        "header header"
        "sidebar main";
      min-height: 100vh;
    }

    /* Header */
    .header {
      grid-area: header;
      background: var(--bg-primary);
      border-bottom: 1px solid var(--border-color);
      padding: var(--spacing-md) var(--spacing-lg);
      position: sticky;
      top: 0;
      z-index: 100;
      display: flex;
      align-items: center;
      gap: var(--spacing-lg);
      flex-wrap: wrap;
    }

    .header__title {
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--text-primary);
    }

    .header__progress {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      flex: 1;
      max-width: 300px;
    }

    .progress-bar {
      flex: 1;
      height: 8px;
      background: var(--bg-tertiary);
      border-radius: 4px;
      overflow: hidden;
    }

    .progress-bar__fill {
      height: 100%;
      background: linear-gradient(90deg, var(--color-done), var(--color-claimed));
      border-radius: 4px;
      transition: width 0.3s ease;
    }

    .progress-bar__text {
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-secondary);
      min-width: 40px;
      text-align: right;
    }

    .header__stats {
      display: flex;
      gap: var(--spacing-sm);
      flex-wrap: wrap;
    }

    .stat-pill {
      display: inline-flex;
      align-items: center;
      gap: var(--spacing-xs);
      padding: var(--spacing-xs) var(--spacing-sm);
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 500;
    }

    .stat-pill--done { background: var(--color-done-bg); color: var(--color-done); }
    .stat-pill--claimed { background: var(--color-claimed-bg); color: var(--color-claimed); }
    .stat-pill--pending { background: var(--color-pending-bg); color: var(--color-pending); }
    .stat-pill--rejected { background: var(--color-rejected-bg); color: var(--color-rejected); }
    .stat-pill--skipped { background: var(--color-skipped-bg); color: var(--color-skipped); }

    .header__tabs {
      display: flex;
      gap: var(--spacing-xs);
    }

    .tab-btn {
      padding: var(--spacing-sm) var(--spacing-md);
      border: none;
      background: transparent;
      color: var(--text-secondary);
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      border-radius: 6px;
      transition: all 0.15s ease;
    }

    .tab-btn:hover {
      background: var(--bg-tertiary);
    }

    .tab-btn--active {
      background: var(--color-claimed-bg);
      color: var(--color-claimed);
    }

    .header__search {
      position: relative;
    }

    .search-input {
      padding: var(--spacing-sm) var(--spacing-md);
      padding-left: 2.25rem;
      border: 1px solid var(--border-color);
      border-radius: 6px;
      background: var(--bg-secondary);
      color: var(--text-primary);
      font-size: 0.875rem;
      width: 200px;
      transition: border-color 0.15s ease;
    }

    .search-input:focus {
      outline: none;
      border-color: var(--color-claimed);
    }

    .search-input::placeholder {
      color: var(--text-muted);
    }

    .search-icon {
      position: absolute;
      left: var(--spacing-sm);
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
      pointer-events: none;
    }

    /* Sidebar */
    .sidebar {
      grid-area: sidebar;
      background: var(--bg-primary);
      border-right: 1px solid var(--border-color);
      padding: var(--spacing-md);
      overflow-y: auto;
      position: sticky;
      top: 60px;
      height: calc(100vh - 60px);
    }

    .sidebar__title {
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: var(--spacing-sm);
    }

    .phase-nav {
      list-style: none;
    }

    .phase-nav__item {
      margin-bottom: var(--spacing-xs);
    }

    .phase-nav__link {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm);
      color: var(--text-secondary);
      text-decoration: none;
      border-radius: 6px;
      font-size: 0.875rem;
      transition: all 0.15s ease;
    }

    .phase-nav__link:hover {
      background: var(--bg-tertiary);
      color: var(--text-primary);
    }

    .phase-nav__link--active {
      background: var(--color-claimed-bg);
      color: var(--color-claimed);
    }

    .phase-nav__link--locked {
      opacity: 0.5;
    }

    .phase-nav__icon {
      flex-shrink: 0;
      width: 1.25em;
      text-align: center;
    }

    .phase-nav__name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .phase-nav__progress {
      font-size: 0.75rem;
      color: var(--text-muted);
    }

    .phase-nav__mini-bar {
      width: 40px;
      height: 4px;
      background: var(--bg-tertiary);
      border-radius: 2px;
      overflow: hidden;
    }

    .phase-nav__mini-fill {
      height: 100%;
      background: var(--color-done);
      border-radius: 2px;
    }

    /* Main Content */
    .main {
      grid-area: main;
      padding: var(--spacing-lg);
      overflow-y: auto;
    }

    .tab-content {
      display: none;
    }

    .tab-content--active {
      display: block;
    }

    /* Phase Cards */
    .phase-card {
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      margin-bottom: var(--spacing-md);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .phase-card__header {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-md);
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease;
    }

    .phase-card__header:hover {
      background: var(--bg-secondary);
    }

    .phase-card__collapse-icon {
      color: var(--text-muted);
      transition: transform 0.2s ease;
    }

    .phase-card--collapsed .phase-card__collapse-icon {
      transform: rotate(-90deg);
    }

    .phase-card__status-icon {
      font-size: 1.25rem;
    }

    .phase-card__name {
      flex: 1;
      font-weight: 600;
      color: var(--text-primary);
    }

    .phase-card__progress-text {
      font-size: 0.875rem;
      color: var(--text-secondary);
    }

    .phase-card__progress-bar {
      width: 100px;
      height: 6px;
      background: var(--bg-tertiary);
      border-radius: 3px;
      overflow: hidden;
    }

    .phase-card__progress-fill {
      height: 100%;
      background: var(--color-done);
      border-radius: 3px;
    }

    .phase-card__body {
      padding: var(--spacing-md);
      border-top: 1px solid var(--border-color);
    }

    .phase-card--collapsed .phase-card__body {
      display: none;
    }

    .phase-card__gate,
    .phase-card__depends {
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-bottom: var(--spacing-sm);
    }

    .phase-card__gate strong,
    .phase-card__depends strong {
      color: var(--text-primary);
    }

    /* Step Table */
    .step-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.875rem;
    }

    .step-table th {
      text-align: left;
      padding: var(--spacing-sm);
      color: var(--text-muted);
      font-weight: 500;
      border-bottom: 1px solid var(--border-color);
    }

    .step-table td {
      padding: var(--spacing-sm);
      border-bottom: 1px solid var(--border-color);
      vertical-align: top;
    }

    .step-table tr:last-child td {
      border-bottom: none;
    }

    .step-table tr:hover {
      background: var(--bg-secondary);
    }

    .step-table__status-icon {
      text-align: center;
    }

    .step-table__id {
      font-family: var(--font-mono);
      font-size: 0.8125rem;
      color: var(--text-secondary);
    }

    .step-table__name {
      font-weight: 500;
      color: var(--text-primary);
    }

    .step-table__agent {
      color: var(--text-secondary);
    }

    .step-table__deps {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-xs);
    }

    .dep-pill {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      padding: 2px 6px;
      background: var(--bg-tertiary);
      border-radius: 4px;
      font-size: 0.75rem;
      font-family: var(--font-mono);
      color: var(--text-secondary);
      text-decoration: none;
    }

    .dep-pill:hover {
      background: var(--bg-secondary);
    }

    .dep-pill--done { color: var(--color-done); }
    .dep-pill--claimed { color: var(--color-claimed); }
    .dep-pill--pending { color: var(--color-pending); }

    /* Step Detail Panel */
    .step-detail {
      background: var(--bg-secondary);
      border-radius: 6px;
      padding: var(--spacing-md);
      margin-top: var(--spacing-sm);
      display: none;
    }

    .step-detail--open {
      display: block;
    }

    .step-detail__row {
      display: flex;
      gap: var(--spacing-md);
      margin-bottom: var(--spacing-sm);
      font-size: 0.875rem;
    }

    .step-detail__label {
      color: var(--text-muted);
      min-width: 100px;
    }

    .step-detail__value {
      color: var(--text-primary);
      flex: 1;
    }

    .step-detail__description {
      background: var(--bg-primary);
      padding: var(--spacing-sm);
      border-radius: 4px;
      white-space: pre-wrap;
      font-size: 0.875rem;
      margin-bottom: var(--spacing-sm);
    }

    .step-detail__checkbox {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      color: var(--text-secondary);
    }

    .step-detail__checkbox input {
      accent-color: var(--color-done);
    }

    .step-detail__checkbox--checked {
      color: var(--color-done);
      text-decoration: line-through;
    }

    .step-detail__rejection {
      background: var(--color-rejected-bg);
      border-left: 3px solid var(--color-rejected);
      padding: var(--spacing-sm);
      border-radius: 0 4px 4px 0;
      font-size: 0.875rem;
    }

    .step-detail__rejection-title {
      font-weight: 600;
      color: var(--color-rejected);
      margin-bottom: var(--spacing-xs);
    }

    /* Step Detail Row */
    .step-detail-row {
      display: none;
    }

    .step-detail-row--open {
      display: table-row;
    }

    /* DAG View */
    .dag-container {
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: var(--spacing-lg);
    }

    .dag-selector {
      margin-bottom: var(--spacing-md);
    }

    .dag-selector__label {
      font-size: 0.875rem;
      font-weight: 500;
      margin-right: var(--spacing-sm);
    }

    .dag-selector__select {
      padding: var(--spacing-sm);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text-primary);
      font-size: 0.875rem;
    }

    .dag-diagram {
      overflow-x: auto;
    }

    /* Status Icons */
    .icon-done { color: var(--color-done); }
    .icon-claimed { color: var(--color-claimed); }
    .icon-pending { color: var(--color-pending); }
    .icon-rejected { color: var(--color-rejected); }
    .icon-skipped { color: var(--color-skipped); }
    .icon-locked { color: var(--color-locked); }

    /* Responsive: sidebar collapses on narrow screens */
    @media (max-width: 768px) {
      .app-container {
        grid-template-columns: 1fr;
        grid-template-areas:
          "header"
          "main";
      }

      .sidebar {
        display: none;
      }

      .header {
        flex-direction: column;
        align-items: stretch;
      }

      .header__progress {
        max-width: 100%;
      }

      .header__stats {
        order: 3;
      }

      .header__tabs {
        order: 4;
      }

      .header__search {
        order: 5;
      }

      .search-input {
        width: 100%;
      }
    }

    /* Utility Classes */
    .hidden { display: none !important; }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
  </style>
</head>
<body>
  <div class="app-container">
    <!-- Header -->
    <header class="header">
      <h1 class="header__title">{{project}}</h1>

      <div class="header__progress">
        <div class="progress-bar">
          <div class="progress-bar__fill" style="width: {{progress_pct}}%"></div>
        </div>
        <span class="progress-bar__text">{{progress_pct}}%</span>
      </div>

      <div class="header__stats">
        <span class="stat-pill stat-pill--done">✓ {{summary.done}}</span>
        <span class="stat-pill stat-pill--claimed">◉ {{summary.claimed}}</span>
        <span class="stat-pill stat-pill--pending">○ {{summary.pending}}</span>
        <span class="stat-pill stat-pill--rejected">✗ {{summary.rejected}}</span>
        <span class="stat-pill stat-pill--skipped">⊘ {{summary.skipped}}</span>
      </div>

      <div class="header__tabs">
        <button class="tab-btn tab-btn--active" data-tab="overview">Overview</button>
        <button class="tab-btn" data-tab="dag">DAG</button>
      </div>

      <div class="header__search">
        <span class="search-icon">🔍</span>
        <input type="text" class="search-input" placeholder="Search steps...">
      </div>
    </header>

    <!-- Sidebar: Phase Navigation -->
    <nav class="sidebar">
      <h2 class="sidebar__title">Phases</h2>
      <ul class="phase-nav">
        {{phase_nav_items}}
      </ul>
    </nav>

    <!-- Main Content -->
    <main class="main">
      <!-- Overview Tab -->
      <div id="tab-overview" class="tab-content tab-content--active">
        {{phase_cards}}
      </div>

      <!-- DAG Tab -->
      <div id="tab-dag" class="tab-content">
        <div class="dag-container">
          <div class="dag-selector">
            <label class="dag-selector__label">View:</label>
            <select class="dag-selector__select" id="dag-select">
              <option value="phase">Phase DAG</option>
              {{step_dag_options}}
            </select>
          </div>
          <div class="dag-diagram" id="dag-diagram">
            <!-- Mermaid diagram renders here -->
          </div>
        </div>
      </div>
    </main>
  </div>

  <!-- Embedded data and rendering scripts -->
  <script>
    const DATA = {{json_data}};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script>
    // Dashboard rendering logic
    // Source: RFC-dashboard.md, Zone 3a (Overview) and Zone 3b (DAG)

    (function() {
      'use strict';

      // --- Status Icon Mapping ---
      const PHASE_ICONS = {
        done: '✓',
        in_progress: '▶',
        pending: '○',
        locked: '🔒'
      };

      const STEP_ICONS = {
        done: '✓',
        claimed: '◉',
        pending: '○',
        skipped: '⊘',
        rejected: '✗'
      };

      // --- Utility Functions ---
      function escapeHtml(str) {
        if (str == null) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
      }

      function formatTimestamp(isoStr) {
        if (!isoStr) return '—';
        try {
          const d = new Date(isoStr);
          return d.toLocaleString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            timeZoneName: 'short'
          });
        } catch {
          return isoStr;
        }
      }

      function getStepIcon(step) {
        if (step.locked) return '🔒';
        return STEP_ICONS[step.status] || '○';
      }

      function getStepIconClass(step) {
        if (step.locked) return 'icon-locked';
        return 'icon-' + step.status;
      }

      function getPhaseIconClass(status) {
        return 'icon-' + (status === 'in_progress' ? 'claimed' : status);
      }

      // --- Tab Switching ---
      function setupTabs() {
        const tabBtns = document.querySelectorAll('.tab-btn');
        const tabContents = document.querySelectorAll('.tab-content');

        tabBtns.forEach(btn => {
          btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;

            tabBtns.forEach(b => b.classList.remove('tab-btn--active'));
            btn.classList.add('tab-btn--active');

            tabContents.forEach(tc => {
              tc.classList.toggle('tab-content--active', tc.id === 'tab-' + tabId);
            });

            // Initialize DAG when switching to DAG tab
            if (tabId === 'dag' && !window._mermaidInitialized) {
              initMermaid();
            }
          });
        });
      }

      // --- Sidebar Phase Navigation ---
      function renderSidebar() {
        const navList = document.querySelector('.phase-nav');
        if (!navList) return;

        let html = '';
        DATA.phases.forEach(phase => {
          const icon = PHASE_ICONS[phase.status] || '○';
          const iconClass = getPhaseIconClass(phase.status);
          const lockedClass = phase.status === 'locked' ? 'phase-nav__link--locked' : '';

          html += `
            <li class="phase-nav__item">
              <a href="#phase-${escapeHtml(phase.id)}" class="phase-nav__link ${lockedClass}" data-phase-id="${escapeHtml(phase.id)}">
                <span class="phase-nav__icon ${iconClass}">${icon}</span>
                <span class="phase-nav__name">${escapeHtml(phase.name)}</span>
                <span class="phase-nav__progress">${phase.progress.done}/${phase.progress.total}</span>
              </a>
            </li>
          `;
        });

        navList.innerHTML = html;

        // Setup click handlers for phase navigation (fixes DAG -> Overview switching)
        navList.querySelectorAll('.phase-nav__link').forEach(link => {
          link.addEventListener('click', (e) => {
            const phaseId = link.dataset.phaseId;
            // Switch to Overview tab and scroll to phase
            switchToOverviewAndScroll(phaseId);
          });
        });

        // Setup scroll spy for active phase highlighting
        setupScrollSpy();
      }

      function setupScrollSpy() {
        const phaseCards = document.querySelectorAll('.phase-card');
        const navLinks = document.querySelectorAll('.phase-nav__link');

        const observer = new IntersectionObserver((entries) => {
          entries.forEach(entry => {
            if (entry.isIntersecting) {
              const phaseId = entry.target.dataset.phaseId;
              navLinks.forEach(link => {
                link.classList.toggle('phase-nav__link--active', link.dataset.phaseId === phaseId);
              });
            }
          });
        }, {
          rootMargin: '-20% 0px -60% 0px'
        });

        phaseCards.forEach(card => observer.observe(card));
      }

      // --- Phase Cards Rendering ---
      function renderPhaseCards() {
        const container = document.getElementById('tab-overview');
        if (!container) return;

        let html = '';
        DATA.phases.forEach(phase => {
          const isCollapsed = phase.status === 'done';
          const collapsedClass = isCollapsed ? 'phase-card--collapsed' : '';
          const icon = PHASE_ICONS[phase.status] || '○';
          const iconClass = getPhaseIconClass(phase.status);

          html += `
            <div class="phase-card ${collapsedClass}" id="phase-${escapeHtml(phase.id)}" data-phase-id="${escapeHtml(phase.id)}">
              <div class="phase-card__header">
                <span class="phase-card__collapse-icon">▼</span>
                <span class="phase-card__status-icon ${iconClass}">${icon}</span>
                <span class="phase-card__name">${escapeHtml(phase.name)}</span>
                <span class="phase-card__progress-text">${phase.progress.done}/${phase.progress.total} (${phase.progress.pct}%)</span>
                <div class="phase-card__progress-bar">
                  <div class="phase-card__progress-fill" style="width: ${phase.progress.pct}%"></div>
                </div>
              </div>
              <div class="phase-card__body">
                ${phase.gate ? `<div class="phase-card__gate"><strong>Gate:</strong> ${escapeHtml(phase.gate)}</div>` : ''}
                ${phase.depends_on.length > 0 ? `<div class="phase-card__depends"><strong>Depends on:</strong> ${renderDepPills(phase.depends_on)}</div>` : ''}
                ${renderStepTable(phase)}
              </div>
            </div>
          `;
        });

        container.innerHTML = html;

        // Attach interactions after dynamic DOM insertion.
        setupPhaseCardInteractions();
        setupStepRowInteractions();
      }

      function togglePhaseCard(header) {
        const card = header.closest('.phase-card');
        if (!card) return;
        card.classList.toggle('phase-card--collapsed');
      }

      function setupPhaseCardInteractions() {
        document.querySelectorAll('.phase-card__header').forEach(header => {
          header.addEventListener('click', () => {
            togglePhaseCard(header);
          });
        });
      }

      function renderDepPills(dependsOn) {
        return dependsOn.map(depId => {
          const depPhase = findPhaseById(depId);
          const statusClass = depPhase ? 'dep-pill--' + depPhase.status : '';
          const icon = depPhase ? (PHASE_ICONS[depPhase.status] || '○') : '?';
          return `<a href="#phase-${escapeHtml(depId)}" class="dep-pill ${statusClass}">${icon} ${escapeHtml(depId)}</a>`;
        }).join(' ');
      }

      function findPhaseById(phaseId) {
        return DATA.phases.find(p => p.id === phaseId);
      }

      function findStepById(stepId) {
        for (const phase of DATA.phases) {
          const step = phase.steps.find(s => s.id === stepId);
          if (step) return { step, phase };
        }
        return null;
      }

      // --- Step Table Rendering ---
      function renderStepTable(phase) {
        if (phase.steps.length === 0) {
          return '<p style="color: var(--text-muted);">No steps defined.</p>';
        }

        let html = `
          <table class="step-table">
            <thead>
              <tr>
                <th class="step-table__status-icon"></th>
                <th>ID</th>
                <th>Name</th>
                <th>Agent</th>
                <th>Dependencies</th>
              </tr>
            </thead>
            <tbody>
        `;

        phase.steps.forEach(step => {
          const icon = getStepIcon(step);
          const iconClass = getStepIconClass(step);
          const agent = step.claimed_by || step.agent || '—';
          const depsHtml = step.depends_on.length > 0 ? renderStepDepPills(step.depends_on, phase) : '—';
          const stepIdEscaped = escapeHtml(step.id);

          html += `
            <tr class="step-row" data-step-id="${stepIdEscaped}">
              <td class="step-table__status-icon ${iconClass}">${icon}</td>
              <td class="step-table__id">${stepIdEscaped}</td>
              <td class="step-table__name">${escapeHtml(step.name)}</td>
              <td class="step-table__agent">${escapeHtml(agent)}</td>
              <td><div class="step-table__deps">${depsHtml}</div></td>
            </tr>
            <tr class="step-detail-row" data-step-detail-id="${stepIdEscaped}">
              <td colspan="5">
                <div class="step-detail" id="detail-${stepIdEscaped}">
                  ${renderStepDetail(step, phase)}
                </div>
              </td>
            </tr>
          `;
        });

        html += '</tbody></table>';
        return html;
      }

      function renderStepDepPills(dependsOn, currentPhase) {
        return dependsOn.map(depId => {
          const result = findStepById(depId);
          let statusClass = '';
          let icon = '?';

          if (result) {
            icon = getStepIcon(result.step);
            statusClass = getStepIconClass(result.step);
          }

          return `<span class="dep-pill ${statusClass}">${icon} ${escapeHtml(depId)}</span>`;
        }).join(' ');
      }

      function _findStepDetailRow(stepId) {
        const rows = document.querySelectorAll('tr.step-detail-row');
        for (const r of rows) {
          if (r.getAttribute('data-step-detail-id') === stepId) return r;
        }
        return null;
      }

      function toggleStepDetail(stepId) {
        const detailRow = _findStepDetailRow(stepId);
        if (!detailRow) return;

        // Toggle the detail row visibility
        detailRow.classList.toggle('step-detail-row--open');

        // Toggle the detail panel inside
        const detail = detailRow.querySelector('.step-detail');
        if (detail) {
          detail.classList.toggle('step-detail--open');
        }
      }

      function setupStepRowInteractions() {
        document.querySelectorAll('.step-row').forEach(row => {
          row.addEventListener('click', () => {
            const stepId = row.getAttribute('data-step-id');
            if (!stepId) return;
            toggleStepDetail(stepId);
          });
        });
      }

      function renderStepDetail(step, phase) {
        let html = '';

        // Status row
        html += `
          <div class="step-detail__row">
            <span class="step-detail__label">Status:</span>
            <span class="step-detail__value">${escapeHtml(step.status)}${step.locked ? ' (locked)' : ''}</span>
          </div>
        `;

        // Claimed info
        if (step.claimed_by) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Claimed by:</span>
              <span class="step-detail__value">${escapeHtml(step.claimed_by)} | ${formatTimestamp(step.claimed_at)}</span>
            </div>
          `;
        }

        // Description with checkbox rendering
        if (step.description) {
          const descWithCheckboxes = renderDescriptionWithCheckboxes(step.description);
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Description:</span>
            </div>
            <div class="step-detail__description">${descWithCheckboxes}</div>
          `;
        }

        // Depends on
        if (step.depends_on.length > 0) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Depends on:</span>
              <span class="step-detail__value">${renderStepDepPills(step.depends_on, phase)}</span>
            </div>
          `;
        }

        // Verification
        if (step.verification) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Verification:</span>
              <span class="step-detail__value"><code>${escapeHtml(step.verification)}</code></span>
            </div>
          `;
        }

        // Refs
        if (step.refs && step.refs.length > 0) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Refs:</span>
              <span class="step-detail__value">${step.refs.map(r => escapeHtml(r)).join(', ')}</span>
            </div>
          `;
        }

        // Evidence
        if (step.evidence) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Evidence:</span>
              <span class="step-detail__value">${escapeHtml(step.evidence)}</span>
            </div>
          `;
        }

        // Rejection history
        if (step.rejection_history && step.rejection_history.length > 0) {
          html += `
            <div class="step-detail__rejection">
              <div class="step-detail__rejection-title">Rejection History (${step.rejection_history.length})</div>
              ${step.rejection_history.map(r => `
                <div>"${escapeHtml(r.reason)}" — ${escapeHtml(r.reviewer || 'unknown')}, ${formatTimestamp(r.timestamp)}</div>
              `).join('')}
            </div>
          `;
        }

        // Skipped reason
        if (step.skipped_reason) {
          html += `
            <div class="step-detail__row">
              <span class="step-detail__label">Skipped reason:</span>
              <span class="step-detail__value">${escapeHtml(step.skipped_reason)}</span>
            </div>
          `;
        }

        return html;
      }

      function renderDescriptionWithCheckboxes(desc) {
        // Convert markdown checkboxes to HTML
        let html = escapeHtml(desc);
        // Match - [x] or - [ ] at start of line
        html = html.replace(/^- \\[x\\] (.*)$/gm, '<div class="step-detail__checkbox step-detail__checkbox--checked"><input type="checkbox" checked disabled> $1</div>');
        html = html.replace(/^- \\[ \\] (.*)$/gm, '<div class="step-detail__checkbox"><input type="checkbox" disabled> $1</div>');
        // Preserve newlines
        html = html.replace(/\\n/g, '<br>');
        return html;
      }

      // --- Search Functionality ---
      function setupSearch() {
        const searchInput = document.querySelector('.search-input');
        if (!searchInput) return;

        searchInput.addEventListener('input', (e) => {
          const query = e.target.value.toLowerCase().trim();
          filterSteps(query);
        });
      }

      function filterSteps(query) {
        const phaseCards = document.querySelectorAll('.phase-card');

        phaseCards.forEach(card => {
          const phaseId = card.dataset.phaseId;
          const phase = findPhaseById(phaseId);
          if (!phase) return;

          let hasVisibleStep = false;
          const rows = card.querySelectorAll('.step-row');

          rows.forEach(row => {
            const stepId = row.dataset.stepId;
            const step = phase.steps.find(s => s.id === stepId);
            if (!step) return;

            const matchesQuery = !query ||
              step.name.toLowerCase().includes(query) ||
              step.description.toLowerCase().includes(query) ||
              (step.agent && step.agent.toLowerCase().includes(query)) ||
              (step.claimed_by && step.claimed_by.toLowerCase().includes(query));

            const detailRow = document.querySelector(`[data-step-detail-id="${stepId}"]`);
            if (matchesQuery) {
              row.style.display = '';
              if (detailRow) detailRow.style.display = '';
              hasVisibleStep = true;
            } else {
              row.style.display = 'none';
              if (detailRow) detailRow.style.display = 'none';
            }
          });

          // Hide phase card if no visible steps
          card.style.display = hasVisibleStep ? '' : 'none';
        });
      }

      // --- DAG Tab ---
      function initMermaid() {
        if (typeof mermaid === 'undefined') {
          console.error('Mermaid.js not loaded');
          return;
        }

        mermaid.initialize({
          startOnLoad: false,
          theme: document.documentElement.matches('.dark, [data-theme="dark"]') ? 'dark' : 'default',
          flowchart: {
            useMaxWidth: true,
            htmlLabels: true
          }
        });

        window._mermaidInitialized = true;
        renderPhaseDAG();
      }

      function renderPhaseDAG() {
        const container = document.getElementById('dag-diagram');
        if (!container) return;

        const mermaidCode = DATA.mermaid.phase_dag;
        renderMermaidDiagram(container, mermaidCode);
      }

      function renderStepDAG(phaseId) {
        const container = document.getElementById('dag-diagram');
        if (!container) return;

        const mermaidCode = DATA.mermaid.step_dags[phaseId];
        if (mermaidCode) {
          renderMermaidDiagram(container, mermaidCode);
        } else {
          container.innerHTML = '<p style="color: var(--text-muted);">No step DAG available for this phase.</p>';
        }
      }

      async function renderMermaidDiagram(container, code) {
        if (!code) {
          container.innerHTML = '<p style="color: var(--text-muted);">No diagram available.</p>';
          return;
        }

        try {
          const { svg } = await mermaid.render('mermaid-graph', code);
          container.innerHTML = svg;

          // Add click handlers to nodes to navigate to phase cards
          setupDAGNodeClickHandlers(container);
        } catch (err) {
          console.error('Mermaid render error:', err);
          container.innerHTML = '<p style="color: var(--color-rejected);">Failed to render diagram.</p>';
        }
      }

      function setupDAGNodeClickHandlers(container) {
        // Find all clickable nodes and add click handlers
        const nodes = container.querySelectorAll('.node');
        nodes.forEach(node => {
          const nodeId = node.id || '';
          // Extract phase/step ID from mermaid node ID (flowchart-{id}-{num})
          const match = nodeId.match(/flowchart-(.+)-\\d+$/);
          if (match) {
            const itemId = match[1].replace(/_/g, '.');
            node.style.cursor = 'pointer';
            node.addEventListener('click', () => {
              // Determine if it's a phase or step
              const isPhase = DATA.phases.some(p => p.id === itemId);
              if (isPhase) {
                switchToOverviewAndScroll(itemId);
              }
            });
          }
        });
      }

      function switchToOverviewAndScroll(phaseId) {
        // Switch to Overview tab
        const overviewBtn = document.querySelector('[data-tab="overview"]');
        if (overviewBtn) {
          overviewBtn.click();
        }

        // Scroll to phase card
        setTimeout(() => {
          const phaseCard = document.getElementById('phase-' + phaseId);
          if (phaseCard) {
            phaseCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
            // Expand if collapsed
            phaseCard.classList.remove('phase-card--collapsed');
          }
        }, 100);
      }

      function setupDAGSelector() {
        const select = document.getElementById('dag-select');
        if (!select) return;

        // Populate options for step DAGs
        const stepDagOptions = Object.keys(DATA.mermaid.step_dags).map(phaseId => {
          const phase = findPhaseById(phaseId);
          const phaseName = phase ? phase.name : phaseId;
          return `<option value="step:${escapeHtml(phaseId)}">${escapeHtml(phaseName)} Steps</option>`;
        }).join('');

        select.innerHTML = '<option value="phase">Phase DAG</option>' + stepDagOptions;

        select.addEventListener('change', (e) => {
          const value = e.target.value;
          if (value === 'phase') {
            renderPhaseDAG();
          } else if (value.startsWith('step:')) {
            const phaseId = value.substring(5);
            renderStepDAG(phaseId);
          }
        });
      }

      // --- Initialize ---
      function init() {
        renderSidebar();
        renderPhaseCards();
        setupTabs();
        setupSearch();
        setupDAGSelector();
      }

      // Run when DOM is ready
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
      } else {
        init();
      }
    })();
  </script>
</body>
</html>"""


def generate_dashboard(plan: Plan) -> str:
    """Generate a complete single-file HTML dashboard from a plan.

    This function combines the data serialization and HTML template
    to produce a standalone HTML file that can be opened in any browser.

    Args:
        plan: The plan to generate a dashboard for.

    Returns:
        Complete HTML string with embedded CSS, JavaScript, and data.

    Example:
        >>> from vectl.models import Plan, Phase, Step
        >>> p = Plan(project="demo", phases=[
        ...     Phase(id="a", name="Alpha", steps=[
        ...         Step(id="a.1", name="S1", status=StepStatus.DONE),
        ...     ]),
        ... ])
        >>> html = generate_dashboard(p)
        >>> "<!DOCTYPE html>" in html
        True
        >>> "demo" in html
        True
    """
    import json

    # Build the data
    data = build_dashboard_data(plan)

    # Serialize to JSON (compact, no ASCII escaping for international text)
    json_str = json.dumps(data, ensure_ascii=False, indent=None)

    # Escape </script> sequences to prevent HTML injection attacks
    # This is a security measure to prevent XSS via data injection
    json_str = json_str.replace("</script>", "<\\/script>")
    json_str = json_str.replace("</SCRIPT>", "<\\/SCRIPT>")

    # Calculate overall progress percentage
    summary = data["summary"]
    total = summary["total_steps"]
    done_plus_skipped = summary["done"] + summary["skipped"]
    progress_pct = round(done_plus_skipped / total * 100) if total > 0 else 0

    # Build template replacements
    replacements = {
        "{{json_data}}": json_str,
        "{{project}}": plan.project,
        "{{progress_pct}}": str(progress_pct),
        "{{summary.done}}": str(summary["done"]),
        "{{summary.claimed}}": str(summary["claimed"]),
        "{{summary.pending}}": str(summary["pending"]),
        "{{summary.rejected}}": str(summary["rejected"]),
        "{{summary.skipped}}": str(summary["skipped"]),
        # These are rendered by JavaScript, but we provide empty defaults
        # for progressive enhancement / no-JS fallback
        "{{phase_nav_items}}": "",
        "{{phase_cards}}": "",
        "{{step_dag_options}}": "",
    }

    # Apply all replacements
    html = DASHBOARD_HTML_TEMPLATE
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    return html
