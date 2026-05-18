"""Pure config serialization/default/env transforms.

Decision row: ``src/vectl/orchestration/config.py`` structural Core extraction.

>>> serialize_tool_allowlist(("read", "write"))
['read', 'write']

>>> deep_update_config({"runner": "opencode"}, {"": "bad"})  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

import yaml
from deal import post, pre


_DEFAULTS: Mapping[str, object] = {
    "runtime.default_runner": "codex",
    "runtime.artifact_root": ".vectl/runs",
    "runtime.workspace_root": ".vectl/workspaces",
    "runtime.isolation_default": "default",
    "runtime.cleanup_policy": "on-success",
    "dispatch.default_role_id": "python-executor",
    "control.idle_poll_interval_ms": 1000,
    "control.max_resolution_attempts_per_case": 1,
    "control.action_ack_timeout_seconds": 5.0,
    "resolver.enabled": True,
    "resolver.default_role_id": "blocked-case-coordinator",
    "resolver.timeout_seconds": 600.0,
    "resolver.max_tool_calls_per_invocation": 100,
    "resolver.max_tool_argument_bytes": 65536,
    "continuity.resume_enabled": True,
    "continuity.stale_artifact_policy": "quarantine",
    "continuity.replay_safety": "conservative",
    "observability.events_jsonl": True,
    "observability.text_log": True,
    "observability.projected_state": True,
    "observability.heartbeat_stale_threshold_seconds": 120,
    "observability.per_step_artifacts": True,
    "observability.per_case_artifacts": True,
    "observability.max_log_megabytes": 100,
    "observability.retention_days": 30,
    "operator.control_channel": "filesystem",
    "operator.default_output": "human",
    "operator.max_pending_actions": 100,
    "drive.max_parallelism": 4,
    "drive.collect_poll_interval_ms": 250,
    "drive.resolver_timeout_seconds": 300.0,
    "drive.planner_timeout_seconds": 300.0,
}


@pre(lambda allowlist: all(item.strip() for item in allowlist))
@post(lambda result: isinstance(result, list) and all(isinstance(item, str) and item for item in result))
def serialize_tool_allowlist(allowlist: Sequence[str]) -> list[str]:
    """Serialize a non-empty tool allowlist sequence to YAML-compatible strings.
    
    >>> serialize_tool_allowlist(("read",))
    ['read']
    """
    return [str(item) for item in allowlist]


@pre(lambda config: isinstance(config, Mapping) and bool(config.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def config_to_dict(config: Mapping[str, object]) -> Mapping[str, object]:
    """Serialize a frozen config-shaped mapping to public YAML schema data.
    
    >>> config_to_dict({"runner": "opencode"})["runner"]
    'opencode'
    """
    return _clone_mapping(config)


@pre(lambda yaml_payload: bool(yaml_payload.strip()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def parse_config_yaml_payload(yaml_payload: str) -> Mapping[str, object]:
    """Parse already-read YAML text into public config schema data.
    
    >>> parse_config_yaml_payload("runner: opencode")["runner"]
    'opencode'
    """
    parsed = yaml.safe_load(yaml_payload) or {}
    if not isinstance(parsed, Mapping):
        raise ValueError("config YAML payload must parse to a mapping")
    return _clone_mapping(parsed)


@pre(lambda config, env_values: isinstance(config, Mapping) and all(isinstance(key, str) and key for key in env_values.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def apply_env_overrides_to_config(
    config: Mapping[str, object],
    env_values: Mapping[str, str],
) -> Mapping[str, object]:
    """Apply already-flattened environment values without reading the environment.
    
    >>> apply_env_overrides_to_config({"orchestration": {"runtime": {}}}, {"runtime.default_runner": "opencode"})["orchestration"]["runtime"]["default_runner"]
    'opencode'
    """
    updates: dict[str, object] = {}
    for key, value in env_values.items():
        _set_dotted(updates, _normalize_env_key(key), _coerce_env_value(_normalize_env_key(key), value))
    return deep_update_config(config, {"orchestration": updates})


@pre(lambda key, defaults: bool(key.strip()) and all(isinstance(item_key, str) and item_key for item_key in defaults.keys()))
@post(lambda result: result is not None)
def get_config_default(key: str, defaults: Mapping[str, object]) -> object:
    """Return a configured default for a non-empty public config key.
    
    >>> get_config_default("runner", {"runner": "opencode"})
    'opencode'
    """
    return defaults.get(key, _DEFAULTS.get(key))


@pre(lambda base, update: all(isinstance(key, str) and key for key in base.keys()) and all(isinstance(key, str) and key for key in update.keys()))
@post(lambda result: isinstance(result, Mapping) and all(isinstance(key, str) and key for key in result.keys()))
def deep_update_config(base: Mapping[str, object], update: Mapping[str, object]) -> Mapping[str, object]:
    """Return merged config schema data without mutating Shell-owned inputs.
    
    >>> deep_update_config({"a": {"b": 1}}, {"a": {"c": 2}})
    {'a': {'b': 1, 'c': 2}}
    """
    result = _clone_mapping(base)
    _deep_merge(result, update)
    return result


@pre(lambda data: isinstance(data, Mapping) and all(isinstance(key, str) and key for key in data.keys()))
@post(lambda result: isinstance(result, Mapping) and bool(result.keys()))
def dict_to_config(data: Mapping[str, object]) -> Mapping[str, object]:
    """Convert public config schema data to a config-shaped DTO mapping.
    
    >>> dict_to_config({"runner": "opencode"})["runner"]
    'opencode'
    """
    return _clone_mapping(data)


@pre(lambda drive_config, source_label: isinstance(drive_config, Mapping) and bool(drive_config.keys()) and bool(source_label.strip()))
@post(lambda result: isinstance(result, Mapping) and "config" in result and "provenance" in result)
def freeze_drive_config_data(
    drive_config: Mapping[str, object],
    source_label: str,
) -> Mapping[str, object]:
    """Build frozen drive-config snapshot payload data.
    
    >>> freeze_drive_config_data({"runner": "opencode"}, "defaults")["provenance"]
    'defaults'
    """
    return {"config": _clone_mapping(drive_config), "provenance": source_label}


@pre(lambda data: isinstance(data, Mapping) and all(isinstance(key, str) and key for key in data.keys()))
@post(lambda result: isinstance(result, dict) and all(isinstance(key, str) and key for key in result.keys()))
def _clone_mapping(data: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, Mapping):
            result[str(key)] = _clone_mapping(value)
        elif isinstance(value, list):
            result[str(key)] = list(value)
        elif isinstance(value, tuple):
            result[str(key)] = list(value)
        else:
            result[str(key)] = value
    return result


@pre(lambda target, source: isinstance(target, dict) and isinstance(source, Mapping) and all(isinstance(key, str) and key for key in source.keys()))
@post(lambda result: result is None)
def _deep_merge(target: dict[str, object], source: Mapping[str, object]) -> None:
    for key, value in source.items():
        key_text = str(key)
        if isinstance(value, Mapping) and isinstance(target.get(key_text), dict):
            nested = target[key_text]
            assert isinstance(nested, dict)
            _deep_merge(nested, value)
        elif isinstance(value, Mapping):
            target[key_text] = _clone_mapping(value)
        else:
            target[key_text] = value


@pre(lambda target, dotted_key, value: isinstance(target, dict) and bool(dotted_key.strip()) and isinstance(value, object))
@post(lambda result: result is None)
def _set_dotted(target: dict[str, object], dotted_key: str, value: object) -> None:
    current = target
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        existing = current.setdefault(part, {})
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = value


@pre(lambda key: bool(key.strip()))
@post(lambda result: bool(result.strip()))
def _normalize_env_key(key: str) -> str:
    if key.startswith("VECTL_ORCH_"):
        key = key[len("VECTL_ORCH_") :].lower()
    if "." in key:
        return key
    aliases = {
        "runtime_artifact_root": "runtime.artifact_root",
        "runtime_workspace_root": "runtime.workspace_root",
        "runtime_default_runner": "runtime.default_runner",
        "dispatch_default_role_id": "dispatch.default_role_id",
        "resolver_timeout_seconds": "resolver.timeout_seconds",
    }
    return aliases.get(key, key.replace("_", "."))


@pre(lambda key, value: bool(key.strip()) and isinstance(value, str))
@post(lambda result: result is not None)
def _coerce_env_value(key: str, value: str) -> object:
    default = _DEFAULTS.get(key)
    if isinstance(default, bool):
        return value.lower() in ("true", "1", "yes")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value
