# RFC: Admitted Plan Mutation — NARROW V1

## Status and authority

Status: ratified and implemented contract for the `admitted-mutation-*` phases. The private exact-byte transaction and the covered MCP/CLI routing are implemented as separate rollback units.

This RFC fixes the smallest approved persistence seam for optional, local admission of Vectl Plan-definition mutations. Its authority comes from:

- `plan.yaml` step `admitted-mutation-contract.baseline-and-rfc` and its `intent_closure.contract.v2`;
- the current persistence path in `src/vectl/io.py`;
- the covered MCP and CLI writers named below;
- `docs/ADR-unified-state.md`, which keeps `plan.yaml` authoritative and Git commits best-effort;
- `docs/RFC-atomic-plan-mutation-batch.md`, which keeps one final CAS save as the batch persistence boundary.

NARROW V1 adds one private finalization/admission/persistence transaction. It does not add a public Preview operation, a caller digest token, repository context discovery, a registry, a ledger, a daemon, or project-specific policy.

## Pre-edit baseline classification

The baseline was captured before repository edits at HEAD `a2eae092687e22a043194fad1433f43e344c1173`, with clean porcelain status and Plan SHA-256 `sha256:dbc2ba4aaf47824a0cbfb0d68af25224f47fe8b53f7ed9b74dd0f6e37c56f8c0`.

| Check | Exit | Observed baseline | Classification |
|---|---:|---|---|
| `uv run pytest` | 1 | 1 failed, 1292 passed, 15 skipped; the failure is `tests/test_mcp_mutate_batch.py::test_registered_schema_and_readme_example_match_runtime` (`KeyError: discriminator`) | pre-existing, outside this step's allowed paths |
| `uv run invar guard` | 0 | passed; 0 errors and 0 warnings | green baseline |
| `uv run ruff check .` | 1 | 2015 existing errors | pre-existing, outside this step's allowed source scope |
| `uv run mypy src` | 1 | 1261 existing errors in 31 files | pre-existing, outside this step's allowed source scope |
| `git diff --check` | 0 | no output | green baseline |
| `uv run vectl validate` | 0 | 0 errors and 45 completed-evidence warnings | warnings are historical and non-blocking outside the active dependency closure |

The corresponding mode-0600 logs are stored in the step artifact directory. A later verifier compares integrated results with this baseline. A new failure, a changed existing failure fingerprint, or a new active-closure error is a regression; historical counts do not authorize new debt.

## Goals

1. Finalize a candidate Plan once, serialize it once, and persist the exact approved bytes.
2. Preserve raw-byte CAS against the Plan bytes originally loaded.
3. Support an optional startup-frozen local admission adapter.
4. Fail before Plan, backup, or Git writes for incomplete configuration and every adapter failure.
5. Report Plan application and Git commit status separately.
6. Route the covered MCP and CLI definition mutations through this one private transaction.
7. Preserve existing behavior when every adapter configuration field is absent.

## Non-goals

- Public Preview or approve-by-token APIs.
- Caller-provided base or candidate digests.
- Repository, issue, diff, branch, policy-file, or user-context scanning.
- Adapter registries, provider hierarchies, plugins, ledgers, databases, caches, or daemons.
- Claims reconciliation or changes to `.git/vectl/claims.json`.
- Guaranteed Git rollback or transactional Git semantics.
- Admission for lifecycle, checklist, clipboard, migration, recovery, initialization, or merge-driver writes.
- Security isolation from another cooperative process running as the same operating-system user.

## Startup configuration

The shell reads and validates this exact environment tuple once when a CLI process or MCP server starts:

| Variable | Contract |
|---|---|
| `VECTL_PLAN_ADMISSION_EXECUTABLE` | absolute path to one regular executable file |
| `VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256` | lowercase `sha256:<64 hex>` digest of that file's startup bytes |
| `VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS` | base-10 integer from 1 through 300 |

Configuration states are fixed for that process lifetime:

- all three fields absent: `not_configured`; mutations retain current behavior;
- all three fields present and valid: `configured`;
- any partial tuple, empty value, relative/non-regular/non-executable path, malformed digest, digest mismatch, or timeout outside the bound: `configuration_invalid`.

`configuration_invalid` is retained as startup state. Every covered mutation fails closed before candidate persistence. Runtime code does not reread these variables per request. There is no command string, argument override, shell snippet, dynamic adapter lookup, or fallback executable.

Immediately before each configured invocation, Vectl resolves and hashes the executable again. Path, file identity, or digest drift produces `executable_identity_mismatch` and zero writes.

## Private transaction

The private transaction accepts an already parsed in-memory mutation result and the existing commit message policy. It performs these ordered stages:

1. Read the authoritative `plan.yaml` with `read_bytes()` and retain those exact base bytes.
2. Compute `base_sha256` over the raw bytes and parse the Plan from those bytes.
3. Deep-copy the supplied candidate so caller-owned state cannot change during finalization.
4. Recalculate locks and run the same selected structural validation policy used by current definition mutation surfaces. Historical completed-evidence diagnostics remain excluded from structural mutation admission.
5. The transaction must serialize the finalized managed Plan exactly once to one immutable `candidate_bytes` value.
6. Compute `candidate_sha256` from `candidate_bytes`.
7. If configured, invoke the admission adapter described below and require approval bound to `candidate_sha256`.
8. Acquire the existing per-Plan file lock.
9. Re-read the current raw Plan bytes and compare their SHA-256 with `base_sha256`. A mismatch raises the existing CAS conflict and writes nothing.
10. Atomically replace `plan.yaml` with the exact `candidate_bytes` buffer. No reserialization is allowed after approval.
11. Read back the persisted raw bytes and require `persisted_sha256 == candidate_sha256`. A mismatch is a persistence error and must never be represented as success.
12. Create the existing backup according to current linked-worktree policy and attempt the existing `git commit --only --no-verify plan.yaml ...` path.
13. Return the receipt defined below.

`src/vectl/io.py::_finalize_admitted_plan_mutation` implements this boundary. Its module-import configuration snapshot freezes the adapter tuple once per CLI/MCP process. Covered callers pass their parsed in-memory candidate into this transaction without adding admission controls to the public mutation schema.

The exact-byte invariant is:

```text
sha256(adapter_candidate_bytes)
  == candidate_sha256
  == sha256(bytes passed to atomic replace)
  == sha256(persisted plan.yaml bytes)
```

Raw-byte CAS compares the original `read_bytes()` payload. Decoding and re-encoding, newline normalization, YAML round trips, or a second serializer call cannot supply the base identity or approved candidate bytes.

## Admission process contract

For configured mode, Vectl creates one repository-external temporary directory. Before creating the workspace, it resolves the selected temporary parent and rejects a parent that is unavailable, is not a writable directory, or is the repository or one of its descendants. That rejection uses `adapter_crash` with fixed bounded text and creates no repository descendant. Vectl then pre-creates these files with mode `0600`:

- `base.plan.yaml`: the exact loaded base bytes;
- `candidate.plan.yaml`: the exact finalized candidate bytes;
- `verdict.json`: initially empty and reserved for the adapter verdict.

Workspace creation failures use `adapter_crash` with the fixed reason `repository-external admission workspace is unavailable`. An `OSError` while setting up or writing any private file also uses `adapter_crash`, with the fixed reason `admission private file setup or write failed`. Initial base/candidate identity setup failures from `lstat` or `read_bytes`, and initial verdict identity setup failures from `lstat`, use that same code and fixed reason before the adapter launches. The external workspace is removed on these failures; neither reason includes the workspace path, protected Plan bytes, or the underlying operating-system diagnostic.

Vectl treats base and candidate files as immutable inputs: it writes each once, records identity, and checks device, inode, mode, size, timestamps, and exact-byte hash after adapter exit. An input that is missing, unreadable, replaced, or no longer regular returns `candidate_identity_mismatch` with the fixed reason `admission adapter changed an immutable Plan input`. Mode `0600` protects against other users; it does not sandbox the same UID.

The only allowed invocation is this argv, with `shell=False`:

```text
[
  VECTL_PLAN_ADMISSION_EXECUTABLE,
  "--base", <absolute base.plan.yaml path>,
  "--candidate", <absolute candidate.plan.yaml path>,
  "--verdict", <absolute verdict.json path>
]
```

Vectl uses the repository root as `cwd`, does not interpolate a shell, captures stdout and stderr, and applies `VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS`. The adapter receives no caller arguments. Vectl removes its temporary directory after the result is fully read and classified.

Approval requires process exit 0 plus one UTF-8 JSON verdict with no duplicate or unknown keys:

```json
{
  "schema_version": "vectl.plan-mutation-admission.v1",
  "decision": "approve",
  "candidate_sha256": "sha256:<64 lowercase hex>"
}
```

`decision` must be a string equal to `approve` or `reject`; arrays, objects, and every other value use `malformed_verdict` with the fixed reason `admission verdict decision is invalid`. A rejection uses the same schema with `decision: "reject"` and one required non-empty `reason`. `reason` is forbidden on approval. The candidate digest must exactly equal the digest computed by Vectl. An `OSError` while reading the identity-checked verdict uses `malformed_verdict` with the fixed reason `admission verdict file is unavailable`.

Bounds:

- verdict file: at most 16 KiB;
- rejection reason: at most 1024 Unicode code points after trimming;
- retained stdout and stderr diagnostics: at most 8 KiB each;
- timeout: 1 through 300 seconds from the startup tuple.

Diagnostics preserve bounded nonsecret context while removing NUL and non-printing control characters. Before a diagnostic is returned, Vectl redacts absolute filesystem paths (including verifier-owned external temporary paths), secret-like tokens, opaque credential-like values, and Plan-derived fragments found in either exact base or candidate input. An `Authorization: Bearer <payload>`, `Authorization: Basic <payload>`, or `Authorization: Digest <payload>` field is redacted as one complete value so a short payload cannot survive token-level redaction. This applies to configured nonzero adapter output and valid rejection reasons. Vectl then normalizes whitespace and applies the 1024-code-point reason bound. Fixed timeout and executable-identity reasons contain no adapter-controlled text. Diagnostics never include Plan bytes, authorization payloads, or protected paths. Stable failure codes are:

- `configuration_invalid`;
- `executable_identity_mismatch`;
- `adapter_timeout`;
- `adapter_crash` for nonzero exit, launch failure, unavailable repository-external workspace creation, or private-file setup/write failure;
- `adapter_output_too_large`;
- `malformed_verdict` for verdict-read failure or encoding, JSON, duplicate-key, field, schema, decision, or reason violations;
- `candidate_identity_mismatch` for verdict digest mismatch or a changed, missing, unreadable, replaced, or non-regular base/candidate input;
- `rejected` for a valid rejection verdict.

Every listed failure returns before Plan replacement, backup mutation, Git invocation, or repository-local temporary state. The caller receives the stable code plus one bounded sanitized reason. It cannot reinterpret malformed or missing output as approval.

## Cooperative same-UID trust boundary

The adapter and Vectl run under one operating-system UID. Mode `0600`, exact executable hashing, fixed argv, and digest checks provide deterministic cooperation and audit evidence. They do not prevent a malicious or compromised same-UID adapter from modifying repository files, Git state, environment-visible resources, or the adapter executable itself.

NARROW V1 therefore treats adapter configuration as trusted local operator configuration. It does not claim sandboxing, multi-tenant isolation, credential isolation, or containment. Strong isolation requires a separately authorized process/container boundary.

## Mutation receipt

A successful private transaction returns this minimum machine receipt:

```json
{
  "schema_version": "vectl.plan-mutation.receipt.v1",
  "plan_applied": true,
  "base_sha256": "sha256:<raw loaded bytes>",
  "candidate_sha256": "sha256:<approved bytes>",
  "persisted_sha256": "sha256:<read-back bytes>",
  "admission_status": "not_configured | approved",
  "git_attempted": true,
  "git_committed": false
}
```

`git_attempted` reflects whether the caller supplied the existing commit-message path. `git_committed` reports the observed best-effort Git result. A successful Plan write remains applied when Git fails, matching `docs/ADR-unified-state.md`. The receipt never equates `plan_applied` with `git_committed`.

Configured failures return a bounded typed error and no success receipt. CAS failure retains the existing CAS error identity. Successful covered MCP and CLI commands append `mutation_receipt: <JSON>` with the complete receipt fields above. MCP batch mode also retains its existing unprefixed `plan_hash`, derived from `persisted_sha256`. The existing human-readable response prefix remains unchanged. CLI commands pass a commit message only when the selected Plan belongs to the current repository/worktree; other explicit Plan paths remain Plan-only and report `git_attempted: false`. No surface claims rollback after a Git failure.

The Git helper continues using `git commit --only --no-verify plan.yaml`; unrelated staged files remain untouched.

## Covered writers

NARROW V1 routes exactly these definition mutations through the private transaction:

- MCP `vectl_mutate` single-action mode;
- MCP `vectl_mutate` `requests` batch mode;
- CLI `add-step`;
- CLI `add-steps`;
- CLI `edit-step`;
- CLI `remove-step`;
- CLI `move-step`;
- CLI `add-phase`;
- CLI `edit-phase`;
- CLI `edit-plan`.

The existing parsing and in-memory mutation primitives remain authoritative. Wiring must preserve omission versus explicit empty values, generated versus explicit IDs, dependency edit modes, refs behavior, linked-worktree checks, CAS diagnostics, and compatible response text apart from the minimum receipt fields.

## Excluded writers

NARROW V1 does not reroute or change:

- claim, complete, defer, reject, skip, or skip-phase lifecycle operations;
- checklist operations;
- clipboard writes;
- init, migration, recovery, merge-driver, backup, or claims-repair flows;
- direct low-level helpers used by isolated tests or internal recovery paths;
- writes to claims, registries, caches, daemons, databases, services, or user credentials.

An excluded writer does not silently gain admission semantics. Expanding coverage requires a new RFC decision and synchronized tests/docs.

## Expected-red selected-execution observer

`tests/expected_red.py` remains a test helper. It adds these proof-only commands:

```text
python3 tests/expected_red.py select pytest <node-id>...
python3 tests/expected_red.py run pytest <node-id>...
```

`select` invokes real pytest collection with `--collect-only`; test bodies do not run. It emits one compact JSON object:

```json
{"schema_version":"vectl.check.selection.v1","selected_ids":["<collected node id>"]}
```

`run` first performs the same collection and emits the selection object. Only after successful collection does it execute pytest. A pytest hook records actual `pytest_runtest_logstart` node IDs, and the command emits:

```json
{"schema_version":"vectl.check.evidence.v1","selected_ids":["<collected node id>"],"executed_ids":["<started node id>"],"exit_code":0,"outcome":"green"}
```

A nonzero pytest result remains authoritative in the inner envelope: `exit_code` contains pytest's real nonzero code and `outcome: "red"` records the result. After emitting a valid evidence envelope, the outer adapter process exits 0 so the production Gate can parse that inner result. Outer 0 only confirms evidence transport; it does not classify the tests as green. Pytest terminal output is preserved on stderr so infrastructure and assertion fingerprints remain observable. Gate selection requires exact non-empty selected/executed identity.

This observer is proof-only developer-test infrastructure. Product admission code must not import, execute, or copy it. It creates no product registry, policy engine, or mutation authorization path. Existing expected-red marker metadata behavior remains unchanged.

## Rollout

1. Land this RFC, observer, and focused observer tests with no product-source change.
2. Add expected-red contract tests with fixed node IDs and owners.
3. Implement the exact-byte private transaction and flip its four assigned nodes. This repository step supplies that implementation.
4. Route the covered MCP/CLI writers and flip the remaining four nodes. This repository step supplies that separate rollback unit.
5. Run integrated selected-execution, real registered MCP/CLI seams, competing-candidate CAS, full regression comparisons, and fresh review.
6. Keep the adapter tuple absent by default. Operators enable all three fields together only after validating the local adapter digest and behavior.

## Rollback and recovery

- Before product implementation, rollback removes this RFC, observer additions, and their developer tests.
- After implementation, revert the implementation commit as one unit and leave all adapter variables absent.
- A partial configuration must remain visibly fail-closed; operators clear all three fields or correct the complete tuple and restart the process.
- Unknown Plan, backup, index, HEAD, claims, lock, or adapter side effects require operator reconciliation. Vectl does not retry an ambiguous write.
- No rollback path pushes, rewrites unrelated Git history, edits claims, or restores unrelated staged files.

## Rejected full Preview design

A broader design would expose public Preview, return candidate bytes or digest tokens to callers, accept later approve-by-digest requests, scan repository context, and retain a mutation ledger or cache. That design expands compatibility, stale-token, privacy, authority, lifecycle, and recovery surfaces before one local persistence seam has evidence.

NARROW V1 keeps preview internal to one transaction, binds approval to repository-external exact bytes, and discards temporary state after one attempt. Future public Preview work requires separate product intent, API compatibility, threat-model, lifecycle, and rollback authority.

## Acceptance criteria

- The baseline table and mode-0600 artifacts classify every pre-edit command result.
- The observer proves collection precedes execution and emits matching installed selection/evidence envelopes.
- All covered writers converge on one private transaction.
- Configured partial state and every adapter failure write no Plan, backup, Git, or repository-local temporary state.
- The temporary parent is repository-external before workspace creation, and every changed, missing, unreadable, replaced, or non-regular immutable input returns the fixed `candidate_identity_mismatch` failure.
- CAS uses raw loaded bytes and persisted bytes equal the one approved serialization buffer.
- Valid unconfigured mode preserves existing mutation behavior.
- The receipt separates Plan application from Git attempt/commit status.
- Excluded writers and the cooperative same-UID limit are explicit.
- Rollout, rollback, and rejected Preview scope remain synchronized with tests and public documentation in later implementation steps.
