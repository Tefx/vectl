<!-- VECTL:AGENTS:BEGIN -->
## Plan Tracking (vectl)

vectl tracks this repo's implementation plan as a structured `plan.yaml`:
what to do next, who claimed it, and what counts as done (with verification evidence).

Full guide: `uvx vectl guide`
Quick view: `uvx vectl status`

### Claim-time Guidance
- `uvx vectl claim` may emit a bounded Guidance block delimited by:
  - `--- VECTL:GUIDANCE:BEGIN ---`
  - `--- VECTL:GUIDANCE:END ---`
- For automation/CI: use `uvx vectl claim --no-guidance` to keep stdout clean.

### CLI vs MCP
- Source of truth: `plan.yaml` (channel-agnostic).
- If MCP is available (IDE / Claude host), prefer MCP tools for plan operations.
- Otherwise use CLI (`uvx vectl ...`).
- Evidence requirements are identical across CLI and MCP.

### plan.yaml — Managed File (DO NOT EDIT DIRECTLY)

`plan.yaml` is exclusively owned by vectl. Direct edits (Edit, Write, sed, or
any file tool) **will** corrupt plan state — vectl performs CAS writes, lock
recalculation, and schema validation on every save, none of which run on direct
edits.

**To modify plan state, ONLY use:**
- CLI: `uvx vectl claim`, `uvx vectl complete`, `uvx vectl mutate`, etc.
- MCP: `vectl_claim`, `vectl_complete`, `vectl_mutate`, etc.

If a vectl command fails, report the error — do **not** edit `plan.yaml`
directly as a workaround. Use `uvx vectl guide stuck` for troubleshooting.

### Rules
- One claimed step at a time.
- Evidence is mandatory when completing (commands run + outputs + gaps).
- Spec uncertainty: leave `# SPEC QUESTION: ...` in code, do not guess.

### For Architects / Planners
- **Design Mode**: Run `uvx vectl guide --on planning` to learn the Architect Protocol.
- **Ambiguity = Failure**: Workers will hallucinate if steps are vague.
- **Constraint Tools**:
  - `--evidence-template`: Force workers to provide specific proof (e.g., "Paste logs here").
  - `--refs`: Pin specific files (e.g., "src/auth.py") to the worker's context.
<!-- VECTL:AGENTS:END -->
