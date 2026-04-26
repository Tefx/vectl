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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from vectl.orchestration.contracts import ResolutionCase, ResolutionReport
from vectl.orchestration.tool_registry import (
    get_tool_family,
    is_registered,
    is_valid_tool,
    validate_allowlist,
)
from vectl.plan_path import is_linked_worktree

if TYPE_CHECKING:
    pass


_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "status",
        "show",
        "read_events",
        "read_state",
        "read_case",
    }
)
_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "claim",
        "complete",
        "defer",
    }
)


def _default_main_worktree_probe() -> bool:
    """Return whether resolver invocation is running in the main worktree.

    Authority:
        docs/ORCHESTRATION-PLANE-RESOLVER-COORDINATION.md section 5
        docs/ORCHESTRATION-PLANE-RUNTIME-WORKTREE-LIFECYCLE.md section 5
    """

    is_linked, _main_root = is_linked_worktree()
    return not is_linked


@dataclass(frozen=True)
class ResolverToolCall:
    """Planned resolver tool call to be authorized by the gateway.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 4.0

    Args:
        family: Canonical tool family identifier.
        name: Canonical tool name.
        surface: Access intent category for this tool request.
            Must be ``"read"`` or ``"write"``.
    """

    family: str
    name: str
    surface: Literal["read", "write"]


@dataclass(frozen=True)
class ResolverToolMediation:
    """Runtime mediation decision for one resolver invocation.

    Authority: docs/ORCHESTRATION-PLANE-LIVE-AUTHORITY-CONTRACT-LOCK.md section 6.2

    Attributes:
        planned_tool_calls: Tool calls requested by the live mediation source.
        allowed_tool_families: Gateway allowlist narrowed to the live request.
        evidence_refs: Machine-readable evidence emitted by the mediation source.
    """

    planned_tool_calls: tuple[ResolverToolCall, ...] = ()
    allowed_tool_families: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewayAuditEvent:
    """Machine-readable audit record for one resolver tool-call attempt.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4 and 8

    Attributes:
        invocation_ref: Invocation reference for event correlation.
        family: Tool family requested by resolver.
        tool: Tool name requested by resolver.
        requested_surface: Requested access surface (read/write).
        outcome: Authorization outcome for this tool request.
        reason_code: Stable machine-readable denial code, empty when allowed.
        reason: Human-readable reason.
    """

    invocation_ref: str
    family: str
    tool: str
    requested_surface: Literal["read", "write"]
    outcome: Literal["allowed", "denied"]
    reason_code: str = ""
    reason: str = ""


@dataclass(frozen=True)
class GatewayDenial:
    """Machine-readable denial descriptor for resolver authorization failures.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4 and 8
    """

    family: str
    tool: str
    requested_surface: Literal["read", "write"]
    reason_code: Literal[
        "family_not_allowed",
        "unknown_family",
        "unknown_tool",
        "surface_mismatch",
        "claim_not_allowed",
    ]
    reason: str


class ResolverInvoker(Protocol):
    """Resolver invocation callable used by :class:`AuditedResolverGateway`.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md section 5
    """

    def __call__(
        self,
        case: ResolutionCase,
        authorized_calls: tuple[ResolverToolCall, ...],
        invocation_ref: str,
    ) -> ResolutionReport:
        """Execute resolver invocation after gateway authorization.

        Args:
            case: Resolution case provided by control.
            authorized_calls: Tool calls allowed by the gateway.
            invocation_ref: Correlation reference assigned by the gateway.

        Returns:
            Bounded machine-readable resolution report.
        """
        ...


ResolverInvokeFn = Callable[
    [ResolutionCase, tuple[ResolverToolCall, ...], str],
    ResolutionReport,
]


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
        tool_call_audit: Machine-readable audit events for each requested tool
            call (allowed or denied).
    """

    outcome: Literal["success", "authorization_denied", "invocation_error"]
    report: ResolutionReport | None = None
    invocation_ref: str | None = None
    tool_authorization_denied: tuple[str, ...] = ()
    tool_call_audit: tuple[GatewayAuditEvent, ...] = ()


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
        denied: tuple[GatewayDenial, ...] = (),
        audit_events: tuple[GatewayAuditEvent, ...] = (),
    ) -> None:
        """
        Initialize the authorization error.

        Args:
            message: Human-readable denial explanation.
            denied_families: Tuple of tool families that were denied.
            denied: Machine-readable denied tool request details.
            audit_events: Audit records for all attempted tool requests.
        """
        super().__init__(message)
        self.denied_families = denied_families
        self.denied = denied
        self.audit_events = audit_events


@dataclass(frozen=True)
class AuditedResolverGateway:
    """Resolver gateway with allowlist enforcement and audit recording.

    Authority: docs/ORCHESTRATION-PLANE-RESOLUTION-CONTRACT.md sections 4, 5, 8
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.6

    This gateway enforces resolver-tool authorization against an explicit
    per-invocation allowlist snapshot and records every tool attempt
    (allowed or denied) in machine-readable audit events.
    """

    planned_tool_calls: tuple[ResolverToolCall, ...]
    resolver_invoker: ResolverInvokeFn
    invocation_ref_factory: Callable[[], str] = lambda: "resolver-gateway-invocation"
    main_worktree_probe: Callable[[], bool] = lambda: True

    def invoke(
        self,
        case: ResolutionCase,
        allowed_tool_families: tuple[str, ...],
    ) -> GatewayInvocationResult:
        """Authorize planned tool calls and invoke resolver on success.

        Args:
            case: The resolution case from control.
            allowed_tool_families: Frozen explicit allowlist snapshot.

        Returns:
            GatewayInvocationResult describing invocation outcome.

        Raises:
            AuthorizationError: If any planned tool call is denied.
        """
        if not self.main_worktree_probe():
            raise AuthorizationError("resolver invocation blocked: not on main worktree")

        invocation_ref = self.invocation_ref_factory()
        denials, _audit_events = _authorize_tool_calls(
            planned_calls=self.planned_tool_calls,
            allowed_tool_families=allowed_tool_families,
            invocation_ref=invocation_ref,
        )
        if denials:
            denied_families = tuple(dict.fromkeys(denial.family for denial in denials))
            raise AuthorizationError(
                message="resolver invocation denied by gateway allowlist",
                denied_families=denied_families,
                denied=denials,
                audit_events=_audit_events,
            )

        report = self.resolver_invoker(case, self.planned_tool_calls, invocation_ref)
        return GatewayInvocationResult(
            outcome="success",
            report=report,
            invocation_ref=invocation_ref,
            tool_call_audit=_audit_events,
        )


def _authorize_tool_calls(
    planned_calls: tuple[ResolverToolCall, ...],
    allowed_tool_families: tuple[str, ...],
    invocation_ref: str,
) -> tuple[tuple[GatewayDenial, ...], tuple[GatewayAuditEvent, ...]]:
    """Authorize planned tool calls and return denials plus audit events.

    Args:
        planned_calls: Resolver-planned tool calls.
        allowed_tool_families: Frozen per-run allowlist snapshot.
        invocation_ref: Invocation correlation reference.

    Returns:
        Pair of ``(denials, audit_events)``.
    """
    denials: list[GatewayDenial] = []
    audit_events: list[GatewayAuditEvent] = []

    for call in planned_calls:
        denial = _authorize_single_call(
            call=call,
            allowed_tool_families=allowed_tool_families,
        )
        if denial is None:
            audit_events.append(
                GatewayAuditEvent(
                    invocation_ref=invocation_ref,
                    family=call.family,
                    tool=call.name,
                    requested_surface=call.surface,
                    outcome="allowed",
                )
            )
            continue

        denials.append(denial)
        audit_events.append(
            GatewayAuditEvent(
                invocation_ref=invocation_ref,
                family=call.family,
                tool=call.name,
                requested_surface=call.surface,
                outcome="denied",
                reason_code=denial.reason_code,
                reason=denial.reason,
            )
        )

    return tuple(denials), tuple(audit_events)


def _authorize_single_call(
    call: ResolverToolCall,
    allowed_tool_families: tuple[str, ...],
) -> GatewayDenial | None:
    """Authorize one tool call against canonical registry and allowlist.

    Args:
        call: Tool call request to authorize.
        allowed_tool_families: Frozen per-run allowlist snapshot.

    Returns:
        ``None`` if allowed, else machine-readable denial.
    """
    if call.family == "core" and call.name == "claim":
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="claim_not_allowed",
            reason="resolver claim attempts are forbidden; claim remains part of normal flow",
        )

    if not validate_allowlist(call.family, allowed_tool_families):
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="family_not_allowed",
            reason=(
                f"tool family {call.family!r} is not in allowed snapshot {allowed_tool_families!r}"
            ),
        )

    if not is_registered(call.family):
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="unknown_family",
            reason=f"unknown canonical tool family {call.family!r}",
        )

    if not is_valid_tool(call.name, call.family):
        metadata = get_tool_family(call.family)
        allowed = metadata.allowed_operations if metadata is not None else ()
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="unknown_tool",
            reason=(
                f"tool {call.name!r} is not registered for family {call.family!r}; "
                f"expected one of {allowed!r}"
            ),
        )

    if call.surface == "read" and call.name in _WRITE_TOOLS:
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="surface_mismatch",
            reason=f"tool {call.name!r} is write-surface and cannot be requested as read",
        )

    if call.surface == "write" and call.name in _READ_ONLY_TOOLS:
        return GatewayDenial(
            family=call.family,
            tool=call.name,
            requested_surface=call.surface,
            reason_code="surface_mismatch",
            reason=f"tool {call.name!r} is read-surface and cannot be requested as write",
        )

    return None


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
    if gateway is None:
        raise NotImplementedError(
            "authorize_and_invoke: default gateway instance is not specified; "
            "provide explicit gateway"
        )

    normalized_allowlist = tuple(dict.fromkeys(allowed_tool_families))
    return gateway.invoke(case=case, allowed_tool_families=normalized_allowlist)


__all__ = [
    "AuthorizationError",
    "AuditedResolverGateway",
    "GatewayAuditEvent",
    "GatewayDenial",
    "GatewayInvocationResult",
    "ResolverInvoker",
    "ResolverToolCall",
    "ResolverToolMediation",
    "ResolverGateway",
    "authorize_and_invoke",
]
