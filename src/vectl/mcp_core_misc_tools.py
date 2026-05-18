"""Internal implementation slice split from mcp_core_tools.py."""

from __future__ import annotations

from returns.result import Success

from vectl.mcp_core_common import *

# @shell_complexity: Checklist tool preserves validation and detailed match diagnostics in one public surface.
def vectl_check(
    step_id: str,
    keyword: str | None = None,
    add: str | None = None,
    revision: str | None = None,
    requests: list[dict[str, Any]] | None = None,
    item_id: str | None = None,
    field: str | None = None,
    index: int | None = None,
    checked: bool | None = None,
    inventory: bool = False,
) -> Result[str, str]:
    """Toggle or add a checklist item in a step's description.

    Args:
        step_id: Step ID containing the checklist.
        keyword: Keyword to toggle a checklist item. Finds the checklist item
            containing this keyword (case-insensitive) and toggles its checked state.
        add: Text for a new unchecked checklist item to append.
        revision: Checklist inventory revision for deterministic mutations.
        requests: Batch deterministic mutation requests. Each request contains
            selector.item_id or selector.field+selector.index and checked.
        item_id: Single deterministic item-id selector.
        field: Single deterministic field selector.
        index: Single deterministic index selector.
        checked: Desired deterministic target state for a single selector.
        inventory: When true, return deterministic inventory without mutation.

    Returns:
        Legacy calls return Markdown text. Deterministic calls return a
        structured JSON-serializable payload with markdown, revision, items,
        diagnostics, and structured retry errors.
    """
    deterministic_supplied = any(
        value is not None for value in (revision, requests, item_id, field, index, checked)
    ) or inventory
    if deterministic_supplied:
        return _vectl_check_deterministic(
            step_id=step_id,
            keyword=keyword,
            revision=revision,
            requests=requests,
            item_id=item_id,
            field=field,
            index=index,
            checked=checked,
            inventory=inventory,
        ).unwrap()

    if keyword is None and add is None:
        return "**Error:** Must provide either 'keyword' or 'add'."

    plan, expected_def_hash = _load()

    try:
        plan = update_checklist(plan, step_id, check=keyword, append=add)
        lock_notice = _save_plan(
            plan,
            expected_def_hash,
            f"mcp: update checklist {step_id}",
        )
    except NoMatchError as e:
        return f"**Error:** No checklist item matches keyword '{e.keyword}'."
    except AmbiguousMatchError as e:
        lines = [f"**Error:** Keyword '{e.keyword}' matches multiple items:"]
        for item in e.candidates:
            lines.append(f"  - {item}")
        lines.append("\nUse a more specific keyword to match exactly one item.")
        return "\n".join(lines)
    except PlanError as e:
        return f"**Error:** {e}"

    # Show updated step
    found = plan.find_step(step_id)
    if found:
        _, step = found
        lines = [f"**Updated checklist:** {step_id}\n"]
        if step.description:
            lines.append("```markdown")
            lines.append(step.description.rstrip())
            lines.append("```")
        if lock_notice:
            lines.append(f"\n{lock_notice}")
        return "\n".join(lines)

    if lock_notice:
        return f"**Updated checklist:** {step_id}\n\n{lock_notice}"
    return f"**Updated checklist:** {step_id}"


# @shell_orchestration: Loads/saves plan through shared shell helpers and adapts JSON-serializable FastMCP payloads.
# @shell_complexity: Deterministic checklist surface must preserve inventory, mutation, and structured error branches.
def _vectl_check_deterministic(
    *,
    step_id: str,
    keyword: str | None,
    revision: str | None,
    requests: list[dict[str, Any]] | None,
    item_id: str | None,
    field: str | None,
    index: int | None,
    checked: bool | None,
    inventory: bool,
) -> Result[dict[str, Any], str]:
    from vectl.cli_plan_checklist_commands import (
        _batch_diagnostics_payload,
        _checklist_exception_payload,
        _checklist_item_payload,
        _parse_checklist_requests,
        _run_deterministic_check_mutation,
    )
    from vectl.core_checklist import get_inventory

    plan, expected_def_hash = _load()
    found = plan.find_step(step_id)
    if found is None:
        return Success({
            "ok": False,
            "markdown": f"**Error:** Step not found: {step_id}",
            "error": {"type": "PlanError", "code": "not_found", "message": f"Step not found: {step_id}"},
        })
    if inventory:
        _phase, step = found
        revision_value, items = get_inventory.__wrapped__(step)
        return Success({
            "ok": True,
            "markdown": f"**Checklist inventory:** {step_id}",
            "step_id": step_id,
            "checklist_inventory_revision": revision_value,
            "items": [_checklist_item_payload(item) for item in items],
        })
    try:
        batch_text = None
        if requests is not None:
            import json

            batch_text = json.dumps(requests)
        plan, payload = _run_deterministic_check_mutation(
            plan,
            step_id=step_id,
            keyword=keyword,
            revision=revision,
            item_id=item_id,
            field=field,
            index=index,
            checked=None if checked is None else str(checked).lower(),
            batch=batch_text,
        )
        lock_notice = _save_plan(plan, expected_def_hash, f"mcp: deterministic checklist {step_id}")
        payload["markdown"] = f"**Updated checklist:** {step_id}"
        payload["Updated checklist"] = True
        if lock_notice:
            payload["lock_notice"] = lock_notice
        return Success(payload)
    except Exception as exc:
        payload = _checklist_exception_payload(exc)
        error_type = payload.get("error", {}).get("type", exc.__class__.__name__)
        payload["markdown"] = f"**Error:** {error_type}: {exc}"
        payload["Updated checklist"] = False
        payload[error_type] = True
        if requests is not None:
            try:
                request_objects = _parse_checklist_requests(
                    batch=__import__("json").dumps(requests),
                    item_id=None,
                    field=None,
                    index=None,
                    checked=None,
                )
                payload.setdefault(
                    "diagnostics",
                    _batch_diagnostics_payload(type("D", (), {"total_requested": len(request_objects), "matched_items": 0, "changed_items": 0, "selector_diagnostics": ()})()),
                )
            except Exception:
                pass
        return Success(payload)


# ---------------------------------------------------------------------------
# Tool 15: vectl_recover
# ---------------------------------------------------------------------------



def vectl_decide(
    running_tasks: list[RunningTask],
    completed_results: list[CompletedResult] | None = None,
    advisor_state: dict[str, object] | None = None,
    max_parallelism: int = 5,
) -> Result[dict[str, Any], str]:
    """Deterministic orchestration advisor.

    Analyzes running tasks and completed results to determine what actions
    the orchestrator should take next. Supports session reuse decisions
    for efficient agent workflow continuation.

    RFC: docs/RFC-vectl-decide-advisor-refresh.md

    Args:
        running_tasks: Currently running tasks (in-flight work).
            Each task has step_id, agent, task_id (execution identity only, NOT reuse handle),
            runner (runner namespace), dispatched_at.
        completed_results: Tasks that have completed since last decision.
            Each result has step_id, task_id, runner, status (SUCCESS/FAIL), output_summary.
        advisor_state: Caller-owned state (completion_times, session_registry, failure_counts).
            Pass your persisted state here; replace it with next_state from output.
            If None, a fresh ephemeral state is used per call.
        max_parallelism: Maximum allowed parallel dispatches (default 5).

    Returns:
        Structured output containing:
        - status: dispatch | wait | blocked | done
        - reason_code: dispatch_available | waiting_on_running | capacity_full |
            no_executable_steps | repeated_failures
        - message: optional human-readable summary
        - actions: list of Action objects
        - next_state: full replacement for caller-owned advisor state
        - policy: explicit policy metadata (reuse_ttl_s, escalation_threshold)
        - decision_log: optional debug/explanatory entries
    """
    result = _decide_impl(
        running_tasks=running_tasks,
        completed_results=completed_results,
        max_parallelism=max_parallelism,
        advisor_state=advisor_state,
    )
    # Return as dict for MCP JSON serialization
    # Use exclude_none=False to ensure all expected fields are present even when None
    return result.model_dump(mode="json", exclude_none=False)
