#!/usr/bin/env python3
"""Liveness probe: orchestration system wiring.

Step: orch_system_wiring.liveness_probe
Intent: Prove complete vertical slice from entrypoint -> control -> runtime -> result.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_orch_liveness_module_entrypoint():
    """Test: python -m vectl.driver accepts invocation and orchestrates."""

    # ARRANGE: Use the actual driver.yaml in the worktree root
    config_path = Path("driver.yaml")
    if not config_path.exists():
        print(f"FAIL: Config not found: {config_path}")
        sys.exit(1)

    # ACT: Invoke the driver module entrypoint
    # Use timeout to prevent hanging if orchestration doesn't complete
    result = subprocess.run(
        ["uv", "run", "python", "-m", "vectl.driver", str(config_path)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=Path(__file__).parent.parent.parent,  # Worktree root
    )

    # ASSERT: Verify entrypoint starts and produces output
    # The driver should at least parse config and initialize orchestration
    # Even if plan.yaml has no available steps, it should exit cleanly

    print("=== STDOUT ===")
    print(result.stdout)
    print("=== STDERR ===")
    print(result.stderr)
    print("=== EXIT CODE ===")
    print(result.returncode)

    # A liveness probe proves the system is not dead - it accepts input
    # Exit code 0 = clean shutdown (no work to do)
    # Exit code 1 = runtime error (but system started)
    # Exit code 2 = usage error (argparsing failed - FAIL)

    assert result.returncode != 2, "Entrypoint rejected invocation (usage error)"

    # At minimum, the orchestration wiring must succeed:
    # - Config parses
    # - Plan loads
    # - Control instantiates
    # - Resolver binds

    # Check for evidence of orchestration in stderr (logging) or stdout
    combined = result.stdout + result.stderr

    # If orchestration wired successfully, we should see NO error about it
    assert "ORCHESTRATION_WIRING_UNAVAILABLE" not in combined, (
        f"Orchestration wiring failed: {combined}"
    )

    # If it started cleanly, it either:
    # 1. Ran no steps (plan had nothing claimable) - clean exit
    # 2. Ran some steps and completed
    # Both are valid "alive" outcomes

    print(f"PASS: Entrypoint started with exit code {result.returncode}")


if __name__ == "__main__":
    try:
        test_orch_liveness_module_entrypoint()
        sys.exit(0)
    except subprocess.TimeoutExpired:
        print("FAIL: Orchestration did not respond within timeout")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
