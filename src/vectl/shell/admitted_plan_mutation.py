"""Exact-byte Plan mutation admission subprocess boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast

from returns.result import Result

from vectl.models import PlanIOError

_ADMISSION_ENV_FIELDS = (
    "VECTL_PLAN_ADMISSION_EXECUTABLE",
    "VECTL_PLAN_ADMISSION_EXECUTABLE_SHA256",
    "VECTL_PLAN_ADMISSION_TIMEOUT_SECONDS",
)
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_VERDICT_BYTES = 16 * 1024
_MAX_DIAGNOSTIC_BYTES = 8 * 1024
_MAX_REJECTION_REASON_CODEPOINTS = 1024
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w:])/(?:[^\s\x00-\x1f\"'<>]+)")
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)\bauthorization\b\s*[:=]\s*(?:(?:bearer|basic|digest)\s+)?"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|credential|password|passwd|secret|token)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SECRET_TOKEN_PATTERN = re.compile(
    r"(?i)\S*(?:bearer|credential|password|passwd|private[_-]?key|api[_-]?key|secret|token)\S*"
)
_OPAQUE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Fa-f0-9]{24,}|[A-Za-z0-9_-]{32,}|"
    r"[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){2})(?![A-Za-z0-9])"
)
_PLAN_FRAGMENT_PATTERN = re.compile(r"[\w@.+/-]{2,}")

AdmissionState = Literal["not_configured", "configured", "configuration_invalid"]


@dataclass(frozen=True)
class ExecutableIdentity:
    """Startup identity retained for one configured admission executable."""

    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True)
class PlanAdmissionConfig:
    """Startup-frozen state for the optional Plan admission adapter."""

    state: AdmissionState
    executable: Path | None = None
    executable_sha256: str | None = None
    timeout_seconds: int | None = None
    executable_identity: ExecutableIdentity | None = None


class PlanAdmissionError(PlanIOError):
    """Bounded typed failure raised before an admitted Plan write."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason[:_MAX_REJECTION_REASON_CODEPOINTS]
        super().__init__(f"{code}: {self.reason}")


def sha256_digest(content: bytes) -> str | Result[str, str]:
    """Return the RFC-formatted SHA-256 digest for exact bytes."""
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _inspect_executable(path: Path) -> ExecutableIdentity | Result[ExecutableIdentity, str]:
    """Read one regular executable's identity and startup byte digest."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise OSError("admission executable must be a regular executable file")
    return ExecutableIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        sha256=cast(str, sha256_digest(path.read_bytes())),
    )


# @shell_complexity: Complete-or-invalid tuple parsing is one startup operation.
def freeze_plan_admission_config(
    environment: Mapping[str, str],
) -> PlanAdmissionConfig | Result[PlanAdmissionConfig, str]:
    """Validate and freeze the exact optional adapter tuple for this process."""
    values = tuple(environment.get(field) for field in _ADMISSION_ENV_FIELDS)
    if values == (None, None, None):
        return PlanAdmissionConfig(state="not_configured")
    if any(value is None or value == "" for value in values):
        return PlanAdmissionConfig(state="configuration_invalid")

    executable_value, digest_value, timeout_value = values
    assert executable_value is not None
    assert digest_value is not None
    assert timeout_value is not None

    executable = Path(executable_value)
    if not executable.is_absolute() or _SHA256_PATTERN.fullmatch(digest_value) is None:
        return PlanAdmissionConfig(state="configuration_invalid")
    try:
        timeout_seconds = int(timeout_value, 10)
    except ValueError:
        return PlanAdmissionConfig(state="configuration_invalid")
    if str(timeout_seconds) != timeout_value or not 1 <= timeout_seconds <= 300:
        return PlanAdmissionConfig(state="configuration_invalid")

    try:
        identity = cast(ExecutableIdentity, _inspect_executable(executable))
    except OSError:
        return PlanAdmissionConfig(state="configuration_invalid")
    if identity.sha256 != digest_value:
        return PlanAdmissionConfig(state="configuration_invalid")

    return PlanAdmissionConfig(
        state="configured",
        executable=executable,
        executable_sha256=digest_value,
        timeout_seconds=timeout_seconds,
        executable_identity=identity,
    )


# Configuration is intentionally frozen at module import (CLI/MCP process startup).
PLAN_ADMISSION_CONFIG = freeze_plan_admission_config(os.environ)


# @shell_complexity: Strict verdict parsing keeps all schema failures fail-closed.
def _parse_admission_verdict(content: bytes) -> dict[str, str] | Result[dict[str, str], str]:
    """Parse the strict bounded V1 adapter verdict object."""

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PlanAdmissionError(
                    "malformed_verdict",
                    "admission verdict contains a duplicate key",
                )
            result[key] = value
        return result

    try:
        decoded = content.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=reject_duplicate_keys)
    except PlanAdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanAdmissionError(
            "malformed_verdict",
            "admission verdict is not UTF-8 JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise PlanAdmissionError(
            "malformed_verdict",
            "admission verdict is not a strict object",
        )

    schema_version = payload.get("schema_version")
    decision = payload.get("decision")
    candidate_sha256 = payload.get("candidate_sha256")
    if schema_version != "vectl.plan-mutation-admission.v1":
        raise PlanAdmissionError("malformed_verdict", "admission verdict schema is invalid")
    if not isinstance(decision, str) or decision not in {"approve", "reject"}:
        raise PlanAdmissionError("malformed_verdict", "admission verdict decision is invalid")
    if not isinstance(candidate_sha256, str) or _SHA256_PATTERN.fullmatch(candidate_sha256) is None:
        raise PlanAdmissionError("malformed_verdict", "admission verdict digest is invalid")

    expected_keys = {"schema_version", "decision", "candidate_sha256"}
    if decision == "reject":
        expected_keys.add("reason")
        reason = payload.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.strip()) > _MAX_REJECTION_REASON_CODEPOINTS
        ):
            raise PlanAdmissionError("malformed_verdict", "admission rejection reason is invalid")
    if set(payload) != expected_keys:
        raise PlanAdmissionError("malformed_verdict", "admission verdict fields are invalid")

    parsed = {
        "schema_version": schema_version,
        "decision": decision,
        "candidate_sha256": candidate_sha256,
    }
    if decision == "reject":
        parsed["reason"] = str(payload["reason"]).strip()
    return parsed


def _repository_root(plan_path: Path) -> Path | Result[Path, str]:
    """Find the containing Git root without invoking Git before admission."""
    current = plan_path.parent.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _input_file_identity(
    path: Path,
) -> (
    tuple[int, int, int, int, int, int, str]
    | Result[tuple[int, int, int, int, int, int, str], str]
):
    """Return immutable-input identity including exact bytes."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise PlanAdmissionError(
            "candidate_identity_mismatch",
            "admission Plan input is no longer a regular file",
        )
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        cast(str, sha256_digest(path.read_bytes())),
    )


# @shell_complexity: Platform temp-parent discovery must preserve native candidates.
def _admission_workspace_candidates() -> (
    tuple[str | bytes, ...] | Result[tuple[str | bytes, ...], str]
):
    """Return temporary-parent candidates without probing them by writing files."""
    if tempfile.tempdir is not None:
        return (tempfile.tempdir,)
    for field in ("TMPDIR", "TEMP", "TMP"):
        if value := os.environ.get(field):
            return (value,)
    if os.name == "nt":
        home = os.path.expanduser("~")
        system_root = os.environ.get("SYSTEMROOT")
        candidates = [os.path.join(home, "AppData", "Local", "Temp")]
        if system_root:
            candidates.append(os.path.join(system_root, "Temp"))
        candidates.extend((r"C:\TEMP", r"C:\TMP", r"\TEMP", r"\TMP"))
    else:
        candidates = ["/tmp", "/var/tmp", "/usr/tmp"]
    candidates.append(os.getcwd())
    return tuple(candidates)


# @shell_complexity: Validation rejects all internal or unusable parents pre-creation.
def _admission_workspace_parent(
    repository_root: Path,
) -> Path | Result[Path, str]:
    """Resolve and validate an external temporary parent without creating state."""
    resolved_repository = repository_root.resolve()
    try:
        candidates = cast(
            tuple[str | bytes, ...],
            _admission_workspace_candidates(),
        )
    except OSError as exc:
        raise PlanAdmissionError(
            "adapter_crash",
            "repository-external admission workspace is unavailable",
        ) from exc
    for candidate in candidates:
        try:
            temporary_parent = Path(os.fsdecode(candidate)).resolve()
            temporary_parent.relative_to(resolved_repository)
        except ValueError:
            pass
        except OSError:
            continue
        else:
            continue

        try:
            parent_metadata = temporary_parent.stat()
        except OSError:
            continue
        if stat.S_ISDIR(parent_metadata.st_mode) and os.access(
            temporary_parent,
            os.W_OK | os.X_OK,
        ):
            return temporary_parent

    raise PlanAdmissionError(
        "adapter_crash",
        "repository-external admission workspace is unavailable",
    )


def _write_private_file(path: Path, content: bytes) -> None | Result[None, str]:
    """Create one exact mode-0600 adapter file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    finally:
        os.close(descriptor)
    return None


def _drain_bounded(stream: BinaryIO, retained: bytearray) -> None:
    """Drain one adapter pipe while retaining at most 8 KiB."""
    while chunk := stream.read(8192):
        remaining = _MAX_DIAGNOSTIC_BYTES - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])


# @shell_complexity: Concurrent drains avoid subprocess pipe deadlock under bounded retention.
def _run_adapter(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> tuple[int, bytes, bytes] | Result[tuple[int, bytes, bytes], str]:
    """Run the fixed adapter argv and retain bounded stdout/stderr diagnostics."""
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    readers = (
        threading.Thread(target=_drain_bounded, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain_bounded, args=(process.stderr, stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1.0)
    return returncode, bytes(stdout), bytes(stderr)


def _sanitize_diagnostic(
    content: bytes | str,
    temporary_path: Path,
    *,
    protected_contents: tuple[bytes, ...],
) -> str | Result[str, str]:
    """Bound one diagnostic after removing paths, secrets, and Plan-derived fragments."""
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    protected_text = tuple(
        protected.decode("utf-8", errors="replace") for protected in protected_contents
    )

    def redact_plan_fragment(match: re.Match[str]) -> str:
        fragment = match.group(0)
        if any(fragment in protected for protected in protected_text):
            return "<plan-content>"
        return fragment

    text = text.replace(str(temporary_path), "<admission-temp>")
    text = _AUTHORIZATION_VALUE_PATTERN.sub("<redacted>", text)
    text = _ABSOLUTE_PATH_PATTERN.sub("<path>", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub("<redacted>", text)
    text = _SECRET_TOKEN_PATTERN.sub("<redacted>", text)
    text = _OPAQUE_TOKEN_PATTERN.sub("<redacted>", text)
    text = _PLAN_FRAGMENT_PATTERN.sub(redact_plan_fragment, text)
    sanitized = "".join(character if character.isprintable() else " " for character in text)
    return " ".join(sanitized.split())[:_MAX_REJECTION_REASON_CODEPOINTS]


# @shell_complexity: Invocation classification enforces all zero-write failure codes.
def admit_candidate_bytes(
    *,
    config: PlanAdmissionConfig,
    plan_path: Path,
    base_bytes: bytes,
    candidate_bytes: bytes,
    candidate_sha256: str,
) -> (
    Literal["not_configured", "approved"]
    | Result[Literal["not_configured", "approved"], str]
):
    """Apply the startup-frozen adapter decision before any repository write."""
    if config.state == "not_configured":
        return "not_configured"
    if config.state != "configured":
        raise PlanAdmissionError(
            "configuration_invalid",
            "plan admission configuration is incomplete or invalid",
        )

    executable = config.executable
    timeout_seconds = config.timeout_seconds
    startup_identity = config.executable_identity
    if executable is None or timeout_seconds is None or startup_identity is None:
        raise PlanAdmissionError(
            "configuration_invalid",
            "plan admission configuration is incomplete or invalid",
        )

    repository_root = cast(Path, _repository_root(plan_path))
    temporary_parent = cast(Path, _admission_workspace_parent(repository_root))
    try:
        workspace = tempfile.TemporaryDirectory(
            prefix="vectl-plan-admission-",
            dir=temporary_parent,
        )
    except OSError as exc:
        raise PlanAdmissionError(
            "adapter_crash",
            "repository-external admission workspace is unavailable",
        ) from exc

    with workspace as temporary:
        temporary_path = Path(temporary).resolve()
        base_path = temporary_path / "base.plan.yaml"
        candidate_path = temporary_path / "candidate.plan.yaml"
        verdict_path = temporary_path / "verdict.json"
        try:
            _write_private_file(base_path, base_bytes)
            _write_private_file(candidate_path, candidate_bytes)
            _write_private_file(verdict_path, b"")
        except OSError as exc:
            raise PlanAdmissionError(
                "adapter_crash",
                "admission private file setup or write failed",
            ) from exc
        try:
            base_identity = _input_file_identity(base_path)
            candidate_identity = _input_file_identity(candidate_path)
            verdict_identity = verdict_path.lstat()
        except (OSError, PlanAdmissionError) as exc:
            raise PlanAdmissionError(
                "adapter_crash",
                "admission private file setup or write failed",
            ) from exc

        try:
            current_identity = _inspect_executable(executable)
        except OSError as exc:
            raise PlanAdmissionError(
                "executable_identity_mismatch",
                "admission executable identity changed after startup",
            ) from exc
        if current_identity != startup_identity:
            raise PlanAdmissionError(
                "executable_identity_mismatch",
                "admission executable identity changed after startup",
            )

        command = [
            str(executable),
            "--base",
            str(base_path),
            "--candidate",
            str(candidate_path),
            "--verdict",
            str(verdict_path),
        ]
        try:
            returncode, stdout, stderr = cast(
                tuple[int, bytes, bytes],
                _run_adapter(
                    command,
                    cwd=repository_root,
                    timeout_seconds=timeout_seconds,
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise PlanAdmissionError(
                "adapter_timeout",
                "admission adapter exceeded its configured timeout",
            ) from exc
        except OSError as exc:
            raise PlanAdmissionError(
                "adapter_crash",
                "admission adapter could not be launched",
            ) from exc

        try:
            inputs_unchanged = (
                _input_file_identity(base_path) == base_identity
                and _input_file_identity(candidate_path) == candidate_identity
            )
        except (OSError, PlanAdmissionError):
            inputs_unchanged = False
        if not inputs_unchanged:
            raise PlanAdmissionError(
                "candidate_identity_mismatch",
                "admission adapter changed an immutable Plan input",
            )
        if returncode != 0:
            diagnostics = _sanitize_diagnostic(
                stderr or stdout,
                temporary_path,
                protected_contents=(base_bytes, candidate_bytes),
            )
            reason = f"admission adapter exited with status {returncode}"
            if diagnostics:
                reason = f"{reason}: {diagnostics}"
            raise PlanAdmissionError("adapter_crash", reason)

        try:
            current_verdict_identity = verdict_path.lstat()
        except OSError as exc:
            raise PlanAdmissionError(
                "malformed_verdict",
                "admission verdict file is unavailable",
            ) from exc
        if (
            not stat.S_ISREG(current_verdict_identity.st_mode)
            or current_verdict_identity.st_dev != verdict_identity.st_dev
            or current_verdict_identity.st_ino != verdict_identity.st_ino
        ):
            raise PlanAdmissionError(
                "malformed_verdict",
                "admission verdict file identity changed",
            )
        if current_verdict_identity.st_size > _MAX_VERDICT_BYTES:
            raise PlanAdmissionError(
                "adapter_output_too_large",
                "admission verdict exceeds 16 KiB",
            )

        try:
            verdict_bytes = verdict_path.read_bytes()
        except OSError as exc:
            raise PlanAdmissionError(
                "malformed_verdict",
                "admission verdict file is unavailable",
            ) from exc
        verdict = cast(
            dict[str, str],
            _parse_admission_verdict(verdict_bytes),
        )
        if verdict["candidate_sha256"] != candidate_sha256:
            raise PlanAdmissionError(
                "candidate_identity_mismatch",
                "admission verdict does not bind the candidate_sha256",
            )
        if verdict["decision"] == "reject":
            raise PlanAdmissionError(
                "rejected",
                cast(
                    str,
                    _sanitize_diagnostic(
                        verdict["reason"],
                        temporary_path,
                        protected_contents=(base_bytes, candidate_bytes),
                    ),
                ),
            )
        return "approved"
