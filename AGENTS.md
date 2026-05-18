

<!-- VECTL:AGENTS:BEGIN -->
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `vectl_guide` (CLI fallback: `vectl guide`)
Quick view: `vectl_status` (CLI fallback: `vectl status`)

### MCP vs CLI
- Source of truth: `plan.yaml` (channel-agnostic).
- **Always prefer MCP tools** (`vectl_status`, `vectl_claim`, `vectl_complete`, etc.) when available.
- CLI fallback priority: `uv run vectl` > `vectl` > `uvx vectl`.
- Evidence requirements are identical across MCP and CLI.

### Claim-time Guidance
- `vectl claim` may emit a bounded Guidance block delimited by:
  - `--- VECTL:GUIDANCE:BEGIN ---`
  - `--- VECTL:GUIDANCE:END ---`
- For automation/CI: use `vectl claim --no-guidance` to keep stdout clean.

### plan.yaml — Managed File (DO NOT EDIT DIRECTLY)

`plan.yaml` is exclusively owned by vectl. Direct edits (Edit, Write, sed, or
any file tool) **will** corrupt plan state — vectl performs CAS writes, lock
recalculation, and schema validation on every save, none of which run on direct
edits.

**To modify plan state, ONLY use:**
- MCP (preferred): `vectl_claim`, `vectl_complete`, `vectl_mutate`, etc.
- CLI (fallback): `uv run vectl claim`, `vectl claim`, or `uvx vectl claim`, etc.

If a vectl command fails, report the error — do **not** edit `plan.yaml`
directly as a workaround. Use `vectl guide stuck` for troubleshooting.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.
- Checklist receipts are deterministic: orchestrators own mutation; workers
  return `checklist_receipt` entries with exact `item_id`, `revision`, and
  `checked` values instead of calling checklist tools or doing natural-language
  fuzzy matching.

### Step ID Uniqueness
**Step IDs must be globally unique across ALL phases.**
- Example: `auth.login` and `api.login` are different step IDs.
- Example: Using just `login` in two phases creates a duplicate — not allowed.
- If you have legacy duplicate step IDs, use `vectl migrate-step-id --dry-run`
  to preview and `--yes` to repair.

### For Architects / Planners
- **Design Mode**: Run `vectl_guide` (CLI fallback: `vectl guide --on planning`) to learn the Architect Protocol.
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Phase-local vs repo-wide evidence**: bucket/phase verification proves local remediation only; it does **not** by itself prove repo-wide readiness.
- **Explicit final-gate policy required**: any large remediation, freeze, or final gate that claims repository readiness must either name the repo-wide regression command(s) to run or explicitly encode the exception policy that allows repo-wide pass to be deferred/not required.
- **Expected-red lifecycle must be governed**: if expected-red, xfail, skip, or advisory debt is tolerated at a final gate, the gate/evidence text must say why it is non-blocking, who owns it, and what lifecycle/disposition keeps it from being mistaken for an ungoverned regression.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof (e.g., "Paste logs here").
  - `--refs`: Pin specific files (e.g., "src/auth.py") to the worker's context.
<!-- VECTL:AGENTS:END -->

<!--invar:begin-->
## Invar

**CRITICAL: Write `@pre`/`@post` contracts and at least one doctest BEFORE implementing a Core function. Guard rejects uncontracted Core code.**

### Architecture

| Zone | Path | Rules |
|------|------|-------|
| Core | `**/core/**` | `@pre` + `@post` + doctest, no I/O imports |
| Shell | `**/shell/**` | returns `Result[T, E]`, handles I/O |

If code touches files, network, env vars, time, randomness, or subprocesses, use Shell.

### Verification

Run `invar guard` after changes. Fix errors before committing.

### Tools

| Tool | Use |
|------|-----|
| `invar guard` | verify architecture and contracts |
| `invar sig <file>` | inspect signatures and contracts |
| `invar map [path]` | inspect entry points |
| `invar refs <file>::<symbol>` | inspect references |

### Contract Traps

```python
# @pre must include all parameters, including defaults
@pre(lambda x, y=0: x >= 0)
def calc(x: int, y: int = 0): ...

# @post only receives result
@post(lambda result: result >= 0)
```

### Escape Hatches

```python
# @invar:allow shell_complexity: orchestration requires many steps
```

Exact syntax and repair patterns: `INVAR.md`
<!--invar:end-->
