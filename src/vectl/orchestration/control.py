"""
Plan-aware orchestration flow.

Authority: docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from vectl.orchestration.contracts import (
    ControlDecision,
    CoreSnapshot,
    ResolutionReport,
    RosterSnapshot,
    RuntimeSnapshot,
)
from vectl.orchestration.core_adapter import CoreAdapter

if TYPE_CHECKING:
    from typing import Final


class RosterSnapshotSource(Protocol):
    """Read-only roster snapshot source consumed by control.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.2 and 5.1
    """

    def snapshot(self) -> RosterSnapshot:
        """Return component snapshot for control evaluation.

        Returns:
            Current roster snapshot for plan-aware control evaluation.
        """
        ...


class RuntimeSnapshotSource(Protocol):
    """Read-only runtime snapshot source consumed by control.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 3.3 and 5.2
    """

    def snapshot(self) -> RuntimeSnapshot:
        """Return component snapshot for control evaluation.

        Returns:
            Current runtime snapshot for plan-aware control evaluation.
        """
        ...


@dataclass(frozen=True)
class ControlInputSources:
    """Required authoritative/control-adjacent snapshot sources.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
        docs/ORCHESTRATION-PLANE-INTERFACES.md sections 4.1 and 5

    This contract pins that control consumes:
        - authoritative core snapshots via ``core_adapter``
        - component snapshots from ``roster`` and ``runtime``
    """

    core_adapter: CoreAdapter
    roster: RosterSnapshotSource
    runtime: RuntimeSnapshotSource


class Control(Protocol):
    """Plan-aware orchestration flow contract.

    Authority:
        docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.1
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.2

    Public surface intentionally exposes only evaluate/apply_resolution.
    """

    def evaluate(
        self,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision:
        """Evaluate authoritative/component snapshots into one control decision.

        Args:
            core: Authoritative core snapshot produced through ``CoreAdapter``.
            roster: Current roster component snapshot.
            runtime: Current runtime component snapshot.

        Returns:
            Next orchestration-plane control decision.
        """
        ...

    def apply_resolution(
        self,
        report: ResolutionReport,
        core: CoreSnapshot,
        roster: RosterSnapshot,
        runtime: RuntimeSnapshot,
    ) -> ControlDecision:
        """Apply resolver report and return a follow-up control decision.

        Args:
            report: Resolver output for a previously unresolved case.
            core: Refreshed authoritative core snapshot.
            roster: Refreshed roster component snapshot.
            runtime: Refreshed runtime component snapshot.

        Returns:
            Follow-up control decision after applying resolver outcome.
        """
        ...


@dataclass(frozen=True)
class LegacyLoopSurfaceSplit:
    """Documented decomposition of legacy loop surfaces by responsibility.

    Authority:
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 4
        docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 5.5

    This record is evidence-only contract metadata for migration planning.
    It explicitly avoids wholesale ``driver/loop.py`` rename/copy migration.
    """

    control_surfaces: tuple[str, ...]
    runtime_surfaces: tuple[str, ...]
    core_adapter_surfaces: tuple[str, ...]
    resolver_surfaces: tuple[str, ...]


LEGACY_LOOP_SURFACE_SPLIT: Final[LegacyLoopSurfaceSplit] = LegacyLoopSurfaceSplit(
    control_surfaces=(
        "run main-loop orchestration branch ownership",
        "decide-dispatch-wait-reconcile flow decisions",
        "blocked/unresolved branch routing to resolver",
    ),
    runtime_surfaces=(
        "handle_dispatch mechanical launch wiring",
        "wait_for_any completion intake",
        "shutdown runner/worktree cleanup choreography",
    ),
    core_adapter_surfaces=(
        "plan load/validate read snapshots",
        "claim/complete/defer lifecycle mutations",
        "step isolation lookup",
    ),
    resolver_surfaces=(
        "judge/reasoning invocation for unresolved cases",
        "resolution report parsing and outcome mapping",
    ),
)


__all__ = [
    "Control",
    "ControlInputSources",
    "LegacyLoopSurfaceSplit",
    "LEGACY_LOOP_SURFACE_SPLIT",
    "RosterSnapshotSource",
    "RuntimeSnapshotSource",
]
