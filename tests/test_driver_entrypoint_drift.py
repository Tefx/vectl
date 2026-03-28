"""Entrypoint parity tests for unified runtime adapter wiring.

These tests verify both process entrypoint surfaces share one adapter path and
the shared exit-code matrix from DRIVER-ARCHITECTURE §2.12.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_module_help() -> tuple[int, str, str]:
    """Run ``python -m vectl.driver --help``."""

    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def run_module_positional_config(config_path: Path) -> tuple[int, str, str]:
    """Run ``python -m vectl.driver <path>``."""

    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", str(config_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def run_module_option_config(config_path: Path) -> tuple[int, str, str]:
    """Run ``python -m vectl.driver --config <path>``."""

    result = subprocess.run(
        [sys.executable, "-m", "vectl.driver", "--config", str(config_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def run_vectl_drive_config(config_path: Path) -> tuple[int, str, str]:
    """Run ``uv run vectl drive --config <path>``."""

    result = subprocess.run(
        ["uv", "run", "vectl", "drive", "--config", str(config_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


class TestHelpParity:
    """Help/usage behavior must be stable for module entrypoint."""

    def test_module_driver_help_exits_zero(self) -> None:
        code, _, _ = run_module_help()
        assert code == 0

    def test_module_driver_help_shows_usage_and_config_option(self) -> None:
        code, stdout, stderr = run_module_help()
        combined = stdout + stderr
        assert code == 0
        assert "usage" in combined.lower()
        assert "--config" in combined


class TestRuntimeErrorParity:
    """Runtime/config errors from both entrypoints must map to exit code 1."""

    def test_module_positional_runtime_error_exits_one(self, tmp_path: Path) -> None:
        missing_config = tmp_path / "missing-driver.yaml"
        code, _, _ = run_module_positional_config(missing_config)
        assert code == 1

    def test_module_option_runtime_error_exits_one(self, tmp_path: Path) -> None:
        missing_config = tmp_path / "missing-driver.yaml"
        code, _, _ = run_module_option_config(missing_config)
        assert code == 1

    def test_vectl_drive_runtime_error_exits_one(self, tmp_path: Path) -> None:
        missing_config = tmp_path / "missing-driver.yaml"
        code, _, _ = run_vectl_drive_config(missing_config)
        assert code == 1

    def test_runtime_error_exit_code_parity_all_entrypoints(self, tmp_path: Path) -> None:
        missing_config = tmp_path / "missing-driver.yaml"
        code_positional, _, _ = run_module_positional_config(missing_config)
        code_option, _, _ = run_module_option_config(missing_config)
        code_vectl, _, _ = run_vectl_drive_config(missing_config)
        assert code_positional == code_option == code_vectl == 1


class TestUsageErrorExitCode:
    """Parsing/usage failures must map to exit code 2."""

    def test_module_driver_unknown_option_exits_two(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver", "--not-a-real-option"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2

    def test_module_driver_requires_config_argument(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "vectl.driver"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 2
