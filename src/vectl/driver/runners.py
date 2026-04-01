"""Runner protocol and core implementations.

Responsibility: Define Runner and RunnerHandle protocols. Provide concrete
implementations for each supported CLI runner. Parse runner output into RunnerResult.

Non-responsibility: Does NOT decide which runner to use (config.route_agent).
Does NOT manage runner lifecycle across the loop (DriverState).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
Blueprint Reference: DRIVER-BLUEPRINT.md Runner Protocol & Implementations

PHASE SCOPE: Core runners (ClaudeRunner, OpenCodeRunner) and extended
runners (CodexRunner, GeminiRunner) are implemented.

This file also preserves compatibility aliases (`CodexRunnerStub`,
`GeminiRunnerStub`) so existing contract tests and downstream code paths remain
stable during rollout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

from .errors import RunnerError, RunnerNotFoundError
from .parsers import (
    ClaudeOutputParser,
    CodexOutputParser,
    GeminiOutputParser,
    OpenCodeOutputParser,
)
from .types import RunnerResult, RunnerStatus, StreamProgressSignal, StreamSignalKind

if TYPE_CHECKING:
    from .config import RunnerConfig


AgentSelectionSupportStatus = Literal["supported", "unsupported", "unverified"]


@dataclass(frozen=True)
class RunnerAgentSelectionCapability:
    """Pinned agent-selection capability for one runner.

    Source:
    - docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``
    - docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``

    Invariants:
    - ``supports_agent_selection`` governs whether planner/judge external-agent
      mode may pass ``--agent`` (or equivalent) to this runner at all.
    - ``selection_arg_template`` is descriptive contract metadata only in this
      phase; it does not authorize runtime interpolation changes by itself.
    - ``status`` stays fail-closed for unverified runners.
    """

    runner_name: str
    supports_agent_selection: bool
    status: AgentSelectionSupportStatus
    selection_arg_template: str | None
    evidence: str
    rejection_outcome_when_selected: str


RUNNER_AGENT_SELECTION_CAPABILITIES: Final[tuple[RunnerAgentSelectionCapability, ...]] = (
    RunnerAgentSelectionCapability(
        runner_name="opencode",
        supports_agent_selection=True,
        status="supported",
        selection_arg_template="--agent {external_agent_name}",
        evidence=(
            "docs/DRIVER-AGENT-SELECTION.md Capability Rules: opencode supports "
            "external agent selection via --agent"
        ),
        rejection_outcome_when_selected="n/a",
    ),
    RunnerAgentSelectionCapability(
        runner_name="claude",
        supports_agent_selection=False,
        status="unsupported",
        selection_arg_template=None,
        evidence=(
            "docs/DRIVER-AGENT-SELECTION.md names only opencode as presently verified; "
            "unsupported runners must reject external_agent_name"
        ),
        rejection_outcome_when_selected="hard config/startup error",
    ),
    RunnerAgentSelectionCapability(
        runner_name="codex",
        supports_agent_selection=False,
        status="unverified",
        selection_arg_template=None,
        evidence=(
            "docs/DRIVER-AGENT-SELECTION.md: codex may support agent placeholder usage only "
            "if vectl has a verified runner contract; v1 must fail closed until verified"
        ),
        rejection_outcome_when_selected="hard config/startup error",
    ),
    RunnerAgentSelectionCapability(
        runner_name="gemini",
        supports_agent_selection=False,
        status="unverified",
        selection_arg_template=None,
        evidence=(
            "docs/DRIVER-AGENT-SELECTION.md requires explicit capability metadata; no Gemini "
            "verification exists in this contract"
        ),
        rejection_outcome_when_selected="hard config/startup error",
    ),
)

RUNNER_AGENT_SELECTION_CAPABILITY_INDEX: Final[dict[str, RunnerAgentSelectionCapability]] = {
    capability.runner_name: capability for capability in RUNNER_AGENT_SELECTION_CAPABILITIES
}


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

    def stream_jsonl(self) -> AsyncIterator[str]:
        """Yield incremental JSONL lines from live runner stdout.

        Source:
        - step ``driver-enhancement-streaming-progress.design-and-test``
          requires incremental JSONL consumption semantics.

        Boundary note:
        - This is live stream transport only.
        - Final completion summary remains ``wait() -> RunnerResult``.
        """
        ...

    def last_progress_signal(self) -> StreamProgressSignal | None:
        """Return the most recent heartbeat/progress signal.

        Source:
        - step ``driver-enhancement-streaming-progress.design-and-test``
          requires explicit last-progress boundary.
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


class _CodexParserAdapter:
    """Adapter from parser module result type to runners module result type."""

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = CodexOutputParser()

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


class _GeminiParserAdapter:
    """Adapter from parser module result type to runners module result type."""

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = GeminiOutputParser()

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
        "_last_progress",
        "_progress_sequence",
        "_process",
        "_parser",
        "_runner_name",
        "_dispatch_started_at",
        "_stdout_lines",
        "_stderr_chunks",
        "_stdout_task",
        "_stderr_task",
        "_stream_queue",
        "_stream_started",
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
        self._last_progress: StreamProgressSignal | None = None
        self._progress_sequence = 0
        self._process = process
        self._parser = parser
        self._runner_name = runner_name
        self._dispatch_started_at = dispatch_started_at
        self._stdout_lines: list[str] = []
        self._stderr_chunks: list[str] = []
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stream_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stream_started = False

    def _ensure_stream_tasks_started(self) -> None:
        if self._stream_started:
            return

        self._stream_started = True
        self._stdout_task = asyncio.create_task(self._consume_stdout())
        self._stderr_task = asyncio.create_task(self._consume_stderr())

    async def _consume_stdout(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            await self._stream_queue.put(None)
            return

        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                stripped = text.rstrip("\r\n")
                if stripped:
                    self._stdout_lines.append(stripped)
                    self._update_progress_signal(stripped)
                    await self._stream_queue.put(stripped)
        finally:
            await self._stream_queue.put(None)

    async def _consume_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return

        while True:
            chunk = await stderr.readline()
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            stripped = text.rstrip("\r\n")
            if stripped:
                self._stderr_chunks.append(stripped)

    def _update_progress_signal(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return

        if not isinstance(payload, dict):
            return

        event_type_raw = payload.get("type")
        if not isinstance(event_type_raw, str):
            return

        message: str | None = None
        if event_type_raw in {"step_start", "thread.started"}:
            kind_enum = StreamSignalKind.HEARTBEAT
            message = event_type_raw
        elif event_type_raw in {"text", "item.completed"}:
            kind_enum = StreamSignalKind.PROGRESS
            if event_type_raw == "text":
                part = payload.get("part")
                if isinstance(part, dict):
                    raw_text = part.get("text")
                    if raw_text is not None:
                        message = str(raw_text)
            elif event_type_raw == "item.completed":
                item = payload.get("item")
                if isinstance(item, dict):
                    raw_text = item.get("text")
                    if raw_text is not None:
                        message = str(raw_text)
        elif event_type_raw in {"step_finish", "turn.completed"}:
            kind_enum = StreamSignalKind.HEARTBEAT
            message = event_type_raw
        else:
            return

        self._progress_sequence += 1
        self._last_progress = StreamProgressSignal(
            kind=kind_enum,
            emitted_at_monotonic=time.monotonic(),
            sequence=self._progress_sequence,
            message=message,
        )

    def stream_jsonl(self) -> AsyncIterator[str]:
        """Yield incremental JSONL lines from live subprocess output.

        Implementation owner:
        - ``driver-enhancement-streaming-progress.impl``
        """
        self._ensure_stream_tasks_started()

        async def _iterator() -> AsyncIterator[str]:
            while True:
                line = await self._stream_queue.get()
                if line is None:
                    return
                yield line

        return _iterator()

    def last_progress_signal(self) -> StreamProgressSignal | None:
        """Return most recent observed heartbeat/progress signal.

        Implementation owner:
        - ``driver-enhancement-streaming-progress.impl``
        """
        return self._last_progress

    async def wait(self, timeout: float | None = None) -> RunnerResult:
        """Wait for process completion and parse runner output.

        Args:
            timeout: Maximum seconds to wait. None means no timeout.

        Returns:
            Parsed runner result with real subprocess exit code attached.

        Raises:
            RunnerError: If wait times out or subprocess wait fails.
        """
        self._ensure_stream_tasks_started()
        try:
            if self._stdout_task is not None:
                await asyncio.wait_for(asyncio.shield(self._stdout_task), timeout=timeout)
            if self._stderr_task is not None:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=timeout)
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
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
        stdout_text = "\n".join(self._stdout_lines)
        stderr_text = "\n".join(self._stderr_chunks)
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


def _format_arg(
    template: str,
    *,
    workdir: str,
    agent: str,
    session_id: str | None = None,
) -> str:
    """Replace known placeholders in argument templates.

    Supported placeholders: `{workdir}`, `{agent}`, `{session_id}`.
    """
    # Do targeted placeholder replacement only. We intentionally avoid
    # `str.format(...)` because runner args may contain literal braces
    # (e.g., inline Python/JSON snippets in tests and scripts).
    formatted = template.replace("{workdir}", workdir).replace("{agent}", agent)
    if session_id is not None:
        formatted = formatted.replace("{session_id}", session_id)
    return formatted


def _build_command(
    config: RunnerConfig,
    *,
    workdir: str,
    agent: str,
    session_id: str | None,
) -> list[str]:
    """Build command argv for subprocess dispatch."""
    argv = [config.command]
    argv.extend(
        _format_arg(arg, workdir=workdir, agent=agent, session_id=session_id) for arg in config.args
    )

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
    if output_parser_name == "codex_jsonl":
        return _CodexParserAdapter()
    if output_parser_name == "gemini_json":
        return _GeminiParserAdapter()

    raise RunnerError(
        runner_name,
        "create",
        f"Unsupported output_parser '{output_parser_name}'",
    )


def _build_codex_resume_command(
    config: RunnerConfig,
    *,
    workdir: str,
    agent: str,
    session_id: str,
) -> list[str]:
    """Build argv for Codex resume semantics.

    Codex resume uses a separate command form (`exec resume`) rather than a
    shared `resume_flag` append pattern.
    """
    if not config.resume_command:
        raise RunnerError("codex", "dispatch", "resume_command is required for codex resume")

    argv = [
        _format_arg(part, workdir=workdir, agent=agent, session_id=session_id)
        for part in config.resume_command
    ]

    has_placeholder = any("{session_id}" in part for part in config.resume_command)
    if not has_placeholder:
        if "resume" in argv:
            idx = len(argv) - 1 - argv[::-1].index("resume")
            argv.insert(idx + 1, session_id)
        else:
            argv.append(session_id)

    if config.prompt_mode == "stdin_dash" and "-" not in argv:
        argv.append("-")

    return argv


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
# Extended Runners (Phase 2)
# =============================================================================


EXTENDED_RUNNERS: frozenset[str] = frozenset({"codex", "gemini"})
"""Runner names implemented in the extended phase."""

# Extended runner capability table (from DRIVER-BLUEPRINT.md)
# | Capability | Codex | Gemini |
# |------------|-------|--------|
# | Headless mode | exec | -p |
# | Stdin prompt | stdin_dash ("-") | stdin |
# | JSON output | --json (JSONL) | --output-format json |
# | Session resume | `exec resume UUID` | --resume INDEX |
# | Working dir | -C DIR | N/A |
# | Experimental | No | Yes (auth issues) |
#
# Resume semantics:
#   - Codex: Uses separate command `codex exec resume <UUID>` with --json flag
#   - Gemini: Uses --resume INDEX flag where INDEX is session number
#
# Parser semantics:
#   - Codex: JSONL stream (thread.started -> item.completed -> turn.completed)
#   - Gemini: Single JSON object (like Claude)
#
# Fallback interactions:
#   - Experimental runners have elevated failure risk
#   - Fallback to core runner (opencode) after 2 consecutive failures
#   - Session reuse may not work for experimental runners
#   - Cost tracking may be unavailable for Gemini


class CodexRunner:
    """Runner for Codex-style JSONL output CLIs.

    Phase 2 Contract: Codex CLI runner with JSONL output parsing.

    Resume Semantics (Blueprint Section: Verified CLI Capabilities):
        - Resume command: `codex exec resume <UUID> --json`
        - Uses DIFFERENT command structure than initial dispatch
        - Session ID regex: ^[0-9a-f]{8}-
        - Config field: resume_command (list[str]) replaces dispatch command

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: JSONL stream (thread.started -> item.completed -> turn.completed)
        - Session ID extraction: thread.started event
        - Status detection: presence of item.completed events
        - Token usage: turn.completed.usage

    Stdin Mode:
        - Uses "-" argument for stdin input (stdin_dash mode)

    Working Directory:
        - Supports -C flag for working directory

    Experimental Status:
        - NOT experimental (stable runner)
        - Standard fallback behavior applies

    Implementation Owner: driver-multi-runner-hardening.impl-runners-extended
    """

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        if config.resume_command is None:
            raise RunnerError(
                name,
                "create",
                "CodexRunner requires resume_command in config",
            )
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
        """Launch a Codex subprocess and return a concrete handle.

        Returns:
            RunnerHandle with session_id from thread.started event.

        Raises:
            RunnerNotFoundError: If codex command not on PATH.
            RunnerError: If subprocess creation fails.
        """
        argv = (
            _build_codex_resume_command(
                self._config,
                workdir=workdir,
                agent=agent,
                session_id=session_id,
            )
            if session_id is not None and self._config.resume_command
            else _build_command(
                self._config,
                workdir=workdir,
                agent=agent,
                session_id=session_id,
            )
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


class GeminiRunner:
    """Runner for Gemini-style JSON output CLIs.

    Phase 2 Contract: Gemini CLI runner with single JSON output parsing.

    Resume Semantics (Blueprint Section: Verified CLI Capabilities):
        - Resume flag: --resume INDEX (INDEX is session number, not UUID)
        - Config field: resume_flag = "--resume"
        - Note: Gemini session management is different from Claude/OpenCode

    Parser Semantics (Blueprint Section: Output Parsers):
        - Format: Single JSON object (like Claude)
        - Session ID: extracted from session_id field
        - Status detection: subtype field (success/error)
        - Token usage: usage field
        - Cost tracking: unverified (may be unavailable)

    Stdin Mode:
        - Standard stdin mode (pipe to -p flag)

    Working Directory:
        - No explicit -C flag (verify against actual CLI)

    Experimental Status:
        - EXPERIMENTAL: true (auth issues, not fully verified)
        - Elevated failure risk
        - May require additional authentication setup

    Implementation Owner: driver-multi-runner-hardening.impl-runners-extended

    Known Issues:
        - Authentication may fail without proper setup
        - Cost tracking may not be available in output
        - Session resume semantics not fully verified
    """

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        if not config.experimental:
            raise RunnerError(
                name,
                "create",
                "GeminiRunner requires experimental=True in config",
            )
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
        """Launch a Gemini subprocess and return a concrete handle.

        Returns:
            RunnerHandle with session_id from JSON output.

        Raises:
            RunnerNotFoundError: If gemini command not on PATH.
            RunnerError: If subprocess creation fails (including auth issues).
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
        RunnerError: If runner name is not recognized.

    Core runners (claude, opencode): Return full implementations.
    Extended runners (codex, gemini): Return extended implementations.
    Unknown runners: Raise RunnerError.
    """
    # Core runners (fully implemented)
    if name == "claude":
        return ClaudeRunner(name, config)
    if name == "opencode":
        return OpenCodeRunner(name, config)

    # Extended runners
    if name == "codex":
        return CodexRunner(name, config)
    if name == "gemini":
        return GeminiRunner(name, config)

    raise RunnerError(
        name,
        "create",
        f"Unknown runner '{name}'. Available core: {sorted(CORE_RUNNERS)}, "
        f"extended: {sorted(EXTENDED_RUNNERS)}",
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
    # Core runners (implemented)
    "ClaudeRunner",
    "OpenCodeRunner",
    # Extended runners
    "CodexRunner",
    "GeminiRunner",
    # Backward-compatibility aliases (deprecated names)
    "CodexRunnerStub",
    "GeminiRunnerStub",
    # Phase scoping
    "CORE_RUNNERS",
    "EXTENDED_RUNNERS",
    # Factory
    "create_runner",
]


# Backward-compatibility aliases for tests/importers that still use stub names.
CodexRunnerStub = CodexRunner
GeminiRunnerStub = GeminiRunner
