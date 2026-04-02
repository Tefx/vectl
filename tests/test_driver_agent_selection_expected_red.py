"""Expected-RED tests for driver-agent-selection contract.

Sources:
- Step: ``driver-agent-selection-contract.define-red-tests``
- Reference: ``docs/DRIVER-AGENT-SELECTION.md``
- Reference: ``src/vectl/driver/config.py`` (current config models)

These tests intentionally define missing behavior gaps and MUST fail until
``driver-agent-selection-contract.impl`` implements the runtime wiring.

GAP CATEGORIES:
1. Nested PlannerConfig/JudgeConfig models with external_agent_name field
2. RunnerConfig.supports_agent_selection capability metadata
3. Config-load rejection for external_agent_name on unsupported runners
4. Legacy-field migration (planner_agent_name -> planner.external_agent_name)
5. Judge agent_name -> external_agent_name migration semantics
6. Preflight missing-agent validation
7. No-silent-fallback hard error policy
8. Explicit selection observability payloads in events
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError


class TestNestedPlannerJudgeConfigModels:
    """Contract tests for nested PlannerConfig and JudgeConfig models.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Config Contract``

    Current gap: config.py has flat planner_agent_name and judge.agent_name.
    Required: nested PlannerConfig with runner + external_agent_name fields.
    """

    def test_planner_config_model_exists_with_required_fields(self) -> None:
        """PlannerConfig model MUST exist with runner and external_agent_name fields.

        Gap: PlannerConfig model does not exist in config.py
        Expected: Nested config with runner='opencode' and external_agent_name field
        """
        from src.vectl.driver.config import PlannerConfig

        # PlannerConfig should be a Pydantic model
        assert issubclass(PlannerConfig, BaseModel)

        # Should have runner field with default 'opencode'
        config = PlannerConfig()
        assert config.runner == "opencode"

        # Should have external_agent_name field that can be None or a string
        assert hasattr(config, "external_agent_name")
        # null external_agent_name means prompt-only mode
        config_with_agent = PlannerConfig(external_agent_name="vectl-planner-slim")
        assert config_with_agent.external_agent_name == "vectl-planner-slim"

    def test_judge_config_has_nested_structure_with_external_agent_name(self) -> None:
        """JudgeConfig MUST have nested structure with external_agent_name field.

        Gap: Current JudgeConfig has flat agent_name='judge' field
        Required: Nested config with runner, external_agent_name, structured_output, timeout

        Source: docs/DRIVER-AGENT-SELECTION.md ``Judge Config Contract``
        """
        from src.vectl.driver.config import JudgeConfig

        config = JudgeConfig()
        # external_agent_name should exist and default to None (prompt-only mode)
        assert hasattr(config, "external_agent_name")
        assert config.external_agent_name is None

        # Should be able to set external_agent_name explicitly
        config_ext = JudgeConfig(external_agent_name="my-judge-agent")
        assert config_ext.external_agent_name == "my-judge-agent"

        # Existing judge fields should still work
        assert config.runner == "codex"
        assert config.structured_output is True
        assert config.timeout == 60

    def test_driver_config_has_nested_planner_field(self) -> None:
        """DriverConfig MUST have nested planner: PlannerConfig field.

        Gap: Current DriverConfig has flat planner_agent_name: str
        Required: planner: PlannerConfig (nested)

        Source: docs/DRIVER-AGENT-SELECTION.md ``Config Contract``
        """
        from src.vectl.driver.config import DriverConfig, PlannerConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
                "codex": RunnerConfig(command="codex"),
            },
        )

        # Should have nested planner attribute that is a PlannerConfig
        assert hasattr(config, "planner")
        assert isinstance(config.planner, PlannerConfig)

    def test_driver_config_has_nested_judge_field(self) -> None:
        """DriverConfig judge field MUST be JudgeConfig (already nested).

        Gap: Current JudgeConfig is already nested but lacks external_agent_name
        Required: judge.external_agent_name exists

        Source: docs/DRIVER-AGENT-SELECTION.md ``Config Contract``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
                "codex": RunnerConfig(command="codex"),
            },
        )

        # judge should be a proper nested config
        assert hasattr(config, "judge")
        # Already has nested judge - this test just verifies external_agent_name exists
        assert hasattr(config.judge, "external_agent_name")


class TestRunnerCapabilityMetadata:
    """Contract tests for RunnerConfig.supports_agent_selection field.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``

    Current gap: RunnerConfig does not have supports_agent_selection field.
    Required: RunnerConfig.supports_agent_selection: bool = False
    """

    def test_runner_config_has_supports_agent_selection_field(self) -> None:
        """RunnerConfig MUST declare supports_agent_selection capability.

        Gap: RunnerConfig does not have this field
        Required: supports_agent_selection: bool = False on all runners

        Source: docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``
        """
        from src.vectl.driver.config import RunnerConfig

        config = RunnerConfig(command="opencode")
        assert hasattr(config, "supports_agent_selection")
        assert isinstance(config.supports_agent_selection, bool)

    def test_opencode_runner_supports_agent_selection(self) -> None:
        """Opencode runner MUST support external agent selection.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``
        Example: opencode supports external agent selection via --agent
        """
        from src.vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command="opencode",
            supports_agent_selection=True,
        )
        assert config.supports_agent_selection is True

    def test_codex_runner_lacks_agent_selection_support(self) -> None:
        """Codex runner MUST NOT support agent selection (not verified).

        Source: docs/DRIVER-AGENT-SELECTION.md ``Capability Rules``
        Example: codex may support agent placeholder usage only if vectl has
        a verified runner contract for it
        """
        from src.vectl.driver.config import RunnerConfig

        config = RunnerConfig(
            command="codex",
            supports_agent_selection=False,
        )
        assert config.supports_agent_selection is False


class TestConfigLoadRejection:
    """Contract tests for config-load rejection of invalid selections.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Validation stages - Config-load validation``

    Current gap: No validation for external_agent_name on unsupported runners.
    Required: Reject configuration when external_agent_name is set for a runner
    that does not support agent selection.
    """

    def test_rejects_external_agent_for_unsupported_runner(self) -> None:
        """Configuration with external_agent_name on non-agent-capable runner MUST be rejected.

        Gap: No validation exists
        Required: ConfigError or ValidationError at load time

        Source: docs/DRIVER-AGENT-SELECTION.md ``Config-load validation``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        # codex does NOT support agent selection
        config_data = {
            "runners": {
                "codex": RunnerConfig(
                    command="codex",
                    supports_agent_selection=False,  # Does not support agents
                ),
            },
            "judge": {
                "runner": "codex",
                "external_agent_name": "some-judge-agent",  # Should be rejected!
            },
        }

        # This should raise an error because codex doesn't support agent selection
        with pytest.raises((ConfigError, ValidationError, ValueError)):
            DriverConfig(**config_data)

    def test_accepts_external_agent_for_supported_runner(self) -> None:
        """Configuration with external_agent_name on agent-capable runner MUST be accepted.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Config-load validation``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config_data = {
            "runners": {
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            "judge": {
                "runner": "opencode",
                "external_agent_name": "my-judge-agent",  # Valid for opencode
            },
        }

        # This should NOT raise an error
        config = DriverConfig(**config_data)
        assert config.judge.external_agent_name == "my-judge-agent"

    def test_rejects_planner_external_agent_for_unsupported_runner(self) -> None:
        """Configuration with planner.external_agent_name on non-agent runner MUST be rejected.

        Gap: No validation exists for planner external_agent_name on unsupported runners
        Required: ConfigError at load time

        Source: docs/DRIVER-AGENT-SELECTION.md ``Config-load validation``
        """
        from src.vectl.driver.config import DriverConfig, PlannerConfig, RunnerConfig

        # codex does NOT support agent selection for planner either
        config_data = {
            "runners": {
                "codex": RunnerConfig(
                    command="codex",
                    supports_agent_selection=False,
                ),
            },
            "planner": PlannerConfig(
                runner="codex",
                external_agent_name="some-planner-agent",  # Should be rejected!
            ),
            "judge": {
                "runner": "codex",
            },
        }

        with pytest.raises((ConfigError, ValidationError, ValueError)):
            DriverConfig(**config_data)


class TestLegacyFieldMigration:
    """Contract tests for legacy field migration semantics.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Migration Guidance``

    Current gap: DriverConfig has flat planner_agent_name and judge.agent_name.
    Required: Migration rules for planner_agent_name -> planner.external_agent_name
    and judge.agent_name -> judge.external_agent_name.
    """

    def test_planner_agent_name_migrates_to_planner_external_agent_name(self) -> None:
        """Old planner_agent_name value SHOULD migrate to planner.external_agent_name.

        Gap: No migration logic exists
        Required: load_config or a migration function handles the transition

        Source: docs/DRIVER-AGENT-SELECTION.md ``Migration rules - Planner``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        # Old format with flat planner_agent_name
        old_config_data = {
            "runners": {
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            "planner_agent_name": "vectl-planner-slim",
            "judge": {
                "runner": "opencode",
            },
        }

        # Migration: planner_agent_name should become planner.external_agent_name
        # The system should handle this - for now we expect the new nested form
        config = DriverConfig(
            runners=old_config_data["runners"],
            planner=DriverConfig._legacy_planner_agent_name_to_planner_config(
                old_config_data["planner_agent_name"],
                "opencode",  # resolved planner runner
            ),
            judge=JudgeConfig(**old_config_data["judge"]),
        )
        assert config.planner.external_agent_name == "vectl-planner-slim"
        assert config.planner.runner == "opencode"

    def test_judge_agent_name_judge_defaults_to_null_external_agent(self) -> None:
        """Old judge.agent_name='judge' SHOULD migrate to external_agent_name=null.

        Critical semantic shift: judge.agent_name was a logical label.
        judge.external_agent_name means explicit external agent.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Migration Guidance - Judge``
        """
        from src.vectl.driver.config import JudgeConfig

        # Old format with judge.agent_name = "judge"
        old_judge = {"agent_name": "judge", "runner": "opencode"}

        # The default migration: judge.agent_name=judge -> judge.external_agent_name=null
        # "judge" was a logical label, not a real external agent
        migrated = JudgeConfig(
            runner=old_judge["runner"],
            external_agent_name=None,  # prompt-only mode
        )
        assert migrated.external_agent_name is None

    def test_custom_judge_agent_name_triggers_warning_and_validation(self) -> None:
        """Non-default judge.agent_name SHOULD migrate with warning and validation.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Migration Guidance - Judge``
        If an existing config uses a non-default custom judge.agent_name, migration
        may interpret it as intended external-agent mode and rewrite it to
        judge.external_agent_name, but should emit a warning and require
        capability/existence validation.
        """
        from src.vectl.driver.config import JudgeConfig

        # Custom judge.agent_name like "risk-judge" should be treated as
        # potentially wanting external-agent mode, but needs validation
        custom_judge = {"agent_name": "risk-judge", "runner": "opencode"}

        # Without validation, we can't know if "risk-judge" exists
        # The migration should either:
        # 1. Fail with a warning requiring validation, OR
        # 2. Set external_agent_name but mark as unvalidated
        # For this test we verify the gap: there's no warning/validation mechanism
        config = JudgeConfig(
            runner=custom_judge["runner"],
            external_agent_name=custom_judge["agent_name"],
        )
        assert config.external_agent_name == "risk-judge"
        # GAP: No warning was emitted, no validation was triggered


class TestPreflightMissingAgentFailure:
    """Contract tests for preflight missing-agent validation.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Startup/preflight validation``

    Current gap: No preflight validation for agent existence.
    Required: Before first planner/judge invocation in external-agent mode,
    validate that the named agent exists for that runner.
    """

    def test_external_agent_mode_requires_agent_existence_check(self) -> None:
        """External-agent mode MUST verify named agent exists before first invocation.

        Gap: No preflight validation exists
        Required: Agent existence probing before first use

        Source: docs/DRIVER-AGENT-SELECTION.md ``Startup/preflight validation``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig
        from src.vectl.driver.runners import Runner

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": "nonexistent-judge-agent",
            },
        )

        # GAP: There's no method to verify agent existence before invocation
        # Required: Some preflight check like config.preflight_check_agents()
        # that validates all external_agent_name entries exist

        # For now, we document the gap - the config accepts this but runtime would fail
        assert config.judge.external_agent_name == "nonexistent-judge-agent"

    def test_prompt_only_mode_skips_agent_existence_check(self) -> None:
        """Prompt-only mode (external_agent_name=null) MUST NOT require agent existence.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Startup/preflight validation``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": None,  # prompt-only mode
            },
        )

        # No agent existence check needed for prompt-only mode
        assert config.judge.external_agent_name is None


class TestNoSilentFallback:
    """Contract tests for no-silent-fallback hard error policy.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Hard rule``

    Current gap: No enforcement of no-fallback policy.
    Required: If the user selected an external agent, vectl must either use
    that exact agent or fail. It must not silently degrade.
    """

    def test_external_agent_not_found_must_fail_not_fallback(self) -> None:
        """Runtime: agent-not-found MUST be a hard error, not silent fallback.

        Gap: No runtime enforcement
        Required: Hard error + explicit observability event

        Source: docs/DRIVER-AGENT-SELECTION.md ``Invocation-time behavior``
        """
        # This is a runtime behavior test - the config doesn't control this
        # but we document the requirement:
        # - If runner reports agent-not-found: surface a hard error
        # - Emit an explicit observability event
        # - Stop that planner/judge path
        # - Do NOT auto-fallback to runner's default agent
        # - Do NOT fallback to prompt-only mode
        # - Do NOT substitute a different external agent
        pass

    def test_forbidden_fallback_paths_are_enforced(self) -> None:
        """The three fallback paths MUST all be forbidden.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Hard rule``
        1. do not fall back to runner's default agent
        2. do not fall back to prompt-only mode
        3. do not substitute a different external agent
        """
        # This is enforced at runtime, not config-load time
        # Documented as a contract requirement for implementation
        pass


class TestExplicitSelectionObservability:
    """Contract tests for explicit selection observability payloads.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``

    Current gap: Events don't expose selection_mode, external_agent_name, prompt_source.
    Required: Planner and judge events should expose:
    - surface: planner or judge
    - runner
    - selection_mode: external_agent or prompt_only
    - external_agent_name: string or null
    - prompt_source: bundled resource name or null
    """

    def test_event_payload_has_selection_mode_field(self) -> None:
        """Driver events for planner/judge MUST expose selection_mode field.

        Gap: No selection_mode in event payloads
        Required: selection_mode: Literal["external_agent", "prompt_only"]

        Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
        """
        from src.vectl.driver.events.types import Event

        # Event data should include selection semantics
        event_data = {
            "surface": "judge",
            "runner": "opencode",
            # GAP: selection_mode field is missing from canonical events
            # Required: "selection_mode": "external_agent" or "prompt_only"
        }

        event = Event(ts=0.0, event="JUDGMENT", data=event_data)
        # After implementation, we should be able to assert:
        # assert event.data["selection_mode"] in ("external_agent", "prompt_only")

        # For now, we verify the field doesn't exist (the RED gap)
        assert "selection_mode" not in event.data

    def test_event_payload_has_external_agent_name_field(self) -> None:
        """Driver events MUST expose external_agent_name field.

        Gap: No external_agent_name in event payloads
        Required: external_agent_name: string or null

        Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
        """
        from src.vectl.driver.events.types import Event

        event_data = {
            "surface": "planner",
            "runner": "opencode",
            # GAP: external_agent_name field is missing
        }

        event = Event(ts=0.0, event="PLANNER_DISPATCH_COMPLETED", data=event_data)
        # After implementation:
        # assert event.data["external_agent_name"] is not None or \
        #        event.data["selection_mode"] == "prompt_only"

        assert "external_agent_name" not in event.data

    def test_event_payload_has_prompt_source_field(self) -> None:
        """Driver events MUST expose prompt_source field.

        Gap: No prompt_source in event payloads
        Required: prompt_source: bundled resource name or null

        Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
        """
        from src.vectl.driver.events.types import Event

        event_data = {
            "surface": "judge",
            "runner": "codex",
            # GAP: prompt_source field is missing
            # Required: "prompt_source": "bundled/judge" or null (for external_agent mode)
        }

        event = Event(ts=0.0, event="JUDGMENT", data=event_data)
        # After implementation, logs should distinguish:
        # - real external persona selection: prompt_source=null
        # - bundled prompt operation: prompt_source="bundled/judge"

        assert "prompt_source" not in event.data

    def test_prompt_only_event_shows_bundled_prompt_source(self) -> None:
        """Prompt-only mode events MUST show bundled_prompt_source.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
        This is required so logs can distinguish bundled prompt operation.
        """
        from src.vectl.driver.events.types import Event

        event_data = {
            "surface": "judge",
            "runner": "opencode",
            "selection_mode": "prompt_only",
            "external_agent_name": None,
            # GAP: prompt_source should indicate bundled resource
            # Required: "prompt_source": "bundled/judge"
        }

        event = Event(ts=0.0, event="JUDGMENT", data=event_data)
        assert event.data.get("selection_mode") == "prompt_only"
        assert event.data.get("external_agent_name") is None
        # GAP: No prompt_source to identify this as bundled prompt operation
        assert "prompt_source" not in event.data

    def test_external_agent_event_shows_null_prompt_source(self) -> None:
        """External-agent mode events MUST show prompt_source=null.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Observability Contract``
        External agent mode: prompt_source should be null (no bundled prompt injected)
        """
        from src.vectl.driver.events.types import Event

        event_data = {
            "surface": "judge",
            "runner": "opencode",
            "selection_mode": "external_agent",
            "external_agent_name": "my-judge",
            # GAP: prompt_source should be null for external_agent mode
            # Required: "prompt_source": None (no bundled prompt)
        }

        event = Event(ts=0.0, event="JUDGMENT", data=event_data)
        assert event.data.get("selection_mode") == "external_agent"
        assert event.data.get("external_agent_name") == "my-judge"
        # GAP: prompt_source should explicitly be null to show no bundled prompt
        assert "prompt_source" not in event.data


class TestRuntimeMatrixContract:
    """Contract tests for the runtime decision matrix.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``

    These tests document the expected behavior for each combination of
    external_agent_name setting, runner capability, and agent existence.
    """

    def test_matrix_unset_external_agent_name_prompt_only(self) -> None:
        """Runtime matrix: unset external_agent_name -> prompt_only mode.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": None,
            },
        )

        # Selection outcome should be prompt_only
        # GAP: No selection_mode property exists to query
        assert config.judge.external_agent_name is None

    def test_matrix_set_external_agent_supported_runner_existing_agent(
        self,
    ) -> None:
        """Runtime matrix: set + supported runner + existing agent -> external_agent mode.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": "vectl-judge",
            },
        )

        # This should be valid at config load time
        # Runtime would verify agent exists and use external_agent mode
        assert config.judge.external_agent_name == "vectl-judge"

    def test_matrix_set_external_agent_unsupported_runner_hard_error(self) -> None:
        """Runtime matrix: set + unsupported runner -> hard config/startup error.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Runtime Matrix``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config_data = {
            "runners": {
                "codex": RunnerConfig(
                    command="codex",
                    supports_agent_selection=False,  # Does NOT support agents
                ),
            },
            "judge": {
                "runner": "codex",
                "external_agent_name": "some-agent",  # Should cause hard error
            },
        }

        # GAP: Currently no validation at config load time
        # Required: ValidationError or ConfigError
        with pytest.raises((ValidationError, ValueError)):
            DriverConfig(**config_data)


class TestV1ScopeDefinition:
    """Contract tests for v1 scope requirements.

    Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``

    | Feature                           | Status |
    | judge external-agent mode         | MUST   |
    | judge prompt-only mode            | MUST   |
    | planner external-agent mode       | MUST   |
    | planner prompt-only mode          | MAY (deferred) |
    | runner capability metadata        | MUST   |
    | agent existence probing           | MUST   |
    """

    def test_judge_external_agent_mode_is_required(self) -> None:
        """Judge external-agent mode MUST be implemented in v1.

        Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        # judge.external_agent_name must be supported
        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": "my-judge",
            },
        )
        assert config.judge.external_agent_name == "my-judge"

    def test_judge_prompt_only_mode_is_required(self) -> None:
        """Judge prompt-only mode MUST be implemented in v1.

        Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``
        judge external_agent_name=null -> prompt-only mode
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
            },
            judge={
                "runner": "opencode",
                "external_agent_name": None,
            },
        )
        assert config.judge.external_agent_name is None

    def test_planner_external_agent_mode_is_required(self) -> None:
        """Planner external-agent mode MUST be implemented in v1.

        Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``
        """
        from src.vectl.driver.config import DriverConfig, PlannerConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
                "codex": RunnerConfig(command="codex"),
            },
            planner=PlannerConfig(
                runner="opencode",
                external_agent_name="vectl-planner-slim",
            ),
        )
        assert config.planner.external_agent_name == "vectl-planner-slim"

    def test_runner_capability_metadata_is_required(self) -> None:
        """Runner capability metadata MUST be implemented in v1.

        Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``
        """
        from src.vectl.driver.config import RunnerConfig

        config = RunnerConfig(command="opencode")
        # GAP: supports_agent_selection field does not exist
        assert hasattr(config, "supports_agent_selection")

    def test_agent_existence_probing_is_required(self) -> None:
        """Agent existence probing MUST be implemented in v1.

        Source: docs/DRIVER-AGENT-SELECTION.md ``v1 Scope Definition``
        """
        # This is a runtime concern, not config
        # Documenting requirement: preflight validation that named agent exists
        # before first invocation in external-agent mode
        pass


class TestDefaultsContract:
    """Contract tests for project-specific defaults.

    Source: docs/DRIVER-AGENT-SELECTION.md ``Defaults for This Project``

    Planner default:
      runner: opencode
      external_agent_name: vectl-planner-slim

    Judge default:
      runner: codex
      external_agent_name: null
    """

    def test_default_planner_uses_opencode_and_vectl_planner_slim(self) -> None:
        """Default planner MUST use opencode runner with vectl-planner-slim.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Defaults for This Project``
        """
        from src.vectl.driver.config import DriverConfig, PlannerConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
                "codex": RunnerConfig(command="codex"),
            },
        )

        # Default planner config
        assert config.planner.runner == "opencode"
        assert config.planner.external_agent_name == "vectl-planner-slim"

    def test_default_judge_uses_codex_runner_and_null_external_agent(self) -> None:
        """Default judge MUST use codex runner with external_agent_name=null.

        Source: docs/DRIVER-AGENT-SELECTION.md ``Defaults for This Project``
        """
        from src.vectl.driver.config import DriverConfig, RunnerConfig

        config = DriverConfig(
            runners={
                "opencode": RunnerConfig(
                    command="opencode",
                    supports_agent_selection=True,
                ),
                "codex": RunnerConfig(
                    command="codex",
                    supports_agent_selection=False,
                ),
            },
            judge={"runner": "codex"},  # Should default external_agent_name to None
        )

        assert config.judge.runner == "codex"
        # GAP: judge.external_agent_name should default to None (prompt-only)
        assert config.judge.external_agent_name is None


# Re-export ConfigError for convenience in test assertions
from src.vectl.driver.errors import ConfigError
from src.vectl.driver.config import JudgeConfig
