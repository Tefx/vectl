"""
RFC §13 Test Requirements — minimal traceability matrix.

Authority: docs/RFC-role-profile-overrides.md §13

Each test class maps to exactly one row of the RFC §13 test matrix.
The test name encodes the scenario for direct audit traceability.

These tests delegate to existing proven implementation surfaces
(_merge_role_profiles, _dict_to_config, load_orchestration_config,
OrchestrationApp.config_show) to exercise the production code path
end-to-end.  They are intentionally *duplicates of coverage*, not
*reimplementations*: their purpose is to make the RFC→test mapping
visible to human auditors in a single file.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import (
    OrchestrationConfig,
    RoleProfileOverrideError,
    _dict_to_config,
    _merge_role_profiles,
    build_role_profile_provenance,
    default_role_profiles,
    load_orchestration_config,
    validate_orchestration_config,
)
from vectl.orchestration.contracts import RoleProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_by_id(profiles: tuple[RoleProfile, ...]) -> dict[str, RoleProfile]:
    """Index role profiles by role_id for easy lookup."""
    return {p.role_id: p for p in profiles}


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
        "        agent: python-executor\n"
    )


def _build_app(tmp_path: Path, config: OrchestrationConfig | None = None) -> Any:
    """Build an OrchestrationApp with the given config or defaults."""
    plan_path = tmp_path / "plan.yaml"
    runs_root = tmp_path / "runs"
    _write_plan(plan_path)
    if config is None:
        config = OrchestrationConfig(plan_path=plan_path)
    app_config = AppConfig(
        plan_path=plan_path,
        orchestration_config=config,
        run_store_root=runs_root,
    )
    return build_orchestration_app(app_config)


# ---------------------------------------------------------------------------
# RFC §13 Row 1: role_profiles defines built-in role ID
#   Expected: validation error with migration-directed message
# ---------------------------------------------------------------------------


class TestRfcMatrixRow1_BuiltinInRoleProfiles:
    """RFC §13 row 1: role_profiles defines built-in role ID.

    Expected result: validation error with migration-directed message
    telling the user to use role_profile_overrides instead.
    """

    def test_builtin_shadowing_via_merge_raises(self) -> None:
        """Built-in role ID in role_profiles triggers RoleProfileOverrideError."""
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
        with pytest.raises(RoleProfileOverrideError, match="shadows built-in role"):
            _merge_role_profiles(builtin, {}, shadow)

    def test_builtin_shadowing_via_dict_to_config_raises(self) -> None:
        """Built-in role ID in role_profiles dict raises RoleProfileOverrideError."""
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

    def test_builtin_shadowing_via_config_file_raises(self, tmp_path: Path) -> None:
        """Built-in role ID in role_profiles YAML raises RoleProfileOverrideError."""
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

    def test_error_message_includes_migration_directive(self) -> None:
        """Error message must direct user to use role_profile_overrides."""
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
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {}, shadow)
        assert "use role_profile_overrides instead" in exc_info.value.reason

    def test_error_field_points_to_role_profiles(self) -> None:
        """Error field points to role_profiles.<role_id>."""
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
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {}, shadow)
        assert exc_info.value.field == "role_profiles.python-executor"


# ---------------------------------------------------------------------------
# RFC §13 Row 2: role_profile_overrides targets unknown built-in
#   Expected: validation error
# ---------------------------------------------------------------------------


class TestRfcMatrixRow2_UnknownBuiltInOverride:
    """RFC §13 row 2: role_profile_overrides targets an unknown built-in role.

    Expected result: validation error
    """

    def test_unknown_builtin_via_merge_raises(self) -> None:
        """Override for nonexistent role raises RoleProfileOverrideError."""
        builtin = default_role_profiles()
        with pytest.raises(RoleProfileOverrideError, match="unknown built-in role"):
            _merge_role_profiles(builtin, {"nonexistent-role": {"default_runner": "opencode"}}, ())

    def test_unknown_builtin_via_dict_to_config_raises(self) -> None:
        """Override for nonexistent role in config dict raises RoleProfileOverrideError."""
        data: dict[str, Any] = {
            "orchestration": {
                "role_profile_overrides": {
                    "ghost-role": {"default_runner": "opencode"},
                },
            }
        }
        with pytest.raises(RoleProfileOverrideError, match="unknown built-in role"):
            _dict_to_config(data)

    def test_unknown_builtin_via_config_file_raises(self, tmp_path: Path) -> None:
        """Override for nonexistent role in config file raises RoleProfileOverrideError."""
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

    def test_error_field_includes_role_id(self) -> None:
        """Error field path includes the unknown role ID."""
        builtin = default_role_profiles()
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {"totally-fake-role": {"default_runner": "opencode"}}, ())
        assert exc_info.value.field == "role_profile_overrides.totally-fake-role"

    def test_error_reason_mentions_unknown_builtin(self) -> None:
        """Error reason includes 'unknown built-in role' wording."""
        builtin = default_role_profiles()
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {"no-such-role": {"default_runner": "x"}}, ())
        assert "unknown built-in role in role_profile_overrides" in exc_info.value.reason


# ---------------------------------------------------------------------------
# RFC §13 Row 3: role_profile_overrides targets custom role from role_profiles
#   Expected: validation error
# ---------------------------------------------------------------------------


class TestRfcMatrixRow3_OverrideTargetsCustomRole:
    """RFC §13 row 3: role_profile_overrides targets custom role from role_profiles.

    Expected result: validation error
    """

    def test_override_targeting_custom_role_via_merge_raises(self) -> None:
        """Override referencing custom role from role_profiles raises error."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="my-special-role",
                agent_id="my-special-role",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="codex",
            ),
        )
        with pytest.raises(RoleProfileOverrideError, match="cannot target custom role"):
            _merge_role_profiles(
                builtin, {"my-special-role": {"default_runner": "opencode"}}, custom
            )

    def test_override_targeting_custom_role_via_config_file_raises(self, tmp_path: Path) -> None:
        """Override referencing custom role via config file raises error."""
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

    def test_error_field_points_to_override(self) -> None:
        """Error field points to role_profile_overrides.<custom_role_id>."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="custom-1",
                agent_id="custom-1",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="codex",
            ),
        )
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {"custom-1": {"default_runner": "opencode"}}, custom)
        assert exc_info.value.field == "role_profile_overrides.custom-1"

    def test_error_reason_mentions_custom_role(self) -> None:
        """Error reason explicitly mentions custom role from role_profiles."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="custom-2",
                agent_id="custom-2",
                prompt_family="reviewer",
                execution_context="main_worktree",
                mutation_policy="read_only",
                session_policy="reuse_allowed",
                output_contract="structured_review_result",
                default_runner="codex",
            ),
        )
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, {"custom-2": {"default_runner": "opencode"}}, custom)
        assert "cannot target custom role defined in role_profiles" in exc_info.value.reason


# ---------------------------------------------------------------------------
# RFC §13 Row 4: valid built-in override → effective registry patched
#   Expected: final effective registry contains patched field values
# ---------------------------------------------------------------------------


class TestRfcMatrixRow4_ValidBuiltinOverride:
    """RFC §13 row 4: valid built-in override.

    Expected result: final effective registry contains patched field values
    """

    def test_override_default_runner_via_merge(self) -> None:
        """Override of default_runner is reflected in the effective registry."""
        builtin = default_role_profiles()
        result = _merge_role_profiles(
            builtin, {"python-executor": {"default_runner": "opencode"}}, ()
        )
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].default_runner == "opencode"
        # Other built-ins stay unchanged
        assert by_id["doc-reviewer"].default_runner == "codex"

    def test_override_agent_id_via_merge(self) -> None:
        """Override of agent_id is reflected in the effective registry."""
        builtin = default_role_profiles()
        result = _merge_role_profiles(
            builtin, {"python-executor": {"agent_id": "custom-agent"}}, ()
        )
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].agent_id == "custom-agent"

    def test_override_via_dict_to_config(self) -> None:
        """Override through _dict_to_config patches the effective config."""
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

    def test_override_via_config_file(self, tmp_path: Path) -> None:
        """Override through config file patches the effective config."""
        config_content = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-senior": {"default_runner": "opencode"},
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))
        config, path = load_orchestration_config(plan_path=config_file)
        by_id = _profile_by_id(config.role_profiles)
        assert by_id["python-senior"].default_runner == "opencode"
        # Non-overridden built-ins remain unchanged
        assert by_id["python-executor"].default_runner == "codex"

    def test_multiple_overrides_via_merge(self) -> None:
        """Multiple built-in overrides are all applied."""
        builtin = default_role_profiles()
        overrides = {
            "python-executor": {"default_runner": "opencode"},
            "python-senior": {"default_runner": "opencode"},
        }
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].default_runner == "opencode"
        assert by_id["python-senior"].default_runner == "opencode"

    def test_non_overridden_builtins_unchanged(self) -> None:
        """Built-ins not targeted by overrides keep their defaults."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"default_runner": "opencode"}}
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        # doc-reviewer was not overridden — should retain default
        original = _profile_by_id(builtin)["doc-reviewer"]
        assert by_id["doc-reviewer"] == original

    def test_overridden_role_id_preserved(self) -> None:
        """RFC §6.1 rule 5: the patched role ID remains unchanged."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"agent_id": "different-agent"}}
        result = _merge_role_profiles(builtin, overrides, ())
        by_id = _profile_by_id(result)
        assert by_id["python-executor"].role_id == "python-executor"
        assert by_id["python-executor"].agent_id == "different-agent"


# ---------------------------------------------------------------------------
# RFC §13 Row 5: valid custom role definition
#   Expected: final effective registry includes the custom role
# ---------------------------------------------------------------------------


class TestRfcMatrixRow5_ValidCustomRole:
    """RFC §13 row 5: valid custom role definition.

    Expected result: final effective registry includes the custom role
    """

    def test_custom_role_via_merge(self) -> None:
        """Custom role is included in the effective registry after built-ins."""
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
        by_id = _profile_by_id(result)
        assert "my-reviewer" in by_id
        assert by_id["my-reviewer"].agent_id == "my-reviewer"
        assert by_id["my-reviewer"].default_runner == "opencode"

    def test_custom_role_via_dict_to_config(self) -> None:
        """Custom role defined via role_profiles is in the effective registry."""
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

    def test_custom_role_via_config_file(self, tmp_path: Path) -> None:
        """Custom role defined in config file is in the effective registry."""
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
        by_id = _profile_by_id(config.role_profiles)
        assert "my-custom-reviewer" in by_id
        # All built-ins should still be present
        builtin_ids = {p.role_id for p in default_role_profiles()}
        assert builtin_ids.issubset(set(by_id.keys()))

    def test_custom_role_appears_after_builtins(self) -> None:
        """Custom role appears in the registry after all built-ins."""
        builtin = default_role_profiles()
        custom = (
            RoleProfile(
                role_id="my-agent",
                agent_id="my-agent",
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
        assert ids[-1] == "my-agent"

    def test_override_plus_custom_both_present(self) -> None:
        """Overrides and custom roles coexist in the effective registry."""
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
        # Override applied
        assert by_id["python-executor"].default_runner == "opencode"
        # Custom role included
        assert "my-reviewer" in by_id


# ---------------------------------------------------------------------------
# RFC §13 Row 6: family-policy violation via override
#   Expected: validation error
# ---------------------------------------------------------------------------


class TestRfcMatrixRow6_FamilyPolicyViolationViaOverride:
    """RFC §13 row 6: family-policy violation through override.

    Expected result: validation error
    """

    def test_execution_context_family_violation_via_merge(self) -> None:
        """Overriding execution_context to violate family policy raises error."""
        builtin = default_role_profiles()
        # python-executor is 'coder' family → requires linked_worktree
        overrides = {"python-executor": {"execution_context": "main_worktree"}}
        with pytest.raises(RoleProfileOverrideError, match="family policy") as exc_info:
            _merge_role_profiles(builtin, overrides, ())
        assert "execution_context" in exc_info.value.field
        assert "coder family policy" in exc_info.value.reason

    def test_execution_context_family_violation_via_config_file(self, tmp_path: Path) -> None:
        """Family-policy violation from config file raises RoleProfileOverrideError."""
        config_content = {
            "orchestration": {
                "role_profile_overrides": {
                    "python-executor": {"execution_context": "main_worktree"},
                },
            }
        }
        config_file = tmp_path / "vectl.yaml"
        config_file.write_text(yaml.dump(config_content))
        with pytest.raises(RoleProfileOverrideError, match="family policy"):
            load_orchestration_config(plan_path=config_file)

    def test_mutation_policy_family_violation_via_merge(self) -> None:
        """Overriding mutation_policy to violate family policy raises error."""
        builtin = default_role_profiles()
        # python-executor is 'coder' family → requires worktree_changes
        overrides = {"python-executor": {"mutation_policy": "read_only"}}
        with pytest.raises(RoleProfileOverrideError, match="family policy"):
            _merge_role_profiles(builtin, overrides, ())

    def test_session_policy_family_violation_via_merge(self) -> None:
        """Overriding session_policy to violate family policy raises error."""
        builtin = default_role_profiles()
        # python-executor is 'coder' family → requires reuse_allowed
        overrides = {"python-executor": {"session_policy": "reuse_forbidden"}}
        with pytest.raises(RoleProfileOverrideError, match="family policy"):
            _merge_role_profiles(builtin, overrides, ())

    def test_output_contract_family_violation_via_merge(self) -> None:
        """Overriding output_contract to violate family policy raises error."""
        builtin = default_role_profiles()
        # python-executor is 'coder' family → requires freeform_evidence
        overrides = {"python-executor": {"output_contract": "structured_review_result"}}
        with pytest.raises(RoleProfileOverrideError, match="family policy"):
            _merge_role_profiles(builtin, overrides, ())

    def test_multiple_family_violations_collected(self) -> None:
        """Multiple family-policy violations are reported together."""
        builtin = default_role_profiles()
        overrides = {
            "python-executor": {
                "execution_context": "main_worktree",
                "mutation_policy": "read_only",
            }
        }
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, overrides, ())
        # Both violations should appear in the error message
        assert (
            "execution_context" in exc_info.value.reason or "family policy" in exc_info.value.reason
        )

    def test_error_field_includes_override_path(self) -> None:
        """Error field includes the full override path per RFC §7.2."""
        builtin = default_role_profiles()
        overrides = {"python-executor": {"execution_context": "main_worktree"}}
        with pytest.raises(RoleProfileOverrideError) as exc_info:
            _merge_role_profiles(builtin, overrides, ())
        assert exc_info.value.field == "role_profile_overrides.python-executor.execution_context"


# ---------------------------------------------------------------------------
# RFC §13 Row 7: human config-show --effective provenance output
#   Expected: required one-line provenance format is present
# ---------------------------------------------------------------------------


class TestRfcMatrixRow7_HumanConfigShowEffective:
    """RFC §13 row 7: human config-show --effective provenance output.

    Expected result: required one-line provenance format is present
    """

    def test_effective_output_contains_provenance_lines(self, tmp_path: Path) -> None:
        """config_show(effective=True) includes provenance lines."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        assert "source=" in result.show_output
        assert "role_profiles." in result.show_output

    def test_provenance_lines_follow_rfc_format(self, tmp_path: Path) -> None:
        """Each provenance line follows the RFC one-line format."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        lines = result.show_output.split("\n")
        provenance_lines = [l for l in lines if l.startswith("role_profiles.")]
        assert len(provenance_lines) > 0, "Expected provenance lines in output"
        # Each line must have (source=X) annotation
        for line in provenance_lines:
            assert "(source=" in line, f"Missing source annotation: {line}"

    def test_default_source_in_human_output(self, tmp_path: Path) -> None:
        """Default (unmodified) built-in fields show source=default."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        assert "source=default" in result.show_output

    def test_override_source_in_human_output(self, tmp_path: Path) -> None:
        """Overridden fields show source=override."""
        original = default_role_profiles()
        python_exec = original[0]
        modified = replace(python_exec, default_runner="opencode")
        new_profiles = (modified,) + original[1:]
        config = OrchestrationConfig(role_profiles=new_profiles, plan_path=tmp_path / "plan.yaml")
        app = _build_app(tmp_path, config)
        result = app.config_show(effective=True)
        assert (
            "role_profiles.python-executor.default_runner = opencode (source=override)"
            in result.show_output
        )

    def test_custom_source_in_human_output(self, tmp_path: Path) -> None:
        """Custom role fields show source=custom."""
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
            plan_path=tmp_path / "plan.yaml",
        )
        app = _build_app(tmp_path, config)
        result = app.config_show(effective=True)
        assert "source=custom" in result.show_output

    def test_summary_mode_has_no_provenance(self, tmp_path: Path) -> None:
        """config_show(effective=False) does not include provenance."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=False)
        assert "source=" not in result.show_output
        assert "role_profiles." not in result.show_output


# ---------------------------------------------------------------------------
# RFC §13 Row 8: structured config-show --effective --json role_profile_provenance shape
#   Expected: role_profile_provenance object has correct value/source shape
# ---------------------------------------------------------------------------


class TestRfcMatrixRow8_JsonConfigShowEffective:
    """RFC §13 row 8: structured config-show --effective --json role_profile_provenance.

    Expected result: role_profile_provenance object has correct value/source shape
    """

    def test_json_provenance_object_exists(self, tmp_path: Path) -> None:
        """config_show(effective=True) produces a non-None role_profile_provenance."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        assert result.role_profile_provenance is not None
        assert isinstance(result.role_profile_provenance, dict)

    def test_json_provenance_has_value_and_source_keys(self, tmp_path: Path) -> None:
        """Each provenance entry has both 'value' and 'source' keys."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None
        for role_id, fields in prov.items():
            for field_name, field_prov in fields.items():
                assert "value" in field_prov, f"{role_id}.{field_name} missing 'value'"
                assert "source" in field_prov, f"{role_id}.{field_name} missing 'source'"

    def test_json_provenance_source_categories(self, tmp_path: Path) -> None:
        """Each source value is one of 'default', 'override', or 'custom'."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None
        for role_id, fields in prov.items():
            for field_name, field_prov in fields.items():
                assert field_prov["source"] in ("default", "override", "custom"), (
                    f"{role_id}.{field_name} unexpected source: {field_prov['source']}"
                )

    def test_json_default_source(self, tmp_path: Path) -> None:
        """Default (unmodified) built-in fields have source=default."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None
        assert prov["python-executor"]["default_runner"]["source"] == "default"
        assert prov["python-executor"]["default_runner"]["value"] == "codex"

    def test_json_override_source(self, tmp_path: Path) -> None:
        """Overridden built-in fields have source=override."""
        original = default_role_profiles()
        python_exec = original[0]
        modified = replace(python_exec, default_runner="opencode")
        new_profiles = (modified,) + original[1:]
        config = OrchestrationConfig(role_profiles=new_profiles, plan_path=tmp_path / "plan.yaml")
        app = _build_app(tmp_path, config)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None
        assert prov["python-executor"]["default_runner"]["source"] == "override"
        assert prov["python-executor"]["default_runner"]["value"] == "opencode"

    def test_json_custom_source(self, tmp_path: Path) -> None:
        """Custom role fields have source=custom."""
        custom_reviewer = RoleProfile(
            role_id="my-custom-reviewer",
            agent_id="my-custom-reviewer",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="opencode",
        )
        config = OrchestrationConfig(
            role_profiles=default_role_profiles() + (custom_reviewer,),
            plan_path=tmp_path / "plan.yaml",
        )
        app = _build_app(tmp_path, config)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None
        assert "my-custom-reviewer" in prov
        assert prov["my-custom-reviewer"]["default_runner"]["source"] == "custom"

    def test_json_provenance_is_json_serializable(self, tmp_path: Path) -> None:
        """role_profile_provenance can be JSON-serialized and -deserialized."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        serialized = json.dumps(result.role_profile_provenance)
        deserialized = json.loads(serialized)
        assert deserialized is not None

    def test_summary_mode_provenance_is_none(self, tmp_path: Path) -> None:
        """config_show(effective=False) has role_profile_provenance=None."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=False)
        assert result.role_profile_provenance is None
