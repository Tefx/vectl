"""Runner parser implementations and dispatch contract.

Responsibility: Parse runner output formats into RunnerResult.
Non-responsibility: Does NOT dispatch subprocesses (that is runner impl).

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.8
Blueprint Reference: DRIVER-BLUEPRINT.md Output Parsers (3 formats)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .errors import RunnerOutputError
from .types import RunnerResult, RunnerStatus

if TYPE_CHECKING:
    pass


def _parse_json_safely(text: str) -> tuple[dict | None, str]:
    """Attempt to parse JSON from possibly-malformed output.

    Returns (parsed_dict, error_message). On success, error_message is empty.
    """
    try:
        data = json.loads(text)
        return data, ""
    except json.JSONDecodeError as e:
        return None, f"JSON parse error at line {e.lineno} col {e.colno}: {e.msg}"


class ClaudeOutputParser:
    """Parse single JSON object from `claude -p --output-format json`.

    Output format (verified 2026-03-28):
        {
            "session_id": "uuid",
            "result": "text output",
            "structured_output": {...},  # When --json-schema used
            "subtype": "success" | "error",
            "total_cost_usd": 0.123,
            "usage": {"input_tokens": N, "output_tokens": N, ...}
        }

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8 (ClaudeRunner)
    Blueprint: DRIVER-BLUEPRINT.md Output Parsers (ClaudeOutputParser)
    """

    def parse(self, stdout: str, elapsed_seconds: float) -> RunnerResult:
        data, err = _parse_json_safely(stdout)
        if data is None:
            return RunnerResult(
                status=RunnerStatus.TRANSPORT_ERROR,
                session_id=None,
                output=stdout,
                elapsed_seconds=elapsed_seconds,
                exit_code=None,
            )

        # Extract structured_output if present (from --json-schema), else result
        raw_output = data.get("structured_output") or data.get("result", "")

        # Handle dict output from structured_output
        if isinstance(raw_output, dict):
            output = json.dumps(raw_output)
        else:
            output = str(raw_output)

        # Determine status from subtype
        subtype = data.get("subtype", "")
        if subtype == "success":
            status = RunnerStatus.SUCCESS
        else:
            status = RunnerStatus.FAIL

        return RunnerResult(
            status=status,
            session_id=data.get("session_id"),
            output=output,
            elapsed_seconds=elapsed_seconds,
            exit_code=0 if status == RunnerStatus.SUCCESS else 1,
            cost_usd=data.get("total_cost_usd"),
            tokens=data.get("usage"),
        )


class OpenCodeOutputParser:
    """Parse JSONL stream from `opencode run --format json`.

    Output format (verified 2026-03-28):
        {"type": "step_start", "sessionID": "ses_xxx", ...}
        {"type": "text", "part": {"text": "..."}}
        ...
        {"type": "step_finish", "part": {"status": "success", "tokens": {...}}}

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.8 (OpenCodeRunner)
    Blueprint: DRIVER-BLUEPRINT.md Output Parsers (OpenCodeOutputParser)
    """

    def parse(self, stdout: str, elapsed_seconds: float) -> RunnerResult:
        lines = stdout.strip().split("\n")
        events: list[dict] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # Skip malformed lines

        if not events:
            return RunnerResult(
                status=RunnerStatus.TRANSPORT_ERROR,
                session_id=None,
                output=stdout,
                elapsed_seconds=elapsed_seconds,
                exit_code=None,
            )

        # Extract session_id from first step_start
        session_id: str | None = None
        for event in events:
            if event.get("type") == "step_start":
                session_id = event.get("sessionID")
                break

        # Concatenate all text parts
        text_parts: list[str] = []
        for event in events:
            if event.get("type") == "text":
                part = event.get("part", {})
                text = part.get("text", "")
                if text:
                    text_parts.append(text)

        output = "\n".join(text_parts)

        # Determine status from step_finish
        status = RunnerStatus.FAIL
        tokens: dict | None = None
        for event in events:
            if event.get("type") == "step_finish":
                part = event.get("part", {})
                if part.get("status") == "success":
                    status = RunnerStatus.SUCCESS
                tokens = part.get("tokens")

        return RunnerResult(
            status=status,
            session_id=session_id,
            output=output,
            elapsed_seconds=elapsed_seconds,
            exit_code=0 if status == RunnerStatus.SUCCESS else 1,
            tokens=tokens,
        )


__all__ = [
    "ClaudeOutputParser",
    "OpenCodeOutputParser",
]
