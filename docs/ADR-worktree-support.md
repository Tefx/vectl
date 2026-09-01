# ADR: Worktree Support for Multi-Agent Concurrency

**Status:** Accepted
**Date:** 2026-03-07
**Participants:** moderator, se-expert, llm-agent-expert

## Context

vectl uses split-state persistence:
- `plan.yaml`: structural definitions (phases, steps, deps) — git-tracked
- `.git/vectl/state.json`: runtime state (status, claimed_by, evidence) — untracked

Multiple AI agents work in different git worktrees concurrently, operating on the same plan. Worktrees provide code isolation, not separate plans.

**Historical behavior (superseded by the 0.9.1 repair):**
- linked-worktree Plan resolution selected the main worktree's `plan.yaml`
- claim and diagnostic helpers still read the process CWD for the branch
- Git backup detection also read the process CWD

This split authority let one linked invocation read and write the integration
Plan while acquiring or releasing claims under the linked branch. It also made
repair, status/show/top diagnostics, migration, batch claim compensation, and
MCP lifecycle operations disagree about which branch was authoritative.

## Decision

### Core Design — Solution B

1. **Resolve one immutable authority per CLI command or MCP request.** It contains the resolved Plan path, claims path, and branch.
2. **A linked implicit invocation selects the integration/main worktree for all three fields.** The only ambient-CWD probe is initial Plan resolution. Branch, claims, Git commit, and backup probes use the resolved Plan worktree afterwards.
3. **Lifecycle transitions use the captured branch.** Claim acquire and exact-match release share the same `ClaimEntry` identity. Repair, status/show/top diagnostics, duplicate-ID migration, and batch claim use it too.
4. **Batch compensation uses the shared full-entry conditional release helper.** A replacement claim is preserved; a failure cannot delete it.
5. **Claims schema and foreign branch entries remain compatible.** Repair scopes to the authority branch and does not delete legacy foreign-branch claims.
6. **Structural mutation remains guarded in linked worktrees unless an explicit Plan override is supplied.** This repair changes lifecycle authority, not that structural-edit policy.

### Implementation

`resolve_plan_authority()` first follows the existing precedence chain:

1. explicit `--plan`/function argument;
2. `VECTL_PLAN_PATH`, then deprecated `VECTL_PLAN`;
3. linked worktree main Plan;
4. walk-up discovery; and
5. fallback `./plan.yaml`.

It then constructs:

```python
PlanAuthority(
    plan_path=<resolved canonical Plan>,
    claims_path=resolve_claims_path(plan_path),
    branch=git -C <plan_path.parent> rev-parse --abbrev-ref HEAD,
)
```

If a non-Git explicit Plan needs legacy compatibility, the branch fallback is
captured once during authority construction. Nested lifecycle code never reads
ambient CWD to choose a branch or claims target.

Linked-worktree fallback semantics remain fail-closed: a successful linked
probe returns the main-root `plan.yaml` even when absent; malformed probes use
an absolute `cwd/plan.yaml` sentinel and never walk up into a stale local Plan.

CLI autosave compares Git common directories, so an implicit linked command
commits the integration Plan. Explicit CLI targets in a different repository
still suppress caller-repository autosave. MCP saves and commits the configured
resolved Plan target.

### Code Changes

| Area | Change |
|------|--------|
| `plan_path.py` | immutable `PlanAuthority`, Plan-root branch and common-dir helpers |
| lifecycle and claims | explicit branch propagation for acquire/release/repair |
| CLI and MCP | one authority feeds lifecycle, repair, migration, diagnostics, and batch claim |
| I/O | backup Git directory derives from `plan_path`, not process CWD |
| tests | real `git worktree add` coverage for CLI, MCP, diagnostics, repair, migration, compensation, and explicit external Plans |

### Integrating parallel worktree changes

A candidate branch produced in a linked worktree must be rebased onto the current
integration branch, or its commits cherry-picked there, before the integration
branch runs `git merge --ff-only`. Resolve any Plan conflict during that rebase
or cherry-pick. Vectl does not perform branch integration or create an
integration service.

## Alternatives Considered

### Move plan.yaml to `.git/vectl/` (Rejected)

Proposed by llm-agent-expert, later withdrawn. Would store the live plan.yaml alongside state.json in `.git/vectl/`, making it immune to git operations.

**Rejected because:** plan.yaml's git-trackability is a hard requirement. PR review, change history, and `git pull` sync all depend on it being a tracked file. A `vectl sync` command would introduce source-of-truth ambiguity between the `.git/vectl/` copy and the repo root copy.

### Per-worktree state.json isolation (Rejected)

Each worktree gets its own state.json via `--git-dir` instead of `--git-common-dir`.

**Rejected because:** directly contradicts the use case. Agents wouldn't see each other's claims, leading to double-claiming and wasted work.

### VECTL_PLAN_PATH only (Rejected as primary mechanism)

Configure each agent's environment variable at spawn time.

**Rejected as primary because:** AI agents don't reliably preserve env vars across subshells, and the orchestrator must configure every agent correctly. Too fragile for the primary mechanism. Retained as escape hatch.

## Residual Risks

1. **The integration worktree can still reset or overwrite `plan.yaml`.** Backups in `.git/vectl/plan.yaml.bak` provide recovery input; they do not prevent that operation.
2. **A user can intentionally override Plan selection with `--plan` or `VECTL_PLAN_PATH`.** That is required for cross-repository operation; callers must select it deliberately.
3. **Foreign-branch legacy claims remain retained.** They are outside the authority branch and require an explicit targeted lifecycle/reconciliation policy before removal.

## Discussion Transcript

Full discussion: 7 sayings across 3 participants, reaching consensus in ~3 minutes.

Key insights:
- **se-expert**: plan.yaml should be read-only for agents; use `git rev-parse --show-toplevel` for robust main worktree detection
- **llm-agent-expert**: AI agents treat worktrees as disposable scratch space; zero-config is critical for agent orchestration; error messages must be actionable for AI agents
- **moderator**: split-state already ensures plan.yaml is read-only during agent work; the simplest solution is redirecting reads to main worktree
