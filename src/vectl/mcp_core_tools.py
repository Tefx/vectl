"""Public compatibility facade for split implementation modules.

Runtime MCP inventory: 20-tool MCP inventory includes `vectl_decide`.
Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.mcp_core_common import _get_next_steps_with_phase, _load, _plan_path, _save_plan
from vectl.mcp_core_common import is_linked_worktree, resolve_claims_path, resolve_plan_path
from vectl.mcp_core_read_tools import vectl_search, vectl_show, vectl_status, vectl_validate
from vectl.mcp_core_lifecycle_tools import ClaimResponseEnvelope, ClaimStepData, vectl_claim, vectl_claim_many, vectl_complete, vectl_lifecycle
from vectl.mcp_core_mutation_tools import vectl_mutate
from vectl.mcp_core_misc_tools import vectl_check, vectl_decide, vectl_migrate_step_id
