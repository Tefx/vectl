"""Internal implementation slice split from cli_orchestration.py."""

from __future__ import annotations

from vectl.cli_orchestration_common import *
from vectl.cli_orchestration_runtime_helpers import *
from vectl.cli_orchestration_drive_helpers import *

def orch_inspect_logs(
    latest: bool = OrchLatestOption,
    tail: int = typer.Option(100, "--tail", help="Show only the last N log lines."),
    follow: bool = OrchFollowOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID."),
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect run logs.

    Contract authority: orch_app.py::OrchestrationApp.inspect_logs()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either --run or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_logs(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data[-tail:] if tail >= 0 else payload.data
        _emit_orch_payload(rows, mode)
        if follow:
            rows = app_runtime.inspect_drive_logs(
                drive_id=resolved_drive_id, child_run_id=child_run_id
            ).data[-tail:]
            _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    payload = app_runtime.inspect_logs(run_id=resolved_run, step_id=step_id)
    rows = payload.data[-tail:] if tail >= 0 else payload.data
    _emit_orch_payload(rows, mode)
    if follow:
        rows = app_runtime.inspect_logs(run_id=resolved_run, step_id=step_id).data[-tail:]
        _emit_orch_payload(rows, mode)


# @shell_complexity: command callback preserves drive-vs-legacy artifact selection and kind filtering.
def orch_inspect_artifacts(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    kind: str | None = typer.Option(None, "--kind", help="Optional artifact kind filter token."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Filter by step ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect derived artifacts from runs.

    Contract authority: orch_app.py::OrchestrationApp.inspect_artifacts()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
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
        payload = app_runtime.inspect_drive_artifacts(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data
        if kind is not None:
            rows = tuple(row for row in rows if f"kind={kind}" in row or kind in row)
        _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is not None and step_id is None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    payload = app_runtime.inspect_artifacts(step_id=step_id)
    rows = payload.data
    if kind is not None:
        rows = tuple(row for row in rows if f"kind={kind}" in row or kind in row)
    _emit_orch_payload(rows, mode)


# @shell_complexity: command callback preserves explicit action selector requirements and drive routing.
def orch_inspect_actions(
    latest: bool = OrchLatestOption,
    status: str | None = typer.Option(None, "--status", help="Action status filter token."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
    child_run_id: str | None = typer.Option(
        None,
        "--child-run-id",
        help="Child-run drill-down selector (exclusive with DRIVE_ID and --latest).",
    ),
) -> None:
    """Inspect actions taken during a run.

    Contract authority: orch_app.py::OrchestrationApp.inspect_actions()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.4
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either --run or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        child_run_id=child_run_id,
        allow_active_drive_default=False,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        _validate_child_run_scope_or_die(app_runtime, resolved_drive_id, child_run_id)
        payload = app_runtime.inspect_drive_actions(
            drive_id=resolved_drive_id, child_run_id=child_run_id
        )
        rows = payload.data
        if status is not None:
            rows = tuple(row for row in rows if f"status={status}" in row)
        _emit_orch_payload(rows, mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is None:
        _die("Run selector required for action inspection: use --run or --latest")
    payload = app_runtime.inspect_actions(run_id=resolved_run)
    rows = payload.data
    if status is not None:
        rows = tuple(row for row in rows if f"status={status}" in row)
    _emit_orch_payload(rows, mode)


# --- vectl orch case (list / show / respond) ---


# @shell_complexity: command callback preserves drive/legacy case-list routing, status filtering, and watch behavior.
def orch_case_list(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status: open, resolved, halt.",
    ),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """List cases (blocked/unresolved situations).

    Contract authority: orch_app.py::OrchestrationApp.case_list()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided or active drive exists
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        # Use drive status to get blocked case IDs
        allowed_statuses = {"open", "resolved", "halt"}
        if status is not None and status not in allowed_statuses:
            _die("Invalid --status value. Expected one of: open, resolved, halt")
        drive_status = app_runtime.drive_status(drive_id=resolved_drive_id)
        rows = (
            tuple(f"case_id={cid} status=open" for cid in drive_status.blocked_case_ids)
            if status in (None, "open")
            else ()
        )
        _emit_orch_payload(rows, mode)
        if watch:
            drive_status = app_runtime.drive_status(drive_id=resolved_drive_id)
            rows = (
                tuple(f"case_id={cid} status=open" for cid in drive_status.blocked_case_ids)
                if status in (None, "open")
                else ()
            )
            _emit_orch_payload(rows, mode)
        return

    # Legacy path: keep run/latest validation above; case_list itself is not run-scoped.
    allowed_statuses = {"open", "resolved", "halt"}
    if status is not None and status not in allowed_statuses:
        _die("Invalid --status value. Expected one of: open, resolved, halt")
    typed_status = cast(
        Literal["open", "resolved", "halt"] | None,
        status if status in allowed_statuses else None,
    )
    payload = app_runtime.case_list(status=typed_status)
    _emit_orch_payload(payload, mode)
    if watch:
        _emit_orch_payload(app_runtime.case_list(status=typed_status), mode)


def orch_case_show(
    case_id: str = typer.Argument(..., help="Case identifier to show."),
    watch: bool = OrchWatchOption,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show detail for a specific case.

    Contract authority: orch_app.py::OrchestrationApp.case_show()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    payload = app_runtime.case_show(case_id=case_id)
    _emit_orch_payload(payload, mode)
    if watch:
        _emit_orch_payload(app_runtime.case_show(case_id=case_id), mode)


# @invar:allow dead_param: drive_id is retained as a public Typer positional compatibility placeholder for drive-scoped case responses.
# @shell_complexity: command callback preserves legacy --response alias and structured --action data assembly.
def orch_case_respond(
    case_id: str = typer.Argument(..., help="Case identifier to respond to."),
    action: str | None = typer.Option(None, "--action", help="Bounded operator action token."),
    data: str | None = typer.Option(None, "--data", help="Optional response payload string."),
    reason: str | None = typer.Option(None, "--reason", help="Optional operator reason."),
    response: str | None = typer.Option(
        None, "--response", "-r", help="Legacy response text alias."
    ),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Respond to a case (operator input to resolver).

    Contract authority: orch_app.py::OrchestrationApp.case_respond()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    del drive_id
    resolved_response = response
    if resolved_response is None and action is not None:
        parts = [f"action={action}"]
        if data is not None:
            parts.append(f"data={data}")
        if reason is not None:
            parts.append(f"reason={reason}")
        resolved_response = " ".join(parts)
    if resolved_response is None:
        _die("Missing operator response: provide --action or --response")
    assert resolved_response is not None
    result = app_runtime.case_respond(case_id=case_id, response=resolved_response)
    if not result.success:
        _die(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch control (pause / unpause / stop) ---


# @shell_complexity: command callback preserves drive-default and legacy pause selector behavior.
def orch_control_pause(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", help="Optional pause reason."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Specific step to pause."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Pause orchestration (stop dispatching new work).

    Contract authority: orch_app.py::OrchestrationApp.control_pause()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided, --latest resolves to
    # active drive, or active drive exists as default (no explicit run/step).
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_pause(drive_id=resolved_drive_id, reason=reason)
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if step_id is None and resolved_run is not None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    try:
        result = app_runtime.control_pause(step_id=step_id, reason=reason)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# @shell_complexity: command callback preserves drive-default and legacy unpause selector behavior.
def orch_control_unpause(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", help="Optional unpause reason."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    step_id: str | None = typer.Option(None, "--step", help="Specific step to unpause."),
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Unpause orchestration (resume dispatching).

    Contract authority: orch_app.py::OrchestrationApp.control_unpause()
    Drive-scoped authority: docs/RFC-orch-drive.md §7.3
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: when drive selector is provided, --latest resolves to
    # active drive, or active drive exists as default (no explicit run/step).
    # Authority: docs/RFC-orch-drive.md §7.3, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
        legacy_step_id=step_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_unpause(drive_id=resolved_drive_id, reason=reason)
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if step_id is None and resolved_run is not None:
        step_id = _step_id_for_run(app_runtime, resolved_run)
    try:
        result = app_runtime.control_unpause(step_id=step_id, reason=reason)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# @shell_complexity: command callback preserves drive-default, legacy run, and force-stop semantics.
def orch_control_stop(
    run_id: str | None = typer.Option(None, "--run", help="Specific run ID (legacy selector)."),
    latest: bool = OrchLatestOption,
    reason: str | None = typer.Option(None, "--reason", "-r", help="Reason for stopping."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Immediate stop: cancel active step child runs (RFC §7.3.1).",
    ),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
    drive_id: str | None = typer.Argument(None, help="Drive identifier (omit to use --latest)."),
) -> None:
    """Stop orchestration entirely.

    Contract authority: orch_app.py::OrchestrationApp.control_stop()

    With --force, requests immediate stop semantics per RFC §7.3.1:
    cancel active step child runs, preserve artifacts, allow resolver/planner
    child runs to complete.

    Drive-scoped authority: docs/RFC-orch-drive.md §7.3, §7.3.1
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)

    # Drive-scoped path: --latest resolves to active drive scope.
    # Authority: docs/RFC-orch-drive.md §7.3, §7.3.1, §7.4
    if run_id is not None and latest:
        _die("Specify either RUN_ID or --latest, not both")
    resolved_drive_id = _resolve_optional_drive_scope(
        app_runtime,
        drive_id=drive_id,
        latest=latest,
        allow_active_drive_default=True,
        legacy_run_id=run_id,
    )
    if resolved_drive_id is not None:
        try:
            result = app_runtime.control_drive_stop(
                drive_id=resolved_drive_id, reason=reason, force=force
            )
        except Exception as exc:
            _orch_internal_error(exc)
            return
        if not result.success:
            _orch_die_on_failure(result.message)
        _emit_orch_payload(_enrich_drive_scope_result(result, resolved_drive_id), mode)
        return

    # Legacy run-scoped path
    resolved_run = run_id or (_resolve_latest_run_id(app_runtime) if latest else None)
    if resolved_run is None and latest:
        _die("No runs available for --latest selector", code=2)
    try:
        result = app_runtime.control_stop(run_id=resolved_run, reason=reason, force=force)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if not result.success:
        _orch_die_on_failure(result.message)
    _emit_orch_payload(result, mode)


# --- vectl orch config (show / validate / tools) ---


def orch_config_show(
    effective: bool = typer.Option(False, "--effective", help="Show effective merged config."),
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show current orchestration configuration.

    Contract authority: orch_app.py::OrchestrationApp.config_show()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    try:
        payload = app_runtime.config_show(effective=effective)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)
        return
    if mode == _OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
        return
    _emit_orch_payload(payload, mode)


def orch_config_validate(
    path: Path | None = OrchConfigPathArgument,
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Validate current orchestration configuration.

    Contract authority: orch_app.py::OrchestrationApp.config_validate()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    target_plan = path if path is not None else plan
    app_runtime = _build_orchestration_runtime_app(plan=target_plan)
    payload = app_runtime.config_validate()
    if mode == _OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
    else:
        _emit_orch_payload(payload, mode)
    if not payload.validation_passed:
        raise typer.Exit(3)


def orch_config_tools(
    json_flag: bool = OrchJsonOption,
    output: _OrchOutputMode = OrchOutputOption,
    plan: Path | None = OrchPlanOption,
) -> None:
    """Show registered tool families and allowlist.

    Contract authority: orch_app.py::OrchestrationApp.config_tools()
    """
    mode = _resolve_orch_output_mode(output=output, json_flag=json_flag, jsonl_flag=False)
    app_runtime = _build_orchestration_runtime_app_or_die(plan=plan)
    payload = app_runtime.config_tools()
    if mode == _OrchOutputMode.HUMAN:
        out.print(_esc(payload.show_output))
        return
    _emit_orch_payload(payload, mode)


# ---------------------------------------------------------------------------
# vectl orch drive: drive-scoped orchestration commands
# Authority: docs/RFC-orch-drive.md sections 7.2, 7.3
# ---------------------------------------------------------------------------



__all__ = [name for name in globals() if not name.startswith("__")]
