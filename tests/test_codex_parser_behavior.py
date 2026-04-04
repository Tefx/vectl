"""Legacy codex parser tests retired with vectl.driver removal.

Authority: driver package was removed; orchestration contracts are now the active
runtime/testing surface in this repository.
"""

from vectl.orchestration.contracts import ExecutionResult


def test_execution_result_status_contract_covers_runtime_surface() -> None:
    result = ExecutionResult(
        step_id="phase.step",
        status="success",
        output_summary="ok",
    )
    assert result.status == "success"
