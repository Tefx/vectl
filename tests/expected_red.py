"""Helpers for durable intentional-red test metadata.

This module centralizes the marker shape used by ``*_red.py`` tests so future
implementation phases can either flip, retire, or explicitly preserve each
intentional-red surface with rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

EXPECTED_RED_LIFECYCLE: Final = "must_flip_or_retire_or_explicitly_preserve_with_rationale"
EXPECTED_RED_REQUIRED_FIELDS: Final = ("owner", "rationale", "lifecycle")


def expected_red_module(*, owner: str, rationale: str) -> pytest.MarkDecorator:
    """Return the canonical module-level marker for intentional-red tests."""

    return pytest.mark.expected_red(
        owner=owner,
        rationale=rationale,
        lifecycle=EXPECTED_RED_LIFECYCLE,
    )


def validate_expected_red_marker(item: pytest.Item) -> str | None:
    """Validate required expected-red metadata for collected red-test items."""

    path = Path(str(item.fspath))
    if not path.name.endswith("_red.py"):
        return None

    marker = item.get_closest_marker("expected_red")
    if marker is None:
        return (
            f"{path}: missing @pytest.mark.expected_red metadata; add "
            "pytestmark = expected_red_module(...)"
        )

    missing = [field for field in EXPECTED_RED_REQUIRED_FIELDS if not marker.kwargs.get(field)]
    if missing:
        return f"{path}: expected_red marker missing required field(s): {', '.join(missing)}"

    lifecycle = marker.kwargs.get("lifecycle")
    if lifecycle != EXPECTED_RED_LIFECYCLE:
        return (
            f"{path}: expected_red lifecycle must be {EXPECTED_RED_LIFECYCLE!r}, got {lifecycle!r}"
        )

    return None
