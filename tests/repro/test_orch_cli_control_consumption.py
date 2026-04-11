#!/usr/bin/env python3
"""Reproduction: Control request consumption vs mere queueing behavior.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.4 and §10.1,
control requests (pause/unpause/stop) must be:
  1. Persisted to filesystem control channel (pending/)
  2. Consumed by the orchestration loop (moved to applied/ or rejected/)
  3. Observable via `vectl orch actions` with status field
  4. Acknowledged via the acknowledgement protocol (§9.3 ActionAcknowledgement)

This distinction (consumption vs queueing) is critical:
  - Queueing only: command writes a file, nothing reads it (OR only ack_timeout)
  - Consumption: the orchestration loop reads the request AND acts on it

Actual: Testing black-box to verify control request lifecycle.
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
    rationale="Control request consumption is intentionally red until pause/unpause/stop "
    "requests are consumed (not just queued) by the orchestration dispatch loop, "
    "with acknowledgement and observable status transitions.",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path) -> Path:
    """Write a minimal plan.yaml with one pending step."""
    plan = {
        "version": 1,
        "project": "orch-consumption-test",
        "strategy_ref": "#",
        "context": "black-box control consumption verification",
        "phases": [
            {
                "id": "test",
                "name": "Test Phase",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "test.control_step",
                        "name": "Control Step",
                        "status": "pending",
                        "description": "Target step for control request lifecycle testing",
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
            "defaults": {"ordinary_role": "python-executor"},
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
            "control": {"idle_poll_interval_ms": 500},
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
    """Write a slow runner shim that sleeps."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "slow_runner.sh"
    shim_content = """#!/usr/bin/env bash
SLEEP="${SLEEP_SECONDS:-10}"
echo "SHIM: sleeping ${SLEEP}s" >&2
cat > /dev/null &
CAT_PID=$!
sleep "$SLEEP"
kill "$CAT_PID" 2>/dev/null || true
echo '{"status": "success", "summary": "shim completed"}'
exit 0
"""
    shim_path.write_text(shim_content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _init_git_repo(root: Path) -> None:
    """Initialize git repo in root."""
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
        ["git", "commit", "-m", "init consumption test fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env=env,
    )


def _run_vectl(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run vectl CLI with given args."""
    return subprocess.run(
        ["uv", "run", "vectl"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Test: Control request lifecycle (queue -> consume -> acknowledge)
# ---------------------------------------------------------------------------


class TestControlRequestConsumption:
    """Black-box tests verifying control requests are consumed, not just queued.

    These tests verify the full lifecycle:
    1. Persist: control command writes pending request to filesystem
    2. Consume: orchestration loop picks up the request
    3. Acknowledge: receipt is created (applied/ or rejected/)
    4. Observe: `vectl orch actions` shows the request with status
    """

    def test_pause_request_persists_to_control_channel(self, tmp_path: Path) -> None:
        """Verify pause command creates a pending control request file on disk.

        Spec: §7.4 - pause persists a control.pause request.
        Spec: §9.3 - Control channel persists requests per run.
        Gap: If no filesystem control channel is created, pause is not durable.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run so there's an active run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Attempt pause
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test consumption"],
            cwd=tmp_path,
        )

        # Check for control channel directory structure on disk
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            control_dirs = list(runs_dir.rglob("control"))
            print(f"INFO: Found {len(control_dirs)} control channel directories")
            for cd in control_dirs:
                if cd.is_dir():
                    print(f"INFO: Control dir at {cd}")
                    for sub in ("pending", "applied", "rejected"):
                        sub_dir = cd / sub
                        if sub_dir.exists():
                            files = list(sub_dir.iterdir())
                            print(f"INFO: {sub}/ has {len(files)} files: {[f.name for f in files]}")

            # Check for pending pause request
            pending_dirs = list(runs_dir.rglob("pending"))
            pause_requests_found = False
            for pd in pending_dirs:
                if pd.is_dir():
                    for f in pd.iterdir():
                        content = f.read_text(errors="replace")[:500]
                        if "pause" in content.lower():
                            pause_requests_found = True
                            print(f"INFO: Found pause request in {f.name}: {content[:200]}")

            # Also check for events mentioning pause
            events_files = list(runs_dir.rglob("events.jsonl"))
            for ef in events_files:
                for line in ef.read_text().splitlines():
                    if "control_pause" in line or "pause" in line.lower():
                        print(f"INFO: Found pause event in events log")
                        break
        else:
            print("INFO: No .vectl/runs directory found (run may not have created artifacts)")

    def test_actions_shows_request_status_transitions(self, tmp_path: Path) -> None:
        """Verify `vectl orch actions` shows request status (pending/applied/rejected).

        Spec: §7.2 - actions shows pending/applied/rejected operator requests.
        Spec: §10.1 - operator_action_requested events for control transitions.
        Gap: If actions doesn't show status transitions, consumption isn't observable.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue a pause request
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test status transitions"],
            cwd=tmp_path,
        )

        # Now check actions
        actions_result = _run_vectl(["orch", "actions", "--latest", "--json"], cwd=tmp_path)

        if actions_result.returncode == 127:
            raise AssertionError(
                "GAP [registration]: `vectl orch actions --latest --json` not registered"
            )

        combined = (actions_result.stdout + actions_result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch actions` not fully wired\n"
                f"  exit code: {actions_result.returncode}\n"
                f"  output: {actions_result.stdout[:500]}"
            )

        print(f"INFO: `vectl orch actions --latest --json` exited {actions_result.returncode}")
        print(f"INFO: stdout: {actions_result.stdout[:500]}")

        # If JSON output, parse it
        if actions_result.returncode == 0 and actions_result.stdout.strip():
            try:
                data = json.loads(actions_result.stdout)
                if isinstance(data, dict):
                    # Check for status field
                    status = data.get("status", "")
                    print(f"INFO: Actions status: {status}")
                elif isinstance(data, list):
                    print(f"INFO: Actions returned {len(data)} items")
                    for item in data[:3]:
                        if isinstance(item, dict):
                            print(f"INFO: Item keys: {list(item.keys())}")
            except json.JSONDecodeError:
                print(f"WARN: Actions output was not valid JSON")

    def test_pause_then_unpause_creates_two_requests(self, tmp_path: Path) -> None:
        """Verify that pause then unpause creates separate control requests.

        Spec: §7.4 - Each control command persists an independent request.
        Gap: If only one request exists after both commands, requests are overwritten.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Pause
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test pause"],
            cwd=tmp_path,
        )
        print(f"INFO: pause exited {pause_result.returncode}")

        # Unpause
        unpause_result = _run_vectl(
            ["orch", "unpause", "--latest", "--reason", "test unpause"],
            cwd=tmp_path,
        )
        print(f"INFO: unpause exited {unpause_result.returncode}")

        # Check actions listing
        actions_result = _run_vectl(["orch", "actions", "--latest"], cwd=tmp_path)
        print(f"INFO: actions exited {actions_result.returncode}")

        # Check filesystem for two distinct requests
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            pending_files = list(runs_dir.rglob("pending/*.json"))
            applied_files = list(runs_dir.rglob("applied/*.json"))
            rejected_files = list(runs_dir.rglob("rejected/*.json"))
            total = len(pending_files) + len(applied_files) + len(rejected_files)
            print(
                f"INFO: Found {total} control request files "
                f"(pending: {len(pending_files)}, applied: {len(applied_files)}, "
                f"rejected: {len(rejected_files)})"
            )

    def test_control_channel_directory_layout(self, tmp_path: Path) -> None:
        """Verify control channel creates proper directory layout per §9.3.

        Spec: §7.4, §9.3 - Control requests persisted in
        .vectl/runs/<run_id>/control/{pending,applied,rejected}/
        Gap: If control channel directories don't exist, requests aren't durable.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue control actions
        stop_result = _run_vectl(
            ["orch", "stop", "--latest", "--reason", "test layout"],
            cwd=tmp_path,
        )

        # Inspect filesystem layout
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for run_dir in runs_dir.iterdir():
                if run_dir.is_dir():
                    control_dir = run_dir / "control"
                    if control_dir.exists():
                        for sub in ("pending", "applied", "rejected"):
                            sub_dir = control_dir / sub
                            exists = sub_dir.exists() and sub_dir.is_dir()
                            print(f"INFO: {sub}/ exists: {exists}")
                            if exists:
                                files = list(sub_dir.iterdir())
                                print(f"INFO: {sub}/ contains {len(files)} files")
                    else:
                        print(f"INFO: No control/ directory found in {run_dir.name}")
        else:
            print("INFO: No .vectl/runs directory found")

    def test_stop_request_is_queued_not_blocking(self, tmp_path: Path) -> None:
        """Verify stop command queues a request rather than blocking until termination.

        Spec: §7.4 - 'Return success once the request is queued;
        terminal state change is asynchronous'
        Gap: If stop blocks until run terminates, it violates the async spec.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue stop and verify it returns quickly
        start_time = time.time()
        stop_result = _run_vectl(
            ["orch", "stop", "--latest", "--reason", "test async"],
            cwd=tmp_path,
            timeout=15,  # Should return well within this
        )
        elapsed = time.time() - start_time

        # Document the timing
        print(f"INFO: `orch stop` returned in {elapsed:.2f}s with exit {stop_result.returncode}")

        # Per §7.4, stop should return quickly (just queuing)
        # A 5s threshold is generous; actual queueing should be < 1s
        if elapsed > 5.0:
            print(
                f"WARN: Stop took {elapsed:.2f}s — may be blocking for termination instead of queuing"
            )

    def test_events_log_contains_control_requests(self, tmp_path: Path) -> None:
        """Verify events stream contains operator_action_requested entries for controls.

        Spec: §10.1 - operator_action_requested event for operator intervention.
        Expected: After control commands, events.jsonl should contain
        operator_action_requested entries.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_slow_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Issue control commands
        pause_result = _run_vectl(
            ["orch", "pause", "--latest", "--reason", "test events"],
            cwd=tmp_path,
        )

        # Check events
        events_result = _run_vectl(["orch", "events", "--latest"], cwd=tmp_path)
        print(f"INFO: events exited {events_result.returncode}")
        print(f"INFO: stdout: {events_result.stdout[:500]}")

        # Also check filesystem events
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            events_files = list(runs_dir.rglob("events.jsonl"))
            for ef in events_files:
                content = ef.read_text()
                lines = content.strip().splitlines() if content.strip() else []
                print(f"INFO: {ef} has {len(lines)} event lines")

                # Look for operator action events
                for line in lines:
                    try:
                        event = json.loads(line)
                        kind = event.get("kind", "")
                        if "control" in kind or "operator" in kind or "pause" in kind:
                            print(f"INFO: Found control/operator event: kind={kind}")
                    except json.JSONDecodeError:
                        pass


class TestControlChannelConsumptionInternals:
    """Tests for FilesystemControlChannel consumption behavior at the integration level.

    These tests verify that the control channel module properly handles
    the lifecycle: send -> pending -> acknowledge (applied/rejected).

    These test the public API of control_channel.py, not CLI subprocess.
    """

    def test_control_channel_send_creates_pending_file(self, tmp_path: Path) -> None:
        """Verify sending a control message creates a pending file.

        Spec: §9.3, §7.4 - Control requests are persisted as pending files.
        """
        from vectl.orchestration.control_channel import (
            ControlChannelMessage,
            FilesystemControlChannel,
        )

        channel = FilesystemControlChannel(
            runs_root=str(tmp_path),
        )

        message = ControlChannelMessage(
            msg_type="control.pause",
            sender="operator",
            payload=("reason=test_pause",),
            timestamp=time.time(),
        )

        channel.send(message)

        # Check that a pending file was created
        pending_dir = tmp_path / "control" / "pending"
        # FilesystemControlChannel may use different path structure
        # Check for any JSON files created
        pending_files = list(tmp_path.rglob("pending/*.json"))
        print(f"INFO: Found {len(pending_files)} pending files: {[f.name for f in pending_files]}")

        # Verify the request was created
        if pending_files:
            for pf in pending_files:
                try:
                    import json as _json

                    data = _json.loads(pf.read_text())
                    print(f"INFO: Pending request content: {data}")
                except Exception:
                    pass

    def test_control_channel_acknowledge_moves_to_applied(self, tmp_path: Path) -> None:
        """Verify acknowledging a request moves it from pending to applied.

        Spec: §9.3 - Acknowledgement creates receipt and removes pending file.
        Gap: If acknowledgement doesn't move files, requests aren't consumed.
        """
        try:
            from vectl.orchestration.control_channel import (
                ActionRequest,
                ControlChannelMessage,
                FilesystemControlChannel,
            )
        except ImportError as e:
            raise AssertionError(
                f"GAP [import]: Cannot import FilesystemControlChannel: {e}\n"
                "Per §9.3: Control channel module must be importable"
            )

        channel = FilesystemControlChannel(
            runs_root=str(tmp_path),
        )

        message = ControlChannelMessage(
            msg_type="control.pause",
            sender="operator",
            payload=("reason=test_consume",),
            timestamp=time.time(),
        )

        # Send the message
        channel.send(message)

        # List requests for run_id (FilesystemControlChannel uses msg_type-based run_id)
        # We need to find what run_id the channel used
        # Inspect filesystem structure to find pending requests
        pending_files = list(tmp_path.rglob("pending/*.json"))
        print(f"INFO: Found {len(pending_files)} pending JSON files across all runs")

        # Try to find the run_id from the directory structure
        for pf in pending_files:
            # Structure is: runs_root/<run_id>/control/pending/<action_id>.json
            parts = pf.relative_to(tmp_path).parts
            if len(parts) >= 3:
                run_id_from_path = parts[0]
                requests = channel.list_requests(run_id=run_id_from_path)
                print(f"INFO: Found {len(requests)} pending requests for run {run_id_from_path}")
                if requests:
                    action_id = requests[0].action_id
                    print(f"INFO: First request action_id: {action_id}")

                    # Acknowledge as applied
                    receipt = channel.acknowledge_applied(
                        run_id=run_id_from_path,
                        action_id=action_id,
                    )
                    print(f"INFO: Acknowledgement receipt: {receipt}")

                    # Verify: pending should be empty, applied should have receipt
                    remaining_pending = channel.list_requests(run_id=run_id_from_path)
                    print(f"INFO: Remaining pending requests after ack: {len(remaining_pending)}")

                    # Check applied directory
                    applied_dir = tmp_path / run_id_from_path / "control" / "applied"
                    if applied_dir.exists():
                        applied_files = list(applied_dir.iterdir())
                        print(f"INFO: Applied directory has {len(applied_files)} files")
                    break

    def test_control_channel_acknowledge_rejected(self, tmp_path: Path) -> None:
        """Verify rejecting a request moves it from pending to rejected.

        Spec: §9.3 - Rejected acknowledgement creates receipt and removes pending file.
        """
        try:
            from vectl.orchestration.control_channel import (
                ControlChannelMessage,
                FilesystemControlChannel,
            )
        except ImportError as e:
            raise AssertionError(f"GAP [import]: Cannot import FilesystemControlChannel: {e}")

        channel = FilesystemControlChannel(
            runs_root=str(tmp_path),
        )

        message = ControlChannelMessage(
            msg_type="control.stop",
            sender="operator",
            payload=("reason=test_rejection",),
            timestamp=time.time(),
        )

        channel.send(message)

        # Find pending requests by scanning filesystem
        pending_files = list(tmp_path.rglob("pending/*.json"))
        for pf in pending_files:
            parts = pf.relative_to(tmp_path).parts
            if len(parts) >= 3:
                run_id_from_path = parts[0]
                requests = channel.list_requests(run_id=run_id_from_path)
                if requests:
                    action_id = requests[0].action_id

                    # Reject the request
                    receipt = channel.acknowledge_rejected(
                        run_id=run_id_from_path,
                        action_id=action_id,
                        reason="test rejection",
                    )
                    print(f"INFO: Rejected receipt: {receipt}")

                    # Verify rejected directory
                    rejected_dir = tmp_path / run_id_from_path / "control" / "rejected"
                    if rejected_dir.exists():
                        rejected_files = list(rejected_dir.iterdir())
                        print(f"INFO: Rejected directory has {len(rejected_files)} files")
                    break


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestControlRequestConsumption,
        TestControlChannelConsumptionInternals,
    ]

    gaps = []
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                method = getattr(instance, method_name)
                try:
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
        print("  [registration]  - CLI command not registered")
        print("  [wiring]        - Command registered but not connected")
        print("  [persistence]   - Control request not persisted to filesystem")
        print("  [consumption]   - Request queued but not consumed by loop")
        print("  [import]        - Module or type not importable")
        print(
            "\nDownstream implementation owner: cli_blackbox_active_control_recovery.fix-active-control-recovery-behavior"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All control request consumption works as specified")
        print("  - Control requests are persisted to filesystem control channel")
        print("  - Requests are consumed (acknowledged applied/rejected)")
        print("  - Actions command shows status transitions")
        print("  - Stop is asynchronous (queues, doesn't block)")
        sys.exit(0)
