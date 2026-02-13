"""Checkpoint public contract (Schema v1.1).

Source: FR "vectl checkpoint" and feedback "Structured Guidance + Phase Context".

Design Goal:
- Provide a machine-readable, token-efficient snapshot of the plan state.
- Support compaction (pre/post drift check) and agent handoff (unambiguous scope).
- CLI and MCP must return the same schema and apply the same bounding rules.

Selection Policy (Deterministic):
1. Focus Selection:
   - If `agent` is provided:
     - Select first claimed step where `claimed_by == agent`.
     - Order by `id` (ascending) - relying on get_next_steps stable sort or explicit ID sort.
   - Else (or if no match for agent):
     - Select first claimed step (any agent).
     - Order by `id` (ascending).
   - Else (no claimed steps):
     - Select first available step from `get_next_steps()`.
     - Order by `id` (ascending).
   - Else (no available steps):
     - Focus is None.

2. Bounding Defaults:
   - next_steps: max 3
   - active_steps: max 3
   - guidance refs: max 3
   - evidence_template: truncated to ~900 chars (same as claim guidance)
   - project_guidance (policy_banner): truncated to ~600 chars

Schema v1.1 (JSON):
{
  "schema": "vectl.checkpoint/v1",
  "generated_at": "<iso-timestamp>",
  "tool": { "name": "vectl", "version": "<ver>" },
  "plan": {
    "project": "<name>",
    "etag": "sha256:<file_hash>"  # From load_plan() for drift detection
  },
  "phase": {  # NEW in v1.1
    "id": "<id>",
    "name": "<name>",
    "context": "<context>"  # Bounded
  } | null,
  "focus": {
    "step_id": "<id>",
    "status": "<status>",
    "claimed_by": "<agent|null>",
    "depends_on": ["<id>", ...]  # NEW in v1.1
  } | null,
  "guidance": {
    "read_before": ["<path>", ...],  # Renamed from refs in v1.1
    "evidence_template": "<text>",
    "policy_banner": "<text>"  # NEW in v1.1 (project_guidance)
  } | null,  # Only present if focus is present
  "blockers": ["<step.id> depends_on <dep.id>", ...], # Only for focus
  "next": ["<step.id>", ...],
  "active_steps": [
    { "step_id": "<id>", "owner": "<agent>", "status": "<status>" }
  ],
  "active_steps_total": <int>,
  "active_steps_truncated": <bool>
}
"""
