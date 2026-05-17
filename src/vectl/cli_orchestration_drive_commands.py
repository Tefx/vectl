"""Internal implementation slice split from cli_orchestration.py."""

from __future__ import annotations

from vectl.cli_orchestration_common import *
from vectl.cli_orchestration_runtime_helpers import *

from vectl.cli_orchestration_drive_helpers import *

def orch_drive(
    agent: str | None = OrchAgentOption,
    max_parallelism: int = OrchMaxParallelismOption,
    once: bool = typer.Option(
        False,
        "--once",
        help="Run one scheduling loop pass and exit instead of supervising in foreground.",
    ),
    poll_interval_seconds: float = typer.Option(
        2.0,
        "--poll-interval",
        help="Seconds between child-run polls in foreground mode.",
    ),
    status_interval_seconds: float = typer.Option(
        30.0,
        "--status-interval",
        help="Seconds between quiet-period progress summaries in foreground mode.",
    ),
    progress: _DriveProgressMode = typer.Option(
        _DriveProgressMode.AUTO,
        "--progress",
        help="Foreground progress renderer: auto|plain|rich|none.",
        case_sensitive=False,
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress foreground progress; print only the final result in human mode.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Include extra child-run and workspace detail in progress output.",
    ),
    jsonl_flag: bool = OrchJsonlOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Start or resolve a full-plan orchestration drive.

    Authority: docs/RFC-orch-drive.md section 7.2

    If an active drive already exists for the same plan, this command attaches
    to that drive instead of starting a duplicate.  This keeps foreground
    supervision/resolver recovery moving after a previous invocation stopped at
    a drive barrier.

    Contract authority: orch_app.py::OrchestrationApp.start_drive()
    """
    from vectl.orchestration.driver import DriveAdmissionError, MaxParallelismError

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=jsonl_flag)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if max_parallelism < 1 or max_parallelism > 32:
        _die(f"max-parallelism must be between 1 and 32, got {max_parallelism}", code=2)
    drive_id: str
    auto_recovery_notice: dict[str, Any] | None = None
    try:
        result = app_runtime.start_drive(agent=agent or "", max_parallelism=max_parallelism)
        drive_id = result.drive_id
    except DriveAdmissionError as exc:
        drive_id = exc.active_drive_id
        try:
            auto_recovery_notice = _auto_recover_active_drive(app_runtime, drive_id)
        except Exception as recover_exc:
            _orch_internal_error(recover_exc)
            return
        if auto_recovery_notice is not None and auto_recovery_notice.get("human_required"):
            _emit_orch_payload(auto_recovery_notice, mode)
            raise typer.Exit(4)
    except MaxParallelismError as exc:
        _die(str(exc), code=2)
        return
    except Exception as exc:
        _orch_internal_error(exc)
        return
    try:
        if once:
            loop_result = app_runtime.run_drive_loop(drive_id)
        else:
            progress_callback = _build_drive_progress_callback(
                mode=mode,
                progress=progress,
                quiet=quiet,
                verbose=verbose,
            )
            loop_result = app_runtime.run_drive_foreground(
                drive_id,
                poll_interval_seconds=poll_interval_seconds,
                status_interval_seconds=status_interval_seconds,
                progress_callback=progress_callback,
                progress_mode=progress.value,
                verbose=verbose,
            )
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if mode == _OrchOutputMode.JSONL and not once:
        return
    if (
        mode == _OrchOutputMode.HUMAN
        and not once
        and not quiet
        and progress is not _DriveProgressMode.NONE
    ):
        return
    _emit_orch_payload(_merge_auto_recovery_notice(loop_result, auto_recovery_notice), mode)


# @shell_orchestration: Typer callback delegates to drive status boundary and emits CLI payloads.
# @shell_complexity: command callback preserves selector validation, not-found mapping, and output modes.
def orch_drive_status(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show current drive status.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.drive_status()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive status requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.drive_status(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if result.summary.startswith("drive ") and "not found" in result.summary:
        _die(f"Drive not found: {resolved_drive_id}", code=2)
    _emit_orch_payload(result, mode)


# @shell_orchestration: Typer callback delegates to drive child-run boundary and emits CLI payloads.
# @shell_complexity: command callback preserves selector validation, empty-list output, and output modes.
def orch_drive_runs(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """List child runs belonging to a drive.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.drive_runs()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive runs requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        runs = app_runtime.drive_runs(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    if not runs:
        _emit_orch_payload([], mode)
        return
    _emit_orch_payload(runs, mode)


# @shell_complexity: command callback preserves drive selector validation and resume error mapping.
def orch_drive_resume(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Resume an interrupted drive session.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.resume_drive()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive resume requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.resume_drive(drive_id=resolved_drive_id)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    _emit_orch_payload(result, mode)


# @shell_complexity: command callback preserves drive recover selector validation, dry-run, and output behavior.
def orch_drive_recover(
    drive_id: str | None = OrchDriveIdArgument,
    latest: bool = OrchLatestOption,
    dry_run: bool = OrchDryRunDriveOption,
    _yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Recover a drive from interrupted state.

    Authority: docs/RFC-orch-drive.md section 7.2

    Contract authority: orch_app.py::OrchestrationApp.recover_drive()
    """
    del _yes
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if drive_id is not None and latest:
        _die("Specify either DRIVE_ID or --latest, not both")
    if drive_id is None and not latest:
        _die("Drive recover requires explicit selector: provide DRIVE_ID or --latest", code=3)
    resolved_drive_id = drive_id
    if latest:
        resolved_drive_id = _resolve_latest_drive_id(app_runtime)
    if resolved_drive_id is None:
        _die("No drives available for --latest selector", code=2)
    try:
        result = app_runtime.recover_drive(drive_id=resolved_drive_id, dry_run=dry_run)
    except Exception as exc:
        _orch_internal_error(exc)
        return
    _emit_orch_payload(result, mode)
