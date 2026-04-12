# RFC: Explicit Role Profile Overrides for Orchestration Config

**Status:** Draft  
**Date:** 2026-04-12  
**Author(s):** ChatGPT (draft for discussion)

## 1. Problem / Current Situation

vectl currently supports orchestration role configuration through:

- built-in role defaults from `default_role_profiles()`
- explicit config file entries under `orchestration.role_profiles`

The current behavior is all-or-nothing:

- if `orchestration.role_profiles` is absent, vectl uses the built-in defaults
- if `orchestration.role_profiles` is present, vectl treats it as the entire authoritative registry

This creates a poor user experience for the most common customization case:

> "I only want to change one field on built-in roles, such as `default_runner: opencode`."

Under the current model, a user who wants to change only the runner must copy a large registry of built-in roles into `vectl.yaml` or `~/.config/vectl/vectl.yaml`.

That causes three concrete problems:

1. **Configuration drift**  
   User configs silently diverge from upstream built-ins as new roles or fields are added.

2. **High maintenance cost**  
   Large copied registries obscure the user's actual intent.

3. **Weak ergonomics for safe customization**  
   A small override requires a large and fragile config block.

## 2. Why the Naive Alternatives Are Not Good Enough

### 2.1 Keep the current behavior

Keeping the current model is simple for implementation, but it pushes the complexity onto users.

This is already causing real friction: changing a single role field requires maintaining a full copied registry.

### 2.2 Make `role_profiles` implicitly merge with built-ins

This looks attractive at first, but it introduces ambiguous semantics.

If vectl allows:

```yaml
orchestration:
  role_profiles:
    python-executor:
      default_runner: opencode
```

there is no longer a clear distinction between:

- defining a new role
- patching a built-in role
- replacing a built-in role

This would make the config system more magical and harder to reason about.

The repo's broader design direction favors explicit control flow and explicit configuration semantics over convenient-but-implicit merging.

## 3. Proposal

Add a new configuration key:

```yaml
orchestration:
  role_profile_overrides:
    python-executor:
      default_runner: opencode
```

and keep existing `role_profiles` semantics unchanged.

### Final meaning of the two keys

| Key | Meaning |
|-----|---------|
| `role_profiles` | Define new custom roles explicitly |
| `role_profile_overrides` | Patch built-in roles by role ID |

This preserves explicitness:

- `role_profiles` means **define/add roles**
- `role_profile_overrides` means **patch built-ins**

## 4. Proposed Config Shapes

### 4.1 Minimal common-case override

```yaml
orchestration:
  runtime:
    default_runner: opencode

  role_profile_overrides:
    python-executor:
      default_runner: opencode
    python-senior:
      default_runner: opencode
    blocked-case-coordinator:
      default_runner: opencode
```

### 4.2 Add a new custom role

```yaml
orchestration:
  role_profiles:
    my-custom-reviewer:
      agent_id: my-custom-reviewer
      prompt_family: reviewer
      execution_context: main_worktree
      mutation_policy: read_only
      session_policy: reuse_allowed
      output_contract: structured_review_result
      default_runner: opencode
```

### 4.3 Use both together

```yaml
orchestration:
  role_profile_overrides:
    python-executor:
      default_runner: opencode

  role_profiles:
    my-custom-reviewer:
      agent_id: my-custom-reviewer
      prompt_family: reviewer
      execution_context: main_worktree
      mutation_policy: read_only
      session_policy: reuse_allowed
      output_contract: structured_review_result
      default_runner: opencode
```

## 5. Effective Registry Resolution

The effective orchestration role registry should be built in this order:

1. Start from `default_role_profiles()`
2. Apply `role_profile_overrides` to matching built-in roles
3. Add explicit custom roles from `role_profiles`
4. Validate the final registry
5. Resolve `dispatch.default_role_id` and `resolver.default_role_id` against the final registry

### 5.1 Important semantic rule

`role_profile_overrides` should only patch built-in roles.

It should **not** silently create new roles.

If the target role ID does not exist in built-ins, validation must fail.

### 5.2 Important semantic rule

`role_profiles` should define new custom roles only.

It should **not** silently replace a built-in role.

If a config file defines a `role_profiles.<role_id>` that already exists in built-ins,
the loader should reject it with a validation error and direct the user to
`role_profile_overrides`.

This keeps the semantics explicit and avoids ambiguous replacement behavior.

## 6. Validation Rules

### 6.1 `role_profile_overrides`

Allowed fields:

- `agent_id`
- `prompt_family`
- `execution_context`
- `mutation_policy`
- `session_policy`
- `output_contract`
- `default_runner`

Validation rules:

1. override target role ID must exist in built-ins
2. unknown override fields must be rejected
3. override targets that refer to custom roles from `role_profiles` must be rejected
4. the patched profile must still satisfy family-policy validation
5. the patched role ID remains unchanged

### 6.2 `role_profiles`

Validation rules:

1. custom role IDs must be globally unique in the final registry
2. custom role IDs must not shadow built-in role IDs
3. all roles loaded from `role_profiles` must satisfy the same family-policy validation as built-ins
4. if `role_profiles.<role_id>` matches a built-in role ID, validation must fail with a migration-directed error that tells the user to use `role_profile_overrides`

### 6.3 Final registry validation

After merge:

1. final role IDs must be unique
2. final profiles must satisfy family-policy invariants
3. `dispatch.default_role_id` must exist in final registry
4. `resolver.default_role_id` must exist in final registry

## 7. CLI and UX Implications

### 7.1 `config-show --effective`

The effective config output should show the final merged profiles.

This RFC requires provenance for role profile fields in the same change.

Required output contracts:

#### Human output

Human output must use one line per fully-qualified field path:

```text
role_profiles.python-executor.default_runner = opencode (source=override)
role_profiles.doc-reviewer.default_runner = codex (source=default)
role_profiles.my-custom-reviewer.default_runner = opencode (source=custom)
```

#### Structured output (`--json` / `--output json`)

Structured output must include a `role_profile_provenance` object with this shape:

```yaml
role_profile_provenance:
  python-executor:
    default_runner:
      value: opencode
      source: override
  doc-reviewer:
    default_runner:
      value: codex
      source: default
  my-custom-reviewer:
    default_runner:
      value: opencode
      source: custom
```

Illustrative human example:

```text
role_profiles.python-executor.default_runner = opencode (source=override)
role_profiles.doc-reviewer.default_runner = codex (source=default)
role_profiles.my-custom-reviewer.default_runner = opencode (source=custom)
```

This is especially important because config merge behavior is otherwise invisible to users.

Minimum provenance categories:

- `default`
- `override`
- `custom`

This provenance may be implemented inside the config inspection surface rather than the runtime config model itself.

### 7.2 `config-validate`

Validation errors should be explicit and corrective.

Required error message formats:

- `unknown built-in role in role_profile_overrides: <role_id>`
- `role_profiles.<role_id> shadows built-in role; use role_profile_overrides instead`
- `role_profile_overrides.<role_id> cannot target custom role defined in role_profiles`
- `role_profile_overrides.<role_id>.<field> violates <prompt_family> family policy`

These errors should be surfaced through the existing configuration validation path
using `ConfigValidationError` (or an equivalent structured validation error type
if the implementation refactors validation internals).

### 7.3 Future CLI affordances

Not required for the initial implementation, but useful follow-ups:

- `vectl orch config-explain orchestration.role_profile_overrides`
- `vectl orch config-init --example role-overrides`
- warnings that suggest `role_profile_overrides` when a user appears to be patching built-ins through `role_profiles`

## 8. Implementation Direction

### 8.1 Primary implementation location

The main implementation should live in:

- `src/vectl/orchestration/config.py`
- `src/vectl/orch_app.py` / config inspection surface for effective provenance rendering
- `src/vectl/cli.py` for final CLI output wiring if the existing payload surface requires extension

This keeps the behavior localized to the configuration loader and validator.

The dispatch/runtime layer should consume only the final effective registry, not the raw override syntax.

### 8.2 Recommended internal shape

Recommended internal flow:

1. parse raw YAML
2. parse built-ins
3. parse override mapping
4. merge into effective `tuple[RoleProfile, ...]`
5. validate final config
6. store only the effective profiles in `OrchestrationConfig`

The runtime should not need to know whether a field came from defaults,
override config, or custom role definition.

Provenance should be computed and surfaced by config inspection/reporting paths,
not by making runtime dispatch depend on provenance metadata.

### 8.3 Suggested helper functions

Examples of likely internal helpers:

- `_parse_role_profile_overrides(...)`
- `_merge_role_profiles(...)`
- `_apply_role_profile_override(...)`

The exact names are not important. The important point is that all role merge logic stays inside the config boundary.

## 9. Non-goals

This RFC does **not** propose:

- wildcard role overrides
- inheritance chains between role profiles
- role-profile templates
- environment-specific profile matrices
- implicit field fallback between custom roles

These would add complexity beyond the problem being solved.

The target is narrow:

> make common built-in role customization explicit and lightweight without making config semantics magical.

## 10. Compatibility / Migration

### Backward compatibility

This proposal intentionally changes config semantics for configs that currently
shadow built-in roles under `role_profiles`.

- existing configs without `role_profile_overrides` continue to work unchanged
- existing `role_profiles` configs that define custom roles continue to work unchanged
- configs that use `role_profiles` to redefine built-in roles must be updated to `role_profile_overrides`

### Migration benefit

Users who currently copied the full built-in role registry only to change
`default_runner` can migrate to a much smaller config.

### Migration recommendation

For user-level configs:

1. keep `runtime.default_runner` if you want a global runner preference
2. replace copied built-in roles with `role_profile_overrides`
3. leave `role_profiles` only for truly custom roles

## 11. Why This Is the Smallest Useful Change

This RFC intentionally avoids rewriting the entire config model.

It adds:

- one new top-level orchestration key
- a merge helper
- a small number of validation rules

That is enough to remove the main user pain while preserving the current explicitness of the config model.

## 12. Remaining Open Question

1. Should vectl emit a migration hint when it detects copied built-in roles in `role_profiles`?
   - Recommendation: yes, eventually, but not required for the first implementation.

## 13. Test Requirements

The implementation must include tests for at least these scenarios:

| Scenario | Expected Result |
|----------|-----------------|
| `role_profiles` defines a built-in role ID | validation error with migration-directed message |
| `role_profile_overrides` targets an unknown built-in role | validation error |
| `role_profile_overrides` targets a custom role from `role_profiles` | validation error |
| valid built-in override | final effective registry contains patched field values |
| valid custom role definition | final effective registry includes the custom role |
| family-policy violation through override | validation error |
| human `config-show --effective` | required one-line provenance format is present |
| structured `config-show --effective --json` | `role_profile_provenance` object has correct `value` / `source` shape |

These tests should live close to existing orchestration config loader and CLI/config inspection tests.

## 14. Decision Summary

This RFC recommends:

1. add `orchestration.role_profile_overrides`
2. keep `role_profiles` for defining custom roles only
3. reject built-in role shadowing from `role_profiles`
4. make `role_profile_overrides` built-in-only; it must not patch custom roles
5. expose field provenance in `config-show --effective` in the same change
6. validate override targets explicitly
7. keep merge logic in `config.py`

This gives users a simple way to customize built-ins without forcing full registry copies and without introducing implicit merge behavior.
