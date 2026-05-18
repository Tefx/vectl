"""Internal runtime/output helpers split from cli_orchestration.py."""

from __future__ import annotations

from vectl.cli_orchestration_common import *

def _build_orchestration_runtime_app(plan: Path | None):
    """Compose orchestration app using frozen-capable runtime config wiring."""
    root_cli = sys.modules.get("vectl.cli")
    patched = getattr(root_cli, "_build_orchestration_runtime_app", None)
    if patched is not None and patched is not _build_orchestration_runtime_app:
        return patched(plan)

    from vectl.orch_app import AppConfig, build_orchestration_app
    from vectl.orchestration.config import load_orchestration_config

    loaded_config, _ = load_orchestration_config()
    if plan is not None:
        loaded_config = replace(loaded_config, plan_path=plan)

    app_config = AppConfig(
        plan_path=loaded_config.plan_path,
        worktree_base_dir=loaded_config.runtime.workspace_root,
        default_agent=loaded_config.dispatch.default_role_id,
        resolver_timeout_seconds=loaded_config.resolver.invocation_timeout_seconds,
        orchestration_config=loaded_config,
        run_store_root=loaded_config.runtime.artifact_root,
    )
    return build_orchestration_app(app_config)


# @shell_complexity: conflict checks preserve documented --json/--jsonl/--output compatibility.
# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _resolve_orch_output_mode(
    *,
    output: _OrchOutputMode,
    json_flag: bool,
    jsonl_flag: bool,
):
    """Resolve one concrete output mode with conflict checks."""

    if json_flag and jsonl_flag:
        _die("Conflicting output flags: --json and --jsonl are mutually exclusive")
    if json_flag and output != _OrchOutputMode.HUMAN:
        _die("Conflicting output flags: --json cannot be combined with --output")
    if jsonl_flag and output != _OrchOutputMode.HUMAN:
        _die("Conflicting output flags: --jsonl cannot be combined with --output")
    if json_flag:
        return _OrchOutputMode.JSON
    if jsonl_flag:
        return _OrchOutputMode.JSONL
    return output


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _orch_failure_exit_code(message: str):
    """Map orchestration failure messages to documented exit codes.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6
    """

    normalized = message.lower()
    if "not found" in normalized or "no runs available" in normalized:
        return 2
    if "resume refused" in normalized or "recovery required" in normalized:
        return 4
    return 1


def _orch_die_on_failure(message: str) -> None:
    """Exit with explicit orchestration failure-class mapping."""

    _die(message, code=_orch_failure_exit_code(message))


def _orch_internal_error(exc: Exception):
    """Surface unexpected orchestration app exceptions as internal errors."""

    _die(f"Internal orchestration error: {exc}", code=5, cause=exc)


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _build_orchestration_runtime_app_or_die(plan: Path | None):
    """Build orchestration runtime app with internal-error mapping."""

    try:
        return _build_orchestration_runtime_app(plan=plan)
    except Exception as exc:  # pragma: no cover - defensive internal mapping
        _orch_internal_error(exc)


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _enrich_drive_scope_result(result: ControlResult, drive_id: str):
    """Enrich a ControlResult with drive-scoped discrimination metadata.

    Authority: docs/RFC-orch-drive.md §7.4 — flat surfaces must report
    ``scope_kind: "drive"`` and a drive identifier to allow operators to
    verify that ``--latest`` resolved to drive scope, not single-run scope.
    """
    payload: dict[str, Any] = asdict(result)
    payload["scope_kind"] = "drive"
    payload["drive_id"] = drive_id
    return payload


# @shell_complexity: recursive dataclass/container normalization is required for JSON output safety.
# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _json_ready(value: Any):
    """Convert CLI payload into JSON-serializable structure."""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


# @shell_complexity: operator guidance branches encode documented drive terminal/recovery cases.
# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _drive_action_guidance(payload: dict[str, Any]):
    """Add operator-action guidance to drive result payloads.

    This is deliberately a tiny presentation-layer helper: drive state remains
    owned by ``OrchestrationApp``/``DriveDriver`` and the CLI only adds explicit
    next-action fields for humans and JSON automation.
    """

    if "drive_id" not in payload or "status" not in payload:
        return payload
    if "reason_code" in payload and "suggested_action" in payload and "human_required" in payload:
        return payload

    status = str(payload.get("status") or "")
    summary = str(payload.get("summary") or "")
    summary_lower = summary.lower()
    conflict_text = "\n".join(str(note) for note in payload.get("conflict_resolutions") or ())
    conflict_lower = conflict_text.lower()
    failed_child_run_ids = payload.get("failed_child_run_ids") or ()

    reason_code = "ok"
    suggested_action = "continue drive supervision"
    human_required = False

    if "retry limit" in summary_lower or "retry limit" in conflict_lower:
        reason_code = "retry_limit"
        suggested_action = (
            "inspect repeated child-run failures, adjust the step, "
            "then rerun `vectl orch drive`"
        )
        human_required = True
    elif "automation stopped" in summary_lower:
        reason_code = "automation_stalled"
        suggested_action = (
            "inspect the unchanged barrier/case, then rerun `vectl orch drive` "
            "after fixing the blocker"
        )
        human_required = True
    elif failed_child_run_ids:
        reason_code = "drive_recovery_failed_children"
        suggested_action = (
            "run `vectl orch drive-recover --latest --dry-run`, "
            "inspect stale child runs, then rerun drive"
        )
        human_required = True
    elif "runtime non-closure" in summary_lower:
        reason_code = "runtime_nonclosure"
        suggested_action = (
            "inspect the failed child run/case, create or run the remediation step, "
            "then rerun drive"
        )
        human_required = True
    elif status == "blocked_operator":
        reason_code = "operator_required"
        suggested_action = (
            "inspect drive cases and respond via `vectl orch case-*` or rerun "
            "`vectl orch drive-recover --latest --dry-run`"
        )
        human_required = True
    elif status in {"resolving", "replanning"}:
        reason_code = "barrier_active"
        suggested_action = (
            "drive will continue through resolver/planner; if stale, run "
            "`vectl orch drive-recover --latest --dry-run`"
        )
    elif status == "recovering":
        reason_code = "recovery_needed"
        suggested_action = (
            "rerun `vectl orch drive`; it will attempt safe recovery, or run "
            "`vectl orch drive-recover --latest --dry-run`"
        )
    elif status in {"halted", "failed_unrecoverable", "stopped"}:
        reason_code = "terminal_attention_required"
        suggested_action = (
            "inspect drive status/logs and start a new drive only after the "
            "terminal cause is understood"
        )
        human_required = status != "stopped"
    elif status == "completed":
        reason_code = "completed"
        suggested_action = "no action required"

    enriched = dict(payload)
    enriched.setdefault("reason_code", reason_code)
    enriched.setdefault("suggested_action", suggested_action)
    enriched.setdefault("human_required", human_required)
    if human_required or reason_code not in {"ok", "completed"}:
        stop_reason = summary or suggested_action
        enriched.setdefault("stop_reason", f"{reason_code}: {stop_reason}"[:300])
    return enriched


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _drive_payload(value: Any):
    """Convert and enrich drive payloads with action guidance."""

    payload = _json_ready(value)
    if isinstance(payload, dict):
        return _drive_action_guidance(payload)
    if isinstance(payload, list):
        return [
            _drive_action_guidance(item) if isinstance(item, dict) else item
            for item in payload
        ]
    return payload


# @shell_complexity: output emitter preserves human/json/jsonl formatting and ANSI-free JSON paths.
def _emit_orch_payload(value: Any, mode: _OrchOutputMode) -> None:
    """Emit orchestration payload in human/json/jsonl formats."""

    payload = _drive_payload(value)

    if mode == _OrchOutputMode.JSON:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    if mode == _OrchOutputMode.JSONL:
        if isinstance(payload, list):
            for item in payload:
                typer.echo(json.dumps(item, sort_keys=True))
        else:
            typer.echo(json.dumps(payload, sort_keys=True))
        return

    if isinstance(payload, list):
        if not payload:
            out.print("[dim]No results.[/]")
            return
        for item in payload:
            if isinstance(item, dict):
                out.print(" ".join(f"{k}={_esc(str(v))}" for k, v in item.items()))
            else:
                out.print(_esc(str(item)))
        return

    if isinstance(payload, dict):
        out.print("\n".join(f"{k}={_esc(str(v))}" for k, v in payload.items()))
        return

    out.print(_esc(str(payload)))


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _format_duration(seconds: float | int | None):
    """Format seconds as compact HH:MM:SS/MM:SS text for CLI progress."""

    if seconds is None:
        return "--:--"
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _drive_event_time(event: dict[str, Any]):
    timestamp = event.get("timestamp")
    if isinstance(timestamp, (int, float)):
        return time.strftime("%H:%M:%S", time.localtime(timestamp))
    return time.strftime("%H:%M:%S")


def _drive_progress_label(progress: _DriveProgressMode):
    if progress is _DriveProgressMode.AUTO:
        return _DriveProgressMode.RICH if out.is_terminal else _DriveProgressMode.PLAIN
    return progress


# @shell_complexity: status snapshot keeps rich/plain progress rendering in one CLI output boundary.
def _emit_drive_status_snapshot(event: dict[str, Any], *, rich_mode: bool) -> None:
    status = str(event.get("status", "unknown")).upper()
    drive_id = str(event.get("drive_id", ""))
    elapsed = _format_duration(cast(float | int | None, event.get("elapsed_seconds")))
    done = event.get("done_steps", 0)
    total = event.get("total_steps", 0)
    running = event.get("running_count", 0)
    capacity = event.get("max_parallelism", "?")
    ready = event.get("ready_count", 0)
    blocked = event.get("blocked_count", 0)
    cases = event.get("case_count", 0)
    summary = str(event.get("summary", ""))
    header = f"Drive {drive_id}  {status}  {elapsed}"
    row = (
        f"Plan: {done}/{total} complete | Running: {running}/{capacity} | "
        f"Queue: {ready} ready | Blocked: {blocked} | Cases: {cases}"
    )
    if rich_mode:
        style = "green" if status == "COMPLETED" else "yellow" if cases else "cyan"
        out.print(Panel(f"{row}\n[dim]{_esc(summary)}[/]", title=_esc(header), border_style=style))
    else:
        out.print(header)
        out.print(row)
        if summary:
            out.print(f"summary={_esc(summary)}")


# @shell_complexity: event-type branching preserves documented foreground drive progress messages.
# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _format_drive_progress_event(event: dict[str, Any], *, verbose: bool):
    event_type = str(event.get("type", ""))
    ts = _drive_event_time(event)
    step_id = str(event.get("step_id", ""))
    run_id = str(event.get("run_id", ""))

    if event_type == "drive_started":
        return (
            f"Starting drive {event.get('drive_id')}  mode=foreground  "
            f"max_parallelism={event.get('max_parallelism')}  "
            f"poll={event.get('poll_interval_seconds')}s  "
            f"progress={event.get('progress_mode', 'auto')}"
        )
    if event_type == "child_dispatched":
        detail = f" workspace={event.get('workspace')}" if verbose else ""
        return (
            f"{ts}  dispatched  {step_id} -> {event.get('agent')} {run_id}{detail}"
        )
    if event_type == "child_running":
        elapsed = _format_duration(cast(float | int | None, event.get("elapsed_seconds")))
        idle = _format_duration(cast(float | int | None, event.get("idle_seconds")))
        ws = event.get("workspace_changes", 0)
        workspace = f" workspace={event.get('workspace')}" if verbose else ""
        return (
            f"{ts}  still running  {step_id}  "
            f"elapsed={elapsed} idle={idle} ws={ws} files{workspace}"
        )
    if event_type == "child_completed":
        summary = str(event.get("summary", ""))
        if summary and not verbose:
            summary = summary[:160]
        return f"{ts}  completed   child {run_id} status={event.get('status')} {summary}".rstrip()
    if event_type == "step_completed":
        return f"{ts}  step done   {step_id}"
    if event_type == "case_opened":
        return (
            f"{ts}  case opened {event.get('case_id')}  from {step_id}  "
            f"reason={event.get('reason')}"
        )
    if event_type == "drive_blocked":
        return (
            f"BLOCKED  case resolution required\n"
            f"Case(s): {', '.join(cast(list[str], event.get('case_ids', [])))}\n"
            f"Reason: {event.get('summary')}\n"
            f"Next:\n  vectl orch case-list --latest\n  vectl orch drive-status --latest"
        )
    if event_type == "drive_terminal":
        status = str(event.get("status", "unknown")).upper()
        summary = str(event.get("summary", ""))
        if status == "COMPLETED":
            return f"COMPLETED  all reachable work finished\nSummary: {summary}"
        if status == "STOPPED":
            return f"STOPPED  external stop requested\nSummary: {summary}"
        return f"{status}  drive finished\nSummary: {summary}"
    return None


# @shell_complexity: callback selection preserves human/jsonl/no-progress drive modes.
def _build_drive_progress_callback(
    *,
    mode: _OrchOutputMode,
    progress: _DriveProgressMode,
    quiet: bool,
    verbose: bool,
):
    """Return an orch drive progress callback for human/jsonl modes."""

    resolved_progress = _drive_progress_label(progress)
    if mode == _OrchOutputMode.JSON:
        return None

    if mode == _OrchOutputMode.JSONL:
        def _jsonl(event: dict[str, Any]) -> None:
            typer.echo(json.dumps(_json_ready(event), sort_keys=True))

        return _jsonl

    if quiet or resolved_progress is _DriveProgressMode.NONE:
        return None

    rich_mode = resolved_progress is _DriveProgressMode.RICH

    def _human(event: dict[str, Any]) -> None:
        if event.get("type") == "status_snapshot":
            _emit_drive_status_snapshot(event, rich_mode=rich_mode)
            return
        line = _format_drive_progress_event(event, verbose=verbose)
        if line:
            if rich_mode and str(event.get("type", "")).startswith("drive_"):
                out.print(line)
            else:
                out.print(_esc(line))

    return _human


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _resolve_latest_run_id(app_runtime: Any):
    """Resolve latest non-terminal run ID from app boundary."""

    runs = app_runtime.runs(limit=200)
    for run in runs:
        if run.status in {"running", "pending", "stall"}:
            return run.run_id
    if runs:
        return runs[0].run_id
    return None


# @shell_orchestration: CLI helper remains in shell to preserve Typer/Rich/orchestration boundary compatibility.
def _step_id_for_run(app_runtime: Any, run_id: str):
    """Resolve step ID for run selector via app read boundary."""
    root_cli = sys.modules.get("vectl.cli")
    patched = getattr(root_cli, "_step_id_for_run", None)
    if patched is not None and patched is not _step_id_for_run:
        return patched(app_runtime, run_id)

    for run in app_runtime.runs(limit=500):
        if run.run_id == run_id:
            return run.step_id
    return None


# --- vectl orch run ---


# @shell_complexity: command callback preserves dry-run, active-drive, error-code, and output-mode behavior.

__all__ = [name for name in globals() if not name.startswith("__")]
