"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *

# @shell_complexity: search preserves grouped phase output and empty-result CLI messaging.
def search(
    pattern: str = typer.Argument(help="Pattern to search for (case-insensitive substring)."),
    phase: str | None = typer.Option(None, "--phase", help="Restrict to a specific phase."),
    state: str | None = typer.Option(
        None, "--state", help="Filter by status (e.g. pending, done, claimed)."
    ),
    regex: bool = typer.Option(False, "--regex", help="Treat pattern as regex."),
    plan: Path | None = PlanOption,
) -> None:
    """Search across phases and steps for a pattern."""
    p, _, _ = _load(plan)

    try:
        matches = search_plan(p, pattern, phase_id=phase, state=state, use_regex=regex)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    if not matches:
        out.print(f"[dim]No matches for '{pattern}'.[/]")
        return

    out.print(f"[bold]{len(matches)} match(es)[/] for [cyan]'{pattern}'[/]:\n")

    # Group by phase
    current_phase = ""
    for m in matches:
        if m.phase_id != current_phase:
            current_phase = m.phase_id
            out.print(f"  [bold blue]{current_phase}[/]")

        if m.step_id:
            out.print(f"    {m.step_id} [dim]({m.field})[/]: {m.snippet}")
        else:
            out.print(f"    [dim]phase.{m.field}[/]: {m.snippet}")

    out.print()
    out.print("[dim]→ vectl show <id>                   Inspect a match[/]")
    out.print("[dim]→ vectl edit-step <id> --desc ...    Edit if needed[/]")


# ---------------------------------------------------------------------------
# mine: agent crash recovery
# ---------------------------------------------------------------------------


# @shell_complexity: mine preserves env fallback, --all behavior, empty-state hints, and claimed-step detail output.
def mine(
    agent: str | None = typer.Option(
        None,
        "--by",
        "--agent",
        "-a",
        help="Agent name to filter by. (env: VECTL_AGENT)",
    ),
    show_all: bool = typer.Option(
        False, "--all", help="Show all claimed steps regardless of agent."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Show steps currently claimed by an agent (crash recovery).

    After a context reset, an agent can run this to find what it was working on.
    Use --all to see all claimed steps across agents.
    """
    if not agent and not show_all:
        agent = os.environ.get("VECTL_AGENT")
    if not agent and not show_all:
        _die("Provide --by <agent> or --all. (Or set VECTL_AGENT env var.)")

    p, _, _ = _load(plan)
    claimed = get_claimed_steps(p, agent=agent)

    if not claimed:
        label = "any agent" if show_all else f"'{agent}'"
        out.print(f"[dim]No steps claimed by {label}.[/]")
        out.print()
        if not show_all:
            out.print(f"[dim]→ vectl claim --by {agent}              Auto-claim next step[/]")
        out.print("[dim]→ vectl mine --all                   Show all claimed steps[/]")
        return

    header = "all agents" if show_all else str(agent)
    out.print(f"[bold]Steps claimed by {header}:[/]\n")
    for phase_id, step in claimed:
        claimed_label = f"  [dim][{step.claimed_by}][/]" if show_all else ""
        suggested_label = f"  [dim]suggested: {step.agent}[/]" if step.agent else ""
        out.print(
            f"  ◉  {step.id} — {step.name}  [dim]({phase_id})[/]{claimed_label}{suggested_label}"
        )
        if step.description.strip():
            summary = _one_line_summary(step.description)
            if summary:
                out.print(f"     [dim]{summary}[/]")
        if step.claimed_at:
            out.print(f"     [dim]Claimed at: {step.claimed_at}[/]")

    out.print()
    out.print("[dim]→ vectl show <id>                     Inspect step details[/]")
    out.print('[dim]→ vectl complete <id> --evidence "..." Mark done[/]')
    out.print("[dim]→ vectl defer <id>                    Release claim[/]")


# ---------------------------------------------------------------------------
# review: multi-layer plan review
# ---------------------------------------------------------------------------


# @shell_orchestration: Review phase filtering is tied to the CLI --phase option.
def _restrict_review_result_to_phase(p: Plan, result: Any, phase_id: str):
    selected_phase = p.find_phase(phase_id)
    if selected_phase is None:
        _die(f"Phase '{phase_id}' not found.")
    return replace(
        result,
        phase_progress=[pp for pp in result.phase_progress if pp.phase_id == phase_id],
        active_phases=[ph for ph in result.active_phases if ph.id == phase_id],
        ref_index={
            ref: step_ids
            for ref, step_ids in result.ref_index.items()
            if any(step.id in step_ids for step in selected_phase.steps)
        },
        total_done=sum(
            1
            for step in selected_phase.steps
            if step.status in (StepStatus.DONE, StepStatus.SKIPPED)
        ),
        total_steps=len(selected_phase.steps),
    )


# @shell_complexity: review preserves layered L1-L4 output and exit behavior for plan diagnostics.
def review(
    phase: str | None = typer.Option(None, "--phase", help="Restrict review to a single phase."),
    show_all: bool = typer.Option(False, "--all", help="Include DONE and LOCKED phases in detail."),
    check_refs: bool = typer.Option(False, "--check-refs", help="Check that ref files exist."),
    plan: Path | None = PlanOption,
) -> None:
    """Multi-layer plan review.

    Layers:
      L1: Validation checks (errors/warnings)
      L2: Phase overview with progress
      L3: Active phase detail (steps, deps, refs)
      L4: Spec coverage (reverse index from refs)
    """
    p, _, plan_path = _load(plan)

    # ── Compute review data ────────────────────────────────────────────
    base_path = plan_path.parent if check_refs else None
    result = review_plan(p, check_refs=check_refs, base_path=base_path, include_done=show_all)
    if phase is not None:
        result = _restrict_review_result_to_phase(p, result, phase)

    # ── L1: Validation ──────────────────────────────────────────────────
    out.print("[bold]L1: Validation[/]")
    if not result.validation_issues:
        out.print("  [green]✓ Plan is valid — 0 errors, 0 warnings[/]")
    else:
        for e in result.errors:
            out.print(f"  [red]ERROR:[/] {_esc(e.message)}")
        for w in result.warnings:
            out.print(f"  [yellow]WARN:[/]  {_esc(w.message)}")
        out.print(f"  [dim]{len(result.errors)} error(s), {len(result.warnings)} warning(s)[/]")
    out.print()

    # ── L2: Phase overview with progress ────────────────────────────────
    out.print("[bold]L2: Phase Overview[/]")
    for pp in result.phase_progress:
        icon = _PHASE_STATUS_STYLE[pp.status][0]
        bar_len = 20
        filled = int(bar_len * pp.pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        out.print(f"  {icon} {pp.phase_id:<20s} [{bar}] {pp.done}/{pp.total} ({pp.pct:.0f}%)")

    out.print(
        f"\n  [bold]Overall: {result.total_done}/{result.total_steps} "
        f"({result.overall_pct:.0f}%)[/]"
    )
    out.print()

    # ── L3: Active phase detail ─────────────────────────────────────────
    out.print("[bold]L3: Active Phase Detail[/]")

    if not result.active_phases:
        if not show_all:
            out.print("  [dim]No active phases. Use --all to include DONE/LOCKED.[/]")
        else:
            out.print("  [dim]No phases found.[/]")
    else:
        for ph in result.active_phases:
            out.print(f"\n  [bold]{_esc(ph.id)}[/] — {_esc(ph.name)} ({ph.status.value})")
            if ph.depends_on:
                out.print(f"    deps: {_esc(', '.join(ph.depends_on))}")
            if ph.gate:
                out.print(f"    gate: [yellow]{_esc(ph.gate)}[/]")
                if _is_claim_consistency_scoped_gate(ph.id):
                    out.print(
                        "    [dim]scoped verification evidence: targeted integration run only "
                        "(not full package-wide coverage proof)[/]"
                    )
            for step in ph.steps:
                if is_step_locked(p, ph, step):
                    icon, style = ("🔒", "dim")
                else:
                    icon, style = _STEP_STATUS_STYLE[step.status]
                # NOTE: Rich treats square brackets as markup tags. Step IDs often contain
                # dots (e.g. "mig.1"), so "deps=[mig.1]" is parsed as markup and disappears.
                # Use parentheses to keep deps/refs visible.
                dep_info = f" deps=({_esc(', '.join(step.depends_on))})" if step.depends_on else ""
                ref_info = f" refs=({_esc(', '.join(step.refs))})" if step.refs else ""
                claimed = f" [yellow]@{_esc(step.claimed_by or '')}[/]" if step.claimed_by else ""
                suggested = f" [dim]suggested: {_esc(step.agent)}[/]" if step.agent else ""
                out.print(
                    f"    [{style}]{icon}[/] {_esc(step.id)} — {_esc(step.name)}"
                    f"{claimed}{suggested}{dep_info}{ref_info}"
                )
    out.print()

    # ── L4: Spec coverage (refs reverse index) ──────────────────────────
    out.print("[bold]L4: Spec Coverage[/]")

    if not result.ref_index:
        out.print("  [dim]No refs defined in any steps.[/]")
    else:
        for ref_path in sorted(result.ref_index):
            step_ids = result.ref_index[ref_path]
            out.print(f"  {_esc(ref_path)} ← {_esc(', '.join(step_ids))}")

    out.print()
    if result.errors:
        out.print("[red bold]⚠ Fix validation errors before proceeding.[/]")
        out.print("[dim]→ vectl validate --check-refs       Full validation[/]")
    else:
        out.print("[dim]→ vectl gate-check <phase>           Check phase gate readiness[/]")
        out.print("[dim]→ vectl next                         See claimable steps[/]")

    if result.errors:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# gate-check: phase gate readiness
# ---------------------------------------------------------------------------


def _exit_if_gate_validation_fails(p: Plan) -> None:
    validation_issues = validate_plan(p)
    validation_errors = [issue for issue in validation_issues if not issue.is_warning]
    if not validation_errors:
        return

    out.print("[red bold]✗ Gate check blocked: plan validation failed.[/]")
    for issue in validation_errors:
        out.print(f"  [red]ERROR:[/] {_esc(issue.message)}")
    out.print("[dim]→ vectl validate                  Full validation report[/]")
    raise typer.Exit(1)


# @shell_complexity: gate-check preserves validation, subprocess gate script execution, manual criteria, and summary output.
def gate_check(
    phase_id: str = typer.Argument(help="Phase ID to check gate readiness."),
    plan: Path | None = PlanOption,
) -> None:
    """Check if a phase is ready to pass its gate.

    Checks:
    1. All steps done/skipped
    2. Run gate_script if defined (subprocess, check exit code)
    3. Print manual gate criteria
    4. Recommend unlock if all pass
    """
    import subprocess as sp

    p, _, plan_path = _load(plan)

    _exit_if_gate_validation_fails(p)

    try:
        gc = core_gate_check(p, phase_id)
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    out.print(Panel(f"Gate Check: {gc.phase_id} — {gc.phase_name}", style="bold"))

    all_pass = True

    # ── Check 1: step completion ──────────────────────────────────────
    if gc.steps_complete:
        out.print(f"  [green]✓ Steps:[/] {gc.done_count}/{gc.total_count} complete")
    else:
        all_pass = False
        out.print(
            f"  [red]✗ Steps:[/] {gc.done_count}/{gc.total_count} complete — "
            f"{len(gc.pending_steps)} remaining:"
        )
        for s in gc.pending_steps:
            icon, style = _STEP_STATUS_STYLE[s.status]
            suggested = f"  [dim]suggested: {s.agent}[/]" if s.agent else ""
            out.print(f"    [{style}]{icon}[/] {s.id} — {s.name}{suggested}")

    # ── Check 2: gate_script (CLI-only, subprocess) ───────────────────
    if gc.gate_script:
        out.print(f"\n  Running gate script: [bold]{gc.gate_script}[/]")
        try:
            result = sp.run(
                gc.gate_script,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(plan_path.parent),
            )
            if result.returncode == 0:
                out.print("  [green]✓ Gate script passed[/]")
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines()[:5]:
                        out.print(f"    {line}")
            else:
                all_pass = False
                out.print(f"  [red]✗ Gate script failed (exit {result.returncode})[/]")
                if result.stderr.strip():
                    for line in result.stderr.strip().splitlines()[:5]:
                        out.print(f"    [red]{line}[/]")
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines()[:5]:
                        out.print(f"    {line}")
        except sp.TimeoutExpired:
            all_pass = False
            out.print("  [red]✗ Gate script timed out (120s)[/]")
        except OSError as e:
            all_pass = False
            out.print(f"  [red]✗ Gate script error: {e}[/]")
    else:
        out.print("\n  [dim]No gate_script defined[/]")

    # ── Check 3: manual gate criteria ─────────────────────────────────
    if gc.gate_criterion:
        out.print("\n  [yellow]Manual gate criterion:[/]")
        out.print(f"  {gc.gate_criterion}")
        if _is_claim_consistency_scoped_gate(gc.phase_id):
            # Source: claim-consistency-recovery.integration-verify-fix Issue 2.
            out.print(
                "  [dim]Scoped verification evidence: interpret targeted integration "
                "output as scoped signal only (not full package-wide coverage proof).[/]"
            )
    else:
        out.print("\n  [dim]No manual gate criterion[/]")

    # ── Summary ───────────────────────────────────────────────────────
    out.print()
    if all_pass:
        out.print("[green bold]✓ Phase is gate-ready.[/]")
        if gc.downstream_locked:
            out.print(f"  Downstream phases: {', '.join(gc.downstream_locked)}")
            out.print("[dim]→ vectl complete will auto-unlock downstream phases[/]")
        out.print("[dim]→ vectl status                       View plan overview[/]")
    else:
        out.print("[red bold]✗ Phase is NOT gate-ready.[/]")
        out.print("[dim]→ vectl show <phase>                  Inspect phase details[/]")
        out.print("[dim]→ vectl next                          See claimable steps[/]")


# ---------------------------------------------------------------------------
# Clipboard Commands
# ---------------------------------------------------------------------------


def clipboard_write_cmd(
    author: str = typer.Option(..., "--author", "-a", help="Who is writing."),
    summary: str = typer.Option(..., "--summary", "-s", help="One-line description."),
    content: str = typer.Option(..., "--content", "-c", help="Payload content."),
    ttl: int = typer.Option(24, "--ttl", "-t", help="Time-to-live in hours (default 24)."),
    plan: Path | None = PlanOption,
) -> None:
    """Write to the plan clipboard.

    Overwrites any existing clipboard content. Use for cross-agent handoffs,
    broadcasts, or notes that don't follow DAG edges.
    """
    p, def_h, plan_path = _load(plan)

    try:
        p = clipboard_write(p, author, summary, content, ttl)
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: clipboard write by {author}")

    cb = p.clipboard
    assert cb is not None
    out.print("[green]Clipboard written.[/]")
    out.print(f"  Author: {cb.author}")
    out.print(f"  Summary: {cb.summary}")
    out.print(f"  Expires: {cb.expires_at}")


def clipboard_read_cmd(
    plan: Path | None = PlanOption,
) -> None:
    """Read the plan clipboard.

    Returns clipboard content if present and unexpired. Shows empty message
    if clipboard is empty or expired.
    """
    from vectl.core import _clipboard_expired

    p, _, _ = _load(plan)

    if p.clipboard is None:
        out.print("[dim]Clipboard is empty.[/]")
        return

    if _clipboard_expired(p.clipboard):
        from datetime import datetime, timezone

        try:
            expires = datetime.fromisoformat(p.clipboard.expires_at.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - expires).total_seconds() / 3600
            out.print("[dim]Clipboard is empty.[/]")
            out.print(
                f"[dim]Note: previous clipboard by {p.clipboard.author} expired "
                f"{hours_ago:.1f}h ago.[/]"
            )
        except (ValueError, AttributeError):
            out.print("[dim]Clipboard is empty.[/]")
        return

    cb = p.clipboard
    out.print(Panel("Clipboard", style="bold"))
    out.print(f"  [bold]Author:[/] {cb.author}")
    out.print(f"  [bold]Summary:[/] {cb.summary}")
    out.print(f"  [bold]Written:[/] {cb.written_at}")
    out.print(f"  [bold]Expires:[/] {cb.expires_at}")
    out.print()
    out.print(Panel(cb.content, title="Content", border_style="dim"))


def clipboard_clear_cmd(
    plan: Path | None = PlanOption,
) -> None:
    """Clear the plan clipboard."""
    p, def_h, plan_path = _load(plan)

    if p.clipboard is None:
        out.print("[dim]Clipboard was already empty.[/]")
        return

    p = clipboard_clear(p)
    _save_plan(p, plan_path, def_h, "vectl: clipboard clear")
    out.print("[green]Clipboard cleared.[/]")


# ---------------------------------------------------------------------------
# Dashboard command
# ---------------------------------------------------------------------------


def dashboard(
    output: Path = DashboardOutputOption,
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open the dashboard in a browser after generation.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Generate a static HTML dashboard for visual project overview.

    Creates a single-file HTML page with:
    - Phase navigation sidebar
    - Progress bars and status pills
    - Step tables with expandable details
    - Dependency graph visualization (DAG)

    Open the generated file in any browser to view. No server required.
    """
    import webbrowser

    p, _, _ = _load(plan)

    try:
        html = generate_dashboard(p)
    except Exception as e:
        _die(f"Failed to generate dashboard: {e}")
        return

    # Ensure parent directory exists
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    console.print(f"[green]Dashboard written to:[/] {output.resolve()}")

    if open_browser:
        # Use file:// URL for local file
        file_url = output.resolve().as_uri()
        webbrowser.open(file_url)
        console.print("[dim]Opening in browser...[/]")
