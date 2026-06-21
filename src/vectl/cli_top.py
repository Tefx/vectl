"""Live terminal progress panel for ``vectl top``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
import select
import sys
import threading

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - POSIX-only q-key support
    termios = None
    tty = None

from returns.result import Result, Success
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as _esc
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from watchfiles import Change, watch

from vectl.cli_plan_common import _check_claim_mismatch, _load, out
from vectl.duplicate_step_id_format import format_duplicate_step_id_diagnostics
from vectl.models import Phase, PhaseStatus, Plan, StepStatus
from vectl.plan_path import resolve_claims_path
from vectl.semantics import is_step_locked

_TOP_DEBOUNCE_MS = 750
_TOP_POLL_DELAY_MS = 10_000
_TOP_FIXED_ROW_ALLOWANCE = 14
_TOP_MIN_PHASE_ROWS = 2


def _now_label() -> Result[str, str]:
    return Success(datetime.now().strftime("%H:%M:%S"))


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

    visible = {
        index for index, phase in enumerate(plan.phases) if phase.status != PhaseStatus.DONE
    }
    tail_budget = max(0, budget - len(visible) - 1)
    tail_start = max(0, phase_count - tail_budget)
    visible.update(range(tail_start, phase_count))
    return Success(visible)


# @shell_orchestration: compact status/progress cell belongs to the live CLI table.
def _phase_progress_cell(phase: Phase) -> Result[Text, str]:
    total = len(phase.steps)
    done = sum(1 for step in phase.steps if step.status in (StepStatus.DONE, StepStatus.SKIPPED))
    ratio = done / total if total else (1.0 if phase.status == PhaseStatus.DONE else 0.0)
    filled = round(ratio * 10)
    labels = {
        PhaseStatus.DONE: ("✓ done", "green"),
        PhaseStatus.IN_PROGRESS: ("▶ active", "blue"),
        PhaseStatus.PENDING: ("○ pending", "yellow"),
        PhaseStatus.LOCKED: ("■ locked", "red"),
    }
    label, style = labels[phase.status]
    cell = Text(f"{label} ", style=style)
    cell.append("█" * filled, style=style)
    cell.append("░" * (10 - filled), style="dim")
    cell.append(f" {done}/{total}", style="dim")
    return Success(cell)


# @shell_orchestration: compact Rich table belongs to the live top output surface.
# @shell_complexity: folding rows and phase progress must stay aligned in one table renderer.
def _build_top_phase_table(plan: Plan, max_phase_rows: int) -> Result[Table, str]:
    table = Table(expand=True, show_lines=True)
    table.add_column("Phase", style="bold", no_wrap=True, overflow="ellipsis", ratio=3)
    table.add_column("Name", overflow="fold", ratio=5)
    table.add_column("Progress", no_wrap=True, width=26)
    table.add_column("Depends On", no_wrap=True, overflow="ellipsis", ratio=3)

    visible = _visible_phase_indices(plan, max_phase_rows).unwrap()
    folded = 0
    for index, phase in enumerate(plan.phases):
        if index not in visible:
            folded += 1
            continue
        if folded:
            table.add_row(
                Text("…", style="dim"),
                Text(f"{folded} completed phases folded", style="dim"),
                "",
                "",
            )
            folded = 0

        table.add_row(
            _esc(phase.id),
            _esc(phase.name),
            _phase_progress_cell(phase).unwrap(),
            _esc(", ".join(phase.depends_on)) if phase.depends_on else "-",
        )

    if folded:
        table.add_row(
            Text("…", style="dim"),
            Text(f"{folded} completed phases folded", style="dim"),
            "",
            "",
        )

    return Success(table)


# @shell_orchestration: Rich Text diagnostics belong to the CLI output surface.
# @shell_complexity: top diagnostics preserve duplicate-ID and claim mismatch branches.
def _diagnostics(plan: Plan, plan_path: Path) -> Result[list[Text], str]:
    lines: list[Text] = []

    duplicate_lines = format_duplicate_step_id_diagnostics(plan)
    if duplicate_lines:
        lines.append(Text("⚠ Duplicate step-ID diagnostics:", style="yellow"))
        lines.extend(Text(f"  {line}", style="yellow") for line in duplicate_lines)

    try:
        has_mismatch, ghost_claims, stale_plan_claims = _check_claim_mismatch(plan, plan_path)
    except Exception as exc:
        lines.append(Text(f"Claim diagnostics unavailable: {exc}", style="yellow"))
        return Success(lines)

    if has_mismatch:
        lines.append(Text("ℹ Stale claims detected (routine after agent restart):", style="dim"))
        if ghost_claims:
            lines.append(Text(f"  Ghost claims: {len(ghost_claims)}", style="dim"))
        if stale_plan_claims:
            lines.append(Text(f"  Stale plan claims: {len(stale_plan_claims)}", style="dim"))
        lines.append(Text("  → Run `vectl repair claims` to fix (safe, idempotent)", style="dim"))

    return Success(lines)


# @shell_orchestration: Rich Panel composition belongs to the CLI output surface.
def _compose_top_panel(
    plan: Plan,
    diagnostics: list[Text],
    *,
    last_updated: str,
    phase_rows: int,
    message: str | None = None,
) -> Result[Panel, str]:
    header = Text(f"Plan: {plan.project}", style="bold")
    footer = Text("Auto-updates on plan changes · q/Ctrl-C to exit", style="dim")
    footer.append(f"\nLast updated: {last_updated}", style="dim")
    if message:
        footer.append(f"\n{message}", style="yellow")

    parts: list[Any] = [
        header,
        _step_summary(plan).unwrap(),
        _build_top_phase_table(plan, phase_rows).unwrap(),
    ]
    parts.extend(diagnostics)
    parts.append(footer)
    return Success(Panel(Group(*parts), title="vectl top", border_style="blue"))


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
) -> Result[Panel, str]:
    """Build the live plan-level renderable for ``vectl top``."""
    diagnostics = _diagnostics(plan, plan_path).unwrap()
    terminal_height = max(1, out.size.height)
    terminal_width = max(20, out.size.width)
    phase_rows = _top_phase_row_budget(terminal_height, len(diagnostics)).unwrap()
    panel = _compose_top_panel(
        plan,
        diagnostics,
        last_updated=last_updated,
        phase_rows=phase_rows,
        message=message,
    ).unwrap()

    while phase_rows > _TOP_MIN_PHASE_ROWS:
        if _rendered_line_count(panel, terminal_width).unwrap() <= terminal_height:
            break
        phase_rows -= 1
        panel = _compose_top_panel(
            plan,
            diagnostics,
            last_updated=last_updated,
            phase_rows=phase_rows,
            message=message,
        ).unwrap()

    return Success(panel)


def _load_top_state(plan_path: Path | None) -> Result[tuple[Plan, Path], str]:
    plan, _, resolved_plan_path = _load(plan_path)
    return Success((plan, resolved_plan_path))


# @shell_orchestration: live CLI loop coordinates filesystem watch, reads, and rendering.
# @shell_complexity: Live, watchfiles, reload recovery, and quit-key cleanup share lifecycle.
def run_top(plan_path: Path | None = None) -> None:
    """Run the live ``vectl top`` panel until interrupted."""
    plan, resolved_plan_path = _load_top_state(plan_path).unwrap()
    claims_path = resolve_claims_path(resolved_plan_path)
    watched_files = {_canonical(resolved_plan_path).unwrap(), _canonical(claims_path).unwrap()}
    watch_roots = _watch_roots(resolved_plan_path, claims_path).unwrap()
    stop_event = threading.Event()
    quit_thread = _start_quit_key_listener(stop_event).unwrap()

    with Live(
        build_top_renderable(
            plan,
            resolved_plan_path,
            last_updated=_now_label().unwrap(),
        ).unwrap(),
        console=out,
        auto_refresh=False,
        transient=False,
    ) as live:
        try:
            for _changes in watch(
                *watch_roots,
                watch_filter=_make_watch_filter(watched_files).unwrap(),
                recursive=False,
                debounce=_TOP_DEBOUNCE_MS,
                poll_delay_ms=_TOP_POLL_DELAY_MS,
                raise_interrupt=False,
                ignore_permission_denied=True,
                stop_event=stop_event,
                rust_timeout=500,
            ):
                if stop_event.is_set():
                    break
                message = None
                try:
                    plan, resolved_plan_path = _load_top_state(plan_path).unwrap()
                except Exception:
                    message = "Last reload failed; keeping previous view and retrying."
                live.update(
                    build_top_renderable(
                        plan,
                        resolved_plan_path,
                        last_updated=_now_label().unwrap(),
                        message=message,
                    ).unwrap(),
                    refresh=True,
                )
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            stop_event.set()
            if quit_thread is not None:
                quit_thread.join(timeout=0.5)

    out.print("[dim]Stopped.[/]")
