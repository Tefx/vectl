"""
Verification tests for RFC-defined role profile override merge logic.

Authority: docs/RFC-role-profile-overrides.md

Tests verify:
- Effective registry construction follows RFC §5 order
- Override validation follows RFC §6.1 (built-in-only targets, allowed fields)
- Custom role validation follows RFC §6.2 (no built-in shadowing)
- Family-policy violations are detected on merged profiles (RFC §6.1 rule 4)
- OrchestrationConfig stores only the effective runtime-facing registry
- Config loading end-to-end through load_orchestration_config
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from vectl.orchestration.config import (
    ConfigValidationError,
    OrchestrationConfig,
    RoleProfileOverrideError,
    _ALLOWED_OVERRIDE_FIELDS,
    _apply_role_profile_override,
    _builtin_role_ids,
    _dict_to_config,
    _merge_role_profiles,
    _parse_role_profile_overrides,
    default_role_profiles,
    load_orchestration_config,
    validate_orchestration_config,
    validate_role_profiles,
)
from vectl.orchestration.contracts import RoleProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_by_id(profiles: tuple[RoleProfile, ...]) -> dict[str, RoleProfile]:
    """Index role profiles by role_id for easy lookup."""
    return {p.role_id: p for p in profiles}


# ---------------------------------------------------------------------------
# _builtin_role_ids
# ---------------------------------------------------------------------------


class TestBuiltinRoleIDs:
    """Verify _builtin_role_ids returns the expected set."""

    def test_contains_all_default_role_ids(self) -> None:
        """All role IDs from default_role_profiles are in the set."""
        builtin = _builtin_role_ids()
        expected = {p.role_id for p in default_role_profiles()}
        assert builtin == frozenset(expected)

    def test_python_executor_is_builtin(self) -> None:
        """python-executor is a recognized built-in."""
        assert "python-executor" in _builtin_role_ids()

    def test_nonexistent_is_not_builtin(self) -> None:
        """An invented role ID is not a built-in."""
        assert "custom-agent-xyz" not in _builtin_role_ids()


# ---------------------------------------------------------------------------
# _parse_role_profile_overrides
# ---------------------------------------------------------------------------


class TestParseRoleProfileOverrides:
    """Verify _parse_role_profile_overrides parsing."""

    def test_parses_valid_overrides(self) -> None:
        """Valid override dict is parsed successfully."""
        raw: dict[str, dict[str, Any]] = {
            "python-executor": {"default_runner": "opencode"},
            "python-senior": {"default_runner": "opencode", "agent_id": "senior-agent"},
        }
        result = _parse_role_profile_overrides(raw)
        assert result["python-executor"]["default_runner"] == "opencode"
        assert result["python-senior"]["default_runner"] == "opencode"
        assert result["python-senior"]["agent_id"] == "senior-agent"

    def test_rejects_non_mapping(self) -> None:
        """Top-level value that is not a mapping is rejected."""
        with pytest.raises(ValueError, match="must be a mapping"):
            _parse_role_profile_overrides({"python-executor": "not-a-dict"})  # type: ignore[arg-type]

    def test_rejects_non_dict_top_level(self) -> None:
        """Top-level input that is not a dict is rejected."""
        with pytest.raises(ValueError, match="must be a mapping"):
            _parse_role_profile_overrides("not-a-dict")  # type: ignore[arg-type]

    def test_empty_overrides_produces_empty_dict(self) -> None:
        """Empty overrides dict returns empty parsed result."""
        result = _parse_role_profile_overrides({})
        assert result == {}


# ---------------------------------------------------------------------------
# _merge_role_profiles: RFC §5 effective registry construction order
# ---------------------------------------------------------------------------


class TestMergeRoleProfilesOrder:
    """Verify merge order per RFC §5: built-ins → overrides → custom."""

    def test_builtins_only_when_no_overrides_or_custom(self) -> None:
        """Without overrides or custom profiles, result equals built-in defaults."""
        builtin = default_role_profiles()
        result = _merge_role_profiles(builtin, {}, ())
        assert result == builtin

    def test_override_applies_to_builtin(self) -> None:
        """Override fields are applied on top of built-in profiles."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"default_runner": "opencode"}}
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].default_runner == "opencode"
        # Other built-ins remain unchanged
        assert by_id["blocked-case-coordinator"].default_runner == "codex"

    def test_override_does_not_change_role_id(self) -> None:
        """RFC §6.1 rule 5: the patched role ID remains unchanged."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"agent_id": "custom-agent"}}
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].role_id == "python-executor"
        assert by_id["python-executor"].agent_id == "custom-agent"

    def test_custom_roles_appended_after_builtins(self) -> None:
        """Custom roles appear after built-in roles in the result tuple."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="my-reviewer",
                agent_id="my-reviewer",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="opencode",
            ),
        )
        result = _merge_role_profiles(builtin, {}, custom)
        ids = [p.role_id for p in result]
        # Custom role comes after all built-ins
        assert ids[-1] == "my-reviewer"
        # Built-in order is preserved
        builtin_ids = [p.role_id for p in builtin]
        assert ids[: len(builtin_ids)] == builtin_ids

    def test_override_and_custom_together(self) -> None:
        """RFC §4.3: Both overrides and custom roles work together."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"default_runner": "opencode"}}
        custom = (
            RoleProfile(
                role_id="my-reviewer",
                agent_id="my-reviewer",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="opencode",
            ),
        )
        result = _merge_role_profiles(builtin, overrides, custom)
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].default_runner == "opencode"
        assert "my-reviewer" in by_id

    def test_multiple_overrides_on_different_builtins(self) -> None:
        """Multiple built-in roles can be overridden simultaneously."""
        builtin = default_role_profiles()
        overrides = {
            "python-executor": {"default_runner": "opencode"},
            "python-senior": {"default_runner": "opencode"},
            "blocked-case-coordinator": {"default_runner": "opencode"},
        }
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        for rid in ("python-executor", "python-senior", "blocked-case-coordinator"):
            assert by_id[rid].default_runner == "opencode"

    def test_non_overridden_builtins_unchanged(self) -> None:
        """Built-ins not targeted by overrides remain at their default values."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"default_runner": "opencode"}}
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        # doc-reviewer was not overridden
        original = _profile_by_id(builtin)["doc-reviewer"]
        assert by_id["doc-reviewer"] == original


# ---------------------------------------------------------------------------
# _merge_role_profiles: RFC §6.1 override validation
# ---------------------------------------------------------------------------


class TestOverrideValidation:
    """Verify override validation per RFC §6.1."""

    def test_override_unknown_builtin_rejected(self) -> None:
        """RFC §6.1 rule 1: Override target must exist in built-ins."""
        builtin = default_role_profiles()
        with pytest.raises(RoleProfileOverrideError, match="unknown built-in role"):
            _merge_role_profiles(builtin, {"nonexistent": {"default_runner": "opencode"}}, ())

    def test_override_targeting_custom_role_rejected(self) -> None:
        """RFC §6.1 rule 3: Override cannot target a custom role."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="my-role",
                agent_id="my-role",
                prompt_family="coder",
                execution_context="linked_worktree",
                mutation_policy="worktree_changes",
                session_policy="reuse_allowed",
                output_contract="freeform_evidence",
                default_runner="codex",
            ),
        )
        with pytest.raises(RoleProfileOverrideError, match="cannot target custom role"):
            _merge_role_profiles(builtin, {"my-role": {"default_runner": "opencode"}}, custom)

    def test_override_unknown_field_rejected(self) -> None:
        """RFC §6.1 rule 2: Unknown override fields are rejected."""
        builtin = default_role_profiles()
        with pytest.raises(RoleProfileOverrideError, match="unknown override field"):
            _merge_role_profiles(builtin, {"python-executor": {"nonexistent_field": "value"}}, ())

    def test_all_allowed_override_fields_accepted(self) -> None:
        """Each field in _ALLOWED_OVERRIDE_FIELDS is accepted for override."""
        builtin = default_role_profiles()
        # Test each allowed field separately
        for field_name in _ALLOWED_OVERRIDE_FIELDS:
            overrides = {"python-executor": {field_name: "test-value"}}
            # This should not raise — if it does, the field was rejected
            result = _merge_role_profiles(builtin, overrides, ())
            by_id = _profile_by_id(result)
            assert by_id["python-executor"].role_id == "python-executor"


# ---------------------------------------------------------------------------
# _merge_role_profiles: RFC §6.2 custom role validation
# ---------------------------------------------------------------------------


class TestCustomRoleValidation:
    """Verify custom role validation per RFC §6.2."""

    def test_custom_role_shadows_builtin_rejected(self) -> None:
        """RFC §6.2 rule 2/4: Custom role ID must not shadow built-in."""
        builtin = default_role_profiles()
        shadow = (
            RoleProfile(
                role_id="python-executor",
                agent_id="python-executor",
                prompt_family="coder",
                execution_context="linked_worktree",
                mutation_policy="worktree_changes",
                session_policy="reuse_allowed",
                output_contract="freeform_evidence",
                default_runner="codex",
            ),
        )
        with pytest.raises(
            RoleProfileOverrideError, match="shadows built-in role.*use role_profile_overrides"
        ):
            _merge_role_profiles(builtin, {}, shadow)

    def test_custom_role_with_unique_id_accepted(self) -> None:
        """Custom role with a unique (non-builtin) ID is accepted."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="my-custom-agent",
                agent_id="my-custom-agent",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="opencode",
            ),
        )
        result = _merge_role_profiles(builtin, {}, custom)
        by_id = _profile_by_id(result)
        assert "my-custom-agent" in by_id


# ---------------------------------------------------------------------------
# Family-policy validation on effective profiles (RFC §6.1 rule 4)
# ---------------------------------------------------------------------------


class TestFamilyPolicyValidation:
    """Verify that family-policy violations are caught on merged profiles."""

    def test_override_causes_family_policy_violation(self) -> None:
        """RFC §6.1 rule 4: Overridden profile must satisfy family policy."""
        builtin = default_role_profiles()
        # python-executor uses 'coder' family which requires linked_worktree
        overrides = {"python-executor": {"execution_context": "main_worktree"}}
        merged = _merge_role_profiles(builtin, overrides, ())
        violations = validate_role_profiles(merged)
        assert len(violations) > 0
        assert "python-executor" in violations[0]
        assert "family policy" in violations[0]


# ---------------------------------------------------------------------------
# _dict_to_config integration
# ---------------------------------------------------------------------------


class TestDictToConfigOverrides:
    """Verify _dict_to_config processes overrides and custom roles correctly."""

    def test_overrides_applied_through_dict_to_config(self) -> None:
        """_dict_to_config applies role_profile_overrides."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
            }
        }
        config = _dict_to_config(data)
        by_id = _profile_by_id(config.role_profiles)
        assert by_id["python-executor"].default_runner == "opencode"

    def test_custom_role_through_dict_to_config(self) -> None:
        """_dict_to_config adds custom roles from role_profiles."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profiles": {
                    "my-reviewer": {
                        "agent_id": "my-reviewer",
                        "prompt_family": "reviewer",
                        "execution_context": "main_worktree",
                        "mutation_policy": "read_only",
                        "session_policy": "reuse_allowed",
                        "output_contract": "structured_review_result",
                        "default_runner": "opencode",
                    },
                },
            }
        }
        config = _dict_to_config(data)
        by_id = _profile_by_id(config.role_profiles)
        assert "my-reviewer" in by_id

    def test_both_overrides_and_custom_through_dict_to_config(self) -> None:
        """_dict_to_config handles both overrides and custom roles."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
                "role_profiles": {
                    "my-reviewer": {
                        "agent_id": "my-reviewer",
                        "prompt_family": "reviewer",
                        "execution_context": "main_worktree",
                        "mutation_policy": "read_only",
                        "session_policy": "reuse_allowed",
                        "output_contract": "structured_review_result",
                        "default_runner": "opencode",
                    },
                },
            }
        }
        config = _dict_to_config(data)
        by_id = _profile_by_id(config.role_profiles)
        assert by_id["python-executor"].default_runner == "opencode"
        assert "my-reviewer" in by_id
        # Built-ins not overridden remain
        assert "blocked-case-coordinator" in by_id

    def test_builtins_remain_when_only_overrides_present(self) -> None:
        """When only overrides (no role_profiles) are given, built-ins are kept."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
            }
        }
        config = _dict_to_config(data)
        builtin_ids = {p.role_id for p in default_role_profiles()}
        config_ids = {p.role_id for p in config.role_profiles}
        assert builtin_ids.issubset(config_ids)

    def test_shadow_in_dict_to_config_raises(self) -> None:
        """role_profiles shadowing built-in raises RoleProfileOverrideError."""
        data: dict[str, Any] = {
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
                },
            }
        }
        with pytest.raises(RoleProfileOverrideError, match="shadows built-in role"):
            _dict_to_config(data)

    def test_unknown_override_target_in_dict_to_config_raises(self) -> None:
        """role_profile_overrides targeting unknown role raises RoleProfileOverrideError."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "nonexistent": {"default_runner": "opencode"},
                },
            }
        }
        with pytest.raises(RoleProfileOverrideError, match="unknown built-in role"):
            _dict_to_config(data)

    def test_no_overrides_no_custom_gives_defaults(self) -> None:
        """When neither overrides nor custom roles are given, defaults are used."""
        data: dict[str, Any] = {"orchestration": {}}
        config = _dict_to_config(data)
        assert config.role_profiles == default_role_profiles()


# ---------------------------------------------------------------------------
# load_orchestration_config end-to-end
# ---------------------------------------------------------------------------


class TestLoadOrchestrationConfigOverrides:
    """Verify load_orchestration_config handles overrides via config file."""

    def test_override_runner_via_config_file(self, tmp_path: Path) -> None:
        """Override default_runner via role_profile_overrides in a config file."""
        config_content = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        config, path = load_orchestration_config(plan_path=config_file)
        assert path == config_file
        by_id = _profile_by_id(config.role_profiles)
        assert by_id["python-executor"].default_runner == "opencode"

    def test_custom_role_via_config_file(self, tmp_path: Path) -> None:
        """Custom role defined via role_profiles in a config file."""
        config_content = {
            "orchestration": {
                "role_profiles": {
                    "my-custom-reviewer": {
                        "agent_id": "my-custom-reviewer",
                        "prompt_family": "reviewer",
                        "execution_context": "main_worktree",
                        "mutation_policy": "read_only",
                        "session_policy": "reuse_allowed",
                        "output_contract": "structured_review_result",
                        "default_runner": "opencode",
                    },
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        config, path = load_orchestration_config(plan_path=config_file)
        assert path == config_file
        by_id = _profile_by_id(config.role_profiles)
        assert "my-custom-reviewer" in by_id
        # All built-ins should still be present
        builtin_ids = {p.role_id for p in default_role_profiles()}
        assert builtin_ids.issubset(set(by_id.keys()))

    def test_shadow_violation_via_config_file(self, tmp_path: Path) -> None:
        """Shadowing built-in via role_profiles in config file raises error."""
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
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        with pytest.raises(RoleProfileOverrideError, match="shadows built-in role"):
            load_orchestration_config(plan_path=config_file)

    def test_unknown_override_target_via_config_file(self, tmp_path: Path) -> None:
        """Overriding unknown built-in via config file raises error."""
        config_content = {
            "orchestration": {
                "role_profile_overrides": {
                    "nonexistent-role": {"default_runner": "opencode"},
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        with pytest.raises(RoleProfileOverrideError, match="unknown built-in role"):
            load_orchestration_config(plan_path=config_file)

    def test_override_targeting_custom_role_via_config_file(self, tmp_path: Path) -> None:
        """Overriding a custom role via role_profile_overrides raises error."""
        config_content = {
            "orchestration": {
                "role_profile_overrides": {
                    "my-role": {"default_runner": "opencode"},
                },
                "role_profiles": {
                    "my-role": {
                        "agent_id": "my-role",
                        "prompt_family": "reviewer",
                        "execution_context": "main_worktree",
                        "mutation_policy": "read_only",
                        "session_policy": "reuse_allowed",
                        "output_contract": "structured_review_result",
                        "default_runner": "codex",
                    },
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))

        with pytest.raises(RoleProfileOverrideError, match="cannot target custom role"):
            load_orchestration_config(plan_path=config_file)


# ---------------------------------------------------------------------------
# OrchestrationConfig stores effective-only registry
# ---------------------------------------------------------------------------


class TestOrchestrationConfigEffectiveOnly:
    """Verify OrchestrationConfig stores only the effective merged registry."""

    def test_config_has_no_override_metadata(self) -> None:
        """OrchestrationConfig does not store raw override data."""
        config = OrchestrationConfig()
        assert not hasattr(config, "role_profile_overrides")

    def test_effective_registry_is_tuple_of_role_profiles(self) -> None:
        """role_profiles is a plain tuple of RoleProfile (no provenance)."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
            }
        }
        config = _dict_to_config(data)
        assert isinstance(config.role_profiles, tuple)
        for profile in config.role_profiles:
            assert isinstance(profile, RoleProfile)


# ---------------------------------------------------------------------------
# validate_orchestration_config with overrides
# ---------------------------------------------------------------------------


class TestValidationWithOverrides:
    """Verify validate_orchestration_config works on effective merged configs."""

    def test_valid_overridden_config_passes_validation(self) -> None:
        """Config with valid overrides passes full validation."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"default_runner": "opencode"},
                },
            }
        }
        config = _dict_to_config(data)
        errors = validate_orchestration_config(config)
        assert len(errors) == 0

    def test_family_policy_violation_after_override_detected(self) -> None:
        """Family-policy violation caused by override is detected."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"execution_context": "main_worktree"},
                },
            }
        }
        config = _dict_to_config(data)
        errors = validate_orchestration_config(config)
        family_errors = [e for e in errors if e.field == "role_profiles"]
        assert len(family_errors) > 0

    def test_dispatch_default_role_still_validated_after_override(self) -> None:
        """dispatch.default_role_id is validated against effective registry."""
        data: dict[str, Any] = {
            "orchestration": {
                "dispatch": {"default_role_id": "nonexistent-role"},
            }
        }
        config = _dict_to_config(data)
        errors = validate_orchestration_config(config)
        assert any(e.field == "dispatch.default_role_id" for e in errors)


# ---------------------------------------------------------------------------
# _apply_role_profile_override edge cases
# ---------------------------------------------------------------------------


class TestApplyRoleProfileOverride:
    """Verify _apply_role_profile_override field application."""

    def test_single_field_override(self) -> None:
        """Override a single field, rest unchanged."""
        builtin = default_role_profiles()
        python_exec = _profile_by_id(builtin)["python-executor"]
        result = _apply_role_profile_override(python_exec, {"default_runner": "opencode"})
        assert result.default_runner == "opencode"
        assert result.agent_id == python_exec.agent_id
        assert result.prompt_family == python_exec.prompt_family
        assert result.execution_context == python_exec.execution_context

    def test_multiple_field_override(self) -> None:
        """Override multiple fields simultaneously."""
        builtin = default_role_profiles()
        python_exec = _profile_by_id(builtin)["python-executor"]
        result = _apply_role_profile_override(
            python_exec,
            {"default_runner": "opencode", "agent_id": "custom-agent"},
        )
        assert result.default_runner == "opencode"
        assert result.agent_id == "custom-agent"
        assert result.prompt_family == python_exec.prompt_family

    def test_original_profile_unchanged(self) -> None:
        """Original profile is not mutated (frozen dataclass)."""
        builtin = default_role_profiles()
        python_exec = _profile_by_id(builtin)["python-executor"]
        _ = _apply_role_profile_override(python_exec, {"default_runner": "opencode"})
        assert python_exec.default_runner == "codex"

    def test_empty_overrides_returns_equivalent_profile(self) -> None:
        """Empty override dict returns a profile with same values."""
        builtin = default_role_profiles()
        python_exec = _profile_by_id(builtin)["python-executor"]
        result = _apply_role_profile_override(python_exec, {})
        assert result == python_exec
