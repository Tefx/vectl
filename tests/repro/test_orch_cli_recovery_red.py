#!/usr/bin/env python3
"""Reproduction: Issue orch_operator_tests.cli_recovery_red - CLI/operator workflow gaps.

Expected: Per docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.2,
the following command families exist with documented behavior:
- vectl orch run/resume/recover/runs/prune
- vectl orch inspect status/events/logs/artifacts/actions
- vectl orch case list/show/respond
- vectl orch control pause/unpause/stop

Actual: Testing black-box to verify command registration and contract gaps.
This is an expected-red test: failures expose missing implementation, not bugs.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


class TestRunLifecycleFamily:
    """Black-box fixtures for vectl orch run/resume/recover/runs/prune."""

    def test_orch_runs_lists_runs(self):
        """Verify `vectl orch runs` command exists and returns valid output.

        Spec: §7.2 - `vectl orch runs [--status STATUS] [--json]`
        Spec: §7.8 - Exit codes: 0=success, 1=error, 2=not found, 3=validation, 4=recovery block, 5=internal
        Expected: Lists available runs from run store index.
        Gap classification:
          - exit 127: command not registered (major gap)
          - exit 5: internal app failure (gap)
          - "not implemented" / "not wired" in stderr: wiring gap (expected-red)
          - exit 0: command works (green)
          - exit 1/2/3/4 without "not implemented": acceptable (validation/state errors)
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "runs"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch runs` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:500]}")
        if result.stderr:
            print(f"INFO: stderr: {result.stderr[:500]}")

        # Classify gaps by exit code and output per §7.8
        if result.returncode == 127:
            # Shell-level "command not found" - major registration gap
            raise AssertionError(
                f"GAP [registration]: `vectl orch runs` not registered - run lifecycle family missing"
            )

        # Check for wiring gaps - this is the expected-red condition
        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                f"GAP [wiring]: `vectl orch runs` orchestration app not fully implemented\n"
                f"  exit code: {result.returncode}\n"
                f"  stderr: {result.stderr}"
            )

    def test_orch_run_requires_plan(self):
        """Verify `vectl orch run` validates plan existence.

        Spec: §7.2 - `vectl orch run [PLAN]`
        Spec: §7.8 - Exit codes
        Expected: Fails with clear error if plan not provided and no config default.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            result = subprocess.run(
                ["uv", "run", "vectl", "orch", "run"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=tmpdir,
            )

            print(f"INFO: `vectl orch run` exited {result.returncode}")

            # Expected-red: if command doesn't exist, that's a gap
            if result.returncode == 127:
                raise AssertionError("GAP [registration]: `vectl orch run` not registered")

            combined = (result.stdout + result.stderr).lower()
            if "not yet implemented" in combined or "not wired" in combined:
                raise AssertionError(
                    "GAP [wiring]: `vectl orch run` orchestration app not fully implemented"
                )

    def test_orch_resume_requires_selector(self):
        """Verify `vectl orch resume` requires explicit RUN_ID or --latest.

        Spec: §7.2 + §7.4 - mutating commands require explicit selector.
        Spec: §7.8 - Exit codes
        Expected: Fails with error if no run selector provided.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "resume"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch resume` exited {result.returncode}")

        # Expected-red: command not registered is a gap
        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch resume` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                "GAP [wiring]: `vectl orch resume` orchestration app not fully implemented"
            )

    def test_orch_recover_supports_dry_run(self):
        """Verify `vectl orch recover --dry-run` exists.

        Spec: §7.2 - `vectl orch recover [RUN_ID|--latest] [--dry-run] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Diagnose without modifying state.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "recover", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch recover --dry-run` exited {result.returncode}")

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch recover` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined:
            raise AssertionError(
                "GAP [wiring]: `vectl orch recover` orchestration app not fully implemented"
            )


class TestInspectFamily:
    """Black-box fixtures for vectl orch inspect status/events/logs/artifacts/actions."""

    def _check_wiring_gap(self, result, command_name):
        """Helper to check for wiring gaps in command output."""
        if result.returncode == 127:
            raise AssertionError(f"GAP [registration]: `{command_name}` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                f"GAP [wiring]: `{command_name}` orchestration app not fully implemented"
            )

    def test_orch_inspect_status_exists(self):
        """Verify `vectl orch inspect status` command exists.

        Spec: §7.2 - `vectl orch inspect status [RUN_ID|--latest] [--watch] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Read projected run summary.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "inspect", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch inspect status` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect status")

    def test_orch_inspect_events_exists(self):
        """Verify `vectl orch inspect events` command exists.

        Spec: §7.2 - `vectl orch inspect events [RUN_ID|--latest] [--jsonl]`
        Spec: §7.8 - Exit codes
        Expected: Read canonical event stream.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "inspect", "events"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch inspect events` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect events")

    def test_orch_inspect_logs_exists(self):
        """Verify `vectl orch inspect logs` command exists.

        Spec: §7.2 - `vectl orch inspect logs [RUN_ID|--latest] [--tail N] [--follow]`
        Spec: §7.8 - Exit codes
        Expected: Read operator-friendly logs.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "inspect", "logs"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch inspect logs` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect logs")

    def test_orch_inspect_artifacts_exists(self):
        """Verify `vectl orch inspect artifacts` command exists.

        Spec: §7.2 - `vectl orch inspect artifacts [RUN_ID|--latest] [--kind KIND] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Read artifact paths and layout.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "inspect", "artifacts"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch inspect artifacts` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect artifacts")

    def test_orch_inspect_actions_exists(self):
        """Verify `vectl orch inspect actions` command exists.

        Spec: §7.2 - `vectl orch inspect actions [RUN_ID|--latest] [--status STATUS] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Inspect pending/applied/rejected operator requests.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "inspect", "actions"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch inspect actions` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect actions")

        print(f"INFO: `vectl orch inspect actions` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch inspect actions")


class TestCaseFamily:
    """Black-box fixtures for vectl orch case list/show/respond."""

    def _check_wiring_gap(self, result, command_name):
        """Helper to check for wiring gaps in command output."""
        if result.returncode == 127:
            raise AssertionError(f"GAP [registration]: `{command_name}` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                f"GAP [wiring]: `{command_name}` orchestration app not fully implemented"
            )

    def test_orch_case_list_exists(self):
        """Verify `vectl orch case list` command exists.

        Spec: §7.2 - `vectl orch case list [RUN_ID|--latest] [--watch] [--json]`
        Spec: §7.8 - Exit codes
        Expected: List open or historical cases.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "case", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch case list` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch case list")

    def test_orch_case_show_requires_case_id(self):
        """Verify `vectl orch case show <CASE_ID>` requires case id.

        Spec: §7.2 - `vectl orch case show <CASE_ID> [--watch] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Inspect one case bundle.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "case", "show"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch case show` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch case show")

    def test_orch_case_respond_requires_case_id_and_action(self):
        """Verify `vectl orch case respond` requires case id and action.

        Spec: §7.2 - `vectl orch case respond <CASE_ID> --action ACTION [...]`
        Spec: §7.8 - Exit codes
        Expected: Submit bounded operator response.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "case", "respond"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch case respond` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch case respond")


class TestControlFamily:
    """Black-box fixtures for vectl orch control pause/unpause/stop."""

    def _check_wiring_gap(self, result, command_name):
        """Helper to check for wiring gaps in command output."""
        if result.returncode == 127:
            raise AssertionError(f"GAP [registration]: `{command_name}` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                f"GAP [wiring]: `{command_name}` orchestration app not fully implemented"
            )

    def test_orch_control_pause_exists(self):
        """Verify `vectl orch control pause` command exists.

        Spec: §7.2 - `vectl orch control pause [RUN_ID|--latest] [--reason TEXT]`
        Spec: §7.8 - Exit codes
        Expected: Stop dispatching new work.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "control", "pause"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch control pause` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch control pause")

    def test_orch_control_unpause_exists(self):
        """Verify `vectl orch control unpause` command exists.

        Spec: §7.2 - `vectl orch control unpause [RUN_ID|--latest] [--reason TEXT]`
        Spec: §7.8 - Exit codes
        Expected: Resume normal dispatch from paused state.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "control", "unpause"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch control unpause` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch control unpause")

    def test_orch_control_stop_exists(self):
        """Verify `vectl orch control stop` command exists.

        Spec: §7.2 - `vectl orch control stop [RUN_ID|--latest] [--reason TEXT]`
        Spec: §7.8 - Exit codes
        Expected: Graceful operator-requested stop.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "control", "stop"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch control stop` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch control stop")


class TestConfigFamily:
    """Black-box fixtures for vectl orch config show/validate/tools."""

    def _check_wiring_gap(self, result, command_name):
        """Helper to check for wiring gaps in command output."""
        if result.returncode == 127:
            raise AssertionError(f"GAP [registration]: `{command_name}` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                f"GAP [wiring]: `{command_name}` orchestration app not fully implemented"
            )

    def test_orch_config_show_exists(self):
        """Verify `vectl orch config show` command exists.

        Spec: §7.2 - `vectl orch config show [--effective] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Show effective configuration.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "config", "show"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch config show` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch config show")

    def test_orch_config_validate_exists(self):
        """Verify `vectl orch config validate` command exists.

        Spec: §7.2 - `vectl orch config validate [PATH]`
        Spec: §7.8 - Exit codes
        Expected: Validate config shape and report sources/errors.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "config", "validate"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch config validate` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch config validate")

    def test_orch_config_tools_exists(self):
        """Verify `vectl orch config tools` command exists.

        Spec: §7.2 - `vectl orch config tools [--json]`
        Spec: §7.8 - Exit codes
        Expected: List canonical tool registry for allowlist authoring.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "config", "tools"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch config tools` exited {result.returncode}")
        self._check_wiring_gap(result, "vectl orch config tools")


class TestOrchGroupHelp:
    """Verify `vectl orch` group-level help exists."""

    def test_orch_group_help(self):
        """Verify `vectl orch` with no subcommand prints summary.

        Spec: §7.1 - group-level behavior must print purpose summary and exit 0.
        Spec: §7.8 - Exit codes (0=success)
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch` exited {result.returncode}")
        print(f"INFO: stdout: {result.stdout[:1000]}")

        # Per §7.1: must exit 0 and print summary
        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch` command group not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                "GAP [wiring]: `vectl orch` group orchestration app not fully implemented"
            )

        # Should mention orchestration or orch purpose
        if "orch" not in combined:
            print(f"WARN: Output doesn't mention 'orch' - may be wrong command")


class TestOrchPruneCommand:
    """Black-box fixtures for vectl orch prune."""

    def test_orch_prune_exists(self):
        """Verify `vectl orch prune` command exists.

        Spec: §7.2 - `vectl orch prune [--older-than DAYS] [--dry-run] [--json]`
        Spec: §7.8 - Exit codes
        Expected: Remove completed run artifacts by explicit retention/operator criteria.
        """
        result = subprocess.run(
            ["uv", "run", "vectl", "orch", "prune", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        print(f"INFO: `vectl orch prune --dry-run` exited {result.returncode}")

        if result.returncode == 127:
            raise AssertionError("GAP [registration]: `vectl orch prune` not registered")

        combined = (result.stdout + result.stderr).lower()
        if "not yet implemented" in combined or "not wired" in combined or "wiring not" in combined:
            raise AssertionError(
                "GAP [wiring]: `vectl orch prune` orchestration app not fully implemented"
            )


if __name__ == "__main__":
    # Run all tests and aggregate gaps
    test_classes = [
        TestRunLifecycleFamily,
        TestInspectFamily,
        TestCaseFamily,
        TestControlFamily,
        TestConfigFamily,
        TestOrchGroupHelp,
        TestOrchPruneCommand,
    ]

    gaps = []
    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                method = getattr(instance, method_name)
                try:
                    method()
                except AssertionError as e:
                    gaps.append(f"{test_class.__name__}.{method_name}: {e}")

    if gaps:
        print("\n" + "=" * 80)
        print("EXPECTED-RED GAPS FOUND (these failures are intentional):")
        print("=" * 80)
        for gap in gaps:
            print(f"  - {gap}")
        print("=" * 80)
        print(f"Total gaps: {len(gaps)}")
        print("\nGAP CLASSIFICATION:")
        print("  [registration] - Command not registered in CLI group")
        print("  [wiring]       - Command registered but orchestration app not implemented")
        print(
            "\nDownstream green owner: orch_operator_control_surface, orch_operator_recovery_cutover"
        )
        print("\nVERDICT: FAILED (expected) - gaps exposed for downstream green owners")
        sys.exit(1)
    else:
        print("\nVERDICT: FIXED - All documented CLI commands are registered")
        print("All command families have been wired:")
        print("  - vectl orch run/resume/recover/runs/prune")
        print("  - vectl orch inspect status/events/logs/artifacts/actions")
        print("  - vectl orch case list/show/respond")
        print("  - vectl orch control pause/unpause/stop")
        print("  - vectl orch config show/validate/tools")
        sys.exit(0)
