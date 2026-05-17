"""Internal implementation slice split from mcp_core_tools.py."""

from __future__ import annotations

from vectl.mcp_core_common import *

class ClaimStepData(BaseModel):
    """Claimed step summary.

    Source: FR R6 — MCP structured output should include guidance and step metadata.
    """

    step_id: str
    step_name: str
    phase_id: str
    phase_name: str
    claimed_by: str
    suggested_agent: str | None = None
    affinity_override: bool = False


class ClaimResponseEnvelope(BaseModel):
    """Structured response for vectl_claim.

    Source: FR R6 — return structured fields; clients can decide how to display.
    Source: docs/ARCHITECTURAL-DEMOLITION-REMEDIATION-PLAN.md DEM-004 —
    keep MCP as a thin transport envelope around lifecycle-owned claim metadata.
    """

    ok: bool
    markdown: str
    claimed: ClaimStepData | None = None
    guidance: GuidancePayload | None = None
    affinity_warning: AffinityWarningMetadata | None = None
    affinity_override: AffinityOverrideMetadata | None = None
    error: str | None = None
    error_code: str | None = None
    duplicate_step_id_recommendation: dict[str, Any] | None = None
    claim_conflict: ClaimConflictMetadata | None = None


# @shell_complexity: Claim preserves conflict, affinity, guidance, duplicate-ID, and lock-notice compatibility.
def vectl_claim(
    agent: str, step_id: str | None = None, guidance: bool = True, force: bool = False
) -> Result[dict[str, Any], str]:
    """Claim a step.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        agent: Agent name claiming the step.
        step_id: Step ID to claim. If omitted, auto-selects first available.
        guidance: If True, include claim-time guidance (refs + evidence template).
        force: If True, override exclusive affinity violations (sets audit trail).

    Returns:
        Structured result with claimed step, affinity warnings, and guidance.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    if step_id is None:
        available = get_next_steps(plan, agent=agent)
        if not available:
            err = "No steps available to claim."
            return ClaimResponseEnvelope(
                ok=False,
                markdown=f"**Error:** {err}",
                error=err,
            ).model_dump(mode="json", exclude_none=True)
        step_id = available[0].id

    lock_notice: str = ""
    try:
        plan, result = claim_step(plan, step_id, agent, force=force, claims_path=claims_path)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: claim step {step_id} by {agent}",
        )
    except ClaimConflictError as e:
        # Claim conflict: existing claim record blocking this attempt
        err = str(e)
        conflict_md_lines = [
            f"**Claim Conflict:** {e.step_id} is already claimed on branch '{e.branch}'",
            f"**Claimed by:** {e.claimant}",
            f"**Claimed at:** {e.claimed_at}",
            "",
            "**Next Steps:**",
            f"1. Inspect: `vectl_show` step_id={e.step_id}",
            "2. If this is a stale/ghost claim (common after agent restarts), "
            "run `vectl_repair_claims()` to fix it — this is routine maintenance, not an error.",
        ]
        return ClaimResponseEnvelope(
            ok=False,
            markdown="\n".join(conflict_md_lines),
            error=err,
            error_code="claim_conflict",
            claim_conflict=e.metadata,
        ).model_dump(mode="json", exclude_none=True)
    except AffinityError as e:
        # RFC: docs/RFC-affinity.md
        # Exclusive affinity violation
        err = str(e)
        return ClaimResponseEnvelope(
            ok=False,
            markdown=f"**Affinity Error:** {err}",
            error=err,
            error_code="affinity_violation",
        ).model_dump(mode="json", exclude_none=True)
    except PlanError as e:
        err = str(e)
        error_code = None
        if "error_code=duplicate_step_id_ambiguous_target" in err:
            error_code = "duplicate_step_id_ambiguous_target"
        elif "error_code=duplicate_step_id_auto_migrate_required" in err:
            error_code = "duplicate_step_id_auto_migrate_required"

        recommendation_payload: dict[str, Any] | None = None
        if error_code == "duplicate_step_id_ambiguous_target" and step_id is not None:
            recommendation = get_duplicate_step_id_recommendation(plan, step_id)
            if recommendation is not None:
                recommendation_payload = {
                    "type": recommendation.type,
                    "step_id": recommendation.step_id,
                    "duplicates": [
                        {"phase": duplicate.phase} for duplicate in recommendation.duplicates
                    ],
                    "resolution_path": {
                        "explicit_phase": recommendation.resolution_path.explicit_phase,
                        "auto_migrate_flag": recommendation.resolution_path.auto_migrate_flag,
                        "migration_tool": recommendation.resolution_path.migration_tool,
                    },
                }

        markdown_lines = [f"**Error:** {err}"]
        if recommendation_payload is not None:
            markdown_lines.extend(
                [
                    "",
                    "Structured repair recommendation:",
                    f"- type: {recommendation_payload['type']}",
                    f"- step_id: {recommendation_payload['step_id']}",
                ]
            )
            duplicates = recommendation_payload["duplicates"]
            for duplicate in duplicates:
                markdown_lines.append(f"  - phase: {duplicate['phase']}")

            resolution_path = recommendation_payload["resolution_path"]
            markdown_lines.extend(
                [
                    "- resolution_path:",
                    f"  - explicit_phase: {resolution_path['explicit_phase']}",
                    f"  - auto_migrate_flag: {resolution_path['auto_migrate_flag']}",
                    f"  - migration_tool: {resolution_path['migration_tool']}",
                ]
            )

        markdown = "\n".join(markdown_lines)
        return ClaimResponseEnvelope(
            ok=False,
            markdown=markdown,
            error=err,
            error_code=error_code,
            duplicate_step_id_recommendation=recommendation_payload,
        ).model_dump(mode="json", exclude_none=True)

    found = plan.find_step(step_id)
    if found:
        phase, step = found

        claimed = ClaimStepData(
            step_id=step.id,
            step_name=step.name,
            phase_id=phase.id,
            phase_name=phase.name,
            claimed_by=agent,
            suggested_agent=step.agent,
            affinity_override=step.affinity_override,
        )

        md_lines: list[str] = [
            f"**Claimed:** {step.id} — {step.name}",
            f"**Phase:** {phase.id}",
            f"**Claimed by:** {agent}",
        ]
        if step.agent:
            md_lines.append(f"**Suggested agent:** {step.agent}")
        md_lines.append("")
        md_lines.append("→ Use `vectl_complete` with evidence when done.")
        md_lines.append("→ Use `vectl_lifecycle` action=defer to release.")
        md_lines.append("")

        # RFC: docs/RFC-affinity.md
        # Build affinity warning/override data
        affinity_warning_data: AffinityWarningMetadata | None = None
        affinity_override_data: AffinityOverrideMetadata | None = None

        if result.warning_message:
            if result.affinity_override:
                affinity_override_data = result.affinity_override_metadata
                md_lines.append(f"⚠️ **Affinity Override:** {result.warning_message}")
            elif result.affinity_warning:
                affinity_warning_data = result.affinity_warning_metadata
                md_lines.append(f"⚠️ **Affinity Warning:** {result.warning_message}")
            md_lines.append("")

        gp: GuidancePayload | None = None
        if guidance:
            gp = build_claim_guidance(plan, phase, step)
            md_lines.append(gp.markdown.rstrip())

        markdown = "\n".join(md_lines).rstrip() + "\n"
        if lock_notice:
            markdown = markdown.rstrip("\n") + f"\n\n{lock_notice}\n"
        return ClaimResponseEnvelope(
            ok=True,
            markdown=markdown,
            claimed=claimed,
            guidance=gp,
            affinity_warning=affinity_warning_data,
            affinity_override=affinity_override_data,
        ).model_dump(mode="json", exclude_none=True)

    fallback_markdown = f"Claimed: {step_id}\n"
    if lock_notice:
        fallback_markdown = fallback_markdown.rstrip("\n") + f"\n\n{lock_notice}\n"
    return ClaimResponseEnvelope(
        ok=True,
        markdown=fallback_markdown,
    ).model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Tool 4: vectl_complete
# ---------------------------------------------------------------------------


# @shell_complexity: Complete preserves mutation, phase completion, next-step preview, and lock notice output.
def vectl_complete(step_id: str, evidence: str) -> Result[str, str]:
    """Complete a step.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        step_id: The step ID to complete.
        evidence: Description of what was done and verification.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    try:
        plan = complete_step(plan, step_id, evidence, claims_path=claims_path)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: complete step {step_id}",
        )
    except PlanError as e:
        return f"**Error:** {e}"

    # Check if phase completed
    found = plan.find_step(step_id)
    parts = [f"**Completed:** {step_id}"]
    if found:
        phase, _ = found
        if phase.status == PhaseStatus.DONE:
            parts.append(f"**Phase '{phase.id}' is now DONE!**")

    # Show next available - use _get_next_steps_with_phase for correct phase tracking
    available = _get_next_steps_with_phase(plan)
    if available:
        parts.append("\n**Next available:**")
        for phase, step in available[:3]:
            parts.append(_fmt_step(plan, step, phase.id))
        if len(available) > 3:
            parts.append(f"  ... and {len(available) - 3} more")

    if lock_notice:
        parts.append(f"\n{lock_notice}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool 5: vectl_lifecycle
# ---------------------------------------------------------------------------


# @shell_complexity: Lifecycle intentionally maps five public actions to legacy Markdown responses.
def vectl_lifecycle(
    action: Literal["defer", "reject", "skip", "skip-phase", "complete-phase"],
    target: str,
    reason: str = "",
    evidence: str = "",
    reviewer: str = "",
    force: bool = False,
) -> Result[str, str]:
    """Change step/phase lifecycle state.

    Lock consistency is maintained automatically. After any write operation, lock
    status is recalculated.

    Args:
        action: One of: defer, reject, skip, skip-phase, complete-phase.
        target: Step ID (for defer/reject/skip) or phase ID (for skip-phase/complete-phase).
        reason: Required for reject, skip, skip-phase. For skip/skip-phase
            must be: superseded, irrelevant, absorbed, deprioritized.
        evidence: Required for complete-phase.
        reviewer: Optional reviewer name for reject.
        force: For skip-phase, allow skipping a locked phase with remaining steps.
            Empty phases (0 steps) are always allowed regardless of lock.
    """
    plan, expected_def_hash = _load()
    claims_path = resolve_claims_path(_plan_path())

    try:
        if action == "defer":
            plan = defer_step(plan, target, claims_path=claims_path)
            msg = f"**Deferred:** {target} → back to pending"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: defer step {target}",
            )
        elif action == "reject":
            if not reason:
                return "**Error:** reason is required for reject."
            plan = reject_step(plan, target, reason, reviewer)
            msg = f"**Rejected:** {target} — {reason}"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: reject step {target}",
            )
        elif action == "skip":
            if not reason:
                return (
                    "**Error:** reason is required for skip "
                    "(superseded/irrelevant/absorbed/deprioritized)."
                )
            plan = skip_step(plan, target, reason)
            msg = f"**Skipped:** {target} — {reason}"
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: skip step {target}",
            )
        elif action == "skip-phase":
            if not reason:
                return "**Error:** reason is required for skip-phase."
            plan, skipped_ids = skip_phase(plan, target, reason, force=force)
            msg = f"**Skipped phase '{target}':** {len(skipped_ids)} steps skipped"
            if skipped_ids:
                msg += "\n" + "\n".join(f"  - {sid}" for sid in skipped_ids)
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: skip phase {target}",
            )
        elif action == "complete-phase":
            if not evidence:
                return "**Error:** evidence is required for complete-phase."
            plan, unlocked = complete_phase(plan, target, evidence)
            msg = f"**Completed phase '{target}':** {len(unlocked)} phase(s) unlocked"
            if unlocked:
                msg += "\n" + "\n".join(f"  - {pid}" for pid in unlocked)
            lock_notice = _save_plan(
                plan,
                expected_def_hash,
                f"mcp: complete phase {target}",
            )
        else:
            return (
                f"**Error:** Unknown action '{action}'. Use: defer, reject, skip, "
                "skip-phase, complete-phase."
            )
    except PlanError as e:
        return f"**Error:** {e}"

    if lock_notice:
        msg += f"\n\n{lock_notice}"
    return msg


# ---------------------------------------------------------------------------
# Tool 6: vectl_search
# ---------------------------------------------------------------------------


