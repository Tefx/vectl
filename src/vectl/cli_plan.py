"""Public compatibility facade for split implementation modules.

Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.cli_plan_common import app, console, out
from vectl.cli_plan_checklist_commands import check_cmd, check_inventory_cmd
from vectl.cli_plan_lock_commands import unlock, recalc_lock, repair_claims_cmd, add_steps_cmd
from vectl.cli_plan_lifecycle_commands import (
    cancel,
    claim,
    complete,
    complete_phase_cmd,
    defer,
    reject,
    skip,
    skip_phase_cmd,
    validate,
)
from vectl.cli_plan_migration_commands import checkpoint, migrate_cmd, migrate_step_id_cmd, recover
from vectl.cli_plan_mutation_commands import (
    add_phase_cmd,
    add_step_cmd,
    edit_phase_cmd,
    edit_plan_cmd,
    edit_step_cmd,
    move_step_cmd,
    remove_step_cmd,
)
from vectl.cli_plan_read_commands import dag, guide_cmd, next_cmd, show, status, top
from vectl.cli_plan_review_commands import (
    clipboard_clear_cmd,
    clipboard_read_cmd,
    clipboard_write_cmd,
    dashboard,
    gate_check,
    mine,
    review,
    search,
)
