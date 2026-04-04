"""
Shared orchestration-plane configuration.

Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

Public surfaces (authority pinned to ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md):
    - OrchestrationConfig            (shared config model / loader / freeze boundary)
    - ResolverToolAllowlist         (resolver tool authorization allowlist surface)
    - freeze_config()                (immutability enforcement boundary)
    - load_orchestration_config()   (config loader surface)

These surfaces are shared orchestration-plane concerns; they are not private
submodules of any single component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Orchestration Config Model
# ---------------------------------------------------------------------

DEFAULT_ROSTER_TTL_SECONDS: Final[float] = 300.0
DEFAULT_RESOLVER_TIMEOUT_SECONDS: Final[float] = 60.0


@dataclass(frozen=True)
class ResolverToolAllowlist:
    """
    Resolver tool authorization allowlist.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    Resolver tool use follows a deny-by-default allowlist model.
    Only tool families explicitly listed here may be used by the resolver.

    Attributes:
        allowed_tool_families: Tuple of allowed tool family identifiers.
            Empty tuple means deny-all (deny-by-default).
        deployment_narrowings: Optional per-deployment allowlist refinements.
    """

    allowed_tool_families: tuple[str, ...] = ()
    deployment_narrowings: tuple[str, ...] = ()

    def is_allowed(self, tool_family: str) -> bool:
        """
        Check whether a tool family is permitted.

        Args:
            tool_family: The tool family identifier to check.

        Returns:
            True only when the tool family is explicitly in the allowlist.
        """
        return tool_family in self.allowed_tool_families


@dataclass(frozen=True)
class RosterConfig:
    """
    Roster-component configuration fragment.

    Attributes:
        default_ttl_seconds: Default time-to-live for registered resources.
        max_reuse_window_seconds: Maximum reuse window before resource exhaustion.
    """

    default_ttl_seconds: float = DEFAULT_ROSTER_TTL_SECONDS
    max_reuse_window_seconds: float = 600.0


@dataclass(frozen=True)
class RuntimeConfig:
    """
    Runtime-component configuration fragment.

    Attributes:
        worktree_base_dir: Base directory for worktree/workspace creation.
        runner_startup_timeout_seconds: Timeout for runner startup.
    """

    worktree_base_dir: Path = Path(".vectl/worktrees")
    runner_startup_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ResolverConfig:
    """
    Resolver-component configuration fragment.

    Attributes:
        tool_allowlist: Explicit allowlist for resolver tool usage.
        invocation_timeout_seconds: Timeout for resolver invocation.
    """

    tool_allowlist: ResolverToolAllowlist = field(default_factory=ResolverToolAllowlist)
    invocation_timeout_seconds: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS


@dataclass(frozen=True)
class OrchestrationConfig:
    """
    Shared orchestration-plane configuration model.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7

    This model is the canonical configuration boundary. It aggregates
    component-specific fragments under one frozen immutable envelope.

    Attributes:
        roster: Roster-component configuration.
        runtime: Runtime-component configuration.
        resolver: Resolver-component configuration.
        plan_path: Authoritative plan definition file path.
    """

    roster: RosterConfig = field(default_factory=RosterConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    resolver: ResolverConfig = field(default_factory=ResolverConfig)
    plan_path: Path = field(default_factory=lambda: Path("plan.yaml"))

    def freeze(self) -> OrchestrationConfig:
        """
        Return this config as an immutable frozen instance.

        Returns:
            A frozen (immutable) view of this configuration.
        """
        return self


# ---------------------------------------------------------------------
# Config Loader / Freeze Boundary
# ---------------------------------------------------------------------
# GAP: Config loader from file/env is not yet specified in design docs.
#      Record the gap on the interface rather than inventing a surface.


def freeze_config(config: OrchestrationConfig) -> OrchestrationConfig:
    """
    Enforce immutability boundary on an orchestration configuration.

    Args:
        config: The configuration to freeze.

    Returns:
        A frozen (immutable) view of the configuration.

    Raises:
        TypeError: If config is not an OrchestrationConfig instance.
    """
    if not isinstance(config, OrchestrationConfig):
        raise TypeError(f"freeze_config requires OrchestrationConfig, got {type(config).__name__}")
    return config.freeze()


def load_orchestration_config(
    plan_path: Path | str | None = None,
) -> OrchestrationConfig:
    """
    Load orchestration configuration from standard locations.

    GAP: The exact config-file format and loader behavior are not yet
    specified in the design docs. This signature is a forward contract
    placeholder. Concrete implementation must not be invented here.

    Args:
        plan_path: Optional explicit plan path override.

    Returns:
        An OrchestrationConfig instance with loaded or default values.

    Raises:
        NotImplementedError: Until the loader semantics are specified.
    """
    # GAP: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7
    # does not yet specify the config-file format or loading behavior.
    # Concrete loading logic must NOT be invented in this contract step.
    raise NotImplementedError(
        "load_orchestration_config: loader semantics not yet specified in design docs"
    )


__all__ = [
    "DEFAULT_ROSTER_TTL_SECONDS",
    "DEFAULT_RESOLVER_TIMEOUT_SECONDS",
    "OrchestrationConfig",
    "ResolverToolAllowlist",
    "RosterConfig",
    "RuntimeConfig",
    "ResolverConfig",
    "freeze_config",
    "load_orchestration_config",
]
