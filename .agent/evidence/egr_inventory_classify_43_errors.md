## refs Read Confirmation (MANDATORY)
- tools/vectl/README.md — NOT READ: required `read` was attempted at `.vectl/worktrees/egr_inventory_classify_43_errors/tools/vectl/README.md`, but the file is absent in this isolated worktree (`ENOENT`). Adjacent repo README files exist, but this exact required ref could not be found.

## Commands
```bash
# Executed from isolated worktree: /Users/tefx/Projects/vectl/.vectl/worktrees/egr_inventory_classify_43_errors
# Note: the step text requested /Users/tefx/Projects/vectl, but the worktree-isolation mandate required all work inside the isolated worktree.
uv run vectl validate
# exit code: 1
```

## Raw Validation Output
```
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
  ERROR: Completed evidence guard: step 
'claim-consistency-recovery.retest-gate-lint-and-type-blockers-fix' in phase 
'claim-consistency-recovery' has unclosed failure evidence (FAIL). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'claim-consistency-recovery.retest-gate-lint-and-type-blockers-retest' in phase 
'claim-consistency-recovery' has unclosed failure evidence (FAIL). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'step-id-resolver-hardening.retest-gate-blockers' in phase 
'step-id-resolver-hardening' has unclosed failure evidence (FAIL). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-execution-infra.retest-gate-blockers' in phase 'driver-execution-infra' 
has unclosed failure evidence (FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-judgment-expansion-replan.expected-red-decide' in phase 
'driver-judgment-expansion-replan' has unclosed failure evidence (FAIL). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-debt-system-wiring.full-plan-check' in phase 'driver-debt-system-wiring'
has unclosed failure evidence (failed tests). Add a later same-phase closure 
step with green/OPEN/gate_open_allowed=true evidence, or record 
expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-evolution-action-registry.verify-planner-dispatch' in phase 
'driver-evolution-action-registry' has unclosed failure evidence (FAIL). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-enhancement-observability.design-and-test' in phase 
'driver-enhancement-observability' has unclosed failure evidence (failed tests).
Add a later same-phase closure step with green/OPEN/gate_open_allowed=true 
evidence, or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-enhancement-streaming-progress.design-and-test' in phase 
'driver-enhancement-streaming-progress' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-enhancement-runner-recovery.design-and-test' in phase 
'driver-enhancement-runner-recovery' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-prompt-foundation.design-and-test' in phase 'driver-prompt-foundation' 
has unclosed failure evidence (failed tests, FAIL). Add a later same-phase 
closure step with green/OPEN/gate_open_allowed=true evidence, or record 
expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 'driver-judge-hardening-foundation.gate'
in phase 'driver-judge-hardening-foundation' has unclosed failure evidence 
(FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-judge-hardening-foundation.fix-contract-ambiguities' in phase 
'driver-judge-hardening-foundation' has unclosed failure evidence (FAIL). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-judge-hardening-foundation.retest-gate' in phase 
'driver-judge-hardening-foundation' has unclosed failure evidence (FAIL). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-judge-hardening-structured-output.gate' in phase 
'driver-judge-hardening-structured-output' has unclosed failure evidence (FAIL).
Add a later same-phase closure step with green/OPEN/gate_open_allowed=true 
evidence, or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'driver-judge-hardening-recovery-policy.design-and-test' in phase 
'driver-judge-hardening-recovery-policy' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-continuity-capability-safety.design-and-test' in phase 
'driver-continuity-capability-safety' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-live-smoke-foundation.ratify-live-smoke-policy' in phase 
'driver-live-smoke-foundation' has unclosed failure evidence (FAIL). Add a later
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-continuity-hygiene-deep-review.gate' in phase 
'driver-continuity-hygiene-deep-review' has unclosed failure evidence (FAIL). 
Add a later same-phase closure step with green/OPEN/gate_open_allowed=true 
evidence, or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 'driver-agent-selection-contract.gate' 
in phase 'driver-agent-selection-contract' has unclosed failure evidence (failed
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-agent-selection-implementation.fix-judge-runner-test-defaults' in phase 
'driver-agent-selection-implementation' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-agent-selection-implementation.retest-judge-runner-test-defaults' in 
phase 'driver-agent-selection-implementation' has unclosed failure evidence 
(failed tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'driver-agent-selection-deep-review.gate' in phase 
'driver-agent-selection-deep-review' has unclosed failure evidence 
(NEEDS_REVISION, FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 'orch_foundation.contract_tests' in 
phase 'orch_foundation' has unclosed failure evidence (failed tests). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 'orch_foundation.gate' in phase 
'orch_foundation' has unclosed failure evidence (failed tests). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'orch_operator_recovery_cutover.fix_recovery_contract_conformance' in phase 
'orch_operator_recovery_cutover' has unclosed failure evidence 
(gate_open_allowed=false). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'orch_operator_recovery_cutover.reverify_recovery_contract_conformance' in phase
'orch_operator_recovery_cutover' has unclosed failure evidence 
(gate_open_allowed=false). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'repo_regression_full_suite_gate.run-full-pytest-regression-gate' in phase 
'repo_regression_full_suite_gate' has unclosed failure evidence (failed tests). 
Add a later same-phase closure step with green/OPEN/gate_open_allowed=true 
evidence, or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics' in
phase 'repo_regression_full_suite_gate' has unclosed failure evidence 
(gate_open_allowed=false). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'repo_regression_triage.capture-full-suite-failure-inventory' in phase 
'repo_regression_test_intent_alignment' has unclosed failure evidence (failed 
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'repo_regression_expected_red_cleanup.realign-observability-red-tests-with-imple
mented-surfaces' in phase 'repo_regression_test_intent_alignment' has unclosed 
failure evidence (failed tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope' in
phase 'repo_regression_semantic_alignment' has unclosed failure evidence (failed
tests). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'cli_blackbox_finalization.full-plan-check' in phase 'cli_blackbox_finalization'
has unclosed failure evidence (runtime non-closure: fail). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'opencode_runner_verification.live-smoke' in phase 
'opencode_runner_verification' has unclosed failure evidence (runtime 
non-closure: fail, FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'opencode_runner_verification.final-gate' in phase 
'opencode_runner_verification' has unclosed failure evidence (failed tests, 
FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'arch-demolition-wave-1.dem-002-extract-shared-next-step-helper' in phase 
'arch-demolition-wave-1' has unclosed failure evidence (failed tests). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 
'arch-demolition-wave-6.dem-017-collapse-duplicate-step-id-leaf-wrappers-without
-breaking-outputs' in phase 'arch-demolition-wave-6' has unclosed failure 
evidence (FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'post_review_remediation.implement-completed-step-failure-evidence-guard' in 
phase 'post_review_remediation' has unclosed failure evidence 
(gate_open_allowed=false, NEEDS_REVISION, runtime non-closure: fail, failed 
tests, FAIL). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'post_review_remediation.reverify-completed-step-failure-evidence-guard' in 
phase 'post_review_remediation' has unclosed failure evidence 
(gate_open_allowed=false, NEEDS_REVISION, runtime non-closure: fail). Add a 
later same-phase closure step with green/OPEN/gate_open_allowed=true evidence, 
or record expected-red/non-blocking owner/lifecycle/gate-intersection 
disposition.
  ERROR: Completed evidence guard: step 'post_review_remediation.gate' in phase 
'post_review_remediation' has unclosed failure evidence (runtime non-closure: 
fail). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'post_review_remediation.backfill-missing-gate-evidence' in phase 
'post_review_remediation' has unclosed failure evidence (runtime non-closure: 
fail). Add a later same-phase closure step with 
green/OPEN/gate_open_allowed=true evidence, or record expected-red/non-blocking 
owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 
'invar_guard_remediation.final-regate-durable-uiux-artifact' in phase 
'invar_guard_remediation' has unclosed failure evidence (FAIL). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.
  ERROR: Completed evidence guard: step 'det_check_tests_gate' in phase 
'det_check_tests' has unclosed failure evidence (failed tests). Add a later 
same-phase closure step with green/OPEN/gate_open_allowed=true evidence, or 
record expected-red/non-blocking owner/lifecycle/gate-intersection disposition.

43 error(s), 0 warning(s)
```

## Completed-Evidence Guard Inventory
| error_index | offending_step_id | offending_phase_id | failure_signal | likely_group | provenance | disposition_needed | intersects_remaining_gates | current_code_verification_required | planned_owner_step |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `claim-consistency-recovery.retest-gate-lint-and-type-blockers-fix` | `claim-consistency-recovery` | FAIL | B_scoped_pass_global_fail | out_of_step_origin | gate_intersection_disposition | unknown | no | egr_dispose_scoped_pass_global_fail_rows |
| 2 | `claim-consistency-recovery.retest-gate-lint-and-type-blockers-retest` | `claim-consistency-recovery` | FAIL | B_scoped_pass_global_fail | out_of_step_origin | gate_intersection_disposition | unknown | no | egr_dispose_scoped_pass_global_fail_rows |
| 3 | `step-id-resolver-hardening.retest-gate-blockers` | `step-id-resolver-hardening` | FAIL | B_scoped_pass_global_fail | out_of_step_origin | gate_intersection_disposition | unknown | no | egr_dispose_scoped_pass_global_fail_rows |
| 4 | `driver-execution-infra.retest-gate-blockers` | `driver-execution-infra` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 5 | `driver-judgment-expansion-replan.expected-red-decide` | `driver-judgment-expansion-replan` | FAIL | A_superseded_by_orchestrator_removal | expected_red | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 6 | `driver-debt-system-wiring.full-plan-check` | `driver-debt-system-wiring` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 7 | `driver-evolution-action-registry.verify-planner-dispatch` | `driver-evolution-action-registry` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 8 | `driver-enhancement-observability.design-and-test` | `driver-enhancement-observability` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 9 | `driver-enhancement-streaming-progress.design-and-test` | `driver-enhancement-streaming-progress` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 10 | `driver-enhancement-runner-recovery.design-and-test` | `driver-enhancement-runner-recovery` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 11 | `driver-prompt-foundation.design-and-test` | `driver-prompt-foundation` | failed tests, FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 12 | `driver-judge-hardening-foundation.gate` | `driver-judge-hardening-foundation` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 13 | `driver-judge-hardening-foundation.fix-contract-ambiguities` | `driver-judge-hardening-foundation` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 14 | `driver-judge-hardening-foundation.retest-gate` | `driver-judge-hardening-foundation` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 15 | `driver-judge-hardening-structured-output.gate` | `driver-judge-hardening-structured-output` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 16 | `driver-judge-hardening-recovery-policy.design-and-test` | `driver-judge-hardening-recovery-policy` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 17 | `driver-continuity-capability-safety.design-and-test` | `driver-continuity-capability-safety` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 18 | `driver-live-smoke-foundation.ratify-live-smoke-policy` | `driver-live-smoke-foundation` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 19 | `driver-continuity-hygiene-deep-review.gate` | `driver-continuity-hygiene-deep-review` | FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 20 | `driver-agent-selection-contract.gate` | `driver-agent-selection-contract` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 21 | `driver-agent-selection-implementation.fix-judge-runner-test-defaults` | `driver-agent-selection-implementation` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 22 | `driver-agent-selection-implementation.retest-judge-runner-test-defaults` | `driver-agent-selection-implementation` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 23 | `driver-agent-selection-deep-review.gate` | `driver-agent-selection-deep-review` | NEEDS_REVISION, FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 24 | `orch_foundation.contract_tests` | `orch_foundation` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 25 | `orch_foundation.gate` | `orch_foundation` | failed tests | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 26 | `orch_operator_recovery_cutover.fix_recovery_contract_conformance` | `orch_operator_recovery_cutover` | gate_open_allowed=false | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 27 | `orch_operator_recovery_cutover.reverify_recovery_contract_conformance` | `orch_operator_recovery_cutover` | gate_open_allowed=false | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 28 | `repo_regression_full_suite_gate.run-full-pytest-regression-gate` | `repo_regression_full_suite_gate` | failed tests | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 29 | `repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics` | `repo_regression_full_suite_gate` | gate_open_allowed=false | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 30 | `repo_regression_triage.capture-full-suite-failure-inventory` | `repo_regression_test_intent_alignment` | failed tests | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 31 | `repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implemented-surfaces` | `repo_regression_test_intent_alignment` | failed tests | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 32 | `repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope` | `repo_regression_semantic_alignment` | failed tests | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 33 | `cli_blackbox_finalization.full-plan-check` | `cli_blackbox_finalization` | runtime non-closure: fail | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 34 | `opencode_runner_verification.live-smoke` | `opencode_runner_verification` | runtime non-closure: fail, FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 35 | `opencode_runner_verification.final-gate` | `opencode_runner_verification` | failed tests, FAIL | A_superseded_by_orchestrator_removal | historical_failure | non_blocking_superseded | no | no | egr_dispose_superseded_orchestrator_removal_rows |
| 36 | `arch-demolition-wave-1.dem-002-extract-shared-next-step-helper` | `arch-demolition-wave-1` | failed tests | B_scoped_pass_global_fail | out_of_step_origin | gate_intersection_disposition | unknown | no | egr_dispose_scoped_pass_global_fail_rows |
| 37 | `arch-demolition-wave-6.dem-017-collapse-duplicate-step-id-leaf-wrappers-without-breaking-outputs` | `arch-demolition-wave-6` | FAIL | B_scoped_pass_global_fail | out_of_step_origin | gate_intersection_disposition | unknown | no | egr_dispose_scoped_pass_global_fail_rows |
| 38 | `post_review_remediation.implement-completed-step-failure-evidence-guard` | `post_review_remediation` | gate_open_allowed=false, NEEDS_REVISION, runtime non-closure: fail, failed tests, FAIL | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 39 | `post_review_remediation.reverify-completed-step-failure-evidence-guard` | `post_review_remediation` | gate_open_allowed=false, NEEDS_REVISION, runtime non-closure: fail | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 40 | `post_review_remediation.gate` | `post_review_remediation` | runtime non-closure: fail | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 41 | `post_review_remediation.backfill-missing-gate-evidence` | `post_review_remediation` | runtime non-closure: fail | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 42 | `invar_guard_remediation.final-regate-durable-uiux-artifact` | `invar_guard_remediation` | FAIL | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |
| 43 | `det_check_tests_gate` | `det_check_tests` | failed tests | C_repo_regression_full_suite_invar_post_review | current_unknown | current_green_closure | yes | yes | egr_close_repo_regression_post_review_rows |

## Count Reconciliation
- Expected starting rows from request: 43
- Current rows observed: 43
- A_superseded_by_orchestrator_removal: 27
- B_scoped_pass_global_fail: 5
- C_repo_regression_full_suite_invar_post_review: 11
- D_current_core_failure: 0
- Drift explanation: none; current observed count matches expected starting count

## Policy Confirmation
- No direct `plan.yaml` edits: YES
- No historical failure fact erasure/falsification: YES
- Product code/test/doc modifications: NO
