"""Cutover validation helpers for orchestration recovery.

Extracted from recovery compatibility module.
"""

from __future__ import annotations

from vectl.orchestration.recovery import CutoverValidationResult, LegacyRunStatus, RecoveryOutcome

class CutoverValidator:
    """
    Surface for validating orchestration-plane cutover readiness.

    Retirement criteria:
        1. An equivalent target component implementation exists
        2. Behavior is covered by tests in the new location
        3. Docs no longer rely on legacy paths as the primary executable reference
        4. The migration does not erase currently known-good behavior
    """

    def __init__(self, *, registry: RunRegistry | None = None) -> None:
        self._registry = registry if registry is not None else RunRegistry()

    def _native_runs_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        """Return native orchestration runs for ``step_id`` from canonical store."""

        return tuple(
            record
            for record in self._registry.all_for_step(step_id)
            if record.source == "orchestration_native"
        )

    def validate_cutover_readiness(self) -> CutoverValidationResult:
        """
        Validate whether orchestration-plane cutover criteria are met.

        Returns:
            CutoverValidationResult describing whether imported legacy runs are
            retired and free of migration/recovery blockers.
        """
        imported_runs = self._registry.imported_legacy_runs()
        criteria_results: list[str] = []
        blocking_items: list[str] = []
        recommendations: list[str] = []

        if not imported_runs:
            criteria_results.append(
                "criterion.1_equivalent_target_component: met "
                "(no imported legacy runs require target-component equivalence checks)"
            )
            criteria_results.append(
                "criterion.2_behavior_covered_by_new_location_tests: met "
                "(no imported legacy runs require new-location behavior evidence)"
            )
            criteria_results.append(
                "criterion.3_docs_no_longer_primary_legacy_reference: met "
                "(no imported legacy runs remain in pre-retirement states)"
            )
            criteria_results.append(
                "criterion.4_no_known_good_behavior_erasure: met "
                "(no imported legacy runs with continuity/recovery blockers)"
            )
            return CutoverValidationResult(
                can_cutover=True,
                criteria_results=tuple(criteria_results),
                blocking_items=(),
                recommendations=(),
            )

        missing_native_steps: list[str] = []
        missing_success_steps: list[str] = []
        docs_primary_reference_runs: list[str] = []

        for record in imported_runs:
            legacy_ref = record.legacy_run_id or record.run_id
            state = record.legacy_migration_state or LegacyRunStatus.PARALLEL.value
            native_runs = self._native_runs_for_step(record.step_id)

            if not native_runs:
                missing_native_steps.append(record.step_id)
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} step_id={record.step_id} "
                    "has no orchestration-native equivalent execution evidence"
                )

            if not any(native.status == "success" for native in native_runs):
                missing_success_steps.append(record.step_id)
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} step_id={record.step_id} "
                    "has no successful orchestration-native execution evidence"
                )

            if state not in {LegacyRunStatus.DEPRECATED.value, LegacyRunStatus.RETIRED.value}:
                docs_primary_reference_runs.append(legacy_ref)
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} migration_state={state} "
                    "is pre-doc-retirement; legacy path may still be primary reference"
                )

            if state != LegacyRunStatus.RETIRED.value:
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} migration_state={state} "
                    "must reach retired before cutover"
                )

            if record.continuity_blocker:
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} continuity_blocker={record.continuity_blocker}"
                )

            cases = self._registry.cases_for_run(record.run_id)
            open_cases = [case for case in cases if case.status == "open"]
            if open_cases:
                case_ids = ",".join(case.case_id for case in open_cases)
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} has open migration/recovery case(s): {case_ids}"
                )

            if record.status in {"running", "pending", "stall"}:
                blocking_items.append(
                    f"legacy_run_id={legacy_ref} remains active status={record.status}; "
                    "resolve or retire before cutover"
                )

        if not missing_native_steps:
            criteria_results.append(
                "criterion.1_equivalent_target_component: met "
                "(every imported legacy run step has orchestration-native execution evidence)"
            )
        else:
            deduped_steps = sorted(set(missing_native_steps))
            criteria_results.append(
                "criterion.1_equivalent_target_component: blocked "
                f"(missing native evidence for step(s): {', '.join(deduped_steps)})"
            )
            recommendations.append(
                "establish orchestration-native implementation coverage for every imported "
                "legacy run step before cutover"
            )

        if not missing_success_steps:
            criteria_results.append(
                "criterion.2_behavior_covered_by_new_location_tests: met "
                "(every imported legacy run step has successful orchestration-native evidence)"
            )
        else:
            deduped_steps = sorted(set(missing_success_steps))
            criteria_results.append(
                "criterion.2_behavior_covered_by_new_location_tests: blocked "
                f"(no successful native evidence for step(s): {', '.join(deduped_steps)})"
            )
            recommendations.append(
                "run and verify successful orchestration-native executions for imported "
                "legacy run steps before retirement"
            )

        if not docs_primary_reference_runs:
            criteria_results.append(
                "criterion.3_docs_no_longer_primary_legacy_reference: met "
                "(all imported legacy runs are in deprecated/retired migration states)"
            )
        else:
            deduped_runs = sorted(set(docs_primary_reference_runs))
            criteria_results.append(
                "criterion.3_docs_no_longer_primary_legacy_reference: blocked "
                f"(legacy migration state still pre-doc-retirement for: {', '.join(deduped_runs)})"
            )
            recommendations.append(
                "advance imported runs to deprecated/retired state only after docs are "
                "updated away from legacy-primary execution guidance"
            )

        recovery_blockers = tuple(
            item
            for item in blocking_items
            if (
                "continuity_blocker=" in item
                or "open migration/recovery case" in item
                or "remains active status=" in item
                or "must reach retired before cutover" in item
            )
        )
        if not recovery_blockers:
            criteria_results.append(
                "criterion.4_no_known_good_behavior_erasure: met "
                "(no continuity blockers, open migration/recovery cases, or active imported runs)"
            )
        else:
            criteria_results.append(
                "criterion.4_no_known_good_behavior_erasure: blocked "
                "(continuity blockers, active imported runs, or open migration/"
                "recovery cases remain)"
            )
            recommendations.append(
                "resolve continuity minimum blockers and close migration/recovery "
                "cases for imported runs"
            )
            recommendations.append(
                "advance imported runs through preferred/deprecated to retired "
                "before orchestration-plane cutover"
            )

        if not blocking_items:
            return CutoverValidationResult(
                can_cutover=True,
                criteria_results=tuple(criteria_results),
                blocking_items=(),
                recommendations=(),
            )

        return CutoverValidationResult(
            can_cutover=False,
            criteria_results=tuple(criteria_results),
            blocking_items=tuple(blocking_items),
            recommendations=tuple(recommendations),
        )
