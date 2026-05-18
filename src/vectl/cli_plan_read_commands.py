"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *

def next_cmd(
    detail: bool = typer.Option(False, "--detail", help="Show full descriptions inline."),
    limit: int = typer.Option(3, "--limit", "-n", help="Max steps to show (default: 3)."),
    all_steps: bool = typer.Option(False, "--all", help="Show all available steps."),
    agent: str | None = typer.Option(
        None, "--agent", "-a", help="Prioritize steps suggested for this agent."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Show claimable steps (what to work on next)."""
    p, _, _ = _load(plan)

    # Show strategy context if present
    if p.context.strip():
        out.print(Panel(p.context.strip(), title="Strategy", border_style="blue"))

    # Get next steps with phase info to correctly track phase for duplicate step IDs
    steps_with_phase = _get_next_steps_with_phase(p, agent=agent)
    if not steps_with_phase:
        out.print("[dim]No claimable steps. All phases may be done or locked.[/]")
        return

    total = len(steps_with_phase)
    display_limit = total if all_steps else limit
    visible = steps_with_phase[:display_limit]

    out.print("[bold]Next Steps[/]\n")

    for i, (phase, step) in enumerate(visible, 1):
        phase_id = phase.id

        icon = _step_icon(step.status, verify=step.verify)
        # One-line summary: first line of description, truncated
        summary = _one_line_summary(step.description)

        deps_str = f"  deps: {', '.join(step.depends_on)}" if step.depends_on else ""
        agent_str = f"  suggested: {step.agent}" if step.agent else ""
        summary_str = f"  {summary}" if summary else ""
        # RFC: docs/RFC-affinity.md
        # Show exclusive affinity icon
        affinity_icon = ""
        if step.agent and (step.affinity or p.default_affinity) == AffinityMode.EXCLUSIVE:
            affinity_icon = " 🔐"

        out.print(
            f"  {i}. {icon}  {_esc(step.id)} — {_esc(step.name)}  "
            f"[dim]({_esc(phase_id)}){_esc(deps_str)}{_esc(agent_str)}[/]{affinity_icon}"
        )
        if summary_str:
            out.print(f"     [dim]{_esc(summary)}[/]")

        if detail and step.description.strip():
            out.print(Panel(Text(step.description.strip()), border_style="dim", padding=(0, 2)))
            if step.verification:
                out.print(f"     [green]Verify:[/] {_esc(step.verification)}")
            if step.refs:
                out.print(f"     [dim]Refs: {_esc(', '.join(step.refs))}[/]")
            out.print()

    hidden = total - len(visible)
    if hidden > 0:
        out.print(f"  [dim]... and {hidden} more[/]")

    out.print()
    out.print("[dim]→ vectl claim <id> --agent <name>   Claim a step[/]")
    out.print("[dim]→ vectl show <id>                   Inspect before claiming[/]")
    if hidden > 0:
        out.print("[dim]→ vectl next --all                  Show all steps[/]")


# ---------------------------------------------------------------------------
# cli.3: status
# ---------------------------------------------------------------------------


# @shell_complexity: status must preserve phase-detail, mismatch, duplicate-warning, and hint output formatting.
def status(
    plan: Path | None = PlanOption,
    phase: str | None = typer.Option(None, "--phase", help="Show detail for a specific phase."),
) -> None:
    """Show plan status overview."""
    p, _, plan_path = _load(plan)

    if phase is not None:
        _show_phase_detail(p, phase)
        _print_duplicate_id_warning_block(p)
        return

    # Check for claims.json vs plan.yaml mismatch
    has_mismatch, ghost_claims, stale_plan_claims = _check_claim_mismatch(p, plan_path)

    # Overview: all phases
    table = Table(title=f"Plan: {p.project}", show_lines=True)
    table.add_column("Phase", style="bold")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Progress")
    table.add_column("Depends On")

    for ph in p.phases:
        total = len(ph.steps)
        done = sum(1 for s in ph.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED))
        bar = f"{done}/{total}"
        if total > 0:
            pct = done / total * 100
            bar += f" ({pct:.0f}%)"

        table.add_row(
            _esc(ph.id),
            _esc(ph.name),
            _phase_icon(ph.status),
            bar,
            _esc(", ".join(ph.depends_on)) if ph.depends_on else "-",
        )

    out.print(table)
    out.print()
    _print_duplicate_id_warning_block(p)

    # B1: Show mismatch indicator when detected
    if has_mismatch:
        out.print()
        out.print("[dim]ℹ Stale claims detected (routine after agent restart):[/]")
        if ghost_claims:
            out.print(f"  [dim]Ghost claims: {len(ghost_claims)}[/]")
        if stale_plan_claims:
            out.print(f"  [dim]Stale plan claims: {len(stale_plan_claims)}[/]")
        out.print("[dim]  → Run `vectl repair claims` to fix (safe, idempotent)[/]")

    out.print()
    out.print("[dim]→ vectl next                      See claimable steps[/]")
    out.print("[dim]→ vectl show <phase>               Phase detail[/]")


# @shell_complexity: phase renderer preserves existing Rich layout and conditional detail fields.
def _show_phase_detail(p: Plan, phase_id: str) -> None:
    phase = next((ph for ph in p.phases if ph.id == phase_id), None)
    if not phase:
        _die(f"Phase '{phase_id}' not found.")
        return  # unreachable

    out.print(f"## Phase: {phase.name}", markup=False)
    out.print(f"**Name:** {phase.name}", markup=False)
    out.print(f"**Status:** {_phase_icon(phase.status)}")
    if phase.depends_on:
        out.print(f"**Depends on:** {', '.join(phase.depends_on)}", markup=False)

    if phase.context:
        out.print(f"**Context:** {phase.context}", markup=False)

    if phase.gate:
        out.print(f"\n**Gate:** {phase.gate}", markup=False)

    out.print("\n### Steps\n")
    for step in phase.steps:
        locked = is_step_locked(p, phase, step)
        icon = _step_icon(step.status, locked=locked, verify=step.verify)
        suggested = f"  [dim]suggested: {_esc(step.agent)}[/]" if step.agent else ""
        # RFC: docs/RFC-affinity.md
        # Show exclusive affinity icon
        affinity_icon = ""
        if step.agent and (step.affinity or p.default_affinity) == AffinityMode.EXCLUSIVE:
            affinity_icon = " 🔐"
        out.print(
            f"  {icon} **{_esc(step.id)}** — {_esc(step.name)} "
            f"({_esc(phase.id)}){suggested}{affinity_icon}"
        )
        if step.status == StepStatus.CLAIMED:
            out.print(f"    [dim]Claimed by {_esc(step.claimed_by or '')}[/]")
            # RFC: docs/RFC-affinity.md
            # Show affinity override warning
            if step.affinity_override:
                out.print("    [yellow]⚠ Affinity overridden[/]")

    out.print()


# @shell_complexity: step renderer preserves existing Rich layout across all step states and diagnostics.
def _show_step_detail(p: Plan, step_id: str, plan_path: Path | None = None) -> None:
    found = p.find_step(step_id)
    if not found:
        _die(f"Step '{_canonical_selector_error_target(step_id)}' not found.")
        return  # unreachable
    phase, step = found

    # B2: Check for mismatch for this specific step
    mismatch_info = ""
    if plan_path is not None and step.status == StepStatus.CLAIMED:
        has_mismatch, ghost_claims, stale_plan_claims = _check_claim_mismatch(p, plan_path)
        if has_mismatch:
            from vectl.claims import get_current_branch

            branch = get_current_branch()
            step_key = f"{branch}:{step_id}"
            if step_key in ghost_claims:
                mismatch_info = (
                    "⚠ Claim exists in claims.json but step is not claimed in plan (ghost)"
                )
            elif step_key not in ghost_claims and step_id in stale_plan_claims:
                mismatch_info = "⚠ Step is claimed in plan but missing from claims.json"

    out.print(f"## Step: {step.name}", markup=False)
    out.print(f"**ID:** {step.id}", markup=False)
    out.print(f"**Phase:** {phase.name} ({phase.id})", markup=False)

    # R1 Source: Bug report "vectl show <step> does not display parent phase description or context"
    # Phase context carries operational guidance for all steps in the phase.
    if phase.context:
        out.print(f"**Phase Context:** {phase.context}", markup=False)

    locked = is_step_locked(p, phase, step)
    out.print(f"**Status:** {_step_icon(step.status, locked=locked, verify=step.verify)}")

    # B2: Show mismatch explanation when detected
    if mismatch_info:
        out.print(f"[yellow]{mismatch_info}[/]")

    _print_duplicate_id_recommendation_for_step(p, step.id)

    if step.agent:
        out.print(f"**Suggested agent:** {step.agent}", markup=False)
        # RFC: docs/RFC-affinity.md
        # Show affinity mode when agent is set
        effective_affinity = step.affinity or p.default_affinity
        affinity_str = effective_affinity.value if effective_affinity else "suggested"
        out.print(f"**Affinity:** {affinity_str}", markup=False)

    # RFC: docs/RFC-affinity.md
    # Show affinity override status if present
    if step.affinity_override:
        out.print("[yellow]**Affinity Override:** true[/]")
        if step.affinity_override_by:
            out.print(f"**Overridden by:** {step.affinity_override_by}", markup=False)
        if step.affinity_override_at:
            out.print(f"**At:** {step.affinity_override_at}", markup=False)

    if step.description:
        out.print(f"\n**Description:**\n{step.description}", markup=False)

    if step.depends_on:
        out.print(f"**Depends On:** {', '.join(step.depends_on)}", markup=False)

    if step.verification:
        out.print(f"\n**Verification:**\n{step.verification}", markup=False)

    if step.status == StepStatus.CLAIMED:
        out.print(f"\n**Claimed By:** {step.claimed_by}", markup=False)
        out.print(f"**At:** {step.claimed_at}", markup=False)

    if step.evidence:
        out.print(f"\n**Evidence:**\n{step.evidence}", markup=False)

    if step.rejection_reason:
        out.print("[red]**Rejection Reason:**[/]")
        out.print(step.rejection_reason, markup=False)
        if step.rejection_history:
            last = step.rejection_history[-1]
            if last.reviewer:
                out.print(f"**Reviewer:** {last.reviewer}", markup=False)

    out.print()
    eid = _esc(step.id)
    if step.status == StepStatus.PENDING:
        out.print(f"[dim]→ vectl claim {eid} --agent <name>[/]")
    elif step.status == StepStatus.CLAIMED:
        out.print(f'[dim]→ vectl complete {eid} --evidence "..."[/]')
        out.print(f"[dim]→ vectl defer {eid}                    Release claim[/]")
    elif step.status == StepStatus.DONE:
        out.print("[dim]→ vectl next                          Find more work[/]")
        out.print(f'[dim]→ vectl reject {eid} --reason "..."     Reject for rework[/]')
    elif step.status == StepStatus.SKIPPED:
        out.print("[dim]→ vectl next                          Find more work[/]")
    elif step.status == StepStatus.REJECTED:
        out.print(f"[dim]→ vectl claim {eid} --agent <name>   Re-claim for rework[/]")


# @shell_orchestration: Selector formatting helper belongs to CLI diagnostics.
def _canonical_selector_error_target(selector: str):
    """Collapse accidental double-prefix selector forms for error output."""
    if "." not in selector:
        return selector
    phase_prefix, _, step_suffix = selector.partition(".")
    if phase_prefix and step_suffix.startswith(f"{phase_prefix}."):
        return format_step_selector(phase_prefix, step_suffix)
    return selector


def show(
    target: str = typer.Argument(..., help="Step ID or Phase ID."),
    plan: Path | None = PlanOption,
) -> None:
    """Show details for a step or phase."""
    p, _, plan_path = _load(plan)

    # Try phase match
    ph = p.find_phase(target)
    if ph is not None:
        _show_phase_detail(p, target)
        _print_duplicate_id_warning_block(p)
        return

    # Try step match
    found = p.find_step(target)
    if found is not None:
        _show_step_detail(p, target, plan_path=plan_path)
        return

    canonical_target = _canonical_selector_error_target(target)
    _die(f"'{canonical_target}' not found as step or phase.")


# ---------------------------------------------------------------------------
# cli.4: guide (renamed from help-agent)
# ---------------------------------------------------------------------------


def guide_cmd(
    on: str | None = typer.Option(
        None,
        "--on",
        help="Show one topic only: startup, stuck, recovery, review, planning, or migration.",
    ),
) -> None:
    """Show agent onboarding guide."""
    if on is None:
        combined = "\n---\n\n".join(g.strip() for g in _GUIDE_ALL)
        combined += (
            "\n\n---\n*Use ``vectl_guide`` (CLI fallback:\n"
            "``vectl guide --on <topic>``) to revisit one section.*"
        )
        out.print(Markdown(combined))
    else:
        guide = _GUIDE_TOPICS.get(on)
        if guide is None:
            _die(
                f"Unknown topic '{on}'. Use: startup, stuck, recovery, review, planning, migration."
            )
            return  # unreachable, for type checker
        out.print(Markdown(guide.strip()))


def dag(
    phase: str | None = typer.Option(
        None, "--phase", help="Show step-level DAG within this phase."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Output dependency graph as Mermaid flowchart.

    Default: phase-level DAG (nodes = phases, edges = depends_on).
    With --phase: step-level DAG within that phase.

    Paste output into GitHub/Obsidian to render as a diagram.
    """
    from vectl.core import generate_mermaid_dag

    p, _, _ = _load(plan)

    try:
        mmd = generate_mermaid_dag(p, phase_id=phase)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _print_duplicate_id_warning_block(p)
    out.print(mmd)


# ---------------------------------------------------------------------------
# cli.5: claim + complete
# ---------------------------------------------------------------------------


# @shell_complexity: claim preserves conflict diagnostics, affinity output, autosave, guidance, and detail rendering semantics.
