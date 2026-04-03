"""
Reusable agent/session resource tracking.

Authority:
    docs/ORCHESTRATION-PLANE-ARCHITECTURE.md section 5.2
    docs/ORCHESTRATION-PLANE-INTERFACES.md section 4.2
    docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md sections 5.3 and 6

Roster contract (per interfaces.md section 4.2):
    - snapshot()  -> RosterSnapshot
    - claim()     -> WorkLease | None
    - release()   -> None
    - register()  -> None

Isolation enforcement (per isolation-semantics.md section 5.3):
    - default      -> reuse opportunistically allowed
    - workspace    -> reuse allowed, fresh workspace required
    - independent  -> NO prior warm resource may satisfy the step

Non-responsibilities (per architecture.md section 5.2):
    - roster does NOT own plan semantics
    - roster does NOT own blocked-state interpretation
    - roster does NOT own next-step orchestration decisions

Session reuse (per architecture.md section 5.2 and non-goals):
    - session_reuse is an INTERNAL roster optimization only
    - it is NOT a first-class architecture concept
    - it must NOT be exposed as public API

Banned abstractions (per architecture.md section 4 and 8.2):
    - continuity_group is NOT part of this contract
    - session_reuse as a task/planner/core concept is NOT exposed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from vectl.orchestration.contracts import RosterSnapshot, WorkLease

if TYPE_CHECKING:
    pass


class IsolationMode(Enum):
    """
    Isolation levels for roster resource matching.

    These values affect whether a warm/reusable resource may be matched
    when claiming a lease. They do NOT affect plan semantics.

    Authority: docs/ORCHESTRATION-PLANE-ISOLATION-SEMANTICS.md section 2
    """

    DEFAULT = "default"
    WORKSPACE = "workspace"
    INDEPENDENT = "independent"


@dataclass(frozen=True)
class ResourceEntry:
    """
    Internal resource record for the roster registry.

    Not exposed in the public contract; used only internally.
    """

    lease: WorkLease
    expires_at: float


class Roster:
    """
    Reusable agent/session resource registry.

    This component owns reusable resource bookkeeping only.
    It does NOT own plan semantics, blocked-state interpretation,
    or next-step orchestration decisions.

    Public contract (per interfaces.md section 4.2):
        - snapshot() -> RosterSnapshot
        - claim(role, isolation=IsolationMode.DEFAULT) -> WorkLease | None
        - release(lease) -> None
        - register(lease, expires_at) -> None

    Error contract (per interfaces.md section 4.2):
        - claim() returning None means no compatible resource is available
        - None must NOT be reinterpreted as a plan blockage
    """

    def __init__(self, default_ttl_seconds: float = 300.0) -> None:
        """
        Initialize the roster.

        Args:
            default_ttl_seconds: Default time-to-live for registered resources.
        """
        self._resources: dict[str, ResourceEntry] = {}
        self._claimed_roles: set[str] = set()
        self._default_ttl = default_ttl_seconds

    def snapshot(self) -> RosterSnapshot:
        """
        Capture current roster state.

        Returns:
            RosterSnapshot representing available reusable resources.
        """
        now = datetime.now().timestamp()
        available: list[str] = []
        working: list[str] = []

        expired_roles = [role for role, entry in self._resources.items() if entry.expires_at <= now]
        for role in expired_roles:
            del self._resources[role]
            self._claimed_roles.discard(role)

        reusable_sessions: list[str] = []
        for role, entry in self._resources.items():
            if role in self._claimed_roles:
                working.append(role)
            else:
                available.append(role)
            if entry.lease.session_id:
                reusable_sessions.append(entry.lease.session_id)

        return RosterSnapshot(
            available_agents=tuple(sorted(set(available))),
            working_agents=tuple(sorted(set(working))),
            reusable_sessions=tuple(sorted(set(reusable_sessions))),
            exhausted_roles=(),
        )

    def claim(
        self,
        role: str,
        isolation: IsolationMode = IsolationMode.DEFAULT,
    ) -> WorkLease | None:
        """
        Claim a compatible reusable resource.

        Args:
            role: The role being sought.
            isolation: Isolation level affecting resource matching.

        Returns:
            WorkLease if a compatible resource is available, else None.

        Isolation enforcement (per isolation-semantics.md section 5.3):
            - DEFAULT:    reuse opportunistically allowed
            - WORKSPACE:  reuse allowed, fresh workspace support needed
            - INDEPENDENT: no prior warm resource may satisfy the step
                          -> always return None (force fresh resource)

        Error contract (per interfaces.md section 4.2):
            Returning None means no compatible reusable resource is currently
            available. Callers must NOT reinterpret None as a plan blockage.
        """
        now = datetime.now().timestamp()

        # INDEPENDENT isolation: no warm resource may satisfy this step
        # (per isolation-semantics.md section 5.3)
        if isolation is IsolationMode.INDEPENDENT:
            return None

        # For DEFAULT and WORKSPACE, scan for a non-expired matching resource
        entry = self._resources.get(role)
        if entry is None:
            return None
        if entry.expires_at <= now:
            # Expired; remove and treat as unavailable
            del self._resources[role]
            self._claimed_roles.discard(role)
            return None
        self._claimed_roles.add(role)
        return entry.lease

    def release(self, lease: WorkLease) -> None:
        """
        Release a previously claimed lease.

        Args:
            lease: The WorkLease to release.
        """
        role = lease.role
        entry = self._resources.get(role)
        if entry is not None and entry.lease == lease:
            self._claimed_roles.discard(role)

    def register(
        self,
        lease: WorkLease,
        expires_at: float,
    ) -> None:
        """
        Register a new resource into the roster.

        Args:
            lease: The WorkLease to register.
            expires_at: Unix timestamp when this lease expires.
        """
        if expires_at <= datetime.now().timestamp():
            self._resources.pop(lease.role, None)
            self._claimed_roles.discard(lease.role)
            return

        self._resources[lease.role] = ResourceEntry(lease=lease, expires_at=expires_at)
        self._claimed_roles.discard(lease.role)
