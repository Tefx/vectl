"""Drive projection rebuild helpers.

Extracted from projections compatibility module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence

from vectl.orchestration.events import OrchestrationEventEnvelope
from vectl.orchestration.projections import FileProjectionReplay, ProjectionReplayDiagnostics, ProjectionStaleError, ReplayResult, Result, _as_int, _as_scope, _as_status

@dataclass(frozen=True)
class DriveProjection:
    """Rebuilt drive scheduling facts for the scheduler loop.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    This projection is derived from the persisted DriveRecord and
    ChildRunRef entries. It is NOT authoritative by itself — it
    reflects the truth of what was persisted.

    Attributes:
        drive_id: Drive identifier this projection belongs to.
        status: Drive lifecycle status at projection time.
        active_child_run_ids: Rebuilt from child-run index: all
            child runs with status in ``{"pending", "running"}``.
        frontier_step_ids: Step IDs that have active child runs or
            are on the claimable frontier. Derived from persisted
            record + child run activity.
        active_step_child_runs: Per-step tuple of active child run refs.
        terminal_step_child_runs: Per-step tuple of terminal child run refs.
        barrier: Barrier state, if any, from the persisted drive record.
        operator_pause_state: Whether the drive is operator-paused.
        max_parallelism: Maximum concurrent step child runs allowed.
        summary: Human-readable summary from the persisted drive record.
    """

    drive_id: str
    status: str
    active_child_run_ids: tuple[str, ...] = ()
    frontier_step_ids: tuple[str, ...] = ()
    active_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = ()
    terminal_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = ()
    barrier: object | None = None
    operator_pause_state: str = "active"
    max_parallelism: int = 4
    summary: str = ""


# @shell_orchestration: Drive projection coordinates persisted store reads into scheduler resume state.
def rebuild_drive_projection(
    store: object,
    drive_id: str,
) -> Result[DriveProjection, Exception]:
    """Rebuild drive scheduling facts from persisted store for the scheduler loop.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    This function takes a ``DriveStore`` and a ``drive_id``, reads the
    latest persisted ``DriveRecord`` and all ``ChildRunRef`` entries,
    and produces a ``DriveProjection`` that the scheduler loop can use
    as its initial state on resume/recover.

    The ``active_child_run_ids`` and per-step breakdown are always
    derived from the child-run index (the store of truth), NOT from
    the ``DriveRecord.active_child_run_ids`` field, which may be stale
    if the drive was interrupted.

    ``frontier_step_ids`` is taken from the persisted ``DriveRecord``
    because computing the DAG frontier requires plan graph authority
    (vectl core), which the projection layer does not access.

    Args:
        store: A ``DriveStore`` instance providing ``load_drive``,
            ``active_child_runs_for_drive``, and ``child_runs_for_drive``.
        drive_id: Drive identifier to project.

    Returns:
        ``DriveProjection`` with rebuilt scheduling facts.

    Raises:
        DriveStoreError: If no DriveRecord exists for the drive_id.
    """
    # Import here to avoid circular imports at module level
    from vectl.orchestration.run_store import DriveStore

    if not isinstance(store, DriveStore):
        raise TypeError(f"store must be a DriveStore instance, got {type(store).__name__}")

    persisted = store.load_drive(drive_id)
    if persisted is None:
        from vectl.orchestration.run_store import DriveStoreError

        raise DriveStoreError(f"no DriveRecord found for drive_id={drive_id!r}")

    return _rebuild_drive_projection_from_records(
        persisted, store.child_runs_for_drive(drive_id), store.active_child_runs_for_drive(drive_id)
    )


# @shell_complexity: Branches partition active/terminal child-run refs while preserving persisted DriveRecord authority.
# @shell_orchestration: Drive record projection preserves scheduler-facing active/terminal child-run indexes.
def _rebuild_drive_projection_from_records(
    persisted: object,
    all_refs: tuple[object, ...],
    active_refs: tuple[object, ...],
) -> Result[DriveProjection, Exception]:
    """Core projection logic, separated for testability without DriveStore.

    Authority: docs/RFC-orch-drive.md sections 9, 10

    Args:
        persisted: The DriveRecord.
        all_refs: All ChildRunRef entries for the drive.
        active_refs: Active (pending/running) ChildRunRef entries for the drive.

    Returns:
        ``DriveProjection`` with rebuilt scheduling facts.
    """
    from vectl.orchestration.contracts import ChildRunRef, DriveBarrier, DriveRecord

    if not isinstance(persisted, DriveRecord):
        raise TypeError(f"persisted must be a DriveRecord instance, got {type(persisted).__name__}")

    active_ids = tuple(ref.run_id for ref in active_refs if isinstance(ref, ChildRunRef))

    # Partition active child runs by step_id
    active_by_step: dict[str, list] = {}
    for ref in active_refs:
        if isinstance(ref, ChildRunRef) and ref.kind == "step" and ref.step_id:
            active_by_step.setdefault(ref.step_id, []).append(ref)

    # Partition terminal (success/fail/cancelled) child runs by step_id
    terminal_by_step: dict[str, list] = {}
    for ref in all_refs:
        if (
            isinstance(ref, ChildRunRef)
            and ref.kind == "step"
            and ref.step_id
            and ref.status in ("success", "fail", "cancelled")
        ):
            terminal_by_step.setdefault(ref.step_id, []).append(ref)

    active_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = tuple(
        (step_id, tuple(refs)) for step_id, refs in sorted(active_by_step.items())
    )
    terminal_step_child_runs: tuple[tuple[str, tuple[object, ...]], ...] = tuple(
        (step_id, tuple(refs)) for step_id, refs in sorted(terminal_by_step.items())
    )

    barrier_value: DriveBarrier | None = persisted.barrier

    return DriveProjection(
        drive_id=persisted.drive_id,
        status=persisted.status,
        active_child_run_ids=active_ids,
        frontier_step_ids=persisted.frontier_step_ids,
        active_step_child_runs=active_step_child_runs,
        terminal_step_child_runs=terminal_step_child_runs,
        barrier=barrier_value,
        operator_pause_state=persisted.operator_pause_state,
        max_parallelism=persisted.max_parallelism,
        summary=persisted.summary,
    )


def replay_drive_events(
    events: tuple[OrchestrationEventEnvelope | Mapping[str, object], ...],
    artifact_root: Path,
    drive_id: str,
) -> Result[ReplayResult, Exception]:
    """Replay drive-scoped events into drive projection state artifacts.

    Authority: docs/RFC-orch-drive.md §16.3, §16.4
              docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §9.2

    This function replays drive events and persists drive-level state
    artifacts under ``<artifact_root>/drive/``. It extends the existing
    FileProjectionReplay semantics to handle drive-scoped event envelopes
    (those with a ``drive_id`` field).

    Args:
        events: Canonical drive events to replay.
        artifact_root: Directory where projection artifacts are persisted.
        drive_id: Drive identifier for scoping.

    Returns:
        ReplayResult with drive projection state and artifact metadata.
    """
    replay = FileProjectionReplay(events=events, artifact_root=artifact_root, run_id=drive_id)
    result = replay.replay_result()

    # Also persist drive-level summary artifacts per §16.4
    drive_dir = artifact_root / "drive"
    drive_dir.mkdir(parents=True, exist_ok=True)

    # Build and write drive summary from last projected state
    if replay._latest is not None:
        latest = replay._latest
        summary = {
            "drive_id": drive_id,
            "status": latest.status,
            "active_execution_count": latest.active_execution_count,
            "dispatch_count": latest.dispatch_count,
            "resolution_count": latest.resolution_count,
            "last_event_seq": latest.last_event_seq,
        }
        _write_json(drive_dir / "summary.json", summary)

        frontier = {
            "drive_id": drive_id,
            "active_step_id": latest.active_step_id,
            "open_case_count": latest.open_case_count,
        }
        _write_json(drive_dir / "frontier.json", frontier)

    return result
