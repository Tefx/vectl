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

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

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


def get_runtime_adapter() -> RuntimeEntrypointAdapter:
    """Return the shared runtime adapter implementation.

    Raises:
        NotImplementedError: Adapter wiring is deferred to implementation step.
    """

    raise NotImplementedError(
        "A4 runtime adapter implementation is deferred; contract pinned only."
    )


def run_drive_cli_entrypoint(
    *,
    config: Path,
    adapter: RuntimeEntrypointAdapter,
) -> int:
    """Run the ``vectl drive`` entrypoint via the shared adapter.

    Raises:
        NotImplementedError: Runtime execution is deferred to implementation step.
    """

    raise NotImplementedError(
        "Contract-only step: drive entrypoint delegation defined, implementation deferred."
    )


def run_driver_module_entrypoint(
    *,
    argv: Sequence[str] | None,
    adapter: RuntimeEntrypointAdapter,
) -> int:
    """Run ``python -m vectl.driver`` via the shared adapter.

    Contract notes:
    - Keeps positional config support.
    - Adds ``--config`` support.

    Raises:
        NotImplementedError: Runtime execution is deferred to implementation step.
    """

    raise NotImplementedError(
        "Contract-only step: module entrypoint delegation defined, implementation deferred."
    )


__all__ = [
    "EntrypointInvocation",
    "EXIT_CODE_MATRIX",
    "EXIT_CODE_RUNTIME_ERROR",
    "EXIT_CODE_USAGE_ERROR",
    "InvocationForm",
    "RuntimeEntrypointAdapter",
    "SUPPORTED_INVOCATION_FORMS",
    "get_runtime_adapter",
    "run_drive_cli_entrypoint",
    "run_driver_module_entrypoint",
]
