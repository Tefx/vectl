"""vectl.driver: Programmatic orchestration engine.

This package implements a deterministic Python driver that replaces the LLM-based
orchestrator with direct plan operations and a unified Judgment Agent for LLM decisions.

Blueprint: DRIVER-BLUEPRINT.md
Architecture: docs/DRIVER-ARCHITECTURE.md
"""

from __future__ import annotations

from . import loop

__all__ = [
    "loop",
]
