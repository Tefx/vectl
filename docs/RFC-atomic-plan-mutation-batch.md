# RFC: Atomic Multi-Operation Plan Mutation

## Status
Implemented.

Priority: P1.

Runtime implementation: `src/vectl/mcp_core_mutation_tools.py` and `src/vectl/mcp_mutation_contracts.py`.

Contract and atomicity verification: `tests/test_mcp_mutate_batch.py`. The registered FastMCP schema, copyable README request, single- and batch-action `refs` persistence, indexed field errors, CAS behavior, and all-or-nothing persistence are executable acceptance evidence.

## Problem

One coherent Plan repair currently requires several independent `vectl_mutate` calls, such as:

1. add a phase;
2. add its steps;
3. edit dependencies or metadata;
4. adjust individual steps.

Each call independently loads `plan.yaml`, recalculates locks, performs a CAS save, and attempts a Git commit. A multi-operation repair therefore exposes intermediate topology, performs avoidable I/O, and can leave only part of the intended repair applied when a later call fails.

The current implementation surface is `src/vectl/mcp_core_mutation_tools.py::vectl_mutate`. It accepts one action, mutates one loaded `Plan`, and calls `_save_plan` once for that action.

Existing narrower analogues do not cover this case:

- `src/vectl/core_plan_step_add.py::add_steps_bulk` batches step creation inside one phase and resolves dependencies in two passes.
- `src/vectl/core/checklist_batch.py::mutate_checklist_batch_impl` validates a checklist batch before applying marker changes.

No existing surface composes phase, step, edit, move, or removal actions into one atomic Plan mutation.

## Goals

- Extend the existing `vectl_mutate` MCP tool with an alternative batch input.
- Load one Plan and expected definition hash per batch.
- Apply requests in order to one in-memory working Plan.
- Allow later requests to target explicit IDs created earlier in the same batch.
- Persist the batch through one lock recalculation, one final validation stage, one CAS save, and one Git commit attempt.
- Return ordered per-request results, the final Plan hash, and any lock-change notice.
- Preserve existing single-action calls and responses.
- Fail without partial batch persistence when any request or final validation fails.

## Constraints

- `plan.yaml` remains the authoritative Plan state.
- Existing CAS conflict semantics remain authoritative.
- Git persistence remains best-effort as defined by `docs/ADR-unified-state.md`: the Plan write succeeds even if the subsequent Git commit attempt fails.
- Claims remain external coordination state and are outside this transaction.
- The linked-worktree mutation guard remains unchanged.
- No external dependency may be added.
- No Plan schema or version change is permitted.

## Decision
Add an optional, typed `requests` parameter to `vectl_mutate`. `requests` is an alternative to the existing top-level single-action form.

One action-discriminated contract defines each request's exact properties and required identifiers. FastMCP derives the public JSON Schema from that contract, while the pre-load Vectl validator consumes the same allowed and required field sets for direct/internal calls and actionable errors. Runtime extra fields are retained only until that validator rejects them; the advertised schema remains closed with `additionalProperties: false`.

The MCP shell owns input-shape validation, batch coordination, persistence, and response formatting. Existing Plan mutation functions remain the only owners of action semantics.

The batch executes against a deep working copy of the loaded Plan. The working copy is discarded on any pre-save failure. The only persistent boundary is the final CAS save.

## Architecture Basis

```yaml
architecture_basis:
  system_layers:
    - layer: mcp_shell
      owner: src/vectl/mcp_mutation_contracts.py and src/vectl/mcp_core_mutation_tools.py
      responsibility: Publish and validate the public input shape, coordinate one batch, and format MCP responses.
      depends_on: [plan_core, validation_core, persistence_shell]
    - layer: plan_core
      owner: src/vectl/core_plan_step_add.py and src/vectl/core_plan_step_edit.py
      responsibility: Apply existing deterministic in-memory Plan mutations.
      depends_on: [canonical_models]
    - layer: validation_core
      owner: src/vectl/core_plan_queries.py and src/vectl/core_plan_validation.py
      responsibility: Recalculate phase locks and reject invalid final topology.
      depends_on: [canonical_models]
    - layer: persistence_shell
      owner: src/vectl/mcp_core_common.py and src/vectl/io.py
      responsibility: Enforce file locking, CAS, atomic replacement, backup, and best-effort Git commit.
      depends_on: [canonical_models, filesystem, git]
    - layer: canonical_models
      owner: src/vectl/models.py
      responsibility: Define Plan, Phase, Step, statuses, and canonical validation types.
      depends_on: []

  source_of_truth_matrix:
    - datum: batch request fields, required identifiers, and machine schema
      authority: typed request union in src/vectl/mcp_mutation_contracts.py
      writers: [mcp_shell]
      readers: [FastMCP registration, pre-load batch validator, contract tests]
    - datum: persisted Plan definition and inline runtime fields
      authority: plan.yaml
      writers: [persistence_shell]
      readers: [mcp_shell, existing CLI and MCP surfaces]
    - datum: optimistic concurrency version
      authority: SHA-256 of the loaded plan.yaml bytes
      writers: [persistence_shell]
      readers: [mcp_shell, persistence_shell]
    - datum: batch candidate
      authority: one transient deep in-memory Plan copy
      writers: [mcp_shell through plan_core]
      readers: [validation_core, persistence_shell after validation]
    - datum: active claims
      authority: .git/vectl/claims.json
      writers: [existing claim lifecycle only]
      readers: [existing claim lifecycle]
      batch_policy: untouched and outside the transaction
    - datum: audit history
      authority: Git history when the best-effort commit succeeds
      writers: [persistence_shell]
      readers: [Git tooling]
      batch_policy: secondary audit trail; plan.yaml remains authoritative

  service_catalog:
    - service: vectl_mutate
      owner: mcp_shell
      nature: imperative shell
      public_surface: existing FastMCP tool
    - service: existing Plan mutation primitives
      owner: plan_core
      nature: in-memory core
      public_surface: internal Python calls
    - service: lock and structural validation
      owner: validation_core
      nature: deterministic core
      public_surface: internal Python calls
    - service: CAS Plan save
      owner: persistence_shell
      nature: imperative shell
      public_surface: private MCP save helper plus existing io.save_plan

  runtime_contract:
    entrypoint: existing MCP tool vectl_mutate served by `uvx vectl mcp`
    input_modes:
      - single_action: existing action plus top-level fields
      - batch: non-empty requests list with explicit create IDs
    atomic_boundary: one Plan working copy through one CAS save
    success_output: ordered Markdown request results, saved Plan hash, and lock notice
    failure_output: input_shape, request[i], stage=final_validation, or existing CAS conflict diagnostics
    allowed_dependencies:
      - Python standard library already used by the repository
      - existing Pydantic, FastMCP, returns, and Invar runtime dependencies
      - existing vectl modules named in this RFC
    forbidden_dependencies:
      - new transaction frameworks
      - databases or durable ledgers
      - new ID-reference languages
      - new external packages without separate approval

  state_strata:
    - stratum: authoritative_persistent
      contents: plan.yaml
      mutation_rule: only persistence_shell may write after final validation and CAS success
    - stratum: coordination_persistent
      contents: .git/vectl/claims.json
      mutation_rule: batch Plan mutation must not read-modify-write claims
    - stratum: transient_candidate
      contents: deep Plan working copy and ordered request results
      mutation_rule: discard completely on any pre-save failure
    - stratum: audit_best_effort
      contents: Git commit for plan.yaml
      mutation_rule: one attempt after a successful Plan write; failure does not roll back plan.yaml

  transport_boundary_rules:
    allowed_inbound:
      - MCP mappings containing the existing action vocabulary
      - strings, booleans, string lists, and existing add-steps entry mappings
    forbidden_inbound:
      - active file handles, Git objects, claim handles, Plan instances, callbacks, or external-effect commands
      - requests nested inside a request
      - symbolic cross-request ID references
    allowed_outbound:
      - Markdown text matching the existing tool return type
      - deterministic request indexes and final Plan hash
    forbidden_outbound:
      - internal Plan objects
      - filesystem or claim handles
      - claims that Git commit success is transactional with the Plan write

  cross_cutting_governance:
    registries: "N/A: the action vocabulary is fixed by the existing vectl_mutate contract; no registry is introduced."
    lifecycle_ordering:
      - validate outer shape and request schemas
      - linked-worktree guard
      - load once and deep-copy
      - apply ordered requests
      - recalculate locks once
      - perform final model and structural validation
      - CAS save once
      - backup and best-effort Git commit
    coordination_mechanisms:
      - plan-file flock serializes CAS and write access
      - expected definition hash detects concurrent writers
    wiring_strategy: explicit private function calls; no dependency-injection container or global mutable state
    governance_owner:
      input_and_batch_lifecycle: mcp_shell
      mutation_semantics: plan_core
      final_validity: validation_core
      durable_write_and_commit_attempt: persistence_shell

  shared_abstractions:
    shared_types:
      - name: BatchMutationRequest
        owner_module: src/vectl/mcp_mutation_contracts.py
        consumers: [FastMCP registration, pre-load batch validator, contract tests]
        rationale: one authority for action fields, required identifiers, and registered schema
      - name: Plan
        owner_module: src/vectl/models.py
        consumers: [mcp_shell, plan_core, validation_core, persistence_shell]
        rationale: canonical state crossing every existing internal boundary
      - name: PlanError and CASConflictError
        owner_module: src/vectl/models.py
        consumers: [mcp_shell, plan_core, persistence_shell]
        rationale: existing error contract shared by mutation and persistence paths
    shared_protocols: "N/A: one implementation exists for each required role; a new Protocol would add unused indirection."
    shared_utilities:
      - name: private save receipt helper
        owner_module: src/vectl/mcp_core_common.py
        consumers: [src/vectl/mcp_core_mutation_tools.py, existing _save_plan compatibility wrapper]
        rationale: expose the saved hash without duplicating CAS translation, backup, or lock-notice behavior
    decision: Reuse canonical Plan and error types; add only one private save helper required by the batch response.

  module_split_recommendations:
    - module: src/vectl/mcp_mutation_contracts.py
      owner: mcp_shell
      change_reason: typed request schemas and validator field sets must share one authority
      allowed_dependencies: [typing, existing Pydantic]
      forbidden_dependencies: [Plan mutation, filesystem, claims, Git]
    - module: src/vectl/mcp_core_mutation_tools.py
      owner: mcp_shell
      change_reason: batch input and orchestration change together with the existing MCP mutation surface
      allowed_dependencies: [plan_core, validation_core, persistence_shell, canonical_models]
      forbidden_dependencies: [claims mutation, direct Git subprocesses, new external packages]
    - module: src/vectl/mcp_core_common.py
      owner: persistence_shell
      change_reason: the saved hash must be exposed while preserving _save_plan compatibility
      allowed_dependencies: [src/vectl/io.py, validation_core, canonical_models]
      forbidden_dependencies: [batch request parsing, action dispatch]
    - module: tests/test_mcp_mutate_batch.py
      owner: batch_contract_tests
      change_reason: isolate atomicity and compatibility evidence from the oversized general MCP test file
      allowed_dependencies: [public MCP tool surface, focused private instrumentation where call-count proof is required]
      forbidden_dependencies: [production-only test hooks, direct plan.yaml mutation as an implementation path]
    - module: README.md and docs/DESIGN.md
      owner: public_and_architecture_docs
      change_reason: publish the new request contract and persistence boundary with implementation parity
      allowed_dependencies: [this RFC, verified public behavior]
      forbidden_dependencies: [claims of stronger Git durability than ADR-unified-state]

  ux_surfaces:
    - surface: MCP vectl_mutate request and Markdown response
      scope: XOR input shape, request indexes, explicit IDs, final hash, lock notice, and actionable failures

  runtime_surfaces:
    - surface: API/server
      launch_or_entrypoint: "uvx vectl mcp"
      minimum_liveness_proof: invoke existing single-action mutation and the new batch form through the registered MCP tool against an isolated Git-backed Plan fixture

  open_questions: []
  readiness: READY
```

Rationale: this basis preserves the existing dependency direction from MCP shell to in-memory Plan logic to one CAS persistence edge. It assigns every changed module to one owner and introduces no implicit shared state.

### Core and shell boundary

- **In-memory Plan logic:** existing `add_phase`, `add_step`, `add_steps_bulk`, `edit_phase`, `edit_step`, `edit_plan`, `remove_step`, and `move_step` functions.
- **MCP shell:** batch input validation, ordered dispatch, error indexing, working-copy ownership, and Markdown response formatting.
- **Validation boundary:** lock recalculation, Pydantic model revalidation, and `validate_plan` structural checks.
- **I/O shell:** plan-file lock, CAS comparison, atomic replacement, backup, and best-effort Git commit.

No mutation function may write files, claims, Git state, or external systems.

## Public Input Contract
Conceptual MCP signature:

```python
vectl_mutate(
    action: MutationAction | None = None,
    # existing single-action fields remain unchanged
    requests: list[BatchMutationRequest] | None = None,
    max_issues: int | None = None,  # diagnostic output bound only
)
```

`BatchMutationRequest` is an action-discriminated union. Every action branch publishes exact properties, required fields, and `additionalProperties: false`; nested `add-steps` entries are closed schemas as well.

### Outer-shape rules

1. Exactly one of `action` or `requests` must select the mutation mode.
2. `requests` must be a non-empty list.
3. When `requests` is present, `action` and every semantically non-default top-level single-action field are rejected.
4. An explicitly supplied default value that has no mutation effect is equivalent to omission.
5. Every request must be a mapping with one supported `action`.
6. Unknown fields and fields irrelevant to the selected action are rejected before Plan load.
7. Malformed requests report their zero-based `request[i]` index when validation reaches the Vectl tool boundary.
8. Unknown-field errors name the action, rejected fields, allowed fields, and known remediation such as `phase_id` versus `step_id` or `refs` versus edit-only `add_refs`/`remove_refs`.
9. `max_issues` is the sole caller-supplied shared diagnostic-output option; it
   bounds final-validation summaries and does not change batch mutation semantics.
   There are no other caller-supplied shared batch options.

### Per-action fields

| Action | Allowed request fields after `action` | Required fields |
|---|---|---|
| `add-phase` | `phase_id`, `name`, `depends_on`, `gate` | `phase_id`, `name` |
| `add-step` | `phase_id`, `step_id`, `name`, `description`, `depends_on`, `verification`, `evidence_template`, `refs`, `status`, `evidence`, `skipped_reason`, `agent` | `phase_id`, `step_id`, `name` |
| `add-steps` | `phase_id`, `steps` | `phase_id`, `steps`; every entry requires `id`, `name` |
| `edit-phase` | `phase_id`, `name`, `context`, `gate`, `depends_on` | `phase_id` |
| `edit-step` | `step_id`, `new_step_id`, `name`, `description`, `depends_on`, `verification`, `evidence_template`, `agent`, `add_refs`, `remove_refs`, `refs` | `step_id` |
| `remove-step` | `step_id`, `force` | `step_id` |
| `move-step` | `step_id`, `target_phase` | `step_id`, `target_phase` |
| `edit-plan` | `project_guidance`, `strategy_ref`, `plan_context` | at least one metadata field |

The existing single-action form keeps its current permissiveness and interpretation. `refs` is accepted and persisted when single-action or batch `add-step` creates a step. Incremental `add_refs` and `remove_refs` remain edit-step-only fields. Stricter action-specific field rejection applies to `requests`.

## Explicit ID and Replay Contract

Every create operation in batch mode requires an explicit ID:

- `add-phase` requires `phase_id`;
- `add-step` requires `step_id`;
- every `add-steps` entry requires `id`.

Later requests use those exact IDs. Cross-request symbolic references and result placeholders are unsupported. `add_steps_bulk` may continue supporting its existing short-slug references within one `add-steps` request.

This rule makes replay safe without a durable mutation ledger. Replaying an already-applied batch fails on duplicate IDs before persistence instead of silently creating suffixed phases or steps.

Single-action auto-generated IDs remain unchanged.

## Atomic Execution Contract

A batch follows this order:

1. Validate the outer input shape.
2. Validate all request mappings, action names, allowed fields, and basic field types.
3. Apply the existing linked-worktree guard.
4. Call `_load()` once to obtain the Plan and expected definition hash.
5. Create a deep working copy of the loaded Plan.
6. Apply each normalized request in order through one private action dispatcher.
7. Record one ordered result for each successful request.
8. After every request succeeds, call `recalc_lock_status()` once on the working copy.
9. Run one final validation stage:
   - revalidate the complete model through the canonical `Plan` model;
   - retain a Pydantic location component only when the `Plan`, `Phase`, `Step`, `Clipboard`, or `RejectionEntry` schema expected at that path admits the field or list index; map unknown components to `<field>`;
   - retain only Pydantic Core built-in error types, map unknown/custom types to `validation_error`, and never copy `msg`, input, context, or URLs;
   - convert any unexpected canonical extraction/normalization failure to one fixed `unexpected_validation_error` without exception/candidate text;
   - call `validate_plan` once with `check_refs=False` and `include_completed_evidence_guard=False` when canonical validation succeeds;
   - convert an unexpected structural-validator exception to one fixed pre-save `unexpected_validation_error` without exception/candidate text, while keeping ordinary structural identifiers actionable;
   - collect every deterministic non-warning issue in validator order;
   - route canonical and structural failures through one formatter: `max_issues=N` renders N rows, `None` renders all rows, and every control-escaped issue value is capped at 512 codepoints with explicit count/value flags.
10. Perform one CAS save using the original expected definition hash and with save-time lock recalculation disabled.
11. Attempt one Git commit through the existing save path.
12. Return ordered request results, the exact hash returned by the successful save, and the precomputed lock notice.

Completed-evidence validation is excluded because this surface edits Plan structure. Historical evidence debt must not prevent a structurally valid repair. Model, duplicate-ID, dependency, DAG, and status-consistency failures remain blocking.

### Atomicity invariants

- No request failure may call the save path.
- Final validation failure may not call the save path.
- CAS conflict occurs before the batch writes `plan.yaml` or attempts its Git commit.
- A failed batch may not mutate claims.
- A failed batch may not persist recalculated phase lock statuses.
- Successful persistence contains every request effect or none of them.
- Intermediate in-memory topology is never externally visible.

## Internal Interface Changes
### `src/vectl/mcp_mutation_contracts.py`

Own the internal typed request union for all eight batch actions and the nested `add-steps` entry. Export allowed and required field sets derived from those types so FastMCP schema generation and pre-load validation cannot drift independently. Keep unknown fields available to the Vectl validator for actionable diagnostics while publishing closed object schemas.


### `src/vectl/mcp_core_mutation_tools.py`
`vectl_mutate` selects single-action or batch mode. Its `requests` annotation references the typed action union so registered clients receive an exact machine schema.

The pre-load validator derives allowed and required fields from `mcp_mutation_contracts`, preserves indexed Vectl errors for direct/internal calls, and adds allowed-field and remediation details for unknown input. Both modes use the same private action dispatcher. `add-step` forwards optional `refs` to the existing core `add_step` primitive in single and batch modes.

No second action implementation path is permitted.

### `src/vectl/mcp_core_common.py`

Add a private save receipt helper equivalent to:

```text
_save_plan_receipt(...) -> (new_plan_hash, lock_notice)
```

The existing `_save_plan(...)` remains a compatibility wrapper that returns only the current lock notice. Existing callers and single-action response text therefore remain unchanged.

The batch calls the receipt helper after performing its own single lock recalculation and passes `recalc_locks=False`.

The batch must not call `save_plan` directly because that would duplicate CAS error translation, backup behavior, and persistence policy.

## Error Contract
| Failure stage | Response identity | Batch persistence |
|---|---|---|
| Conflicting or missing outer shape | `input_shape` | No load or write |
| Unknown/irrelevant request field | `request[i]`, action, rejected fields, allowed fields, remediation when known | No load or write |
| Other malformed request | `request[i]` and action when available | No write |
| Unknown or ambiguous target | `request[i]` | No write |
| Duplicate ID or dependency violation | `request[i]` | No write |
| Final model or Plan validation | `stage=final_validation` | No write |
| CAS conflict | Existing actionable CAS conflict message | No batch write or commit |
| Git commit failure | Existing best-effort behavior and logging | Plan remains written |

FastMCP may reject missing required fields from the typed schema before the Python function executes. Errors produced by Vectl's pre-load validator always include the exact zero-based request index. Final whole-Plan validation errors are not assigned a fabricated request index.

## Success Response Contract

Batch mode continues returning Markdown so the existing MCP return type remains stable.

Example shape:

```text
## Plan mutation batch

- request[0] add-phase: Added phase `provider-recovery`
- request[1] add-steps: Added 3 steps
- request[2] edit-phase: Updated phase `provider-recovery`
- request[3] edit-step: Updated step `provider-recovery.verify`

requests_applied: 4
plan_hash: <sha256>

[vectl] Lock status updated: ...
```

Single-action output remains byte-for-byte compatible except for unrelated pre-existing nondeterminism such as generated IDs.

## Persistence and Concurrency Semantics

The existing `save_plan` path already provides:

1. plan-specific `flock` serialization;
2. CAS comparison while holding the lock;
3. temp-file write and atomic rename;
4. final SHA-256 calculation;
5. one best-effort Git commit attempt when a commit message is supplied.

The batch commit message should identify one batch operation and request count. The batch does not provide atomicity between the filesystem write and Git itself. Changing that durability model is outside this RFC.

A concurrent writer may legitimately change the Plan or Git HEAD. The batch guarantee is limited to producing no partial batch persistence after a CAS conflict.

## Compatibility

- Existing `action=...` callers retain the same fields, mutation semantics, save behavior, and response format.
- The FastMCP schema will make `action` optional so `requests` can be the alternative selector; runtime validation enforces the XOR rule.
- Existing `add-steps` behavior remains available in both modes.
- The linked-worktree guard and `VECTL_PLAN_PATH` escape hatch remain unchanged.
- The CLI has separate mutation commands and no generic `mutate` abstraction, so this RFC adds no CLI batch surface.

## Test Requirements
`tests/test_mcp_mutate_batch.py` owns the focused contract instead of extending the already large `tests/test_mcp.py`.

Required cases:

1. **Ordered success:** add one explicit phase, add explicit steps with dependencies, edit the phase, and edit one step. The final Plan validates and contains every change.
2. **Single execution boundary:** instrument `_load`, `recalc_lock_status`, the save helper, and `_git_commit_plan`; each is called once on success.
3. **Returned hash:** `plan_hash` equals the SHA-256 of the saved `plan.yaml` bytes.
4. **Request failure:** a later request contains an invalid dependency or duplicate ID; the error identifies its request index, while Plan bytes, claims bytes, persisted lock statuses, and Git HEAD remain unchanged.
5. **Final validation failure:** an injected candidate with independent phase, dependency, and step blockers is checked by the real validator; the response reports all blocking diagnostics in order, with no write or commit. A bounded response is the requested prefix, and a corrected follow-up succeeds.
6. **CAS conflict:** another writer changes `plan.yaml` between load and save; the writer's bytes remain authoritative and the batch produces no commit.
7. **Mixed input:** `requests` combined with `action` or a meaningful top-level single-action field fails before load.
8. **Replay:** a successful explicit-ID batch fails safely on the second execution without a second write or commit.
9. **Single-action characterization:** existing response text remains unchanged.
10. **Refs persistence:** single-action and batch `add-step` both persist `refs` exactly.
11. **Actionable strict errors:** unknown fields fail before load and report request index, action, allowed fields, and identifier/ref remediation.
12. **Registered schema:** a real stdio `uv run vectl mcp` session exposes an action-discriminated union with exact fields, required IDs, closed action objects, and closed `add-steps` entries.
13. **Executable documentation:** the marked README JSON example is parsed without modification, sent through that registered stdio tool, and its persisted refs are asserted.
14. **Existing suite:** all current mutation, CAS, Git, linked-worktree, validation, and add-steps tests remain green.

Verification must use the repository's Invar gate. Final readiness requires the repo-wide regression command, not only focused tests.

## Documentation Requirements
Implementation synchronously maintains:

- `README.md`: all-or-nothing boundary, exact ID rules, per-action field matrix, ref semantics, and one marked copyable request executed by tests.
- `docs/DESIGN.md`: working-copy/final-validation/single-save flow plus typed-schema and pre-load validation ownership.
- `vectl_mutate` docstring: XOR rule, action-specific explicit IDs, `add-step` refs, request indexing, and batch response fields.
- this RFC: implemented status, typed schema source, error responsibility, and executable acceptance evidence.

No new ADR is required while Git durability remains governed by `docs/ADR-unified-state.md`.

### Diagnostic and review follow-up

The MCP mutation surface performs candidate validation before `_save_admitted_plan_receipt`. Canonical and structural failures share `stage=final_validation`; `validation_source` distinguishes them. Canonical paths are schema-allowlisted, canonical types are Pydantic-built-in-allowlisted, and raw Pydantic values are never copied. Unexpected canonical or structural exceptions use fixed pre-save text; ordinary structural diagnostics keep actionable identifiers. `max_issues=N` renders at most N rows while `None` renders every row. Both modes escape controls/newlines, cap each issue at exactly 512 codepoints, and publish `blocking_issue_count`, actual `rendered_issue_count`, `issues_truncated`, and `issue_values_truncated`. Failure does not change candidate bytes, SHA binding, claims, locks, Git, or the persistence boundary.

Admission diagnostic rendering is best-effort accidental-disclosure hygiene for a trusted same-UID adapter. It returns fixed `admission diagnostic unavailable` for input over 8192 raw or decoded units and Python strings containing surrogates, then bounds normal output to 1024 codepoints. Ordinary authorization fields, common secret assignments, obvious opaque tokens, protected Plan values, temporary/absolute paths, and ordinary URL userinfo or secret-like components are replaced with `<redacted>`. URL inspection performs one percent-decoding pass; safe URLs remain exact, and placeholder restoration in reverse creation order preserves nested `<admission-temp>` markers. Deliberate control splitting, malformed multi-`@` authorities, repeated encoding, Unicode whitespace obfuscation, and parser ambiguity are outside this hygiene contract. The sanitizer does not claim sandboxing, credential isolation, or hostile-input containment; the adapter can already access same-UID resources.

`vectl_review` keeps default `scope="global"` and separates validation closure from rendering. Phase rendering stays target-local. Phase validation includes target dependencies, every explicit gate selector's exact and qualified candidates, candidate step dependencies, and candidate phase dependencies. Explicit integration forces unclosed failure-evidence inspection of each completed candidate root regardless of review-like naming, so relevant failures block readiness. Distinct exact/qualified interpretations block readiness. Active closure includes every candidate interpretation and Plan-wide duplicate identity blockers.

For bounded `max_issues=N`, L1 issues, L2 phases, L3 phase headers, L3 global step rows, L4 refs, gate pending steps, and downstream phases each have N rows. L3 phase-dependency links, L3 step-dependency links, and L4 links each use one global N-link budget. User scalars are control/newline escaped and capped at 160 codepoints; L1 issues use 512. Each section reports pre-bound totals, actual rendered counts, count-truncation flags, and rendered-value truncation flags. A fixed 65,536-codepoint output cap falls back to metadata with zero detail rendered counts and `output_safety_truncated: true`. `max_issues=None` preserves all review rows, links, and values without bounded scalar/output caps.

## Implementation Target Map
Primary targets:

- `src/vectl/mcp_mutation_contracts.py`: canonical action-discriminated request types and derived field sets.
- `src/vectl/mcp_core_mutation_tools.py::vectl_mutate`: typed public annotation, pre-load diagnostics, shared dispatch, and `add-step` refs forwarding.
- `src/vectl/mcp_core_common.py::_save_plan` and its existing private save receipt helper.
- `tests/test_mcp_mutate_batch.py`: direct contract, atomicity, real stdio schema, and executable README evidence.
- `README.md`, `docs/DESIGN.md`, and this RFC.

Existing primitives remain the only action owners:

- `src/vectl/core_plan_step_add.py::{add_phase,add_step,add_steps_bulk}`
- `src/vectl/core_plan_step_edit.py::{edit_phase,edit_step,edit_plan,remove_step,move_step}`
- `src/vectl/core_plan_queries.py::recalc_lock_status`
- `src/vectl/core_plan_validation.py::validate_plan`
- `src/vectl/io.py::save_plan`

## Non-goals

- Generic transactions.
- A new Plan schema or Plan entity.
- A durable mutation or idempotency ledger.
- Symbolic cross-request ID references.
- Batching claims, completion, dispatch, child work, repository implementation, or external effects.
- Automatic Planner or CAS retry loops.
- Guaranteed Git rollback when the best-effort commit fails.
- A generic CLI batch mutation command.

## Trade-offs

### Explicit IDs

Benefit: deterministic same-batch references and safe replay.

Cost: more verbose batch requests.

Failure condition: allowing generated IDs with existing suffix-on-collision behavior would silently duplicate work on replay.

### Deep working copy

Benefit: request helpers may partially mutate an object before raising without exposing any partial result.

Cost: one additional in-memory Plan copy per batch.

Failure condition: exceptionally large Plans may increase peak memory, though YAML load and serialization already require the full Plan in memory.

### Markdown batch response

Benefit: preserves the existing MCP return type and single-action compatibility.

Cost: callers parse text rather than a typed batch receipt.

Failure condition: a future requirement for machine-consumed result objects would need a separate compatibility decision.

### Structural final validation

Benefit: blocks invalid topology while allowing repair of unrelated historical evidence debt.

Cost: the batch response does not report completed-evidence findings.

Failure condition: callers requiring evidence validation must run the existing validation or gate surfaces after mutation.

## Acceptance Criteria
The RFC is satisfied when all of the following hold:

- one successful multi-operation batch performs one load, one lock recalculation, one final validation stage, one CAS save, and one Git commit attempt;
- later requests can target explicit phase and step IDs created earlier in the same batch;
- registered FastMCP clients see an action-discriminated, closed schema with exact fields and required IDs;
- single-action and batch `add-step` persist optional `refs` without loss;
- unknown-field failures identify the request index, action, rejected fields, allowed fields, and known remediation before Plan load;
- request-level, final-validation, and CAS failures persist no batch state;
- replay does not create suffixed duplicate phases or steps;
- existing single-action calls and responses do not regress;
- final response contains ordered request outcomes, the saved Plan hash, and any lock notice;
- the copyable README request passes unmodified through the real registered stdio MCP tool;
- README, design documentation, tool documentation, tests, and repo-wide verification remain synchronized.

## Open Questions

None blocking. Any proposed relaxation of explicit IDs, change to Git durability, typed response migration, or CLI parity requires a separate decision.
