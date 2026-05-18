"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *

def unlock(
    phase_id: str = typer.Argument(help="Phase ID to unlock."),
    evidence: str = typer.Option("", "--evidence", "-e", help="Evidence for unlocking."),
    plan: Path | None = PlanOption,
) -> None:
    """Unlock a locked phase by validating all dependencies are done."""
    p, def_h, plan_path = _load(plan)
    try:
        p = unlock_phase(p, phase_id)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: unlock phase {phase_id}")
    out.print(f"[green]Unlocked:[/] {phase_id}")
    if evidence:
        out.print(f"[dim]Evidence: {evidence}[/]")
    out.print()
    out.print(f"[dim]→ vectl show {phase_id}[/]")
    out.print("[dim]→ vectl next[/]")


# ---------------------------------------------------------------------------
# recalc-lock: repair lock/pending status for all phases
# Source: vectl plan step cli-recalc-lock (task specification).
# ---------------------------------------------------------------------------


def recalc_lock(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would change without saving.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Recalculate LOCKED/PENDING status for all phases.

    Walks every phase and reapplies dependency rules: phases whose
    dependencies are not all DONE become LOCKED; phases whose dependencies
    are all DONE (or have none) become PENDING. DONE and IN_PROGRESS phases
    are never affected.

    Normally vectl maintains lock status automatically on every write. Use
    this command only for human diagnosis after direct YAML edits that may
    have left lock status inconsistent.

    For each phase whose status changes, prints a line of the form:
        [vectl] Lock status updated: phase-a (pending)

    --dry-run previews what would change without saving the plan file.
    """
    p, def_h, plan_path = _load(plan)

    if dry_run:
        # Operate on a deep copy so the original plan is not mutated.
        p_copy = p.model_copy(deep=True)
        changed = recalc_lock_status(p_copy)
        msg = format_lock_changes(changed, p_copy)
        out.print(msg if msg else "[vectl] Lock status is consistent. No changes needed.")
    else:
        changed = recalc_lock_status(p)
        msg = format_lock_changes(changed, p)
        _save_plan(p, plan_path, def_h, "vectl: recalc lock status")
        out.print(msg if msg else "[vectl] Lock status is consistent. No changes needed.")


# ---------------------------------------------------------------------------
# cli.12b: repair claims (operator recovery)
# Source: claim-consistency-recovery.repair-claims-command
# ---------------------------------------------------------------------------


# @shell_complexity: repair-claims preserves dry-run/json modes and human reconciliation report formatting.
def repair_claims_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview reconciliation without writing claims.json.",
    ),
    step: str | None = typer.Option(
        None,
        "--step",
        help="Repair only this step's claim on current branch.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Repair claims.json consistency against plan.yaml (current branch scope)."""

    p, _, plan_path = _load(plan)
    claims_path = resolve_claims_path(plan_path)

    try:
        result = repair_claims(
            p,
            plan_path,
            claims_path,
            dry_run=dry_run,
            step_id=step,
        )
    except PlanError as e:
        _die(str(e))
        return

    payload = result.to_dict()

    if as_json:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    mode = "[dry-run]" if dry_run else ""
    out.print(f"[bold]Repair claims {mode}[/]")
    out.print(f"Status: {result.status.value}")
    out.print(f"Policy: {result.policy}")
    out.print(f"Branch: {result.branch}")
    out.print(f"Plan: {result.plan_path}")
    out.print(f"Claims: {result.claims_path}")
    if result.step_scope is not None:
        out.print(f"Scope: step={result.step_scope}")
    if result.missing_claims_file:
        out.print("Claims file was missing; fallback started from empty map.")

    if not result.actions:
        out.print("No claim inconsistencies detected.")
        return

    out.print(f"Applied actions: {len(result.actions)}")
    for action in result.actions:
        out.print(f"- {action.action}: {action.key} ({action.reason})")


# @shell_complexity: batch add command preserves stdin/YAML validation and post-mutation output contract.
def add_steps_cmd(
    phase: str = typer.Option(..., "--phase", help="Phase ID to add steps to."),
    plan: Path | None = PlanOption,
) -> None:
    """Batch add steps from stdin (YAML format).

    Reads a YAML list from stdin. Each entry has:
      - name (required)
      - desc, after, verify, refs, id (optional)
      - status: pending (default), done, skipped (optional, for import)
      - evidence: required when status=done (optional)
      - skipped_reason: required when status=skipped (optional)

    Example input:
      - name: "Step A"
        desc: "Do thing A"
        status: done
        evidence: "commit abc"
      - name: "Step B"
        after: ["phase.step-a"]
    """
    _check_not_linked_worktree(plan)
    import yaml

    raw = sys.stdin.read()
    if not raw.strip():
        _die("No input from stdin. Pipe YAML step definitions.")
        return  # unreachable

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        _die(f"Invalid YAML from stdin: {e}")
        return  # unreachable

    if not isinstance(data, list):
        _die("Expected YAML list of step definitions from stdin.")
        return  # unreachable

    p, def_h, plan_path = _load(plan)
    try:
        p, generated_ids = add_steps_bulk(p, phase, data)
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _save_plan(p, plan_path, def_h, f"vectl: add steps to phase {phase}")

    out.print(f"[green]Added {len(generated_ids)} step(s)[/] to phase [bold]{phase}[/]:")
    for gid in generated_ids:
        out.print(f"  • {gid}")
    out.print()
    out.print(f"[dim]→ vectl show {phase}         See all steps[/]")
    out.print("[dim]→ vectl next              See claimable steps[/]")
    out.print("[dim]→ vectl search <pattern>  Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.14: search (plan-wide pattern search)
# ---------------------------------------------------------------------------


# @shell_complexity: search preserves grouped phase output and empty-result CLI messaging.
