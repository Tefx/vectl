"""Legacy extended-runner protocol tests retired.

Authority: runner protocol under ``vectl.driver`` no longer exists in this repo.
"""

from vectl.orchestration.contracts import ExecutionRequest


def test_execution_request_contract_is_current_dispatch_input() -> None:
    request = ExecutionRequest(
        step_id="phase.step",
        role="python-executor",
        runner="claude",
        work_refs=(),
        session_id=None,
    )
    assert request.runner == "claude"
