# RFC: Static HTML Dashboard (`vectl dashboard`)

**Status:** Draft
**Date:** 2026-02-17
**Author:** tefx + Claude Opus 4.6

## Problem

Human stakeholders need to review project progress visually. The current
options are CLI (`vectl status`, `vectl render`) and MCP tools — both
require terminal access and familiarity with vectl commands. A browser-
based view would be more accessible and allow richer interaction
(expand/collapse, search, DAG visualization).

## Decision Record

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Theme follows `prefers-color-scheme` | Respect OS preference |
| 2 | Mermaid.js via CDN | Keep HTML file small; offline not required |
| 3 | DAG view included | Dependency visualization is core to vectl value |
| 4 | Command name: `vectl dashboard` | Distinct from existing `render` (Markdown) |
| 5 | Step dependencies shown in list view too | DAG is core, redundancy aids comprehension |

## Design

### Architecture

```
plan.yaml ──→ Python (core.py) ──→ JSON ──→ HTML template ──→ dashboard.html
               read + serialize     embed     single-file output
```

**Generation pipeline:**
1. `load_plan()` reads plan.yaml into Pydantic models
2. New `build_dashboard_data(plan) -> dict` serializes to JSON-safe dict
3. New `generate_dashboard(plan) -> str` renders HTML with embedded JSON
4. CLI command writes to file, optionally opens browser

**No server. No database. Pure static file.**

### CLI Interface

```
vectl dashboard                      # → plan-dashboard.html
vectl dashboard --out report.html    # custom output path
vectl dashboard --open               # generate + open in browser
```

### Page Layout

Single-page, three-zone layout:

```
┌─────────────────────────────────────────────────────────┐
│  HEADER                                                 │
│  Project name  ██████████████░░░░ 78%   Stats pills     │
│  [Overview]  [DAG]                        🔍 search     │
├──────────┬──────────────────────────────────────────────┤
│  SIDEBAR │  MAIN CONTENT                                │
│          │                                              │
│  Phase   │  Phase cards (expand/collapse)               │
│  nav     │  — or —                                      │
│  list    │  DAG view (Mermaid)                          │
│          │                                              │
└──────────┴──────────────────────────────────────────────┘
```

Responsive: sidebar collapses to top nav on narrow screens.

### Zone 1: Header Bar

| Element | Detail |
|---------|--------|
| Project name | `plan.project` |
| Global progress bar | `done+skipped / total` with color gradient |
| Stat pills | Colored badges: done (green), claimed (blue), pending (gray), rejected (red), skipped (purple) |
| Tab switcher | "Overview" (default), "DAG" |
| Search box | Client-side filter on step name, description, agent |

### Zone 2: Sidebar — Phase Navigation

Sticky vertical list of all phases:

```
✓ core        5/5
✓ cli_read    4/4
▶ mcp         3/5   ← highlighted (in_progress)
○ backlog     0/3
🔒 future     0/2   ← dimmed (locked)
```

- Click scrolls main content to that phase card (anchor link)
- Active phase highlighted based on scroll position
- Status icon + color coding (see Color System below)
- Mini progress: `done/total`

### Zone 3a: Overview Tab — Phase Cards

Each phase is a collapsible card:

```
┌─────────────────────────────────────────────────────────┐
│ [▼] ▶ mcp — MCP Server Integration           3/5 (60%) │
│     ████████████████████░░░░░░░░░░                      │
│                                                         │
│  Gate: All MCP tools pass integration tests             │
│  Depends on: core → cli_write                           │
│                                                         │
│  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐ │
│  │ ✓  mcp.1  Server Scaffold     alice   deps: —     │ │
│  │ ✓  mcp.2  Status Tool         alice   deps: mcp.1 │ │
│  │ ✓  mcp.3  Claim Tool          bob     deps: mcp.1 │ │
│  │ ◉  mcp.4  Complete Tool       bob     deps: mcp.3 │ │
│  │ ○  mcp.5  Review Tool         —       deps: mcp.4 │ │
│  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘ │
└─────────────────────────────────────────────────────────┘
```

**Collapse rules:**
- `done` phases: collapsed by default
- `in_progress` / `pending` / `locked`: expanded by default
- Click header to toggle

**Step row columns:**
- Status icon
- Step ID
- Step name
- Agent (claimed_by or suggested agent)
- Dependencies (`depends_on` list, rendered as linked pills)

**Step detail panel** (click row to expand):

```
┌──────────────────────────────────────────────────┐
│  Status: claimed                                 │
│  Claimed by: bob  |  At: 2026-02-15 10:30 UTC   │
│                                                  │
│  Description:                                    │
│  Implement vectl_complete MCP tool               │
│  ☑ Parse evidence parameter                      │
│  ☐ Validate step is claimed                      │
│  ☐ Return updated status                         │
│                                                  │
│  Depends on: mcp.3 (Claim Tool) ✓               │
│  Verification: pytest tests/test_mcp.py -v       │
│  Refs: docs/spec.md#complete                     │
│                                                  │
│  Evidence: (none yet)                            │
│                                                  │
│  Rejection History: (1 rejection)                │
│  └ "Missing error handling" — alice, Feb 14      │
└──────────────────────────────────────────────────┘
```

- `- [x]` / `- [ ]` in description rendered as visual checkboxes (read-only)
- `depends_on` shows step name + status icon (cross-reference)
- Rejection history shown with warning style if present
- Evidence shown if step is done

### Zone 3b: DAG Tab — Dependency Graph

Uses Mermaid.js (CDN: `https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js`).

Two sub-views via dropdown:

1. **Phase DAG** (default): reuses `generate_mermaid_dag(plan)` output
2. **Step DAG per phase**: dropdown to select phase, reuses
   `generate_mermaid_dag(plan, phase_id=...)` output

Node styling via Mermaid `classDef`:
- done = green fill
- claimed/in_progress = blue fill
- pending = gray fill
- rejected = red fill
- locked = dark gray fill, dashed border

Click on a node scrolls to the corresponding phase card in Overview tab.

### Color System

| Status | Light mode | Dark mode | Icon |
|--------|-----------|-----------|------|
| done | `#16a34a` | `#22c55e` | ✓ |
| claimed / in_progress | `#2563eb` | `#3b82f6` | ◉ / ▶ |
| pending | `#6b7280` | `#9ca3af` | ○ |
| rejected | `#dc2626` | `#ef4444` | ✗ |
| skipped | `#9333ea` | `#a855f7` | ⊘ |
| locked | `#374151` | `#4b5563` | 🔒 |

Theme: follows `prefers-color-scheme: dark` media query.
CSS custom properties for all colors, switched via media query.

### Data Serialization

New function `build_dashboard_data(plan: Plan) -> dict`:

```python
{
  "project": "vectl",
  "generated_at": "2026-02-17T12:00:00Z",
  "summary": {
    "total_phases": 24,
    "total_steps": 117,
    "done": 109, "claimed": 3, "pending": 4,
    "rejected": 0, "skipped": 1
  },
  "phases": [
    {
      "id": "core", "name": "Core Logic",
      "status": "done",
      "gate": "All core unit tests pass",
      "context": "Foundation layer...",
      "depends_on": [],
      "progress": {"done": 5, "total": 5, "pct": 100},
      "steps": [
        {
          "id": "core.1", "name": "Pydantic Data Models",
          "status": "done",
          "description": "Define all data models...",
          "depends_on": [],
          "agent": null,
          "claimed_by": "engineer-1",
          "claimed_at": "2026-02-08T...",
          "evidence": "commit ab1a38f...",
          "verification": "pytest ...",
          "refs": ["docs/spec.md"],
          "rejection_history": [],
          "skipped_reason": null
        }
      ]
    }
  ],
  "mermaid": {
    "phase_dag": "graph LR\n  core[...] --> ...",
    "step_dags": {
      "core": "graph LR\n  ...",
      "mcp": "graph LR\n  ..."
    }
  }
}
```

Key points:
- Pre-compute mermaid strings server-side (reuse `generate_mermaid_dag`)
- Pre-compute progress summaries (avoid doing math in JS)
- Include `generated_at` timestamp for staleness awareness

### HTML Template

Single-file HTML with three embedded blocks:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{project}} — vectl dashboard</title>
  <style>/* all CSS inline */</style>
</head>
<body>
  <div id="app"></div>
  <script>
    const DATA = {{json_data}};
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  <script>/* all JS inline — render from DATA */</script>
</body>
</html>
```

Template lives in `src/vectl/dashboard_template.html` (or as a Python
string constant in a new `src/vectl/dashboard.py` module).

### Implementation Scope

**New files:**
- `src/vectl/dashboard.py` — `build_dashboard_data()`, `generate_dashboard()`, HTML template

**Modified files:**
- `src/vectl/cli.py` — add `dashboard` command
- `tests/test_dashboard.py` — unit tests for data serialization + HTML generation

**Not in scope (future):**
- `vectl serve` (live-reload dev server)
- GitHub Pages CI integration
- Export to PDF

## Reuse from Existing Code

| Existing | Reused for |
|----------|------------|
| `render_plan()` | Reference for phase/step rendering logic |
| `generate_mermaid_dag()` | Mermaid strings embedded in JSON data |
| `_PHASE_ICON` / `_STEP_ICON` | Status icon mapping |
| `review_plan()` → `PhaseProgress` | Progress computation pattern |
| `is_step_locked()` from semantics | Lock state for step display |
