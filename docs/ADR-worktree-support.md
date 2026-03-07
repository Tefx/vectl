# ADR: Worktree Support for Multi-Agent Concurrency

**Status:** Accepted
**Date:** 2026-03-07
**Participants:** moderator, se-expert, llm-agent-expert

## Context

vectl uses split-state persistence:
- `plan.yaml`: structural definitions (phases, steps, deps) — git-tracked
- `.git/vectl/state.json`: runtime state (status, claimed_by, evidence) — untracked

Multiple AI agents work in different git worktrees concurrently, operating on the same plan. Worktrees provide code isolation, not separate plans.

**Current behavior:**
- `resolve_state_path()` uses `--git-common-dir` → all worktrees share state.json (correct)
- `resolve_plan_path()` uses walk-up discovery → each worktree finds its own plan.yaml copy (problematic)

**Risks identified:**
- Agents routinely run `git reset --hard`, `git checkout`, `git clean -fd` in worktrees, which can obliterate or replace plan.yaml
- Walk-up discovery may find a stale plan.yaml in the linked worktree instead of the canonical one in the main worktree

## Decision

### Core Design

1. **plan.yaml is read-only during agent work** — claim/complete only write state.json (already the case post split-state refactor). Only `mutate` writes plan.yaml.
2. **linked worktree → auto-resolve to main worktree's plan.yaml** — vectl detects linked worktrees and reads plan.yaml from the main worktree root.
3. **mutate is blocked in linked worktrees** — structural changes must happen in the main worktree. Error messages include the main worktree path and `VECTL_PLAN_PATH` override instructions.
4. **state.json unchanged** — already shared via `--git-common-dir` with CAS merge retry.

### Implementation

Main worktree root resolution:
```python
git_common_dir = git rev-parse --git-common-dir
git_dir = git rev-parse --git-dir

if git_dir != git_common_dir:
    # linked worktree → resolve main worktree root
    main_root = git -C <common_dir> rev-parse --show-toplevel
    return main_root / "plan.yaml"
else:
    # main worktree → normal walk-up discovery
    ...
```

Using `git rev-parse --show-toplevel` from the common dir is more robust than assuming `git_common_dir.parent` (which fails for bare repos, `--separate-git-dir`, submodules).

Linked-worktree fallback semantics:
- If linked-worktree main root resolves successfully, vectl returns `<main_root>/plan.yaml` deterministically even if the file is missing.
- vectl does **not** fall back to linked-worktree/local walk-up discovery in that case (prevents stale local plan selection).
- If linked-worktree probe is malformed/partial, vectl fails closed to an absolute `cwd/plan.yaml` sentinel path.

`VECTL_PLAN_PATH` remains as an escape hatch (already exists, highest precedence in resolution chain).

### Code Changes

| File | Change |
|------|--------|
| `plan_path.py` | `resolve_plan_path()` adds worktree detection |
| `core.py` or `mcp_server.py` | mutate operations add linked worktree guard |
| tests | Cover worktree scenarios |

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

1. **Main worktree agent does `git reset --hard`** — still overwrites plan.yaml. Mitigated by divergence-guard phase (Layer 2: backup in `.git/vectl/plan.yaml.bak`).
2. **Agent `git add .` commits stale plan.yaml to branch** — doesn't break coordination (vectl reads from main worktree), but may cause merge noise. Can be handled via `.gitattributes` or merge strategy.

## Discussion Transcript

Full discussion: 7 sayings across 3 participants, reaching consensus in ~3 minutes.

Key insights:
- **se-expert**: plan.yaml should be read-only for agents; use `git rev-parse --show-toplevel` for robust main worktree detection
- **llm-agent-expert**: AI agents treat worktrees as disposable scratch space; zero-config is critical for agent orchestration; error messages must be actionable for AI agents
- **moderator**: split-state already ensures plan.yaml is read-only during agent work; the simplest solution is redirecting reads to main worktree
