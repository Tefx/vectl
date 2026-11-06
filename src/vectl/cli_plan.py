"""Public compatibility facade for split implementation modules.

Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.cli_plan_checklist_commands import (
    check_cmd as check_cmd,
    check_inventory_cmd as check_inventory_cmd,
)
from vectl.cli_plan_common import app as app, console as console, out as out
from vectl.cli_plan_lifecycle_commands import (
    cancel as cancel,
    claim as claim,
    claim_many_cmd as claim_many_cmd,
    complete as complete,
    complete_phase_cmd as complete_phase_cmd,
    defer as defer,
    reject as reject,
    skip as skip,
    skip_phase_cmd as skip_phase_cmd,
    validate as validate,
)
from vectl.cli_plan_lock_commands import (
    add_steps_cmd as add_steps_cmd,
    recalc_lock as recalc_lock,
    repair_claims_cmd as repair_claims_cmd,
    unlock as unlock,
)
from vectl.cli_plan_migration_commands import (
    checkpoint as checkpoint,
    migrate_cmd as migrate_cmd,
    migrate_step_id_cmd as migrate_step_id_cmd,
    recover as recover,
)
from vectl.cli_plan_mutation_commands import (
    add_phase_cmd as add_phase_cmd,
    add_step_cmd as add_step_cmd,
    edit_phase_cmd as edit_phase_cmd,
    edit_plan_cmd as edit_plan_cmd,
    edit_step_cmd as edit_step_cmd,
    move_step_cmd as move_step_cmd,
    remove_step_cmd as remove_step_cmd,
)
from vectl.cli_plan_read_commands import (
    dag as dag,
    guide_cmd as guide_cmd,
    next_cmd as next_cmd,
    show as show,
    status as status,
    top as top,
)
from vectl.cli_plan_review_commands import (
    clipboard_clear_cmd as clipboard_clear_cmd,
    clipboard_read_cmd as clipboard_read_cmd,
    clipboard_write_cmd as clipboard_write_cmd,
    dashboard as dashboard,
    gate_check as gate_check,
    mine as mine,
    review as review,
    search as search,
)
