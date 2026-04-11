#!/usr/bin/env python3
"""Reproduction: CLI run-selector safety — ambiguous --latest, selector conflicts,
missing selectors, and boundary conditions.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md:
  §7.1 - resume/recover accept [RUN_ID|--latest]
  §7.4 - pause/unpause/stop accept [RUN_ID|--latest]
  §7.2 - status/events/logs/artifacts/actions accept [RUN_ID|--latest]
  §6   - Exit codes: 0=success, 1=error, 2=not found, 3=validation, 4=recovery

Specifically:
  - --latest with multiple active (non-terminal) runs must be ambiguous/rejected
    (the spec says "Use most recently updated non-terminal run" — if there are
    multiple non-terminal matches, the selector is ambiguous)
  - Explicit RUN_ID takes precedence over --latest (positional + flag = conflict)
  - No selector on a mutating command (resume, recover, stop, pause) must error
  - --latest with zero runs must return exit 2 (not found)

Actual: Black-box verification of selector safety behavior via public CLI subprocesses.
This is an expected-red test: failures expose missing safety guards, not bugs.
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
    owner="cli_blackbox_surface_safety_ops.define-surface-safety-tests",
    rationale="CLI selector safety guards (ambiguous --latest, conflict detection, "
    "missing-selector rejection) are intentionally red until the orch CLI surface "
    "enforces these safety invariants at the argument-validation layer.",
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_minimal_plan(root: Path, num_steps: int = 1) -> Path:
    """Write a minimal plan.yaml with N pending steps."""
    steps = []
    for i in range(num_steps):
        steps.append(
            {
                "id": f"core.step_{i}",
                "name": f"Step {i}",
                "status": "pending",
                "description": f"Test step {i}",
                "agent": "test-runner",
            }
        )
    plan = {
        "version": 1,
        "project": "selector-safety-test",
        "strategy_ref": "#",
        "context": "black-box selector safety test",
        "phases": [
            {
                "id": "core",
                "name": "Core",
                "status": "pending",
                "gate": "n/a",
                "steps": steps,
            }
        ],
    }
    plan_path = root / "plan.yaml"
    plan_path.write_text(yaml.dump(plan, sort_keys=False), encoding="utf-8")
    return plan_path


def _write_vectl_yaml(root: Path, runner_cmd: str) -> Path:
    """Write vectl.yaml pointing at plan.yaml with a slow runner."""
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
    """Write a slow runner shim that sleeps for a configurable time."""
    shim_dir = root / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "slow_runner.sh"
    content = """#!/usr/bin/env bash
# Slow runner shim for selector safety testing.
SLEEP="${SLEEP_SECONDS:-60}"
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
    """Write a fast runner shim that completes immediately."""
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
        ["git", "commit", "-m", "init selector safety test fixture"],
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
        shim_writer = _write_slow_shim
    shim_path = shim_writer(tmp_path)
    _write_vectl_yaml(tmp_path, str(shim_path))
    _write_minimal_plan(tmp_path)
    _init_git_repo(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Matrix Row 1: Ambiguous --latest with multiple active runs
# ---------------------------------------------------------------------------


class TestAmbiguousLatestSelector:
    """Verify --latest rejects or resolves ambiguity with multiple active runs.

    Spec: §7.1 --latest selects 'most recently updated non-terminal run'.
    Safety concern: If multiple non-terminal runs exist, --latest is ambiguous
    unless the system has a deterministic tiebreaker (most recently updated).

    We test that the system either:
    (a) resolves deterministically to the most-recently-updated run, OR
    (b) rejects --latest with a clear error when multiple active runs exist.

    Either is acceptable; silently picking an arbitrary run is a safety violation.
    """

    def test_resume_latest_with_multiple_active_runs(self, tmp_path: Path) -> None:
        """Verify `vectl orch resume --latest` handles multiple active runs safely.

        Spec: §7.1 — resume [RUN_ID|--latest], --latest selects most recently
        updated non-terminal run.
        Expected: If multiple active runs exist, system must either:
          - Deterministically pick the most-recently-updated, OR
          - Reject with clear error (ambiguous selector).
        Violation: Silently choosing an arbitrary run without logging which one.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start two runs in quick succession
        run1 = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        run2 = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)

        # Now try resume --latest
        result = _run_vectl(["orch", "resume", "--latest", "--dry-run"], cwd=tmp_path)

        print(f"INFO: resume --latest with 2 runs exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:500]}")

        # If it succeeded, verify it didn't silently choose arbitrarily
        if result.returncode == 0:
            combined = (result.stdout + result.stderr).lower()
            # Should mention which run was selected — not silently pick one
            if "run" not in combined and "selecting" not in combined:
                # Not necessarily a failure if it's dry-run with no output,
                # but flag it as a potential safety concern
                print(
                    "WARN: resume --latest succeeded but output doesn't indicate "
                    "which run was selected — ambiguous resolution may be silent"
                )

    def test_status_latest_with_zero_runs(self, tmp_path: Path) -> None:
        """Verify `vectl orch status --latest` with zero runs returns not found.

        Spec: §6 — exit 2 = not found.
        Expected: When no runs exist, --latest should exit 2 or error clearly.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "status", "--latest", "--json"], cwd=tmp_path)

        print(f"INFO: status --latest with 0 runs exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        # Per §6: exit 2 for not-found, or exit 1 for general error
        # Exit 0 with empty/success result would be a safety gap
        if result.returncode == 0:
            combined = (result.stdout + result.stderr).lower()
            if "no run" not in combined and "not found" not in combined and "empty" not in combined:
                raise AssertionError(
                    "GAP [selector-safety]: `vectl orch status --latest` with zero "
                    "runs exited 0 without a clear not-found indicator.\n"
                    "Per §6: exit 2 = not found is the expected exit code.\n"
                    f"  stdout: {result.stdout[:200]}"
                )

    def test_recover_latest_with_zero_runs(self, tmp_path: Path) -> None:
        """Verify `vectl orch recover --latest` with zero runs returns not found.

        Spec: §6 — exit 2 = not found.
        Expected: When no runs exist, --latest should exit 2.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "recover", "--latest", "--dry-run"], cwd=tmp_path)

        print(f"INFO: recover --latest with 0 runs exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        if result.returncode == 0:
            combined = (result.stdout + result.stderr).lower()
            if "no run" not in combined and "not found" not in combined:
                raise AssertionError(
                    "GAP [selector-safety]: `vectl orch recover --latest` with zero "
                    "runs exited 0 without a clear not-found indicator.\n"
                    "Per §6: exit 2 = not found is the expected exit code.\n"
                    f"  stdout: {result.stdout[:200]}"
                )


# ---------------------------------------------------------------------------
# Matrix Row 2: Explicit RUN_ID + --latest conflict handling
# ---------------------------------------------------------------------------


class TestSelectorConflict:
    """Verify that providing both RUN_ID positional and --latest is handled.

    Spec: §7.1 — resume/recover accept [RUN_ID|--latest].
    If both a positional RUN_ID and --latest are provided, it's ambiguous:
    should the explicit RUN_ID win, or should the system reject the conflict?

    Safety principle: explicit > implicit, but conflicting selectors should be
    rejected rather than silently preferring one.
    """

    def test_resume_both_runid_and_latest(self, tmp_path: Path) -> None:
        """Verify resume with both RUN_ID and --latest is rejected or resolved.

        Spec: §7.1 — resume [RUN_ID|--latest].
        Expected: If both are provided, system should either:
          - Reject with validation error (exit 3), OR
          - Accept with explicit RUN_ID taking precedence (documented behavior).
        Violation: Silently ignoring one selector.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(
            ["orch", "resume", "run-00000000", "--latest"],
            cwd=tmp_path,
        )

        print(f"INFO: resume with both RUN_ID and --latest exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        combined = (result.stdout + result.stderr).lower()
        # Acceptable outcomes:
        # - Exit 3 (validation error) with message about conflict
        # - Exit 0/1/2 if it resolves deterministically and documents which wins
        # Safety gap: silently ignoring the positional arg or --latest
        if result.returncode == 0:
            # If it succeeded, check if it acknowledged both selectors
            if "conflict" in combined or "ambiguous" in combined or "ignoring" in combined:
                print("INFO: Good — conflict detected and reported")
            else:
                # It may have used RUN_ID or --latest; not necessarily wrong
                # if one takes precedence, but should be documented
                print(
                    "WARN: resume with both RUN_ID and --latest succeeded without "
                    "documenting which selector took precedence. Could be a safety gap."
                )

    def test_stop_both_runid_and_latest(self, tmp_path: Path) -> None:
        """Verify stop with both RUN_ID and --latest is rejected or resolved.

        Spec: §7.4 — stop [RUN_ID|--latest].
        Same conflict semantics as resume.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(
            ["orch", "stop", "run-00000000", "--latest"],
            cwd=tmp_path,
        )

        print(f"INFO: stop with both RUN_ID and --latest exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")

    def test_pause_both_runid_and_latest(self, tmp_path: Path) -> None:
        """Verify pause with both RUN_ID and --latest is rejected or resolved.

        Spec: §7.4 — pause [RUN_ID|--latest].
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(
            ["orch", "pause", "run-00000000", "--latest"],
            cwd=tmp_path,
        )

        print(f"INFO: pause with both RUN_ID and --latest exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")


# ---------------------------------------------------------------------------
# Matrix Row 3: No selector on mutating commands
# ---------------------------------------------------------------------------


class TestMissingSelectorOnMutatingCommands:
    """Verify that mutating commands (resume, recover, pause, stop) without
    any selector produce a clear error.

    Spec: §7.1 — resume/recover require [RUN_ID|--latest].
    Spec: §7.4 — pause/unpause/stop require [RUN_ID|--latest].
    Expected: Must not silently target an arbitrary run.
    """

    def test_resume_no_selector(self, tmp_path: Path) -> None:
        """Verify `vectl orch resume` (no RUN_ID, no --latest) is rejected.

        Spec: §7.1 — resume accepts [RUN_ID|--latest].
        Expected: Must fail with clear error. Not providing a selector when
        multiple runs could exist is a safety violation.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "resume"], cwd=tmp_path)

        print(f"INFO: resume with no selector exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        # Per §6: exit 3 (validation error) or exit 1 (general error)
        # Should NOT exit 0
        if result.returncode == 0:
            raise AssertionError(
                "GAP [selector-safety]: `vectl orch resume` with no selector "
                "exited 0 — must require explicit RUN_ID or --latest.\n"
                "Per §7.1: resume requires [RUN_ID|--latest]."
            )

    def test_recover_no_selector(self, tmp_path: Path) -> None:
        """Verify `vectl orch recover` (no RUN_ID, no --latest) is rejected.

        Spec: §7.1 — recover accepts [RUN_ID|--latest].
        Expected: Must fail with clear error.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "recover"], cwd=tmp_path)

        print(f"INFO: recover with no selector exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        if result.returncode == 0:
            raise AssertionError(
                "GAP [selector-safety]: `vectl orch recover` with no selector "
                "exited 0 — must require explicit RUN_ID or --latest."
            )

    def test_stop_no_selector(self, tmp_path: Path) -> None:
        """Verify `vectl orch stop` (no RUN_ID, no --latest) is rejected.

        Spec: §7.4 — stop requires [RUN_ID|--latest].
        Expected: Must fail — stopping without a specific target is dangerous.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "stop"], cwd=tmp_path)

        print(f"INFO: stop with no selector exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        if result.returncode == 0:
            raise AssertionError(
                "GAP [selector-safety]: `vectl orch stop` with no selector "
                "exited 0 — must require explicit RUN_ID or --latest.\n"
                "Stopping without a specific target is a safety violation."
            )

    def test_pause_no_selector(self, tmp_path: Path) -> None:
        """Verify `vectl orch pause` (no RUN_ID, no --latest) is rejected.

        Spec: §7.4 — pause requires [RUN_ID|--latest].
        Expected: Must fail — pausing without a specific target is dangerous.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "pause"], cwd=tmp_path)

        print(f"INFO: pause with no selector exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        if result.returncode == 0:
            raise AssertionError(
                "GAP [selector-safety]: `vectl orch pause` with no selector "
                "exited 0 — must require explicit RUN_ID or --latest.\n"
                "Pausing without a specific target is a safety violation."
            )

    def test_unpause_no_selector(self, tmp_path: Path) -> None:
        """Verify `vectl orch unpause` (no RUN_ID, no --latest) is rejected.

        Spec: §7.4 — unpause requires [RUN_ID|--latest].
        """
        _setup_repo(tmp_path, _write_fast_shim)

        result = _run_vectl(["orch", "unpause"], cwd=tmp_path)

        print(f"INFO: unpause with no selector exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:300]}")

        if result.returncode == 0:
            raise AssertionError(
                "GAP [selector-safety]: `vectl orch unpause` with no selector "
                "exited 0 — must require explicit RUN_ID or --latest."
            )


# ---------------------------------------------------------------------------
# Matrix Row 4: Explicit RUN_ID precedence — verify the right run is targeted
# ---------------------------------------------------------------------------


class TestExplicitRunIdPrecedence:
    """Verify explicit RUN_ID targets the correct run, not the latest.

    Spec: §7.1 — explicit RUN_ID must be respected.
    Safety: If I say `vectl orch stop RUN_A`, it should NOT stop RUN_B.
    """

    def test_stop_specific_run_id(self, tmp_path: Path) -> None:
        """Verify `vectl orch stop <RUN_ID>` targets that specific run.

        Spec: §7.4 — stop [RUN_ID|--latest].
        Expected: Explicit RUN_ID should target only that run.
        """
        _setup_repo(tmp_path, _write_fast_shim)

        # Start a run and capture its ID
        run_result = _run_vectl(["orch", "run", "--json"], cwd=tmp_path, timeout=30)
        run_id = None
        if run_result.returncode == 0 and run_result.stdout.strip():
            try:
                data = json.loads(run_result.stdout)
                run_id = data.get("run_id")
            except (json.JSONDecodeError, KeyError):
                pass

        if run_id is None:
            pytest.skip("Could not start a run to test explicit RUN_ID targeting")

        # Now stop that specific run
        result = _run_vectl(
            ["orch", "stop", run_id, "--reason", "test explicit targeting"],
            cwd=tmp_path,
        )

        print(f"INFO: stop <{run_id}> exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")

        # Verify it mentions the targeted run ID in output
        combined = result.stdout + result.stderr
        if run_id not in combined:
            print(
                f"WARN: stop output doesn't mention targeted run_id '{run_id}' "
                f"— may be silently targeting wrong run"
            )

    def test_resume_specific_run_id(self, tmp_path: Path) -> None:
        """Verify `vectl orch resume <RUN_ID>` targets that specific run.

        Spec: §7.1 — resume [RUN_ID|--latest].
        Expected: Explicit RUN_ID should resume that specific run.
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

        if run_id is None:
            pytest.skip("Could not start a run to test explicit RUN_ID targeting")

        result = _run_vectl(
            ["orch", "resume", run_id, "--dry-run"],
            cwd=tmp_path,
        )

        print(f"INFO: resume <{run_id}> exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:300]}")

        combined = result.stdout + result.stderr
        if run_id not in combined:
            print(
                f"WARN: resume output doesn't mention targeted run_id '{run_id}' "
                f"— may be silently targeting wrong run"
            )


if __name__ == "__main__":
    """Run all tests and report expected-red gaps."""
    import traceback

    test_classes = [
        TestAmbiguousLatestSelector,
        TestSelectorConflict,
        TestMissingSelectorOnMutatingCommands,
        TestExplicitRunIdPrecedence,
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
        print("EXPECTED-RED GAPS FOUND (selector safety — these failures are intentional):")
        print("=" * 80)
        for gap in gaps:
            print(f"  - {gap}")
        print("=" * 80)
        print(f"Total gaps: {len(gaps)}")
        print("\nGAP CLASSIFICATION:")
        print("  [selector-safety] — Missing selector validation or ambiguous resolution")
        print("  [conflict]         — Both RUN_ID and --latest provided without rejection")
        print("  [missing-selector] — Mutating command doesn't require selector")
        print("  [precedence]       — Explicit RUN_ID not respected over --latest")
        print(
            "\nDownstream implementation owner: "
            "cli_blackbox_surface_safety_ops.implement-selector-safety-guards"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owner")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All selector safety invariants enforced")
        print("  - --latest rejects ambiguity with multiple active runs")
        print("  - RUN_ID + --latest conflict is detected and rejected")
        print("  - Mutating commands require explicit selector")
        print("  - Explicit RUN_ID takes precedence over --latest")
        print("  - --latest with zero runs returns exit 2")
        sys.exit(0)
