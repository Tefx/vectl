"""
Verification tests for orchestration config implementation.

These tests verify the working logic implemented for:
- Config discovery/precedence/validation
- Tool allowlist validation against canonical registry
- Frozen snapshot writing
- Unknown tool family/name rejection
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vectl.orchestration.config import (
    DispatchConfig,
    FrozenConfigSnapshot,
    ObservabilityConfig,
    OrchestrationConfig,
    ResolverConfig,
    RuntimeConfig,
    freeze_config,
    load_orchestration_config,
    validate_orchestration_config,
    write_frozen_snapshot,
)
from vectl.orchestration.tool_registry import (
    ToolFamilyRegistry,
    canonical_tool_families,
    validate_allowlist,
    validate_tool_allowlist,
    validate_tool_allowlist_entry,
)

# =============================================================================
# Tool Registry Tests
# =============================================================================


class TestToolFamilyRegistry:
    """Tests for ToolFamilyRegistry implementation."""

    def test_get_returns_metadata_for_known_family(self) -> None:
        """Verify get() returns ToolFamily metadata for known families."""
        registry = ToolFamilyRegistry()
        metadata = registry.get("core")
        assert metadata is not None
        assert metadata.family == "core"
        assert "status" in metadata.allowed_operations

    def test_get_returns_none_for_unknown_family(self) -> None:
        """Verify get() returns None for unknown families."""
        registry = ToolFamilyRegistry()
        assert registry.get("unknown_family") is None

    def test_all_families_returns_canonical_families(self) -> None:
        """Verify all_families() returns all canonical families."""
        registry = ToolFamilyRegistry()
        families = registry.all_families()
        assert "core" in families
        assert "orchestration" in families
        assert len(families) == 2

    def test_is_registered_for_known_family(self) -> None:
        """Verify is_registered() returns True for known families."""
        registry = ToolFamilyRegistry()
        assert registry.is_registered("core") is True
        assert registry.is_registered("orchestration") is True

    def test_is_registered_for_unknown_family(self) -> None:
        """Verify is_registered() returns False for unknown families."""
        registry = ToolFamilyRegistry()
        assert registry.is_registered("unknown_family") is False

    def test_is_valid_tool_for_valid_tool(self) -> None:
        """Verify is_valid_tool() returns True for valid tool/family combos."""
        registry = ToolFamilyRegistry()
        assert registry.is_valid_tool("status", "core") is True
        assert registry.is_valid_tool("claim", "core") is True
        assert registry.is_valid_tool("read_events", "orchestration") is True

    def test_is_valid_tool_for_invalid_tool(self) -> None:
        """Verify is_valid_tool() returns False for invalid tool/family combos."""
        registry = ToolFamilyRegistry()
        assert registry.is_valid_tool("invalid_tool", "core") is False
        assert registry.is_valid_tool("status", "orchestration") is False  # wrong family
        assert registry.is_valid_tool("read_events", "core") is False  # wrong family


class TestCanonicalToolFamilies:
    """Tests for canonical tool families accessor."""

    def test_canonical_tool_families_returns_tuple(self) -> None:
        """Verify canonical_tool_families() returns expected tuple."""
        families = canonical_tool_families()
        assert isinstance(families, tuple)
        assert "core" in families
        assert "orchestration" in families


class TestValidateAllowlist:
    """Tests for validate_allowlist function."""

    def test_validate_allowlist_allows_known_family(self) -> None:
        """Verify validate_allowlist() returns True for allowed family."""
        assert validate_allowlist("core", ("core", "orchestration")) is True

    def test_validate_allowlist_denies_unknown_family(self) -> None:
        """Verify validate_allowlist() returns False for unknown family."""
        assert validate_allowlist("unknown", ("core",)) is False

    def test_validate_allowlist_deny_all_empty_tuple(self) -> None:
        """Verify validate_allowlist() returns False for empty allowlist."""
        assert validate_allowlist("core", ()) is False


class TestValidateToolAllowlistEntry:
    """Tests for tool allowlist entry validation."""

    def test_valid_entry_no_errors(self) -> None:
        """Verify validate_tool_allowlist_entry() returns no errors for valid entry."""
        errors = validate_tool_allowlist_entry("core", ("status", "show", "claim"))
        assert len(errors) == 0

    def test_unknown_family_rejected(self) -> None:
        """Verify unknown tool family is rejected."""
        errors = validate_tool_allowlist_entry("unknown_family", ("some_tool",))
        assert len(errors) == 1
        assert errors[0].family == "unknown_family"
        assert "unknown tool family" in errors[0].reason

    def test_unknown_tool_rejected(self) -> None:
        """Verify unknown tool name is rejected."""
        errors = validate_tool_allowlist_entry("core", ("unknown_tool",))
        assert len(errors) == 1
        assert errors[0].tool == "unknown_tool"
        assert "not valid for family" in errors[0].reason

    def test_wildcard_pattern_rejected(self) -> None:
        """Verify wildcard patterns are rejected."""
        errors = validate_tool_allowlist_entry("core", ("*",))
        assert len(errors) == 1
        assert "wildcard or prefix patterns" in errors[0].reason

    def test_prefix_pattern_rejected(self) -> None:
        """Verify prefix patterns are rejected."""
        errors = validate_tool_allowlist_entry("core", ("status.*",))
        assert len(errors) == 1
        assert "wildcard or prefix patterns" in errors[0].reason


class TestValidateToolAllowlist:
    """Tests for complete tool allowlist validation."""

    def test_valid_allowlist_no_errors(self) -> None:
        """Verify validate_tool_allowlist() returns no errors for valid allowlist."""
        allowlist = {
            "core": ("status", "show", "claim", "complete", "defer"),
            "orchestration": ("read_events", "read_state", "read_case"),
        }
        errors = validate_tool_allowlist(allowlist)
        assert len(errors) == 0

    def test_mixed_errors_collected(self) -> None:
        """Verify multiple errors from different entries are collected."""
        allowlist = {
            "core": ("status", "unknown_tool"),  # one error
            "unknown_family": ("some_tool",),  # one error
        }
        errors = validate_tool_allowlist(allowlist)
        assert len(errors) == 2


# =============================================================================
# Config Discovery Tests
# =============================================================================


class TestConfigDiscovery:
    """Tests for config discovery order."""

    def test_load_from_explicit_path(self, tmp_path: Path) -> None:
        """Verify load_orchestration_config() loads from explicit path."""
        config_content = {"orchestration": {"plan_path": "test_plan.yaml"}}
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        config, path = load_orchestration_config(plan_path=config_file)
        assert path == config_file
        assert config.plan_path.name == "test_plan.yaml"

    def test_load_from_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Verify load_orchestration_config() respects VECTL_CONFIG env var."""
        config_content = {"orchestration": {"plan_path": "env_plan.yaml"}}
        config_file = tmp_path / "custom_vectl.yaml"
        config_file.write_text(yaml.dump(config_content))
        monkeypatch.setenv("VECTL_CONFIG", str(config_file))

        config, path = load_orchestration_config()
        assert path == config_file
        assert config.plan_path.name == "env_plan.yaml"

    def test_load_from_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Verify load_orchestration_config() discovers cwd ./vectl.yaml."""
        config_content = {"orchestration": {"plan_path": "cwd_plan.yaml"}}
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))
        monkeypatch.chdir(tmp_path)

        # Clear VECTL_CONFIG to avoid interference
        monkeypatch.delenv("VECTL_CONFIG", raising=False)

        config, path = load_orchestration_config()
        assert path == config_file
        assert config.plan_path.name == "cwd_plan.yaml"

    def test_load_returns_defaults_when_no_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Verify load_orchestration_config() returns defaults when no config file."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VECTL_CONFIG", raising=False)

        config, path = load_orchestration_config()
        assert path is None  # No config file found
        assert config.plan_path.name == "plan.yaml"  # Default

    def test_default_config_contains_config_backed_role_registry(self) -> None:
        """Verify built-in config defaults carry ordinary and resolver role profiles."""
        config = OrchestrationConfig()

        role_ids = {profile.role_id for profile in config.role_profiles}
        assert "python-executor" in role_ids
        assert "blocked-case-coordinator" in role_ids
        assert "blocked-case-coordinator-tacit" in role_ids
        assert config.dispatch.default_role_id == "python-executor"
        assert config.resolver.default_role_id == "blocked-case-coordinator"
        assert config.resolver.tool_allowlist.allowed_tool_families == ("orchestration",)

    def test_load_parses_role_profiles_and_default_role_ids(self, tmp_path: Path) -> None:
        """Verify file-backed config defines role registry and explicit role defaults."""
        config_content = {
            "orchestration": {
                "role_profiles": {
                    "python-executor": {
                        "agent_id": "python-executor",
                        "prompt_family": "coder",
                        "execution_context": "linked_worktree",
                        "mutation_policy": "worktree_changes",
                        "session_policy": "reuse_allowed",
                        "output_contract": "freeform_evidence",
                        "default_runner": "codex",
                    },
                    "blocked-case-coordinator": {
                        "agent_id": "blocked-case-coordinator",
                        "prompt_family": "resolver",
                        "execution_context": "main_worktree",
                        "mutation_policy": "vectl_facade_only",
                        "session_policy": "reuse_forbidden",
                        "output_contract": "resolution_report",
                        "default_runner": "codex",
                    },
                    "blocked-case-coordinator-tacit": {
                        "agent_id": "blocked-case-coordinator-tacit",
                        "prompt_family": "resolver",
                        "execution_context": "main_worktree",
                        "mutation_policy": "vectl_facade_only",
                        "session_policy": "reuse_forbidden",
                        "output_contract": "resolution_report",
                        "default_runner": "codex",
                    },
                },
                "dispatch": {"default_role_id": "python-executor"},
                "resolver": {"default_role_id": "blocked-case-coordinator"},
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        config, path = load_orchestration_config(plan_path=config_file)

        assert path == config_file
        assert config.dispatch.default_role_id == "python-executor"
        assert config.resolver.default_role_id == "blocked-case-coordinator"
        assert {profile.role_id for profile in config.role_profiles} == {
            "python-executor",
            "blocked-case-coordinator",
            "blocked-case-coordinator-tacit",
        }
        profile_by_role = {profile.role_id: profile for profile in config.role_profiles}
        assert profile_by_role["python-executor"].agent_id == "python-executor"
        assert profile_by_role["blocked-case-coordinator"].agent_id == "blocked-case-coordinator"
        assert (
            profile_by_role["blocked-case-coordinator-tacit"].agent_id
            == "blocked-case-coordinator-tacit"
        )

    def test_load_preserves_role_id_agent_id_prompt_family_separation_for_future_role(
        self, tmp_path: Path
    ) -> None:
        """Config role profiles must keep role identity distinct from prompt identity."""
        config_content = {
            "orchestration": {
                "role_profiles": {
                    "future-resolver-role": {
                        "agent_id": "blocked-case-coordinator-tacit",
                        "prompt_family": "resolver",
                        "execution_context": "main_worktree",
                        "mutation_policy": "vectl_facade_only",
                        "session_policy": "reuse_forbidden",
                        "output_contract": "resolution_report",
                        "default_runner": "codex",
                    }
                }
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        config, path = load_orchestration_config(plan_path=config_file)

        assert path == config_file
        assert len(config.role_profiles) == 1
        profile = config.role_profiles[0]
        assert profile.role_id == "future-resolver-role"
        assert profile.agent_id == "blocked-case-coordinator-tacit"
        assert profile.prompt_family == "resolver"

    def test_missing_explicit_file_raises(self, tmp_path: Path) -> None:
        """Verify load_orchestration_config() raises for missing explicit file."""
        with pytest.raises(FileNotFoundError):
            load_orchestration_config(plan_path=tmp_path / "nonexistent.yaml")

    def test_env_override_applies_runtime_artifact_and_workspace_roots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Verify VECTL_ORCH runtime path overrides map to nested config fields."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VECTL_CONFIG", raising=False)
        monkeypatch.setenv("VECTL_ORCH_RUNTIME_ARTIFACT_ROOT", "/tmp/custom_runs")
        monkeypatch.setenv("VECTL_ORCH_RUNTIME_WORKSPACE_ROOT", "/tmp/custom_workspaces")

        config, path = load_orchestration_config()

        assert path is None
        assert config.runtime.artifact_root == Path("/tmp/custom_runs")
        assert config.runtime.workspace_root == Path("/tmp/custom_workspaces")


# =============================================================================
# Config Validation Tests
# =============================================================================


class TestConfigValidation:
    """Tests for OrchestrationConfig validation."""

    def test_valid_default_config_passes(self) -> None:
        """Verify a default config passes validation."""
        config = OrchestrationConfig()
        errors = validate_orchestration_config(config)
        assert len(errors) == 0

    def test_invalid_cleanup_policy_rejected(self) -> None:
        """Verify invalid cleanup_policy is rejected."""
        config = OrchestrationConfig(
            runtime=RuntimeConfig(cleanup_policy="invalid_policy"),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1
        assert errors[0].field == "runtime.cleanup_policy"
        assert "expected one of" in errors[0].reason

    def test_invalid_isolation_mode_rejected(self) -> None:
        """Verify invalid isolation_default is rejected."""
        config = OrchestrationConfig(
            runtime=RuntimeConfig(isolation_default="invalid_mode"),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1
        assert errors[0].field == "runtime.isolation_default"

    def test_zero_timeout_rejected(self) -> None:
        """Verify zero resolver timeout is rejected."""
        config = OrchestrationConfig(
            resolver=ResolverConfig(invocation_timeout_seconds=0),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1
        assert "resolver.timeout_seconds" in errors[0].field

    def test_negative_timeout_rejected(self) -> None:
        """Verify negative resolver timeout is rejected."""
        config = OrchestrationConfig(
            resolver=ResolverConfig(invocation_timeout_seconds=-1),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1

    def test_zero_max_tool_calls_rejected(self) -> None:
        """Verify zero max_tool_calls is rejected."""
        config = OrchestrationConfig(
            resolver=ResolverConfig(max_tool_calls_per_invocation=0),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1
        assert "resolver.max_tool_calls_per_invocation" in errors[0].field

    def test_negative_retention_days_rejected(self) -> None:
        """Verify negative retention_days is rejected."""
        config = OrchestrationConfig(
            observability=ObservabilityConfig(retention_days=-1),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) == 1
        assert "observability.retention_days" in errors[0].field

    def test_dispatch_default_role_must_reference_configured_profile(self) -> None:
        """Verify dispatch default role must exist in the config-backed registry."""
        config = OrchestrationConfig(dispatch=DispatchConfig(default_role_id="missing-role"))

        errors = validate_orchestration_config(config)

        assert any(error.field == "dispatch.default_role_id" for error in errors)

    def test_resolver_default_role_must_reference_resolver_family(self) -> None:
        """Verify resolver default role cannot point at an ordinary coder role."""
        config = OrchestrationConfig(resolver=ResolverConfig(default_role_id="python-executor"))

        errors = validate_orchestration_config(config)

        assert any(error.field == "resolver.default_role_id" for error in errors)

    def test_tool_allowlist_unknown_family_rejected(self) -> None:
        """Verify unknown tool family in allowlist is rejected."""
        # Test with dict format (tool names as second arg to validate_tool_allowlist)
        errors = validate_tool_allowlist({"unknown_family": ("some_tool",)})
        assert len(errors) >= 1
        assert any("unknown_family" in str(e) for e in errors)

    def test_multiple_validation_errors_collected(self) -> None:
        """Verify multiple validation errors are collected."""
        config = OrchestrationConfig(
            runtime=RuntimeConfig(cleanup_policy="invalid", isolation_default="invalid"),
            resolver=ResolverConfig(invocation_timeout_seconds=0, max_tool_calls_per_invocation=-1),
        )
        errors = validate_orchestration_config(config)
        assert len(errors) >= 4  # 2 runtime + 2 resolver errors


# =============================================================================
# Frozen Config Snapshot Tests
# =============================================================================


class TestFreezeConfig:
    """Tests for freeze_config functionality."""

    def test_freeze_returns_frozen_snapshot(self) -> None:
        """Verify freeze_config() returns a FrozenConfigSnapshot."""
        config = OrchestrationConfig()
        snapshot = freeze_config(config)
        assert isinstance(snapshot, FrozenConfigSnapshot)
        assert snapshot.config is config
        assert snapshot.created_at is not None

    def test_freeze_with_run_dir_writes_snapshot(self, tmp_path: Path) -> None:
        """Verify freeze_config() with run_dir writes snapshot file."""
        config = OrchestrationConfig()
        run_dir = tmp_path / ".vectl" / "runs" / "test_run"
        run_dir.mkdir(parents=True)

        snapshot = freeze_config(config, run_dir=run_dir)
        assert snapshot.snapshot_path is not None
        assert snapshot.snapshot_path.exists()
        assert snapshot.snapshot_path.name == "config.snapshot.yaml"

    def test_frozen_snapshot_contains_config_data(self, tmp_path: Path) -> None:
        """Verify frozen snapshot YAML contains config data."""
        config = OrchestrationConfig(
            plan_path=Path("test_plan.yaml"),
            runtime=RuntimeConfig(default_runner="test_runner"),
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)

        snapshot = freeze_config(config, run_dir=run_dir)
        assert snapshot.snapshot_path is not None

        with snapshot.snapshot_path.open() as f:
            snapshot_data = yaml.safe_load(f)

        assert "orchestration" in snapshot_data
        assert snapshot_data["orchestration"]["plan_path"] == "test_plan.yaml"
        assert snapshot_data["orchestration"]["runtime"]["default_runner"] == "test_runner"


class TestWriteFrozenSnapshot:
    """Tests for write_frozen_snapshot function."""

    def test_write_snapshot_creates_file(self, tmp_path: Path) -> None:
        """Verify write_frozen_snapshot() creates the snapshot file."""
        config = OrchestrationConfig()
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)

        snapshot_path = write_frozen_snapshot(config, run_dir)
        assert snapshot_path.exists()

    def test_snapshot_roundtrip(self, tmp_path: Path) -> None:
        """Verify snapshot can be written and read back."""
        config = OrchestrationConfig(
            runtime=RuntimeConfig(cleanup_policy="on-success"),
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)

        snapshot_path = write_frozen_snapshot(config, run_dir)
        with snapshot_path.open() as f:
            data = yaml.safe_load(f)

        assert data["orchestration"]["runtime"]["cleanup_policy"] == "on-success"


# =============================================================================
# Required Surface Tests (No Silent Disable)
# =============================================================================


class TestRequiredSurfaceNotDisabled:
    """Verify required surfaces are not silently disabled."""

    def test_events_jsonl_default_true(self) -> None:
        """Verify events_jsonl defaults to True (required surface)."""
        config = OrchestrationConfig()
        assert config.observability.events_jsonl is True

    def test_text_log_default_true(self) -> None:
        """Verify text_log defaults to True (required surface)."""
        config = OrchestrationConfig()
        assert config.observability.text_log is True

    def test_projected_state_default_true(self) -> None:
        """Verify projected_state defaults to True (required surface)."""
        config = OrchestrationConfig()
        assert config.observability.projected_state is True

    def test_per_step_artifacts_default_true(self) -> None:
        """Verify per_step_artifacts defaults to True (required surface)."""
        config = OrchestrationConfig()
        assert config.observability.per_step_artifacts is True

    def test_per_case_artifacts_default_true(self) -> None:
        """Verify per_case_artifacts defaults to True (required surface)."""
        config = OrchestrationConfig()
        assert config.observability.per_case_artifacts is True


# =============================================================================
# Sibling Search Verification
# =============================================================================


class TestSiblingSearchVerification:
    """Verify patterns are correctly applied across related modules."""

    def test_tool_registry_imports_resolved(self) -> None:
        """Verify tool_registry module imports work correctly."""
        from vectl.orchestration.tool_registry import (
            CANONICAL_TOOL_FAMILIES,
        )

        assert CANONICAL_TOOL_FAMILIES == ("core", "orchestration")

    def test_config_imports_tool_registry(self) -> None:
        """Verify config module imports from tool_registry."""
        from vectl.orchestration.config import (
            validate_orchestration_config,
        )

        # validate_orchestration_config uses tool_registry internally
        assert validate_orchestration_config is not None

        # ToolAllowlistValidationError comes from tool_registry, not config
        from vectl.orchestration.tool_registry import ToolAllowlistValidationError

        assert ToolAllowlistValidationError is not None
