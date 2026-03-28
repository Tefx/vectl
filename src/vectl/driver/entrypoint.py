"""Shared runtime entrypoint adapter contract for driver invocation.

Source of contract requirements:
- Step ``driver-debt-cli-entrypoint-unification.contract`` in the active worktree.
- ``docs/DRIVER-ARCHITECTURE.md`` (entrypoint ownership and boundaries).

This module is intentionally contract-only in this phase.

Pinned requirements:
1. Both process entrypoints delegate to one shared runtime adapter surface.
2. Supported invocation forms are:
   - ``vectl drive --config <path>``
   - ``python -m vectl.driver <path>``
   - ``python -m vectl.driver --config <path>``
3. Shared exit-code semantics:
   - runtime/config execution error -> 1
   - argument parsing / usage error -> 2
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from . import loop

InvocationForm = Literal[
    "vectl-drive-option-config",
    "python-module-positional-config",
    "python-module-option-config",
]

SUPPORTED_INVOCATION_FORMS: Final[tuple[InvocationForm, ...]] = (
    "vectl-drive-option-config",
    "python-module-positional-config",
    "python-module-option-config",
)

EXIT_CODE_RUNTIME_ERROR: Final[int] = 1
EXIT_CODE_USAGE_ERROR: Final[int] = 2

EXIT_CODE_MATRIX: Final[dict[str, int]] = {
    "runtime_or_config_error": EXIT_CODE_RUNTIME_ERROR,
    "argument_parsing_or_usage_error": EXIT_CODE_USAGE_ERROR,
}


@dataclass(frozen=True)
class EntrypointInvocation:
    """Normalized invocation payload shared by both entry surfaces.

    Args:
        form: Which user-facing command shape invoked the runtime.
        argv: Raw argv payload (for usage/error reporting parity checks).
        config_arg: Raw config argument payload before normalization.
    """

    form: InvocationForm
    argv: Sequence[str]
    config_arg: str | Path | None


class RuntimeEntrypointAdapter(Protocol):
    """Shared adapter for runtime entrypoint unification.

    The implementation in a later step must centralize:
    - config normalization
    - async invocation of ``vectl.driver.loop.run``
    - error-to-exit-code mapping
    """

    def normalize_config(self, invocation: EntrypointInvocation) -> Path:
        """Return canonical config path for runtime invocation."""
        ...

    def invoke_async(self, config_path: Path) -> None:
        """Invoke async runtime and wait for completion."""
        ...

    def map_error_to_exit_code(self, error: BaseException) -> int:
        """Map an exception to process exit code using shared matrix."""
        ...


class EntrypointUsageError(ValueError):
    """Raised when invocation inputs fail shared usage validation."""


@dataclass(frozen=True)
class SharedRuntimeEntrypointAdapter:
    """Unified runtime adapter used by both process entrypoints."""

    def normalize_config(self, invocation: EntrypointInvocation) -> Path:
        """Return canonical config path for runtime invocation.

        Args:
            invocation: Entrypoint invocation payload from either surface.

        Returns:
            Canonical absolute config path with user-home expansion.

        Raises:
            EntrypointUsageError: If invocation does not provide a config path.
        """

        raw = invocation.config_arg
        if raw is None:
            raise EntrypointUsageError("Missing required config path")

        config_text = str(raw).strip()
        if not config_text:
            raise EntrypointUsageError("Config path cannot be empty")

        return Path(config_text).expanduser().resolve()

    def invoke_async(self, config_path: Path) -> None:
        """Invoke async runtime and wait for completion.

        Args:
            config_path: Normalized path to driver config.
        """

        asyncio.run(loop.run(config_path))

    def map_error_to_exit_code(self, error: BaseException) -> int:
        """Map an exception to process exit code using shared matrix.

        Args:
            error: Exception raised by parsing, normalization, or runtime.

        Returns:
            Exit code using the shared runtime/usage matrix.
        """

        if isinstance(error, SystemExit):
            if error.code == 0:
                return 0
            if error.code == EXIT_CODE_USAGE_ERROR:
                return EXIT_CODE_USAGE_ERROR
            return EXIT_CODE_RUNTIME_ERROR

        if isinstance(error, (EntrypointUsageError, argparse.ArgumentError)):
            return EXIT_CODE_USAGE_ERROR

        return EXIT_CODE_RUNTIME_ERROR


_RUNTIME_ADAPTER: Final[RuntimeEntrypointAdapter] = SharedRuntimeEntrypointAdapter()


def _build_driver_module_parser() -> argparse.ArgumentParser:
    """Build parser for ``python -m vectl.driver`` forms.

    Returns:
        Configured parser supporting positional config and ``--config``.
    """

    parser = argparse.ArgumentParser(prog="python -m vectl.driver")
    parser.add_argument("config", nargs="?", metavar="CONFIG")
    parser.add_argument("--config", dest="config_option", metavar="CONFIG")
    return parser


def _parse_driver_module_invocation(argv: Sequence[str] | None) -> EntrypointInvocation:
    """Parse module argv into normalized invocation metadata.

    Args:
        argv: Optional argv payload excluding program name.

    Returns:
        Parsed invocation payload for shared adapter execution.

    Raises:
        SystemExit: Raised by argparse for help/usage flows.
    """

    parser = _build_driver_module_parser()
    argv_tuple = tuple(sys.argv[1:] if argv is None else argv)
    parsed = parser.parse_args(argv_tuple)

    if parsed.config is not None and parsed.config_option is not None:
        parser.error("cannot combine positional CONFIG with --config")

    if parsed.config_option is not None:
        form: InvocationForm = "python-module-option-config"
        config_arg: str | Path | None = parsed.config_option
    elif parsed.config is not None:
        form = "python-module-positional-config"
        config_arg = parsed.config
    else:
        parser.error("config path is required (use CONFIG or --config)")

    return EntrypointInvocation(form=form, argv=argv_tuple, config_arg=config_arg)


def _invoke_with_adapter(
    invocation: EntrypointInvocation,
    *,
    adapter: RuntimeEntrypointAdapter,
) -> int:
    """Execute one invocation through the shared adapter path."""

    try:
        config_path = adapter.normalize_config(invocation)
        adapter.invoke_async(config_path)
        return 0
    except BaseException as error:
        code = adapter.map_error_to_exit_code(error)
        if not isinstance(error, SystemExit):
            print(str(error), file=sys.stderr)
        return code


def get_runtime_adapter() -> RuntimeEntrypointAdapter:
    """Return the shared runtime adapter implementation.

    Raises:
        NotImplementedError: Adapter wiring is deferred to implementation step.
    """

    return _RUNTIME_ADAPTER


def run_drive_cli_entrypoint(
    *,
    config: Path,
    adapter: RuntimeEntrypointAdapter,
) -> int:
    """Run the ``vectl drive`` entrypoint via the shared adapter.

    Args:
        config: Parsed Typer ``--config`` path.
        adapter: Shared adapter implementation.

    Returns:
        Process-compatible exit code.
    """

    invocation = EntrypointInvocation(
        form="vectl-drive-option-config",
        argv=("--config", str(config)),
        config_arg=config,
    )
    return _invoke_with_adapter(invocation, adapter=adapter)


def run_driver_module_entrypoint(
    *,
    argv: Sequence[str] | None,
    adapter: RuntimeEntrypointAdapter,
) -> int:
    """Run ``python -m vectl.driver`` via the shared adapter.

    Contract notes:
    - Keeps positional config support.
    - Adds ``--config`` support.

    Args:
        argv: Optional process argv payload excluding program name.
        adapter: Shared adapter implementation.

    Returns:
        Process-compatible exit code.
    """

    try:
        invocation = _parse_driver_module_invocation(argv)
    except BaseException as error:
        return adapter.map_error_to_exit_code(error)

    return _invoke_with_adapter(invocation, adapter=adapter)


__all__ = [
    "EntrypointInvocation",
    "EXIT_CODE_MATRIX",
    "EXIT_CODE_RUNTIME_ERROR",
    "EXIT_CODE_USAGE_ERROR",
    "InvocationForm",
    "RuntimeEntrypointAdapter",
    "SharedRuntimeEntrypointAdapter",
    "SUPPORTED_INVOCATION_FORMS",
    "get_runtime_adapter",
    "run_drive_cli_entrypoint",
    "run_driver_module_entrypoint",
]
