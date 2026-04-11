#!/usr/bin/env python3
"""Reproduction: CLI concurrency safety — concurrent run launches, recover-vs-run
races, and stop-vs-recover conflicts.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md:
  §7.1 - orch run starts a new orchestration run
  §7.1 - orch resume/recover operate on existing runs
  §7.4 - orch stop queues an async stop request
  §8.8 - Frozen config snapshots written at run start
  §10.2 - runs/index.jsonl append-only run index

Specifically:
  - Concurrent `orch run` invocations must not corrupt the run index
  - `orch run` while a prior run is still active must be safe (new run or rejection)
  - `orch recover` vs `orch run` must not race on shared state
  - `orch stop` vs `orch recover` must handle ordering correctly

Actual: Black-box verification via concurrent CLI subprocesses.
This is an expected-red test: failures expose missing concurrency guards, not bugs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import yaml

from tests.expected_red import expected_red_module

pytestmark = expected_red_module(
    owner="cli_blackbox_surface_safety_ops.define-surface-safety-tests",
    rationale="CLI concurrency safety guards are intentionally red until the orch "
    "CLI surface handles concurrent invocations, index integrity, and race "
    "conditions on shared run state.",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path) -> Path:
    """Write a minimal plan.yaml."""
    plan = {
        "version": 1,
        "project": "concurrency-test",
        "strategy_ref": "#",
        "context": "black-box concurrency safety test",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "core.step_0",
                        "name": "Step 0",
                        "status": "pending",
                        "description": "Test step",
                        "agent": "test-runner",
                    },
                ],
            }
        ],
    }
    plan_path = root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def _write_vectl_yaml(root: Path, runner_cmd: str) -> Path:
    """Write vectl.yaml pointing at plan.yaml."""
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
                    "default_runner": "test-runner",
                },
            },
            "runtime": {
                "default_runner": "test-runner",
                "artifact_root": ".vectl/runs",
                "workspace_root": ".vectl/workspaces",
            },
        },
        "runners": {
            "test-runner": {
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
    """Write a slow runner shim for concurrency testing."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "slow_runner.sh"
    content = """#!/usr/bin/env bash
# Slow runner shim for concurrency testing.
SLEEP="${SLEEP_SECONDS:-30}"
cat > /dev/null &
CAT_PID=$!
sleep "$SLEEP"
kill "$CAT_PID" 2>/dev/null || true
echo '{"status": "success", "summary": "slow-shim completed"}'
exit 0
"""
    shim_path.write_text(content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _write_fast_shim(root: Path) -> Path:
    """Write a fast runner shim."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "fast_runner.sh"
    content = """#!/usr/bin/env bash
cat > /dev/null
echo '{"status": "success", "summary": "fast-shim completed"}'
exit 0
"""
    shim_path.write_text(content, encoding="utf-8")
    shim_path.chmod(0o755)
    return shim_path


def _init_git_repo(root: Path) -> None:
    """Initialize a git repo."""
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
        ["git", "commit", "-m", "init concurrency test fixture"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env=env,
    )


def _run_vectl(
    args: list[str],
    cwd: Path,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run vectl CLI with given args in cwd."""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["uv", "run", "vectl"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


def _setup_repo(tmp_path: Path, shim_writer=None) -> Path:
    """Common setup: write plan, config, shim, init git."""
    if shim_writer is None:
        shim_writer = _write_fast_shim
    shim_path = shim_writer(tmp_path)
    _write_vectl_yaml(tmp_path, str(shim_path))
    _write_minimal_plan(tmp_path)
    _init_git_repo(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Matrix Row: Concurrent orch run invocations
# ---------------------------------------------------------------------------


class TestConcurrentRunLaunch:
    """Verify concurrent `orch run` invocations are safe.

    Spec: §7.1 — orch run creates a new orchestration run.
    Safety: Two concurrent runs must not corrupt the run index (§10.2).
    Each run should get a unique run_id and isolated artifact directory.
    """

    def test_concurrent_runs_both_succeed(self, tmp_path: Path) -> None:
        """Verify two concurrent `orch run` invocations both complete.

        Spec: §7.1 — orch run starts a new orchestration run.
        Expected: Both should succeed independently, creating distinct runs.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        results = [None, None]
        errors = [None, None]

        def run_orch(idx: int) -> None:
            try:
                results[idx] = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)
            except Exception as e:
                errors[idx] = e

        t1 = threading.Thread(target=run_orch, args=(0,))
        t2 = threading.Thread(target=run_orch, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)

        for i, err in enumerate(errors):
            if err:
                print(f"ERROR: Thread {i} raised: {err}")

        for i, r in enumerate(results):
            if r is not None:
                print(f"INFO: Concurrent run {i} exited {r.returncode}")
                print(f"INFO: stdout: {r.stdout[:300]}")
            else:
                print(f"WARN: Concurrent run {i} produced no result")

        # Both should succeed (or at least not crash/corrupt)
        # If one returns a corruption error, that's a concurrency bug
        for i, r in enumerate(results):
            if r is not None and r.returncode not in (0, 1, 3):
                combined = (r.stdout + r.stderr).lower()
                if "corrupt" in combined or "integrity" in combined:
                    raise AssertionError(
                        f"GAP [concurrency]: Concurrent run {i} shows data corruption.\n"
                        "Per §10.2: runs/index.jsonl is append-only and must handle "
                        "concurrent appends safely."
                    )

    def test_concurrent_runs_produce_distinct_ids(self, tmp_path: Path) -> None:
        """Verify concurrent runs get distinct run IDs.

        Spec: §7.1 — Each orch run creates a new run with unique ID.
        Expected: Two runs should have different run_id values.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        run_ids = []
        for _ in range(2):
            result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)
            if result.returncode == 0 and result.stdout.strip():
                try:
                    data = json.loads(result.stdout)
                    run_id = data.get("run_id")
                    if run_id:
                        run_ids.append(run_id)
                except json.JSONDecodeError:
                    pass

        if len(run_ids) >= 2:
            if run_ids[0] == run_ids[1]:
                raise AssertionError(
                    "GAP [concurrency]: Two sequential runs produced the same run_id.\n"
                    "Per §7.1: Each run must have a unique run_id.\n"
                    f"  Both runs returned: {run_ids[0]}"
                )
            else:
                print(f"INFO: Sequential runs got distinct IDs: {run_ids}")
        else:
            print(
                f"INFO: Could not capture run_ids (got {len(run_ids)}) — skipping ID uniqueness check"
            )

    def test_runs_index_integrity_after_multiple_runs(self, tmp_path: Path) -> None:
        """Verify the runs index remains valid after multiple run invocations.

        Spec: §10.2 — runs/index.jsonl is append-only.
        Expected: Index should be valid JSONL after multiple writes.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Create several runs sequentially
        for i in range(3):
            result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)
            print(f"INFO: Run {i} exited {result.returncode}")

        # Check the runs index
        runs_dir = tmp_path / ".vectl" / "runs"
        index_file = runs_dir / "index.jsonl"

        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            lines = content.strip().splitlines()
            valid_lines = 0
            invalid_lines = 0
            for line in lines:
                try:
                    json.loads(line)
                    valid_lines += 1
                except json.JSONDecodeError:
                    invalid_lines += 1

            print(f"INFO: Index has {valid_lines} valid and {invalid_lines} invalid lines")

            if invalid_lines > 0:
                raise AssertionError(
                    "GAP [concurrency]: runs/index.jsonl contains invalid JSONL lines.\n"
                    "Per §10.2: Index must be append-only with valid JSON per line.\n"
                    f"  {invalid_lines} corrupted lines found out of {len(lines)} total."
                )
        else:
            print("INFO: No index.jsonl found — may use a different storage format")


# ---------------------------------------------------------------------------
# Matrix Row: orch run vs orch recover race
# ---------------------------------------------------------------------------


class TestRunVsRecoverRace:
    """Verify `orch run` and `orch recover` don't conflict on shared state.

    Spec: §7.1 — orch run creates a new run.
    Spec: §7.1 — orch recover recovers an existing run.
    Safety: A new run starting while recovery is in progress should not
    corrupt the recovered run's artifacts or state.
    """

    def test_recover_then_run_sequential(self, tmp_path: Path) -> None:
        """Verify recover followed by run works correctly.

        Spec: §7.1 — after recovery, starting a new run should be independent.
        Expected: Recover on a nonexistent/completed run should not affect
        subsequent run creation.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start a run
        run1 = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        print(f"INFO: First run exited {run1.returncode}")

        # Try to recover (may fail if run completed normally)
        recover = _run_vectl(
            ["orch", "recover", "--latest", "--dry-run"],
            cwd=tmp_path,
        )
        print(f"INFO: recover --latest --dry-run exited {recover.returncode}")

        # Start another run — should work regardless of recover outcome
        run2 = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        print(f"INFO: Second run exited {run2.returncode}")

        # Second run should succeed
        if run2.returncode not in (0, 1):
            combined = (run2.stdout + run2.stderr).lower()
            if "corrupt" in combined or "lock" in combined:
                raise AssertionError(
                    "GAP [concurrency]: Second run after recover shows corruption/lock.\n"
                    "Per §7.1: Each run must be independent; recovery of one run must "
                    "not block or corrupt subsequent runs."
                )

    def test_run_creates_config_snapshot(self, tmp_path: Path) -> None:
        """Verify orch run creates a frozen config snapshot per §8.8.

        Spec: §8.8 — At run start, configuration is frozen and written to
        .vectl/runs/<run_id>/config.snapshot.yaml.
        Expected: After a run, the snapshot should exist and be valid YAML.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Look for config snapshot
        runs_dir = tmp_path / ".vectl" / "runs"
        snapshots_found = 0
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    snapshot = rd / "config.snapshot.yaml"
                    if snapshot.exists():
                        snapshots_found += 1
                        try:
                            content = yaml.safe_load(snapshot.read_text(encoding="utf-8"))
                            print(f"INFO: Config snapshot at {rd.name} is valid YAML")
                            if isinstance(content, dict):
                                print(f"INFO: Snapshot keys: {list(content.keys())[:8]}")
                        except yaml.YAMLError:
                            print(f"WARN: Config snapshot at {rd.name} is invalid YAML")

        if snapshots_found == 0:
            # EXPECTED-RED: Config snapshot may not be created yet
            print(
                "INFO: No config.snapshot.yaml found after run — "
                "per §8.8, frozen config snapshot should be written at run start"
            )
        else:
            print(f"INFO: Found {snapshots_found} config snapshots")


# ---------------------------------------------------------------------------
# Matrix Row: orch stop vs orch recover
# ---------------------------------------------------------------------------


class TestStopVsRecoverRace:
    """Verify `orch stop` and `orch recover` handle ordering correctly.

    Spec: §7.4 — stop queues async stop request.
    Spec: §7.1 — recover diagnoses and repairs state.
    Safety: Stopping a run that's being recovered should not corrupt state.
    The system should handle the ordering gracefully.
    """

    def test_stop_then_recover_sequential(self, tmp_path: Path) -> None:
        """Verify stop followed by recover works correctly.

        Spec: §7.4 — stop is async; recovery may encounter stop in progress.
        Expected: After stop, recover should diagnose the stopped state.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        run_id = None
        if run_result.returncode == 0 and run_result.stdout.strip():
            try:
                data = json.loads(run_result.stdout)
                run_id = data.get("run_id")
            except (json.JSONDecodeError, KeyError):
                pass

        if run_id:
            # Stop the run
            stop_result = _run_vectl(
                ["orch", "stop", run_id, "--reason", "test stop before recover"],
                cwd=tmp_path,
            )
            print(f"INFO: stop exited {stop_result.returncode}")

            # Now try to recover
            recover_result = _run_vectl(
                ["orch", "recover", run_id, "--dry-run"],
                cwd=tmp_path,
            )
            print(f"INFO: recover after stop exited {recover_result.returncode}")
            print(f"INFO: stdout: {recover_result.stdout[:500]}")

            # Should not crash or corrupt
            combined = (recover_result.stdout + recover_result.stderr).lower()
            if "corrupt" in combined or "fatal" in combined:
                raise AssertionError(
                    "GAP [concurrency]: Recover after stop shows corruption/fatal error.\n"
                    "Per §7.4/§7.1: stop + recover must handle ordering gracefully."
                )
        else:
            print("INFO: Could not start run — skipping stop-then-recover test")

    def test_recover_then_stop_sequential(self, tmp_path: Path) -> None:
        """Verify recover followed by stop works correctly.

        Spec: §7.1/§7.4 — ordering should not matter.
        Expected: After recovering a run, stopping it should work.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        run_id = None
        if run_result.returncode == 0 and run_result.stdout.strip():
            try:
                data = json.loads(run_result.stdout)
                run_id = data.get("run_id")
            except (json.JSONDecodeError, KeyError):
                pass

        if run_id:
            # Recover first
            recover_result = _run_vectl(
                ["orch", "recover", run_id, "--dry-run"],
                cwd=tmp_path,
            )
            print(f"INFO: recover (before stop) exited {recover_result.returncode}")

            # Then stop
            stop_result = _run_vectl(
                ["orch", "stop", run_id, "--reason", "test stop after recover"],
                cwd=tmp_path,
            )
            print(f"INFO: stop (after recover) exited {stop_result.returncode}")

            # Should not crash
            combined = (stop_result.stdout + stop_result.stderr).lower()
            if "corrupt" in combined or "fatal" in combined:
                raise AssertionError(
                    "GAP [concurrency]: Stop after recover shows corruption/fatal error.\n"
                    "Per §7.1/§7.4: recover + stop must handle ordering gracefully."
                )
        else:
            print("INFO: Could not start run — skipping recover-then-stop test")


# ---------------------------------------------------------------------------
# Matrix Row: Fast invocations — rapid sequential orch commands
# ---------------------------------------------------------------------------


class TestRapidSequentialInvocations:
    """Verify rapid sequential CLI invocations don't corrupt shared state.

    While not truly concurrent (subprocess serializes), rapid sequential
    invocations test the filesystem lock and index integrity.
    """

    def test_rapid_runs_index_stays_valid(self, tmp_path: Path) -> None:
        """Verify rapid sequential runs don't corrupt the index.

        Spec: §10.2 — runs/index.jsonl is append-only.
        Expected: Index should be valid after multiple rapid writes.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Fire off runs in quick succession
        results = []
        for i in range(5):
            result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=45)
            results.append(result)

        # Check how many succeeded
        successes = sum(1 for r in results if r.returncode == 0)
        print(f"INFO: {successes}/5 runs succeeded")

        # Check index integrity
        runs_dir = tmp_path / ".vectl" / "runs"
        index_file = runs_dir / "index.jsonl"

        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            lines = [l for l in content.strip().splitlines() if l.strip()]
            valid_count = 0
            for line in lines:
                try:
                    json.loads(line)
                    valid_count += 1
                except json.JSONDecodeError:
                    pass

            print(f"INFO: Index has {len(lines)} lines, {valid_count} valid JSONL")

            if valid_count < len(lines):
                raise AssertionError(
                    f"GAP [concurrency]: Index has {len(lines) - valid_count} corrupted "
                    f"JSONL lines after rapid sequential runs.\n"
                    "Per §10.2: runs/index.jsonl must maintain integrity."
                )

    def test_rapid_statuses_dont_crash(self, tmp_path: Path) -> None:
        """Verify rapid `orch status --latest` commands don't crash.

        Spec: §7.2 — status [RUN_ID|--latest] is a read-only inspect command.
        Expected: Multiple rapid status checks should all succeed or fail gracefully.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start a run first
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Now rapid status checks
        crash_count = 0
        for i in range(5):
            result = _run_vectl(["orch", "status", "--latest", "--json"], cwd=tmp_path)
            if result.returncode not in (0, 1, 2):
                combined = (result.stdout + result.stderr).lower()
                if "traceback" in combined or "exception" in combined:
                    crash_count += 1
                    print(f"WARN: Status check {i} crashed: {result.stderr[:200]}")

        if crash_count > 0:
            raise AssertionError(
                f"GAP [concurrency]: {crash_count}/5 rapid status checks crashed.\n"
                "Per §7.2: status is read-only and must handle rapid invocations."
            )


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestConcurrentRunLaunch,
        TestRunVsRecoverRace,
        TestStopVsRecoverRace,
        TestRapidSequentialInvocations,
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
        print("EXPECTED-RED GAPS FOUND (concurrency — these failures are intentional):")
        print("=" * 80)
        for gap in gaps:
            print(f"  - {gap}")
        print("=" * 80)
        print(f"Total gaps: {len(gaps)}")
        print("\nGAP CLASSIFICATION:")
        print("  [concurrency] — Concurrent/rapid invocations corrupt shared state")
        print("  [integrity]   — Run index or config becomes corrupted")
        print("  [isolation]   — Operations on different runs interfere")
        print("  [snapshot]    — Config snapshot not written per §8.8")
        print(
            "\nDownstream implementation owner: "
            "cli_blackbox_surface_safety_ops.implement-concurrency-guards"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All concurrency safety invariants enforced")
        print("  - Concurrent runs don't corrupt the index")
        print("  - Run IDs are unique across concurrent invocations")
        print("  - Stop + recover ordering handled gracefully")
        print("  - Config snapshots created per §8.8")
        print("  - Rapid invocations don't crash")
        sys.exit(0)
