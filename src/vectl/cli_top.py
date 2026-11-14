"""Live terminal progress panel for ``vectl top``."""

from __future__ import annotations

import os
import select
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - POSIX-only q-key support
    termios = None
    tty = None

from returns.result import Result, Success
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from watchfiles import Change, watch

from vectl.cli_plan_common import (
    _check_claim_mismatch,
    _claim_mismatch_diagnostics,
    _duplicate_id_diagnostics,
    out,
)
from vectl.io import load_plan_definition
from vectl.models import Phase, PhaseStatus, Plan, PlanError, PlanIOError, StepStatus
from vectl.plan_path import PlanAuthority, authority_for_plan_path, resolve_plan_path
from vectl.semantics import is_step_locked

_DEFAULT_OUT = out

_TOP_DEBOUNCE_MS = 750
_TOP_POLL_DELAY_MS = 10_000
_TOP_FIXED_ROW_ALLOWANCE = 14
_TOP_MIN_PHASE_ROWS = 1


class _BorderlessPanel(Panel):
    """Keep the Panel API while yielding only its content at very short heights."""

    # @shell_orchestration: Rich rendering protocol yields segments, not a Result.
    def __rich_console__(self, console, options):
        yield from console.render(self.renderable, options.update(width=self.width))


def _now_label() -> Result[str, str]:
    return Success(datetime.now().strftime("%H:%M:%S"))


def _terminal_size() -> Result[tuple[int, int], str]:
    if out is not _DEFAULT_OUT and (
        getattr(out, "_width", None) is not None or getattr(out, "_height", None) is not None
    ):
        size = out.size
        return Success((size.width, size.height))
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            size = os.get_terminal_size(stream.fileno())
        except (OSError, ValueError, AttributeError):
            continue
        return Success((size.columns, size.lines))
    size = out.size
    return Success((size.width, size.height))


def _canonical(path: Path | str) -> Result[Path, str]:
    return Success(Path(path).resolve(strict=False))


def _watch_roots(plan_path: Path, claims_path: Path) -> Result[tuple[Path, ...], str]:
    roots = {_canonical(plan_path).unwrap().parent}
    claims_parent = _canonical(claims_path).unwrap().parent
    if claims_parent.exists():
        roots.add(claims_parent)
    return Success(tuple(sorted(roots)))


# @shell_orchestration: keyboard event helper belongs to the live CLI loop.
def _handle_quit_key(value: str, stop_event: threading.Event) -> Result[bool, str]:
    if value in {"q", "Q"}:
        stop_event.set()
        return Success(True)
    return Success(False)


# @shell_orchestration: POSIX cbreak stdin listener supports top-style q exit.
# @shell_complexity: terminal mode setup, select loop, and restoration must stay atomic.
def _quit_key_listener(stop_event: threading.Event, stream: Any = None) -> Result[None, str]:
    if stream is None:
        stream = sys.stdin
    if termios is None or tty is None or not stream.isatty():
        return Success(None)

    fd = stream.fileno()
    original_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not stop_event.is_set():
            readable, _, _ = select.select([stream], [], [], 0.2)
            if readable:
                _handle_quit_key(stream.read(1), stop_event).unwrap()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original_attrs)
    return Success(None)


# @shell_orchestration: daemon listener lifecycle is owned by the live CLI loop.
def _start_quit_key_listener(stop_event: threading.Event) -> Result[threading.Thread | None, str]:
    if termios is None or tty is None or not sys.stdin.isatty():
        return Success(None)
    thread = threading.Thread(
        target=lambda: _quit_key_listener(stop_event).unwrap(),
        daemon=True,
    )
    thread.start()
    return Success(thread)


# @shell_orchestration: watchfiles requires a plain predicate callback.
def _make_watch_filter(watched_files: set[Path]) -> Result[Callable[[Change, str], bool], str]:
    watched = {_canonical(path).unwrap() for path in watched_files}

    def _include(_change: Change, changed_path: str) -> bool:
        return _canonical(changed_path).unwrap() in watched

    return Success(_include)


# @shell_orchestration: Rich Text presentation helper belongs to the CLI output surface.
# @shell_complexity: compact status summary must count all visible step states together.
def _step_summary(plan: Plan) -> Result[Text, str]:
    total = 0
    complete = 0
    claimed = 0
    pending = 0
    rejected = 0
    locked = 0

    for phase in plan.phases:
        for step in phase.steps:
            total += 1
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED):
                complete += 1
            elif step.status == StepStatus.CLAIMED:
                claimed += 1
            elif step.status == StepStatus.REJECTED:
                rejected += 1
            elif step.status == StepStatus.PENDING:
                pending += 1
                if is_step_locked(plan, phase, step):
                    locked += 1

    parts = [f"steps {complete}/{total} complete", f"claimed {claimed}", f"pending {pending}"]
    if rejected:
        parts.append(f"rejected {rejected}")
    if locked:
        parts.append(f"locked {locked}")
    return Success(Text(" · ".join(parts), style="dim"))


# @shell_orchestration: terminal-height budgeting belongs to the live CLI output surface.
def _top_phase_row_budget(terminal_height: int, diagnostic_count: int) -> Result[int, str]:
    available = terminal_height - _TOP_FIXED_ROW_ALLOWANCE - diagnostic_count
    return Success(max(_TOP_MIN_PHASE_ROWS, available))


# @shell_orchestration: top view keeps non-done phases and tail progress visible.
def _visible_phase_indices(plan: Plan, max_phase_rows: int) -> Result[set[int], str]:
    phase_count = len(plan.phases)
    budget = max(1, max_phase_rows)
    if phase_count <= budget:
        return Success(set(range(phase_count)))

    active = [i for i, phase in enumerate(plan.phases) if phase.status == PhaseStatus.IN_PROGRESS]
    waiting = [
        i
        for i, phase in enumerate(plan.phases)
        if phase.status in (PhaseStatus.PENDING, PhaseStatus.LOCKED)
    ]
    visible = set((active + waiting)[:budget])
    # Reserve one row for a fold notice when filling with completed history.
    tail_budget = max(0, budget - len(visible) - 1)
    done = [i for i, phase in enumerate(plan.phases) if phase.status == PhaseStatus.DONE]
    if tail_budget:
        visible.update(done[-tail_budget:])
    if not visible and done:
        visible.add(done[-1])
    return Success(visible)


# @shell_orchestration: compact status/progress cell belongs to the live CLI table.
def _phase_progress_cell(phase: Phase, *, terminal_width: int = 80) -> Result[Text, str]:
    total = len(phase.steps)
    done = sum(1 for step in phase.steps if step.status in (StepStatus.DONE, StepStatus.SKIPPED))
    ratio = done / total if total else (1.0 if phase.status == PhaseStatus.DONE else 0.0)
    bar_width = 10 if terminal_width >= 80 else (4 if terminal_width >= 40 else 0)
    filled = round(ratio * bar_width)
    labels = {
        PhaseStatus.DONE: ("✓ done", "green"),
        PhaseStatus.IN_PROGRESS: ("▶ active", "blue"),
        PhaseStatus.PENDING: ("○ pending", "yellow"),
        PhaseStatus.LOCKED: ("■ locked", "red"),
    }
    label, style = labels[phase.status]
    cell = Text(f"{label} " if terminal_width >= 56 else "", style=style)
    cell.append("█" * filled, style=style)
    cell.append("░" * (bar_width - filled), style="dim")
    cell.append(f"{' ' if bar_width else ''}{done}/{total}", style="dim")
    return Success(cell)


# @shell_orchestration: compact Rich table belongs to the live top output surface.
# @shell_complexity: folding rows and phase progress must stay aligned in one table renderer.
def _build_top_phase_table(
    plan: Plan,
    max_phase_rows: int,
    *,
    terminal_width: int = 80,
    show_lines: bool = True,
    show_header: bool = True,
) -> Result[Table, str]:
    narrow = terminal_width < 56
    wide = terminal_width >= 80
    table = Table(
        expand=True,
        box=None if narrow or not show_header else box.SQUARE,
        padding=(0, 0) if narrow else (0, 1),
        show_lines=show_lines and wide,
        show_header=show_header and not narrow,
    )
    table.add_column("Phase", style="bold", no_wrap=True, overflow="ellipsis", ratio=3)
    if wide:
        table.add_column("Name", overflow="fold", ratio=5, min_width=26)
    elif not narrow:
        table.add_column("Name", ratio=5, no_wrap=True, overflow="ellipsis")
    table.add_column("Progress", no_wrap=True, justify="right")
    if wide:
        table.add_column("Depends On", no_wrap=True, overflow="ellipsis", ratio=3)

    visible = _visible_phase_indices(plan, max_phase_rows).unwrap()
    symbols = {
        PhaseStatus.LOCKED: "■",
        PhaseStatus.PENDING: "○",
        PhaseStatus.IN_PROGRESS: "▶",
        PhaseStatus.DONE: "✓",
    }
    index = 0
    while index < len(plan.phases):
        phase = plan.phases[index]
        if index not in visible:
            start = index
            while index < len(plan.phases) and index not in visible:
                index += 1
            completed = all(ph.status == PhaseStatus.DONE for ph in plan.phases[start:index])
            label = f"{index - start} {'completed ' if completed else ''}phases folded"
            cells = (
                [Text(f"… {label}", style="dim"), ""]
                if narrow
                else [
                    Text("…", style="dim"),
                    Text(
                        label,
                        style="dim",
                        no_wrap=not wide or not show_header,
                        overflow="ellipsis",
                    ),
                    "",
                ]
            )
            if wide:
                cells.append("")
            table.add_row(*cells)
            continue
        primary = f"{symbols[phase.status]} {phase.id} {phase.name}" if narrow else phase.id
        cells = [Text(primary, no_wrap=True, overflow="ellipsis")]
        if not narrow:
            cells.append(Text(phase.name, no_wrap=True, overflow="ellipsis"))
        cells.append(
            _phase_progress_cell(
                phase,
                terminal_width=terminal_width,
            ).unwrap()
        )
        if wide:
            cells.append(Text(", ".join(phase.depends_on) or "-"))
        table.add_row(*cells)
        index += 1

    return Success(table)


# @shell_orchestration: Rich Text diagnostics belong to the CLI output surface.
# @shell_complexity: top diagnostics preserve duplicate-ID and claim mismatch branches.
def _diagnostics(
    plan: Plan,
    plan_path: Path,
    *,
    branch: str | None = None,
    claims_path: Path | None = None,
) -> Result[list[Text], str]:
    lines = _duplicate_id_diagnostics(plan).unwrap()

    try:
        has_mismatch, ghost_claims, stale_plan_claims, stale_entries = _check_claim_mismatch(
            plan, plan_path, branch=branch, claims_path=claims_path
        )
    except Exception as exc:
        lines.append(Text(f"Claim diagnostics unavailable: {exc}", style="yellow"))
        return Success(lines)

    if has_mismatch:
        lines.extend(
            _claim_mismatch_diagnostics(ghost_claims, stale_plan_claims, stale_entries).unwrap()
        )

    return Success(lines)


# @shell_orchestration: Rich Panel composition belongs to the CLI output surface.
def _compose_top_panel(
    plan: Plan,
    diagnostics: list[Text],
    *,
    last_updated: str,
    phase_rows: int,
    terminal_width: int = 80,
    terminal_height: int = 24,
    dense: bool = False,
    message: str | None = None,
) -> Result[Panel, str]:
    header = Text(f"Plan: {plan.project}", style="bold", no_wrap=True, overflow="ellipsis")
    footer = Text(
        f"{last_updated} · q/Ctrl-C to exit"
        if dense or terminal_width < 80
        else f"Auto-updates on plan changes · q/Ctrl-C to exit\nLast updated: {last_updated}",
        style="dim",
        no_wrap=True,
        overflow="ellipsis",
    )
    parts: list[Any] = [
        header,
        _step_summary(plan).unwrap(),
        _build_top_phase_table(
            plan,
            phase_rows,
            terminal_width=terminal_width,
            show_lines=not dense,
            show_header=terminal_height >= 15 and not dense,
        ).unwrap(),
    ]
    parts[1].no_wrap = True
    parts[1].overflow = "ellipsis"
    if dense and diagnostics:
        parts.append(
            Text(
                f"! {len(diagnostics)} diagnostic lines · vectl status for details",
                style="yellow",
                no_wrap=True,
                overflow="ellipsis",
            )
        )
    else:
        for line in diagnostics:
            text = line.copy()
            if terminal_width < 80:
                text = Text(
                    text.plain.replace("⚠", "!").replace("ℹ", "i").replace("→", "i"),
                    style=line.style,
                )
            parts.append(text)
    parts.append(footer)
    if message:
        parts.append(Text(message, style="yellow", no_wrap=True, overflow="ellipsis"))
    panel_type = _BorderlessPanel if terminal_height < 10 else Panel
    return Success(
        panel_type(
            Group(*parts),
            title="vectl top" if terminal_height >= 10 else None,
            border_style="blue",
            box=box.SQUARE if terminal_width >= 80 else box.HORIZONTALS,
            padding=(0, 1) if terminal_width >= 80 and terminal_height >= 10 else (0, 0),
            width=terminal_width,
        )
    )


# @shell_orchestration: off-screen Rich render measures whether the live panel fits.
def _rendered_line_count(renderable: Any, width: int) -> Result[int, str]:
    buffer = StringIO()
    console = Console(file=buffer, record=True, width=max(20, width), color_system=None)
    console.print(renderable)
    return Success(len(console.export_text().splitlines()))


# @shell_orchestration: Rich Panel composition belongs to the CLI output surface.
def build_top_renderable(
    plan: Plan,
    plan_path: Path,
    *,
    last_updated: str,
    message: str | None = None,
    branch: str | None = None,
    claims_path: Path | None = None,
) -> Result[Panel, str]:
    """Build the live plan-level renderable for ``vectl top``."""
    diagnostics = _diagnostics(plan, plan_path, branch=branch, claims_path=claims_path).unwrap()
    width, height = _terminal_size().unwrap()
    terminal_height = max(1, height)
    terminal_width = max(20, width)
    phase_rows = min(
        max(1, len(plan.phases)), _top_phase_row_budget(terminal_height, len(diagnostics)).unwrap()
    )
    dense = terminal_height < 15
    while True:
        panel = _compose_top_panel(
            plan,
            diagnostics,
            last_updated=last_updated,
            phase_rows=phase_rows,
            terminal_width=terminal_width,
            terminal_height=terminal_height,
            dense=dense,
            message=message,
        ).unwrap()
        if _rendered_line_count(panel, terminal_width).unwrap() <= terminal_height:
            return Success(panel)
        if not dense and phase_rows > max(2, _TOP_MIN_PHASE_ROWS):
            phase_rows -= 1
        elif not dense:
            dense = True
        elif phase_rows > _TOP_MIN_PHASE_ROWS:
            phase_rows -= 1
        else:
            return Success(panel)


def _load_top_state(plan_path: Path | None) -> Result[tuple[Plan, Path, PlanAuthority], str]:
    resolved_plan_path = resolve_plan_path(plan_path)
    plan, _ = load_plan_definition(resolved_plan_path)
    authority = authority_for_plan_path(resolved_plan_path)
    return Success((plan, resolved_plan_path, authority))


# @shell_orchestration: live CLI loop coordinates filesystem watch, reads, and rendering.
# @shell_complexity: Live, watchfiles, reload recovery, and quit-key cleanup share lifecycle.
def run_top(plan_path: Path | None = None) -> None:
    """Run the live ``vectl top`` panel until interrupted."""
    plan, resolved_plan_path, authority = _load_top_state(plan_path).unwrap()
    if out is _DEFAULT_OUT:
        # Rich caches dimensions at construction and also consults its environment on each render.
        # Use a console-local environment so Live follows resizes without changing os.environ.
        out._width = None
        out._height = None
        out._environ = {
            key: value for key, value in out._environ.items() if key not in {"COLUMNS", "LINES"}
        }
    stop_event = threading.Event()
    quit_thread = _start_quit_key_listener(stop_event).unwrap()
    try:
        terminal_size = _terminal_size().unwrap()
        last_updated = _now_label().unwrap()
        message = None
        out.clear()
        with Live(
            build_top_renderable(
                plan,
                resolved_plan_path,
                last_updated=last_updated,
                message=message,
                branch=authority.branch,
                claims_path=authority.claims_path,
            ).unwrap(),
            console=out,
            auto_refresh=False,
            transient=False,
        ) as live:
            while not stop_event.is_set():
                # Diagnostics may have created the metadata directory during rendering.
                watch_roots = _watch_roots(resolved_plan_path, authority.claims_path).unwrap()
                watched_files = {resolved_plan_path, authority.claims_path}
                changes = watch(
                    *watch_roots,
                    watch_filter=_make_watch_filter(watched_files).unwrap(),
                    recursive=False,
                    debounce=_TOP_DEBOUNCE_MS,
                    poll_delay_ms=_TOP_POLL_DELAY_MS,
                    raise_interrupt=False,
                    ignore_permission_denied=True,
                    stop_event=stop_event,
                    rust_timeout=500,
                    yield_on_timeout=True,
                )
                try:
                    for batch in changes:
                        if stop_event.is_set():
                            break
                        current_size = _terminal_size().unwrap()
                        if batch or message is not None:
                            try:
                                candidate = _load_top_state(plan_path).unwrap()
                                plan, resolved_plan_path, authority = candidate
                                message = None
                                last_updated = _now_label().unwrap()
                            except (PlanIOError, PlanError, OSError):
                                message = "Last reload failed; keeping previous view and retrying."
                        elif current_size == terminal_size:
                            if (
                                watch_roots
                                == _watch_roots(resolved_plan_path, authority.claims_path).unwrap()
                            ):
                                continue
                        terminal_size = current_size
                        out.clear()
                        live.update(
                            build_top_renderable(
                                plan,
                                resolved_plan_path,
                                last_updated=last_updated,
                                message=message,
                                branch=authority.branch,
                                claims_path=authority.claims_path,
                            ).unwrap(),
                            refresh=True,
                        )
                        if (
                            watched_files != {resolved_plan_path, authority.claims_path}
                            or watch_roots
                            != _watch_roots(resolved_plan_path, authority.claims_path).unwrap()
                        ):
                            break
                    else:
                        break
                finally:
                    changes.close()
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        if quit_thread is not None:
            quit_thread.join(timeout=0.5)

    out.print("[dim]Stopped.[/]")
