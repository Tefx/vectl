"""Contracts for pure config serialization/default/env transforms.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> serialize_tool_allowlist(("read", "write"))
Traceback (most recent call last):
...
NotImplementedError: contract stub: serialize_tool_allowlist

>>> deep_update_config({"runner": "opencode"}, {"": "bad"})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda allowlist: all(item.strip() for item in allowlist))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def serialize_tool_allowlist(allowlist: Sequence[str]) -> list[str]:
    """Serialize a non-empty tool allowlist sequence to YAML-compatible strings.
    
    >>> serialize_tool_allowlist(("read",))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: serialize_tool_allowlist
    """
    raise NotImplementedError("contract stub: serialize_tool_allowlist")


@pre(lambda config: isinstance(config, Mapping) and bool(config.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def config_to_dict(config: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize a frozen config-shaped mapping to public YAML schema data.
    
    >>> config_to_dict({"runner": "opencode"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: config_to_dict
    """
    raise NotImplementedError("contract stub: config_to_dict")


@pre(lambda yaml_payload: bool(yaml_payload.strip()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def parse_config_yaml_payload(yaml_payload: str) -> Mapping[str, object]:
    """Parse already-read YAML text into public config schema data.
    
    >>> parse_config_yaml_payload("runner: opencode")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: parse_config_yaml_payload
    """
    raise NotImplementedError("contract stub: parse_config_yaml_payload")


@pre(lambda config, env_values: isinstance(config, Mapping) and all(isinstance(key, str) and key for key in env_values.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def apply_env_overrides_to_config(
    config: Mapping[str, object],
    env_values: Mapping[str, str],
) -> Mapping[str, object]:
    """Apply already-flattened environment values without reading the environment.
    
    >>> apply_env_overrides_to_config({"runner": "opencode"}, {"VECTL_RUNNER": "opencode"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: apply_env_overrides_to_config
    """
    raise NotImplementedError("contract stub: apply_env_overrides_to_config")


@pre(lambda key, defaults: bool(key.strip()) and all(isinstance(item_key, str) and item_key for item_key in defaults.keys()))
@post(lambda result: result is not None)
def get_config_default(key: str, defaults: Mapping[str, object]) -> object:
    """Return a configured default for a non-empty public config key.
    
    >>> get_config_default("runner", {"runner": "opencode"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: get_config_default
    """
    raise NotImplementedError("contract stub: get_config_default")


@pre(lambda base, update: all(isinstance(key, str) and key for key in base.keys()) and all(isinstance(key, str) and key for key in update.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def deep_update_config(base: Mapping[str, object], update: Mapping[str, object]) -> Mapping[str, object]:
    """Return merged config schema data without mutating Shell-owned inputs.
    
    >>> deep_update_config({"runner": "opencode"}, {"timeout": 1})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: deep_update_config
    """
    raise NotImplementedError("contract stub: deep_update_config")


@pre(lambda data: isinstance(data, Mapping) and all(isinstance(key, str) and key for key in data.keys()))
@post(lambda result: isinstance(result, Mapping) and bool(result.keys()))
def dict_to_config(data: Mapping[str, object]) -> Mapping[str, object]:
    """Convert public config schema data to a config-shaped DTO mapping.
    
    >>> dict_to_config({"runner": "opencode"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: dict_to_config
    """
    raise NotImplementedError("contract stub: dict_to_config")


@pre(lambda drive_config, source_label: isinstance(drive_config, Mapping) and bool(drive_config.keys()) and bool(source_label.strip()))
@post(lambda result: isinstance(result, Mapping) and "config" in result and "provenance" in result)
def freeze_drive_config_data(
    drive_config: Mapping[str, object],
    source_label: str,
) -> Mapping[str, object]:
    """Build frozen drive-config snapshot payload data.
    
    >>> freeze_drive_config_data({"runner": "opencode"}, "defaults")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: freeze_drive_config_data
    """
    raise NotImplementedError("contract stub: freeze_drive_config_data")
