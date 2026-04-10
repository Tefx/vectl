# Live Smoke Real Runner Validation — 2026-04-10

## Scope locked for this phase

Live runner matrix (CLI surfaces only):

1. codex × vectl-execution
2. codex × dispatch
3. opencode × vectl-execution
4. opencode × dispatch

Out of scope for this phase:

- browser/UI smoke validation (dashboard/UI suites)

## Prerequisite contract

Before classifying any live lane as product-failing, the following must be checked:

- `RUN_LIVE_RUNNER_TESTS=1` (opt-in gate)
- required runner binary exists on `PATH` (`codex`, `opencode`)
- runner auth/session is available
- required model override path is supported

Skip reasons are governed and must remain explicit:

- `live_runner_opt_in_missing`
- `live_runner_binary_missing`
- `live_runner_auth_missing`
- `live_runner_override_unsupported`

## Commands executed

```bash
uv run pytest tests/live_smoke -q
uv run pytest tests/live_smoke -q -rs
```

## Observed results

- `60 passed`
- `17 skipped`
- `0 failed`

All observed skips were opt-in/prerequisite related (`RUN_LIVE_RUNNER_TESTS` not set to `1`).
No product-failure assertions were observed in this matrix run.

## Classification boundary (must preserve)

- **Environment/prerequisite issue**: skip-governed; not a product defect.
- **Product defect**: preflight passes, live invocation runs, assertion fails with full subprocess evidence (argv, return code, stdout, stderr).

This boundary prevents hiding product bugs behind prerequisite skips and avoids misclassifying environment setup as product regressions.
