# orch_removal_foundation_gate — Gate Review Evidence

Step: `orch_removal_foundation_gate`  
Agent: `gate-reviewer`  
Branch: `vectl/step-orch_removal_foundation_gate`  
Decision: **OPEN**

## Headline
PASS — foundation deletion scope and worktree cleanup policy are explicit enough to protect downstream deletion work.

## Blocking Status
CLEAR. No blocker-class issue found in the inspected source artifacts.

## Proof-Gap Status
NON-BLOCKING GAP: `tools/vectl/README.md` is absent in this worktree and therefore was **NOT READ**. This satisfies the required-reading fallback instruction to state NOT READ with reason; it does not block this gate because the material decision basis is supplied by the two required evidence artifacts and the user-request context.

## Required Reading / Artifact Inspection
- `tools/vectl/README.md`: NOT READ — direct read failed with ENOENT; `test -f tools/vectl/README.md` returned `no`.
- `user-request:2026-06-17#remove-built-in-orchestrator-runtime`: READ from provided task context; key constraint is remove built-in orch runtime while preserving `vectl_decide`.
- `CONSTITUTION.md`: NOT READ — `find . -name CONSTITUTION.md -print` returned no paths in the isolated worktree.
- `.agent/evidence/orch_removal_scope_matrix.md`: READ.
- `.agent/evidence/orch_removal_worktree_policy.md`: READ.

## Positive Requirement Coverage Ledger

| requirement_id | source_ref/key passage | required proof | evidence reviewed | status | blocker_if_unproven |
| --- | --- | --- | --- | --- | --- |
| ORCH-REMOVE-SCOPE | User request removes built-in `vectl orch`; scope matrix row says source/docs/tests are assigned to deletion/cleanup owners. | Explicit matrix naming orchestration-owned source, docs, tests, residual cleanup targets, and phase dependencies so no downstream deletion target is ambiguous. | `.agent/evidence/orch_removal_scope_matrix.md:30-37`, source deletion targets `:41-54`, cleanup targets `:55-59`, docs `:61-72`, tests `:75-96`, active cleanup `:163-167`, dependency order `:277-284`. | PROVEN | Yes |
| DECIDE-PRESERVE | User request preserves `vectl_decide`; scope matrix says `vectl_decide remains part of vectl` and lists preserved paths/fields. | Explicit preserve list and path-presence gate for decide source, DTOs, tests, MCP surface, and RFC. | `.agent/evidence/orch_removal_scope_matrix.md:35`, preservation targets `:169-202`, non-goals `:215-221`, final grep presence probe `:223-241`. | PROVEN | Yes |
| ISOLATION-REMOVE | User request removes `Step.isolation`/`IsolationMode`; scope matrix maps model/io/test/doc residue. | Explicit affected-file map for schema cleanup and final grep coverage for isolation residue. | `.agent/evidence/orch_removal_scope_matrix.md:36`, affected-file map `:204-212`, final grep deletion/forbidden terms `:242-270`. | PROVEN | Yes |
| FINAL-GREP | User request requires final forbidden-term closure. | Concrete final gate command preserving decide files, asserting deletion-target absence, and grepping active source/tests/docs while excluding plan/orchestrator history. | `.agent/evidence/orch_removal_scope_matrix.md:37`, closure gate `:223-270`, allowed exceptions `:272-275`. | PROVEN | Yes |
| WT-SAFE-1 | Worktree policy says `.vectl/workspaces` and `.vectl/worktrees` contain registered git worktrees; direct deletion is forbidden. | Registry-backed proof and policy requiring `git worktree remove` / `git worktree prune`, not `rm -rf`, for registered paths. | `.agent/evidence/orch_removal_worktree_policy.md:15-17`, policy disposition `:19-25`, worktree-list proof begins `:32-40`, safe directory inspection `:289-436`. | PROVEN | Yes |
| WT-SAFE-2 | Worktree policy states allowed/forbidden side effects and no cleanup occurred. | Explicit side-effect policy preserving plan/orchestrator state and deferring cleanup in a proof-only step. | `.agent/evidence/orch_removal_worktree_policy.md:17`, policy disposition `:19-25`, risk probe `:27-30`, status/changed-files/gaps `:675-692`. | PROVEN | Yes |

## Orphan Requirements
None found. The six gate decision-basis rows requested by the step are all represented above.

## Blockers
None.

## Warnings
- `tools/vectl/README.md` is absent; both source artifacts also record it as NOT READ with ENOENT. If a later orchestrator expects that file to be authoritative, restore/provide it before relying on README-specific requirements.
- The artifacts are investigation artifacts; they do not execute deletion or final grep. Downstream implementation still must run the final grep/path-presence/absence gate after code changes.

## Notes
- Constitution audit: no `CONSTITUTION.md` was found in the isolated worktree, so no Constitution clause can be applied.
- Checklist audit: no checklist artifact was found under `.agent`; no `vectl_check` action was attempted.
- No product-code mutation occurred in the foundation proof phase: `git show --name-status 1c841cce` shows only `.agent/evidence/orch_removal_scope_matrix.md` added; `git show --name-status 2adf5bdd` shows only `.agent/evidence/orch_removal_worktree_policy.md` added. Before writing this gate artifact, `git diff --name-status Main...HEAD` was empty and `git status --porcelain=v1` was clean.
- This gate review itself writes only `.agent/evidence/orch_removal_foundation_gate.md`.

## Evidence Commands Run
```text
cd .vectl/worktrees/orch_removal_foundation_gate && pwd && git rev-parse --show-toplevel && git branch --show-current && git status --short && find .. -name CONSTITUTION.md -print
read tools/vectl/README.md -> ENOENT
read .agent/evidence/orch_removal_scope_matrix.md -> OK
read .agent/evidence/orch_removal_worktree_policy.md -> OK
git log --oneline --decorate -5
git diff --name-status Main...HEAD -> empty before gate artifact write
git status --porcelain=v1 -> empty before gate artifact write
test -f tools/vectl/README.md -> no
find . -name CONSTITUTION.md -print -> empty
find .agent -iname '*checklist*' -print -> empty
git show --name-status 1c841cce -> only .agent/evidence/orch_removal_scope_matrix.md
git show --name-status 2adf5bdd -> only .agent/evidence/orch_removal_worktree_policy.md
```

## Verdict
PASS / gate decision **OPEN**.

## Orchestrator Action Hint
COMPLETE.
