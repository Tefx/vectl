"""Legacy gemini parser tests retired with vectl.driver removal.

Authority: parser-specific assertions tied to removed driver APIs are obsolete.
"""

from vectl.orchestration.contracts import RuntimeSnapshot


def test_runtime_snapshot_contract_is_available() -> None:
    snapshot = RuntimeSnapshot(active_workspaces=(), active_executions=(), stalled_executions=())
    assert snapshot.active_workspaces == ()
