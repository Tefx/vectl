# Legacy Driver Test Layout Reconciliation (B4)

Purpose: reconcile prior claim vocabulary with the enacted repository layout,
without rewriting historical failed-step records.

## Vocabulary mapping

| Previous wording in claims/evidence | Final repository reality |
| --- | --- |
| "rehomed into legacy driver subdirectories" | Rehomed into a **flat** file set under `tests/legacy/driver/*.py` |
| "partitioned legacy buckets under `tests/legacy/driver/<bucket>/...`" | Bucketization is represented by filename prefixes (for example `test_driver_continuity_*`), not nested folders |
| "moved driver tests under `tests/legacy/driver/`" | Correct, with explicit constraint: moved baseline files are direct children of `tests/legacy/driver/` |

## Scope note

- Historical failed-step records remain unchanged.
- Authoritative migration/orchestration docs now explicitly encode the flat
  `tests/legacy/driver/*.py` expectation.
