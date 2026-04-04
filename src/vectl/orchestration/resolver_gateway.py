"""
Resolver allowlist validation and invocation gateway.

Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 2, 4, 5
Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.5

Public surfaces (this module):
    - ResolverGateway           (protocol for resolver invocation with allowlist gate)
    - GatewayInvocationResult  (result of a gateway-authorized invocation)
    - authorize_and_invoke()   (authorize + invoke convenience boundary)

Note: This module addresses the "resolver allowlist validation surfaces"
from the task description. The exact invocation model (sync, async, subprocess)
is not yet specified; this module records interface anchors with documented gaps.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.contracts import ResolutionCase, ResolutionReport

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------
# Resolver Gateway Protocol
# ---------------------------------------------------------------------
# GAP: The exact invocation model (sync, async, subprocess, remote) for
# resolver invocation is not yet specified. The gateway protocol records
# the expected boundary role.


class ResolverGateway(Protocol):
    """
    Protocol for resolver invocation with allowlist authorization gate.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4 and 5

    The gateway is the boundary that enforces the resolver tool allowlist
    before invocation proceeds. All resolver invocations must pass through
    an authorized gateway.

    GAP: The exact invocation model (sync, async, subprocess, remote) is
    not yet specified. This protocol records the expected boundary role.
    """

    def invoke(
        self,
        case: ResolutionCase,
        allowed_tool_families: tuple[str, ...],
    ) -> GatewayInvocationResult:
        """
        Authorize and invoke the resolver for a resolution case.

        The gateway MUST enforce deny-by-default allowlist validation before
        any resolver tooling is used.

        Args:
            case: The resolution case from control.
            allowed_tool_families: Explicit allowlist of permitted tool families.
                Empty tuple means deny-all.

        Returns:
            GatewayInvocationResult with the outcome of the authorized invocation.

        Raises:
            AuthorizationError: If the allowlist check fails (denied tool use).
            NotImplementedError: Until invocation model is specified.
        """
        ...


@dataclass(frozen=True)
class GatewayInvocationResult:
    """
    Result of a gateway-authorized resolver invocation.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 5 and 10

    GAP: The exact result schema fields are not yet fully specified.
    The fields below represent the known minimum anchor.

    Attributes:
        outcome: The resolution outcome status from the resolver.
        report: The ResolutionReport returned by the resolver.
        invocation_ref: Reference identifier for this invocation.
        tool_authorization_denied: Set of tool families denied by the gateway
            (useful for debugging authorization failures).
    """

    outcome: Literal["success", "authorization_denied", "invocation_error"]
    report: ResolutionReport | None = None
    invocation_ref: str | None = None
    tool_authorization_denied: tuple[str, ...] = ()


# ---------------------------------------------------------------------
# Authorization Error
# ---------------------------------------------------------------------


class AuthorizationError(Exception):
    """
    Raised when a resolver invocation is denied by the allowlist gateway.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0
    """

    def __init__(
        self,
        message: str,
        denied_families: tuple[str, ...] = (),
    ) -> None:
        """
        Initialize the authorization error.

        Args:
            message: Human-readable denial explanation.
            denied_families: Tuple of tool families that were denied.
        """
        super().__init__(message)
        self.denied_families = denied_families


# ---------------------------------------------------------------------
# Authorize-and-Invoke Convenience Boundary
# ---------------------------------------------------------------------
# GAP: The global default gateway instance is not yet specified.
# This function records the convenience surface with a documented gap.


def authorize_and_invoke(
    case: ResolutionCase,
    allowed_tool_families: tuple[str, ...],
    gateway: ResolverGateway | None = None,
) -> GatewayInvocationResult:
    """
    Authorize a resolution case and invoke the resolver through the gateway.

    This is a convenience boundary that combines allowlist validation
    and resolver invocation in one call.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4 and 5

    GAP: The default gateway instance when gateway=None is not yet specified.
    Concrete default behavior must NOT be invented in this contract step.

    Args:
        case: The resolution case from control.
        allowed_tool_families: Explicit allowlist of permitted tool families.
        gateway: Optional explicit gateway instance. If None, a default
            gateway must be globally available.

    Returns:
        GatewayInvocationResult for the authorized invocation.

    Raises:
        AuthorizationError: If allowlist validation fails.
        NotImplementedError: Until invocation and default gateway semantics
            are specified.
    """
    raise NotImplementedError(
        "authorize_and_invoke: default gateway and invocation model not yet specified in design docs"
    )


__all__ = [
    "AuthorizationError",
    "GatewayInvocationResult",
    "ResolverGateway",
    "authorize_and_invoke",
]
