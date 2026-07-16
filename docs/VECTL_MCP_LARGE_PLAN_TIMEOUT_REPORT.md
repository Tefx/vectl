# Vectl MCP Large-Plan Timeout Report

## Summary

Vectl MCP read calls become slow and can time out when `plan.yaml` grows to several megabytes. Concurrent read calls against one stdio MCP server amplify the delay and may close the client transport even though the server process remains alive.

The observed failure occurred while inspecting ResoFeed's plan through five parallel `vectl_show` calls. Subsequent serial `vectl_status`, `vectl_show`, and `vectl_validate` calls succeeded.

Execution admission now scopes completed-evidence failures to the active phase/step dependency closure and explicit `vectl.gate.scope.v2` `integrated_steps`. Unrelated completed-history failures remain visible as warnings, while full-history `review` continues to block on them. Global ID, DAG, dependency, and status-consistency checks remain blocking.

## Environment

- Vectl MCP command: `uv run --project /Users/tefx/Projects/vectl vectl mcp`
- Transport: stdio through the Codex app server
- Plan size: 8,217,573 bytes
- Plan shape: 127 phases
- Vectl MCP configuration: no explicit `tool_timeout_sec`; the local configuration generator can emit `supports_parallel_tool_calls`, but per-server support was not confirmed for Codex 0.144.2

## Reproduction

1. Start Vectl MCP in a repository containing the observed large plan.
2. Send five `vectl_show` requests concurrently to the same stdio server.
3. Observe `MCP error -32001: Request timed out` for the requests.
4. Immediately call another Vectl tool and observe that the client may report `Connection closed`.
5. Retry `vectl_status`, `vectl_show`, and `vectl_validate` serially; they complete successfully.

## Expected behavior

- Read-oriented MCP calls should remain within the configured tool timeout for supported plan sizes. `vectl_status` may also repair ghost claims and is therefore not a pure read.
- When parallel calls are unsupported, the runtime should serialize them or reject concurrency without closing the transport.
- A timed-out request should not make later serial calls unusable.

## Actual behavior

- Five parallel `vectl_show` requests timed out.
- A following validation request reported a closed connection.
- The MCP process remained present.
- Later serial calls recovered and the plan validated with zero errors and zero warnings.

## Measurements

| Operation | Observed serial duration |
|---|---:|
| `vectl status` | 9.7 seconds |
| `vectl show item-summary-deep-links-assurance-v2` | 11.7 seconds |
| `vectl validate --check-refs` | 12.0 seconds |

`load_plan_definition()` reads and parses the complete YAML document on every tool call. Profiling isolated the dominant cost:

| Loader | Observed parse duration |
|---|---:|
| `yaml.SafeLoader` | 3–10 seconds |
| `yaml.CSafeLoader` | 0.4–1.1 seconds |

File reading, content hashing, and Pydantic model construction were small compared with YAML parsing.

## Relevant implementation

- `src/vectl/mcp_core_common.py::_load()` calls `load_plan_definition()` for every MCP tool invocation.
- `src/vectl/io.py::load_plan_definition()` uses `yaml.safe_load(content)`.
- `src/vectl/mcp_core_read_tools.py::vectl_status()` and `vectl_show()` each load the complete plan before formatting a bounded result.
- The MCP configuration generator supports `tool_timeout_sec` and can emit `supports_parallel_tool_calls`; the installed Codex 0.144.2 configuration reference and `codex mcp get` did not confirm the latter as an effective per-server setting.

## Additional observation

Sixteen Vectl MCP server instances were present under one Codex app-server process, represented by 32 wrapper/child processes using about 200 MB total. Their ages ranged from roughly one to thirteen hours. This suggests per-session or reconnect accumulation. It was not required to reproduce the timeout, but it should be investigated separately because process ownership is not visible enough to terminate stale instances safely.

## Root-cause assessment

The direct cause is repeated whole-plan YAML parsing combined with concurrent calls against one stdio server. Parallel requests multiply CPU-bound Python parsing work and queueing delay until the client timeout is reached. The transport closure is a secondary client/server recovery problem after those request timeouts.

## Validation scope during remediation

`vectl validate` and MCP `vectl_validate` use active-dependency-closure evidence severity:

- Completed failures in transitive phase dependencies, same-phase step dependencies, or explicit gate `integrated_steps` remain errors.
- The canonical machine-readable carrier is one `<vectl_gate_scope>` JSON block in an explicit gate step's `description`; `Phase.gate` remains a human-readable criterion.
- Malformed, duplicated, misplaced, or unresolved active gate scope fails conservatively by retaining blocking severity for all completed-evidence failures.
- Completed failures outside that closure are emitted as non-blocking warnings rather than disappearing.
- Duplicate IDs, unknown dependencies, cycles, and phase/step status inconsistencies remain global errors.
- Full-history `vectl review` retains blocking completed-evidence behavior.

This separation prevents unrelated legacy evidence debt from blocking a new independent phase without weakening the dependencies that authorize its execution. It does not reduce YAML parsing cost and is only an admission prerequisite for the loader remediation.

## Immediate workaround

- Serialize all Vectl MCP calls for large plans.
- Do not issue parallel `status`, `show`, `search`, `review`, or `validate` calls to one server.
- Use the configured project CLI only as a diagnostic fallback when the MCP transport is unavailable:
  `uv run --project /Users/tefx/Projects/vectl vectl ...`

## Proposed remediation

The active-dependency-closure validation rule above must remain enabled while this work executes; full-history review remains the audit surface.

1. Use `yaml.CSafeLoader` when available, with `SafeLoader` fallback.
2. Add semantic-equivalence tests covering both loaders.
3. Add an 8 MB-class MCP performance fixture for `status`, `show`, and `validate`.
4. Set the canonical Vectl MCP configuration to:
   - `supports_parallel_tool_calls = false`
   - `tool_timeout_sec = 180`
5. Ensure the client serializes calls when parallel tool calls are disabled.
6. Preserve or recreate the MCP transport after a request timeout.
7. Investigate session/reconnect cleanup for orphaned stdio server processes.

## Acceptance criteria

- Serial `status`, `show`, and `validate --check-refs` complete reliably against the large-plan fixture.
- The optimized loader produces a `Plan` equivalent to the current loader.
- Concurrent requests are serialized or rejected without transport closure.
- A timed-out request does not prevent a later serial request from succeeding.
- Session shutdown or reconnect does not leave an unbounded number of Vectl MCP processes.
- An unrelated completed-history failure is reported as a warning during `validate` and remains an error during full-history `review`.
- A completed failure in the active phase dependency closure or explicit gate `integrated_steps` remains blocking.
