# ADR: Unified State Architecture

**Status:** Accepted
**Date:** 2026-03-08
**Participants:** moderator, se-expert, se-radical, llm-agent-expert, software-architect

## Context

vectl v0.6 introduced split-state: `plan.yaml` (git-tracked definitions) + `.git/vectl/state.json` (untracked mutable state). This solved merge-conflict concerns but created worse problems:

1. **State invisibility** — completion status hidden from `git diff`, `git log`, PR review
2. **Multi-branch contamination** — `state.json` shared across all branches via `.git/`; switching branches leaves orphan state
3. **Agent rollback** — agents run `git restore plan.yaml` (or equivalent), losing uncommitted vectl changes. Proven recurring incident; prompt-based prevention has ~60-75% compliance rate
4. **Multi-user impossibility** — `state.json` doesn't cross `git clone` boundaries; no coordination between machines

### Incident that precipitated this decision

Sub-agents repeatedly executed `git restore plan.yaml`, rolling back plan structure changes that vectl had written but not committed. This caused plan/state divergence where `state.json` contained entries for steps that no longer existed in `plan.yaml`. The root cause is architectural: plan.yaml modifications sat in the working tree uncommitted, exposed to agent git operations.

## Decision

### Retire state.json. Unify state back into plan.yaml.

Completion status (`status`, `evidence`, `done_at`, `claimed_by`) moves back into `plan.yaml` as inline fields on each step. Every write operation (`complete`, `mutate`, `init`) is immediately followed by `git commit --only --no-verify plan.yaml`.

Claims (ephemeral coordination) move to `.git/vectl/claims.json`, protected by `flock()`.

### File layout

```
plan.yaml                    # structure + completion (git-tracked)
.git/vectl/claims.json       # active claims (flock-protected, worktree-shared)
.gitattributes                # plan.yaml merge=vectl (multi-user, Phase 3)
```

### Why this works

**Agent rollback eliminated:** `git restore plan.yaml` restores to HEAD, which already contains vectl's changes (committed immediately). No uncommitted window.

**Multi-branch isolated:** Each branch carries its own `plan.yaml` with its own completion state. Switching branches switches state. No contamination.

**Git visible:** `git log plan.yaml` shows full progress history. PRs show completion changes.

**Multi-user via git:** `git push/pull` synchronizes completion state. Custom merge driver resolves step-level conflicts automatically.

## Claims Design

| Property | Value |
|----------|-------|
| Storage | `.git/vectl/claims.json` (untracked) |
| Key | `(branch, step_id)` |
| Protection | `flock()` on sidecar `.git/vectl/claims.json.lock` |
| TTL | 2 hours; lazy cleanup on each `vectl claim` call |
| Release | Explicit via `vectl unclaim` / `vectl defer`, or TTL expiry |
| Worktree sharing | Yes (`.git/` shared via `--git-common-dir`) |
| Cross-machine | Not synchronized (each machine independent) |

Claims are ephemeral coordination (like PID files), not durable record. Loss on daemon crash or `git clone` is correct behavior — no active agents exist in a fresh clone.

## Write Protocol

```
vectl complete <step>:
  1. flock(claims.json.lock) → validate claim → remove claim entry → unlock
  2. read plan.yaml
  3. set step.status = "done", step.evidence = "...", step.done_at = now()
  4. write plan.yaml (tempfile + rename)
  5. git commit --only --no-verify plan.yaml -m "[vectl] complete <step>"

vectl claim <step>:
  1. flock(claims.json.lock) → check no existing claim → write claim → unlock
  2. (plan.yaml not modified)

vectl mutate <args>:
  1. modify plan.yaml structure
  2. git commit --only --no-verify plan.yaml -m "[vectl] mutate: <description>"
```

Concurrent CLI + MCP writes: `git commit` uses git's own `index.lock` for mutual exclusion. If commit fails due to concurrent write, retry once after re-reading.

## Concurrency Model

| Scenario | Mechanism |
|----------|-----------|
| Single agent | Direct read/write, no contention |
| Multi-agent same MCP | MCP server asyncio serializes requests |
| Multi-agent different processes | flock on claims.json; git index.lock on plan.yaml commits |
| Multi-agent worktrees | Claims keyed by (branch, step_id); each worktree has own plan.yaml copy |
| Multi-user same branch | Custom merge driver (`vectl merge-driver`) resolves at step-id granularity |
| Multi-user different branches | Git natural isolation |

### Worktree behavior

Git forbids two worktrees on the same branch. Claude Code creates temporary branches for worktree agents. Therefore "multi-agent same branch" does not occur in worktree scenarios — it is always multi-agent on different branches sharing the same `.git/`.

## Migration

### From pre-v0.6 (single plan.yaml with inline state)

No migration needed. The format is forward-compatible — pre-v0.6 plans already have status fields inline.

### From v0.6+ (split plan.yaml + state.json)

Automatic on first `_load()`:
1. Detect `.git/vectl/state.json` exists
2. Read state entries (status, evidence, claimed_by, done_at)
3. Merge into plan.yaml by step_id
4. `git commit --only --no-verify plan.yaml -m "[vectl] migrate: merge state into plan"`
5. Rename `state.json` → `state.json.migrated` (backup)
6. Log: "Migrated N steps from state.json into plan.yaml"

Edge cases:
- Orphan state entries (step in state but not plan) → skip, warn
- plan.yaml already has status fields → state.json values take precedence
- Empty state.json → skip, delete

Rollback: `mv state.json.migrated state.json` + `git restore plan.yaml`

## Multi-user Merge Strategy (Phase 3)

`.gitattributes`: `plan.yaml merge=vectl`

`git config merge.vectl.driver "uvx vectl merge-driver %O %A %B"`

The merge driver:
- Parses base, ours, theirs as YAML
- Merges at step-id granularity: different steps modified → auto-merge
- Same step modified by both sides → mark conflict for human resolution
- Returns 0 (success) or 1 (conflict)

## Supersedes

- **ADR-worktree-support.md** — worktree detection and mutate-guard remain valid; the "state.json unchanged" section is superseded by this ADR
- **Split-state design** (v0.6) — fully replaced

## Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Extra commits from vectl operations | Noisier git history | Filter with `git log --invert-grep --grep='\[vectl\]'` |
| flock is local-filesystem only | NFS not supported | Document; not a target environment |
| Claims not cross-machine | Multi-machine double-claim possible | `vectl complete` validates step not already done; second complete fails |
| Merge driver needed for multi-user | Setup required | `vectl init` can configure automatically |

## Alternatives Considered

| Alternative | Verdict | Reason |
|-------------|---------|--------|
| Keep split-state, fix incrementally | Rejected | Agent rollback is architectural, not patchable |
| Shared daemon for serialization | Deferred | MCP server already serializes; daemon adds lifecycle complexity for marginal benefit |
| chmod 444 on plan.yaml | Rejected | Commit-after-mutate already eliminates the vulnerability; chmod adds complexity for edge cases (git pull, manual edit) |
| flock on plan.yaml writes | Rejected | git's own index.lock provides sufficient mutual exclusion for commits |
| Git refs/notes for state | Rejected | Poor tooling support, invisible to standard git workflows |
| CRDT / event sourcing | Rejected | Over-engineering for 10-50 step plans |
