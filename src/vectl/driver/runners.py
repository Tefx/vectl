"""Runner protocol and core implementations.

Responsibility: Define Runner and RunnerHandle protocols. Provide concrete
implementations for each supported CLI runner. Parse runner output into RunnerResult.

Non-responsibility: Does NOT decide which runner to use (config.route_agent).
Does NOT manage runner lifecycle across the loop (DriverState).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
Blueprint Reference: DRIVER-BLUEPRINT.md Runner Protocol & Implementations

PHASE SCOPE: Core runners only (ClaudeRunner, OpenCodeRunner).
Extended runners (CodexRunner, GeminiRunner) are deferred to
driver-multi-runner-hardening phase.

CONTRACT PURITY: This file pins protocols, type definitions, and stub signatures.
Substantive implementations belong in driver-execution phases, not here.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from .errors import RunnerError, RunnerNotFoundError
from .parsers import ClaudeOutputParser, OpenCodeOutputParser

if TYPE_CHECKING:
    from .config import RunnerConfig


# =============================================================================
# Type Definitions
# =============================================================================


class RunnerStatus(str, Enum):
    """Outcome of a runner execution.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1 (types.py)
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol
    """

    SUCCESS = "success"
    FAIL = "fail"
    STALL = "stall"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True)
class RunnerResult:
    """Parsed output from a completed runner process.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.1 (types.py)
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol
    """

    status: RunnerStatus
    session_id: str | None
    output: str
    elapsed_seconds: float
    exit_code: int | None
    cost_usd: float | None = None
    tokens: dict[str, int] | None = None


# =============================================================================
# Protocols
# =============================================================================


class RunnerHandle(Protocol):
    """Handle to a running CLI subprocess.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol

    Lifecycle:
        - Created by Runner.dispatch()
        - Waited on by asyncio loop (wait_for_any)
        - Killed on shutdown or timeout

    Protocol contract:
        - session_id: Session ID for potential reuse, extracted from runner output
        - pid: Process ID for liveness checks
        - wait(): Wait for process completion, return RunnerResult
        - kill(): Kill the subprocess (idempotent)
        - is_alive(): Return True if process still running
    """

    session_id: str | None
    pid: int | None

    async def wait(self, timeout: float | None = None) -> RunnerResult:
        """Wait for process completion.

        Args:
            timeout: Maximum seconds to wait. None = no timeout.

        Returns:
            RunnerResult with parsed output.

        Raises:
            RunnerError: On timeout or unexpected failure.
        """
        ...

    async def kill(self) -> None:
        """Kill the subprocess. Idempotent."""
        ...

    def is_alive(self) -> bool:
        """Return True if process still running."""
        ...


class Runner(Protocol):
    """Protocol for dispatching work to a CLI agent.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol
    """

    name: str
    """Runner identifier from config.runners key."""

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """Launch a CLI subprocess and return a handle.

        Args:
            prompt: Rendered prompt text for the worker.
            agent: Agent name for --agent flag if supported.
            workdir: Working directory (worktree path).
            session_id: If provided, resume this session.

        Returns:
            RunnerHandle for async waiting.

        Raises:
            RunnerNotFoundError: If runner command not on PATH.
            RunnerError: If subprocess creation fails.
        """
        ...


class OutputParser(Protocol):
    """Parse runner stdout into RunnerResult.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint: DRIVER-BLUEPRINT.md Output Parsers (3 formats)
    """

    def parse(self, stdout: str, elapsed_seconds: float) -> RunnerResult:
        """Parse raw stdout into structured result.

        Args:
            stdout: Raw output from runner process.
            elapsed_seconds: Time elapsed during execution.

        Returns:
            RunnerResult with parsed status, session_id, output, etc.
        """
        ...


class _ClaudeParserAdapter:
    """Adapter from parser module result type to runners module result type."""

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = ClaudeOutputParser()

    def parse(self, stdout: str, elapsed_seconds: float) -> RunnerResult:
        parsed = self._delegate.parse(stdout, elapsed_seconds)
        return RunnerResult(
            status=RunnerStatus(parsed.status.value),
            session_id=parsed.session_id,
            output=parsed.output,
            elapsed_seconds=parsed.elapsed_seconds,
            exit_code=parsed.exit_code,
            cost_usd=parsed.cost_usd,
            tokens=parsed.tokens,
        )


class _OpenCodeParserAdapter:
    """Adapter from parser module result type to runners module result type."""

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = OpenCodeOutputParser()

    def parse(self, stdout: str, elapsed_seconds: float) -> RunnerResult:
        parsed = self._delegate.parse(stdout, elapsed_seconds)
        return RunnerResult(
            status=RunnerStatus(parsed.status.value),
            session_id=parsed.session_id,
            output=parsed.output,
            elapsed_seconds=parsed.elapsed_seconds,
            exit_code=parsed.exit_code,
            cost_usd=parsed.cost_usd,
            tokens=parsed.tokens,
        )


class _SubprocessRunnerHandle:
    """Concrete handle for subprocess-backed runners."""

    __slots__ = (
        "session_id",
        "pid",
        "_process",
        "_parser",
        "_runner_name",
        "_dispatch_started_at",
    )

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        parser: OutputParser,
        runner_name: str,
        dispatch_started_at: float,
        session_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.pid: int | None = process.pid
        self._process = process
        self._parser = parser
        self._runner_name = runner_name
        self._dispatch_started_at = dispatch_started_at

    async def wait(self, timeout: float | None = None) -> RunnerResult:
        """Wait for process completion and parse runner output.

        Args:
            timeout: Maximum seconds to wait. None means no timeout.

        Returns:
            Parsed runner result with real subprocess exit code attached.

        Raises:
            RunnerError: If wait times out or subprocess wait fails.
        """
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._process.communicate(), timeout=timeout
            )
        except TimeoutError as exc:
            await self.kill()
            raise RunnerError(
                self._runner_name,
                "wait",
                f"runner timed out after {timeout}s",
            ) from exc
        except OSError as exc:
            raise RunnerError(
                self._runner_name,
                "wait",
                f"failed while waiting on subprocess: {exc}",
            ) from exc

        elapsed_seconds = time.monotonic() - self._dispatch_started_at
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        parse_input = stdout_text if stdout_text.strip() else stderr_text

        parsed = self._parser.parse(parse_input, elapsed_seconds=elapsed_seconds)
        return_code = self._process.returncode

        status = parsed.status
        # Non-zero subprocess exits cannot remain SUCCESS even if output says so.
        # Source: step gate preview item #2 and #3 (status classification correctness).
        if status == RunnerStatus.SUCCESS and return_code != 0:
            status = RunnerStatus.FAIL

        output = parsed.output
        if stderr_text and stderr_text not in output:
            output = f"{output}\n\n[stderr]\n{stderr_text}".strip()

        self.session_id = parsed.session_id or self.session_id
        return RunnerResult(
            status=status,
            session_id=self.session_id,
            output=output,
            elapsed_seconds=elapsed_seconds,
            exit_code=return_code,
            cost_usd=parsed.cost_usd,
            tokens=parsed.tokens,
        )

    async def kill(self) -> None:
        """Kill subprocess if still running. Idempotent."""
        if self._process.returncode is None:
            self._process.kill()
            await self._process.wait()

    def is_alive(self) -> bool:
        """Return whether subprocess is still running."""
        return self._process.returncode is None


def _format_arg(template: str, *, workdir: str, agent: str) -> str:
    """Replace `{workdir}` and `{agent}` placeholders."""
    # Do targeted placeholder replacement only. We intentionally avoid
    # `str.format(...)` because runner args may contain literal braces
    # (e.g., inline Python/JSON snippets in tests and scripts).
    return template.replace("{workdir}", workdir).replace("{agent}", agent)


def _build_command(
    config: RunnerConfig,
    *,
    workdir: str,
    agent: str,
    session_id: str | None,
) -> list[str]:
    """Build command argv for subprocess dispatch."""
    argv = [config.command]
    argv.extend(_format_arg(arg, workdir=workdir, agent=agent) for arg in config.args)

    if config.prompt_mode == "stdin_dash":
        argv.append("-")

    if session_id is not None and config.resume_flag:
        argv.extend([config.resume_flag, session_id])

    return argv


def _select_output_parser(runner_name: str, output_parser_name: str) -> OutputParser:
    """Resolve configured parser to concrete parser implementation."""
    if output_parser_name == "claude_json":
        return _ClaudeParserAdapter()
    if output_parser_name == "opencode_jsonl":
        return _OpenCodeParserAdapter()

    raise RunnerError(
        runner_name,
        "create",
        f"Unsupported output_parser '{output_parser_name}' for core runner scope",
    )


async def _spawn_process(
    *,
    runner_name: str,
    argv: list[str],
    prompt: str,
    workdir: str,
) -> asyncio.subprocess.Process:
    """Spawn subprocess and write prompt to stdin with explicit close."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
    except FileNotFoundError as exc:
        raise RunnerNotFoundError(runner_name, "dispatch") from exc
    except OSError as exc:
        raise RunnerError(runner_name, "dispatch", f"failed to spawn subprocess: {exc}") from exc

    stdin = process.stdin
    if stdin is not None:
        stdin.write(prompt.encode("utf-8"))
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await stdin.drain()
        stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await stdin.wait_closed()

    return process


# =============================================================================
# Core Runners (Phase 1)
# =============================================================================


CORE_RUNNERS: frozenset[str] = frozenset({"claude", "opencode"})
"""Runner names implemented in the core phase."""


class ClaudeRunner:
    """Runner for Claude-style JSON output CLIs."""

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        self.name = name
        self._config = config
        self._parser = _select_output_parser(name, config.output_parser)

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """Launch a Claude subprocess and return a concrete handle.

        Args:
            prompt: Prompt text piped to stdin.
            agent: Agent identifier for argument templating.
            workdir: Subprocess working directory.
            session_id: Optional session to resume via configured resume_flag.

        Returns:
            Concrete RunnerHandle implementation.

        Raises:
            RunnerNotFoundError: If configured command is unavailable.
            RunnerError: If subprocess spawning fails.
        """
        argv = _build_command(
            self._config,
            workdir=workdir,
            agent=agent,
            session_id=session_id,
        )
        process = await _spawn_process(
            runner_name=self.name,
            argv=argv,
            prompt=prompt,
            workdir=workdir,
        )
        return _SubprocessRunnerHandle(
            process=process,
            parser=self._parser,
            runner_name=self.name,
            dispatch_started_at=time.monotonic(),
            session_id=session_id,
        )


class OpenCodeRunner:
    """Runner for OpenCode-style JSONL output CLIs."""

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        self.name = name
        self._config = config
        self._parser = _select_output_parser(name, config.output_parser)

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """Launch an OpenCode subprocess and return a concrete handle.

        Args:
            prompt: Prompt text piped to stdin.
            agent: Agent identifier for argument templating.
            workdir: Subprocess working directory.
            session_id: Optional session to resume via configured resume_flag.

        Returns:
            Concrete RunnerHandle implementation.

        Raises:
            RunnerNotFoundError: If configured command is unavailable.
            RunnerError: If subprocess spawning fails.
        """
        argv = _build_command(
            self._config,
            workdir=workdir,
            agent=agent,
            session_id=session_id,
        )
        process = await _spawn_process(
            runner_name=self.name,
            argv=argv,
            prompt=prompt,
            workdir=workdir,
        )
        return _SubprocessRunnerHandle(
            process=process,
            parser=self._parser,
            runner_name=self.name,
            dispatch_started_at=time.monotonic(),
            session_id=session_id,
        )


# =============================================================================
# Factory
# =============================================================================


def create_runner(name: str, config: RunnerConfig) -> Runner:
    """Create a runner instance from configuration.

    Args:
        name: Runner name (key in config.runners).
        config: Runner configuration.

    Returns:
        Runner implementation instance.

    Raises:
        RunnerError: If runner name is not in core scope.

    Runtime behavior: Return core runner implementations for this phase,
    raise for out-of-scope runners.
    """
    if name == "claude":
        return ClaudeRunner(name, config)
    if name == "opencode":
        return OpenCodeRunner(name, config)

    # Deferred to driver-multi-runner-hardening phase
    if name in ("codex", "gemini"):
        raise RunnerError(
            name,
            "create",
            f"Runner '{name}' is not in core scope. "
            "Implement in driver-multi-runner-hardening phase.",
        )

    raise RunnerError(
        name,
        "create",
        f"Unknown runner '{name}'. Available: {sorted(CORE_RUNNERS)}",
    )


__all__ = [
    # Status enum
    "RunnerStatus",
    # Data types
    "RunnerResult",
    # Protocols
    "RunnerHandle",
    "Runner",
    "OutputParser",
    # Core runner stubs
    "ClaudeRunner",
    "OpenCodeRunner",
    # Factory
    "create_runner",
    # Phase scope
    "CORE_RUNNERS",
]
