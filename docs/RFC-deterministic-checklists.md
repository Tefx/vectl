# RFC: Deterministic Checklists

## 1. Ownership & Architecture
The pure core checklist inventory and mutation service (`core_checklist.py`) is the **single authoritative owner** for checklist parsing and mutation across CLI and MCP.
CLI, MCP, and orchestration surfaces MUST delegate to it and MUST NOT re-derive checklist facts or parse markdown themselves.

## 2. Scope & Supported Fields
For the first version, checklist inventory and mutation are limited to exactly two fields of a Step:
- `description`
- `verification`

`evidence_template` is explicitly deferred. The core service MUST return an
`UnsupportedFieldError` with `code: unsupported_field` rather than silently
parsing `evidence_template` or any other field. This applies to inventory,
single mutation, and batch mutation API paths.

## 3. Revision Scope and Invariants
Deterministic mutation requires a snapshot-scoped `checklist_inventory_revision`.
**Revision Scope**: The complete raw Markdown of both supported fields (`description` and `verification`) after line-ending canonicalization. Unrelated step metadata (like title or agent) is excluded from the revision hash.

**Mutation Invariant**: Marker-only mutation. Item text, indentation, ordering, line endings after canonicalization, and surrounding Markdown MUST be preserved exactly. The only permitted change is `[ ]` <-> `[x]` marker toggling.

## 4. Selectors and Modes
Snapshot-scoped selectors are supported to ensure deterministic targeting.

| Mode | Selector Type | Description |
|------|---------------|-------------|
| Legacy Keyword Toggle | `keyword` | Case-insensitive substring match (legacy support). Toggles state. |
| Deterministic Single | `item_id` or `field+index` | Exact targeting of a single item to a specific `checked` boolean state. |
| Deterministic Batch | List of deterministic single selectors | All-or-nothing batch update of multiple items to specified states. |
| Invalid Mode | Mixed legacy & deterministic | Combining legacy keyword toggle with deterministic exact state in one request or batch is an invalid mode combination. |

Mutations require explicit all-or-nothing batch semantics when multiple requests are sent.
Legacy keyword selectors MUST omit an exact `checked` state and therefore toggle.
Deterministic selectors (`item_id` or `field+index`) MUST include an exact
`checked` boolean. Any request that provides both a legacy keyword selector and
an exact state, or combines legacy toggle requests with deterministic exact-state
requests in a batch, MUST fail with `InvalidSelectorError` and MUST NOT mutate
the plan.

### 4.1 CLI and MCP Surface Contract

The legacy human CLI path remains separate from the deterministic path:

```bash
vectl check STEP_ID KEYWORD
```

This path uses the human keyword matcher, toggles the matched item, prints
`Updated checklist: <step>`, and points the user at `vectl show <step>` for the
updated Markdown. It does not accept `--revision`, exact selectors, or a target
`checked` state.

Deterministic CLI mutation uses the inventory revision plus an exact selector
and target state:

```bash
vectl check STEP_ID \
  --revision <checklist_inventory_revision> \
  --item-id <item_id> \
  --checked true \
  --json
```

`--revision` is an alias for `--checklist-inventory-revision`. The supported
single-selector forms are `--item-id <item_id>` or `--field description|verification
--index <zero-based-index>`. Machine-readable CLI JSON MUST be parseable JSON
without Rich/ANSI styling.

Deterministic inventory is exposed as:

```bash
vectl check-inventory STEP_ID --json
```

The JSON payload includes `step_id`, `checklist_inventory_revision`, and `items`.
Each item includes `item_id`, `field`, `index`, `text`, and `checked`.

Deterministic batch mutation is all-or-nothing and uses `--batch` with a JSON
array of request objects. Each request may provide top-level selector fields or a
`selector` object plus a boolean `checked`:

```bash
vectl check STEP_ID \
  --revision <checklist_inventory_revision> \
  --batch '[{"item_id":"description:0:<hash>","checked":true}]' \
  --json
```

If any selector is stale, invalid, unsupported, or ambiguous, no checklist item
may be mutated; the response must include structured selector diagnostics where
available.

The MCP `vectl_check` wrapper exposes the same deterministic surface with
`inventory`, `revision`, `item_id`, `field`, `index`, `checked`, and `requests`
fields. Legacy MCP calls (`keyword` or `add` without deterministic fields)
continue to return Markdown text; deterministic MCP inventory/mutation returns a
structured JSON-serializable payload with the same inventory fields, diagnostics,
and structured error payloads.

## 5. Structured Errors and Retry Guidance

| Error | Cause | Retry Guidance |
|-------|-------|----------------|
| `StaleRevisionError` | The provided `checklist_inventory_revision` does not match current plan state. | Refresh the step/inventory to get the latest revision and re-evaluate the target state. |
| `ItemNotFoundError` | No item matches the deterministic selector (`item_id` or `field+index`). | Re-fetch the inventory; the item may have been removed or re-indexed. |
| `UnsupportedFieldError`| Attempted to mutate or inventory a field other than `description` or `verification` (e.g., `evidence_template`). | Restrict operations to supported fields only. |
| `InvalidSelectorError` | The selector is malformed or invalid mode combination used. | Check selector syntax and ensure modes aren't mixed. |
| `AmbiguousLegacyMatch` | Legacy keyword matches multiple items. | Use a more specific keyword or switch to deterministic selectors. |
| `NoLegacyMatch` | Legacy keyword matches zero items. | Verify the keyword spelling or refresh the step. |

Structured API error payloads MUST include:
- `code`: one of `stale_revision`, `item_not_found`, `unsupported_field`,
  `invalid_selector`, `ambiguous_legacy_match`, or `no_legacy_match`.
- `message`: human-readable summary.
- `retry`: `{retryable: bool, action: string, message: string}` where `action`
  is one of `refresh_inventory`, `narrow_selector`, `fix_request`, or
  `unsupported`.
- Optional selector context: `item_id`, `field`, `index`, and/or `revision` when
  applicable.

Retry guidance is normative: stale revisions use `refresh_inventory`, ambiguous
legacy matches use `narrow_selector`, malformed/mixed selectors use
`fix_request`, and unsupported fields use `unsupported` with `retryable: false`.
A stale deterministic mutation MUST fail with `code: stale_revision`,
`retry.action: refresh_inventory`, and no state mutation.

## 6. Orchestration Contract
The orchestrator owns checklist mutations.
It injects item-id/revision-based worker receipts into worker context.
Workers return receipts keyed by `item_id` and `revision` and MUST NOT perform natural-language fuzzy mapping.
Workers MUST NOT call vectl checklist tools directly in orchestrated worktree
tasks; they report desired final states only through `checklist_receipt` entries.

Concrete worker receipt schema:

```yaml
checklist_receipt:
  - item_id: "description:0:<snapshot-hash-fragment>"
    revision: "<checklist_inventory_revision>"
    checked: true
```

The orchestrator MUST reject receipts missing `item_id`, `revision`, or
`checked`, and MUST refresh inventory before retrying when a receipt revision is
stale.
When current inventory has changed, stale receipt revisions are rejected after
the orchestrator refreshes the current `checklist_inventory_revision`; the
orchestrator must not infer a replacement target from natural language or item
text.
