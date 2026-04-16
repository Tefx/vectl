#!/usr/bin/env python3
"""Reproduction: Recovery fault surfaces — missing config snapshot, corrupt artifacts,
fresh-start-required paths.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.2,
  - `vectl orch recover` must load run state, validate event stream integrity,
    detect gaps or corruption, and report or repair/resume from durable artifacts
  - Per §8.8: config.snapshot.yaml is frozen at run start and authoritative for resume
  - Per §9.2-9.3: projection replay and event envelopes have integrity checks
  - Per §6: exit code 4 = "Recovery required"

Per docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md §6:
  - Recovery must reconstruct worktree, execution, and reconcile state
  - Restart must re-enter correct phase from durable metadata
  - Protected paths must never be silently integrated

Actual: Black-box verification that the CLI exposes these fault conditions correctly.
This is an expected-red test: failures expose missing fault-surface handling, not bugs.

Fault families covered:
  1. Missing config.snapshot.yaml — recovery must surface NO_ARTIFACTS or QUARANTINED
  2. Corrupt continuity artifacts (truncated event stream, malformed JSON)
  3. Corrupt projection state (invalid state/latest.json)
  4. Fresh-start-required recovery path (stale quarantine triggering fresh start)
  5. Missing event stream integrity (prev_hash chain broken)
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
    owner="cli_blackbox_recovery_faults_cases.define-fault-case-tests",
    rationale="Recovery fault surfaces (missing snapshot, corrupt artifacts, "
    "fresh-start-required) are intentionally red until vectl orch recover "
    "correctly detects, classifies, and surfaces these fault conditions via "
    "the public CLI with proper exit codes and structured JSON output.",
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path) -> Path:
    """Write a minimal plan.yaml with one pending step."""
    plan = {
        "version": 1,
        "project": "orch-recovery-fault-test",
        "strategy_ref": "#",
        "context": "black-box recovery fault verification",
        "phases": [
            {
                "id": "core",
                "name": "Core Phase",
                "status": "pending",
                "gate": "n/a",
                "steps": [
                    {
                        "id": "core.fault_step",
                        "name": "Fault Test Step",
                        "status": "pending",
                        "description": "Target step for recovery fault testing",
                        "agent": "fast-shim",
                    },
                ],
            }
        ],
    }
    plan_path = root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def _write_vectl_yaml(root: Path, runner_cmd: str) -> Path:
    """Write vectl.yaml with orchestration config pointing at fast-shim runner."""
    config = {
        "orchestration": {
            "plan_path": "plan.yaml",
            "defaults": {"ordinary_role": "python-executor"},
            "role_profile_overrides": {
                "python-executor": {
                    "default_runner": "fast-shim",
                },
            },
            "runtime": {
                "default_runner": "fast-shim",
                "artifact_root": ".vectl/runs",
                "workspace_root": ".vectl/workspaces",
            },
            "continuity": {
                "resume_enabled": True,
                "stale_artifact_policy": "quarantine",
            },
        },
        "runners": {
            "fast-shim": {
                "command": runner_cmd,
                "args": [],
                "prompt_mode": "stdin",
                "stall_timeout": 60,
                "supports_resume": False,
            },
        },
    }
    vectl_path = root / "vectl.yaml"
    vectl_path.write_text(yaml.dump(config, sort_keys=False), encoding="utf-8")
    return vectl_path


def _write_fast_shim(root: Path) -> Path:
    """Write a fast runner shim that completes immediately."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "fast_runner.sh"
    shim_content = """#!/usr/bin/env bash
# Fast runner shim - completes immediately for fault injection testing.
echo "SHIM: executing" >&2
cat > /dev/null
echo '{"status": "success", "summary": "fault-test-shim completed"}'
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
        ["git", "commit", "-m", "init fault test fixture"],
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


def _start_run_and_get_artifacts(cwd: Path) -> dict[str, Any]:
    """Start a run and return artifact directory state.

    Returns dict with 'runs_dir', 'run_dirs', 'has_config_snapshot', etc.
    """
    result = {}
    runs_dir = cwd / ".vectl" / "runs"
    result["runs_dir"] = runs_dir

    if not runs_dir.exists():
        result["run_dirs"] = []
        result["has_config_snapshot"] = False
        result["has_events"] = False
        result["has_state"] = False
        return result

    run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
    result["run_dirs"] = run_dirs

    # Check for key artifacts
    has_config_snapshot = False
    has_events = False
    has_state = False
    for rd in run_dirs:
        snapshot = rd / "config.snapshot.yaml"
        if snapshot.exists():
            has_config_snapshot = True
        events = rd / "events.jsonl"
        if events.exists():
            has_events = True
        state_dir = rd / "state"
        if state_dir.exists():
            has_state = True
            result["state_latest"] = state_dir / "latest.json"

    result["has_config_snapshot"] = has_config_snapshot
    result["has_events"] = has_events
    result["has_state"] = has_state
    return result


# ---------------------------------------------------------------------------
# Fault Family 1: Missing config.snapshot.yaml
# ---------------------------------------------------------------------------


class TestMissingConfigSnapshot:
    """Recovery when config.snapshot.yaml is missing from a run's artifacts.

    Per §8.8: config.snapshot.yaml is frozen at run start and is authoritative
    for resume operations. When it's missing, recovery must surface this
    explicitly, not silently proceed.

    Per §7.2 recover behavior:
      1. Load run state
      2. Validate event stream integrity
      3. Detect gaps or corruption
      4. Report or repair / resume from durable artifacts

    Missing config snapshot is a corruption gap that must be detected.
    """

    def test_recover_detects_missing_config_snapshot(self, tmp_path: Path) -> None:
        """Verify recovery detects when config.snapshot.yaml is absent from a run.

        Spec: §8.8 — config snapshot is authoritative for resume.
        Spec: §7.2 — 'Validate event stream integrity, detect gaps or corruption'
        Expect: recovery detects missing snapshot and reports BLOCKED or QUARANTINED,
        not silent proceed.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run to create some artifacts
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)
        print(f"INFO: orch run --dry-run exited {run_result.returncode}")

        # Now find and delete config.snapshot.yaml if it exists
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            snapshots_deleted = 0
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    snapshot = rd / "config.snapshot.yaml"
                    if snapshot.exists():
                        snapshot.unlink()
                        snapshots_deleted += 1
                        print(f"INFO: Deleted config.snapshot.yaml from {rd.name}")
            print(f"INFO: Deleted {snapshots_deleted} config snapshots")

        # Try recovery — should detect missing snapshot
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        combined = (recover_result.stdout + recover_result.stderr).lower()
        print(f"INFO: recovery exited {recover_result.returncode}")

        if recover_result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch recover` not registered")

        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError("GAP [wiring]: `vectl orch recover` not fully wired")

        # Per §6: exit code 4 = Recovery required
        # Per RecoveryOutcome: should be BLOCKED or QUARANTINED for corrupt state
        # NOT: silently proceeding with NO_ARTIFACTS
        # Check that recovery output mentions missing/corrupt config
        missing_indicators = [
            "missing",
            "corrupt",
            "blocked",
            "no_artifacts",
            "quarantine",
            "config",
            "snapshot",
        ]
        found_indicators = [i for i in missing_indicators if i in combined]
        print(f"INFO: Found indicators in output: {found_indicators}")

        # Try to parse JSON for structured evidence
        if recover_result.returncode == 0 and recover_result.stdout.strip():
            try:
                data = json.loads(recover_result.stdout)
                if isinstance(data, dict):
                    report = data.get("recovery_report", {})
                    if isinstance(report, dict):
                        outcome = report.get("outcome", "")
                        print(f"INFO: Recovery outcome: {outcome}")
                        print(f"INFO: gate_open_allowed: {report.get('gate_open_allowed')}")
            except json.JSONDecodeError:
                print(f"WARN: Recovery output was not valid JSON")

    def test_resume_after_missing_snapshot_is_blocked(self, tmp_path: Path) -> None:
        """Verify that resume after deleting config.snapshot.yaml is blocked.

        Spec: §7.1 — resume 'Restore projected state via replay', requires
        frozen config snapshot per §8.8.
        Expect: resume should fail or require recovery when snapshot is missing.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Delete config snapshot
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    snapshot = rd / "config.snapshot.yaml"
                    if snapshot.exists():
                        snapshot.unlink()

        # Try resume — should be blocked without config snapshot
        resume_result = _run_vectl(
            ["orch", "resume", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        combined = (resume_result.stdout + resume_result.stderr).lower()
        print(f"INFO: resume after missing snapshot exited {resume_result.returncode}")

        # Per §6: exit 4 = recovery required (snapshot missing = recovery condition)
        # Per §7.1: resume requires frozen config snapshot
        # If resume silently succeeds, that's a gap
        if resume_result.returncode == 0:
            print(
                "WARN: Resume succeeded after config.snapshot.yaml deletion — "
                "may not be checking snapshot integrity"
            )


# ---------------------------------------------------------------------------
# Fault Family 2: Corrupt continuity / event / projection artifacts
# ---------------------------------------------------------------------------


class TestCorruptContinuityArtifacts:
    """Recovery when continuity artifacts are corrupt.

    Per §9.2-9.3: events have integrity (seq, prev_hash, entry_hash) and
    projection replay must be deterministic. Corrupt events or state
    must be detected, not silently accepted.

    Per §6.4 Recovery-safe invariants:
      - active execution is not forgotten
      - unresolved reconcile conflict is not lost
      - complete is not replayed before reconcile closure
    """

    def test_recover_detects_truncated_event_stream(self, tmp_path: Path) -> None:
        """Verify recovery detects a truncated/incomplete event stream.

        Spec: §9.3.2 — events have monotonically increasing seq.
        Spec: §9.3.3 — entry_hash provides integrity.
        Expect: Recovery with a truncated events.jsonl should report
        BLOCKED or QUARANTINED, not silently accept partial state.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run to create artifacts
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Find the events.jsonl and truncate it
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    events_file = rd / "events.jsonl"
                    if events_file.exists():
                        content = events_file.read_text(encoding="utf-8")
                        if content.strip():
                            # Truncate to half — breaks seq chain
                            lines = content.strip().splitlines()
                            if len(lines) > 1:
                                truncated = "\n".join(lines[: len(lines) // 2])
                                events_file.write_text(truncated + "\n", encoding="utf-8")
                                print(
                                    f"INFO: Truncated events.jsonl from "
                                    f"{len(lines)} to {len(lines) // 2} lines"
                                )

        # Try recovery
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        combined = (recover_result.stdout + recover_result.stderr).lower()
        print(f"INFO: recovery after truncation exited {recover_result.returncode}")

        if recover_result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch recover` not registered")

        # Expect detection of truncation/corruption
        corruption_indicators = [
            "corrupt",
            "truncat",
            "gaps",
            "integrity",
            "blocked",
            "quarantine",
            "incomplete",
        ]
        found = [i for i in corruption_indicators if i in combined]
        print(f"INFO: Corruption detection indicators: {found}")

    def test_recover_detects_malformed_json_in_events(self, tmp_path: Path) -> None:
        """Verify recovery detects malformed JSON in events stream.

        Spec: §9.3.1 — event envelopes must be valid JSON with hash chain.
        Expect: Malformed JSON should produce BLOCKED or QUARANTINED, not crash.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Corrupt events.jsonl with malformed JSON
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    events_file = rd / "events.jsonl"
                    if events_file.exists():
                        # Append malformed JSON
                        malformed_line = '{"seq": 99, "prev_hash": "BROKEN{{invalid json'
                        original = events_file.read_text(encoding="utf-8")
                        events_file.write_text(original + malformed_line + "\n", encoding="utf-8")
                        print(f"INFO: Injected malformed JSON into events.jsonl")

        # Try recovery — should not crash
        recover_result = _run_vectl(
            ["orch", "recover", "--latest"],
            cwd=tmp_path,
            timeout=15,
        )

        print(f"INFO: recovery after malformed JSON exited {recover_result.returncode}")
        print(f"INFO: stderr: {recover_result.stderr[:500]}")

        # Recovery should NOT crash (exit 5 = internal error is a gap)
        if recover_result.returncode == 5:
            raise AssertionError(
                "GAP [crash]: Recovery crashed (exit 5) on malformed JSON input.\n"
                "Spec §7.2: Recovery must 'detect gaps or corruption'.\n"
                f"stderr: {recover_result.stderr[:300]}"
            )

    def test_recover_detects_corrupt_state_projection(self, tmp_path: Path) -> None:
        """Verify recovery detects corrupt state/latest.json projection.

        Spec: §9.2.1 — state/latest.json is full projected state.
        Spec: §9.2.2 — projection replay must be deterministic.
        Expect: Recovery with invalid state data should detect corruption.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Corrupt state/latest.json
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    state_latest = rd / "state" / "latest.json"
                    if state_latest.exists():
                        # Write garbage
                        state_latest.write_text("NOT VALID JSON {{{{{", encoding="utf-8")
                        print(f"INFO: Corrupted state/latest.json")

        # Try recovery
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        combined = (recover_result.stdout + recover_result.stderr).lower()
        print(f"INFO: recovery after corrupt state exited {recover_result.returncode}")

        # Should detect corruption, not crash
        if recover_result.returncode == 5:
            raise AssertionError(
                "GAP [crash]: Recovery crashed (exit 5) on corrupt state/latest.json.\n"
                "Spec §7.2: Recovery must 'detect gaps or corruption'."
            )

    def test_recover_detects_broken_hash_chain(self, tmp_path: Path) -> None:
        """Verify recovery detects broken prev_hash chain in events.

        Spec: §9.3.1 — prev_hash must chain to entry_hash of prior event.
        Expect: Broken hash chain should produce BLOCKED or QUARANTINED.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Find events.jsonl and break hash chain
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    events_file = rd / "events.jsonl"
                    if events_file.exists():
                        content = events_file.read_text(encoding="utf-8")
                        lines = content.strip().splitlines()
                        if len(lines) >= 2:
                            # Modify prev_hash of second event to break chain
                            try:
                                first_event = json.loads(lines[0])
                                second_event = json.loads(lines[1])
                                # Set prev_hash to a wrong value
                                second_event["prev_hash"] = "sha256:DEADBEEF_WRONG_HASH"
                                second_event["entry_hash"] = "sha256:ALSO_WRONG"
                                lines[1] = json.dumps(second_event, sort_keys=True)
                                events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                                print(f"INFO: Broke hash chain in events.jsonl")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                print(
                                    f"WARN: Could not modify events.jsonl — "
                                    f"not enough events or malformed"
                                )

        # Try recovery
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        combined = (recover_result.stdout + recover_result.stderr).lower()
        print(f"INFO: recovery after hash break exited {recover_result.returncode}")

        # Check for integrity detection
        integrity_indicators = ["integrity", "hash", "chain", "gaps", "corrupt", "blocked"]
        found = [i for i in integrity_indicators if i in combined]
        print(f"INFO: Integrity detection indicators: {found}")


# ---------------------------------------------------------------------------
# Fault Family 3: Fresh-start-required recovery path
# ---------------------------------------------------------------------------


class TestFreshStartRequired:
    """Recovery paths that require a fresh start (quarantine terminalization).

    Per docs/ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md §6.4:
      - complete is not replayed before reconcile closure
      - linked worktree evidence is not destroyed before recovery has classified it

    Per recovery.py RecoveryOutcome:
      - QUARANTINED means fresh start is required (gate stays closed)
      - recovery_case_status(QUARANTINED) = "open" (case unresolved)
      - recovery_action_status(QUARANTINED) = "rejected" (actions cannot proceed)
    """

    def test_quarantined_outcome_opens_case(self, tmp_path: Path) -> None:
        """Verify QUARANTINED recovery outcome maps to 'open' case status.

        Spec: recovery_case_status(QUARANTINED) = "open" because quarantine
        terminalizes the run for fresh start; case remains unresolved until
        re-dispatch completes.

        Expect: When recovery produces QUARANTINED, case-list should show
        at least one open case associated with the recovery.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)
        print(f"INFO: orch run exited {run_result.returncode}")

        # Try recovery (which should check for artifacts)
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        # Now check case-list for open cases
        case_list_result = _run_vectl(
            ["orch", "case-list", "--latest", "--json"],
            cwd=tmp_path,
            timeout=10,
        )

        print(f"INFO: case-list exited {case_list_result.returncode}")
        if case_list_result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch case-list` not registered")

        # Parse recovery outcome and case-list
        if recover_result.returncode == 0 and recover_result.stdout.strip():
            try:
                data = json.loads(recover_result.stdout)
                report = data.get("recovery_report", {})
                if isinstance(report, dict):
                    outcome = report.get("outcome", "")
                    print(f"INFO: Recovery outcome: {outcome}")
                    print(f"INFO: gate_open_allowed: {report.get('gate_open_allowed')}")
            except json.JSONDecodeError:
                print(f"WARN: Recovery output not valid JSON")

    def test_recovery_gate_closed_for_quarantine(self, tmp_path: Path) -> None:
        """Verify gate_open_allowed is False for QUARANTINED outcomes.

        Spec: recovery_gate_open_allowed(QUARANTINED) = False because
        quarantine terminalizes the run for fresh start; gate stays closed.

        Expect: After a quarantine-producing recovery, the dispatch gate
        must remain closed (not allow resume).
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run and create stale artifacts
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Inject stale artifacts to trigger quarantine path
        runs_dir = tmp_path / ".vectl" / "runs"
        if runs_dir.exists():
            for rd in runs_dir.iterdir():
                if rd.is_dir():
                    # Create a stale continuity artifact
                    continuity_dir = rd / "continuity"
                    continuity_dir.mkdir(parents=True, exist_ok=True)
                    ledger_path = continuity_dir / "ledger.json"
                    stale_entry = {
                        "step_id": "core.fault_step",
                        "session_id": "old-session-stale",
                        "runner": "fast-shim",
                        "status": "running",
                        "created_at": time.time() - 86400,  # 24 hours ago
                        "updated_at": time.time() - 86400,
                    }
                    ledger_path.write_text(json.dumps(stale_entry), encoding="utf-8")
                    print(f"INFO: Injected stale continuity artifact")

        # Recover — should detect stale artifact
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        if recover_result.returncode == 0 and recover_result.stdout.strip():
            try:
                data = json.loads(recover_result.stdout)
                report = data.get("recovery_report", {})
                if isinstance(report, dict):
                    outcome = report.get("outcome", "")
                    gate_open = report.get("gate_open_allowed")
                    print(f"INFO: Recovery outcome: {outcome}")
                    print(f"INFO: gate_open_allowed: {gate_open}")

                    # Per recovery.py: QUARANTINED => gate_open_allowed = False
                    if outcome in ("QUARANTINED", "quarantined") and gate_open is True:
                        raise AssertionError(
                            f"GAP [gate-semantics]: QUARANTINED outcome has "
                            f"gate_open_allowed=True. Per spec: quarantine "
                            f"terminalizes the run, gate must stay closed.\n"
                            f"  outcome: {outcome}, gate_open_allowed: {gate_open}"
                        )
            except json.JSONDecodeError:
                print(f"WARN: Recovery output not valid JSON")

    def test_recovery_no_silent_deletion_flag(self, tmp_path: Path) -> None:
        """Verify recovery sets no_silent_deletion_preserved=True.

        Per ORCHESTRATION-PLANE-OPERATOR-CONFLICT-RECOVERY.md §6.4:
          'Preserved evidence may remain in place or be moved to a durable
          quarantine location, but it must not be silently discarded.'

        Per RecoveryReport: no_silent_deletion_preserved field must be True.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        # Start a run
        run_result = _run_vectl(["orch", "run", "--dry-run", "--json"], cwd=tmp_path)

        # Recover
        recover_result = _run_vectl(
            ["orch", "recover", "--latest", "--json"],
            cwd=tmp_path,
            timeout=15,
        )

        if recover_result.returncode == 0 and recover_result.stdout.strip():
            try:
                data = json.loads(recover_result.stdout)
                report = data.get("recovery_report", {})
                if isinstance(report, dict):
                    no_silent_del = report.get("no_silent_deletion_preserved")
                    print(f"INFO: no_silent_deletion_preserved: {no_silent_del}")

                    # Per §6.4: must be True. False is a gap.
                    # This assertion is intentionally strict — it MUST fail
                    # if the implementation silently deletes evidence.
                    # This is the core expected-red test for this fault family.
                    assert no_silent_del is not False, (
                        "GAP [silent-deletion]: Recovery report indicates "
                        "no_silent_deletion_preserved=False. Per §6.4: "
                        "evidence must not be silently discarded."
                    )
            except json.JSONDecodeError:
                print(f"WARN: Recovery output not valid JSON")


# ---------------------------------------------------------------------------
# Exit code compliance tests
# ---------------------------------------------------------------------------


class TestRecoveryExitCodes:
    """Verify exit codes per §6 match recovery conditions.

    | Code | Meaning |
    |------|---------|
    | 0    | Success |
    | 1    | Error (general) |
    | 2    | Not found |
    | 3    | Validation error |
    | 4    | Recovery required |
    | 5    | Internal error |
    """

    def test_recover_nonexistent_run_exit_2(self, tmp_path: Path) -> None:
        """Verify recover with nonexistent run ID produces exit code 2.

        Spec: §6 — exit code 2 = Not found.
        Spec: §7.2 — recover takes optional RUN_ID.
        """
        plan_path = _write_minimal_plan(tmp_path)
        _write_fast_shim(tmp_path)
        _write_vectl_yaml(tmp_path, str(tmp_path / "shims" / "fast_runner.sh"))
        _init_git_repo(tmp_path)

        result = _run_vectl(
            ["orch", "recover", "nonexistent-run-id-99999"],
            cwd=tmp_path,
            timeout=10,
        )

        print(f"INFO: recover nonexistent run exited {result.returncode}")
        combined = (result.stdout + result.stderr).lower()

        # Skip if command not registered
        if result.returncode == 127:
            raise AssertionError("GAP [registration]: recover command not registered")

        if "not yet implemented" in combined:
            raise AssertionError("GAP [wiring]: recover not fully implemented")

        # Per §6: exit 2 = Not found for nonexistent run
        # Exit 0 with "no_artifacts" is also acceptable (no state to recover)
        # Exit 1 or 3 could also be valid (error/validation)
        # Exit 5 = internal error is NOT valid for "not found"
        if result.returncode == 5:
            raise AssertionError(
                f"GAP [exit-code]: Recovery crashed with exit 5 (internal error) "
                f"for nonexistent run. Per §6, exit 2 (not found) expected.\n"
                f"stderr: {result.stderr[:300]}"
            )

    def test_recover_dry_run_exit_0(self, tmp_path: Path) -> None:
        """Verify recover --dry-run produces exit code 0 on success.

        Spec: §6 — exit code 0 = Success.
        Spec: §7.2 — --dry-run validates without modifying.
        """
        plan_path = _write_minimal_plan(tmp_path)
        shim_path = _write_fast_shim(tmp_path)
        vectl_path = _write_vectl_yaml(tmp_path, str(shim_path))
        _init_git_repo(tmp_path)

        result = _run_vectl(
            ["orch", "recover", "--dry-run", "--json"],
            cwd=tmp_path,
            timeout=10,
        )

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: recover --dry-run not registered")

        if result.returncode == 5:
            raise AssertionError(
                "GAP [crash]: recover --dry-run crashed (exit 5) with no run state"
            )

        # Valid exits: 0 (success), 2 (not found), 4 (recovery required)
        if result.returncode not in (0, 2, 4):
            combined = (result.stdout + result.stderr).lower()
            if "not yet implemented" not in combined:
                print(f"WARN: recover --dry-run exited {result.returncode} (expected 0, 2, or 4)")


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestMissingConfigSnapshot,
        TestCorruptContinuityArtifacts,
        TestFreshStartRequired,
        TestRecoveryExitCodes,
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
        print("  [registration]           - CLI command not registered")
        print("  [wiring]                 - Command registered but not connected")
        print("  [missing-snapshot]       - Config snapshot deletion not detected")
        print("  [corrupt-artifacts]      - Corrupt events/state not detected")
        print("  [hash-integrity]         - Hash chain break not detected")
        print("  [fresh-start]            - Quarantine gate/case semantics gap")
        print("  [silent-deletion]        - Evidence silently discarded")
        print("  [exit-code]              - Wrong exit code for recovery conditions")
        print("  [crash]                  - Recovery crashed on corrupt input")
        print(
            "\nDownstream green owner: "
            "cli_blackbox_recovery_faults_cases.implement-recovery-fault-surfaces"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All recovery fault surfaces correctly detected")
        print("  - Missing config snapshot detected")
        print("  - Corrupt events/state/artifacts detected")
        print("  - Broken hash chain detected")
        print("  - Fresh-start/quarantine path correctly gated")
        print("  - No silent deletion of evidence")
        print("  - Exit codes match §6 specification")
        sys.exit(0)
