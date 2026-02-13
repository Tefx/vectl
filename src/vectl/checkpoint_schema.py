"""Checkpoint public contract (Schema v1.2).

Source: FR "vectl checkpoint" and feedback "LLM-Native & Token Economy".

Design Goal:
- Provide a machine-readable, token-efficient snapshot of the plan state.
- Support compaction (pre/post drift check) and agent handoff (unambiguous scope).
- Enable zero-shot reasoning by providing Next Hints (name/intent).
- Reduce noise via Lite Mode (no metadata).

Selection Policy (Deterministic):
1. Focus Selection:
   - If `agent` is provided:
     - Select first claimed step where `claimed_by == agent`.
     - Order by `id` (ascending).
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
   - guidance read_before: max 3
   - evidence_template: truncated to ~900 chars
   - project_guidance (policy_banner): truncated to ~600 chars
   - phase_context: truncated to ~120 chars (aggressive)

Schema v1.2 (JSON):
{
  "schema": "vectl.checkpoint/v1",
  "metadata": {  # NEW in v1.2: Grouped metadata (omitted in lite mode)
    "generated_at": "<iso-timestamp>",
    "tool": { "name": "vectl", "version": "<ver>" },
    "plan": {
      "project": "<name>",
      "etag": "sha256:<file_hash>"
    }
  } | undefined,  # Omitted if lite=True
  "phase": {
    "id": "<id>",
    "name": "<name>",
    "context": "<context>"  # Bounded (120 chars)
  } | null,
  "focus": {
    "step_id": "<id>",
    "name": "<name>",  # NEW in v1.2
    "status": "<status>",
    "claimed_by": "<agent|null>",
    "depends_on": ["<id>", ...]
  } | null,
  "guidance": {
     "read_before": ["<path>", ...],
     "evidence_template": "<text>",
     "policy_banner": "<text>"
  } | undefined,  # Omitted unless include_guidance yields non-empty content
  "next": [
    { "step_id": "<id>", "name": "<name>" }, ...  # UPDATED in v1.2 (hints)
  ],
  "active_steps": [
    { "step_id": "<id>", "name": "<name>", "claimed_by": "<agent>", "status": "<status>" }
  ] | undefined,  # Omitted in lite mode IF active_steps == [focus]
  "active_steps_total": <int>,
  "active_steps_truncated": <bool>
}
"""
