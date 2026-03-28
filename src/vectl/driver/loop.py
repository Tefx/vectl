"""Main runtime loop contract for the Python driver.

Responsibility: Coordinate the decide -> dispatch -> wait -> reconcile cycle,
startup recovery, and graceful shutdown.

Non-responsibility: Does NOT implement runner internals, prompt rendering
templates, judge internals, git operations, or vectl core lifecycle internals.
It orchestrates those modules through their existing contracts.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.11, Section 3,
Section 4, and Q1-Q3.

CONTRACT PURITY: This module pins runtime signatures, type definitions, and
bounded deferrals only. Business/runtime behavior is intentionally deferred to
later implementation phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from vectl.models import Action

    from .config import DriverConfig
    from .judge import Judge
    from .observe import Observer
    from .runners import Runner
    from .session import SessionPool
    from .types import CompletedEntry, DriverState


LoopSurfaceName = Literal[
    "run",
    "handle_dispatch",
    "handle_complete",
    "reconcile",
    "shutdown",
]


@dataclass(frozen=True)
class DeferredRuntimeBranch:
    """Bounded runtime behavior intentionally deferred to a later phase.

    The purpose of this type is architectural: it records behavior that MUST
    remain part of the approved runtime surface even though this contract step
    does not implement it yet.
    """

    branch: str
    deferred_to: str
    rationale: str


@dataclass(frozen=True)
class StartupRecoveryContract:
    """Contract summary for startup recovery owned by ``run()``.

    Minimal runtime scope:
    - reload durable plan/claims state
    - repair stale claim metadata
    - clean up orphaned worktrees

    Explicitly deferred:
    - anomaly semantics that require judge-mediated repair vs halt decisions
    """

    reload_plan: bool = True
    repair_claims: bool = True
    cleanup_orphans: bool = True
    anomaly_judgment_deferred: bool = True


@dataclass(frozen=True)
class GracefulShutdownContract:
    """Contract summary for graceful shutdown owned by ``shutdown()``."""

    set_halt_requested: bool = True
    wait_for_running_handles: bool = True
    kill_remaining_processes: bool = True
    cleanup_orphans: bool = True
    emit_final_event: bool = True
    close_observer: bool = True


STARTUP_RECOVERY_CONTRACT: Final[StartupRecoveryContract] = StartupRecoveryContract()
GRACEFUL_SHUTDOWN_CONTRACT: Final[GracefulShutdownContract] = GracefulShutdownContract()

# Bounded REPLAN-capable branches intentionally deferred to the
# driver-judgment-expansion-replan phase. These names are contract authority for
# future implementation work and prevent silent narrowing of runtime behavior.
DEFERRED_REPLAN_BRANCHES: Final[tuple[DeferredRuntimeBranch, ...]] = (
    DeferredRuntimeBranch(
        branch="handle_dispatch.preflight_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "PREFLIGHT invocation commitment exists, but verdict-specific runtime "
            "handling beyond the single-runner happy path is deferred."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.evidence_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "EVIDENCE judgment is committed at reconcile success boundaries; "
            "REPLAN handling must remain available for semantic adequacy flows."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.failure_classification_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "FAILURE classification may require planner involvement before any "
            "non-blocking or downstream-blocker conclusion is accepted."
        ),
    ),
    DeferredRuntimeBranch(
        branch="reconcile.escalation_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Section 2.11 explicitly reserves repeated-failure escalation verdict "
            "handling including REPLAN once threshold is reached."
        ),
    ),
    DeferredRuntimeBranch(
        branch="run.startup_recovery_anomaly_replan",
        deferred_to="driver-judgment-expansion-replan",
        rationale=(
            "Startup recovery may surface structural or semantic anomalies whose "
            "safe disposition is deferred beyond minimal happy-path startup."
        ),
    ),
)


async def run(config_path: Path) -> None:
    """Main driver entry point.

    Contract pin from docs/DRIVER-ARCHITECTURE.md Section 2.11:
    1. Load config.
    2. Resolve durable plan path.
    3. Initialize runners, judge, observer, session pool, and driver state.
    4. Perform startup recovery according to ``STARTUP_RECOVERY_CONTRACT``.
    5. Enter the decide -> dispatch -> wait -> reconcile loop.
    6. Exit through ``shutdown()`` on completion, SIGINT, or SIGTERM.

    Minimal runtime scope for this phase:
    - single-runner / single-judge happy path only
    - startup recovery and graceful shutdown contracts are pinned

    Deferred beyond this phase:
    - REPLAN-capable branches listed in ``DEFERRED_REPLAN_BRANCHES``
    - multi-runner hardening
    - full judgment expansion semantics
    """
    raise NotImplementedError("loop.run contract stub; implementation belongs to runtime phase")


async def handle_dispatch(
    action: Action,
    state: DriverState,
    config: DriverConfig,
    runners: dict[str, Runner],
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Handle one ``claim_and_dispatch`` action from ``decide()``.

    Contract pin from Section 2.11 and Q1-Q3:
    - optionally invoke preflight judgment at the committed trigger point
    - reload plan state before lifecycle mutation
    - claim step using CAS-safe plan/claims path
    - create worktree
    - resolve runner
    - resolve session reuse for the single-runner happy path
    - render prompt and dispatch runner
    - register in ``DriverState``

    Deferred branch authority:
    - ``handle_dispatch.preflight_replan`` remains deferred and may not be
      silently removed in later phases.
    """
    raise NotImplementedError(
        "loop.handle_dispatch contract stub; implementation belongs to runtime phase"
    )


async def handle_complete(
    action: Action,
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Handle one ``complete`` action from ``decide()``.

    Contract pin from Section 2.11:
    - reload plan from disk before mutation
    - complete the step using CAS-safe lifecycle operations
    - persist the resulting plan state

    This surface exists separately from ``reconcile()`` because ``decide()`` may
    emit completion actions based on prior iteration results.
    """
    raise NotImplementedError(
        "loop.handle_complete contract stub; implementation belongs to runtime phase"
    )


async def reconcile(
    completed: CompletedEntry,
    state: DriverState,
    judge: Judge,
    session_pool: SessionPool,
    observer: Observer,
    plan_path: Path,
) -> None:
    """Reconcile one completed runner result.

    Contract pin from Section 2.11, Section 3, Section 4, and Q1-Q3:
    - success path: rules first, then judge where semantic validation is required,
      then durable completion, merge, session recording, and worktree cleanup
    - failure path: increment counters, defer under threshold, preserve
      escalation threshold contract at ``count >= 3``
    - all durable mutations reload plan state before write
    - merge operations are serialized by ``DriverState.merge_lock``

    Minimal runtime scope for this phase:
    - single-runner / single-judge happy path only

    Explicit deferred REPLAN-capable branches:
    - ``reconcile.evidence_replan``
    - ``reconcile.failure_classification_replan``
    - ``reconcile.escalation_replan``
    """
    raise NotImplementedError(
        "loop.reconcile contract stub; implementation belongs to runtime phase"
    )


async def shutdown(state: DriverState, observer: Observer) -> None:
    """Graceful shutdown sequence.

    Contract pin from Section 2.11:
    1. Set ``state.halt_requested = True``.
    2. Wait for running handles up to configured timeout bounds.
    3. Kill any remaining processes.
    4. Clean up orphan worktrees.
    5. Emit a FINAL event using ``state.summary()``.
    6. Close the observer.

    The exact orchestration order is locked by ``GRACEFUL_SHUTDOWN_CONTRACT``;
    implementation details are deferred.
    """
    raise NotImplementedError(
        "loop.shutdown contract stub; implementation belongs to runtime phase"
    )


__all__ = [
    "DEFERRED_REPLAN_BRANCHES",
    "GRACEFUL_SHUTDOWN_CONTRACT",
    "LoopSurfaceName",
    "GracefulShutdownContract",
    "DeferredRuntimeBranch",
    "STARTUP_RECOVERY_CONTRACT",
    "StartupRecoveryContract",
    "handle_complete",
    "handle_dispatch",
    "reconcile",
    "run",
    "shutdown",
]
