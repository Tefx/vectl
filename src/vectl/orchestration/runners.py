"""Runner backend substrate for orchestration runtime.

Authority: docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md sections 8-10
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from vectl.orchestration.contracts import ExecutionRequest


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


class RunnerNotFoundError(RunnerError):
    """Raised when runtime requests an unknown runner identifier."""


class RunnerLaunchError(RunnerError):
    """Raised when runner launch mechanics fail."""


class RunnerPollError(RunnerError):
    """Raised when runner poll mechanics fail."""


class RunnerCancelError(RunnerError):
    """Raised when runner cancellation mechanics fail."""


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
                f"Runner '{self.runner_id}' failed to launch for step '{request.step_id}': {exc}"
            ) from exc

        self._processes[run_id] = process
        return RunnerLaunchResult(
            handle=RunnerHandle(
                runner=self.runner_id, run_id=run_id, session_id=request.session_id
            ),
            initial_summary=f"Runner '{self.runner_id}' started",
        )

    def resume(self, request: ExecutionRequest, workspace: Path) -> RunnerLaunchResult:
        raise RunnerLaunchError(f"Runner '{self.runner_id}' does not support resume semantics")

    def poll(self, handle: RunnerHandle) -> RunnerPollResult:
        process = self._processes.get(handle.run_id)
        if process is None:
            raise RunnerPollError(f"Unknown runner handle '{handle.run_id}' for '{self.runner_id}'")

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
                f"Failed to read runner output for '{handle.run_id}': {exc}"
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
                f"Failed to terminate runner handle '{handle.run_id}': {exc}"
            ) from exc
