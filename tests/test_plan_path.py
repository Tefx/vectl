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

import warnings
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from vectl.plan_path import (
    ENV_PLAN_PATH,
    ENV_PLAN_PATH_DEPRECATED,
    is_linked_worktree,
    resolve_plan_path,
    resolve_state_path,
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

        assert cli_mod.resolve_plan_path is resolve_plan_path

    def test_mcp_uses_shared_resolver(self) -> None:
        import vectl.mcp_server as mcp_mod

        assert mcp_mod.resolve_plan_path is resolve_plan_path


class TestResolveStatePath:
    """resolve_state_path follows git and non-git location strategy."""

    def test_explicit_plan_path_skips_default_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_path = Path("/tmp/explicit/plan.yaml")

        def _should_not_be_called() -> Path:
            raise AssertionError(
                "resolve_plan_path should not be called when plan_path is explicit"
            )

        monkeypatch.setattr("vectl.plan_path.resolve_plan_path", _should_not_be_called)
        with patch(
            "vectl.plan_path.subprocess.run",
            return_value=CompletedProcess([], 1, "", "not a git repository"),
        ):
            result = resolve_state_path(plan_path)

        assert result == plan_path.parent / ".vectl" / "state.json"

    def test_in_git_repo_uses_git_common_dir(self) -> None:
        plan_path = Path("/tmp/project/plan.yaml")
        with patch(
            "vectl.plan_path.subprocess.run",
            return_value=CompletedProcess([], 0, "/common/git\n", ""),
        ) as mock_run:
            result = resolve_state_path(plan_path)

        assert result == Path("/common/git") / "vectl" / "state.json"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=plan_path.parent,
        )

    def test_in_git_repo_anchors_relative_git_common_dir_to_plan_dir(self) -> None:
        plan_path = Path("/tmp/project/plan.yaml")
        with patch(
            "vectl.plan_path.subprocess.run",
            return_value=CompletedProcess([], 0, ".git\n", ""),
        ) as mock_run:
            result = resolve_state_path(plan_path)

        assert result == plan_path.parent / ".git" / "vectl" / "state.json"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=plan_path.parent,
        )

    def test_non_git_repo_falls_back_to_vectl_dir(self) -> None:
        plan_path = Path("/tmp/project/plan.yaml")
        with patch(
            "vectl.plan_path.subprocess.run",
            return_value=CompletedProcess([], 1, "", "not a git repository"),
        ) as mock_run:
            result = resolve_state_path(plan_path)

        assert result == plan_path.parent / ".vectl" / "state.json"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=plan_path.parent,
        )

    def test_git_invocation_error_falls_back_to_vectl_dir(self) -> None:
        plan_path = Path("/tmp/project/plan.yaml")
        with patch(
            "vectl.plan_path.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file or directory", "git"),
        ):
            result = resolve_state_path(plan_path)

        assert result == plan_path.parent / ".vectl" / "state.json"

    def test_uses_plan_path_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def resolve_plan_path_mock() -> Path:
            return Path("/different/plan.yaml")

        monkeypatch.setattr(
            "vectl.plan_path.resolve_plan_path",
            resolve_plan_path_mock,
        )
        with patch(
            "vectl.plan_path.subprocess.run",
            return_value=CompletedProcess([], 0, "  /common/git  \n", ""),
        ) as mock_run:
            result = resolve_state_path()

        assert result == Path("/common/git") / "vectl" / "state.json"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=Path("/different").resolve(),
        )


class TestWorktreeDetection:
    """Worktree detection in resolve_plan_path() and is_linked_worktree()."""

    def test_linked_worktree_resolves_to_main_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In a linked worktree, resolve_plan_path returns main worktree's plan.yaml."""
        # Create main worktree with plan.yaml
        main_root = tmp_path / "main_worktree"
        main_root.mkdir()
        main_plan = main_root / "plan.yaml"
        main_plan.write_text("project: main\nphases: []\n")

        # Mock is_linked_worktree to return (True, main_root)
        monkeypatch.setattr(
            "vectl.plan_path.is_linked_worktree",
            lambda: (True, main_root),
        )
        # Ensure no env vars and no local plan.yaml
        monkeypatch.delenv(ENV_PLAN_PATH, raising=False)
        monkeypatch.delenv(ENV_PLAN_PATH_DEPRECATED, raising=False)

        subdir = tmp_path / "linked_worktree" / "sub"
        subdir.mkdir(parents=True)
        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            result = resolve_plan_path()

        assert result == main_plan

    def test_main_worktree_uses_walkup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In main worktree (not linked), resolve_plan_path uses walkup discovery."""
        # Mock is_linked_worktree to return (False, None) - main worktree
        monkeypatch.setattr(
            "vectl.plan_path.is_linked_worktree",
            lambda: (False, None),
        )
        monkeypatch.delenv(ENV_PLAN_PATH, raising=False)
        monkeypatch.delenv(ENV_PLAN_PATH_DEPRECATED, raising=False)

        # Create plan.yaml in parent directory
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("project: test\nphases: []\n")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)

        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            result = resolve_plan_path()

        assert result == plan_file

    def test_git_not_available_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When git is not installed, resolve_plan_path falls through to walkup."""
        monkeypatch.delenv(ENV_PLAN_PATH, raising=False)
        monkeypatch.delenv(ENV_PLAN_PATH_DEPRECATED, raising=False)

        # Create plan.yaml in parent directory
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("project: test\nphases: []\n")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)

        # Mock subprocess.run to raise OSError (git not installed)
        def run_raises_oserror(*args, **kwargs):
            raise OSError(2, "No such file or directory", "git")

        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            with patch("vectl.plan_path.subprocess.run", side_effect=run_raises_oserror):
                result = resolve_plan_path()

        assert result == plan_file

    def test_git_fails_falls_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When git command fails, resolve_plan_path falls through to walkup."""
        monkeypatch.delenv(ENV_PLAN_PATH, raising=False)
        monkeypatch.delenv(ENV_PLAN_PATH_DEPRECATED, raising=False)

        # Create plan.yaml in parent directory
        plan_file = tmp_path / "plan.yaml"
        plan_file.write_text("project: test\nphases: []\n")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)

        # Mock subprocess.run to return non-zero (git command failed)
        with patch("vectl.plan_path.Path.cwd", return_value=subdir):
            with patch(
                "vectl.plan_path.subprocess.run",
                return_value=CompletedProcess([], 1, "", "not a git repository"),
            ):
                result = resolve_plan_path()

        assert result == plan_file

    def test_env_var_overrides_worktree_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VECTL_PLAN_PATH env var takes precedence over worktree detection."""
        # Set env var to a specific path
        env_plan_path = tmp_path / "env_plan.yaml"
        env_plan_path.write_text("project: env\nphases: []\n")
        monkeypatch.setenv(ENV_PLAN_PATH, str(env_plan_path))

        # Even if worktree detection would find something, env var should win
        monkeypatch.setattr(
            "vectl.plan_path.is_linked_worktree",
            lambda: (True, tmp_path / "main_worktree"),
        )

        result = resolve_plan_path()

        assert result == env_plan_path

    def test_is_linked_worktree_returns_true_with_main_root(self) -> None:
        """is_linked_worktree returns (True, main_root) when in linked worktree."""

        # Mock git commands: git-common-dir differs from git-dir (indicates linked worktree)
        def mock_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "--git-common-dir" in cmd:
                return CompletedProcess([], 0, "/main/.git\n", "")
            if "--git-dir" in cmd:
                return CompletedProcess([], 0, "/main/.git/worktrees/linked\n", "")
            if "--show-toplevel" in cmd:
                return CompletedProcess([], 0, "/main\n", "")
            raise AssertionError(f"Unexpected command: {cmd}")

        with patch("vectl.plan_path.subprocess.run", side_effect=mock_run):
            is_linked, main_root = is_linked_worktree()

        assert is_linked is True
        assert main_root is not None
        assert "main" in str(main_root)

    def test_is_linked_worktree_returns_false_in_main_worktree(
        self,
    ) -> None:
        """is_linked_worktree returns (False, None) when in main worktree."""

        # Mock git commands: git-common-dir equals git-dir (main worktree)
        def mock_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "--git-common-dir" in cmd:
                return CompletedProcess([], 0, "/main/.git\n", "")
            if "--git-dir" in cmd:
                return CompletedProcess([], 0, "/main/.git\n", "")
            raise AssertionError(f"Unexpected command: {cmd}")

        with patch("vectl.plan_path.subprocess.run", side_effect=mock_run):
            is_linked, main_root = is_linked_worktree()

        assert is_linked is False
        assert main_root is None
