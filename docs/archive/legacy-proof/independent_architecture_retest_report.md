## [Review] Architecture Retest Report

**Tester**: software-architect (independent)
**Step**: `orch_operator_deep_review.retest_architecture_blockers`
**Date**: 2026-04-05

### refs Read Confirmation

- `tools/vectl/README.md`: read attempted before review; file not present at `/Users/tefx/Projects/vectl/.vectl/worktrees/orch_operator_deep_review.retest_architecture_blockers/tools/vectl/README.md`.

## Behavioral Proof Register

| blocker_family | requirement_ref | behavior_claim | runtime_proof_expected | evidence_ref | status | closure_path | gate_decision_basis |
|---|---|---|---|---|---|---|---|
| B1 boundary integrity | `orch_operator_deep_review.architecture_review/B1` | Inspection/query surfaces depend only on explicit public read contracts, not `RunRegistry` private helpers. | Query tests should prove `RunsQueryImpl` works against an injected boundary object and source search should show no `_latest_*` helper reach-through from inspection code. | `src/vectl/orchestration/inspection_queries.py:28-33,172-255,295-395`; `src/vectl/orchestration/run_store.py:899-943`; `tests/orchestration/unit/test_inspection_queries.py:7-49`; `rg -n "_latest_records_by_run_id|_latest_cases_by_case_id" src/vectl/orchestration/inspection_queries.py` -> no matches | PROVEN | `RunInspectionBoundary` + `RunRegistryInspectionView` now own query-facing reads; inspection module consumes only the adapter boundary. | No private-helper reach-through remains in inspection code, and executable proof shows the query layer accepts a fake boundary object instead of concrete registry internals. |
| B2 state ownership / concurrency safety | `orch_operator_deep_review.architecture_review/B2` | Events and control-channel state are no longer process-global mutable registries; ownership is explicit, bounded, and instance-scoped. | Source search should show removal of default `ContextVar` registry/channel state, and runtime tests should prove explicit registry/channel injection plus concurrent event append ordering. | `src/vectl/orchestration/events.py:371-496`; `src/vectl/orchestration/control_channel.py:613-630`; `tests/orchestration/unit/test_events.py:197-238`; `tests/orchestration/unit/test_control_channel.py:135-143`; `rg -n "ContextVar|set_default_event_registry|get_default_event_registry|reset_default_event_registry|set_default_control_channel|get_default_control_channel|reset_default_control_channel|_PATH_LOCKS" src/vectl/orchestration` -> no matches | PROVEN | Event sink locking is instance-owned (`_thread_lock` + file lock) and control send requires explicit `channel=` injection. | The old process-global mutable registry pattern is absent. Concurrency proof covers parallel writers, and lifecycle ownership is explicit at the call boundary for both events and control. |
| B3 durability / ordering | `orch_operator_deep_review.architecture_review/B3` | Run admission, running-state persistence, and event sequencing now share a single ordered durability path. | Tests should prove pending is durable before runtime start, events occur only after running record is durable, and event-sink failure terminalizes the run. | `src/vectl/orch_app.py:340-438,490-520,639-652`; `src/vectl/orchestration/run_store.py:568-606`; `tests/orchestration/unit/test_orch_app.py:126-205`; `rg -n "_admit_start_and_persist_running|admission_guard\(|run_started|run_status_changed" src/vectl/orch_app.py` | PROVEN | `_admit_start_and_persist_running()` wraps admission guard, pending save, runtime start, running save, and event emit under one ordered control path with fail-terminalization on runtime/event faults. | Evidence shows no split best-effort path remains for start/resume. Failure paths are explicit and durably terminalized, closing the non-atomic sequencing blocker. |
| B4 workspace-root ownership | `orch_operator_deep_review.architecture_review/B4` | Runtime workspace resolution is owned by `RuntimeConfig.workspace_root`, not hard-coded `.vectl/worktrees`. | Source search should show no remaining `.vectl/worktrees` literals in runtime pathing, and tests should prove configured workspace roots are honored in app/runtime flows. | `src/vectl/orchestration/runtime.py:151-216`; `src/vectl/orch_app.py:220-238,2216-2225`; `tests/orchestration/unit/test_runtime.py:62-135`; `tests/orchestration/unit/test_orch_app.py:220-238`; `rg -n "\.vectl/worktrees" src` -> no matches | PROVEN | `Runtime.workspace_root` is instance state, and composition root wires runtime from orchestration config. | Runtime path ownership now follows config authority; executable tests confirm custom workspace roots and fresh isolation behavior under `.vectl/workspaces`-style config. |

## Overall Verdict

- gate_open_allowed: true
- rationale:
  - All four blocker families were re-reviewed independently against both source boundaries and executable proof.
  - No blocker-class issue remains intersecting the final gate.
  - No unproven architecture claim remains in B1-B4 scope.

### Certainty

- B1: [Proven]
- B2: [Proven]
- B3: [Proven]
- B4: [Proven]
