"""Regression checks for legacy driver test layout."""

from pathlib import Path


def test_driver_tests_live_under_legacy_tree() -> None:
    """Ensure driver tests stay in the legacy tree.

    Args:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the legacy tree is missing or top-level driver tests reappear.
    """
    repo_root = Path(__file__).resolve().parents[3]
    tests_root = repo_root / "tests"
    legacy_driver_root = tests_root / "legacy" / "driver"

    assert legacy_driver_root.is_dir(), "Expected tests/legacy/driver/ to exist"
    assert list(tests_root.glob("test_driver*.py")) == []
