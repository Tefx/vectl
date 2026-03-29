"""Compatibility facade for driver event sinks.

Ownership split:
- observe.py remains the sink-facing import surface
- driver.events.registry is the canonical schema/source of truth

Authoritative source:
- docs/ADR-driver-evolution-foundation.md#45-event-registry-becomes-source-of-truth
- docs/ADR-driver-evolution-foundation.md#104-event-registry-is-a-static-declaration-table
"""

from __future__ import annotations

from .events.sinks import FileObserver, NullObserver, Observer, create_observer
from .events.types import Event

__all__ = ["Event", "Observer", "FileObserver", "NullObserver", "create_observer"]
