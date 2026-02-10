"""Tests for plan path resolution (p0-parity.2-plan-path-tests).

Verifies the canonical precedence chain:
  1. explicit parameter
  2. VECTL_PLAN_PATH env var
  3. VECTL_PLAN env var (deprecated, warns)
  4. walk-up discovery
  5. ./plan.yaml fallback

Source: p0-parity.2-plan-path-tests step — lock path semantics with parity tests.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from vectl.plan_path import (
    ENV_PLAN_PATH,
    ENV_PLAN_PATH_DEPRECATED,
    resolve_plan_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove plan path env vars before each test."""
    monkeypatch.delenv(ENV_PLAN_PATH, raising=False)
    monkeypatch.delenv(ENV_PLAN_PATH_DEPRECATED, raising=False)


# ---------------------------------------------------------------------------
# Precedence tests
# ---------------------------------------------------------------------------


class TestExplicitParam:
    """Explicit parameter always wins."""

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH, "/env/plan.yaml")
        result = resolve_plan_path(explicit=Path("/explicit/plan.yaml"))
        assert result == Path("/explicit/plan.yaml")

    def test_explicit_none_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH, "/env/plan.yaml")
        result = resolve_plan_path(explicit=None)
        assert result == Path("/env/plan.yaml")


class TestEnvVarPrecedence:
    """VECTL_PLAN_PATH takes precedence over VECTL_PLAN."""

    def test_canonical_env_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH, "/canonical/plan.yaml")
        monkeypatch.setenv(ENV_PLAN_PATH_DEPRECATED, "/deprecated/plan.yaml")
        result = resolve_plan_path()
        assert result == Path("/canonical/plan.yaml")

    def test_canonical_env_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH, "/canonical/plan.yaml")
        result = resolve_plan_path()
        assert result == Path("/canonical/plan.yaml")

    def test_deprecated_env_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH_DEPRECATED, "/deprecated/plan.yaml")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = resolve_plan_path()
        assert result == Path("/deprecated/plan.yaml")
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "VECTL_PLAN" in str(w[0].message)
        assert "VECTL_PLAN_PATH" in str(w[0].message)


class TestDeprecationWarning:
    """VECTL_PLAN emits a DeprecationWarning."""

    def test_no_warning_for_canonical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH, "/canonical/plan.yaml")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            resolve_plan_path()
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0

    def test_warning_for_deprecated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_PLAN_PATH_DEPRECATED, "/deprecated/plan.yaml")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            resolve_plan_path()
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 1


class TestWalkUpDiscovery:
    """Walk-up finds plan.yaml in parent directories."""

    def test_walk_up_finds_plan_in_parent(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("project: test\nphases: []\n")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)
        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            result = resolve_plan_path()
        assert result == plan_file

    def test_walk_up_finds_plan_in_cwd(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("project: test\nphases: []\n")
        with patch("vectl.plan_path.Path.cwd", return_value=tmp_path):
            result = resolve_plan_path()
        assert result == plan_file

    def test_walk_up_prefers_closest(self, tmp_path: Path) -> None:
        """Walk-up returns the first match, not the root one."""
        root_plan = tmp_path / "plan.yaml"
        root_plan.write_text("project: root\nphases: []\n")
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        child_plan = child_dir / "plan.yaml"
        child_plan.write_text("project: child\nphases: []\n")
        subdir = child_dir / "sub"
        subdir.mkdir()
        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            result = resolve_plan_path()
        assert result == child_plan


class TestFallback:
    """Falls back to ./plan.yaml when nothing found."""

    def test_fallback_when_no_file_exists(self, tmp_path: Path) -> None:
        """When no plan.yaml exists anywhere, returns ./plan.yaml."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch("vectl.plan_path.Path.cwd", return_value=empty_dir):
            result = resolve_plan_path()
        assert result == Path("plan.yaml")


class TestCLIMCPParity:
    """Both CLI and MCP use the same resolver module."""

    def test_cli_uses_shared_resolver(self) -> None:
        import vectl.cli as cli_mod

        assert getattr(cli_mod, "resolve_plan_path") is resolve_plan_path

    def test_mcp_uses_shared_resolver(self) -> None:
        import vectl.mcp_server as mcp_mod

        assert getattr(mcp_mod, "resolve_plan_path") is resolve_plan_path
