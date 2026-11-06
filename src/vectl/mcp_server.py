"""MCP server exposing vectl tools to agents.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
Tools generally return Markdown-formatted text.

Feature request (2026-02-12): claim-time guidance ("Output guidance when running vectl claim").
To satisfy FR R6 (structured guidance output), vectl_claim returns a structured
payload including a Guidance object plus a Markdown rendition.
Plan path: resolved via shared plan_path.resolve_plan_path() —
  explicit/override path > VECTL_PLAN_PATH > VECTL_PLAN (deprecated) >
  linked-worktree main-root plan path (no local walk-up fallback, even when
  missing) > malformed linked-worktree probe fail-closed sentinel
  (absolute cwd/plan.yaml, no walk-up) > walk-up > ./plan.yaml.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path as Path
from typing import Any as Any, Literal as Literal

from fastmcp import FastMCP
from pydantic import BaseModel as BaseModel

from vectl.agents_md import AgentsTarget as AgentsTarget, upsert_agents_md as upsert_agents_md
from vectl.claim_guidance import (
    GuidancePayload as GuidancePayload,
    build_claim_guidance as build_claim_guidance,
)
from vectl.claims import get_current_branch as get_current_branch, repair_claims as repair_claims
from vectl.core import (
    _SENTINEL as _SENTINEL,
    add_phase as add_phase,
    add_step as add_step,
    add_steps_bulk as add_steps_bulk,
    apply_duplicate_step_id_migration as apply_duplicate_step_id_migration,
    build_duplicate_step_id_migration_dry_run as build_duplicate_step_id_migration_dry_run,
    build_duplicate_step_id_migration_evidence as build_duplicate_step_id_migration_evidence,
    claim_step as claim_step,
    clipboard_clear as clipboard_clear,
    clipboard_write as clipboard_write,
    complete_phase as complete_phase,
    complete_step as complete_step,
    defer_step as defer_step,
    edit_phase as edit_phase,
    edit_plan as edit_plan,
    edit_step as edit_step,
    format_lock_changes as format_lock_changes,
    gate_check as gate_check,
    get_claimed_steps as get_claimed_steps,
    get_next_steps as get_next_steps,
    move_step as move_step,
    recalc_lock_status as recalc_lock_status,
    reject_step as reject_step,
    remove_step as remove_step,
    render_plan as render_plan,
    review_plan as review_plan,
    search_plan as search_plan,
    skip_phase as skip_phase,
    skip_step as skip_step,
    update_checklist as update_checklist,
    validate_plan as validate_plan,
)
from vectl.duplicate_step_id_format import (
    format_duplicate_step_id_diagnostics as format_duplicate_step_id_diagnostics,
    format_duplicate_step_id_recommendation as format_duplicate_step_id_recommendation,
    get_duplicate_step_id_recommendation as get_duplicate_step_id_recommendation,
)
from vectl.io import (
    _backup_definition as _backup_definition,
    _resolve_git_dir as _resolve_git_dir,
    load_plan_definition as load_plan_definition,
    save_plan as save_plan,
)
from vectl.lifecycle import (
    AffinityOverrideMetadata as AffinityOverrideMetadata,
    AffinityWarningMetadata as AffinityWarningMetadata,
    ClaimConflictError as ClaimConflictError,
    ClaimConflictMetadata as ClaimConflictMetadata,
    ClaimStepMetadata as ClaimStepMetadata,
    claim_affinity_metadata as claim_affinity_metadata,
    claim_conflict_metadata as claim_conflict_metadata,
    claim_step_metadata as claim_step_metadata,
)
from vectl.mcp_core_tools import (
    _get_next_steps_with_phase as _get_next_steps_with_phase,
    _load as _load,
    _plan_path as _plan_path,
    _save_plan as _save_plan,
    vectl_check as vectl_check,
    vectl_claim as vectl_claim,
    vectl_claim_many as vectl_claim_many,
    vectl_complete as vectl_complete,
    vectl_decide as vectl_decide,
    vectl_lifecycle as vectl_lifecycle,
    vectl_migrate_step_id as vectl_migrate_step_id,
    vectl_mutate as vectl_mutate,
    vectl_search as vectl_search,
    vectl_show as vectl_show,
    vectl_status as vectl_status,
    vectl_validate as vectl_validate,
)
from vectl.mcp_project_tools import vectl_clipboard, vectl_init, vectl_recover, vectl_repair_claims
from vectl.mcp_render_tools import (
    vectl_checkpoint,
    vectl_dag,
    vectl_guide,
    vectl_render,
    vectl_review,
)
from vectl.models import (
    AffinityError as AffinityError,
    AmbiguousMatchError as AmbiguousMatchError,
    CASConflictError as CASConflictError,
    CompletedResult as CompletedResult,
    InitResult as InitResult,
    NoMatchError as NoMatchError,
    PhaseStatus as PhaseStatus,
    Plan as Plan,
    PlanError as PlanError,
    RunningTask as RunningTask,
    Step as Step,
    StepStatus as StepStatus,
)
from vectl.plan_path import (
    is_linked_worktree as is_linked_worktree,
    resolve_claims_path as resolve_claims_path,
    resolve_plan_path as resolve_plan_path,
)
from vectl.semantics import is_step_locked as is_step_locked

mcp = FastMCP(
    "vectl",
    instructions=(
        "vectl manages phased development plans for AI agents. "
        "Use vectl_status to understand the plan, vectl_claim to start work, "
        "vectl_complete when done. Each tool returns Markdown text.\n\n"
        "Parameter convention:\n"
        "  - `step_id`: always a step (format: 'phase.step', e.g. 'core.validate')\n"
        "  - `phase_id`: always a phase (format: 'phase', e.g. 'core')\n"
        "  - `target`: accepts either a step ID or phase ID "
        "(used by vectl_show, vectl_lifecycle)\n\n"
        "Tool groups:\n"
        "  Core loop: vectl_status, vectl_claim, vectl_claim_many, vectl_complete, vectl_check\n"
        "  Inspect:   vectl_show, vectl_search, vectl_dag, vectl_render\n"
        "  Lifecycle: vectl_lifecycle, vectl_review, vectl_checkpoint\n"
        "  Mutate:    vectl_mutate\n"
        "  Help:      vectl_guide (call with topic='stuck' if blocked)\n"
        "  Setup:     vectl_init, vectl_validate, vectl_recover, vectl_repair_claims"
    ),
)


# Register imported concrete tool implementations.
vectl_check = mcp.tool()(vectl_check)
vectl_checkpoint = mcp.tool()(vectl_checkpoint)
vectl_claim = mcp.tool()(vectl_claim)
vectl_claim_many = mcp.tool()(vectl_claim_many)
vectl_clipboard = mcp.tool()(vectl_clipboard)
vectl_complete = mcp.tool()(vectl_complete)
vectl_dag = mcp.tool()(vectl_dag)
vectl_decide = mcp.tool()(vectl_decide)
vectl_guide = mcp.tool()(vectl_guide)
vectl_init = mcp.tool()(vectl_init)
vectl_lifecycle = mcp.tool()(vectl_lifecycle)
vectl_migrate_step_id = mcp.tool()(vectl_migrate_step_id)
vectl_mutate = mcp.tool()(vectl_mutate)
vectl_recover = mcp.tool()(vectl_recover)
vectl_render = mcp.tool()(vectl_render)
vectl_repair_claims = mcp.tool()(vectl_repair_claims)
vectl_review = mcp.tool()(vectl_review)
vectl_search = mcp.tool()(vectl_search)
vectl_show = mcp.tool()(vectl_show)
vectl_status = mcp.tool()(vectl_status)
vectl_validate = mcp.tool()(vectl_validate)


class _ToolWrapper:
    """Compatibility shim for tests that import MCP tool symbols.

    FastMCP's @mcp.tool() returns FunctionTool objects (not plain callables).
    This shim unwraps FunctionTool.fn to get the actual function, making
    the symbol both callable and exposing .fn for tests that use it.
    """

    def __init__(self, fn: Any) -> None:
        # Unwrap FunctionTool to get the actual callable
        actual = getattr(fn, "fn", fn)
        object.__setattr__(self, "_fn", actual)

    @property
    def fn(self) -> Callable[..., Any]:
        """Return the underlying callable."""
        return object.__getattribute__(self, "_fn")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return object.__getattribute__(self, "_fn")(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_fn"), name)


# Wrap all tool functions for test compatibility
_vectl_status_tool = vectl_status
vectl_status = _ToolWrapper(vectl_status)  # type: ignore[assignment]

_vectl_validate_tool = vectl_validate
vectl_validate = _ToolWrapper(vectl_validate)  # type: ignore[assignment]

_vectl_show_tool = vectl_show
vectl_show = _ToolWrapper(vectl_show)  # type: ignore[assignment]

_vectl_claim_tool = vectl_claim
vectl_claim = _ToolWrapper(vectl_claim)  # type: ignore[assignment]

_vectl_claim_many_tool = vectl_claim_many
vectl_claim_many = _ToolWrapper(vectl_claim_many)  # type: ignore[assignment]

_vectl_complete_tool = vectl_complete
vectl_complete = _ToolWrapper(vectl_complete)  # type: ignore[assignment]

_vectl_lifecycle_tool = vectl_lifecycle
vectl_lifecycle = _ToolWrapper(vectl_lifecycle)  # type: ignore[assignment]

_vectl_search_tool = vectl_search
vectl_search = _ToolWrapper(vectl_search)  # type: ignore[assignment]

_vectl_mutate_tool = vectl_mutate
vectl_mutate = _ToolWrapper(vectl_mutate)  # type: ignore[assignment]

_vectl_review_tool = vectl_review
vectl_review = _ToolWrapper(vectl_review)  # type: ignore[assignment]

_vectl_guide_tool = vectl_guide
vectl_guide = _ToolWrapper(vectl_guide)  # type: ignore[assignment]

_vectl_dag_tool = vectl_dag
vectl_dag = _ToolWrapper(vectl_dag)  # type: ignore[assignment]

_vectl_clipboard_tool = vectl_clipboard
vectl_clipboard = _ToolWrapper(vectl_clipboard)  # type: ignore[assignment]

_vectl_init_tool = vectl_init
vectl_init = _ToolWrapper(vectl_init)  # type: ignore[assignment]

_vectl_render_tool = vectl_render
vectl_render = _ToolWrapper(vectl_render)  # type: ignore[assignment]

_vectl_check_tool = vectl_check
vectl_check = _ToolWrapper(vectl_check)  # type: ignore[assignment]

_vectl_recover_tool = vectl_recover
vectl_recover = _ToolWrapper(vectl_recover)  # type: ignore[assignment]

_vectl_repair_claims_tool = vectl_repair_claims
vectl_repair_claims = _ToolWrapper(vectl_repair_claims)  # type: ignore[assignment]

_vectl_checkpoint_tool = vectl_checkpoint
vectl_checkpoint = _ToolWrapper(vectl_checkpoint)  # type: ignore[assignment]

_vectl_decide_tool = vectl_decide
vectl_decide = _ToolWrapper(vectl_decide)  # type: ignore[assignment]


if __name__ == "__main__":
    mcp.run()
