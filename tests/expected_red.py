#!/usr/bin/env -S uv run python
"""Helpers for durable intentional-red test metadata.

This module centralizes the marker shape used by ``*_red.py`` tests so future
implementation phases can either flip, retire, or explicitly preserve each
intentional-red surface with rationale.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Final

import pytest

EXPECTED_RED_LIFECYCLE: Final = "must_flip_or_retire_or_explicitly_preserve_with_rationale"
EXPECTED_RED_REQUIRED_FIELDS: Final = ("owner", "rationale", "lifecycle")
ADMITTED_MUTATION_CHECK_ID: Final = "admitted_mutation_expected_red"
_OBSERVATION_BYTE_LIMIT: Final = 48 * 1024


def expected_red_module(*, owner: str, rationale: str) -> pytest.MarkDecorator:
    """Return the canonical module-level marker for intentional-red tests."""

    return pytest.mark.expected_red(
        owner=owner,
        rationale=rationale,
        lifecycle=EXPECTED_RED_LIFECYCLE,
    )


def validate_expected_red_marker(item: pytest.Item) -> str | None:
    """Validate required expected-red metadata for collected red-test items."""

    path = Path(str(item.fspath))
    if not path.name.endswith("_red.py"):
        return None

    marker = item.get_closest_marker("expected_red")
    if marker is None:
        return (
            f"{path}: missing @pytest.mark.expected_red metadata; add "
            "pytestmark = expected_red_module(...)"
        )

    missing = [field for field in EXPECTED_RED_REQUIRED_FIELDS if not marker.kwargs.get(field)]
    if missing:
        return f"{path}: expected_red marker missing required field(s): {', '.join(missing)}"

    lifecycle = marker.kwargs.get("lifecycle")
    if lifecycle != EXPECTED_RED_LIFECYCLE:
        return (
            f"{path}: expected_red lifecycle must be {EXPECTED_RED_LIFECYCLE!r}, got {lifecycle!r}"
        )

    return None


class _PytestExecutionObserver:
    """Capture pytest's collected and actually started node IDs."""

    def __init__(self) -> None:
        self.selected_ids: list[str] = []
        self.executed_ids: list[str] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Record pytest's final collection in its native order."""

        self.selected_ids = [item.nodeid for item in session.items]

    def pytest_runtest_logstart(
        self,
        nodeid: str,
        location: tuple[str, int | None, str],
    ) -> None:
        """Record each test body that pytest starts."""

        del location
        self.executed_ids.append(nodeid)


def _invoke_pytest(args: list[str]) -> tuple[int, _PytestExecutionObserver, str, str]:
    """Invoke the installed pytest host and retain its terminal streams."""

    observer = _PytestExecutionObserver()
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = int(pytest.main(args, plugins=[observer]))
    return exit_code, observer, stdout.getvalue(), stderr.getvalue()


def _emit(payload: dict[str, object]) -> None:
    """Emit one compact selected-execution protocol envelope."""

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _identity_digest(identities: list[str]) -> str:
    """Hash the canonical identity envelope expected by the production Gate."""

    encoded = json.dumps(
        {"identities": identities},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8", "surrogateescape")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _bounded_observations(*streams: str, identities: list[str]) -> list[str]:
    """Retain unique failure fingerprints without overflowing the evidence envelope."""

    observations: list[str] = []
    seen: set[str] = set()
    used = 0
    for value in [*identities, *(line.strip() for stream in streams for line in stream.splitlines())]:
        if not value or value in seen:
            continue
        encoded_size = len(value.encode("utf-8", "surrogateescape"))
        if used + encoded_size > _OBSERVATION_BYTE_LIMIT:
            break
        seen.add(value)
        observations.append(value)
        used += encoded_size
    return observations


def _copy_terminal_output(stdout: str, stderr: str) -> None:
    """Preserve pytest diagnostics outside the machine-readable stdout stream."""

    if stdout:
        print(stdout, file=sys.stderr, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")


def main(argv: list[str] | None = None) -> int:
    """Collect or run exact pytest node IDs for selected-execution evidence."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[0] not in {"select", "run"} or args[1] != "pytest":
        print(
            "usage: expected_red.py select|run pytest <node-id>...",
            file=sys.stderr,
        )
        return 2

    mode = args[0]
    requested_ids = args[2:]
    collection_code, collection, collection_stdout, collection_stderr = _invoke_pytest(
        ["--collect-only", "-q", *requested_ids]
    )
    selected_ids = sorted(collection.selected_ids)
    _emit(
        {
            "schema_version": "vectl.check.selection.v1",
            "check_id": ADMITTED_MUTATION_CHECK_ID,
            "identities": selected_ids,
            "digest": _identity_digest(selected_ids),
        }
    )
    if collection_code != 0:
        _copy_terminal_output(collection_stdout, collection_stderr)
        return collection_code
    if mode == "select":
        return 0

    run_code, execution, run_stdout, run_stderr = _invoke_pytest(requested_ids)
    _copy_terminal_output(run_stdout, run_stderr)
    executed_ids = sorted(execution.executed_ids)
    _emit(
        {
            "schema_version": "vectl.check.evidence.v1",
            "check_id": ADMITTED_MUTATION_CHECK_ID,
            "selected_ids": selected_ids,
            "executed_ids": executed_ids,
            "exit_code": run_code,
            "outcome": "green" if run_code == 0 else "red",
            "observations": _bounded_observations(
                run_stdout,
                run_stderr,
                identities=executed_ids,
            ),
            "artifacts": [],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
