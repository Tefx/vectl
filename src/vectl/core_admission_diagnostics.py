"""Pure matching helpers for bounded admission diagnostic hygiene."""

from __future__ import annotations

import re
import unicodedata

from invar_runtime import Contract, post, pre

DIAGNOSTIC_URL_PATTERN = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s\x00-\x1f]+")
_TOKEN_PUNCTUATION = frozenset("_@.+*-")


# @invar:allow shell_result: Pure bounded matcher; root placement avoids package import cycle.
# @shell_complexity: Casefold mapping preserves exact original-text replacement spans.
@pre(
    Contract(
        lambda source, protected_values=None, replacement=None: (
            isinstance(source, str)
            and (
                protected_values is None
                or (
                    isinstance(protected_values, (set, frozenset))
                    and all(isinstance(value, str) and value for value in protected_values)
                )
            )
            and (replacement is None or isinstance(replacement, str) and replacement)
        ),
        "protected values and replacement must be non-empty text",
    )
)
@post(Contract(lambda result: isinstance(result, str), "redacted diagnostic is text"))
def redact_protected_scalar_values(
    source: str,
    protected_values: set[str] | frozenset[str],
    replacement: str,
) -> str:
    """Replace leftmost-longest casefolded scalar tokens.

    The bounded diagnostic makes direct matching preferable to a parser or trie.
    URL separators are ordinary token boundaries, while punctuation used by IDs
    remains part of a token.

    >>> redact_protected_scalar_values(
    ...     "P.SS q foo-bar Red Fox", {"p.ß", "q", "foo-", "Red Fox"}, "<plan>"
    ... )
    '<plan> <plan> foo-bar <plan>'
    >>> redact_protected_scalar_values(
    ...     "https://example.test/SecretTail?q=1", {"SecretTail"}, "<plan>"
    ... )
    'https://example.test/<plan>?q=1'
    """
    if not replacement:
        raise ValueError("protected scalar replacement must be non-empty")
    if any(not isinstance(value, str) or not value for value in protected_values):
        raise ValueError("protected scalar values must be non-empty")

    folded_parts: list[str] = []
    folded_boundary_to_original: dict[int, int] = {0: 0}
    folded_length = 0
    for original_index, character in enumerate(source):
        folded = character.casefold()
        folded_parts.append(folded)
        folded_length += len(folded)
        folded_boundary_to_original[folded_length] = original_index + 1
    folded_source = "".join(folded_parts)

    def is_token_character(character: str) -> bool:
        return (
            unicodedata.category(character)[:1] in {"L", "M", "N"}
            or character in _TOKEN_PUNCTUATION
        )

    def has_exact_boundaries(start: int, end: int) -> bool:
        if (
            start > 0
            and is_token_character(source[start - 1])
            and is_token_character(source[start])
        ):
            return False
        return not (
            end < len(source)
            and is_token_character(source[end - 1])
            and is_token_character(source[end])
        )

    candidates: list[tuple[int, int]] = []
    for protected in protected_values:
        folded_protected = protected.casefold()
        folded_start = folded_source.find(folded_protected)
        while folded_start >= 0:
            folded_end = folded_start + len(folded_protected)
            original_start = folded_boundary_to_original.get(folded_start)
            original_end = folded_boundary_to_original.get(folded_end)
            if (
                original_start is not None
                and original_end is not None
                and has_exact_boundaries(original_start, original_end)
            ):
                candidates.append((original_start, original_end))
            folded_start = folded_source.find(folded_protected, folded_start + 1)

    output: list[str] = []
    cursor = 0
    for start, end in sorted(candidates, key=lambda span: (span[0], -span[1])):
        if start < cursor:
            continue
        output.extend((source[cursor:start], replacement))
        cursor = end
    output.append(source[cursor:])
    return "".join(output)
