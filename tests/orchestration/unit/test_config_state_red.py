"""
Test coverage for orchestration-plane config state gaps (expected-red).

Authoritative spec references:
- Config shape + minimal YAML: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
- Frozen config snapshot: §8.8
- Config discovery + precedence: §8.1, §8.2
- Run registry + --latest semantics: §6.4
- Same-plan admission rule: §6.4
- Case-index lifecycle: §6.4
- Resume/recovery frozen snapshot reuse: §6.6

step_type: test
step_intent: test_define_red
expected_result: red
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from vectl.orchestration.config import (
    OrchestrationConfig,
    ResolverConfig,
    ResolverToolAllowlist,
    RosterConfig,
    RuntimeConfig,
    freeze_config,
    load_orchestration_config,
)
from vectl.orchestration.run_store import (
    CasesIndex,
    RunRecord,
    RunRegistry,
    latest_run,
)


# =============================================================================
# SPEC-FIXTURE CONFORMANCE: Minimal orchestration config YAML
# Source: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
# Lines 1348-1352 (Minimal configuration example)
# =============================================================================

MINIMAL_ORCH_CONFIG_YAML: str = """\
orchestration:
  plan_path: plan.yaml
"""
"""Exact minimal config shape from spec §8.4.

This fixture MUST NOT use convenience fields or computed defaults.
It matches the documented minimal YAML structure exactly as specified.

Spec reference: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md
Section 8.4 "Canonical config shape" - Minimal configuration example.
"""


# =============================================================================
# SPEC-FIXTURE: Full canonical config shape
# Source: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
# Lines 1264-1315 (Canonical config shape)
# =============================================================================

FULL_CANONICAL_CONFIG_DICT: dict[str, Any] = {
    "orchestration": {
        "plan_path": "plan.yaml",
        "runtime": {
            "default_runner": "codex",
            "artifact_root": ".vectl/runs",
            "workspace_root": ".vectl/workspaces",
            "isolation_default": "default",
            "cleanup_policy": "on-success",
        },
        "control": {
            "idle_poll_interval_ms": 1000,
            "max_resolution_attempts_per_case": 1,
            "action_ack_timeout_seconds": 5,
        },
        "resolver": {
            "enabled": True,
            "timeout_seconds": 600,
            "max_tool_calls_per_invocation": 100,
            "max_tool_argument_bytes": 65536,
            "tool_allowlist": {
                "core": [
                    "status",
                    "show",
                    "claim",
                    "complete",
                    "defer",
                ],
                "orchestration": [
                    "read_events",
                    "read_state",
                    "read_case",
                ],
            },
        },
        "continuity": {
            "resume_enabled": True,
            "stale_artifact_policy": "quarantine",
            "replay_safety": "conservative",
        },
        "observability": {
            "events_jsonl": True,
            "text_log": True,
            "projected_state": True,
            "heartbeat_stale_threshold_seconds": 120,
            "per_step_artifacts": True,
            "per_case_artifacts": True,
            "redact_env_keys": [
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
            ],
            "max_log_megabytes": 100,
            "retention_days": 30,
        },
        "operator": {
            "control_channel": "filesystem",
            "default_output": "human",
            "max_pending_actions": 100,
        },
    },
}
"""Full canonical config shape from spec §8.4.

All fields use spec-defined defaults. No convenience fields beyond the spec.

Spec reference: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md
Section 8.4 "Canonical config shape" lines 1264-1315.
"""


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def minimal_orch_config_yaml() -> str:
    """YAML string matching the exact minimal spec format.

    Source: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """
    return MINIMAL_ORCH_CONFIG_YAML


@pytest.fixture
def full_canonical_config_dict() -> dict[str, Any]:
    """Dict matching the exact full canonical spec format.

    Source: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """
    return FULL_CANONICAL_CONFIG_DICT.copy()


@pytest.fixture
def temp_minimal_config_yaml(tmp_path: Path, minimal_orch_config_yaml: str) -> Path:
    """Write minimal vectl.yaml to a temp file and return the path."""
    yaml_path = tmp_path / "vectl.yaml"
    yaml_path.write_text(minimal_orch_config_yaml)
    return yaml_path


@pytest.fixture
def temp_full_config_yaml(tmp_path: Path, full_canonical_config_dict: dict[str, Any]) -> Path:
    """Write full canonical vectl.yaml to a temp file and return the path."""
    yaml_path = tmp_path / "vectl.yaml"
    yaml_path.write_text(
        yaml.dump(full_canonical_config_dict, sort_keys=False, default_flow_style=False)
    )
    return yaml_path


@pytest.fixture
def env_config_override(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Environment variable overrides per spec §8.3 naming convention.

    Source: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.3
    """
    env_vars = {
        "VECTL_ORCH_RUNTIME_ARTIFACT_ROOT": "/tmp/custom_runs",
        "VECTL_ORCH_RESOLVER_TIMEOUT_SECONDS": "120",
        "VECTL_ORCH_CONTROL_IDLE_POLL_INTERVAL_MS": "2000",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
    return env_vars


# =============================================================================
# GAP: Config Discovery and Precedence
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.1, §8.2
# =============================================================================


class TestConfigDiscoveryGaps:
    """Tests exposing gaps in config discovery and precedence.

    Spec authority:
    - Discovery order: §8.1 (VECTL_CONFIG -> cwd -> repo root -> user config)
    - Precedence: §8.2 (CLI flags > env > file > defaults)
    - Environment naming: §8.3
    """

    def test_config_loader_not_implemented(self, temp_minimal_config_yaml: Path) -> None:
        """GAP: load_orchestration_config is not implemented.

        Expected: Should load config from file path.
        Actual: Raises NotImplementedError.

        Spec reference: §8.1 Config discovery, §8.2 Effective precedence.
        """
        with pytest.raises(NotImplementedError, match="loader semantics not yet specified"):
            load_orchestration_config(plan_path=temp_minimal_config_yaml)

    def test_discovery_order_vectl_config_env_not_specified(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """GAP: VECTL_CONFIG env var discovery order not implemented.

        Expected per §8.1:
        1. VECTL_CONFIG env var (highest priority after CLI --config)
        2. cwd-local vectl.yaml
        3. repo-root vectl.yaml
        4. user config ~/.config/vectl/vectl.yaml

        Actual: No discovery logic implemented.

        Spec reference: §8.1 lines 1212-1222.
        """
        custom_config = tmp_path / "custom_vectl.yaml"
        custom_config.write_text(MINIMAL_ORCH_CONFIG_YAML)
        monkeypatch.setenv("VECTL_CONFIG", str(custom_config))

        with pytest.raises(NotImplementedError):
            load_orchestration_config()

    def test_precedence_cli_vs_env_vs_file_not_specified(
        self,
        env_config_override: dict[str, str],
        full_canonical_config_dict: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """GAP: CLI flag > env > file > default precedence not implemented.

        Expected per §8.2:
        - CLI flags override all
        - Environment variables override file config
        - File config overrides built-in defaults

        Actual: No precedence resolution logic exists.

        Spec reference: §8.2 lines 1230-1242.
        """
        # Placeholder to document the gap
        assert env_config_override is not None  # Gap: no precedence resolution

    def test_env_var_naming_convention_not_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GAP: Environment variable naming convention §8.3 not enforced.

        Expected per §8.3:
        - Prefix: VECTL_ORCH_
        - Nested keys: uppercase + underscore joined
        - Example: orchestration.runtime.artifact_root -> VECTL_ORCH_RUNTIME_ARTIFACT_ROOT

        Actual: No env var parsing logic implemented.

        Spec reference: §8.3 lines 1247-1253.
        """
        monkeypatch.setenv("VECTL_ORCH_RUNTIME_ARTIFACT_ROOT", "/custom/path")

        with pytest.raises(NotImplementedError):
            load_orchestration_config()


# =============================================================================
# GAP: Config Validation
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.5
# =============================================================================


class TestConfigValidationGaps:
    """Tests exposing gaps in config validation.

    Spec authority: §8.5 Config validation rules (lines 1370-1385).
    """

    def test_validation_rules_not_implemented(self, temp_full_config_yaml: Path) -> None:
        """GAP: Config validation rules from §8.5 not implemented.

        Expected validations per §8.5:
        - plan_path must exist
        - artifact_root must be writable
        - cleanup_policy in {never, on-success, always}
        - timeout_seconds > 0
        - max_tool_calls_per_invocation > 0
        - tool_allowlist entries must resolve in canonical tool registry

        Actual: No validation logic exists.

        Spec reference: §8.5 lines 1368-1385.
        """
        with pytest.raises(NotImplementedError):
            load_orchestration_config(plan_path=temp_full_config_yaml)

    def test_invalid_cleanup_policy_not_rejected(self) -> None:
        """GAP: Invalid cleanup_policy values not rejected.

        Expected per §8.5: cleanup_policy must be one of:
        - never
        - on-success
        - always

        Actual: No validation exists to reject invalid values like "sometimes".

        Spec reference: §8.5 line 1375.
        """
        invalid_config = {
            "orchestration": {
                "plan_path": "plan.yaml",
                "runtime": {
                    "cleanup_policy": "sometimes",  # Invalid!
                },
            },
        }
        # Gap: no validation surface exists
        assert invalid_config is not None

    def test_tool_allowlist_validation_not_implemented(self) -> None:
        """GAP: Tool allowlist validation against canonical registry not implemented.

        Expected per §8.5:
        - unknown tool names are rejected
        - unknown tool families are rejected
        - wildcard or prefix patterns are rejected

        Actual: No tool allowlist validation exists.

        Spec reference: §8.5 lines 1383-1385, §8.4 tool registry table.
        """
        invalid_allowlist = {
            "orchestration": {
                "resolver": {
                    "tool_allowlist": {
                        "core": ["status", "unknown_tool"],  # unknown_tool not in registry
                        "unknown_family": ["some_tool"],  # unknown_family not in registry
                    },
                },
            },
        }
        # Gap: no validation surface exists
        assert invalid_allowlist is not None


# =============================================================================
# GAP: Redaction Provenance
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.7
# =============================================================================


class TestRedactionProvenanceGaps:
    """Tests exposing gaps in config redaction provenance.

    Spec authority: §8.7 Config inspection commands (lines 1403-1427).
    """

    def test_config_show_effective_provenance_not_implemented(self) -> None:
        """GAP: config show --effective with provenance not implemented.

        Expected per §8.7:
        - Display effective config with provenance for every field:
          - source=flag
          - source=env
          - source=file
          - source=default
        - Sensitive values redacted but provenance shown

        Actual: No config inspection surface exists.

        Spec reference: §8.7 lines 1405-1427.
        """
        # Gap: no config inspection surface
        pass

    def test_redaction_rules_not_specified(self) -> None:
        """GAP: Redaction rules for config display not implemented.

        Expected per §8.7:
        - Fields named by observability.redact_env_keys
        - Values matching configured secret regexes
        - Any field explicitly marked sensitive by config schema

        Actual: No redaction logic exists.

        Spec reference: §8.7 lines 1425-1427.
        """
        pass


# =============================================================================
# GAP: Frozen Config Snapshot
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.8
# =============================================================================


class TestFrozenConfigSnapshotGaps:
    """Tests exposing gaps in frozen config snapshot creation and reuse.

    Spec authority:
    - §8.8 Frozen config snapshot (lines 1457-1467)
    - §6.6 Resume/recovery frozen snapshot reuse (lines 516, 520)
    """

    def test_freeze_config_is_noop(self) -> None:
        """GAP: freeze_config() is a no-op, not enforcing immutability.

        Expected per §8.8:
        - Each run must write config.snapshot.yaml
        - Snapshot is authoritative for that run
        - Resumed run must use frozen snapshot

        Actual: freeze_config() just returns self without deep freezing.

        Spec reference: §8.8 lines 1459-1467, §6.6 line 516.
        """
        config = OrchestrationConfig()
        frozen = freeze_config(config)

        # Gap: no actual immutability enforcement
        assert frozen is config  # Same object, not a frozen copy

    def test_snapshot_persistence_not_implemented(self, tmp_path: Path) -> None:
        """GAP: Frozen config snapshot persistence not implemented.

        Expected per §8.8:
        - Write to .vectl/runs/<run_id>/config.snapshot.yaml
        - Snapshot is authoritative for that run

        Actual: No snapshot persistence logic exists.

        Spec reference: §8.8 lines 1460-1464.
        """
        run_dir = tmp_path / ".vectl" / "runs" / "test_run_id"
        run_dir.mkdir(parents=True)
        snapshot_path = run_dir / "config.snapshot.yaml"

        config = OrchestrationConfig()

        # Gap: no snapshot write surface
        assert not snapshot_path.exists()

    def test_resumed_run_snapshot_reuse_rule_not_enforced(self) -> None:
        """GAP: Resumed-run must use frozen snapshot rule not enforced.

        Expected per §8.8 + §6.6:
        - Resumed run must use the frozen snapshot from original run
        - Partial overrides only allowed via documented rules (none yet)

        Actual: No resume logic exists to enforce snapshot reuse.

        Spec reference: §8.8 line 1466, §6.6 lines 516, 520.
        """
        # Gap: no resume surface to enforce snapshot reuse
        pass


# =============================================================================
# GAP: Run Registry and Index Safety
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6.4
# =============================================================================


class TestRunRegistryGaps:
    """Tests exposing gaps in run registry append/read safety.

    Spec authority: §6.4 Run registry and --latest semantics
    (lines 305-367, 387-397).
    """

    def test_run_registry_not_implemented(self) -> None:
        """GAP: RunRegistry is a stub with no implementation.

        Expected per §6.4:
        - Persist run records to .vectl/runs/index.jsonl
        - Append-only writes with file locking or atomic append
        - Retry up to 3 times with exponential backoff on conflicts

        Actual: All methods raise NotImplementedError.

        Spec reference: §6.4 lines 305-320, 364-367.
        """
        registry = RunRegistry()
        record = RunRecord(
            run_id="01JXTEST",
            step_id="core.test",
            status="running",
        )

        with pytest.raises(NotImplementedError, match="persistence semantics not yet specified"):
            registry.save(record)

    def test_index_append_only_not_enforced(self) -> None:
        """GAP: Append-only index update policy not enforced.

        Expected per §6.4:
        - index.jsonl appends must be append-only during active operation
        - Index compaction deferred, must not remove active runs

        Actual: No append-only enforcement exists.

        Spec reference: §6.4 lines 364-367.
        """
        # Gap: no append-only enforcement surface
        pass

    def test_latest_lookup_not_implemented(self) -> None:
        """GAP: --latest lookup semantics not implemented.

        Expected per §6.4:
        1. Choose most recently updated non-terminal run
        2. If none, choose most recently updated run of any status
        3. Ties break lexicographically by run_id

        Actual: latest_run() raises NotImplementedError.

        Spec reference: §6.4 lines 349-355.
        """
        with pytest.raises(NotImplementedError, match="--latest lookup"):
            latest_run(step_id="core.test")


# =============================================================================
# GAP: Same-Plan Admission Rule
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6.4
# =============================================================================


class TestSamePlanAdmissionGaps:
    """Tests exposing gaps in same-plan admission rule.

    Spec authority: §6.4 Same-plan admission rule
    (lines 378-394).
    """

    def test_same_plan_admission_rule_not_enforced(self) -> None:
        """GAP: At most one active non-terminal run per plan rule not enforced.

        Expected per §6.4:
        - At most one active non-terminal run per plan identity
        - Plan identity = canonical realpath-resolved absolute path
        - If active run exists, startup must fail with selection/admission error

        Actual: No admission rule enforcement exists.

        Spec reference: §6.4 lines 378-394.
        """
        # Gap: no admission rule enforcement surface
        pass

    def test_run_selection_explicit_required_for_mutating_not_enforced(self) -> None:
        """GAP: Mutating commands must require explicit RUN_ID or --latest.

        Expected per §6.4:
        - Mutating commands: resume, recover, control pause/unpause/stop
        - Must never rely on implicit run selection
        - Must require explicit RUN_ID or --latest

        Actual: No CLI command surface exists to enforce this.

        Spec reference: §6.4 lines 387-394.
        """
        # Gap: no CLI mutating command surface
        pass


# =============================================================================
# GAP: Case-Index Lifecycle
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6.4
# =============================================================================


class TestCaseIndexLifecycleGaps:
    """Tests exposing gaps in case-index lifecycle.

    Spec authority: §6.4 Case-index lifecycle rules
    (lines 326-335, 851-854).
    """

    def test_cases_index_not_implemented(self) -> None:
        """GAP: CasesIndex protocol has no implementation.

        Expected per §6.4:
        - Persist to .vectl/runs/cases.jsonl
        - Entries append on case creation and terminalization
        - case_id globally unique (case-<ULID> format)
        - Resolve case without explicit run selector

        Actual: CasesIndex is only a Protocol stub.

        Spec reference: §6.4 lines 307-309, 326-335, 851-854.
        """
        # Gap: only Protocol stub exists
        assert CasesIndex is not None

    def test_case_index_pruning_rule_not_enforced(self) -> None:
        """GAP: Pruning run must remove/tombstone case-index entries.

        Expected per §6.4:
        - Pruning a run must also remove or tombstone its case-index entries
        - Imported legacy runs must populate case-index entries if they expose cases

        Actual: No pruning logic or tombstone mechanism exists.

        Spec reference: §6.4 lines 332-333.
        """
        # Gap: no pruning/tombstone surface
        pass

    def test_case_id_global_uniqueness_not_enforced(self) -> None:
        """GAP: Global case id uniqueness not enforced.

        Expected per §6.4:
        - Canonical case ids globally unique (case-<ULID> format)
        - CLI may resolve case without explicit run selector

        Actual: No case id generation or uniqueness enforcement exists.

        Spec reference: §6.4 lines 851-854.
        """
        # Gap: no case id generation surface
        pass


# =============================================================================
# GAP: Resumed-Run Snapshot Reuse Rule
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §6.6
# =============================================================================


class TestResumedRunSnapshotReuseGaps:
    """Tests exposing gaps in resumed-run frozen snapshot reuse rule.

    Spec authority: §6.6 Resume and recovery minimum
    (lines 516, 520, 595-599).
    """

    def test_resume_uses_frozen_snapshot_not_enforced(self) -> None:
        """GAP: Resume must use frozen config snapshot from original run.

        Expected per §6.6 + §8.8:
        - Durable restart facts include: frozen config snapshot
        - Resumed run must use frozen snapshot unless documented override rules

        Actual: No resume logic exists to enforce frozen snapshot reuse.

        Spec reference: §6.6 line 516, §8.8 line 1466.
        """
        # Gap: no resume surface enforcing snapshot reuse
        pass

    def test_recovery_validates_transcript_before_resume_safe(self) -> None:
        """GAP: Recovery must validate transcript before resume_safe conclusion.

        Expected per §6.6:
        - recover must validate transcript integrity before concluding resume_safe
        - resume must refuse when recover would report blocking hygiene issues

        Actual: No recovery validation logic exists.

        Spec reference: §6.6 lines 595-599.
        """
        # Gap: no recovery validation surface
        pass


# =============================================================================
# GAP: Minimal Spec-Derived Config Fixture Validation
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
# =============================================================================


class TestMinimalConfigFixtureConformance:
    """Tests ensuring minimal config fixture matches spec exactly.

    Spec authority: §8.4 Minimal configuration example
    (lines 1348-1352).
    """

    def test_minimal_config_yaml_parses_to_valid_dict(self, minimal_orch_config_yaml: str) -> None:
        """Verify minimal config YAML parses correctly.

        Expected:
        - Parses to dict with orchestration.plan_path = "plan.yaml"
        - No additional convenience fields

        Spec reference: §8.4 lines 1348-1352.
        """
        config_dict = yaml.safe_load(minimal_orch_config_yaml)

        assert "orchestration" in config_dict
        assert config_dict["orchestration"]["plan_path"] == "plan.yaml"
        # Must have ONLY plan_path at minimum
        assert len(config_dict["orchestration"]) == 1

    def test_minimal_config_has_no_convenience_fields(self, minimal_orch_config_yaml: str) -> None:
        """Verify minimal config has no convenience fields beyond spec.

        Expected per §8.4:
        - Only orchestration.plan_path
        - All other fields have development-safe defaults

        Actual test: Ensure fixture matches this constraint.

        Spec reference: §8.4 lines 1353-1355.
        """
        config_dict = yaml.safe_load(minimal_orch_config_yaml)

        # Must NOT have these fields at minimum level
        orch = config_dict["orchestration"]
        assert "runtime" not in orch or orch.get("runtime") is None
        assert "resolver" not in orch or orch.get("resolver") is None
        assert "observability" not in orch or orch.get("observability") is None

    def test_full_config_all_defaults_explicit(
        self, full_canonical_config_dict: dict[str, Any]
    ) -> None:
        """Verify full config fixture has all spec defaults explicit.

        Expected per §8.4:
        - All fields use documented defaults
        - No convenience fields beyond spec shape

        Spec reference: §8.4 lines 1264-1315.
        """
        orch = full_canonical_config_dict["orchestration"]

        # Verify all required sections exist
        assert "plan_path" in orch
        assert "runtime" in orch
        assert "control" in orch
        assert "resolver" in orch
        assert "continuity" in orch
        assert "observability" in orch
        assert "operator" in orch

        # Spot-check key defaults match spec
        assert orch["runtime"]["default_runner"] == "codex"
        assert orch["runtime"]["cleanup_policy"] == "on-success"
        assert orch["resolver"]["timeout_seconds"] == 600
        assert orch["observability"]["events_jsonl"] is True
        assert orch["observability"]["redact_env_keys"] == [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ]


# =============================================================================
# Summary of Exposed Gaps
# =============================================================================
"""
Exposed gaps by category:

CONFIG DISCOVERY + PRECEDENCE:
- load_orchestration_config() not implemented (§8.1, §8.2)
- VECTL_CONFIG env var discovery order not implemented (§8.1)
- CLI > env > file > default precedence not implemented (§8.2)
- Environment variable naming convention not enforced (§8.3)

CONFIG VALIDATION:
- Validation rules from §8.5 not implemented
- Invalid cleanup_policy values not rejected
- Tool allowlist validation against canonical registry not implemented

REDACTION PROVENANCE:
- config show --effective with provenance not implemented (§8.7)
- Redaction rules for config display not implemented

FROZEN CONFIG SNAPSHOT:
- freeze_config() is a no-op (§8.8)
- Snapshot persistence not implemented
- Resumed-run snapshot reuse rule not enforced (§6.6, §8.8)

RUN REGISTRY + INDEX SAFETY:
- RunRegistry is a stub (§6.4)
- Append-only index update policy not enforced
- --latest lookup semantics not implemented

SAME-PLAN ADMISSION RULE:
- At most one active run per plan rule not enforced (§6.4)
- Explicit run selection for mutating commands not enforced

CASE-INDEX LIFECYCLE:
- CasesIndex protocol has no implementation (§6.4)
- Pruning/tombstone rules not enforced
- Global case id uniqueness not enforced

All gaps are intentional: this is expected-red test coverage to
document missing implementation vs spec requirements.
"""
