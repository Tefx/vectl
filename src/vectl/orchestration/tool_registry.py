"""
Canonical tool family metadata and resolver allowlist validation surfaces.

Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7 (resolver config)

Public surfaces (this module):
    - ToolFamily             (canonical tool family metadata schema)
    - ToolFamilyRegistry     (registry of known tool families)
    - validate_allowlist()  (resolver allowlist validation surface)
    - canonical_tool_families()  (canonical tool family identifiers)

Note: This module addresses the "canonical tool family metadata and resolver
allowlist validation surfaces". The exact tool family taxonomy is not yet
specified in the design docs; this module records interface anchors with
documented gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Canonical Tool Family Identifiers
# ---------------------------------------------------------------------
# GAP: The exact canonical tool family taxonomy is not yet specified.
# These are forward-declared string literals for forward-contract surfaces.
# Concrete tool families must NOT be added without design-doc specification.

CanonicalToolFamily = Literal[
    "vectl.core",
    "vectl.orchestration",
    "vectl.planner",
    "filesystem",
    "http",
]
"""Known tool family identifier literals (forward contract)."""


CANONICAL_TOOL_FAMILIES: Final[tuple[str, ...]] = (
    "vectl.core",
    "vectl.orchestration",
    "vectl.planner",
    "filesystem",
    "http",
)
"""Tuple of currently known canonical tool family identifiers."""


# ---------------------------------------------------------------------
# Tool Family Metadata
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolFamily:
    """
    Canonical metadata for a tool family.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    GAP: The exact metadata fields and their semantics are not yet fully
    specified. The fields below represent the known minimum anchor.

    Attributes:
        family: Tool family identifier string.
        description: Human-readable description of the family.
        allowed_operations: Tuple of specifically allowed operation names
            within this family. Empty means all operations are allowed
            (subject to deny-by-default at resolver level).
    """

    family: str
    description: str = ""
    allowed_operations: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Tool Family Registry
# ---------------------------------------------------------------------


class ToolFamilyRegistry:
    """
    Registry of known tool families and their metadata.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    GAP: The registry's population source (static allowlist, dynamic
    discovery, config-driven) is not yet specified. No concrete
    implementation should be added in this contract step.
    """

    def get(self, family: str) -> ToolFamily | None:
        """
        Look up metadata for a tool family.

        Args:
            family: The tool family identifier to look up.

        Returns:
            ToolFamily metadata if the family is registered, else None.

        Raises:
            NotImplementedError: Until registry semantics are specified.
        """
        raise NotImplementedError(
            "ToolFamilyRegistry.get: registry population not yet specified in design docs"
        )

    def all_families(self) -> tuple[str, ...]:
        """
        Return all registered tool family identifiers.

        Returns:
            Tuple of all registered tool family identifiers.

        Raises:
            NotImplementedError: Until registry semantics are specified.
        """
        raise NotImplementedError(
            "ToolFamilyRegistry.all_families: registry population not yet specified in design docs"
        )

    def is_registered(self, family: str) -> bool:
        """
        Check whether a tool family is registered.

        Args:
            family: The tool family identifier to check.

        Returns:
            True if the family is registered.

        Raises:
            NotImplementedError: Until registry semantics are specified.
        """
        raise NotImplementedError(
            "ToolFamilyRegistry.is_registered: registry population not yet specified in design docs"
        )


# ---------------------------------------------------------------------
# Canonical Tool Families Accessor
# ---------------------------------------------------------------------


def canonical_tool_families() -> tuple[str, ...]:
    """
    Return the tuple of canonical tool family identifiers.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    Returns:
        Tuple of canonical tool family identifier strings.
    """
    return CANONICAL_TOOL_FAMILIES


# ---------------------------------------------------------------------
# Resolver Allowlist Validation Surface
# ---------------------------------------------------------------------


def validate_allowlist(
    tool_family: str,
    allowed_families: tuple[str, ...],
) -> bool:
    """
    Validate whether a tool family is permitted by the resolver allowlist.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    This function implements deny-by-default validation:
    a tool family is allowed only if it appears in allowed_families.

    Args:
        tool_family: The tool family identifier to validate.
        allowed_families: The explicit allowlist of permitted tool families.
            Empty tuple means deny-all.

    Returns:
        True only when tool_family is explicitly in allowed_families.
    """
    if not allowed_families:
        return False
    return tool_family in allowed_families


__all__ = [
    "CanonicalToolFamily",
    "CANONICAL_TOOL_FAMILIES",
    "ToolFamily",
    "ToolFamilyRegistry",
    "canonical_tool_families",
    "validate_allowlist",
]
