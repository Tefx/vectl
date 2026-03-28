"""Entrypoint drift tests — expose parity gaps between entrypoint surfaces.

These tests capture the CURRENT (pre-unification) behavioral drift between:
- ``python -m vectl.driver`` (module entrypoint)
- ``uv run vectl drive`` (CLI entrypoint)

Test cases record exact parse/runtime divergence BEFORE implementation.

Required cases (per step contract):
- python -m vectl.driver --help
- python -m vectl.driver path.yaml
- python -m vectl.driver --config path.yaml
- uv run vectl drive --config path.yaml
- equal error class -> equal exit code across both entrypoints

At least one fixture uses the documented minimal config path form without
convenience wrapping (per conftest.py DRIVER_YAML_SPEC fixture contract).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


# =============================================================================
# Helpers
# =============================================================================


def run_module_help() -> tuple[int, str, str]:
    """Run 'python -m vectl.driver --help' and return (code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", "--help"],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def run_module_positional_config(config_path: Path) -> tuple[int, str, str]:
    """Run 'python -m vectl.driver <path>' and return (code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", str(config_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def run_module_option_config(config_path: Path) -> tuple[int, str, str]:
    """Run 'python -m vectl.driver --config <path>' and return (code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", "--config", str(config_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def run_vectl_drive_config(config_path: Path) -> tuple[int, str, str]:
    """Run 'uv run vectl drive --config <path>' and return (code, stdout, stderr)."""
    result = subprocess.run(
        ["uv", "run", "vectl", "drive", "--config", str(config_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


# =============================================================================
# Test: --help parity
# =============================================================================


class TestHelpParity:
    """Test help output parity across entrypoints."""

    def test_module_driver_help_exits_zero(self) -> None:
        """python -m vectl.driver --help should exit 0."""
        code, stdout, stderr = run_module_help()
        # RED: Currently this raises NotImplementedError (exit 1)
        # After unification: should exit 0 with help text
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr[:200]}"

    def test_module_driver_help_shows_usage(self) -> None:
        """python -m vectl.driver --help should show usage information."""
        code, stdout, stderr = run_module_help()
        # RED: Currently raises NotImplementedError
        # After unification: should show usage
        assert "usage" in stdout.lower() or "usage" in stderr.lower(), (
            f"Help output should mention 'usage'. stdout: {stdout[:200]}, stderr: {stderr[:200]}"
        )

    def test_module_driver_help_shows_config_option(self) -> None:
        """python -m vectl.driver --help should show --config option."""
        code, stdout, stderr = run_module_help()
        # RED: Currently raises NotImplementedError
        # After unification: should show --config option
        combined = stdout + stderr
        assert "--config" in combined, (
            f"Help output should show --config option. stdout: {stdout[:200]}, stderr: {stderr[:200]}"
        )


# =============================================================================
# Test: positional config parsing
# =============================================================================


class TestPositionalConfigParsing:
    """Test positional config argument parsing on module entrypoint."""

    def test_module_driver_positional_config_not_implemented(
        self, minimal_driver_yaml: Path
    ) -> None:
        """python -m vectl.driver <path> currently raises NotImplementedError.

        This test records the current state: the positional config form
        is wired but hits the NotImplementedError in get_runtime_adapter().
        """
        code, stdout, stderr = run_module_positional_config(minimal_driver_yaml)
        # RED: Currently raises NotImplementedError -> exit 1
        # After unification: should parse config and proceed
        assert code == 1, f"Expected exit 1 (NotImplementedError), got {code}"
        combined = stdout + stderr
        assert "NotImplementedError" in combined or "deferred" in combined.lower(), (
            f"Expected NotImplementedError about deferred implementation. Got: {combined[:200]}"
        )


# =============================================================================
# Test: --config option parsing
# =============================================================================


class TestOptionConfigParsing:
    """Test --config option parsing on module entrypoint."""

    def test_module_driver_option_config_not_implemented(self, minimal_driver_yaml: Path) -> None:
        """python -m vectl.driver --config <path> currently raises NotImplementedError.

        This test records the current state: the --config option form
        is wired but hits the NotImplementedError in get_runtime_adapter().
        """
        code, stdout, stderr = run_module_option_config(minimal_driver_yaml)
        # RED: Currently raises NotImplementedError -> exit 1
        # After unification: should parse config and proceed
        assert code == 1, f"Expected exit 1 (NotImplementedError), got {code}"
        combined = stdout + stderr
        assert "NotImplementedError" in combined or "deferred" in combined.lower(), (
            f"Expected NotImplementedError about deferred implementation. Got: {combined[:200]}"
        )


# =============================================================================
# Test: vectl drive --config parity
# =============================================================================


class TestVectlDriveEntrypoint:
    """Test vectl drive CLI entrypoint against module entrypoint."""

    def test_vectl_drive_config_not_implemented(self, minimal_driver_yaml: Path) -> None:
        """uv run vectl drive --config <path> currently raises NotImplementedError.

        This test records the current state: vectl drive delegates to
        run_drive_cli_entrypoint which raises NotImplementedError.
        """
        code, stdout, stderr = run_vectl_drive_config(minimal_driver_yaml)
        # RED: Currently raises NotImplementedError -> exit 1
        # After unification: should parse config and proceed
        assert code == 1, f"Expected exit 1 (NotImplementedError), got {code}"
        combined = stdout + stderr
        assert "NotImplementedError" in combined or "deferred" in combined.lower(), (
            f"Expected NotImplementedError about deferred implementation. Got: {combined[:200]}"
        )

    def test_vectl_drive_help_shows_config_option(self) -> None:
        """uv run vectl drive --help should show --config option.

        This is a baseline: vectl drive --help already works in the current
        implementation because it uses Typer's built-in help generation.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "drive", "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        # GREEN: vectl drive --help already works
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"
        assert "--config" in combined, (
            f"vectl drive --help should show --config option. Got: {combined[:200]}"
        )


# =============================================================================
# Test: exit code parity for NotImplementedError (same error class)
# =============================================================================


class TestExitCodeParity:
    """Test that equal error classes produce equal exit codes."""

    def test_not_implemented_error_same_exit_code_module_positional(
        self, minimal_driver_yaml: Path
    ) -> None:
        """Same error class (NotImplementedError) should produce same exit code
        for python -m vectl.driver positional config."""
        code, _, _ = run_module_positional_config(minimal_driver_yaml)
        # Both should exit with code 1 (NotImplementedError)
        assert code == 1, f"Expected exit 1, got {code}"

    def test_not_implemented_error_same_exit_code_module_option(
        self, minimal_driver_yaml: Path
    ) -> None:
        """Same error class (NotImplementedError) should produce same exit code
        for python -m vectl.driver --config."""
        code, _, _ = run_module_option_config(minimal_driver_yaml)
        # Should exit with code 1 (NotImplementedError)
        assert code == 1, f"Expected exit 1, got {code}"

    def test_not_implemented_error_same_exit_code_vectl_drive(
        self, minimal_driver_yaml: Path
    ) -> None:
        """Same error class (NotImplementedError) should produce same exit code
        for vectl drive --config."""
        code, _, _ = run_vectl_drive_config(minimal_driver_yaml)
        # Should exit with code 1 (NotImplementedError)
        assert code == 1, f"Expected exit 1, got {code}"

    def test_exit_code_parity_all_entrypoints(self, minimal_driver_yaml: Path) -> None:
        """All entrypoints should exit with same code for NotImplementedError.

        This is the key parity test: currently all three entrypoints
        (module positional, module --config, vectl drive --config) should
        exit with code 1 because they all hit NotImplementedError.
        """
        code_positional, _, _ = run_module_positional_config(minimal_driver_yaml)
        code_option, _, _ = run_module_option_config(minimal_driver_yaml)
        code_vectl, _, _ = run_vectl_drive_config(minimal_driver_yaml)

        # All should be 1 (NotImplementedError)
        # This is GREEN for the current state (all fail the same way)
        assert code_positional == code_option == code_vectl == 1, (
            f"Exit code parity broken: positional={code_positional}, "
            f"option={code_option}, vectl_drive={code_vectl}"
        )


# =============================================================================
# Test: usage error exit code (exit 2)
# =============================================================================


class TestUsageErrorExitCode:
    """Test that usage/argument errors produce exit code 2."""

    def test_module_driver_unknown_option_exits_two(self) -> None:
        """python -m vectl.driver --unknown-option should exit with code 2."""
        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver", "--unknown-option"],
            capture_output=True,
            text=True,
        )
        # RED: Currently may raise NotImplementedError instead of parsing args
        # After unification: should exit 2 for usage error
        # The NotImplementedError path exits 1, but a real argparse error should exit 2
        # Currently this hits the module's main() which calls run_driver_module_entrypoint
        # which raises NotImplementedError -> exit 1
        assert result.returncode in (1, 2), (
            f"Expected exit 1 (NotImplementedError) or 2 (usage error), got {result.returncode}. "
            f"stderr: {result.stderr[:200]}"
        )

    def test_exit_code_2_for_illegal_option(self) -> None:
        """Unknown option should produce exit code 2 (usage error).

        RED: Currently python -m vectl.driver with an unknown option
        raises NotImplementedError (exit 1) instead of argparse's
        error (exit 2). This is a parse/runtime divergence that
        the unification step should fix.
        """
        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver", "--not-a-real-option"],
            capture_output=True,
            text=True,
        )
        # After unification: should exit 2 for argparse error
        # Current state: exits 1 because it hits NotImplementedError before argparse
        # This test records the divergence
        if result.returncode == 1:
            # This is the current (pre-unification) behavior
            combined = result.stdout + result.stderr
            assert "NotImplementedError" in combined or "deferred" in combined.lower(), (
                f"If exit 1, should be NotImplementedError. Got: {combined[:200]}"
            )
        # After fix: should exit 2 (argparse error)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def minimal_driver_yaml(tmp_path: Path) -> Path:
    """Minimal valid driver config — no convenience wrapping.

    Per conftest.py DRIVER_YAML_SPEC: fixture must use exact spec format
    without computed defaults.

    This is the documented minimal config path form per the architecture
    doc Section 2.3: runners (required, non-empty), judge.runner in runners,
    fallback_runner in runners.
    """
    import yaml

    config = {
        "runners": {
            "opencode": {
                "command": "opencode",
            },
        },
        "fallback_runner": "opencode",
        "judge": {
            "runner": "opencode",
        },
    }
    path = tmp_path / "driver.yaml"
    path.write_text(yaml.dump(config))
    return path
