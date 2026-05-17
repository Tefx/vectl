"""Contracts for pure role-profile default and override extraction.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> builtin_role_ids()
Traceback (most recent call last):
...
NotImplementedError: contract stub: builtin_role_ids

>>> apply_role_profile_override("", {"model": "x"}, ())  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda: len("builtin_role_ids") > 0)
@post(lambda result: isinstance(result, frozenset) and len(result) > 0 and all(role_id.strip() for role_id in result))
def builtin_role_ids() -> frozenset[str]:
    """Return non-empty built-in role identifiers.
    
    >>> builtin_role_ids()
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: builtin_role_ids
    """
    raise NotImplementedError("contract stub: builtin_role_ids")


@pre(lambda: len("default_role_profiles") > 0)
@post(lambda result: isinstance(result, tuple) and len({profile["id"] for profile in result if isinstance(profile, Mapping) and "id" in profile}) == len(result))
def default_role_profiles() -> tuple[Mapping[str, object], ...]:
    """Return built-in role profiles without duplicate effective role IDs.
    
    >>> default_role_profiles()
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: default_role_profiles
    """
    raise NotImplementedError("contract stub: default_role_profiles")


@pre(lambda role_id, override, builtin_ids: bool(role_id.strip()) and isinstance(override, Mapping) and all(str(key).strip() for key in override.keys()) and all(item.strip() for item in builtin_ids))
@post(lambda result: isinstance(result, Mapping) and bool(result.get("id")))
def apply_role_profile_override(
    role_id: str,
    override: Mapping[str, object],
    builtin_ids: Sequence[str],
) -> Mapping[str, object]:
    """Apply an override only to a known built-in role target.
    
    >>> apply_role_profile_override("planner", {"model": "x"}, ("planner",))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: apply_role_profile_override
    """
    raise NotImplementedError("contract stub: apply_role_profile_override")


@pre(lambda defaults, custom_profiles, overrides: all(isinstance(item, Mapping) and str(item.get("id", "")).strip() for item in defaults) and all(isinstance(item, Mapping) and str(item.get("id", "")).strip() for item in custom_profiles) and all(str(key).strip() for key in overrides.keys()))
@post(lambda result: isinstance(result, tuple) and len({profile["id"] for profile in result if isinstance(profile, Mapping) and "id" in profile}) == len(result))
def merge_role_profiles(
    defaults: Sequence[Mapping[str, object]],
    custom_profiles: Sequence[Mapping[str, object]],
    overrides: Mapping[str, Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Merge defaults, custom roles, and overrides without duplicate role IDs.
    
    >>> merge_role_profiles(({"id": "planner"},), ({"id": "critic"},), {})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: merge_role_profiles
    """
    raise NotImplementedError("contract stub: merge_role_profiles")
