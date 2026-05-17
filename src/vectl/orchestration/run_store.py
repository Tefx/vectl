"""Public compatibility facade for orchestration run-store surfaces.

The implementation lives in ``vectl.orchestration._run_store_impl`` so this
stable import path can remain small while preserving historical monkeypatch and
private-helper compatibility for legacy importers.
"""

from __future__ import annotations

import sys as _sys

from vectl.orchestration import _run_store_impl as _impl

_sys.modules[__name__] = _impl
