"""Public compatibility facade for orchestration configuration surfaces.

The implementation lives in ``vectl.orchestration._config_impl`` so this
stable import path can remain small while preserving historical monkeypatch and
private-helper compatibility for legacy importers.
"""

from __future__ import annotations

from vectl.orchestration import _config_impl as _impl

for _name, _value in vars(_impl).items():
    if _name in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}:
        continue
    globals()[_name] = _value

def load_orchestration_config(plan_path=None):
    """Load config while preserving facade-level monkeypatch compatibility."""
    _impl.USER_CONFIG_DIR = globals().get("USER_CONFIG_DIR", _impl.USER_CONFIG_DIR)
    return _impl.load_orchestration_config(plan_path=plan_path)

del _name, _value
