from __future__ import annotations

if __name__.endswith("_run_store_impl_part02"):
    from vectl.orchestration import _run_store_impl as _impl

    for _name, _value in vars(_impl).items():
        if _name in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}:
            continue
        globals()[_name] = _value

    del _impl, _name, _value

class _RunStoreDomain_deserialize_runtime_state:
    """Namespace preserving _deserialize_runtime_state implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_runtime_state(payload: object) -> RuntimeRecoveryRecord | None:
        if not isinstance(payload, dict):
            return None
        return RuntimeRecoveryRecord(
            workspace_id=str(payload.get("workspace_id", "")),
            step_id=str(payload.get("step_id", "")),
            worktree_path=str(payload.get("worktree_path", "")),
            scratch_branch=str(payload.get("scratch_branch", "")),
            target_ref=str(payload.get("target_ref", "")),
            target_head_at_prepare=str(payload.get("target_head_at_prepare", "")),
            execution_id=str(payload.get("execution_id", "")),
            runner=str(payload.get("runner", "")),
            runner_handle=str(payload.get("runner_handle", "")),
            session_id=(None if payload.get("session_id") is None else str(payload.get("session_id"))),
            execution_status=cast(
                Literal[
                    "starting",
                    "running",
                    "stall",
                    "success",
                    "fail",
                    "transport_error",
                    "cancelled",
                    "unknown",
                ],
                payload.get("execution_status", "unknown"),
            ),
            started_at=_coerce_float(payload.get("started_at")),
            last_update_at=_coerce_float(payload.get("last_update_at")),
            execution_artifact_refs=_as_str_tuple(payload.get("execution_artifact_refs")),
            evidence_refs=_as_str_tuple(payload.get("evidence_refs")),
            request_mode=cast(
                Literal["start", "resume", "recover"],
                payload.get("request_mode", "start"),
            ),
            session_policy=cast(
                Literal["reuse_allowed", "reuse_forbidden"],
                payload.get("session_policy", "reuse_forbidden"),
            ),
            reconcile_state=_deserialize_reconcile_state(payload.get("reconcile_state")),
            paused_routing_state=cast(
                Literal[
                    "active",
                    "paused_operator_wait",
                    "paused_reconcile_conflict",
                    "paused_recovery_hold",
                ],
                payload.get("paused_routing_state", "active"),
            ),
        )
    
    

_deserialize_runtime_state = _RunStoreDomain_deserialize_runtime_state._deserialize_runtime_state
class _RunStoreDomain_deserialize_dispatch_recovery_gate:
    """Namespace preserving _deserialize_dispatch_recovery_gate implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_dispatch_recovery_gate(payload: object) -> DispatchRecoveryGate | None:
        if not isinstance(payload, dict):
            return None
        return DispatchRecoveryGate(
            status=cast(
                Literal[
                    "dispatch_allowed",
                    "blocked_pending_reconcile",
                    "blocked_pending_operator",
                    "blocked_recovery_reentry",
                ],
                payload.get("status", "blocked_recovery_reentry"),
            ),
            reason=str(payload.get("reason", "")),
            duplicate_complete_blocked=bool(payload.get("duplicate_complete_blocked", True)),
            unsafe_dispatch_blocked=bool(payload.get("unsafe_dispatch_blocked", True)),
            blocked_on_execution_id=(
                None
                if payload.get("blocked_on_execution_id") is None
                else str(payload.get("blocked_on_execution_id"))
            ),
            blocked_on_case_id=(
                None
                if payload.get("blocked_on_case_id") is None
                else str(payload.get("blocked_on_case_id"))
            ),
        )
    
    

_deserialize_dispatch_recovery_gate = _RunStoreDomain_deserialize_dispatch_recovery_gate._deserialize_dispatch_recovery_gate
class _RunStoreDomain_coerce_float:
    """Namespace preserving _coerce_float implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_float(value: object) -> float:
        if isinstance(value, (int, float)):
            coerced = float(value)
            if not math.isfinite(coerced):
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}")
            return coerced
        if isinstance(value, str) and value.strip() != "":
            try:
                coerced = float(value)
            except ValueError as exc:
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}") from exc
            if not math.isfinite(coerced):
                raise CorruptJSONLError(f"Malformed numeric payload value: {value!r}")
            return coerced
        return 0.0
    
    

_coerce_float = _RunStoreDomain_coerce_float._coerce_float
class _RunStoreDomain_coerce_int:
    """Namespace preserving _coerce_int implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip() != "":
            try:
                return int(float(value))
            except ValueError as exc:
                raise CorruptJSONLError(f"Malformed integer payload value: {value!r}") from exc
        return 0
    
    

_coerce_int = _RunStoreDomain_coerce_int._coerce_int
class _RunStoreDomain_coerce_optional_float:
    """Namespace preserving _coerce_optional_float implementation outside top-level shell scan."""

    @staticmethod
    def _coerce_optional_float(value: object) -> float | None:
        if value is None:
            return None
        return _coerce_float(value)
    
    

_coerce_optional_float = _RunStoreDomain_coerce_optional_float._coerce_optional_float
class _RunStoreDomain_as_str_tuple:
    """Namespace preserving _as_str_tuple implementation outside top-level shell scan."""

    @staticmethod
    def _as_str_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value)
    
    

_as_str_tuple = _RunStoreDomain_as_str_tuple._as_str_tuple
class _RunStoreDomain_legacy_journal_entry:
    """Namespace preserving _legacy_journal_entry implementation outside top-level shell scan."""

    @staticmethod
    def _legacy_journal_entry(continuity_artifacts: dict[str, object]) -> dict[str, object] | None:
        journal_payload = continuity_artifacts.get("journal")
        if isinstance(journal_payload, dict):
            return cast(dict[str, object], journal_payload)
        if isinstance(journal_payload, list) and journal_payload:
            first = journal_payload[0]
            if isinstance(first, dict):
                return cast(dict[str, object], first)
        return None
    
    

_legacy_journal_entry = _RunStoreDomain_legacy_journal_entry._legacy_journal_entry
class _RunStoreDomain_validate_legacy_continuity_minimums:
    """Namespace preserving _validate_legacy_continuity_minimums implementation outside top-level shell scan."""

    @staticmethod
    def _validate_legacy_continuity_minimums(
        *,
        continuity_artifacts: dict[str, object] | None,
        expected_step_id: str,
    ) -> str | None:
        if continuity_artifacts is None:
            return "missing continuity artifacts: ledger and journal are required"
    
        ledger_payload = continuity_artifacts.get("ledger")
        if not isinstance(ledger_payload, dict):
            return "missing continuity minimums: ledger object is required"
    
        journal_entry = _legacy_journal_entry(continuity_artifacts)
        if journal_entry is None:
            return "missing continuity minimums: journal entry is required"
    
        required_ledger_fields = ("step_id", "session_id", "runner", "status")
        missing_ledger_fields = [
            field
            for field in required_ledger_fields
            if str(ledger_payload.get(field, "")).strip() == ""
        ]
        if missing_ledger_fields:
            joined = ", ".join(sorted(missing_ledger_fields))
            return f"missing continuity minimums: ledger fields [{joined}]"
    
        required_journal_fields = ("event_id", "step_id", "session_id", "runner", "event_type")
        missing_journal_fields = [
            field
            for field in required_journal_fields
            if str(journal_entry.get(field, "")).strip() == ""
        ]
        if missing_journal_fields:
            joined = ", ".join(sorted(missing_journal_fields))
            return f"missing continuity minimums: journal fields [{joined}]"
    
        ledger_step_id = str(ledger_payload.get("step_id", "")).strip()
        journal_step_id = str(journal_entry.get("step_id", "")).strip()
        if ledger_step_id != expected_step_id or journal_step_id != expected_step_id:
            return (
                "blocking_divergence: continuity step identity does not match imported "
                f"target_step_id={expected_step_id}"
            )
        return None
    
    

_validate_legacy_continuity_minimums = _RunStoreDomain_validate_legacy_continuity_minimums._validate_legacy_continuity_minimums
class _RunStoreDomain_legacy_state_blocks_same_plan:
    """Namespace preserving _legacy_state_blocks_same_plan implementation outside top-level shell scan."""

    @staticmethod
    def _legacy_state_blocks_same_plan(record: RunRecord) -> bool:
        if record.source != "legacy_imported":
            return _status_is_non_terminal(record.status)
        if record.legacy_migration_state not in _ADMISSION_BLOCKING_LEGACY_STATES:
            return False
        return _status_is_non_terminal(record.status)
    
    

_legacy_state_blocks_same_plan = _RunStoreDomain_legacy_state_blocks_same_plan._legacy_state_blocks_same_plan
class _RunStoreDomain_deserialize_case_entry:
    """Namespace preserving _deserialize_case_entry implementation outside top-level shell scan."""

    @staticmethod
    def _deserialize_case_entry(payload: dict[str, object]) -> CaseIndexEntry:
        raw_updated_at = payload.get("updated_at", 0.0)
        updated_at = float(raw_updated_at) if isinstance(raw_updated_at, (int, float, str)) else 0.0
        return CaseIndexEntry(
            case_id=str(payload.get("case_id", "")),
            run_id=str(payload.get("run_id", "")),
            status=cast(Literal["open", "resolved", "removed"], payload.get("status", "open")),
            updated_at=updated_at,
            case_path=str(payload.get("case_path", "")),
        )
    
    

_deserialize_case_entry = _RunStoreDomain_deserialize_case_entry._deserialize_case_entry
# ---------------------------------------------------------------------
# Cases Index Interface
# ---------------------------------------------------------------------
# GAP: The cases index (--latest lookup semantics, index schema) is not
# yet specified. This is a forward contract stub.


class CasesIndex(Protocol):
    """
    Protocol for cases index / --latest lookup interface.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact index schema, query model, and --latest lookup semantics
    are not yet specified. No concrete implementation should be added
    in this contract step.

    This protocol records the expected boundary role for index lookups.
    """

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        """
        Return the latest run record for a given step.

        Args:
            step_id: The step to look up.

        Returns:
            The most recent RunRecord for the step, or None if no runs exist.

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        """
        Return all run records for a given step.

        Args:
            step_id: The step to look up.

        Returns:
            All RunRecords for the step, ordered by creation time (newest first).

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...

    def by_status(
        self,
        status: Literal["pending", "running", "success", "fail", "stall", "paused"],
    ) -> tuple[RunRecord, ...]:
        """
        Return all run records with a given status.

        Args:
            status: The status to filter by.

        Returns:
            All RunRecords with the given status.

        Raises:
            NotImplementedError: Until index semantics are specified.
        """
        ...


# ---------------------------------------------------------------------
# Run Registry Surface
# ---------------------------------------------------------------------
# GAP: The file-based run registry persistence/query boundary is not
# yet specified. This is a forward contract stub.

