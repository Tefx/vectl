# [Analysis] orch_removal_worktree_policy evidence

## Scope
Proof artifact for safe cleanup policy for `.vectl/workspaces` and `.vectl/worktrees` artifacts. This step is investigation only; no cleanup was performed.

## refs Read Confirmation
- `tools/vectl/README.md`: NOT READ as file because it is absent in the isolated worktree. Confirmation: `git ls-files tools/vectl/README.md .vectl AGENTS.md CONSTITUTION.md` returned only `AGENTS.md`; direct `read` on the required path failed with ENOENT before implementation.
- `user-request:2026-06-17#safe-worktree-cleanup`: READ from provided step context. Key passage: `.vectl/workspaces` / `.vectl/worktrees` contain registered git worktrees; cleanup must use `git worktree remove/prune`, not `rm -rf`.
- `.vectl/`: READ by safe inspection of `/Users/tefx/Projects/vectl/.vectl` because `.vectl/` is absent inside this linked worktree. Key insight: artifact roots `workspaces/` and `worktrees/` exist in the main repository artifact directory and contain registered git worktree paths.
- `AGENTS.md`: READ. Key passage: `plan.yaml` is managed by vectl and must not be edited directly; evidence is mandatory when completing work.
- `CONSTITUTION.md`: NOT READ because no such file is present in the isolated worktree (`find ... CONSTITUTION.md` returned no files).
- Additional source `docs/ADR-worktree-support.md`: READ. Key passage: linked worktrees isolate code, while plan/state authority remains tied to the main worktree/canonical plan path; `plan.yaml` is read-only during agent work.
- Additional source `docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md`: READ. Key passage: runtime owns standard git worktree lifecycle and cleanup must not destroy unresolved evidence.

## Requirement Coverage
- WT-SAFE-1: PASS. Raw `git worktree list --porcelain` below proves many `.vectl/workspaces` and `.vectl/worktrees` descendants are registered worktrees. Policy forbids direct deletion of registered worktree dirs and requires `git worktree remove <registered-path>` / `git worktree prune` for registry-aware cleanup.
- WT-SAFE-2: PASS. Policy below states allowed/forbidden side effects, preserves plan/orchestrator state, and records that no cleanup was performed by this proof-only step.

## Policy Disposition
1. Direct deletion is forbidden for `/Users/tefx/Projects/vectl/.vectl/workspaces` and `/Users/tefx/Projects/vectl/.vectl/worktrees` as aggregate directories because they contain registered git worktrees.
2. Any path classified `REGISTERED_EXACT` must only be removed, when lifecycle ownership says it is safe, by `git worktree remove <registered-path>` from a valid repository context. Do not use `rm -rf` on those paths.
3. Any path classified `CONTAINS_REGISTERED_NESTED` must not be deleted directly because that would bypass git registration for nested worktrees.
4. Prunable registered entries indicate stale git worktree metadata. The safe cleanup action is `git worktree prune`; this step did not run prune because the assignment requested proof/policy and did not authorize actual cleanup.
5. Unregistered immediate paths with no registered nested worktrees observed in this probe are: ['/Users/tefx/Projects/vectl/.vectl/workspaces/.vectl', '/Users/tefx/Projects/vectl/.vectl/workspaces/test-phase.step1']. They are not git-registered worktrees in this probe; cleanup is still deferred here because the step is a proof artifact only and not an artifact deletion task.
6. No `plan.yaml`, `.git/vectl/claims.json`, or orchestrator state files were edited.

## Risk Probe Results
- Confirm whether each `.vectl/workspaces/*` or `.vectl/worktrees/*` path is registered: see `safe directory inspection` section. Most immediate `.vectl/workspaces/*` entries are `REGISTERED_EXACT`; `.vectl/worktrees/*` active step dirs are `REGISTERED_EXACT`.
- Cleanup deferred: all registered or nested-registered paths listed in inspection are deferred because direct deletion would bypass git worktree registry. Prunable registered paths deferred to `git worktree prune`: ['/Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/case-1', '/Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.deploy', '/Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.independent', '/Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.ready'].
- Unregistered paths: ['/Users/tefx/Projects/vectl/.vectl/workspaces/.vectl', '/Users/tefx/Projects/vectl/.vectl/workspaces/test-phase.step1']. They have no registered nested worktrees in this probe, but deletion remains deferred because this investigation did not include cleanup authority.

## Verification
### Run: git worktree list --porcelain -> PASS
```text
worktree /Users/tefx/Projects/vectl
HEAD 5ae67db82531cae5e0c5c217809ef1b3c479b2a5
branch refs/heads/Main

worktree /private/var/folders/rs/6_0h1ssn5439q1yfqy4pykg00000gn/T/pytest-of-tefx/pytest-60/test_build_wires_runtime_works0/custom-workspaces/core.ready
HEAD afe738d0cd1cc2bf03f84343a040b579b19d26d0
branch refs/heads/vectl/scratch/core.ready-c9b98ff0
prunable gitdir file points to non-existent location

worktree /private/var/folders/rs/6_0h1ssn5439q1yfqy4pykg00000gn/T/pytest-of-tefx/pytest-83/test_build_wires_runtime_works0/custom-workspaces/core.ready
HEAD c92037bcbfc9605281cfd6dafb3c7ccc3c54b43d
branch refs/heads/vectl/scratch/core.ready-5b465a6f
prunable gitdir file points to non-existent location

worktree /private/var/folders/rs/6_0h1ssn5439q1yfqy4pykg00000gn/T/pytest-of-tefx/pytest-84/test_build_wires_runtime_works0/custom-workspaces/core.ready
HEAD c92037bcbfc9605281cfd6dafb3c7ccc3c54b43d
branch refs/heads/vectl/scratch/core.ready-61cb9785
prunable gitdir file points to non-existent location

worktree /private/var/folders/rs/6_0h1ssn5439q1yfqy4pykg00000gn/T/pytest-of-tefx/pytest-86/test_build_wires_runtime_works0/custom-workspaces/core.ready
HEAD ae1405262e1438c95a885b30e3d2afe5b40af835
branch refs/heads/vectl/scratch/core.ready-80cc56a2
prunable gitdir file points to non-existent location

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership
HEAD de45b861826725b2255148da57fa7f1cb9fec2ba
branch refs/heads/vectl/scratch/arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership-51054e40

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper
HEAD 28036444abcc4ac4d0a3e620b10afff36f0ad177
branch refs/heads/vectl/scratch/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper-517e00b9

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/case-1
HEAD 28036444abcc4ac4d0a3e620b10afff36f0ad177
branch refs/heads/vectl/scratch/case-1-4aadc860

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.deploy
HEAD 28036444abcc4ac4d0a3e620b10afff36f0ad177
branch refs/heads/vectl/scratch/core.deploy-c70527d6

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.independent
HEAD 28036444abcc4ac4d0a3e620b10afff36f0ad177
branch refs/heads/vectl/scratch/core.independent-f2877286

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.ready
HEAD 28036444abcc4ac4d0a3e620b10afff36f0ad177
branch refs/heads/vectl/scratch/core.ready-9567c86b

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts
HEAD 1dc963e540869520032f237c5964d0d578032e3c
branch refs/heads/vectl/scratch/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts-b54ea9fe

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/case-1
HEAD 9fb6c5bcb5463e03fd546a71d62b9475c006495e
branch refs/heads/vectl/scratch/case-1-dca86024

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.deploy
HEAD 9fb6c5bcb5463e03fd546a71d62b9475c006495e
branch refs/heads/vectl/scratch/core.deploy-b0a53b65

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.independent
HEAD 9fb6c5bcb5463e03fd546a71d62b9475c006495e
branch refs/heads/vectl/scratch/core.independent-d309a7df

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.ready
HEAD 9fb6c5bcb5463e03fd546a71d62b9475c006495e
branch refs/heads/vectl/scratch/core.ready-dfa2473b

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2J01VFZ5EW9VTYN4P83FJ0
HEAD 006fc86dd666f52509533b15ac60fb1778c17cd3
branch refs/heads/vectl/scratch/case-01KQ2J01VFZ5EW9VTYN4P83FJ0-62b1e71c

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2VNKWCYHRBTBKXWC32S4W8
HEAD 006fc86dd666f52509533b15ac60fb1778c17cd3
branch refs/heads/vectl/scratch/case-01KQ2VNKWCYHRBTBKXWC32S4W8-ead01f54

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2YMCJAV859S1WQ19WKXBS8
HEAD 593421e1f40068a4a42848b42036a7ad8bc569ca
branch refs/heads/vectl/scratch/case-01KQ2YMCJAV859S1WQ19WKXBS8-f060608b

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ312QJCZ7D1FXCW8D8YWG67
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ312QJCZ7D1FXCW8D8YWG67-079fd39a

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ31ZCT905YQTQ95WQHFTPTT
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ31ZCT905YQTQ95WQHFTPTT-716375f2

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ39RPYDTZK144JQV0KGDEHW
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ39RPYDTZK144JQV0KGDEHW-75b17ac9

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3AA7FMFJX962VY2EE9M22H
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3AA7FMFJX962VY2EE9M22H-0e9e8418

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3J59R5BQP7TJB1R1YCHC5Y
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3J59R5BQP7TJB1R1YCHC5Y-9a7cee7e

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3K7ASHYMYKFNV045Q7WAHV
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3K7ASHYMYKFNV045Q7WAHV-25b2b413

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3M5PK0KZW7ZA15R0SGE8Y5
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3M5PK0KZW7ZA15R0SGE8Y5-92cf0bd4

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3PY41414CRS355R7TB8EYR
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3PY41414CRS355R7TB8EYR-afad4d08

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3RYFNMCFMZ3GW77MQFM41J
HEAD 788e506ed9869e6882f0894132409750d1513e17
branch refs/heads/vectl/scratch/case-01KQ3RYFNMCFMZ3GW77MQFM41J-11e5e91d

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3VXRDP7N0GH9VDCHABDRTE
HEAD cf9f7f15540684e0e1464d44d9a612921083c9c0
branch refs/heads/vectl/scratch/case-01KQ3VXRDP7N0GH9VDCHABDRTE-4e3c3b58

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3XR7YNX5D6Q1EF03NY650C
HEAD 732725970e0df9c7cfd3221005b4020760ab2a96
branch refs/heads/vectl/scratch/case-01KQ3XR7YNX5D6Q1EF03NY650C-7c66cf8b

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4D0JK3RQCSRYVNF05XJBEK
HEAD 5ad95fa2621f0350cdd0a0da6cc6db713a3ff853
branch refs/heads/vectl/scratch/case-01KQ4D0JK3RQCSRYVNF05XJBEK-48e4741d

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4J5FND2563SHRDGRQC2WNQ
HEAD 3159a5e57c6c601203b9b22f3169141e6dca36f7
branch refs/heads/vectl/scratch/case-01KQ4J5FND2563SHRDGRQC2WNQ-094ed60e

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5AWR2WBP7W15K1KDCJ1W0J
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/case-01KQ5AWR2WBP7W15K1KDCJ1W0J-9c5f6d03

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5CQV8ACKA6ZK5XRDSJXFNF
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/case-01KQ5CQV8ACKA6ZK5XRDSJXFNF-57ad3f27

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR
HEAD 1f50f4a521939a05ca8ab8624d0b91f988761f88
branch refs/heads/vectl/scratch/case-01KQ5DF3FCP95FVFFP2Z4EMRMR-01509ffd

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR/.vectl/workspaces/core.ready
HEAD 1f50f4a521939a05ca8ab8624d0b91f988761f88
branch refs/heads/vectl/scratch/core.ready-217e9482

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5EKWSE1E1209S8D84KGJDR
HEAD 1f50f4a521939a05ca8ab8624d0b91f988761f88
branch refs/heads/vectl/scratch/case-01KQ5EKWSE1E1209S8D84KGJDR-15773fec

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ7XBHY77AB70NH750QN3N4Y
HEAD 4085f0a3d122c999224c99ea174ec3f26b009226
branch refs/heads/vectl/scratch/case-01KQ7XBHY77AB70NH750QN3N4Y-04470408

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/case-1
HEAD dbee319b6767b4cb80faee559abb43bb5b0038f2
branch refs/heads/vectl/scratch/case-1-ea0cc0be

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/config_role_profile_overrides_impl.merge-loader-and-registry
HEAD 2ced0d9bb600d581c90c008841965bf5228bc4cf
branch refs/heads/vectl/scratch/config_role_profile_overrides_impl.merge-loader-and-registry-e0166bcd

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/core.deploy
HEAD dbee319b6767b4cb80faee559abb43bb5b0038f2
branch refs/heads/vectl/scratch/core.deploy-e2bca8b7

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/core.fix
HEAD 2d8491e0a2c704bdf43a0bdf830a2d9976813cda
branch refs/heads/vectl/scratch/core.fix-36c42e10

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/core.independent
HEAD dbee319b6767b4cb80faee559abb43bb5b0038f2
branch refs/heads/vectl/scratch/core.independent-1c65a298

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/core.ready
HEAD dbee319b6767b4cb80faee559abb43bb5b0038f2
branch refs/heads/vectl/scratch/core.ready-8195bc7f

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/core.seed
HEAD 1126f55e6a8280355a6ad3c569d3b8aba682a934
branch refs/heads/vectl/scratch/core.seed-11a5b467

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/invar_guard_remediation.collect-failure-inventory
HEAD 4085f0a3d122c999224c99ea174ec3f26b009226
branch refs/heads/vectl/scratch/invar_guard_remediation.collect-failure-inventory-ed990f53

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-cli-blackbox-finalization
HEAD 1f50f4a521939a05ca8ab8624d0b91f988761f88
branch refs/heads/vectl/scratch/post_review_remediation.reverify-cli-blackbox-finalization-2dd42fa7

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/post_review_remediation.reverify-opencode-runner-final-gate-377ee6fd

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/case-1
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/case-1-8e320816

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.deploy
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/core.deploy-dd4c4f82

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.independent
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/core.independent-a2ba2d52

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.ready
HEAD 733f7b0c8c3cf6626dfc2f24d5b5e59acdac3ea2
branch refs/heads/vectl/scratch/core.ready-7533aabc

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_5a24847757034017b4967448bb06180f
HEAD 4085f0a3d122c999224c99ea174ec3f26b009226
branch refs/heads/vectl/scratch/runtime_5a24847757034017b4967448bb06180f-2a19ace9

worktree /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_c84c5e4ad52b40d185300da2442a6bfa
HEAD 4085f0a3d122c999224c99ea174ec3f26b009226
branch refs/heads/vectl/scratch/runtime_c84c5e4ad52b40d185300da2442a6bfa-7ad36c4a

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/case-1
HEAD ae1405262e1438c95a885b30e3d2afe5b40af835
branch refs/heads/vectl/scratch/case-1-9ae43629
prunable gitdir file points to non-existent location

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.deploy
HEAD ae1405262e1438c95a885b30e3d2afe5b40af835
branch refs/heads/vectl/scratch/core.deploy-24e9b6cd
prunable gitdir file points to non-existent location

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.independent
HEAD ae1405262e1438c95a885b30e3d2afe5b40af835
branch refs/heads/vectl/scratch/core.independent-1667e830
prunable gitdir file points to non-existent location

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.ready
HEAD ae1405262e1438c95a885b30e3d2afe5b40af835
branch refs/heads/vectl/scratch/core.ready-1d07040b
prunable gitdir file points to non-existent location

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/egr_inventory_classify_43_errors
HEAD 5ae67db82531cae5e0c5c217809ef1b3c479b2a5
branch refs/heads/vectl/step-egr_inventory_classify_43_errors

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_scope_matrix
HEAD 5ae67db82531cae5e0c5c217809ef1b3c479b2a5
branch refs/heads/vectl/step-orch_removal_scope_matrix

worktree /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_worktree_policy
HEAD 5ae67db82531cae5e0c5c217809ef1b3c479b2a5
branch refs/heads/vectl/step-orch_removal_worktree_policy

```

### Run: safe directory inspection -> PASS
```text
SAFE DIRECTORY INSPECTION
roots inspected:
- /Users/tefx/Projects/vectl/.vectl/workspaces exists=True
- /Users/tefx/Projects/vectl/.vectl/worktrees exists=True

immediate entries:
## /Users/tefx/Projects/vectl/.vectl/workspaces
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/.vectl
  status: UNREGISTERED_IMMEDIATE_NO_REGISTERED_NESTED
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper
  status: REGISTERED_EXACT
  registered_prunable: False
  nested_registered_paths:
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/case-1 prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.deploy prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.independent prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.ready prunable=False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts
  status: REGISTERED_EXACT
  registered_prunable: False
  nested_registered_paths:
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/case-1 prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.deploy prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.independent prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.ready prunable=False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2J01VFZ5EW9VTYN4P83FJ0
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2VNKWCYHRBTBKXWC32S4W8
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2YMCJAV859S1WQ19WKXBS8
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ312QJCZ7D1FXCW8D8YWG67
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ31ZCT905YQTQ95WQHFTPTT
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ39RPYDTZK144JQV0KGDEHW
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3AA7FMFJX962VY2EE9M22H
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3J59R5BQP7TJB1R1YCHC5Y
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3K7ASHYMYKFNV045Q7WAHV
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3M5PK0KZW7ZA15R0SGE8Y5
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3PY41414CRS355R7TB8EYR
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3RYFNMCFMZ3GW77MQFM41J
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3VXRDP7N0GH9VDCHABDRTE
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3XR7YNX5D6Q1EF03NY650C
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4D0JK3RQCSRYVNF05XJBEK
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4J5FND2563SHRDGRQC2WNQ
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5AWR2WBP7W15K1KDCJ1W0J
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5CQV8ACKA6ZK5XRDSJXFNF
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR
  status: REGISTERED_EXACT
  registered_prunable: False
  nested_registered_paths:
    - /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR/.vectl/workspaces/core.ready prunable=False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5EKWSE1E1209S8D84KGJDR
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ7XBHY77AB70NH750QN3N4Y
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/case-1
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/config_role_profile_overrides_impl.merge-loader-and-registry
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/core.deploy
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/core.fix
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/core.independent
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/core.ready
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/core.seed
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/invar_guard_remediation.collect-failure-inventory
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-cli-blackbox-finalization
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate
  status: REGISTERED_EXACT
  registered_prunable: False
  nested_registered_paths:
    - /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/case-1 prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.deploy prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.independent prunable=False
    - /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.ready prunable=False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_5a24847757034017b4967448bb06180f
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_c84c5e4ad52b40d185300da2442a6bfa
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/workspaces/test-phase.step1
  status: UNREGISTERED_IMMEDIATE_NO_REGISTERED_NESTED
## /Users/tefx/Projects/vectl/.vectl/worktrees
- path: /Users/tefx/Projects/vectl/.vectl/worktrees/egr_inventory_classify_43_errors
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_scope_matrix
  status: REGISTERED_EXACT
  registered_prunable: False
- path: /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_worktree_policy
  status: REGISTERED_EXACT
  registered_prunable: False

registered worktrees under cleanup roots:
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/case-1 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.deploy prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.independent prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper/.vectl/workspaces/core.ready prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/case-1 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.deploy prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.independent prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts/.vectl/workspaces/core.ready prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2J01VFZ5EW9VTYN4P83FJ0 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2VNKWCYHRBTBKXWC32S4W8 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2YMCJAV859S1WQ19WKXBS8 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ312QJCZ7D1FXCW8D8YWG67 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ31ZCT905YQTQ95WQHFTPTT prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ39RPYDTZK144JQV0KGDEHW prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3AA7FMFJX962VY2EE9M22H prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3J59R5BQP7TJB1R1YCHC5Y prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3K7ASHYMYKFNV045Q7WAHV prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3M5PK0KZW7ZA15R0SGE8Y5 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3PY41414CRS355R7TB8EYR prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3RYFNMCFMZ3GW77MQFM41J prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3VXRDP7N0GH9VDCHABDRTE prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3XR7YNX5D6Q1EF03NY650C prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4D0JK3RQCSRYVNF05XJBEK prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4J5FND2563SHRDGRQC2WNQ prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5AWR2WBP7W15K1KDCJ1W0J prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5CQV8ACKA6ZK5XRDSJXFNF prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR/.vectl/workspaces/core.ready prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5EKWSE1E1209S8D84KGJDR prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ7XBHY77AB70NH750QN3N4Y prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/case-1 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/config_role_profile_overrides_impl.merge-loader-and-registry prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/core.deploy prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/core.fix prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/core.independent prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/core.ready prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/core.seed prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/invar_guard_remediation.collect-failure-inventory prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-cli-blackbox-finalization prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/case-1 prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.deploy prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.independent prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate/.vectl/workspaces/core.ready prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_5a24847757034017b4967448bb06180f prunable=False
- /Users/tefx/Projects/vectl/.vectl/workspaces/runtime_c84c5e4ad52b40d185300da2442a6bfa prunable=False
- /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/case-1 prunable=True
- /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.deploy prunable=True
- /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.independent prunable=True
- /Users/tefx/Projects/vectl/.vectl/worktrees/det_check_final_gate/.vectl/workspaces/core.ready prunable=True
- /Users/tefx/Projects/vectl/.vectl/worktrees/egr_inventory_classify_43_errors prunable=False
- /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_scope_matrix prunable=False
- /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_worktree_policy prunable=False
```

### Run: inspect `.vectl/` artifact directory -> PASS
```text
/Users/tefx/Projects/vectl/.vectl/continuity
/Users/tefx/Projects/vectl/.vectl/continuity/journal
/Users/tefx/Projects/vectl/.vectl/continuity/quarantine
/Users/tefx/Projects/vectl/.vectl/continuity/ledger
/Users/tefx/Projects/vectl/.vectl/workspaces
/Users/tefx/Projects/vectl/.vectl/workspaces/runtime_5a24847757034017b4967448bb06180f
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ7XBHY77AB70NH750QN3N4Y
/Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-002-extract-shared-next-step-helper
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2VNKWCYHRBTBKXWC32S4W8
/Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-cli-blackbox-finalization
/Users/tefx/Projects/vectl/.vectl/workspaces/core.ready
/Users/tefx/Projects/vectl/.vectl/workspaces/core.seed
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3RYFNMCFMZ3GW77MQFM41J
/Users/tefx/Projects/vectl/.vectl/workspaces/runtime_c84c5e4ad52b40d185300da2442a6bfa
/Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3J59R5BQP7TJB1R1YCHC5Y
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3PY41414CRS355R7TB8EYR
/Users/tefx/Projects/vectl/.vectl/workspaces/core.fix
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3XR7YNX5D6Q1EF03NY650C
/Users/tefx/Projects/vectl/.vectl/workspaces/invar_guard_remediation.collect-failure-inventory
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5EKWSE1E1209S8D84KGJDR
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5AWR2WBP7W15K1KDCJ1W0J
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3K7ASHYMYKFNV045Q7WAHV
/Users/tefx/Projects/vectl/.vectl/workspaces/test-phase.step1
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ31ZCT905YQTQ95WQHFTPTT
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5CQV8ACKA6ZK5XRDSJXFNF
/Users/tefx/Projects/vectl/.vectl/workspaces/.vectl
/Users/tefx/Projects/vectl/.vectl/workspaces/core.deploy
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4J5FND2563SHRDGRQC2WNQ
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ4D0JK3RQCSRYVNF05XJBEK
/Users/tefx/Projects/vectl/.vectl/workspaces/arch-demolition-wave-4.dem-005-and-dem-006-deduplicate-control-and-resolver-contracts
/Users/tefx/Projects/vectl/.vectl/workspaces/core.independent
/Users/tefx/Projects/vectl/.vectl/workspaces/config_role_profile_overrides_impl.merge-loader-and-registry
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3VXRDP7N0GH9VDCHABDRTE
/Users/tefx/Projects/vectl/.vectl/workspaces/post_review_remediation.reverify-opencode-runner-final-gate
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ39RPYDTZK144JQV0KGDEHW
/Users/tefx/Projects/vectl/.vectl/workspaces/case-1
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2J01VFZ5EW9VTYN4P83FJ0
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3AA7FMFJX962VY2EE9M22H
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ312QJCZ7D1FXCW8D8YWG67
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ3M5PK0KZW7ZA15R0SGE8Y5
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ5DF3FCP95FVFFP2Z4EMRMR
/Users/tefx/Projects/vectl/.vectl/workspaces/case-01KQ2YMCJAV859S1WQ19WKXBS8
/Users/tefx/Projects/vectl/.vectl/driver-events.jsonl
/Users/tefx/Projects/vectl/.vectl/reconcile-untracked
/Users/tefx/Projects/vectl/.vectl/reconcile-untracked/exec-arch-demolition-wave-1.dem-003-extract-duplicate-step-id-formatting-owner-d01b179c
/Users/tefx/Projects/vectl/.vectl/worktrees
/Users/tefx/Projects/vectl/.vectl/worktrees/egr_inventory_classify_43_errors
/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_scope_matrix
/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_worktree_policy
/Users/tefx/Projects/vectl/.vectl/runs
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5D4SWXEDEK6A99K2010JSA
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5ED0N6DQA0F8PVCCJ1ETWD
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3W8ENKXMBYW6J1XGMEQK52
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3PY561PW95KVM5SFDKB2AY
/Users/tefx/Projects/vectl/.vectl/runs/01KNEC1P0K7SBAZND1MMWG6QFV
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2V4SZ66Q0VR345FF2MJJ03
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TY5NQRCCAF2ZB3CYS2DDQ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2GGGS5V23CGJZY08WMA7FK
/Users/tefx/Projects/vectl/.vectl/runs/drv_1e2917bd2d1349c7a7f05ecac5f6b2c4
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4HMZBHWNNBF4MNSXGQV5J1
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3XA5AWYWDYZSK1GFWC7A7V
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5ASZ12ZZYMVGYYKF0DRC66
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4FRKZVQPX11K574N3XBB09
/Users/tefx/Projects/vectl/.vectl/runs/exec-arch-demolition-wave-1.dem-001-canonicalize-agents-md-ownership-03025498
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3JAXWHTX3ARS85G2HTNVBW
/Users/tefx/Projects/vectl/.vectl/runs/drv_d7d16af4506a4333b306f2bcddda572a
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4EXFRVS84F20Q6CM9TEZ8C
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TRXJVNHK0XCTM3C0WW53Q
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4N6V6SKHMFAABNJDSGHSQP
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TKCX0G6X0PXWYASDTR3XJ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4CCPXWQDRJT1TE7SCSDX1K
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4D2TR7WDQD16RXAFS069HG
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3MEBXFZA44M4FHAJZYMVZD
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2XHSJJQAC9GQ7VKW08NTEQ
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3XRA4FQ23RY5SX0S54H7P1
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2T6DKKSK0QS4HQAG897D98
/Users/tefx/Projects/vectl/.vectl/runs/drv_ea441baa1a6a49418e3849d4592b341e
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2VTWN6DH1J5J6ANBFAMGG9
/Users/tefx/Projects/vectl/.vectl/runs/index.jsonl
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3K7BXZT020PXQZDVYYQ4HZ
/Users/tefx/Projects/vectl/.vectl/runs/orchestration.log
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4N888J7052B7R6RHEM2ZKT
/Users/tefx/Projects/vectl/.vectl/runs/01KQ821RREP22PSJHBBXATY9QE
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3J5DXQC7VDAX2F4TT5CQHB
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5ASSYZP47X87RNKWR5SBMN
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5CQZT8NWGP1STG3RABS1NV
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2VNPS5XSNNPKV7TXTJ36XD
/Users/tefx/Projects/vectl/.vectl/runs/drv_d43ab301ad6042f38ff4788bc00c51da
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2YMDK81X663DF44HZF9Q2P
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2494S3F1BGRBF2ECJXGR4V
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2V31QGFVJ9E6JWC83Z2M4Q
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5EM3GTQWKGQM4V8NW1S101
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ825Q9S61JM99HPZX06WKDW
/Users/tefx/Projects/vectl/.vectl/runs/01KQ8233N7CK6312Q5S0M22MBZ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4E971GA19GCJ8EKKT3DRBS
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5D33ZPNYKVC7WVM9T3D3Z2
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TNGAFVH31AR3HX1GPV00T
/Users/tefx/Projects/vectl/.vectl/runs/01KQ39TCMKKZA4P8NEJ6RP3HHQ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5E52CV98RSGG91EPMQ4PBJ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ7WYM1306G9X6GADVG10TBY
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ81TMH9CM32PXMEFYARHXHK
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2PBYEAV7ZM5EX2SMHYC2KS
/Users/tefx/Projects/vectl/.vectl/runs/01KQ43AAXRC7BTB1C6ERRJCW17
/Users/tefx/Projects/vectl/.vectl/runs/drives
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5F4284S485SBGBP6JY3D14
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5DF87G2CV13XBMSMYSGJV7
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2VXCA3K5MR7WR2THCPYDTJ
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TBDWQ26HK2EJ0FV64PAAZ
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ8238YWGA71Z0X7JVNTP66D
/Users/tefx/Projects/vectl/.vectl/runs/drv_74246ec6015e46c79b4fe4bc25e1b619
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4CB5DBQC21QK9DZQGVMPWX
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3158472Y6CV16Z1E27BGD2
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ31ZEYHDTETAT1CYNCRWXN6
/Users/tefx/Projects/vectl/.vectl/runs/01KNEC1NZKA53C0ZP713N8ZGA8
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2QNJ1C7D4T51K6YNTW23QS
/Users/tefx/Projects/vectl/.vectl/runs/01KQ44R2GQH6NAV66DWN46HK5Z
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TE5JHFY908JGZT8SWESV5
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5ECKY4PNWCJ7CT147Q3ZA8
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5F6MCMPXAW69R0S7PQG1XY
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5DZA5W057K34K4Q1X3CF0Z
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3VXSKBNHCBGXC271A5D1QH
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ7XBKARMRBFWG51GSFB7J66
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3MCSVKHPB0EFJY5RNY748P
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ4D0KVZ4CZV96E7RWPD76MX
/Users/tefx/Projects/vectl/.vectl/runs/01KQ1WF3FC0PNRDYTS736Z6V4A
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5CH6W55DSVV02F6ZX7Q1MF
/Users/tefx/Projects/vectl/.vectl/runs/drv_43c9ab4709324af1a5e3062a6a27d8d8
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4FTVTZQ8BVF1K363W4FJGX
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3RYGTN7Y3P8JQJFZKDAW85
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5EK020WMYH0EGFY4W4TV1Y
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3Q473AN7B07RM18ZDJ97PX
/Users/tefx/Projects/vectl/.vectl/runs/01KNYVFC7FQ1KYFS6HEFWTX461
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5CHC1NS6961T6SF7AE9N96
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ312SMJCMF1YR7MSAY35ETB
/Users/tefx/Projects/vectl/.vectl/runs/events.jsonl
/Users/tefx/Projects/vectl/.vectl/runs/events.jsonl.lock
/Users/tefx/Projects/vectl/.vectl/runs/01JXTEST
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3X09J2GPEVK5EQG3RRAJ9W
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5D50J72644SEW2EMZY96QA
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3AA98K1TF377MV1ARPDDHK
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3Q97FAYQN52033MZA554SG
/Users/tefx/Projects/vectl/.vectl/runs/.admission.lock
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3S6EP19YRZE2KP11W90S7R
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2WVSB9M5YY7NKTNHE9DE6V
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2T2DRB2WCC1G5TCVYWZYNT
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2QG4EBMK5626016443T9A5
/Users/tefx/Projects/vectl/.vectl/runs/drv_c7bbe5448f2346cbae7a18164bbc2496
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3M5QQSJX5QAZ6CWQG20JS8
/Users/tefx/Projects/vectl/.vectl/runs/01KQ4DKWT66EG5KVEGQ4M2QSRK
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5BP4CY7HQJT261MFSB20HW
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3S519JK7MD2576GCJ88ASZ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ2YPYKTQ07WHWY4C9F329G1
/Users/tefx/Projects/vectl/.vectl/runs/01KQ825J1QYMAFQJ1BKFF5Q750
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ39RRP0XFAETV958H484FA8
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2SEBM925HW5GAHQZFPWXZB
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3J9GMFJ93TNWJV1S15WJ6V
/Users/tefx/Projects/vectl/.vectl/runs/01KQ43XYEH8PTYADPQFNVC315Y
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ4J5H2G3X1QB43EMRZGCDA2
/Users/tefx/Projects/vectl/.vectl/runs/01KQ5E4X6SX6F99B6FVQNSX4PG
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ5AWTGQNJASN3TVJZ4V3073
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2TW7020588MF2ZYPZWJJ4Z
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3W9XRFPKMCDV6CBAEVA06N
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ2Q1ZV6M4NMKW8TS3V7YPFV
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3F4EFGC1G7V6CSS400JS7Z
/Users/tefx/Projects/vectl/.vectl/runs/drv_f58d0d69bfab439995048f3e76af5c42
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3V7VVZ4KJ0JQN5QXPXRKVV
/Users/tefx/Projects/vectl/.vectl/runs/drv_18ac1a9518f746fe92f44d51c53430a7
/Users/tefx/Projects/vectl/.vectl/runs/01KQ384HBWV1KMD1F7G24V1G63
/Users/tefx/Projects/vectl/.vectl/runs/resolver-01KQ3KBD853923J4PPEAE91TFC
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3KDNNTMY7BJB478W7M425Y
/Users/tefx/Projects/vectl/.vectl/runs/drv_0478354927ce4201ba23cc7a1414d3cc
/Users/tefx/Projects/vectl/.vectl/runs/01KQ45KCH91SW8KF0Q01Y3HRDJ
/Users/tefx/Projects/vectl/.vectl/runs/01KQ3XANGJ4W69FK79XB41F622
```

### Run: required-ref file presence check -> PASS with missing-ref notes
```text
AGENTS.md
```

### Run: git status --short before artifact write -> PASS
```text
```

## Spec link
- user-request:2026-06-17#safe-worktree-cleanup

## Changed Files
- `.agent/evidence/orch_removal_worktree_policy.md`

## Gaps / Notes
- `tools/vectl/README.md` is not present in this isolated worktree, so it could not be read.
- `.vectl/` is not present as a child of the isolated worktree root; the actual artifact/worktree directory inspected was `/Users/tefx/Projects/vectl/.vectl`.
- No cleanup commands (`git worktree remove`, `git worktree prune`, or deletion commands) were run.
