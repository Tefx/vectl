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

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .errors import ConfigError

AgentSelectionMode = Literal["prompt_only", "external_agent"]
AgentSelectionSurfaceName = Literal["planner", "judge"]


@dataclass(frozen=True)
class AgentSelectionSurfaceContract:
    """Authoritative nested config contract for one driver surface.

    Source:
    - docs/DRIVER-AGENT-SELECTION.md ``Config Contract``
    - docs/DRIVER-AGENT-SELECTION.md ``Runtime Semantics``
    - docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``

    Boundary-only notes:
    - This contract pins the intended public shape without migrating runtime
      behavior in this step.
    - ``legacy_field`` names the field slated for migration away from the
      current flat/logical configuration surface.
    """

    surface: AgentSelectionSurfaceName
    intended_model_name: str
    runner_field: str
    external_agent_name_field: str
    structured_output_field: str | None
    timeout_field: str | None
    prompt_only_when: str
    external_agent_when: str
    bundled_prompt_allowed_in_external_mode: bool
    bundled_prompt_required_in_prompt_only_mode: bool
    default_runner: str
    default_external_agent_name: str | None
    legacy_field: str
    v1_prompt_only_status: str
    authority_reference: str


@dataclass(frozen=True)
class DriverAgentSelectionConfigContract:
    """Pinned driver-wide selection contract and migration semantics.

    Source:
    - docs/DRIVER-AGENT-SELECTION.md ``Decision Summary``
    - docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``
    - docs/DRIVER-AGENT-SELECTION.md ``Fallback and Error Policy``
    - docs/DRIVER-AGENT-SELECTION.md ``Migration Guidance``
    - docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``
    """

    contract_id: str
    source_step_id: str
    docs_sections: tuple[str, ...]
    shared_mode_rule: str
    forbidden_public_fields: tuple[str, ...]
    config_load_rejections: tuple[str, ...]
    startup_rejections: tuple[str, ...]
    invocation_failures: tuple[str, ...]
    hard_no_fallback_policy: tuple[str, ...]
    migration_semantics: tuple[str, ...]
    runtime_matrix_outcomes: tuple[str, ...]


PLANNER_AGENT_SELECTION_CONTRACT: Final[AgentSelectionSurfaceContract] = (
    AgentSelectionSurfaceContract(
        surface="planner",
        intended_model_name="PlannerConfig",
        runner_field="planner.runner",
        external_agent_name_field="planner.external_agent_name",
        structured_output_field=None,
        timeout_field=None,
        prompt_only_when="planner.external_agent_name is absent or null",
        external_agent_when="planner.external_agent_name is non-empty",
        bundled_prompt_allowed_in_external_mode=False,
        bundled_prompt_required_in_prompt_only_mode=True,
        default_runner="opencode",
        default_external_agent_name="vectl-planner-slim",
        legacy_field="planner_agent_name",
        v1_prompt_only_status=(
            "deferred; explicit null should be rejected until planner prompt-only ships"
        ),
        authority_reference="docs/DRIVER-AGENT-SELECTION.md#planner",
    )
)

JUDGE_AGENT_SELECTION_CONTRACT: Final[AgentSelectionSurfaceContract] = (
    AgentSelectionSurfaceContract(
        surface="judge",
        intended_model_name="JudgeConfig",
        runner_field="judge.runner",
        external_agent_name_field="judge.external_agent_name",
        structured_output_field="judge.structured_output",
        timeout_field="judge.timeout",
        prompt_only_when="judge.external_agent_name is absent or null",
        external_agent_when="judge.external_agent_name is non-empty",
        bundled_prompt_allowed_in_external_mode=False,
        bundled_prompt_required_in_prompt_only_mode=True,
        default_runner="codex",
        default_external_agent_name=None,
        legacy_field="judge.agent_name",
        v1_prompt_only_status="must ship",
        authority_reference="docs/DRIVER-AGENT-SELECTION.md#judge",
    )
)

DRIVER_AGENT_SELECTION_CONFIG_CONTRACT: Final[DriverAgentSelectionConfigContract] = (
    DriverAgentSelectionConfigContract(
        contract_id="driver-agent-selection-config-v1",
        source_step_id="driver-agent-selection-contract.pin-contract",
        docs_sections=(
            "Decision Summary",
            "Config Contract",
            "Capability Rules",
            "Fallback and Error Policy",
            "Migration Guidance",
            "Runtime Matrix",
            "v1 Scope Definition",
        ),
        shared_mode_rule=(
            "planner and judge use the same rule: external_agent_name present => "
            "external_agent mode; absent/null => prompt_only mode; no hybrid mode"
        ),
        forbidden_public_fields=("mode", "prompt_profile"),
        config_load_rejections=(
            "planner.runner missing from runners",
            "judge.runner missing from runners",
            "external_agent_name set for runner lacking supports_agent_selection",
            "planner.external_agent_name explicitly null in v1 before planner prompt-only ships",
        ),
        startup_rejections=(
            "external-agent mode must verify named agent exists before first invocation",
            "if existence cannot be verified reliably, fail closed",
        ),
        invocation_failures=(
            "agent-not-found from runner is a hard error",
            "unsupported-agent behavior from runner is a hard error",
        ),
        hard_no_fallback_policy=(
            "do not fall back to runner default agent",
            "do not fall back to prompt-only mode",
            "do not substitute a different external agent",
        ),
        migration_semantics=(
            "planner_agent_name -> planner.external_agent_name with resolved planner.runner",
            "absent/null planner_agent_name -> planner.external_agent_name null",
            "judge.agent_name=judge migrates to judge.external_agent_name null",
            "non-default judge.agent_name may migrate to "
            "judge.external_agent_name with warning and validation",
        ),
        runtime_matrix_outcomes=(
            "unset/null external_agent_name => prompt_only runtime outcome",
            "set external_agent_name + supported runner + existing agent => "
            "external_agent runtime outcome",
            "set external_agent_name + unsupported runner => hard config/startup error",
            "set external_agent_name + missing agent => hard startup/runtime error",
        ),
    )
)


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
    agent_name: str = "judge"
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
    planner_agent_name: str = "vectl-planner"
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

    @model_validator(mode="after")
    def _validate_no_agent_routing_default(self) -> DriverConfig:
        """Reject agent_routing.default as it is not a supported routing key.

        Default routing is exclusively handled by fallback_runner, not by a
        reserved routing key in agent_routing.

        Contract: docs/contracts/driver-routing-hardening-contract.yaml
        """
        if "default" in self.agent_routing:
            raise ValueError(
                "agent_routing.default is not supported. Remove agent_routing.default "
                "and use explicit exact/glob entries in agent_routing plus fallback_runner "
                "for default routing behavior."
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
