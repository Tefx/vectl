# Full-suite failure inventory — 2026-04-10

Command provenance:

```bash
uv run pytest -q
```

Observed suite result on this worktree:

- 31 failed
- 1754 passed
- 39 skipped
- 19 xfailed
- 1 warning

> Note: the task brief expected **27 current failures**, but the exact command above produced **31 current failures** in this worktree. This inventory records the observed 31-failure set without expanding scope.

## Normalized remediation buckets

| Bucket | Count | Initial category | Owner hypothesis |
| --- | ---: | --- | --- |
| A | 7 | stale expected-red config/run-registry tests vs implemented surfaces | orchestration config + run-store maintainers (`src/vectl/orchestration/config.py`, `src/vectl/orchestration/run_store.py`) |
| B | 3 | orchestration contract/export surface drift | orchestration boundary maintainers (`src/vectl/orchestration/contracts.py`, `src/vectl/orchestration/__init__.py`) |
| C | 2 | recovery status/quarantine semantics drift | recovery + app inspection maintainers (`src/vectl/orchestration/recovery.py`, `src/vectl/orch_app.py`) |
| D | 1 | runtime reconcile tracking regression | runtime lifecycle maintainers (`src/vectl/orchestration/runtime.py`) |
| E | 7 | missing/incomplete driver CLI + liveness fixture | CLI/driver maintainers (`src/vectl/cli.py`, missing `driver` surface / missing `driver.yaml`) |
| F | 11 | observability contract backlog / stale expected-red probes | observability maintainers (`src/vectl/orchestration/events.py`, `projections.py`, `run_store.py`, `tool_registry.py`) |

## Companion xfail/skip context relevant to disposal

- `tests/orchestration/unit/test_dispatch_policy_red.py` contributed **17 xfails** in the same run. Those remain quarantined as explicit expected-red dispatch/prompt-policy gaps, so they are adjacent backlog rather than new regressions.
- `tests/orchestration/unit/test_runner_backend_red.py` contributed **2 xfails** in the same run. That is adjacent runner-backend backlog while one runtime-lifecycle test is now hard-failing.
- The failing files below are **not** xfail-marked; several are documented in-file as `expected_result: red` / `EXPECTED-RED`, but currently fail as normal test failures.
- The suite also reported **39 skips** (live smoke, dashboard UI, repair black-box coverage). Those skips appear orthogonal to the failing inventory and do not change initial disposal ordering for the failures below.

## Failure inventory by file/test

### Bucket A — stale expected-red config/run-registry tests vs implemented surfaces (7)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/orchestration/unit/test_config_state_red.py` | `TestConfigDiscoveryGaps::test_config_loader_not_implemented` | stale expected-red | config maintainers | `load_orchestration_config()` now returns a config tuple instead of raising `NotImplementedError`. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestConfigDiscoveryGaps::test_discovery_order_vectl_config_env_not_specified` | stale expected-red | config maintainers | discovery path is implemented enough to avoid the expected `NotImplementedError`. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestConfigDiscoveryGaps::test_env_var_naming_convention_not_enforced` | stale expected-red | config maintainers | env override path no longer raises the documented stub error. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestConfigValidationGaps::test_validation_rules_not_implemented` | stale expected-red | config maintainers | loader path no longer raises `NotImplementedError`; test needs re-baseline or conversion to positive validation assertions. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestFrozenConfigSnapshotGaps::test_freeze_config_is_noop` | stale expected-red | config maintainers | `freeze_config()` now returns `FrozenConfigSnapshot(config=config.freeze(), ...)`, so identity/no-op assertion is obsolete. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestRunRegistryGaps::test_run_registry_not_implemented` | stale expected-red | run-store maintainers | `RunRegistry.save()` no longer raises the expected persistence stub error. |
| `tests/orchestration/unit/test_config_state_red.py` | `TestRunRegistryGaps::test_latest_lookup_not_implemented` | stale expected-red | run-store maintainers | `latest_run()` delegates to `RunRegistry.latest_for_step()` instead of raising `NotImplementedError`. |

### Bucket B — orchestration contract/export surface drift (3)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/orchestration/unit/test_contract_lock_boundaries.py` | `test_resolution_case_stays_bounded_while_resolver_contract_is_exported` | contract drift | contracts maintainers | `ResolutionCase` now carries 9 fields (`case_id`, `case_source`, `summary`, `blocked_step_ids`, `artifact_refs` added) while contract-lock test still expects 4. |
| `tests/orchestration/unit/test_contracts.py` | `TestResolutionCase::test_resolution_case_fields_match_spec` | contract drift | contracts maintainers | spec/test still pins the older 4-field shape. |
| `tests/orchestration/unit/test_imports.py` | `TestPackageImports::test_orchestration_all_exports_match_spec` | export surface drift | package boundary maintainers | `src/vectl/orchestration/__init__.py` exports dispatch/prompt helpers beyond the older bounded `__all__` contract. |

### Bucket C — recovery status/quarantine semantics drift (2)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/orchestration/unit/test_recovery_contract_conformance.py` | `test_recovery_report_semantics_are_shared_across_consumer_surfaces` | recovery projection drift | recovery + orch-app maintainers | `inspect_actions()` output did not include the expected blocked-status row derived from `recovery_action_status(report.outcome)`. |
| `tests/orchestration/unit/test_recovery_contract_conformance.py` | `test_recovery_quarantine_preserves_artifacts_when_gate_open_blocked` | recovery gate drift | recovery maintainers | `report.gate_open_allowed` was `True` where the contract test expects quarantine/blocking semantics after stale heartbeat. |

### Bucket D — runtime reconcile tracking regression (1)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/orchestration/unit/test_runtime_lifecycle.py` | `test_capture_reconcile_result_updates_tracking_sets` | runtime state-tracking regression | runtime lifecycle maintainers | `begin_reconcile()` did not populate `pending_reconciles` before capture, so the tracking-set transition contract is broken. |

### Bucket E — missing/incomplete driver CLI + liveness fixture (7)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/repro/test_orch_liveness.py` | `test_orch_liveness_module_entrypoint` | missing liveness fixture/entrypoint | CLI/driver maintainers | hard failure occurs before invocation because repo root has no `driver.yaml`. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_command_exists` | missing CLI surface | CLI maintainers | `runner.invoke(app, ["drive", "--help"])` exits 2, consistent with command not being registered. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_config_flag_recognized` | missing CLI surface | CLI maintainers | `drive --help` exits 2, so `--config` help contract is absent. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_default_config_is_driver_yaml` | missing CLI surface | CLI maintainers | default help text for `driver.yaml` is absent because `drive` command is absent. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_no_longer_imports_removed_driver_entrypoint` | missing Python export | CLI maintainers | `from vectl.cli import drive` raises `ImportError`; no `drive` symbol exists in `src/vectl/cli.py`. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_help_shows_ref_to_blueprint` | missing CLI surface | CLI maintainers | help contract cannot be checked while command registration is absent. |
| `tests/test_cli.py` | `TestDriveCLI::test_drive_help_shows_docstring_description` | missing CLI surface | CLI maintainers | help contract cannot be checked while command registration is absent. |

### Bucket F — observability contract backlog / stale expected-red probes (11)

| File | Test | Initial category | Owner hypothesis | Disposal note |
| --- | --- | --- | --- | --- |
| `tests/repro/test_orch_observability_red.py` | `TestEventEnvelopeIntegrity::test_event_envelope_has_seq_field` | stale probe / constructor-schema mismatch | events maintainers | constructor now validates payload schema first and rejects bare `control_dispatch` envelopes missing `agent`/`step_id` payload keys. |
| `tests/repro/test_orch_observability_red.py` | `TestEventEnvelopeIntegrity::test_event_envelope_has_prev_hash_chain` | stale probe / constructor-schema mismatch | events maintainers | same failure mode as above; probe no longer reaches field assertion. |
| `tests/repro/test_orch_observability_red.py` | `TestEventEnvelopeIntegrity::test_event_envelope_has_entry_hash` | stale probe / constructor-schema mismatch | events maintainers | same failure mode as above; field exists in implementation but test setup no longer matches registry validation rules. |
| `tests/repro/test_orch_observability_red.py` | `TestEventEnvelopeIntegrity::test_event_envelope_hash_computation_is_sha256` | stale probe / constructor-schema mismatch | events maintainers | same payload-schema gate prevents the intended hash-method check. |
| `tests/repro/test_orch_observability_red.py` | `TestProjectionReplay::test_projection_replay_produces_latest_json` | implementation gap | projections maintainers | explicit `NotImplementedError("GAP: replay not implemented")` path still active in test fixture. |
| `tests/repro/test_orch_observability_red.py` | `TestRunArtifactSchemas::test_final_json_schema` | artifact schema gap | run-store / projection maintainers | `RunRecord` is still missing expected `final.json` fields (`summary`, `halt_reason`, `artifacts`) per test contract. |
| `tests/repro/test_orch_observability_red.py` | `TestStepArtifactSchemas::test_step_key_path_normalization` | missing helper | observability artifact maintainers | `normalize_step_key` function is absent. |
| `tests/repro/test_orch_observability_red.py` | `TestStepArtifactSchemas::test_step_key_is_reversible` | missing helper | observability artifact maintainers | `denormalize_step_key` function is absent. |
| `tests/repro/test_orch_observability_red.py` | `TestRunRegistrySchemas::test_latest_run_resolution` | stale expected-red vs implemented lookup | run-store maintainers | test still asserts missing wiring even though `latest_run()` exists and returns a result. |
| `tests/repro/test_orch_observability_red.py` | `TestToolRegistryValidation::test_tool_registry_core_family_tools` | registry naming drift | tool-registry maintainers | implementation exposes family-level registry plus `_CANONICAL_TOOL_TO_FAMILY`, but test expects public `CANONICAL_TOOL_REGISTRY`. |
| `tests/repro/test_orch_observability_red.py` | `TestToolRegistryValidation::test_tool_registry_orchestration_family_tools` | registry naming drift | tool-registry maintainers | same public-surface mismatch as the preceding test. |

## Disposal ordering recommendation

1. Bucket E first: restores missing CLI/driver command surface and removes a visible user-facing failure cluster.
2. Bucket D/C next: these look like active behavior drift rather than stale red tests.
3. Buckets A/B/F after that: mostly re-baseline/spec-alignment work where tests and implementation disagree on whether backlog items are still intentionally red.

## Source anchors used for categorization

- `tests/orchestration/unit/test_config_state_red.py`
- `tests/orchestration/unit/test_contract_lock_boundaries.py`
- `tests/orchestration/unit/test_contracts.py`
- `tests/orchestration/unit/test_imports.py`
- `tests/orchestration/unit/test_recovery_contract_conformance.py`
- `tests/orchestration/unit/test_runtime_lifecycle.py`
- `tests/repro/test_orch_liveness.py`
- `tests/repro/test_orch_observability_red.py`
- `tests/test_cli.py`
- `src/vectl/orchestration/config.py`
- `src/vectl/orchestration/contracts.py`
- `src/vectl/orchestration/__init__.py`
- `src/vectl/orchestration/events.py`
- `src/vectl/orchestration/run_store.py`
- `src/vectl/orchestration/tool_registry.py`
