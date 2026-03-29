"""Implementation-focused runtime tests for Judge.

These tests verify implemented behavior without relying on external CLI tools.
They use a fake runner injected via ``Judge._runner_override``.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.10
Blueprint Reference: docs/JUDGE-AGENT-PROMPT.md
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from typing import Any

import pytest
import yaml

from src.vectl.driver.config import JudgeConfig
from src.vectl.driver.errors import JudgmentParseError, JudgmentTimeoutError
from src.vectl.driver.judge import Judge, _extract_verdict_payload
from src.vectl.driver.judgments import JudgmentRequest, JudgmentType


class _ObserverSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return None


class _RunnerSuccess:
    name = "fake-success"

    async def dispatch_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {
            "verdict": "ACCEPT",
            "reason": "Looks good",
            "suggested_action": None,
            "planner_instruction": None,
        }

    async def dispatch_text(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        return ""


class _RunnerMalformed:
    name = "fake-malformed"

    async def dispatch_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {
            "verdict": "ACCEPT",
            "reason": "Missing strict fields",
            # Missing suggested_action/planner_instruction on purpose.
        }

    async def dispatch_text(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        return ""


class _RunnerTimeout:
    name = "fake-timeout"

    async def dispatch_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        await asyncio.sleep(timeout + 0.2)
        return {
            "verdict": "ACCEPT",
            "reason": "late",
            "suggested_action": None,
            "planner_instruction": None,
        }

    async def dispatch_text(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        await asyncio.sleep(timeout + 0.2)
        return ""


def _request_preflight() -> JudgmentRequest:
    return JudgmentRequest(
        type=JudgmentType.PREFLIGHT,
        step_id="core.impl",
        context={
            "step_id": "core.impl",
            "step_description": "Implement feature",
            "step_verification": "Run tests",
            "step_refs": "[]",
        },
        failure_history=[],
        plan_summary="Phase 1",
    )


def _judge_config(*, preflight: bool = True, timeout: int = 1) -> JudgeConfig:
    return JudgeConfig(
        runner="opencode",
        model=None,
        structured_output=True,
        timeout=timeout,
        preflight=preflight,
        evidence_validation=True,
        failure_classification=True,
        escalation=True,
        gate_assessment=True,
        cold_context=True,
        anomaly=True,
        skip_preflight_for=[],
    )


def test_judge_accepts_strict_json_verdict_and_emits_observer_event() -> None:
    observer = _ObserverSpy()
    judge = Judge(_judge_config(preflight=True), observer)
    judge._runner_override = _RunnerSuccess()

    verdict = asyncio.run(judge.judge(_request_preflight()))

    assert verdict.verdict == "ACCEPT"
    assert verdict.reason == "Looks good"
    assert len(observer.events) == 1
    event_type, payload = observer.events[0]
    assert event_type == "JUDGMENT"
    assert payload["type"] == "preflight"
    assert payload["step_id"] == "core.impl"
    assert payload["verdict"] == "ACCEPT"


def test_judge_rejects_malformed_verdict_shape() -> None:
    observer = _ObserverSpy()
    judge = Judge(_judge_config(preflight=True), observer)
    judge._runner_override = _RunnerMalformed()

    with pytest.raises(JudgmentParseError):
        asyncio.run(judge.judge(_request_preflight()))


def test_judge_timeout_path_raises_documented_error() -> None:
    observer = _ObserverSpy()
    judge = Judge(_judge_config(preflight=True, timeout=1), observer)
    judge._runner_override = _RunnerTimeout()

    with pytest.raises(JudgmentTimeoutError) as exc_info:
        asyncio.run(judge.judge(_request_preflight()))

    assert exc_info.value.judgment_type == "preflight"
    assert exc_info.value.step_id == "core.impl"
    assert exc_info.value.timeout_seconds == 1


def test_disabled_judgment_type_is_skipped_cleanly() -> None:
    observer = _ObserverSpy()
    judge = Judge(_judge_config(preflight=False), observer)
    judge._runner_override = _RunnerSuccess()

    verdict = asyncio.run(judge.judge(_request_preflight()))

    assert verdict.verdict == "DEFER"
    assert "disabled" in verdict.reason
    assert len(observer.events) == 1
    event_type, payload = observer.events[0]
    assert event_type == "JUDGMENT"
    assert payload["verdict"] == "DEFER"


def test_extract_verdict_payload_empty_output_raises_parse_error() -> None:
    with pytest.raises(JudgmentParseError):
        _extract_verdict_payload("")


def test_extract_verdict_payload_partial_codex_events_raise_parse_error() -> None:
    partial_stream = """{"type":"thread.started","thread_id":"th-1"}
{"type":"turn.started","turn_id":"turn-1"}"""
    with pytest.raises(JudgmentParseError):
        _extract_verdict_payload(partial_stream)


def test_codex_command_reuses_driver_yaml_runner_config() -> None:
    driver_yaml = {
        "runners": {
            "codex": {
                "command": "codex",
                "args": [
                    "exec",
                    "--json",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-C",
                    "{workdir}",
                ],
                "prompt_mode": "stdin_dash",
            },
            "opencode": {
                "command": "opencode",
                "args": ["run", "--format", "json"],
            },
        },
        "fallback_runner": "opencode",
        "judge": {"runner": "codex"},
    }

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "driver.yaml").write_text(yaml.dump(driver_yaml), encoding="utf-8")
        original_cwd = Path.cwd()
        try:
            # Judge command lookup is cwd-relative by design.
            os.chdir(td_path)
            observer = _ObserverSpy()
            judge = Judge(
                JudgeConfig(
                    runner="codex",
                    structured_output=True,
                    timeout=30,
                ),
                observer,
            )
            command, _ = judge._build_subprocess_command("system", "user")
        finally:
            os.chdir(original_cwd)

    assert command[0:3] == ["codex", "exec", "--json"]
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "-C" in command
    assert "-" in command
    assert "--output-schema" in command
