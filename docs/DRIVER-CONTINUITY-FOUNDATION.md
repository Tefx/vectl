# Driver Continuity Foundation

Status: corrective architecture basis + contract repair for `driver-continuity-foundation`.

This document exists because the prior continuity-foundation evidence cited
artifacts that were not present in the repository. This file is the canonical,
repo-present architecture basis and review surface for downstream continuity
phases.

## 1. Scope and posture

This foundation covers only the bootstrap-blocker continuity minimum needed for:

- session authority convergence
- durable resume state
- replay safety
- runner capability semantics for restart/resume decisions
- minimum recovery telemetry

This foundation explicitly does **not** own enhancement-only ambitions such as:

- rich analytics dashboards
- operator-facing recovery UX improvements
- long-horizon telemetry aggregation
- post-bootstrap observability beyond minimum recovery telemetry

Those remain downstream enhancement work and do not intersect the continuity
foundation gate.

## 2. System layers

1. **Driver control loop** (`src/vectl/driver/loop.py`)
   - owns runtime dispatch/reconcile sequencing
   - consumes continuity handoff decisions but does not become durable storage
2. **Session authority layer** (`src/vectl/driver/session.py`, `src/vectl/decide.py`)
   - `DecideState` owns dispatch intent
   - `SessionPool` owns runner-aware reusable session cache
3. **Runner continuity capability layer** (`src/vectl/driver/runner_continuity.py`)
   - names what each runner can safely do for resume/persist/replay
4. **Durable continuity contract layer** (`src/vectl/driver/types.py`)
   - defines ledger, journal, replay envelope, and recovery-controller IO shapes
5. **Startup recovery controller** (contract only in `src/vectl/driver/types.py`)
   - consumes durable continuity facts and produces resumable handoffs / repair actions

## 3. Source-of-truth matrix

| Continuity fact | Authority | Why |
|---|---|---|
| Dispatch wants reuse vs fresh | `DecideState` via `vectl.decide` | Dispatch intent must stay with decide semantics, not SessionPool heuristics |
| Runner-compatible reusable session candidate | `SessionPool` | Driver-only optimization keyed by runner match + TTL |
| Durable restart/resume fact | `ContinuityLedgerEntry` contract | Must survive process death; process memory is insufficient |
| Replay/idempotency identity | `ReplaySafetyEnvelope` contract | Prevents restart logic from inferring safety from free-form text |
| Minimum recovery telemetry | `ContinuityJournalEntry` contract | Narrow bootstrap journal for restart decisions only |
| Runner continuity semantics | `RUNNER_CONTINUITY_CAPABILITIES` | Explicit capability contract per runner |
| Startup repair planning input/output | `StartupRecoveryControllerInput/Output` | Prevents startup recovery from ad hoc dict-based branching |

## 3a. Service catalog

| Service / module | Role | Depends on |
|---|---|---|
| `src/vectl/driver/session.py` | Ephemeral runner-aware session cache + authority map | `SessionEntry`, `SessionConfig` |
| `src/vectl/driver/runner_continuity.py` | Canonical runner continuity capability declarations | none beyond stdlib |
| `src/vectl/driver/types.py` | Shared continuity contracts for ledger/journal/replay/recovery | existing driver/shared types |
| `src/vectl/driver/loop.py` | Runtime consumer of continuity decisions | session cache, runners, judge, continuity contracts |
| startup recovery controller (deferred implementation) | Builds restart/resume/repair decisions from continuity contracts | ledger/journal/capability snapshots |

## 4. Runtime contract

### Restart

- Restart decisions must consume durable ledger + journal facts, not just in-memory `SessionPool` state.
- A runner session is resumable only if:
  - runner capability says resume is supported
  - capability snapshot still matches the resumed runner surface
  - replay envelope still scopes the attempted action safely

### Resume

- Resume is an optimization, not the source of truth.
- `SessionPool` may suggest a session candidate, but durable continuity records decide whether restart-time reuse is safe.
- If capability confidence is missing or mismatched, the controller must require a fresh session.

### Abort / failure

- Abort/failure must produce recovery-grade journal facts sufficient to classify the interrupted attempt on restart.
- Judge outcomes inform policy handoff but do not become the continuity authority themselves.

### Replay

- Replay safety is envelope-gated.
- No downstream continuity phase may infer replay safety solely from runner support for resume.
- Partial output is treated conservatively unless the recorded envelope and capability contract say retry/replay is safe.

## 5. State strata

1. **Intent state**: decide-side reuse/fresh intent in `DecideState`
2. **Resolved continuity contract**: ledger/journal/handoff/capability contracts in `types.py` and `runner_continuity.py`
3. **Mutable runtime process state**: `DriverState`, live handles, PID liveness
4. **Persisted continuity state**: future durable `ContinuityLedgerEntry` / `ContinuityJournalEntry` instances
5. **Transport/process state**: runner subprocesses and CLI resume flags

The key boundary is that process state and session cache are not durable continuity authority.

## 6. Transport boundary rules

- Runner CLI affordances (`--resume`, persistent sessions, etc.) are capability inputs, not policy outputs.
- A runner's ability to accept a resume token does not by itself authorize restart-time resume.
- Restart after crash must use durable continuity contracts plus capability snapshot, not stale process memory.
- Capability mismatch forces a fresh session rather than best-guess replay.

## 7. Cross-cutting governance

- Judge remains owner of semantic failure classification and escalation verdicts.
- Continuity owns how those outcomes are journaled and consumed for restart/recovery.
- Minimum recovery telemetry is intentionally smaller than enhancement observability.
- Journal write points must eventually cover success, failure, abort, and crash-adjacent restart edges.

## 8. Shared abstractions

- `ReplaySafetyEnvelope`
- `ContinuityJournalEntry`
- `ContinuityLedgerEntry`
- `ContinuityHandoff`
- `RunnerContinuityCapability`
- `StartupRecoveryControllerInput`
- `StartupRecoveryControllerOutput`

These exist to stop downstream phases from inventing incompatible local shapes.

## 9. Module split recommendations

- Keep `DecideState` as decide-owned dispatch intent memory.
- Keep `SessionPool` as driver-owned runner-aware cache.
- Put durable continuity record shapes in `types.py`.
- Put runner restart/resume semantics in `runner_continuity.py`.
- Keep runtime orchestration in `loop.py`; it consumes continuity contracts rather than owning them.

This removes double truth by separating *dispatch intent*, *ephemeral cache*, and *durable continuity authority*.

## 10. Downstream unlock criteria

### Safe for `driver-continuity-authority-ledger`

That phase can now implement durable persistence because the durable record,
journal minimum, replay envelope, and startup recovery IO shapes are pinned.

### Safe for `driver-continuity-capability-safety`

That phase can now wire restart/resume decision logic because per-runner
continuity capability semantics and fresh-session fallback rules are pinned.

## 11. Deferrals and non-intersection rationale

Deferred intentionally:

- persistence implementation details
- actual journal writes in runtime paths
- recovery controller execution logic
- richer observability and analytics

These deferrals do not intersect the foundation gate because this document and
the contract artifacts already pin the shapes, ownership, and decision inputs
needed for downstream decomposition.

## 12. Corrective note on fabricated claims

The truthful continuity-foundation artifacts now present in the repo are:

- `docs/DRIVER-CONTINUITY-FOUNDATION.md`
- `src/vectl/driver/session.py` (`SESSION_CONTINUITY_AUTHORITIES`)
- `src/vectl/driver/types.py` (`ReplaySafetyEnvelope`, `ContinuityJournalEntry`, `ContinuityLedgerEntry`, `ContinuityHandoff`, startup recovery controller IO/protocol)
- `src/vectl/driver/runner_continuity.py`
- `tests/test_driver_continuity_contract.py`

No other continuity artifact or test should be cited unless it exists exactly by path.

## 13. Open questions

- Which durable storage file or store will hold `ContinuityLedgerEntry` instances? Deferred to `driver-continuity-authority-ledger` because the shape is now pinned and storage choice does not change downstream semantics.
- Where should capability snapshot IDs be persisted: alongside ledger rows or derived at startup from config? Deferred to `driver-continuity-capability-safety` because both options consume the same pinned contract.

## 14. Readiness

READY FOR DOWNSTREAM DECOMPOSITION.

Reason:

- continuity authority boundaries are explicit
- durable resume and replay-safe contracts are repo-present
- runner capability semantics are explicit per runner
- minimum recovery telemetry is narrowed to bootstrap scope
- deferred items are implementation choices, not missing architectural ownership
