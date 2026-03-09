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
