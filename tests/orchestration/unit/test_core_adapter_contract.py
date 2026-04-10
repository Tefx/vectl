"""Contract tests for orchestration core authority bridge.

Authority:
    docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.6
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 7
"""

from __future__ import annotations

import inspect
from typing import Protocol, get_type_hints


def test_core_adapter_is_protocol() -> None:
    from vectl.orchestration.core_adapter import CoreAdapter

    assert issubclass(CoreAdapter, Protocol)


def test_core_adapter_exposes_authoritative_bridge_methods() -> None:
    from vectl.orchestration.core_adapter import CoreAdapter

    required = {
        "snapshot",
        "step_isolation",
        "claim_step",
        "complete_step",
        "defer_step",
    }
    actual = {
        name
        for name, value in inspect.getmembers(CoreAdapter)
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert required <= actual


def test_core_adapter_step_isolation_uses_authoritative_isolation_mode() -> None:
    from vectl.models import IsolationMode
    from vectl.orchestration.core_adapter import CoreAdapter

    hints = get_type_hints(CoreAdapter.step_isolation)
    assert hints.get("return") is IsolationMode
