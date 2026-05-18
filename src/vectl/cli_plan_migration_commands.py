"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *

def migrate_cmd(
    plan: Path | None = PlanOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Migrate legacy split-state runtime data into unified plan.yaml.

    Source: docs/ADR-unified-state.md (explicit migration phase requirement).
    """
    plan_path = resolve_plan_path(plan)
    state_path = resolve_state_path(plan_path)
    migrated_path = state_path.with_suffix(".json.migrated")

    if not state_path.exists():
        if migrated_path.exists():
            out.print(f"[green]Already migrated:[/] {migrated_path}")
            return
        out.print(f"[yellow]No legacy state file found:[/] {state_path}")
        return

    loaded_plan, _, _ = _load(plan)
    try:
        for phase in loaded_plan.phases:
            for step in phase.steps:
                require_unambiguous_target_step_id(loaded_plan, step.id, operation="migrate")
    except PlanError as e:
        _die(str(e))
        return

    out.print("[bold]Migration preview[/]")
    out.print(f"  plan: {plan_path}")
    out.print(f"  legacy state: {state_path}")
    out.print(f"  backup target: {migrated_path}")

    if not yes:
        out.print()
        confirm = typer.prompt("Run split-state migration now? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        result = migrate_from_split_state(plan_path)
    except PlanIOError as e:
        _die(str(e))
        return  # unreachable, keeps type-checkers honest

    if result.already_migrated:
        out.print(f"[green]Already migrated:[/] {migrated_path}")
        return

    out.print(
        "[green]Migration complete:[/] "
        f"steps={result.migrated_steps}, phases={result.migrated_phases}"
    )
    out.print(f"[dim]Backup:[/] {migrated_path}")

    if result.warnings:
        out.print("[yellow]Warnings:[/]")
        for warning in result.warnings:
            out.print(f"  - {warning}")


# @shell_complexity: duplicate-ID migration CLI must preserve dry-run/apply JSON and human output contract in one public command.
def migrate_step_id_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview duplicate step-ID migration without writing plan.yaml.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt for apply."),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable payload including report and evidence.",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Migrate duplicate step IDs to globally unique IDs.

    Source: step `step-id-migration-tooling.surfaces` and contract
    docs/contracts/duplicate-id-migration-contract.yaml dry-run/apply schema.
    """

    loaded_plan, def_h, plan_path = _load(plan)
    claims_path = resolve_claims_path(plan_path)
    branch = get_current_branch()

    report, _ = build_duplicate_step_id_migration_dry_run(
        loaded_plan,
        claims_path=claims_path,
        branch=branch,
    )
    preview_evidence = build_duplicate_step_id_migration_evidence(
        command="vectl migrate-step-id",
        command_args=["--dry-run"],
        run_mode="dry-run",
        migrated=False,
        report=report,
    )

    if dry_run:
        dry_run_payload: dict[str, object] = {
            "status": "recommendation_only",
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }
        if as_json:
            typer.echo(json.dumps(dry_run_payload, sort_keys=True))
            return

        out.print("[bold]Duplicate step-ID migration dry-run[/]")
        out.print(f"Plan: {plan_path}")
        out.print(f"Branch: {branch}")
        out.print(f"Duplicate groups: {len(report.duplicate_groups)}")
        out.print(f"Rename map entries: {len(report.rename_map)}")
        affected_phases = ", ".join(report.affected_phases) if report.affected_phases else "-"
        out.print(f"Affected phases: {affected_phases}")
        out.print("[dim]report[/]")
        out.print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        out.print("[dim]evidence[/]")
        out.print(json.dumps(preview_evidence.to_dict(), indent=2, sort_keys=True))
        return

    if not yes:
        out.print("[bold]Duplicate step-ID migration apply preview[/]")
        out.print(f"Plan: {plan_path}")
        out.print(f"Branch: {branch}")
        out.print(f"Duplicate groups: {len(report.duplicate_groups)}")
        out.print(f"Rename map entries: {len(report.rename_map)}")
        if report.claimed_step_conflicts:
            out.print("[yellow]Claimed-step conflicts detected; apply will be blocked.[/]")
        out.print()
        confirm = typer.prompt("Apply duplicate step-ID migration now? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        apply_result = apply_duplicate_step_id_migration(
            plan_path,
            expected_hash=def_h,
            claims_path=claims_path,
            branch=branch,
            command="vectl migrate-step-id",
            command_args=["--yes"] if yes else [],
            run_mode="apply",
        )
    except CASConflictError:
        _die(
            "CAS conflict: plan.yaml was modified by another process since you loaded it. "
            "Re-read with `vectl status` or `vectl show`, then retry migration."
        )
        return
    except PlanError as e:
        _die(str(e))
        return

    apply_payload: dict[str, object] = {
        "status": "repair_applied",
        "migrated": apply_result.migrated,
        "new_plan_hash": apply_result.new_plan_hash,
        "report": apply_result.report.to_dict(),
        "evidence": apply_result.evidence.to_dict(),
    }

    if as_json:
        typer.echo(json.dumps(apply_payload, sort_keys=True))
        return

    out.print("[green]Duplicate step-ID migration complete.[/]")
    out.print(f"migrated={apply_result.migrated}")
    out.print(f"new_plan_hash={apply_result.new_plan_hash or '-'}")
    out.print("[dim]report[/]")
    out.print(json.dumps(apply_result.report.to_dict(), indent=2, sort_keys=True))
    out.print("[dim]evidence[/]")
    out.print(json.dumps(apply_result.evidence.to_dict(), indent=2, sort_keys=True))


# @shell_complexity: recovery command preserves backup discovery, diff preview, confirmation, and restore diagnostics.
def recover(
    plan: Path | None = PlanOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Recover plan from backup in .git/vectl/plan.yaml.bak.

    Restores the plan to a previous state from the backup file.
    Shows a diff of what will change before applying.
    """
    from vectl.core import apply_recovery, preview_recovery

    _, _, plan_path = _load(plan)

    # Find backup path
    root_cli = sys.modules.get("vectl.cli")
    resolve_git_dir = getattr(root_cli, "_resolve_git_dir", _resolve_git_dir)
    git_dir = resolve_git_dir(plan_path)
    if git_dir is None:
        _die("Not in a git repository or in a linked worktree")

    assert git_dir is not None  # Type narrowing for pyright
    backup_path = git_dir / "vectl" / "plan.yaml.bak"
    if not backup_path.exists():
        _die(f"Backup not found: {backup_path}")

    # Preview diff without writing
    result: RecoverResult | None = None
    try:
        result = preview_recovery(plan_path, backup_path)
    except PlanError as e:
        _die(str(e))
    except PlanIOError as e:
        _die(str(e))

    assert result is not None
    out.print("[bold]Recovery diff:[/]")
    out.print(result.diff_summary)
    diff_output = result.diff_summary

    if not yes:
        out.print()
        confirm = typer.prompt("Restore plan.yaml from backup? (y/N)", default="n")
        if confirm.lower() != "y":
            out.print("[yellow]Cancelled.[/]")
            return

    try:
        apply_recovery(backup_path, plan_path)
    except PlanError as e:
        _die(str(e))
    except PlanIOError as e:
        _die(str(e))

    out.print("[green]Plan restored from backup.[/]")
    out.print(diff_output)
    out.print()
    out.print("[dim]Note: For git-based recovery, use `git log --all --follow --plan.yaml`[/]")
    out.print("[dim]to find commits and `git show <ref>:plan.yaml` to preview.[/]")


def checkpoint(
    agent: str | None = typer.Option(
        os.environ.get("VECTL_AGENT"),
        "--agent",
        "-a",
        help="Agent name (affects focus selection). (env: VECTL_AGENT)",
    ),
    next_limit: int = typer.Option(3, "--next", "-n", help="Max next steps."),
    include_guidance: bool = typer.Option(
        False, "--include-guidance", help="Include guidance (refs/templates)."
    ),
    lite: bool = typer.Option(
        True, "--lite/--full", help="Minimize output (omit metadata, redundant info)."
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON."),
    plan: Path | None = PlanOption,
) -> None:
    """Output machine-readable plan checkpoint (JSON).

    Source: FR "vectl checkpoint".
    Intent: Provide a deterministic, bounded snapshot for compaction/handoff.
    """
    import json

    from vectl.checkpoint import build_checkpoint

    p, def_h, _ = _load(plan)

    data = build_checkpoint(
        p,
        file_hash=def_h,
        agent=agent,
        next_limit=next_limit,
        include_guidance=include_guidance,
        lite=lite,
    )

    if pretty:
        out.print(json.dumps(data, indent=2))
    else:
        # Print raw JSON string to stdout
        print(json.dumps(data))


# ---------------------------------------------------------------------------
# cli.9: add-step + add-phase (architect commands)
# ---------------------------------------------------------------------------


# @shell_complexity: add-step preserves import-status parsing, template source validation, mutation, and guidance output.
