"""Best-effort diagnostic hygiene for a trusted local admission adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from returns.result import Result

from vectl.core_admission_diagnostics import (
    DIAGNOSTIC_URL_PATTERN,
    redact_protected_scalar_values,
)

_MAX_RAW_DIAGNOSTIC_LENGTH = 8192
_MAX_REJECTION_REASON_CODEPOINTS = 1024
_UNAVAILABLE_DIAGNOSTIC = "admission diagnostic unavailable"
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)\bauthorization\b\s*[:=]\s*"
    r"(?:"
    r"(?:bearer|basic)\s+(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    r"|digest\s+[^;\r\n]+"
    r"|(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    r")"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|apikey|authorization|credential|password|passwd|"
    r"secret|token)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_OBVIOUS_SECRET_TOKEN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-]))"
    r"[A-Za-z0-9_-]*(?:credential|password|passwd|api[_-]?key|secret|token)"
    r"[A-Za-z0-9_-]*"
)
_OPAQUE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Fa-f0-9]{24,}|[A-Za-z0-9_-]{32,}|"
    r"[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){2})(?![A-Za-z0-9])"
)
_URL_SECRET_COMPONENT_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:authorization|bearer|credential|password|passwd|"
    r"private[_-]?key|privatekey|api[_-]?key|apikey|secret|token)"
    r"(?![A-Za-z0-9])"
)
_PLAN_FRAGMENT_PATTERN = re.compile(r"[\w@.+/*?-]+")
_DIAGNOSTIC_SUBJECT_PATTERN = re.compile(r"(?P<prefix>^|:\s)(?P<identity>[\w@.+/-]{2,})(?=:)")
_PROTECTED_DIAGNOSTIC_SUBJECT_PATTERN = re.compile(r"(?P<prefix>:\s)<plan-content>(?=:)")
_QUOTED_DIAGNOSTIC_VALUE_PATTERN = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"\r\n]+)(?P=quote)")
_STABLE_ERROR_CODE_PATTERN = re.compile(r"(?<![\w])\b[A-Z][A-Z0-9_]{2,}(?=:\s)")
_CONTRACT_FIELD_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:scope|claims|checks|bindings)(?:\.[A-Za-z_][\w-]*)+"
)
_VIOLATION_CLASS_PATTERN = re.compile(r"(?i)\b(?:must not be empty|is shadowed by)\b")
_FRAGMENT_BOUNDARY_PUNCTUATION = frozenset("_@.+/*?-")
_MIN_DISTINCTIVE_FRAGMENT_CODEPOINTS = 6


# @shell_complexity: Redaction order protects credentials before Plan matching.
def sanitize_admission_diagnostic(
    content: bytes | str,
    temporary_path: Path,
    *,
    protected_contents: tuple[bytes, ...],
) -> str | Result[str, str]:
    """Return bounded accidental-leak hygiene for trusted-adapter diagnostics."""
    if len(content) > _MAX_RAW_DIAGNOSTIC_LENGTH:
        return _UNAVAILABLE_DIAGNOSTIC
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    if len(text) > _MAX_RAW_DIAGNOSTIC_LENGTH or any(
        0xD800 <= ord(character) <= 0xDFFF for character in text
    ):
        return _UNAVAILABLE_DIAGNOSTIC

    protected_scalar_values: set[str] = set()

    def url_requires_redaction(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            decoded_authority = unquote(parsed.netloc, errors="replace")
            decoded_components = unquote(
                f"{parsed.path}?{parsed.query}#{parsed.fragment}",
                errors="replace",
            )
        except (UnicodeError, ValueError):
            return True
        return "@" in decoded_authority or bool(
            _URL_SECRET_COMPONENT_PATTERN.search(decoded_components)
        )

    def redact_absolute_path_tail(source: str, replacement: str) -> str:
        for index, character in enumerate(source):
            if character != "/":
                continue
            if index > 0 and (source[index - 1].isalnum() or source[index - 1] in "_-.~/"):
                continue
            return source[:index] + replacement
        return source

    def collect_scalar_values(value: object) -> None:
        if isinstance(value, str):
            if normalized := value.strip():
                protected_scalar_values.add(normalized)
        elif isinstance(value, Mapping):
            for nested in value.values():
                collect_scalar_values(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_scalar_values(nested)

    for protected_bytes in protected_contents:
        protected = protected_bytes.decode("utf-8", errors="replace")
        try:
            collect_scalar_values(yaml.safe_load(protected))
        except yaml.YAMLError:
            pass
        for line in protected.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#") and (comment := stripped[1:].strip()):
                protected_scalar_values.add(comment)

    protected_fragment_sources = tuple(
        fragment.casefold()
        for scalar in protected_scalar_values
        for fragment in _PLAN_FRAGMENT_PATTERN.findall(scalar)
    )

    def is_delimiter_aligned_fragment(fragment: str, protected: str) -> bool:
        if len(fragment) < _MIN_DISTINCTIVE_FRAGMENT_CODEPOINTS:
            return False
        start = protected.find(fragment)
        while start >= 0:
            end = start + len(fragment)
            before_ok = start == 0 or protected[start - 1] in _FRAGMENT_BOUNDARY_PUNCTUATION
            after_ok = end == len(protected) or protected[end] in _FRAGMENT_BOUNDARY_PUNCTUATION
            if before_ok and after_ok:
                return True
            start = protected.find(fragment, start + 1)
        return False

    diagnostic_fragments = {
        fragment
        for fragment in _PLAN_FRAGMENT_PATTERN.findall(text)
        if any(
            is_delimiter_aligned_fragment(fragment.casefold(), protected)
            for protected in protected_fragment_sources
        )
    }
    protected_match_values = frozenset(protected_scalar_values | diagnostic_fragments)

    occupied_characters = set(text)
    for protected_value in protected_match_values:
        occupied_characters.update(protected_value)
    preserved_semantics: list[tuple[str, str]] = []
    next_placeholder = 0xE000

    def preserve(value: str) -> str:
        nonlocal next_placeholder
        while next_placeholder < 0xF900:
            placeholder = chr(next_placeholder)
            next_placeholder += 1
            if placeholder not in occupied_characters:
                occupied_characters.add(placeholder)
                preserved_semantics.append((placeholder, value))
                return placeholder
        raise RuntimeError("admission diagnostic placeholder space exhausted")

    try:
        text = DIAGNOSTIC_URL_PATTERN.sub(
            lambda match: (
                preserve("<redacted>") if url_requires_redaction(match.group(0)) else match.group(0)
            ),
            text,
        )
        text = _AUTHORIZATION_VALUE_PATTERN.sub(lambda _match: preserve("<redacted>"), text)
        text = _SECRET_ASSIGNMENT_PATTERN.sub(lambda _match: preserve("<redacted>"), text)

        if protected_match_values:
            text = redact_protected_scalar_values(
                text,
                protected_match_values,
                "<plan-content>",
            )

        temporary_path_text = str(temporary_path)
        if temporary_path_text in text:
            text = text.replace(
                temporary_path_text,
                preserve("<admission-temp>"),
            )
        text = DIAGNOSTIC_URL_PATTERN.sub(
            lambda match: preserve(match.group(0)),
            text,
        )
        text = redact_absolute_path_tail(text, preserve("<path>"))
        text = _OBVIOUS_SECRET_TOKEN_PATTERN.sub(lambda _match: preserve("<redacted>"), text)
        text = _OPAQUE_TOKEN_PATTERN.sub(lambda _match: preserve("<redacted>"), text)
        text = _STABLE_ERROR_CODE_PATTERN.sub(lambda match: preserve(match.group(0)), text)
        text = _PROTECTED_DIAGNOSTIC_SUBJECT_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{preserve('<step-id>')}",
            text,
        )
        text = _DIAGNOSTIC_SUBJECT_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{preserve('<step-id>')}",
            text,
        )
        text = _QUOTED_DIAGNOSTIC_VALUE_PATTERN.sub(
            lambda match: (
                preserve(match.group(0))
                if _CONTRACT_FIELD_PATH_PATTERN.fullmatch(match.group("value"))
                else match.group(0)
            ),
            text,
        )
        text = _VIOLATION_CLASS_PATTERN.sub(lambda match: preserve(match.group(0)), text)
    except RuntimeError:
        return _UNAVAILABLE_DIAGNOSTIC

    for placeholder, value in reversed(preserved_semantics):
        text = text.replace(placeholder, value)
    printable = "".join(character if character.isprintable() else " " for character in text)
    return " ".join(printable.split())[:_MAX_REJECTION_REASON_CODEPOINTS]


_sanitize_diagnostic = sanitize_admission_diagnostic
