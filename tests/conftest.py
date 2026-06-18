"""Shared pytest hooks for the test suite."""

from __future__ import annotations

import pytest

from tests.expected_red import validate_expected_red_marker


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Fail collection when intentional-red tests lack durable lifecycle metadata."""

    del session, config
    errors = [error for item in items if (error := validate_expected_red_marker(item))]
    if errors:
        raise pytest.UsageError("\n".join(errors))
