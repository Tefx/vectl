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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.contracts import CoreSnapshot, RosterSnapshot, RuntimeSnapshot

if TYPE_CHECKING:
    pass


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

    GAP: The default registry instance is not yet specified.

    Args:
        inspect_query: The query parameters.
        registry: Optional explicit query instance. If None, a default
            registry must be globally available.

    Returns:
        RunsInspectView with query results.

    Raises:
        NotImplementedError: Until query semantics are specified.
    """
    raise NotImplementedError(
        "query_runs: default registry and query semantics not yet specified in design docs"
    )


def query_cases(
    cases_query: str | None = None,
    registry: CasesQuery | None = None,
) -> tuple[str, ...]:
    """
    Convenience boundary for querying the cases registry.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The default registry instance is not yet specified.

    Args:
        cases_query: Optional query specifier (e.g., "open", step_id filter).
            None means return all cases.
        registry: Optional explicit query instance. If None, a default
            registry must be globally available.

    Returns:
        Tuple of matching case identifiers.

    Raises:
        NotImplementedError: Until query semantics are specified.
    """
    raise NotImplementedError(
        "query_cases: default registry and query semantics not yet specified in design docs"
    )


__all__ = [
    "RunsInspectView",
    "InspectQuery",
    "RunsQuery",
    "CasesQuery",
    "query_runs",
    "query_cases",
]
