# [Design] Live Smoke Test-Surface Decisions (Lightweight)

Status: finalized for `driver-live-smoke-foundation.finalize-test-surface-decisions`

## Scope

- In scope: minimum shared test surface for additive `codex`/`opencode` live smoke implementation fan-out.
- Out of scope: subprocess implementation details, assertions internals, production runner redesign, CI rollout policy.

## Shared ownership split (pinned)

- `tests/live_smoke/helpers.py`
  - Owns shared harness wiring only (env gate checks, runner availability/auth preflight, subprocess capture envelope, common diagnostics formatting).
  - Does not own scenario-specific parsing policy.
- `tests/live_smoke/test_codex_judge_live.py`
  - Owns real codex judge structured-output behavior only.
  - No tolerant fallback-to-text success path.
- `tests/live_smoke/test_opencode_judge_live.py`
  - Owns real opencode judge extraction behavior.
  - Tolerant JSONL/text extraction is allowed here when structured JSON is not directly usable.
- `tests/live_smoke/test_codex_dispatch_live.py`
  - Owns real codex regular dispatch behavior only.
- `tests/live_smoke/test_opencode_dispatch_live.py`
  - Owns real opencode regular dispatch behavior only.

## Marker taxonomy (exact)

- Shared marker: `live_runner`
- Runner markers: `codex_live`, `opencode_live`
- Scenario markers: `judge_live`, `dispatch_live`

Expected per-test marker combinations:

- codex judge: `live_runner and codex_live and judge_live`
- opencode judge: `live_runner and opencode_live and judge_live`
- codex dispatch: `live_runner and codex_live and dispatch_live`
- opencode dispatch: `live_runner and opencode_live and dispatch_live`

## Skip semantics (exact)

- `live_runner_opt_in_missing`: `RUN_LIVE_RUNNER_TESTS` is not exactly `1`.
- `live_runner_binary_missing`: required runner binary is unavailable on `PATH`.
- `live_runner_auth_missing`: required auth/session for the runner/provider is unavailable.
- `live_runner_override_unsupported`: required model/invocation override cannot be honored on the runner path under test.

These are test-policy skips and must not be interpreted as production fallback behavior.

## Evidence contract for live smoke failures (exact)

For every failing live smoke subprocess assertion, test evidence MUST include all fields below:

1. Full command (`argv`) as executed.
2. `returncode`.
3. `stdout` (captured text, even if empty).
4. `stderr` (captured text, even if empty).

Omitting any field is contract-incomplete evidence.

## Manual invocation matrix (exact selections)

All commands are opt-in and manual:

```bash
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and judge_live" tests/live_smoke/test_codex_judge_live.py tests/live_smoke/test_opencode_judge_live.py -v
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and dispatch_live" tests/live_smoke/test_codex_dispatch_live.py tests/live_smoke/test_opencode_dispatch_live.py -v
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner" tests/live_smoke/test_codex_judge_live.py tests/live_smoke/test_opencode_judge_live.py tests/live_smoke/test_codex_dispatch_live.py tests/live_smoke/test_opencode_dispatch_live.py -v
```

## Extraction boundaries (explicit)

- Codex judge decision: in structured codex judge coverage, success requires usable structured output. There is no tolerant fallback-to-text success path in that step.
- OpenCode judge decision: tolerant JSONL/text extraction belongs only to `test_opencode_judge_live.py`.

## Production authorities preserved

- Production invocation contracts remain defined by existing driver authorities (`driver.yaml` and `docs/DRIVER-ARCHITECTURE.md`).
- This document only pins additive live-smoke test surface boundaries and selection semantics.
