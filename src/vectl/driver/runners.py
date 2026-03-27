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

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

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


# =============================================================================
# Core Runner Stubs (Phase 1)
# =============================================================================


# Phase scope: Core runners only. Extended runners deferred to multi-runner phase.
CORE_RUNNERS: frozenset[str] = frozenset({"claude", "opencode"})
"""Runner names implemented in this phase.

Architecture: Core-only scope per phase plan.
Blueprint: Phase 1 -> ClaudeRunner, OpenCodeRunner
Future: driver-multi-runner-hardening -> CodexRunner, GeminiRunner

CONTRACT: This constant defines the phase scope. Implementation phases
(ClaudeRunnerImpl, OpenCodeRunnerImpl) register runners matching this set.
"""


class ClaudeRunner:
    """Runner for Claude CLI (`claude -p --output-format json`).

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol, Config Schema

    Config example:
        command: "claude"
        args: ["-p", "--output-format", "json", "--dangerously-skip-permissions"]
        prompt_mode: stdin
        stall_timeout: 300
        resume_flag: "--resume"
        session_id_regex: "^[0-9a-f]{8}-[0-9a-f]{4}-"
        output_parser: claude_json
        persist_session: true

    Contract stub: Implementation provided in driver-execution phase.
    Runtime behavior: Dispatch subprocess, parse JSON output, return RunnerHandle.
    """

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        self.name = name
        self._config = config
        self._parser: OutputParser | None = None  # Set in impl phase

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """STUB: Implementation in driver-execution phase.

        Raises:
            NotImplementedError: This stub must be replaced by implementation.
        """
        raise NotImplementedError(
            "ClaudeRunner.dispatch() stub: implement in driver-execution phase"
        )


class OpenCodeRunner:
    """Runner for OpenCode CLI (`opencode run --format json`).

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8
    Blueprint: DRIVER-BLUEPRINT.md Runner Protocol, Config Schema

    Config example:
        command: "opencode"
        args: ["run", "--format", "json", "--dir", "{workdir}"]
        prompt_mode: stdin
        stall_timeout: 600
        resume_flag: "--session"
        session_id_regex: "^ses_[a-z0-9]+"
        output_parser: opencode_jsonl
        persist_session: true

    Contract stub: Implementation provided in driver-execution phase.
    Runtime behavior: Dispatch subprocess, parse JSONL stream, return RunnerHandle.
    """

    __slots__ = ("name", "_config", "_parser")

    def __init__(self, name: str, config: RunnerConfig) -> None:
        self.name = name
        self._config = config
        self._parser: OutputParser | None = None  # Set in impl phase

    async def dispatch(
        self,
        prompt: str,
        agent: str,
        workdir: str,
        session_id: str | None = None,
    ) -> RunnerHandle:
        """STUB: Implementation in driver-execution phase.

        Raises:
            NotImplementedError: This stub must be replaced by implementation.
        """
        raise NotImplementedError(
            "OpenCodeRunner.dispatch() stub: implement in driver-execution phase"
        )


# =============================================================================
# Factory Stub
# =============================================================================


def create_runner(name: str, config: RunnerConfig) -> Runner:
    """Create a runner instance from configuration.

    Args:
        name: Runner name (key in config.runners).
        config: Runner configuration.

    Returns:
        Runner implementation stub.

    Raises:
        RunnerError: If runner name is not in core scope.

    Contract stub: Implementation registration in driver-execution phase.
    Runtime behavior: Return runner stub for core scope, raise for extended scope.
    """
    from .errors import RunnerError

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
