"""Session reuse pool.

Responsibility: Track completed sessions and find reusable ones based on
step dependency, runner match, and TTL.

Non-responsibility: Does NOT make dispatch decisions. Does NOT interact with
`decide.py`/`decision_state.py` decide-memory internals directly.

Architecture Reference: docs/DRIVER-ARCHITECTURE.md Section 2.5
Architecture Reference: docs/DRIVER-ARCHITECTURE.md Q4 (Ownership Boundary)
Blueprint Reference: DRIVER-BLUEPRINT.md Session Pool (session.py)
"""

from __future__ import annotations

import time

from .config import SessionConfig
from .types import SessionEntry


class SessionPool:
    """Pool of completed sessions eligible for reuse.

    Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, SessionPool class
    Blueprint: DRIVER-BLUEPRINT.md Session Pool (session.py)

    This is a runner-aware session pool with per-runner TTL and runner-name
    matching.

    Ownership Boundary: SessionPool vs DecideState

    `decide.py` mutates decide-side memory through ``DecideState`` and receives
    that container via an explicit ``state`` argument. The driver runtime passes
    ``DriverState.decide_state`` and does NOT write decide-memory fields directly.

    The driver's `SessionPool` is a **parallel, runner-aware** pool that adds
    runner-name matching and per-runner TTL overrides -- capabilities that `decide.py`
    does not have. The two mechanisms serve different purposes:

    | Concern | `DecideState` (decide.py) | `SessionPool` (driver) |
    |---------|--------------------------------|----------------------|
    | Owner | `DriverState` + `decide()` mutation contract | `loop.py` via
    | | | `session.py` |
    | Written by | `decide()` when processing `CompletedResult` |
    | | | `loop.py` reconcile path after successful merge |
    | Read by | `decide()` during `should_reuse_session()` |
    | | | `loop.py` dispatch path, BEFORE calling `decide()` |
    | Runner-aware | No | Yes (runner-name match required) |
    | Per-runner TTL | No | Yes |
    | Purpose | decide() internal: determine session field on Action |
    | | | Driver: pre-resolve session_id for dispatch optimization |

    **Reconciliation**: When `decide()` produces an `Action` with `session="reuse"` and
    a `task_id`, the driver uses that `task_id`. When `decide()` produces `session="fresh"`,
    the driver MAY override by checking `SessionPool.find_reusable()` for a runner-aware
    match. This is safe because session reuse is best-effort: if the session is stale,
    the runner starts fresh (graceful degradation).

    **Decision**: [ADR] Decide-side memory lives in ``DecideState`` and is passed
    explicitly from ``DriverState``. ``SessionPool`` remains a supplementary
    runner-aware layer for dispatch-time optimization.

    Drift note (doc-sync pending): docs/DRIVER-ARCHITECTURE.md Section 2.5/Q4
    still describe module-global decide state from the pre-contract baseline.
    """

    def __init__(self, config: SessionConfig) -> None:
        """Initialize the session pool.

        Args:
            config: Session configuration with reuse_ttl and optional ttl_overrides.
        """
        self._entries: dict[str, SessionEntry] = {}
        self._default_ttl = config.reuse_ttl
        self._ttl_overrides = config.ttl_overrides

    def record(self, step_id: str, session_id: str, runner_name: str, agent: str) -> None:
        """Record a completed session for potential reuse.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, SessionPool.record
        Blueprint: DRIVER-BLUEPRINT.md Session Pool

        This is called by loop.py's reconcile path after a successful merge,
        recording the session for potential reuse by downstream steps.

        Args:
            step_id: The step that completed with this session.
            session_id: The runner session ID (e.g., claude UUID, opencode ses_XXX).
            runner_name: The runner that was used (e.g., "claude", "opencode").
            agent: The agent that executed the step.
        """
        self._entries[step_id] = SessionEntry(
            session_id=session_id,
            runner_name=runner_name,
            agent=agent,
            step_id=step_id,
            completed_at=time.monotonic(),
        )

    def find_reusable(
        self, step_id: str, agent: str, runner_name: str, depends_on: list[str]
    ) -> str | None:
        """Find a reusable session_id, or None.

        Architecture: docs/DRIVER-ARCHITECTURE.md Section 2.5, SessionPool.find_reusable
        Blueprint: DRIVER-BLUEPRINT.md Session Pool

        Reuse requires:
        1. Exactly one dependency in depends_on
        2. That dependency has a recorded session
        3. That session's runner matches runner_name
        4. TTL has not expired

        Accepted limitation: Only single-dependency steps are eligible
        for session reuse. Steps with 0 or 2+ dependencies always get
        fresh sessions. This is a deliberate simplification -- multi-dep
        session selection would require heuristics (most recent? longest
        lived?) with unclear benefit. The single-dep case covers the
        majority of linear chains (contract -> impl -> test).

        Args:
            step_id: The step to dispatch (unused in lookup, but useful for logging).
            agent: The agent that will execute the step.
            runner_name: The resolved runner name for dispatch.
            depends_on: List of step_ids this step depends on (typically 0, 1, or more).

        Returns:
            session_id if a reusable session is found, None otherwise.
        """
        # Single-dependency limitation: only steps with exactly one dependency
        # are eligible for session reuse
        if len(depends_on) != 1:
            return None

        # Look up the dependency's session entry
        entry = self._entries.get(depends_on[0])
        if not entry:
            return None

        # Runner must match for correct context
        if entry.runner_name != runner_name:
            return None

        # Check TTL with per-runner override
        ttl = self._ttl_overrides.get(runner_name, self._default_ttl)
        if time.monotonic() - entry.completed_at > ttl:
            return None

        # Session is reusable
        return entry.session_id
