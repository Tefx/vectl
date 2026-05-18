"""Public compatibility facade for split implementation modules.

Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.mcp_core_common import _get_next_steps_with_phase, _load, _plan_path, _save_plan
from vectl.mcp_core_common import is_linked_worktree, resolve_claims_path, resolve_plan_path
from vectl.mcp_core_read_tools import vectl_status, vectl_show
from vectl.mcp_core_lifecycle_tools import ClaimResponseEnvelope, ClaimStepData, vectl_claim, vectl_complete, vectl_lifecycle
from vectl.mcp_core_mutation_tools import vectl_migrate_step_id, vectl_mutate, vectl_search, vectl_validate
from vectl.mcp_core_misc_tools import vectl_check, vectl_decide
