"""Legacy codex resume tests retired with vectl.driver removal.

Authority: runtime lifecycle now lives under vectl.orchestration.runtime.
"""

from vectl.orchestration.runtime import Runtime


def test_runtime_surface_replaces_driver_resume_surface() -> None:
    runtime = Runtime()
    assert runtime.snapshot().active_executions == ()
