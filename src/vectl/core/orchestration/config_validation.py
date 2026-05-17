"""Contracts for pure orchestration config validation/provenance extraction.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> validate_role_profile({"id": "planner", "tool_allowlist": ["read"]})
Traceback (most recent call last):
...
NotImplementedError: contract stub: validate_role_profile

>>> validate_role_profile({"id": "", "tool_allowlist": ["read"]})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda profile: isinstance(profile, Mapping) and bool(str(profile.get("id", "")).strip()))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_role_profile(profile: Mapping[str, object]) -> list[str]:
    """Return stable diagnostics for one constructed role profile.
    
    >>> validate_role_profile({"id": "planner"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: validate_role_profile
    """
    raise NotImplementedError("contract stub: validate_role_profile")


@pre(lambda profiles: all(isinstance(profile, Mapping) and str(profile.get("id", "")).strip() for profile in profiles))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_role_profiles(profiles: Sequence[Mapping[str, object]]) -> list[str]:
    """Return deterministic diagnostics for constructed role profiles.
    
    >>> validate_role_profiles(({"id": "planner"},))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: validate_role_profiles
    """
    raise NotImplementedError("contract stub: validate_role_profiles")


@pre(lambda config: isinstance(config, Mapping) and bool(config.keys()))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def validate_orchestration_config(config: Mapping[str, object]) -> list[str]:
    """Return stable diagnostics for a constructed orchestration config.
    
    >>> validate_orchestration_config({"runner": "opencode"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: validate_orchestration_config
    """
    raise NotImplementedError("contract stub: validate_orchestration_config")


@pre(lambda profiles, source_label: bool(source_label.strip()) and all(isinstance(profile, Mapping) and str(profile.get("id", "")).strip() for profile in profiles))
@post(lambda result: isinstance(result, Mapping) and list(result.keys()) == sorted(result.keys()))
def build_role_profile_provenance(
    profiles: Sequence[Mapping[str, object]],
    source_label: str,
) -> Mapping[str, str]:
    """Return sorted role-profile provenance keys.
    
    >>> build_role_profile_provenance(({"id": "planner"},), "defaults")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_role_profile_provenance
    """
    raise NotImplementedError("contract stub: build_role_profile_provenance")


@pre(lambda config, source_label: isinstance(config, Mapping) and bool(config.keys()) and bool(source_label.strip()))
@post(lambda result: isinstance(result, Mapping) and list(result.keys()) == sorted(result.keys()))
def build_drive_config_provenance(config: Mapping[str, object], source_label: str) -> Mapping[str, str]:
    """Return sorted drive-config provenance keys.
    
    >>> build_drive_config_provenance({"runner": "opencode"}, "defaults")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_drive_config_provenance
    """
    raise NotImplementedError("contract stub: build_drive_config_provenance")
