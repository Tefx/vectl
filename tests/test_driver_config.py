"""Test contracts for driver config.

These test stubs verify the configuration contracts defined in config.py.
Each test is a contract placeholder that will be filled during implementation.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.3
Blueprint Reference: DRIVER-BLUEPRINT.md Configuration Schema (driver.yaml)
"""

import pytest
from pathlib import Path


class TestRunnerConfig:
    """Contract tests for RunnerConfig model."""

    def test_runner_config_required_fields(self) -> None:
        """RunnerConfig MUST require: command.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        raise NotImplementedError("Contract: RunnerConfig.command is required")

    def test_runner_config_defaults(self) -> None:
        """RunnerConfig defaults: args=[], prompt_mode='stdin', stall_timeout=300,
        persist_session=True, output_parser='claude_json', experimental=False.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, runners section
        """
        raise NotImplementedError("Contract: RunnerConfig field defaults")

    def test_runner_config_output_parser_values(self) -> None:
        """RunnerConfig.output_parser MUST be one of:
        'claude_json' | 'opencode_jsonl' | 'codex_jsonl' | 'gemini_json'.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema
        """
        raise NotImplementedError("Contract: RunnerConfig.output_parser enum values")

    def test_runner_config_prompt_mode_values(self) -> None:
        """RunnerConfig.prompt_mode MUST be 'stdin' | 'stdin_dash'.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
        """
        raise NotImplementedError("Contract: RunnerConfig.prompt_mode enum values")


class TestSessionConfig:
    """Contract tests for SessionConfig model."""

    def test_session_config_defaults(self) -> None:
        """SessionConfig defaults: reuse_ttl=300, ttl_overrides={}.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, session section
        """
        raise NotImplementedError("Contract: SessionConfig field defaults")


class TestJudgeConfig:
    """Contract tests for JudgeConfig model."""

    def test_judge_config_defaults(self) -> None:
        """JudgeConfig defaults:
        runner='opencode', model=None, structured_output=True, timeout=60,
        preflight=True, evidence_validation=True, failure_classification=True,
        escalation=True, gate_assessment=True, cold_context=True, anomaly=True.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, judge section
        """
        raise NotImplementedError("Contract: JudgeConfig field defaults")

    def test_judge_config_all_bool_flags(self) -> None:
        """JudgeConfig MUST have boolean flags for all judgment type toggles.

        Judgment types: preflight, evidence_validation, failure_classification,
        escalation, gate_assessment, cold_context, anomaly.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Decision Architecture (Tier 3)
        """
        raise NotImplementedError("Contract: JudgeConfig judgment type flags are bool")

    def test_judge_config_skip_preflight_for_type(self) -> None:
        """JudgeConfig.skip_preflight_for MUST be list[str] defaulting to [].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, skip_preflight_for
        """
        raise NotImplementedError("Contract: JudgeConfig.skip_preflight_for is list[str]")


class TestOrchestrationConfig:
    """Contract tests for OrchestrationConfig model."""

    def test_orchestration_config_defaults(self) -> None:
        """OrchestrationConfig defaults: max_parallelism=5, merge_strategy='squash'.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, orchestration section
        """
        raise NotImplementedError("Contract: OrchestrationConfig field defaults")


class TestObservabilityConfig:
    """Contract tests for ObservabilityConfig model."""

    def test_observability_config_defaults(self) -> None:
        """ObservabilityConfig defaults:
        events_file='.vectl/driver-events.jsonl', log_level='INFO',
        print_progress=True, cost_tracking=True.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, observability section
        """
        raise NotImplementedError("Contract: ObservabilityConfig field defaults")


class TestDriverConfig:
    """Contract tests for DriverConfig model."""

    def test_driver_config_required_runners(self) -> None:
        """DriverConfig.runners MUST be dict[str, RunnerConfig] and non-empty.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema
        """
        raise NotImplementedError("Contract: DriverConfig.runners is dict[str, RunnerConfig]")

    def test_driver_config_defaults(self) -> None:
        """DriverConfig defaults:
        plan_path=None, agent_routing={}, fallback_runner='opencode',
        orchestration=OrchestrationConfig(), session=SessionConfig(),
        judge=JudgeConfig(), observability=ObservabilityConfig().

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        raise NotImplementedError("Contract: DriverConfig field defaults")

    def test_driver_config_route_agent_fallback(self) -> None:
        """DriverConfig.route_agent(agent) MUST fall back to fallback_runner
        when no routing match found.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, fallback_runner
        """
        raise NotImplementedError("Contract: DriverConfig.route_agent fallback behavior")

    def test_driver_config_route_agent_glob(self) -> None:
        """DriverConfig.route_agent(agent) MUST support glob patterns via fnmatch.

        Example: '*-tester' matches 'python-tester', 'backend-tester', etc.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, agent_routing
        """
        raise NotImplementedError("Contract: DriverConfig.route_agent glob matching")

    def test_driver_config_invariant_judge_runner(self) -> None:
        """DriverConfig MUST validate that judge.runner exists in runners.

        Raises ConfigError if judge.runner not in runners dict.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Invariant: DriverConfig.runners MUST contain the runner referenced by
        DriverConfig.judge.runner.
        """
        raise NotImplementedError("Contract: DriverConfig judge.runner validation")

    def test_driver_config_invariant_fallback_runner(self) -> None:
        """DriverConfig MUST validate that fallback_runner exists in runners.

        Raises ConfigError if fallback_runner not in runners dict.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Invariant: DriverConfig.fallback_runner MUST reference a key in
        DriverConfig.runners.
        """
        raise NotImplementedError("Contract: DriverConfig fallback_runner validation")


class TestLoadConfig:
    """Contract tests for load_config function."""

    def test_load_config_yaml_parse(self) -> None:
        """load_config(path) MUST parse YAML and validate with Pydantic.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (config = load_config(config_path))
        """
        raise NotImplementedError("Contract: load_config YAML parsing")

    def test_load_config_missing_file(self) -> None:
        """load_config(path) MUST raise ConfigError for missing file.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        raise NotImplementedError("Contract: load_config missing file error")

    def test_load_config_invalid_yaml(self) -> None:
        """load_config(path) MUST raise ConfigError for invalid YAML.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        raise NotImplementedError("Contract: load_config invalid YAML error")

    def test_load_config_validation_error(self) -> None:
        """load_config(path) MUST raise ConfigError for Pydantic validation failure.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        raise NotImplementedError("Contract: load_config validation error")
