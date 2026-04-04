"""
Run registry / cases index / --latest lookup interfaces.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3
(no dedicated section; uses contracts.py shared types)

Public surfaces (this module):
    - RunRecord              (individual run record schema)
    - CasesIndex            (cases index / --latest lookup interface)
    - RunRegistry           (run registry surface for persisting/querying runs)
    - latest_run()          (--latest lookup convenience function)

Note: This module addresses the "run registry / cases index / --latest lookup
interfaces" surface. The exact persistence backend and indexing scheme are
not yet specified in the design docs; this module records interface anchors
with documented gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Run Record Schema
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RunRecord:
    """
    Individual run record schema.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact run record schema and required fields are not yet fully
    specified. The fields below represent the known minimum anchor.

    Attributes:
        run_id: Unique identifier for this run.
        step_id: Step this run is associated with.
        agent: Agent that executed or is executing this run.
        status: Execution status of the run.
        created_at: Timestamp when the run record was created.
        finished_at: Timestamp when the run finished (if applicable).
        output_summary: Human-readable summary of run output.
    """

    run_id: str
    step_id: str
    agent: str | None = None
    status: Literal["pending", "running", "success", "fail", "stall"] | None = None
    created_at: float | None = None
    finished_at: float | None = None
    output_summary: str = ""


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
        status: Literal["pending", "running", "success", "fail", "stall"],
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


class RunRegistry:
    """
    Run registry surface for persisting and querying run records.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The exact persistence backend (file-based, SQLite, etc.) and
    query API are not yet specified. No concrete implementation should
    be added in this contract step.
    """

    def save(self, record: RunRecord) -> None:
        """
        Persist a run record.

        Args:
            record: The run record to persist.

        Raises:
            NotImplementedError: Until registry persistence is specified.
        """
        raise NotImplementedError(
            "RunRegistry.save: persistence semantics not yet specified in design docs"
        )

    def by_id(self, run_id: str) -> RunRecord | None:
        """
        Look up a run record by run ID.

        Args:
            run_id: The run ID to look up.

        Returns:
            The RunRecord if found, else None.

        Raises:
            NotImplementedError: Until registry query semantics are specified.
        """
        raise NotImplementedError(
            "RunRegistry.by_id: query semantics not yet specified in design docs"
        )

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        """
        Return the latest run record for a step (--latest lookup).

        Args:
            step_id: The step to look up.

        Returns:
            The most recent RunRecord for the step, or None.

        Raises:
            NotImplementedError: Until --latest lookup semantics are specified.
        """
        raise NotImplementedError(
            "RunRegistry.latest_for_step: --latest lookup not yet specified in design docs"
        )

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        """
        Return all run records for a step.

        Args:
            step_id: The step to look up.

        Returns:
            All RunRecords for the step.

        Raises:
            NotImplementedError: Until registry query semantics are specified.
        """
        raise NotImplementedError(
            "RunRegistry.all_for_step: query semantics not yet specified in design docs"
        )


# ---------------------------------------------------------------------
# --latest Lookup Convenience Function
# ---------------------------------------------------------------------


def latest_run(step_id: str, registry: RunRegistry | None = None) -> RunRecord | None:
    """
    Return the latest run record for a step (--latest lookup convenience surface).

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md (shared)

    GAP: The default registry instance and global lookup behavior are not
    yet specified.

    Args:
        step_id: The step to look up.
        registry: Optional explicit registry instance. If None, a default
            registry must be globally available.

    Returns:
        The most recent RunRecord for the step, or None.

    Raises:
        NotImplementedError: Until --latest lookup and default registry
            semantics are specified.
    """
    raise NotImplementedError(
        "latest_run: --latest lookup and default registry not yet specified in design docs"
    )


__all__ = [
    "RunRecord",
    "CasesIndex",
    "RunRegistry",
    "latest_run",
]
