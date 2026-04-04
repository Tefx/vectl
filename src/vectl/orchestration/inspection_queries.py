"""
Joined read-query DTOs for inspection/query surfaces.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3, 5
(no dedicated section; uses contracts.py shared types)

Public surfaces (this module):
    - RunsInspectView         (joined DTO for runs inspection)
    - InspectQuery           (query parameter schema for inspect operations)
    - query_runs()           (run registry query boundary)
    - query_cases()          (case/unresolved registry query boundary)

Note: This module addresses the "joined read-query DTOs for runs, inspect, and
case surfaces". It complements control_channel.py (which focuses on the
control-channel protocol) with pure read-query surfaces. The exact query
model and persistence backend are not yet specified; this module records
interface anchors with documented gaps.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from vectl.orchestration.contracts import CoreSnapshot, RosterSnapshot, RuntimeSnapshot
from vectl.orchestration.run_store import CaseIndexEntry, RunRecord, RunRegistry
from vectl.orchestration.projections import RunStateView

if TYPE_CHECKING:
    pass


# Status values shared across run-store and projections
_RUN_STATUSES: tuple[Literal["pending", "running", "success", "fail", "stall"], ...] = (
    "pending",
    "running",
    "success",
    "fail",
    "stall",
)


# ---------------------------------------------------------------------
# Runs Inspect View — joined read-query DTO for runs
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# Inspect Query — query parameter schema
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# Query Protocols
# ---------------------------------------------------------------------
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
        registry: RunRegistry instance for run-record lookups.
        projection_root: Optional artifact root for RunStateView enrichment.
    """

    def __init__(
        self,
        registry: RunRegistry,
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
            records_by_run_id = self._registry._latest_records_by_run_id()
            records = [r for r in records_by_run_id.values() if r.agent == inspect_query.agent]
            records.sort(key=lambda r: (r.updated_at or 0.0, r.run_id), reverse=True)
        elif inspect_query.status:
            records = list(self._registry.by_status(inspect_query.status))
        else:
            records_by_run_id = self._registry._latest_records_by_run_id()
            records = list(records_by_run_id.values())
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
        registry: RunRegistry instance for case-index lookups.
    """

    def __init__(self, registry: RunRegistry) -> None:
        self._registry = registry

    def open_cases(self) -> tuple[str, ...]:
        """
        Return identifiers for all open (unresolved) cases.

        Returns:
            Tuple of open case identifiers.
        """
        latest_cases = self._registry._latest_cases_by_case_id()
        open_ids = [case_id for case_id, entry in latest_cases.items() if entry.status == "open"]
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


# ---------------------------------------------------------------------
# Query Convenience Functions
# ---------------------------------------------------------------------
# GAP: The default query registry instance is not yet specified.
# These functions record the convenience surface with documented gaps.


def query_runs(
    inspect_query: InspectQuery,
    registry: RunsQuery | None = None,
) -> RunsInspectView:
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
        registry = RunsQueryImpl(RunRegistry())
    return registry.query(inspect_query)


def query_cases(
    cases_query: str | None = None,
    registry: CasesQuery | None = None,
) -> tuple[str, ...]:
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
        registry = CasesQueryImpl(RunRegistry())

    if cases_query is None or cases_query == "open":
        return registry.open_cases()
    # Treat as step_id filter
    return registry.by_step(cases_query)


__all__ = [
    "RunsInspectView",
    "InspectQuery",
    "RunsQuery",
    "CasesQuery",
    "RunsQueryImpl",
    "CasesQueryImpl",
    "query_runs",
    "query_cases",
]
