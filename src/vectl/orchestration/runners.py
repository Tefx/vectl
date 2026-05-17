"""Runner backend substrate for orchestration runtime.

Authority: docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md sections 8-10
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from typing_extensions import TypeAliasType

from vectl.orchestration.contracts import (
    _RUNNER_PROMPT_WORKSPACE_RELATIVE,
    ExecutionRequest,
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
)
from vectl.orchestration.prompt_materialization import (
    build_opencode_launch_argv,
    build_opencode_launch_env,
    build_runner_handoff_env,
    resolve_prompt_artifact_paths,
)
from vectl.orchestration.resolution_reports import validate_resolution_report_payload

_OPENCODE_STDOUT_SUMMARY_LIMIT = 5000
_STRUCTURED_REVIEW_OUTCOMES = {"pass", "needs_fix", "needs_replan", "operator_required"}

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)

# Guard-facing compatibility alias for parser/predicate helpers that must keep
# direct return values consumed by RunnerPollResult and registry adapters.
# Any is deliberately contained to the alias rather than public function bodies.
Result = TypeAliasType("Result", Any, type_params=(_T, _E))


@dataclass(frozen=True)
class RunnerCapabilities:
    """Stable mechanical capabilities of a runner backend adapter.

    Args:
        runner_id: Stable runner namespace.
        supports_resume: Whether resume semantics are supported.
        supports_cancel: Whether cancellation is supported.
        supports_streaming: Whether streaming output is supported.
    """

    runner_id: str
    supports_resume: bool
    supports_cancel: bool
    supports_streaming: bool


@dataclass(frozen=True)
class RunnerHandle:
    """Opaque handle identifying one launched execution."""

    runner: str
    run_id: str
    session_id: str | None


@dataclass(frozen=True)
class RunnerLaunchResult:
    """Launch response with handle and startup summary."""

    handle: RunnerHandle
    initial_summary: str


@dataclass(frozen=True)
class RunnerPollResult:
    """Normalized non-blocking runner poll result."""

    status: Literal["running", "success", "fail", "stall", "transport_error"]
    output_summary: str
    session_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class RunnerError(Exception):
    """Base class for runner-backend mechanical failures."""

    runner_id: str
    reason: str

    def __init__(self, *, runner_id: str, reason: str, detail: str = "") -> None:
        self.runner_id = runner_id
        self.reason = reason
        message = f"runner={runner_id} reason={reason}"
        if detail:
            message = f"{message} detail={detail}"
        super().__init__(message)


class RunnerNotFoundError(RunnerError):
    """Raised when runtime requests an unknown runner identifier."""

    def __init__(
        self,
        message: str | None = None,
        *,
        runner_id: str | None = None,
        detail: str = "",
    ) -> None:
        resolved_runner = runner_id or "unknown"
        resolved_detail = message if message is not None else detail
        super().__init__(
            runner_id=resolved_runner, reason="runner_not_found", detail=resolved_detail
        )


class RunnerLaunchError(RunnerError):
    """Raised when runner launch mechanics fail."""

    reason: Literal[
        "workspace_invalid",
        "request_invalid",
        "resource_unavailable",
        "internal",
    ]

    def __init__(
        self,
        *,
        runner_id: str,
        reason: Literal[
            "workspace_invalid",
            "request_invalid",
            "resource_unavailable",
            "internal",
        ],
        detail: str = "",
    ) -> None:
        super().__init__(runner_id=runner_id, reason=reason, detail=detail)
        self.reason = reason


class RunnerResumeError(RunnerError):
    """Raised when runner resume mechanics fail."""

    reason: Literal[
        "resume_unsupported",
        "session_missing",
        "session_invalid",
        "internal",
    ]

    def __init__(
        self,
        *,
        runner_id: str,
        reason: Literal[
            "resume_unsupported",
            "session_missing",
            "session_invalid",
            "internal",
        ],
        detail: str = "",
    ) -> None:
        super().__init__(runner_id=runner_id, reason=reason, detail=detail)
        self.reason = reason


class RunnerPollError(RunnerError):
    """Raised when runner poll mechanics fail."""

    reason: Literal["handle_unknown", "transport_failed", "internal"]

    def __init__(
        self,
        *,
        runner_id: str,
        reason: Literal["handle_unknown", "transport_failed", "internal"],
        detail: str = "",
    ) -> None:
        super().__init__(runner_id=runner_id, reason=reason, detail=detail)
        self.reason = reason


class RunnerCancelError(RunnerError):
    """Raised when runner cancellation mechanics fail."""

    reason: Literal[
        "cancel_unsupported",
        "already_complete",
        "transport_failed",
        "internal",
    ]

    def __init__(
        self,
        *,
        runner_id: str,
        reason: Literal[
            "cancel_unsupported",
            "already_complete",
            "transport_failed",
            "internal",
        ],
        detail: str = "",
    ) -> None:
        super().__init__(runner_id=runner_id, reason=reason, detail=detail)
        self.reason = reason


class Runner(Protocol):
    """Mechanical runner execution protocol."""

    def capabilities(self) -> RunnerCapabilities:
        """Return stable capabilities for this runner."""
        ...

    def launch(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        """Launch new work in a prepared workspace."""
        ...

    def resume(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        """Resume previously started work in a prepared workspace."""
        ...

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        """Poll runner state without blocking for completion."""
        ...

    def cancel(self, handle: RunnerHandle) -> None:
        """Attempt cancellation of a running handle."""
        ...


class OpenCodeRunnerError(RunnerError):
    """Base class for OpenCode runner specific errors."""

    def __init__(self, *, runner_id: str, reason: str, detail: str = "") -> None:
        super().__init__(runner_id=runner_id, reason=reason, detail=detail)


class OpenCodeContinueForbiddenError(OpenCodeRunnerError):
    """Raised when ``--continue`` semantics are attempted in orchestration.

    Authority: docs/RFC-opencode-orchestration-runner.md section 15.2

    ``--continue`` targets "last session" and is non-deterministic for
    machine recovery. Only ``--session <session_id>`` is allowed for
    deterministic session continuation.
    """

    def __init__(self, *, runner_id: str = "opencode", detail: str = "") -> None:
        super().__init__(
            runner_id=runner_id,
            reason="continue_forbidden",
            detail=detail
            or (
                "--continue is not allowed in orchestration; "
                "use --session <session_id> for deterministic session continuation"
            ),
        )


_RUNNER_ERROR_TAXONOMY = (
    RunnerError,
    OpenCodeRunnerError,
    OpenCodeContinueForbiddenError,
)


# @shell_orchestration: JSON event scanning remains beside OpenCode subprocess output handling to preserve runner parsing compatibility.
def _iter_json_values_from_text(text: str):
    """Yield JSON values embedded in runner stdout."""

    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        char = text[index]
        if char not in "[{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        yield value
        index += max(end, 1)


# @shell_orchestration: Text extraction is runner-output parsing glue coupled to OpenCode event envelopes.
# @shell_complexity: Branches preserve nested dict/list event traversal and text/content compatibility.
def _append_opencode_text_parts(value: object, sink: list[str]) -> None:
    """Collect text payloads from OpenCode JSON event envelopes."""

    if isinstance(value, dict):
        for key in ("text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                sink.append(item.strip())
        for key in ("part", "parts", "message", "messages", "data"):
            if key in value:
                _append_opencode_text_parts(value[key], sink)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _append_opencode_text_parts(item, sink)


# @shell_orchestration: Structured-review detector is runner-output parser glue for OpenCode event envelopes.
def _looks_like_structured_review_payload(value: dict[str, object]) -> Result[bool, ValueError]:
    if {"type", "sessionID", "timestamp", "part"}.intersection(value.keys()):
        return False
    return value.get("review_outcome") in _STRUCTURED_REVIEW_OUTCOMES and isinstance(
        value.get("summary"), str
    )


# @shell_orchestration: Machine-result extraction stays with OpenCode stdout parsing because it normalizes shell runner envelopes.
# @shell_complexity: Branches preserve report validation, structured-review fallback, nested containers, and JSON-in-string parsing.
def _append_machine_result_payloads(value: object, sink: list[dict[str, object]]) -> None:
    """Collect machine-result JSON payloads from arbitrary event values."""

    if isinstance(value, dict):
        try:
            validate_resolution_report_payload(value)
        except ValueError:
            if _looks_like_structured_review_payload(value):
                sink.append(value)
        else:
            sink.append(value)
        for nested in value.values():
            _append_machine_result_payloads(nested, sink)
        return
    if isinstance(value, list | tuple):
        for nested in value:
            _append_machine_result_payloads(nested, sink)
        return
    if isinstance(value, str):
        for nested in _iter_json_values_from_text(value):
            _append_machine_result_payloads(nested, sink)


# @shell_orchestration: Session-id finder is runner-output parser glue for OpenCode event envelopes.
# @shell_complexity: Branches preserve recursive dict/list search across OpenCode event shapes.
def _find_opencode_session_id(value: object) -> Result[str | None, ValueError]:
    if isinstance(value, dict):
        for key in ("sessionID", "session_id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for nested in value.values():
            found = _find_opencode_session_id(nested)
            if found is not None:
                return found
        return None
    if isinstance(value, list | tuple):
        for nested in value:
            found = _find_opencode_session_id(nested)
            if found is not None:
                return found
    return None


# @shell_orchestration: Stdout session-id extraction feeds RunnerPollResult metadata from OpenCode event streams.
def _opencode_session_id_from_stdout(stdout: str) -> Result[str | None, ValueError]:
    session_id: str | None = None
    for value in _iter_json_values_from_text(stdout):
        found = _find_opencode_session_id(value)
        if found is not None:
            session_id = found
    return session_id


# @shell_orchestration: DB path resolver is coupled to OpenCode shell session-summary lookup.
def _opencode_db_path() -> Result[Path, OSError]:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


# @shell_complexity: Branches preserve absent DB, sqlite failures, malformed rows, machine payload priority, and text fallback behavior.
def _summarize_opencode_session_db(session_id: str) -> Result[str | None, sqlite3.Error]:
    db_path = _opencode_db_path()
    if not db_path.exists():
        return None

    machine_payloads: list[dict[str, object]] = []
    text_parts: list[str] = []
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "select data from part where session_id=? order by time_created, id",
            (session_id,),
        )
        for (raw_data,) in rows:
            try:
                value = json.loads(raw_data)
            except (TypeError, json.JSONDecodeError):
                continue
            _append_machine_result_payloads(value, machine_payloads)
            _append_opencode_text_parts(value, text_parts)
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    if machine_payloads:
        return json.dumps(machine_payloads[-1], separators=(",", ":"))
    if text_parts:
        return text_parts[-1].strip()
    return None


# @shell_orchestration: Stdout summarizer normalizes OpenCode subprocess output into RunnerPollResult summaries.
# @shell_complexity: Branches preserve machine-payload priority, DB fallback, text extraction, JSON-only, and raw stdout behavior.
def _summarize_opencode_stdout(stdout: str) -> Result[str, ValueError]:
    """Return runner-visible text from OpenCode JSON stdout when possible."""

    machine_payloads: list[dict[str, object]] = []
    text_parts: list[str] = []
    saw_json_event = False
    session_id: str | None = None
    for value in _iter_json_values_from_text(stdout):
        saw_json_event = True
        found_session_id = _find_opencode_session_id(value)
        if found_session_id is not None:
            session_id = found_session_id
        _append_machine_result_payloads(value, machine_payloads)
        _append_opencode_text_parts(value, text_parts)
    if machine_payloads:
        return json.dumps(machine_payloads[-1], separators=(",", ":"))
    db_summary = None
    if saw_json_event and session_id is not None:
        db_summary = _summarize_opencode_session_db(session_id)
        if db_summary and _is_machine_result_summary(db_summary):
            return db_summary
    if text_parts:
        return text_parts[-1].strip()
    if db_summary:
        return db_summary
    if saw_json_event:
        return "OpenCode emitted JSON event stream without final text"
    return stdout.strip()


# @shell_orchestration: Machine-summary detector is parser glue for RunnerPollResult stdout truncation decisions.
def _is_machine_result_summary(value: str) -> Result[bool, ValueError]:
    """Return whether a stdout summary is a parser-critical machine payload."""

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    try:
        validate_resolution_report_payload(parsed)
    except ValueError:
        return _looks_like_structured_review_payload(parsed)
    return True


@dataclass
class SubprocessRunner:
    """Concrete runner that executes configured commands via subprocess.

    Args:
        runner_id: Logical runner namespace (e.g. "task", "claude").
        command: Command argv used for launch.
    """

    runner_id: str
    command: tuple[str, ...]
    _processes: dict[str, subprocess.Popen[str]] = field(default_factory=dict)

    def capabilities(self) -> RunnerCapabilities:
        return RunnerCapabilities(
            runner_id=self.runner_id,
            supports_resume=False,
            supports_cancel=True,
            supports_streaming=False,
        )

    def launch(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        run_id = f"run-{request.step_id}-{uuid.uuid4().hex[:8]}"
        try:
            process = subprocess.Popen(
                list(self.command),
                cwd=str(workspace),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise RunnerLaunchError(
                runner_id=self.runner_id,
                reason="resource_unavailable",
                detail=(
                    f"failed to launch step '{request.step_id}' in workspace '{workspace}': {exc}"
                ),
            ) from exc

        self._processes[run_id] = process
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner=self.runner_id, run_id=run_id, session_id=request.session_id
            ),
            initial_summary=f"Runner '{self.runner_id}' started",
        )

    def resume(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        raise RunnerResumeError(
            runner_id=self.runner_id,
            reason="resume_unsupported",
            detail=(f"resume unsupported for step '{request.step_id}' in workspace '{workspace}'"),
        )

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        process = self._processes.get(handle.run_id)
        if process is None:
            raise RunnerPollError(
                runner_id=self.runner_id,
                reason="handle_unknown",
                detail=f"unknown runner handle '{handle.run_id}'",
            )

        returncode = process.poll()
        if returncode is None:
            try:
                returncode = process.wait(timeout=0.01)
            except subprocess.TimeoutExpired:
                return RunnerPollResult(status="running", output_summary="Runner still active")

        try:
            stdout = process.stdout.read() if process.stdout else ""
            stderr = process.stderr.read() if process.stderr else ""
        except OSError as exc:
            raise RunnerPollError(
                runner_id=self.runner_id,
                reason="transport_failed",
                detail=f"failed to read output for handle '{handle.run_id}': {exc}",
            ) from exc
        finally:
            self._processes.pop(handle.run_id, None)

        if returncode == 0:
            status: Literal["success", "fail", "stall", "transport_error"] = "success"
            summary = "Runner completed successfully (exit 0)"
        elif returncode in (-2, -15, -9):
            status = "stall"
            summary = f"Runner terminated by signal {abs(returncode)}"
        else:
            status = "fail"
            summary = f"Runner failed with exit code {returncode}"

        if stderr and status != "success":
            summary = f"{summary}; stderr={stderr.strip()[:200]}"
        elif stdout and status == "success":
            summary = f"{summary}; stdout={stdout.strip()[:200]}"

        return RunnerPollResult(
            status=status,
            output_summary=summary,
            session_id=handle.session_id,
        )

    def cancel(self, handle: RunnerHandle) -> None:
        process = self._processes.get(handle.run_id)
        if process is None:
            return
        try:
            process.terminate()
        except OSError as exc:
            raise RunnerCancelError(
                runner_id=self.runner_id,
                reason="transport_failed",
                detail=f"failed to terminate runner handle '{handle.run_id}': {exc}",
            ) from exc

from vectl.orchestration.opencode_runner import OpenCodeRunner
