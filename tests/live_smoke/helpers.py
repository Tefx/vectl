"""Shared live smoke test harness.

Responsibility: env gate checks, runner availability/auth preflight,
subprocess capture envelope, and common diagnostics formatting.

Does NOT own scenario-specific parsing policy.

Marker taxonomy (from DRIVER-LIVE-SMOKE-POLICY.md):
    - Shared marker: live_runner
    - Runner markers: codex_live, opencode_live
    - Scenario markers: dispatch_live

Skip semantics (exact):
    - live_runner_opt_in_missing: RUN_LIVE_RUNNER_TESTS is not exactly "1"
    - live_runner_binary_missing: required runner binary unavailable on PATH
    - live_runner_auth_missing: required auth/session unavailable
    - live_runner_override_unsupported: required model/invocation override cannot be honored

Evidence contract (exact):
    For every failing live smoke subprocess assertion, test evidence MUST include:
    1. Full command (argv) as executed
    2. returncode
    3. stdout (captured text, even if empty)
    4. stderr (captured text, even if empty)
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    pass

# =============================================================================
# Marker taxonomy (exact from policy)
# =============================================================================

# Shared marker for all live smoke tests
live_runner = pytest.mark.live_runner
"""Marks tests that are part of the live smoke test suite."""

codex_live = pytest.mark.codex_live
"""Marks tests that use the codex runner."""

opencode_live = pytest.mark.opencode_live
"""Marks tests that use the opencode runner."""

dispatch_live = pytest.mark.dispatch_live
"""Marks tests that exercise regular dispatch behavior."""


# =============================================================================
# Pinned model IDs (exact from policy)
# =============================================================================

# Codex dispatch uses this pinned model
CODEX_MODEL: str = "gpt-5.4-mini"
# Opencode dispatch uses this pinned model
OPENCODE_MODEL: str = "ollama-cloud/minimax-m2.7"

# Model-to-runner mapping for override plumbing
RUNNER_FOR_MODEL: dict[str, str] = {
    CODEX_MODEL: "codex",
    OPENCODE_MODEL: "opencode",
}

# Scenario to runner mapping
SCENARIO_RUNNER: dict[str, str] = {
    "codex_dispatch": "codex",
    "opencode_dispatch": "opencode",
}


# =============================================================================
# Skip reason constants (exact from policy)
# =============================================================================

SKIP_OPT_IN_MISSING: str = "live_runner_opt_in_missing"
"""RUN_LIVE_RUNNER_TESTS is not exactly '1'."""

SKIP_BINARY_MISSING: str = "live_runner_binary_missing"
"""Required runner binary is unavailable on PATH."""

SKIP_AUTH_MISSING: str = "live_runner_auth_missing"
"""Required auth/session for the runner/provider is unavailable."""

SKIP_OVERRIDE_UNSUPPORTED: str = "live_runner_override_unsupported"
"""Required model/invocation override cannot be honored on the runner path."""


# =============================================================================
# Opt-in gate
# =============================================================================

RUN_LIVE_RUNNER_TESTS_ENVAR: str = "RUN_LIVE_RUNNER_TESTS"
"""Environment variable that gates live runner tests.

Must be exactly "1" to enable.
"""


def is_live_runner_opted_in() -> bool:
    """Check if live runner tests are opted in via environment variable.

    Returns:
        True only if RUN_LIVE_RUNNER_TESTS is exactly "1".
    """
    return os.environ.get(RUN_LIVE_RUNNER_TESTS_ENVAR, "") == "1"


# =============================================================================
# Runner availability preflight
# =============================================================================

# Known runner commands to check on PATH
KNOWN_RUNNER_COMMANDS: frozenset[str] = frozenset({"codex", "opencode"})


def runner_binary_available(runner_name: str) -> bool:
    """Check if a runner binary is available on PATH.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        True if the command is found on PATH.
    """
    if runner_name not in KNOWN_RUNNER_COMMANDS:
        return False

    # Use shutil.which for cross-platform PATH lookup
    import shutil

    return shutil.which(runner_name) is not None


def get_runner_version(runner_name: str) -> str | None:
    """Get the version string of a runner binary.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        Version string if available, None otherwise.
    """
    if not runner_binary_available(runner_name):
        return None

    try:
        result = subprocess.run(
            [runner_name, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


# =============================================================================
# Auth/session preflight (placeholder - auth checking is runner-specific)
# =============================================================================

_auth_checks: dict[str, bool] = {}


def set_auth_check(runner_name: str, available: bool) -> None:
    """Set auth availability for a runner (for testing).

    Args:
        runner_name: One of "codex", "opencode".
        available: Whether auth is available.
    """
    _auth_checks[runner_name] = available


def is_auth_available(runner_name: str) -> bool:
    """Check if auth/session is available for a runner.

    This is a stub. Real auth checking is runner-specific.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        True if auth is available or no check was registered.
    """
    return _auth_checks.get(runner_name, True)


# =============================================================================
# Subprocess capture envelope
# =============================================================================


@dataclass
class SubprocessResult:
    """Captured result from a live smoke subprocess execution.

    Preserves full evidence contract: argv, returncode, stdout, stderr.
    """

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    # Convenience fields populated during diagnostics
    runner_name: str | None = None
    scenario: str | None = None

    def format_diagnostics(self) -> str:
        """Format full diagnostics for evidence.

        Returns:
            Multi-line diagnostics string with all evidence fields.
        """
        lines = [
            f"Command: {' '.join(shlex.quote(arg) for arg in self.argv)}",
            f"Returncode: {self.returncode}",
            f"Stdout:\n{self.stdout}",
            f"Stderr:\n{self.stderr}",
        ]
        if self.runner_name:
            lines.insert(0, f"Runner: {self.runner_name}")
        if self.scenario:
            lines.insert(0, f"Scenario: {self.scenario}")
        return "\n".join(lines)

    def assert_success(self, msg: str | None = None) -> None:
        """Assert subprocess exited successfully.

        Args:
            msg: Optional additional message.

        Raises:
            AssertionError: If returncode != 0.
        """
        if self.returncode != 0:
            details = self.format_diagnostics()
            full_msg = f"{msg or 'Subprocess failed'}\n{details}"
            raise AssertionError(full_msg)

    def assert_failed(self, msg: str | None = None) -> None:
        """Assert subprocess exited with non-zero code.

        Args:
            msg: Optional additional message.

        Raises:
            AssertionError: If returncode == 0.
        """
        if self.returncode == 0:
            details = self.format_diagnostics()
            full_msg = f"{msg or 'Subprocess succeeded unexpectedly'}\n{details}"
            raise AssertionError(full_msg)


def run_subprocess(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    input_str: str | None = None,
    timeout: float | None = 30.0,
    runner_name: str | None = None,
    scenario: str | None = None,
) -> SubprocessResult:
    """Run a subprocess and capture full evidence.

    This is the canonical subprocess capture envelope for live smoke tests.
    It always captures argv, returncode, stdout, and stderr.

    Args:
        argv: Command argument list.
        cwd: Working directory (default: current directory).
        env: Environment variables (merged with os.environ).
        input_str: Input string to pipe to stdin.
        timeout: Timeout in seconds (default 30).
        runner_name: Runner name for diagnostics.
        scenario: Scenario name for diagnostics.

    Returns:
        SubprocessResult with full evidence.

    Raises:
        OSError: If subprocess cannot be spawned.
        subprocess.TimeoutExpired: If timeout exceeded.
    """
    merged_env: dict[str, str] = dict(os.environ)
    if env:
        merged_env.update(env)

    kwargs: dict[str, str | int | float | dict[str, str] | None] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }

    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = merged_env
    if input_str is not None:
        kwargs["input"] = input_str

    try:
        result = subprocess.run(argv, **kwargs)  # type: ignore[arg-type]
    except subprocess.TimeoutExpired as e:
        return SubprocessResult(
            argv=argv,
            returncode=-1,
            stdout="",
            stderr=f"Timeout after {timeout}s: {e}",
            runner_name=runner_name,
            scenario=scenario,
        )

    return SubprocessResult(
        argv=argv,
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        runner_name=runner_name,
        scenario=scenario,
    )


# =============================================================================
# Skip helpers for pytest
# =============================================================================


def skip_if_not_opted_in() -> pytest.skip | None:
    """Skip if RUN_LIVE_RUNNER_TESTS is not exactly '1'.

    Returns:
        pytest.skip call if not opted in, None otherwise.
    """
    if not is_live_runner_opted_in():
        return pytest.skip(
            f"Live runner tests require {RUN_LIVE_RUNNER_TESTS_ENVAR}=1",
            allow_module_level=True,
        )
    return None


def skip_if_binary_missing(runner_name: str) -> pytest.skip | None:
    """Skip if the runner binary is not on PATH.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        pytest.skip call if binary missing, None otherwise.
    """
    if not runner_binary_available(runner_name):
        version = get_runner_version(runner_name)
        msg = f"Runner '{runner_name}' not found on PATH"
        if version:
            msg += f" (found version: {version})"
        return pytest.skip(msg, allow_module_level=True)
    return None


def skip_if_auth_missing(runner_name: str) -> pytest.skip | None:
    """Skip if auth/session is not available for the runner.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        pytest.skip call if auth missing, None otherwise.
    """
    if not is_auth_available(runner_name):
        return pytest.skip(
            f"Auth/session not available for runner '{runner_name}'",
            allow_module_level=True,
        )
    return None


# =============================================================================
# Skip matrix: returns dict of skip_reason -> bool for diagnostic clarity
# =============================================================================


def check_skip_matrix(runner_name: str) -> dict[str, bool]:
    """Check all skip conditions for a runner and return a matrix.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        Dict mapping skip reason to whether it applies.
    """
    return {
        SKIP_OPT_IN_MISSING: not is_live_runner_opted_in(),
        SKIP_BINARY_MISSING: not runner_binary_available(runner_name),
        SKIP_AUTH_MISSING: not is_auth_available(runner_name),
        SKIP_OVERRIDE_UNSUPPORTED: False,  # Placeholder - actual check is runner-specific
    }


def get_skip_reason(runner_name: str) -> str | None:
    """Get the first applicable skip reason for a runner.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        Skip reason string if any skip applies, None if no skip applies.
    """
    matrix = check_skip_matrix(runner_name)
    for reason, applies in matrix.items():
        if applies:
            return reason
    return None


# =============================================================================
# Override mechanism plumbing
# =============================================================================

# Environment variables for model override
CODEX_MODEL_ENVAR: str = "VECTL_CODEX_MODEL"
OPENCODE_MODEL_ENVAR: str = "VECTL_OPENCODE_MODEL"


def get_codex_model_override() -> str | None:
    """Get the codex model override from environment.

    Returns:
        Model ID if set, None otherwise.
    """
    return os.environ.get(CODEX_MODEL_ENVAR)


def get_opencode_model_override() -> str | None:
    """Get the opencode model override from environment.

    Returns:
        Model ID if set, None otherwise.
    """
    return os.environ.get(OPENCODE_MODEL_ENVAR)


def get_effective_model(runner_name: str) -> str:
    """Get the effective model for a runner.

    If an override is set via environment variable, it is used.
    Otherwise, the pinned model from policy is used.

    Args:
        runner_name: One of "codex", "opencode".

    Returns:
        Effective model ID.
    """
    if runner_name == "codex":
        override = get_codex_model_override()
        return override if override else CODEX_MODEL
    elif runner_name == "opencode":
        override = get_opencode_model_override()
        return override if override else OPENCODE_MODEL
    else:
        # Unknown runner - return as-is
        return runner_name


def build_model_override_args(runner_name: str, model: str) -> list[str]:
    """Build command-line arguments for model override.

    This is runner-specific. Not all runners support model override.

    Args:
        runner_name: One of "codex", "opencode".
        model: Model ID to use.

    Returns:
        List of additional command-line arguments, empty if not supported.
    """
    if runner_name == "codex":
        # Codex uses --model flag
        return ["--model", model]
    elif runner_name == "opencode":
        # Opencode uses --model flag
        return ["--model", model]
    return []


# =============================================================================
# Fixture factories for common preflight patterns
# =============================================================================


class LiveRunnerPreflight:
    """Callable preflight checker for live runner tests.

    Usage:
        @pytest.fixture
        def codex_live_check():
            return LiveRunnerPreflight("codex")

        def test_something(codex_live_check):
            codex_live_check()
            # proceed with test
    """

    __slots__ = ("runner_name",)

    def __init__(self, runner_name: str) -> None:
        if runner_name not in KNOWN_RUNNER_COMMANDS:
            raise ValueError(f"Unknown runner: {runner_name}")
        self.runner_name = runner_name

    def __call__(self) -> None:
        """Run all preflight checks and skip if any fail."""
        skip_reason = get_skip_reason(self.runner_name)
        if skip_reason:
            pytest.skip(f"live_runner preflight failed: {skip_reason}", allow_module_level=True)

    def check_binary(self) -> bool:
        """Check if binary is available without skipping.

        Returns:
            True if binary available, False otherwise.
        """
        return runner_binary_available(self.runner_name)

    def check_auth(self) -> bool:
        """Check if auth is available without skipping.

        Returns:
            True if auth available, False otherwise.
        """
        return is_auth_available(self.runner_name)


# Convenience instances
codex_live_preflight = LiveRunnerPreflight("codex")
opencode_live_preflight = LiveRunnerPreflight("opencode")


# =============================================================================
# Mock helpers for testing the harness itself
# =============================================================================


def mock_runner_available(runner_name: str, available: bool) -> mock._patch:
    """Patch runner_binary_available for testing.

    Args:
        runner_name: One of "codex", "opencode".
        available: Whether to report as available.

    Returns:
        Context manager for use with `with`.
    """
    return mock.patch(
        "tests.live_smoke.helpers.runner_binary_available",
        lambda name: available if name == runner_name else runner_binary_available(name),
    )


def mock_auth_available(runner_name: str, available: bool) -> mock._patch:
    """Patch is_auth_available for testing.

    Args:
        runner_name: One of "codex", "opencode".
        available: Whether to report as available.

    Returns:
        Context manager for use with `with`.
    """
    return mock.patch(
        "tests.live_smoke.helpers.is_auth_available",
        lambda name: available if name == runner_name else is_auth_available(name),
    )
