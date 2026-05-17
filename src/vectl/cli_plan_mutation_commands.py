"""Internal implementation slice split from cli_plan.py."""

from __future__ import annotations

from vectl.cli_plan_common import *

def add_step_cmd(
    phase: str = typer.Option(..., "--phase", help="Phase ID to add step to."),
    name: str = typer.Option(..., "--name", help="Step name."),
    desc: str = typer.Option("", "--desc", "--description", help="Step description."),
    after: str | None = typer.Option(
        None, "--after", help="Comma-separated step IDs this depends on."
    ),
    verify: str = typer.Option("", "--verify", "--verification", help="Verification command."),
    refs: str | None = typer.Option(None, "--refs", help="Comma-separated ref paths."),
    evidence_template: str = typer.Option(
        "",
        "--evidence-template",
        help="Optional short template for completion evidence (inline).",
    ),
    evidence_template_file: Path | None = EvidenceTemplateFileOption,
    step_id: str | None = typer.Option(None, "--id", help="Explicit step ID (auto if omitted)."),
    import_status: str | None = typer.Option(
        None,
        "--status",
        help="Initial status for import: pending (default), done, skipped.",
    ),
    import_evidence: str | None = typer.Option(
        None,
        "--evidence",
        "-e",
        help="Evidence (required when --status=done).",
    ),
    skipped_reason: str | None = typer.Option(
        None,
        "--skipped-reason",
        help="Skip reason (required when --status=skipped).",
    ),
    step_agent: str | None = typer.Option(
        None,
        "--agent",
        help="Advisory agent suggestion (which agent should work on this step).",
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Add a new step to a phase.

    Supports importing steps with pre-set status for migration:
      --status done --evidence "commit abc"
      --status skipped --skipped-reason "absorbed into X"
    """
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    depends_on = [d.strip() for d in after.split(",") if d.strip()] if after else None
    refs_list = [r.strip() for r in refs.split(",") if r.strip()] if refs else None

    if evidence_template_file is not None and evidence_template:
        _die("Use only one of --evidence-template or --evidence-template-file.")
        return  # unreachable

    template_val = evidence_template
    if evidence_template_file is not None:
        template_val = evidence_template_file.read_text(encoding="utf-8")

    # Parse import status
    step_status: StepStatus | None = None
    if import_status is not None:
        try:
            step_status = StepStatus(import_status)
        except ValueError:
            _die(f"Invalid status '{import_status}'. Must be one of: pending, done, skipped.")
            return  # unreachable

    try:
        p, generated_id = add_step(
            p,
            phase,
            name,
            step_id=step_id,
            description=desc,
            depends_on=depends_on,
            verification=verify,
            evidence_template=template_val,
            refs=refs_list,
            status=step_status,
            evidence=import_evidence,
            skipped_reason=skipped_reason,
            agent=step_agent,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    _save_plan(p, plan_path, def_h, f"vectl: add step {generated_id}")

    out.print(f"[green]Added step:[/] {generated_id} → phase [bold]{phase}[/]")

    # Long slug warning (only for auto-generated IDs)
    if step_id is None and len(generated_id) > 40:
        out.print(f"[yellow]⚠ Slug is {len(generated_id)} chars. Use --id to set a shorter ID.[/]")

    out.print()
    out.print(f"[dim]→ vectl claim {generated_id} --agent <name>   Claim this step[/]")
    out.print(f"[dim]→ vectl add-step --phase {phase}       Add another step[/]")
    out.print(f"[dim]→ vectl status --phase {phase}         See all steps in phase[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


def add_phase_cmd(
    name: str = typer.Option(..., "--name", help="Phase name."),
    after: str | None = typer.Option(
        None, "--after", help="Comma-separated phase IDs this depends on."
    ),
    gate: str = typer.Option("", "--gate", help="Gate criterion text."),
    context: str = typer.Option("", "--context", "--ctx", help="Phase context/description."),
    phase_id: str | None = typer.Option(None, "--id", help="Explicit phase ID (auto if omitted)."),
    plan: Path | None = PlanOption,
) -> None:
    """Add a new phase to the plan."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    depends_on = [d.strip() for d in after.split(",") if d.strip()] if after else None

    try:
        p, generated_id = add_phase(
            p,
            name,
            phase_id=phase_id,
            depends_on=depends_on,
            gate=gate,
            context=context,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable, for type checker

    _save_plan(p, plan_path, def_h, f"vectl: add phase {generated_id}")

    # Show status based on auto-determined initial status
    ph = p.find_phase(generated_id)
    assert ph is not None  # just created it
    status_icon = _phase_icon(ph.status)

    out.print(f"[green]Added phase:[/] {generated_id} ({status_icon})")

    # Long slug warning (only for auto-generated IDs)
    if phase_id is None and len(generated_id) > 40:
        out.print(f"[yellow]⚠ Slug is {len(generated_id)} chars. Use --id to set a shorter ID.[/]")

    out.print()
    out.print(f"[dim]→ vectl add-step --phase {generated_id}       Add steps to this phase[/]")
    out.print(f"[dim]→ vectl status --phase {generated_id}         Inspect phase[/]")
    out.print("[dim]→ vectl status                      See full plan[/]")


# @shell_complexity: edit-plan preserves mutually-exclusive file/inline options and sentinel update semantics.
def edit_plan_cmd(
    project_guidance: str | None = typer.Option(
        None,
        "--project-guidance",
        help="Project-level claim-time guidance (inline; use '' to clear).",
    ),
    project_guidance_file: Path | None = ProjectGuidanceFileOption,
    strategy_ref: str | None = typer.Option(
        None,
        "--strategy-ref",
        help="Plan-level strategy reference (use '' to clear).",
    ),
    context: str | None = typer.Option(
        None,
        "--context",
        help="Plan context (inline; use '' to clear).",
    ),
    context_file: Path | None = ContextFileOption,
    plan: Path | None = PlanOption,
) -> None:
    """Edit plan-level metadata without manually editing plan.yaml.

    Source:
        - User instruction in this conversation (2026-02-12): "No manual YAML edits",
          and agreed `--*-file` approach.
    """
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL, edit_plan

    if project_guidance_file is not None and project_guidance is not None:
        _die("Use only one of --project-guidance or --project-guidance-file.")
        return  # unreachable
    if context_file is not None and context is not None:
        _die("Use only one of --context or --context-file.")
        return  # unreachable

    if (
        project_guidance is None
        and project_guidance_file is None
        and strategy_ref is None
        and context is None
        and context_file is None
    ):
        _die(
            "Nothing to edit. Provide at least one of: --project-guidance, "
            "--project-guidance-file, --strategy-ref, --context, --context-file."
        )
        return  # unreachable

    p, def_h, plan_path = _load(plan)

    pg_val = project_guidance
    if project_guidance_file is not None:
        pg_val = project_guidance_file.read_text(encoding="utf-8")

    ctx_val = context
    if context_file is not None:
        ctx_val = context_file.read_text(encoding="utf-8")

    try:
        p = edit_plan(
            p,
            project_guidance=pg_val if pg_val is not None else _SENTINEL,
            strategy_ref=strategy_ref if strategy_ref is not None else _SENTINEL,
            context=ctx_val if ctx_val is not None else _SENTINEL,
        )
    except PlanError as e:
        _die(str(e))
        return  # unreachable

    _save_plan(p, plan_path, def_h, "vectl: edit plan metadata")
    out.print("[green]Updated plan metadata[/]")
    out.print("[dim]→ vectl status                      See plan overview[/]")


# ---------------------------------------------------------------------------
# cli.11: edit-step, remove-step, move-step (architect mutations)
# ---------------------------------------------------------------------------


# @shell_orchestration: Edit-step option validation is coupled to Typer CLI diagnostics.
def _ensure_edit_step_has_change(
    *,
    name: str | None,
    desc: str | None,
    verify: str | None,
    step_agent: str | None,
    add_deps: list[str] | None,
    rm_deps: list[str] | None,
    add_refs: list[str] | None,
    rm_refs: list[str] | None,
    evidence_template: str | None,
    new_step_id: str | None,
    evidence_template_file: Path | None,
) -> None:
    if any((name, desc, verify, step_agent, add_deps, rm_deps, add_refs, rm_refs)):
        return
    if evidence_template is not None or new_step_id is not None or evidence_template_file is not None:
        return
    _die(
        "Nothing to edit. Provide at least one of --name, --desc, --verify, --agent, "
        "--add-dep, --rm-dep, --add-ref, --rm-ref, --evidence_template, "
        "--new-id, "
        "--evidence_template_file."
    )


# @shell_complexity: edit-step preserves mutually-exclusive template sources, sentinel semantics, dependency/ref edits, and rename output.
def edit_step_cmd(
    step_id: str = typer.Argument(help="Step ID to edit."),
    name: str | None = typer.Option(None, "--name", help="New step name."),
    desc: str | None = typer.Option(None, "--desc", "--description", help="New description."),
    verify: str | None = typer.Option(
        None, "--verify", "--verification", help="New verification command."
    ),
    step_agent: str | None = typer.Option(
        None, "--agent", help="New agent suggestion (use '' to clear)."
    ),
    add_dep: str | None = typer.Option(
        None, "--add-dep", help="Comma-separated step IDs to add as dependencies."
    ),
    rm_dep: str | None = typer.Option(
        None, "--rm-dep", help="Comma-separated step IDs to remove from dependencies."
    ),
    add_ref: str | None = typer.Option(None, "--add-ref", help="Comma-separated ref paths to add."),
    rm_ref: str | None = typer.Option(
        None, "--rm-ref", help="Comma-separated ref paths to remove."
    ),
    evidence_template: str | None = typer.Option(
        None,
        "--evidence-template",
        help="New completion evidence template (inline; use '' to clear).",
    ),
    new_step_id: str | None = typer.Option(
        None,
        "--new-id",
        help="Rename this step to a new globally unique ID.",
    ),
    evidence_template_file: Path | None = EvidenceTemplateFileOption,
    plan: Path | None = PlanOption,
) -> None:
    """Edit a step's metadata."""
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL

    p, def_h, plan_path = _load(plan)

    add_deps = [d.strip() for d in add_dep.split(",") if d.strip()] if add_dep else None
    rm_deps = [d.strip() for d in rm_dep.split(",") if d.strip()] if rm_dep else None
    add_refs = [r.strip() for r in add_ref.split(",") if r.strip()] if add_ref else None
    rm_refs = [r.strip() for r in rm_ref.split(",") if r.strip()] if rm_ref else None

    _ensure_edit_step_has_change(
        name=name,
        desc=desc,
        verify=verify,
        step_agent=step_agent,
        add_deps=add_deps,
        rm_deps=rm_deps,
        add_refs=add_refs,
        rm_refs=rm_refs,
        evidence_template=evidence_template,
        new_step_id=new_step_id,
        evidence_template_file=evidence_template_file,
    )

    if evidence_template_file is not None and evidence_template is not None:
        _die("Use only one of --evidence-template or --evidence-template-file.")
        return  # unreachable

    # Agent: None means "not provided" (sentinel); "" means "clear"
    agent_val = _SENTINEL
    if step_agent is not None:
        agent_val = None if step_agent == "" else step_agent  # type: ignore[assignment]

    template_val = _SENTINEL
    if evidence_template_file is not None:
        template_val = evidence_template_file.read_text(encoding="utf-8")  # type: ignore[assignment]
    elif evidence_template is not None:
        template_val = evidence_template  # type: ignore[assignment]

    try:
        p = edit_step(
            p,
            step_id,
            name=name if name is not None else _SENTINEL,
            description=desc if desc is not None else _SENTINEL,
            verification=verify if verify is not None else _SENTINEL,
            evidence_template=template_val,
            agent=agent_val,
            add_deps=add_deps,
            remove_deps=rm_deps,
            add_refs=add_refs,
            remove_refs=rm_refs,
            new_step_id=new_step_id if new_step_id is not None else _SENTINEL,
        )
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: edit step {step_id}")

    out.print(f"[green]Edited:[/] {step_id}")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# @shell_complexity: edit-phase preserves no-op validation, dependency parsing, sentinel semantics, and output hints.
def edit_phase_cmd(
    phase_id: str = typer.Argument(help="Phase ID to edit."),
    name: str | None = typer.Option(None, "--name", help="New phase name."),
    context: str | None = typer.Option(None, "--context", "--ctx", help="New context."),
    gate: str | None = typer.Option(None, "--gate", help="New gate criterion."),
    add_dep: str | None = typer.Option(
        None, "--add-dep", help="Comma-separated phase IDs to add as dependencies."
    ),
    rm_dep: str | None = typer.Option(
        None, "--rm-dep", help="Comma-separated phase IDs to remove from dependencies."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Edit a phase's metadata."""
    _check_not_linked_worktree(plan)
    from vectl.core import _SENTINEL

    p, def_h, plan_path = _load(plan)

    add_deps = [d.strip() for d in add_dep.split(",") if d.strip()] if add_dep else None
    rm_deps = [d.strip() for d in rm_dep.split(",") if d.strip()] if rm_dep else None

    if name is None and context is None and gate is None and not add_deps and not rm_deps:
        _die(
            "Nothing to edit. Provide at least one of --name, --context, --gate, "
            "--add-dep, --rm-dep."
        )
        return  # unreachable

    try:
        p = edit_phase(
            p,
            phase_id,
            name=name if name is not None else _SENTINEL,
            context=context if context is not None else _SENTINEL,
            gate=gate if gate is not None else _SENTINEL,
            add_deps=add_deps,
            remove_deps=rm_deps,
        )
    except PlanError as e:
        _die(str(e))

    _save_plan(p, plan_path, def_h, f"vectl: edit phase {phase_id}")

    out.print(f"[green]Edited phase:[/] {phase_id}")
    out.print()
    out.print(f"[dim]→ vectl show {phase_id}[/]")
    out.print("[dim]→ vectl search <pattern>              Check consistency[/]")


def remove_step_cmd(
    step_id: str = typer.Argument(help="Step ID to remove (must be pending)."),
    force: bool = typer.Option(
        False, "--force", help="Remove even if other steps depend on it (cleans up dep refs)."
    ),
    plan: Path | None = PlanOption,
) -> None:
    """Remove a pending step from its phase."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)
    try:
        p = remove_step(p, step_id, force=force)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: remove step {step_id}")
    out.print(f"[yellow]Removed:[/] {step_id}")
    if force:
        out.print("[dim]  Dependency references cleaned up in remaining steps.[/]")
    out.print()
    out.print("[dim]→ vectl status                    Plan overview[/]")
    out.print("[dim]→ vectl next                      See claimable steps[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


def move_step_cmd(
    step_id: str = typer.Argument(help="Step ID to move (must be pending)."),
    to_phase: str = typer.Option(..., "--to-phase", help="Target phase ID."),
    plan: Path | None = PlanOption,
) -> None:
    """Move a pending step to a different phase."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)

    # Capture deps before move (move_step clears them — lossy operation)
    found = p.find_step(step_id)
    cleared_deps = list(found[1].depends_on) if found else []

    try:
        p = move_step(p, step_id, to_phase)
    except PlanError as e:
        _die(str(e))
    _save_plan(p, plan_path, def_h, f"vectl: move step {step_id} to {to_phase}")
    out.print(f"[green]Moved:[/] {step_id} → {to_phase}")
    if cleared_deps:
        out.print(f"[yellow]⚠ Cleared {len(cleared_deps)} dep(s):[/] {', '.join(cleared_deps)}")
        out.print("[dim]  Dependencies are phase-scoped. Re-add in target phase if needed.[/]")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")
    out.print(f"[dim]→ vectl show {to_phase}[/]")
    out.print("[dim]→ vectl search <pattern>            Check consistency[/]")


# ---------------------------------------------------------------------------
# cli.12: unlock (explicit phase unlock)
# ---------------------------------------------------------------------------


