# [Design] Live Smoke Test-Surface Decisions (Lightweight)

Status: finalized for `driver-live-smoke-foundation.finalize-test-surface-decisions`

## Scope

- In scope: minimum shared test surface for additive `codex`/`opencode` live smoke implementation fan-out.
- Out of scope: subprocess implementation details, assertions internals, production runner redesign, CI rollout policy.

## Model IDs (exact)

Pinned models for live smoke tests:

| Runner | Scenario | Model ID |
|--------|----------|----------|
| codex | judge | `gpt-5.4-mini` |
| codex | dispatch | `gpt-5.4-mini` |
| opencode | judge | `ollama-cloud/minimax-m2.7` |
| opencode | dispatch | `ollama-cloud/minimax-m2.7` |

These are hardcoded in `tests/live_smoke/helpers.py` as `CODEX_MODEL` and `OPENCODE_MODEL`.

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

### PASS vs explicit SKIP

| Result | Evidence Required |
|--------|------------------|
| **PASS** | Test completes with `returncode == 0` and assertions pass. Evidence includes: command, returncode, stdout, stderr. |
| **explicit SKIP** | One of the skip reasons applies. Skip is documented with reason (e.g., `pytest.skip("live_runner_opt_in_missing")`). |

A SKIP is **not** a failure - it means the test environment doesn't meet requirements. A FAIL requires:
- Full subprocess evidence (argv, returncode, stdout, stderr)
- Parse errors or assertion failures with complete diagnostics

## Evidence contract for live smoke failures (exact)

For every failing live smoke subprocess assertion, test evidence MUST include all fields below:

1. Full command (`argv`) as executed.
2. `returncode`.
3. `stdout` (captured text, even if empty).
4. `stderr` (captured text, even if empty).

Omitting any field is contract-incomplete evidence.

## Override mechanism (env-only)

Model overrides are supported **only via environment variables**:

| Runner | Environment Variable | Default | Override Example |
|--------|---------------------|---------|------------------|
| codex | `VECTL_CODEX_MODEL` | `gpt-5.4-mini` | `VECTL_CODEX_MODEL=gpt-4o` |
| opencode | `VECTL_OPENCODE_MODEL` | `ollama-cloud/minimax-m2.7` | `VECTL_OPENCODE_MODEL=other/model` |

**Important**: The `--model` flag is appended to the runner CLI invocation based on the effective model. The override mechanism is:
1. Check env var for override
2. If set, use that value
3. Otherwise, use pinned default

This applies to all live smoke test invocations.

## Manual invocation matrix (exact selections)

All commands are opt-in and manual:

### Judge smoke (both runners)
```bash
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and judge_live" tests/live_smoke/test_codex_judge_live.py tests/live_smoke/test_opencode_judge_live.py -v
```

### Dispatch smoke (both runners)
```bash
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and dispatch_live" tests/live_smoke/test_codex_dispatch_live.py tests/live_smoke/test_opencode_dispatch_live.py -v
```

### Combined smoke (all scenarios)
```bash
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner" tests/live_smoke/test_codex_judge_live.py tests/live_smoke/test_opencode_judge_live.py tests/live_smoke/test_codex_dispatch_live.py tests/live_smoke/test_opencode_dispatch_live.py -v
```

### Runner-specific selections
```bash
# Codex only (judge + dispatch)
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and codex_live" tests/live_smoke/ -v

# OpenCode only (judge + dispatch)
RUN_LIVE_RUNNER_TESTS=1 pytest -m "live_runner and opencode_live" tests/live_smoke/ -v
```

## Extraction boundaries (explicit)

### Codex judge structured-output rule

In structured codex judge coverage:
- **Success requires usable structured output** (JSON matching `VERDICT_SCHEMA`)
- **No tolerant fallback-to-text success path** - if structured output is unavailable, the test fails
- Schema rejection is classified as **FAILURE**, not a fallback opportunity

This is enforced in `test_codex_judge_live.py` - specifically in tests like `test_codex_judge_structured_output_invocation` and `test_codex_judge_schema_rejection_is_failure`.

### OpenCode judge extraction

OpenCode judge uses **tolerant JSONL/text extraction**:
- Attempts structured JSON parsing first
- Falls back to text extraction if JSON parsing fails
- This is allowed behavior for opencode - the test documents the tolerant extraction capability

Implemented in `test_opencode_judge_live.py` via `test_opencode_judge_verdict_extraction_from_jsonl`.

### Fallback policy

| Runner | Scenario | Structured Required? | Fallback Allowed? |
|--------|----------|-------------------|-------------------|
| codex | judge | Yes | **No** - strict enforcement |
| codex | dispatch | No | Yes - text extraction is fine |
| opencode | judge | No | Yes - tolerant extraction |
| opencode | dispatch | No | Yes - text extraction is fine |

**Note**: Fallback policy is for **runner unavailability/auth/runner issues only**. It does NOT reopen model selection - models are pinned and override is env-only.

## Production authorities preserved

- Production invocation contracts remain defined by existing driver authorities (`driver.yaml` and `docs/DRIVER-ARCHITECTURE.md`).
- This document only pins additive live-smoke test surface boundaries and selection semantics.
