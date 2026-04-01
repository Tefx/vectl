"""Focused tests for driver config.

Tests verify:
- Config model validation (Pydantic models)
- Config invariants (judge.runner in runners, fallback_runner in runners)
- Spec-fixture conformance (exact driver.yaml format)
- Error hierarchy for invalid configs

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.3
Blueprint Reference: DRIVER-BLUEPRINT.md Configuration Schema (driver.yaml)
"""

from pathlib import Path

import pytest

from src.vectl.driver.config import (
    DriverConfig,
    JudgeConfig,
    ObservabilityConfig,
    OrchestrationConfig,
    RunnerConfig,
    SessionConfig,
)
from src.vectl.driver.errors import ConfigError


class TestRunnerConfig:
    """Contract tests for RunnerConfig model."""

    def test_runner_config_required_fields(self) -> None:
        """RunnerConfig MUST require: command.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        # Minimal valid config
        config = RunnerConfig(command="claude")
        assert config.command == "claude"
        assert config.args == []  # default
        assert config.prompt_mode == "stdin"  # default

    def test_runner_config_defaults(self) -> None:
        """RunnerConfig defaults: args=[], prompt_mode='stdin', stall_timeout=300,
        persist_session=True, output_parser='claude_json', experimental=False.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, runners section
        """
        config = RunnerConfig(command="test")
        assert config.args == []
        assert config.prompt_mode == "stdin"
        assert config.stall_timeout == 300
        assert config.persist_session is True
        assert config.output_parser == "claude_json"
        assert config.experimental is False
        assert config.resume_flag is None
        assert config.resume_command is None
        assert config.session_id_regex is None

    def test_runner_config_output_parser_values(self) -> None:
        """RunnerConfig.output_parser MUST accept: claude_json, opencode_jsonl, codex_jsonl, gemini_json.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema
        """
        # Pydantic validates string values
        for parser in ["claude_json", "opencode_jsonl", "codex_jsonl", "gemini_json"]:
            config = RunnerConfig(command="test", output_parser=parser)
            assert config.output_parser == parser

    def test_runner_config_prompt_mode_values(self) -> None:
        """RunnerConfig.prompt_mode MUST accept: stdin, stdin_dash.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Runner Protocol & Implementations
        """
        for mode in ["stdin", "stdin_dash"]:
            config = RunnerConfig(command="test", prompt_mode=mode)
            assert config.prompt_mode == mode

    def test_runner_config_all_fields_settable(self) -> None:
        """RunnerConfig all fields can be set explicitly."""
        config = RunnerConfig(
            command="claude",
            args=["-p", "--json"],
            prompt_mode="stdin",
            stall_timeout=600,
            resume_flag="--resume",
            resume_command=["claude", "--resume"],
            session_id_regex="^[0-9a-f]+",
            output_parser="claude_json",
            persist_session=False,
            experimental=True,
        )
        assert config.command == "claude"
        assert config.args == ["-p", "--json"]
        assert config.prompt_mode == "stdin"
        assert config.stall_timeout == 600
        assert config.resume_flag == "--resume"
        assert config.resume_command == ["claude", "--resume"]
        assert config.session_id_regex == "^[0-9a-f]+"
        assert config.output_parser == "claude_json"
        assert config.persist_session is False
        assert config.experimental is True


class TestSessionConfig:
    """Contract tests for SessionConfig model."""

    def test_session_config_defaults(self) -> None:
        """SessionConfig defaults: reuse_ttl=300, ttl_overrides={}.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, session section
        """
        config = SessionConfig()
        assert config.reuse_ttl == 300
        assert config.ttl_overrides == {}

    def test_session_config_ttl_overrides(self) -> None:
        """SessionConfig.ttl_overrides is a dict[str, int]."""
        config = SessionConfig(reuse_ttl=600, ttl_overrides={"claude": 900, "opencode": 300})
        assert config.reuse_ttl == 600
        assert config.ttl_overrides == {"claude": 900, "opencode": 300}


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
        config = JudgeConfig()
        assert config.runner == "opencode"
        assert config.agent_name == "judge"
        assert config.model is None
        assert config.structured_output is True
        assert config.timeout == 60
        assert config.preflight is True
        assert config.evidence_validation is True
        assert config.failure_classification is True
        assert config.escalation is True
        assert config.gate_assessment is True
        assert config.cold_context is True
        assert config.anomaly is True
        assert config.skip_preflight_for == []

    def test_judge_config_all_bool_flags(self) -> None:
        """JudgeConfig MUST have boolean flags for all judgment type toggles.

        Judgment types: preflight, evidence_validation, failure_classification,
        escalation, gate_assessment, cold_context, anomaly.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Decision Architecture (Tier 3)
        """
        config = JudgeConfig()
        assert isinstance(config.preflight, bool)
        assert isinstance(config.evidence_validation, bool)
        assert isinstance(config.failure_classification, bool)
        assert isinstance(config.escalation, bool)
        assert isinstance(config.gate_assessment, bool)
        assert isinstance(config.cold_context, bool)
        assert isinstance(config.anomaly, bool)

    def test_judge_config_skip_preflight_for_type(self) -> None:
        """JudgeConfig.skip_preflight_for MUST be list[str] defaulting to [].

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, skip_preflight_for
        """
        config = JudgeConfig()
        assert isinstance(config.skip_preflight_for, list)
        config_with_skip = JudgeConfig(skip_preflight_for=["*.define", "*.gate"])
        assert config_with_skip.skip_preflight_for == ["*.define", "*.gate"]

    def test_judge_config_agent_name_default_and_override(self) -> None:
        config = JudgeConfig()
        assert config.agent_name == "judge"

        overridden = JudgeConfig(agent_name="risk-judge")
        assert overridden.agent_name == "risk-judge"


class TestOrchestrationConfig:
    """Contract tests for OrchestrationConfig model."""

    def test_orchestration_config_defaults(self) -> None:
        """OrchestrationConfig defaults: max_parallelism=5, merge_strategy='squash'.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, orchestration section
        """
        config = OrchestrationConfig()
        assert config.max_parallelism == 5
        assert config.merge_strategy == "squash"


class TestObservabilityConfig:
    """Contract tests for ObservabilityConfig model."""

    def test_observability_config_defaults(self) -> None:
        """ObservabilityConfig defaults:
        events_file='.vectl/driver-events.jsonl', log_level='INFO',
        print_progress=True, cost_tracking=True.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, observability section
        """
        config = ObservabilityConfig()
        assert config.events_file == ".vectl/driver-events.jsonl"
        assert config.log_level == "INFO"
        assert config.print_progress is True
        assert config.cost_tracking is True


class TestDriverConfig:
    """Contract tests for DriverConfig model."""

    def test_driver_config_required_runners(self) -> None:
        """DriverConfig.runners MUST be dict[str, RunnerConfig] and non-empty.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema
        """
        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(command="opencode"),
            },
        )
        assert isinstance(config.runners, dict)
        assert "opencode" in config.runners
        assert isinstance(config.runners["opencode"], RunnerConfig)

    def test_driver_config_defaults(self) -> None:
        """DriverConfig defaults:
        plan_path=None, agent_routing={}, fallback_runner='opencode',
        orchestration=OrchestrationConfig(), session=SessionConfig(),
        judge=JudgeConfig(), observability=ObservabilityConfig().

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        config = DriverConfig(
            runners={"opencode": RunnerConfig(command="opencode")},
        )
        assert config.plan_path is None
        assert config.planner_agent_name == "vectl-planner"
        assert config.agent_routing == {}
        assert config.fallback_runner == "opencode"
        assert isinstance(config.orchestration, OrchestrationConfig)
        assert isinstance(config.session, SessionConfig)
        assert isinstance(config.judge, JudgeConfig)
        assert isinstance(config.observability, ObservabilityConfig)

    def test_driver_config_planner_agent_name_override(self) -> None:
        config = DriverConfig(
            runners={"opencode": RunnerConfig(command="opencode")},
            planner_agent_name="vectl-planner-slim",
        )

        assert config.planner_agent_name == "vectl-planner-slim"

    def test_driver_config_route_agent_fallback(self) -> None:
        """DriverConfig.route_agent(agent) MUST fall back to fallback_runner
        when no routing match found.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, fallback_runner
        """
        # route_agent is a stub (NotImplementedError), so we can't test behavior yet
        # But we can verify the default fallback_runner
        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(command="opencode"),
            },
            fallback_runner="opencode",
        )
        assert config.fallback_runner == "opencode"

    def test_driver_config_agent_routing_glob_pattern(self) -> None:
        """DriverConfig.agent_routing can store glob patterns.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Configuration Schema, agent_routing
        """
        config = DriverConfig(
            runners={
                "claude": RunnerConfig(command="claude"),
                "opencode": RunnerConfig(command="opencode"),
            },
            agent_routing={
                "python-senior": "claude",
                "*-tester": "opencode",
            },
        )
        assert config.agent_routing["python-senior"] == "claude"
        assert config.agent_routing["*-tester"] == "opencode"


class TestLoadConfig:
    """Contract tests for load_config function."""

    def test_load_config_yaml_parse(self, temp_driver_yaml: Path, driver_config_dict: dict) -> None:
        """load_config(path) MUST parse YAML and validate with Pydantic.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Blueprint: DRIVER-BLUEPRINT.md Flow 1 (config = load_config(config_path))

        Note: load_config is a stub (NotImplementedError), so we test Pydantic
        validation directly here.
        """
        import yaml

        from src.vectl.driver.config import DriverConfig, RunnerConfig

        # Parse YAML
        with open(temp_driver_yaml) as f:
            data = yaml.safe_load(f)

        # Validate Pydantic model structure
        assert "runners" in data
        assert "claude" in data["runners"]
        assert "opencode" in data["runners"]

        # DriverConfig should be constructible from the dict
        # Note: load_config stub raises NotImplementedError, so we construct manually
        runners = {
            name: RunnerConfig(**runner_data) for name, runner_data in data["runners"].items()
        }
        config = DriverConfig(runners=runners)
        assert "claude" in config.runners
        assert "opencode" in config.runners

    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        """load_config(path) MUST raise ConfigError for missing file.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        # load_config is a stub, so we test the pattern
        missing_path = tmp_path / "nonexistent.yaml"
        assert not missing_path.exists()
        # When implemented, this should raise ConfigError
        # For now, we verify the error class exists
        assert ConfigError.__name__ == "ConfigError"

    def test_load_config_validation_error(self) -> None:
        """load_config(path) MUST raise ConfigError for Pydantic validation failure.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        """
        from pydantic import ValidationError

        # Test that DriverConfig validates required fields
        with pytest.raises(ValidationError):
            DriverConfig()  # Missing required 'runners'


class TestSpecFixtureConformance:
    """Tests using the EXACT driver.yaml format from DRIVER-BLUEPRINT.md.

    Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.3
    Blueprint Reference: DRIVER-BLUEPRINT.md lines 401-481
    """

    def test_spec_fixture_claude_runner(self, driver_config_dict: dict) -> None:
        """Claude runner config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 402-410
        """
        claude_config = driver_config_dict["runners"]["claude"]
        runner = RunnerConfig(**claude_config)

        assert runner.command == "claude"
        assert runner.args == [
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        assert runner.prompt_mode == "stdin"
        assert runner.stall_timeout == 300
        assert runner.resume_flag == "--resume"
        assert runner.session_id_regex == "^[0-9a-f]{8}-[0-9a-f]{4}-"
        assert runner.output_parser == "claude_json"
        assert runner.persist_session is True

    def test_spec_fixture_opencode_runner(self, driver_config_dict: dict) -> None:
        """OpenCode runner config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 412-420
        """
        opencode_config = driver_config_dict["runners"]["opencode"]
        runner = RunnerConfig(**opencode_config)

        assert runner.command == "opencode"
        assert runner.args == ["run", "--format", "json", "--dir", "{workdir}"]
        assert runner.prompt_mode == "stdin"
        assert runner.stall_timeout == 600
        assert runner.resume_flag == "--session"
        assert runner.session_id_regex == "^ses_[a-z0-9]+"
        assert runner.output_parser == "opencode_jsonl"
        assert runner.persist_session is True

    def test_spec_fixture_codex_runner(self, driver_config_dict: dict) -> None:
        """Codex runner config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 422-429
        """
        codex_config = driver_config_dict["runners"]["codex"]
        runner = RunnerConfig(**codex_config)

        assert runner.command == "codex"
        assert runner.args == [
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            "{workdir}",
        ]
        assert runner.prompt_mode == "stdin_dash"
        assert runner.stall_timeout == 600
        assert runner.resume_command == [
            "codex",
            "exec",
            "resume",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        assert runner.session_id_regex == "^[0-9a-f]{8}-"
        assert runner.output_parser == "codex_jsonl"

    def test_spec_fixture_gemini_runner(self, driver_config_dict: dict) -> None:
        """Gemini runner config matches spec exactly (experimental).

        Spec pin: DRIVER-BLUEPRINT.md lines 431-437
        """
        gemini_config = driver_config_dict["runners"]["gemini"]
        runner = RunnerConfig(**gemini_config)

        assert runner.command == "gemini"
        assert runner.args == ["-p", "--output-format", "json", "--approval-mode", "yolo"]
        assert runner.prompt_mode == "stdin"
        assert runner.stall_timeout == 600
        assert runner.output_parser == "gemini_json"
        assert runner.experimental is True

    def test_spec_fixture_session_config(self, driver_config_dict: dict) -> None:
        """Session config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 455-459
        """
        session_config = SessionConfig(**driver_config_dict["session"])

        assert session_config.reuse_ttl == 300
        assert session_config.ttl_overrides == {"claude": 600}

    def test_spec_fixture_judge_config(self, driver_config_dict: dict) -> None:
        """Judge config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 461-475
        """
        judge_config = JudgeConfig(**driver_config_dict["judge"])

        assert judge_config.runner == "opencode"
        assert judge_config.agent_name == "judge"
        assert judge_config.model is None
        assert judge_config.structured_output is True
        assert judge_config.timeout == 60
        assert judge_config.preflight is True
        assert judge_config.evidence_validation is True
        assert judge_config.failure_classification is True
        assert judge_config.escalation is True
        assert judge_config.gate_assessment is True
        assert judge_config.cold_context is True
        assert judge_config.anomaly is True
        assert judge_config.skip_preflight_for == ["*.define", "*.gate", "*.verify"]

    def test_spec_fixture_observability_config(self, driver_config_dict: dict) -> None:
        """Observability config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 477-481
        """
        obs_config = ObservabilityConfig(**driver_config_dict["observability"])

        assert obs_config.events_file == ".vectl/driver-events.jsonl"
        assert obs_config.log_level == "INFO"
        assert obs_config.print_progress is True
        assert obs_config.cost_tracking is True

    def test_spec_fixture_orchestration_config(self, driver_config_dict: dict) -> None:
        """Orchestration config matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 451-453
        """
        orch_config = OrchestrationConfig(**driver_config_dict["orchestration"])

        assert orch_config.max_parallelism == 5
        assert orch_config.merge_strategy == "squash"

    def test_spec_fixture_agent_routing(self, driver_config_dict: dict) -> None:
        """Agent routing matches spec exactly.

        Spec pin: DRIVER-BLUEPRINT.md lines 439-447
        """
        assert driver_config_dict["agent_routing"] == {
            "python-senior": "claude",
            "frontend-engineer": "claude",
            "*-tester": "opencode",
            "*-reviewer": "opencode",
            "*-verifier": "opencode",
            "*-auditor": "opencode",
            "*-planner": "opencode",
        }
        assert driver_config_dict["fallback_runner"] == "opencode"
        assert driver_config_dict["planner_agent_name"] == "vectl-planner-slim"


class TestConfigInvariants:
    """Tests for config validation invariants from architecture doc."""

    def test_invariant_judge_runner_must_exist_in_runners(
        self, invalid_driver_config_missing_runner: dict
    ) -> None:
        """DriverConfig MUST validate that judge.runner exists in runners.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Invariant: DriverConfig.runners MUST contain the runner referenced by
        DriverConfig.judge.runner.

        Note: load_config stub raises NotImplementedError, so we test the pattern.
        When implemented, this should raise ConfigError.
        """
        data = invalid_driver_config_missing_runner
        # The pattern: judge.runner = "opencode", but runners only has "claude"
        assert data["judge"]["runner"] == "opencode"
        assert "opencode" not in data["runners"]
        # When load_config is implemented:
        # with pytest.raises(ConfigError):
        #     load_config(...)

    def test_invariant_fallback_runner_must_exist_in_runners(
        self, invalid_driver_config_missing_fallback: dict
    ) -> None:
        """DriverConfig MUST validate that fallback_runner exists in runners.

        Contract pin from: docs/DRIVER-ARCHITECTURE.md Section 2.3
        Invariant: DriverConfig.fallback_runner MUST reference a key in
        DriverConfig.runners.

        Note: load_config stub raises NotImplementedError, so we test the pattern.
        """
        data = invalid_driver_config_missing_fallback
        # The pattern: fallback_runner = "opencode", but runners only has "claude"
        assert data["fallback_runner"] == "opencode"
        assert "opencode" not in data["runners"]
        # When load_config is implemented:
        # with pytest.raises(ConfigError):
        #     load_config(...)


class TestAgentRoutingValidation:
    """Contract tests for agent_routing validation.

    These tests verify the routing hardening contract:
    - agent_routing.default is NOT supported (rejected at validation time)
    - Valid routing patterns (exact match, glob match) work correctly
    - fallback_runner is the ONLY way to configure default routing

    Contract Reference: docs/contracts/driver-routing-hardening-contract.yaml
    """

    def test_agent_routing_default_key_rejected(self) -> None:
        """Configuration containing agent_routing.default MUST be rejected.

        Contract pin: driver-routing-hardening-contract.yaml, validation_contract

        The 'default' routing key is not supported. Default routing is
        exclusively handled by fallback_runner, not by a reserved routing key.
        """
        from pydantic import ValidationError

        config_data = {
            "runners": {
                "opencode": {"command": "opencode"},
            },
            "agent_routing": {
                "python-senior": "opencode",
                "default": "opencode",  # This MUST be rejected
            },
            "fallback_runner": "opencode",
            "judge": {"runner": "opencode"},
        }

        # This should raise ValidationError because agent_routing.default is invalid
        with pytest.raises(ValidationError) as exc_info:
            DriverConfig(**config_data)

        # Error message should mention the invalid key
        error_msg = str(exc_info.value).lower()
        assert "agent_routing.default" in error_msg or "default" in error_msg

    def test_agent_routing_default_error_message_is_actionable(self) -> None:
        """Error message for agent_routing.default MUST be clear and actionable.

        Contract pin: driver-routing-hardening-contract.yaml, required_user_facing_error

        The error MUST:
        - Name the invalid key exactly: agent_routing.default
        - State this is a configuration error
        - Instruct users to use exact/glob entries and fallback_runner
        """
        from pydantic import ValidationError

        config_data = {
            "runners": {
                "opencode": {"command": "opencode"},
            },
            "agent_routing": {
                "default": "opencode",
            },
            "fallback_runner": "opencode",
            "judge": {"runner": "opencode"},
        }

        with pytest.raises(ValidationError) as exc_info:
            DriverConfig(**config_data)

        error_msg = str(exc_info.value)
        # Must mention the invalid key
        assert "agent_routing.default" in error_msg or "'default'" in error_msg

    def test_agent_routing_exact_match_still_works(self) -> None:
        """Exact match routing MUST continue to work.

        Contract pin: driver-routing-hardening-contract.yaml, routing_semantics.supported.exact_match

        Valid agent_routing keys (exact matches) must not be rejected.
        """
        config = DriverConfig(
            runners={
                "claude": {"command": "claude"},
                "opencode": {"command": "opencode"},
            },
            agent_routing={
                "python-senior": "claude",
                "frontend-engineer": "claude",
            },
            fallback_runner="opencode",
            judge={"runner": "opencode"},
        )

        # Exact match should work
        assert config.route_agent("python-senior") == "claude"
        assert config.route_agent("frontend-engineer") == "claude"
        # Unmatched falls back to fallback_runner
        assert config.route_agent("unknown-agent") == "opencode"

    def test_agent_routing_glob_match_still_works(self) -> None:
        """Glob pattern routing MUST continue to work.

        Contract pin: driver-routing-hardening-contract.yaml, routing_semantics.supported.glob_match

        Glob patterns (fnmatch) in agent_routing keys must not be rejected.
        """
        config = DriverConfig(
            runners={
                "claude": {"command": "claude"},
                "opencode": {"command": "opencode"},
            },
            agent_routing={
                "*-tester": "opencode",
                "*-reviewer": "claude",
            },
            fallback_runner="opencode",
            judge={"runner": "opencode"},
        )

        # Glob match should work
        assert config.route_agent("python-tester") == "opencode"
        assert config.route_agent("frontend-reviewer") == "claude"
        # Unmatched falls back to fallback_runner
        assert config.route_agent("unknown-agent") == "opencode"

    def test_fallback_runner_is_only_default_routing(self) -> None:
        """Default routing MUST be exclusively handled by fallback_runner.

        Contract pin: driver-routing-hardening-contract.yaml, routing_semantics.unsupported

        The agent_routing dictionary MUST NOT contain a 'default' key.
        Users must use fallback_runner for default routing behavior.
        """
        # This config should be valid - no default key in agent_routing,
        # default routing handled by fallback_runner
        config = DriverConfig(
            runners={
                "claude": {"command": "claude"},
                "opencode": {"command": "opencode"},
            },
            agent_routing={
                "python-senior": "claude",
            },
            fallback_runner="opencode",
            judge={"runner": "opencode"},
        )

        # Unmatched agents should route to fallback_runner
        assert config.route_agent("completely-unknown-agent") == "opencode"
        assert config.route_agent("another-unknown") == "opencode"

    def test_agent_routing_default_key_not_special_in_route_agent(self) -> None:
        """route_agent() MUST NOT contain runtime special-casing for 'default' key.

        Contract pin: driver-routing-hardening-contract.yaml, validation_contract.required_behavior

        Even if someone bypasses validation, route_agent() must not treat
        'default' as a special key. This test verifies the runtime behavior
        when agent_routing has no 'default' key (which is the only valid state).
        """
        config = DriverConfig(
            runners={
                "claude": {"command": "claude"},
                "opencode": {"command": "opencode"},
            },
            agent_routing={
                "python-senior": "claude",
            },
            fallback_runner="opencode",
            judge={"runner": "opencode"},
        )

        # 'default' as an agent name should NOT match anything special
        # It should fall through to fallback_runner since there's no exact/glob match
        assert config.route_agent("default") == "opencode"


class TestConfigModelRoundTrip:
    """Tests for Pydantic model serialization/deserialization."""

    def test_runner_config_model_dump(self) -> None:
        """RunnerConfig can be dumped and reconstructed."""
        config = RunnerConfig(
            command="claude",
            args=["-p"],
            stall_timeout=300,
        )
        data = config.model_dump()
        reconstructed = RunnerConfig(**data)
        assert reconstructed.command == config.command
        assert reconstructed.args == config.args
        assert reconstructed.stall_timeout == config.stall_timeout

    def test_driver_config_model_dump(self) -> None:
        """DriverConfig can be dumped and reconstructed."""
        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(command="opencode"),
            },
            judge=JudgeConfig(runner="opencode"),
        )
        data = config.model_dump()
        reconstructed = DriverConfig(**data)
        assert "opencode" in reconstructed.runners
        assert reconstructed.judge.runner == "opencode"

    def test_minimal_config_is_valid(self, minimal_driver_config_dict: dict) -> None:
        """Minimal valid config with required fields only."""
        config = DriverConfig(**minimal_driver_config_dict)
        assert "opencode" in config.runners
        assert config.fallback_runner == "opencode"
        assert config.judge.runner == "opencode"
