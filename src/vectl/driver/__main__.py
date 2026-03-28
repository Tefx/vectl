"""CLI entrypoint contract for ``python -m vectl.driver``.

Source of contract requirements:
- Step ``driver-debt-cli-entrypoint-unification.contract``.
- ``docs/DRIVER-ARCHITECTURE.md`` entrypoint/runtime sections.

This module intentionally delegates to the shared adapter surface in
``vectl.driver.entrypoint``. Runtime wiring and parse implementation are deferred.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from .entrypoint import get_runtime_adapter, run_driver_module_entrypoint

DEFAULT_DRIVER_CONFIG_PATH: Final[str] = "driver.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the driver from the command line.

    Contract:
    - accepts optional argv override for testability
    - supports positional ``config`` and ``--config`` forms (deferred wiring)
    - delegates runtime behavior to shared adapter in ``vectl.driver.entrypoint``
    - returns process exit status instead of calling ``sys.exit`` directly
    """
    return run_driver_module_entrypoint(argv=argv, adapter=get_runtime_adapter())


__all__ = ["DEFAULT_DRIVER_CONFIG_PATH", "main"]


if __name__ == "__main__":
    import sys

    sys.exit(main())
