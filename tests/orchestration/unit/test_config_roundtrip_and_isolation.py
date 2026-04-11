"""
Regression tests for config round-trip serialization symmetry and
deterministic env isolation from home-config bleed.

Authority: vectl step config_role_profile_overrides_impl.fix-roundtrip-and-env-isolation

These tests prove:
1. _config_to_dict / _dict_to_config round-trip preserves RFC §6.2 semantics
   (built-in IDs are serialized as role_profile_overrides, NOT role_profiles).
2. Discovery tests are deterministic regardless of ~/.config/vectl/vectl.yaml.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from vectl.orchestration.config import (
    OrchestrationConfig,
    _config_to_dict,
    _dict_to_config,
    default_role_profiles,
    load_orchestration_config,
    write_frozen_snapshot,
    load_frozen_snapshot,
)
from vectl.orchestration.contracts import RoleProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_plan(plan_path: Path) -> None:
    """Write a minimal valid plan.yaml for test purposes."""
    plan_path.write_text(
        "version: 1\n"
        "name: test-plan\n"
        "phases:\n"
        "  - id: core\n"
        "    name: Core\n"
        "    steps:\n"
        "      - id: core.ready\n"
        "        name: Ready\n"
        "        agent: python-executor\n",
    )


# ---------------------------------------------------------------------------
# Round-trip Regression Tests
# ---------------------------------------------------------------------------


class TestConfigRoundTripSymmetry:
    """Verify that _config_to_dict -> _dict_to_config round-trips cleanly.

    Authority: RFC §6.2 (built-in IDs must NOT appear under role_profiles).

    Root cause: _config_to_dict previously emitted ALL role_profiles (including
    built-ins) under the 'role_profiles' key. But _dict_to_config treats
    'role_profiles' entries as custom roles and rejects built-in IDs. This
    asymmetry caused round-trip failure.
    """

    def test_default_config_roundtrip(self) -> None:
        """Default config round-trips without validation errors."""
        config = OrchestrationConfig()
        d = _config_to_dict(config)

        # Must NOT raise RoleProfileOverrideError
        config2 = _dict_to_config(d)

        # Role profiles must match
        assert config2.role_profiles == config.role_profiles

    def test_default_config_serialized_no_role_profiles_key(self) -> None:
        """Default config (no changes) emits NO role_profiles or role_profile_overrides."""
        config = OrchestrationConfig()
        d = _config_to_dict(config)
        orch = d["orchestration"]

        # All defaults → no overrides, no custom roles
        assert "role_profiles" not in orch
        assert "role_profile_overrides" not in orch

    def test_override_only_config_roundtrip(self) -> None:
        """Config with only overrides round-trips preserving override values."""
        original = default_role_profiles()
        python_exec = original[0]
        modified = replace(python_exec, default_runner="opencode")
        new_profiles = (modified,) + original[1:]
        config = OrchestrationConfig(role_profiles=new_profiles)

        d = _config_to_dict(config)
        # Overrides should be in role_profile_overrides, not role_profiles
        orch = d["orchestration"]
        assert "role_profile_overrides" in orch
        assert orch["role_profile_overrides"]["python-executor"]["default_runner"] == "opencode"
        # No built-in IDs under role_profiles
        assert "role_profiles" not in orch or all(
            rid not in {p.role_id for p in default_role_profiles()}
            for rid in orch.get("role_profiles", {})
        )

        # Round-trip must not raise
        config2 = _dict_to_config(d)
        by_id = {p.role_id: p for p in config2.role_profiles}
        assert by_id["python-executor"].default_runner == "opencode"

    def test_custom_role_only_config_roundtrip(self) -> None:
        """Config with only a custom role round-trips preserving custom role."""
        custom_reviewer = RoleProfile(
            role_id="my-reviewer",
            agent_id="my-reviewer",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="opencode",
        )
        config = OrchestrationConfig(
            role_profiles=default_role_profiles() + (custom_reviewer,),
        )
        d = _config_to_dict(config)
        orch = d["orchestration"]

        # Custom role should be under role_profiles
        assert "role_profiles" in orch
        assert "my-reviewer" in orch["role_profiles"]
        # My-reviewer should NOT be in role_profile_overrides
        assert "my-reviewer" not in orch.get("role_profile_overrides", {})

        # Round-trip must not raise
        config2 = _dict_to_config(d)
        by_id = {p.role_id: p for p in config2.role_profiles}
        assert "my-reviewer" in by_id
        assert by_id["my-reviewer"].default_runner == "opencode"

    def test_override_and_custom_roundtrip(self) -> None:
        """Config with both overrides and custom roles round-trips correctly."""
        original = default_role_profiles()
        modified = replace(original[0], default_runner="opencode")
        custom_reviewer = RoleProfile(
            role_id="my-reviewer",
            agent_id="my-reviewer",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="opencode",
        )
        config = OrchestrationConfig(
            role_profiles=(modified,) + original[1:] + (custom_reviewer,),
        )

        d = _config_to_dict(config)
        config2 = _dict_to_config(d)

        by_id = {p.role_id: p for p in config2.role_profiles}
        assert by_id["python-executor"].default_runner == "opencode"
        assert "my-reviewer" in by_id
        # Unmodified built-ins unchanged
        assert by_id["doc-reviewer"].default_runner == "codex"

    def test_no_builtin_ids_in_serialized_role_profiles(self) -> None:
        """Serialized dict never has built-in IDs under role_profiles.

        This is the core of the round-trip symmetry contract: built-in IDs
        must ONLY appear under role_profile_overrides, never under role_profiles,
        because _dict_to_config rejects them there per RFC §6.2.
        """
        builtin_ids = {p.role_id for p in default_role_profiles()}

        # Test with various config states
        configs_to_test = [
            # Default config
            OrchestrationConfig(),
            # Config with override
            OrchestrationConfig(
                role_profiles=(replace(default_role_profiles()[0], default_runner="opencode"),)
                + default_role_profiles()[1:]
            ),
            # Config with custom role
            OrchestrationConfig(
                role_profiles=default_role_profiles()
                + (
                    RoleProfile(
                        role_id="custom-1",
                        agent_id="custom-1",
                        prompt_family="reviewer",
                        execution_context="main_worktree",
                        mutation_policy="read_only",
                        session_policy="reuse_allowed",
                        output_contract="structured_review_result",
                        default_runner="opencode",
                    ),
                ),
            ),
        ]

        for config in configs_to_test:
            d = _config_to_dict(config)
            orch = d["orchestration"]
            serialized_role_profiles = orch.get("role_profiles", {})
            serialized_override_ids = set(orch.get("role_profile_overrides", {}).keys())

            # No built-in ID may appear under role_profiles
            for rid in serialized_role_profiles:
                assert rid not in builtin_ids, (
                    f"Built-in role ID {rid!r} found under role_profiles in serialized dict "
                    f"— this breaks _dict_to_config round-trip symmetry per RFC §6.2"
                )

            # Override IDs must all be built-in
            for rid in serialized_override_ids:
                assert rid in builtin_ids, (
                    f"Non-built-in role ID {rid!r} found under role_profile_overrides"
                )

    def test_frozen_snapshot_roundtrip(self, tmp_path: Path) -> None:
        """Frozen snapshot write -> load round-trips correctly with overrides."""
        original = default_role_profiles()
        modified = replace(original[0], default_runner="opencode")
        custom = RoleProfile(
            role_id="custom-1",
            agent_id="custom-1",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="opencode",
        )
        config = OrchestrationConfig(
            role_profiles=(modified,) + original[1:] + (custom,),
        )

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        snapshot_path = write_frozen_snapshot(config, run_dir)
        reloaded = load_frozen_snapshot(snapshot_path)

        by_id = {p.role_id: p for p in reloaded.role_profiles}
        assert by_id["python-executor"].default_runner == "opencode"
        assert "custom-1" in by_id
        assert by_id["custom-1"].default_runner == "opencode"

    def test_deep_update_config_roundtrip(self) -> None:
        """_deep_update_config (which uses _config_to_dict/_dict_to_config) round-trips."""
        from vectl.orchestration.config import _deep_update_config

        config = OrchestrationConfig()
        # This internally calls _config_to_dict then _dict_to_config
        result = _deep_update_config(
            config,
            {"orchestration": {"runtime": {"cleanup_policy": "always"}}},
        )
        assert result.runtime.cleanup_policy == "always"
        # Verify role_profiles are intact
        builtin_ids = {p.role_id for p in default_role_profiles()}
        result_ids = {p.role_id for p in result.role_profiles}
        assert builtin_ids.issubset(result_ids)

    def test_multiple_overrides_roundtrip(self) -> None:
        """Multiple built-in overrides round-trip correctly."""
        original = default_role_profiles()
        overrides_map = {
            "python-executor": "opencode",
            "python-senior": "opencode",
            "blocked-case-coordinator": "opencode",
        }
        modified_list = []
        for p in original:
            if p.role_id in overrides_map:
                modified_list.append(replace(p, default_runner=overrides_map[p.role_id]))
            else:
                modified_list.append(p)
        config = OrchestrationConfig(role_profiles=tuple(modified_list))

        d = _config_to_dict(config)
        config2 = _dict_to_config(d)

        by_id = {p.role_id: p for p in config2.role_profiles}
        for rid, expected_runner in overrides_map.items():
            assert by_id[rid].default_runner == expected_runner
        # Non-overridden built-ins remain
        assert by_id["doc-reviewer"].default_runner == "codex"


# ---------------------------------------------------------------------------
# Env Isolation Regression Tests
# ---------------------------------------------------------------------------


class TestConfigDiscoveryEnvIsolation:
    """Verify config discovery tests are deterministic regardless of host
    ~/.config/vectl/vectl.yaml content.

    Root cause: Tests calling load_orchestration_config() without explicit path
    would discover ~/.config/vectl/vectl.yaml from the host machine. Since that
    file uses pre-RFC role_profiles format (with built-in IDs), _dict_to_config
    rejects it per RFC §6.2, causing RoleProfileOverrideError in previously-passing
    tests.

    Fix: All tests that call load_orchestration_config() without explicit path
    now monkeypatch USER_CONFIG_DIR to a non-existent path, preventing ambient
    home-config bleed.
    """

    def test_no_config_returns_defaults_without_home_config_bleed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """load_orchestration_config() returns defaults when no config file exists.

        This test proves isolation from ~/.config/vectl/vectl.yaml.
        Without isolation, a home config with pre-RFC role_profiles causes
        RoleProfileOverrideError.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VECTL_CONFIG", raising=False)
        monkeypatch.setattr(
            "vectl.orchestration.config.USER_CONFIG_DIR",
            str(tmp_path / "nonexistent_home_config"),
        )

        config, path = load_orchestration_config()
        assert path is None
        assert config.plan_path.name == "plan.yaml"
        # Verify built-in role profiles are intact
        builtin_ids = {p.role_id for p in default_role_profiles()}
        config_ids = {p.role_id for p in config.role_profiles}
        assert builtin_ids.issubset(config_ids)

    def test_cwd_config_isolated_from_home_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cwd config discovery is isolated from home config."""
        config_content = {"orchestration": {"plan_path": "cwd_plan.yaml"}}
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("VECTL_CONFIG", raising=False)
        monkeypatch.setattr(
            "vectl.orchestration.config.USER_CONFIG_DIR",
            str(tmp_path / "nonexistent_home_config"),
        )

        config, path = load_orchestration_config()
        assert path == config_file
        assert config.plan_path.name == "cwd_plan.yaml"

    def test_env_override_isolated_from_home_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Env var override is isolated from home config."""
        monkeypatch.delenv("VECTL_CONFIG", raising=False)
        monkeypatch.setattr(
            "vectl.orchestration.config.USER_CONFIG_DIR",
            str(tmp_path / "nonexistent_home_config"),
        )
        monkeypatch.setenv("VECTL_ORCH_RUNTIME_ARTIFACT_ROOT", "/tmp/isolated_runs")

        config, path = load_orchestration_config()
        assert path is None
        assert config.runtime.artifact_root == Path("/tmp/isolated_runs")
