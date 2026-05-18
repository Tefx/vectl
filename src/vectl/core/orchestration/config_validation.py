"""Pure orchestration config validation/provenance extraction.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> validate_role_profile({"id": "planner", "prompt_family": "planner", "execution_context": "main_worktree", "mutation_policy": "vectl_facade_only", "session_policy": "reuse_forbidden", "output_contract": "vectl_facade_mutation"})
[]

>>> validate_role_profile({"id": "", "tool_allowlist": ["read"]})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


_ROLE_FAMILY_POLICY: Mapping[str, Mapping[str, str]] = {
    "coder": {
        "execution_context": "linked_worktree",
        "mutation_policy": "worktree_changes",
        "session_policy": "reuse_allowed",
        "output_contract": "freeform_evidence",
    },
    "planner": {
        "execution_context": "main_worktree",
        "mutation_policy": "vectl_facade_only",
        "session_policy": "reuse_forbidden",
        "output_contract": "vectl_facade_mutation",
    },
    "reviewer": {
        "execution_context": "main_worktree",
        "mutation_policy": "read_only",
        "session_policy": "reuse_allowed",
        "output_contract": "structured_review_result",
    },
    "resolver": {
        "execution_context": "main_worktree",
        "mutation_policy": "vectl_facade_only",
        "session_policy": "reuse_forbidden",
        "output_contract": "resolution_report",
    },
}


@pre(lambda profile: isinstance(profile, Mapping) and bool(str(profile.get("id", "")).strip()))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_role_profile(profile: Mapping[str, object]) -> list[str]:
    """Return stable diagnostics for one constructed role profile.
    
    >>> validate_role_profile({"id": "custom"})
    []
    """
    family = str(profile.get("prompt_family", ""))
    policy = _ROLE_FAMILY_POLICY.get(family)
    if policy is None:
        return []
    errors: list[str] = []
    for field_name, expected in policy.items():
        actual = profile.get(field_name)
        if actual is not None and str(actual) != expected:
            errors.append(f"{field_name}={actual!r} expected {expected!r}")
    return errors


@pre(lambda profiles: all(isinstance(profile, Mapping) and str(profile.get("id", "")).strip() for profile in profiles))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_role_profiles(profiles: Sequence[Mapping[str, object]]) -> list[str]:
    """Return deterministic diagnostics for constructed role profiles.
    
    >>> validate_role_profiles(({"id": "planner"}, {"id": "planner"}))
    ["duplicate role profile 'planner'"]
    """
    errors: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        role_id = str(profile["id"])
        if role_id in seen:
            errors.append(f"duplicate role profile {role_id!r}")
        seen.add(role_id)
        family_errors = validate_role_profile(profile)
        if family_errors:
            errors.append(
                f"Role profile {role_id!r} violates {profile.get('prompt_family')!r} family policy: "
                + ", ".join(family_errors)
            )
    return errors


@pre(lambda config: isinstance(config, Mapping) and bool(config.keys()))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_orchestration_config(config: Mapping[str, object]) -> list[str]:
    """Return stable diagnostics for a constructed orchestration config.
    
    >>> validate_orchestration_config({"runtime": {"cleanup_policy": "sometimes"}})
    ['runtime.cleanup_policy: expected one of: never, on-success, always']
    """
    errors: list[str] = []
    runtime = _section(config, "runtime")
    if runtime.get("cleanup_policy", "on-success") not in ("never", "on-success", "always"):
        errors.append("runtime.cleanup_policy: expected one of: never, on-success, always")
    if runtime.get("isolation_default", "default") not in ("default", "workspace", "independent"):
        errors.append("runtime.isolation_default: expected one of: default, workspace, independent")
    numeric_rules = (
        ("resolver", "timeout_seconds", 600.0, "must be > 0", lambda value: float(value) <= 0),
        ("resolver", "max_tool_calls_per_invocation", 100, "must be > 0", lambda value: int(value) <= 0),
        ("resolver", "max_tool_argument_bytes", 65536, "must be > 0", lambda value: int(value) <= 0),
        ("control", "action_ack_timeout_seconds", 5.0, "must be > 0", lambda value: float(value) <= 0),
        ("observability", "heartbeat_stale_threshold_seconds", 120, "must be > 0", lambda value: int(value) <= 0),
        ("observability", "retention_days", 30, "must be >= 0", lambda value: int(value) < 0),
        ("observability", "max_log_megabytes", 100, "must be > 0", lambda value: int(value) <= 0),
        ("operator", "max_pending_actions", 100, "must be > 0", lambda value: int(value) <= 0),
        ("drive", "collect_poll_interval_ms", 250, "must be > 0", lambda value: int(value) <= 0),
        ("drive", "resolver_timeout_seconds", 300.0, "must be > 0", lambda value: float(value) <= 0),
        ("drive", "planner_timeout_seconds", 300.0, "must be > 0", lambda value: float(value) <= 0),
    )
    for section_name, key, default, reason, predicate in numeric_rules:
        value = _section(config, section_name).get(key, default)
        if predicate(value):
            errors.append(f"{section_name}.{key}: {reason}")
    max_parallelism = int(_section(config, "drive").get("max_parallelism", 4))
    if max_parallelism < 1 or max_parallelism > 32:
        errors.append("drive.max_parallelism: must be between 1 and 32 inclusive")
    profiles = config.get("role_profiles", ())
    if isinstance(profiles, Sequence) and not isinstance(profiles, str):
        errors.extend(validate_role_profiles(profiles))
    return errors


@pre(lambda profiles, source_label: bool(source_label.strip()) and all(isinstance(profile, Mapping) and str(profile.get("id", "")).strip() for profile in profiles))
@post(lambda result: isinstance(result, Mapping) and list(result.keys()) == sorted(result.keys()))
def build_role_profile_provenance(
    profiles: Sequence[Mapping[str, object]],
    source_label: str,
) -> Mapping[str, str]:
    """Return sorted role-profile provenance keys.
    
    >>> build_role_profile_provenance(({"id": "planner"},), "defaults")
    {'planner': 'defaults'}
    """
    return {str(profile["id"]): source_label for profile in sorted(profiles, key=lambda item: str(item["id"]))}


@pre(lambda config, source_label: isinstance(config, Mapping) and bool(config.keys()) and bool(source_label.strip()))
@post(lambda result: isinstance(result, Mapping) and list(result.keys()) == sorted(result.keys()))
def build_drive_config_provenance(config: Mapping[str, object], source_label: str) -> Mapping[str, str]:
    """Return sorted drive-config provenance keys.
    
    >>> build_drive_config_provenance({"max_parallelism": 4}, "defaults")
    {'drive.max_parallelism': 'defaults'}
    """
    return {f"drive.{key}": source_label for key in sorted(config.keys())}


@pre(lambda config, name: isinstance(config, Mapping) and bool(name.strip()))
@post(lambda result: isinstance(result, Mapping))
def _section(config: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = config.get(name, {})
    return value if isinstance(value, Mapping) else {}
