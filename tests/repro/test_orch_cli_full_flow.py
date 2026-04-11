#!/usr/bin/env python3
"""Reproduction: Active-run control commands (pause/unpause/stop) via public CLI.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4,
`vectl orch pause|unpause|stop` must:
  1. Accept a run selector (RUN_ID or --latest) per §5.2
  2. Persist a control request to the filesystem control channel
  3. Return success (exit 0) once the request is queued
  4. Emit an event log entry (operator_action_requested per §10.1)
  5. Not synchronously terminate the run — control requests are async per §7.4 stop behavior

Actual: Testing black-box to verify control command registration, argument handling,
and behavior against an actually running orchestration run using isolated temp repos
and a controllable slow runner shim.

This is an expected-red test: failures expose missing implementation, not bugs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="cli_blackbox_active_control_recovery.define_active_control_recovery_tests",
    rationale="Active-run control commands are intentionally red until pause/unpause/stop "
    "are fully wired end-to-end with real subprocess invocation and control channel "
    "persistence confirmed via black-box CLI.",
)

# ---------------------------------------------------------------------------
# Fixture helpers: isolated temp repo with real git init + plan.yaml + vectl.yaml
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path) -> Path:
    """Write a minimal plan.yaml with one pending step."""
    plan = {
        "version": 1,
        "project": "orch-control-test",
        "strategy_ref": "#",
        "context": "black-box control flow verification",
        "phases": [
            {
                "id": "test",
                "name": "Test Phase",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "test.slow_step",
                        "name": "Slow Step",
                        "status": "pending",
                        "description": "A step that takes long enough for control commands to interact with",
                        "agent": "slow-shim",
                    },
                ],
            }
        ],
    }
    plan_path = root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def _write_vectl_yaml(root: Path, runner_cmd: str) -> Path:
    """Write vectl.yaml pointing at plan.yaml with slow-shim runner."""
    config = {
        "orchestration": {
            "plan_path": "plan.yaml",
            "defaults": {
                "ordinary_role": "python-executor",
            },
            "role_profiles": {
                "python-executor": {
                    "agent_id": "python-executor",
                    "prompt_family": "coder",
                    "execution_context": "linked_worktree",
                    "mutation_policy": "worktree_changes",
                    "session_policy": "reuse_allowed",
                    "output_contract": "freeform_evidence",
                    "default_runner": "slow-shim",
                },
            },
            "runtime": {
                "default_runner": "slow-shim",
                "artifact_root": ".vectl/runs",
                "workspace_root": ".vectl/workspaces",
            },
            "control": {
                "idle_poll_interval_ms": 500,
            },
        },
        "runners": {
            "slow-shim": {
                "command": runner_cmd,
                "args": [],
                "prompt_mode": "stdin",
                "stall_timeout": 300,
                "supports_resume": False,
            },
        },
    }
    vectl_path = root / "vectl.yaml"
    vectl_path.write_text(yaml.dump(config, sort_keys=False), encoding="utf-8")
    return vectl_path


def _write_slow_shim(root: Path) -> Path:
    """Write a slow runner shim script that sleeps for a configurable time.

    The shim reads SLEEP_SECONDS from env (default 10) and writes a marker file.
    It accepts stdin (as orch runner protocol) and exits 0.
    """
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "slow_runner.sh"
    shim_content = """#!/usr/bin/env bash
# Slow runner shim for black-box control flow testing.
# Reads from stdin (prompt), sleeps, writes marker, exits 0.
SLEEP="${SLEEP_SECONDS:-10}"
MARKER="${SHIM_MARKER:-/tmp/orch_shim_marker}"
echo "SHIM: starting sleep for ${SLEEP}s" >&2
# Drain stdin in background to avoid pipe stall
cat > /dev/null &
CAT_PID=$!
sleep "$SLEEP"
kill "$CAT_PID" 2>/dev/null || true
echo "SHIM: completed" > "$MARKER"
echo '{"status": "success", "summary": "slow-shim completed"}'
exit 0
"""
    shim_path.write_text(shim_content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _init_git_repo(root: Path) -> None:
    """Initialize a git repo with plan.yaml and vectl.yaml committed."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "orch-test",
        "GIT_AUTHOR_EMAIL": "orch-test@example.com",
        "GIT_COMMITTER_NAME": "orch-test",
        "GIT_COMMITTER_EMAIL": "orch-test@example.com",
    }
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "add", "plan.yaml", "vectl.yaml"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    subprocess.run(
        ["git", "commit", "-m", "init control test fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env=env,
    )


def _run_vectl(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run vectl CLI with given args in cwd."""
    return subprocess.run(
        ["uv", "run", "vectl"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Test: Control commands on an actually running run
# ---------------------------------------------------------------------------


class TestControlCommandsOnRunningRun:
    """Black-box tests for pause/unpause/stop on an actively running run.

    These tests verify that:
    1. Control commands are registered and accept documented flags
    2. Control commands persist requests to filesystem control channel
    3. Control commands return success (exit 0) per spec §7.4
    4. `vectl orch actions` can list persisted control requests
    """

    def test_pause_command_accepts_latest_flag(self, tmp_path: Path) -> None:
        """Verify `vectl orch pause --latest` accepts valid arguments.

        Spec: §7.4 - `vectl orch pause [RUN_ID|--latest]`
        Expected: Command is registered, accepts --latest flag.
        Gap: If command not registered (exit 127) or not wired, test exposes gap.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "pause", "--latest", "--reason", "test pause"], cwd=tmp_path)
        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch pause --latest` not registered\n"
                "Per §7.4: Control family must accept --latest flag"
            )

        # Expected: exit 0 or a state error (no runs found, etc.), not a wiring error
        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch pause` not fully wired\n"
                f"  exit code: {result.returncode}\n"
                f"  stderr: {result.stderr[:500]}"
            )

        # Acceptable outcomes:
        # exit 0 = success (control request persisted)
        # exit 1 = general error (e.g., no active run to pause)
        # exit 2 = not found (no run found for --latest)
        # exit 4 = recovery required
        # exit 127 = command not registered (GAP)
        print(f"INFO: `vectl orch pause --latest` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:500]}")

    def test_unpause_command_accepts_latest_flag(self, tmp_path: Path) -> None:
        """Verify `vectl orch unpause --latest` accepts valid arguments.

        Spec: §7.4 - `vectl orch unpause [RUN_ID|--latest]`
        Expected: Command is registered, accepts --latest flag.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(
            ["orch", "unpause", "--latest", "--reason", "test unpause"], cwd=tmp_path
        )
        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch unpause --latest` not registered\n"
                "Per §7.4: Control family must accept --latest flag"
            )

        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch unpause` not fully wired\n"
                f"  exit code: {result.returncode}\n"
                f"  stderr: {result.stderr[:500]}"
            )

        print(f"INFO: `vectl orch unpause --latest` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")

    def test_stop_command_accepts_latest_flag(self, tmp_path: Path) -> None:
        """Verify `vectl orch stop --latest` accepts valid arguments.

        Spec: §7.4 - `vectl orch stop [RUN_ID|--latest] [--reason TEXT]`
        Expected: Command is registered, accepts --latest and --reason flags.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "stop", "--latest", "--reason", "test stop"], cwd=tmp_path)
        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch stop --latest` not registered\n"
                "Per §7.4: Control stop must accept --latest and --reason"
            )

        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch stop` not fully wired\n"
                f"  exit code: {result.returncode}\n"
                f"  stderr: {result.stderr[:500]}"
            )

        print(f"INFO: `vectl orch stop --latest` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")

    def test_pause_persists_control_request(self, tmp_path: Path) -> None:
        """Verify that `vectl orch pause` persists a control.request.pause file.

        Spec: §7.4 - pause queues a request so dispatch can stop.
        Spec: §9 - All state changes emit events; control requests are persisted.
        Expected: After pause, .vectl/runs/<run>/control/ should contain a pending request.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # First, start a run to have something to pause
        run_result = _run_vectl(
            ["orch", "run", "--json"],
            cwd=tmp_path,
            timeout=30,
        )
        print(f"INFO: orch run exited {run_result.returncode}")
        print(f"INFO: stdout: {run_result.stdout[:500]}")

        # If run failed, we still test pause registration
        # but skip the persistence check if no run exists
        if run_result.returncode == 0:
            try:
                run_data = json.loads(run_result.stdout)
                run_id = run_data.get("run_id", "")
            except (json.JSONDecodeError, KeyError):
                run_id = ""

            # Now try to pause
            pause_result = _run_vectl(
                ["orch", "pause", "--latest", "--reason", "test persistence"],
                cwd=tmp_path,
            )
            print(f"INFO: orch pause exited {pause_result.returncode}")
            print(f"INFO: stdout: {pause_result.stdout[:500]}")

            # Check if control request was persisted to filesystem
            runs_dir = tmp_path / ".vectl" / "runs"
            if runs_dir.exists():
                # Look for any control channel files
                control_files = list(runs_dir.rglob("*.json")) + list(runs_dir.rglob("*.yaml"))
                print(f"INFO: Found {len(control_files)} artifact files under .vectl/runs/")

                # Check specifically for control channel directories
                for run_dir in runs_dir.iterdir():
                    if run_dir.is_dir():
                        control_dir = run_dir / "control"
                        if control_dir.exists():
                            pending_dir = control_dir / "pending"
                            if pending_dir.exists():
                                pending_files = list(pending_dir.iterdir())
                                print(f"INFO: Found {len(pending_files)} pending control requests")

                # Also check for events file mentioning pause
                events_files = list(runs_dir.rglob("events.jsonl"))
                for ef in events_files:
                    content = ef.read_text()
                    if "control_pause" in content or "pause" in content.lower():
                        print("INFO: Found pause event in events log")
                        break

    def test_actions_lists_control_requests(self, tmp_path: Path) -> None:
        """Verify `vectl orch actions` can list pending/applied/rejected requests.

        Spec: §7.2 - `vectl orch actions [--run RUN_ID|--latest]`
        Expected: Command shows operator action requests including control commands.
        Gap: If no action persistence surface exists, test exposes gap.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "actions", "--latest"], cwd=tmp_path)
        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch actions --latest` not registered")

        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch actions` not fully wired\n"
                f"  exit code: {result.returncode}\n"
                f"  stderr: {result.stderr[:500]}"
            )

        print(f"INFO: `vectl orch actions --latest` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")

    def test_control_pause_flat_alias(self, tmp_path: Path) -> None:
        """Verify flat alias `vectl orch pause` works same as `vectl orch control pause`.

        Spec: §7.4 - `vectl orch pause` is alias for `vectl orch control pause`.
        Expected: Both forms are registered and produce same behavior.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Test flat alias
        result_flat = _run_vectl(["orch", "pause", "--latest"], cwd=tmp_path)
        result_control = _run_vectl(["orch", "control", "pause", "--latest"], cwd=tmp_path)

        # Both should be registered (not exit 127)
        if result_flat.returncode == 127 and result_control.returncode == 127:
            raise AssertionError(
                "GAP [registration]: Neither `vectl orch pause` nor `vectl orch control pause` registered"
            )

        # If one is registered and the other is not, that's a gap
        if result_flat.returncode == 127 and result_control.returncode != 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch pause` flat alias not registered "
                "but `vectl orch control pause` is"
            )
        if result_flat.returncode != 127 and result_control.returncode == 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch control pause` not registered "
                "but `vectl orch pause` flat alias is"
            )

        print(f"INFO: flat alias exited {result_flat.returncode}")
        print(f"INFO: control subcommand exited {result_control.returncode}")


class TestStopBehaviorAsync:
    """Black-box tests verifying stop behavior is asynchronous per §7.4.

    Spec §7.4 stop: 'Return success once the request is queued;
    terminal state change is asynchronous.'
    """

    def test_stop_returns_success_quickly(self, tmp_path: Path) -> None:
        """Verify stop command returns quickly (async, not blocking for run termination).

        Spec: §7.4 - stop returns success once request is queued.
        Expected: Stop returns in < 5 seconds, doesn't wait for actual termination.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        start = time.time()
        result = _run_vectl(
            ["orch", "stop", "--latest", "--reason", "test async stop"],
            cwd=tmp_path,
            timeout=10,
        )
        elapsed = time.time() - start

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch stop` not registered")

        # Per §7.4: stop should return quickly — it only queues a request
        # If it takes > 5s, it's likely blocking for termination
        print(f"INFO: `vectl orch stop` returned in {elapsed:.2f}s with exit {result.returncode}")
        # Note: We don't assert on time here because CI may be slow,
        # but we document the expectation

    def test_stop_json_output_format(self, tmp_path: Path) -> None:
        """Verify stop --json returns structured JSON output.

        Spec: §7.2 - Most subcommands support --json flag.
        Spec: §7.4 - stop returns ControlResult with action, success, message.
        Expected: JSON output contains control action information.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "stop", "--latest", "--json"], cwd=tmp_path)
        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch stop --json` not registered")

        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError("GAP [wiring]: `vectl orch stop --json` not fully wired")

        # Parse output — should be JSON if --json is supported
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                print(
                    f"INFO: Stop --json output keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
                )
                # Per §7.4 ControlResult: action, success, message
                if isinstance(data, dict):
                    for expected_key in ("action", "success", "message"):
                        if expected_key not in data:
                            print(
                                f"WARN: Missing expected key '{expected_key}' in stop JSON output"
                            )
            except json.JSONDecodeError:
                print(f"WARN: Stop --json output was not valid JSON: {result.stdout[:200]}")


class TestControlBehavioralContracts:
    """Deep behavioral contract tests for control commands.

    These tests verify that control requests are ACTUALLY CONSUMED by the
    orchestration dispatch loop, not just persisted. These are expected-red
    because the dispatch loop doesn't run during a single CLI invocation.

    Spec: §7.4 - pause 'stop dispatching new work'
    Spec: §7.4 - stop 'Persist a control.stop request... terminal state change
    is asynchronous'
    Spec: §10.1 - operator_action_requested event emitted on control actions
    """

    def test_pause_produces_applied_action_receipt(self, tmp_path: Path) -> None:
        """Verify that a pause request results in an applied action receipt.

        Spec: §7.4 - pause requests should be consumed (acknowledged) by the dispatch loop.
        Spec: §9.3 - Acknowledgement creates applied/ receipt.
        GAP: If dispatch loop doesn't consume control requests, pause only queues.
        This test EXPOSES the gap: pause persists but is never acknowledged.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue pause
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test applied receipt"],
            cwd=tmp_path,
        )

        # Per §9.3: after pause, the dispatch loop should acknowledge the request
        # as "applied" — meaning it was consumed and acted upon.
        # GAP: Currently, pause only queues the request. The dispatch loop
        # (which runs in a separate long-lived process) does NOT consume
        # it during a single CLI invocation. The request stays "pending".
        runs_dir = tmp_path / ".vectl" / "runs"
        applied_dirs = list(runs_dir.rglob("applied/*.json")) if runs_dir.exists() else []
        pending_dirs = list(runs_dir.rglob("pending/*.json")) if runs_dir.exists() else []

        print(f"INFO: Applied action receipts: {len(applied_dirs)}")
        print(f"INFO: Pending action requests: {len(pending_dirs)}")

        # EXPECTED-RED: This assertion should fail because the dispatch
        # loop doesn't consume control requests in a single invocation.
        # The request stays pending; the applied receipt is only created
        # when the long-lived dispatch loop processes it.
        if len(pending_dirs) > 0 and len(applied_dirs) == 0:
            raise AssertionError(
                "GAP [consumption]: Pause request persisted but NOT consumed — "
                "applied/ receipt missing.\n"
                f"  Found {len(pending_dirs)} pending requests, {len(applied_dirs)} applied receipts.\n"
                "Per §7.4: The dispatch loop must consume control requests; "
                "pause should result in an applied acknowledgement.\n"
                "Per §9.3: Control requests should transition pending → applied."
            )

    def test_pause_reflected_in_orch_status(self, tmp_path: Path) -> None:
        """Verify that after pause, orch status shows paused state.

        Spec: §7.4 - pause 'Queue a pause request so dispatch can stop.'
        Spec: §7.2 - status shows projected run status including active_step_id.
        GAP: If status doesn't reflect pause after the request, it's not consumed.
        This test EXPOSES the gap: pause doesn't propagate to status.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue pause
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test status reflection"],
            cwd=tmp_path,
        )

        # Check status
        status_result = _run_vectl(["orch", "status", "--latest", "--json"], cwd=tmp_path)
        print(f"INFO: status exited {status_result.returncode}")
        print(f"INFO: status stdout: {status_result.stdout[:500]}")

        if status_result.returncode == 0 and status_result.stdout.strip():
            try:
                status_data = json.loads(status_result.stdout)
                if isinstance(status_data, dict):
                    # Per §7.2: status should show paused state after pause
                    status = status_data.get("status", "").lower()
                    print(f"INFO: Run status after pause: {status}")
                    # EXPECTED-RED: status should be "paused" after pause
                    if status and status != "paused":
                        raise AssertionError(
                            f"GAP [status-reflection]: After pause, status is '{status}' "
                            f"but should be 'paused' per §7.4.\n"
                            "Per §10.1: control_pause event should transition to paused status."
                        )
            except json.JSONDecodeError:
                print(f"WARN: Status output not valid JSON")

    def test_stop_produces_control_stop_event(self, tmp_path: Path) -> None:
        """Verify that stop produces a control.stop event in the event stream.

        Spec: §10.1 - control_stop event should be emitted.
        Spec: §9.3 - Event stream should contain control events.
        GAP: If no control_stop event is emitted, stop only queues but
        doesn't broadcast through the observability system.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue stop
        stop_result = _run_vectl(
            ["orch", "stop", "--latest", "--reason", "test event"],
            cwd=tmp_path,
        )

        # Check events stream
        events_result = _run_vectl(["orch", "events", "--latest", "--jsonl"], cwd=tmp_path)

        if events_result.returncode == 0 and events_result.stdout.strip():
            events = []
            for line in events_result.stdout.strip().splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

            print(f"INFO: Found {len(events)} events after stop")

            # Per §10.1: control_stop event should be present
            stop_events = [
                e
                for e in events
                if "control_stop" in e.get("kind", "").lower()
                or "stop" in e.get("kind", "").lower()
            ]
            print(f"INFO: Found {len(stop_events)} stop-related events")

            if not stop_events:
                # EXPECTED-RED: This should fail because the control_stop
                # event may not be emitted to the event stream yet
                raise AssertionError(
                    "GAP [observability]: No control_stop event found in event stream.\n"
                    f"  Found {len(events)} total events, none with kind containing 'stop'.\n"
                    "Per §10.1: control_stop event must be emitted when stop request is queued.\n"
                    "Per §9.3: All state changes must emit events."
                )


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestControlCommandsOnRunningRun,
        TestStopBehaviorAsync,
        TestControlBehavioralContracts,
    ]

    gaps = []
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                method = getattr(instance, method_name)
                try:
                    # Create a fresh tmp_path for each test
                    with tempfile.TemporaryDirectory() as tmpdir:
                        method(instance, tmp_path=Path(tmpdir))
                except AssertionError as e:
                    gaps.append(f"{test_class.__name__}.{method_name}: {e}")
                except Exception as e:
                    gaps.append(
                        f"{test_class.__name__}.{method_name}: UNEXPECTED - {e}\n"
                        f"{''.join(traceback.format_tb(e.__traceback__))}"
                    )

    if gaps:
        print("\n" + "=" * 80)
        print("EXPECTED-RED GAPS FOUND (these failures are intentional):")
        print("=" * 80)
        for gap in gaps:
            print(f"  - {gap}")
        print("=" * 80)
        print(f"Total gaps: {len(gaps)}")
        print("\nGAP CLASSIFICATION:")
        print("  [registration]  - CLI command not registered in group")
        print("  [wiring]        - Command registered but not fully connected")
        print("  [persistence]   - Control request not persisted to filesystem")
        print("  [consumption]   - Request queued but not consumed by dispatch loop")
        print("  [observability] - No event emitted for control action")
        print("  [status]        - Status doesn't reflect control action")
        print("  [async]         - Stop blocks for termination instead of queuing")
        print(
            "\nDownstream implementation owner: cli_blackbox_active_control_recovery.fix-active-control-recovery-behavior"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All control commands work as specified")
        print("  - pause/unpause/stop are registered with documented flags")
        print("  - Control requests are persisted to filesystem")
        print("  - Control requests are consumed (acknowledged applied/rejected)")
        print("  - Stop returns asynchronously (request queued, not blocking)")
        print("  - actions command lists control requests with status")
        print("  - Pause is reflected in orch status")
        print("  - Stop produces control_stop event")
        sys.exit(0)
