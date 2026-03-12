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
- MCP `status` next-step list reports incorrect phase label for the second duplicate entry (`P2 duplicate` shown as `(p1)`).
