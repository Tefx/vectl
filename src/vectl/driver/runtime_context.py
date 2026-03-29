"""Shared runtime context contract for loop action handlers.

Contract purity: this module defines typed handler inputs only. It introduces no
runtime behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .types import DriverState

if TYPE_CHECKING:
    from .config import DriverConfig
    from .judge import Judge
    from .observe import Observer
    from .runners import Runner
    from .session import SessionPool


@dataclass(frozen=True)
class RuntimeContext:
    """Unified handler context shared by loop action handlers.

    Authority:
    - docs/ADR-driver-evolution-foundation.md#96-handlers-receive-a-unified-runtimecontext

    Invariants:
        - action handlers consume this shared context boundary rather than
          role-specific micro-contexts in the initial action-registry design.
        - this type is contract surface only; population/wiring remains an
          implementation concern.
    """

    state: DriverState
    config: DriverConfig
    runners: Mapping[str, Runner]
    judge: Judge
    session_pool: SessionPool
    observer: Observer
    plan_path: Path


__all__ = ["RuntimeContext"]
