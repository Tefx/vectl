# Invar Guard Remediation UI/UX Proof Artifact

Standalone durable artifact for step `invar_guard_remediation.persist-standalone-uiux-proof-artifact`.

## Provenance and Scope

- Artifact path: `docs/invar_guard_remediation/uiux-proof-artifact.md`.
- Current-mainline environment: isolated worktree `.vectl/worktrees/invar_guard_remediation.persist-standalone-uiux-proof-artifact` on branch `vectl/step-invar_guard_remediation.persist-standalone-uiux-proof-artifact`.
- CLI command provenance: `pyproject.toml` declares `[project.scripts] vectl = "vectl.cli:app"`; evidence below uses `.venv/bin/python` with Typer `CliRunner` against `from vectl.cli import app`, per dispatch guardrail, and labels the represented user-facing command.
- Managed-plan-summary reliance: none. This artifact is a repository documentation file outside `plan.yaml`, `.git/vectl/`, and managed plan summaries.
- Repository dogma note: no `CONSTITUTION.md` exists in this worktree. `INVAR.md` was read; relevant passage: “Run `invar guard` after changes. Fix errors before committing.”
- Required README note: `tools/vectl/README.md` was NOT READ because the `tools/` directory is absent in this current worktree.

## Evidence Index

- E1: Help/surface snapshot for represented `vectl --help`.
- E2: Help/surface snapshot for represented `vectl recover --help`.
- E3: Help/surface snapshot for represented `vectl orch --help`.
- E4: Help/surface snapshot for represented `vectl orch recover --help`.
- E5: Help/surface snapshot for represented `vectl orch drive-recover --help`.
- E6: JSON parseability and zero-ANSI proof for represented `vectl orch config validate --json`.
- E7: Facade import proof for `from vectl.core import RecoverResult`.
- E8: Fresh guard context for `uvx invar-tools guard --all`.

## Command Executability Matrix

| Evidence | Actual command executed | Represented user-facing command | Expected result class | Exit status | PASS/FAIL | Evidence reference |
|---|---|---:|---|---:|---|---|
| E1 | `.venv/bin/python` Typer `CliRunner`, args `['--help']` | `vectl --help` | Help renders successfully | 0 | PASS | [E1](#e1--represented-vectl---help) |
| E2 | `.venv/bin/python` Typer `CliRunner`, args `['recover', '--help']` | `vectl recover --help` | Help renders successfully | 0 | PASS | [E2](#e2--represented-vectl-recover---help) |
| E3 | `.venv/bin/python` Typer `CliRunner`, args `['orch', '--help']` | `vectl orch --help` | Help renders successfully | 0 | PASS | [E3](#e3--represented-vectl-orch---help) |
| E4 | `.venv/bin/python` Typer `CliRunner`, args `['orch', 'recover', '--help']` | `vectl orch recover --help` | Help renders successfully | 0 | PASS | [E4](#e4--represented-vectl-orch-recover---help) |
| E5 | `.venv/bin/python` Typer `CliRunner`, args `['orch', 'drive-recover', '--help']` | `vectl orch drive-recover --help` | Help renders successfully | 0 | PASS | [E5](#e5--represented-vectl-orch-drive-recover---help) |
| E6 | `.venv/bin/python` Typer `CliRunner`, args `['orch', 'config', 'validate', '--json']` | `vectl orch config validate --json` | JSON output parses and contains zero ANSI escapes | 0 | PASS | [E6](#e6--json-parseability-and-zero-ansi-proof) |
| E7 | `.venv/bin/python - <<'PY' ... from vectl.core import RecoverResult ... PY` | Python facade import | Import succeeds in current environment | 0 | PASS | [E7](#e7--facade-import-proof) |
| E8 | `uvx invar-tools guard --all` | `uvx invar-tools guard --all` | Guard context available with zero errors | 0 | PASS_WITH_WARNINGS | [E8](#e8--fresh-guard-context) |

## Help / Surface Snapshots

### E1 — represented `vectl --help`

```text
===== vectl --help exit=0 =====
                                                                                 
 Usage: vectl [OPTIONS] COMMAND [ARGS]...                                       
                                                                                 
 Agentic Implementation Plan Manager.                                           
                                                                                 
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --version             -V        Show version and exit.                       │
│ --install-completion            Install completion for the current shell.    │
│ --show-completion               Show completion for the current shell, to    │
│                                 copy it or customize the installation.       │
│ --help                          Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ mcp              Start the MCP server (stdio mode).                          │
│ render           Render plan as Markdown (read-only export).                 │
│ diff             Show plan changes since a git ref (default: last commit).   │
│ log              Show recent plan mutations from git history.                │
│ agents-md        Upsert the vectl section in AGENTS.md or CLAUDE.md.         │
│ init             Create a new plan.yaml template and configure AGENTS.md /   │
│                  CLAUDE.md.                                                  │
│ next             Show claimable steps (what to work on next).                │
│ status           Show plan status overview.                                  │
│ show             Show details for a step or phase.                           │
│ guide            Show agent onboarding guide.                                │
│ dag              Output dependency graph as Mermaid flowchart.               │
│ claim            Claim a step for work.                                      │
│ complete         Mark a step as done with evidence.                          │
│ complete-phase   Mark a phase as done with evidence.                         │
│ defer            Return a claimed step to pending.                           │
│ reject           Reject a completed step, moving it back for rework.         │
│ skip             Skip a step with a reason.                                  │
│ cancel           Cancel a step (alias for skip --reason irrelevant).         │
│ skip-phase       Skip all remaining steps in a phase.                        │
│ check            Toggle or add a checklist item in a step's description.     │
│ validate         Validate plan structure and consistency.                    │
│ migrate          Migrate legacy split-state runtime data into unified        │
│                  plan.yaml.                                                  │
│ migrate-step-id  Migrate duplicate step IDs to globally unique IDs.          │
│ recover          Recover plan from backup in .git/vectl/plan.yaml.bak.       │
│ checkpoint       Output machine-readable plan checkpoint (JSON).             │
│ add-step         Add a new step to a phase.                                  │
│ add-phase        Add a new phase to the plan.                                │
│ edit-plan        Edit plan-level metadata without manually editing           │
│                  plan.yaml.                                                  │
│ edit-step        Edit a step's metadata.                                     │
│ edit-phase       Edit a phase's metadata.                                    │
│ remove-step      Remove a pending step from its phase.                       │
│ move-step        Move a pending step to a different phase.                   │
│ unlock           Unlock a locked phase by validating all dependencies are    │
│                  done.                                                       │
│ recalc-lock      Recalculate LOCKED/PENDING status for all phases.           │
│ add-steps        Batch add steps from stdin (YAML format).                   │
│ search           Search across phases and steps for a pattern.               │
│ mine             Show steps currently claimed by an agent (crash recovery).  │
│ review           Multi-layer plan review.                                    │
│ gate-check       Check if a phase is ready to pass its gate.                 │
│ clipboard-write  Write to the plan clipboard.                                │
│ clipboard-read   Read the plan clipboard.                                    │
│ clipboard-clear  Clear the plan clipboard.                                   │
│ dashboard        Generate a static HTML dashboard for visual project         │
│                  overview.                                                   │
│ repair           Operator recovery commands.                                 │
│ orch             Orchestration operator commands: run, resume, recover,      │
│                  inspect, case, control, config, migration.                  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### E2 — represented `vectl recover --help`

```text
===== vectl recover --help exit=0 =====
                                                                                 
 Usage: vectl recover [OPTIONS]                                                 
                                                                                 
 Recover plan from backup in .git/vectl/plan.yaml.bak.                          
                                                                                 
 Restores the plan to a previous state from the backup file.                    
 Shows a diff of what will change before applying.                              
                                                                                 
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --plan  -p      PATH  Path to plan YAML file. Defaults to auto-discovery     │
│                       (walk-up). (env: VECTL_PLAN_PATH)                      │
│ --yes   -y            Skip confirmation prompt.                              │
│ --help                Show this message and exit.                            │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### E3 — represented `vectl orch --help`

```text
===== vectl orch --help exit=0 =====
                                                                                 
 Usage: vectl orch [OPTIONS] COMMAND [ARGS]...                                  
                                                                                 
 Orchestration operator commands: run, resume, recover, inspect, case, control, 
 config, migration.                                                             
                                                                                 
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ run                      Start or resume an orchestration run.               │
│ resume                   Resume an existing orchestration run from           │
│                          artifacts.                                          │
│ recover                  Recover orchestration state from continuity         │
│                          artifacts.                                          │
│ runs                     List orchestration runs.                            │
│ prune                    Prune old runs and artifacts.                       │
│ cutover-validate         Validate cutover readiness against migration        │
│                          retirement criteria.                                │
│ migration-advance-state  Advance imported legacy migration state via         │
│                          canonical bridge wiring.                            │
│ status                   Inspect current orchestration status.               │
│ events                   Inspect orchestration events.                       │
│ logs                     Inspect run logs.                                   │
│ artifacts                Inspect derived artifacts from runs.                │
│ actions                  Inspect actions taken during a run.                 │
│ case-list                List cases (blocked/unresolved situations).         │
│ case-show                Show detail for a specific case.                    │
│ case-respond             Respond to a case (operator input to resolver).     │
│ pause                    Pause orchestration (stop dispatching new work).    │
│ unpause                  Unpause orchestration (resume dispatching).         │
│ stop                     Stop orchestration entirely.                        │
│ config-show              Show current orchestration configuration.           │
│ config-validate          Validate current orchestration configuration.       │
│ config-tools             Show registered tool families and allowlist.        │
│ drive                    Start or resolve a full-plan orchestration drive.   │
│ drive-status             Show current drive status.                          │
│ drive-runs               List child runs belonging to a drive.               │
│ drive-resume             Resume an interrupted drive session.                │
│ drive-recover            Recover a drive from interrupted state.             │
│ inspect                  Inspect orchestration runtime surfaces.             │
│ case                     Case inspection and operator response surfaces.     │
│ control                  Operator control actions (pause/unpause/stop).      │
│ config                   Orchestration config surfaces.                      │
│ migration                Legacy migration and cutover surfaces.              │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### E4 — represented `vectl orch recover --help`

```text
===== vectl orch recover --help exit=0 =====
                                                                                 
 Usage: vectl orch recover [OPTIONS] [RUN_ID]                                   
                                                                                 
 Recover orchestration state from continuity artifacts.                         
                                                                                 
 Contract authority: orch_app.py::OrchestrationApp.recover()                    
 Selector safety authority:                                                     
 docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §7.1, §6           
   §7.1 — recover accepts [RUN_ID|--latest]                                     
   §6   — exit 2 = not found                                                    
                                                                                 
╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   run_id      [RUN_ID]  Run identifier to recover.                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --latest                               Select latest run automatically.      │
│ --step             TEXT                Step ID to recover.                   │
│ --dry-run                              Run in dry-run mode.                  │
│ --json                                 Emit machine-readable JSON output.    │
│ --output           [human|json|jsonl]  Output mode: human|json|jsonl         │
│                                        [default: human]                      │
│ --yes      -y                          Skip confirmation prompt.             │
│ --plan     -p      PATH                Path to plan YAML file. Defaults to   │
│                                        auto-discovery (walk-up). (env:       │
│                                        VECTL_PLAN_PATH)                      │
│ --help                                 Show this message and exit.           │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### E5 — represented `vectl orch drive-recover --help`

```text
===== vectl orch drive-recover --help exit=0 =====
                                                                                 
 Usage: vectl orch drive-recover [OPTIONS] [DRIVE_ID]                           
                                                                                 
 Recover a drive from interrupted state.                                        
                                                                                 
 Authority: docs/RFC-orch-drive.md section 7.2                                  
                                                                                 
 Contract authority: orch_app.py::OrchestrationApp.recover_drive()              
                                                                                 
╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   drive_id      [DRIVE_ID]  Drive identifier (omit to use --latest).         │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --latest                               Select latest run automatically.      │
│ --dry-run                              Compute result without applying       │
│                                        changes.                              │
│ --yes      -y                          Skip confirmation prompt.             │
│ --json                                 Emit machine-readable JSON output.    │
│ --output           [human|json|jsonl]  Output mode: human|json|jsonl         │
│                                        [default: human]                      │
│ --plan     -p      PATH                Path to plan YAML file. Defaults to   │
│                                        auto-discovery (walk-up). (env:       │
│                                        VECTL_PLAN_PATH)                      │
│ --help                                 Show this message and exit.           │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## E6 — JSON Parseability and Zero ANSI Proof

Exact proof command executed:

```bash
.venv/bin/python - <<'PY'
import json, re
from typer.testing import CliRunner
from vectl.cli import app
runner = CliRunner()
r = runner.invoke(app, ['orch', 'config', 'validate', '--json'])
ansi = bool(re.search(r'\x1b\[[0-?]*[ -/]*[@-~]', r.output))
parsed = json.loads(r.output)
print('represented_command: vectl orch config validate --json')
print('runner_args:', ['orch', 'config', 'validate', '--json'])
print('exit_status:', r.exit_code)
print('raw_output:', r.output.strip())
print('json_parse_success:', isinstance(parsed, dict))
print('parsed_keys:', sorted(parsed.keys()))
print('contains_ansi_escape_sequences:', ansi)
print('validation_passed:', parsed.get('validation_passed'))
PY
```

Pasted output:

```text
===== JSON ANSI PROOF =====
represented_command: vectl orch config validate --json
runner_args: ['orch', 'config', 'validate', '--json']
exit_status: 0
raw_output: {"role_profile_provenance": null, "show_output": "configuration is valid", "tools": ["core", "orchestration", "drive"], "validation_passed": true}
json_parse_success: True
parsed_keys: ['role_profile_provenance', 'show_output', 'tools', 'validation_passed']
contains_ansi_escape_sequences: False
validation_passed: True
```

Decision: PASS. The command exits 0, `json.loads` succeeds, the parsed object contains the expected validation payload keys, and the ANSI regex reports `False`.

## E7 — Facade Import Proof

Exact command executed:

```bash
.venv/bin/python - <<'PY'
from vectl.core import RecoverResult
print('import_success=True')
print(f'imported_name={RecoverResult.__name__}')
print(f'imported_module={RecoverResult.__module__}')
print(f'qualname={RecoverResult.__qualname__}')
PY
```

Pasted output:

```text
import_success=True
imported_name=RecoverResult
imported_module=vectl.core_plan_mutations
qualname=RecoverResult
```

Decision: PASS. `from vectl.core import RecoverResult` succeeds in the same worktree-local `.venv` used for CLI proof generation.

## E8 — Fresh Guard Context

Exact command executed:

```bash
uvx invar-tools guard --all
```

Exit status: `0`.

Pasted summary fields from output:

```json
{
  "status": "passed",
  "summary": {
    "files_checked": 143,
    "errors": 0,
    "warnings": 54,
    "infos": 4
  },
  "escape_hatches": {
    "count": 6,
    "gating": {
      "status": "warning",
      "budget": {
        "used": 14,
        "limit": 15
      }
    }
  },
  "verification_level": "STANDARD",
  "doctest": {
    "passed": true,
    "output": ""
  },
  "crosshair": {
    "status": "skipped",
    "reason": "no core files found"
  },
  "property_tests": {
    "status": "skipped",
    "reason": "no core files"
  }
}
```

Guard context decision: PASS_WITH_WARNINGS. The guard status is `passed`, `summary.errors` is `0`, warning/info counts are explicitly captured (`54` warnings, `4` infos), and escape budget is `14/15` with gating status `warning`.

## Behavioral Proof Register

| requirement_ref | behavior_claim | runtime_proof_expected | evidence_ref | status | closure_path | gate_decision_basis |
|---|---|---|---|---|---|---|
| required artifact durability | A standalone repository artifact exists under `docs/invar_guard_remediation/` and is not a managed plan summary. | File is committed at `docs/invar_guard_remediation/uiux-proof-artifact.md`. | This file, sections “Provenance and Scope” and “Evidence Index”. | PASS | Created durable markdown artifact in repository docs. | Auditor can inspect this committed file without plan summaries. |
| required CLI surface: root | Root CLI help exposes top-level commands and exits successfully. | In-process Typer `CliRunner` for represented `vectl --help` exits 0 and prints help. | E1 | PASS | Pasted full help snapshot. | Exit 0 and visible command list. |
| required CLI surface: plan recover | Plan recovery help exposes options and exits successfully. | In-process Typer `CliRunner` for represented `vectl recover --help` exits 0 and prints help. | E2 | PASS | Pasted full help snapshot. | Exit 0 and visible `--plan`, `--yes`, `--help`. |
| required CLI surface: orch | Orchestration help exposes operator command family and exits successfully. | In-process Typer `CliRunner` for represented `vectl orch --help` exits 0 and prints help. | E3 | PASS | Pasted full help snapshot. | Exit 0 and visible orchestration commands. |
| required CLI surface: orch recover | Orchestration recovery help exposes selectors, JSON output, dry-run, and plan option. | In-process Typer `CliRunner` for represented `vectl orch recover --help` exits 0 and prints help. | E4 | PASS | Pasted full help snapshot. | Exit 0 and visible recovery options. |
| required CLI surface: drive recover | Drive recovery help exposes current recovery surface. | In-process Typer `CliRunner` for represented `vectl orch drive-recover --help` exits 0 and prints help. | E5 | PASS | Pasted full help snapshot; no equivalent substitution needed because current command exists. | Exit 0 and visible drive recovery options. |
| command executability matrix | Artifact records actual command, expected result class, exit status, PASS/FAIL, and evidence reference for each required proof command. | Matrix rows contain all required columns. | Command Executability Matrix | PASS | Matrix included near top of artifact. | Auditor can map each claim to pasted evidence. |
| JSON ANSI proof | Machine-readable JSON command output parses and contains zero ANSI escape sequences. | `json.loads` succeeds and ANSI regex reports false. | E6 | PASS | Pasted exact command and parse/check output. | `json_parse_success: True`, `contains_ansi_escape_sequences: False`, exit 0. |
| facade import proof | `from vectl.core import RecoverResult` succeeds in current-mainline environment. | Worktree-local `.venv/bin/python` import prints class identity. | E7 | PASS | Pasted exact command and import output. | `import_success=True` and class module/qualname captured. |
| fresh guard context | Current `uvx invar-tools guard --all` context is captured with status, errors, warnings/infos, and escape budget. | Guard command exits 0 and emits summary. | E8 | PASS_WITH_WARNINGS | Pasted summary fields and exit status. | `status: passed`, `errors: 0`, warnings/infos counts and `14/15` escape budget captured. |
| uiux-auditor decision support | Artifact alone enables PASS/PASS_WITH_DEBT/FAIL decision. | Validation note defines decision criteria below. | UI/UX Auditor Validation Note | PASS | This standalone artifact includes evidence and criteria. | Auditor does not need prompt text, plan summaries, or ephemeral dispatch context. |

## UI/UX Auditor Validation Note

A `uiux-auditor` can decide from this artifact alone:

- PASS if all required CLI help snapshots (E1–E5), command matrix, JSON ANSI proof (E6), facade import proof (E7), behavioral proof register, and fresh guard context (E8) are present and internally consistent.
- PASS_WITH_DEBT if the UI/UX proof obligations pass but guard context remains warning-bearing with zero errors, as it does here (`status=passed`, `summary.errors=0`, `warnings=54`, `infos=4`, escape budget `14/15`).
- FAIL if any required surface snapshot is missing, any command evidence lacks an exit status, JSON parsing fails, ANSI escapes are present in the JSON payload, the facade import fails, the artifact is absent from repository docs, or the artifact relies only on plan summaries / prompt text instead of pasted evidence.

Current artifact decision basis: PASS_WITH_DEBT is available due to guard warnings and escape-budget warning; no blocker-class UI/UX proof obligation remains unproven in this artifact.

## Verification Notes

- Required current equivalents: `vectl orch drive-recover --help` exists in the current CLI and was used directly; no substitute equivalent was needed.
- No product code was modified to obtain evidence.
- No `vectl` CLI binary was invoked. All vectl CLI/UI evidence used Typer `CliRunner` against `vectl.cli.app` from `.venv/bin/python` as required.
- No plan lifecycle commands were invoked. Help commands do not mutate state; the JSON proof uses orchestration config validation rather than plan lifecycle state.
