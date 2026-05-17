"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *
from vectl.cli_plan_read_commands import _show_step_detail

def claim(
    step_id: str | None = typer.Argument(
        None, help="Step ID to claim (auto-picks first available if omitted)."
    ),
    agent: str = typer.Option(
        os.environ.get("VECTL_AGENT", "agent"),
        "--by",
        "--agent",
        "-a",
        help="Agent name. (env: VECTL_AGENT)",
    ),
    guidance: bool = typer.Option(
        True,
        "--guidance/--no-guidance",
        help="Show claim-time Guidance block (refs + evidence template).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Override exclusive affinity violations (sets audit trail).",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Claim a step for work.

    When step_id is omitted, automatically claims the first available step
    from get_next_steps() (halves the common-path tool calls: next+claim → claim).

    RFC: docs/RFC-affinity.md
    Enforces agent affinity. Use --force to override exclusive affinity.
    """
    p, def_h, plan_path = _load(plan)

    if step_id is None:
        candidates = get_next_steps(p, agent=agent)
        if not candidates:
            _die("No claimable steps available. All phases may be done or locked.")
        step_id = candidates[0].id
        out.print(f"[dim]Auto-selected:[/] {step_id}")

    try:
        p, result = claim_step(
            p,
            step_id,
            agent,
            force=force,
            claims_path=resolve_claims_path(plan_path),
        )
    except ClaimConflictError as e:
        # Rich claim conflict diagnostics with actionable next step
        metadata = e.metadata
        console.print(f"[red bold]Error:[/] {e}", soft_wrap=True)
        console.print()
        console.print("[bold]Claim Details:[/]")
        console.print(f"  Step ID:    {metadata.step_id}", soft_wrap=True)
        console.print(f"  Branch:     {metadata.branch}", soft_wrap=True)
        console.print(f"  Claimed by: {metadata.claimant}", soft_wrap=True)
        console.print(f"  Claimed at: {metadata.claimed_at}", soft_wrap=True)
        console.print()
        console.print("[bold]Next Steps:[/]")
        console.print(f"  1. Inspect the step: [cyan]vectl show {metadata.step_id}[/]")
        console.print("  2. If this claim is stale, you can repair claims with:")
        console.print("     [cyan]vectl repair claims --dry-run[/]")
        raise typer.Exit(1) from None
    except PlanError as e:
        _die(str(e))
        return  # unreachable, but satisfies Pyright

    # RFC: docs/RFC-affinity.md
    # Display affinity warning/override messages
    if result.warning_message:
        if result.affinity_override:
            out.print(f"[yellow]⚠️  {result.warning_message}[/]")
        else:
            out.print(f"[yellow]⚠️  Affinity warning: {result.warning_message}[/]")

    _save_plan(p, plan_path, def_h, f"vectl: claim {step_id} by {agent}")
    out.print(f"[green]Claimed:[/] {step_id} by {agent}")

    # Show affinity override in claim summary if applicable
    if result.affinity_override:
        step = p.find_step(step_id)
        if step:
            step_obj = step[1]
            out.print(f"  [dim]Affinity Override: true (by {step_obj.affinity_override_by})[/]")

    # Feature request (2026-02-12): claim-time guidance.
    # Source: user feature request "Output guidance when running vectl claim".
    # R1/R4: clear markers + bounded content.
    if guidance:
        found = p.find_step(step_id)
        if found is not None:
            from vectl.claim_guidance import build_claim_guidance

            phase_obj, step_obj = found
            payload = build_claim_guidance(p, phase_obj, step_obj)
            out.print()
            out.print(payload.markdown, markup=False)

    # Show full step spec (saves a show call)
    found = p.find_step(step_id)
    if found is not None:
        out.print()
        _show_step_detail(p, step_id, plan_path=plan_path)


# @shell_complexity: complete preserves mutation, next-step preview, and no-more-work output in one Typer handler.
def complete(
    step_id: str = typer.Argument(help="Step ID to complete."),
    evidence: str = typer.Option(..., "--evidence", "-e", help="Evidence of completion."),
    plan: Path | None = PlanOption,
) -> None:
    """Mark a step as done with evidence."""
    p, def_h, plan_path = _load(plan)
    try:
        p = complete_step(p, step_id, evidence, claims_path=resolve_claims_path(plan_path))
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: complete {step_id}")
    out.print(f"[green]Completed:[/] {step_id}")

    # Show next available steps - use _get_next_steps_with_phase for correct phase tracking
    next_steps_with_phase = _get_next_steps_with_phase(p)
    if next_steps_with_phase:
        out.print()
        out.print("[bold]Next available:[/]")
        for phase, s in next_steps_with_phase[:3]:
            icon = _step_icon(s.status, verify=s.verify)
            suggested = f"  suggested: {_esc(s.agent)}" if s.agent else ""
            out.print(f"  {icon}  {s.id} — {s.name}  [dim]({phase.id}){suggested}[/]")
        if len(next_steps_with_phase) > 3:
            out.print(f"  [dim]... and {len(next_steps_with_phase) - 3} more[/]")
        out.print()
        out.print("[dim]→ vectl claim <id> --agent <name>[/]")
        out.print("[dim]→ vectl show <id>                  [/]")
    else:
        out.print("[dim]No more claimable steps.[/]")


def complete_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to complete."),
    evidence: str = typer.Option(
        ..., "--evidence", "-e", help="Evidence/audit note for phase completion."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Mark a phase as done with evidence.

    Source:
        - Eidos migration requires explicit phase completion to avoid dependency
          deadlocks when importing historical step statuses.
        - User instruction in this conversation: choose option B (extend tool
          then test) before continuing migrate phase.
    """
    p, def_h, plan_path = _load(plan)
    try:
        p, unlocked = complete_phase(p, phase_id, evidence)
    except PlanError as e:
        _die(str(e))
        return  # unreachable (typer exits), but keeps type-checkers honest

    _save_plan(p, plan_path, def_h, f"vectl: complete phase {phase_id}")
    out.print(f"[green]Completed phase:[/] {phase_id}")
    if unlocked:
        out.print(f"[green]Unlocked:[/] {', '.join(unlocked)}")
    out.print()
    out.print("[dim]→ vectl status                      See full plan[/]")
    out.print("[dim]→ vectl next                        See claimable steps[/]")


# ---------------------------------------------------------------------------
# cli.6: defer + reject + skip
# ---------------------------------------------------------------------------


def defer(
    step_id: str = typer.Argument(help="Step ID to defer."),
    plan: Path | None = PlanOption,
) -> None:
    """Return a claimed step to pending."""
    p, def_h, plan_path = _load(plan)
    try:
        p = defer_step(p, step_id, claims_path=resolve_claims_path(plan_path))
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: defer {step_id}")
    out.print(f"[yellow]Deferred:[/] {step_id}")
    out.print()
    out.print("[dim]→ vectl next                      Find new work[/]")


def reject(
    step_id: str = typer.Argument(help="Step ID to reject."),
    reason: str = typer.Option(..., "--reason", "-r", help="Rejection reason."),
    reviewer: str = typer.Option(
        "", "--by", "--reviewer", help="Reviewer name. (env: VECTL_AGENT)"
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Reject a completed step, moving it back for rework."""
    p, def_h, plan_path = _load(plan)
    try:
        p = reject_step(p, step_id, reason, reviewer)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: reject {step_id}")
    out.print(f"[red]Rejected:[/] {step_id} — {reason}")
    out.print()
    out.print("[dim]→ vectl next                      Rejected steps appear first[/]")


def skip(
    step_id: str = typer.Argument(help="Step ID to skip."),
    reason: str = typer.Option(
        ...,
        "--reason",
        "-r",
        help="Reason for skipping: superseded, irrelevant, absorbed, deprioritized.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Skip a step with a reason.

    Valid reasons: superseded, irrelevant, absorbed, deprioritized.
    """
    p, def_h, plan_path = _load(plan)
    try:
        p = skip_step(p, step_id, reason)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: skip {step_id}")
    out.print(f"[dim]Skipped:[/] {step_id} — {reason}")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")


def cancel(
    step_id: str = typer.Argument(help="Step ID to cancel (alias for skip --reason irrelevant)."),
    plan: Path | None = PlanOption,
) -> None:
    """Cancel a step (alias for skip --reason irrelevant)."""
    p, def_h, plan_path = _load(plan)
    try:
        p = skip_step(p, step_id, SkipReason.IRRELEVANT.value)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: cancel {step_id}")
    out.print(f"[dim]Cancelled:[/] {step_id} — irrelevant")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")


def skip_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to skip."),
    reason: str = typer.Option(
        ...,
        "--reason",
        "-r",
        help="Reason for skipping: superseded, irrelevant, absorbed, deprioritized.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Allow skipping a locked phase with remaining steps.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Skip all remaining steps in a phase.

    Pending/rejected steps are skipped. Claimed steps are deferred first, then skipped.
    Done/skipped steps are left unchanged. Phase auto-completes when all steps
    are done or skipped.

    Locked phases with 0 steps can always be skipped (the lock protects nothing).
    Use --force to skip a locked phase that still has remaining steps.
    """
    p, def_h, plan_path = _load(plan)
    skipped_ids: list[str] = []
    try:
        p, skipped_ids = skip_phase(p, phase_id, reason, force=force)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: skip phase {phase_id}")
    if skipped_ids:
        out.print(f"[dim]Skipped phase:[/] {phase_id} — {reason}")
        for sid in skipped_ids:
            out.print(f"  [dim]↳[/] {sid}")
    else:
        out.print(f"[dim]Phase {phase_id}:[/] no steps to skip (all already done/skipped or empty)")
    out.print()
    out.print("[dim]→ vectl next                      See remaining steps[/]")
    out.print("[dim]→ vectl status                    Plan overview[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.7: update-checklist
# ---------------------------------------------------------------------------


def check_cmd(
    step_id: str = typer.Argument(help="Step ID containing the checklist."),
    keyword: str | None = typer.Argument(None, help="Keyword to toggle a checklist item."),
    add: str | None = typer.Option(None, "--add", help="Text for a new checklist item."),
    plan: Path | None = PlanOption,
) -> None:
    """Toggle or add a checklist item in a step's description."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)
    try:
        p = update_checklist(p, step_id, check=keyword, append=add)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: update checklist {step_id}")
    out.print(f"[green]Updated checklist:[/] {step_id}")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")


# ---------------------------------------------------------------------------
# cli.8: validate
# ---------------------------------------------------------------------------


# @shell_complexity: validate preserves grouped error/warning output and CLI exit behavior.
def validate(
    plan: Path | None = PlanOption,
    check_refs: bool = typer.Option(False, "--check-refs", help="Check that ref files exist."),
) -> None:
    """Validate plan structure and consistency."""
    p, _, plan_path = _load(plan)

    base_path = plan_path.parent if check_refs else None
    errors = validate_plan(p, check_refs=check_refs, base_path=base_path)

    if not errors:
        out.print("[green bold]✓ Plan is valid.[/]")
        out.print()
        out.print("[dim]→ vectl status                    Plan overview[/]")
        out.print("[dim]→ vectl next                      See claimable steps[/]")
        return

    errs = [e for e in errors if not e.is_warning]
    warns = [e for e in errors if e.is_warning]

    for e in errs:
        out.print(f"  [red]ERROR:[/] {e.message}")
    for w in warns:
        out.print(f"  [yellow]WARN:[/]  {w.message}")

    out.print()
    out.print(f"[red]{len(errs)} error(s)[/], [yellow]{len(warns)} warning(s)[/]")

    if errs:
        raise typer.Exit(1)


# @shell_complexity: migration command preserves preview, confirmation, idempotence, and warning output semantics.
