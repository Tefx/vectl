# Repo regression remediation execution sequence

This sequence translates the classified failure buckets into a dependency-safe execution order that keeps test-only cleanup separate from live behavioral repair.

## Ordering rule

1. Clear stale expected-red failures first so later verification only reflects live contract/behavior drift.
2. Re-baseline locked contract assertions next so semantic/runtime work is measured against the current documented interface.
3. Resolve live semantic/runtime drift before touching missing CLI/fixture surfaces, because those failures represent green-contract behavior seams rather than absent legacy affordances.
4. Repair fixture/CLI surface next, after behavior semantics are stable.
5. Triage observability debt after the higher-signal runtime/CLI clusters are settled, because that bucket mixes stale probes with genuine backlog and would otherwise create noise.
6. Run the full-suite gate only after every prior bucket has landed.

## Downstream remediation phases and steps

| Order | Downstream phase | Downstream step | Buckets | Why here |
| --- | --- | --- | --- | --- |
| 1 | `expected-red-cleanup` | `config-run-registry-red-rebaseline` | Bucket A | Test-only cleanup against already-implemented config/run-registry surfaces removes stale red noise before any behavior triage. |
| 2 | `contract-lock-realignment` | `resolution-case-and-runtime-locks` | Bucket B (ResolutionCase lock tests), Bucket D | Runtime lifecycle expectations must be validated against the post-rebaseline contract shape before broader semantic fixes proceed. |
| 3 | `semantic-runtime-drift-resolution` | `recovery-surface-conformance` | Bucket C | These are the highest-signal live behavior failures and should be fixed without mixing in test drift or CLI backlog. |
| 4 | `fixture-cli-surface-repair` | `driver-entrypoint-and-help-contract` | Bucket E | CLI/liveness surface repair depends on stable recovery/runtime semantics, but should still finish before observability triage so user-facing surface failures stop obscuring later gate results. |
| 5 | `observability-debt-triage` | `event-probe-rebaseline-and-schema-backlog-split` | Bucket F | This bucket must be split into stale expected-red probe cleanup vs true implementation backlog only after earlier contract/behavior work settles the suite baseline. |
| 6 | `full-suite-gate` | `pytest-q-final-regression-gate` | All prior buckets | Final suite gate confirms the sequence removed noise in order and did not reintroduce mixed test/behavior drift. |

## Bucket-to-step mapping

| Bucket | Classification from dispositions | Downstream phase/step | Execution notes |
| --- | --- | --- | --- |
| A | stale expected-red config/run-registry tests | `expected-red-cleanup/config-run-registry-red-rebaseline` | Convert/remove obsolete expected-red assertions first; no product behavior changes should be mixed into this step. |
| B | stale contract lock drift + CLI export-surface drift | `contract-lock-realignment/resolution-case-and-runtime-locks` | Re-lock `ResolutionCase` and bounded export expectations to current docs/implementation before downstream semantic fixes. |
| C | likely behavior regression in recovery semantics | `semantic-runtime-drift-resolution/recovery-surface-conformance` | Implementation-focused repair step; keep isolated from test-only churn. |
| D | runtime reconcile lifecycle drift | `contract-lock-realignment/resolution-case-and-runtime-locks` | Validate and fix lifecycle contract immediately after lock realignment so runtime semantics are stable before recovery/CLI follow-ons. |
| E | missing fixture / CLI public surface mismatch | `fixture-cli-surface-repair/driver-entrypoint-and-help-contract` | Handle repo-root `driver.yaml`/`drive` surface issues only after live runtime semantics stop moving. |
| F | observability stale probes + schema/helper backlog | `observability-debt-triage/event-probe-rebaseline-and-schema-backlog-split` | First separate stale expected-red probes from genuine implementation debt, then queue any remaining behavior work without contaminating the earlier behavioral phases. |

## Dependency guardrails

- Bucket A must finish before Buckets B-F are re-verified, otherwise stale expected-red failures mask whether later changes are real regressions.
- Bucket B must finish before Bucket C because recovery/reporting semantics rely on the current exported contract language.
- Bucket D is paired with Bucket B instead of Bucket C so runtime lifecycle expectations are stabilized before recovery-conformance fixes consume them.
- Bucket E intentionally follows semantic/runtime stabilization so missing CLI/fixture work does not get conflated with recovery/runtime behavior changes.
- Bucket F is deferred until last among remediation buckets because it contains both stale tests and real backlog; triaging it earlier would reintroduce thrash.
- The final full-suite gate is valid only if each prior phase lands independently and re-verifies its own bucket before the aggregate run.

## Final-gate policy for future large remediations

- Phase-local re-verification is **required but not sufficient** for a gate that claims repository readiness.
- Any future large-remediation/final gate must explicitly choose one of these policies:
  1. **Repo-wide regression required** — name the aggregate command(s) (for example `uv run pytest -q`) and require them to pass.
  2. **Explicit exception policy** — state why a repo-wide pass is not yet required, which failures are governed/non-blocking, and what follow-up gate or owner carries the remaining debt.
- Do not imply repo-wide readiness from prose such as "final gate", "freeze", or "regression complete" unless the governing gate text also names the aggregate command or exception policy.
- Expected-red/xfail/skip items must be tracked as lifecycle-governed exceptions, not left as unexplained red output at the end of remediation.

## Evidence expectations for repo-wide readiness gates

- Keep **phase-local evidence** separate from **repo-wide gate evidence**.
- Phase-local evidence should show each bucket's scoped rerun and its result.
- Repo-wide gate evidence should show the named aggregate command, exit/result summary, and whether the suite is fully green or green except for explicitly governed exceptions.
- If exceptions remain, evidence must list:
  - the failing/xfail/skip/expected-red item
  - why it is non-blocking
  - its lifecycle disposition (for example: stale expected-red removed, converted to green, intentionally xfailed with rationale, or deferred under named follow-up policy)
  - the owner/follow-up gate responsible for clearing it

## Recommended verification sequence

1. Execute the bucket-local tests for Bucket A.
2. Execute the contract/runtime-focused tests for Buckets B and D.
3. Execute the recovery conformance tests for Bucket C.
4. Execute the CLI/liveness tests for Bucket E.
5. Execute the observability repro/unit tests for Bucket F.
6. Run `uv run pytest -q` as the final aggregate gate.

## Spec links

- `docs/repo_regression_failure_dispositions.yaml`
- `docs/full-suite-failure-inventory-2026-04-10.md`
