# [Review] Migration State Audit — Landed Operator Surface

## Scope

Audit the landed migration-state model and legacy-retirement readiness against the current operator-facing orchestration surface.

## Re-anchor to request

Requested outputs:

1. retirement readiness register
2. authority boundary audit
3. same-plan coexistence policy
4. final disposition `OPEN` or `BLOCKED` with blockers
5. refs-read confirmation for `tools/vectl/README.md`

## Refs Read Confirmation

- `[Proven]` Requested ref `tools/vectl/README.md` is not present in this worktree.
- `[Proven]` Read equivalent top-level user surface at `README.md` instead.
- `[Proven]` Supporting evidence: the repo contains `README.md` at root and no `tools/` tree in this worktree.

## Landed Operator Surface Observed

`[Proven]` The landed operator-facing orchestration surface is the `vectl orch` CLI and the composed `OrchestrationApp` facade, specifically:

- run/resume/recover (`src/vectl/cli.py:762-841`)
- runs (`src/vectl/cli.py:847-865`)
- cases (`src/vectl/cli.py:1064-1158`)
- control (`src/vectl/cli.py:1164-1257`)
- config (`src/vectl/cli.py:1263-1329`)

`[Proven]` There is no landed operator-facing cutover/readiness command on this surface.

## Retirement Readiness Register

| Area | Expected authority | Landed implementation | Readiness | Evidence | Rationale |
|---|---|---|---|---|---|
| Migration-state enum | Recovery/cutover contract | `parallel/preferred/deprecated/retired` persisted on `RunRecord.legacy_migration_state` | READY | `src/vectl/orchestration/run_store.py:71-74,125-136`; `src/vectl/orchestration/recovery.py:285-302` | `[Proven]` The state model exists and is consistently named in storage and recovery contracts. |
| Canonical imported-run storage | Single orchestration authority path | Imported legacy runs stored in canonical run registry as `source="legacy_imported"` | READY | `src/vectl/orchestration/run_store.py:683-697`; `src/vectl/orchestration/recovery.py:406-438` | `[Proven]` This preserves one authority path instead of parallel registries. |
| Operator visibility of migration state | Operator can inspect imported-run state | `OrchestrationApp.runs()` and `vectl orch runs` surface `source`, `legacy_run_id`, `legacy_migration_state`, `continuity_blocker` | READY | `src/vectl/orch_app.py:1367-1377`; `tests/orchestration/unit/test_orch_app.py:287-320`; `src/vectl/cli.py:847-865` | `[Proven]` The state is inspectable through the landed operator surface. |
| Continuity-minimum gating | Imported legacy runs must not silently bypass recovery requirements | Missing/invalid continuity artifacts become `continuity_blocker`, force `stall`, and open a case | READY | `src/vectl/orchestration/run_store.py:323-366,668-710`; `tests/orchestration/unit/test_run_store.py:219-243` | `[Proven]` This makes unsafe imports visible and blocking. |
| Same-plan coexistence gate | Active legacy imports should block unsafe duplicate admission | `parallel/preferred/deprecated` + non-terminal status block same-plan admission; `retired` does not | READY | `src/vectl/orchestration/run_store.py:369-374,731-766`; `tests/orchestration/unit/test_run_store.py:184-217,246-272` | `[Proven]` Coexistence behavior is explicit and tested. |
| Cutover readiness decision | Full retirement criteria from migration doc should be operator-verifiable | `CutoverValidator` only checks imported-run presence, blocker cases, and active statuses | BLOCKED | `src/vectl/orchestration/recovery.py:537-652`; `docs/ORCHESTRATION-PLANE-MIGRATION.md:123-130`; `tests/orchestration/unit/test_recovery.py:25-75` | `[Proven]` The implementation does not verify criteria 1-4 from the authority doc; it verifies a narrower imported-run health condition. |
| Operator cutover action surface | Operator should have a landed way to validate/disposition retirement | No CLI/MCP operator command found for cutover validation or migration-state advancement | BLOCKED | `src/vectl/cli.py:762-1329` | `[Proven]` The landed surface exposes runs/cases/control/config, but no cutover/readiness command. |
| Legacy bridge practicality | Import bridge should not create unavoidable blocker debt | `RunStoreLegacyRunBridge.import_legacy_run()` always passes `continuity_artifacts=None`, which guarantees a blocker and open case | BLOCKED | `src/vectl/orchestration/recovery.py:418-438`; `src/vectl/orchestration/run_store.py:328-337,668-710` | `[Proven]` The bridge is authority-safe, but not retirement-ready as an operator workflow because its default import path self-blocks. |

## Authority Boundary Audit

### Preserved boundaries

- `[Proven]` Core remains the declared authority for plan/lifecycle/claims, while orchestration consumes that authority rather than replacing it (`docs/ORCHESTRATION-PLANE-ARCHITECTURE.md:46-60`, `docs/ORCHESTRATION-PLANE-INTERFACES.md:36-46`).
- `[Proven]` Shared orchestration contracts keep `control`, `roster`, `runtime`, and `resolver` roles separated (`src/vectl/orchestration/contracts.py:16-177`, `src/vectl/orchestration/interfaces.py:27-240`).
- `[Proven]` Legacy migration data is folded into the canonical run store instead of creating a second persistence authority (`src/vectl/orchestration/recovery.py:406-438`; `src/vectl/orchestration/run_store.py:683-697`).

### Boundary drift / gaps

- `[Proven]` Retirement authority is only partially landed: the architecture doc defines four retirement criteria, but the validator enforces only imported-run state/blocker health, not equivalence coverage or documentation authority (`docs/ORCHESTRATION-PLANE-MIGRATION.md:123-130`; `src/vectl/orchestration/recovery.py:544-652`).
- `[Proven]` Operator authority is incomplete: migration-state inspection is surfaced, but migration-state advancement and cutover disposition are not exposed on the landed operator surface (`src/vectl/cli.py:847-865,1064-1329`).
- `[Likely]` This gap would force operators to rely on internal Python surfaces or ad hoc repository inspection, which violates the stated goal of validating retirement against the landed operator surface.

## Same-Plan Coexistence Policy

`[Proven]` Current same-plan coexistence policy is:

1. Native non-terminal runs always block same-plan admission.
2. Imported legacy runs in `parallel`, `preferred`, or `deprecated` block same-plan admission while non-terminal.
3. Imported legacy runs in `retired` do not block same-plan admission.
4. Missing continuity minimums escalate the imported run to `stall` and open a recovery/migration case.
5. Cutover readiness remains blocked while imported runs are non-retired, active, or have open recovery cases.

Evidence:

- `src/vectl/orchestration/run_store.py:369-374,731-766`
- `tests/orchestration/unit/test_run_store.py:184-217,219-243,246-272`
- `src/vectl/orchestration/recovery.py:593-645`

### Trade-off

- Gain: `[Proven]` strong safety against double-running the same plan during coexistence.
- Cost: `[Proven]` no graceful operator-facing path yet exists to progress a run from observed coexistence state to retirement-ready disposition.

## Final Disposition

**Disposition: BLOCKED**

### Blocking items

1. `[Proven]` Full legacy-retirement criteria are not implemented in the landed validator; criteria 1-4 from the migration authority doc are reduced to imported-run/case health checks.
2. `[Proven]` No landed operator command exists to validate cutover readiness or advance migration state, so retirement cannot be completed against the current operator surface alone.
3. `[Proven]` The concrete legacy bridge import path self-creates blocker debt by importing with `continuity_artifacts=None`.
4. `[Proven]` Requested reference `tools/vectl/README.md` is absent, so exact ref-read confirmation cannot be satisfied as written.

## Open Questions

- Should cutover readiness remain an internal Python surface, or must it be surfaced as an operator command before retirement is allowed?
- Is `RunStoreLegacyRunBridge.import_legacy_run()` intended only for tests/scaffolding, or is it meant to support real operator cutover?
- What surface is supposed to prove migration criteria 1-4 beyond imported-run health?

## Certainty

Overall: `[Proven]` for current-state findings, `[Likely]` for operator-impact consequences.
