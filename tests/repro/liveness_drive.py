"""Liveness probe: vectl drive command.

Expected:
  - CLI command starts without import/initialization errors
  - Config loading succeeds
  - Runtime path is invoked (reaches decide/dispatch loop)
  - events.jsonl is created with proper DECIDE and FINAL events
  - Clean exit

Actual:
  - Verified via execution
"""

import subprocess
import sys
import tempfile
from pathlib import Path


def create_minimal_driver_yaml(path: Path) -> None:
    """Create a minimal driver.yaml configuration."""
    config = """
# Minimal driver config for liveness probe
plan_path: null

runners:
  opencode:
    command: "opencode"
    args: ["run", "--format", "json"]
    prompt_mode: stdin_dash
    stall_timeout: 60
    output_parser: opencode_jsonl

fallback_runner: opencode

orchestration:
  max_parallelism: 1

session:
  reuse_ttl: 300

judge:
  runner: opencode
  preflight: false
  evidence_validation: false
  failure_classification: false
  escalation: false
  gate_assessment: false
  cold_context: false
  anomaly: false

observability:
  events_file: ".vectl/driver-events.jsonl"
  log_level: INFO
  print_progress: true
"""
    path.write_text(config.strip(), encoding="utf-8")


def create_empty_plan_yaml(path: Path) -> None:
    """Create a minimal empty plan.yaml (no executable steps)."""
    plan = """
version: 1
project: liveness-probe-test
context: |
  Empty plan for vectl drive liveness probe.

phases: []
"""
    path.write_text(plan.strip(), encoding="utf-8")


def main() -> int:
    """Run liveness probe for vectl drive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create minimal config
        driver_yaml = tmp_path / "driver.yaml"
        plan_yaml = tmp_path / "plan.yaml"

        create_minimal_driver_yaml(driver_yaml)
        create_empty_plan_yaml(plan_yaml)

        # Initialize git repo (required for plan operations)
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "liveness@probe.test"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=5,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Liveness Probe"],
            cwd=str(tmp_path),
            capture_output=True,
            timeout=5,
            check=True,
        )

        # Create .vectl directory for events
        vectl_dir = tmp_path / ".vectl"
        vectl_dir.mkdir(parents=True, exist_ok=True)

        # Run vectl drive with timeout
        # The driver should:
        # 1. Load config (validated by vectl.driver.config.load_config)
        # 2. Load plan (validated by vectl.io.load_plan_definition)
        # 3. Initialize observer (validated by observer.emit writing to events.jsonl)
        # 4. Enter decide loop (validated by DECIDE event in events.jsonl)
        # 5. Exit cleanly (validated by FINAL event in events.jsonl)
        try:
            result = subprocess.run(
                ["uv", "run", "vectl", "drive", "--config", str(driver_yaml)],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("FAIL: vectl drive timed out after 60s (possible infinite loop)")
            print("This indicates the command started but did not exit cleanly.")
            return 1

        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode

        print("=" * 70)
        print("LIVENESS PROBE: vectl drive --config driver.yaml")
        print("=" * 70)
        print()
        print(f"Exit code: {exit_code}")
        print()
        print(f"STDOUT ({len(stdout)} chars):")
        print(stdout[:1000] if stdout else "(empty)")
        print()
        print(f"STDERR ({len(stderr)} chars):")
        print(stderr[:1000] if stderr else "(empty)")
        print()

        # Verify liveness:
        # 1. Exit should be 0 (clean exit with no steps)
        # 2. Should NOT have Python import/syntax errors (dead-on-arrival)
        # 3. events.jsonl should exist and contain DECIDE + FINAL events

        combined = stdout + stderr

        # Check for Python import/syntax errors (definitive DOA marker)
        fatal_patterns = [
            "ModuleNotFoundError:",
            "SyntaxError:",
            "IndentationError:",
            "cannot import name '",
            "ImportError:",
        ]

        for pattern in fatal_patterns:
            if pattern in combined:
                print(f"FAIL: Fatal error pattern detected: {pattern}")
                print("This indicates the command failed to initialize properly.")
                return 1

        print("PASS: No Python import/syntax errors detected")

        # Check events.jsonl was created (proves observer initialization + runtime entry)
        events_file = vectl_dir / "driver-events.jsonl"
        if not events_file.exists():
            print("FAIL: events.jsonl was not created")
            print("This indicates the driver did not reach the runtime entry point.")
            return 1

        events_content = events_file.read_text(encoding="utf-8")
        if not events_content.strip():
            print("FAIL: events.jsonl exists but is empty")
            print("This indicates the driver started but did not emit any events.")
            return 1

        print("PASS: events.jsonl was created and has content")
        print()
        print("Events (liveness proof):")
        for line in events_content.splitlines()[:10]:
            print(f"  {line[:150]}")
        print()

        # Parse events and verify required markers
        import json

        events = []
        for line in events_content.splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        event_types = [e.get("event") for e in events]

        # Required events for liveness:
        # - DECIDE: proves the driver entered the main loop
        # - FINAL: proves the driver completed the shutdown sequence
        required_events = ["DECIDE", "FINAL"]
        missing = [e for e in required_events if e not in event_types]

        if missing:
            print(f"FAIL: Missing required events: {missing}")
            print(f"Found events: {event_types}")
            return 1

        print(f"PASS: Required events present: {required_events}")

        # Verify exit code is 0 (clean shutdown)
        if exit_code != 0:
            print(f"FAIL: Exit code should be 0 for clean shutdown, got {exit_code}")
            return 1

        print("PASS: Exit code is 0 (clean shutdown)")
        print()
        print("=" * 70)
        print("LIVENESS VERDICT: PASS")
        print("  ✓ Command starts without import errors")
        print("  ✓ Config is loaded (no ConfigError)")
        print("  ✓ Plan is loaded (no PlanError)")
        print("  ✓ Observer creates events.jsonl")
        print("  ✓ Driver enters DECIDE loop (DECIDE event)")
        print("  ✓ Driver exits cleanly (FINAL event, exit code 0)")
        print("=" * 70)
        return 0


if __name__ == "__main__":
    sys.exit(main())
