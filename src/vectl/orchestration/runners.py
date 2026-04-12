"""Runner backend substrate for orchestration runtime.

Authority: docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md sections 8-10
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from vectl.orchestration.contracts import (
    ExecutionRequest,
    OpenCodeLaunchConfig,
    PromptArtifactPaths,
    _RUNNER_PROMPT_WORKSPACE_RELATIVE,
)
from vectl.orchestration.prompt_materialization import (
    build_opencode_launch_argv,
    build_opencode_launch_env,
    build_runner_handoff_env,
    resolve_prompt_artifact_paths,
)


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


@dataclass
class OpenCodeRunner:
    """Dedicated runner adapter for OpenCode orchestration execution.

    Authority: docs/RFC-opencode-orchestration-runner.md sections 9, 15

    This runner implements the frozen OpenCode CLI contract:

    - **Start mode** (no session_id):
      ``opencode run --format json --dir <workspace> --agent <agent_id>
      --file .vectl/orch/runner_prompt.md "Read the attached runner prompt..."``

    - **Resume mode** (with session_id):
      ``opencode run --format json --dir <workspace> --session <session_id>
      --agent <agent_id> --file .vectl/orch/runner_prompt.md
      "Continue this session..."``

    - ``--continue`` is explicitly forbidden; only ``--session <id>``
      provides deterministic session continuation (RFC §15.2).

    The launch command is built by delegating to the frozen contract in
    ``prompt_materialization.build_opencode_launch_argv``.

    Args:
        artifact_root: Root directory for run artifacts (e.g. ``.vectl/runs``).
            Used to resolve prompt artifact paths for launch environment
            variable construction.
        config: Launch configuration. Defaults to ``OpenCodeLaunchConfig()``
            which pins the frozen command flags from the RFC.
    """

    runner_id: str = "opencode"
    artifact_root: Path = field(default_factory=lambda: Path(".vectl/runs"))
    config: OpenCodeLaunchConfig = field(default_factory=OpenCodeLaunchConfig)
    _processes: dict[str, subprocess.Popen[str]] = field(default_factory=dict)

    def capabilities(self) -> RunnerCapabilities:
        """OpenCode supports resume (via ``--session``), cancel, but not streaming.

        Authority: docs/RFC-opencode-orchestration-runner.md section 9.3
        """
        return RunnerCapabilities(
            runner_id=self.runner_id,
            supports_resume=True,
            supports_cancel=True,
            supports_streaming=False,
        )

    def _build_launch_argv(
        self,
        *,
        request: ExecutionRequest,
        workspace: Path,
        session_id: str | None = None,
    ) -> tuple[str, ...]:
        """Build the OpenCode launch argv for start or resume.

        Authority: docs/RFC-opencode-orchestration-runner.md sections 9.2, 9.3

        Delegates to ``build_opencode_launch_argv`` from the prompt materialization
        module, which uses the frozen ``OpenCodeLaunchConfig``.

        Args:
            request: ExecutionRequest providing agent_id and session context.
            workspace: Absolute path to the execution workspace.
            session_id: Session identifier for resume, or None for start.

        Returns:
            Tuple of argv strings for subprocess launch.
        """
        return build_opencode_launch_argv(
            workspace=workspace,
            agent_id=request.agent_id or "opencode",
            session_id=session_id,
            config=self.config,
        )

    def _build_launch_env(
        self,
        *,
        request: ExecutionRequest,
        workspace: Path,
    ) -> dict[str, str]:
        """Build the process environment for OpenCode launch.

        Merges the required ``VECTL_ORCH_*`` handoff variables into the
        parent environment.

        Args:
            request: ExecutionRequest providing run identity and prompt paths.
            workspace: Absolute path to the execution workspace.

        Returns:
            Complete environment dict for subprocess launch.
        """
        run_id = self._resolve_handoff_run_id(request)
        paths = self._resolve_handoff_paths(
            request=request,
            workspace=workspace,
            run_id=run_id,
        )
        handoff_env = build_runner_handoff_env(
            run_id=run_id,
            step_id=request.step_id,
            agent_id=request.agent_id or "opencode",
            artifact_paths=paths,
        )
        return build_opencode_launch_env(handoff_env=handoff_env)

    @staticmethod
    def _run_id_from_work_refs(work_refs: tuple[str, ...]) -> str | None:
        for ref in work_refs:
            if ref.startswith("run_id="):
                value = ref.partition("=")[2].strip()
                if value:
                    return value
        return None

    @staticmethod
    def _run_id_from_prompt_bundle_path(prompt_bundle_path: str) -> str | None:
        if not prompt_bundle_path:
            return None
        path = Path(prompt_bundle_path)
        # Expected: <artifact_root>/<run_id>/input/prompt_bundle.json
        if len(path.parts) < 3:
            return None
        parent = path.parent
        if parent.name != "input":
            return None
        run_id = parent.parent.name
        return run_id or None

    def _resolve_handoff_run_id(self, request: ExecutionRequest) -> str:
        run_id = self._run_id_from_work_refs(request.work_refs)
        if run_id:
            return run_id
        run_id = self._run_id_from_prompt_bundle_path(request.prompt_bundle_path)
        if run_id:
            return run_id
        return request.step_id

    def _resolve_handoff_paths(
        self,
        *,
        request: ExecutionRequest,
        workspace: Path,
        run_id: str,
    ) -> PromptArtifactPaths:
        if request.prompt_bundle_path and request.runner_prompt_path:
            return PromptArtifactPaths(
                prompt_bundle_path=request.prompt_bundle_path,
                runner_prompt_path=request.runner_prompt_path,
                workspace_prompt_path=str(workspace / _RUNNER_PROMPT_WORKSPACE_RELATIVE),
                workspace_prompt_relative=_RUNNER_PROMPT_WORKSPACE_RELATIVE,
            )
        return resolve_prompt_artifact_paths(
            artifact_root=self.artifact_root,
            run_id=run_id,
            workspace=workspace,
        )

    def _spawn_process(
        self,
        *,
        request: ExecutionRequest,
        workspace: Path,
        session_id: str | None,
    ) -> RunnerLaunchResult:
        """Spawn an OpenCode subprocess for launch or resume.

        Args:
            request: ExecutionRequest for this execution.
            workspace: Absolute path to the execution workspace.
            session_id: Session identifier for resume, or None for start.

        Returns:
            RunnerLaunchResult with the process handle.

        Raises:
            RunnerLaunchError: If the subprocess cannot be started.
        """
        run_id = f"run-{request.step_id}-{uuid.uuid4().hex[:8]}"
        argv = self._build_launch_argv(request=request, workspace=workspace, session_id=session_id)
        env = self._build_launch_env(request=request, workspace=workspace)

        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise RunnerLaunchError(
                runner_id=self.runner_id,
                reason="resource_unavailable",
                detail=(
                    f"failed to launch OpenCode for step '{request.step_id}' "
                    f"in workspace '{workspace}': {exc}"
                ),
            ) from exc

        self._processes[run_id] = process
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner=self.runner_id,
                run_id=run_id,
                session_id=session_id,
            ),
            initial_summary=f"OpenCode runner started for step '{request.step_id}'",
        )

    def launch(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        """Launch a fresh OpenCode execution in the prepared workspace.

        Authority: docs/RFC-opencode-orchestration-runner.md section 9.2

        Starts OpenCode in non-interactive one-shot mode using the frozen
        launch command contract. The workspace must contain a materialized
        prompt at ``.vectl/orch/runner_prompt.md``.

        Args:
            request: ExecutionRequest for this execution. Must have
                ``request_mode="start"`` and ``session_id=None``.
            workspace: Absolute path to the prepared workspace directory.

        Returns:
            RunnerLaunchResult with handle and initial summary.

        Raises:
            RunnerLaunchError: If subprocess spawn fails.
            OpenCodeContinueForbiddenError: If ``--continue`` semantics
                are detected (this should never happen for start mode but
                is defended against).
        """
        return self._spawn_process(
            request=request,
            workspace=workspace,
            session_id=None,
        )

    def resume(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        """Resume an existing OpenCode session in the prepared workspace.

        Authority: docs/RFC-opencode-orchestration-runner.md section 9.3

        Uses ``--session <session_id>`` for deterministic session continuation.
        ``--continue`` is explicitly forbidden (RFC §15.2).

        Args:
            request: ExecutionRequest for this execution. Must have
                ``request_mode="resume"`` and a non-empty ``session_id``.
            workspace: Absolute path to the prepared workspace directory.

        Returns:
            RunnerLaunchResult with handle and initial summary.

        Raises:
            RunnerResumeError: If session_id is missing or empty.
            OpenCodeContinueForbiddenError: If ``--continue`` semantics
                are attempted instead of ``--session``.
            RunnerLaunchError: If subprocess spawn fails.
        """
        if not request.session_id:
            raise RunnerResumeError(
                runner_id=self.runner_id,
                reason="session_missing",
                detail=(
                    f"resume requires a session_id for step '{request.step_id}'; "
                    f"--continue is forbidden in orchestration (RFC §15.2)"
                ),
            )
        return self._spawn_process(
            request=request,
            workspace=workspace,
            session_id=request.session_id,
        )

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        """Poll OpenCode execution state without blocking.

        Authority: docs/RFC-opencode-orchestration-runner.md section 9.4

        Returns ``running`` while the process is active, and a terminal
        status when the subprocess completes. Preserves stdout/stderr refs
        as evidence and session metadata if available.

        Args:
            handle: RunnerHandle from launch() or resume().

        Returns:
            RunnerPollResult with status, output_summary, and evidence refs.

        Raises:
            RunnerPollError: If the handle is unknown or output cannot be read.
        """
        process = self._processes.get(handle.run_id)
        if process is None:
            raise RunnerPollError(
                runner_id=self.runner_id,
                reason="handle_unknown",
                detail=f"unknown OpenCode runner handle '{handle.run_id}'",
            )

        returncode = process.poll()
        if returncode is None:
            try:
                returncode = process.wait(timeout=0.01)
            except subprocess.TimeoutExpired:
                return RunnerPollResult(
                    status="running", output_summary="OpenCode runner still active"
                )

        try:
            stdout = process.stdout.read() if process.stdout else ""
            stderr = process.stderr.read() if process.stderr else ""
        except OSError as exc:
            raise RunnerPollError(
                runner_id=self.runner_id,
                reason="transport_failed",
                detail=f"failed to read output for OpenCode handle '{handle.run_id}': {exc}",
            ) from exc
        finally:
            self._processes.pop(handle.run_id, None)

        evidence_refs: list[str] = []
        if stdout:
            evidence_refs.append(f"stdout:{len(stdout)} chars")
        if stderr:
            evidence_refs.append(f"stderr:{len(stderr)} chars")

        if returncode == 0:
            status: Literal["success", "fail", "stall", "transport_error"] = "success"
            summary = "OpenCode completed successfully (exit 0)"
        elif returncode in (-2, -15, -9):
            status = "stall"
            summary = f"OpenCode terminated by signal {abs(returncode)}"
        else:
            status = "fail"
            summary = f"OpenCode failed with exit code {returncode}"

        if stderr and status != "success":
            summary = f"{summary}; stderr={stderr.strip()[:500]}"
        elif stdout and status == "success":
            summary = f"{summary}; stdout={stdout.strip()[:500]}"

        return RunnerPollResult(
            status=status,
            output_summary=summary,
            session_id=handle.session_id,
            evidence_refs=tuple(evidence_refs),
        )

    def cancel(self, handle: RunnerHandle) -> None:
        """Attempt graceful termination of the OpenCode process.

        Authority: docs/RFC-opencode-orchestration-runner.md section 9.5

        Sends SIGTERM via ``process.terminate()``. Does not silently
        discard persisted session metadata needed for later recovery.

        Args:
            handle: RunnerHandle to cancel.

        Raises:
            RunnerCancelError: If termination fails due to transport error.
        """
        process = self._processes.get(handle.run_id)
        if process is None:
            return
        try:
            process.terminate()
        except OSError as exc:
            raise RunnerCancelError(
                runner_id=self.runner_id,
                reason="transport_failed",
                detail=f"failed to terminate OpenCode runner handle '{handle.run_id}': {exc}",
            ) from exc
