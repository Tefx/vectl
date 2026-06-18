"""Internal implementation slice split from mcp_core_tools.py.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
"""

from __future__ import annotations

from vectl.mcp_core_common import *

# @shell_complexity: Search preserves error, empty, truncation, and match formatting branches.
def vectl_search(
    pattern: str, phase_id: str | None = None, regex: bool = False
) -> Result[str, str]:
    """Search the plan.

    Args:
        pattern: Search pattern (substring or regex).
        phase_id: Restrict search to a specific phase.
        regex: If True, interpret pattern as regex.
    """
    plan, _ = _load()

    try:
        matches = search_plan(plan, pattern, phase_id=phase_id, use_regex=regex)
    except PlanError as e:
        return f"**Error:** {e}"

    if not matches:
        return f"*No matches for '{pattern}'.*"

    lines = [f"## Search: {pattern}\n", f"**{len(matches)} match(es)**\n"]
    for match in matches[:20]:
        loc = match.step_id or match.phase_id
        lines.append(f"- **{loc}** ({match.field}) — {match.snippet}")
    if len(matches) > 20:
        lines.append(f"\n  ... and {len(matches) - 20} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# vectl_mutate
# ---------------------------------------------------------------------------


# @shell_complexity: Mutate preserves one public schema across all plan mutation actions and compatibility aliases.
def vectl_mutate(
    action: Literal[
        "add-step",
        "add-steps",
        "edit-step",
        "remove-step",
        "move-step",
        "add-phase",
        "edit-phase",
        "edit-plan",
    ],
    phase_id: str = "",
    step_id: str = "",
    name: str = "",
    description: str = "",
    depends_on: list[str] | None = None,
    add_refs: list[str] | None = None,
    remove_refs: list[str] | None = None,
    refs: list[str] | None = None,
    target_phase: str = "",
    force: bool = False,
    gate: str = "",
    context: str = "",
    verification: str = "",
    evidence_template: str = "",
    project_guidance: str | None = None,
    strategy_ref: str | None = None,
    plan_context: str | None = None,
    status: str = "",
    evidence: str = "",
    skipped_reason: str = "",
    agent: str = "",
    new_step_id: str = "",
    steps: list[dict] | None = None,
) -> Result[str, str]:
    """Modify plan structure.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        action: The mutation action to perform.
        phase_id: Phase ID (for add-step, add-steps, add-phase, edit-phase).
        step_id: Step ID (for edit-step, remove-step, move-step). Also used
            as explicit --id for add-step/add-phase if provided.
        name: Name (for add-step, add-phase, edit-step, edit-phase).
        description: Description (for add-step, edit-step).
        depends_on: Dependencies (for add-step, add-phase, edit-phase, edit-step).
        add_refs: Refs to add (for edit-step).
        remove_refs: Refs to remove (for edit-step).
        refs: Refs to set (for edit-step, overrides add_refs/remove_refs).
        target_phase: Target phase (for move-step).
        force: Force remove (clean dep refs) for remove-step.
        gate: Gate criterion (for add-phase, edit-phase).
        context: Context (for edit-phase).
        verification: Verification (for add-step, edit-step).
        evidence_template: Optional short template for completion evidence
            (for add-step, edit-step).
        project_guidance: Project-level claim-time guidance (edit-plan only).
        strategy_ref: Plan-level strategy ref (edit-plan only).
        plan_context: Plan context (edit-plan only).
        status: Initial status for import (add-step only): pending, done, skipped.
        evidence: Evidence string (add-step only, required when status=done).
        skipped_reason: Skip reason (add-step only, required when status=skipped).
        agent: Advisory agent suggestion (add-step, edit-step). Which agent should
            work on this step. Not enforced; any agent can still claim any step.
        steps: List of step dicts for add-steps (batch add). Each dict supports:
            name (required), description/desc, depends_on/after, verification/verify,
            refs, status (pending/done/skipped), evidence, skipped_reason, agent, id.
            For intra-batch refs, use short slugs (e.g. "step-a" instead of "phase.step-a").
    """
    # Guard: block mutations in linked worktrees
    import os

    if not os.environ.get("VECTL_PLAN_PATH"):
        import sys

        root_mcp = sys.modules.get("vectl.mcp_server")
        linked_probe = getattr(root_mcp, "is_linked_worktree", is_linked_worktree)
        is_linked, main_root = linked_probe()
        if is_linked and main_root is not None:
            return (
                f"Mutate blocked: running in a linked worktree. "
                f"Plan mutations must be performed in the main worktree at {main_root}. "
                f"Override: set VECTL_PLAN_PATH={main_root}/plan.yaml"
            )
        if is_linked and main_root is None:
            return (
                "Mutate blocked: linked worktree detected but main worktree root "
                "could not be resolved from git output. "
                "Set VECTL_PLAN_PATH to the main worktree plan.yaml and retry."
            )

    plan, expected_def_hash = _load()

    try:
        if action == "add-step":
            if not phase_id or not name:
                return "**Error:** phase_id and name required for add-step."
            step_status = StepStatus(status) if status else None
            plan, new_id = add_step(
                plan,
                phase_id,
                name,
                description=description,
                depends_on=depends_on or [],
                step_id=step_id or None,
                verification=verification,
                evidence_template=evidence_template,
                status=step_status,
                evidence=evidence or None,
                skipped_reason=skipped_reason or None,
                agent=agent or None,
            )
            msg = f"**Added step:** {new_id} to phase '{phase_id}'"

        elif action == "add-steps":
            if not phase_id:
                return "**Error:** phase_id required for add-steps."
            if steps is None:
                return "**Error:** steps required for add-steps."
            # Translate MCP parameter names to internal names
            normalized_steps: list[dict[str, object]] = []
            for i, entry in enumerate(steps):
                if not isinstance(entry, dict):
                    return f"**Error:** Step {i} must be a dict."
                name_val = entry.get("name")
                if not name_val:
                    return f"**Error:** Step {i}: 'name' is required."
                step_dict: dict[str, object] = {"name": name_val}
                # description/desc
                if "description" in entry:
                    step_dict["desc"] = entry["description"]
                elif "desc" in entry:
                    step_dict["desc"] = entry["desc"]
                # depends_on/after -> after for internal
                if "depends_on" in entry:
                    step_dict["after"] = entry["depends_on"]
                elif "after" in entry:
                    step_dict["after"] = entry["after"]
                # verification/verify
                if "verification" in entry:
                    step_dict["verify"] = entry["verification"]
                elif "verify" in entry:
                    step_dict["verify"] = entry["verify"]
                # refs
                if "refs" in entry:
                    step_dict["refs"] = entry["refs"]
                # status
                if "status" in entry:
                    step_dict["status"] = entry["status"]
                # evidence
                if "evidence" in entry:
                    step_dict["evidence"] = entry["evidence"]
                # skipped_reason
                if "skipped_reason" in entry:
                    step_dict["skipped_reason"] = entry["skipped_reason"]
                # agent
                if "agent" in entry:
                    step_dict["agent"] = entry["agent"]
                # id
                if "id" in entry:
                    step_dict["id"] = entry["id"]
                normalized_steps.append(step_dict)
            plan, generated_ids = add_steps_bulk(plan, phase_id, normalized_steps)
            msg = f"**Added {len(generated_ids)} step(s) to phase '{phase_id}':\n"
            msg += "\n".join(f"  - {sid}" for sid in generated_ids)

        elif action == "edit-step":
            if not step_id:
                return "**Error:** step_id required for edit-step."
            plan = edit_step(
                plan,
                step_id,
                name=name or _SENTINEL,
                description=description or _SENTINEL,
                verification=verification or _SENTINEL,
                evidence_template=evidence_template or _SENTINEL,
                agent=agent if agent else _SENTINEL,
                depends_on=depends_on if depends_on is not None else _SENTINEL,
                add_refs=add_refs,
                remove_refs=remove_refs,
                refs=refs if refs is not None else _SENTINEL,
                new_step_id=new_step_id if new_step_id else _SENTINEL,
            )
            msg = f"**Updated step:** {step_id}"

        elif action == "remove-step":
            if not step_id:
                return "**Error:** step_id required for remove-step."
            plan = remove_step(plan, step_id, force=force)
            msg = f"**Removed step:** {step_id}"

        elif action == "move-step":
            if not step_id or not target_phase:
                return "**Error:** step_id and target_phase required for move-step."
            plan = move_step(plan, step_id, target_phase)
            msg = f"**Moved step:** {step_id} → phase '{target_phase}'"

        elif action == "add-phase":
            if not name:
                return "**Error:** name required for add-phase."
            plan, new_id = add_phase(
                plan,
                name,
                depends_on=depends_on or [],
                gate=gate,
                phase_id=phase_id or None,
            )
            msg = f"**Added phase:** {new_id}"

        elif action == "edit-phase":
            if not phase_id:
                return "**Error:** phase_id required for edit-phase."
            plan = edit_phase(
                plan,
                phase_id,
                name=name or _SENTINEL,
                context=context or _SENTINEL,
                gate=gate or _SENTINEL,
                depends_on=depends_on if depends_on is not None else _SENTINEL,
            )
            msg = f"**Updated phase:** {phase_id}"

        elif action == "edit-plan":
            if project_guidance is None and strategy_ref is None and plan_context is None:
                return (
                    "**Error:** Provide at least one of: project_guidance, strategy_ref, "
                    "plan_context."
                )
            plan = edit_plan(
                plan,
                project_guidance=project_guidance if project_guidance is not None else _SENTINEL,
                strategy_ref=strategy_ref if strategy_ref is not None else _SENTINEL,
                context=plan_context if plan_context is not None else _SENTINEL,
            )
            msg = "**Updated plan metadata**"

        else:
            return f"**Error:** Unknown action '{action}'."

        commit_target = step_id or phase_id or action
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: mutate {action} {commit_target}",
        )
    except PlanError as e:
        return f"**Error:** {e}"

    result = msg + "\n\n→ Use `vectl_search` to verify plan consistency."
    if lock_notice:
        result += f"\n\n{lock_notice}"
    return result



# @shell_complexity: Validate preserves grouped error/warning Markdown output.
def vectl_validate(check_refs: bool = False) -> Result[str, str]:
    """Validate plan structure and consistency (MCP read-only equivalent)."""
    plan, _ = _load()
    base_path = _plan_path().parent if check_refs else None
    issues = validate_plan(plan, check_refs=check_refs, base_path=base_path)

    parts: list[str] = ["## Validation"]
    if not issues:
        parts.append("✓ Plan is valid — 0 errors, 0 warnings")
        return "\n".join(parts)

    errors = [issue for issue in issues if not issue.is_warning]
    warnings = [issue for issue in issues if issue.is_warning]
    for issue in errors:
        parts.append(f"ERROR: {issue.message}")
    for issue in warnings:
        parts.append(f"WARN: {issue.message}")
    parts.append(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return "\n".join(parts)


def vectl_migrate_step_id(
    run_mode: Literal["dry-run", "apply"] = "dry-run"
) -> Result[dict[str, Any], str]:
    """Expose duplicate step-ID migration dry-run/apply surfaces.

    Source: step `step-id-migration-tooling.surfaces` and contract
    docs/contracts/duplicate-id-migration-contract.yaml dry_run_schema and
    migration_evidence_schema.
    """
    plan, expected_def_hash = _load()
    plan_path = _plan_path()
    claims_path = resolve_claims_path(plan_path)
    branch = get_current_branch()

    report, _ = build_duplicate_step_id_migration_dry_run(
        plan,
        claims_path=claims_path,
        branch=branch,
    )
    preview_evidence = build_duplicate_step_id_migration_evidence(
        command="vectl_migrate_step_id",
        command_args=["run_mode=dry-run"],
        run_mode="dry-run",
        migrated=False,
        report=report,
    )

    if run_mode == "dry-run":
        return {
            "ok": True,
            "status": "recommendation_only",
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }

    try:
        apply_result = apply_duplicate_step_id_migration(
            plan_path,
            expected_hash=expected_def_hash,
            claims_path=claims_path,
            branch=branch,
            command="vectl_migrate_step_id",
            command_args=["run_mode=apply"],
            run_mode="apply",
        )
    except CASConflictError as e:
        return {
            "ok": False,
            "status": "recommendation_only",
            "error": (
                "CAS conflict: plan.yaml was modified by another process since you loaded it. "
                "Re-read with `vectl_status` or `vectl_show`, then retry migration."
            ),
            "raw_error": str(e),
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }
    except PlanError as e:
        return {
            "ok": False,
            "status": "recommendation_only",
            "error": str(e),
            "report": report.to_dict(),
            "evidence": preview_evidence.to_dict(),
        }

    return {
        "ok": True,
        "status": "repair_applied",
        "migrated": apply_result.migrated,
        "new_plan_hash": apply_result.new_plan_hash,
        "report": apply_result.report.to_dict(),
        "evidence": apply_result.evidence.to_dict(),
    }


# ---------------------------------------------------------------------------
# vectl_guide
# ---------------------------------------------------------------------------



