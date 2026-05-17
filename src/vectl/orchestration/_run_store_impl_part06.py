from __future__ import annotations
class DriveActiveRunBlockedError(RunStoreError):
    """Raised when orch run is rejected because an active drive owns the plan.

    Authority: docs/RFC-orch-drive.md section 14.1, section 7.1

    When a drive is active for a plan, public single-run mutating commands
    (orch run) must reject with a clear error including the active drive_id
    and instructing the operator to use drive-scoped commands instead.

    The required exit code for CLI callers is 2 (not found / not admitted).
    """

    def __init__(self, message: str, *, active_drive_id: str) -> None:
        super().__init__(message)
        self.active_drive_id = active_drive_id
        self.message = message


class AdmissionAuthority:
    """Unified same-plan admission authority combining run-level and drive-level checks.

    Authority: docs/RFC-orch-drive.md section 14

    This class enforces the following admission rules:

    1. **Drive admission** (section 14.1): At most one active drive per plan
       identity. ``orch drive`` resolves-or-creates one active drive per plan.
       A new drive may be created after the prior drive reaches terminal status.

    2. **Run rejection under active drive** (section 7.1): When an active
       drive exists for a plan, ``orch run`` must reject with exit code 2,
       including the active ``drive_id`` and instructing the operator to use
       drive-scoped commands.

    3. **Child-run admission** (section 14.2): Within an active drive, child
       runs are admitted by the scheduler using lease-addressable ownership
       (drive_id + step_id + run_id). This is NOT checked by the admission
       authority; it is scheduler-owned.

    Attributes:
        run_registry: The run registry for legacy run-level admission checks.
        drive_store: The drive store for drive-level admission checks.
    """

    def __init__(
        self,
        run_registry: RunRegistry,
        drive_store: DriveStore,
    ) -> None:
        self._run_registry = run_registry
        self._drive_store = drive_store

    @property
    def run_registry(self) -> RunRegistry:
        """Return the underlying run registry."""
        return self._run_registry

    @property
    def drive_store(self) -> DriveStore:
        """Return the underlying drive store."""
        return self._drive_store

    def can_admit_run(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Check whether a standalone orch run can be admitted for the given plan.

        Authority: docs/RFC-orch-drive.md section 7.1, 14.1

        This method enforces two admission gates:
            1. Active drive gate: If an active drive exists for this plan,
               reject with the active drive_id.
            2. Legacy run gate: If an active non-terminal run exists for this
               plan (legacy same-plan exclusion), reject with conflict IDs.

        Args:
            plan_path: Canonical absolute path to the plan.
            current_run_id: Optional run ID to exempt from conflict checks
                (for resume/recover re-admission).

        Returns:
            Tuple of (allowed, reason, active_drive_id_if_blocked).
            If allowed is True, reason is empty and drive_id is None.
            If blocked by active drive, reason explains and drive_id is set.
            If blocked by legacy run conflict, reason lists conflicts and
            drive_id is None.
        """
        # Gate 1: Active drive blocks standalone run admission.
        # Authority: RFC-orch-drive.md section 7.1
        # "If an active drive exists for the same plan, public single-run
        #  mutating commands must reject with a clear error explaining that
        #  drive ownership is authoritative."
        active_drive = self._drive_store.active_drive_for_plan(plan_path)
        if active_drive is not None:
            return (
                False,
                (
                    f"active drive {active_drive.drive_id} owns plan "
                    f"'{plan_path}'; use drive-scoped commands instead of "
                    f"orch run"
                ),
                active_drive.drive_id,
            )

        # Gate 2: Legacy same-plan run exclusion.
        # Authority: Existing RunRegistry.assert_can_admit_same_plan logic.
        # Terminal-drive creation policy (RFC 14.1): when no active drive
        # exists, fall back to the existing same-plan run admission check.
        allowed, conflicts = self._run_registry.can_admit_same_plan(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )
        if not allowed:
            conflict_ids = ", ".join(record.run_id for record in conflicts)
            return (
                False,
                f"conflicting active runs for plan '{plan_path}': {conflict_ids}",
                None,
            )

        return (True, "", None)

    def assert_can_admit_run(
        self,
        plan_path: str,
        *,
        current_run_id: str | None = None,
    ) -> None:
        """Raise if a standalone orch run cannot be admitted for the plan.

        Authority: docs/RFC-orch-drive.md section 7.1, 14.1

        Raises:
            DriveActiveRunBlockedError: If an active drive blocks admission.
            SamePlanAdmissionError: If legacy run conflicts block admission.
        """
        allowed, reason, active_drive_id = self.can_admit_run(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )
        if allowed:
            return

        # Determine which error type to raise.
        if active_drive_id is not None:
            raise DriveActiveRunBlockedError(
                reason,
                active_drive_id=active_drive_id,
            )

        # Legacy run conflict — delegate to RunRegistry for the error.
        # Re-run the check via RunRegistry to get the SamePlanAdmissionError
        # with its canonical message format.
        self._run_registry.assert_can_admit_same_plan(
            plan_path=plan_path,
            current_run_id=current_run_id,
        )

    def can_admit_drive(self, plan_path: str) -> tuple[bool, str, str | None]:
        """Check whether a new drive can be created for the given plan.

        Authority: docs/RFC-orch-drive.md section 14.1

        A new drive may be created if:
            - No active drive exists for the same plan identity.
            - Terminal drives are allowed and do not block new drive creation.

        Args:
            plan_path: Canonical absolute path to the plan.

        Returns:
            Tuple of (allowed, reason, existing_drive_id_if_blocked).
            If allowed is True, reason is empty and existing_drive_id is None.
            If blocked, reason explains and existing_drive_id is set.
        """
        existing = self._drive_store.active_drive_for_plan(plan_path)
        if existing is not None:
            return (
                False,
                (
                    f"active drive already exists for plan '{plan_path}': "
                    f"drive_id={existing.drive_id}"
                ),
                existing.drive_id,
            )
        return (True, "", None)

    def assert_can_admit_drive(self, plan_path: str) -> None:
        """Raise if a new drive cannot be created for the plan.

        Authority: docs/RFC-orch-drive.md section 14.1

        Raises:
            DriveAdmissionConflictError: If an active drive exists for this plan.
        """
        self._drive_store.assert_can_admit_drive(plan_path)

    def resolve_or_create_drive(
        self,
        plan_path: str,
        *,
        agent: str = "",
        max_parallelism: int = 4,
    ) -> tuple[DriveRecord, bool]:
        """Resolve the active drive for a plan, or create a new one.

        Authority: docs/RFC-orch-drive.md section 7.2, 14.1

        This method implements the "resolve-or-create" admission pattern:
            - If an active drive already exists for the plan, return it.
            - If no active drive exists, create a new one and return it.

        Terminal drives are historical immutables and do not block new drive
        creation (only active drives block).

        Args:
            plan_path: Canonical absolute path to ``plan.yaml``.
            agent: Agent role that owns this drive (used only on creation).
            max_parallelism: Maximum concurrent step child runs (1-32).

        Returns:
            Tuple of (DriveRecord, was_created).
            was_created is True if a new drive was created, False if an
            existing active drive was resolved.

        Raises:
            MaxParallelismError: If max_parallelism is outside [1, 32].
        """
        if max_parallelism < 1 or max_parallelism > 32:
            raise MaxParallelismError(max_parallelism)

        # Try to resolve an existing active drive.
        existing = self._drive_store.active_drive_for_plan(plan_path)
        if existing is not None:
            return (existing, False)

        # Create a new drive.
        drive_id = f"drv_{generate_run_id()}"
        now = _current_timestamp()
        record = DriveRecord(
            drive_id=drive_id,
            plan_path=plan_path,
            status="running",
            started_at=now,
            updated_at=now,
            agent=agent,
            max_parallelism=max_parallelism,
            summary=f"Drive created for plan '{plan_path}' with max_parallelism={max_parallelism}",
        )
        self._drive_store.save_drive(record)
        return (record, True)


__all__ = [
    "AdmissionAuthority",
    "RunRecord",
    "RunSummary",
    "HaltReason",
    "RunArtifact",
    "RunStatus",
    "CaseIndexEntry",
    "HeartbeatArtifact",
    "Liveness",
    "RunStoreError",
    "CorruptJSONLError",
    "AppendConflictError",
    "SamePlanAdmissionError",
    "DriveActiveRunBlockedError",
    "LegacyContinuityMinimumError",
    "CasesIndex",
    "RunRegistry",
    "RunInspectionBoundary",
    "RunRegistryInspectionView",
    "LegacyMigrationState",
    "generate_run_id",
    "generate_case_id",
    "run_artifact_root_path",
    "heartbeat_path",
    "classify_liveness",
    "latest_run",
    # Drive store types
    "DriveStore",
    "DriveRetryLedgerEntry",
    "DriveStoreError",
    "DriveAdmissionConflictError",
    "MaxParallelismError",
    "TERMINAL_DRIVE_STATUSES",
    "LeaseStatus",
    "LeaseReleasedReason",
]
