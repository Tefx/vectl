# vectl Design & Architecture

Technical internals for contributors and architects.
For usage, see [README](../README.md).

## Data Model (`plan.yaml`)

```yaml
version: 1
project: my-project
context: |
  PHILOSOPHY: Clean code, comprehensive tests.
  METHODOLOGY: Models → IO → Logic → CLI → MCP.

phases:
  - id: core
    name: Core Logic
    status: done
    gate: "All tests pass"

  - id: auth
    name: Auth Module
    depends_on: [core]
    status: in_progress
    gate: "Auth tests pass"
    steps:
      - id: auth.user-model
        name: User Model
        status: claimed
        claimed_by: engineer-1
        description: |
          Define User schema.
          - [x] Basic fields
          - [ ] Validation logic
        verification: "uv run pytest tests/test_user.py -v"
        refs: ["docs/specs/auth.md#user-schema"]
```

### ID Scheme

| Format | Example | Rule |
|--------|---------|------|
| Phase ID | `core`, `cli-ux` | Short, unique across plan |
| Step ID | `core.pydantic-models` | `{phase_id}.{slug}` auto-generated from name |

Slug auto-generation: kebab-case from name. Collision resolved by appending `-2`, `-3`.

### Ordering

- **Execution order**: Determined by `depends_on` (DAG). Not by list position.
- **Display order**: Within DAG constraints, then alphabetical.

## CAS (Compare-And-Swap) Safety
All write operations use CAS to prevent concurrent modification:

1. Load plan → get file hash
2. Modify in memory
3. Save with expected hash → atomic write (temp file + rename)
4. If hash mismatch → `CASConflictError`, retry

For MCP `vectl_mutate` batches, `src/vectl/mcp_mutation_contracts.py` owns the action-discriminated transport types, exact allowed fields, and required identifiers. FastMCP publishes those branches as closed JSON Schema objects, while `mcp_core_mutation_tools.py` uses the derived field sets for indexed pre-load diagnostics. This keeps machine guidance and executable validation aligned; `add-step.refs` travels through the shared dispatcher into the canonical `Step.refs` field in both input modes.

The shell deep-copies the loaded Plan into one working copy and applies ordered requests through the same action dispatcher used by single-action mode. Both mutation modes recalculate locks once, perform canonical model and structural final validation, and collect every non-warning validator issue in deterministic validator order before crossing the admitted save boundary. Canonical Pydantic entries discard `msg`, input, context, and URLs. A location component survives only when the model expected at that path (`Plan`, `Phase`, `Step`, `Clipboard`, or `RejectionEntry`) admits the field or list index; a type survives only when Pydantic Core declares it as built-in. Unknown locations become `<field>` and unknown/custom types become `validation_error`. Unexpected canonical extraction/normalization failures and structural-validator exceptions become one fixed pre-save `unexpected_validation_error` without exception/candidate text. Ordinary structural issues retain actionable identifiers. The shared formatter escapes controls/newlines and caps every canonical or structural issue value at 512 codepoints after escaping. `max_issues=N` bounds rows; `None` retains every row, while the 512-codepoint value cap remains. A failed candidate returns before `_save_admitted_plan_receipt`, so Plan bytes and SHA, CAS identity, claims, locks, and Git HEAD remain untouched.

The successful Plan write is the atomic batch boundary. The save path then makes one best-effort Git commit attempt. Best-effort Git failure does not roll back `plan.yaml`, so Git history remains a secondary audit trail rather than part of the Plan transaction. Claims, CLI mutation commands, and external effects remain outside the batch.

MCP review keeps the existing full-history `scope="global"` default and adds `scope="phase"` and `scope="active_dependency_closure"`. Core diagnostics build a structural validation closure independently from the render projection. Phase rendering remains target-only; phase validation includes the target's transitive phase dependencies. The active closure includes executable or recoverable phases, their transitive phase dependencies, and declared dependencies of pending, claimed, or rejected steps. Plan-wide duplicate phase/step-ID issues remain blockers in every scope. Narrative Plan fields (`description`, `verification`, `evidence`, `Phase.gate`, and legacy `<vectl_gate_scope>` blocks) are display data only: validation and review scope never infer state from their contents.

For bounded `max_issues=N`, L1 issue rows, L2 phase rows, L3 phase headers, L3 global step rows, L4 ref rows, gate pending rows, and downstream rows each have an N-row budget. L3 phase-dependency links, L3 step-dependency links, and L4 links each have one global N-link budget. Rendered scalars are control/newline escaped and capped at 160 codepoints after escaping; L1 issue messages use 512. Every section publishes pre-bound totals, actual rendered counts, count-truncation flags, and rendered-value truncation flags. The response has a fixed 65,536-codepoint cap; its metadata-only fallback sets detail rendered counts to zero and `output_safety_truncated: true`. `max_issues=None` retains every review row/link/value and bypasses these scalar and total-output caps.

Admission diagnostics cross a trusted same-UID adapter boundary. The sanitizer is a small best-effort hygiene pass for accidental disclosure in ordinary errors, not a containment mechanism for malicious local code. It rejects diagnostics over 8192 raw or decoded units and Python strings containing surrogates with fixed `admission diagnostic unavailable`; normalizes surviving output to 1024 codepoints; and replaces ordinary complete authorization fields with `<redacted>`, along with common secret assignments, obvious opaque tokens, protected Plan scalars/fragments, temporary paths, and absolute path tails. URL handling uses ordinary lexical candidates, simple userinfo/component inspection, and one percent-decoding pass. Safe URLs remain exact, including URLs followed by email or mention prose. Private-use semantic placeholders are selected outside the bounded diagnostic and protected values, then restored in reverse creation order so nested `<admission-temp>` markers survive safe-URL preservation. Deliberate control splitting, malformed multi-`@` authorities, repeated encoding, Unicode whitespace obfuscation, and parser ambiguity are outside this hygiene contract. Zero-write admission, CAS, immutable-input, and fixed-failure behavior remain independent of diagnostic rendering.

## Architecture

```
models.py       (~300L)  — Enums, Pydantic models, dataclasses, exceptions
io.py           (~110L)  — YAML IO + CAS
core.py       (~1,570L)  — All business logic (validation, state machine, mutation, render, diff)
cli.py        (~1,670L)  — Typer CLI commands (33 commands)
mcp_server.py   (~780L)  — FastMCP server (10 tools)
```

## Tests

```bash
uvx vectl --version               # Verify installation
uv run pytest tests/ -q           # 456 tests
```

| File | Tests | Coverage |
|------|-------|----------|
| `test_cli.py` | 124 | All CLI commands end-to-end |
| `test_architect.py` | 82 | Plan mutation, slugs, batch add |
| `test_mcp.py` | 61 | MCP server tools |
| `test_logic.py` | 55 | State machine, auto-unlock, cascade |
| `test_review.py` | 40 | Review layers, gate checks |
| `test_models.py` | 21 | Pydantic validation, enums |
| `test_render.py` | 19 | Markdown export |
| `test_validation.py` | 17 | DAG validation, status consistency |
| `test_diff.py` | 13 | Plan change detection |
| `test_checklist.py` | 12 | Checklist toggle, append |
| `test_io.py` | 11 | YAML IO, CAS |
| `test_properties.py` | 1 | Stateful property-based testing |

## Claim Recovery

vectl maintains claim state in `claims.json` (branch-scoped). When this state
diverges from `plan.yaml`, use the repair command:

```bash
uvx vectl repair claims --dry-run     # preview
uvx vectl repair claims               # repair all
uvx vectl repair claims --step <id>  # scoped repair
```

### Policy: Plan Precedence

| Principle | Description |
|-----------|-------------|
| Source of truth | `plan.yaml` claim-visible state |
| Scope | Current git branch only |
| Preservation | Out-of-scope entries (other branches, unrelated steps) kept intact |
| Modes | `--dry-run` (safe preview), `--step` (scoped), `--json` (machine output) |

### Ghost Claims & Split-Brain

- **Ghost claim**: Entry in claims.json but step is not claimed in plan.yaml
- **Split-brain**: Entry in claims.json disagrees with plan.yaml (different owner, stale timestamp)

The repair command removes ghosts and reconciles split-brain entries using plan precedence.
