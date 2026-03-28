"""CLI entrypoint contract for ``python -m vectl.driver``.

Responsibility: Provide the process entry surface for the driver runtime.

Non-responsibility: Does NOT implement orchestration logic. Delegates runtime
execution to ``loop.run``.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.11.

CONTRACT PURITY: This module pins the entrypoint signature only. Argument
parsing and process wiring remain implementation work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Sequence

DEFAULT_DRIVER_CONFIG_PATH: Final[Path] = Path("driver.yaml")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the driver from the command line.

    Contract:
    - accept an optional argv override for testability
    - resolve the driver config path (default: ``driver.yaml``)
    - delegate async orchestration to ``vectl.driver.loop.run``
    - return process exit status rather than calling ``sys.exit`` directly

    Startup recovery and graceful shutdown semantics are owned by ``loop.run``;
    this entrypoint only exposes the process boundary.
    """
    raise NotImplementedError(
        "driver.__main__.main contract stub; implementation belongs to runtime phase"
    )


__all__ = ["DEFAULT_DRIVER_CONFIG_PATH", "main"]
