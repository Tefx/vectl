# orch_removal_runtime_phase_probe Evidence

<pre_checklist_scan>
1. Runtime surface drift risk: CLI help may still expose removed built-in `orch` commands even if source imports are gone. Status: checked by installed CLI help probe.
2. Preservation risk: removing built-in orchestration could accidentally remove or unregister `vectl_decide` at the MCP/runtime boundary. Status: checked by import and direct MCP tool invocation.
3. False-green risk: tests may collect zero items or bypass runtime seams. Status: checked by pytest collection count (`300 items`) plus `uvx --from . vectl --help` installed-entrypoint probe.
</pre_checklist_scan>

## Summary

- Gate verdict: PASS
- Runtime surface removal: PASS — `orch` absent from installed `vectl --help`; forbidden orchestration source references absent under `src/vectl`.
- Preservation decision: PASS — `vectl_decide` remains registered/importable and returns a structured dispatch decision.
- Product code/tests/docs modified: none.
- Evidence artifact only: `.agent/evidence/orch_removal_runtime_phase_probe.md`.

## Required Reading

- `tools/vectl/README.md`: NOT READ — file absent in isolated worktree (`test -f tools/vectl/README.md` returned absent).
- `user-request`: runtime verification: built-in orch removed; vectl_decide preserved.
- `src/vectl/cli.py`: read.
- `src/vectl/mcp_server.py`: read.
- `tests/test_decide.py`: read.
- `tests/test_decide_state_isolation.py`: read.
- `tests/test_mcp.py`: read.
- `tests/test_io.py`: read.
- `tests/test_models.py`: read.

## Commands Run

### 1. Required test suite

Command:

```bash
uv run pytest tests/test_models.py tests/test_io.py tests/test_mcp.py tests/test_decide.py tests/test_decide_state_isolation.py -q
```

Exit code: 0

Raw output excerpt:

```text
============================= test session starts ==============================
platform darwin -- Python 3.12.12, pytest-9.1.0, pluggy-1.6.0
rootdir: /Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_runtime_phase_probe
configfile: pyproject.toml
plugins: anyio-4.14.0, cov-7.1.0, hypothesis-6.155.3, returns-0.28.0, base-url-2.1.0, playwright-0.8.0
collected 300 items
...
tests/test_decide_state_isolation.py ...............                     [100%]

============================= 300 passed in 7.01s ==============================
```

### 2. Required installed CLI help probe

Command:

```bash
uvx --refresh --from . vectl --help
```

Exit code: 0

Raw output excerpt:

```text
Usage: vectl [OPTIONS] COMMAND [ARGS]...

Agentic Implementation Plan Manager.

Commands:
  mcp              Start the MCP server (stdio mode).
  render           Render plan as Markdown (read-only export).
  diff             Show plan changes since a git ref (default: last commit).
  log              Show recent plan mutations from git history.
  agents-md        Upsert the vectl section in AGENTS.md or CLAUDE.md.
  init             Create a new plan.yaml template and configure AGENTS.md / CLAUDE.md.
  next             Show claimable steps (what to work on next).
  status           Show plan status overview.
  show             Show details for a step or phase.
  guide            Show agent onboarding guide.
  dag              Output dependency graph as Mermaid flowchart.
  claim            Claim a step for work.
  complete         Mark a step as done with evidence.
  complete-phase   Mark a phase as done with evidence.
  defer            Return a claimed step to pending.
  reject           Reject a completed step, moving it back for rework.
  skip             Skip a step with a reason.
  cancel           Cancel a step (alias for skip --reason irrelevant).
  skip-phase       Skip all remaining steps in a phase.
  check            Toggle/add a checklist item or run deterministic checklist mutation.
  check-inventory  Show deterministic checklist inventory for a step.
  validate         Validate plan structure and consistency.
  migrate          Migrate legacy split-state runtime data into unified plan.yaml.
  migrate-step-id  Migrate duplicate step IDs to globally unique IDs.
  recover          Recover plan from backup in .git/vectl/plan.yaml.bak.
  checkpoint       Output machine-readable plan checkpoint (JSON).
  add-step         Add a new step to a phase.
  add-phase        Add a new phase to the plan.
  edit-plan        Edit plan-level metadata without manually editing plan.yaml.
  edit-step        Edit a step's metadata.
  edit-phase       Edit a phase's metadata.
  remove-step      Remove a pending step from its phase.
  move-step        Move a pending step to a different phase.
  unlock           Unlock a locked phase by validating all dependencies are done.
  recalc-lock      Recalculate LOCKED/PENDING status for all phases.
  add-steps        Batch add steps from stdin (YAML format).
  search           Search across phases and steps for a pattern.
  mine             Show steps currently claimed by an agent (crash recovery).
  review           Multi-layer plan review.
  gate-check       Check if a phase is ready to pass its gate.
  clipboard-write  Write to the plan clipboard.
  clipboard-read   Read the plan clipboard.
  clipboard-clear  Clear the plan clipboard.
  dashboard        Generate a static HTML dashboard for visual project overview.
  repair           Operator recovery commands.
```

### 3. Required Python import probe

Command:

```bash
uv run python - <<'PY'
import importlib
modules = ['vectl.cli', 'vectl.decide', 'vectl.decision_state', 'vectl.model_outputs']
for name in modules:
    mod = importlib.import_module(name)
    origin = getattr(mod, '__file__', None)
    print(f'{name}: OK origin={origin}')
PY
```

Exit code: 0

Raw output:

```text
vectl.cli: OK origin=/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_runtime_phase_probe/src/vectl/cli.py
vectl.decide: OK origin=/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_runtime_phase_probe/src/vectl/decide.py
vectl.decision_state: OK origin=/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_runtime_phase_probe/src/vectl/decision_state.py
vectl.model_outputs: OK origin=/Users/tefx/Projects/vectl/.vectl/worktrees/orch_removal_runtime_phase_probe/src/vectl/model_outputs.py
```

### 4. Required source absence probe

Command:

```bash
python - <<'PY'
from pathlib import Path
needles = [
    'vectl.orchestration',
    'vectl.shell.orchestration',
    'shell.orchestration',
    'from vectl.shell import orchestration',
    'core.orchestration',
    'orch_app',
    'cli_orchestration',
]
root = Path('src/vectl')
matches = []
for path in root.rglob('*.py'):
    text = path.read_text(encoding='utf-8')
    for lineno, line in enumerate(text.splitlines(), 1):
        for needle in needles:
            if needle in line:
                matches.append((str(path), lineno, needle, line.strip()))
if matches:
    print('FORBIDDEN_MATCHES')
    for path, lineno, needle, line in matches:
        print(f'{path}:{lineno}: {needle}: {line}')
    raise SystemExit(1)
print('SOURCE_ABSENCE_PROBE PASS')
print('scanned_root=src/vectl')
print('forbidden_needles=' + ', '.join(needles))
PY
```

Exit code: 0

Raw output:

```text
SOURCE_ABSENCE_PROBE PASS
scanned_root=src/vectl
forbidden_needles=vectl.orchestration, vectl.shell.orchestration, shell.orchestration, from vectl.shell import orchestration, core.orchestration, orch_app, cli_orchestration
```

### 5. Required CLI `orch` absence + `vectl_decide` support probe

Command:

```bash
set -o pipefail
HELP=$(uvx --refresh --from . vectl --help 2>&1)
printf '%s\n' "$HELP" | grep -i 'orch' && { echo 'CLI_HELP_ORCH_PRESENT'; exit 1; } || echo 'CLI_HELP_ORCH_ABSENCE PASS'
uv run python - <<'PY'
import os, tempfile, yaml
from pathlib import Path
from vectl.mcp_server import vectl_decide as _vectl_decide_tool
vectl_decide = _vectl_decide_tool.fn
with tempfile.TemporaryDirectory() as d:
    plan_path = Path(d) / 'plan.yaml'
    plan_path.write_text(yaml.dump({'project':'probe','phases':[{'id':'p1','name':'P1','status':'pending','steps':[{'id':'p1.s1','name':'S1','status':'pending'}]}]}), encoding='utf-8')
    old = os.environ.get('VECTL_PLAN_PATH')
    os.environ['VECTL_PLAN_PATH'] = str(plan_path)
    try:
        result = vectl_decide(running_tasks=[], advisor_state={'completion_times': {}, 'session_registry': {}, 'failure_counts': {}})
    finally:
        if old is None:
            os.environ.pop('VECTL_PLAN_PATH', None)
        else:
            os.environ['VECTL_PLAN_PATH'] = old
print('VECTL_DECIDE_SUPPORT PASS')
print('status=' + str(result.get('status')))
print('reason_code=' + str(result.get('reason_code')))
print('actions=' + str([(a.get('action'), a.get('step_id')) for a in result.get('actions', [])]))
print('has_next_state=' + str(isinstance(result.get('next_state'), dict)))
PY
```

Exit code: 0

Raw output:

```text
CLI_HELP_ORCH_ABSENCE PASS
VECTL_DECIDE_SUPPORT PASS
status=dispatch
reason_code=dispatch_available
actions=[('claim_and_dispatch', 'p1.s1')]
has_next_state=True
```

## Behavioral Proof Register

| requirement_ref | behavior_claim | runtime_proof_expected | evidence_ref | status | closure_path | gate_decision_basis |
|---|---|---|---|---|---|---|
| user-request.runtime-surface-removal | Built-in `orch` runtime surface is removed from installed CLI help. | Installed entrypoint help from `uvx --refresh --from . vectl --help` has no `orch` command/string. | Commands 2 and 5 | PROVEN | Complete gate; no preservation needed for removed CLI surface. | Installed CLI entrypoint did not expose `orch`; grep probe passed. |
| user-request.source-absence | Removed orchestration source seams are absent under `src/vectl`. | Source scan finds no forbidden references: `vectl.orchestration`, `vectl.shell.orchestration`, `shell.orchestration`, `from vectl.shell import orchestration`, `core.orchestration`, `orch_app`, `cli_orchestration`. | Command 4 | PROVEN | Complete gate. | No forbidden source matches. |
| user-request.decide-preserved | `vectl_decide` remains supported after orch removal. | MCP server import exposes callable `vectl_decide`; invocation returns structured decision with status/reason/actions/next_state. | Commands 3 and 5 | PROVEN | Preserve `vectl_decide`. | Direct runtime invocation returned `dispatch`, `dispatch_available`, and `claim_and_dispatch` action. |
| user-request.regression-suite | Decide/MCP/IO/model behavior remains green. | Required pytest command collects nonzero tests and passes. | Command 1 | PROVEN | Complete gate. | 300 tests collected, 300 passed; no empty-room failure. |

## Protocol Results

| Protocol | Result | Evidence | Gap |
|---|---|---|---|
| P1 Empty Room | PASS | `collected 300 items`; `300 passed` | None |
| P8 Caller Reachability | PASS | `vectl_decide` registered/imported from `vectl.mcp_server` and invoked | None |
| P9 Smoke/Liveness | PASS | Installed CLI help via `uvx --refresh --from . vectl --help`; MCP decision function runtime probe | None |

## Gate Decision

PASS. Runtime removal of built-in `orch` is verified, and `vectl_decide` preservation is verified. No product-code/test/doc changes were made.
