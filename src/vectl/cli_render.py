"""Read-only CLI rendering commands.

Spec authority: top-level CLI registration delegates ``render``, ``diff``, and
``log`` here without changing their output contracts.
"""

from __future__ import annotations

from pathlib import Path
import typer
from returns.result import Result, Success
from rich.console import Console

from vectl.core import diff_plans, format_lock_changes, recalc_lock_status, render_plan
from vectl.io import load_plan_definition, save_plan
from vectl.models import CASConflictError, Plan, PlanError, PlanIOError
from vectl.plan_path import resolve_plan_path

console = Console(stderr=True)
out = Console()

PlanOption = typer.Option(
    None,
    "--plan",
    "-p",
    help="Path to plan YAML file. Defaults to auto-discovery (walk-up). (env: VECTL_PLAN_PATH)",
)
RenderOutputOption = typer.Option(None, "--output", "-o", help="Write to file instead of stdout.")


def _die(msg: str, code: int = 1, *, cause: Exception | None = None) -> Result[None, str]:
    console.print(f"[red bold]Error:[/] {msg}")
    if cause is None:
        raise typer.Exit(code)
    raise typer.Exit(code) from cause


# @shell_orchestration: Delegates I/O through plan-path and YAML loader helpers.
def _load(plan_path: Path | None) -> Result[tuple[Plan, str, Path], str]:
    """Load plan.yaml and return plan with CAS hash."""

    target = resolve_plan_path(plan_path)
    try:
        plan_def, def_hash = load_plan_definition(target)
    except PlanIOError as exc:
        _die(str(exc))
        raise  # unreachable, for type checker
    return plan_def, def_hash, target


# @shell_orchestration: Delegates persistence through save_plan while preserving CLI API.
def _save_plan(
    plan: Plan, plan_path: Path, expected_def_hash: str, msg: str
) -> Result[None, str]:
    """Save plan.yaml with CAS semantics and lock recalculation."""

    changed_ids = recalc_lock_status(plan)
    commit_message = msg if _should_autosave_commit(plan_path).unwrap() else None
    try:
        save_plan(plan, plan_path, expected_hash=expected_def_hash, commit_message=commit_message)
    except CASConflictError:
        _die(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl status` or `vectl show`, then retry your mutation."
        )

    notice = format_lock_changes(changed_ids, plan)
    if notice:
        print(notice)
    return Success(None)


def _git_toplevel_for(path: Path) -> Result[Path | None, str]:
    """Return git toplevel for a path, or None when unavailable."""

    import subprocess as sp

    try:
        result = sp.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, sp.TimeoutExpired):
        return Success(None)

    resolved = result.stdout.strip()
    if result.returncode != 0 or not resolved:
        return Success(None)
    return Success(Path(resolved).resolve())


def _should_autosave_commit(plan_path: Path) -> Result[bool, str]:
    """Allow autosave commit only when plan is in current repo/worktree."""

    cwd_repo = _git_toplevel_for(Path.cwd()).unwrap()
    plan_repo = _git_toplevel_for(plan_path.parent.resolve()).unwrap()
    return Success(cwd_repo is not None and plan_repo is not None and cwd_repo == plan_repo)


def render(
    phase: str | None = typer.Option(None, "--phase", help="Render only this phase."),
    full: bool = typer.Option(
        False, "--full", help="Show complete step descriptions (no truncation)."
    ),
    output: Path | None = RenderOutputOption,
    plan: Path | None = PlanOption,
) -> Result[None, str]:
    """Render plan as Markdown (read-only export)."""

    plan_obj, _, _ = _load(plan)
    try:
        md = render_plan(plan_obj, phase_id=phase, full=full)
    except PlanError as exc:
        _die(str(exc))
        return  # unreachable

    if output is not None:
        output.write_text(md, encoding="utf-8")
        console.print(f"[green]Written:[/] {output}")
    else:
        out.print(md)
    return Success(None)


# @shell_complexity: Diff rendering is intentionally branchy to preserve existing human output.
def diff_cmd(
    ref: str = typer.Argument("HEAD", help="Git ref to compare against (default: HEAD)."),
    plan: Path | None = PlanOption,
) -> Result[None, str]:
    """Show plan changes since a git ref (default: last commit)."""

    import subprocess as sp

    import yaml

    p_new, _, plan_path = _load(plan)
    try:
        result = sp.run(
            ["git", "show", f"{ref}:./{plan_path.name}"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(plan_path.parent.resolve()),
        )
    except (sp.TimeoutExpired, OSError) as exc:
        _die(f"Git error: {exc}")
        return  # unreachable

    if result.returncode != 0:
        if "does not exist" in result.stderr or "not exist" in result.stderr:
            out.print(f"[dim]No plan.yaml at {ref} — showing current state as all new.[/]")
            p_old = Plan(project=p_new.project)
        else:
            _die(f"git show {ref}:./{plan_path.name} failed: {result.stderr.strip()}")
            return  # unreachable
    else:
        try:
            p_old = Plan(**yaml.safe_load(result.stdout))
        except Exception as exc:
            _die(f"Failed to parse plan at {ref}: {exc}")
            return  # unreachable

    diff = diff_plans(p_old, p_new)
    if not diff.has_changes:
        out.print(f"[dim]No changes since {ref}.[/]")
        return Success(None)

    out.print(f"[bold]Changes since {ref}:[/]\n")
    if diff.phase_changes:
        out.print("[bold]Phases:[/]")
        for pc in diff.phase_changes:
            if pc.kind == "added":
                out.print(f"  [green]+[/] {pc.phase_id} — {pc.phase_name}")
            elif pc.kind == "removed":
                out.print(f"  [red]-[/] {pc.phase_id} — {pc.phase_name}")
            elif pc.kind == "status_changed":
                old_val = pc.old_status.value if pc.old_status else "?"
                new_val = pc.new_status.value if pc.new_status else "?"
                out.print(f"  [yellow]~[/] {pc.phase_id} — {old_val} → {new_val}")
        out.print()

    if diff.step_changes:
        out.print("[bold]Steps:[/]")
        for sc in diff.step_changes:
            if sc.kind == "added":
                out.print(f"  [green]+[/] {sc.step_id} — {sc.step_name} ({sc.phase_id})")
            elif sc.kind == "removed":
                out.print(f"  [red]-[/] {sc.step_id} — {sc.step_name} ({sc.phase_id})")
            elif sc.kind == "status_changed":
                old_val = sc.old_status.value if sc.old_status else "?"
                new_val = sc.new_status.value if sc.new_status else "?"
                out.print(
                    f"  [yellow]~[/] {sc.step_id} — {old_val} → {new_val} ({sc.phase_id})"
                )
            elif sc.kind == "modified":
                out.print(f"  [cyan]≈[/] {sc.step_id} — {sc.detail} ({sc.phase_id})")

    total = len(diff.phase_changes) + len(diff.step_changes)
    out.print(f"\n[dim]{total} change(s) total[/]")
    return Success(None)


# @shell_complexity: Git history rendering preserves existing detailed per-change output.
def log_cmd(
    last: int = typer.Option(
        5, "--last", "-n", help="Number of recent commits to show (default: 5)."
    ),
    plan: Path | None = PlanOption,
) -> Result[None, str]:
    """Show recent plan mutations from git history."""

    import subprocess as sp

    plan_resolved = resolve_plan_path(plan)
    plan_dir = str(plan_resolved.parent)
    try:
        result = sp.run(
            ["git", "log", f"-{last}", "--format=%H|%ai|%s", "--", plan_resolved.name],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=plan_dir,
        )
    except (sp.TimeoutExpired, OSError) as exc:
        _die(f"Git error: {exc}")
        return  # unreachable

    if result.returncode != 0:
        _die(f"git log failed: {result.stderr.strip()}")
        return  # unreachable

    lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    if not lines:
        out.print("[dim]No git history for plan.yaml.[/]")
        return Success(None)

    out.print(f"[bold]Plan history (last {len(lines)}):[/]\n")
    for index, line in enumerate(lines):
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        commit_hash, date, message = parts[0], parts[1], parts[2]
        out.print(f"  [bold cyan]{commit_hash[:8]}[/] {date[:10]}  {message}")
        _print_commit_plan_diff(commit_hash, plan_resolved, plan_dir)
        if index < len(lines) - 1:
            out.print()
    return Success(None)


# @shell_complexity: Branches mirror diff categories emitted by core diff model.
def _print_commit_plan_diff(
    commit_hash: str, plan_resolved: Path, plan_dir: str
) -> Result[None, str]:
    old_plan = _git_plan_at_ref(f"{commit_hash}~1", plan_resolved, plan_dir).unwrap()
    new_plan = _git_plan_at_ref(commit_hash, plan_resolved, plan_dir).unwrap()
    if old_plan is not None and new_plan is not None:
        diff = diff_plans(old_plan, new_plan)
        if not diff.has_changes:
            out.print("    [dim](no structural changes)[/]")
            return Success(None)
        for pc in diff.phase_changes:
            if pc.kind == "added":
                out.print(f"    [green]+phase[/] {pc.phase_id} ({pc.phase_name})")
            elif pc.kind == "removed":
                out.print(f"    [red]-phase[/] {pc.phase_id}")
            elif pc.kind == "status_changed":
                old_v = pc.old_status.value if pc.old_status else "?"
                new_v = pc.new_status.value if pc.new_status else "?"
                out.print(f"    [yellow]~phase[/] {pc.phase_id}: {old_v} → {new_v}")
        for sc in diff.step_changes:
            if sc.kind == "added":
                out.print(f"    [green]+step[/]  {sc.step_id} ({sc.step_name})")
            elif sc.kind == "removed":
                out.print(f"    [red]-step[/]  {sc.step_id}")
            elif sc.kind == "status_changed":
                old_v = sc.old_status.value if sc.old_status else "?"
                new_v = sc.new_status.value if sc.new_status else "?"
                out.print(f"    [yellow]~step[/]  {sc.step_id}: {old_v} → {new_v}")
            elif sc.kind == "modified":
                out.print(f"    [cyan]≈step[/]  {sc.step_id}: {sc.detail}")
    elif new_plan is not None:
        out.print("    [green](initial commit)[/]")
    else:
        out.print("    [dim](unable to parse)[/]")
    return Success(None)


# @shell_orchestration: Subprocess and parser failures are normalized to None for log display.
def _git_plan_at_ref(ref: str, plan_path: Path, cwd: str) -> Result[Plan | None, str]:
    """Try to load plan.yaml at a given git ref. Returns None on failure."""

    import subprocess as sp

    import yaml

    try:
        result = sp.run(
            ["git", "show", f"{ref}:./{plan_path.name}"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except (sp.TimeoutExpired, OSError):
        return Success(None)

    if result.returncode != 0:
        return Success(None)

    try:
        return Success(Plan(**yaml.safe_load(result.stdout)))
    except Exception:
        return Success(None)
