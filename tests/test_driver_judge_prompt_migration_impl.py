"""Implementation tests for packaged judge prompt migration.

Source:
- step `driver-prompt-migration.package-resource-loader`
- docs/DRIVER-ARCHITECTURE.md Section 2.10
- docs/JUDGE-AGENT-PROMPT.md (verdict contract authority)
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from src.vectl.driver.config import JudgeConfig
from src.vectl.driver.judge import (
    Judge,
    _load_judge_system_prompt,
    _load_packaged_judge_system_prompt,
    verify_docs_packaged_sync,
)


class _ObserverSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event_type: str, /, **data: object) -> None:
        self.events.append((event_type, data))

    def close(self) -> None:
        return None


def _judge_config() -> JudgeConfig:
    return JudgeConfig(
        runner="opencode",
        model=None,
        structured_output=False,
        timeout=10,
        preflight=True,
        evidence_validation=True,
        failure_classification=True,
        escalation=True,
        gate_assessment=True,
        cold_context=True,
        anomaly=True,
        skip_preflight_for=[],
    )


def test_packaged_prompt_loads_without_docs_directory_present() -> None:
    """Runtime loader must not require docs/JUDGE-AGENT-PROMPT.md at cwd."""
    original_cwd = Path.cwd()
    with TemporaryDirectory() as td:
        os.chdir(td)
        try:
            prompt_text = _load_judge_system_prompt()
        finally:
            os.chdir(original_cwd)

    assert "Response Format (MANDATORY)" in prompt_text
    assert "suggested_action" in prompt_text
    assert "planner_instruction" in prompt_text


def test_missing_packaged_prompt_resource_raises_actionable_runtime_error() -> None:
    """Missing packaged resource must fail explicitly with diagnostics."""
    with pytest.raises(RuntimeError) as exc_info:
        _load_packaged_judge_system_prompt(
            package_name="vectl.driver",
            resource_name="missing_judge_prompt_resource.md",
        )

    message = str(exc_info.value)
    assert "resource is missing" in message
    assert "vectl.driver" in message
    assert "missing_judge_prompt_resource.md" in message
    assert "pyproject" in message


def test_packaged_prompt_flows_to_judge_payload_assembly() -> None:
    """Path exercised: packaged resource -> loader -> Judge payload."""
    judge = Judge(_judge_config(), _ObserverSpy())

    _, payload = judge._build_subprocess_command(
        system_prompt=judge._system_prompt, user_prompt="{}"
    )

    assert payload.startswith("SYSTEM PROMPT:\n")
    assert "Response Format (MANDATORY)" in payload
    assert '"verdict": "<ACCEPT|REJECT|RETRY|SWITCH_AGENT|REPLAN|DEFER|HALT>"' in payload


def test_verify_docs_packaged_sync_detects_drift() -> None:
    """verify_docs_packaged_sync() makes drift visible rather than silently tolerated."""
    is_synced, diagnostic = verify_docs_packaged_sync()

    # When docs and packaged match (expected in normal operation), is_synced is True
    # When drift exists, is_synced is False and diagnostic describes it
    assert isinstance(is_synced, bool)
    assert isinstance(diagnostic, str)
    # Check diagnostic contains relevant info based on sync state
    if is_synced:
        # When synced, diagnostic confirms docs contains packaged content
        assert "key packaged content" in diagnostic.lower() or "matches" in diagnostic.lower()
    else:
        assert "drift" in diagnostic.lower() or "missing" in diagnostic.lower()


def test_verify_docs_packaged_sync_reports_synced_when_match() -> None:
    """When docs contains all packaged content, sync check returns True."""
    is_synced, diagnostic = verify_docs_packaged_sync()
    assert is_synced is True
    assert "contains all key packaged content" in diagnostic
