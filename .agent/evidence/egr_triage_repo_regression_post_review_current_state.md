# Evidence: egr_triage_repo_regression_post_review_current_state
## refs Read Confirmation
- tools/vectl/README.md — NOT READ: path `tools/vectl/README.md` does not exist in this isolated worktree; `find . -maxdepth 3 -name README.md` only found `./README.md`; `git ls-files tools/vectl/README.md CONSTITUTION.md` returned no tracked paths.
- CONSTITUTION.md — absent in isolated worktree; `read` returned ENOENT and `git ls-files CONSTITUTION.md` returned no tracked path.

## Commands
```sh
uv run vectl validate
uv run vectl show repo_regression_full_suite_gate.run-full-pytest-regression-gate
uv run vectl show repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
uv run vectl show repo_regression_triage.capture-full-suite-failure-inventory
uv run vectl show repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implemented-surfaces
uv run vectl show repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope
uv run vectl show post_review_remediation.implement-completed-step-failure-evidence-guard
uv run vectl show post_review_remediation.reverify-completed-step-failure-evidence-guard
uv run vectl show post_review_remediation.gate
uv run vectl show post_review_remediation.backfill-missing-gate-evidence
uv run vectl show invar_guard_remediation.final-regate-durable-uiux-artifact
uv run vectl show det_check_tests_gate
uv run pytest
uv run invar guard --all
```

## Command Exit Summary
- `uv run vectl validate` exit: 1
- `uv run vectl show <Group C step>` exits:
  - repo_regression_full_suite_gate.run-full-pytest-regression-gate 0
  - repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics 0
  - repo_regression_triage.capture-full-suite-failure-inventory 0
  - repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implemented-surfaces 0
  - repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope 0
  - post_review_remediation.implement-completed-step-failure-evidence-guard 0
  - post_review_remediation.reverify-completed-step-failure-evidence-guard 0
  - post_review_remediation.gate 0
  - post_review_remediation.backfill-missing-gate-evidence 0
  - invar_guard_remediation.final-regate-durable-uiux-artifact 0
  - det_check_tests_gate 0
- `uv run pytest` exit: 1
- `uv run invar guard --all` exit: 0

## Group C Triage Table
| offending_step_id | current_status | current_code_probe_required | command_evidence_ref | disposition_for_next_step | owner_if_blocking |
|---|---|---|---|---|---|
| repo_regression_full_suite_gate.run-full-pytest-regression-gate | current_blocker_owner_required | yes — full repo pytest required for a full-suite gate | Raw Outputs C1 + C2 + C13 | Current `uv run pytest` still fails (3 `tests/test_init_gitattributes.py` failures); historical recovery failures are not the current failing set, but repo-wide full-suite closure is not green. | pytest regression owner for `tests/test_init_gitattributes.py` / init CLI compatibility |
| repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics | current_green_closure | yes — full pytest covers prior recovery contract tests; no recovery contract failures in current failing set | Raw Outputs C3 + C13 | Plan-evidence hygiene: row contains historical `gate_open_allowed=false`, but current suite no longer fails the targeted recovery-conformance tests. |  |
| repo_regression_triage.capture-full-suite-failure-inventory | current_blocker_owner_required | yes — full pytest required to validate inventory currency | Raw Outputs C4 + C13 | Current inventory is stale relative to current probe: old evidence recorded 31 failures; current probe shows 3 different failures. Needs refreshed inventory or closure tied to current failing set. | repo-regression triage owner |
| repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implemented-surfaces | current_green_closure | yes — full pytest covers observability repro suite; no observability failures in current failing set | Raw Outputs C5 + C13 | Plan-evidence hygiene: historical failed-tests proof is superseded by current full-suite non-failure for this surface. |  |
| repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope | current_green_closure | yes — full pytest covers prior failing lock tests; no lock-boundary/import failures in current failing set | Raw Outputs C6 + C13 | Plan-evidence hygiene: historical failed-tests proof is superseded by current full-suite non-failure for this scope. |  |
| post_review_remediation.implement-completed-step-failure-evidence-guard | current_green_closure | yes — validate and full pytest required because row implements current guard | Raw Outputs C1 + C7 + C13 | Guard implementation tests are green in current full suite; validate fails because the guard is detecting historical completed-evidence strings, so this row is evidence hygiene/backfill, not product-code blocker. |  |
| post_review_remediation.reverify-completed-step-failure-evidence-guard | current_green_closure | yes — validate and full pytest required because row verifies guard exposure | Raw Outputs C1 + C8 + C13 | Reverify tests are green in current full suite; validate failure is caused by historical evidence tokens in completed rows. |  |
| post_review_remediation.gate | current_green_closure | yes — validate plus current test probe; gate row itself is evidence-sensitive | Raw Outputs C1 + C9 + C13 | Gate evidence reports OPEN and current post-review guard tests pass; validate flags historical `runtime non-closure` text, so next action is evidence hygiene/backfill, not product-code repair for this row. |  |
| post_review_remediation.backfill-missing-gate-evidence | current_green_closure | yes — validate plus current test probe; backfill row exists specifically for evidence closure | Raw Outputs C1 + C10 + C13 | Backfill evidence reports OPEN/no blocking-now; validate flags historical token text. Treat as evidence-hygiene refinement if validator requires mechanical closure syntax. |  |
| invar_guard_remediation.final-regate-durable-uiux-artifact | current_green_closure | yes — ran `uv run invar guard --all` because row depends on invar state | Raw Outputs C11 + C14 | Current invar guard exits 0 with 0 errors, 60 warnings, 4 infos; residual warnings are non-blocking guard-budget debt, not blocker-class failure. |  |
| det_check_tests_gate | non_blocking_legacy_debt_with_owner | full pytest run required because repo regression context includes this row; deterministic expected-red gate itself is historical test-anchor evidence | Raw Outputs C12 + C13 | Expected-red gate evidence is explicitly non-blocking with lifecycle: implementation ownership delegated to `det_check_impl`; current full pytest has no deterministic-checklist failures, while unrelated init gitattributes failures remain. |  |

## Policy Confirmation
- No direct plan.yaml edits: CONFIRMED (only read-only `vectl validate/show` commands were run).
- No historical failure fact erasure/falsification: CONFIRMED (artifact preserves raw failing validate/pytest outputs).
- Product code/test/doc modifications: NO (only this evidence artifact under `.agent/evidence/` is intended for commit).
- Plan state mutation commands: NOT RUN. No `uvx vectl`, no claim/complete/defer/check/mutate commands.

## Behavioral Proof Register
- repo-wide pytest current state: PROVEN failing — `uv run pytest` exit 1, `3 failed, 3027 passed, 43 skipped`.
- completed-evidence guard current output: PROVEN failing on plan evidence — `uv run vectl validate` exit 1 with 43 completed-evidence guard errors.
- Group C `vectl show` accessibility: PROVEN — all 11 targeted `uv run vectl show` commands exit 0.
- invar current state: PROVEN pass-with-warnings — `uv run invar guard --all` exit 0, 0 errors, 60 warnings, 4 infos.
- product-code modification absence: PROVEN by git status before artifact write; final git status to be recorded after commit.

## Interpretation Summary
`uv run vectl validate` is currently red because the completed-evidence guard finds unclosed failure evidence in historical completed rows. For Group C, the remaining true current blocker-class failures are the repo-wide full-suite gate and stale full-suite inventory rows because current `uv run pytest` still fails in `tests/test_init_gitattributes.py`. The invar row is current-green under a fresh guard probe. Most post-review guard rows are evidence-hygiene rows: the guard tests pass, but their completed evidence contains historical failure tokens that the guard now flags.

## Raw Outputs

### C1: `uv run vectl validate`
Exit code: 1

```text
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

### C2: `uv run vectl show repo_regression_full_suite_gate.run-full-pytest-regression-gate`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Run full pytest regression gate
**ID:** repo_regression_full_suite_gate.run-full-pytest-regression-gate
**Phase:** Repo-Wide Full Suite Gate (repo_regression_full_suite_gate)
**Status:** ✓ done

**Description:**
Run `uv run pytest -q` after all remediation work. Classify any remaining 
non-passing tests as either true blockers or explicitly governed 
expected-red/skip cases; raw unmanaged failures are not allowed.

**Verification:**
Full pytest output is captured and either fully green or reduced to explicitly 
governed non-blocking statuses.

**Evidence:**
Command: `uv run pytest -q`

Summary counts:
- 1782 passed, 2 failed, 39 skipped, 20 xfailed, 1 warning.

Remaining non-passing tests and dispositions:
- Governed non-blocking statuses:
  - 39 skips (live-smoke and dashboard opt-in gating)
  - 20 xfails (intentional expected-red modules with marker governance)
- Blockers detected (unmanaged failures):
  1. 
tests/orchestration/unit/test_recovery_contract_conformance.py::test_recovery_re
port_semantics_are_shared_across_consumer_surfaces
  2. 
tests/orchestration/unit/test_recovery_contract_conformance.py::test_recovery_qu
arantine_preserves_artifacts_when_gate_open_blocked

Disposition action:
- Batched remediation path created in plan:
  - repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
  - 
repo_regression_full_suite_gate.retest-recovery-contract-conformance-semantics
- Downstream policy steps were dependency-gated behind retest.

Notes:
- No files were changed in this gate execution step.

→ vectl next                          Find more work
→ vectl reject repo_regression_full_suite_gate.run-full-pytest-regression-gate 
--reason "..."     Reject for rework

```

### C3: `uv run vectl show repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Repair recovery contract conformance semantics
**ID:** 
repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics
**Phase:** Repo-Wide Full Suite Gate (repo_regression_full_suite_gate)
**Status:** ✓ done
**Suggested agent:** python-senior
**Affinity:** suggested

**Description:**
work_type: defect_repair
step_type: implementation
verification_scope: local-check
protects_gates:
  - repo_regression_full_suite_gate repo-wide full-suite gate
  - repo_regression_full_suite_gate.decide-static-guard-scope-and-enforcement

Repair the shared recovery contract semantics behind both failing tests in 
`tests/orchestration/unit/test_recovery_contract_conformance.py`.

Scope:
- Align `RecoveryOutcome.NO_ARTIFACTS` semantics across recovery helpers and 
consumer surfaces
- Reconcile blocked-row / quarantine artifact preservation behavior with 
`gate_open_allowed`
- Update the authoritative recovery contract implementation so consumer surfaces
share the same resolved fact instead of drifting per-surface

Refs / likely touch paths:
- `tests/orchestration/unit/test_recovery_contract_conformance.py`
- `src/vectl/orchestration/recovery.py`
- `src/vectl/orch_app.py`

## Blockers to Fix (ALL of these, not just the first)
### B1: shared consumer-surface semantics drift
- Failure: `test_recovery_report_semantics_are_shared_across_consumer_surfaces`
- Suspected root cause: `NO_ARTIFACTS` and blocked-row reporting semantics 
diverged between recovery contract helpers and rendered consumer status surfaces
- Grep hint: 
`NO_ARTIFACTS|gate_open_allowed|recovery_gate_open_allowed|status_data`

### B2: quarantine artifact preservation / gate-open drift
- Failure: `test_recovery_quarantine_preserves_artifacts_when_gate_open_blocked`
- Suspected root cause: quarantine/blocked reporting disagrees about whether 
artifacts remain preserved when gate opening is blocked
- Grep hint: `quarantine|blocked|artifact|gate_open_allowed`

## Root Cause vs Symptom
- Root cause: one recovery contract fact is being interpreted inconsistently 
across recovery helpers and consumer/reporting surfaces
- Symptom: contract conformance tests disagree on blocked rows and 
`gate_open_allowed` behavior for `NO_ARTIFACTS` / quarantine scenarios
- Why this fix is not surface-level: fix the shared contract and downstream 
consumption path, not just assertions in one surface

## Completion Criteria (stub exclusion)
This is an IMPLEMENTATION step. Stub-only completion is NOT acceptable.
Each deliverable MUST contain substantive logic that processes its inputs and 
produces outputs that vary based on those inputs.
**Depends On:** repo_regression_full_suite_gate.run-full-pytest-regression-gate

**Verification:**
- Local-check protecting the repo_regression_full_suite_gate phase boundary
- Recovery contract helpers and consumer/reporting surfaces agree on 
`NO_ARTIFACTS`, blocked-row, artifact-preservation, and `gate_open_allowed` 
semantics
- Focused regression evidence demonstrates the highest-risk failure path 
(quarantine artifacts preserved while gate opening remains blocked) in addition 
to the main shared-semantics path

**Evidence:**
## refs Read Confirmation
- tests/orchestration/unit/test_recovery_contract_conformance.py — read; key 
scenarios validate shared consumer-surface semantics and quarantine 
artifact-preservation behavior.
- src/vectl/orchestration/recovery.py — read; key functions: 
`recovery_gate_open_allowed`, `recovery_case_status`, `recovery_action_status`.
- src/vectl/orch_app.py — read; consumer surfaces delegate to recovery helpers.

## Fix Evidence
- blocker_fingerprint: recovery-contract-conformance / NO_ARTIFACTS / 
gate_open_allowed
- root_cause_hypothesis: helper-level semantics drift (`QUARANTINED` treated as 
gate-open-allowed) plus test fixture plan YAML missing phase name causing 
incorrect NO_ARTIFACTS path.
- files_changed:
  - src/vectl/orchestration/recovery.py
  - tests/orchestration/unit/test_recovery_contract_conformance.py
  - tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py
- sibling_search_performed: recovery helper callsites across orch_app and tests 
confirmed all consumer surfaces derive status from shared helpers.

## Main Path Proof
- Removed `RecoveryOutcome.QUARANTINED` from gate-open-allowed set in 
authoritative helper.
- Updated helper semantics/docs so case/action status mapping now consistently 
derives from one contract path.
- Corrected test plan phase definition (`name: Core`) to ensure intended 
recovery scenario executes.

## Failure Path Proof
- Quarantine scenario now matches blocked gate semantics:
  - `recovery_gate_open_allowed(QUARANTINED) == False`
  - corresponding case/action statuses become open/rejected and align with 
report `gate_open_allowed=False`.
- Regression verification:
  - `uv run pytest 
tests/orchestration/unit/test_recovery_contract_conformance.py 
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py -v` -> 22 
passed.

## Remaining Risk
- none

→ vectl next                          Find more work
→ vectl reject 
repo_regression_full_suite_gate.fix-recovery-contract-conformance-semantics 
--reason "..."     Reject for rework

```

### C4: `uv run vectl show repo_regression_triage.capture-full-suite-failure-inventory`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Capture full-suite failure inventory
**ID:** repo_regression_triage.capture-full-suite-failure-inventory
**Phase:** Repo Regression Test Intent Alignment 
(repo_regression_test_intent_alignment)
**Status:** ✓ done

**Description:**
Record the current failing set from `uv run pytest -q`, grouped by file/test and
normalized into remediation buckets. Include all 27 current failures and any 
companion xfail/skip context relevant to disposal.

**Verification:**
Failure inventory exists with every failing test assigned an initial category 
and owner hypothesis. Evidence includes the exact command run and summarized 
counts by bucket.

**Evidence:**
Artifact:
- Commit hash: 14ca9be5b89405c03f799834eba1c1389c3d2d16
- Inventory file: docs/full-suite-failure-inventory-2026-04-10.md

Verification:
- `uv run pytest -q` -> 31 failed, 1754 passed, 39 skipped, 19 xfailed, 1 
warning (51.41s).
- Inventory includes normalized buckets and per-bucket counts:
  - stale expected-red config/run-registry vs implemented surfaces: 7
  - orchestration contract/export surface drift: 3
  - recovery status/quarantine semantics drift: 2
  - runtime reconcile tracking regression: 1
  - missing/incomplete driver CLI + liveness fixture: 7
  - observability contract backlog/stale expected-red probes: 11

Spec link:
- docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md
- docs/ORCHESTRATION-PLANE-INTERFACES.md
- docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md

Notes:
- Step description referenced 27 failures; command observed 31 failures in this 
run and inventory explicitly records that discrepancy.

→ vectl next                          Find more work
→ vectl reject repo_regression_triage.capture-full-suite-failure-inventory 
--reason "..."     Reject for rework

```

### C5: `uv run vectl show repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implemented-surfaces`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Realign observability red tests with implemented surfaces
**ID:** 
repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implem
ented-surfaces
**Phase:** Repo Regression Test Intent Alignment 
(repo_regression_test_intent_alignment)
**Status:** ✓ done

**Description:**
Audit `tests/repro/test_orch_observability_red.py` for tests that now fail 
because event envelope fields, schemas, or implemented surfaces have 
legitimately advanced. Convert stale red assertions into current behavior checks
or explicit expected-red markers tied to remaining debt.

**Verification:**
Observable stale-red failures are removed from the repro suite; only genuine 
unresolved gaps remain and are labeled intentionally.

**Evidence:**
Tests updated vs retained as intentional red:
- Updated stale observability red assertions in 
`tests/repro/test_orch_observability_red.py` to current implemented behavior for
event envelope fields/hash behavior, projection replay paths, latest_run 
precedence, and tool-family operation coverage.
- Converted remaining unresolved debt to explicit intentional xfail markers 
where surfaces remain unimplemented.

Remaining unresolved GAPs:
- RunRecord terminal-manifest fields (`summary`, `halt_reason`, `artifacts`) not
yet modeled.
- Missing step artifact key normalization helpers (`normalize_step_key`, 
`denormalize_step_key`).

Command(s) run:
- `uv run python -m pytest tests/repro/test_orch_observability_red.py` -> before
11 failed/37 passed; after 43 passed/5 xfailed.

Files changed:
- tests/repro/test_orch_observability_red.py

Commit:
- 1b641b0

Gaps/Notes:
- Reported ref `tools/vectl/README.md` missing in worktree; relied on repository
surfaces under orchestration modules.

→ vectl next                          Find more work
→ vectl reject 
repo_regression_expected_red_cleanup.realign-observability-red-tests-with-implem
ented-surfaces --reason "..."     Reject for rework

```

### C6: `uv run vectl show repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Audit other over-tight locks in failing scope
**ID:** 
repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope
**Phase:** Repo Regression Semantic Alignment 
(repo_regression_semantic_alignment)
**Status:** ✓ done

**Description:**
Search the failing-scope tests for other exact-field-count or exact-export-shape
assertions likely to drift after legitimate contract evolution, and align them 
to semantic invariants where appropriate.

**Verification:**
No remaining failing-scope lock test relies on stale exact-shape assumptions 
without explicit rationale.

**Evidence:**
Additional locks found:
- Exact ResolutionCase field-shape assertions in 
tests/orchestration/unit/test_contract_lock_boundaries.py and 
tests/orchestration/integration/test_control_resolver_contract.py.
- Exact export/method shape locks in tests/orchestration/unit/test_imports.py 
and tests/orchestration/unit/test_core_adapter_contract.py.

Adjustments made:
- Relaxed exact-shape checks into semantic invariant checks for required 
fields/exports/methods while allowing additive compatible contract evolution.

Search/test evidence:
- `uv run pytest -q tests/orchestration/unit/test_contract_lock_boundaries.py 
tests/orchestration/integration/test_control_resolver_contract.py 
tests/orchestration/unit/test_core_adapter_contract.py 
tests/orchestration/unit/test_imports.py` -> after changes 23 passed (before: 2 
failed, 21 passed).

Files changed:
- tests/orchestration/unit/test_contract_lock_boundaries.py
- tests/orchestration/integration/test_control_resolver_contract.py
- tests/orchestration/unit/test_core_adapter_contract.py
- tests/orchestration/unit/test_imports.py

Commit:
- 2fe0ae2

Notes:
- `tools/vectl/README.md` unavailable in worktree per subagent; explicit 
out-of-scope lock file left unchanged.

→ vectl next                          Find more work
→ vectl reject 
repo_regression_contract_alignment.audit-other-tight-locks-in-failing-scope 
--reason "..."     Reject for rework

```

### C7: `uv run vectl show post_review_remediation.implement-completed-step-failure-evidence-guard`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Implement completed-step failure evidence guard
**ID:** post_review_remediation.implement-completed-step-failure-evidence-guard
**Phase:** Post-Review Non-Invar Remediation (post_review_remediation)
**Phase Context:** Fix or close system-review findings that are not repo-wide 
invar/static-debt remediation. Explicitly excludes 
shell_result/file_size/shell_complexity_debt cleanup from uvx invar-tools guard 
--all.
**Status:** ✓ done
**Suggested agent:** python-senior-tacit
**Affinity:** suggested

**Description:**
Add a non-invar guard that prevents completed review/gate/test/verification 
evidence from hiding unclosed failure states.

Problem to fix:
- System review found completed steps whose evidence contained `FAIL`, 
`NEEDS_REVISION`, `gate_open_allowed=false`, non-expected-red failed tests, or 
`runtime non-closure: fail` while later closure was absent or ambiguous.
- The plan currently relies on manual review to notice those contradictions.

Scope:
- Implement a lightweight scanner in the existing plan review/validation surface
that inspects completed review/gate/test/verification-like steps.
- The scanner must flag failure evidence unless one of these is mechanically 
visible:
  1. a later same-phase step closes the blocker with 
green/OPEN/gate_open_allowed=true evidence;
  2. the step is explicitly intentional expected-red with 
owner/lifecycle/non-intersection disposition;
  3. the failure is explicitly non-blocking with gate-intersection proof.
- Keep this separate from repo-wide invar/static-debt checks; do not invoke `uvx
invar-tools guard --all`.

Expected-red / falsifiability requirement:
1. Add or run a focused test fixture containing a completed gate step with 
`gate_open_allowed=false` and no later closure; prove the new guard reports it.
2. Add or run a paired fixture where a later retest closes the blocker; prove 
the guard does not report a false positive.
3. Capture the failing output before implementation if the test is newly 
authored red-first.

Out of scope:
- Do NOT mass-rewrite historical plan evidence.
- Do NOT fix repo-wide `shell_result`, `file_size`, or `shell_complexity_debt` 
findings in this step.

## Completion Criteria (stub exclusion)
This is an IMPLEMENTATION step. Stub-only completion is not acceptable. The 
guard must classify real evidence text and produce actionable step IDs / 
reasons.

**Verification:**
- Targeted tests prove unclosed failure evidence is reported and 
closed/expected-red cases are not false positives.
- Existing plan review/validate CLI or MCP path exposes the guard output, or the
step explains the exact existing surface chosen.
- Run the focused tests added/updated for this guard.
- Do not require `uvx invar-tools guard --all`.

**Evidence:**
## refs Read Confirmation
- tools/vectl/README.md — NOT READ: path does not exist in this checkout; 
`README.md` exists at repo root, but step ref was stale.
- src/vectl/core_plan_queries.py — read `validate_plan()` and `review_plan()`; 
chosen surface is `validate_plan()` so CLI review/validate and MCP validate 
inherit the guard.
- src/vectl/cli_plan.py — read `validate()` and `review()` output paths; 
validation issues print as ERROR and review exits 1 on errors.
- src/vectl/mcp_core_tools.py — read `vectl_validate()`; MCP read-only 
validation reports `validate_plan()` issues.
- tests — read `tests/test_validation.py` and `tests/test_review.py`; added 
focused guard fixtures and CLI review exposure test.

## Expected-Red Proof
- Red fixture/test command before implementation:
```
uv run pytest -q tests/test_validation.py::TestCompletedEvidenceGuard
```
- Failing output:
```
tests/test_validation.py F.. [100%]
FAILED 
TestCompletedEvidenceGuard.test_done_gate_failure_without_later_closure_is_block
ing
E assert False
1 failed, 2 passed
```

## Implementation Evidence
- Surface chosen for guard output: `validate_plan()` in 
`src/vectl/core_plan_queries.py`, therefore `vectl validate`, `vectl review`, 
`vectl gate-check` pre-validation, and `vectl_validate` can expose it without a 
new framework.
- Failure tokens classified: `gate_open_allowed=false`, `NEEDS_REVISION`, 
`runtime non-closure: fail`, numeric failed tests, and uppercase 
`FAIL`/`FAILED`/`FAILURE`.
- Closure/expected-red exceptions implemented: later same-phase completed 
evidence with `gate_open_allowed=true`, approved/pass review outcome, runtime 
non-closure closed/pass, `PASS`/green/OPEN evidence; expected-red/non-blocking 
exception requires owner + lifecycle/disposition + 
gate-intersection/non-intersection proof.
- Files changed: `src/vectl/core_plan_queries.py`, `tests/test_validation.py`, 
`tests/test_review.py`.

## Green Verification
```
uv run pytest -q tests/test_validation.py::TestCompletedEvidenceGuard 
tests/test_review.py::TestReview::test_review_exposes_completed_evidence_guard
# 4 passed

uv run pytest -q tests/test_validation.py tests/test_review.py 
tests/test_orch_cli_surface.py
# 116 passed
```

## Invar Exclusion
- Did not attempt repo-wide invar/static debt remediation.
- `invar_guard(changed=True)` was run and failed on existing/out-of-scope 
`shell_result`, `file_size`, and `shell_complexity_debt` findings across 
`core_plan_queries.py`/`cli_orchestration.py`; this step intentionally does not 
run or require `uvx invar-tools guard --all`.

→ vectl next                          Find more work
→ vectl reject 
post_review_remediation.implement-completed-step-failure-evidence-guard --reason
"..."     Reject for rework

```

### C8: `uv run vectl show post_review_remediation.reverify-completed-step-failure-evidence-guard`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Reverify completed-step failure evidence guard
**ID:** post_review_remediation.reverify-completed-step-failure-evidence-guard
**Phase:** Post-Review Non-Invar Remediation (post_review_remediation)
**Phase Context:** Fix or close system-review findings that are not repo-wide 
invar/static-debt remediation. Explicitly excludes 
shell_result/file_size/shell_complexity_debt cleanup from uvx invar-tools guard 
--all.
**Status:** ✓ done
**Suggested agent:** integration-verifier-tacit
**Affinity:** suggested

**Description:**
Independent verification for the completed-step failure evidence guard.

Scope:
- Validate that the guard catches unclosed failed review/gate/test evidence.
- Validate that it does not flag closed blocker chains, governed expected-red 
cases, or explicitly non-intersecting debt.
- Validate output is actionable: step ID, failure reason, and closure 
requirement are visible to operators.

This is a non-invar verification step; repo-wide static debt is explicitly out 
of scope.
**Depends On:** 
post_review_remediation.implement-completed-step-failure-evidence-guard

**Verification:**
- Run focused guard tests.
- Exercise the public review/validate surface selected by the implementation 
step.
- Produce PASS/BLOCK decision with evidence of true positive and true negative 
cases.
- Do not require `uvx invar-tools guard --all`.

**Evidence:**
## refs Read Confirmation
- src/vectl/cli_plan.py — validation/review surfaces print `validate_plan()` 
issues as ERROR and exit 1 when errors exist.
- src/vectl/core_plan_queries.py — guard is wired through `validate_plan()` and 
reports `Completed evidence guard: step ... unclosed failure evidence (...)`.
- src/vectl/mcp_core_tools.py — `vectl_validate()` calls `validate_plan()` and 
therefore inherits the same guard output.
- tests — `tests/test_validation.py::TestCompletedEvidenceGuard` and 
`tests/test_review.py::TestReview::test_review_exposes_completed_evidence_guard`
were independently exercised.

## Verification Report
Independent verifier task `ses_235b87e5fffenptncoM1rYH03j` returned 
PASS/BLOCKING CLOSED.

Commands executed by verifier:
```
uv run pytest -q tests/test_validation.py::TestCompletedEvidenceGuard
# 3 passed
uv run pytest -q 
tests/test_review.py::TestReview::test_review_exposes_completed_evidence_guard
# 1 passed
uv run pytest -q tests/test_validation.py tests/test_review.py
# 94 passed
```
Verifier also performed live `validate_plan()` and CLI `vectl validate` / `vectl
review` checks on unclosed/closed/expected-red fixtures; both public CLI 
surfaces exited 1 for unclosed failures and showed guard keyword, step id, and 
failure reason.

## Coverage Matrix
- Unclosed `gate_open_allowed=false`: CAUGHT
- `NEEDS_REVISION` without closure: CAUGHT
- Later retest closure: NOT_FLAGGED
- Governed expected-red: NOT_FLAGGED
- Actionable output: YES — guard keyword + step id + failure reason + closure 
requirement visible.

## Decision
- [x] PASS
- [ ] BLOCKED

Caveat recorded: no dedicated test for `runtime non-closure: fail`; 
implementation recognizes it, but this is a minor non-blocking coverage gap for 
this reverify step. Repo-wide invar/static debt remained out of scope.

→ vectl next                          Find more work
→ vectl reject 
post_review_remediation.reverify-completed-step-failure-evidence-guard --reason 
"..."     Reject for rework

```

### C9: `uv run vectl show post_review_remediation.gate`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Gate post-review non-invar remediation
**ID:** post_review_remediation.gate
**Phase:** Post-Review Non-Invar Remediation (post_review_remediation)
**Phase Context:** Fix or close system-review findings that are not repo-wide 
invar/static-debt remediation. Explicitly excludes 
shell_result/file_size/shell_complexity_debt cleanup from uvx invar-tools guard 
--all.
**Status:** ✓ done
**Suggested agent:** gate-reviewer-tacit
**Affinity:** suggested

**Description:**
Final independent gate for post-review non-invar remediation.

Scope:
- Verify driver agent-selection deep-review findings are remediated or 
authoritatively closed.
- Verify OpenCode runner final-gate historical CLI blockers are regated green or
have explicit non-intersection proof.
- Verify CLI black-box finalization runtime non-closure is regated green.
- Verify the completed-step failure evidence guard prevents new unclosed failed 
gate/test/review evidence from silently remaining `done`.
- Explicitly exclude repo-wide invar/static-debt families (`shell_result`, 
`file_size`, `shell_complexity_debt`) from this phase decision; those require a 
separate remediation lane.

Gate should BLOCK if any non-invar blocker-class issue remains open or if 
evidence lacks refs read confirmation / command output.
**Depends On:** 
post_review_remediation.reverify-driver-agent-selection-deep-review, 
post_review_remediation.reverify-opencode-runner-final-gate, 
post_review_remediation.reverify-cli-blackbox-finalization, 
post_review_remediation.reverify-completed-step-failure-evidence-guard, 
post_review_remediation.backfill-missing-gate-evidence

**Verification:**
- Review all phase step evidence.
- Confirm every non-invar blocker from the system review is PROVEN closed or 
explicitly non-intersecting.
- Confirm invar/static debt is excluded with a separate-owner note rather than 
silently passed.
- Produce OPEN/BLOCK gate decision.

**Evidence:**
## refs Read Confirmation (MANDATORY)
- docs/DRIVER-AGENT-SELECTION.md — NOT READ: file absent after planned driver 
demolition (`git ls-files` returned no output). Replacement read: 
`docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md`; key passage: `role_id`
is plan-facing, `agent_id` is concrete runtime persona, prompt policy lives in 
dispatch layer, runtime/runner consume already-resolved dispatch material.
- docs/RFC-opencode-orchestration-runner.md — OpenCode uses dedicated 
`OpenCodeRunner`; launch contract is `opencode run --format json --dir 
<workspace> --agent <agent_id> --file .vectl/orch/runner_prompt.md`; recovery 
resumes native session first, then uses explicit fresh relaunch.
- README.md — vectl is an execution control plane requiring evidence, DAG/claim 
discipline, and continuity recovery via `uv run vectl orch recover 
[RUN_ID|--latest]`; dry-run is diagnostic and no-silent-deletion is required.
- docs/ORCHESTRATION-CLI-QUICKSTART.md — `vectl orch recover` replaces legacy 
continuity repair; `--latest` fails explicitly if no run exists; command sheet 
includes 
run/resume/recover/runs/status/events/logs/artifacts/actions/pause/unpause/stop/
prune/config.

## Gate Review Report
**Reviewer**: gate-reviewer-tacit
**Phase**: post_review_remediation

## Step Evidence Matrix
| Step ID | Status | Evidence quality | Concerns |
|---|---|---|---|
| post_review_remediation.fix-driver-agent-selection-deep-review | done | 
Adequate; refs absence explained, replacement authority cited, regression + 
green tests recorded | None blocking |
| post_review_remediation.reverify-driver-agent-selection-deep-review | done | 
Strong; replacement refs, grep closure, equivalent tests | None |
| post_review_remediation.fix-opencode-runner-final-gate-cli-surface | done | 
Adequate; current-state closure proof with role-profile tests | None |
| post_review_remediation.reverify-opencode-runner-final-gate | done | Strong; 
OpenCode/runtime/repro suites green; unrelated worktree failures dispositioned 
non-intersecting | None |
| post_review_remediation.fix-cli-blackbox-finalization-runtime-nonclosure | 
done | Originally weak | Closed by supplemental backfill evidence |
| post_review_remediation.reverify-cli-blackbox-finalization | done | Strong; 
refs, CLI smoke, liveness tests, no public `runtime non-closure: fail` leak | 
None |
| post_review_remediation.implement-completed-step-failure-evidence-guard | done
| Strong; red-first proof, implementation surface, focused green tests | Minor 
non-blocking: runtime-token dedicated case not separately tested, source 
recognizes token |
| post_review_remediation.reverify-completed-step-failure-evidence-guard | done 
| Adequate; public validate/review exposure verified | None blocking |
| post_review_remediation.implement-drive-autonomy-safe-recovery-and-actions | 
done | Originally lacked refs-read detail | Closed by supplemental backfill 
evidence |
| post_review_remediation.backfill-missing-gate-evidence | done | Strong; refs +
command output supplied for both prior gaps | None |

## Closure Register
- Driver agent-selection SHOULD_FIX: PROVEN — historical files are absent by 
design; replacement dispatch policy plus current dispatch/orch tests pass (`115 
passed`).
- OpenCode final-gate CLI blocker: PROVEN — OpenCode/recovery tests pass (`89 
passed`), historical CLI recovery/liveness family passes (`21 passed`).
- CLI black-box runtime non-closure: PROVEN — recovery/liveness tests pass, 
source scan shows only classifier/guard strings, not public CLI failure output.
- Completed-step failure evidence guard: PROVEN — focused guard/review tests 
pass (`4 passed`); guard reports actionable completed evidence failures.
- Repo-wide invar/static debt: EXCLUDED_TO_SEPARATE_LANE — phase explicitly 
excludes `shell_result`, `file_size`, `shell_complexity_debt`; no decision made 
on those families and no repo-wide invar guard was required.

## Issues Identified
| Severity | Description | Related Step | Blocking? |
|---|---|---|---|
| None | No non-invar blocker-class issue remains open after supplemental 
evidence | N/A | No |

## Commands Reviewed/Executed
```sh
vectl_show post_review_remediation
vectl_render post_review_remediation --full
vectl_review post_review_remediation --include-done --check-refs
git ls-files "docs/DRIVER-AGENT-SELECTION.md" "src/vectl/driver/loop.py" 
"src/vectl/driver/judge.py"
uv run pytest -q 
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_uses_pro
file_agent_id_for_runner_request 
tests/orchestration/unit/test_dispatch_policy.py 
tests/orchestration/unit/test_prompt_artifact_contract.py
uv run pytest -q tests/orchestration/unit/test_opencode_runner.py 
tests/orchestration/integration/test_recovery_fallback.py
uv run pytest -q tests/repro/test_orch_cli_recovery_red.py 
tests/repro/test_orch_liveness.py
uv run pytest -q tests/test_orch_cli_surface.py -k "drive and (active or auto or
unsafe or foreground or once)"
uv run pytest -q tests/test_validation.py::TestCompletedEvidenceGuard 
tests/test_review.py::TestReview::test_review_exposes_completed_evidence_guard
uv run vectl orch drive --help
uv run vectl orch drive-recover --latest --dry-run --json
```
```text
git ls-files historical driver refs: no output.
Dispatch/prompt closure: 115 passed.
OpenCode runner + recovery fallback: 89 passed.
CLI recovery-red + liveness: 21 passed.
Drive autonomy filtered tests: 7 passed, 18 deselected.
Completed evidence guard tests: 4 passed.
drive --help rendered documented options.
drive-recover dry-run JSON returned status=blocked_operator, 
reason_code=operator_required, human_required=true, suggested_action present.
```

## Decision
- [x] OPEN
- [ ] BLOCKED

→ vectl next                          Find more work
→ vectl reject post_review_remediation.gate --reason "..."     Reject for rework

```

### C10: `uv run vectl show post_review_remediation.backfill-missing-gate-evidence`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Backfill missing post-review gate evidence
**ID:** post_review_remediation.backfill-missing-gate-evidence
**Phase:** Post-Review Non-Invar Remediation (post_review_remediation)
**Phase Context:** Fix or close system-review findings that are not repo-wide 
invar/static-debt remediation. Explicitly excludes 
shell_result/file_size/shell_complexity_debt cleanup from uvx invar-tools guard 
--all.
**Status:** ✓ done
**Suggested agent:** integration-verifier-tacit
**Affinity:** suggested

**Description:**
Supplemental evidence-only closure for final gate proof gaps found in 
`post_review_remediation.gate` review.

Scope:
- Provide refs-read confirmation and command output for 
`post_review_remediation.fix-cli-blackbox-finalization-runtime-nonclosure`, 
whose original completion evidence lacked audit-grade refs/command detail.
- Provide refs-read confirmation and command output for 
`post_review_remediation.implement-drive-autonomy-safe-recovery-and-actions`, 
whose original completion evidence had command summaries but no explicit 
refs-read confirmation.
- Do not change product code unless a live blocker is found.
- Do not run or require repo-wide `uvx invar-tools guard --all`; invar/static 
debt remains separate-lane.

This step exists only to make the final non-invar gate auditable without 
rewriting historical completed-step evidence.
**Depends On:** 
post_review_remediation.fix-cli-blackbox-finalization-runtime-nonclosure, 
post_review_remediation.implement-drive-autonomy-safe-recovery-and-actions

**Verification:**
- Read and cite the relevant CLI/recovery/runner/drive refs.
- Run focused CLI black-box runtime non-closure tests or current equivalent and 
confirm no `runtime non-closure: fail` public CLI output remains.
- Run focused drive autonomy/action-guidance tests and CLI smoke commands.
- Produce OPEN/BLOCK supplemental evidence decision for final gate.

**Evidence:**
## refs Read Confirmation
- README.md — `vectl` is the execution control plane for AI agents and enforces 
evidence requirements, claim locking, DAG discipline, and agent affinity.
- docs/ORCHESTRATION-CLI-QUICKSTART.md — documents `vectl orch` as the 
orchestration entrypoint; recovery uses `vectl orch recover RUN_ID` / `--dry-run
--json`; selector safety and no-silent-deletion are explicit; command cheat 
sheet covers orch surfaces.
- docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md — canonical 
CLI/config/observability spec; command structure covers 
run/resume/recover/runs/prune, inspect status/events/logs/artifacts/actions, 
case list/show/respond, control pause/unpause/stop, config show/validate/tools.
- docs/RFC-opencode-orchestration-runner.md — OpenCode uses a dedicated runner; 
launch/resume require headless `opencode run --format json --dir ... --agent 
<agent_id> --file ...`; recovery prefers native resume then explicit fresh 
relaunch.
- tests/repro/test_orch_cli_recovery_red.py — black-box recovery-red matrix 
covers run lifecycle, inspect, case, control, config, help, and prune surfaces 
and verifies previously expected-red CLI/operator workflow gaps are closed.
- tests/repro/test_orch_liveness.py — `test_orch_liveness_cli_entrypoint` 
creates a minimal fixture, invokes `vectl orch run --json`, asserts exit 0, no 
runtime wiring failure, success true, and step_id `core.ready`.
- tests/test_orch_cli_surface.py — drive autonomy tests cover command 
registration, active-drive attach, safe auto-recovery, 
unsafe/operator-controlled recovery, retry limit reporting, foreground/once 
behavior, JSON/JSONL/plain progress, and structured 
`auto_recovery`/`reason_code`/`human_required`/`suggested_action` output.

## Supplemental Verification Report
**Commands executed**:
```sh
uv run pytest -q tests/repro/test_orch_cli_recovery_red.py 
tests/repro/test_orch_liveness.py
uv run pytest -q tests/test_orch_cli_surface.py -k "drive and (active or auto or
unsafe or foreground or once)"
uv run pytest -q tests/test_orch_cli_surface.py -k "drive" --no-header
uv run vectl orch drive --help
uv run vectl orch drive-recover --latest --dry-run --json
source scan for "runtime non-closure" in src/vectl/ and tests/
uv run pytest -q tests/repro/test_orch_cli_recovery_red.py -v --no-header
```
**Actual output**:
```text
CLI recovery-red + liveness: 21 passed in 6.98s.
Drive autonomy filtered tests: 7 passed, 18 deselected in 0.33s.
All drive-related CLI surface tests: 16 passed, 9 deselected in 0.44s.
`vectl orch drive --help` rendered command help with options including --agent, 
--max-parallelism, --once, --poll-interval, --status-interval, --progress, 
--quiet, --verbose, --jsonl, --json, --output, --plan, --help.
`vectl orch drive-recover --latest --dry-run --json` returned structured dry-run
JSON with status=blocked_operator, reason_code=operator_required, 
human_required=true, suggested_action, conflict_resolutions, 
recovered_child_run_ids, failed_child_run_ids, and summary.
Source scan: `runtime non-closure` appears as classification/guard strings in 
src/vectl/orch_app.py, src/vectl/cli_orchestration.py, and 
src/vectl/core_plan_queries.py; no tests/ matches and no unhandled public CLI 
output leak. CLI maps the token to reason_code=runtime_nonclosure/action 
guidance.
Verbose recovery-red rerun: 20 passed in 6.23s; all previously expected-red 
tests green.
```

## Closure
- CLI black-box runtime non-closure missing evidence: PROVEN — 
recovery-red/liveness public CLI tests are green; no unhandled `runtime 
non-closure: fail` leaks to public CLI output; remaining source tokens are 
classifier/guard strings.
- Drive autonomy/action-guidance missing evidence: PROVEN — drive surface tests 
pass for safe auto-recovery, unsafe operator-controlled recovery, retry limits, 
active-drive attach, foreground/once behavior, and structured guidance; CLI 
smoke confirms help and dry-run recovery JSON surfaces.
- blocking-now: none

## Decision
- [x] OPEN
- [ ] BLOCKED

No product-code changes were made by this supplemental evidence step; repo-wide 
invar/static debt remains explicitly outside this non-invar gate lane.

→ vectl next                          Find more work
→ vectl reject post_review_remediation.backfill-missing-gate-evidence --reason 
"..."     Reject for rework

```

### C11: `uv run vectl show invar_guard_remediation.final-regate-durable-uiux-artifact`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Final regate after durable UI/UX artifact
**ID:** invar_guard_remediation.final-regate-durable-uiux-artifact
**Phase:** Invar Guard Remediation (invar_guard_remediation)
**Status:** ✓ done
**Suggested agent:** gate-reviewer
**Affinity:** suggested

**Description:**
step_type: gate
step_intent: retest_green
expected_result: green
verification_scope: full-plan-check
agent_source: default
independence_level: L1
supersedes_failed_step: invar_guard_remediation.post-blocker-closure-regate
risk_tags:
  - full_guard_regate
  - durable_artifact_required
  - downstream_phase_unlock

Fresh/cold final regate for `invar_guard_remediation` after durable standalone 
artifact persistence and independent `uiux-auditor` artifact validation.

This step supersedes the failed 
`invar_guard_remediation.post-blocker-closure-regate` path. The failed regate 
found that the UI/UX artifact was embedded only in managed plan 
summaries/evidence/prompt text. This final regate MUST evaluate the durable 
repository artifact and the new `uiux-auditor` artifact audit instead of 
reopening based on stale or managed-summary evidence.

As an independent `gate-reviewer`, decide whether the phase can close and 
whether downstream deterministic-checklist phases may proceed.

## Required Fresh/Cold Checks
- Run or verify a fresh/cold `uvx invar-tools guard --all` execution on current 
mainline state.
- Confirm full guard output includes exit status, `status`, `errors`, 
`warnings`, `infos`, and escape-budget count.
- Confirm the six cited Core contract warning sites remain fixed or otherwise 
have explicit non-blocking residual disposition with downstream non-intersection
proof.
- Verify the standalone UI/UX artifact exists under 
`docs/invar_guard_remediation/` or an explicitly equivalent repository docs path
outside managed plan summaries.
- Verify the artifact contains pasted help/surface snapshots, command 
executability matrix, JSON ANSI proof, facade import proof, behavioral proof 
register, and uiux-auditor-compatible validation note.
- Verify `invar_guard_remediation.uiux-audit-durable-artifact` completed from 
the durable artifact file and emitted PASS/PASS_WITH_DEBT/FAIL, blockers array, 
and `gate_open_allowed`.

## Gate Decision Basis
The decision basis MUST map each behavioral/runtime obligation to:
- requirement_ref
- behavior_claim
- runtime_proof_expected
- evidence_ref
- status: PROVEN | NON_BLOCKING | NEEDS_TEST | UNPROVEN | UNCERTAIN_BLOCKING
- closure_path
- gate_decision_basis

`OPEN` is allowed only when all blocker-class obligations are PROVEN or have 
explicit non-intersection proof. `NEEDS_TEST`, `UNPROVEN`, and 
`UNCERTAIN_BLOCKING` must BLOCK unless accompanied by explicit non-intersection 
evidence.

## Downstream Protection
This full-plan-check protects the locked `det_check_contracts`, 
`det_check_tests`, `det_check_impl`, and `det_check_finalization` phases. It is 
the authoritative closure path for `invar_guard_remediation` after the failed 
`invar_guard_remediation.post-blocker-closure-regate` review.

## Acceptance Checklist
- [x] Fresh/cold `uvx invar-tools guard --all` evidence is reviewed and 
current-mainline.
- [x] Durable standalone UI/UX artifact file is reviewed directly and contains 
all required subproofs rather than plan-summary references.
- [x] New durable-artifact `uiux-auditor` validation is reviewed and gates on 
the artifact file itself.
- [x] Core contract warning register is reviewed and every cited warning is 
fixed or dispositioned with downstream non-intersection proof.
- [x] Gate decision basis includes structured behavioral proof register fields 
for each blocker family.
- [x] OPEN decision is denied if evidence relies on stale partial success, 
discarded branch state, or managed plan summaries alone.
- [x] Downstream deterministic-checklist phases remain blocked unless this final
regate opens.
**Depends On:** invar_guard_remediation.persist-standalone-uiux-proof-artifact, 
invar_guard_remediation.uiux-audit-durable-artifact

**Verification:**
- [x] Fresh/cold `uvx invar-tools guard --all` was run or independently verified
on current mainline and output includes exit status, 
status/errors/warnings/infos, and escape budget.
- [x] Durable standalone UI/UX artifact includes pasted help/surface snapshots, 
command executability matrix, JSON ANSI proof, facade import proof, behavioral 
proof register, and uiux-auditor-compatible validation note.
- [x] UI/UX proof was validated by 
`invar_guard_remediation.uiux-audit-durable-artifact` against the durable 
artifact file, not prompt/plan evidence.
- [x] Each cited Core contract warning is proven fixed with non-vacuous 
@pre/@post/doctest or has narrow non-blocking residual disposition under INVAR 
policy with downstream non-intersection proof.
- [x] Gate decision basis records PROVEN/NON_BLOCKING/BLOCK for each blocker 
family and does not open on stale partial-success evidence.
- [x] The decision explicitly states whether `det_check_contracts` and later 
locked phases may proceed.
- [x] OPEN is forbidden if any required proof exists only in managed plan 
summaries or prior prompt/evidence text.

**Evidence:**
## refs Read Confirmation
- CONSTITUTION.md — NOT READ: searched workspace; not found.
- docs/invar_guard_remediation/uiux-proof-artifact.md — READ directly; key 
passages: provenance states artifact is under repository docs with 
“Managed-plan-summary reliance: none”; evidence index lists E1-E8; validation 
note defines PASS/PASS_WITH_DEBT/FAIL criteria.
- INVAR.md — READ; key passages: Core requires @pre/@post/doctest; shell owns 
I/O; run `invar guard` after changes; escape hatches must be rare and reasoned.
- tools/vectl/README.md — NOT READ: file absent in worktree.

## Final Durable-Artifact Regate Report
**Reviewer**: gate-reviewer
**Phase**: invar_guard_remediation
**Supersedes failed step**: invar_guard_remediation.post-blocker-closure-regate

### Fresh/Cold Guard Evidence
- Command: `uvx invar-tools guard --all`
- Exit code: 0
- Output: `status: passed`, `summary.errors: 0`, `summary.warnings: 54`, 
`summary.infos: 4`, `escape_hatches.count: 6`, weighted budget `14/15`, gating 
`warning`, doctest `passed: true`.
- No blocker-class guard errors remain; remaining findings are warnings/infos, 
chiefly size/complexity and escape-budget warning.

### Durable Artifact Review
- Artifact: `docs/invar_guard_remediation/uiux-proof-artifact.md`
- Inspected directly: YES
- Marker validation: `artifact_exists=True`, `line_count=392`, 
`missing_required_markers=[]`
- Required subproofs present: help/surface snapshots E1-E5, command 
executability matrix, JSON parseability/zero ANSI proof, facade import proof, 
behavioral proof register, uiux-auditor-compatible validation note.
- Managed plan summary reliance: NONE.

### UI/UX Auditor Evidence Reviewed
- Durable artifact audit verdict: PASS
- proof_gap_status: NON_BLOCKING
- blocking_status: CLOSED
- blockers: []
- gate_open_allowed: true

### Core Contract Warning Register Review
Six cited Core contract warning sites remain fixed; current guard has zero 
errors and no missing-contract blockers. Remaining residual warnings are 
non-blocking structural debt and do not intersect downstream 
deterministic-checklist gate readiness.

### Gate Decision Basis
All blocker-class obligations are PROVEN or NON_BLOCKING with explicit 
non-intersection proof. No required proof relies only on managed plan summaries.

### Gate Decision
PASS / OPEN. `invar_guard_remediation` may close. `det_check_contracts` and 
later deterministic-checklist phases may proceed.

### Behavioral Proof Register
- fresh_guard: PROVEN — fresh guard exit 0, zero errors, explicit 
warnings/infos/escape budget.
- durable_artifact: PROVEN — repository docs artifact exists and required 
markers present.
- uiux_subproofs: PROVEN — E1-E8 subproofs present in artifact.
- uiux_auditor: PROVEN — durable artifact audit PASS, blockers empty, 
gate_open_allowed true.
- core_contracts: PROVEN — cited Core warning family fixed; guard has no 
blocker-class contract findings.
- residual_warnings: NON_BLOCKING — warning-level size/complexity/escape budget 
debt with zero errors and no downstream gate intersection.
- downstream_protection: PROVEN — gate explicitly opens downstream phases only 
after durable artifact and guard proof.

### Closure Signals
- step_intent: retest_green
- expected_result: green
- observed_result: OPEN
- verdict: PASS
- proof_gap_status: NONE
- blocking_status: CLOSED
- blockers: []
- gate_open_allowed: true
- orchestrator_action_hint: COMPLETE
- product_implementation_files_modified: none

### Verification
- `pwd && git branch --show-current && git status --short && git log --oneline 
-5` — branch confirmed 
`vectl/step-invar_guard_remediation.final-regate-durable-uiux-artifact`.
- `uvx invar-tools guard --all` — exit 0.
- Artifact marker validation — exit 0; all required markers present.
- Final `git status --short` — clean.

### Files changed
None.

### Commit hash(es)
none — verify-only gate; no changes required.

### Gaps/Notes
Guard remains warning-bearing (54 warnings, 4 infos, escape budget 14/15) and 
accepted as governed non-blocking debt.

### checklist_receipt
All description acceptance checklist items and verification checklist items were
satisfied and toggled/updated before completion.

→ vectl next                          Find more work
→ vectl reject invar_guard_remediation.final-regate-durable-uiux-artifact 
--reason "..."     Reject for rework

```

### C12: `uv run vectl show det_check_tests_gate`
Exit code: 0

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
## Step: Gate expected-red checklist tests
**ID:** det_check_tests_gate
**Phase:** Deterministic Checklist Handling: Expected-Red Test Definitions 
(det_check_tests)
**Status:** ✓ done
**Suggested agent:** gate-reviewer
**Affinity:** suggested

**Description:**
As an INDEPENDENT REVIEWER (gate-reviewer), assess whether expected-red 
checklist tests expose the declared contract gaps without product implementation
leakage.

Acceptance checklist:
- [x] Both expected-red test steps changed only tests/fixtures/helpers.
- [x] Red failures map to declared deterministic checklist gaps.
- [x] No product implementation files were changed in this phase.
- [x] Legacy human keyword compatibility tests remain present.
- [x] Downstream implementation steps have clear test anchors.
**Depends On:** det_check_tests_core_red, det_check_tests_surfaces_red

**Verification:**
- [x] phase-check protecting `det_check_impl`: verify expected-red evidence is 
specific, mapped to declared gaps, and test-only.
- [x] Confirm no expected-red failure is unrelated infrastructure noise.
- [x] Confirm implementation can proceed with explicit contract/test anchors.

**Evidence:**
status: SUCCESS

## refs Read Confirmation
- docs/RFC-deterministic-checklists.md — owner, revision/selector, and receipt 
requirements confirmed.
- tests/test_checklist.py — deterministic core expected-red anchors confirmed; 
legacy keyword compatibility still present.
- tests/test_cli.py — CLI deterministic expected-red anchors and legacy keyword 
test confirmed.
- tests/test_mcp.py — MCP deterministic expected-red anchors and legacy keyword 
test confirmed.
- tests/orchestration/unit/test_checklist_receipt_contract.py — receipt parser 
expected-red anchor confirmed.

## Expected-Red Gate Report
| Step ID | Test-only scope | Red maps to gap | Concerns |
| --- | --- | --- | --- |
| det_check_tests_core_red | yes | yes | none |
| det_check_tests_surfaces_red | yes | yes | none |

## Gate Decision Basis
- requirement_ref: `docs/RFC-deterministic-checklists.md` 
ownership/revision/selector/receipt requirements.
- behavior_claim: expected-red tests expose core, CLI/MCP, and orchestration 
receipt gaps without product implementation leakage.
- runtime_proof_expected: expected-red only; green proof owned by det_check_impl
verification.
- evidence_ref: commits `79fe240` and `5ec6168`; focused pytest expected-red 
outputs.
- status: PROVEN.
- closure_path: det_check_impl must implement core inventory/mutation, CLI 
deterministic surfaces, MCP deterministic fields, and receipt parser until 
anchors pass.
- gate_decision_basis: OPEN.

gate_open_allowed: true
verdict: PASS
blockers: []
orchestrator_action_hint: COMPLETE

## Behavioral Proof Register
- expected-red failures map to declared deterministic checklist gaps: PROVEN.
- phase changed only tests/fixtures/helpers: PROVEN.
- legacy keyword compatibility tests remain present: PROVEN.
- implementation has explicit test anchors: PROVEN.

## Verification
- Gate reviewer re-ran core expected-red command: 9 failed / 12 passed, all 
expected-red from core_checklist stubs.
- Gate reviewer re-ran surface expected-red command: 8 failed / 26 passed / 390 
deselected, all expected-red from missing deterministic CLI/MCP/receipt 
surfaces.
- Files changed: none (read-only gate).
- Commit hash(es): none (read-only gate).

## Checklist receipt
- All acceptance and verification checklist items were satisfied and toggled 
complete.

## Gaps/Notes
- No blockers remain; det_check_impl can proceed with explicit contract/test 
anchors.

→ vectl next                          Find more work
→ vectl reject det_check_tests_gate --reason "..."     Reject for rework

```

### C13: `uv run pytest`
Exit code: 1

```text
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
============================= test session starts ==============================
platform darwin -- Python 3.12.12, pytest-9.1.0, pluggy-1.6.0 -- /Users/tefx/Projects/vectl/.vectl/worktrees/egr_triage_repo_regression_post_review_current_state/.venv/bin/python3
cachedir: .pytest_cache
hypothesis profile 'default'
rootdir: /Users/tefx/Projects/vectl/.vectl/worktrees/egr_triage_repo_regression_post_review_current_state
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.0, cov-7.1.0, hypothesis-6.155.3, returns-0.28.0, base-url-2.1.0, playwright-0.8.0
collecting ... collected 3073 items

tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLive::test_codex_dispatch_opt_in_gate SKIPPED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLive::test_codex_dispatch_skip_matrix PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLive::test_codex_dispatch_binary_available PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLive::test_codex_dispatch_skip_if_binary_missing PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLive::test_codex_dispatch_skip_if_auth_missing PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_effective_model PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_model_override_args PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_env_var_override PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_subprocess_invocation SKIPPED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_output_parsing SKIPPED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchLiveIntegration::test_codex_dispatch_transport_vs_assertion_failure SKIPPED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_parse_valid_json_item_completed PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_parse_raw_text_when_not_json PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_parse_empty_output PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_parse_malformed_jsonl PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_assert_codex_dispatch_success_with_status PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_assert_codex_dispatch_success_with_raw_output PASSED [  0%]
tests/live_smoke/test_codex_dispatch_live.py::TestCodexDispatchParsing::test_assert_codex_dispatch_success_fails_on_error_indicator PASSED [  0%]
tests/live_smoke/test_drive_autonomy_live.py::TestDriveAutonomyLiveSmoke::test_cli_auto_recovers_stale_child_and_continues SKIPPED [  0%]
tests/live_smoke/test_drive_autonomy_live.py::TestDriveAutonomyLiveSmoke::test_cli_reports_retry_limit_without_looping_forever SKIPPED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLive::test_opencode_dispatch_opt_in_gate SKIPPED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLive::test_opencode_dispatch_skip_matrix PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLive::test_opencode_dispatch_binary_available PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLive::test_opencode_dispatch_skip_if_binary_missing PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLive::test_opencode_dispatch_skip_if_auth_missing PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_effective_model PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_model_override_args PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_env_var_override PASSED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_subprocess_invocation SKIPPED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_output_parsing SKIPPED [  0%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchLiveIntegration::test_opencode_dispatch_transport_vs_assertion_failure SKIPPED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_parse_valid_json_text_event PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_parse_raw_text_when_not_json PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_parse_empty_output PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_parse_malformed_jsonl PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_assert_opencode_dispatch_success_with_status PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_assert_opencode_dispatch_success_with_raw_output PASSED [  1%]
tests/live_smoke/test_opencode_dispatch_live.py::TestOpencodeDispatchParsing::test_assert_opencode_dispatch_success_fails_on_error_indicator PASSED [  1%]
tests/live_smoke/test_opencode_long_runtime_live.py::TestOpenCodeLongRuntimeLive::test_opt_in_gates SKIPPED [  1%]
tests/live_smoke/test_opencode_long_runtime_live.py::TestOpenCodeLongRuntimeLive::test_opencode_long_runtime_subprocess SKIPPED [  1%]
tests/live_smoke/test_opencode_long_runtime_live.py::TestOpenCodeLongRuntimeLive::test_opencode_long_runtime_with_session_resume SKIPPED [  1%]
tests/live_smoke/test_opencode_long_runtime_live.py::TestOpenCodeLongRuntimeLive::test_long_live_recover_dry_run_not_in_default_loop SKIPPED [  1%]
tests/live_smoke/test_resolver_live.py::test_resolver_live_path_reaches_real_runner[codex-codex_live] SKIPPED [  1%]
tests/live_smoke/test_resolver_live.py::test_resolver_live_path_reaches_real_runner[opencode-opencode_live] SKIPPED [  1%]
tests/live_smoke/test_vectl_orchestrator_e2e_live.py::TestVectlOrchestratorE2ELive::test_opt_in_gates SKIPPED [  1%]
tests/live_smoke/test_vectl_orchestrator_e2e_live.py::TestVectlOrchestratorE2ELive::test_full_orchestrator_flow_replan_resolve_and_merge SKIPPED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_dispatch_decision_from_authoritative_core_snapshot PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_wait_decision_when_in_progress_blocks_dispatch PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolve_decision_for_blocked_steps_without_dispatch_path PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolve_decision_preserves_unresolved_truth_from_core PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_done_decision_when_plan_complete_and_runtime_idle PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolve_conflict_when_plan_complete_with_remaining_work PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolution_unblocked_re_evaluate_from_refreshed_state PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolution_waiting_maps_to_wait PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolution_operator_required_maps_to_wait PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_resolution_halt_maps_to_halt PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_authoritative_core_adapter_propagates_isolation_from_plan PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_authoritative_core_snapshot_derived_from_official_surfaces PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_roster_honors_independent_isolation_no_reuse PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_control_evaluates_same_decision_paths_as_legacy_loop_design PASSED [  1%]
tests/orchestration/integration/test_control_isolation_flow.py::test_all_authoritative_snapshots_read_once_per_evaluation PASSED [  1%]
tests/orchestration/integration/test_control_resolver_contract.py::test_control_invokes_resolver_only_when_normal_flow_does_not_close PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_blocked_case_unblocked_resolution_end_to_end PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_unresolved_case_operator_required_resolution_end_to_end PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_resolver_returns_bounded_report_for_all_status_values PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_control_refreshes_all_snapshots_after_resolution PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_resolver_forbidden_from_becoming_permanent_controller PASSED [  2%]
tests/orchestration/integration/test_control_resolver_contract.py::test_resolution_case_contains_current_state_not_speculation PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestExpandedExecutionRequestDispatch::test_start_mode_execution_request_fields_persist_in_state PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestExpandedExecutionRequestDispatch::test_resume_mode_execution_request_fields_persist_in_state PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestExpandedExecutionRequestDispatch::test_recover_mode_execution_request_fields_persist_in_state PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestSessionMetadataPersistence::test_poll_result_session_id_updates_execution_state PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestSessionMetadataPersistence::test_evidence_refs_initially_empty_in_execution_state PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_write_recovery_continuity_native_resume PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_write_recovery_continuity_fresh_relaunch PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_write_recovery_attempt_resume PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_write_recovery_attempt_recover_fallback PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_recovered_via_truth_distinction PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRecoveryContinuityArtifacts::test_both_recovery_artifacts_written_together PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestSessionAwareContinuityBootstrap::test_bootstrap_writes_runner_metadata_to_ledger PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestSessionAwareContinuityBootstrap::test_bootstrap_defaults_session_to_run_id PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestSessionAwareContinuityBootstrap::test_journal_includes_session_metadata PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRuntimeRecoveryRecordExpanded::test_evidence_refs_round_trip PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRuntimeRecoveryRecordExpanded::test_deserialization_preserves_expanded_fields PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRuntimeRecoveryRecordExpanded::test_deserialization_defaults_for_missing_fields PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestAgentExecutionStateExpanded::test_expanded_fields_default_values PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestAgentExecutionStateExpanded::test_expanded_fields_round_trip PASSED [  2%]
tests/orchestration/integration/test_opencode_runtime_continuity_integration.py::TestRunStoreSessionMetadata::test_runtime_state_with_session_metadata_round_trips PASSED [  2%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_claim_before_start_seam PASSED [  2%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_claim_before_start_durable_before_start PASSED [  2%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_reconcile_before_complete_seam PASSED [  2%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_reconcile_merge_conflict_prevents_complete PASSED [  2%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_review_needs_fix_routes_to_resolution_case PASSED [  3%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_review_needs_replan_routes_to_resolution_case PASSED [  3%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_review_operator_required_routes_to_resolution_case PASSED [  3%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_review_parse_failure_routes_to_resolution_case PASSED [  3%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_review_pass_allows_complete_step PASSED [  3%]
tests/orchestration/integration/test_orch_app_routing_seam.py::test_full_lifecycle_proof_all_three_seams PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_run_completes_step_to_done PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_run_produces_terminal_run_record PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_run_event_artifacts_exist PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_sequential_runs_advance_plan PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_explicit_step_id_completes PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2ECompletion::test_run_no_claimable_step_fails_gracefully PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2EReconcileBehavior::test_run_creates_frozen_config_snapshot PASSED [  3%]
tests/orchestration/integration/test_orch_e2e_lifecycle.py::TestOrchestrationE2EReconcileBehavior::test_run_creates_heartbeat_artifact PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_valid_session_all_checks_pass PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_missing_file_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_invalid_json_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_missing_required_field_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_empty_field_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_wrong_runner_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_missing_prompt_bundle_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_missing_runner_prompt_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestSessionValidation::test_session_empty_runner_prompt_fails PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackNativeResume::test_native_resume_chosen_when_session_valid PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackFreshRelaunch::test_fresh_relaunch_when_session_missing PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackFreshRelaunch::test_fresh_relaunch_when_session_invalid_runner PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackFreshRelaunch::test_fresh_relaunch_when_session_missing_field PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryTruthLabeling::test_native_resume_continuity_json PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryTruthLabeling::test_fresh_relaunch_continuity_json PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryTruthLabeling::test_truth_labels_are_semantically_distinct PASSED [  3%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryAttemptArtifacts::test_resume_attempt_artifact_for_native_resume PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryAttemptArtifacts::test_both_attempt_artifacts_for_fresh_relaunch PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryAttemptArtifacts::test_attempt_artifacts_in_recovery_directory PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackExplicitFailure::test_both_paths_fail_returns_null_continuity PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackExplicitFailure::test_both_paths_fail_attempts_record_failure_reasons PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryArtifactReadback::test_continuity_readback_round_trip PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryArtifactReadback::test_attempt_readback_round_trip PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryArtifactReadback::test_recover_attempt_readback PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryArtifactReadback::test_readback_returns_none_when_missing PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryArtifactReadback::test_readback_returns_none_on_corrupt_json PASSED [  4%]
tests/orchestration/integration/test_recovery_fallback.py::TestRecoveryFallbackEndToEnd::test_fallback_result_persist_consistent_with_orch_app PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfig::test_default_values PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfig::test_custom_values PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfig::test_in_orchestration_config PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfig::test_config_round_trip_yaml PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_valid_config_no_errors PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_max_parallelism_below_minimum PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_max_parallelism_above_maximum PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_max_parallelism_boundary_min PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_max_parallelism_boundary_max PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_collect_poll_interval_zero PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_resolver_timeout_zero PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveConfigValidation::test_planner_timeout_zero PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_freeze_config_returns_drive_provenance PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_default_provenance_source PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_custom_provenance_source PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_drift_detection PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_no_drift_when_equal PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_freeze_drive_config PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestFrozenConfigProvenance::test_frozen_snapshot_round_trip PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventRegistry::test_all_drive_kinds_in_registry PASSED [  4%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventRegistry::test_drive_started_schema PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventRegistry::test_child_run_admitted_schema PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventRegistry::test_drive_final_schema PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventRegistry::test_no_missing_drive_family_members PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_basic_construction PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_missing_drive_id_raises PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_naive_timestamp_raises PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_entry_hash_deterministic PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_different_payload_different_hash PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_to_orchestration_envelope PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventEnvelope::test_with_integrity PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestBuildDriveEvent::test_basic_event PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestBuildDriveEvent::test_with_optional_fields PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventChainIntegrity::test_valid_chain PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventChainIntegrity::test_seq_drift_detected PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventChainIntegrity::test_prev_hash_drift_detected PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventChainIntegrity::test_empty_chain_valid PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveEventChainIntegrity::test_orchestration_envelope_chain_preserves_drive_id PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionReplay::test_replay_drive_events_persists_artifacts PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionReplay::test_projection_handles_drive_events PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionReplay::test_drive_barrier_events_affect_case_count PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionReplay::test_projection_replay_after_restart PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionFromRecords::test_projection_from_records PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionFromRecords::test_projection_with_barrier PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestDriveProjectionFromRecords::test_projection_active_from_child_runs_not_record PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestToolRegistryDriveFamily::test_drive_family_registered PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestToolRegistryDriveFamily::test_drive_tools_validated PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestToolRegistryDriveFamily::test_unknown_drive_tool_rejected PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestToolRegistryDriveFamily::test_drive_family_in_allowlist PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestArtifactSchemaWiring::test_step_artifact_from_projection PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestArtifactSchemaWiring::test_case_artifact_from_projection PASSED [  5%]
tests/orchestration/test_drive_events_config_observability.py::TestArtifactSchemaWiring::test_drive_artifacts_persisted PASSED [  6%]
tests/orchestration/test_drive_events_config_observability.py::TestConfigPrecedenceDrift::test_drift_on_max_parallelism PASSED [  6%]
tests/orchestration/test_drive_events_config_observability.py::TestConfigPrecedenceDrift::test_no_drift_on_matching_values PASSED [  6%]
tests/orchestration/test_drive_events_config_observability.py::TestConfigPrecedenceDrift::test_partial_drift PASSED [  6%]
tests/orchestration/test_drive_events_config_observability.py::TestConfigPrecedenceDrift::test_freeze_config_snapshot_includes_drive_provenance PASSED [  6%]
tests/orchestration/test_drive_events_config_observability.py::TestConfigPrecedenceDrift::test_drive_env_var_override PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceMatrix::test_matrix_fixture_exists PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceMatrix::test_required_scenarios_are_all_encoded PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceMatrix::test_all_scenarios_pin_real_opencode_authority PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceMatrix::test_matrix_yaml_round_trips_for_external_operator_use PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceMatrix::test_required_coverage_classes_have_operator_readable_proof_map PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveHarnessFixtureWiring::test_harness_writes_real_opencode_config PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveHarnessFixtureWiring::test_harness_initializes_real_git_repo PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_harness.py::TestOpenCodeDriveAcceptanceLiveSmoke::test_linear_drive_smoke_uses_real_opencode_runner SKIPPED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_matrix_fixture_exists PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_required_scenarios_are_all_encoded PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_all_scenarios_pin_real_opencode_authority PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_matrix_yaml_round_trips_for_external_operator_use PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_coverage_includes_all_rfc_required_classes PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_required_classes_map_to_concrete_test_proof PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMatrixFixtureCompleteness::test_high_risk_classes_include_live_or_e2e_proof PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLinearDAGAutoDrain::test_linear_dag_auto_drain_to_completion PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLinearDAGAutoDrain::test_linear_dag_persists_final_state PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMultiPhaseDAGAutoDrain::test_multi_phase_drain_respects_phase_order PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestMultiPhaseDAGAutoDrain::test_multi_phase_auto_close PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestParallelReadyFrontierDispatch::test_parallel_frontier_dispatches_all_ready PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestParallelReadyFrontierDispatch::test_parallel_frontier_captures_all_step_ids PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestParallelReadyFrontierDispatch::test_dispatch_batch_launches_and_persists_child_run_refs PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestBoundedParallelismEnforcement::test_bounded_parallelism_limits_dispatch PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestBoundedParallelismEnforcement::test_max_parallelism_boundary_values PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestBoundedParallelismEnforcement::test_max_parallelism_rejects_invalid PASSED [  6%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestRuntimeFailureResolverContinue::test_runtime_failure_enters_barrier PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestRuntimeFailureResolverContinue::test_barrier_state_persisted PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveResumeAndRecover::test_resume_restores_active_child_runs PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveResumeAndRecover::test_recover_preserves_barrier_state PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveResumeAndRecover::test_resume_terminal_drive_returns_current_state PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveResumeAndRecover::test_recover_terminal_drive_returns_immediately PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveResumeAndRecover::test_recover_dry_run_no_state_change PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestPauseUnpauseStopControl::test_pause_transitions_to_paused_status PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestPauseUnpauseStopControl::test_stop_is_terminal PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveAdmissionGuard::test_duplicate_active_drive_rejected PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveAdmissionGuard::test_new_drive_allowed_after_terminal PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveTransitionValidation::test_valid_transitions_accepted PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveTransitionValidation::test_terminal_statuses_are_absorbing PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveTransitionValidation::test_invalid_transitions_rejected PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestPhasePlanAutoClose::test_plan_complete_transitions_to_completed PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestPhasePlanAutoClose::test_plan_incomplete_stays_running PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveFrontierSnapshot::test_frontier_captures_claimable_steps PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveFrontierSnapshot::test_frontier_empty_when_no_claimable PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveChildRunPersistence::test_child_run_created_and_loaded PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveChildRunPersistence::test_child_run_list_by_drive PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveChildRunPersistence::test_active_child_runs_filtering PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestHarnessFixtureWiring::test_harness_writes_real_opencode_config PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestHarnessFixtureWiring::test_harness_initializes_real_git_repo PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestHarnessFixtureWiring::test_harness_builds_live_app PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestHarnessFixtureWiring::test_harness_start_drive_returns_valid_result PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveControlConsumption::test_stop_drive_is_terminal PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestDriveControlConsumption::test_paused_drive_returns_immediately_on_loop PASSED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_linear_drive_smoke_uses_real_opencode_runner SKIPPED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_foreground_drive_supervisor_jsonl_monitors_real_child_to_completion SKIPPED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_drive_resume_uses_real_app_surface SKIPPED [  7%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_drive_recover_uses_real_app_surface SKIPPED [  8%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_control_pause_and_unpause_drive SKIPPED [  8%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_control_stop_drive SKIPPED [  8%]
tests/orchestration/test_opencode_drive_acceptance_matrix.py::TestLiveOpenCodeDriveMatrix::test_phase_auto_close_with_real_app SKIPPED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestParallelChildRunPrepareStart::test_prepare_child_run_creates_workspace_with_drive_context PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestParallelChildRunPrepareStart::test_multiple_step_child_runs_prepare_independently PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestParallelChildRunPrepareStart::test_start_child_run_returns_child_run_ref PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestParallelChildRunPrepareStart::test_multiple_step_child_runs_start_independently PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_active_step_count_zero_initially PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_active_step_count_tracks_running_child_runs PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_active_step_count_per_drive_isolation PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_resolver_runs_do_not_consume_step_capacity PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_planner_runs_do_not_consume_step_capacity PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCapacityAccounting::test_step_count_decreases_on_terminal_result PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCollectChildRun::test_collect_child_run_returns_none_while_running PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCollectChildRun::test_collect_child_run_updates_status_on_success PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCollectChildRun::test_collect_child_run_updates_status_on_fail PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestCollectChildRun::test_collect_child_run_on_non_drive_execution_returns_none_ref PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefBookkeeping::test_child_run_ref_has_workspace PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefBookkeeping::test_child_run_ref_has_runner PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefBookkeeping::test_child_run_ref_has_artifact_root PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefBookkeeping::test_resolver_child_run_ref_has_case_id PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefBookkeeping::test_planner_child_run_ref_has_planner_request_id PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefQuery::test_child_run_ref_lookup PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestChildRunRefQuery::test_child_run_ref_lookup_nonexistent PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestDriveChildRunQueries::test_child_runs_for_drive PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestDriveChildRunQueries::test_active_child_runs_for_drive PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestBarrierRunCapacityRegression::test_step_and_resolver_coexist PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestBarrierRunCapacityRegression::test_step_and_planner_coexist PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestBarrierRunCapacityRegression::test_all_barrier_kinds_no_step_count PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestParallelWorkspaceIsolation::test_parallel_child_runs_have_distinct_worktree_paths PASSED [  8%]
tests/orchestration/test_runtime_parallel_child_run.py::TestStatusMapping::test_map_success PASSED [  9%]
tests/orchestration/test_runtime_parallel_child_run.py::TestStatusMapping::test_map_fail PASSED [  9%]
tests/orchestration/test_runtime_parallel_child_run.py::TestStatusMapping::test_map_stall PASSED [  9%]
tests/orchestration/test_runtime_parallel_child_run.py::TestStatusMapping::test_map_transport_error PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunMainPath::test_child_run_noop_reconcile_produces_result PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunMainPath::test_child_run_merged_reconcile_produces_result PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunMainPath::test_child_run_uncommitted_workspace_changes_are_merged PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunMainPath::test_child_run_reconcile_returns_child_run_ref_via_separate_query PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunFailurePreservation::test_merge_conflict_preserves_conflict_files PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunFailurePreservation::test_merge_conflict_does_not_report_false_success PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunFailurePreservation::test_merge_conflict_preserves_worktree_binding_evidence PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBeginReconcileChildRunFailurePreservation::test_abort_preserves_error_evidence PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestCleanupChildRunLifecycleBarriers::test_cleanup_blocked_while_execution_active PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestCleanupChildRunLifecycleBarriers::test_cleanup_blocked_when_reconcile_uncaptured PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestCleanupChildRunLifecycleBarriers::test_cleanup_blocked_on_merge_conflict PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestCleanupChildRunLifecycleBarriers::test_forced_cleanup_succeeds_even_with_merge_conflict PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestCleanupChildRunLifecycleBarriers::test_successful_reconcile_allows_cleanup PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_merged_recovery_state_has_no_conflict_files PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_noop_recovery_state_has_no_conflict_files PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_merge_conflict_preserves_truthful_evidence PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_aborted_preserves_error_summary PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_protected_path_policy_restored_with_evidence PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestBuildReconcileRecoveryState::test_artifact_refs_never_empty_on_merge_conflict PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestReconcileRecoveryStateTypes::test_closure_status_matches_reconcile_result_status PASSED [  9%]
tests/orchestration/test_runtime_reconcile_mechanics.py::TestReconcileRecoveryStateTypes::test_recovery_state_is_frozen PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_orchestrator_receipt_uses_item_id_and_revision PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_prompt_injects_deterministic_checklist_inventory PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_parse_checklist_receipt_requires_item_id_and_revision PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_parse_checklist_receipt_records_field_revision_and_final_state PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_parse_checklist_receipt_accepts_rfc_minimal_entries_exactly PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_receipt_validation_is_reachable_from_freeform_evidence_assessment PASSED [  9%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_freeform_assessment_rejects_stale_receipt_after_inventory_refresh PASSED [ 10%]
tests/orchestration/unit/test_checklist_receipt_contract.py::test_freeform_assessment_accepts_receipt_matching_refreshed_inventory PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStartSurface::test_start_drive_returns_result PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStartSurface::test_start_drive_admission_error_includes_drive_id PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStartSurface::test_start_drive_max_parallelism_validation PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStartSurface::test_start_drive_parallelism_boundaries PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStartSurface::test_drive_driver_wires_app_resolver PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStatusSurface::test_drive_status_existing_drive PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStatusSurface::test_drive_status_not_found PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStatusSurface::test_drive_status_includes_frontier PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStatusSurface::test_drive_status_includes_blocked_case_ids PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveStatusSurface::test_foreground_does_not_exit_before_resolver_for_resolving_case PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveRunsSurface::test_drive_runs_empty PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveRunsSurface::test_drive_runs_with_child_runs PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestResolveLatestDriveId::test_no_drives_returns_none PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestResolveLatestDriveId::test_returns_active_drive PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestActiveDriveRejection::test_run_rejected_when_active_drive_exists PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestActiveDriveRejection::test_has_active_drive_for_plan PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestActiveDriveRejection::test_no_rejection_when_no_active_drive PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveControlConsumption::test_run_drive_loop_consumes_queued_stop_before_dispatch PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveControlConsumption::test_force_stop_cancels_persisted_active_child_refs PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveResumeSurface::test_resume_existing_drive PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveRecoverSurface::test_recover_dry_run PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestDriveRecoverSurface::test_recover_actual PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_help_exits_zero PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_status_help_exits_zero PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_runs_help_exits_zero PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_resume_help_exits_zero PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_recover_help_exits_zero PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_status_no_selector_exits_3 PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_resume_no_selector_exits_3 PASSED [ 10%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_recover_no_selector_exits_3 PASSED [ 11%]
tests/orchestration/unit/test_cli_drive_lifecycle_surface.py::TestCLIDriveExitCodes::test_cli_drive_runs_no_selector_exits_3 PASSED [ 11%]
tests/orchestration/unit/test_config_projection_recovery_residual_slice.py::test_config_explicit_relative_path_and_env_precedence_preserve_overwrite_semantics PASSED [ 11%]
tests/orchestration/unit/test_config_projection_recovery_residual_slice.py::test_projection_payloads_preserve_new_state_precedence_and_stale_conflict PASSED [ 11%]
tests/orchestration/unit/test_config_projection_recovery_residual_slice.py::test_recovery_missing_session_falls_back_and_malformed_readback_is_none PASSED [ 11%]
tests/orchestration/unit/test_config_projection_recovery_residual_slice.py::test_recovery_native_resume_keeps_truth_label_and_absolute_workspace PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_default_config_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_default_config_serialized_no_role_profiles_key PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_override_only_config_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_custom_role_only_config_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_override_and_custom_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_no_builtin_ids_in_serialized_role_profiles PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_frozen_snapshot_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_legacy_frozen_snapshot_with_builtin_role_profiles_loads PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_deep_update_config_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigRoundTripSymmetry::test_multiple_overrides_roundtrip PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigDiscoveryEnvIsolation::test_no_config_returns_defaults_without_home_config_bleed PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigDiscoveryEnvIsolation::test_cwd_config_isolated_from_home_config PASSED [ 11%]
tests/orchestration/unit/test_config_roundtrip_and_isolation.py::TestConfigDiscoveryEnvIsolation::test_env_override_isolated_from_home_config PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_default_profiles_all_default_source PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_override_produces_override_source PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_custom_role_produces_custom_source PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_provenance_covers_all_profile_fields PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_override_and_default_coexist PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestBuildRoleProfileProvenance::test_mix_of_builtin_and_custom_roles PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceHuman::test_effective_includes_provenance_lines PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceHuman::test_effective_provenance_format PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceHuman::test_effective_shows_all_builtin_roles PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceHuman::test_summary_mode_has_no_provenance PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceHuman::test_override_shows_override_source PASSED [ 11%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_effective_json_includes_provenance PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_provenance_json_structure PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_json_serializable PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_summary_mode_has_no_provenance_json PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_override_source_in_json PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_custom_source_in_json PASSED [ 12%]
tests/orchestration/unit/test_config_show_provenance.py::TestConfigShowProvenanceJSON::test_default_source_in_json PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_get_returns_metadata_for_known_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_get_returns_none_for_unknown_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_all_families_returns_canonical_families PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_is_registered_for_known_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_is_registered_for_unknown_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_is_valid_tool_for_valid_tool PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestToolFamilyRegistry::test_is_valid_tool_for_invalid_tool PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestCanonicalToolFamilies::test_canonical_tool_families_returns_tuple PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateAllowlist::test_validate_allowlist_allows_known_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateAllowlist::test_validate_allowlist_denies_unknown_family PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateAllowlist::test_validate_allowlist_deny_all_empty_tuple PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlistEntry::test_valid_entry_no_errors PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlistEntry::test_unknown_family_rejected PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlistEntry::test_unknown_tool_rejected PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlistEntry::test_wildcard_pattern_rejected PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlistEntry::test_prefix_pattern_rejected PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlist::test_valid_allowlist_no_errors PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestValidateToolAllowlist::test_mixed_errors_collected PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_from_explicit_path PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_from_env_var PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_from_cwd PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_returns_defaults_when_no_file PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_default_config_contains_config_backed_role_registry PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_parses_role_profiles_and_default_role_ids PASSED [ 12%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_rejects_builtin_shadow_in_role_profiles PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_load_preserves_role_id_agent_id_prompt_family_separation_for_future_role PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_missing_explicit_file_raises PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigDiscovery::test_env_override_applies_runtime_artifact_and_workspace_roots PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_valid_default_config_passes PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_invalid_cleanup_policy_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_invalid_isolation_mode_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_zero_timeout_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_negative_timeout_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_zero_max_tool_calls_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_negative_retention_days_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_dispatch_default_role_must_reference_configured_profile PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_resolver_default_role_must_reference_resolver_family PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_tool_allowlist_unknown_family_rejected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestConfigValidation::test_multiple_validation_errors_collected PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestFreezeConfig::test_freeze_returns_frozen_snapshot PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestFreezeConfig::test_freeze_with_run_dir_writes_snapshot PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestFreezeConfig::test_frozen_snapshot_contains_config_data PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestWriteFrozenSnapshot::test_write_snapshot_creates_file PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestWriteFrozenSnapshot::test_snapshot_roundtrip PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestRequiredSurfaceNotDisabled::test_events_jsonl_default_true PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestRequiredSurfaceNotDisabled::test_text_log_default_true PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestRequiredSurfaceNotDisabled::test_projected_state_default_true PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestRequiredSurfaceNotDisabled::test_per_step_artifacts_default_true PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestRequiredSurfaceNotDisabled::test_per_case_artifacts_default_true PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestSiblingSearchVerification::test_tool_registry_imports_resolved PASSED [ 13%]
tests/orchestration/unit/test_config_state_impl.py::TestSiblingSearchVerification::test_config_imports_tool_registry PASSED [ 13%]
tests/orchestration/unit/test_config_state_red.py::TestConfigDiscoveryGaps::test_config_loader_not_implemented PASSED [ 13%]
tests/orchestration/unit/test_config_state_red.py::TestConfigDiscoveryGaps::test_discovery_order_vectl_config_env_not_specified PASSED [ 13%]
tests/orchestration/unit/test_config_state_red.py::TestConfigDiscoveryGaps::test_precedence_cli_vs_env_vs_file_not_specified PASSED [ 13%]
tests/orchestration/unit/test_config_state_red.py::TestConfigDiscoveryGaps::test_env_var_naming_convention_not_enforced PASSED [ 13%]
tests/orchestration/unit/test_config_state_red.py::TestConfigValidationGaps::test_validation_rules_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestConfigValidationGaps::test_invalid_cleanup_policy_not_rejected PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestConfigValidationGaps::test_tool_allowlist_validation_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestRedactionProvenanceGaps::test_config_show_effective_provenance_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestRedactionProvenanceGaps::test_redaction_rules_not_specified PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestFrozenConfigSnapshotGaps::test_freeze_config_is_noop PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestFrozenConfigSnapshotGaps::test_snapshot_persistence_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestFrozenConfigSnapshotGaps::test_resumed_run_snapshot_reuse_rule_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestRunRegistryGaps::test_run_registry_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestRunRegistryGaps::test_index_append_only_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestRunRegistryGaps::test_latest_lookup_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestSamePlanAdmissionGaps::test_same_plan_admission_rule_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestSamePlanAdmissionGaps::test_run_selection_explicit_required_for_mutating_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestCaseIndexLifecycleGaps::test_cases_index_not_implemented PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestCaseIndexLifecycleGaps::test_case_index_pruning_rule_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestCaseIndexLifecycleGaps::test_case_id_global_uniqueness_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestResumedRunSnapshotReuseGaps::test_resume_uses_frozen_snapshot_not_enforced PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestResumedRunSnapshotReuseGaps::test_recovery_validates_transcript_before_resume_safe PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestMinimalConfigFixtureConformance::test_minimal_config_yaml_parses_to_valid_dict PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestMinimalConfigFixtureConformance::test_minimal_config_has_no_convenience_fields PASSED [ 14%]
tests/orchestration/unit/test_config_state_red.py::TestMinimalConfigFixtureConformance::test_full_config_all_defaults_explicit PASSED [ 14%]
tests/orchestration/unit/test_contract_lock_boundaries.py::test_resolution_case_preserves_required_semantics_while_resolver_contract_is_exported PASSED [ 14%]
tests/orchestration/unit/test_contract_lock_boundaries.py::test_execution_result_surfaces_operator_message_for_runtime_failures PASSED [ 14%]
tests/orchestration/unit/test_contract_lock_boundaries.py::test_core_adapter_contract_pins_normal_claim_and_post_reconcile_complete PASSED [ 14%]
tests/orchestration/unit/test_contract_lock_boundaries.py::test_runtime_surface_preserves_backend_lifecycle_split PASSED [ 14%]
tests/orchestration/unit/test_contract_lock_boundaries.py::test_core_adapter_is_the_only_contract_locked_mutation_boundary PASSED [ 14%]
tests/orchestration/unit/test_contracts.py::TestCoreSnapshot::test_core_snapshot_is_dataclass PASSED [ 14%]
tests/orchestration/unit/test_contracts.py::TestCoreSnapshot::test_core_snapshot_fields_match_spec PASSED [ 14%]
tests/orchestration/unit/test_contracts.py::TestCoreSnapshot::test_core_snapshot_frozen PASSED [ 14%]
tests/orchestration/unit/test_contracts.py::TestCoreSnapshot::test_core_snapshot_field_types PASSED [ 14%]
tests/orchestration/unit/test_contracts.py::TestRosterSnapshot::test_roster_snapshot_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestRosterSnapshot::test_roster_snapshot_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestRosterSnapshot::test_roster_snapshot_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestRuntimeSnapshot::test_runtime_snapshot_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestRuntimeSnapshot::test_runtime_snapshot_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestRuntimeSnapshot::test_runtime_snapshot_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestControlDecision::test_control_decision_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestControlDecision::test_control_decision_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestControlDecision::test_control_decision_kind_literal_values PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestControlDecision::test_control_decision_optional_fields PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestWorkLease::test_work_lease_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestWorkLease::test_work_lease_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestWorkLease::test_work_lease_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_request_mode_literal_values PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_session_policy_literal_values PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_defaults PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_explicit_mode_and_policy PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionRequest::test_execution_request_backward_compatible PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionResult::test_execution_result_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionResult::test_execution_result_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionResult::test_execution_result_status_literal_values PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestExecutionResult::test_execution_result_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionCase::test_resolution_case_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionCase::test_resolution_case_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionCase::test_resolution_case_frozen PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionCase::test_resolution_case_nested_snapshots PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionReport::test_resolution_report_is_dataclass PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionReport::test_resolution_report_fields_match_spec PASSED [ 15%]
tests/orchestration/unit/test_contracts.py::TestResolutionReport::test_resolution_report_status_literal_values PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestResolutionReport::test_resolution_report_frozen PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestResolutionReport::test_resolution_report_planner_request_optional PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestIsolationModeContract::test_isolation_mode_values_match_spec PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRequestMode::test_request_mode_literal_values PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestSessionPolicy::test_session_policy_literal_values PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveredVia::test_recovered_via_literal_values PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveredVia::test_recovered_via_truth_labels_are_distinct PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_is_dataclass PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_fields_match_spec PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_frozen PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_recovered_via_type PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_required_and_optional_fields PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryContinuity::test_recovery_continuity_both_paths PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_is_dataclass PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_fields_match_spec PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_frozen PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_attempt_kind_literal_values PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_minimal_construction PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_resume_scenario PASSED [ 16%]
tests/orchestration/unit/test_contracts.py::TestRecoveryAttempt::test_recovery_attempt_recover_with_fallback PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_dispatches_claimable_step_with_configured_role PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_roster_none_claim_is_not_interpreted_as_plan_blockage PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_ignores_roster_available_agent_order_for_dispatch_role PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_waits_when_execution_is_already_active PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_returns_done_when_plan_complete_and_runtime_idle PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_unresolved_reasons_yield_resolve_not_dispatch_regression PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_evaluate_blocked_without_dispatch_path_yields_resolve PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_apply_resolution_unblocked_re_evaluates_from_refreshed_state PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_apply_resolution_waiting_maps_to_wait PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_apply_resolution_operator_required_maps_to_wait PASSED [ 16%]
tests/orchestration/unit/test_control.py::test_apply_resolution_halt_maps_to_halt PASSED [ 17%]
tests/orchestration/unit/test_control.py::test_evaluate_current_reads_all_sources_once PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_send_persists_pause_request_in_pending_layout PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_bounded_pending_actions_enforced PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_applied_and_rejected_acknowledgement_lookup PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_wait_for_ack_timeout_is_explicit PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_malformed_and_duplicate_pending_files_are_rejected PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_run_local_path_normalization_prevents_cross_run_leakage PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_wait_for_ack_observes_async_applied_receipt PASSED [ 17%]
tests/orchestration/unit/test_control_channel.py::test_send_to_control_requires_explicit_channel_instance PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDeterministicFrontierOrdering::test_sorts_step_ids_lexicographically PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDeterministicFrontierOrdering::test_already_sorted_is_idempotent PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDeterministicFrontierOrdering::test_single_step_frontier PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDeterministicFrontierOrdering::test_empty_frontier PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDeterministicFrontierOrdering::test_deterministic_ordering_in_dispatch_batch PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBoundedCapacityAccounting::test_full_capacity_dispatch PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBoundedCapacityAccounting::test_partial_capacity_truncation PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBoundedCapacityAccounting::test_zero_capacity_waits PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBoundedCapacityAccounting::test_capacity_single_slot_remaining PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBoundedCapacityAccounting::test_role_bindings_cover_all_dispatched_steps PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_barrier_runtime_failure_yields_wait PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_barrier_merge_conflict_yields_wait PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_barrier_recovery_gate_yields_wait PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_barrier_operator_pause_yields_halt PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_barrier_blocks_dispatch_even_with_claimable PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestBarrierHandling::test_recovery_gate_blocked_without_barrier PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestOperatorPause::test_operator_paused_drive_yields_halt PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestOperatorPause::test_operator_active_drive_allows_dispatch PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestOpenCasesRouting::test_open_cases_with_drive_returns_resolve PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestOpenCasesRouting::test_open_cases_without_drive_not_routed_to_resolve PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_dispatch_batch_step_ids_non_empty PASSED [ 17%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_dispatch_batch_role_bindings_cover_all PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_resolve_case_ids_non_empty PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_blocked_resolve_has_case_ids PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_wait_step_ids_and_case_ids_empty PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_done_step_ids_and_case_ids_empty PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_halt_barrier_required_true PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDecisionInvariants::test_replan_planner_request_present PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_empty_dispatch_batch_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_dispatch_batch_missing_role_binding_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_resolve_empty_case_ids_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_replan_none_planner_request_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_wait_with_step_ids_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_wait_with_case_ids_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_done_with_step_ids_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_halt_without_barrier_required_rejected PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_valid_dispatch_batch_passes PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestInvariantValidation::test_valid_dispatch_passes PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDoneDecision::test_done_when_plan_complete_and_idle_no_drive PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDoneDecision::test_done_with_drive_no_active_runs PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestDoneDecision::test_not_done_with_active_child_runs PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestHaltDecision::test_halt_from_operator_pause_barrier PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestHaltDecision::test_halt_from_operator_paused_drive PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestHaltDecision::test_halt_from_resolver_report PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestHaltDecision::test_halt_from_planner_halt_bundle PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_unblocked_replan_transition PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_unblocked_re_evaluate_with_claimable PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_unblocked_with_drive_dispatch_batch PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_waiting_maps_to_wait PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_operator_required_maps_to_wait PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyResolution::test_halt_maps_to_halt PASSED [ 18%]
tests/orchestration/unit/test_control_drive.py::TestApplyPlannerResult::test_applyable_re_evaluates PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestApplyPlannerResult::test_applyable_with_drive_dispatch_batch PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestApplyPlannerResult::test_applyable_no_claimable_yields_wait PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestApplyPlannerResult::test_operator_required_yields_wait_with_barrier PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestApplyPlannerResult::test_halt_yields_halt_with_barrier PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestCustomDispatchRole::test_custom_role_in_drive_batch PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestCustomDispatchRole::test_custom_role_in_legacy_dispatch PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestSyntheticCaseIds::test_unresolved_state_has_synthetic_case_id PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestSyntheticCaseIds::test_blocked_steps_have_synthetic_case_id PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestSyntheticCaseIds::test_plan_conflict_has_synthetic_case_id PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_dispatch_batch PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_dispatch_without_drive PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_resolve_from_blocked PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_resolve_from_open_cases PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_replan_from_resolver PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_wait_from_active_work PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_done_from_plan_complete PASSED [ 19%]
tests/orchestration/unit/test_control_drive.py::TestAllDecisionKinds::test_halt_from_operator_pause PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_contract.py::test_core_adapter_is_protocol PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_contract.py::test_core_adapter_exposes_authoritative_bridge_methods PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_contract.py::test_core_adapter_step_isolation_uses_authoritative_isolation_mode PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_snapshot_uses_authoritative_core_reads PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_snapshot_does_not_turn_evidence_guard_debt_into_scheduler_blocker PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_step_isolation_surfaces_workspace_and_independent PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_claim_and_complete_mutations_flow_through_core_surface PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_claim_rejects_non_normal_flow_contract PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_load_step_data_for_dispatch_public_seam PASSED [ 19%]
tests/orchestration/unit/test_core_adapter_impl.py::test_load_step_data_for_dispatch_returns_none_for_missing_step PASSED [ 19%]
tests/orchestration/unit/test_dispatch_policy.py::TestStepVerifyToVerifyMode::test_none_maps_to_none PASSED [ 19%]
tests/orchestration/unit/test_dispatch_policy.py::TestStepVerifyToVerifyMode::test_expected_red_maps_correctly PASSED [ 19%]
tests/orchestration/unit/test_dispatch_policy.py::TestStepVerifyToVerifyMode::test_must_green_maps_correctly PASSED [ 19%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_get_returns_concrete_profile PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_get_returns_different_profiles_for_different_roles PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_has_role_returns_true_for_known_role PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_has_role_returns_false_for_unknown_role PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_get_fails_explicitly_on_unknown_role PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_resolve_role_with_step_agent PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_resolve_role_without_step_agent PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_resolve_role_with_empty_step_agent PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_resolver_family_roles_require_main_worktree PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_default_resolver_profiles_preserve_cleaned_taxonomy PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_planner_family_roles_require_main_worktree PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_reviewer_family_roles_require_main_worktree PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_coder_family_roles_use_linked_worktree PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_custom_profiles_extend_registry PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_main_worktree_family_policy_is_validated_for_custom_profiles PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_default_role_is_configurable PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigRoleProfileRegistry::test_registry_defaults_are_loaded_from_orchestration_config PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_render_returns_prompt_bundle PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_has_role_for_known_role PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_has_role_delegates_to_role_registry_when_provided PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_rendering_differs_by_role_family PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_coder_family_prompt_contains_step_data PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_planner_family_prompt_includes_remediation_context PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_resolver_family_prompt_includes_facade_constraint PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_tacit_resolver_role_renders_distinct_prompt_content PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_agent_id_precedence_preserves_tacit_prompt_for_future_role_id PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_main_worktree_context_in_messages PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestConfigPromptRegistry::test_vectl_facade_only_in_messages PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_from_control_decision PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_default_role_when_no_agent PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_role_from_step_agent PASSED [ 20%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_rejects_non_dispatch_decision PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_rejects_missing_step_id PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_dispatch_spec_rejects_unknown_role PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_resolution_subtask_spec PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_build_resolution_subtask_spec_rejects_unknown_role PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_verify_mode_mapping_in_dispatch_spec PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestDispatchCoordinator::test_prompt_rendering_is_centralized PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestReviewResultNormalization::test_pass_outcome_returns_none PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestReviewResultNormalization::test_needs_fix_creates_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestReviewResultNormalization::test_needs_replan_creates_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestReviewResultNormalization::test_operator_required_creates_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestReviewResultNormalization::test_normalize_preserves_evidence_refs PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestParseFailureNormalization::test_parse_failure_creates_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestParseFailureNormalization::test_parse_failure_includes_raw_output_preview PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestPlannerSubagentRouting::test_planner_dispatch_through_resolver_not_step PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestPlannerSubagentRouting::test_no_silent_downgrade_of_planner_to_coder PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestCoreAdapterDispatchBoundary::test_core_adapter_loads_step_data_for_dispatch PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy.py::TestCoreAdapterDispatchBoundary::test_adapter_returns_none_for_missing_step PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::test_dispatch_spec_construction_from_control_decision PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::test_dispatch_spec_verify_mode_expected_red_wires_correctly PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::test_dispatch_spec_main_worktree_roles_enforce_execution_context PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigRoleProfileRegistryBehavior::test_role_profile_registry_get_returns_concrete_profile PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigRoleProfileRegistryBehavior::test_role_profile_registry_has_role_unknown_role_returns_false PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigRoleProfileRegistryBehavior::test_role_profile_registry_fails_explicitly_on_unknown_role PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigPromptRegistryBehavior::test_prompt_registry_render_returns_prompt_bundle PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigPromptRegistryBehavior::test_prompt_registry_has_role_for_known_role PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestConfigPromptRegistryBehavior::test_prompt_rendering_differs_by_role_family PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestStructuredReviewResultNormalization::test_structured_review_result_pass_normalizes_to_continue PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestStructuredReviewResultNormalization::test_structured_review_result_needs_fix_normalizes_to_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestStructuredReviewResultNormalization::test_structured_review_result_needs_replan_normalizes_to_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestStructuredReviewResultNormalization::test_structured_review_result_operator_required_normalizes_to_resolution_case PASSED [ 21%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestStructuredReviewResultNormalization::test_structured_review_result_parse_failure_creates_resolution_case PASSED [ 22%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestMainWorktreeRolePolicy::test_resolver_family_roles_require_main_worktree_context PASSED [ 22%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestMainWorktreeRolePolicy::test_planner_family_roles_require_main_worktree_context PASSED [ 22%]
tests/orchestration/unit/test_dispatch_policy_red.py::TestMainWorktreeRolePolicy::test_reviewer_family_roles_require_main_worktree_context PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByPausedOperatorNotification::test_dispatch_blocked_when_operator_notification_paused PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByPausedOperatorNotification::test_dispatch_blocked_when_notification_paused_reconcile_conflict PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByPausedOperatorNotification::test_dispatch_blocked_when_notification_paused_recovery_hold PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByPausedOperatorNotification::test_dispatch_not_blocked_when_notification_is_active PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByPausedOperatorNotification::test_dispatch_not_blocked_when_no_notification PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByRecoveryGate::test_dispatch_blocked_when_unsafe_dispatch_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByRecoveryGate::test_dispatch_not_blocked_when_gate_allows PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByRecoveryGate::test_dispatch_not_blocked_when_no_recovery_report PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByRecoveryGate::test_dispatch_not_blocked_when_recovery_report_has_no_gate PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchBlockedByRecoveryGate::test_both_notification_and_gate_block PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestCompleteBlockedByGate::test_complete_blocked_when_duplicate_complete_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestCompleteBlockedByGate::test_complete_blocked_by_paused_notification PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestCompleteBlockedByGate::test_complete_not_blocked_when_gate_cleared PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestCompleteBlockedByGate::test_complete_not_blocked_when_no_gate PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestRouteTerminalExecutionDuplicateCompleteGate::test_route_terminal_raises_on_duplicate_complete_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestRouteTerminalExecutionDuplicateCompleteGate::test_route_terminal_raises_on_paused_notification PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchResolutionSubtaskGateEnforcement::test_dispatch_resolution_subtask_raises_on_dispatch_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestDispatchResolutionSubtaskGateEnforcement::test_dispatch_resolution_subtask_raises_on_paused_operator PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestStartRuntimeExecutionGateEnforcement::test_start_runtime_raises_on_dispatch_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestAdmitStartAndPersistRunningGateEnforcement::test_admit_start_raises_on_dispatch_blocked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_all_dispatch_entry_paths_checked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_all_complete_entry_paths_checked PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_gate_check_returns_tuple PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_dispatch_blocked_error_importable PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_duplicate_complete_blocked_error_importable PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_paused_operator_notification_blocks_after_recovery PASSED [ 22%]
tests/orchestration/unit/test_dispatch_recovery_gate_enforcement.py::TestNoBypassPaths::test_recovery_gate_reconstruction_blocks_dispatch PASSED [ 23%]
tests/orchestration/unit/test_drive_auto_resolution.py::test_stale_runtime_barrier_auto_unblocks_without_resolver PASSED [ 23%]
tests/orchestration/unit/test_drive_auto_resolution.py::test_stale_runtime_barrier_does_not_auto_unblock_paused_drive PASSED [ 23%]
tests/orchestration/unit/test_drive_auto_resolution.py::test_stale_runtime_barrier_does_not_auto_unblock_retry_limit PASSED [ 23%]
tests/orchestration/unit/test_drive_auto_resolution.py::test_agent_assisted_recovery_opens_case_and_invokes_resolver PASSED [ 23%]
tests/orchestration/unit/test_drive_auto_resolution.py::test_agent_assisted_recovery_preserves_operator_pause PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_fields PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_frozen PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_minimal_construction PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_full_construction PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_status_literal PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeaseContract::test_drive_lease_released_reason_literal PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveConfigFrozenContract::test_drive_config_frozen_fields PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveConfigFrozenContract::test_drive_config_frozen_frozen PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveConfigFrozenContract::test_drive_config_frozen_defaults PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveConfigFrozenContract::test_drive_config_frozen_from_orch_config PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_save_and_load_lease PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_lease_update_latest_wins PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_active_leases_for_drive PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_active_lease_for_step PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_lease_by_run_id_not_found PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestDriveLeasePersistence::test_empty_store_active_leases PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_lease_marks_released PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_already_released_returns_none PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_nonexistent_returns_none PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_leases_for_step PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_leases_for_nonexistent_step PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseInvalidation::test_invalidate_leases_preserves_completed_lease PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestSamePlanDriveAdmission::test_new_drive_blocked_while_active_exists PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestSamePlanDriveAdmission::test_new_drive_allowed_when_terminal_exists PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestSamePlanDriveAdmission::test_paused_drive_blocks_admission PASSED [ 23%]
tests/orchestration/unit/test_drive_config_frozen.py::TestSamePlanDriveAdmission::test_different_plan_allows_admission PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_save_and_load_frozen_config PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_frozen_config_is_immutable PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_frozen_config_survives_config_drift PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_load_missing_frozen_config_returns_none PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_frozen_config_separate_per_drive PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestFrozenDriveConfigPersistence::test_freeze_from_orch_config_defaults PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseDriveAdmissionIntegration::test_lease_ownership_is_drive_step_run PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseDriveAdmissionIntegration::test_active_drive_blocks_new_drive_with_leases PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseDriveAdmissionIntegration::test_terminal_drive_allows_new_drive_with_leases_cleared PASSED [ 24%]
tests/orchestration/unit/test_drive_config_frozen.py::TestLeaseDriveAdmissionIntegration::test_planner_mutation_invalidates_unstarted_lease PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveStatus::test_drive_status_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunKind::test_child_run_kind_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunStatus::test_child_run_status_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestBarrierReason::test_barrier_reason_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationAction::test_planner_mutation_action_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerBundleStatus::test_planner_bundle_status_literal_values PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerRequest::test_planner_request_is_dataclass PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerRequest::test_planner_request_fields_match_rfc PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerRequest::test_planner_request_frozen PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerRequest::test_planner_request_required_field PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerRequest::test_planner_request_full_construction PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationItem::test_is_dataclass PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationItem::test_fields_match_rfc PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationItem::test_frozen PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationItem::test_minimal_construction PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationItem::test_full_construction PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationBundle::test_is_dataclass PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationBundle::test_fields_match_rfc PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationBundle::test_frozen PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationBundle::test_required_fields_only PASSED [ 24%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerMutationBundle::test_full_construction_rfc_example PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_is_dataclass PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_fields_match_rfc PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_frozen PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_required_field PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_full_construction_rfc_example PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveBarrier::test_barrier_reason_type PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_is_dataclass PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_fields_match_rfc PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_frozen PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_required_fields PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_full_construction_rfc_example PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_kind_literal_type PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_status_literal_type PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_kind_discriminator_step_has_step_id PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_kind_discriminator_resolver_has_case_id PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestChildRunRef::test_kind_discriminator_planner_has_planner_request_id PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_is_dataclass PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_fields_match_rfc PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_frozen PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_required_fields PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_full_construction_rfc_example PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_status_literal_type PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_barrier_embedding PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_barrier_embed_not_top_level_siblings PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_barrier_construction_in_drive_record PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_terminal_statuses PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestDriveRecord::test_operator_pause_state_literal PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_dispatch_batch_decision PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_resolve_decision_with_case_ids PASSED [ 25%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_replan_decision_with_planner_request PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_wait_decision PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_done_decision PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_halt_decision PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestControlDecisionExpanded::test_dispatch_backward_compatible PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestResolutionReportPlannerRequestExtension::test_planner_request_defaults_to_none PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestResolutionReportPlannerRequestExtension::test_planner_request_attached PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestResolutionReportPlannerRequestExtension::test_planner_request_with_operator_required PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerAdapterProtocol::test_planner_adapter_is_protocol PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerAdapterProtocol::test_planner_adapter_has_invoke_method PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerAdapterProtocol::test_planner_adapter_has_apply_method PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerAdapterProtocol::test_planner_adapter_invoke_return_type PASSED [ 26%]
tests/orchestration/unit/test_drive_contracts.py::TestPlannerAdapterProtocol::test_planner_adapter_invoke_parameter_type PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDrivePauseConsumption::test_pause_consumed_sets_operator_pause_state PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDrivePauseConsumption::test_pause_consumed_acknowledged PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDrivePauseConsumption::test_pause_then_control_halt_decision PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveUnpauseConsumption::test_unpause_consumed_restores_active_state PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveUnpauseConsumption::test_unpause_acknowledged PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveUnpauseConsumption::test_stop_prioritized_over_pause PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveUnpauseConsumption::test_stop_on_paused_drive PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestNoControlChannel::test_no_channel_no_consume PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestNoControlChannel::test_consume_drive_control_returns_record_unchanged PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestUnknownMessageTypeConsumption::test_drive_scoped_messages_only PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestEndToEndSeamClosure::test_full_pause_unpause_cycle PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestEndToEndSeamClosure::test_pause_with_barrier_state PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestEndToEndSeamClosure::test_unpause_while_in_barrier PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveControlConsumptionNoPending::test_no_pending_returns_unchanged PASSED [ 26%]
tests/orchestration/unit/test_drive_control_consumption.py::TestDriveStopConsumptionTerminal::test_stop_is_terminal PASSED [ 26%]
tests/orchestration/unit/test_drive_foreground_progress.py::test_foreground_drive_does_not_replay_historical_child_dispatches PASSED [ 26%]
tests/orchestration/unit/test_drive_foreground_progress.py::test_foreground_drive_continues_after_unpause_control_consumption PASSED [ 26%]
tests/orchestration/unit/test_drive_foreground_progress.py::test_foreground_drive_gives_blocked_barrier_one_resolver_pass PASSED [ 26%]
tests/orchestration/unit/test_drive_foreground_progress.py::test_foreground_drive_reports_stopped_as_terminal_even_with_barrier PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestDriveInspectQuery::test_minimal_query PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestDriveInspectQuery::test_query_with_child_run PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestDriveInspectQuery::test_query_pagination PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestDriveInspectView::test_view_defaults PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveStatus::test_returns_drive_state PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveStatus::test_with_child_run_selector PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveStatus::test_child_run_not_in_drive_raises PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestValidateChildRunInDrive::test_valid_child_run PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestValidateChildRunInDrive::test_invalid_child_run_raises PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestValidateChildRunInDrive::test_child_run_from_different_drive_raises PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestValidateChildRunInDrive::test_scope_boundary_never_escapes_drive PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveEvents::test_filters_events_by_drive_id PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveEvents::test_filters_by_child_run_id PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveLogs::test_filters_logs_by_drive_id PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveLogs::test_filters_by_child_run_id PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestQueryDriveActions::test_filters_by_child_run_id PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_pause_persists_action PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_unpause_persists_action PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_stop_persists_action PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_stop_force_appends_marker PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_stop_without_force_no_marker PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_reason_included_in_payload PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestSendDriveControl::test_stop_force_preserves_artifact_notes PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestChildRunScopeIsolation::test_query_status_with_wrong_child_raises PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestChildRunScopeIsolation::test_query_artifacts_with_wrong_child_raises PASSED [ 27%]
tests/orchestration/unit/test_drive_queries.py::TestChildRunScopeIsolation::test_query_actions_is_scoped PASSED [ 27%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_operator_pause_maps_to_blocked_operator PASSED [ 27%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_planner_needed_maps_to_replanning PASSED [ 27%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_runtime_failure_maps_to_resolving PASSED [ 27%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_merge_conflict_maps_to_resolving PASSED [ 27%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_review_failed_maps_to_resolving PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestBarrierStatusMapping::test_recovery_gate_maps_to_resolving PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestDriveLaunchFailureBarrier::test_launch_failure_records_runtime_case_and_releases_claim PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestDriveLaunchFailureBarrier::test_launch_failure_retry_limit_blocks_operator PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeBarrierRestoration::test_resume_with_barrier_preserves_barrier PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeBarrierRestoration::test_resume_with_operator_pause_preserves_pause PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeBarrierRestoration::test_resume_with_planner_needed_preserves_replanning PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeBarrierRestoration::test_resume_without_barrier_goes_running PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeBarrierRestoration::test_resume_preserves_operator_pause_state PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeFrontierRecompute::test_resume_recomputes_frontier_from_core PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverStaleRunningState::test_recover_classifies_stale_run_as_failed PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverStaleRunningState::test_recover_with_liveness_stale_marks_failed PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverStaleRunningState::test_recover_blocks_operator_after_repeated_stale_child_failures PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverTerminalArtifact::test_recover_terminal_artifact_beats_stale_status PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverTerminalArtifact::test_recover_conflict_terminal_artifact_in_registry PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverFrontierRecompute::test_recover_recomputes_frontier_from_core PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverFrontierRecompute::test_recover_releases_orphaned_drive_claim PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverFrontierRecompute::test_recover_releases_historical_orphaned_drive_claim PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverFrontierRecompute::test_recover_releases_claim_persisted_before_child_ref PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverOpenCaseBarrierReentry::test_recover_creates_barrier_for_open_cases PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverOpenCaseBarrierReentry::test_recover_preserves_existing_barrier PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverDryRun::test_dry_run_does_not_modify_drive PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverTransitionValidation::test_recover_validates_transition_to_running PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverTransitionValidation::test_recover_transition_through_recovering PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverOperatorPauseStatePreservation::test_recover_preserves_paused_operator_state PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverOperatorPauseStatePreservation::test_recover_preserves_active_operator_state PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestResumeTerminalDrive::test_resume_terminal_drive_returns_current_state PASSED [ 28%]
tests/orchestration/unit/test_drive_recovery_semantics.py::TestRecoverTerminalDrive::test_recover_terminal_drive_returns_nothing PASSED [ 28%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_creates_persisted_drive PASSED [ 28%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_persists_to_store PASSED [ 28%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_rejects_duplicate_active_drive PASSED [ 28%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_allows_after_terminal PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_max_parallelism_validation PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestStartDriveAppSurface::test_start_drive_snapshots_frontier PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_returns_result PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_dispatch_batch PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_done PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_terminal_drive_returns_immediately PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_persists_status PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRunDriveLoopAppSurface::test_run_drive_loop_nonexistent_drive_raises PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestResumeDriveAppSurface::test_resume_drive_returns_result PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestResumeDriveAppSurface::test_resume_drive_restores_child_runs PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestResumeDriveAppSurface::test_resume_drive_terminal_drive PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestResumeDriveAppSurface::test_resume_drive_nonexistent_drive_raises PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRecoverDriveAppSurface::test_recover_drive_returns_result PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRecoverDriveAppSurface::test_recover_drive_dry_run PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRecoverDriveAppSurface::test_recover_drive_with_barrier PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRecoverDriveAppSurface::test_recover_drive_terminal_drive PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestRecoverDriveAppSurface::test_recover_drive_nonexistent_drive_raises PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestTransitionValidation::test_valid_transitions PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestTransitionValidation::test_invalid_transition_raises PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestTransitionValidation::test_invalid_transition_preserves_statuses PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestOrchestrationAppDriveSurface::test_start_drive_app_delegation PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestOrchestrationAppDriveSurface::test_drive_status_app_delegation PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestGenerateDriveId::test_drive_id_format PASSED [ 29%]
tests/orchestration/unit/test_driver_loop_proof.py::TestGenerateDriveId::test_drive_id_uniqueness PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_canonical_jsonl_framing_and_entry_hash_are_deterministic PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_jsonl_sink_appends_and_enforces_seq_prev_hash_hooks PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_event_seq_hook_violation_is_rejected PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_unknown_event_name_and_payload_drift_are_rejected PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_truncated_or_malformed_trailing_records_surface_corruption PASSED [ 29%]
tests/orchestration/unit/test_events.py::test_jsonl_sink_reloads_authoritative_tail_across_instances PASSED [ 30%]
tests/orchestration/unit/test_events.py::test_jsonl_sink_parallel_writers_preserve_append_only_chain PASSED [ 30%]
tests/orchestration/unit/test_events.py::test_register_sink_requires_explicit_registry_instance PASSED [ 30%]
tests/orchestration/unit/test_evidence.py::test_summarize_plan_evidence_omits_opencode_event_stream PASSED [ 30%]
tests/orchestration/unit/test_evidence.py::test_assess_freeform_evidence_blocks_explicit_failure_status PASSED [ 30%]
tests/orchestration/unit/test_evidence.py::test_assess_freeform_evidence_allows_empty_error_field PASSED [ 30%]
tests/orchestration/unit/test_evidence.py::test_assess_freeform_evidence_allows_quoted_empty_error_field PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestInspectDriveStatusScopeKind::test_inspect_drive_status_includes_scope_kind PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestInspectDriveStatusScopeKind::test_inspect_drive_status_json_includes_scope_kind PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestDriveStatusResultScopeKind::test_drive_status_result_includes_scope_kind PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestDriveScopeControlResultEnrichment::test_enrich_drive_scope_result_adds_scope_kind PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestDriveScopeControlResultEnrichment::test_enrich_preserves_original_fields PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestResolvesToDriveScope::test_status_latest_resolves_to_drive_when_active PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestResolvesToDriveScope::test_control_drive_stop_returns_control_result PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestResolvesToDriveScope::test_control_drive_pause_returns_control_result PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestResolvesToDriveScope::test_control_drive_unpause_returns_control_result PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope::test_inspect_drive_events_with_default_scope PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope::test_inspect_drive_logs_with_default_scope PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope::test_inspect_drive_artifacts_with_default_scope PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope::test_inspect_drive_actions_with_default_scope PASSED [ 30%]
tests/orchestration/unit/test_flat_surface_drive_scope.py::TestFlatSurfaceLatestAllInspectionSurfacesResolveToDriveScope::test_case_list_uses_drive_scope PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestPackageImports::test_import_all_contracts_from_package_root PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestPackageImports::test_import_all_protocols_from_package_root PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestPackageImports::test_orchestration_all_exports_match_spec PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestDirectModuleImports::test_import_contracts_from_contracts_module PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestDirectModuleImports::test_import_core_adapter_from_module PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestDirectModuleImports::test_import_protocols_from_interfaces_module PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestSignatureGaps::test_protocol_runtime_checkability PASSED [ 30%]
tests/orchestration/unit/test_imports.py::TestSignatureGaps::test_contract_field_type_annotations PASSED [ 30%]
tests/orchestration/unit/test_inspection_queries.py::test_runs_query_reads_through_explicit_inspection_boundary PASSED [ 30%]
tests/orchestration/unit/test_interfaces.py::TestControlProtocol::test_control_is_protocol PASSED [ 30%]
tests/orchestration/unit/test_interfaces.py::TestControlProtocol::test_control_has_evaluate_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestControlProtocol::test_control_has_apply_resolution_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestControlProtocol::test_control_evaluate_return_type PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_is_protocol PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_has_snapshot_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_has_claim_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_has_release_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_has_register_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRosterProtocol::test_roster_claim_return_type PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_is_protocol PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_has_snapshot_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_has_prepare_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_has_start_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_has_collect_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_has_cleanup_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestRuntimeProtocol::test_runtime_start_return_type PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestResolverProtocol::test_resolver_is_protocol PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestResolverProtocol::test_resolver_has_resolve_method PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestResolverProtocol::test_resolver_resolve_parameter_type PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestResolverProtocol::test_resolver_resolve_return_type PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestProtocolGaps::test_all_protocol_parameters_have_type_annotations PASSED [ 31%]
tests/orchestration/unit/test_interfaces.py::TestProtocolGaps::test_protocol_methods_have_spec_aligned_docstrings PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_opencode_runner_is_registered_in_default_registry PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_opencode_runner_is_not_subprocess_runner PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_other_runners_remain_subprocess PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_get_opencode_runner_returns_opencode_runner PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_get_opencode_runner_with_custom_artifact_root PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerRegistration::test_get_opencode_runner_with_custom_config PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCapabilities::test_capabilities_supports_resume PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCapabilities::test_capabilities_supports_cancel PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCapabilities::test_capabilities_does_not_support_streaming PASSED [ 31%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCapabilities::test_capabilities_runner_id_is_opencode PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCapabilities::test_default_runner_id_is_opencode PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchArgv::test_start_mode_argv_matches_rfc_contract PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchArgv::test_resume_mode_argv_includes_session PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchArgv::test_continue_flag_never_appears PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchArgv::test_launch_argv_delegates_to_frozen_contract PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchArgv::test_launch_argv_with_session_delegates_correctly PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchEnv::test_launch_env_includes_all_handoff_vars PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchEnv::test_launch_env_step_id_used_as_run_id PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchEnv::test_launch_env_uses_run_id_from_prompt_bundle_path PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchEnv::test_launch_env_work_refs_run_id_overrides_prompt_path PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchEnv::test_launch_env_agent_id_correct PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_spawns_subprocess_with_correct_argv PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_uses_correct_working_directory PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_sets_stdin_devnull PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_captures_stdout_stderr PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_raises_on_oserror PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunch::test_launch_env_includes_handoff_vars PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_spawns_subprocess_with_session PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_does_not_use_continue PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_uses_resume_bootstrap_message PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_raises_on_missing_session_id PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_raises_on_empty_session_id PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerResume::test_resume_raises_on_oserror PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_returns_running_while_process_active PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_returns_success_on_exit_0 PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_summarizes_opencode_json_event_text PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_prefers_final_opencode_text_event PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_extracts_report_from_long_opencode_event_stream PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_extracts_structured_review_payload_from_event_stream PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_falls_back_to_opencode_db_when_stdout_lacks_final_text PASSED [ 32%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_prefers_db_machine_payload_over_stdout_prose PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_does_not_truncate_machine_payloads PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_omits_raw_opencode_json_when_no_final_text PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_ignores_status_summary_protocol_envelope PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_returns_fail_on_nonzero_exit PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_returns_stall_on_signal PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_preserves_stdout_evidence_refs PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_preserves_stderr_evidence_refs PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_raises_on_unknown_handle PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_preserves_session_id PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerPoll::test_poll_cleans_up_handle_after_completion PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCancel::test_cancel_sends_terminate PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCancel::test_cancel_is_noop_on_unknown_handle PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCancel::test_cancel_preserves_session_metadata PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerCancel::test_cancel_raises_on_terminate_error PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeContinueForbidden::test_continue_forbidden_error_is_runner_error PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeContinueForbidden::test_continue_forbidden_error_default_detail PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeContinueForbidden::test_continue_forbidden_error_custom_detail PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeContinueForbidden::test_continue_forbidden_error_reason PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerDefaultAgentFallback::test_launch_uses_fallback_agent_when_empty PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerDefaultAgentFallback::test_build_argv_uses_fallback_agent_when_empty PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchIntegration::test_materialized_prompt_paths_appear_in_launch_env PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner.py::TestOpenCodeRunnerLaunchIntegration::test_launch_argv_references_workspace_prompt PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerRegistryUnknownRunner::test_registry_get_raises_for_unknown_runner PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerRegistryUnknownRunner::test_registry_get_lists_available_runners_in_error PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerRegistryUnknownRunner::test_registry_get_returns_registered_runner PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerRegistryUnknownRunner::test_registry_register_overwrites PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerNotFoundErrorInit::test_runner_not_found_error_with_message PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerNotFoundErrorInit::test_runner_not_found_error_with_detail PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerNotFoundErrorInit::test_runner_not_found_error_defaults_runner_id PASSED [ 33%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerNotFoundErrorInit::test_runner_not_found_error_is_runner_error PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_error_base_class PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_not_found_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_launch_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_resume_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_poll_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_runner_cancel_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_opencode_runner_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_opencode_continue_forbidden_error_hierarchy PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_all_runner_errors_have_runner_id PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerErrorHierarchy::test_all_runner_errors_have_reason PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchErrorReasons::test_launch_error_accepts_documented_reasons[workspace_invalid] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchErrorReasons::test_launch_error_accepts_documented_reasons[request_invalid] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchErrorReasons::test_launch_error_accepts_documented_reasons[resource_unavailable] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchErrorReasons::test_launch_error_accepts_documented_reasons[internal] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchErrorReasons::test_launch_error_includes_detail_in_message PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerResumeErrorReasons::test_resume_error_accepts_documented_reasons[resume_unsupported] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerResumeErrorReasons::test_resume_error_accepts_documented_reasons[session_missing] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerResumeErrorReasons::test_resume_error_accepts_documented_reasons[session_invalid] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerResumeErrorReasons::test_resume_error_accepts_documented_reasons[internal] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerResumeErrorReasons::test_resume_error_detail_in_message PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollErrorReasons::test_poll_error_accepts_documented_reasons[handle_unknown] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollErrorReasons::test_poll_error_accepts_documented_reasons[transport_failed] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollErrorReasons::test_poll_error_accepts_documented_reasons[internal] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCancelErrorReasons::test_cancel_error_accepts_documented_reasons[cancel_unsupported] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCancelErrorReasons::test_cancel_error_accepts_documented_reasons[already_complete] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCancelErrorReasons::test_cancel_error_accepts_documented_reasons[transport_failed] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCancelErrorReasons::test_cancel_error_accepts_documented_reasons[internal] PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerPollOSError::test_poll_raises_transport_error_on_stdout_read_oserror PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_default_fields PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_is_frozen PASSED [ 34%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_fields_match_spec PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_request_mode_types PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_session_policy_types PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestExecutionRequestContract::test_execution_request_with_all_fields_explicit PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_is_frozen PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_fields_match_spec PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_native_session_resume PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_fresh_relaunch PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_default_values PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryContinuityContract::test_recovery_continuity_paths_are_distinct PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_is_frozen PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_fields_match_spec PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_resume_kind PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_recover_kind PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_default_values PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_records_failure_reason PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRecoveryAttemptContract::test_recovery_attempt_records_fallback_relaunch PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerHandleContract::test_runner_handle_is_frozen PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerHandleContract::test_runner_handle_fields PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerHandleContract::test_runner_handle_without_session PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerHandleContract::test_runner_handle_with_session PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchResultContract::test_runner_launch_result_is_frozen PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerLaunchResultContract::test_runner_launch_result_fields PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_is_frozen PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_fields PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_accepts_all_status_values[running] PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_accepts_all_status_values[success] PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_accepts_all_status_values[fail] PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_accepts_all_status_values[stall] PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_accepts_all_status_values[transport_error] PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerPollResultContract::test_runner_poll_result_default_values PASSED [ 35%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCapabilitiesContract::test_runner_capabilities_is_frozen PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestRunnerCapabilitiesContract::test_runner_capabilities_fields PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerErrorDetail::test_opencode_runner_error_with_detail PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerErrorDetail::test_opencode_runner_error_without_detail PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerBuildLaunchEnv::test_build_launch_env_defaults_opencode_agent_id PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerBuildLaunchEnv::test_build_launch_env_uses_provided_agent_id PASSED [ 36%]
tests/orchestration/unit/test_opencode_runner_unit_coverage.py::TestOpenCodeRunnerBuildLaunchEnv::test_build_launch_env_uses_step_id_as_run_id PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorNotificationPersistenceStructural::test_operator_notification_record_round_trip PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorNotificationPersistenceStructural::test_receipt_state_transitions_preserved PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorNotificationPersistenceStructural::test_paused_routing_state_values_round_trip PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestReconcileConflictSurvivalStructural::test_merge_conflict_reconcile_state_round_trip PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestReconcileConflictSurvivalStructural::test_aborted_reconcile_preserves_evidence PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestReconcileConflictSurvivalStructural::test_protected_path_policies_round_trip PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestDispatchGatePersistenceStructural::test_dispatch_recovery_gate_round_trip PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestDispatchGatePersistenceStructural::test_dispatch_gate_clears_on_reconcile_closure PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestRecoverySemanticsStructural::test_gate_open_allowed_prevents_operator_required PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestRecoverySemanticsStructural::test_recovery_case_status_maps_operator_required_to_open PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestRecoverySemanticsStructural::test_recovery_action_status_maps_operator_required_to_pending PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestCombinedPersistenceStructural::test_run_record_persists_notification_gate_and_runtime_together PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestCombinedPersistenceStructural::test_continuity_ledger_entry_carries_notifications_and_gate PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestCombinedPersistenceStructural::test_startup_recovery_input_output_carries_all_state PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorPauseBlocksDispatchBehavior::test_orch_app_operator_required_creates_notification_with_paused_state PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorPauseBlocksDispatchBehavior::test_orch_app_halted_resolver_clears_notification PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestRecoveryReconstructsPausedStateBehavior::test_recovery_report_carries_operator_notifications PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestRecoveryReconstructsPausedStateBehavior::test_recovery_report_carries_dispatch_recovery_gate PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestProtectedPathExplicitSurfaceBehavior::test_recovery_report_surfaces_protected_path_policy PASSED [ 36%]
tests/orchestration/unit/test_operator_pause_conflict_recovery_red.py::TestOperatorNotificationMatchesOrchAppSurface::test_orch_app_notification_fields_cover_spec PASSED [ 36%]
tests/orchestration/unit/test_orch_app.py::test_foreground_supervisor_continues_resolver_and_planner_barriers PASSED [ 36%]
tests/orchestration/unit/test_orch_app.py::test_build_composes_real_collaborators_without_noop_resolver_dependency PASSED [ 36%]
tests/orchestration/unit/test_orch_app.py::test_terminal_drive_cleanup_sweeps_success_child_by_persisted_path PASSED [ 36%]
tests/orchestration/unit/test_orch_app.py::test_terminal_drive_cleanup_does_not_bypass_live_lifecycle_barrier PASSED [ 36%]
tests/orchestration/unit/test_orch_app.py::test_runtime_mediation_source_narrows_allowlist_to_live_case_tools PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_runtime_mediation_source_uses_case_artifact_directives PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_requires_gateway_live_path PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_returns_operator_required_when_gateway_denies PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_denies_runtime_mediated_tool_outside_config_allowlist PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_reaches_runtime_runner_through_gateway PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_blocks_planner_worktree_bypass PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_build_resolution_case_requires_resolve_decision PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_build_resolution_case_uses_explicit_non_closure_input PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_review_non_pass_normalizes_to_resolution_case PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_review_parse_failure_normalizes_to_resolution_case PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_surfaces_operator_required_via_case_views PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_emits_explicit_operator_notification_events PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_route_resolution_case_covers_all_resolver_outcomes[unblocked-dispatch-False] PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_route_resolution_case_covers_all_resolver_outcomes[waiting-wait-False] PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_route_resolution_case_covers_all_resolver_outcomes[operator_required-wait-True] PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_route_resolution_case_covers_all_resolver_outcomes[halt-halt-False] PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_invokes_configured_default_resolver_role PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_parses_opencode_stdout_wrapped_resolution_report PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_ignores_opencode_protocol_envelope PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_ignores_invalid_status_summary_envelope PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_resolve_case_supports_explicit_tacit_resolver_config_selection PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_route_resolution_case_refreshes_state_after_real_resolver_invocation PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_and_control_route_through_typed_boundaries PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_runs_summarizes_stored_opencode_event_streams PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_admission_failure_when_same_plan_already_has_active_run PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_persists_pending_before_runtime_start PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_claims_before_runtime_start PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_refuses_runtime_start_when_authoritative_claim_fails PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_run_consumes_authoritative_step_isolation_for_runtime_request PASSED [ 37%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_blocks_context_drift_before_runtime_launch PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_renders_prompt_bundle_before_runtime_launch PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_uses_profile_agent_id_for_runner_request PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_fails_closed_when_prompt_registry_lacks_role_support PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_completes_only_after_reconcile_allows_it PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_hard_gates_non_closing_reconcile_status PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_parse_structured_review_result_accepts_yaml_fence PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_parse_structured_review_result_accepts_nested_opencode_text PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_blocks_freeform_failure_evidence PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_refreshes_inventory_before_rejecting_stale_receipt PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_build_dispatch_spec_preserves_canonical_default_role_precedence PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_build_dispatch_spec_binds_live_roster_reuse_without_rewriting_role PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_build_dispatch_spec_rejects_roster_role_rewrite PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_start_runtime_execution_turns_roster_reuse_into_resume_request PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_non_pass_review_becomes_resolution_case PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_route_terminal_execution_parse_failure_becomes_resolution_case PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_dispatch_resolution_subtask_uses_shared_runtime_substrate PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_run_runtime_start_failure_terminalizes_after_durable_admission PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_run_events_emit_only_after_running_record_is_durable PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_run_event_persistence_failure_terminalizes_run PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_build_fails_when_authoritative_plan_path_missing PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_build_wires_runtime_workspace_root_from_orchestration_config PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_control_pause_reports_ambiguous_run_selection PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_control_reason_and_force_are_persisted_in_pending_actions PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_control_stop_uses_explicit_run_id_when_multiple_runs_exist PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_config_show_effective_returns_expanded_view PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_resume_safe_replays_from_durable_artifacts PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_recover_and_resume_from_durable_artifacts PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_recover_blocks_on_blocking_divergence PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_recover_blocks_on_corrupt_blocking PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_recover_blocks_on_ambiguous_blocking PASSED [ 38%]
tests/orchestration/unit/test_orch_app.py::test_recover_terminalizes_fresh_start_required_before_restart PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_recover_dry_run_does_not_mutate_durable_state PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_imported_legacy_runs_are_visible_via_orch_runs_surface PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_recover_surfaces_imported_legacy_continuity_blockers PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_cutover_validate_surfaces_four_retirement_criteria PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_migration_advance_state_updates_imported_run_record PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_resume_blocks_when_event_transcript_is_corrupt PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_decision_matrix_blocks_or_requires_fresh_start_for_unknown_or_stale_heartbeat[True] PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_decision_matrix_blocks_or_requires_fresh_start_for_unknown_or_stale_heartbeat[False] PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_prompt_materialization_uses_worktree_path_not_workspace_root PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_prompt_materialization_resume_path_uses_worktree PASSED [ 39%]
tests/orchestration/unit/test_orch_app.py::test_workspace_worktree_path_raises_for_unknown_workspace PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_status_is_applyable PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_carries_affected_steps PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_carries_evidence_refs PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_carries_constraints_as_safety_notes PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_summary_includes_reason PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_mutations_empty_when_request_has_no_mutations PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestConstructBundleFromRequest::test_bundle_preserves_request_mutations PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_applyable_clears_barrier_and_returns_running PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_applyable_includes_lease_invalidation_count PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_applyable_with_wait_decision PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_operator_required_transitions_to_blocked_operator PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_halt_transitions_to_halted PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_invalid_transition_preserves_current_status PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestApplyPlannerBundleToDrive::test_applyable_with_done_decision PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlanPlannerMutationApplier::test_non_applyable_bundle_records_all_as_failed PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlanPlannerMutationApplier::test_empty_applyable_bundle_succeeds PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationResult::test_default_values PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationResult::test_frozen PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationResult::test_with_results PASSED [ 39%]
tests/orchestration/unit/test_planner_loop_integration.py::TestAsStrList::test_none_returns_empty PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestAsStrList::test_list_of_strings PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestAsStrList::test_tuple_of_strings PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestAsStrList::test_single_value PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestAsStrList::test_list_of_ints_coerced PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopBarrierEntry::test_replan_decision_enters_planner_barrier PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopBarrierEntry::test_construct_bundle_preserves_planner_request PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestStaleLeaseInvalidation::test_invalidate_superseded_leases_calls_store_for_each_step PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestStaleLeaseInvalidation::test_invalidate_superseded_leases_returns_empty_for_empty_ids PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationApplierContract::test_applier_calls_core_add_step PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationApplierContract::test_applier_calls_core_remove_step PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationApplierContract::test_applier_records_failed_mutations PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerMutationApplierContract::test_applier_unsupported_action_fails PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopIntegrationEndToEnd::test_applyable_bundle_returns_to_running PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopIntegrationEndToEnd::test_operator_required_bundle_transitions_to_blocked PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopIntegrationEndToEnd::test_halt_bundle_transitions_to_halted PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestPlannerLoopIntegrationEndToEnd::test_planner_applier_uses_facade_not_direct_edit PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestUpdateDriveRecordPlannerTransitions::test_replanning_to_running_preserves_drive_id PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestUpdateDriveRecordPlannerTransitions::test_replanning_to_blocked_operator_preserves_fields PASSED [ 40%]
tests/orchestration/unit/test_planner_loop_integration.py::TestUpdateDriveRecordPlannerTransitions::test_replanning_to_halted_preserves_fields PASSED [ 40%]
tests/orchestration/unit/test_projections_impl.py::test_replay_derives_latest_summary_and_metrics PASSED [ 40%]
tests/orchestration/unit/test_projections_impl.py::test_replay_exposes_step_and_case_artifact_hooks PASSED [ 40%]
tests/orchestration/unit/test_projections_impl.py::test_projection_persistence_failure_surfaces_projection_stale PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_prompt_artifact_paths_is_frozen_dataclass PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_prompt_artifact_paths_fields_match_spec PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_authority_bundle_under_input_dir PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_authority_runner_prompt_under_input_dir PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_workspace_prompt_under_orch_dir PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_workspace_prompt_relative_is_frozen_constant PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactPathsContract::test_run_id_appears_in_authority_paths PASSED [ 40%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestRunnerHandoffEnvContract::test_runner_handoff_env_is_frozen_dataclass PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestRunnerHandoffEnvContract::test_runner_handoff_env_fields_match_spec PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestRunnerHandoffEnvContract::test_runner_handoff_env_as_dict_produces_required_keys PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestRunnerHandoffEnvContract::test_runner_handoff_env_as_dict_values_match_constructor PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestRunnerHandoffEnvContract::test_build_runner_handoff_env_uses_workspace_prompt_path PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_opencode_launch_config_is_frozen_dataclass PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_opencode_launch_config_fields_match_spec PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_file_flag_defaults_to_workspace_runner_prompt PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_format_flag_defaults_to_json PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_bootstrap_start_matches_rfc PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestOpenCodeLaunchConfigContract::test_bootstrap_resume_matches_rfc PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPathResolutionContract::test_authority_bundle_path_structure PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPathResolutionContract::test_authority_runner_prompt_path_structure PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPathResolutionContract::test_workspace_prompt_path_structure PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPathResolutionContract::test_different_run_ids_produce_different_authority_paths PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPathResolutionContract::test_different_workspaces_produce_different_workspace_paths PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_start_mode_argv_matches_rfc PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_resume_mode_argv_includes_session PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_file_flag_appears_after_agent_flag PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_continue_flag_is_not_used PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_start_uses_start_bootstrap_message PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_resume_uses_resume_bootstrap_message PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchArgvContract::test_launch_argv_is_a_tuple_of_strings PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchEnvContract::test_launch_env_includes_all_handoff_vars PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchEnvContract::test_launch_env_merges_with_parent PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestLaunchEnvContract::test_launch_env_overrides_parent_conflicts PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_materialize_creates_prompt_bundle_json PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_materialize_creates_runner_prompt_md PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_resolution_report_output_contract_renders_json_prompt PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_structured_review_result_output_contract_renders_json_prompt PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_materialize_creates_workspace_copy PASSED [ 41%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptMaterializationContract::test_workspace_copy_matches_authority_copy PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptBundleSha256Contract::test_same_bundle_produces_same_sha256 PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptBundleSha256Contract::test_different_bundles_produce_different_sha256 PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptBundleSha256Contract::test_sha256_is_64_hex_chars PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestFrozenConstantsContract::test_runs_input_dir_is_input PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestFrozenConstantsContract::test_prompt_bundle_filename PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestFrozenConstantsContract::test_runner_prompt_filename PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestFrozenConstantsContract::test_workspace_orch_dir PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestFrozenConstantsContract::test_workspace_prompt_relative PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestEndToEndHandoffContract::test_full_handoff_pipeline_produces_consistent_artifacts_and_env PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestEndToEndHandoffContract::test_handoff_contract_fields_cover_all_rfc_requirements PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_valid_artifacts_pass_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_missing_bundle_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_invalid_json_bundle_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_missing_required_field_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_empty_field_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_missing_runner_prompt_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_empty_runner_prompt_fails_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_materialized_artifacts_pass_validation PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_validation_result_is_frozen_dataclass PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestPromptArtifactValidation::test_validation_result_fields_match_spec PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestMaterializationRecoveryIntegration::test_materialize_then_validate_then_env PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestMaterializationRecoveryIntegration::test_workspace_copy_exists_for_runner_startup PASSED [ 42%]
tests/orchestration/unit/test_prompt_artifact_contract.py::TestMaterializationRecoveryIntegration::test_authority_bundle_contains_required_recovery_fields PASSED [ 42%]
tests/orchestration/unit/test_recovery.py::test_cutover_validator_passes_when_no_imported_runs_exist PASSED [ 42%]
tests/orchestration/unit/test_recovery.py::test_cutover_validator_blocks_non_retired_imports PASSED [ 42%]
tests/orchestration/unit/test_recovery.py::test_cutover_validator_blocks_open_recovery_cases PASSED [ 42%]
tests/orchestration/unit/test_recovery_contract_conformance.py::test_recovery_report_semantics_are_shared_across_consumer_surfaces PASSED [ 42%]
tests/orchestration/unit/test_recovery_contract_conformance.py::test_recovery_quarantine_preserves_artifacts_when_gate_open_blocked PASSED [ 42%]
tests/orchestration/unit/test_resolver.py::test_validate_resolution_report_payload_accepts_machine_readable_shape PASSED [ 42%]
tests/orchestration/unit/test_resolver.py::test_parse_resolution_report_payload_returns_bounded_report PASSED [ 42%]
tests/orchestration/unit/test_resolver.py::test_parse_resolution_report_payload_preserves_planner_mutations PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_parse_resolution_report_payload_rejects_unknown_fields PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_bound_resolver_returns_bounded_report_for_unblocked_case PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_bound_resolver_returns_bounded_report_for_waiting_case PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_bound_resolver_returns_operator_required_instead_of_false_certainty PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_bound_resolver_forwards_configured_default_role_id PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_should_preserve_case_reason_only_for_blocked_or_unresolved PASSED [ 43%]
tests/orchestration/unit/test_resolver.py::test_map_payload_to_report_does_not_force_reason_for_non_blocked_cases PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_authorize_and_invoke_requires_explicit_gateway PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_allowed_call_passes_and_records_audit_event PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_disallowed_family_is_denied_with_machine_readable_reason PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_unknown_tool_name_is_denied PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_write_surface_request_for_read_only_tool_is_denied PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_claim_tool_is_denied_even_when_core_family_is_allowlisted PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_gateway_requires_main_worktree_execution_site PASSED [ 43%]
tests/orchestration/unit/test_resolver_gateway.py::test_allowlist_runtime_enforcement_varies_with_snapshot PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierEntryCreatesResolutionCase::test_resolve_decision_enters_barrier_and_creates_case PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierEntryCreatesResolutionCase::test_barrier_captures_case_ids_from_control_decision PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierEntryCreatesResolutionCase::test_barrier_entry_preserves_drive_id_and_plan_path PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierEntryCreatesResolutionCase::test_existing_runtime_failure_barrier_invokes_resolver PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverInvocationPayloads::test_resolver_receives_current_state_snapshots PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverInvocationPayloads::test_resolver_receives_drive_context_in_case PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestPostResolutionStateRefresh::test_stale_pre_resolution_state_is_not_reused PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestPostResolutionStateRefresh::test_unblocked_resolution_clears_barrier PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_unblocked_without_planner_returns_to_running PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_unblocked_with_planner_request_transitions_to_replanning PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_unblocked_with_planner_request_auto_applies_when_planner_wired PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_waiting_stays_in_resolving PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_operator_required_transitions_to_blocked_operator PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestResolverContinuationSemantics::test_halt_transitions_to_halted PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestStaleStateRegression::test_control_uses_refreshed_snapshots_after_unblocked PASSED [ 43%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestStaleStateRegression::test_barrier_entry_with_no_resolver_records_case_but_no_resolver_run PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierReasonMapping::test_runtime_failure_maps PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierReasonMapping::test_merge_conflict_maps PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierReasonMapping::test_review_failed_maps PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierReasonMapping::test_continuity_block_maps_to_recovery_gate PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestBarrierReasonMapping::test_unknown_maps_to_runtime_failure PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestInferCaseSource::test_merge_conflict_in_reason PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestInferCaseSource::test_review_in_reason PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestInferCaseSource::test_runtime_in_reason PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestInferCaseSource::test_unknown_defaults PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestApplyResolutionReportToDrive::test_unblocked_clears_barrier PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestApplyResolutionReportToDrive::test_waiting_preserves_barrier PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestApplyResolutionReportToDrive::test_operator_required_transitions_to_blocked_operator PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestApplyResolutionReportToDrive::test_halt_transitions_to_halted PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestApplyResolutionReportToDrive::test_unblocked_with_planner_request_transitions_to_replanning PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestUpdateDriveRecord::test_updates_status_and_barrier PASSED [ 44%]
tests/orchestration/unit/test_resolver_loop_integration.py::TestUpdateDriveRecord::test_clears_barrier_with_none PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierReason::test_review_outcome_produces_correct_barrier[pass-None-running] PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierReason::test_review_outcome_produces_correct_barrier[needs_fix-review_failed-resolving] PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierReason::test_review_outcome_produces_correct_barrier[needs_replan-review_failed-replanning] PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierReason::test_review_outcome_produces_correct_barrier[operator_required-None-blocked_operator] PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierTransition::test_pass_does_not_enter_barrier PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierTransition::test_needs_fix_enters_review_failed_barrier PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierTransition::test_needs_replan_enters_planner_barrier PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierTransition::test_operator_required_transitions_to_blocked PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewGateConstructor::test_pass_result_construction PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewGateConstructor::test_needs_fix_result_construction PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewGateConstructor::test_needs_replan_result_construction PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewGateConstructor::test_operator_required_result_construction PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestDefaultReviewGate::test_json_review_output_passes PASSED [ 44%]
tests/orchestration/unit/test_review_gate_integration.py::TestDefaultReviewGate::test_malformed_output_degrades_to_needs_fix PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestDefaultReviewGate::test_fenced_yaml_review_output_passes PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestDefaultReviewGate::test_needs_replan_adds_planner_request PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestDefaultReviewGate::test_needs_replan_preserves_planner_mutations PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierRecovery::test_needs_fix_barrier_can_resolve_to_running PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierRecovery::test_needs_replan_barrier_can_resolve_to_replanning PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierRecovery::test_planner_barrier_can_clear_to_running PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeBarrierRecovery::test_blocked_operator_requires_unblock PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_default_review_gate_reviews_terminal_child_before_control PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_terminal_needs_replan_applies_planner_mutation PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_control_resolve_from_review_triggers_barrier PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_control_replan_from_review_triggers_barrier PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_control_halt_from_operator_required PASSED [ 45%]
tests/orchestration/unit/test_review_gate_integration.py::TestReviewOutcomeWithDriverIntegration::test_control_dispatch_after_pass PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow1_BuiltinInRoleProfiles::test_builtin_shadowing_via_merge_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow1_BuiltinInRoleProfiles::test_builtin_shadowing_via_dict_to_config_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow1_BuiltinInRoleProfiles::test_builtin_shadowing_via_config_file_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow1_BuiltinInRoleProfiles::test_error_message_includes_migration_directive PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow1_BuiltinInRoleProfiles::test_error_field_points_to_role_profiles PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow2_UnknownBuiltInOverride::test_unknown_builtin_via_merge_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow2_UnknownBuiltInOverride::test_unknown_builtin_via_dict_to_config_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow2_UnknownBuiltInOverride::test_unknown_builtin_via_config_file_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow2_UnknownBuiltInOverride::test_error_field_includes_role_id PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow2_UnknownBuiltInOverride::test_error_reason_mentions_unknown_builtin PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow3_OverrideTargetsCustomRole::test_override_targeting_custom_role_via_merge_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow3_OverrideTargetsCustomRole::test_override_targeting_custom_role_via_config_file_raises PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow3_OverrideTargetsCustomRole::test_error_field_points_to_override PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow3_OverrideTargetsCustomRole::test_error_reason_mentions_custom_role PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_override_default_runner_via_merge PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_override_agent_id_via_merge PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_override_via_dict_to_config PASSED [ 45%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_override_via_config_file PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_multiple_overrides_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_non_overridden_builtins_unchanged PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow4_ValidBuiltinOverride::test_overridden_role_id_preserved PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow5_ValidCustomRole::test_custom_role_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow5_ValidCustomRole::test_custom_role_via_dict_to_config PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow5_ValidCustomRole::test_custom_role_via_config_file PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow5_ValidCustomRole::test_custom_role_appears_after_builtins PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow5_ValidCustomRole::test_override_plus_custom_both_present PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_execution_context_family_violation_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_execution_context_family_violation_via_config_file PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_mutation_policy_family_violation_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_session_policy_family_violation_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_output_contract_family_violation_via_merge PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_multiple_family_violations_collected PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow6_FamilyPolicyViolationViaOverride::test_error_field_includes_override_path PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_effective_output_contains_provenance_lines PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_provenance_lines_follow_rfc_format PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_default_source_in_human_output PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_override_source_in_human_output PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_custom_source_in_human_output PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow7_HumanConfigShowEffective::test_summary_mode_has_no_provenance PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_provenance_object_exists PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_provenance_has_value_and_source_keys PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_provenance_source_categories PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_default_source PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_override_source PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_custom_source PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_json_provenance_is_json_serializable PASSED [ 46%]
tests/orchestration/unit/test_rfc_role_profile_overrides_matrix.py::TestRfcMatrixRow8_JsonConfigShowEffective::test_summary_mode_provenance_is_none PASSED [ 46%]
tests/orchestration/unit/test_role_profile_overrides.py::TestBuiltinRoleIDs::test_contains_all_default_role_ids PASSED [ 46%]
tests/orchestration/unit/test_role_profile_overrides.py::TestBuiltinRoleIDs::test_python_executor_is_builtin PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestBuiltinRoleIDs::test_nonexistent_is_not_builtin PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestParseRoleProfileOverrides::test_parses_valid_overrides PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestParseRoleProfileOverrides::test_rejects_non_mapping PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestParseRoleProfileOverrides::test_rejects_non_dict_top_level PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestParseRoleProfileOverrides::test_empty_overrides_produces_empty_dict PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_builtins_only_when_no_overrides_or_custom PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_override_applies_to_builtin PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_override_does_not_change_role_id PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_custom_roles_appended_after_builtins PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_override_and_custom_together PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_multiple_overrides_on_different_builtins PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestMergeRoleProfilesOrder::test_non_overridden_builtins_unchanged PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOverrideValidation::test_override_unknown_builtin_rejected PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOverrideValidation::test_override_targeting_custom_role_rejected PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOverrideValidation::test_override_unknown_field_rejected PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOverrideValidation::test_all_allowed_override_fields_accepted PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestCustomRoleValidation::test_custom_role_shadows_builtin_rejected PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestCustomRoleValidation::test_custom_role_with_unique_id_accepted PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestFamilyPolicyValidation::test_override_causes_family_policy_violation PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_overrides_applied_through_dict_to_config PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_custom_role_through_dict_to_config PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_both_overrides_and_custom_through_dict_to_config PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_builtins_remain_when_only_overrides_present PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_shadow_in_dict_to_config_raises PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_unknown_override_target_in_dict_to_config_raises PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestDictToConfigOverrides::test_no_overrides_no_custom_gives_defaults PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestLoadOrchestrationConfigOverrides::test_override_runner_via_config_file PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestLoadOrchestrationConfigOverrides::test_custom_role_via_config_file PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestLoadOrchestrationConfigOverrides::test_shadow_violation_via_config_file PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestLoadOrchestrationConfigOverrides::test_unknown_override_target_via_config_file PASSED [ 47%]
tests/orchestration/unit/test_role_profile_overrides.py::TestLoadOrchestrationConfigOverrides::test_override_targeting_custom_role_via_config_file PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOrchestrationConfigEffectiveOnly::test_config_has_no_override_metadata PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestOrchestrationConfigEffectiveOnly::test_effective_registry_is_tuple_of_role_profiles PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationWithOverrides::test_valid_overridden_config_passes_validation PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationWithOverrides::test_family_policy_violation_after_override_detected PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationWithOverrides::test_dispatch_default_role_still_validated_after_override PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestApplyRoleProfileOverride::test_single_field_override PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestApplyRoleProfileOverride::test_multiple_field_override PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestApplyRoleProfileOverride::test_original_profile_unchanged PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestApplyRoleProfileOverride::test_empty_overrides_returns_equivalent_profile PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_unknown_builtin_target_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_unknown_builtin_target_via_config_file_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_custom_role_override_target_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_builtin_shadowing_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_override_execution_context_family_policy_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_override_mutation_policy_family_policy_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_override_session_policy_family_policy_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_override_output_contract_family_policy_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_multiple_family_policy_violations_collected PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_family_policy_violation_via_config_file PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_unknown_override_field_error_format PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_invalid_default_role_id_after_valid_overrides PASSED [ 48%]
tests/orchestration/unit/test_role_profile_overrides.py::TestValidationErrorContracts::test_error_attributes_are_stable PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_claim_returns_none_when_role_unregistered PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_register_and_claim_default_reuses_warm_resource PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_claim_independent_never_reuses_warm_resource PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_expired_registration_is_not_claimable PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_release_moves_claimed_role_back_to_available PASSED [ 48%]
tests/orchestration/unit/test_roster.py::test_snapshot_reusable_sessions_reports_session_ids PASSED [ 48%]
tests/orchestration/unit/test_run_store.py::test_run_id_is_sortable_ulid_like PASSED [ 48%]
tests/orchestration/unit/test_run_store.py::test_append_and_read_latest_for_step PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_latest_selection_tie_breaks_by_run_id PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_malformed_trailing_jsonl_surfaces_corruption PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_prune_run_tombstones_case_index_entries PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_missing_heartbeat_classifies_as_unknown PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_same_plan_admission_rejects_conflicting_active_runs PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_append_conflict_retry_is_bounded_and_observable PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_append_conflict_raises_after_retry_budget PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_unimported_legacy_artifacts_do_not_surface_as_native_runs PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_imported_legacy_run_is_visible_and_blocks_same_plan_when_active PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_import_missing_continuity_minimums_creates_case_blocker PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_retired_imported_legacy_run_does_not_block_same_plan_admission PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_public_latest_queries_are_authoritative_and_sorted PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_case_lookup_surfaces_latest_entry_and_filters_removed PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_admit_for_start_persists_pending_and_rejects_conflict PASSED [ 49%]
tests/orchestration/unit/test_run_store.py::test_malformed_numeric_run_payload_surfaces_corruption PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_save_and_load_minimal_drive PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_save_and_load_full_drive PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_save_update_latest_wins PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_auto_timestamp_on_zero_updated_at PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_barrier_none_round_trips PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveRecordRoundTrip::test_terminal_status_values_round_trip PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_save_and_load_step_child_run PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_save_and_load_resolver_child_run PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_save_and_load_planner_child_run PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_child_runs_for_drive PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_child_runs_for_step PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_child_runs_by_kind PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_child_run_update_latest_wins PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_empty_store_returns_none PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestChildRunRefRoundTrip::test_cross_drive_isolation PASSED [ 49%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_active_drives_excludes_terminal PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_terminal_drives_returns_completed_ones PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_all_drives_returns_everything PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_active_drive_for_plan PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_active_drive_for_plan_returns_none_for_terminal PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveDriveLookup::test_active_drive_for_plan_prefers_latest PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveAdmission::test_admit_when_no_active_drive PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveAdmission::test_admit_rejects_conflicting_active_drive PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveAdmission::test_admit_allows_when_terminal_exists PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveAdmission::test_admission_error_has_active_drive_id PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestPersistenceSurvivesRestart::test_drive_persists_across_store_instances PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestPersistenceSurvivesRestart::test_child_run_persists_across_store_instances PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestPersistenceSurvivesRestart::test_drive_and_child_run_persist_together PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_replay_rebuilds_active_child_run_ids PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_replay_drive_state_rebuilds_record PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_replay_drive_state_raises_for_missing_drive PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_projection_rebuild_function PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_projection_rebuild_with_barrier PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_projection_terminal_child_runs PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_projection_raises_for_missing_drive PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestProjectionReplay::test_projection_raises_for_wrong_store_type PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_pending_child_runs_are_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_running_child_runs_are_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_success_child_runs_not_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_fail_child_runs_not_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_stall_child_runs_not_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_cancelled_child_runs_not_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_transport_error_child_runs_not_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestActiveChildRunFiltering::test_mixed_active_and_terminal_only_returns_active PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveStoreJSONLDurability::test_empty_store_returns_empty_collections PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveStoreJSONLDurability::test_drive_store_creates_directories PASSED [ 50%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveStoreJSONLDurability::test_child_run_store_creates_directories PASSED [ 51%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveStoreJSONLDurability::test_operator_pause_state_round_trips PASSED [ 51%]
tests/orchestration/unit/test_run_store_drive.py::TestDriveStoreJSONLDurability::test_barrier_with_all_fields_round_trips PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_runner_backend_start_returns_concrete_execution_handle PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_runner_backend_protocol_enforces_start_collect_separation PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_runtime_lifecycle_cleanup_preserves_reconcile_disposition PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_complete_step_requires_explicit_reconcile_disposition PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_execution_result_operator_message_populated_on_transport_error PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_resolver_invocation_failure_produces_operator_message PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_resolver_authority_contract_pins_main_worktree_only PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_resolver_gateway_invoke_respects_resolver_authority_contract PASSED [ 51%]
tests/orchestration/unit/test_runner_backend_red.py::test_core_adapter_enforces_normal_flow_only_claim PASSED [ 51%]
tests/orchestration/unit/test_runtime.py::test_prepare_start_collect_cleanup_lifecycle PASSED [ 51%]
tests/orchestration/unit/test_runtime.py::test_prepare_fresh_isolation_recreates_workspace[isolation=workspace] PASSED [ 51%]
tests/orchestration/unit/test_runtime.py::test_prepare_fresh_isolation_recreates_workspace[isolation=independent] PASSED [ 51%]
tests/orchestration/unit/test_runtime.py::test_prepare_rejects_planner_facade_execution_in_linked_worktree_runtime PASSED [ 51%]
tests/orchestration/unit/test_runtime.py::test_prepare_and_cleanup_work_inside_running_event_loop[asyncio] PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_prepare_creates_workspace PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_start_creates_execution_state PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_snapshot_includes_reconcile_states PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_can_complete_requires_reconcile PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_can_complete_allows_merged PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_can_complete_allows_noop PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_can_complete_blocks_merge_conflict PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_get_unresolved_message_returns_none_for_success PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_get_unresolved_message_includes_operator_attention PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_cleanup_blocks_on_active_execution PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_cleanup_blocks_on_unresolved_reconcile PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_cleanup_allows_after_merged_reconcile PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_capture_reconcile_result_updates_tracking_sets PASSED [ 51%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_executes_real_merge_into_integration_context PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_returns_aborted_when_worktree_integrity_preflight_fails PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_returns_aborted_when_integration_context_is_dirty PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_adopts_identical_untracked_collision PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_backs_up_nonidentical_untracked_collision PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_filters_protected_paths_before_merge PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_begin_reconcile_enforces_serialization_lock_conflict PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_runtime_start_uses_resume_substrate_when_mode_requests_reuse PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_runtime_resume_requires_session_id_with_runner_resume_taxonomy PASSED [ 52%]
tests/orchestration/unit/test_runtime_lifecycle.py::test_runner_resume_error_exposes_required_taxonomy_fields PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestRunnerFailProducesCase::test_fail_shim_run_creates_case_listable PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestRunnerFailProducesCase::test_case_show_requires_case_id PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestRunnerFailProducesCase::test_case_respond_requires_action_or_response PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestRunnerFailProducesCase::test_case_list_status_filter PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestCasePersistenceAcrossRecovery::test_case_list_after_recovery PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestCasePersistenceAcrossRecovery::test_case_show_after_recovery PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestNotificationStatusTransitions::test_case_respond_with_action_acknowledges PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestNotificationStatusTransitions::test_case_list_shows_status_field PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestCaseArtifactLayout::test_case_artifact_directory_creates_json_files PASSED [ 52%]
tests/repro/test_orch_cli_case_lifecycle.py::TestCaseArtifactLayout::test_case_show_schema_fields PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestConcurrentRunLaunch::test_concurrent_runs_both_succeed PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestConcurrentRunLaunch::test_concurrent_runs_produce_distinct_ids PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestConcurrentRunLaunch::test_runs_index_integrity_after_multiple_runs PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestRunVsRecoverRace::test_recover_then_run_sequential PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestRunVsRecoverRace::test_run_creates_config_snapshot PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestStopVsRecoverRace::test_stop_then_recover_sequential PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestStopVsRecoverRace::test_recover_then_stop_sequential PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestRapidSequentialInvocations::test_rapid_runs_index_stays_valid PASSED [ 52%]
tests/repro/test_orch_cli_concurrency.py::TestRapidSequentialInvocations::test_rapid_statuses_dont_crash PASSED [ 52%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_pause_request_persists_to_control_channel PASSED [ 52%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_actions_shows_request_status_transitions PASSED [ 52%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_pause_then_unpause_creates_two_requests PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_control_channel_directory_layout PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_stop_request_is_queued_not_blocking PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlRequestConsumption::test_events_log_contains_control_requests PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlChannelConsumptionInternals::test_control_channel_send_creates_pending_file PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlChannelConsumptionInternals::test_control_channel_acknowledge_moves_to_applied PASSED [ 53%]
tests/repro/test_orch_cli_control_consumption.py::TestControlChannelConsumptionInternals::test_control_channel_acknowledge_rejected PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_pause_command_accepts_latest_flag PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_unpause_command_accepts_latest_flag PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_stop_command_accepts_latest_flag PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_pause_persists_control_request PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_actions_lists_control_requests PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlCommandsOnRunningRun::test_control_pause_flat_alias PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestStopBehaviorAsync::test_stop_returns_success_quickly PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestStopBehaviorAsync::test_stop_json_output_format PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlBehavioralContracts::test_pause_produces_applied_action_receipt PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlBehavioralContracts::test_pause_reflected_in_orch_status PASSED [ 53%]
tests/repro/test_orch_cli_full_flow.py::TestControlBehavioralContracts::test_stop_produces_control_stop_event PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestPruneSafety::test_prune_dry_run_lists_eligible_runs PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestPruneSafety::test_prune_preserves_active_runs PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestPruneSafety::test_prune_json_output_format PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestConfigOverrideBehavior::test_plan_path_override PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestConfigOverrideBehavior::test_env_artifact_root_override PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestConfigOverrideBehavior::test_env_workspace_root_override PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestJsonAndExitCodeContracts::test_runs_json_is_valid_json PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestJsonAndExitCodeContracts::test_config_show_effective_json_is_valid PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestJsonAndExitCodeContracts::test_validation_error_exit_code PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestJsonAndExitCodeContracts::test_config_validate_valid_config PASSED [ 53%]
tests/repro/test_orch_cli_prune_config.py::TestJsonAndExitCodeContracts::test_config_validate_json_stable PASSED [ 53%]
tests/repro/test_orch_cli_recovery_faults.py::TestMissingConfigSnapshot::test_recover_detects_missing_config_snapshot PASSED [ 53%]
tests/repro/test_orch_cli_recovery_faults.py::TestMissingConfigSnapshot::test_resume_after_missing_snapshot_is_blocked PASSED [ 53%]
tests/repro/test_orch_cli_recovery_faults.py::TestCorruptContinuityArtifacts::test_recover_detects_truncated_event_stream PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestCorruptContinuityArtifacts::test_recover_detects_malformed_json_in_events PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestCorruptContinuityArtifacts::test_recover_detects_corrupt_state_projection PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestCorruptContinuityArtifacts::test_recover_detects_broken_hash_chain PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestFreshStartRequired::test_quarantined_outcome_opens_case PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestFreshStartRequired::test_recovery_gate_closed_for_quarantine PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestFreshStartRequired::test_recovery_no_silent_deletion_flag PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestRecoveryExitCodes::test_recover_nonexistent_run_exit_2 PASSED [ 54%]
tests/repro/test_orch_cli_recovery_faults.py::TestRecoveryExitCodes::test_recover_dry_run_exit_0 PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_latest_without_dry_run PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_latest_produces_terminal_state PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_with_json_output PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_with_specific_run_id PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_dry_run_vs_non_dry_run_difference PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryNonDryRun::test_recover_after_run_creates_continuity_artifacts PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryReportFields::test_recover_json_contains_recovery_report PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryQuarantineBehavior::test_recover_no_silent_deletion PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryTerminalClosure::test_non_dry_run_recovery_modifies_artifact_state PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryTerminalClosure::test_non_dry_run_json_has_gate_open_allowed_field PASSED [ 54%]
tests/repro/test_orch_cli_recovery_live.py::TestRecoveryTerminalClosure::test_recover_creates_recovery_artifacts_in_runs_dir PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestRunLifecycleFamily::test_orch_runs_lists_runs PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestRunLifecycleFamily::test_orch_run_requires_plan PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestRunLifecycleFamily::test_orch_resume_requires_selector PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestRunLifecycleFamily::test_orch_recover_supports_dry_run PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestInspectFamily::test_orch_inspect_status_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestInspectFamily::test_orch_inspect_events_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestInspectFamily::test_orch_inspect_logs_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestInspectFamily::test_orch_inspect_artifacts_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestInspectFamily::test_orch_inspect_actions_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestCaseFamily::test_orch_case_list_exists PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestCaseFamily::test_orch_case_show_requires_case_id PASSED [ 54%]
tests/repro/test_orch_cli_recovery_red.py::TestCaseFamily::test_orch_case_respond_requires_case_id_and_action PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestControlFamily::test_orch_control_pause_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestControlFamily::test_orch_control_unpause_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestControlFamily::test_orch_control_stop_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestConfigFamily::test_orch_config_show_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestConfigFamily::test_orch_config_validate_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestConfigFamily::test_orch_config_tools_exists PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestOrchGroupHelp::test_orch_group_help PASSED [ 55%]
tests/repro/test_orch_cli_recovery_red.py::TestOrchPruneCommand::test_orch_prune_exists PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerNotFound::test_nonexistent_runner_rejected_explicitly PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerLaunchFailure::test_launch_failure_surfaces_as_error PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerExecutionFailure::test_fail_runner_produces_fail_status PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerExecutionFailure::test_fail_runner_observable_via_status_command PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerStall::test_stall_runner_observable_via_status PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerTransportError::test_transport_error_runner_observable PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerTransportError::test_transport_error_creates_artifact_evidence PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerFaultProducesEventsAndCases::test_fail_runner_produces_events PASSED [ 55%]
tests/repro/test_orch_cli_runner_faults.py::TestRunnerFaultProducesEventsAndCases::test_fail_runner_events_includes_runtime_finish PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestAmbiguousLatestSelector::test_resume_latest_with_multiple_active_runs PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestAmbiguousLatestSelector::test_status_latest_with_zero_runs PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestAmbiguousLatestSelector::test_recover_latest_with_zero_runs PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestSelectorConflict::test_resume_both_runid_and_latest PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestSelectorConflict::test_stop_both_runid_and_latest PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestSelectorConflict::test_pause_both_runid_and_latest PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestMissingSelectorOnMutatingCommands::test_resume_no_selector PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestMissingSelectorOnMutatingCommands::test_recover_no_selector PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestMissingSelectorOnMutatingCommands::test_stop_no_selector PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestMissingSelectorOnMutatingCommands::test_pause_no_selector PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestMissingSelectorOnMutatingCommands::test_unpause_no_selector PASSED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestExplicitRunIdPrecedence::test_stop_specific_run_id SKIPPED [ 55%]
tests/repro/test_orch_cli_selector_safety.py::TestExplicitRunIdPrecedence::test_resume_specific_run_id SKIPPED [ 56%]
tests/repro/test_orch_liveness.py::test_orch_liveness_cli_entrypoint PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_envelope_has_seq_field PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_envelope_has_prev_hash_chain PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_envelope_has_entry_hash PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_envelope_hash_computation_is_sha256 PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_seq_ordering_is_monotonic PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventEnvelopeIntegrity::test_event_corruption_detection PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_run_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_control_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_roster_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_runtime_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_resolver_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_projection_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_registry_has_operator_family PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestEventRegistryPayloadValidation::test_event_payload_schema_validation PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_projection_replay_exists PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_projection_replay_produces_latest_json PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_state_latest_json_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_projection_replay_produces_summary_json PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_projection_replay_produces_metrics_json PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestProjectionReplay::test_state_directory_is_created PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestRunArtifactSchemas::test_final_json_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestRunArtifactSchemas::test_final_json_halt_reason_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestRunArtifactSchemas::test_final_json_summary_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_step_key_path_normalization PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_step_key_preserves_case PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_step_key_percent_encodes_special_chars PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_step_key_is_reversible PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_request_json_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestStepArtifactSchemas::test_result_json_schema PASSED [ 56%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_case_json_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_prompt_json_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_capability_json_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_report_json_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_transcript_jsonl_integrity_fields PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_transcript_hash_algorithm_is_sha256 PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_transcript_log_is_derived_from_jsonl PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestCaseArtifactSchemas::test_case_id_is_globally_unique_ulid PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestRunRegistrySchemas::test_index_jsonl_entry_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestRunRegistrySchemas::test_cases_jsonl_entry_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestRunRegistrySchemas::test_latest_run_resolution PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestHeartbeatLiveness::test_heartbeat_json_schema PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestHeartbeatLiveness::test_liveness_values_are_canonical PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestConfigSnapshot::test_config_snapshot_yaml_written PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestConfigSnapshot::test_config_snapshot_is_authoritative_for_resume PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestToolRegistryValidation::test_tool_registry_has_canonical_table PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestToolRegistryValidation::test_tool_registry_core_family_tools PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestToolRegistryValidation::test_tool_registry_orchestration_family_tools PASSED [ 57%]
tests/repro/test_orch_observability_red.py::TestToolRegistryValidation::test_tool_allowlist_validation PASSED [ 57%]
tests/repro/test_repair_continuity_black_box.py::test_repair_help_no_longer_advertises_continuity PASSED [ 57%]
tests/repro/test_repair_continuity_black_box.py::test_repair_continuity_command_is_removed PASSED [ 57%]
tests/repro/test_repair_continuity_black_box.py::test_orch_recover_dry_run_json_is_supported_surface PASSED [ 57%]
tests/test_agent_field.py::TestStepAgentModel::test_default_is_none PASSED [ 57%]
tests/test_agent_field.py::TestStepAgentModel::test_set_agent PASSED     [ 57%]
tests/test_agent_field.py::TestStepAgentModel::test_roundtrip_yaml PASSED [ 57%]
tests/test_agent_field.py::TestStepAgentModel::test_none_not_serialized PASSED [ 57%]
tests/test_agent_field.py::TestGetNextStepsAgent::test_no_agent_returns_all PASSED [ 57%]
tests/test_agent_field.py::TestGetNextStepsAgent::test_agent_prioritization PASSED [ 57%]
tests/test_agent_field.py::TestGetNextStepsAgent::test_agent_bob PASSED  [ 57%]
tests/test_agent_field.py::TestGetNextStepsAgent::test_rejected_still_first PASSED [ 57%]
tests/test_agent_field.py::TestGetNextStepsAgent::test_unknown_agent_deprioritizes_all_assigned PASSED [ 57%]
tests/test_agent_field.py::TestAddStepAgent::test_add_step_with_agent PASSED [ 58%]
tests/test_agent_field.py::TestAddStepAgent::test_add_step_without_agent PASSED [ 58%]
tests/test_agent_field.py::TestAddStepsBulkAgent::test_bulk_with_agent PASSED [ 58%]
tests/test_agent_field.py::TestEditStepAgent::test_edit_set_agent PASSED [ 58%]
tests/test_agent_field.py::TestEditStepAgent::test_edit_clear_agent PASSED [ 58%]
tests/test_agent_field.py::TestEditStepAgent::test_edit_no_agent_change PASSED [ 58%]
tests/test_agent_field.py::TestCliNextAgent::test_next_shows_agent PASSED [ 58%]
tests/test_agent_field.py::TestCliNextAgent::test_next_agent_flag PASSED [ 58%]
tests/test_agent_field.py::TestCliShowAgent::test_show_displays_agent PASSED [ 58%]
tests/test_agent_field.py::TestCliShowAgent::test_show_no_agent PASSED   [ 58%]
tests/test_agent_field.py::TestCliAddStepAgent::test_add_step_with_agent PASSED [ 58%]
tests/test_agent_field.py::TestCliEditStepAgent::test_edit_step_set_agent PASSED [ 58%]
tests/test_agent_field.py::TestCliEditStepAgent::test_edit_step_clear_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpStatusAgent::test_status_shows_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpStatusAgent::test_status_agent_prioritization PASSED [ 58%]
tests/test_agent_field.py::TestMcpShowAgent::test_show_step_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpShowAgent::test_show_step_no_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpMutateAgent::test_add_step_with_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpMutateAgent::test_edit_step_agent PASSED [ 58%]
tests/test_agent_field.py::TestMcpClaimAutoSelectAgent::test_claim_auto_select_prioritizes_agent PASSED [ 58%]
tests/test_arch_demolition_wave0_characterization.py::test_wave0_agents_md_preserves_legacy_header_and_appends_single_managed_block PASSED [ 58%]
tests/test_arch_demolition_wave0_characterization.py::test_wave0_cli_and_mcp_next_step_ordering_filtering_match PASSED [ 58%]
tests/test_arch_demolition_wave0_characterization.py::test_wave0_duplicate_step_id_diagnostics_are_stable_on_cli_status PASSED [ 58%]
tests/test_arch_demolition_wave0_characterization.py::test_wave0_claim_conflict_shape_matches_cli_and_mcp PASSED [ 58%]
tests/test_architect.py::TestSlugify::test_basic PASSED                  [ 58%]
tests/test_architect.py::TestSlugify::test_special_chars PASSED          [ 58%]
tests/test_architect.py::TestSlugify::test_whitespace PASSED             [ 58%]
tests/test_architect.py::TestSlugify::test_mixed_case PASSED             [ 58%]
tests/test_architect.py::TestSlugify::test_already_slug PASSED           [ 58%]
tests/test_architect.py::TestSlugify::test_numbers PASSED                [ 58%]
tests/test_architect.py::TestSlugify::test_empty_string PASSED           [ 58%]
tests/test_architect.py::TestSlugify::test_only_special_chars PASSED     [ 59%]
tests/test_architect.py::TestUniqueIds::test_step_id_no_collision PASSED [ 59%]
tests/test_architect.py::TestUniqueIds::test_step_id_collision PASSED    [ 59%]
tests/test_architect.py::TestUniqueIds::test_step_id_multiple_collisions PASSED [ 59%]
tests/test_architect.py::TestUniqueIds::test_phase_id_no_collision PASSED [ 59%]
tests/test_architect.py::TestUniqueIds::test_phase_id_collision PASSED   [ 59%]
tests/test_architect.py::TestAddStep::test_basic_auto_id PASSED          [ 59%]
tests/test_architect.py::TestAddStep::test_explicit_id PASSED            [ 59%]
tests/test_architect.py::TestAddStep::test_with_all_fields PASSED        [ 59%]
tests/test_architect.py::TestAddStep::test_auto_id_collision PASSED      [ 59%]
tests/test_architect.py::TestAddStep::test_phase_not_found PASSED        [ 59%]
tests/test_architect.py::TestAddStep::test_locked_phase_allows_adding PASSED [ 59%]
tests/test_architect.py::TestAddStep::test_duplicate_explicit_id PASSED  [ 59%]
tests/test_architect.py::TestAddStep::test_invalid_depends_on PASSED     [ 59%]
tests/test_architect.py::TestAddStep::test_done_phase_reopens PASSED     [ 59%]
tests/test_architect.py::TestAddStep::test_in_progress_phase_ok PASSED   [ 59%]
tests/test_architect.py::TestAddStep::test_pending_phase_stays_pending PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_done_with_evidence PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_done_without_evidence_raises PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_skipped_with_reason PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_skipped_without_reason_raises PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_claimed_rejected_as_transient PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_pending_explicit_is_default PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_done_step_does_not_reopen_done_phase PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_skipped_step_does_not_reopen_done_phase PASSED [ 59%]
tests/test_architect.py::TestAddStepImportStatus::test_pending_step_reopens_done_phase PASSED [ 59%]
tests/test_architect.py::TestAddPhase::test_basic_auto_id PASSED         [ 59%]
tests/test_architect.py::TestAddPhase::test_explicit_id PASSED           [ 59%]
tests/test_architect.py::TestAddPhase::test_with_all_fields PASSED       [ 59%]
tests/test_architect.py::TestAddPhase::test_auto_id_collision PASSED     [ 59%]
tests/test_architect.py::TestAddPhase::test_duplicate_explicit_id PASSED [ 60%]
tests/test_architect.py::TestAddPhase::test_invalid_depends_on PASSED    [ 60%]
tests/test_architect.py::TestAddPhase::test_status_pending_no_deps PASSED [ 60%]
tests/test_architect.py::TestAddPhase::test_status_pending_when_deps_done PASSED [ 60%]
tests/test_architect.py::TestAddPhase::test_status_locked_when_deps_not_done PASSED [ 60%]
tests/test_architect.py::TestAddPhase::test_multiple_deps_all_done PASSED [ 60%]
tests/test_architect.py::TestAddPhase::test_multiple_deps_one_not_done PASSED [ 60%]
tests/test_architect.py::TestEditStep::test_edit_name PASSED             [ 60%]
tests/test_architect.py::TestEditStep::test_edit_description PASSED      [ 60%]
tests/test_architect.py::TestEditStep::test_edit_verification PASSED     [ 60%]
tests/test_architect.py::TestEditStep::test_add_dep PASSED               [ 60%]
tests/test_architect.py::TestEditStep::test_add_dep_invalid PASSED       [ 60%]
tests/test_architect.py::TestEditStep::test_add_dep_idempotent PASSED    [ 60%]
tests/test_architect.py::TestEditStep::test_remove_dep PASSED            [ 60%]
tests/test_architect.py::TestEditStep::test_remove_dep_nonexistent_is_noop PASSED [ 60%]
tests/test_architect.py::TestEditStep::test_step_not_found PASSED        [ 60%]
tests/test_architect.py::TestEditStep::test_multiple_edits PASSED        [ 60%]
tests/test_architect.py::TestRemoveStep::test_remove_pending PASSED      [ 60%]
tests/test_architect.py::TestRemoveStep::test_remove_claimed_fails PASSED [ 60%]
tests/test_architect.py::TestRemoveStep::test_remove_not_found PASSED    [ 60%]
tests/test_architect.py::TestRemoveStep::test_remove_with_dependents_fails PASSED [ 60%]
tests/test_architect.py::TestRemoveStep::test_remove_force_cleans_deps PASSED [ 60%]
tests/test_architect.py::TestMoveStep::test_move_basic PASSED            [ 60%]
tests/test_architect.py::TestMoveStep::test_move_clears_deps PASSED      [ 60%]
tests/test_architect.py::TestMoveStep::test_move_claimed_fails PASSED    [ 60%]
tests/test_architect.py::TestMoveStep::test_move_same_phase_fails PASSED [ 60%]
tests/test_architect.py::TestMoveStep::test_move_target_not_found PASSED [ 60%]
tests/test_architect.py::TestMoveStep::test_move_with_dependents_fails PASSED [ 60%]
tests/test_architect.py::TestUnlockPhase::test_unlock_locked_deps_done PASSED [ 60%]
tests/test_architect.py::TestUnlockPhase::test_unlock_deps_not_done PASSED [ 60%]
tests/test_architect.py::TestUnlockPhase::test_unlock_not_locked_fails PASSED [ 60%]
tests/test_architect.py::TestUnlockPhase::test_unlock_not_found PASSED   [ 61%]
tests/test_architect.py::TestUnlockPhase::test_unlock_multiple_deps_all_done PASSED [ 61%]
tests/test_architect.py::TestUnlockPhase::test_unlock_multiple_deps_one_not_done PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_basic_bulk PASSED        [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_with_all_fields PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_sequential_deps PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_explicit_ids PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_missing_name_fails PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_phase_not_found PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_empty_list PASSED   [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_after_as_comma_string PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_intra_batch_short_slug_ref PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_cycle_detection_rollback PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_mixed_existing_and_batch_deps PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulk::test_bulk_unknown_dep_in_batch_fails PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_done_with_evidence PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_skipped_with_reason PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_done_without_evidence_raises PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_skipped_without_reason_raises PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_transient_status_rejected PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_mixed_statuses PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_all_done_does_not_complete_phase PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_done_steps_do_not_reopen_done_phase PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_pending_step_reopens_done_phase PASSED [ 61%]
tests/test_architect.py::TestAddStepsBulkImportStatus::test_bulk_invalid_status_string PASSED [ 61%]
tests/test_architect.py::TestEditPhase::test_edit_name PASSED            [ 61%]
tests/test_architect.py::TestEditPhase::test_edit_context PASSED         [ 61%]
tests/test_architect.py::TestEditPhase::test_edit_gate PASSED            [ 61%]
tests/test_architect.py::TestEditPhase::test_add_dep PASSED              [ 61%]
tests/test_architect.py::TestEditPhase::test_add_dep_invalid PASSED      [ 61%]
tests/test_architect.py::TestEditPhase::test_add_dep_self PASSED         [ 61%]
tests/test_architect.py::TestEditPhase::test_rm_dep PASSED               [ 62%]
tests/test_architect.py::TestEditPhase::test_depends_on_override PASSED  [ 62%]
tests/test_architect.py::TestEditPhase::test_depends_on_override_clears PASSED [ 62%]
tests/test_architect.py::TestEditPhase::test_depends_on_override_invalid PASSED [ 62%]
tests/test_architect.py::TestEditPhase::test_depends_on_override_self PASSED [ 62%]
tests/test_architect.py::TestEditPhase::test_depends_on_overrides_add_remove PASSED [ 62%]
tests/test_architect.py::TestEditPhase::test_phase_not_found PASSED      [ 62%]
tests/test_autofix_message.py::TestCliAutofixMessage::test_autofix_message_present PASSED [ 62%]
tests/test_autofix_message.py::TestCliAutofixMessage::test_autofix_message_contains_phase_and_status PASSED [ 62%]
tests/test_autofix_message.py::TestCliAutofixMessage::test_autofix_message_not_a_warning PASSED [ 62%]
tests/test_autofix_message.py::TestCliAutofixMessage::test_no_autofix_message_when_consistent PASSED [ 62%]
tests/test_autofix_message.py::TestMcpAutofixMessage::test_autofix_message_returned PASSED [ 62%]
tests/test_autofix_message.py::TestMcpAutofixMessage::test_autofix_message_contains_phase_and_status PASSED [ 62%]
tests/test_autofix_message.py::TestMcpAutofixMessage::test_autofix_message_not_a_warning PASSED [ 62%]
tests/test_autofix_message.py::TestMcpAutofixMessage::test_no_autofix_message_when_consistent PASSED [ 62%]
tests/test_checklist.py::TestCheckItem::test_toggle_unchecked_to_checked PASSED [ 62%]
tests/test_checklist.py::TestCheckItem::test_toggle_checked_to_unchecked PASSED [ 62%]
tests/test_checklist.py::TestCheckItem::test_case_insensitive PASSED     [ 62%]
tests/test_checklist.py::TestCheckItem::test_no_match_raises PASSED      [ 62%]
tests/test_checklist.py::TestCheckItem::test_ambiguous_match_raises PASSED [ 62%]
tests/test_checklist.py::TestCheckItem::test_exact_enough_match PASSED   [ 62%]
tests/test_checklist.py::TestAppendItem::test_append_new_item PASSED     [ 62%]
tests/test_checklist.py::TestAppendItem::test_append_to_empty_description PASSED [ 62%]
tests/test_checklist.py::TestCheckAndAppend::test_both_check_and_append PASSED [ 62%]
tests/test_checklist.py::TestEdgeCases::test_no_check_no_append_raises PASSED [ 62%]
tests/test_checklist.py::TestEdgeCases::test_step_not_found PASSED       [ 62%]
tests/test_checklist.py::TestEdgeCases::test_multiple_toggles PASSED     [ 62%]
tests/test_checklist.py::TestDeterministicInventory::test_inventory_discovers_items_from_both_fields PASSED [ 62%]
tests/test_checklist.py::TestDeterministicInventory::test_missing_and_empty_fields_produce_empty_inventories PASSED [ 62%]
tests/test_checklist.py::TestDeterministicInventory::test_item_id_values_are_snapshot_scoped PASSED [ 62%]
tests/test_checklist.py::TestDeterministicMutation::test_stale_revision_rejects_mutation PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_deterministic_single_mutation_is_idempotent PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_legacy_keyword_toggle_compatibility PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_invalid_selector_mode_combinations PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_marker_only_preservation PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_atomic_batch_mutation_diagnostics PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_literal_rev1_is_rejected_without_mutation PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_failed_mixed_batch_is_all_or_nothing PASSED [ 63%]
tests/test_checklist.py::TestDeterministicMutation::test_unsupported_field_selector_reports_structured_error PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_schema_structure PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_lite_mode PASSED      [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_lite_mode_omits_redundant_active PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_deterministic_focus_any_claimed PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_deterministic_focus_agent_preference PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_focus_fallback_to_next PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_bounding_limits PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointCore::test_guidance_inclusion PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointActiveSteps::test_active_steps_include_names_and_claimed_by PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointParity::test_cli_mcp_output_match PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointClipboard::test_clipboard_present_when_non_empty PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointClipboard::test_clipboard_omitted_when_empty PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointClipboard::test_clipboard_omitted_when_expired PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointClipboard::test_clipboard_present_in_full_mode PASSED [ 63%]
tests/test_checkpoint.py::TestCheckpointClipboard::test_clipboard_clear_updates_plan_yaml PASSED [ 63%]
tests/test_claims.py::test_acquire_release_claim PASSED                  [ 63%]
tests/test_claims.py::test_stale_claim_cleanup PASSED                    [ 63%]
tests/test_claims.py::test_concurrent_claims PASSED                      [ 63%]
tests/test_claims.py::test_missing_claims_file PASSED                    [ 63%]
tests/test_claims.py::test_branch_isolation PASSED                       [ 63%]
tests/test_claims.py::test_claims_use_sidecar_lock_file PASSED           [ 63%]
tests/test_claims.py::test_repair_claims_dry_run_keeps_file_unchanged PASSED [ 63%]
tests/test_claims.py::test_repair_claims_step_scope_preserves_unrelated_entries PASSED [ 64%]
tests/test_claims.py::test_repair_claims_missing_file_restores_claimed_state PASSED [ 64%]
tests/test_claims.py::test_repair_claims_invalid_step_id_fails PASSED    [ 64%]
tests/test_claims.py::test_repair_claims_split_brain_plan_precedence_updates_entry PASSED [ 64%]
tests/test_claims.py::test_repair_claims_status_repair_attempted PASSED  [ 64%]
tests/test_claims.py::test_repair_claims_status_repair_succeeded PASSED  [ 64%]
tests/test_claims.py::test_repair_claims_status_in_output PASSED         [ 64%]
tests/test_cli.py::TestVersion::test_version_flag PASSED                 [ 64%]
tests/test_cli.py::TestVersion::test_short_version_flag PASSED           [ 64%]
tests/test_cli.py::TestInit::test_init_creates_file PASSED               [ 64%]
tests/test_cli.py::TestInit::test_init_refuses_existing PASSED           [ 64%]
tests/test_cli.py::TestInit::test_init_creates_agents_md PASSED          [ 64%]
tests/test_cli.py::TestInit::test_init_appends_to_existing_agents_md PASSED [ 64%]
tests/test_cli.py::TestInit::test_init_agents_md_idempotent PASSED       [ 64%]
tests/test_cli.py::TestInit::test_init_preserves_legacy_block_and_appends_new PASSED [ 64%]
tests/test_cli.py::TestInit::test_agents_md_command_upserts PASSED       [ 64%]
tests/test_cli.py::TestClaudeDetection::test_fresh_project_with_claude_dir_creates_claude_md PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_fresh_project_without_claude_dir_creates_agents_md PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_existing_agents_md_with_markers_preserved_despite_claude_dir PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_existing_claude_md_with_markers_preserved PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_existing_agents_md_without_markers_wins_over_claude_dir PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_init_creates_claude_md_when_claude_dir_present PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_both_files_exist_agents_with_markers_wins PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_both_files_exist_claude_with_markers_wins PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_target_flag_agents_forces_agents_md PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_target_flag_claude_forces_claude_md PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_target_flag_overrides_existing_file_detection PASSED [ 64%]
tests/test_cli.py::TestClaudeDetection::test_init_target_flag_claude PASSED [ 64%]
tests/test_cli.py::TestEditPlan::test_edit_plan_project_guidance_file PASSED [ 64%]
tests/test_cli.py::TestEditPlan::test_edit_plan_rejects_dual_inputs PASSED [ 64%]
tests/test_cli.py::TestNext::test_shows_claimable_steps PASSED           [ 64%]
tests/test_cli.py::TestNext::test_shows_strategy_context PASSED          [ 65%]
tests/test_cli.py::TestNext::test_empty_plan PASSED                      [ 65%]
tests/test_cli.py::TestNext::test_shows_one_line_summary PASSED          [ 65%]
tests/test_cli.py::TestNext::test_detail_flag_shows_full_description PASSED [ 65%]
tests/test_cli.py::TestNext::test_affordance_hints PASSED                [ 65%]
tests/test_cli.py::TestNext::test_default_limit_caps_at_three PASSED     [ 65%]
tests/test_cli.py::TestNext::test_limit_flag PASSED                      [ 65%]
tests/test_cli.py::TestNext::test_all_flag_shows_everything PASSED       [ 65%]
tests/test_cli.py::TestNext::test_short_limit_flag PASSED                [ 65%]
tests/test_cli.py::TestStatus::test_overview PASSED                      [ 65%]
tests/test_cli.py::TestStatus::test_phase_detail PASSED                  [ 65%]
tests/test_cli.py::TestStatus::test_phase_not_found PASSED               [ 65%]
tests/test_cli.py::TestGuide::test_default_startup PASSED                [ 65%]
tests/test_cli.py::TestGuide::test_stuck_topic PASSED                    [ 65%]
tests/test_cli.py::TestGuide::test_review_topic PASSED                   [ 65%]
tests/test_cli.py::TestGuide::test_planning_topic PASSED                 [ 65%]
tests/test_cli.py::TestGuide::test_unknown_topic PASSED                  [ 65%]
tests/test_cli.py::TestClaim::test_claim_step PASSED                     [ 65%]
tests/test_cli.py::TestClaim::test_claim_reject_with_existing_claim_shows_diagnostics PASSED [ 65%]
tests/test_cli.py::TestClaim::test_claim_nonexistent PASSED              [ 65%]
tests/test_cli.py::TestClaim::test_claim_blocked_step PASSED             [ 65%]
tests/test_cli.py::TestClaim::test_claim_error_for_already_qualified_id_is_not_double_prefixed PASSED [ 65%]
tests/test_cli.py::TestClaim::test_auto_claim_picks_first PASSED         [ 65%]
tests/test_cli.py::TestClaim::test_auto_claim_no_steps_fails PASSED      [ 65%]
tests/test_cli.py::TestRepairClaimsCLI::test_repair_claims_relative_and_absolute_plan_paths_share_claims_target PASSED [ 65%]
tests/test_cli.py::TestRepairClaimsCLI::test_repair_claims_step_scope_preserves_unrelated_claims PASSED [ 65%]
tests/test_cli.py::TestRepairClaimsCLI::test_repair_claims_missing_file_fallback_and_invalid_step PASSED [ 65%]
tests/test_cli.py::TestComplete::test_complete_step PASSED               [ 65%]
tests/test_cli.py::TestComplete::test_complete_unclaimed PASSED          [ 65%]
tests/test_cli.py::TestComplete::test_claim_and_complete_use_claims_store PASSED [ 65%]
tests/test_cli.py::TestComplete::test_claim_complete_with_external_plan_avoids_misleading_autosave_noise PASSED [ 65%]
tests/test_cli.py::TestComplete::test_claim_in_repo_preserves_autosave_commit_behavior PASSED [ 66%]
tests/test_cli.py::TestComplete::test_gate_check_claim_consistency_shows_scoped_verification_note PASSED [ 66%]
tests/test_cli.py::TestComplete::test_split_brain_claim_exists_but_plan_shows_pending PASSED [ 66%]
tests/test_cli.py::TestCompletePhase::test_complete_phase_historical_success PASSED [ 66%]
tests/test_cli.py::TestCompletePhase::test_complete_phase_fails_if_pending_steps PASSED [ 66%]
tests/test_cli.py::TestCompletePhase::test_complete_phase_requires_evidence_option PASSED [ 66%]
tests/test_cli.py::TestCompletePhase::test_complete_phase_requires_deps_done PASSED [ 66%]
tests/test_cli.py::TestDefer::test_defer_claimed PASSED                  [ 66%]
tests/test_cli.py::TestDefer::test_defer_pending_fails PASSED            [ 66%]
tests/test_cli.py::TestReject::test_reject_done PASSED                   [ 66%]
tests/test_cli.py::TestReject::test_reject_pending_fails PASSED          [ 66%]
tests/test_cli.py::TestSkip::test_skip_pending PASSED                    [ 66%]
tests/test_cli.py::TestSkip::test_skip_done_fails PASSED                 [ 66%]
tests/test_cli.py::TestSkip::test_skip_invalid_reason PASSED             [ 66%]
tests/test_cli.py::TestSkip::test_cancel_alias PASSED                    [ 66%]
tests/test_cli.py::TestSkip::test_cancel_done_fails PASSED               [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_all_pending PASSED     [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_locked_fails PASSED    [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_invalid_reason PASSED  [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_with_done_steps PASSED [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_cascades_unlock PASSED [ 66%]
tests/test_cli.py::TestSkipPhase::test_skip_phase_locked_with_force PASSED [ 66%]
tests/test_cli.py::TestCheck::test_check_item PASSED                     [ 66%]
tests/test_cli.py::TestCheck::test_add_item PASSED                       [ 66%]
tests/test_cli.py::TestCheck::test_no_args_fails PASSED                  [ 66%]
tests/test_cli.py::TestValidate::test_valid_plan PASSED                  [ 66%]
tests/test_cli.py::TestValidate::test_invalid_plan PASSED                [ 66%]
tests/test_cli.py::TestValidate::test_validate_ignores_split_state_orphans PASSED [ 66%]
tests/test_cli.py::TestValidate::test_load_ignores_legacy_state_until_explicit_migration PASSED [ 66%]
tests/test_cli.py::TestMigrate::test_migrate_no_state_message PASSED     [ 66%]
tests/test_cli.py::TestMigrate::test_migrate_already_migrated_message PASSED [ 67%]
tests/test_cli.py::TestMigrate::test_migrate_preview_and_confirm_path PASSED [ 67%]
tests/test_cli.py::TestMigrate::test_migrate_runs_and_shows_summary PASSED [ 67%]
tests/test_cli.py::TestMigrate::test_migrate_shows_orphan_warnings PASSED [ 67%]
tests/test_cli.py::TestRecover::test_recover_command_shows_diff PASSED   [ 67%]
tests/test_cli.py::TestRecover::test_recover_command_no_backup_exit_1 PASSED [ 67%]
tests/test_cli.py::TestRecover::test_recover_command_corrupted_backup_shows_error PASSED [ 67%]
tests/test_cli.py::TestRecover::test_recover_command_permission_error_on_backup_dir PASSED [ 67%]
tests/test_cli.py::TestFullLifecycle::test_claim_complete_unblocks_deps PASSED [ 67%]
tests/test_cli.py::TestFullLifecycle::test_full_phase_completion PASSED  [ 67%]
tests/test_cli.py::TestAddStep::test_basic_auto_id PASSED                [ 67%]
tests/test_cli.py::TestAddStep::test_explicit_id PASSED                  [ 67%]
tests/test_cli.py::TestAddStep::test_with_all_flags PASSED               [ 67%]
tests/test_cli.py::TestAddStep::test_add_step_evidence_template_file PASSED [ 67%]
tests/test_cli.py::TestAddStep::test_phase_not_found PASSED              [ 67%]
tests/test_cli.py::TestAddStep::test_locked_phase_allows_adding PASSED   [ 67%]
tests/test_cli.py::TestAddStep::test_affordance_hints PASSED             [ 67%]
tests/test_cli.py::TestLongSlugWarning::test_add_step_long_slug_warns PASSED [ 67%]
tests/test_cli.py::TestLongSlugWarning::test_add_step_short_slug_no_warning PASSED [ 67%]
tests/test_cli.py::TestLongSlugWarning::test_add_step_explicit_id_no_warning PASSED [ 67%]
tests/test_cli.py::TestLongSlugWarning::test_add_phase_long_slug_warns PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_done_with_evidence PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_done_without_evidence_fails PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_skipped_with_reason PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_skipped_without_reason_fails PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_transient_status_fails PASSED [ 67%]
tests/test_cli.py::TestAddStepImportStatusCli::test_add_step_pending_explicit PASSED [ 67%]
tests/test_cli.py::TestAddStepsImportStatusCli::test_bulk_done_steps PASSED [ 67%]
tests/test_cli.py::TestAddStepsImportStatusCli::test_bulk_mixed_statuses PASSED [ 67%]
tests/test_cli.py::TestAddStepsImportStatusCli::test_bulk_done_without_evidence_fails PASSED [ 67%]
tests/test_cli.py::TestAddStepsImportStatusCli::test_bulk_transient_status_fails PASSED [ 67%]
tests/test_cli.py::TestEditStepCli::test_edit_name PASSED                [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_desc PASSED                [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_add_dep PASSED             [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_evidence_template_inline PASSED [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_add_rm_ref PASSED          [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_rm_dep PASSED              [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_nothing_fails PASSED       [ 68%]
tests/test_cli.py::TestEditStepCli::test_edit_not_found PASSED           [ 68%]
tests/test_cli.py::TestRemoveStepCli::test_remove_pending PASSED         [ 68%]
tests/test_cli.py::TestRemoveStepCli::test_remove_claimed_fails PASSED   [ 68%]
tests/test_cli.py::TestRemoveStepCli::test_remove_with_dependents_fails PASSED [ 68%]
tests/test_cli.py::TestRemoveStepCli::test_remove_with_force_cleans_deps PASSED [ 68%]
tests/test_cli.py::TestMoveStepCli::test_move_basic PASSED               [ 68%]
tests/test_cli.py::TestMoveStepCli::test_move_target_not_found PASSED    [ 68%]
tests/test_cli.py::TestMoveStepCli::test_move_warns_about_cleared_deps PASSED [ 68%]
tests/test_cli.py::TestMoveStepCli::test_move_no_warning_when_no_deps PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_step PASSED                       [ 68%]
tests/test_cli.py::TestShow::test_show_step_with_description PASSED      [ 68%]
tests/test_cli.py::TestShow::test_show_phase PASSED                      [ 68%]
tests/test_cli.py::TestShow::test_show_not_found PASSED                  [ 68%]
tests/test_cli.py::TestShow::test_show_not_found_collapses_double_prefixed_selector PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_claimed_step_has_agent PASSED     [ 68%]
tests/test_cli.py::TestShow::test_show_pending_step_suggests_claim PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_done_step_suggests_next_and_reject PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_claimed_step_suggests_defer PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_skipped_step_suggests_next PASSED [ 68%]
tests/test_cli.py::TestShow::test_show_rejected_step_suggests_claim PASSED [ 68%]
tests/test_cli.py::TestShow::test_duplicate_explicit_id PASSED           [ 68%]
tests/test_cli.py::TestAddPhase::test_basic_auto_id PASSED               [ 68%]
tests/test_cli.py::TestAddPhase::test_explicit_id PASSED                 [ 68%]
tests/test_cli.py::TestAddPhase::test_with_all_flags PASSED              [ 68%]
tests/test_cli.py::TestAddPhase::test_auto_locked_when_deps_not_done PASSED [ 69%]
tests/test_cli.py::TestAddPhase::test_auto_pending_when_no_deps PASSED   [ 69%]
tests/test_cli.py::TestAddPhase::test_deps_not_found PASSED              [ 69%]
tests/test_cli.py::TestAddPhase::test_duplicate_explicit_id PASSED       [ 69%]
tests/test_cli.py::TestAddPhase::test_affordance_hints PASSED            [ 69%]
tests/test_cli.py::TestLinkedWorktreeGuard::test_cli_mutate_blocked_in_linked_worktree PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeGuard::test_cli_mutate_allowed_with_env_override PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeGuard::test_cli_mutate_blocked_when_main_root_unresolved PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeImplicitResolution::test_cli_status_resolves_to_main_worktree_plan_without_explicit_path PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeImplicitResolution::test_cli_claim_resolves_to_main_worktree_plan_without_explicit_path PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeImplicitResolution::test_cli_mutate_with_explicit_plan_allowed_in_linked_worktree PASSED [ 69%]
tests/test_cli.py::TestLinkedWorktreeImplicitResolution::test_cli_fails_closed_on_malformed_worktree_without_stale_local_fallback PASSED [ 69%]
tests/test_cli.py::TestUnlockCli::test_unlock_locked_phase PASSED        [ 69%]
tests/test_cli.py::TestUnlockCli::test_unlock_deps_not_done PASSED       [ 69%]
tests/test_cli.py::TestUnlockCli::test_unlock_not_locked PASSED          [ 69%]
tests/test_cli.py::TestUnlockCli::test_unlock_not_found PASSED           [ 69%]
tests/test_cli.py::TestUnlockCli::test_unlock_affordance_hints PASSED    [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_add_from_stdin PASSED      [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_empty_stdin PASSED         [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_invalid_yaml PASSED        [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_not_a_list PASSED          [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_phase_not_found PASSED     [ 69%]
tests/test_cli.py::TestAddStepsCli::test_bulk_with_deps PASSED           [ 69%]
tests/test_cli.py::TestSearch::test_search_finds_match PASSED            [ 69%]
tests/test_cli.py::TestSearch::test_search_no_match PASSED               [ 69%]
tests/test_cli.py::TestSearch::test_search_phase_filter PASSED           [ 69%]
tests/test_cli.py::TestSearch::test_search_checklist_content PASSED      [ 69%]
tests/test_cli.py::TestSearch::test_search_regex_mode PASSED             [ 69%]
tests/test_cli.py::TestSearch::test_search_invalid_regex PASSED          [ 69%]
tests/test_cli.py::TestSearchHints::test_add_step_has_search_hint PASSED [ 69%]
tests/test_cli.py::TestSearchHints::test_edit_step_has_search_hint PASSED [ 69%]
tests/test_cli.py::TestSearchHints::test_remove_step_has_search_hint PASSED [ 70%]
tests/test_cli.py::TestSearchHints::test_move_step_has_search_hint PASSED [ 70%]
tests/test_cli.py::TestSearchHints::test_edit_phase_has_search_hint PASSED [ 70%]
tests/test_cli.py::TestSearchHints::test_skip_phase_has_search_hint PASSED [ 70%]
tests/test_cli.py::TestEditPhase::test_edit_phase_name PASSED            [ 70%]
tests/test_cli.py::TestEditPhase::test_edit_phase_context PASSED         [ 70%]
tests/test_cli.py::TestEditPhase::test_edit_phase_gate PASSED            [ 70%]
tests/test_cli.py::TestEditPhase::test_edit_phase_not_found PASSED       [ 70%]
tests/test_cli.py::TestEditPhase::test_edit_phase_nothing_to_edit PASSED [ 70%]
tests/test_cli.py::TestMine::test_mine_shows_claimed PASSED              [ 70%]
tests/test_cli.py::TestMine::test_mine_filters_by_agent PASSED           [ 70%]
tests/test_cli.py::TestMine::test_mine_empty PASSED                      [ 70%]
tests/test_cli.py::TestDag::test_dag_phase_level PASSED                  [ 70%]
tests/test_cli.py::TestDag::test_dag_step_level PASSED                   [ 70%]
tests/test_cli.py::TestDag::test_dag_phase_not_found PASSED              [ 70%]
tests/test_cli.py::TestDag::test_dag_has_drill_hint PASSED               [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_validate_duplicate_step_ids_are_hard_errors PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_gate_check_blocks_duplicate_step_id_plan PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_status_shows_duplicate_diagnostics PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_show_step_emits_structured_repair_recommendation PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_dag_shows_duplicate_diagnostics PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_claim_blocks_ambiguous_duplicate_target_without_opt_in PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_check_blocks_ambiguous_duplicate_target_without_opt_in PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_migrate_blocks_when_duplicate_targets_require_auto_migrate PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_migrate_step_id_dry_run_json_reports_mapping_and_evidence PASSED [ 70%]
tests/test_cli.py::TestDuplicateIdDiagnostics::test_migrate_step_id_apply_json_saves_then_becomes_noop PASSED [ 70%]
tests/test_cli.py::TestClipboardWrite::test_write_basic PASSED           [ 70%]
tests/test_cli.py::TestClipboardWrite::test_write_empty_author_fails PASSED [ 70%]
tests/test_cli.py::TestClipboardWrite::test_write_empty_content_fails PASSED [ 70%]
tests/test_cli.py::TestClipboardWrite::test_write_custom_ttl PASSED      [ 70%]
tests/test_cli.py::TestClipboardRead::test_read_basic PASSED             [ 71%]
tests/test_cli.py::TestClipboardRead::test_read_empty PASSED             [ 71%]
tests/test_cli.py::TestClipboardClear::test_clear_basic PASSED           [ 71%]
tests/test_cli.py::TestClipboardClear::test_clear_already_empty PASSED   [ 71%]
tests/test_cli.py::TestAffinityCli::test_claim_no_agent_no_check PASSED  [ 71%]
tests/test_cli.py::TestAffinityCli::test_claim_suggested_mismatch_warns PASSED [ 71%]
tests/test_cli.py::TestAffinityCli::test_claim_exclusive_mismatch_rejects PASSED [ 71%]
tests/test_cli.py::TestAffinityCli::test_claim_exclusive_force_allows PASSED [ 71%]
tests/test_cli.py::TestAffinityCli::test_show_displays_affinity PASSED   [ 71%]
tests/test_cli.py::TestAffinityCli::test_next_shows_exclusive_icon PASSED [ 71%]
tests/test_cli.py::TestCliUnifiedStateIntegration::test_claim_updates_plan_yaml PASSED [ 71%]
tests/test_cli.py::TestCliUnifiedStateIntegration::test_complete_updates_plan_yaml PASSED [ 71%]
tests/test_cli.py::TestCliUnifiedStateIntegration::test_mutation_updates_plan_yaml_only PASSED [ 71%]
tests/test_cli.py::TestCliUnifiedStateIntegration::test_status_is_read_only_for_plan_yaml PASSED [ 71%]
tests/test_cli.py::TestCliUnifiedStateIntegration::test_legacy_migration_preserves_embedded_runtime_state PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_status_baseline_clean_when_no_mismatch PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_show_baseline_clean_when_no_mismatch PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_status_shows_mismatch_indicator PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_status_shows_ghost_claims_count PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_status_shows_stale_plan_claims_count PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_status_shows_repair_instructions PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_show_step_explains_mismatch PASSED [ 71%]
tests/test_cli.py::TestClaimMismatchVisibility::test_show_step_clean_for_non_claimed PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_drive_command_is_not_registered PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_root_help_exposes_orch_surface_instead PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_orch_group_help_is_available PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_drive_invocation_reports_removed_surface PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_drive_python_import_surface_is_absent PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_orch_run_help_is_available PASSED  [ 71%]
tests/test_cli.py::TestDriveCLI::test_orch_status_help_is_available PASSED [ 71%]
tests/test_cli.py::TestDriveCLI::test_orch_surface_replaces_drive_for_operator_workflows PASSED [ 71%]
tests/test_cli.py::TestDeterministicCheckCLI::test_legacy_keyword_toggle_works PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_deterministic_mutation_requires_revision PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_deterministic_mutation_stale_revision PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_invalid_selector_modes_fail PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_batch_mutation_atomic PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_json_stale_revision_exits_nonzero_with_parseable_error PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_valid_json_batch_uses_current_revision_and_top_level_selector_alias PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_json_batch_unsupported_field_exits_nonzero PASSED [ 72%]
tests/test_cli.py::TestDeterministicCheckCLI::test_inventory_machine_readable PASSED [ 72%]
tests/test_codex_parser_behavior.py::test_execution_result_status_contract_covers_runtime_surface PASSED [ 72%]
tests/test_codex_resume_behavior.py::test_runtime_surface_replaces_driver_resume_surface PASSED [ 72%]
tests/test_core.py::test_recover_restores_from_backup PASSED             [ 72%]
tests/test_core.py::test_preview_recovery_does_not_write_plan PASSED     [ 72%]
tests/test_core.py::test_apply_recovery_writes_backup_content PASSED     [ 72%]
tests/test_core.py::test_recover_no_backup_error PASSED                  [ 72%]
tests/test_core.py::test_validate_plan_validates_dag PASSED              [ 72%]
tests/test_core.py::test_validate_plan_detects_duplicate_phase_ids PASSED [ 72%]
tests/test_core.py::test_validate_plan_detects_duplicate_step_ids PASSED [ 72%]
tests/test_core.py::test_claim_step_with_claims_path_writes_claim_entry PASSED [ 72%]
tests/test_core.py::test_claim_step_calls_cleanup_stale_claims PASSED    [ 72%]
tests/test_core.py::test_complete_step_sets_done_at_timestamp PASSED     [ 72%]
tests/test_core.py::test_complete_step_releases_claim_from_claims_json PASSED [ 72%]
tests/test_core.py::test_defer_step_releases_claim_from_claims_json PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_empty_plan PASSED  [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_single_phase_single_step_done PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_progress_calculation_with_skipped PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_step_status_counts PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_step_detail_fields PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_phase_fields PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_mermaid_phase_dag PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_mermaid_step_dags PASSED [ 72%]
tests/test_dashboard.py::TestBuildDashboardData::test_locked_step_detection PASSED [ 73%]
tests/test_dashboard.py::TestBuildDashboardData::test_generated_at_iso8601 PASSED [ 73%]
tests/test_dashboard.py::TestBuildDashboardData::test_json_serializable PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_is_valid_html PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_doctype PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_html_lang PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_charset PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_viewport PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_title_placeholder PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_style_tag PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_has_custom_properties PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_has_dark_mode_media_query PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_light_colors_match_rfc PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_dark_colors_match_rfc PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_has_grid_layout PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_css_has_responsive_breakpoint PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_header_structure PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_sidebar_structure PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_main_content_areas PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_mermaid_container PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_javascript PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_data_placeholder PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_mermaid_cdn PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_js_has_render_functions PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_js_has_status_icons PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_js_has_tab_switching PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_js_has_scroll_spy PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_required_placeholders PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_status_icon_classes PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_phase_card_styles PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_step_detail_styles PASSED [ 73%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_has_progress_bar_styles PASSED [ 74%]
tests/test_dashboard.py::TestDashboardHtmlTemplate::test_template_closes_all_tags PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_generates_valid_html PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_injects_project_name PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_injects_json_data PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_progress_calculation PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_escapes_script_tags_in_data PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_handles_special_characters PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_handles_unicode PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_empty_plan PASSED   [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_summary_counts_injected PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_output_is_single_html_file PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_parses_as_valid_html PASSED [ 74%]
tests/test_dashboard.py::TestGenerateDashboard::test_build_dashboard_data_structure PASSED [ 74%]
tests/test_dashboard.py::TestDashboardCLI::test_dashboard_creates_file PASSED [ 74%]
tests/test_dashboard.py::TestDashboardCLI::test_dashboard_custom_output PASSED [ 74%]
tests/test_dashboard.py::TestDashboardCLI::test_dashboard_default_output PASSED [ 74%]
tests/test_dashboard.py::TestDashboardCLI::test_dashboard_with_real_plan PASSED [ 74%]
tests/test_dashboard.py::TestDashboardCLI::test_html_tags_in_content_are_escaped PASSED [ 74%]
tests/test_dashboard_ui.py::test_page_loads_without_errors[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_header_shows_project_name[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_header_shows_progress_bar[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_header_shows_stat_pills[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_sidebar_lists_all_phases[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_title_contains_project_name[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_done_phases_collapsed_by_default[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_active_phases_expanded_by_default[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_click_header_toggles_collapse[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_click_step_row_opens_detail[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_click_step_row_again_closes_detail[chromium] SKIPPED [ 74%]
tests/test_dashboard_ui.py::test_dependencies_cell_uses_table_cell_layout[chromium] SKIPPED [ 75%]
tests/test_dashboard_ui.py::test_step_detail_shows_rejection_history[chromium] SKIPPED [ 75%]
tests/test_dashboard_ui.py::test_long_description_no_overflow[chromium] SKIPPED [ 75%]
tests/test_dashboard_ui.py::test_many_phases_sidebar_scrolls[chromium] SKIPPED [ 75%]
tests/test_decide.py::TestRunnerProvenance::test_running_task_requires_runner PASSED [ 75%]
tests/test_decide.py::TestRunnerProvenance::test_running_task_rejects_unknown_runner PASSED [ 75%]
tests/test_decide.py::TestRunnerProvenance::test_completed_result_requires_runner PASSED [ 75%]
tests/test_decide.py::TestTaskIdSemantics::test_claim_action_has_task_id_as_identity_only PASSED [ 75%]
tests/test_decide.py::TestTaskIdSemantics::test_reuse_action_has_reuse_token_not_task_id PASSED [ 75%]
tests/test_decide.py::TestTaskIdSemantics::test_reuse_runner_follows_completed_result_runner PASSED [ 75%]
tests/test_decide.py::TestTaskIdSemantics::test_reuse_runner_round_trips_through_advisor_state PASSED [ 75%]
tests/test_decide.py::TestCallerOwnedState::test_decide_accepts_advisor_state_dict PASSED [ 75%]
tests/test_decide.py::TestCallerOwnedState::test_next_state_is_full_replacement_not_merge PASSED [ 75%]
tests/test_decide.py::TestCallerOwnedState::test_explicit_state_round_trip PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_output_has_status_not_continuation PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_output_has_reason_code_not_halt_reason PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_status_reason_code_mapping PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_status_at_capacity PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_repeated_failures_map_to_blocked_status PASSED [ 75%]
tests/test_decide.py::TestStatusReasonCode::test_pending_escalation_keeps_followup_call_blocked PASSED [ 75%]
tests/test_decide.py::TestPolicyMetadata::test_output_includes_policy_metadata PASSED [ 75%]
tests/test_decide.py::TestSessionFieldRemoved::test_action_has_no_session_field PASSED [ 75%]
tests/test_decide.py::TestShouldReuseSessionReturns::test_should_reuse_returns_reuse_token PASSED [ 75%]
tests/test_decide.py::TestShouldReuseSessionReturns::test_should_reuse_returns_none_for_stale_parent PASSED [ 75%]
tests/test_decide.py::test_decide_expected_red_fail_completes PASSED     [ 75%]
tests/test_decide.py::test_decide_must_green_fail_normal_failure_path PASSED [ 75%]
tests/test_decide.py::test_decide_successor_visible_after_simulated_completion PASSED [ 75%]
tests/test_decide_state_isolation.py::TestDecideStateIsolation::test_two_decide_state_instances_are_isolated PASSED [ 75%]
tests/test_decide_state_isolation.py::TestDecideStateIsolation::test_should_reuse_session_isolated_with_explicit_state PASSED [ 75%]
tests/test_decide_state_isolation.py::TestDecideStateIsolation::test_should_reuse_session_no_leak_between_explicit_states PASSED [ 75%]
tests/test_decide_state_isolation.py::TestLegacyFallbackIsolation::test_resolve_state_returns_isolated_state_when_none PASSED [ 75%]
tests/test_decide_state_isolation.py::TestLegacyFallbackIsolation::test_state_none_callers_do_not_mutate_legacy_state PASSED [ 76%]
tests/test_decide_state_isolation.py::TestLegacyFallbackIsolation::test_should_reuse_session_with_none_ignores_shared_legacy PASSED [ 76%]
tests/test_decide_state_isolation.py::TestDriverRuntimePathIsolation::test_decide_accepts_advisor_state_dict PASSED [ 76%]
tests/test_decide_state_isolation.py::TestDriverRuntimePathIsolation::test_failure_counts_isolated_in_advisor_state PASSED [ 76%]
tests/test_decide_state_isolation.py::TestDriverRuntimePathIsolation::test_should_reuse_session_uses_explicit_state_not_global PASSED [ 76%]
tests/test_decide_state_isolation.py::TestDecideIntegrationIsolation::test_two_decide_calls_with_separate_advisor_states_isolated PASSED [ 76%]
tests/test_decide_state_isolation.py::TestFailureEscalationWithExplicitState::test_failure_count_tracking_in_advisor_state PASSED [ 76%]
tests/test_decide_state_isolation.py::TestRunnerProvenanceGaps::test_completed_result_requires_runner PASSED [ 76%]
tests/test_decide_state_isolation.py::TestRunnerProvenanceGaps::test_completed_result_missing_runner_rejected PASSED [ 76%]
tests/test_decide_state_isolation.py::TestRunnerProvenanceGaps::test_running_task_requires_runner PASSED [ 76%]
tests/test_decide_state_isolation.py::TestRunnerProvenanceGaps::test_running_task_rejects_unknown_runner PASSED [ 76%]
tests/test_diff.py::TestDiffPlans::test_no_changes PASSED                [ 76%]
tests/test_diff.py::TestDiffPlans::test_step_status_change PASSED        [ 76%]
tests/test_diff.py::TestDiffPlans::test_phase_status_change PASSED       [ 76%]
tests/test_diff.py::TestDiffPlans::test_phase_added PASSED               [ 76%]
tests/test_diff.py::TestDiffPlans::test_phase_removed PASSED             [ 76%]
tests/test_diff.py::TestDiffPlans::test_step_added PASSED                [ 76%]
tests/test_diff.py::TestDiffPlans::test_step_removed PASSED              [ 76%]
tests/test_diff.py::TestDiffPlans::test_step_name_modified PASSED        [ 76%]
tests/test_diff.py::TestDiffPlans::test_step_description_modified PASSED [ 76%]
tests/test_diff.py::TestDiffPlans::test_empty_to_populated PASSED        [ 76%]
tests/test_diff.py::TestDiffPlans::test_populated_to_empty PASSED        [ 76%]
tests/test_diff.py::TestDiffPlans::test_has_changes_property PASSED      [ 76%]
tests/test_diff.py::TestDiffPlans::test_status_change_takes_precedence_over_modification PASSED [ 76%]
tests/test_duplicate_id_migration_contract.py::test_contract_has_required_sections PASSED [ 76%]
tests/test_duplicate_id_migration_contract.py::test_contract_pins_claimed_step_and_alias_policy PASSED [ 76%]
tests/test_duplicate_id_migration_contract.py::test_dry_run_schema_includes_mapping_and_follow_up_fields PASSED [ 76%]
tests/test_duplicate_id_migration_contract.py::test_retry_contract_has_no_auto_retry_in_phase_b PASSED [ 76%]
tests/test_duplicate_id_migration_dag.py::TestDAGPreservation::test_rewrite_algorithm_preserves_edge_cardinality PASSED [ 76%]
tests/test_duplicate_id_migration_dag.py::TestDAGPreservation::test_rewrite_algorithm_preserves_topological_order PASSED [ 76%]
tests/test_duplicate_id_migration_dag.py::TestDAGPreservation::test_rewrite_algorithm_no_new_cycles PASSED [ 76%]
tests/test_duplicate_id_migration_dag.py::TestDAGPreservation::test_no_cross_phase_edges_created PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestRenameAlgorithm::test_deterministic_rename PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestRenameAlgorithm::test_injective_mapping PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestRenameAlgorithm::test_stable_dry_run PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestRenameAlgorithm::test_canonical_keeps_original_id PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestRenameAlgorithm::test_noncanonical_uses_phase_prefix PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestDryRunSchema::test_dry_run_required_fields PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestDryRunSchema::test_run_mode_is_dry_run PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestDryRunSchema::test_rename_map_format PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestDryRunSchema::test_depends_on_rewrites_format PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestDryRunSchema::test_claimed_step_conflicts_format PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestApplyMode::test_claimed_step_blocked PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestApplyMode::test_claimed_step_reports_conflict PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestApplyMode::test_idempotent_when_no_duplicates PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestApplyMode::test_idempotent_after_success PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestApplyMode::test_no_persisted_state_on_interruption PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestHalfAutomaticRetry::test_auto_migrate_flag_is_unavailable PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestHalfAutomaticRetry::test_without_migration_apply_fails_with_guidance PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestHalfAutomaticRetry::test_auto_migrate_is_explicitly_unsupported PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestHalfAutomaticRetry::test_max_zero_retry PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestHalfAutomaticRetry::test_no_additional_auto_retry PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestMigrationEvidence::test_evidence_required_fields PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestMigrationEvidence::test_evidence_run_mode_includes_apply PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestMigrationEvidence::test_evidence_retry_format PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestCompatibilityPolicy::test_old_id_aliases_not_supported PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestCompatibilityPolicy::test_old_ids_visible_in_mapping PASSED [ 77%]
tests/test_duplicate_id_migration_dag.py::TestCompatibilityPolicy::test_old_id_targeting_fails_with_guidance PASSED [ 77%]
tests/test_duplicate_id_migration_engine.py::test_dry_run_produces_deterministic_duplicate_map_and_dep_rewrites PASSED [ 77%]
tests/test_duplicate_id_migration_engine.py::test_apply_blocks_when_duplicate_occurrence_is_claimed_in_plan PASSED [ 77%]
tests/test_duplicate_id_migration_engine.py::test_dry_run_reports_claim_conflicts_from_claims_store PASSED [ 77%]
tests/test_duplicate_id_migration_engine.py::test_apply_uses_cas_and_does_not_partial_write_on_conflict PASSED [ 78%]
tests/test_duplicate_id_migration_engine.py::test_apply_is_idempotent_after_successful_migration PASSED [ 78%]
tests/test_extended_runner_protocol.py::test_execution_request_contract_is_current_dispatch_input PASSED [ 78%]
tests/test_gemini_parser_behavior.py::test_runtime_snapshot_contract_is_available PASSED [ 78%]
tests/test_gemini_resume_behavior.py::test_default_dispatch_role_is_stable PASSED [ 78%]
tests/test_init_gitattributes.py::test_init_creates_gitattributes FAILED [ 78%]
tests/test_init_gitattributes.py::test_init_idempotent_gitattributes FAILED [ 78%]
tests/test_init_gitattributes.py::test_init_gitattributes_preserves_existing FAILED [ 78%]
tests/test_io.py::TestLoadPlanDefinition::test_load_valid PASSED         [ 78%]
tests/test_io.py::TestLoadPlanDefinition::test_load_nonexistent PASSED   [ 78%]
tests/test_io.py::TestLoadPlanDefinition::test_load_invalid_yaml PASSED  [ 78%]
tests/test_io.py::TestLoadPlanDefinition::test_load_non_mapping PASSED   [ 78%]
tests/test_io.py::TestSavePlan::test_save_creates_file PASSED            [ 78%]
tests/test_io.py::TestSavePlan::test_save_creates_parent_dirs PASSED     [ 78%]
tests/test_io.py::TestSavePlan::test_round_trip PASSED                   [ 78%]
tests/test_io.py::TestSavePlan::test_save_with_commit_message_creates_git_commit PASSED [ 78%]
tests/test_io.py::TestSavePlan::test_git_commit_plan_retries_once_on_index_lock_contention PASSED [ 78%]
tests/test_io.py::TestAffinityCleanup::test_clean_dict_omits_affinity_defaults_from_models PASSED [ 78%]
tests/test_io.py::TestAffinityCleanup::test_clean_dict_preserves_non_default_affinity_values PASSED [ 78%]
tests/test_io.py::TestAffinityCleanup::test_clean_dict_avoids_hardcoded_affinity_field_name_literals PASSED [ 78%]
tests/test_io.py::TestIsolationCleanup::test_clean_dict_omits_default_isolation_value PASSED [ 78%]
tests/test_io.py::TestIsolationCleanup::test_clean_dict_preserves_non_default_isolation_values PASSED [ 78%]
tests/test_io.py::TestCAS::test_cas_success PASSED                       [ 78%]
tests/test_io.py::TestCAS::test_cas_conflict PASSED                      [ 78%]
tests/test_io.py::TestCAS::test_cas_no_hash_skips_check PASSED           [ 78%]
tests/test_io.py::TestCAS::test_cas_new_file_no_conflict PASSED          [ 78%]
tests/test_io.py::TestCAS::test_atomic_cas_and_write_are_serialized_under_plan_lock PASSED [ 78%]
tests/test_io.py::TestDefinitionBackup::test_backup_created_on_save PASSED [ 78%]
tests/test_io.py::TestDefinitionBackup::test_backup_matches_plan_content PASSED [ 78%]
tests/test_io.py::TestDefinitionBackup::test_backup_skipped_in_linked_worktree PASSED [ 78%]
tests/test_io.py::TestCorruptedBackupHandling::test_load_corrupted_yaml_backup PASSED [ 78%]
tests/test_io.py::TestCorruptedBackupHandling::test_load_invalid_plan_structure_backup PASSED [ 79%]
tests/test_io.py::TestClipboardRoundTrip::test_clipboard_round_trip PASSED [ 79%]
tests/test_io.py::TestClipboardRoundTrip::test_clipboard_omitted_when_none PASSED [ 79%]
tests/test_io.py::TestLockStatusRoundtrip::test_pending_phase_with_unmet_dep_is_locked_after_roundtrip PASSED [ 79%]
tests/test_io.py::TestLockStatusRoundtrip::test_locked_phase_with_all_deps_done_is_unlocked_after_roundtrip PASSED [ 79%]
tests/test_io.py::TestBackupCrashSafety::test_backup_crash_safety_no_temp_file_left_behind PASSED [ 79%]
tests/test_io.py::TestAnimaIncidentReproducer::test_split_brain_claim_rejects_but_plan_shows_pending PASSED [ 79%]
tests/test_io.py::TestAnimaIncidentReproducer::test_concurrent_claim_vs_plan_write_race PASSED [ 79%]
tests/test_lifecycle_imports.py::test_lifecycle_functions_importable_from_core_and_lifecycle PASSED [ 79%]
tests/test_logic.py::TestGetNextSteps::test_returns_unblocked_steps PASSED [ 79%]
tests/test_logic.py::TestGetNextSteps::test_locked_phase_excluded PASSED [ 79%]
tests/test_logic.py::TestGetNextSteps::test_rejected_steps_first PASSED  [ 79%]
tests/test_logic.py::TestGetNextSteps::test_step_deps_satisfied PASSED   [ 79%]
tests/test_logic.py::TestGetNextSteps::test_phase_auto_unlock PASSED     [ 79%]
tests/test_logic.py::TestGetNextSteps::test_get_next_steps_does_not_mutate_phase_status PASSED [ 79%]
tests/test_logic.py::TestGetNextSteps::test_empty_plan PASSED            [ 79%]
tests/test_logic.py::TestAutoUnlockPhases::test_unlocks_eligible_phases PASSED [ 79%]
tests/test_logic.py::TestAutoUnlockPhases::test_does_not_unlock_when_deps_unmet PASSED [ 79%]
tests/test_logic.py::TestAutoUnlockPhases::test_idempotent PASSED        [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_pending PASSED            [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_updates_phase_to_in_progress PASSED [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_rejected_step PASSED      [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_already_claimed_fails PASSED [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_done_step_fails PASSED    [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_blocked_step_fails PASSED [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_in_locked_phase_fails PASSED [ 79%]
tests/test_logic.py::TestClaimStep::test_claim_nonexistent_fails PASSED  [ 79%]
tests/test_logic.py::TestCompleteStep::test_complete_claimed PASSED      [ 79%]
tests/test_logic.py::TestCompleteStep::test_complete_unclaimed_fails PASSED [ 79%]
tests/test_logic.py::TestCompleteStep::test_complete_all_steps_completes_phase PASSED [ 79%]
tests/test_logic.py::TestCompleteStep::test_complete_cascades_unlock_to_downstream PASSED [ 79%]
tests/test_logic.py::TestDeferStep::test_defer_claimed PASSED            [ 80%]
tests/test_logic.py::TestDeferStep::test_defer_pending_fails PASSED      [ 80%]
tests/test_logic.py::TestRejectStep::test_reject_done PASSED             [ 80%]
tests/test_logic.py::TestRejectStep::test_reject_pending_fails PASSED    [ 80%]
tests/test_logic.py::TestRejectStep::test_reject_reverts_phase_done PASSED [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_pending PASSED              [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_claimed PASSED              [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_done_fails PASSED           [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_invalid_reason PASSED       [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_all_valid_reasons PASSED    [ 80%]
tests/test_logic.py::TestSkipStep::test_skip_all_completes_phase PASSED  [ 80%]
tests/test_logic.py::TestGetClaimedSteps::test_returns_claimed_by_agent PASSED [ 80%]
tests/test_logic.py::TestGetClaimedSteps::test_returns_all_claimed_when_no_agent PASSED [ 80%]
tests/test_logic.py::TestGetClaimedSteps::test_empty_when_none_claimed PASSED [ 80%]
tests/test_logic.py::TestGetClaimedSteps::test_includes_phase_id PASSED  [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_all_pending PASSED         [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_defers_claimed_first PASSED [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_preserves_done_steps PASSED [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_mixed_states PASSED        [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_cascades_unlock PASSED     [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_locked_phase_with_steps_fails PASSED [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_locked_phase_with_steps_force_allowed PASSED [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_empty_locked_phase_allowed PASSED [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_done_phase_fails PASSED    [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_invalid_reason PASSED      [ 80%]
tests/test_logic.py::TestSkipPhase::test_skip_phase_not_found PASSED     [ 80%]
tests/test_logic.py::TestCompletePhase::test_complete_fails_if_any_step_pending PASSED [ 80%]
tests/test_logic.py::TestCompletePhase::test_complete_sets_phase_done_and_unlocks_downstream PASSED [ 80%]
tests/test_logic.py::TestCompletePhase::test_complete_requires_deps_done PASSED [ 80%]
tests/test_logic.py::TestCompletePhase::test_complete_locked_phase_when_deps_done PASSED [ 80%]
tests/test_logic.py::TestCompletePhase::test_complete_empty_evidence_rejected PASSED [ 80%]
tests/test_logic.py::TestSearchPlan::test_basic_substring_match PASSED   [ 81%]
tests/test_logic.py::TestSearchPlan::test_case_insensitive PASSED        [ 81%]
tests/test_logic.py::TestSearchPlan::test_phase_filter PASSED            [ 81%]
tests/test_logic.py::TestSearchPlan::test_regex_mode PASSED              [ 81%]
tests/test_logic.py::TestSearchPlan::test_invalid_regex PASSED           [ 81%]
tests/test_logic.py::TestSearchPlan::test_no_matches PASSED              [ 81%]
tests/test_logic.py::TestSearchPlan::test_phase_level_match PASSED       [ 81%]
tests/test_logic.py::TestSearchPlan::test_match_in_gate PASSED           [ 81%]
tests/test_logic.py::TestSearchPlan::test_match_snippet_extraction PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_basic_write PASSED         [ 81%]
tests/test_logic.py::TestClipboardWrite::test_overwrite_existing PASSED  [ 81%]
tests/test_logic.py::TestClipboardWrite::test_summary_truncation PASSED  [ 81%]
tests/test_logic.py::TestClipboardWrite::test_content_limit_exceeded PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_empty_content_rejected PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_whitespace_only_content_rejected PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_empty_author_rejected PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_whitespace_only_author_rejected PASSED [ 81%]
tests/test_logic.py::TestClipboardWrite::test_custom_ttl PASSED          [ 81%]
tests/test_logic.py::TestClipboardWrite::test_default_ttl_is_24h PASSED  [ 81%]
tests/test_logic.py::TestClipboardRead::test_read_basic PASSED           [ 81%]
tests/test_logic.py::TestClipboardRead::test_read_empty_clipboard PASSED [ 81%]
tests/test_logic.py::TestClipboardRead::test_read_expired_clipboard PASSED [ 81%]
tests/test_logic.py::TestClipboardClear::test_clear_basic PASSED         [ 81%]
tests/test_logic.py::TestClipboardClear::test_clear_already_empty PASSED [ 81%]
tests/test_logic.py::TestClipboardExpiry::test_not_expired PASSED        [ 81%]
tests/test_logic.py::TestClipboardExpiry::test_expired PASSED            [ 81%]
tests/test_logic.py::TestClipboardExpiry::test_expiry_boundary PASSED    [ 81%]
tests/test_logic.py::TestAffinityClaim::test_claim_no_agent_field_no_check PASSED [ 81%]
tests/test_logic.py::TestAffinityClaim::test_claim_agent_matches_no_warning PASSED [ 81%]
tests/test_logic.py::TestAffinityClaim::test_claim_suggested_mismatch_warns PASSED [ 81%]
tests/test_logic.py::TestAffinityClaim::test_claim_exclusive_mismatch_rejects PASSED [ 82%]
tests/test_logic.py::TestAffinityClaim::test_claim_exclusive_mismatch_force_allows PASSED [ 82%]
tests/test_logic.py::TestAffinityClaim::test_claim_uses_plan_default_affinity PASSED [ 82%]
tests/test_logic.py::TestAffinityClaim::test_claim_step_affinity_overrides_plan_default PASSED [ 82%]
tests/test_logic.py::TestAffinityClaim::test_claim_exclusive_matches_allows PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule1_done_phase_not_changed PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule1_done_phase_without_deps_not_changed PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule1_done_phase_with_done_deps_stays_done PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule2_in_progress_with_unmet_deps_not_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule2_in_progress_with_met_deps_not_changed PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule3_pending_with_unmet_dep_becomes_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule3_pending_with_in_progress_dep_becomes_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule3_pending_with_all_deps_done_not_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule3_pending_without_deps_not_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule3_partially_unmet_deps_becomes_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule4_locked_with_all_deps_done_becomes_pending PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule4_locked_with_unmet_deps_stays_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule4_locked_with_multiple_deps_all_done_becomes_pending PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_rule4_locked_with_one_unmet_of_multiple_deps_stays_locked PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_returns_empty_list_when_no_changes PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_returns_all_changed_phase_ids_in_plan_order PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_idempotent_on_already_consistent_plan PASSED [ 82%]
tests/test_logic.py::TestRecalcLockStatus::test_circular_dep_does_not_loop PASSED [ 82%]
tests/test_manual_yaml_edit.py::TestManualYamlEditLockCorrection::test_pending_with_unmet_dep_becomes_locked PASSED [ 82%]
tests/test_manual_yaml_edit.py::TestManualYamlEditLockCorrection::test_locked_with_done_dep_becomes_pending PASSED [ 82%]
tests/test_manual_yaml_edit.py::TestManualYamlEditLockCorrection::test_done_phase_not_regressed PASSED [ 82%]
tests/test_manual_yaml_edit.py::TestRecalcLockDryRun::test_dry_run_does_not_modify_disk PASSED [ 82%]
tests/test_manual_yaml_edit.py::TestRecalcLockDryRun::test_dry_run_reports_what_would_change PASSED [ 82%]
tests/test_mcp.py::TestVectlStatus::test_shows_phase_overview PASSED     [ 82%]
tests/test_mcp.py::TestVectlStatus::test_shows_next_available PASSED     [ 82%]
tests/test_mcp.py::TestVectlStatus::test_no_agent_no_mine_section PASSED [ 82%]
tests/test_mcp.py::TestVectlStatus::test_agent_shows_mine_section_empty PASSED [ 83%]
tests/test_mcp.py::TestVectlStatus::test_agent_shows_claimed_steps PASSED [ 83%]
tests/test_mcp.py::TestVectlStatus::test_status_shows_duplicate_step_id_diagnostics PASSED [ 83%]
tests/test_mcp.py::TestVectlStatus::test_status_next_steps_shows_correct_phase_for_duplicate_ids PASSED [ 83%]
tests/test_mcp.py::TestVectlValidate::test_validate_reports_duplicate_step_id_error PASSED [ 83%]
tests/test_mcp.py::TestVectlReviewDuplicateBlocking::test_review_gate_check_is_blocked_when_plan_has_duplicate_step_ids PASSED [ 83%]
tests/test_mcp.py::TestVectlMigrateStepId::test_dry_run_returns_report_and_evidence PASSED [ 83%]
tests/test_mcp.py::TestVectlMigrateStepId::test_apply_returns_report_and_persists_migration_then_noops PASSED [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_step PASSED                  [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_phase PASSED                 [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_not_found PASSED             [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_phase_gate PASSED            [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_step_deps PASSED             [ 83%]
tests/test_mcp.py::TestVectlShow::test_show_step_duplicate_id_recommendation PASSED [ 83%]
tests/test_mcp.py::TestVectlClaim::test_claim_specific_step PASSED       [ 83%]
tests/test_mcp.py::TestVectlClaim::test_auto_claim PASSED                [ 83%]
tests/test_mcp.py::TestVectlClaim::test_claim_error_on_dep_unmet PASSED  [ 83%]
tests/test_mcp.py::TestVectlClaim::test_claim_shows_affordance PASSED    [ 83%]
tests/test_mcp.py::TestVectlClaim::test_claim_reject_with_existing_claim_returns_structured_error PASSED [ 83%]
tests/test_mcp.py::TestVectlClaim::test_claim_blocks_ambiguous_duplicate_target_without_opt_in PASSED [ 83%]
tests/test_mcp.py::TestRepairClaimsMcp::test_repair_claims_dry_run_preview PASSED [ 83%]
tests/test_mcp.py::TestRepairClaimsMcp::test_repair_claims_step_scope_preserves_unrelated_entries PASSED [ 83%]
tests/test_mcp.py::TestRepairClaimsMcp::test_repair_claims_output_shape_and_policy PASSED [ 83%]
tests/test_mcp.py::TestVectlComplete::test_complete_claimed_step PASSED  [ 83%]
tests/test_mcp.py::TestVectlComplete::test_complete_shows_next_available PASSED [ 83%]
tests/test_mcp.py::TestVectlComplete::test_complete_unclaimed_error PASSED [ 83%]
tests/test_mcp.py::TestVectlComplete::test_complete_last_step_unlocks_next_phase PASSED [ 83%]
tests/test_mcp.py::TestVectlLifecycle::test_defer_claimed_step PASSED    [ 83%]
tests/test_mcp.py::TestVectlLifecycle::test_claim_and_defer_use_claims_store PASSED [ 83%]
tests/test_mcp.py::TestVectlLifecycle::test_reject_done_step PASSED      [ 83%]
tests/test_mcp.py::TestVectlLifecycle::test_reject_requires_reason PASSED [ 83%]
tests/test_mcp.py::TestVectlLifecycle::test_skip_step PASSED             [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_skip_requires_reason PASSED  [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_skip_phase PASSED            [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_skip_locked_phase_with_force PASSED [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_complete_phase_historical PASSED [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_unknown_action PASSED        [ 84%]
tests/test_mcp.py::TestVectlLifecycle::test_lifecycle_blocks_ambiguous_duplicate_target_without_opt_in PASSED [ 84%]
tests/test_mcp.py::TestVectlSearch::test_search_by_name PASSED           [ 84%]
tests/test_mcp.py::TestVectlSearch::test_search_no_matches PASSED        [ 84%]
tests/test_mcp.py::TestVectlSearch::test_search_restricted_to_phase PASSED [ 84%]
tests/test_mcp.py::TestVectlSearch::test_search_regex PASSED             [ 84%]
tests/test_mcp.py::TestVectlSearch::test_search_invalid_regex PASSED     [ 84%]
tests/test_mcp.py::TestVectlMutate::test_add_step PASSED                 [ 84%]
tests/test_mcp.py::TestVectlMutate::test_add_step_requires_phase_and_name PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_add_step_rejects_duplicate_id_across_plan PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_step PASSED                [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_step_evidence_template PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_step_refs PASSED           [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_step_requires_id PASSED    [ 84%]
tests/test_mcp.py::TestVectlMutate::test_remove_step PASSED              [ 84%]
tests/test_mcp.py::TestVectlMutate::test_remove_step_requires_id PASSED  [ 84%]
tests/test_mcp.py::TestVectlMutate::test_move_step PASSED                [ 84%]
tests/test_mcp.py::TestVectlMutate::test_move_step_requires_target PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_add_phase PASSED                [ 84%]
tests/test_mcp.py::TestVectlMutate::test_add_phase_requires_name PASSED  [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_phase PASSED               [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_phase_requires_id PASSED   [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_phase_depends_on PASSED    [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_phase_depends_on_clear PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_edit_plan_project_guidance PASSED [ 84%]
tests/test_mcp.py::TestVectlMutate::test_unknown_action PASSED           [ 84%]
tests/test_mcp.py::TestVectlMutate::test_mutate_shows_search_hint PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_basic PASSED          [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_requires_phase_id PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_requires_steps PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_empty_list PASSED     [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_dependencies PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_full_dep_refs PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_done_status PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_done_requires_evidence PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_skipped_status PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_skip_requires_reason PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_refs PASSED      [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_agent PASSED     [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_verification PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_verify_alias PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_with_desc_alias PASSED [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_invalid_phase PASSED  [ 85%]
tests/test_mcp.py::TestVectlMutate::test_add_steps_missing_name PASSED   [ 85%]
tests/test_mcp.py::TestVectlMutateLinkedWorktreeGuard::test_mcp_mutate_blocked_in_linked_worktree PASSED [ 85%]
tests/test_mcp.py::TestVectlMutateLinkedWorktreeGuard::test_mcp_mutate_allowed_in_main_worktree PASSED [ 85%]
tests/test_mcp.py::TestVectlMutateLinkedWorktreeGuard::test_mcp_mutate_allowed_with_env_override PASSED [ 85%]
tests/test_mcp.py::TestVectlMutateLinkedWorktreeGuard::test_mcp_mutate_blocked_when_main_root_unresolved PASSED [ 85%]
tests/test_mcp.py::TestVectlMcpLinkedWorktreeImplicitResolution::test_mcp_status_resolves_to_main_worktree_plan_without_explicit_path PASSED [ 85%]
tests/test_mcp.py::TestVectlMcpLinkedWorktreeImplicitResolution::test_mcp_claim_resolves_to_main_worktree_plan_without_explicit_path PASSED [ 85%]
tests/test_mcp.py::TestVectlMcpLinkedWorktreeImplicitResolution::test_mcp_mutate_with_explicit_plan_allowed_in_linked_worktree PASSED [ 85%]
tests/test_mcp.py::TestVectlMcpLinkedWorktreeImplicitResolution::test_mcp_status_fails_closed_on_malformed_worktree_without_walkup PASSED [ 85%]
tests/test_mcp.py::TestWorkflow::test_full_lifecycle PASSED              [ 85%]
tests/test_mcp.py::TestWorkflow::test_defer_and_reclaim PASSED           [ 85%]
tests/test_mcp.py::TestAffinity::test_claim_no_agent_no_check PASSED     [ 85%]
tests/test_mcp.py::TestAffinity::test_claim_suggested_warns PASSED       [ 85%]
tests/test_mcp.py::TestAffinity::test_claim_exclusive_rejects PASSED     [ 86%]
tests/test_mcp.py::TestAffinity::test_claim_exclusive_force_allows PASSED [ 86%]
tests/test_mcp.py::TestAffinity::test_claim_exclusive_matching_agent_allows PASSED [ 86%]
tests/test_mcp.py::TestAffinity::test_claim_guidance_shows_affinity PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_returns_all_layers PASSED       [ 86%]
tests/test_mcp.py::TestVectlReview::test_l1_valid_plan PASSED            [ 86%]
tests/test_mcp.py::TestVectlReview::test_l2_shows_progress PASSED        [ 86%]
tests/test_mcp.py::TestVectlReview::test_l3_shows_active_phases PASSED   [ 86%]
tests/test_mcp.py::TestVectlReview::test_l3_shows_step_details PASSED    [ 86%]
tests/test_mcp.py::TestVectlReview::test_l4_shows_ref_coverage PASSED    [ 86%]
tests/test_mcp.py::TestVectlReview::test_l4_no_refs PASSED               [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_check_included_when_phase_id PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_check_complete_phase PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_check_shows_criterion PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_check_shows_downstream PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_check_invalid_phase PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_no_gate_check_without_phase_id PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_review_shows_duplicate_step_id_diagnostics PASSED [ 86%]
tests/test_mcp.py::TestVectlReview::test_gate_script_warning PASSED      [ 86%]
tests/test_mcp.py::TestVectlReview::test_validation_error_plan PASSED    [ 86%]
tests/test_mcp.py::TestVectlGuide::test_all_topics_returned_by_default PASSED [ 86%]
tests/test_mcp.py::TestVectlGuide::test_specific_topic_startup PASSED    [ 86%]
tests/test_mcp.py::TestVectlGuide::test_specific_topic_stuck PASSED      [ 86%]
tests/test_mcp.py::TestVectlGuide::test_specific_topic_review PASSED     [ 86%]
tests/test_mcp.py::TestVectlGuide::test_specific_topic_planning PASSED   [ 86%]
tests/test_mcp.py::TestVectlGuide::test_specific_topic_migration PASSED  [ 86%]
tests/test_mcp.py::TestVectlGuide::test_invalid_topic_returns_error PASSED [ 86%]
tests/test_mcp.py::TestVectlGuide::test_all_topics_has_separator PASSED  [ 86%]
tests/test_mcp.py::TestVectlGuide::test_no_plan_file_needed PASSED       [ 86%]
tests/test_mcp.py::TestVectlDag::test_phase_dag_default PASSED           [ 86%]
tests/test_mcp.py::TestVectlDag::test_phase_dag_edges PASSED             [ 86%]
tests/test_mcp.py::TestVectlDag::test_step_dag PASSED                    [ 87%]
tests/test_mcp.py::TestVectlDag::test_step_dag_phase_not_found PASSED    [ 87%]
tests/test_mcp.py::TestVectlDag::test_drill_hint_in_phase_dag PASSED     [ 87%]
tests/test_mcp.py::TestVectlDag::test_dag_includes_duplicate_step_id_warning_comments PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardWrite::test_write_basic PASSED      [ 87%]
tests/test_mcp.py::TestVectlClipboardWrite::test_write_overwrites PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardWrite::test_write_empty_author_rejected PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardWrite::test_write_empty_content_rejected PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardRead::test_read_basic PASSED        [ 87%]
tests/test_mcp.py::TestVectlClipboardRead::test_read_empty PASSED        [ 87%]
tests/test_mcp.py::TestVectlClipboardClear::test_clear_basic PASSED      [ 87%]
tests/test_mcp.py::TestVectlClipboardClear::test_clear_already_empty PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardInvalidAction::test_invalid_action PASSED [ 87%]
tests/test_mcp.py::TestVectlClipboardCAS::test_cas_conflict_message PASSED [ 87%]
tests/test_mcp.py::TestMcpUnifiedPlanPath::test_claim_persists_to_plan_yaml_only PASSED [ 87%]
tests/test_mcp.py::TestMcpUnifiedPlanPath::test_load_returns_definition_plan_hash_only PASSED [ 87%]
tests/test_mcp.py::TestMcpUnifiedPlanPath::test_load_ignores_state_json_until_explicit_migration PASSED [ 87%]
tests/test_mcp.py::TestMcpUnifiedPlanPath::test_save_plan_cas_conflict_raises_plan_error PASSED [ 87%]
tests/test_mcp.py::TestMcpUnifiedPlanPath::test_read_only_tools_do_not_write_state_json PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_creates_plan_and_agents_md PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_refuses_existing_plan PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_custom_plan_path PASSED      [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_claude_md_with_claude_dir PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_explicit_agents_target PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_appends_to_existing_agents_md PASSED [ 87%]
tests/test_mcp.py::TestVectlInit::test_init_idempotent_agents_md PASSED  [ 87%]
tests/test_mcp.py::TestVectlCheck::test_toggle_checklist_item PASSED     [ 87%]
tests/test_mcp.py::TestVectlCheck::test_toggle_checked_to_unchecked PASSED [ 87%]
tests/test_mcp.py::TestVectlCheck::test_add_checklist_item PASSED        [ 87%]
tests/test_mcp.py::TestVectlCheck::test_both_toggle_and_add PASSED       [ 87%]
tests/test_mcp.py::TestVectlCheck::test_error_no_keyword_or_add PASSED   [ 87%]
tests/test_mcp.py::TestVectlCheck::test_error_no_match PASSED            [ 88%]
tests/test_mcp.py::TestVectlCheck::test_error_ambiguous_match PASSED     [ 88%]
tests/test_mcp.py::TestVectlCheck::test_error_step_not_found PASSED      [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_basic PASSED             [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_includes_phase_table PASSED [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_includes_step_details PASSED [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_phase_filter PASSED      [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_full_mode PASSED         [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_invalid_phase PASSED     [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_shows_gate PASSED        [ 88%]
tests/test_mcp.py::TestVectlRender::test_render_shows_context PASSED     [ 88%]
tests/test_mcp.py::TestVectlRecover::test_recover_restores_without_prompt PASSED [ 88%]
tests/test_mcp.py::TestVectlRecover::test_recover_corrupted_backup_returns_error PASSED [ 88%]
tests/test_mcp.py::TestVectlRecover::test_recover_permission_error_returns_error PASSED [ 88%]
tests/test_mcp.py::TestVectlDecide::test_vectl_decide_returns_structured_output PASSED [ 88%]
tests/test_mcp.py::TestVectlDecide::test_vectl_decide_empty_plan PASSED  [ 88%]
tests/test_mcp.py::TestVectlDecide::test_vectl_decide_with_running_tasks PASSED [ 88%]
tests/test_mcp.py::TestVectlDecide::test_vectl_decide_repeated_failures_return_blocked_status PASSED [ 88%]
tests/test_mcp.py::TestDeterministicCheckMCP::test_legacy_keyword_toggle_remains_covered PASSED [ 88%]
tests/test_mcp.py::TestDeterministicCheckMCP::test_mcp_schema_backward_compatible_and_deterministic_fields PASSED [ 88%]
tests/test_mcp.py::TestDeterministicCheckMCP::test_mcp_stale_revision_rejected_without_mutation PASSED [ 88%]
tests/test_mcp.py::TestDeterministicCheckMCP::test_mcp_invalid_selector_mode_fails PASSED [ 88%]
tests/test_merge_driver.py::test_merge_non_conflicting_step_changes PASSED [ 88%]
tests/test_merge_driver.py::test_merge_same_step_conflict_returns_1_and_marks_ours PASSED [ 88%]
tests/test_merge_driver.py::test_merge_non_conflicting_phase_additions PASSED [ 88%]
tests/test_merge_driver.py::test_merge_non_conflicting_step_additions PASSED [ 88%]
tests/test_merge_driver.py::test_merge_non_conflicting_property_changes PASSED [ 88%]
tests/test_merge_driver.py::test_merge_same_property_conflict PASSED     [ 88%]
tests/test_merge_driver.py::test_merge_mixed_step_changes_conflict_when_any_field_conflicts PASSED [ 88%]
tests/test_merge_driver.py::test_merge_phase_conflict PASSED             [ 88%]
tests/test_merge_driver.py::test_merge_step_deleted_in_theirs PASSED     [ 89%]
tests/test_merge_driver.py::test_merge_step_deleted_in_ours PASSED       [ 89%]
tests/test_merge_driver.py::test_merge_step_moved_between_phases PASSED  [ 89%]
tests/test_merge_driver.py::test_merge_step_move_and_status_update PASSED [ 89%]
tests/test_merge_driver.py::test_merge_step_status_update_and_move PASSED [ 89%]
tests/test_merge_driver.py::test_merge_identical_changes_no_conflict PASSED [ 89%]
tests/test_migration.py::test_migrate_normal PASSED                      [ 89%]
tests/test_migration.py::test_migrate_orphan_steps PASSED                [ 89%]
tests/test_migration.py::test_migrate_orphan_phases PASSED               [ 89%]
tests/test_migration.py::test_migrate_plan_has_inline_status_state_wins PASSED [ 89%]
tests/test_migration.py::test_migrate_warns_on_inline_step_value_overwrite PASSED [ 89%]
tests/test_migration.py::test_migrate_empty_state PASSED                 [ 89%]
tests/test_migration.py::test_migrate_already_migrated PASSED            [ 89%]
tests/test_migration.py::test_migrate_no_state_file PASSED               [ 89%]
tests/test_migration.py::test_migrate_idempotent PASSED                  [ 89%]
tests/test_migration.py::test_migrate_clipboard PASSED                   [ 89%]
tests/test_migration.py::test_migrate_creates_git_commit PASSED          [ 89%]
tests/test_models.py::TestStepStatus::test_enum_values PASSED            [ 89%]
tests/test_models.py::TestStepStatus::test_all_values PASSED             [ 89%]
tests/test_models.py::TestPhaseStatus::test_enum_values PASSED           [ 89%]
tests/test_models.py::TestPhaseStatus::test_all_values PASSED            [ 89%]
tests/test_models.py::TestRejectionEntry::test_basic PASSED              [ 89%]
tests/test_models.py::TestRejectionEntry::test_with_reviewer PASSED      [ 89%]
tests/test_models.py::TestStep::test_minimal PASSED                      [ 89%]
tests/test_models.py::TestStep::test_isolation_explicit_workspace PASSED [ 89%]
tests/test_models.py::TestStep::test_isolation_explicit_independent PASSED [ 89%]
tests/test_models.py::TestStep::test_skipped_requires_reason PASSED      [ 89%]
tests/test_models.py::TestStep::test_skipped_with_reason_ok PASSED       [ 89%]
tests/test_models.py::TestStep::test_rejected_requires_reason PASSED     [ 89%]
tests/test_models.py::TestStep::test_rejected_with_reason_ok PASSED      [ 89%]
tests/test_models.py::TestStep::test_claimed_requires_claimed_by PASSED  [ 89%]
tests/test_models.py::TestStep::test_claimed_with_claimed_by_ok PASSED   [ 90%]
tests/test_models.py::TestStep::test_full_step PASSED                    [ 90%]
tests/test_models.py::TestStep::test_done_at_default_none PASSED         [ 90%]
tests/test_models.py::TestStep::test_done_at_with_timestamp PASSED       [ 90%]
tests/test_models.py::TestStep::test_done_at_none_with_status_done PASSED [ 90%]
tests/test_models.py::TestPhase::test_minimal PASSED                     [ 90%]
tests/test_models.py::TestPhase::test_with_steps PASSED                  [ 90%]
tests/test_models.py::TestPhase::test_locked_phase PASSED                [ 90%]
tests/test_models.py::TestPlan::test_minimal PASSED                      [ 90%]
tests/test_models.py::TestPlan::test_find_step PASSED                    [ 90%]
tests/test_models.py::TestPlan::test_find_step_not_found PASSED          [ 90%]
tests/test_models.py::TestPlan::test_find_step_qualified_lookup_exact_match_priority PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_qualified_lookup_fallback PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_qualified_lookup_partition_behavior PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_qualified_lookup_empty_parts PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_qualified_lookup_with_exact_step_id_in_specific_phase PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_no_match_returns_none PASSED [ 90%]
tests/test_models.py::TestPlan::test_find_step_case_sensitivity PASSED   [ 90%]
tests/test_models.py::TestPlan::test_find_phase PASSED                   [ 90%]
tests/test_models.py::TestClipboard::test_minimal PASSED                 [ 90%]
tests/test_models.py::TestClipboard::test_empty_author_rejected PASSED   [ 90%]
tests/test_models.py::TestClipboard::test_whitespace_only_author_rejected PASSED [ 90%]
tests/test_models.py::TestClipboard::test_empty_content_rejected PASSED  [ 90%]
tests/test_models.py::TestClipboard::test_whitespace_only_content_rejected PASSED [ 90%]
tests/test_models.py::TestPlanClipboard::test_plan_without_clipboard PASSED [ 90%]
tests/test_models.py::TestPlanClipboard::test_plan_with_clipboard PASSED [ 90%]
tests/test_models.py::TestPlanClipboard::test_backward_compat_plan_without_clipboard_field PASSED [ 90%]
tests/test_models.py::TestAffinityMode::test_enum_values PASSED          [ 90%]
tests/test_models.py::TestAffinityMode::test_all_values PASSED           [ 90%]
tests/test_models.py::TestStepAffinity::test_step_affinity_none_default PASSED [ 90%]
tests/test_models.py::TestStepAffinity::test_step_affinity_exclusive PASSED [ 90%]
tests/test_models.py::TestStepAffinity::test_step_affinity_override_fields_default PASSED [ 91%]
tests/test_models.py::TestStepAffinity::test_step_affinity_override_set PASSED [ 91%]
tests/test_models.py::TestPlanAffinity::test_plan_default_affinity_suggested PASSED [ 91%]
tests/test_models.py::TestPlanAffinity::test_plan_default_affinity_exclusive PASSED [ 91%]
tests/test_models.py::TestAffinityBackwardCompat::test_existing_plan_without_affinity_loads PASSED [ 91%]
tests/test_models.py::TestPlanPlanId::test_default_none PASSED           [ 91%]
tests/test_models.py::TestPlanPlanId::test_explicit_value PASSED         [ 91%]
tests/test_models.py::TestPlanPlanId::test_backward_compat_parse_without_plan_id PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_command_registration_matrix PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_commands_delegate_through_orch_app_boundary PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_runs_first_loop_after_start PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_defaults_to_foreground_supervisor PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_attaches_to_active_drive_for_foreground_supervision PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_once_runs_loop_for_active_drive PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_auto_applies_safe_recovery_for_active_drive PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_auto_retries_retryable_failed_child_run PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_auto_recovers_orphaned_claim_only_preview PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_agent_assisted_recovery_before_operator_stop PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_stops_for_operator_recovery_when_preview_is_unsafe PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_reports_retry_limit_as_stop_reason PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_json_includes_compact_stop_reason PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_human_mode_emits_progress_events PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_quiet_human_mode_prints_final_result_only PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_drive_jsonl_emits_progress_event_stream PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_case_list_latest_reads_drive_blocked_cases_without_attribute_error PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_case_list_latest_status_filter_respects_open_only_drive_cases PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_output_mode_conflict_is_rejected PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_resume_requires_selector PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_inspect_actions_requires_selector PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_inspect_actions_requires_selector_even_with_active_drive PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_status_latest_falls_back_to_legacy_run_scope_when_no_drives PASSED [ 91%]
tests/test_orch_cli_surface.py::test_orch_control_and_config_flags_propagate_to_orch_app PASSED [ 92%]
tests/test_orch_cli_surface.py::test_orch_control_stop_forwards_explicit_run_id PASSED [ 92%]
tests/test_orch_cli_surface.py::test_orch_explicit_exit_code_mapping_for_not_found_and_recovery PASSED [ 92%]
tests/test_orch_cli_surface.py::test_orch_internal_error_maps_to_exit_code_5 PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_status_preserves_brackets_in_phase_name PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_show_phase_preserves_brackets PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_show_step_preserves_brackets_in_name PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_show_step_preserves_checklist_checkboxes PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_show_step_preserves_verification_brackets PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_review_preserves_brackets_in_phase_name PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_review_preserves_brackets_in_step_name PASSED [ 92%]
tests/test_output_hygiene.py::TestMarkupNotSwallowed::test_next_preserves_brackets PASSED [ 92%]
tests/test_plan_path.py::TestExplicitParam::test_explicit_overrides_env PASSED [ 92%]
tests/test_plan_path.py::TestExplicitParam::test_explicit_none_falls_through PASSED [ 92%]
tests/test_plan_path.py::TestEnvVarPrecedence::test_canonical_env_wins PASSED [ 92%]
tests/test_plan_path.py::TestEnvVarPrecedence::test_canonical_env_alone PASSED [ 92%]
tests/test_plan_path.py::TestEnvVarPrecedence::test_deprecated_env_alone PASSED [ 92%]
tests/test_plan_path.py::TestDeprecationWarning::test_no_warning_for_canonical PASSED [ 92%]
tests/test_plan_path.py::TestDeprecationWarning::test_warning_for_deprecated PASSED [ 92%]
tests/test_plan_path.py::TestWalkUpDiscovery::test_walk_up_finds_plan_in_parent PASSED [ 92%]
tests/test_plan_path.py::TestWalkUpDiscovery::test_walk_up_finds_plan_in_cwd PASSED [ 92%]
tests/test_plan_path.py::TestWalkUpDiscovery::test_walk_up_prefers_closest PASSED [ 92%]
tests/test_plan_path.py::TestFallback::test_fallback_when_no_file_exists PASSED [ 92%]
tests/test_plan_path.py::TestCLIMCPParity::test_cli_uses_shared_resolver PASSED [ 92%]
tests/test_plan_path.py::TestCLIMCPParity::test_mcp_uses_shared_resolver PASSED [ 92%]
tests/test_plan_path.py::TestUnifiedStatePosture::test_resolve_state_path_removed PASSED [ 92%]
tests/test_plan_path.py::TestWorktreeDetection::test_linked_worktree_resolves_to_main_root PASSED [ 92%]
tests/test_plan_path.py::TestWorktreeDetection::test_main_worktree_uses_walkup PASSED [ 92%]
tests/test_plan_path.py::TestWorktreeDetection::test_git_not_available_falls_through PASSED [ 92%]
tests/test_plan_path.py::TestWorktreeDetection::test_git_fails_falls_through PASSED [ 92%]
tests/test_plan_path.py::TestWorktreeDetection::test_env_var_overrides_worktree_detection PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeDetection::test_is_linked_worktree_returns_true_with_main_root PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeDetection::test_is_linked_worktree_returns_false_in_main_worktree PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeDetection::test_is_linked_worktree_normalizes_relative_common_dir PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeDetection::test_is_linked_worktree_malformed_git_output_fails_closed PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_is_linked_worktree_absolute_paths_resolve_main_root PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_resolve_plan_path_none_uses_main_root_with_absolute_git_paths PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_linked_worktree_prefers_valid_main_plan_over_local PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_linked_worktree_missing_main_plan_does_not_fall_back_to_local_walkup PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_malformed_git_output_uses_absolute_fallback PASSED [ 93%]
tests/test_plan_path.py::TestWorktreeResolutionRegressions::test_linked_worktree_uses_main_plan_path_even_if_file_missing PASSED [ 93%]
tests/test_properties.py::TestPlanStateMachine::runTest PASSED           [ 93%]
tests/test_render.py::TestRenderPlan::test_full_plan_has_title PASSED    [ 93%]
tests/test_render.py::TestRenderPlan::test_full_plan_has_summary_table PASSED [ 93%]
tests/test_render.py::TestRenderPlan::test_full_plan_has_phase_sections PASSED [ 93%]
tests/test_render.py::TestRenderPlan::test_full_plan_has_step_bullets PASSED [ 93%]
tests/test_render.py::TestRenderPlan::test_omits_claimed_by PASSED       [ 93%]
tests/test_render.py::TestRenderPlan::test_includes_gate PASSED          [ 93%]
tests/test_render.py::TestRenderPlan::test_includes_context PASSED       [ 93%]
tests/test_render.py::TestRenderPlan::test_step_description_summary PASSED [ 93%]
tests/test_render.py::TestRenderPlan::test_single_phase_filter PASSED    [ 93%]
tests/test_render.py::TestRenderPlan::test_single_phase_not_found PASSED [ 93%]
tests/test_render.py::TestRenderPlan::test_empty_plan PASSED             [ 93%]
tests/test_render.py::TestRenderPlan::test_phase_with_no_steps PASSED    [ 93%]
tests/test_render.py::TestFirstLine::test_simple PASSED                  [ 93%]
tests/test_render.py::TestFirstLine::test_multiline PASSED               [ 93%]
tests/test_render.py::TestFirstLine::test_skips_empty_lines PASSED       [ 93%]
tests/test_render.py::TestFirstLine::test_skips_checklist PASSED         [ 93%]
tests/test_render.py::TestFirstLine::test_truncates PASSED               [ 93%]
tests/test_render.py::TestFirstLine::test_empty_string PASSED            [ 93%]
tests/test_render.py::TestFirstLine::test_whitespace_only PASSED         [ 93%]
tests/test_render.py::TestRenderFull::test_default_truncates_long_description PASSED [ 94%]
tests/test_render.py::TestRenderFull::test_full_no_truncation PASSED     [ 94%]
tests/test_render.py::TestRenderFull::test_full_multiline_indented PASSED [ 94%]
tests/test_render.py::TestRenderFull::test_full_step_without_description PASSED [ 94%]
tests/test_render.py::TestRenderFull::test_full_with_phase_filter PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_phase_dag_has_flowchart_header PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_phase_dag_contains_all_phases PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_phase_dag_contains_edges PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_phase_dag_has_hint PASSED     [ 94%]
tests/test_render.py::TestMermaidDag::test_step_dag_has_flowchart_header PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_step_dag_contains_steps PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_step_dag_contains_edges PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_step_dag_no_cross_phase_edges PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_step_dag_phase_not_found PASSED [ 94%]
tests/test_render.py::TestMermaidDag::test_empty_plan PASSED             [ 94%]
tests/test_render.py::TestMermaidDag::test_no_deps_no_edges PASSED       [ 94%]
tests/test_render.py::TestMermaidDag::test_mermaid_node_id_sanitization PASSED [ 94%]
tests/test_review.py::TestReview::test_review_shows_all_layers PASSED    [ 94%]
tests/test_review.py::TestReview::test_l1_valid_plan PASSED              [ 94%]
tests/test_review.py::TestReview::test_l2_shows_progress_bars PASSED     [ 94%]
tests/test_review.py::TestReview::test_l3_shows_active_phases PASSED     [ 94%]
tests/test_review.py::TestReview::test_l3_shows_all_with_flag PASSED     [ 94%]
tests/test_review.py::TestReview::test_l3_shows_deps_and_gate PASSED     [ 94%]
tests/test_review.py::TestReview::test_l4_shows_ref_coverage PASSED      [ 94%]
tests/test_review.py::TestReview::test_l4_no_refs PASSED                 [ 94%]
tests/test_review.py::TestReview::test_review_shows_affordance_hints PASSED [ 94%]
tests/test_review.py::TestReview::test_review_exits_1_on_validation_errors PASSED [ 94%]
tests/test_review.py::TestReview::test_review_exits_1_on_duplicate_step_ids PASSED [ 94%]
tests/test_review.py::TestReview::test_review_exposes_completed_evidence_guard PASSED [ 94%]
tests/test_review.py::TestGateCheck::test_gate_ready PASSED              [ 94%]
tests/test_review.py::TestGateCheck::test_gate_not_ready PASSED          [ 94%]
tests/test_review.py::TestGateCheck::test_gate_check_shows_criteria PASSED [ 95%]
tests/test_review.py::TestGateCheck::test_gate_check_phase_not_found PASSED [ 95%]
tests/test_review.py::TestGateCheck::test_gate_check_shows_downstream PASSED [ 95%]
tests/test_review.py::TestGateCheck::test_gate_script_pass PASSED        [ 95%]
tests/test_review.py::TestGateCheck::test_gate_script_fail PASSED        [ 95%]
tests/test_review.py::TestGateCheck::test_gate_check_no_gate_script PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_returns_review_result PASSED  [ 95%]
tests/test_review.py::TestReviewPlan::test_phase_progress_counts PASSED  [ 95%]
tests/test_review.py::TestReviewPlan::test_total_counts PASSED           [ 95%]
tests/test_review.py::TestReviewPlan::test_active_phases_excludes_done_and_locked PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_include_done_shows_all PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_ref_index PASSED              [ 95%]
tests/test_review.py::TestReviewPlan::test_ref_index_empty_when_no_refs PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_validation_issues_on_valid_plan PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_validation_issues_on_broken_plan PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_skipped_steps_count_as_done PASSED [ 95%]
tests/test_review.py::TestReviewPlan::test_empty_plan PASSED             [ 95%]
tests/test_review.py::TestReviewPlan::test_errors_and_warnings_properties PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_returns_gate_check_result PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_complete_phase_is_gate_ready PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_incomplete_phase_is_not_gate_ready PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_gate_criterion PASSED      [ 95%]
tests/test_review.py::TestGateCheckCore::test_gate_script PASSED         [ 95%]
tests/test_review.py::TestGateCheckCore::test_no_gate_script PASSED      [ 95%]
tests/test_review.py::TestGateCheckCore::test_downstream_locked PASSED   [ 95%]
tests/test_review.py::TestGateCheckCore::test_no_downstream_when_unlocked PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_phase_not_found_raises PASSED [ 95%]
tests/test_review.py::TestGateCheckCore::test_phase_id_and_name PASSED   [ 95%]
tests/test_runtime_context_vertical_slice.py::test_plan_aware_control_evaluates_dispatch_from_current_sources PASSED [ 95%]
tests/test_runtime_lifecycle.py::TestWorktreeCreateUsesGitWorktreeNotMkdir::test_worktree_create_does_not_mkdir_before_git_worktree PASSED [ 95%]
tests/test_runtime_lifecycle.py::TestWorktreeCreateUsesGitWorktreeNotMkdir::test_worktree_create_records_target_head PASSED [ 95%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_begin_reconcile_acquires_per_target_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_begin_reconcile_captures_result_and_releases_target_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_concurrent_reconcile_same_target_raises_error PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_different_target_refs_allow_concurrent_reconcile PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_capture_reconcile_result_releases_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileSerialization::test_capture_reconcile_result_merge_conflict_releases_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_reconcile_disposition_returns_merged PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_reconcile_disposition_returns_noop PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_reconcile_disposition_returns_none_for_merge_conflict PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_reconcile_disposition_returns_none_before_reconcile PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_can_complete_rejects_without_reconcile PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_can_complete_rejects_merge_conflict PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_can_complete_accepts_after_merged PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestReconcileDispositionProof::test_can_complete_accepts_after_noop PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCoreAdapterReconcileDisposition::test_complete_step_includes_reconcile_disposition_in_evidence PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCoreAdapterReconcileDisposition::test_complete_step_noop_disposition_in_evidence PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_cleanup_blocks_without_reconcile PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_cleanup_blocks_merge_conflict PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_cleanup_blocks_aborted PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_get_unresolved_message_merge_conflict PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_get_unresolved_message_aborted PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_cleanup_releases_reconcile_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestCleanupLifecycleEnforcement::test_cleanup_failure_preserves_workspace_state_and_lock PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestFreshWorkspaceHints::test_workspace_hint_detected PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestFreshWorkspaceHints::test_independent_hint_detected PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestFreshWorkspaceHints::test_no_isolation_hint_not_fresh PASSED [ 96%]
tests/test_runtime_lifecycle.py::TestFreshWorkspaceHints::test_empty_refs_not_fresh PASSED [ 96%]
tests/test_semantics.py::TestDeriveStepStatus::test_done_step_not_blocked PASSED [ 96%]
tests/test_semantics.py::TestDeriveStepStatus::test_skipped_step_not_blocked PASSED [ 96%]
tests/test_semantics.py::TestDeriveStepStatus::test_claimed_step_blocked_by_status PASSED [ 96%]
tests/test_semantics.py::TestDeriveStepDeps::test_pending_no_deps_claimable PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_pending_with_met_deps_claimable PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_pending_with_unmet_deps_blocked PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_pending_with_missing_dep_blocked PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_skipped_dep_counts_as_met PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_rejected_step_with_met_deps_claimable PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepDeps::test_rejected_step_with_unmet_deps_blocked PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepPhaseActive::test_locked_phase_blocks_step PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepPhaseActive::test_locked_phase_with_met_deps_unlocks PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepPhaseActive::test_in_progress_phase_allows_step PASSED [ 97%]
tests/test_semantics.py::TestDeriveStepPhaseActive::test_done_phase_blocks_pending_step PASSED [ 97%]
tests/test_semantics.py::TestIsStepLocked::test_pending_with_unmet_deps_is_locked PASSED [ 97%]
tests/test_semantics.py::TestIsStepLocked::test_pending_without_deps_is_not_locked PASSED [ 97%]
tests/test_semantics.py::TestIsStepLocked::test_non_pending_is_never_locked PASSED [ 97%]
tests/test_semantics.py::TestIsStepLocked::test_pending_with_met_deps_is_not_locked PASSED [ 97%]
tests/test_semantics.py::TestStepDerivedLocked::test_locked_when_unmet_dep PASSED [ 97%]
tests/test_semantics.py::TestStepDerivedLocked::test_not_locked_when_phase_inactive_only PASSED [ 97%]
tests/test_semantics.py::TestStepDerivedLocked::test_not_locked_when_not_blocked PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_simple_linear_plan PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_partially_done_plan PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_all_done_plan PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_multi_phase_with_locks PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_multi_phase_auto_unlock PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_rejected_with_deps PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_parallel_steps_no_deps PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_claimed_step_not_in_next PASSED [ 97%]
tests/test_semantics.py::TestSemanticParityWithCore::test_mixed_statuses PASSED [ 97%]
tests/test_semantics.py::TestCLIMCPLockAgreement::test_cli_mcp_share_lock_function PASSED [ 97%]
tests/test_semantics.py::TestCLIMCPLockAgreement::test_core_render_shares_lock_function PASSED [ 97%]
tests/test_smoke.py::TestPlanPathFromNestedDir::test_discovers_plan_from_child_dir PASSED [ 97%]
tests/test_smoke.py::TestPlanPathResolution::test_relative_and_absolute_plan_path_share_plan_yaml PASSED [ 97%]
tests/test_smoke.py::TestPlanPathResolution::test_env_var_overrides_walk_up PASSED [ 98%]
tests/test_smoke.py::TestLockedClaimableAgreement::test_locked_phase_steps_not_in_next PASSED [ 98%]
tests/test_smoke.py::TestLockedClaimableAgreement::test_dep_blocked_step_not_in_next PASSED [ 98%]
tests/test_smoke.py::TestLockedClaimableAgreement::test_review_shows_locked_icon PASSED [ 98%]
tests/test_smoke.py::TestLockedClaimableAgreement::test_show_locked_phase_shows_locks PASSED [ 98%]
tests/test_smoke.py::TestClaimCompleteWorkflow::test_claim_complete_cycle PASSED [ 98%]
tests/test_smoke.py::TestClaimCompleteWorkflow::test_review_include_done_shows_done_phases PASSED [ 98%]
tests/test_smoke.py::TestClaimCompleteWorkflow::test_review_without_all_hides_done PASSED [ 98%]
tests/test_smoke.py::TestSearchAndValidate::test_search_finds_step PASSED [ 98%]
tests/test_smoke.py::TestSearchAndValidate::test_validate_passes_clean_plan PASSED [ 98%]
tests/test_validation.py::TestPhaseIDUniqueness::test_unique_ids_ok PASSED [ 98%]
tests/test_validation.py::TestPhaseIDUniqueness::test_duplicate_phase_id PASSED [ 98%]
tests/test_validation.py::TestPhaseDAG::test_valid_depends_on PASSED     [ 98%]
tests/test_validation.py::TestPhaseDAG::test_unknown_depends_on PASSED   [ 98%]
tests/test_validation.py::TestPhaseDAG::test_cycle PASSED                [ 98%]
tests/test_validation.py::TestPhaseDAG::test_three_node_cycle PASSED     [ 98%]
tests/test_validation.py::TestCompletedEvidenceGuard::test_done_gate_failure_without_later_closure_is_blocking PASSED [ 98%]
tests/test_validation.py::TestCompletedEvidenceGuard::test_done_gate_failure_closed_by_later_retest_is_allowed PASSED [ 98%]
tests/test_validation.py::TestCompletedEvidenceGuard::test_intentional_expected_red_with_lifecycle_disposition_is_allowed PASSED [ 98%]
tests/test_validation.py::TestStepDAG::test_valid_step_deps PASSED       [ 98%]
tests/test_validation.py::TestStepDAG::test_unknown_step_dep PASSED      [ 98%]
tests/test_validation.py::TestStepDAG::test_step_cycle PASSED            [ 98%]
tests/test_validation.py::TestStepDAG::test_duplicate_step_id PASSED     [ 98%]
tests/test_validation.py::TestStatusConsistency::test_claimed_step_in_locked_phase PASSED [ 98%]
tests/test_validation.py::TestStatusConsistency::test_pending_step_in_locked_phase_ok PASSED [ 98%]
tests/test_validation.py::TestStatusConsistency::test_phase_done_but_step_pending PASSED [ 98%]
tests/test_validation.py::TestStatusConsistency::test_phase_done_all_steps_done_ok PASSED [ 98%]
tests/test_validation.py::TestRefsCheck::test_refs_check_missing_file PASSED [ 98%]
tests/test_validation.py::TestRefsCheck::test_refs_check_existing_file PASSED [ 98%]
tests/test_validation.py::TestRefsCheck::test_refs_with_fragment PASSED  [ 98%]
tests/test_validation.py::TestLegacyDuplicateStepIdHardErrors::test_duplicate_step_id_is_error PASSED [ 98%]
tests/test_validation.py::TestLegacyDuplicateStepIdHardErrors::test_duplicate_step_id_error_includes_repair_guidance PASSED [ 99%]
tests/test_validation.py::TestLegacyDuplicateStepIdHardErrors::test_review_plan_marks_duplicate_step_id_as_blocking_error PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_add_step_rejects_duplicate_explicit_id PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_add_step_rejects_duplicate_auto_generated_id PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_add_step_rejects_duplicate_id_across_phases PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_add_steps_bulk_rejects_duplicate_id_across_phases PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_claim_rejects_ambiguous_duplicate_target_without_opt_in PASSED [ 99%]
tests/test_validation.py::TestWriteSurfaceRejectsDuplicateIds::test_add_phase_rejects_duplicate_phase_id PASSED [ 99%]
tests/test_validation.py::TestHardErrorFlipRegressions::test_duplicate_step_id_remains_hard_error PASSED [ 99%]
tests/test_validation.py::TestHardErrorFlipRegressions::test_cli_validate_flag_placeholder PASSED [ 99%]
tests/test_validation.py::TestHardErrorFlipRegressions::test_add_step_future_strict_mode_placeholder PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_depends_on_cycle_raises_error PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_add_deps_cycle_raises_error PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_cycle_rollback_keeps_non_dependency_edits PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_remove_deps_cycle_raises_error PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_valid_deps_succeeds PASSED [ 99%]
tests/test_validation.py::TestEditStepCycleDetection::test_edit_step_no_dep_mutation_no_cycle_check PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_claim_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_claim_step_with_already_qualified_id_does_not_double_prefix PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_complete_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_defer_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_reject_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_skip_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_edit_step_cycle_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_remove_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_remove_step_with_already_qualified_id_does_not_double_prefix PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_move_step_wrong_status_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_move_step_wrong_phase_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_claim_step_inactive_phase_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_claim_step_unmet_deps_error_uses_qualified_id PASSED [ 99%]
tests/test_validation.py::TestQualifiedIdInErrorMessages::test_error_message_copy_pasteable_as_step_id PASSED [100%]

=================================== FAILURES ===================================
_______________________ test_init_creates_gitattributes ________________________
tests/test_init_gitattributes.py:14: in test_init_creates_gitattributes
    with runner.isolated_filesystem(temp_dir=tmp_path):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'CliRunner' object has no attribute 'isolated_filesystem'
______________________ test_init_idempotent_gitattributes ______________________
tests/test_init_gitattributes.py:28: in test_init_idempotent_gitattributes
    with runner.isolated_filesystem(temp_dir=tmp_path):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'CliRunner' object has no attribute 'isolated_filesystem'
__________________ test_init_gitattributes_preserves_existing __________________
tests/test_init_gitattributes.py:51: in test_init_gitattributes_preserves_existing
    with runner.isolated_filesystem(temp_dir=tmp_path):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^
E   AttributeError: 'CliRunner' object has no attribute 'isolated_filesystem'
=========================== short test summary info ============================
FAILED tests/test_init_gitattributes.py::test_init_creates_gitattributes - At...
FAILED tests/test_init_gitattributes.py::test_init_idempotent_gitattributes
FAILED tests/test_init_gitattributes.py::test_init_gitattributes_preserves_existing
============ 3 failed, 3027 passed, 43 skipped in 123.80s (0:02:03) ============

```

### C14: `uv run invar guard --all`
Exit code: 0

```json
warning: `VIRTUAL_ENV=/Users/tefx/Projects/larva/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
{
  "status": "passed",
  "static": {
    "passed": true,
    "errors": 0,
    "warnings": 60,
    "infos": 4,
    "findings": [
      {
        "file": "docs/archive/consumer_boundary_conformance_verify.py",
        "line": 230,
        "rule": "shell_too_complex",
        "severity": "info",
        "message": "Shell function 'main' has 9 branches (max: 3)",
        "fix": {
          "action": "manual",
          "instruction": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Shell function with excessive branching complexity",
          "cannot_detect": [
            "Whether complexity is justified",
            "Domain-specific patterns"
          ],
          "hint": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        }
      },
      {
        "file": "src/vectl/core_plan_step_add.py",
        "line": 187,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'add_steps_bulk' has 225 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_orchestration_inspect_commands.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 573 lines (95% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] orch_case_list (67L) | Deps: none\n[B] orch_control_stop (63L) | Deps: none\n[C] orch_inspect_logs (59L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/cli_plan_mutation_commands.py",
        "line": 7,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'add_step_cmd' has 95 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_plan_mutation_commands.py",
        "line": 269,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'edit_step_cmd' has 96 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 1250,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp._start_runtime_execution' has 107 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 1541,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp._admit_start_and_persist_running' has 110 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 1784,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp._collect_and_route_terminal' has 217 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 2247,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp.run_drive_foreground' has 169 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 2753,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp._finalize_drive_child_run' has 120 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 3136,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp.run' has 102 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 3261,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp.resume' has 115 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 3741,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp._evaluate_recovery_decision' has 148 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 4076,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'OrchestrationApp.recover' has 392 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 5536,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'build_orchestration_app' has 395 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_orchestration_runtime_helpers.py",
        "line": 126,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function '_drive_action_guidance' has 84 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/core_checklist.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 484 lines (96% of 500 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] mutate_checklist (20L) | Deps: vectl\n[B] _resolve_field_index_selector (19L) | Deps: none\n[C] _error_payload (18L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/core_checklist.py",
        "line": 464,
        "rule": "internal_import",
        "severity": "warning",
        "message": "Function 'mutate_checklist' has internal imports: vectl",
        "fix": {
          "action": "manual",
          "instruction": "Move imports to top of file or move function to Shell"
        },
        "rule_meta": {
          "category": "purity",
          "detects": "Import statement inside function body",
          "cannot_detect": [
            "Lazy imports for performance",
            "Circular import workarounds"
          ],
          "hint": "Move import to module top-level or justify with comment"
        }
      },
      {
        "file": "src/vectl/cli_plan_migration_commands.py",
        "line": 70,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'migrate_step_id_cmd' has 108 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/mcp_render_tools.py",
        "line": 351,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'vectl_review' has 116 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_plan_lifecycle_commands.py",
        "line": 8,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'claim' has 97 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/mcp_core_lifecycle_tools.py",
        "line": 43,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'vectl_claim' has 177 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/claim_guidance.py",
        "line": 39,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'build_claim_guidance' has 105 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/mcp_core_mutation_tools.py",
        "line": 44,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'vectl_mutate' has 218 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/mcp_project_tools.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 617 lines (88% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] _clipboard_clear, _clipboard_read, _clipboard_write, _load, _plan_path, _save_plan, vectl_clipboard, vectl_recover, vectl_repair_claims (258L) | Deps: vectl\n[B] vectl_init (68L) | Deps: pathlib\n[C] _get_next_steps_with_phase (46L) | Deps: vectl"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/core_plan_validation.py",
        "line": 54,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'validate_plan' has 112 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_plan_read_commands.py",
        "line": 185,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function '_show_step_detail' has 96 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/cli_orchestration_drive_commands.py",
        "line": 10,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'orch_drive' has 97 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_loop.py",
        "line": 29,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverLoopMixin.run_drive_loop' has 213 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_recovery.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 511 lines (85% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] DriverRecoveryMixin.recover_drive (344L) | Deps: vectl\n[B] DriverRecoveryMixin._release_superseded_leases (36L) | Deps: none\n[C] DriverRecoveryMixin._find_terminal_artifact (35L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_recovery.py",
        "line": 27,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverRecoveryMixin.recover_drive' has 311 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/dispatch_policy.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 567 lines (94% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] ConfigPromptRegistry.render, _build_context_messages, _build_template_data, _render_checklist_inventory, _select_prompt_content (161L) | Deps: none\n[B] ConfigRoleProfileRegistry.resolve_role (33L) | Deps: none\n[C] ConfigRoleProfileRegistry.get (25L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/resolver_gateway.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 534 lines (89% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] AuditedResolverGateway.invoke, _authorize_single_call, _authorize_tool_calls (166L) | Deps: none\n[B] authorize_and_invoke (38L) | Deps: none\n[C] ResolverGateway.invoke (24L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 596 lines (99% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] recovery_action_status, recovery_case_status, recovery_gate_open_allowed (68L) | Deps: none\n[B] RunStoreLegacyRunBridge.set_migration_status (40L) | Deps: none\n[C] RunStoreLegacyRunBridge.get_migration_status (26L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/projection_helpers.py",
        "line": 110,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function '_derive_state' has 161 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/projection_helpers.py",
        "line": 20,
        "rule": "shell_too_complex",
        "severity": "info",
        "message": "Shell function '_select_events' has 5 branches (max: 3)",
        "fix": {
          "action": "manual",
          "instruction": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Shell function with excessive branching complexity",
          "cannot_detect": [
            "Whether complexity is justified",
            "Domain-specific patterns"
          ],
          "hint": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery_cutover.py",
        "line": 33,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'CutoverValidator.validate_cutover_readiness' has 177 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/core_adapter.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 671 lines (95% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] PlanPlannerMutationApplier.apply_bundle (48L) | Deps: none\n[B] PlanCoreAdapter.snapshot (45L) | Deps: none\n[C] PlanCoreAdapter.load_step_data_for_dispatch (36L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery_fallback.py",
        "line": 97,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'validate_session_for_resume' has 130 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery_fallback.py",
        "line": 290,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'recover_with_fallback' has 168 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_control.py",
        "line": 16,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverControlMixin._consume_drive_control' has 177 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/events.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 673 lines (96% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] DriveEventEnvelope.__post_init__, EventRegistry.emit, OrchestrationEventEnvelope.__post_init__, OrchestrationEventEnvelope.from_record, validate_payload (151L) | Deps: none\n[B] JsonlEventSink._authoritative_tail, load_event_jsonl (37L) | Deps: none\n[C] JsonlEventSink.emit (25L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_run_store_impl_part01.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 618 lines (88% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] _RunStoreDomain_deserialize_run_record._deserialize_run_record (42L) | Deps: none\n[B] _append_jsonl_with_retry (35L) | Deps: none\n[C] _RunStoreDomain_deserialize_operator_notification._deserialize_operator_notification (33L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_config_impl_part01.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 649 lines (92% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] _builtin_role_ids, build_role_profile_provenance, default_role_profiles (164L) | Deps: none\n[B] _parse_custom_role_profiles (48L) | Deps: none\n[C] validate_role_profile, validate_role_profiles (44L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/control.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 567 lines (94% of 600 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] PlanAwareControl.evaluate (200L) | Deps: none\n[B] PlanAwareControl.apply_resolution (67L) | Deps: none\n[C] PlanAwareControl.apply_planner_result (57L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/control.py",
        "line": 190,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'PlanAwareControl.evaluate' has 172 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/inspection_queries.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 696 lines (99% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] query_drive_status, validate_child_run_in_drive (74L) | Deps: none\n[B] query_drive_artifacts (44L) | Deps: none\n[C] RunsQueryImpl.query (37L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery_artifacts.py",
        "line": 92,
        "rule": "shell_too_complex",
        "severity": "info",
        "message": "Shell function 'read_recovery_continuity' has 4 branches (max: 3)",
        "fix": {
          "action": "manual",
          "instruction": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Shell function with excessive branching complexity",
          "cannot_detect": [
            "Whether complexity is justified",
            "Domain-specific patterns"
          ],
          "hint": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/recovery_artifacts.py",
        "line": 117,
        "rule": "shell_too_complex",
        "severity": "info",
        "message": "Shell function 'read_recovery_attempt' has 4 branches (max: 3)",
        "fix": {
          "action": "manual",
          "instruction": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Shell function with excessive branching complexity",
          "cannot_detect": [
            "Whether complexity is justified",
            "Domain-specific patterns"
          ],
          "hint": "Extract logic to Core, or add: # @shell_complexity: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/control_channel.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 696 lines (99% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] FilesystemControlChannel._acknowledge, FilesystemControlChannel._mark_rejected_from_file, FilesystemControlChannel._oldest_pending_request, FilesystemControlChannel._reject_malformed_and_duplicate_pending, FilesystemControlChannel.list_requests, FilesystemControlChannel.lookup_acknowledgement, FilesystemControlChannel.send, _read_json_object, _write_json_atomic (163L) | Deps: none\n[B] _RequestDeserializer.__call__ (37L) | Deps: none\n[C] _ReceiptDeserializer.__call__ (31L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_config_impl_part04.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 570 lines (81% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] validate_orchestration_config (268L) | Deps: none\n[B] build_drive_config_provenance, freeze_config (109L) | Deps: none\n[C] load_orchestration_config (49L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_config_impl_part04.py",
        "line": 263,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'validate_orchestration_config' has 243 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_planner.py",
        "line": 18,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverPlannerMixin._handle_replan_decision' has 117 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_resolver.py",
        "line": 24,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverResolverMixin._handle_resolve_decision' has 137 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_review.py",
        "line": 22,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverReviewMixin._review_terminal_child_run' has 92 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/runners.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 593 lines (84% of 700 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] _append_machine_result_payloads, _append_opencode_text_parts, _find_opencode_session_id, _is_machine_result_summary, _iter_json_values_from_text, _looks_like_structured_review_payload, _opencode_db_path, _opencode_session_id_from_stdout, _summarize_opencode_session_db, _summarize_opencode_stdout (163L) | Deps: none\n[B] SubprocessRunner.poll (48L) | Deps: none\n[C] SubprocessRunner.launch (29L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_config_impl_part02.py",
        "line": 59,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function '_merge_role_profiles' has 164 code lines (max: 100 for shell)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/orchestration/driver_resume.py",
        "line": 21,
        "rule": "function_size",
        "severity": "warning",
        "message": "Function 'DriverResumeMixin.resume_drive' has 103 code lines (max: 80 for default)",
        "fix": {
          "action": "manual",
          "instruction": "Extract helper functions"
        },
        "rule_meta": {
          "category": "size",
          "detects": "Function exceeds max_function_lines limit",
          "cannot_detect": [
            "Algorithm complexity",
            "Whether extraction helps readability"
          ],
          "hint": "Extract helper functions or simplify logic"
        }
      },
      {
        "file": "src/vectl/core/orchestration/prompt_contracts.py",
        "line": null,
        "rule": "file_size_warning",
        "severity": "warning",
        "message": "File has 488 lines (97% of 500 limit)",
        "fix": {
          "action": "manual",
          "instruction": "Consider splitting before reaching limit.\nExtractable groups:\n[A] output_contract_line_list, output_contract_lines, render_runner_prompt_bundle_md, render_runner_prompt_md (97L) | Deps: none\n[B] _resolve_path_text, build_default_opencode_launch_argv, build_opencode_launch_argv (64L) | Deps: none\n[C] build_runner_handoff_env, build_runner_handoff_env_data (40L) | Deps: none"
        },
        "rule_meta": {
          "category": "size",
          "detects": "File approaching max_file_lines limit (80% threshold)",
          "cannot_detect": [
            "Whether split is actually needed"
          ],
          "hint": "Consider splitting before reaching limit"
        }
      },
      {
        "file": "src/vectl/orchestration/_run_store_impl_part01.py",
        "line": 141,
        "rule": "dead_export",
        "severity": "warning",
        "message": "Shell class 'HeartbeatArtifact' is never referenced",
        "fix": {
          "action": "manual",
          "instruction": "Remove unused class, or add: # @invar:allow dead_export: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Public shell function or class with zero detected callers across src/ (excluding tests/)",
          "cannot_detect": [
            "Arbitrary runtime dispatch",
            "Reflection-only callers with no static registration site",
            "External consumers outside src/"
          ],
          "hint": "Add a caller in src/ or mark with: # @invar:allow <rule>: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/_run_store_impl_part01.py",
        "line": 151,
        "rule": "dead_export",
        "severity": "warning",
        "message": "Shell class 'RunStoreError' is never referenced",
        "fix": {
          "action": "manual",
          "instruction": "Remove unused class, or add: # @invar:allow dead_export: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Public shell function or class with zero detected callers across src/ (excluding tests/)",
          "cannot_detect": [
            "Arbitrary runtime dispatch",
            "Reflection-only callers with no static registration site",
            "External consumers outside src/"
          ],
          "hint": "Add a caller in src/ or mark with: # @invar:allow <rule>: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/_run_store_impl_part01.py",
        "line": 173,
        "rule": "dead_export",
        "severity": "warning",
        "message": "Shell class 'LegacyContinuityMinimumError' is never referenced",
        "fix": {
          "action": "manual",
          "instruction": "Remove unused class, or add: # @invar:allow dead_export: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Public shell function or class with zero detected callers across src/ (excluding tests/)",
          "cannot_detect": [
            "Arbitrary runtime dispatch",
            "Reflection-only callers with no static registration site",
            "External consumers outside src/"
          ],
          "hint": "Add a caller in src/ or mark with: # @invar:allow <rule>: <reason>"
        }
      },
      {
        "file": "src/vectl/orchestration/_config_impl_part01.py",
        "line": 149,
        "rule": "dead_export",
        "severity": "warning",
        "message": "Shell class 'ConfigValue' is never referenced",
        "fix": {
          "action": "manual",
          "instruction": "Remove unused class, or add: # @invar:allow dead_export: <reason>"
        },
        "rule_meta": {
          "category": "shell",
          "detects": "Public shell function or class with zero detected callers across src/ (excluding tests/)",
          "cannot_detect": [
            "Arbitrary runtime dispatch",
            "Reflection-only callers with no static registration site",
            "External consumers outside src/"
          ],
          "hint": "Add a caller in src/ or mark with: # @invar:allow <rule>: <reason>"
        }
      },
      {
        "file": "<project>",
        "line": null,
        "rule": "escape_hatch_budget",
        "severity": "warning",
        "message": "Escape hatch weighted budget: 14/15",
        "fix": null
      }
    ]
  },
  "summary": {
    "files_checked": 147,
    "errors": 0,
    "warnings": 60,
    "infos": 4
  },
  "escape_hatches": {
    "count": 6,
    "by_rule": {
      "shell_complexity": 1,
      "entry_point_too_thick": 2,
      "dead_param": 1,
      "file_size": 2
    },
    "details": [
      {
        "file": "docs/archive/consumer_boundary_conformance_verify.py",
        "line": 231,
        "rule": "shell_complexity",
        "reason": "archived conformance verifier is a linear shell script"
      },
      {
        "file": "src/vectl/plan_path.py",
        "line": 182,
        "rule": "entry_point_too_thick",
        "reason": "git-common-dir fallback must remain a single public resolver"
      },
      {
        "file": "src/vectl/cli_orchestration_inspect_commands.py",
        "line": 277,
        "rule": "dead_param",
        "reason": "drive_id is retained as a public Typer positional compatibility placeholder for drive-scoped case responses."
      },
      {
        "file": "src/vectl/orch_app.py",
        "line": 1,
        "rule": "file_size",
        "reason": "orchestration app is the documented composition root and public CLI routing facade; splitting requires a compatibility migration outside this scoped guard remediation."
      },
      {
        "file": "src/vectl/core_plan_mutations.py",
        "line": 1,
        "rule": "file_size",
        "reason": "Plan mutation compatibility module keeps lifecycle, clipboard, agents-md, and recovery APIs co-located for existing CLI/MCP imports while step add/edit internals migrate behind stable re-exports."
      },
      {
        "file": "src/vectl/migration.py",
        "line": 64,
        "rule": "entry_point_too_thick",
        "reason": "legacy split-state path fallback must remain public"
      }
    ],
    "gating": {
      "status": "warning",
      "per_rule_violations": [
        {
          "rule": "escape_hatch_budget",
          "count": 1,
          "violations": [
            {
              "severity": "warning",
              "file": "<project>",
              "line": null,
              "message": "Escape hatch weighted budget: 14/15"
            }
          ]
        }
      ],
      "budget": {
        "used": 14,
        "limit": 15
      },
      "non_suppressible": [],
      "combination": []
    }
  },
  "verification_level": "STANDARD",
  "doctest": {
    "passed": true,
    "output": ""
  },
  "crosshair": {
    "status": "skipped",
    "reason": "no core files found"
  },
  "property_tests": {
    "status": "skipped",
    "reason": "no core files"
  }
}

```
