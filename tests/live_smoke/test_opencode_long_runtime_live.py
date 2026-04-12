"""Opt-in long-running live smoke scenarios for OpenCode runner.

These tests are intentionally excluded from normal developer loops via
environment gating:

- RUN_LIVE_RUNNER_TESTS=1
- RUN_LONG_LIVE_RUNNER_TESTS=1

They exercise extended-time real-runner behavior without being part of
the default test path.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from tests.live_smoke.helpers import (
    OPENCODE_MODEL,
    OPENCODE_MODEL_ENVAR,
    LiveRunnerPreflight,
    build_model_override_args,
    is_live_runner_opted_in,
    is_long_live_runner_opted_in,
    live_runner,
    long_live,
    opencode_live,
    run_subprocess,
    skip_if_auth_missing,
    skip_if_binary_missing,
)

LONG_SOAK_PROMPT = """You are running a long-runtime smoke test.

Required behavior:
1. Execute this exact shell command and wait for completion:
   python -c \"import time; time.sleep(45); print('LONG_SOAK_OK')\"
2. After the command exits, respond ONLY with this exact JSON:
   {"status":"completed","message":"LONG_SOAK_OK"}

Do not add markdown. Do not add extra text.
"""


def _extract_session_id(jsonl_output: str) -> str | None:
    """Extract sessionID/sessionId from OpenCode JSONL output."""
    for raw in jsonl_output.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        session_id = payload.get("sessionID") or payload.get("sessionId")
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


@pytest.fixture
def opencode_long_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault(OPENCODE_MODEL_ENVAR, OPENCODE_MODEL)
    return env


@live_runner
@opencode_live
@long_live
class TestOpenCodeLongRuntimeLive:
    """Long-running live scenarios for OpenCode."""

    def test_opt_in_gates(self) -> None:
        if not is_live_runner_opted_in():
            pytest.skip("requires RUN_LIVE_RUNNER_TESTS=1", allow_module_level=True)
        if not is_long_live_runner_opted_in():
            pytest.skip("requires RUN_LONG_LIVE_RUNNER_TESTS=1", allow_module_level=True)

    def test_opencode_long_runtime_subprocess(
        self,
        tmp_path: Any,
        opencode_long_env: dict[str, str],
    ) -> None:
        """Run a single extended-duration real opencode dispatch.

        This validates that longer-running runner tasks complete and return
        full subprocess evidence.
        """
        if not is_live_runner_opted_in() or not is_long_live_runner_opted_in():
            pytest.skip("long live suite not opted in", allow_module_level=True)

        preflight = LiveRunnerPreflight("opencode")
        if not preflight.check_binary():
            skip_if_binary_missing("opencode")
        if not preflight.check_auth():
            skip_if_auth_missing("opencode")

        model_args = build_model_override_args("opencode", OPENCODE_MODEL)
        command = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            str(tmp_path),
            *model_args,
            LONG_SOAK_PROMPT,
        ]

        start = time.monotonic()
        result = run_subprocess(
            command,
            cwd=str(tmp_path),
            env=opencode_long_env,
            timeout=900.0,
            runner_name="opencode",
            scenario="opencode_long_runtime_live",
        )
        elapsed = time.monotonic() - start

        if result.returncode != 0:
            stderr_lower = result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(
                    f"opencode auth failure: {result.stderr[:200]}", allow_module_level=True
                )

        result.assert_success("long-runtime opencode subprocess failed")
        assert "LONG_SOAK_OK" in (result.stdout + result.stderr), result.format_diagnostics()
        # Soft lower bound: confirms this exercised a non-trivial runtime window.
        assert elapsed >= 30.0, (
            f"expected long-running scenario >=30s, got {elapsed:.2f}s\n"
            f"{result.format_diagnostics()}"
        )

    def test_opencode_long_runtime_with_session_resume(
        self,
        tmp_path: Any,
        opencode_long_env: dict[str, str],
    ) -> None:
        """Run a long task, then resume same OpenCode session with another long task."""
        if not is_live_runner_opted_in() or not is_long_live_runner_opted_in():
            pytest.skip("long live suite not opted in", allow_module_level=True)

        preflight = LiveRunnerPreflight("opencode")
        if not preflight.check_binary():
            skip_if_binary_missing("opencode")
        if not preflight.check_auth():
            skip_if_auth_missing("opencode")

        model_args = build_model_override_args("opencode", OPENCODE_MODEL)
        start_cmd = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            str(tmp_path),
            *model_args,
            (
                'Run: python -c "import time; time.sleep(25); print("LONG_RESUME_START_OK")" '
                'Then reply with JSON: {"status":"completed","message":"LONG_RESUME_START_OK"}'
            ),
        ]

        start_result = run_subprocess(
            start_cmd,
            cwd=str(tmp_path),
            env=opencode_long_env,
            timeout=900.0,
            runner_name="opencode",
            scenario="opencode_long_runtime_resume_start",
        )
        if start_result.returncode != 0:
            stderr_lower = start_result.stderr.lower()
            if any(kw in stderr_lower for kw in ["auth", "login", "unauthorized", "session"]):
                pytest.skip(
                    f"opencode auth failure: {start_result.stderr[:200]}",
                    allow_module_level=True,
                )
        start_result.assert_success("initial long runtime command failed")
        session_id = _extract_session_id(start_result.stdout)
        assert session_id, start_result.format_diagnostics()

        resume_cmd = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            str(tmp_path),
            "--session",
            session_id,
            *model_args,
            (
                'Run: python -c "import time; time.sleep(20); print("LONG_RESUME_CONTINUE_OK")" '
                'Then reply with JSON: {"status":"completed","message":"LONG_RESUME_CONTINUE_OK"}'
            ),
        ]

        resume_result = run_subprocess(
            resume_cmd,
            cwd=str(tmp_path),
            env=opencode_long_env,
            timeout=900.0,
            runner_name="opencode",
            scenario="opencode_long_runtime_resume_continue",
        )
        resume_result.assert_success("session resume long runtime command failed")
        assert "LONG_RESUME_CONTINUE_OK" in (resume_result.stdout + resume_result.stderr), (
            resume_result.format_diagnostics()
        )

    def test_long_live_recover_dry_run_not_in_default_loop(
        self,
        tmp_path: Any,
        opencode_long_env: dict[str, str],
    ) -> None:
        """Execute long-lived orch run then verify recover --dry-run succeeds.

        This stays opt-in only and does not enter the default test suite.
        """
        if not is_live_runner_opted_in() or not is_long_live_runner_opted_in():
            pytest.skip("long live suite not opted in", allow_module_level=True)

        preflight = LiveRunnerPreflight("opencode")
        if not preflight.check_binary():
            skip_if_binary_missing("opencode")
        if not preflight.check_auth():
            skip_if_auth_missing("opencode")

        plan = tmp_path / "plan.yaml"
        cfg = tmp_path / "vectl.yaml"
        plan.write_text(
            "\n".join(
                [
                    "version: 1",
                    "project: opencode-long-recover",
                    "phases:",
                    "  - id: core",
                    "    name: Core",
                    "    steps:",
                    "      - id: core.long",
                    "        name: Long",
                    "        status: pending",
                    "        description: |",
                    '          Run python -c "import time; time.sleep(20);"',
                    "          \"print('LONG_RECOVER_OK')\"",
                    "          Then return YAML success.",
                    "        verification: |",
                    "          Command finishes and output includes LONG_RECOVER_OK.",
                ]
            )
        )
        cfg.write_text(
            "\n".join(
                [
                    "orchestration:",
                    "  plan_path: plan.yaml",
                    "  runtime:",
                    "    default_runner: opencode",
                    "    artifact_root: .vectl/runs",
                    "  role_profile_overrides:",
                    "    python-executor:",
                    "      default_runner: opencode",
                ]
            )
        )

        init_cmd = ["git", "init", "-b", "main"]
        run_subprocess(init_cmd, cwd=str(tmp_path), timeout=30.0)
        run_subprocess(["git", "add", "plan.yaml", "vectl.yaml"], cwd=str(tmp_path), timeout=30.0)
        commit_env = dict(opencode_long_env)
        commit_env.update(
            {
                "GIT_AUTHOR_NAME": "long-live",
                "GIT_AUTHOR_EMAIL": "long-live@test",
                "GIT_COMMITTER_NAME": "long-live",
                "GIT_COMMITTER_EMAIL": "long-live@test",
            }
        )
        run_subprocess(
            ["git", "commit", "-m", "init"], cwd=str(tmp_path), env=commit_env, timeout=30.0
        )

        run_cmd = [
            "uv",
            "run",
            "--project",
            "/Users/tefx/Projects/vectl",
            "vectl",
            "orch",
            "run",
            "--json",
        ]
        run_result = run_subprocess(
            run_cmd,
            cwd=str(tmp_path),
            env=opencode_long_env,
            timeout=1200.0,
            runner_name="opencode",
            scenario="opencode_long_live_orch_run",
        )
        run_result.assert_success("long-live orch run failed")

        recover_cmd = [
            "uv",
            "run",
            "--project",
            "/Users/tefx/Projects/vectl",
            "vectl",
            "orch",
            "recover",
            "--latest",
            "--dry-run",
            "--json",
        ]
        recover_result = run_subprocess(
            recover_cmd,
            cwd=str(tmp_path),
            env=opencode_long_env,
            timeout=1200.0,
            runner_name="opencode",
            scenario="opencode_long_live_orch_recover_dry_run",
        )
        recover_result.assert_success("long-live recover --dry-run failed")
        payload = json.loads(recover_result.stdout)
        assert payload.get("success") is True, recover_result.format_diagnostics()
        report = payload.get("recovery_report") or {}
        assert isinstance(report, dict), recover_result.format_diagnostics()
        assert report.get("outcome") in {"recovered", "no_artifacts"}, (
            recover_result.format_diagnostics()
        )
