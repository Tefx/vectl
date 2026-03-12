"""Tests for duplicate step ID migration contract: DAG preservation and dry-run/apply semantics.

Source:
    - docs/contracts/duplicate-id-migration-contract.yaml
    - step-id-migration-tooling.tests
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_contract() -> dict[str, Any]:
    contract_path = Path("docs/contracts/duplicate-id-migration-contract.yaml")
    payload = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


# ---------------------------------------------------------------------------
# DAG Preservation: depends_on rewrite rules
# ---------------------------------------------------------------------------


class TestDAGPreservation:
    """Contract: depends_on_rewrite_rules invariants must hold."""

    def test_rewrite_algorithm_preserves_edge_cardinality(self) -> None:
        """Each step's depends_on list must have same length before/after rewrite."""
        contract = _load_contract()
        rewrite_spec = contract["depends_on_rewrite_rules"]

        # Verify the invariant is documented
        assert "invariants" in rewrite_spec
        invariants = rewrite_spec["invariants"]
        assert any("Edge cardinality preserved" in inv for inv in invariants), (
            "Contract must require edge cardinality preservation"
        )

    def test_rewrite_algorithm_preserves_topological_order(self) -> None:
        """Topological order within each phase must be preserved after rewrite."""
        contract = _load_contract()
        rewrite_spec = contract["depends_on_rewrite_rules"]

        assert "invariants" in rewrite_spec
        invariants = rewrite_spec["invariants"]
        assert any("Topological order preserved" in inv for inv in invariants), (
            "Contract must require topological order preservation"
        )

    def test_rewrite_algorithm_no_new_cycles(self) -> None:
        """Rewrite must not introduce new cycles in step DAG."""
        contract = _load_contract()
        rewrite_spec = contract["depends_on_rewrite_rules"]

        assert "invariants" in rewrite_spec
        invariants = rewrite_spec["invariants"]
        assert any("No new cycles introduced" in inv for inv in invariants), (
            "Contract must guarantee no cycles introduced"
        )

    def test_no_cross_phase_edges_created(self) -> None:
        """Rewrite must not create cross-phase edges (DAG remains phase-scoped)."""
        contract = _load_contract()
        rewrite_spec = contract["depends_on_rewrite_rules"]

        # Verify rewrite algorithm section exists
        assert "rewrite_algorithm" in rewrite_spec
        algorithm = rewrite_spec["rewrite_algorithm"]
        algorithm_text = " ".join(algorithm)

        # Contract explicitly states "do not create cross-phase edges"
        assert "cross-phase" in algorithm_text.lower(), (
            "Contract must explicitly restrict cross-phase edges"
        )


# ---------------------------------------------------------------------------
# Rename Algorithm Guarantees
# ------------------------------------------------------------------------


class TestRenameAlgorithm:
    """Contract: rename_algorithm guarantees."""

    def test_deterministic_rename(self) -> None:
        """Identical input plan must produce identical old->new map."""
        contract = _load_contract()
        guarantees = contract["rename_algorithm"]["guarantees"]

        assert any("Deterministic" in g or "deterministic" in g.lower() for g in guarantees), (
            "Contract must guarantee deterministic rename"
        )

    def test_injective_mapping(self) -> None:
        """Each migrated occurrence must get exactly one unique new ID."""
        contract = _load_contract()
        guarantees = contract["rename_algorithm"]["guarantees"]

        assert any("Injective" in g or "injective" in g.lower() for g in guarantees), (
            "Contract must guarantee injective mapping"
        )

    def test_stable_dry_run(self) -> None:
        """Repeated dry-runs without mutation must return identical map."""
        contract = _load_contract()
        guarantees = contract["rename_algorithm"]["guarantees"]

        assert any("Stable dry-run" in g or "stable" in g.lower() for g in guarantees), (
            "Contract must guarantee stable dry-run output"
        )

    def test_canonical_keeps_original_id(self) -> None:
        """First occurrence in plan order keeps original ID."""
        contract = _load_contract()
        rule = contract["rename_algorithm"]["rule"]

        assert rule.get("canonical_keeps_original") is True, (
            "Canonical occurrence must keep original ID"
        )

    def test_noncanonical_uses_phase_prefix(self) -> None:
        """Non-canonical occurrences must use phase_id.template format."""
        contract = _load_contract()
        rule = contract["rename_algorithm"]["rule"]

        assert "noncanonical_template" in rule
        assert "{phase_id}" in rule["noncanonical_template"], (
            "Non-canonical template must include phase_id"
        )


# ---------------------------------------------------------------------------
# Dry-Run Schema Validation
# ------------------------------------------------------------------------


class TestDryRunSchema:
    """Contract: dry_run_schema format and required fields."""

    def test_dry_run_required_fields(self) -> None:
        """Dry-run output must include all required fields."""
        contract = _load_contract()
        required = set(contract["dry_run_schema"]["required_fields"])

        expected = {
            "run_mode",
            "duplicate_groups",
            "rename_map",
            "depends_on_rewrites",
            "affected_phases",
            "claimed_step_conflicts",
            "compatibility_notes",
            "requires_manual_follow_up",
        }
        assert expected.issubset(required), f"Missing required fields: {expected - required}"

    def test_run_mode_is_dry_run(self) -> None:
        """run_mode must be 'dry-run' in dry-run output."""
        contract = _load_contract()
        field_contract = contract["dry_run_schema"]["field_contract"]

        assert field_contract["run_mode"] == "dry-run", "run_mode must be 'dry-run'"

    def test_rename_map_format(self) -> None:
        """rename_map entries must include phase_id, old_step_id, new_step_id."""
        contract = _load_contract()
        field_contract = contract["dry_run_schema"]["field_contract"]

        rename_map_spec = field_contract["rename_map"]
        assert "phase_id" in rename_map_spec
        assert "old_step_id" in rename_map_spec
        assert "new_step_id" in rename_map_spec

    def test_depends_on_rewrites_format(self) -> None:
        """depends_on_rewrites must include old and new depends_on values."""
        contract = _load_contract()
        field_contract = contract["dry_run_schema"]["field_contract"]

        rewrite_spec = field_contract["depends_on_rewrites"]
        assert "old_depends_on" in rewrite_spec
        assert "new_depends_on" in rewrite_spec

    def test_claimed_step_conflicts_format(self) -> None:
        """claimed_step_conflicts must identify conflicted steps."""
        contract = _load_contract()
        field_contract = contract["dry_run_schema"]["field_contract"]

        conflict_spec = field_contract["claimed_step_conflicts"]
        assert "phase_id" in conflict_spec
        assert "step_id" in conflict_spec
        assert "claimed_by" in conflict_spec


# ---------------------------------------------------------------------------
# Apply-Mode Semantics
# ------------------------------------------------------------------------


class TestApplyMode:
    """Contract: apply-mode behavior assertions."""

    def test_claimed_step_blocked(self) -> None:
        """Apply must be blocked if any duplicate is claimed."""
        contract = _load_contract()
        policy = contract["claimed_step_policy"]

        assert policy["mode"] == "block-with-guidance", "Must block apply when duplicate is claimed"

    def test_claimed_step_reports_conflict(self) -> None:
        """Dry-run must report claimed conflicts in claimed_step_conflicts."""
        contract = _load_contract()
        behavior = contract["claimed_step_policy"]["behavior"]

        assert any("claimed_step_conflicts" in b for b in behavior), (
            "Must report claimed conflicts in dry-run"
        )

    def test_idempotent_when_no_duplicates(self) -> None:
        """Apply is no-op when no duplicate groups exist."""
        contract = _load_contract()
        idempotence = contract["idempotence_and_interruption"]["idempotence"]

        assert any("no duplicate" in i.lower() or "no-op" in i.lower() for i in idempotence), (
            "Must be no-op with no duplicates"
        )

    def test_idempotent_after_success(self) -> None:
        """After successful apply, rerunning returns empty rename_map."""
        contract = _load_contract()
        idempotence = contract["idempotence_and_interruption"]["idempotence"]

        assert any(
            "rerunning apply" in i.lower() or "empty rename_map" in i.lower() for i in idempotence
        ), "Must be idempotent after success"

    def test_no_persisted_state_on_interruption(self) -> None:
        """If interrupted before save, no persisted migration state allowed."""
        contract = _load_contract()
        interruption = contract["idempotence_and_interruption"]["interruption"]

        assert any(
            "before save" in i.lower() or "no persisted" in i.lower() for i in interruption
        ), "Must not persist state on interruption before save"


# ---------------------------------------------------------------------------
# Half-Automatic Retry Contract
# ------------------------------------------------------------------------


class TestHalfAutomaticRetry:
    """Contract: --auto-migrate retry semantics."""

    def test_requires_explicit_opt_in(self) -> None:
        """Auto-migration must require explicit --auto-migrate flag."""
        contract = _load_contract()
        trigger = contract["half_automatic_retry_contract"]["trigger"]

        assert "required_opt_in" in trigger
        assert trigger["required_opt_in"] == "--auto-migrate", "Must require --auto-migrate flag"

    def test_without_flag_fails_with_guidance(self) -> None:
        """Without --auto-migrate, must fail with repair recommendation."""
        contract = _load_contract()
        behavior = contract["half_automatic_retry_contract"]["behavior"]

        assert any("Without --auto-migrate" in b and "fail" in b.lower() for b in behavior), (
            "Must fail without flag"
        )

    def test_with_flag_runs_repair(self) -> None:
        """With --auto-migrate, must run repair first."""
        contract = _load_contract()
        behavior = contract["half_automatic_retry_contract"]["behavior"]

        assert any("With --auto-migrate" in b and "repair" in b.lower() for b in behavior), (
            "Must run repair with flag"
        )

    def test_max_one_retry(self) -> None:
        """Retry must be attempted exactly once."""
        contract = _load_contract()
        constraints = contract["half_automatic_retry_contract"]["retry_constraints"]

        assert constraints["max_retries"] == 1, "Must retry exactly once"

    def test_no_additional_auto_retry(self) -> None:
        """No additional auto-retry beyond the single retry."""
        contract = _load_contract()
        constraints = contract["half_automatic_retry_contract"]["retry_constraints"]

        assert constraints["additional_auto_retry"] is False, (
            "Must not auto-retry beyond single attempt"
        )


# ---------------------------------------------------------------------------
# Migration Evidence Schema
# ------------------------------------------------------------------------


class TestMigrationEvidence:
    """Contract: migration_evidence_schema requirements."""

    def test_evidence_required_fields(self) -> None:
        """Migration evidence must include all required fields."""
        contract = _load_contract()
        required = set(contract["migration_evidence_schema"]["required_fields"])

        expected = {
            "command",
            "command_args",
            "run_mode",
            "migrated",
            "rename_map",
            "depends_on_rewrites",
            "affected_phases",
            "claimed_step_conflicts",
            "retry",
        }
        assert expected.issubset(required), f"Missing required fields: {expected - required}"

    def test_evidence_run_mode_includes_apply(self) -> None:
        """run_mode must include 'apply' mode."""
        contract = _load_contract()
        field_contract = contract["migration_evidence_schema"]["field_contract"]

        assert "apply" in field_contract["run_mode"].lower(), "Evidence must support apply mode"

    def test_evidence_retry_format(self) -> None:
        """retry field must include attempted, succeeded, error."""
        contract = _load_contract()
        field_contract = contract["migration_evidence_schema"]["field_contract"]

        retry_spec = field_contract["retry"]
        assert "attempted" in retry_spec
        assert "succeeded" in retry_spec
        assert "error" in retry_spec


# ---------------------------------------------------------------------------
# Compatibility Policy
# ------------------------------------------------------------------------


class TestCompatibilityPolicy:
    """Contract: backward compatibility stance."""

    def test_old_id_aliases_not_supported(self) -> None:
        """Old IDs must not be permanent aliases."""
        contract = _load_contract()
        policy = contract["compatibility_policy"]

        assert policy["old_id_aliases_supported"] is False, (
            "Old IDs must not become permanent aliases"
        )

    def test_old_ids_visible_in_mapping(self) -> None:
        """Old IDs must be surfaced in dry-run/apply mapping output."""
        contract = _load_contract()
        visibility = contract["compatibility_policy"]["old_id_visibility"]

        assert any("dry-run" in v.lower() or "mapping" in v.lower() for v in visibility), (
            "Old IDs must be visible in output"
        )

    def test_old_id_targeting_fails_with_guidance(self) -> None:
        """Targeting old ID after migration must fail with guidance."""
        contract = _load_contract()
        behavior = contract["compatibility_policy"]["post_migration_old_id_behavior"]

        assert any("fails" in b.lower() and "guidance" in b.lower() for b in behavior), (
            "Must fail with guidance when targeting old ID"
        )
