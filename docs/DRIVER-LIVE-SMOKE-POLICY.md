# [Design] Driver Live Smoke Policy Ratification

Status: ratified for `driver-live-smoke-foundation.ratify-live-smoke-policy`

## Scope

- In scope: additive live smoke test/doc/config policy for `codex` and `opencode` only.
- Out of scope: production runner redesign, model-name rediscovery, new runner support, CI platform redesign.

## Ratified decisions

- [Proven] **Pinned smoke models**
  - codex judge: `gpt-5.4-mini`
  - codex dispatch: `gpt-5.4-mini`
  - opencode judge: `ollama-cloud/minimax-m2.7`
  - opencode dispatch: `ollama-cloud/minimax-m2.7`
- [Proven] **Marker set**: `live_runner`, `codex_live`, `opencode_live`, `judge_live`, `dispatch_live`.
- [Likely] **Opt-in gate**: `RUN_LIVE_RUNNER_TESTS=1`.
  - Rationale: live smoke depends on external binaries, auth state, and billable providers; default-off keeps ordinary test runs deterministic.
- [Likely] **Execution mode**: `manual-only`.
  - Rationale: this phase is policy ratification for additive smoke coverage, not a commitment to secrets-backed CI. Manual-only avoids making core CI or release health depend on local/provider auth and binary availability.
- [Proven] **Codex judge boundary**:
  - structured mode is schema-enforced via `codex exec --help` `--output-schema <FILE>`
  - tolerant text fallback is **not** a normal compatibility success path
  - valid codex judge live outcomes in structured mode are only:
    - `PASS`: real structured output accepted
    - `FAILURE`: schema/structured-output rejection or unusable structured contract result
    - `SKIP`: missing binary, missing auth/prereq, or opt-in gate absent
- [Proven] **OpenCode judge boundary**:
  - `opencode run --help` exposes `--format json`
  - tolerant JSONL/text extraction **may** be the normal compatibility path for live judge coverage

## Skip taxonomy

- `live_runner_opt_in_missing`: `RUN_LIVE_RUNNER_TESTS` not set to `1`
- `live_runner_binary_missing`: required binary not on `PATH`
- `live_runner_auth_missing`: runner/provider login missing or rejected
- `live_runner_override_unsupported`: runner path cannot honor the ratified model pin through its supported override surface

These are test-policy skips, not production fallback decisions.

## Ratified runner/scenario matrix

| Scenario | Pinned model id | Override mechanism | Invocation shape | Expected markers | Fallback policy | Override confirmation surface |
|---|---|---|---|---|---|---|
| `codex-judge` | `gpt-5.4-mini` | **Fixed `driver.yaml` source**: pin model in `runners.codex.args` using Codex-supported `-m/--model`; judge inherits base argv from `driver.yaml`, then appends only ephemeral `--output-schema <tempfile>` | `codex exec --json ... -m gpt-5.4-mini ... --output-schema <tempfile> -` where base argv originates from `driver.yaml` | `live_runner`, `codex_live`, `judge_live` | No cross-runner fallback. On missing opt-in/binary/auth -> `SKIP`. On unsupported override surface -> `SKIP`. On schema rejection/unusable structured result -> explicit `FAILURE`. | `codex --help` (`-m, --model`), `codex exec --help` (`--output-schema <FILE>`), `driver.yaml`, `docs/DRIVER-ARCHITECTURE.md` codex invocation source-of-truth section |
| `codex-dispatch` | `gpt-5.4-mini` | **Fixed `driver.yaml` source**: pin model in `runners.codex.args` using Codex-supported `-m/--model` | `codex exec --json ... -m gpt-5.4-mini ... -` from `driver.yaml` contract | `live_runner`, `codex_live`, `dispatch_live` | No cross-runner fallback. On missing opt-in/binary/auth -> `SKIP`. On unsupported override surface -> `SKIP`. Do not reopen model choice via alternate runner. | `codex --help` (`-m, --model`), `driver.yaml`, `docs/DRIVER-ARCHITECTURE.md` codex invocation source-of-truth section |
| `opencode-judge` | `ollama-cloud/minimax-m2.7` | **CLI flag**: pass `-m/--model` directly on the smoke invocation | `opencode run --format json -m ollama-cloud/minimax-m2.7 ...` | `live_runner`, `opencode_live`, `judge_live` | No cross-runner fallback. On missing opt-in/binary/auth -> `SKIP`. On unsupported override surface -> `SKIP`. Tolerant JSONL/text extraction remains an allowed compatibility path. | `opencode --help` (`-m, --model`), `opencode run --help` (`--format json`) |
| `opencode-dispatch` | `ollama-cloud/minimax-m2.7` | **CLI flag**: pass `-m/--model` directly on the smoke invocation | `opencode run --format json -m ollama-cloud/minimax-m2.7 ...` | `live_runner`, `opencode_live`, `dispatch_live` | No cross-runner fallback. On missing opt-in/binary/auth -> `SKIP`. On unsupported override surface -> `SKIP`. Do not reopen model choice via alternate runner. | `opencode --help` (`-m, --model`), `opencode run --help` (`--format json`) |

## Boundary notes

- [Proven] For Codex, `driver.yaml` remains the authoritative base invocation source. Live smoke policy may require the pinned model to be represented inside that config-owned argv, because the documented Codex judge contract starts from `runners.codex.args` and allows only the per-call `--output-schema` append.
- [Likely] For OpenCode, the smoke harness should pin the model with `-m/--model` at invocation time because the runner help surface exposes that flag directly and the live smoke initiative is additive test work rather than a production config redesign.
- [Likely] Live smoke rows validate the pinned runner/scenario pair itself. Therefore fallback for smoke means classified `SKIP`, not rerouting the test to a different runner/model.

## Implementation handoff

1. Add smoke-test marker wiring exactly as ratified above.
2. Gate all live smoke tests on `RUN_LIVE_RUNNER_TESTS=1`.
3. Emit skip reasons using the ratified taxonomy.
4. Preserve Codex structured-output strictness; do not count tolerant text extraction as codex-judge success.
5. Preserve OpenCode tolerant JSONL/text extraction as a valid live compatibility path.

## Open questions

- None for this policy step. The remaining work is implementation of the already-ratified test/doc/config wiring.
