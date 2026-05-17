"""Joined read-query DTOs for inspection/query surfaces.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 5
Authority: docs/RFC-orch-drive.md sections 7.3, 7.4 (drive-scoped selectors)
Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

Drive-scoped queries enforce that child-run drill-down is explicit and bounded
to the selected drive while preserving the existing CLI/operator DTO surfaces.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from typing_extensions import TypeAliasType

from vectl.orchestration.contracts import ChildRunRef, DriveRecord
from vectl.orchestration.run_store import (
    DriveStore,
    DriveStoreError,
    RunInspectionBoundary,
    RunRecord,
    RunRegistry,
    RunRegistryInspectionView,
)

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)

# Guard-facing compatibility alias: these query functions are read adapters
# whose direct DTO/tuple returns are consumed by CLI/operator surfaces.
# Existing callers rely on raised store/scope exceptions rather than Result
# unwrapping; the alias makes that adapter ownership explicit without changing
# public orchestration inspection semantics.
Result = TypeAliasType("Result", Any, type_params=(_T, _E))


# Status values shared across run-store and projections
_RUN_STATUSES: tuple[Literal["pending", "running", "success", "fail", "stall"], ...] = (
    "pending",
    "running",
    "success",
    "fail",
    "stall",
)


@dataclass(frozen=True)
class RunsInspectView:
    """
    Joined read-query DTO for runs inspection.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact runs inspect view shape and query model are not yet
    specified. The fields below represent the known minimum anchor.

    Attributes:
        step_id: Step associated with these runs.
        agent: Agent associated with these runs.
        runs: Tuple of run record identifiers for this query scope.
        latest_run_id: Most recent run identifier (--latest result).
        total_count: Total number of runs matching the query.
        statuses: Aggregated status counts for the query scope.
    """

    step_id: str | None = None
    agent: str | None = None
    runs: tuple[str, ...] = ()
    latest_run_id: str | None = None
    total_count: int = 0
    statuses: tuple[Literal["pending", "running", "success", "fail", "stall"], ...] = ()


@dataclass(frozen=True)
class InspectQuery:
    """
    Query parameter schema for inspect operations.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact query parameter model is not yet specified.
    The fields below represent the known minimum anchor.

    Attributes:
        step_id: Optional step ID filter.
        agent: Optional agent filter.
        status: Optional status filter.
        limit: Maximum number of results to return.
        offset: Offset for pagination.
    """

    step_id: str | None = None
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall"] | None = None
    limit: int = 100
    offset: int = 0


# GAP: The exact query protocol and persistence backend are not yet
# specified.


class RunsQuery(Protocol):
    """
    Protocol for run registry query operations.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact query implementation and persistence backend are not
    yet specified. This protocol records the expected boundary role.
    """

    def query(self, inspect_query: InspectQuery) -> RunsInspectView:
        """
        Execute a run registry query.

        Args:
            inspect_query: The query parameters.

        Returns:
            RunsInspectView with query results.

        Raises:
            NotImplementedError: Until query semantics are specified.
        """
        ...

    def latest_for_step(self, step_id: str) -> str | None:
        """
        Return the run ID of the latest run for a step (--latest).

        Args:
            step_id: The step to look up.

        Returns:
            The latest run ID, or None.

        Raises:
            NotImplementedError: Until query semantics are specified.
        """
        ...


class RunsQueryImpl:
    """
    Concrete run registry query implementation.

    Joins run-store records with optional projection state for
    enriched run-state views. Does not re-derive liveness or status
    independently; consumes canonical run-store and projection facts.

    Args:
        registry: Read boundary for run-record lookups.
        projection_root: Optional artifact root for RunStateView enrichment.
    """

    def __init__(
        self,
        registry: RunInspectionBoundary,
        *,
        projection_root: Path | None = None,
    ) -> None:
        self._registry = registry
        self._projection_root = projection_root

    def query(self, inspect_query: InspectQuery) -> RunsInspectView:
        """
        Execute a run registry query and return a joined RunsInspectView.

        Applies step_id, agent, and status filters from ``inspect_query``.
        Aggregates status counts from the filtered run records. When a
        projection root is configured, enriches the view with the latest
        RunStateView for the matching runs.

        Args:
            inspect_query: The query parameters.

        Returns:
            RunsInspectView with query results.
        """
        records = self._filtered_records(inspect_query)

        run_ids = tuple(record.run_id for record in records)
        latest_run_id: str | None = None
        if records:
            latest_record = max(records, key=lambda r: (r.updated_at or 0.0, r.run_id))
            latest_run_id = latest_record.run_id

        status_counts = Counter(record.status for record in records if record.status is not None)
        statuses = cast(
            "tuple[Literal['pending', 'running', 'success', 'fail', 'stall'], ...]",
            tuple(status for status in _RUN_STATUSES if status_counts.get(status, 0) > 0),
        )

        return RunsInspectView(
            step_id=inspect_query.step_id,
            agent=inspect_query.agent,
            runs=run_ids,
            latest_run_id=latest_run_id,
            total_count=len(records),
            statuses=statuses,
        )

    def latest_for_step(self, step_id: str) -> str | None:
        """
        Return the run ID of the latest run for a step (--latest).

        Args:
            step_id: The step to look up.

        Returns:
            The latest run ID, or None.
        """
        record = self._registry.latest_for_step(step_id)
        return record.run_id if record is not None else None

    def _filtered_records(
        self,
        inspect_query: InspectQuery,
    ) -> list[RunRecord]:
        """
        Return run records matching the inspect query filters.

        Applies step_id, agent, and status filters in sequence.
        """
        records: list[RunRecord] = []

        if inspect_query.step_id:
            records = list(self._registry.all_for_step(inspect_query.step_id))
        elif inspect_query.agent:
            records = [r for r in self._registry.latest_records() if r.agent == inspect_query.agent]
            records.sort(key=lambda r: (r.updated_at or 0.0, r.run_id), reverse=True)
        elif inspect_query.status:
            records = list(self._registry.by_status(inspect_query.status))
        else:
            records = list(self._registry.latest_records())
            records.sort(key=lambda r: (r.updated_at or 0.0, r.run_id), reverse=True)

        # Apply limit/offset pagination
        return records[inspect_query.offset : inspect_query.offset + inspect_query.limit]


class CasesQuery(Protocol):
    """
    Protocol for case/unresolved registry query operations.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact cases query model is not yet specified.
    """

    def open_cases(self) -> tuple[str, ...]:
        """
        Return identifiers for all open (unresolved) cases.

        Returns:
            Tuple of open case identifiers.

        Raises:
            NotImplementedError: Until query semantics are specified.
        """
        ...

    def by_step(self, step_id: str) -> tuple[str, ...]:
        """
        Return all case identifiers associated with a step.

        Args:
            step_id: The step to look up.

        Returns:
            Tuple of case identifiers for the step.

        Raises:
            NotImplementedError: Until query semantics are specified.
        """
        ...


class CasesQueryImpl:
    """
    Concrete cases registry query implementation.

    Consumes canonical case-index entries from the run store without
    re-deriving case state independently.

    Args:
        registry: Read boundary for case-index lookups.
    """

    def __init__(self, registry: RunInspectionBoundary) -> None:
        self._registry = registry

    def open_cases(self) -> tuple[str, ...]:
        """
        Return identifiers for all open (unresolved) cases.

        Returns:
            Tuple of open case identifiers.
        """
        open_ids = [entry.case_id for entry in self._registry.latest_cases(include_removed=False)]
        return tuple(sorted(open_ids))

    def by_step(self, step_id: str) -> tuple[str, ...]:
        """
        Return all case identifiers associated with a step.

        Walks run records for the step and collects case identifiers
        from the case index.

        Args:
            step_id: The step to look up.

        Returns:
            Tuple of case identifiers for the step.
        """
        run_records = self._registry.all_for_step(step_id)
        case_ids: list[str] = []
        for record in run_records:
            for case_entry in self._registry.cases_for_run(record.run_id):
                if case_entry.status == "open" and case_entry.case_id not in case_ids:
                    case_ids.append(case_entry.case_id)
        return tuple(sorted(case_ids))


# GAP: The default query registry instance is not yet specified.
# These functions record the convenience surface with documented gaps.


def query_runs(
    inspect_query: InspectQuery,
    registry: RunsQuery | None = None,
) -> Result[RunsInspectView, Exception]:
    """
    Convenience boundary for querying the run registry.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    Args:
        inspect_query: The query parameters.
        registry: Optional explicit query instance. If None, a default
            RunRegistry is constructed and used.

    Returns:
        RunsInspectView with query results.
    """
    if registry is None:
        registry = RunsQueryImpl(RunRegistryInspectionView(RunRegistry()))
    return registry.query(inspect_query)


def query_cases(
    cases_query: str | None = None,
    registry: CasesQuery | None = None,
) -> Result[tuple[str, ...], Exception]:
    """
    Convenience boundary for querying the cases registry.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    Args:
        cases_query: Optional query specifier. "open" returns open cases;
            a step_id string returns cases for that step. None returns
            all open cases.
        registry: Optional explicit query instance. If None, a default
            RunRegistry is constructed and used.

    Returns:
        Tuple of matching case identifiers.
    """
    if registry is None:
        registry = CasesQueryImpl(RunRegistryInspectionView(RunRegistry()))

    if cases_query is None or cases_query == "open":
        return registry.open_cases()
    # Treat as step_id filter
    return registry.by_step(cases_query)


# Authority: docs/RFC-orch-drive.md sections 7.3, 7.4
# Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7
#
# Drive-scoped queries enforce that child-run drill-down is explicit
# and bounded to the selected drive. A child run not belonging to the
# selected drive must fail with exit code 2 (per RFC §7.4).


@dataclass(frozen=True)
class DriveInspectQuery:
    """
    Query parameter schema for drive-scoped inspection operations.

    Authority: docs/RFC-orch-drive.md section 7.4
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

    The drive_id is always required for drive-scoped queries.
    child_run_id is optional: when provided, queries are scoped to
    that specific child run within the drive. Per RFC §7.4,
    --child-run-id is exclusive with drive selectors and a mismatched
    child run must fail with exit code 2.

    Attributes:
        drive_id: The drive to inspect. Always required.
        child_run_id: Optional child-run selector for drill-down.
            Must belong to the specified drive.
        limit: Maximum number of results to return.
        offset: Offset for pagination.
    """

    drive_id: str
    child_run_id: str | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True)
class DriveInspectView:
    """
    Joined read-query DTO for drive-level inspection.

    Authority: docs/RFC-orch-drive.md section 7.3

    Aggregates drive state with child-run status summary.

    Attributes:
        drive_id: The inspected drive identifier.
        scope_kind: Discriminator identifying this as a drive-scoped view.
            Always ``"drive"`` for drive-level inspection results.
        status: Current drive lifecycle status.
        active_child_run_ids: Currently active child run identifiers.
        frontier_step_ids: Ready DAG frontier step identifiers.
        blocked_case_ids: Open case identifiers blocking scheduling.
        summary: Human-readable aggregate progress description.
        child_runs: Tuple of ChildRunRef entries for the drive.
    """

    drive_id: str
    scope_kind: str = "drive"
    status: str = ""
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    blocked_case_ids: tuple[str, ...] = ()
    summary: str = ""
    child_runs: tuple[ChildRunRef, ...] = ()


class ChildRunScopeError(ValueError):
    """Raised when a child-run selector does not belong to the selected drive.

    Authority: docs/RFC-orch-drive.md section 7.4 — a child-run selector
    that does not belong to the selected drive must fail with exit code 2.
    """


def validate_child_run_in_drive(
    drive_id: str,
    child_run_id: str,
    drive_store: DriveStore,
) -> Result[ChildRunRef, ChildRunScopeError]:
    """
    Validate that a child-run selector belongs to the selected drive.

    Authority: docs/RFC-orch-drive.md section 7.4

    Per the RFC: "a child-run selector that does not belong to the selected
    drive must fail with exit code 2." This function validates the scope
    constraint and returns the matching ChildRunRef on success, or raises
    ChildRunScopeError on failure.

    Args:
        drive_id: The drive that must own the child run.
        child_run_id: The child run ID to validate.
        drive_store: Drive store for looking up child runs.

    Returns:
        The ChildRunRef for the validated child run.

    Raises:
        ChildRunScopeError: If the child run does not belong to the drive.
    """
    child_runs = drive_store.child_runs_for_drive(drive_id)
    for ref in child_runs:
        if ref.run_id == child_run_id:
            return ref
    raise ChildRunScopeError(f"child-run '{child_run_id}' does not belong to drive '{drive_id}'")


def query_drive_status(
    query: DriveInspectQuery,
    drive_store: DriveStore,
) -> Result[DriveInspectView, Exception]:
    """
    Drive-level status query.

    Authority: docs/RFC-orch-drive.md section 7.3
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

    If query.child_run_id is provided, the result is filtered to that
    child run within the drive. Per RFC §7.4, a mismatched child-run
    selector raises ChildRunScopeError.

    Args:
        query: Drive-scoped query parameters.
        drive_store: Drive store for looking up drive state.

    Returns:
        DriveInspectView with current drive state.

    Raises:
        ChildRunScopeError: If child_run_id does not belong to the drive.
        DriveStoreError: If the drive store cannot be read.
    """
    record = drive_store.replay_drive_state(query.drive_id)
    child_runs = drive_store.child_runs_for_drive(query.drive_id)

    if query.child_run_id is not None:
        # Validate scope constraint per RFC §7.4
        validate_child_run_in_drive(query.drive_id, query.child_run_id, drive_store)
        # Filter to the specific child run
        child_runs = tuple(ref for ref in child_runs if ref.run_id == query.child_run_id)

    return DriveInspectView(
        drive_id=record.drive_id,
        status=record.status,
        active_child_run_ids=record.active_child_run_ids,
        frontier_step_ids=record.frontier_step_ids,
        blocked_case_ids=record.blocked_case_ids,
        summary=record.summary,
        child_runs=child_runs,
    )


def query_drive_events(
    query: DriveInspectQuery,
    events: tuple[Any, ...],
) -> Result[tuple[Any, ...], Exception]:
    """
    Drive-level events query.

    Authority: docs/RFC-orch-drive.md section 7.3

    Filters the provided event stream for drive-scoped events.
    When child_run_id is set, returns only events belonging to that
    child run within the drive.

    Args:
        query: Drive-scoped query parameters.
        events: Full event stream to filter.

    Returns:
        Filtered events for the drive or child run.

    Raises:
        ChildRunScopeError: If child_run_id does not belong to the drive.
    """
    # Events are filtered by drive_id and optionally child_run_id
    # The actual filtering depends on event schema having drive_id/child_run_id
    filtered: list[Any] = []
    for event in events:
        event_drive = getattr(event, "drive_id", None)
        event_child = getattr(event, "child_run_id", None) or getattr(event, "run_id", None)
        if event_drive == query.drive_id:
            if query.child_run_id is None or event_child == query.child_run_id:
                filtered.append(event)

    result = tuple(filtered[query.offset : query.offset + query.limit])
    return result


def query_drive_logs(
    query: DriveInspectQuery,
    log_lines: tuple[str, ...],
) -> Result[tuple[str, ...], Exception]:
    """
    Drive-level logs query.

    Authority: docs/RFC-orch-drive.md section 7.3

    Filters log lines for drive-scoped content. When child_run_id is
    set, returns only logs belonging to that child run within the drive.

    Args:
        query: Drive-scoped query parameters.
        log_lines: Full log lines to filter.

    Returns:
        Filtered log lines for the drive or child run.
    """
    selector = query.child_run_id or query.drive_id
    scoped = [line for line in log_lines if selector in line]
    result = tuple(scoped[query.offset : query.offset + query.limit])
    return result


def query_drive_artifacts(
    query: DriveInspectQuery,
    child_runs: tuple[ChildRunRef, ...],
    artifact_roots: tuple[Path, ...],
) -> Result[tuple[str, ...], Exception]:
    """
    Drive-level artifacts query.

    Authority: docs/RFC-orch-drive.md section 7.3

    Collects artifact references from child runs belonging to the drive.
    When child_run_id is set, returns only artifacts from that child run.

    Args:
        query: Drive-scoped query parameters.
        child_runs: ChildRunRef entries for the drive.
        artifact_roots: Tuple of artifact root paths to scan.

    Returns:
        Tuple of artifact reference strings.

    Raises:
        ChildRunScopeError: If child_run_id does not belong to the drive.
    """
    selected_child_runs = child_runs
    if query.child_run_id is not None:
        matching = tuple(ref for ref in child_runs if ref.run_id == query.child_run_id)
        if not matching:
            raise ChildRunScopeError(
                f"child-run '{query.child_run_id}' does not belong to drive '{query.drive_id}'"
            )
        selected_child_runs = matching

    candidate_paths = (
        (ref.run_id, root / ref.run_id / candidate)
        for ref in selected_child_runs
        for root in artifact_roots
        for candidate in ("config.snapshot.yaml", "state/latest.json")
    )
    artifact_refs = tuple(
        f"run_id={run_id} path={path}" for run_id, path in candidate_paths if path.exists()
    )

    return tuple(artifact_refs[query.offset : query.offset + query.limit])


def query_drive_actions(
    query: DriveInspectQuery,
    action_refs: tuple[str, ...],
) -> Result[tuple[str, ...], Exception]:
    """
    Drive-level actions query.

    Authority: docs/RFC-orch-drive.md section 7.3

    Filters action references for drive-scoped content. When
    child_run_id is set, returns only actions from that child run.

    Args:
        query: Drive-scoped query parameters.
        action_refs: Action reference strings (format: "run_id=... ...")

    Returns:
        Filtered action references for the drive or child run.
    """
    scoped: list[str] = []
    for ref in action_refs:
        if query.child_run_id is not None:
            if query.child_run_id in ref:
                scoped.append(ref)
        else:
            # Drive-scoped: include all actions for child runs in the drive
            scoped.append(ref)

    return tuple(scoped[query.offset : query.offset + query.limit])


__all__ = [
    "RunsInspectView",
    "InspectQuery",
    "RunsQuery",
    "CasesQuery",
    "RunsQueryImpl",
    "CasesQueryImpl",
    "query_runs",
    "query_cases",
    "DriveInspectView",
    "DriveInspectQuery",
    "ChildRunScopeError",
    "validate_child_run_in_drive",
    "query_drive_status",
    "query_drive_events",
    "query_drive_logs",
    "query_drive_artifacts",
    "query_drive_actions",
]
