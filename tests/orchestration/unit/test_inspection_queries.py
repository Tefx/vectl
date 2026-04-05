from __future__ import annotations

from vectl.orchestration.inspection_queries import InspectQuery, RunsQueryImpl
from vectl.orchestration.run_store import CaseIndexEntry, RunRecord


class _FakeInspectionBoundary:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._records = (
            RunRecord(run_id="run-2", step_id="core.ready", status="running", updated_at=2.0),
            RunRecord(run_id="run-1", step_id="core.ready", status="pending", updated_at=1.0),
        )

    def latest_records(self) -> tuple[RunRecord, ...]:
        self.calls.append("latest_records")
        return self._records

    def all_for_step(self, step_id: str) -> tuple[RunRecord, ...]:
        self.calls.append(f"all_for_step:{step_id}")
        return tuple(record for record in self._records if record.step_id == step_id)

    def by_status(self, status: str) -> tuple[RunRecord, ...]:
        self.calls.append(f"by_status:{status}")
        return tuple(record for record in self._records if record.status == status)

    def latest_for_step(self, step_id: str) -> RunRecord | None:
        self.calls.append(f"latest_for_step:{step_id}")
        matches = [record for record in self._records if record.step_id == step_id]
        if not matches:
            return None
        return max(matches, key=lambda item: (item.updated_at or 0.0, item.run_id))

    def latest_cases(self, *, include_removed: bool = True) -> tuple[CaseIndexEntry, ...]:
        self.calls.append(f"latest_cases:{include_removed}")
        return ()

    def cases_for_run(self, run_id: str) -> tuple[CaseIndexEntry, ...]:
        self.calls.append(f"cases_for_run:{run_id}")
        return ()


def test_runs_query_reads_through_explicit_inspection_boundary() -> None:
    boundary = _FakeInspectionBoundary()
    view = RunsQueryImpl(boundary).query(InspectQuery(step_id="core.ready", limit=10, offset=0))

    assert view.runs == ("run-2", "run-1")
    assert view.total_count == 2
    assert "all_for_step:core.ready" in boundary.calls
