# RFC: Plan Clipboard

**Status:** Draft
**Date:** 2026-02-16
**Author:** tefx + Claude Opus 4.6

## Problem

Agents working on the same plan sometimes need to pass ad-hoc information
that does not follow DAG edges. Examples:

1. **Cross-tool handoff**: Agent A in Claude Code (Opus) completes a complex
   architecture design. Agent B in OpenCode (GLM) needs that design to
   implement. They share no runtime, no message bus — only the plan.yaml
   on disk.

2. **Cross-phase broadcast**: A human or agent discovers a constraint
   ("freeze DB schema changes until March 1") that affects all phases.
   There is no natural step or edge to attach this to.

3. **Reviewer -> engineer (off-DAG)**: A reviewer analyzes the codebase
   and produces findings that don't map to a single step's
   `rejection_reason`. The engineer needs to read them.

Current workarounds — temp files, stuffing context into step evidence
where it doesn't belong, relying on human relay — are fragile and
untracked.

## Design

A single-slot clipboard on the plan. One item at a time. Write overwrites.
Clipboard entries auto-expire after a writer-specified TTL (default 24h).

### Why single-slot

- **Forces discipline**: if agents can only leave one note, they'll put
  the most important thing there. Multi-slot invites dumping.
- **Zero management overhead**: no keys to remember, no routing decisions
  ("which memo should I read?"), no deletion lifecycle.
- **Matches reality**: vectl plans are low-concurrency (1-3 agents).
  Simultaneous independent messages are rare.
- **Obvious failure mode**: if overwrite-loss is felt, that's signal that
  named KV is needed. Better to discover this from real pain than to
  over-build speculatively.

### TTL (time-to-live)

Clipboard entries have a writer-specified TTL, default 24 hours. The
writer knows the expected consumption window — quick handoffs get short
TTL, long-lived broadcasts get longer TTL.

- TTL is specified in hours at write time (`ttl` parameter, default 24).
- Server computes `expires_at = written_at + ttl` and stores it.
- Expiry is checked at read time (read-time filtering), not by mutating
  plan.yaml. This keeps `read` and `checkpoint` as pure queries with no
  CAS side effects.
- Expired clipboard is treated as empty in `checkpoint` and `read`
  output. The data remains in plan.yaml until the next `write` or
  `clear` naturally replaces it.

### Upgrade path

If single-slot proves insufficient: `clipboard` field becomes a `memos`
list, tool gains a `key` parameter. Forward-compatible migration.

## Data Model

New optional field on `Plan`, declared immediately before `phases` so it
appears before phases in YAML output (`model_dump()` preserves
declaration order):

```python
class Clipboard(BaseModel):
    author: str          # who wrote it (non-empty, validated)
    summary: str         # one-line description, max 80 chars
    content: str         # payload, max 8000 chars
    written_at: str      # ISO 8601 timestamp
    expires_at: str      # ISO 8601 timestamp, server-computed

class Plan(BaseModel):
    version: int = 1
    project: str
    strategy_ref: str = ""
    context: str = ""
    project_guidance: str = ""
    clipboard: Clipboard | None = None  # <-- new, before phases
    phases: list[Phase] = Field(default_factory=list)
```

YAML representation:

```yaml
version: 1
project: my-project
clipboard:
  author: opus-agent
  summary: "3-phase Alembic migration plan"
  written_at: "2026-02-16T10:00:00Z"
  expires_at: "2026-02-17T10:00:00Z"
  content: |
    Decision: Use Alembic with 3-phase migration.
    Phase A: Add new columns (nullable), deploy code that writes both.
    Phase B: Backfill, add NOT NULL constraints.
    Phase C: Drop old columns.
    Critical: Phase B must run off-peak. Estimated 2M rows, ~15min.
phases:
  ...
```

When clipboard is empty (or expired and subsequently cleared), the field
is omitted from YAML (not `clipboard: null`). This is automatic —
`io.py` already uses `exclude_none=True`.

### Backward compatibility

Adding an optional field with default `None` is an additive change.
Older vectl versions silently ignore unknown fields (Pydantic v2
default `extra="ignore"`). No `version` bump required — version bumps
are reserved for breaking changes.

## MCP Tool

Single tool, three actions:

```
vectl_clipboard(action="write", summary="...", content="...", author="...", ttl=24)
vectl_clipboard(action="read")
vectl_clipboard(action="clear")
```

### `write`

- Overwrites any existing clipboard content (even if unexpired).
- `summary` is required. Truncated to 80 chars with ellipsis if longer
  (never rejected — agents are bad at counting characters).
- `content` is required and must be non-empty (whitespace-only is
  rejected). Max 8000 chars. Rejected if exceeded (error: "Content
  exceeds 8000 char limit ({actual} chars). Shorten or split into step
  evidence.").
- `author` is required and must be non-empty.
- `ttl` is optional, in hours, default 24. Server computes
  `expires_at = written_at + timedelta(hours=ttl)`.
- `written_at` is set automatically (server-side UTC).
- Returns: confirmation with summary and expires_at.

### `read`

- Pure read, no side effects, no CAS needed.
- Returns clipboard content, author, summary, written_at, expires_at.
- If empty or expired: returns "Clipboard is empty." (If expired, adds:
  "Note: previous clipboard by {author} expired {N}h ago.")

### `clear`

- Sets `clipboard` to `None`.
- Returns: "Clipboard cleared." or "Clipboard was already empty."
- Typical use: writer clears after the handoff is consumed, or before
  writing a new note to a different audience.

### CAS

`write` and `clear` mutate plan.yaml and inherit the existing CAS
mechanism. `read` is a pure query — no CAS needed.

Concurrent write/clear conflict -> `CASConflictError` with actionable
message: "Clipboard write conflict — another agent modified the plan.
Re-read with `vectl_status` or `vectl_show`, then retry."

## Integration

### Checkpoint (`vectl_checkpoint`)

When clipboard is non-empty **and not expired**, include summary in
checkpoint output (both `lite=True` and `lite=False` — the clipboard
block is ~150 chars, well within token budget):

```json
{
  "schema": "vectl.checkpoint/v1",
  "clipboard": {
    "author": "opus-agent",
    "summary": "3-phase Alembic migration plan",
    "written_at": "2026-02-16T10:00:00Z",
    "expires_at": "2026-02-17T10:00:00Z"
  },
  ...
}
```

When clipboard is empty or expired: `clipboard` key is **omitted**
(token economy — don't pay for what you don't use).

Checkpoint is the primary discovery mechanism. Agents see the summary
and decide whether to call `vectl_clipboard(action="read")` for full
content.

### Claim guidance (`vectl_claim`)

Clipboard is **not** surfaced in claim guidance. Checkpoint already
serves as the discovery channel, and claim guidance is a higher-frequency
path where repeated clipboard summaries would accumulate as noise.

## Constraints

| Constraint | Value | Rationale |
|-----------|-------|-----------|
| Content max | 8000 chars | Single item, no multiplier effect. ~2-2.5K tokens worst case. |
| Summary max | 80 chars | One line in checkpoint. Soft-enforced (truncate, don't reject). |
| Slot count | 1 | By design. See "Why single-slot" above. |
| TTL default | 24 hours | Writer-specified. Quick handoffs: shorter. Broadcasts: longer. |
| Storage | Inline plan.yaml | Single item, bounded at 8K. No sidecar complexity. |
| CAS | Inherited | write/clear only. read is pure query. |
| Author | Non-empty string | Validated, same as `claimed_by` on Step. |
| Content | Non-empty, non-whitespace | Empty/whitespace write is rejected. Use `clear` to empty. |
| Expiry check | Read-time only | Never mutates plan.yaml as side effect of read/checkpoint. |

### Token budget

Clipboard does **not** add to claim-time guidance budget (not surfaced
in claim guidance).

Checkpoint overhead when clipboard is non-empty and unexpired: ~150
chars (author + summary + written_at + expires_at). Omitted when empty
or expired.

Content (up to 8000 chars) is never auto-injected anywhere. Only
accessible via explicit `vectl_clipboard(action="read")`.

## Implementation

| File | Change | Lines |
|------|--------|-------|
| `models.py` | `Clipboard` model + `Plan.clipboard` field | ~12 |
| `mcp_server.py` | `vectl_clipboard` tool (write/read/clear + expiry) | ~70 |
| `checkpoint.py` | Clipboard summary in checkpoint (with expiry check) | ~10 |
| `cli.py` | `clipboard-write`, `clipboard-read`, `clipboard-clear` commands | ~40 |
| **Total** | | **~130** |

CLI commands follow the existing flat naming convention (`add-step`,
`edit-step`, `skip-phase`, etc.) — no subcommand groups.

### Tests (~30)

- write: basic, overwrite existing, summary truncation, content limit
  exceeded, empty content rejected, whitespace-only rejected, empty
  author rejected, custom ttl
- read: basic, empty clipboard, expired clipboard
- clear: basic, already empty, clear expired
- overwrite: write-write sequence preserves only latest
- ttl: default 24h, custom ttl, expiry boundary (just expired vs not
  yet expired)
- CAS: concurrent write conflict, concurrent clear conflict
- checkpoint: clipboard summary present when non-empty and unexpired,
  omitted when empty, omitted when expired, present in both lite and
  non-lite modes
- claim guidance: clipboard NOT present in guidance output
- YAML round-trip: write -> save -> load -> read preserves all fields
  including expires_at
- backward compat: plan.yaml without clipboard field loads correctly

## Non-Goals

- **Named keys / multi-slot** — deliberate v1 constraint.
- **Scope binding to phases** — no auto-cleanup lifecycle.
- **Sidecar files** — single item doesn't justify the complexity.
- **Content search** — clipboard is a note, not a database.
- **Access control** — any agent can read/write. vectl is a
  coordination tool, not a security boundary.
- **Append semantics** — write is always full-replace. History lives
  in git.
- **Read-then-clear** — rejected. Breaks broadcast (multiple readers)
  and re-read after context compaction. Also makes read a write
  operation requiring CAS.
- **Write-time cleanup of expired data** — expired clipboard stays in
  plan.yaml until next write/clear. Read-time filtering is sufficient;
  mutating on read would require CAS for a query operation.
- **Claim guidance integration** — clipboard discovery happens via
  checkpoint, not claim guidance.
- **Version bump** — additive optional field, not a breaking change.
- **DAG-bound context injection** — separate, orthogonal enhancement
  to `claim_guidance` (upstream evidence propagation). Can be done
  independently.
