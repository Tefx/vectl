"""Public compatibility facade for split implementation modules.

Preserves the original import path while keeping cohesive implementation slices internal.
"""

from __future__ import annotations

from vectl.cli_orchestration_common import app, orch_app, orch_inspect_app, orch_case_app, orch_control_app, orch_config_app, orch_migration_app
from vectl.cli_orchestration_runtime_helpers import _build_orchestration_runtime_app
from vectl.cli_orchestration_run_commands import orch_run, orch_resume, orch_recover, orch_runs, orch_prune, orch_migration_validate_cutover, orch_migration_advance_state, orch_inspect_status, orch_inspect_events
from vectl.cli_orchestration_inspect_commands import orch_inspect_logs, orch_inspect_artifacts, orch_inspect_actions, orch_case_list, orch_case_show, orch_case_respond, orch_control_pause, orch_control_unpause, orch_control_stop, orch_config_show, orch_config_validate, orch_config_tools
from vectl.cli_orchestration_drive_commands import orch_drive, orch_drive_status, orch_drive_runs, orch_drive_resume, orch_drive_recover
