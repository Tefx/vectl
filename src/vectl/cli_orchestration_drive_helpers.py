"""Internal implementation slice split from cli_orchestration.py."""

from __future__ import annotations

from vectl.cli_orchestration_common import *
from vectl.cli_orchestration_runtime_helpers import *

OrchDriveIdArgument = typer.Argument(
    None,
    help="Drive identifier (omit to use --latest).",
)
OrchMaxParallelismOption = typer.Option(
    4,
    "--max-parallelism",
    help="Maximum concurrent step child runs (1-32, default 4).",
)
OrchDryRunDriveOption = typer.Option(
    False, "--dry-run", help="Compute result without applying changes."
)
OrchChildRunIdOption = typer.Option(
    None,
    "--child-run-id",
    help="Child-run selector for drill-down (exclusive with DRIVE_ID and --latest).",
)
OrchDriveForceOption = typer.Option(
    False,
    "--force",
    help="Request immediate stop (cancel active step child runs).",
)


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _resolve_latest_drive_id(app_runtime: Any):
    """Resolve the latest drive ID from the orchestration app boundary.

    Authority: docs/RFC-orch-drive.md section 7.5

    Returns:
        The latest drive_id, or None if no drives exist.
    """
    return app_runtime.resolve_latest_drive_id()


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
# @shell_complexity: selector routing preserves RFC drive/legacy compatibility and active-drive defaults.
def _resolve_optional_drive_scope(
    app_runtime: Any,
    *,
    drive_id: str | None,
    latest: bool,
    child_run_id: str | None = None,
    allow_active_drive_default: bool,
    legacy_run_id: str | None = None,
    legacy_step_id: str | None = None,
):
    """Resolve drive scope only when a drive selector is actually available.

    Authority: docs/RFC-orch-drive.md sections 7.3, 7.4

    ``--latest`` routes to drive scope only when a drive exists. Otherwise the
    flat CLI surface falls back to the legacy run-scoped ``--latest`` behavior.

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: Explicit drive selector.
        latest: Whether ``--latest`` was provided.
        child_run_id: Optional child-run selector.
        allow_active_drive_default: Whether missing selectors may default to
            the active drive for the plan.
        legacy_run_id: Legacy run selector for conflict/default checks.
        legacy_step_id: Legacy step selector for conflict/default checks.

    Returns:
        The resolved drive identifier when drive scope applies, else ``None``.

    Raises:
        typer.Exit: On selector conflicts or invalid child-run defaults.
    """
    if child_run_id is not None and (drive_id is not None or latest):
        _die("--child-run-id is exclusive with DRIVE_ID and --latest", code=2)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")

    if drive_id is not None:
        return drive_id

    if latest:
        return _resolve_latest_drive_id(app_runtime)

    active_drive_id = app_runtime.has_active_drive_for_plan()
    if child_run_id is not None:
        if active_drive_id is None:
            _die(
                "No active drive found. Provide DRIVE_ID, --latest, or --child-run-id",
                code=3,
            )
        return active_drive_id

    if not allow_active_drive_default or active_drive_id is None:
        return None

    if legacy_run_id is not None or legacy_step_id is not None:
        return None

    return active_drive_id


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _resolve_drive_selector_or_die(
    app_runtime: Any,
    drive_id: str | None,
    latest: bool,
    child_run_id: str | None = None,
):
    """Resolve a drive selector and validate selector exclusivity.

    Authority: docs/RFC-orch-drive.md sections 7.3, 7.4
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md section 7.7

    Selector composition rules (per RFC §7.4):
    - --child-run-id is exclusive with positional DRIVE_ID and --latest
    - When an active drive exists, inspection/control defaults target the drive
    - --latest selects the most recently updated drive for the plan

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: Explicit drive ID, or None.
        latest: Whether --latest was specified.
        child_run_id: Optional child-run selector for drill-down.

    Returns:
        The resolved drive_id string.

    Raises:
        typer.Exit: On selector conflict or missing selector.
    """
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
    )
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)

    return resolved_drive_id


# @shell_orchestration: validation delegates to drive store/inspection boundary and maps failures to Typer exit.
def _validate_child_run_scope_or_die(
    app_runtime: Any,
    drive_id: str,
    child_run_id: str | None,
) -> None:
    """Validate that a child run belongs to the specified drive.

    Authority: docs/RFC-orch-drive.md section 7.4

    Per RFC: "a child-run selector that does not belong to the selected
    drive must fail with exit code 2."

    Args:
        app_runtime: The orchestration app runtime.
        drive_id: The drive to validate against.
        child_run_id: The child run to validate, or None (no-op).
    """
    if child_run_id is None:
        return
    from vectl.orchestration.inspection_queries import ChildRunScopeError

    try:
        store = app_runtime._drive_store()
        from vectl.orchestration.inspection_queries import validate_child_run_in_drive

        validate_child_run_in_drive(drive_id, child_run_id, drive_store=store)
    except ChildRunScopeError as exc:
        _die(str(exc), code=2)


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _drive_recovery_preview_has_work(preview: Any):
    """Return True when a dry-run recovery preview found recoverable work."""

    if getattr(preview, "recovered_child_run_ids", ()):
        return True
    if getattr(preview, "failed_child_run_ids", ()):
        return True
    notes = tuple(getattr(preview, "conflict_resolutions", ()) or ())
    if any(note != "dry-run: no changes applied" for note in notes):
        return True
    return False


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _drive_recovery_preview_is_orphaned_claim_only(preview: Any, notes_lower: str):
    """Return True for the narrow orphaned-claim recovery case.

    A previous foreground supervisor can die after a child run reaches a
    terminal state but before the drive releases the core claim.  Recovery can
    prove that state when there are no child runs to recover/fail and every
    recovery note only releases an orphaned drive claim with no active child
    remaining.  That action is safe to auto-apply because it removes stale
    orchestration bookkeeping; it does not mark work complete or discard a live
    child process.
    """

    notes = tuple(
        str(note)
        for note in getattr(preview, "conflict_resolutions", ()) or ()
        if str(note) != "dry-run: no changes applied"
    )
    summary = str(getattr(preview, "summary", "")).lower()
    barrier = getattr(preview, "barrier", None)
    barrier_reason = (
        barrier.get("reason") if isinstance(barrier, dict) else getattr(barrier, "reason", None)
    )
    allowed_barrier = barrier_reason in {
        None,
        "runtime_failure",
        "review_failed",
        "recovery_gate",
    }
    return (
        not getattr(preview, "recovered_child_run_ids", ())
        and not getattr(preview, "failed_child_run_ids", ())
        and bool(notes)
        and all(
            "released orphaned drive claim" in note.lower()
            and "no active child run remains" in note.lower()
            for note in notes
        )
        and ("transition to resolving" in summary or "transition to running" in summary)
        and allowed_barrier
        and "operator required" not in notes_lower
        and "merge conflict" not in notes_lower
    )


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _drive_recovery_preview_requires_operator(preview: Any):
    """Conservatively identify recovery previews that should not auto-apply."""

    notes = "\n".join(str(note) for note in getattr(preview, "conflict_resolutions", ()) or ())
    notes_lower = notes.lower()
    if _drive_recovery_preview_is_orphaned_claim_only(preview, notes_lower):
        return False

    status = str(getattr(preview, "status", ""))
    terminal_or_operator = status in {
        "blocked_operator",
        "failed_unrecoverable",
        "halted",
        "stopped",
    }
    unsafe_notes = any(token in notes_lower for token in ("merge conflict", "operator", "manual"))
    failed_child_run_ids = tuple(
        str(run_id) for run_id in getattr(preview, "failed_child_run_ids", ()) or ()
    )
    # Auto-retry only the narrow stale-run case recover_drive can prove:
    # persisted pending/running child, no live process, no terminal artifact.
    # Everything else stays operator-owned.
    retryable_failed_child = bool(failed_child_run_ids) and (
        "live process missing" in notes_lower
        and "no terminal artifact" in notes_lower
        and "stale" in notes_lower
        and all(run_id.lower() in notes_lower for run_id in failed_child_run_ids)
    )
    return terminal_or_operator or unsafe_notes or (
        bool(failed_child_run_ids) and not retryable_failed_child
    )


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _attempt_agent_assisted_recovery_preview(
    app_runtime: Any,
    drive_id: str,
    preview: Any,
):
    """Let resolver automation try once, then return a fresh recovery preview."""

    assistant = getattr(app_runtime, "attempt_agent_assisted_drive_recovery", None)
    if not callable(assistant):
        return preview, {"attempted": False, "reason": "not_supported"}
    try:
        result = assistant(
            drive_id=drive_id,
            reason=str(getattr(preview, "summary", "") or "drive recovery requires operator"),
        )
        followup = app_runtime.recover_drive(drive_id=drive_id, dry_run=True)
        return followup, {
            "attempted": True,
            "result": _json_ready(result),
        }
    except Exception as exc:
        return preview, {
            "attempted": True,
            "error": str(exc),
        }


# @shell_complexity: recovery branches preserve safe auto-apply versus operator-required drive behavior.
# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _auto_recover_active_drive(app_runtime: Any, drive_id: str):
    """Attempt safe startup recovery for an already-active drive.

    Returns a blocking payload when recovery needs operator attention.  Returns
    ``None`` when no recovery is needed or safe recovery was applied.
    """

    preview = app_runtime.recover_drive(drive_id=drive_id, dry_run=True)
    if not _drive_recovery_preview_has_work(preview):
        return None
    agent_assisted: dict[str, Any] | None = None
    if _drive_recovery_preview_requires_operator(preview):
        preview, agent_assisted = _attempt_agent_assisted_recovery_preview(
            app_runtime,
            drive_id,
            preview,
        )
        if not _drive_recovery_preview_has_work(preview):
            return {
                "drive_id": drive_id,
                "status": getattr(preview, "status", "running"),
                "summary": getattr(preview, "summary", "agent-assisted recovery cleared blocker"),
                "auto_recovery": {
                    "applied": False,
                    "agent_assisted": agent_assisted,
                    "conflict_resolutions": tuple(
                        getattr(preview, "conflict_resolutions", ()) or ()
                    ),
                },
            }
    if _drive_recovery_preview_requires_operator(preview):
        payload = _drive_action_guidance(_json_ready(preview))
        payload["auto_recovery"] = {
            "applied": False,
            "reason": "operator_required",
            "preview_summary": getattr(preview, "summary", ""),
            "agent_assisted": agent_assisted,
        }
        payload["human_required"] = True
        payload["reason_code"] = payload.get("reason_code") or "recovery_requires_operator"
        return payload

    applied = app_runtime.recover_drive(drive_id=drive_id, dry_run=False)
    return {
        "drive_id": drive_id,
        "status": getattr(applied, "status", "running"),
        "summary": getattr(applied, "summary", "drive auto-recovery applied"),
        "auto_recovery": {
            "applied": True,
            "conflict_resolutions": tuple(getattr(applied, "conflict_resolutions", ()) or ()),
            "recovered_child_run_ids": tuple(getattr(applied, "recovered_child_run_ids", ()) or ()),
            "failed_child_run_ids": tuple(getattr(applied, "failed_child_run_ids", ()) or ()),
            "agent_assisted": agent_assisted,
        },
    }


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _merge_auto_recovery_notice(result: Any, notice: dict[str, Any] | None):
    """Attach an auto-recovery note to a drive result payload."""

    if notice is None:
        return result
    payload = _json_ready(result)
    if not isinstance(payload, dict):
        return result
    payload["auto_recovery"] = notice.get("auto_recovery", notice)
    return payload


# @shell_complexity: drive callback preserves admission, safe recovery, progress, jsonl, and quiet-mode behavior.

__all__ = [name for name in globals() if not name.startswith("__")]
