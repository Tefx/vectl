"""Pytest fixtures for driver tests.

This module provides fixtures for testing the driver contracts.
At least one fixture uses the exact spec format from DRIVER-BLUEPRINT.md.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml


# =============================================================================
# SPEC-FIXTURE CONFORMANCE: Exact driver.yaml format from blueprint
# Source: DRIVER-BLUEPRINT.md lines 401-481
# =============================================================================

DRIVER_YAML_SPEC: dict[str, Any] = {
    "runners": {
        "claude": {
            "command": "claude",
            "args": ["-p", "--output-format", "json", "--dangerously-skip-permissions"],
            "prompt_mode": "stdin",
            "stall_timeout": 300,
            "resume_flag": "--resume",
            "session_id_regex": "^[0-9a-f]{8}-[0-9a-f]{4}-",
            "output_parser": "claude_json",
            "persist_session": True,
        },
        "opencode": {
            "command": "opencode",
            "args": ["run", "--format", "json", "--dir", "{workdir}"],
            "prompt_mode": "stdin",
            "stall_timeout": 600,
            "resume_flag": "--session",
            "session_id_regex": "^ses_[a-z0-9]+",
            "output_parser": "opencode_jsonl",
            "persist_session": True,
        },
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
            "stall_timeout": 600,
            "resume_command": [
                "codex",
                "exec",
                "resume",
                "--json",
                "--dangerously-bypass-approvals-and-sandbox",
            ],
            "session_id_regex": "^[0-9a-f]{8}-",
            "output_parser": "codex_jsonl",
        },
        "gemini": {
            "command": "gemini",
            "args": ["-p", "--output-format", "json", "--approval-mode", "yolo"],
            "prompt_mode": "stdin",
            "stall_timeout": 600,
            "output_parser": "gemini_json",
            "experimental": True,
        },
    },
    "agent_routing": {
        "python-senior": "claude",
        "frontend-engineer": "claude",
        "*-tester": "opencode",
        "*-reviewer": "opencode",
        "*-verifier": "opencode",
        "*-auditor": "opencode",
        "*-planner": "opencode",
    },
    "fallback_runner": "opencode",
    "planner_agent_name": "vectl-planner-slim",
    "orchestration": {
        "max_parallelism": 5,
        "merge_strategy": "squash",
    },
    "session": {
        "reuse_ttl": 300,
        "ttl_overrides": {
            "claude": 600,
        },
    },
    "judge": {
        "runner": "opencode",
        "agent_name": "judge",
        "model": None,
        "structured_output": True,
        "timeout": 60,
        "preflight": True,
        "evidence_validation": True,
        "failure_classification": True,
        "escalation": True,
        "gate_assessment": True,
        "cold_context": True,
        "anomaly": True,
        "skip_preflight_for": ["*.define", "*.gate", "*.verify"],
    },
    "observability": {
        "events_file": ".vectl/driver-events.jsonl",
        "log_level": "INFO",
        "print_progress": True,
        "cost_tracking": True,
    },
}
"""Exact spec-format driver.yaml configuration from DRIVER-BLUEPRINT.md lines 401-481.

This fixture MUST NOT use convenience fields or computed defaults.
It matches the documented driver.yaml structure exactly as specified.

Architecture Reference: DRIVER-BLUEPRINT.md Configuration Schema (driver.yaml)
Docs Reference: docs/DRIVER-ARCHITECTURE.md Section 2.3
"""


@pytest.fixture
def driver_config_yaml() -> str:
    """YAML string matching the exact spec format.

    Source: DRIVER-BLUEPRINT.md lines 401-481
    """
    return yaml.dump(DRIVER_YAML_SPEC, sort_keys=False, default_flow_style=False)


@pytest.fixture
def driver_config_dict() -> dict[str, Any]:
    """Dict matching the exact spec format.

    Source: DRIVER-BLUEPRINT.md lines 401-481
    """
    return DRIVER_YAML_SPEC.copy()


@pytest.fixture
def minimal_driver_config_dict() -> dict[str, Any]:
    """Minimal valid driver config with required fields only.

    Per Architecture doc Section 2.3:
    - runners is required and non-empty
    - judge.runner MUST exist in runners
    - fallback_runner MUST exist in runners
    """
    return {
        "runners": {
            "opencode": {
                "command": "opencode",
            },
        },
        "fallback_runner": "opencode",
        "judge": {
            "runner": "opencode",
        },
    }


@pytest.fixture
def temp_driver_yaml(tmp_path: Path, driver_config_yaml: str) -> Path:
    """Write driver.yaml to a temp file and return the path."""
    yaml_path = tmp_path / "driver.yaml"
    yaml_path.write_text(driver_config_yaml)
    return yaml_path


@pytest.fixture
def invalid_driver_config_missing_runner() -> dict[str, Any]:
    """Invalid config: judge.runner not in runners."""
    return {
        "runners": {
            "claude": {"command": "claude"},
        },
        "fallback_runner": "claude",
        "judge": {
            "runner": "opencode",  # Not in runners!
        },
    }


@pytest.fixture
def invalid_driver_config_missing_fallback() -> dict[str, Any]:
    """Invalid config: fallback_runner not in runners."""
    return {
        "runners": {
            "claude": {"command": "claude"},
        },
        "fallback_runner": "opencode",  # Not in runners!
        "judge": {
            "runner": "claude",
        },
    }
