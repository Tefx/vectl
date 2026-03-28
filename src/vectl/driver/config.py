"""Configuration schema and loader.

Responsibility: Load driver.yaml, validate via Pydantic, provide typed config
to all other modules.

Non-responsibility: Does NOT own runtime state. Does NOT resolve plan paths
(uses vectl.plan_path for that).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.3
Blueprint Reference: DRIVER-BLUEPRINT.md Configuration Schema (driver.yaml)

Dependencies: pydantic, pyyaml, types (if referencing driver types)
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .errors import ConfigError


class RunnerConfig(BaseModel):
    """Configuration for a single runner (CLI agent).

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, RunnerConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, runners section
    """

    command: str
    args: list[str] = Field(default_factory=list)
    prompt_mode: str = "stdin"  # "stdin" | "stdin_dash"
    stall_timeout: int = 300
    resume_flag: str | None = None
    resume_command: list[str] | None = None
    session_id_regex: str | None = None
    output_parser: str = (
        "claude_json"  # "claude_json" | "opencode_jsonl" | "codex_jsonl" | "gemini_json"
    )
    persist_session: bool = True
    experimental: bool = False


class SessionConfig(BaseModel):
    """Session reuse configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, SessionConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, session section
    """

    reuse_ttl: int = 300
    ttl_overrides: dict[str, int] = Field(default_factory=dict)


class JudgeConfig(BaseModel):
    """Judgment agent configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, JudgeConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, judge section
    """

    runner: str = "opencode"  # All sidecar/subagent calls default to opencode
    model: str | None = None
    structured_output: bool = True  # Use model structured output (see Architecture doc)
    timeout: int = 60  # Per-judgment timeout in seconds
    preflight: bool = True  # JudgmentType.PREFLIGHT
    evidence_validation: bool = True  # JudgmentType.EVIDENCE
    failure_classification: bool = True  # JudgmentType.FAILURE
    escalation: bool = True  # JudgmentType.ESCALATION
    gate_assessment: bool = True  # JudgmentType.GATE
    cold_context: bool = True  # JudgmentType.COLD_CONTEXT
    anomaly: bool = True  # JudgmentType.ANOMALY
    skip_preflight_for: list[str] = Field(default_factory=list)


class OrchestrationConfig(BaseModel):
    """Orchestration behavior configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, OrchestrationConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, orchestration section
    """

    max_parallelism: int = 5
    merge_strategy: str = "squash"


class ObservabilityConfig(BaseModel):
    """Observability configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, ObservabilityConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, observability section
    """

    events_file: str = ".vectl/driver-events.jsonl"
    log_level: str = "INFO"
    print_progress: bool = True
    cost_tracking: bool = True


class DriverConfig(BaseModel):
    """Root driver configuration.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, DriverConfig
    Blueprint: DRIVER-BLUEPRINT.md Configuration Schema

    Invariants:
        - DriverConfig.runners MUST contain the runner referenced by
          DriverConfig.judge.runner. Validated at load time; raises
          ConfigError on violation.
        - DriverConfig.fallback_runner MUST reference a key in
          DriverConfig.runners.
    """

    plan_path: str | None = None  # null = auto-detect via resolve_plan_path()
    runners: dict[str, RunnerConfig]
    agent_routing: dict[str, str] = Field(default_factory=dict)
    fallback_runner: str = "opencode"
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    def route_agent(self, agent: str) -> str:
        """Resolve agent name to runner name via agent_routing config.

        Supports exact match and glob patterns (fnmatch).
        Falls back to self.fallback_runner.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, DriverConfig.route_agent
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, agent_routing section
        """
        # First, try exact match
        if agent in self.agent_routing:
            return self.agent_routing[agent]

        # Then, try glob patterns
        for pattern, runner in self.agent_routing.items():
            if fnmatch(agent, pattern):
                return runner

        # Fall back to default runner
        return self.fallback_runner

    @model_validator(mode="after")
    def _validate_runners_exist(self) -> DriverConfig:
        """Validate that judge.runner and fallback_runner exist in runners.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, DriverConfig invariants
        """
        # Check fallback_runner exists
        if self.fallback_runner not in self.runners:
            raise ValueError(
                f"fallback_runner '{self.fallback_runner}' not found in runners. "
                f"Available runners: {list(self.runners.keys())}"
            )

        # Check judge.runner exists
        if self.judge.runner not in self.runners:
            raise ValueError(
                f"judge.runner '{self.judge.runner}' not found in runners. "
                f"Available runners: {list(self.runners.keys())}"
            )

        return self


def load_config(path: Path) -> DriverConfig:
    """Load and validate driver.yaml configuration.

    Args:
        path: Path to driver.yaml

    Returns:
        Validated DriverConfig instance.

    Raises:
        ConfigError: If the configuration is invalid or missing required fields.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.3, config.py loader
    Blueprint: DRIVER-BLUEPRINT.md Flow 1 (config = load_config(config_path))
    """
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in configuration file {path}: {e}") from e

    if data is None:
        raise ConfigError(f"Empty configuration file: {path}")

    if not isinstance(data, dict):
        raise ConfigError(f"Configuration must be a dictionary, got {type(data).__name__}")

    try:
        config = DriverConfig(**data)
    except Exception as e:
        raise ConfigError(f"Invalid configuration in {path}: {e}") from e

    return config
