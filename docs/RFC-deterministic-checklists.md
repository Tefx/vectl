# RFC: Deterministic Checklists

## 1. Ownership & Architecture
The pure core checklist inventory and mutation service (`core_checklist.py`) is the **single authoritative owner** for checklist parsing and mutation across CLI and MCP.
CLI, MCP, and orchestration surfaces MUST delegate to it and MUST NOT re-derive checklist facts or parse markdown themselves.

## 2. Scope & Supported Fields
For the first version, checklist inventory and mutation are limited to exactly two fields of a Step:
- `description`
- `verification`

`evidence_template` is explicitly deferred. The core service MUST return an unsupported/deferred error rather than silently parsing `evidence_template` or any other field.

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
| Invalid Mode | Mixed legacy & deterministic | Combining legacy keyword toggle with deterministic exact state is an invalid mode combination. |

Mutations require explicit all-or-nothing batch semantics when multiple requests are sent.

## 5. Structured Errors and Retry Guidance

| Error | Cause | Retry Guidance |
|-------|-------|----------------|
| `StaleRevisionError` | The provided `checklist_inventory_revision` does not match current plan state. | Refresh the step/inventory to get the latest revision and re-evaluate the target state. |
| `ItemNotFoundError` | No item matches the deterministic selector (`item_id` or `field+index`). | Re-fetch the inventory; the item may have been removed or re-indexed. |
| `UnsupportedFieldError`| Attempted to mutate or inventory a field other than `description` or `verification` (e.g., `evidence_template`). | Restrict operations to supported fields only. |
| `InvalidSelectorError` | The selector is malformed or invalid mode combination used. | Check selector syntax and ensure modes aren't mixed. |
| `AmbiguousLegacyMatch` | Legacy keyword matches multiple items. | Use a more specific keyword or switch to deterministic selectors. |
| `NoLegacyMatch` | Legacy keyword matches zero items. | Verify the keyword spelling or refresh the step. |

## 6. Orchestration Contract
The orchestrator owns checklist mutations.
It injects item-id/revision-based worker receipts into worker context.
Workers return receipts keyed by `item_id` and `revision` and MUST NOT perform natural-language fuzzy mapping.
