#!/usr/bin/env python3
"""Liveness probe: supported orchestration CLI wiring.

Step: orch_system_wiring.liveness_probe
Intent: Prove the public orchestration CLI accepts a clean, test-local fixture.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_local_orch_fixture(root: Path) -> None:
    """Create a minimal, self-contained orchestration fixture."""

    (root / "plan.yaml").write_text(
        """
version: 1
project: orch-liveness-test
phases:
  - id: core
    name: Core
    steps:
      - id: core.ready
        name: Ready
        status: pending
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "vectl.yaml").write_text(
        """
orchestration:
  plan_path: plan.yaml
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _init_git_repo(root: Path) -> None:
    """Initialize the minimal git state required by orchestration worktrees."""

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    subprocess.run(
        ["git", "add", "plan.yaml", "vectl.yaml"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    commit_env = {
        "GIT_AUTHOR_NAME": "orch-liveness",
        "GIT_AUTHOR_EMAIL": "orch-liveness@example.com",
        "GIT_COMMITTER_NAME": "orch-liveness",
        "GIT_COMMITTER_EMAIL": "orch-liveness@example.com",
    }
    subprocess.run(
        ["git", "commit", "-m", "init orchestration liveness fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={**os.environ, **commit_env},
    )


def test_orch_liveness_cli_entrypoint():
    """Test: `vectl orch run` works with a test-local clean fixture."""

    with tempfile.TemporaryDirectory() as tmpdir:
        fixture_root = Path(tmpdir)
        _write_local_orch_fixture(fixture_root)
        _init_git_repo(fixture_root)

        # ACT: invoke the supported orchestration CLI against the local fixture
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "run", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=fixture_root,
        )

        # ASSERT: verify the supported public CLI starts and produces a run result

        print("=== STDOUT ===")
        print(result.stdout)
        print("=== STDERR ===")
        print(result.stderr)
        print("=== EXIT CODE ===")
        print(result.returncode)

        assert result.returncode == 0, result.stderr or result.stdout

        combined = result.stdout + result.stderr
        assert "Runtime wiring failure" not in combined, combined
        assert '"success": true' in result.stdout, result.stdout
        assert '"step_id": "core.ready"' in result.stdout, result.stdout

        print(f"PASS: Supported CLI started with exit code {result.returncode}")


if __name__ == "__main__":
    try:
        test_orch_liveness_cli_entrypoint()
        sys.exit(0)
    except subprocess.TimeoutExpired:
        print("FAIL: Orchestration did not respond within timeout")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
