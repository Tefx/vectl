# Orchestration Plane Migration and Legacy Disposition

> Migration/reference document for moving from the current legacy package
> `src/vectl/driver/` toward the target orchestration plane.

**Status:** Migration design reference  
**Target architecture:** `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`  
**Interface authority:** `docs/ORCHESTRATION-PLANE-INTERFACES.md`  
**Related docs:** `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`, `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`, `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`

---

## 1. Purpose

This document answers three practical questions:

1. What parts of the current legacy package are likely to map into `control`,
   `roster`, `runtime`, or `resolver`?
2. What should happen to current code and tests during the transition?
3. What documentation should be treated as target authority vs legacy reference?

This is a migration/reference document. It does **not** redefine the target
architecture.

---

## 2. Documentation Set

### 2.1 Target architecture documents

These are the current authoritative target docs:

- `docs/ADR-orchestration-plane-reset.md`
- `docs/ORCHESTRATION-PLANE-ARCHITECTURE.md`
- `docs/ORCHESTRATION-PLANE-INTERFACES.md`
- `docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md`
- `docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md`
- `docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md`
- `docs/ORCHESTRATION-PLANE-MIGRATION.md`

### 2.2 Legacy implementation reference documents

These describe the existing baseline implementation and remain useful as
reference material:

- `docs/DRIVER-ARCHITECTURE.md`
- `docs/DRIVER-CONTINUITY-FOUNDATION.md`
- `docs/ADR-driver-evolution-foundation.md`

### 2.3 Pre-reset historical references

These remain useful only as historical or implementation context:

- `docs/RFC-decide.md`

---

## 3. Migration Principles

### Principle A — Delete concepts before deleting behavior

Superseded future-design prose should be removed early.

Working implementation behavior should not be removed until an equivalent target
component exists and is covered by tests.

### Principle B — Keep migration leverage

The current legacy package is still the best executable reference for many
mechanical/runtime behaviors. Do not destroy that leverage prematurely.

### Principle C — Migrate by responsibility, not by file name

The target architecture is not a 1:1 rename of `src/vectl/driver/` modules.
Some legacy modules will likely be split across target components.

### Principle D — Preserve observable behavior before retiring baseline code

If legacy code is removed, the new orchestration-plane component must preserve
the relevant behavior and test coverage first.

---

## 4. Likely Mapping from Legacy Package to Target Components

This table is intentionally directional, not final.

| Legacy module / concern | Likely target home | Notes |
|---|---|---|
| `session.py` | `roster` | strongest direct mapping |
| `worktree.py` | `runtime` | mechanical workspace chores |
| `runners.py` | `runtime` | runner/process mechanics |
| `parsers.py` | `runtime` | execution result parsing likely stays near runtime |
| `observe.py` / events | `orchestration/events.py` | shared support owned by the orchestration plane, not by one component |
| `dispatch.py` | split / undecided | may start in `runtime`, but final home depends on execution-request design |
| `loop.py` | split heavily | plan-aware flow belongs to `control`; mechanical orchestration support belongs elsewhere |
| `judge.py` | `resolver.py` support | any surviving LLM invocation glue belongs under blocked-case reasoning |
| `judgments.py` | `judgments.py` shared support | typed/local judgment schemas/helpers remain reusable support, not a top-level component |
| `config.py` | shared orchestration-plane config support | likely not a single component-owned concept |
| `types.py` / `errors.py` | split by owner | should follow final component ownership |
| `entrypoint.py` / `__main__.py` | future orchestration-plane entry surfaces | legacy shell only for now |

---

## 5. Code Disposition Rule

### Keep now

Keep the current legacy package code under `src/vectl/driver/` while:

- it remains the current working baseline,
- target component equivalents do not yet exist,
- and legacy tests still provide unique behavior coverage.

### Do not do now

Do **not**:

- broadly delete `src/vectl/driver/`
- mass-rename legacy modules into target names without responsibility changes
- remove tests just because the target architecture has renamed concepts

### Retire later only when all are true

A legacy code path may be retired only when:

1. an equivalent target component implementation exists,
2. behavior is covered by tests in the new location,
3. docs no longer rely on the legacy path as the primary executable reference,
4. the migration does not erase currently known-good behavior.

---

## 6. Test Disposition Rule

### Keep now

Keep tests that validate:

- mechanical runtime behavior
- runner integration
- worktree lifecycle
- continuity/recovery baseline behavior in the legacy system
- liveness/smoke behavior of the current implementation

### Rehome later

When target components land, tests should be:

- **moved** if they validate the same behavior under the new owner,
- **split** if one legacy test covered multiple future owners,
- **deleted** only if they exist solely to validate a retired legacy concept.

Recommended future test layout:

```text
tests/orchestration/unit/
tests/orchestration/integration/
tests/legacy/driver/*.py   # flat legacy-driver baseline files (no nested subdirs)
```

For migration-finish continuity, treat `tests/legacy/driver/` as a flat file
set. Do not assume category subdirectories under this path.

### Important

Do not delete a legacy test merely because it references `driver` terminology.
First decide whether the test is asserting behavior, or only asserting an old
concept name.

---

## 7. Immediate Cleanup vs Deferred Cleanup

### Immediate cleanup

- remove superseded future-design ADRs/docs
- mark legacy docs as legacy reference
- mark pre-reset RFCs as historical/pre-reset reference

### Deferred cleanup

- deleting legacy code modules
- deleting legacy behavior tests
- removing legacy entrypoints

These belong only after equivalent orchestration-plane behavior exists.

---

## 8. Migration Success Criteria

Migration is succeeding when:

- target docs are the clear authority for future design,
- legacy docs are clearly positioned as implementation reference,
- target component boundaries stay stable,
- code moves by responsibility rather than by naming fashion,
- and behavior/test evidence is preserved throughout.

---

## 9. Summary

The current package under `src/vectl/driver/` should be treated as a legacy but
valuable baseline.

The right immediate action is:

- clean docs,
- fix target vocabulary,
- map responsibilities,
- preserve code and tests as migration leverage.

The wrong immediate action is:

- deleting working behavior before target equivalents exist.
