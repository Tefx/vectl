"""Internal implementation slice split from mcp_core_tools.py."""

from __future__ import annotations

from vectl.mcp_core_common import *

# @shell_complexity: Checklist tool preserves validation and detailed match diagnostics in one public surface.
def vectl_check(
    step_id: str,
    keyword: str | None = None,
    add: str | None = None,
) -> Result[str, str]:
    """Toggle or add a checklist item in a step's description.

    Args:
        step_id: Step ID containing the checklist.
        keyword: Keyword to toggle a checklist item. Finds the checklist item
            containing this keyword (case-insensitive) and toggles its checked state.
        add: Text for a new unchecked checklist item to append.

    Returns:
        Markdown-formatted result showing the updated checklist.
    """
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

