"""Public compatibility facade for split implementation modules.

Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.cli_plan_common import app, console, out
from vectl.cli_plan_read_commands import next_cmd, status, show, guide_cmd, dag
from vectl.cli_plan_lifecycle_commands import claim, complete, complete_phase_cmd, defer, reject, skip, cancel, skip_phase_cmd, check_cmd, validate
from vectl.cli_plan_migration_commands import migrate_cmd, migrate_step_id_cmd, recover, checkpoint
from vectl.cli_plan_mutation_commands import add_step_cmd, add_phase_cmd, edit_plan_cmd, edit_step_cmd, edit_phase_cmd, remove_step_cmd, move_step_cmd
from vectl.cli_plan_lock_commands import unlock, recalc_lock, repair_claims_cmd, add_steps_cmd
from vectl.cli_plan_review_commands import search, mine, review, gate_check, clipboard_write_cmd, clipboard_read_cmd, clipboard_clear_cmd, dashboard
