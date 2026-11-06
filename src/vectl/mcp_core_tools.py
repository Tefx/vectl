"""Public compatibility facade for split implementation modules.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.mcp_core_common import (
    _get_next_steps_with_phase as _get_next_steps_with_phase,
    _load as _load,
    _plan_path as _plan_path,
    _save_plan as _save_plan,
    is_linked_worktree as is_linked_worktree,
    resolve_claims_path as resolve_claims_path,
    resolve_plan_path as resolve_plan_path,
)
from vectl.mcp_core_lifecycle_tools import (
    ClaimResponseEnvelope as ClaimResponseEnvelope,
    ClaimStepData as ClaimStepData,
    vectl_claim as vectl_claim,
    vectl_claim_many as vectl_claim_many,
    vectl_complete as vectl_complete,
    vectl_lifecycle as vectl_lifecycle,
)
from vectl.mcp_core_misc_tools import (
    vectl_check as vectl_check,
    vectl_decide as vectl_decide,
    vectl_migrate_step_id as vectl_migrate_step_id,
)
from vectl.mcp_core_mutation_tools import vectl_mutate as vectl_mutate
from vectl.mcp_core_read_tools import (
    vectl_search as vectl_search,
    vectl_show as vectl_show,
    vectl_status as vectl_status,
    vectl_validate as vectl_validate,
)
