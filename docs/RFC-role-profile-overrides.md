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
| `role_profiles` | Define new custom roles explicitly; legacy built-in shadowing remains temporarily supported during migration |
| `role_profile_overrides` | Patch built-in roles by role ID |

This preserves explicitness:

- `role_profiles` means **define/add roles**
- `role_profile_overrides` means **patch built-ins**

For backward compatibility, existing configs that redefine built-in roles under
`role_profiles` are still accepted in the initial rollout, but they should be
treated as a deprecated compatibility path rather than the preferred model.

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

`role_profiles` should primarily define new custom roles.

However, for backward compatibility, the initial rollout should still accept
configs that redefine built-in role IDs under `role_profiles`.

Recommended staged behavior:

1. **Initial rollout:** accept built-in role shadowing under `role_profiles`, but emit a deprecation warning that directs users to `role_profile_overrides`
2. **Future cleanup release:** optionally escalate that warning into a validation error once migration tooling exists

This keeps the rollout safe while still steering the config model toward the
clearer long-term semantics.

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
3. the patched profile must still satisfy family-policy validation
4. the patched role ID remains unchanged

### 6.2 `role_profiles`

Validation rules:

1. custom role IDs must be globally unique in the final registry
2. if a `role_profiles.<role_id>` shadows a built-in role in the initial rollout, emit a deprecation warning
3. all roles loaded from `role_profiles` must satisfy the same family-policy validation as built-ins
4. future cleanup release may convert built-in shadowing from warning to validation error

### 6.3 Final registry validation

After merge:

1. final role IDs must be unique
2. final profiles must satisfy family-policy invariants
3. `dispatch.default_role_id` must exist in final registry
4. `resolver.default_role_id` must exist in final registry

## 7. CLI and UX Implications

### 7.1 `config-show --effective`

The effective config output should show the final merged profiles.

Recommended future improvement: include provenance for role profile fields.

Example:

```text
role_profiles.python-executor.default_runner = opencode (source=override)
role_profiles.doc-reviewer.default_runner = codex (source=default)
role_profiles.my-custom-reviewer.default_runner = opencode (source=custom)
```

This is especially important because config merge behavior is otherwise invisible to users.

### 7.2 `config-validate`

Validation errors should be explicit and corrective.

Recommended examples:

- `unknown built-in role in role_profile_overrides: python-executorr`
- `role_profiles.python-executor shadows built-in role; this legacy form is deprecated, use role_profile_overrides instead`
- `role_profile_overrides.python-executor.output_contract violates coder family policy`

### 7.3 Future CLI affordances

Not required for the initial implementation, but useful follow-ups:

- `vectl orch config-explain orchestration.role_profile_overrides`
- `vectl orch config-init --example role-overrides`
- warnings that suggest `role_profile_overrides` when a user appears to be patching built-ins through `role_profiles`

## 8. Implementation Direction

### 8.1 Primary implementation location

The main implementation should live in:

- `src/vectl/orchestration/config.py`

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

This proposal is backward compatible in the initial rollout:

- existing configs without `role_profile_overrides` continue to work unchanged
- existing full explicit `role_profiles` configs continue to work
- existing configs that shadow built-in roles under `role_profiles` continue to work, but should receive a deprecation warning

### Migration benefit

Users who currently copied the full built-in role registry only to change
`default_runner` can migrate to a much smaller config.

### Migration recommendation

For user-level configs:

1. keep `runtime.default_runner` if you want a global runner preference
2. replace copied built-in roles with `role_profile_overrides`
3. leave `role_profiles` only for truly custom roles

For rollout sequencing:

1. ship `role_profile_overrides`
2. warn on built-in shadowing under `role_profiles`
3. optionally add migration tooling or `config-init --example role-overrides`
4. only later consider rejecting built-in shadowing entirely

## 11. Why This Is the Smallest Useful Change

This RFC intentionally avoids rewriting the entire config model.

It adds:

- one new top-level orchestration key
- a merge helper
- a small number of validation rules

That is enough to remove the main user pain while preserving the current explicitness of the config model.

## 12. Open Questions

1. Should provenance for effective role fields be added in the same change, or later?
   - Recommendation: later is acceptable, but it is strongly desirable.

2. Should `role_profile_overrides` be allowed to patch custom roles from `role_profiles`?
   - Recommendation: no. Keep it built-in-only.

3. Should vectl emit a migration hint when it detects copied built-in roles in `role_profiles`?
   - Recommendation: yes, eventually, but not required for the first implementation.

4. Should built-in shadowing under `role_profiles` remain supported indefinitely?
   - Recommendation: no. Keep it only as a migration bridge.

## 13. Decision Summary

This RFC recommends:

1. add `orchestration.role_profile_overrides`
2. keep `role_profiles` as the long-term place for custom roles
3. support built-in shadowing under `role_profiles` only as a deprecated compatibility path in the initial rollout
4. validate override targets explicitly
5. keep merge logic in `config.py`

This gives users a simple way to customize built-ins without forcing full registry copies and without introducing implicit merge behavior.
