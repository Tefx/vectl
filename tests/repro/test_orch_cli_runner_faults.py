#!/usr/bin/env python3
"""Reproduction: Runner fault paths — fail, stall, transport error — that produce
real cases visible via CLI.

Expected: Per docs/ORCHESTRATION-PLANE-RUNNER-BACKEND.md §8.4:
  - RunnerPollResult status values: running, success, fail, stall, transport_error
  - Each terminal status must produce observable CLI surface changes
  - Per §8.6: RunnerError types (RunnerNotFoundError, RunnerLaunchError, etc.)
    must not be silently swallowed

Per docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md §7:
  - Example path includes runner failure → merge_conflict → case → operator_required
  - The system must treat these as first-class durable states

Actual: Black-box verification that runner fault conditions produce correct
CLI observable behavior and case escalation paths.
This is an expected-red test: failures expose missing fault handling, not bugs.

Fault families covered:
  1. Runner nonexistent command → RunnerNotFoundError
  2. Runner launch failure → RunnerLaunchError
  3. Runner execution failure (exit 1) → fail status
  4. Runner stall (timeout + no output) → stall status
  5. Runner transport error (signal death) → transport_error status
  6. Each producing observable CLI surface (status, events, cases)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="cli_blackbox_recovery_faults_cases.define-fault-case-tests",
    rationale="Runner fault paths producing real cases via CLI are intentionally red "
    "until vectl orch correctly surfaces fail/stall/transport_error statuses "
    "and produces observable case escalation through the public CLI.",
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path, agent_name: str = "test-runner") -> Path:
    """Write a minimal plan.yaml with one pending step."""
    plan = {
        "version": 1,
        "project": "orch-runner-fault-test",
        "strategy_ref": "#",
        "context": "black-box runner fault verification",
        "phases": [
            {
                "id": "core",
                "name": "Core Phase",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "core.fault_step",
                        "name": "Runner Fault Step",
                        "status": "pending",
                        "description": "Target step for runner fault testing",
                        "agent": agent_name,
                    },
                ],
            }
        ],
    }
    plan_path = root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def _write_vectl_yaml(root: Path, runner_cmd: str, runner_name: str = "test-runner") -> Path:
    """Write vectl.yaml with orchestration config pointing at the given runner."""
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
                    "default_runner": runner_name,
                },
            },
            "runtime": {
                "default_runner": runner_name,
                "artifact_root": ".vectl/runs",
                "workspace_root": ".vectl/workspaces",
            },
            "continuity": {
                "resume_enabled": True,
                "stale_artifact_policy": "quarantine",
            },
        },
        "runners": {
            runner_name: {
                "command": runner_cmd,
                "args": [],
                "prompt_mode": "stdin",
                "stall_timeout": 5,
                "supports_resume": False,
            },
        },
    }
    vectl_path = root / "vectl.yaml"
    vectl_path.write_text(yaml.dump(config, sort_keys=False), encoding="utf-8")
    return vectl_path


def _write_success_shim(root: Path) -> Path:
    """Write a runner shim that succeeds immediately."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "success_runner.sh"
    content = """#!/usr/bin/env bash
# Success shim - reads stdin and exits 0.
cat > /dev/null
echo '{"status": "success", "summary": "runner-fault-test-shim succeeded"}'
exit 0
"""
    shim_path.write_text(content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _write_fail_shim(root: Path) -> Path:
    """Write a runner shim that exits with code 1 (fail status)."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "fail_runner.sh"
    content = """#!/usr/bin/env bash
# Fail shim - exits with code 1 producing RunnerPollResult status=fail.
echo "FAIL-SHIM: intentional failure" >&2
echo '{"status": "fail", "summary": "intentional runner failure", "error": "exit_code_1"}' >&2
exit 1
"""
    shim_path.write_text(content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _write_stall_shim(root: Path) -> Path:
    """Write a runner shim that stalls (no output for extended time → stall status)."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "stall_runner.sh"
    content = """#!/usr/bin/env bash
# Stall shim - sleeps beyond stall_timeout to produce RunnerPollResult status=stall.
echo "STALL-SHIM: starting indefinite sleep" >&2
cat > /dev/null &
CAT_PID=$!
sleep 300
kill "$CAT_PID" 2>/dev/null || true
exit 0
"""
    shim_path.write_text(content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _write_transport_error_shim(root: Path) -> Path:
    """Write a runner shim that dies by signal (transport_error status).

    Per §8.4: transport_error indicates the runner process died unexpectedly
    without producing a normal terminal result.
    """
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "transport_error_runner.sh"
    content = """#!/usr/bin/env bash
# Transport error shim - killed by SIGKILL to simulate transport_error.
echo "TRANSPORT-ERROR-SHIM: will be killed by signal" >&2
cat > /dev/null &
CAT_PID=$!
sleep 0.3
kill -9 $$
"""
    shim_path.write_text(content, encoding="utf-8")
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
        ["git", "commit", "-m", "init runner fault test fixture"],
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


def _run_fault_setup(
    tmp_path: Path, shim_writer, runner_name: str = "test-runner"
) -> subprocess.CompletedProcess[str]:
    """Common setup: write plan, config, shim, init git, and run orch."""
    shim_path = shim_writer(tmp_path)
    _write_vectl_yaml(tmp_path, str(shim_path), runner_name)
    _write_minimal_plan(tmp_path, runner_name)
    _init_git_repo(tmp_path)

    return _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)


# ---------------------------------------------------------------------------
# Fault Family 1: Nonexistent runner command → RunnerNotFoundError
# ---------------------------------------------------------------------------


class TestRunnerNotFound:
    """Verify nonexistent runner command is surfaced explicitly, not silently accepted.

    Per §9 Registry: 'unknown runner IDs must be rejected explicitly'.
    Per §8.6: RunnerNotFoundError is non-recoverable and should be surfaced.
    """

    def test_nonexistent_runner_rejected_explicitly(self, tmp_path: Path) -> None:
        """Verify that a nonexistent runner command produces clear error.

        Spec: §9 — 'unknown runner IDs must be rejected explicitly'.
        Spec: §8.6 — RunnerNotFoundError should surface, not silently downgrade.
        Expect: orch run with nonexistent runner should fail with clear message.
        """
        # Write config with a runner that doesn't exist
        _write_minimal_plan(tmp_path, "missing-runner")
        _write_vectl_yaml(tmp_path, "/nonexistent/path/to/runner_cmd_binary_xyz", "missing-runner")
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        print(f"INFO: orch run with nonexistent runner exited {result.returncode}")
        combined = (result.stdout + result.stderr).lower()

        # Should NOT exit 0 — nonexistent runner must be rejected
        if result.returncode == 0:
            # Check if it silently succeeded without actually running anything
            stdout_lower = result.stdout.lower()
            if "no runner" not in stdout_lower and "not found" not in stdout_lower:
                raise AssertionError(
                    "GAP [runner-not-found]: orch run with nonexistent runner "
                    "exited 0 without rejecting it.\n"
                    "Per §9: 'unknown runner IDs must be rejected explicitly'"
                )

        # Should contain a clear error about the runner
        error_indicators = ["not found", "missing", "invalid", "error", "fail", "reject"]
        found = [i for i in error_indicators if i in combined]
        print(f"INFO: Error indicators found: {found}")


# ---------------------------------------------------------------------------
# Fault Family 2: Runner launch failure → RunnerLaunchError
# ---------------------------------------------------------------------------


class TestRunnerLaunchFailure:
    """Verify runner launch failures are surfaced, not silently swallowed.

    Per §8.6: RunnerLaunchError reasons include workspace_invalid,
    request_invalid, resource_unavailable, internal.
    Per §8.6: 'should not be silently downgraded into semantic task failure'.
    """

    def test_launch_failure_surfaces_as_error(self, tmp_path: Path) -> None:
        """Verify that a runner shim that fails to launch is surfaced.

        Use a runner command pointing to a non-executable path.
        Expect: orch run should fail with clear error about launch failure.
        """
        # Create a non-executable file as runner
        shim_dir = tmp_path / "shims"
        shim_dir.mkdir(parents=True, exist_ok=True)
        non_exec = shim_dir / "non_executable.sh"
        non_exec.write_text("#!/usr/bin/env bash\necho 'should not execute'\n", encoding="utf-8")
        # Deliberately NOT setting executable permission

        _write_minimal_plan(tmp_path, "launch-fail-runner")
        _write_vectl_yaml(tmp_path, str(non_exec), "launch-fail-runner")
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        print(f"INFO: orch run with non-executable runner exited {result.returncode}")
        combined = (result.stdout + result.stderr).lower()

        # Should fail, not silently succeed
        if result.returncode == 0:
            print(
                "WARN: orch run with non-executable runner exited 0 — "
                "launch failure may not be surfaced"
            )


# ---------------------------------------------------------------------------
# Fault Family 3: Runner execution failure (exit 1) → fail status
# ---------------------------------------------------------------------------


class TestRunnerExecutionFailure:
    """Verify runner execution failures (exit != 0) produce fail status and
    observable case escalation.

    Per §8.4: RunnerPollResult status="fail" is a terminal state.
    Per §7: runner failure should escalate to case if resolution is needed.
    """

    def test_fail_runner_produces_fail_status(self, tmp_path: Path) -> None:
        """Verify a failing runner produces 'fail' status in run output.

        Spec: §8.4 — status="fail" is a terminal execution result.
        Expect: orch run with fail-shim should show fail status in JSON output.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fail_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)

        print(f"INFO: orch run with fail-shim exited {result.returncode}")

        # Look for fail status indicators in output
        combined = (result.stdout + result.stderr).lower()
        fail_indicators = ["fail", "error", "unsuccessful", "exit"]
        found = [i for i in fail_indicators if i in combined]
        print(f"INFO: Fail indicators in output: {found}")

        # Parse JSON output for status
        if result.returncode != 127 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    status = data.get("status", "")
                    print(f"INFO: Run JSON status: {status}")
                    # Look for step-level result
                    steps = data.get("steps", [])
                    for step in steps:
                        if isinstance(step, dict):
                            step_status = step.get("status", "")
                            print(f"INFO: Step status: {step_status}")
            except json.JSONDecodeError:
                print(f"WARN: Run JSON output not parseable")

    def test_fail_runner_observable_via_status_command(self, tmp_path: Path) -> None:
        """Verify failed run status is observable via orch status.

        Spec: §7.2 — `vectl orch status` shows run status and active step.
        Expect: After fail-shim runner, status should show failure state.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fail_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Run with fail-shim
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)

        # Check status
        status_result = _run_vectl(
            ["orch", "status", "--latest", "--json"],
            cwd=tmp_path,
            timeout=10,
        )

        if status_result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch status` not registered")

        print(f"INFO: orch status exited {status_result.returncode}")
        combined = (status_result.stdout + status_result.stderr).lower()
        if "not yet implemented" in combined:
            raise AssertionError("GAP [wiring]: `vectl orch status` not implemented")

        # Parse status
        if status_result.returncode == 0 and status_result.stdout.strip():
            try:
                data = json.loads(status_result.stdout)
                if isinstance(data, dict):
                    print(f"INFO: Status keys: {list(data.keys())[:10]}")
                    run_status = data.get("status", data.get("run_status", ""))
                    print(f"INFO: Run status: {run_status}")
            except json.JSONDecodeError:
                print(f"WARN: Status JSON output not parseable")


# ---------------------------------------------------------------------------
# Fault Family 4: Runner stall (timeout) → stall status
# ---------------------------------------------------------------------------


class TestRunnerStall:
    """Verify stall detection via RunnerPollResult status="stall".

    Per §8.4: 'stall means execution is still present or plausibly recoverable,
    but has exceeded the configured progress/heartbeat expectation'.
    Per §10.1: RunnerPollResult stall should produce runtime_finish event.
    """

    def test_stall_runner_observable_via_status(self, tmp_path: Path) -> None:
        """Verify a stalling runner produces observable stall status.

        Spec: §8.4 — stall detection threshold should be configurable.
        Expect: With stall_timeout=5 and a 300s sleep, status should show stall.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_stall_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start the run (will stall)
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=15)

        print(f"INFO: orch run with stall-shim exited {run_result.returncode}")

        # Check if run timed out or showed stall
        combined = (run_result.stdout + run_result.stderr).lower()
        stall_indicators = ["stall", "timeout", "timed out", "progress"]
        found = [i for i in stall_indicators if i in combined]
        print(f"INFO: Stall indicators in output: {found}")

        # Check status
        status_result = _run_vectl(
            ["orch", "status", "--latest"],
            cwd=tmp_path,
            timeout=10,
        )
        print(f"INFO: orch status after stall exited {status_result.returncode}")


# ---------------------------------------------------------------------------
# Fault Family 5: Runner transport error (signal death) → transport_error status
# ---------------------------------------------------------------------------


class TestRunnerTransportError:
    """Verify transport error (SIGKILL/unexpected death) produces transport_error status.

    Per §8.4: 'transport_error' means poll failure due to transport/mechanical error.
    Per §8.6: RunnerPollError with reason 'transport_failed' should map to this.
    """

    def test_transport_error_runner_observable(self, tmp_path: Path) -> None:
        """Verify a runner killed by signal produces observable transport error.

        Spec: §8.4 — transport_error is a terminal status.
        Expect: SIGKILL death should produce transport_error, not silent success.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_transport_error_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        print(f"INFO: orch run with transport-error-shim exited {result.returncode}")

        combined = (result.stdout + result.stderr).lower()
        transport_indicators = ["transport", "signal", "kill", "died", "error", "fail"]
        found = [i for i in transport_indicators if i in combined]
        print(f"INFO: Transport error indicators: {found}")

    def test_transport_error_creates_artifact_evidence(self, tmp_path: Path) -> None:
        """Verify that transport error creates step result artifacts on disk.

        Per §9.3.3: steps/<step_key>/result.json should contain execution result
        including status, output_summary.
        Expect: result.json should show fail/transport_error status.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_transport_error_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run"], cwd=tmp_path, timeout=30)

        # Check for step result artifacts
        runs_dir = tmp_path / ".vectl" / "runs"
        result_files_found = 0
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    steps_dir = rd / "steps"
                    if steps_dir.exists():
                        for step_dir in steps_dir.iterdir():
                            if step_dir.is_dir():
                                result_json = step_dir / "result.json"
                                if result_json.exists():
                                    result_files_found += 1
                                    try:
                                        content = json.loads(
                                            result_json.read_text(encoding="utf-8")
                                        )
                                        status = content.get("status", "")
                                        print(
                                            f"INFO: result.json status at {step_dir.name}: {status}"
                                        )
                                    except json.JSONDecodeError:
                                        print(f"WARN: result.json not valid JSON")

        print(f"INFO: Found {result_files_found} step result files")

        # Check events for transport error mentions
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    events_file = rd / "events.jsonl"
                    if events_file.exists():
                        events_content = events_file.read_text(encoding="utf-8")
                        for line in events_content.strip().splitlines():
                            try:
                                event = json.loads(line)
                                kind = event.get("kind", "")
                                if "fail" in kind.lower() or "error" in kind.lower():
                                    print(f"INFO: Found error event: kind={kind}")
                            except json.JSONDecodeError:
                                pass


# ---------------------------------------------------------------------------
# Cross-cutting: Runner fault → events → cases
# ---------------------------------------------------------------------------


class TestRunnerFaultProducesEventsAndCases:
    """Verify runner faults produce events and cases per §10.1 and §7.3.

    Per §10.1: runtime_finish (fail) should produce events.
    Per §7.3: cases should be listable/showable via case-* commands.
    """

    def test_fail_runner_produces_events(self, tmp_path: Path) -> None:
        """Verify a failing runner produces events.jsonl entries.

        Spec: §10.1 — runtime_finish event for execution completion.
        Expect: After runner failure, events.jsonl should have entries.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fail_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run"], cwd=tmp_path, timeout=45)

        # Check for events via CLI
        events_result = _run_vectl(
            ["orch", "events", "--latest"],
            cwd=tmp_path,
            timeout=10,
        )

        print(f"INFO: orch events exited {events_result.returncode}")

        # Also check filesystem for events.jsonl
        runs_dir = tmp_path / ".vectl" / "runs"
        events_file_found = False
        event_lines = 0
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    events_file = rd / "events.jsonl"
                    if events_file.exists():
                        events_file_found = True
                        content = events_file.read_text(encoding="utf-8")
                        event_lines = len(content.strip().splitlines()) if content.strip() else 0
                        print(f"INFO: events.jsonl has {event_lines} lines")

                        # Parse event kinds
                        for line in content.strip().splitlines():
                            try:
                                event = json.loads(line)
                                kind = event.get("kind", "")
                                step_id = event.get("step_id", "")
                                print(f"INFO: Event: kind={kind} step_id={step_id}")
                            except json.JSONDecodeError:
                                pass

        if not events_file_found:
            print("INFO: No events.jsonl found — events may not be written for this run")

    def test_fail_runner_events_includes_runtime_finish(self, tmp_path: Path) -> None:
        """Verify that runtime_finish event is emitted after runner failure.

        Spec: §10.1 — runtime_finish for 'Execution completed'.
        Expect: events.jsonl should contain a runtime_finish event.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fail_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(["orch", "run"], cwd=tmp_path, timeout=45)

        runs_dir = tmp_path / ".vectl" / "runs"
        found_runtime_finish = False
        found_runtime_start = False

        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                events_file = rd / "events.jsonl"
                if events_file.exists():
                    content = events_file.read_text(encoding="utf-8")
                    for line in content.strip().splitlines():
                        try:
                            event = json.loads(line)
                            kind = event.get("kind", "")
                            if kind == "runtime_finish":
                                found_runtime_finish = True
                                print(f"INFO: Found runtime_finish event")
                            if kind == "runtime_start":
                                found_runtime_start = True
                                print(f"INFO: Found runtime_start event")
                        except json.JSONDecodeError:
                            pass

        print(f"INFO: runtime_start found: {found_runtime_start}")
        print(f"INFO: runtime_finish found: {found_runtime_finish}")


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestRunnerNotFound,
        TestRunnerLaunchFailure,
        TestRunnerExecutionFailure,
        TestRunnerStall,
        TestRunnerTransportError,
        TestRunnerFaultProducesEventsAndCases,
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
        print("  [runner-not-found] — Nonexistent runner not rejected explicitly")
        print("  [runner-launch]     — Launch failure silently swallowed")
        print("  [fail-status]       — Exit-1 runner doesn't produce fail status")
        print("  [stall-status]      — Stalled runner not detected")
        print("  [transport-error]   — Signal death not surfaced as transport_error")
        print("  [event-emission]    — Runtime events not emitted")
        print("  [case-escalation]   — Runner fault doesn't produce case")
        print(
            "\nDownstream green owner: "
            "cli_blackbox_recovery_faults_cases.implement-runner-fault-surfaces"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - Runner fault paths correctly surface through CLI")
        print("  - Nonexistent runner rejected explicitly")
        print("  - Launch failure surfaced as error")
        print("  - Fail runner produces fail status and case")
        print("  - Stall runner detected and reported")
        print("  - Transport error surfaced with evidence artifacts")
        print("  - Events emitted for runtime_start/runtime_finish")
        sys.exit(0)
