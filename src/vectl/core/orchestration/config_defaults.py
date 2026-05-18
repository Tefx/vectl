"""Pure role-profile default and override extraction.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> sorted(builtin_role_ids())[:2]
['blocked-case-coordinator', 'blocked-case-coordinator-tacit']

>>> apply_role_profile_override("", {"model": "x"}, ())  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


_DEFAULT_ROLE_PROFILES: tuple[Mapping[str, object], ...] = (
    {
        "id": "python-executor",
        "agent_id": "python-executor",
        "prompt_family": "coder",
        "execution_context": "linked_worktree",
        "mutation_policy": "worktree_changes",
        "session_policy": "reuse_allowed",
        "output_contract": "freeform_evidence",
        "default_runner": "codex",
    },
    {
        "id": "python-senior",
        "agent_id": "python-senior",
        "prompt_family": "coder",
        "execution_context": "linked_worktree",
        "mutation_policy": "worktree_changes",
        "session_policy": "reuse_allowed",
        "output_contract": "freeform_evidence",
        "default_runner": "codex",
    },
    {
        "id": "vectl-planner",
        "agent_id": "vectl-planner",
        "prompt_family": "planner",
        "execution_context": "main_worktree",
        "mutation_policy": "vectl_facade_only",
        "session_policy": "reuse_forbidden",
        "output_contract": "vectl_facade_mutation",
        "default_runner": "codex",
    },
    {
        "id": "gate-reviewer",
        "agent_id": "gate-reviewer",
        "prompt_family": "reviewer",
        "execution_context": "main_worktree",
        "mutation_policy": "read_only",
        "session_policy": "reuse_allowed",
        "output_contract": "structured_review_result",
        "default_runner": "codex",
    },
    {
        "id": "doc-reviewer",
        "agent_id": "doc-reviewer",
        "prompt_family": "reviewer",
        "execution_context": "main_worktree",
        "mutation_policy": "read_only",
        "session_policy": "reuse_allowed",
        "output_contract": "structured_review_result",
        "default_runner": "codex",
    },
    {
        "id": "spec-readiness-auditor",
        "agent_id": "spec-readiness-auditor",
        "prompt_family": "reviewer",
        "execution_context": "main_worktree",
        "mutation_policy": "read_only",
        "session_policy": "reuse_allowed",
        "output_contract": "structured_review_result",
        "default_runner": "codex",
    },
    {
        "id": "blocked-case-coordinator",
        "agent_id": "blocked-case-coordinator",
        "prompt_family": "resolver",
        "execution_context": "main_worktree",
        "mutation_policy": "vectl_facade_only",
        "session_policy": "reuse_forbidden",
        "output_contract": "resolution_report",
        "default_runner": "codex",
    },
    {
        "id": "blocked-case-coordinator-tacit",
        "agent_id": "blocked-case-coordinator-tacit",
        "prompt_family": "resolver",
        "execution_context": "main_worktree",
        "mutation_policy": "vectl_facade_only",
        "session_policy": "reuse_forbidden",
        "output_contract": "resolution_report",
        "default_runner": "codex",
    },
)

_ALLOWED_OVERRIDE_FIELDS = frozenset(
    {
        "agent_id",
        "prompt_family",
        "execution_context",
        "mutation_policy",
        "session_policy",
        "output_contract",
        "default_runner",
    }
)


@pre(
    lambda profiles=_DEFAULT_ROLE_PROFILES: len(profiles) > 0
    and all(str(profile.get("id", "")).strip() for profile in profiles)
)
@post(
    lambda result: isinstance(result, frozenset)
    and len(result) > 0
    and all(role_id.strip() for role_id in result)
)
def builtin_role_ids(
    profiles: Sequence[Mapping[str, object]] = _DEFAULT_ROLE_PROFILES,
) -> frozenset[str]:
    """Return non-empty built-in role identifiers.
    
    >>> "python-executor" in builtin_role_ids()
    True
    """
    return frozenset(str(profile["id"]) for profile in profiles)


@pre(
    lambda profiles=_DEFAULT_ROLE_PROFILES: len(profiles) > 0
    and len({str(profile.get("id", "")) for profile in profiles}) == len(profiles)
)
@post(
    lambda result: isinstance(result, tuple)
    and len({profile["id"] for profile in result if isinstance(profile, Mapping) and "id" in profile})
    == len(result)
)
def default_role_profiles(
    profiles: Sequence[Mapping[str, object]] = _DEFAULT_ROLE_PROFILES,
) -> tuple[Mapping[str, object], ...]:
    """Return built-in role profiles without duplicate effective role IDs.
    
    >>> default_role_profiles()[0]["id"]
    'python-executor'
    """
    return tuple(dict(profile) for profile in profiles)


@pre(lambda role_id, override, builtin_ids: bool(role_id.strip()) and isinstance(override, Mapping) and all(str(key).strip() for key in override.keys()) and all(item.strip() for item in builtin_ids))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("id")))
def apply_role_profile_override(
    role_id: str,
    override: Mapping[str, object],
    builtin_ids: Sequence[str],
) -> Mapping[str, object]:
    """Apply an override only to a known built-in role target.
    
    >>> apply_role_profile_override("planner", {"agent_id": "planner-2"}, ("planner",))["agent_id"]
    'planner-2'
    """
    builtin_set = frozenset(builtin_ids)
    if role_id not in builtin_set:
        raise ValueError(f"unknown built-in role in role_profile_overrides: {role_id!r}")
    unknown = sorted(str(key) for key in override if str(key) not in _ALLOWED_OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(
            f"unknown override field {unknown[0]!r}; allowed fields: {sorted(_ALLOWED_OVERRIDE_FIELDS)}"
        )
    base = next(
        (dict(profile) for profile in _DEFAULT_ROLE_PROFILES if str(profile["id"]) == role_id),
        {"id": role_id},
    )
    for field_name, value in override.items():
        base[str(field_name)] = str(value)
    base["id"] = role_id
    return base


@pre(lambda defaults, custom_profiles, overrides: all(isinstance(item, Mapping) and str(item.get("id", "")).strip() for item in defaults) and all(isinstance(item, Mapping) and str(item.get("id", "")).strip() for item in custom_profiles) and all(str(key).strip() for key in overrides.keys()))
@post(lambda result: isinstance(result, tuple) and len({profile["id"] for profile in result if isinstance(profile, Mapping) and "id" in profile}) == len(result))
def merge_role_profiles(
    defaults: Sequence[Mapping[str, object]],
    custom_profiles: Sequence[Mapping[str, object]],
    overrides: Mapping[str, Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Merge defaults, custom roles, and overrides without duplicate role IDs.
    
    >>> [p["id"] for p in merge_role_profiles(({"id": "planner"},), ({"id": "critic"},), {})]
    ['planner', 'critic']
    """
    builtin_ids = frozenset(str(profile["id"]) for profile in defaults)
    custom_ids = frozenset(str(profile["id"]) for profile in custom_profiles)
    for role_id in overrides:
        if role_id not in builtin_ids:
            raise ValueError(f"unknown built-in role in role_profile_overrides: {role_id!r}")
        if role_id in custom_ids:
            raise ValueError(f"role_profile_overrides.{role_id} cannot target custom role defined in role_profiles")
    for profile in custom_profiles:
        role_id = str(profile["id"])
        if role_id in builtin_ids:
            raise ValueError(f"role_profiles.{role_id} shadows built-in role; use role_profile_overrides instead")

    merged: dict[str, Mapping[str, object]] = {}
    for profile in defaults:
        role_id = str(profile["id"])
        merged[role_id] = dict(profile)
        if role_id in overrides:
            patched = dict(profile)
            for key, value in overrides[role_id].items():
                if key not in _ALLOWED_OVERRIDE_FIELDS:
                    raise ValueError(
                        f"unknown override field {key!r}; allowed fields: {sorted(_ALLOWED_OVERRIDE_FIELDS)}"
                    )
                patched[key] = str(value)
            merged[role_id] = patched
    for profile in custom_profiles:
        merged[str(profile["id"])] = dict(profile)
    ordered_ids = [str(profile["id"]) for profile in defaults] + [
        str(profile["id"]) for profile in custom_profiles if str(profile["id"]) not in builtin_ids
    ]
    return tuple(merged[role_id] for role_id in ordered_ids if role_id in merged)
