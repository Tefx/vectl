"""Internal implementation slice split from mcp_core_tools.py.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
"""
from __future__ import annotations
# Compatibility wildcard imports expose the shared mutation slice symbols.
# ruff: noqa: F403, F405
import json
import re
from typing import cast
from pydantic import ValidationError

from vectl.core.diagnostics import (
    format_final_validation_failure,
    format_unexpected_final_validation_failure,
    normalize_model_validation_issues,
)
from vectl.mcp_core_common import *
from vectl.mcp_core_common import _save_admitted_plan_receipt
from vectl.mcp_mutation_contracts import (
    BATCH_FIELDS,
    BATCH_REQUIRED_FIELDS,
    BATCH_STEP_FIELDS,
    BATCH_STEP_REQUIRED_FIELDS,
    UNKNOWN_FIELD_HINTS,
    BatchMutationRequest,
    MutationAction,
)
# ---------------------------------------------------------------------------
# vectl_mutate
# ---------------------------------------------------------------------------


# Backward-compatible private names used by focused contract tests and diagnostics.
_BATCH_FIELDS = BATCH_FIELDS
_BATCH_REQUIRED_FIELDS = BATCH_REQUIRED_FIELDS
_BATCH_STEP_FIELDS = BATCH_STEP_FIELDS
_BATCH_STEP_REQUIRED_FIELDS = BATCH_STEP_REQUIRED_FIELDS
_MUTATION_ACTIONS = frozenset(_BATCH_FIELDS)
_STRING_FIELDS = frozenset(
    {
        "phase_id",
        "step_id",
        "new_step_id",
        "name",
        "description",
        "target_phase",
        "gate",
        "context",
        "verification",
        "evidence_template",
        "project_guidance",
        "strategy_ref",
        "plan_context",
        "status",
        "evidence",
        "skipped_reason",
        "agent",
    }
)
_STRING_LIST_FIELDS = frozenset({"depends_on", "add_refs", "remove_refs", "refs"})


# @shell_complexity: Strict action-specific schemas keep malformed batches before Plan load.
def _validate_batch_requests(requests: object) -> Result[list[dict[str, Any]], str]:
    """Validate strict batch request schemas before loading persistent state."""
    if not isinstance(requests, list) or not requests:
        raise PlanError("input_shape: requests must be a non-empty list.")

    validated: list[dict[str, Any]] = []
    for index, raw_request in enumerate(requests):
        if not isinstance(raw_request, dict):
            raise PlanError(f"request[{index}]: request must be a mapping.")
        request = dict(raw_request)
        action = request.get("action")
        if not isinstance(action, str) or action not in _MUTATION_ACTIONS:
            raise PlanError(f"request[{index}]: unknown or missing action '{action}'.")

        unknown = sorted(set(request) - _BATCH_FIELDS[action])
        if unknown:
            hints = dict.fromkeys(
                UNKNOWN_FIELD_HINTS[(action, field)]
                for field in unknown
                if (action, field) in UNKNOWN_FIELD_HINTS
            )
            hint = f" {' '.join(hints)}" if hints else ""
            raise PlanError(
                f"request[{index}] {action}: unsupported field(s): {', '.join(unknown)}; "
                f"allowed fields: {', '.join(sorted(_BATCH_FIELDS[action]))}.{hint}"
            )

        for field in _STRING_FIELDS & request.keys():
            if not isinstance(request[field], str):
                raise PlanError(f"request[{index}] {action}: '{field}' must be a string.")
        for field in _STRING_LIST_FIELDS & request.keys():
            value = request[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise PlanError(
                    f"request[{index}] {action}: '{field}' must be a list of strings."
                )
        if "force" in request and not isinstance(request["force"], bool):
            raise PlanError(f"request[{index}] {action}: 'force' must be a boolean.")

        required_fields = tuple(
            field for field in _BATCH_REQUIRED_FIELDS[action] if field != "action"
        )
        missing = [field for field in required_fields if not request.get(field)]
        if missing:
            explicit = " explicit ID" if action in {"add-phase", "add-step"} else ""
            raise PlanError(
                f"request[{index}] {action}:{explicit} "
                f"{', '.join(repr(field) for field in missing)} required."
            )
        if action == "edit-plan" and not any(
            field in request for field in ("project_guidance", "strategy_ref", "plan_context")
        ):
            raise PlanError(
                f"request[{index}] edit-plan: provide project_guidance, strategy_ref, "
                "or plan_context."
            )
        if action == "add-steps":
            entries = request["steps"]
            if not isinstance(entries, list):
                raise PlanError(f"request[{index}] add-steps: 'steps' must be a list.")
            for step_index, entry in enumerate(entries):
                prefix = f"request[{index}] add-steps: step[{step_index}]"
                if not isinstance(entry, dict):
                    raise PlanError(f"{prefix} must be a mapping.")
                step_unknown = sorted(set(entry) - _BATCH_STEP_FIELDS)
                if step_unknown:
                    hints = dict.fromkeys(
                        UNKNOWN_FIELD_HINTS[(action, field)]
                        for field in step_unknown
                        if (action, field) in UNKNOWN_FIELD_HINTS
                    )
                    hint = f" {' '.join(hints)}" if hints else ""
                    raise PlanError(
                        f"{prefix}: unsupported field(s): {', '.join(step_unknown)}; "
                        f"allowed fields: {', '.join(sorted(_BATCH_STEP_FIELDS))}.{hint}"
                    )
                for field in (
                    "id",
                    "name",
                    "description",
                    "desc",
                    "verification",
                    "verify",
                    "status",
                    "evidence",
                    "skipped_reason",
                    "agent",
                ):
                    if field in entry and not isinstance(entry[field], str):
                        raise PlanError(f"{prefix} '{field}' must be a string.")
                for field in ("depends_on", "refs"):
                    if field in entry and (
                        not isinstance(entry[field], list)
                        or not all(isinstance(item, str) for item in entry[field])
                    ):
                        raise PlanError(f"{prefix} '{field}' must be a list of strings.")
                after = entry.get("after")
                if after is not None and not (
                    isinstance(after, str)
                    or (
                        isinstance(after, list)
                        and all(isinstance(item, str) for item in after)
                    )
                ):
                    raise PlanError(f"{prefix} 'after' must be a string or list of strings.")
                missing_step_fields = [
                    field for field in _BATCH_STEP_REQUIRED_FIELDS if not entry.get(field)
                ]
                if missing_step_fields:
                    missing_field = missing_step_fields[0]
                    if missing_field == "id":
                        raise PlanError(f"{prefix} explicit ID 'id' is required.")
                    raise PlanError(f"{prefix} '{missing_field}' is required.")
        validated.append(request)
    return validated


# @shell_complexity: Alias translation preserves the existing add-steps MCP contract.
def _normalize_bulk_steps(steps: list[dict[str, Any]]) -> Result[list[dict[str, object]], str]:
    """Translate MCP add-steps aliases to the existing core representation."""
    normalized_steps: list[dict[str, object]] = []
    for i, entry in enumerate(steps):
        if not isinstance(entry, dict):
            raise PlanError(f"Step {i} must be a dict.")
        name_val = entry.get("name")
        if not name_val:
            raise PlanError(f"Step {i}: 'name' is required.")
        step_dict: dict[str, object] = {"name": name_val}
        if "description" in entry:
            step_dict["desc"] = entry["description"]
        elif "desc" in entry:
            step_dict["desc"] = entry["desc"]
        if "depends_on" in entry:
            step_dict["after"] = entry["depends_on"]
        elif "after" in entry:
            step_dict["after"] = entry["after"]
        if "verification" in entry:
            step_dict["verify"] = entry["verification"]
        elif "verify" in entry:
            step_dict["verify"] = entry["verify"]
        for field in ("refs", "status", "evidence", "skipped_reason", "agent", "id"):
            if field in entry:
                step_dict[field] = entry[field]
        normalized_steps.append(step_dict)
    return normalized_steps


# @shell_complexity: One dispatcher preserves all existing mutation action semantics.
def _apply_mutation_request(
    plan: Plan, request: dict[str, Any]
) -> Result[tuple[Plan, str], str]:
    """Apply one existing mutation action to an in-memory Plan."""
    action = request["action"]
    phase_id = request.get("phase_id", "")
    step_id = request.get("step_id", "")
    name = request.get("name", "")
    description = request.get("description", "")
    depends_on = request.get("depends_on")
    verification = request.get("verification", "")
    evidence_template = request.get("evidence_template", "")
    agent = request.get("agent", "")

    if action == "add-step":
        if not phase_id or not name:
            raise PlanError("phase_id and name required for add-step.")
        status = request.get("status", "")
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
            refs=request.get("refs"),
            status=step_status,
            evidence=request.get("evidence") or None,
            skipped_reason=request.get("skipped_reason") or None,
            agent=agent or None,
        )
        return plan, f"**Added step:** {new_id} to phase '{phase_id}'"

    if action == "add-steps":
        if not phase_id:
            raise PlanError("phase_id required for add-steps.")
        steps = request.get("steps")
        if steps is None:
            raise PlanError("steps required for add-steps.")
        normalized_steps = _normalize_bulk_steps(steps)
        plan, generated_ids = add_steps_bulk(plan, phase_id, normalized_steps)
        msg = f"**Added {len(generated_ids)} step(s) to phase '{phase_id}':\n"
        return plan, msg + "\n".join(f"  - {sid}" for sid in generated_ids)

    if action == "edit-step":
        if not step_id:
            raise PlanError("step_id required for edit-step.")
        plan = edit_step(
            plan,
            step_id,
            name=name or _SENTINEL,
            description=description or _SENTINEL,
            verification=verification or _SENTINEL,
            evidence_template=evidence_template or _SENTINEL,
            agent=agent if agent else _SENTINEL,
            depends_on=depends_on if depends_on is not None else _SENTINEL,
            add_refs=request.get("add_refs"),
            remove_refs=request.get("remove_refs"),
            refs=request["refs"] if request.get("refs") is not None else _SENTINEL,
            new_step_id=request.get("new_step_id") or _SENTINEL,
        )
        return plan, f"**Updated step:** {step_id}"

    if action == "remove-step":
        if not step_id:
            raise PlanError("step_id required for remove-step.")
        plan = remove_step(plan, step_id, force=request.get("force", False))
        return plan, f"**Removed step:** {step_id}"

    if action == "move-step":
        target_phase = request.get("target_phase", "")
        if not step_id or not target_phase:
            raise PlanError("step_id and target_phase required for move-step.")
        plan = move_step(plan, step_id, target_phase)
        return plan, f"**Moved step:** {step_id} → phase '{target_phase}'"

    if action == "add-phase":
        if not name:
            raise PlanError("name required for add-phase.")
        plan, new_id = add_phase(
            plan,
            name,
            depends_on=depends_on or [],
            gate=request.get("gate", ""),
            phase_id=phase_id or None,
        )
        return plan, f"**Added phase:** {new_id}"

    if action == "edit-phase":
        if not phase_id:
            raise PlanError("phase_id required for edit-phase.")
        plan = edit_phase(
            plan,
            phase_id,
            name=name or _SENTINEL,
            context=request.get("context") or _SENTINEL,
            gate=request.get("gate") or _SENTINEL,
            depends_on=depends_on if depends_on is not None else _SENTINEL,
        )
        return plan, f"**Updated phase:** {phase_id}"

    if action == "edit-plan":
        project_guidance = request.get("project_guidance")
        strategy_ref = request.get("strategy_ref")
        plan_context = request.get("plan_context")
        if project_guidance is None and strategy_ref is None and plan_context is None:
            raise PlanError(
                "Provide at least one of: project_guidance, strategy_ref, plan_context."
            )
        plan = edit_plan(
            plan,
            project_guidance=project_guidance if project_guidance is not None else _SENTINEL,
            strategy_ref=strategy_ref if strategy_ref is not None else _SENTINEL,
            context=plan_context if plan_context is not None else _SENTINEL,
        )
        return plan, "**Updated plan metadata**"

    raise PlanError(f"Unknown action '{action}'.")


def _linked_worktree_mutation_error() -> Result[str | None, str]:
    """Return the existing linked-worktree guard message, if mutation is blocked."""
    import os

    if os.environ.get("VECTL_PLAN_PATH"):
        return None

    import sys

    root_mcp = sys.modules.get("vectl.mcp_server")
    linked_probe = getattr(root_mcp, "is_linked_worktree", is_linked_worktree)
    is_linked, main_root = linked_probe()
    if is_linked and main_root is not None:
        return (
            "Mutate blocked: running in a linked worktree. "
            f"Plan mutations must be performed in the main worktree at {main_root}. "
            f"Override: set VECTL_PLAN_PATH={main_root}/plan.yaml"
        )
    if is_linked:
        return (
            "Mutate blocked: linked worktree detected but main worktree root "
            "could not be resolved from git output. "
            "Set VECTL_PLAN_PATH to the main worktree plan.yaml and retry."
        )
    return None


def _single_request(
    action: MutationAction,
    *,
    phase_id: str,
    step_id: str,
    name: str,
    description: str,
    depends_on: list[str] | None,
    add_refs: list[str] | None,
    remove_refs: list[str] | None,
    refs: list[str] | None,
    target_phase: str,
    force: bool,
    gate: str,
    context: str,
    verification: str,
    evidence_template: str,
    project_guidance: str | None,
    strategy_ref: str | None,
    plan_context: str | None,
    status: str,
    evidence: str,
    skipped_reason: str,
    agent: str,
    new_step_id: str,
    steps: list[dict] | None,
) -> Result[dict[str, Any], str]:
    """Build the shared dispatcher input for the compatible single-action mode."""
    return {
        "action": action,
        "phase_id": phase_id,
        "step_id": step_id,
        "name": name,
        "description": description,
        "depends_on": depends_on,
        "add_refs": add_refs,
        "remove_refs": remove_refs,
        "refs": refs,
        "target_phase": target_phase,
        "force": force,
        "gate": gate,
        "context": context,
        "verification": verification,
        "evidence_template": evidence_template,
        "project_guidance": project_guidance,
        "strategy_ref": strategy_ref,
        "plan_context": plan_context,
        "status": status,
        "evidence": evidence,
        "skipped_reason": skipped_reason,
        "agent": agent,
        "new_step_id": new_step_id,
        "steps": steps,
    }


# @shell_complexity: Mutate coordinates strict batch validation and compatible dispatch.
def vectl_mutate(
    action: MutationAction | None = None,
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
    requests: list[BatchMutationRequest] | None = None,
    max_issues: int | None = None,
    expected_base_sha256: str | None = None,
) -> Result[str, str]:
    """Modify plan structure in single-action or atomic batch mode.

    Exactly one of ``action`` or a non-empty ``requests`` list selects the mode.
    Batch create requests require an explicit ID, errors include the zero-based
    request index, and success returns ordered results plus ``plan_hash``. The batch
    uses one deep working copy, final validation, and one admitted transaction;
    single-action response text keeps its existing prefix and appends the transaction
    receipt.

    Args:
        action: The mutation action to perform in compatible single-action mode.
        requests: Ordered, action-discriminated request mappings for all-or-nothing
            batch mode. Batch IDs: ``add-phase`` uses ``phase_id``;
            ``add-step`` uses ``step_id``; each ``add-steps`` entry uses ``id``.
            Nested requests and symbolic result references are rejected.
        phase_id: Phase ID (for add-step, add-steps, add-phase, edit-phase).
        step_id: Step ID (for add-step, edit-step, remove-step, move-step).
        name: Name (for add-step, add-phase, edit-step, edit-phase).
        description: Description (for add-step, edit-step).
        depends_on: Dependencies (for add-step, add-phase, edit-phase, edit-step).
        add_refs: Refs to add (for edit-step).
        remove_refs: Refs to remove (for edit-step).
        refs: Refs for add-step, or replacement refs for edit-step (overrides
            add_refs/remove_refs).
        target_phase: Target phase (for move-step).
        force: Force remove (clean dep refs) for remove-step.
        gate: Gate criterion (for add-phase, edit-phase).
        context: Context (for edit-phase).
        verification: Verification (for add-step, edit-step).
        evidence_template: Optional short template for completion evidence.
        project_guidance: Project-level claim-time guidance (edit-plan only).
        strategy_ref: Plan-level strategy ref (edit-plan only).
        plan_context: Plan context (edit-plan only).
        status: Initial status for import (add-step only).
        evidence: Evidence string (add-step only).
        skipped_reason: Skip reason (add-step only).
        agent: Advisory agent suggestion (add-step, edit-step).
        new_step_id: Replacement step ID (edit-step only).
        steps: Step mappings for add-steps.
        max_issues: Optional positive row bound for final-validation issues. Every
            canonical or structural issue value is control-escaped and capped at 512
            codepoints; counts and count/value truncation flags remain explicit.
        expected_base_sha256: Optional raw-Plan SHA-256 observed by the caller.
            A mismatch rejects the request before candidate mutation or persistence.
    """
    if expected_base_sha256 is not None and (
        not isinstance(expected_base_sha256, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_base_sha256) is None
    ):
        return "**Error:** expected_base_sha256 must be sha256:<64 lowercase hex>."
    if (
        max_issues is not None
        and (isinstance(max_issues, bool) or not isinstance(max_issues, int) or max_issues < 1)
    ):
        return "**Error:** max_issues must be a positive integer."
    issue_bound = max_issues

    meaningful_single_fields = any(
        (
            phase_id,
            step_id,
            name,
            description,
            depends_on,
            add_refs,
            remove_refs,
            refs,
            target_phase,
            force,
            gate,
            context,
            verification,
            evidence_template,
            project_guidance is not None,
            strategy_ref is not None,
            plan_context is not None,
            status,
            evidence,
            skipped_reason,
            agent,
            new_step_id,
            steps,
        )
    )
    batch_mode = requests is not None
    if (action is None) == (not batch_mode):
        return "**Error:** input_shape: exactly one of action or requests is required."
    if batch_mode and meaningful_single_fields:
        return "**Error:** input_shape: requests cannot be combined with single-action fields."

    if batch_mode:
        try:
            normalized_requests = _validate_batch_requests(requests)
        except PlanError as error:
            return f"**Error:** {error}"
    else:
        assert action is not None
        normalized_requests = [
            _single_request(
                action,
                phase_id=phase_id,
                step_id=step_id,
                name=name,
                description=description,
                depends_on=depends_on,
                add_refs=add_refs,
                remove_refs=remove_refs,
                refs=refs,
                target_phase=target_phase,
                force=force,
                gate=gate,
                context=context,
                verification=verification,
                evidence_template=evidence_template,
                project_guidance=project_guidance,
                strategy_ref=strategy_ref,
                plan_context=plan_context,
                status=status,
                evidence=evidence,
                skipped_reason=skipped_reason,
                agent=agent,
                new_step_id=new_step_id,
                steps=steps,
            )
        ]

    guard_error = _linked_worktree_mutation_error()
    if guard_error:
        return guard_error

    authority = _authority().unwrap()
    plan, expected_def_hash = _load()
    current_base_sha256 = f"sha256:{expected_def_hash}"
    if expected_base_sha256 is not None and expected_base_sha256 != current_base_sha256:
        return (
            "**Conflict:** expected_base_sha256 does not match the current Plan.\n"
            f"expected_base_sha256: {expected_base_sha256}\n"
            f"current_base_sha256: {current_base_sha256}\n"
            "plan_applied: false"
        )
    working_plan = plan.model_copy(deep=True)
    results: list[tuple[str, str]] = []
    try:
        for index, request in enumerate(normalized_requests):
            try:
                working_plan, message = _apply_mutation_request(working_plan, request)
            except (PlanError, ValueError) as error:
                if batch_mode:
                    return f"**Error:** request[{index}] {request.get('action')}: {error}"
                return f"**Error:** {error}"
            results.append((request["action"], message))

        changed_locks = recalc_lock_status(working_plan)
        lock_notice = format_lock_changes(changed_locks, working_plan)
        try:
            canonical_payload = working_plan.model_dump(
                mode="python",
                warnings=False,
            )
            working_plan = Plan.model_validate(canonical_payload)
        except ValidationError as error:
            try:
                model_issues = normalize_model_validation_issues(
                    error.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    )
                )
                return format_final_validation_failure(
                    model_issues,
                    max_issues=issue_bound,
                    source="canonical_model",
                )
            except Exception:
                return format_unexpected_final_validation_failure(
                    "canonical_model", issue_bound
                )
        except Exception:
            return format_unexpected_final_validation_failure(
                "canonical_model", issue_bound
            )

        try:
            issues = validate_plan(working_plan, check_refs=False)
            blocking_issues = [issue for issue in issues if not issue.is_warning]
            if blocking_issues:
                return format_final_validation_failure(
                    issues,
                    max_issues=issue_bound,
                    source="structural",
                )
        except Exception:
            return format_unexpected_final_validation_failure(
                "structural", issue_bound
            )

        if batch_mode:
            receipt_payload, _ = cast(
                tuple[dict[str, object], str],
                _save_admitted_plan_receipt(
                    working_plan,
                    expected_def_hash,
                    f"mcp: mutate batch ({len(normalized_requests)} requests)",
                    recalc_locks=False,
                    authority=authority,
                ),
            )
            plan_hash = str(receipt_payload["persisted_sha256"]).removeprefix("sha256:")
        else:
            commit_target = step_id or phase_id or action
            receipt_payload, persisted_lock_notice = cast(
                tuple[dict[str, object], str],
                _save_admitted_plan_receipt(
                    working_plan,
                    expected_def_hash,
                    f"mcp: mutate {action} {commit_target}",
                    recalc_locks=False,
                    authority=authority,
                ),
            )
            if not lock_notice:
                lock_notice = persisted_lock_notice
    except PlanError as error:
        return f"**Error:** {error}"

    receipt_text = json.dumps(receipt_payload, sort_keys=True)
    if not batch_mode:
        result = results[0][1] + "\n\n→ Use `vectl_search` to verify plan consistency."
        if lock_notice:
            result += f"\n\n{lock_notice}"
        return result + f"\n\nmutation_receipt: {receipt_text}"

    lines = ["## Plan mutation batch", ""]
    for index, (request_action, message) in enumerate(results):
        summary = " ".join(message.replace("**", "").split())
        lines.append(f"- request[{index}] {request_action}: {summary}")
    lines.extend(
        [
            "",
            f"requests_applied: {len(results)}",
            f"plan_hash: {plan_hash}",
            f"mutation_receipt: {receipt_text}",
        ]
    )
    if lock_notice:
        lines.extend(["", lock_notice])
    return "\n".join(lines)



