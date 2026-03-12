## Duplicate-ID rollout verification (CLI + MCP)

Contract reference: `docs/contracts/duplicate-id-rollout-policy.yaml`

Fixtures:
- `tests/repro/fixtures/unique_plan.yaml`
- `tests/repro/fixtures/legacy_duplicate_plan.yaml`

Covered checks:
- Happy path: unique-step plan on `status`, `validate`, `review`
- Legacy duplicate read surfaces warning mode (`status`, `validate`, `review`, `show`, `dag`)
- Write-surface duplicate creation block (`add-step`, MCP `mutate(action=add-step)`)
- Targeted mutate ambiguity block (`claim`, MCP `claim(step_id=...)`)
- No-mutation guarantee on rejected writes (sha256 before/after)

Observed issue:
- None. Regression case for MCP `status` phase-label correctness is covered by `tests/test_mcp.py::TestVectlStatus::test_status_next_steps_shows_correct_phase_for_duplicate_ids`.
