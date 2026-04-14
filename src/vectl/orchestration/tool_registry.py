"""
Canonical tool family metadata and resolver allowlist validation surfaces.

Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7 (resolver config)

Public surfaces (this module):
    - ToolFamily             (canonical tool family metadata schema)
    - ToolFamilyRegistry     (registry of known tool families)
    - validate_allowlist()  (resolver allowlist validation surface)
    - canonical_tool_families()  (canonical tool family identifiers)
    - validate_tool_allowlist_entry()  (per-entry validation against registry)

Note: Tool family taxonomy is defined in §8.4 canonical registry table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Canonical Tool Family Identifiers
# ---------------------------------------------------------------------
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
# Canonical tool registry table defines the authoritative tool families and tools.

CanonicalToolFamily = Literal[
    "core",
    "orchestration",
    "drive",
]
"""Known tool family identifier literals (from §8.4 canonical registry table)."""


CANONICAL_TOOL_FAMILIES: Final[tuple[str, ...]] = (
    "core",
    "orchestration",
    "drive",
)
"""Tuple of currently known canonical tool family identifiers (§8.4)."""

# Tool name to family mapping from §8.4 canonical registry table
_CANONICAL_TOOL_TO_FAMILY: Final[dict[str, str]] = {
    "status": "core",
    "show": "core",
    "claim": "core",
    "complete": "core",
    "defer": "core",
    "read_events": "orchestration",
    "read_state": "orchestration",
    "read_case": "orchestration",
    # Drive tool family — Authority: RFC-orch-drive §16.3
    "drive_status": "drive",
    "drive_events": "drive",
    "drive_read_case": "drive",
    "drive_frontier": "drive",
    "drive_child_runs": "drive",
}
"""Map of canonical tool names to their families (§8.4 registry table)."""


# ---------------------------------------------------------------------
# Tool Family Metadata
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolFamily:
    """
    Canonical metadata for a tool family.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

    Attributes:
        family: Tool family identifier string.
        description: Human-readable description of the family.
        allowed_operations: Tuple of specifically allowed operation names
            within this family.
    """

    family: str
    description: str = ""
    allowed_operations: tuple[str, ...] = ()


# Static registry data — populated from §8.4 canonical registry table
_CANONICAL_FAMILY_METADATA: Final[dict[str, ToolFamily]] = {
    "core": ToolFamily(
        family="core",
        description="Core vectl CLI tools",
        allowed_operations=("status", "show", "claim", "complete", "defer"),
    ),
    "orchestration": ToolFamily(
        family="orchestration",
        description="Orchestration-plane read surfaces",
        allowed_operations=("read_events", "read_state", "read_case"),
    ),
    "drive": ToolFamily(
        family="drive",
        description="Drive-plane read surfaces for resolver/planner inspection",
        allowed_operations=(
            "drive_status",
            "drive_events",
            "drive_read_case",
            "drive_frontier",
            "drive_child_runs",
        ),
    ),
}
"""Static metadata for canonical tool families."""


# ---------------------------------------------------------------------
# Tool Family Registry
# ---------------------------------------------------------------------


class ToolFamilyRegistry:
    """
    Registry of known tool families and their metadata.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

    The registry is populated from the static canonical registry table in §8.4.
    """

    def get(self, family: str) -> ToolFamily | None:
        """
        Look up metadata for a tool family.

        Args:
            family: The tool family identifier to look up.

        Returns:
            ToolFamily metadata if the family is registered, else None.
        """
        return _CANONICAL_FAMILY_METADATA.get(family)

    def all_families(self) -> tuple[str, ...]:
        """
        Return all registered tool family identifiers.

        Returns:
            Tuple of all registered tool family identifiers.
        """
        return tuple(_CANONICAL_FAMILY_METADATA.keys())

    def is_registered(self, family: str) -> bool:
        """
        Check whether a tool family is registered.

        Args:
            family: The tool family identifier to check.

        Returns:
            True if the family is registered.
        """
        return family in _CANONICAL_FAMILY_METADATA

    def is_valid_tool(self, tool_name: str, family: str) -> bool:
        """
        Check whether a tool name is valid for a given family.

        Args:
            tool_name: The tool name to check.
            family: The tool family identifier.

        Returns:
            True if the tool name is valid for the family.
        """
        metadata = self.get(family)
        if metadata is None:
            return False
        return tool_name in metadata.allowed_operations


# Singleton instance for module-level functions
_registry = ToolFamilyRegistry()


# ---------------------------------------------------------------------
# Canonical Tool Families Accessor
# ---------------------------------------------------------------------


def canonical_tool_families() -> tuple[str, ...]:
    """
    Return the tuple of canonical tool family identifiers.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

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


# ---------------------------------------------------------------------
# Tool Allowlist Entry Validation
# ---------------------------------------------------------------------


class ToolAllowlistValidationError(ValueError):
    """
    Raised when a tool allowlist entry fails validation.

    Attributes:
        family: The tool family that failed validation.
        tool: The specific tool name that failed (if applicable).
        reason: Human-readable reason for the validation failure.
    """

    def __init__(self, family: str, tool: str | None, reason: str) -> None:
        self.family = family
        self.tool = tool
        self.reason = reason
        parts = [f"family={family!r}"]
        if tool is not None:
            parts.append(f"tool={tool!r}")
        parts.append(f"reason={reason!r}")
        super().__init__("tool allowlist validation failed: " + ", ".join(parts))


def validate_tool_allowlist_entry(
    family: str,
    tools: tuple[str, ...],
) -> list[ToolAllowlistValidationError]:
    """
    Validate a tool allowlist entry against the canonical registry.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.5

    Validates that:
    - The tool family is a known canonical family
    - Each tool name is valid for that family
    - No wildcard or prefix patterns are used

    Args:
        family: The tool family identifier to validate.
        tools: Tuple of tool names allowed for this family.

    Returns:
        List of validation errors (empty if all valid).
    """
    errors: list[ToolAllowlistValidationError] = []

    # Check family is known
    if not _registry.is_registered(family):
        errors.append(
            ToolAllowlistValidationError(
                family=family,
                tool=None,
                reason=f"unknown tool family {family!r}; "
                f"expected one of {_registry.all_families()}",
            )
        )
        return errors  # Can't validate tools without valid family

    # Check each tool name
    for tool in tools:
        # Reject wildcard or prefix patterns
        if "*" in tool or tool.endswith(".*"):
            errors.append(
                ToolAllowlistValidationError(
                    family=family,
                    tool=tool,
                    reason="wildcard or prefix patterns are not allowed",
                )
            )
            continue

        # Check tool is valid for family
        if not _registry.is_valid_tool(tool, family):
            metadata = _registry.get(family)
            allowed = metadata.allowed_operations if metadata else ()
            errors.append(
                ToolAllowlistValidationError(
                    family=family,
                    tool=tool,
                    reason=f"tool {tool!r} is not valid for family {family!r}; "
                    f"expected one of {allowed}",
                )
            )

    return errors


def validate_tool_allowlist(
    allowlist: dict[str, tuple[str, ...]],
) -> list[ToolAllowlistValidationError]:
    """
    Validate a complete tool allowlist dict against the canonical registry.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.5

    Args:
        allowlist: Dict mapping family names to tuples of allowed tool names.

    Returns:
        List of validation errors (empty if all valid).
    """
    all_errors: list[ToolAllowlistValidationError] = []
    for family, tools in allowlist.items():
        all_errors.extend(validate_tool_allowlist_entry(family, tools))
    return all_errors


__all__ = [
    "CanonicalToolFamily",
    "CANONICAL_TOOL_FAMILIES",
    "ToolFamily",
    "ToolFamilyRegistry",
    "canonical_tool_families",
    "validate_allowlist",
    "validate_tool_allowlist",
    "validate_tool_allowlist_entry",
    "ToolAllowlistValidationError",
]
