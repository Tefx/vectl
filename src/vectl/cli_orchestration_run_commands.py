"""Internal implementation slice split from cli_orchestration.py."""

from __future__ import annotations

from vectl.cli_orchestration_common import *
from vectl.cli_orchestration_runtime_helpers import *
from vectl.cli_orchestration_drive_helpers import *

def orch_run(
    step_id: str | None = typer.Argument(
        None, help="Step ID to run (auto-selects next if omitted)."
    ),
    agent: str | None = OrchAgentOption,
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Start or resume an orchestration run.

    Contract authority: orch_app.py::OrchestrationApp.run()

    Authority: docs/RFC-orch-drive.md section 7.1
      - orch run must fail with exit code 2 if an active drive exists for
        the same plan
      - error text must include the active drive_id
      - error text must instruct the operator to use drive-scoped commands

    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6
      - exit 2 = not found / validation boundary for active-drive rejection
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: run start validation completed",
                "step_id": step_id,
                "agent": agent,
            },
            mode,
        )
        return
    try:
        result = app_runtime.run(step_id=step_id, agent=agent)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        # Authority: RFC-orch-drive.md section 7.1
        # "orch run must fail with exit code 2 if an active drive exists"
        message = result.message.lower()
        if "active drive exists" in message or "drive_id=" in message:
            _die(result.message, code=2)
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch resume ---


# @shell_complexity: command callback preserves RUN_ID/--latest selector validation and exit codes.
def orch_resume(
    run_id: str | None = typer.Argument(None, help="Run identifier to resume."),
    latest: bool = OrchLatestOption,
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Resume an existing orchestration run from artifacts.

    Contract authority: orch_app.py::OrchestrationApp.resume()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.1, §6
      §7.1 — resume accepts [RUN_ID|--latest]
      §6   — exit 2 = not found, exit 3 = validation
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    if run_id is None and not latest:
        _die("Resume requires explicit selector: provide RUN_ID or --latest", code=3)
    resolved_run_id = run_id
    if latest:
        resolved_run_id = _resolve_latest_run_id(app_runtime)
    if latest and resolved_run_id is None:
        _die("No runs available for --latest selector", code=2)
    if resolved_run_id is None:
        _die("Run selector required: provide RUN_ID or --latest")
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: resume validation completed",
                "run_id": resolved_run_id,
            },
            mode,
        )
        return
    assert resolved_run_id is not None
    try:
        result = app_runtime.resume(run_id=resolved_run_id)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch recover ---


# @shell_complexity: command callback preserves recover selector validation, dry-run, and output behavior.
def orch_recover(
    run_id: str | None = typer.Argument(None, help="Run identifier to recover."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Step ID to recover."),
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    _yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """Recover orchestration state from continuity artifacts.

    Contract authority: orch_app.py::OrchestrationApp.recover()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.1, §6
      §7.1 — recover accepts [RUN_ID|--latest]
      §6   — exit 2 = not found
    """
    del _yes
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    if run_id is None and not latest:
        _die("Recover requires explicit selector: provide RUN_ID or --latest", code=3)
    resolved_run_id = run_id
    if latest:
        resolved_run_id = _resolve_latest_run_id(app_runtime)
    if latest and resolved_run_id is None:
        _die("No runs available for --latest selector", code=2)
    resolved_step_id = step_id
    if resolved_step_id is None and resolved_run_id is not None:
        resolved_step_id = _step_id_for_run(app_runtime, resolved_run_id)
    try:
        result = app_runtime.recover(step_id=resolved_step_id, dry_run=dry_run)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch runs ---


def orch_runs(
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    status: str | None = typer.Option(None, "--status", help="Optional run status filter."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum runs to show."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """List orchestration runs.

    Contract authority: orch_app.py::OrchestrationApp.runs()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    runs = app_runtime.runs(step_id=step_id, limit=limit)
    if status is not None:
        runs = tuple(run for run in runs if run.status == status)
    _emit_orch_payload(runs, mode)


# --- vectl orch prune ---


# @shell_complexity: command callback preserves mutually exclusive pruning selectors and dry-run output.
def orch_prune(
    before: float | None = typer.Option(None, "--before", help="Unix timestamp threshold."),
    older_than: int | None = typer.Option(
        None,
        "--older-than",
        help="Prune entries older than N days.",
    ),
    dry_run: bool = OrchDryRunOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    force: bool = typer.Option(False, "--force", "-y", help="Skip confirmation prompt."),
    plan: Path | None = OrchPlanOption,
) -> None:
    """Prune old runs and artifacts.

    Contract authority: orch_app.py::OrchestrationApp.prune()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    if older_than is not None and before is not None:
        _die("Specify either --before or --older-than, not both")
    resolved_before = before
    if older_than is not None:
        resolved_before = time.time() - float(older_than) * 86400.0
    if dry_run:
        _emit_orch_payload(
            {
                "success": True,
                "message": "Dry-run only: prune candidate scan completed",
                "before": resolved_before,
            },
            mode,
        )
        return
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    result = app_runtime.prune(before=resolved_before, force=force)
    if not result.success:
        _die(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch migration (cutover validate / state advance) ---


# @shell_orchestration: Typer callback delegates to orchestration app cutover boundary and emits CLI payloads.
def orch_migration_validate_cutover(
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Validate cutover readiness against migration retirement criteria.

    Contract authority: recovery.py::CutoverValidator.validate_cutover_readiness()
    """

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    result = app_runtime.cutover_validate()
    _emit_orch_payload(result, mode)


# @shell_complexity: command callback maps public migration enum values to legacy bridge statuses.
def orch_migration_advance_state(
    status: _OrchMigrationState = _OrchMigrationStateArgument,
    legacy_run_id: str | None = typer.Option(
        None,
        "--legacy-run-id",
        help="Optional imported legacy run ID scope. Omit to update all imported runs.",
    ),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Advance imported legacy migration state via canonical bridge wiring.

    Contract authority: recovery.py::RunStoreLegacyRunBridge.set_migration_status()
    """

    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    mapped_status: LegacyRunStatus
    if status is _OrchMigrationState.PARALLEL:
        mapped_status = LegacyRunStatus.PARALLEL
    elif status is _OrchMigrationState.PREFERRED:
        mapped_status = LegacyRunStatus.PREFERRED
    elif status is _OrchMigrationState.DEPRECATED:
        mapped_status = LegacyRunStatus.DEPRECATED
    else:
        mapped_status = LegacyRunStatus.RETIRED

    result = app_runtime.migration_advance_state(
        status=mapped_status,
        legacy_run_id=legacy_run_id,
    )
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch inspect (status / events / logs / artifacts / actions) ---


# @shell_complexity: command callback preserves drive-vs-legacy selector routing and watch output.
def orch_inspect_status(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Step ID to inspect."),
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect current orchestration status.

    Contract authority: orch_app.py::OrchestrationApp.inspect_status()
    Selector safety authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.2, §6

    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive_id or child_run_id is provided, or --latest
    # selects a drive, or active drive exists as default, use drive inspection.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4 — "--latest resolves to
    # active drive scope, not single-run scope, when a drive exists."
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_status(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        _emit_orch_payload(payload, mode)
        if watch:
            payload = app_runtime.inspect_drive_status(
                drive_id=resolved_drive_id, child_run_id=child_run_id
            )
            _emit_orch_payload(payload, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if latest and resolved_run is None:
        _die("No runs available for --latest selector", code=2)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_status(step_id=step_id)
    _emit_orch_payload(payload, mode)
    if watch:
        payload = app_runtime.inspect_status(step_id=step_id)
        _emit_orch_payload(payload, mode)


# @shell_complexity: command callback preserves drive-vs-legacy selector routing and json/jsonl modes.
def orch_inspect_events(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    follow: bool = OrchFollowOption,
    jsonl_flag: bool = OrchJsonlOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum events to show."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect orchestration events.

    Contract authority: orch_app.py::OrchestrationApp.inspect_events()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=jsonl_flag)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_events(
            drive_id=resolved_drive_id, child_run_id=child_run_id, limit=limit
        )
        _emit_orch_payload(payload.data, mode)
        if follow:
            payload = app_runtime.inspect_drive_events(
                drive_id=resolved_drive_id, child_run_id=child_run_id, limit=limit
            )
            _emit_orch_payload(payload.data, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_events(step_id=step_id, limit=limit)
    _emit_orch_payload(payload.data, mode)
    if follow:
        payload = app_runtime.inspect_events(step_id=step_id, limit=limit)
        _emit_orch_payload(payload.data, mode)


# @shell_complexity: command callback preserves drive-vs-legacy log selection, tailing, and follow behavior.

__all__ = [name for name in globals() if not name.startswith("__")]
