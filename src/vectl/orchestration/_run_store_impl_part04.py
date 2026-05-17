from __future__ import annotations
_deserialize_drive_record = _RunStoreDomain_deserialize_drive_record._deserialize_drive_record
class _RunStoreDomain_deserialize_child_run_ref:
    """Namespace preserving _deserialize_child_run_ref implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_child_run_ref(payload: dict[str, object]) -> ChildRunRef:
        """Deserialize a ChildRunRef from a raw dict payload."""
        return ChildRunRef(
            run_id=str(payload.get("run_id", "")),
            drive_id=str(payload.get("drive_id", "")),
            kind=cast(ChildRunKind, payload.get("kind", "step")),
            status=cast(ChildRunStatus, payload.get("status", "pending")),
            step_id=(None if payload.get("step_id") is None else str(payload.get("step_id"))),
            case_id=(None if payload.get("case_id") is None else str(payload.get("case_id"))),
            planner_request_id=(
                None
                if payload.get("planner_request_id") is None
                else str(payload.get("planner_request_id"))
            ),
            workspace=str(payload.get("workspace", "")),
            runner=str(payload.get("runner", "")),
            session_id=(None if payload.get("session_id") is None else str(payload.get("session_id"))),
            artifact_root=str(payload.get("artifact_root", "")),
        )
    
    

_deserialize_child_run_ref = _RunStoreDomain_deserialize_child_run_ref._deserialize_child_run_ref
class _RunStoreDomain_deserialize_drive_lease:
    """Namespace preserving _deserialize_drive_lease implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_lease(payload: dict[str, object]) -> DriveLease:
        """Deserialize a DriveLease from a raw dict payload.
    
        Authority: docs/RFC-orch-drive.md section 14.3
        """
        return DriveLease(
            drive_id=str(payload.get("drive_id", "")),
            step_id=str(payload.get("step_id", "")),
            run_id=str(payload.get("run_id", "")),
            status=cast(LeaseStatus, payload.get("status", "active")),
            created_at=_coerce_float(payload.get("created_at")),
            released_at=(
                None
                if payload.get("released_at") is None
                else _coerce_float(payload.get("released_at"))
            ),
            released_reason=cast(
                LeaseReleasedReason | None,
                payload.get("released_reason"),
            ),
        )
    
    

_deserialize_drive_lease = _RunStoreDomain_deserialize_drive_lease._deserialize_drive_lease
class _RunStoreDomain_deserialize_drive_config_frozen:
    """Namespace preserving _deserialize_drive_config_frozen implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_drive_config_frozen(payload: dict[str, object]) -> DriveConfigFrozen:
        """Deserialize a DriveConfigFrozen from a raw dict payload.
    
        Authority: docs/RFC-orch-drive.md sections 8.1, 15.1
        """
        return DriveConfigFrozen(
            drive_id=str(payload.get("drive_id", "")),
            max_parallelism=_coerce_int(payload.get("max_parallelism")),
            control_idle_poll_interval_ms=_coerce_int(payload.get("control_idle_poll_interval_ms")),
            control_action_ack_timeout_seconds=_coerce_float(
                payload.get("control_action_ack_timeout_seconds")
            ),
            resolver_invocation_timeout_seconds=_coerce_float(
                payload.get("resolver_invocation_timeout_seconds")
            ),
            resolver_max_tool_calls_per_invocation=_coerce_int(
                payload.get("resolver_max_tool_calls_per_invocation")
            ),
            frozen_at=_coerce_float(payload.get("frozen_at")),
        )
    
    

_deserialize_drive_config_frozen = _RunStoreDomain_deserialize_drive_config_frozen._deserialize_drive_config_frozen
@dataclass(frozen=True)
class DriveRetryLedgerEntry:
    """Durable per-step retry fact for drive-owned child failures."""

    drive_id: str
    step_id: str
    run_id: str
    failure_class: str
    summary: str = ""
    created_at: float = 0.0


class _RunStoreDomain_drive_sort_key:
    """Namespace preserving _drive_sort_key implementation outside top-level shell scan."""

    @staticmethod
    def _drive_sort_key(record: DriveRecord) -> tuple[float, str]:
        """Sort key for DriveRecord: (updated_at, drive_id)."""
        return (record.updated_at, record.drive_id)
    
    

_drive_sort_key = _RunStoreDomain_drive_sort_key._drive_sort_key
class _RunStoreDomain_child_run_sort_key:
    """Namespace preserving _child_run_sort_key implementation outside top-level shell scan."""

    @staticmethod
    def _child_run_sort_key(ref: ChildRunRef) -> tuple[float, str]:
        """Sort key for ChildRunRef by updated_at derived from run_id prefix."""
        return (0.0, ref.run_id)
    
    

_child_run_sort_key = _RunStoreDomain_child_run_sort_key._child_run_sort_key
class DriveStoreError(RuntimeError):
    """Base class for drive store failures."""


class DriveAdmissionConflictError(DriveStoreError):
    """Raised when drive admission discovers a conflicting active drive.

    Attributes:
        active_drive_id: The drive_id of the conflicting active drive.
    """

    def __init__(self, message: str, *, active_drive_id: str) -> None:
        super().__init__(message)
        self.active_drive_id = active_drive_id
        self.message = message


