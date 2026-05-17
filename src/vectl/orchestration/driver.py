"""Public drive-level orchestration loop and barrier-management surface.

Authority: docs/RFC-orch-drive.md sections 10, 14, 15, 17.
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md sections 3.11.

This compatibility module preserves the original ``vectl.orchestration.driver``
import path while the active-drive state machine, barriers, resume and recovery
implementation live in cohesive driver submodules.
"""

from __future__ import annotations

from vectl.orchestration.driver_impl import DriveDriver
from vectl.orchestration.driver_planning_helpers import (
    _apply_planner_bundle_to_drive,
    _construct_bundle_from_request,
)
from vectl.orchestration.driver_resolution_helpers import (
    _apply_resolution_report_to_drive,
    _barrier_reason_for_case_source,
    _infer_case_source,
    _update_drive_record,
)
from vectl.orchestration.driver_transitions import (
    DRIVE_DEFAULTS,
    DRIVE_TRANSITIONS,
    TERMINAL_DRIVE_STATUSES,
    InvalidDriveTransitionError,
    _barrier_status,
    _generate_drive_id,
    validate_drive_transition,
)
from vectl.orchestration.driver_types import (
    DriveAdmissionError,
    DriveLoopResult,
    DriveRecoverResult,
    DriveResumeResult,
    DriveStartResult,
    DriveStatusResult,
    MaxParallelismError,
)

__all__ = [
    "DRIVE_DEFAULTS",
    "DRIVE_TRANSITIONS",
    "DriveAdmissionError",
    "DriveDriver",
    "DriveLoopResult",
    "DriveRecoverResult",
    "DriveResumeResult",
    "DriveStartResult",
    "DriveStatusResult",
    "InvalidDriveTransitionError",
    "MaxParallelismError",
    "TERMINAL_DRIVE_STATUSES",
    "validate_drive_transition",
]
