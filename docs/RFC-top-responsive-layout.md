# RFC: Responsive Small-Screen Layout & Live-Reload Hardening for `vectl top`

- **Status**: Accepted
- **Target Repository**: `/Users/tefx/Projects/vectl`
- **Pinned Baseline Commit**: `eddb79f52d5aed00de92c9e6bb5b79b14bf66b1d`
- **Primary Modules**: `src/vectl/cli_top.py`, `src/vectl/cli_plan_read_commands.py`, `src/vectl/cli_plan_common.py`, `tests/test_cli.py`

---

## 1. Binding Contract (`MUST`)

### 1.1 Scope & Ownership Guardrail
- **Layer Ownership**: All changes belong to the CLI Shell layer (`src/vectl/cli_top.py`, `src/vectl/cli_plan_read_commands.py`, `src/vectl/cli_plan_common.py`) and CLI test suite (`tests/test_cli.py`). Core domain models and state machines (`src/vectl/models.py`, `src/vectl/semantics.py`, `src/vectl/core.py`) and MCP server tools (`src/vectl/mcp_server.py`) remain unchanged.
- **Hard Non-Goals**:
  1. **Read-Only Payload Invariant**: `vectl top` MUST remain strictly read-only with respect to `plan.yaml` and `claims.json` payloads (Basis: `README.md` §5 Monitor Progress, `tests/test_cli.py::TestTop::test_top_is_read_only_for_plan_yaml`).
  2. **Zero CLI Surface Expansion**: Preserve `vectl top [--plan PATH]` without adding manual layout mode flags or subcommands (Basis: `src/vectl/cli_plan_read_commands.py:top`, `tests/test_cli.py::TestTop::test_help_is_simple`).
  3. **Phase-Granularity Boundary**: `vectl top` renders plan-level summary and phase-level progress rows only; do not embed step-level sub-rows inside the `vectl top` phase table.

### 1.2 Public & Cross-Module Surfaces
1. **`build_top_renderable` Contract** (Basis: `src/vectl/cli_top.py:build_top_renderable`, `tests/test_cli.py:TestTop`):
   - `build_top_renderable(plan, plan_path, *, last_updated, message=None, branch=None, claims_path=None)` MUST return `Result[Panel, str]` with `panel.width == max(20, terminal_width)`.
   - Across all supported viewports, the rendered text MUST contain `"Plan: <plan.project>"` exactly once, contain `"q/Ctrl-C to exit"`, and never contain the duplicate title `"vectl top · Plan"`.
2. **Module Entrypoint Backward Compatibility** (Basis: `tests/test_cli.py`, `tests/test_linked_worktree_authority.py`):
   - Calling `_build_top_phase_table(plan, max_phase_rows)` with positional arguments only MUST default to the desktop wide tier (`terminal_width >= 80`), returning a 4-column `Table` (`Phase`, `Name`, `Progress`, `Depends On`) with `show_lines=True`.
   - `_diagnostics(plan, plan_path, *, branch=None, claims_path=None)` MUST preserve its signature and full `Result[list[Text], str]` output; single-line diagnostic condensation for short viewports MUST occur only during panel composition.

### 1.3 State Authority, Concurrency & Failure Precedence
1. **Atomic Reload & Error Containment** (Basis: `src/vectl/cli_top.py:run_top`):
   - Live reloads in `run_top` MUST resolve `(plan, resolved_plan_path, authority)` atomically before updating live view state. If any stage raises an error (`PlanIOError`, `PlanError`, or `OSError`), `run_top` MUST retain the complete prior `(plan, resolved_plan_path, authority)` tuple without partial overwrites.
   - Live reloads MUST NOT invoke CLI exit helpers (`_die` / `typer.Exit`, which inherits from `BaseException`) or write error output to `stderr`. Reload failures are surfaced solely via the in-panel `message` notice (`"Last reload failed; keeping previous view and retrying."`).
   - While a reload failure is active (`message is not None`), subsequent watch timeout ticks MUST retry loading even when `current_size == terminal_size`. When healthy (`message is None`), resize-only timeout ticks MUST redraw from in-memory state without reloading from disk.
2. **Late Claims Directory Discovery** (Basis: `src/vectl/cli_top.py:_watch_roots`):
   - Even when the claims metadata directory (`.vectl/` or `.git/vectl/`) does not exist prior to startup, `run_top` MUST include `authority.claims_path`'s directory in active watch coverage once initial diagnostics or a subsequent operation creates it.
3. **Terminal `cbreak` & Listener Teardown Boundary** (Basis: `src/vectl/cli_top.py:_start_quit_key_listener`):
   - The `try/finally` cleanup boundary that sets `stop_event` and joins the quit-key listener thread MUST enclose the entire post-start lifetime of `_start_quit_key_listener`, including initial render construction and `Live.__enter__`.
4. **Peer Read-Only Claim-Mismatch Symmetry** (Basis: `src/vectl/cli_plan_read_commands.py:_show_step_detail`):
   - `_show_step_detail` MUST evaluate `ghost_claims` even when `step.status != StepStatus.CLAIMED`, and MUST form `step_key = f"{resolved_branch}:{step.id}"` using canonical `step.id` rather than the raw CLI selector string.

### 1.4 Required Postconditions & Discriminating Acceptance
1. **Responsive Width Tiers (`terminal_width`)**:
   - **Wide (`terminal_width >= 80`)**: Full-border `Panel` with 4-column `Table` (`Phase`, `Name`, `Progress`, `Depends On`).
   - **Compact (`56 <= terminal_width < 80`, e.g., iOS landscape)**: Omits left/right outer `Panel` vertical borders and omits the `Depends On` column, rendering a 3-column `Table` (`Phase`, `Name`, `Progress`, `show_lines=False`) with non-zero readable width for both `Phase` and `Name`.
   - **Narrow (`36 <= terminal_width < 56`, e.g., iOS portrait)**: Omits left/right outer `Panel` vertical borders, table borders, and column headers; renders each visible phase on a single line with `<status_symbol> <phase.id> <phase.name>` in the primary column and compact progress (`4`-cell bar + `done/total` at `40 <= terminal_width < 56`; `done/total` at `terminal_width < 40`) in the right-aligned secondary column. Folded-phase notices occupy the primary column span so they are never hidden in a collapsed secondary column.
   - **Single-Width Glyph Safety**: On `terminal_width < 80`, all status and diagnostic symbols MUST have deterministic terminal cell width `1` (`✓`, `▶`, `○`, `■`, `█`, `░`, `…`, `!`, `i`), avoiding emoji or ambiguous-width glyphs.
2. **Bounded Phase Selection & Height Fitting (`terminal_height`)**:
   - When non-`DONE` phases exceed `max_phase_rows`, `_visible_phase_indices` MUST bound selected phases to `max(1, max_phase_rows)` in priority order: `PhaseStatus.IN_PROGRESS` → earliest `PhaseStatus.PENDING` / `PhaseStatus.LOCKED` → tail `PhaseStatus.DONE`. When non-`DONE` phases are fewer than `max_phase_rows`, tail `DONE` filling preserves `{p1, p6, p7}` selection at `max_phase_rows=4` when `p1` is active among 8 phases.
   - Any contiguous folded range composed exclusively of `PhaseStatus.DONE` phases MUST emit text containing `"{folded} completed phases folded"`; ranges containing non-`DONE` phases emit `"{folded} phases folded"`.
   - Across all supported viewports (`terminal_width >= 36, terminal_height >= 8`), `build_top_renderable` MUST progressively shed vertical chrome (`show_lines=False`, single-line diagnostics summary, single-line header/footer, `show_header=False` at `terminal_height < 15`, and borderless panel fallback at `terminal_height < 10`) and decrement `phase_rows` down to a floor of `1` so that `_rendered_line_count(panel, terminal_width) <= terminal_height` with at least `1` visible phase row.

---

## 2. Implementation Notes & Hazard Map (`ADVISORY`)

- **`src/vectl/cli_top.py:_compose_top_panel`**: `Panel(..., box=box.HORIZONTALS, padding=(0, 0), width=terminal_width)` preserves the `Panel` return type and `panel.width` invariant while removing the 4-column left/right border overhead on `< 80` column viewports.
- **`src/vectl/cli_top.py:_load_top_state`**: Calling `resolve_plan_path` and `load_plan_definition` directly instead of `cli_plan_common._load` avoids `_die()` writing to `stderr` and raising `typer.Exit` (`BaseException`) during live reload.
- **`src/vectl/cli_top.py:_quit_key_listener`**: Keep quit-key detection restricted to `{"q", "Q"}` under `stream.read(1)`; do not treat bare `\x1b` as quit because multi-byte escape sequences (arrow keys, focus events) start with `\x1b`.

---

## 3. Admitted Finding Ledger (Pinned Commit `eddb79f52d5aed00de92c9e6bb5b79b14bf66b1d`)

Downstream planning and execution MUST consume this converged Stage 1 Finding Ledger directly without repeating redundant Stage 1 discovery:

| ID | Tier & Action | Locator (`path:symbol`) | Violated Obligation & Counterexample | Affected Family | Required Closure Condition |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **F-1** | Tier 1 `[WIRE \| FIX]` | `src/vectl/cli_top.py`: `_build_top_phase_table`, `_compose_top_panel`, `build_top_renderable` | Fixed 43-col overhead (`Panel` 4 + 4-col `Table` 13 + `Progress` 26) collapses `Name` and `Depends On` to 0 width at `42×24` / `40×16`, hiding fold notices and phase names; `_TOP_MIN_PHASE_ROWS = 2` and multi-line chrome overflow `H <= 16`. | `cli_top` responsive width tiers and height-fit pipeline | Satisfy §1.4 width/height postconditions across `(42, 24)`, `(40, 12)`, `(76, 14)`, `(80, 24)`, and `(120, 20)`. |
| **F-2** | Tier 1 `[FIX]` | `src/vectl/cli_top.py`: `_visible_phase_indices`, `_build_top_phase_table` | Unbounded inclusion of all non-`DONE` phases overflows `max_phase_rows` when `len(non_done) > max_phase_rows`. | `cli_top` phase selection and fold label emission | Bound selected phases to `max(1, max_phase_rows)` by priority (`IN_PROGRESS → PENDING/LOCKED → tail DONE`) while preserving `{p1, p6, p7}` at `max_phase_rows=4`. |
| **F-3** | Tier 1 `[FIX]` | `src/vectl/cli_top.py`: `_watch_roots`, `_load_top_state`, `run_top` | (a) `_load` calls `_die()` (`stderr` + `typer.Exit` `BaseException`) on `PlanIOError`; (b) `plan` is overwritten before `authority` succeeds; (c) failed reloads never retry on timeout ticks; (d) `_watch_roots` runs before initial diagnostics creates `.vectl/`, omitting `claims_parent`. | `cli_top` live watch and reload state machine | Load `(plan, resolved_plan_path, authority)` atomically without `_die()`, retry on timeout when `message is not None`, and include `claims_parent` after initial diagnostics / directory creation. |
| **F-4** | Tier 1 `[FIX]` | `src/vectl/cli_top.py`: `_start_quit_key_listener`, `run_top` | `_start_quit_key_listener` starts `cbreak` thread before the `try/finally` inside `with Live(...)`, leaking thread/terminal state if initial render raises. | `cli_top` terminal lifecycle cleanup | Enclose initial render and `Live` context entry inside the `try/finally` that sets `stop_event` and joins `quit_thread`. |
| **F-5** | Tier 1 `[FIX]` | `src/vectl/cli_plan_read_commands.py`: `_show_step_detail` | `step.status == StepStatus.CLAIMED` guard makes `ghost_claims` check unreachable for `PENDING`/`DONE` steps, and `step_key` uses raw `step_id` selector instead of canonical `step.id`. | Peer read-only CLI claim diagnostics (`status`, `show`, `top`) | Check `ghost_claims` regardless of `step.status` when `plan_path is not None`, and use `step.id` in `step_key`. |
| **F-6** | Tier 1 `[REWRITE_TEST]` | `tests/test_cli.py`: `TestTop` | `TestTop` only tests widths `80`/`120` with `<=1` active phase and misses narrow/compact/short viewports, `len(non_done) > max_phase_rows`, `PlanIOError` reload recovery + timeout retry, and late `claims_parent` watch coverage. | `TestTop` Gate 1 stimulus & oracle calibration | Add calibrated tests for narrow/compact/short viewports, non-DONE priority bounding, atomic reload failure + timeout retry, listener cleanup, and `_show_step_detail` ghost claims. |
| **F-7** | Tier 2 `[CONSOLIDATE]` | `src/vectl/cli_top.py`: `_diagnostics`; `src/vectl/cli_plan_common.py`: `_build_phase_overview_table`, `_check_claim_mismatch`; `src/vectl/cli_plan_read_commands.py`: `status` | Duplicate step-ID and 5-line stale-claim diagnostic formatting across `status` and `cli_top._diagnostics`; redundant inner import in `_check_claim_mismatch`; stale docstring on `_build_phase_overview_table`. | Shared CLI diagnostic formatting helpers | Consolidate shared diagnostic formatting helper, remove redundant inner import, and update `_build_phase_overview_table` docstring. |
| **F-8** | Tier 2 `[DELETE \| REWRITE_TEST]` | `src/vectl/cli_plan_common.py`: `_git_toplevel_for`; `tests/test_cli.py`: `TestTop` | Dead `_git_toplevel_for` helper and trailing orphaned comment in `cli_plan_common.py`; `TestTop` resize test couples to private `_load_top_state` / `watchfiles` constants. | CLI module hygiene & Gate 2 test survivability | Delete dead `_git_toplevel_for` and orphaned comment; keep `TestTop` resize-without-reload assertions anchored to real I/O or compatible loader seam. |
