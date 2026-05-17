"""Filesystem and process-environment reads for prompt artifact recovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeAliasType, TypeVar

from vectl.orchestration.contracts import PromptArtifactPaths

_T = TypeVar("_T")
_E = TypeVar("_E")


@dataclass(frozen=True)
class Success(Generic[_T]):
    """Successful shell boundary result."""

    value: _T


@dataclass(frozen=True)
class Failure(Generic[_E]):
    """Failed shell boundary result."""

    error: _E


Result = TypeAliasType("Result", Any, type_params=(_T, _E))


@dataclass(frozen=True)
class RecoveryPromptArtifactRead:
    """Prompt artifact content read from the filesystem for recovery validation."""

    paths: PromptArtifactPaths
    bundle_data: dict[str, object]
    prompt_content: str


def read_process_environment() -> Result[dict[str, str], OSError]:
    """Read the ambient process environment at the shell boundary."""
    try:
        return Success(dict(os.environ))
    except OSError as exc:
        return Failure(exc)


def read_prompt_bundle_json(bundle_path: Path) -> Result[dict[str, object], str]:
    """Read and decode a prompt bundle JSON object."""
    if not bundle_path.exists():
        return Failure(f"prompt_bundle.json not found at {bundle_path}")

    try:
        raw_bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Failure(f"prompt_bundle.json is not valid JSON: {exc}")

    if not isinstance(raw_bundle_data, dict):
        return Failure("prompt_bundle.json is not a JSON object")

    return Success(dict(raw_bundle_data))


def read_runner_prompt_text(prompt_path: Path) -> Result[str, str]:
    """Read a runner prompt Markdown file."""
    if not prompt_path.exists():
        return Failure(f"runner_prompt.md not found at {prompt_path}")

    return Success(prompt_path.read_text(encoding="utf-8"))


def read_recovery_prompt_artifacts(
    *,
    paths: PromptArtifactPaths,
) -> Result[RecoveryPromptArtifactRead, str]:
    """Read prompt recovery artifacts while preserving historical failure reasons."""
    bundle_result = read_prompt_bundle_json(Path(paths.prompt_bundle_path))
    if isinstance(bundle_result, Failure):
        return Failure(bundle_result.error)

    prompt_result = read_runner_prompt_text(Path(paths.runner_prompt_path))
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.error)

    return Success(
        RecoveryPromptArtifactRead(
            paths=paths,
            bundle_data=bundle_result.value,
            prompt_content=prompt_result.value,
        )
    )
