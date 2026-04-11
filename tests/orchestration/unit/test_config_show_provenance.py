"""
Tests for config-show --effective provenance surface.

Authority: docs/RFC-role-profile-overrides.md §7.1

Verifies:
- build_role_profile_provenance produces correct source categories
- Human output includes provenance annotation lines
- JSON output includes role_profile_provenance object
- Custom roles are attributed source=custom
- Overridden built-in fields are attributed source=override
- Default built-in fields are attributed source=default
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from vectl.orch_app import AppConfig, build_orchestration_app
from vectl.orchestration.config import (
    OrchestrationConfig,
    RoleFieldProvenance,
    build_role_profile_provenance,
    default_role_profiles,
)
from vectl.orchestration.contracts import RoleProfile


# ---------------------------------------------------------------------------
# Unit tests for build_role_profile_provenance
# ---------------------------------------------------------------------------


class TestBuildRoleProfileProvenance:
    """Tests for the provenance builder function (RFC §7.1)."""

    def test_default_profiles_all_default_source(self) -> None:
        """All fields on built-in profiles with no overrides → source=default."""
        config = OrchestrationConfig()
        provenance = build_role_profile_provenance(config)

        # Every built-in profile should be present
        assert set(provenance.keys()) == {p.role_id for p in default_role_profiles()}

        # Every field on every built-in profile should have source=default
        for role_id, fields in provenance.items():
            for field_name, prov in fields.items():
                assert prov.source == "default", (
                    f"{role_id}.{field_name} expected default, got {prov.source}"
                )

    def test_override_produces_override_source(self) -> None:
        """A built-in profile with an overridden field → source=override for that field."""
        # Modify python-executor's default_runner from "codex" to "opencode"
        original = default_role_profiles()
        python_exec = original[0]
        assert python_exec.role_id == "python-executor"

        modified = replace(python_exec, default_runner="opencode")
        new_profiles = (modified,) + original[1:]
        config = OrchestrationConfig(role_profiles=new_profiles)
        provenance = build_role_profile_provenance(config)

        # default_runner should be override; other fields should still be default
        assert provenance["python-executor"]["default_runner"].source == "override"
        assert provenance["python-executor"]["default_runner"].value == "opencode"
        assert provenance["python-executor"]["agent_id"].source == "default"

    def test_custom_role_produces_custom_source(self) -> None:
        """A custom role (not in built-in set) → source=custom for all fields."""
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
        config = OrchestrationConfig(role_profiles=default_role_profiles() + (custom_reviewer,))
        provenance = build_role_profile_provenance(config)

        assert "my-custom-reviewer" in provenance
        for field_name, prov in provenance["my-custom-reviewer"].items():
            assert prov.source == "custom", (
                f"my-custom-reviewer.{field_name} expected custom, got {prov.source}"
            )

    def test_provenance_covers_all_profile_fields(self) -> None:
        """Every field in RoleProfile (except role_id) has a provenance entry."""
        config = OrchestrationConfig()
        provenance = build_role_profile_provenance(config)

        expected_fields = {
            "agent_id",
            "prompt_family",
            "execution_context",
            "mutation_policy",
            "session_policy",
            "output_contract",
            "default_runner",
        }

        for role_id, fields in provenance.items():
            assert set(fields.keys()) == expected_fields, (
                f"{role_id} provenance fields mismatch: {set(fields.keys())} vs {expected_fields}"
            )

    def test_override_and_default_coexist(self) -> None:
        """Multiple overrides on different fields produce correct per-field provenance."""
        original = default_role_profiles()
        python_exec = original[0]
        assert python_exec.role_id == "python-executor"

        # Override both default_runner and prompt_family
        modified = replace(python_exec, default_runner="opencode", prompt_family="planner")
        new_profiles = (modified,) + original[1:]
        config = OrchestrationConfig(role_profiles=new_profiles)
        provenance = build_role_profile_provenance(config)

        assert provenance["python-executor"]["default_runner"].source == "override"
        assert provenance["python-executor"]["prompt_family"].source == "override"
        assert provenance["python-executor"]["agent_id"].source == "default"

    def test_mix_of_builtin_and_custom_roles(self) -> None:
        """Provenance correctly categorizes built-in default, override, and custom."""
        original = default_role_profiles()
        python_exec = original[0]
        # Override default_runner
        modified = replace(python_exec, default_runner="opencode")
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
        config = OrchestrationConfig(role_profiles=(modified,) + original[1:] + (custom_reviewer,))
        provenance = build_role_profile_provenance(config)

        # python-executor: override for default_runner, default for agent_id
        assert provenance["python-executor"]["default_runner"].source == "override"
        assert provenance["python-executor"]["agent_id"].source == "default"

        # doc-reviewer (unmodified built-in): all default
        assert provenance["doc-reviewer"]["default_runner"].source == "default"

        # my-reviewer (custom): all custom
        assert provenance["my-reviewer"]["default_runner"].source == "custom"


# ---------------------------------------------------------------------------
# Integration tests for config_show with provenance
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


class TestConfigShowProvenanceHuman:
    """Tests for human-readable provenance output (RFC §7.1)."""

    def test_effective_includes_provenance_lines(self, tmp_path: Path) -> None:
        """effective=True includes role_profiles provenance lines in show_output."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        # Should contain provenance lines
        assert "source=default" in result.show_output
        assert "role_profiles." in result.show_output

    def test_effective_provenance_format(self, tmp_path: Path) -> None:
        """Each provenance line follows the RFC format: role_profiles.{role_id}.{field} = value (source=X)."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        # Check that at least one line matches the expected format exactly
        # RFC example: role_profiles.python-executor.default_runner = codex (source=default)
        lines = result.show_output.split("\n")
        provenance_lines = [l for l in lines if l.startswith("role_profiles.")]
        assert len(provenance_lines) > 0, "No provenance lines found in effective output"

        # Each provenance line should contain "(source=" annotation
        for line in provenance_lines:
            assert "(source=" in line, f"Missing source annotation in: {line}"

    def test_effective_shows_all_builtin_roles(self, tmp_path: Path) -> None:
        """effective=True includes provenance for all built-in roles."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        # All built-in roles should appear in output
        for p in default_role_profiles():
            assert f"role_profiles.{p.role_id}." in result.show_output, (
                f"Missing provenance for built-in role {p.role_id}"
            )

    def test_summary_mode_has_no_provenance(self, tmp_path: Path) -> None:
        """effective=False (summary mode) does not include provenance."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=False)

        assert "source=" not in result.show_output
        assert "role_profiles." not in result.show_output

    def test_override_shows_override_source(self, tmp_path: Path) -> None:
        """Overridden built-in field shows source=override in human output."""
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


class TestConfigShowProvenanceJSON:
    """Tests for structured (JSON) provenance output (RFC §7.1)."""

    def test_effective_json_includes_provenance(self, tmp_path: Path) -> None:
        """effective=True includes role_profile_provenance in ConfigResult."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        assert result.role_profile_provenance is not None
        assert isinstance(result.role_profile_provenance, dict)

    def test_provenance_json_structure(self, tmp_path: Path) -> None:
        """role_profile_provenance follows the RFC shape: {role_id: {field: {value, source}}}."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)
        prov = result.role_profile_provenance
        assert prov is not None

        # Check structure: role_id -> field_name -> {value, source}
        for role_id, fields in prov.items():
            assert isinstance(fields, dict), f"{role_id} fields should be dict"
            for field_name, field_prov in fields.items():
                assert isinstance(field_prov, dict), f"{role_id}.{field_name} should be dict"
                assert "value" in field_prov, f"{role_id}.{field_name} missing 'value'"
                assert "source" in field_prov, f"{role_id}.{field_name} missing 'source'"
                assert field_prov["source"] in ("default", "override", "custom"), (
                    f"{role_id}.{field_name} unexpected source: {field_prov['source']}"
                )

    def test_json_serializable(self, tmp_path: Path) -> None:
        """role_profile_provenance is JSON-serializable."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        # Must not raise - validates that the provenance dict is JSON-safe
        serialized = json.dumps(result.role_profile_provenance)
        deserialized = json.loads(serialized)
        assert deserialized is not None

    def test_summary_mode_has_no_provenance_json(self, tmp_path: Path) -> None:
        """effective=False has role_profile_provenance=None."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=False)
        assert result.role_profile_provenance is None

    def test_override_source_in_json(self, tmp_path: Path) -> None:
        """Overridden field has source=override in JSON provenance."""
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

    def test_custom_source_in_json(self, tmp_path: Path) -> None:
        """Custom role fields have source=custom in JSON provenance."""
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
        assert prov["my-custom-reviewer"]["default_runner"]["value"] == "opencode"

    def test_default_source_in_json(self, tmp_path: Path) -> None:
        """Unmodified built-in fields have source=default in JSON provenance."""
        app = _build_app(tmp_path)
        result = app.config_show(effective=True)

        prov = result.role_profile_provenance
        assert prov is not None
        # python-executor.default_runner should be "codex" with source "default"
        assert prov["python-executor"]["default_runner"]["value"] == "codex"
        assert prov["python-executor"]["default_runner"]["source"] == "default"
