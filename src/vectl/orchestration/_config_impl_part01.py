from __future__ import annotations
"""
Shared orchestration-plane configuration.

Authority:
- docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7
- docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.1-§8.8

Public surfaces (authority pinned to ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md):
    - OrchestrationConfig            (shared config model / loader / freeze boundary)
    - ResolverToolAllowlist         (resolver tool authorization allowlist surface)
    - freeze_config()                (immutability enforcement boundary)
    - load_orchestration_config()   (config loader surface)

owns:
    ConfigValue, ResolverToolAllowlist, RosterConfig, DispatchConfig,
    RuntimeConfig, ControlConfig, ResolverConfig, RoleFieldProvenance,
    ContinuityConfig, ObservabilityConfig, OperatorConfig, DriveConfig,
    OrchestrationConfig, FrozenConfigSnapshot, ConfigValidationError,
    RoleProfileOverrideError, plus config loading/validation/freeze helpers.

Does not own runtime/execution contract models such as DispatchSpec,
ExecutionRequest, ExecutionResult, DriveRecord, DriveLease, DriveConfigFrozen,
or RoleProfile; those remain contract-owned and may be imported here only to
materialize frozen config and role-profile outputs.

These surfaces are shared orchestration-plane concerns; they are not private
submodules of any single component.
"""


import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypeVar, cast

from typing_extensions import TypeAliasType

import yaml

from vectl.orchestration.contracts import (
    DriveConfigFrozen,
    ExecutionContext,
    MutationPolicy,
    RoleOutputContract,
    RoleProfile,
    SessionPolicy,
)
from vectl.orchestration.tool_registry import (
    CANONICAL_TOOL_FAMILIES,
    validate_tool_allowlist,
)

if TYPE_CHECKING:
    pass

_T = TypeVar("_T")
_E = TypeVar("_E", bound=Exception)
Result = TypeAliasType("Result", Any, type_params=(_T, _E))


# ---------------------------------------------------------------------
# Orchestration Config Model
# ---------------------------------------------------------------------

DEFAULT_ROSTER_TTL_SECONDS: Final[float] = 300.0
DEFAULT_RESOLVER_TIMEOUT_SECONDS: Final[float] = 60.0

# Spec §8.4 defaults
DEFAULT_RUNNER: Final[str] = "codex"
DEFAULT_ARTIFACT_ROOT: Final[str] = ".vectl/runs"
DEFAULT_WORKSPACE_ROOT: Final[str] = ".vectl/workspaces"
DEFAULT_ISOLATION_DEFAULT: Final[str] = "default"
DEFAULT_CLEANUP_POLICY: Final[str] = "on-success"
DEFAULT_IDLE_POLL_INTERVAL_MS: Final[int] = 1000
DEFAULT_MAX_RESOLUTION_ATTEMPTS: Final[int] = 1
DEFAULT_ACTION_ACK_TIMEOUT_SECONDS: Final[float] = 5.0
DEFAULT_RESOLVER_ENABLED: Final[bool] = True
DEFAULT_RESOLVER_TIMEOUT_SECONDS_CFG: Final[float] = 600.0
DEFAULT_MAX_TOOL_CALLS: Final[int] = 100
DEFAULT_MAX_TOOL_ARGUMENT_BYTES: Final[int] = 65536
DEFAULT_RESUME_ENABLED: Final[bool] = True
DEFAULT_STALE_ARTIFACT_POLICY: Final[str] = "quarantine"
DEFAULT_REPLAY_SAFETY: Final[str] = "conservative"
DEFAULT_EVENTS_JSONL: Final[bool] = True
DEFAULT_TEXT_LOG: Final[bool] = True
DEFAULT_PROJECTED_STATE: Final[bool] = True
DEFAULT_HEARTBEAT_STALE_THRESHOLD_SECONDS: Final[int] = 120
DEFAULT_PER_STEP_ARTIFACTS: Final[bool] = True
DEFAULT_PER_CASE_ARTIFACTS: Final[bool] = True
DEFAULT_REDACT_ENV_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)
DEFAULT_MAX_LOG_MEGABYTES: Final[int] = 100
DEFAULT_RETENTION_DAYS: Final[int] = 30
DEFAULT_CONTROL_CHANNEL: Final[str] = "filesystem"
DEFAULT_DEFAULT_OUTPUT: Final[str] = "human"
DEFAULT_MAX_PENDING_ACTIONS: Final[int] = 100
DEFAULT_DISPATCH_ROLE_ID: Final[str] = "python-executor"
DEFAULT_RESOLVER_ROLE_ID: Final[str] = "blocked-case-coordinator"

# Drive config defaults — Authority: §8.4.1
DEFAULT_DRIVE_MAX_PARALLELISM: Final[int] = 4
DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS: Final[int] = 250
DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS: Final[float] = 300.0
DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS: Final[float] = 300.0

# Config file name
CONFIG_FILE_NAME: Final[str] = "vectl.yaml"
# Environment variable prefix
ENV_PREFIX: Final[str] = "VECTL_ORCH_"
# User config directory
USER_CONFIG_DIR: Final[str] = "~/.config/vectl"

# Role profile override fields allowed by RFC §6.1
_ALLOWED_OVERRIDE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "agent_id",
        "prompt_family",
        "execution_context",
        "mutation_policy",
        "session_policy",
        "output_contract",
        "default_runner",
    }
)


# @shell_orchestration: Role registry helper remains colocated with config dataclass defaults and validation surfaces.
def _builtin_role_ids() -> Result[frozenset[str], Exception]:
    """Return the set of built-in role IDs from ``default_role_profiles()``.

    Authority: docs/RFC-role-profile-overrides.md §5.1, §6.1

    Built-in role IDs are the authoritative target set for
    ``role_profile_overrides``. Custom roles defined in
    ``role_profiles`` are not valid override targets.
    """
    return frozenset(p.role_id for p in default_role_profiles())


# ---------------------------------------------------------------------
# Config Value Objects for Provenance Tracking
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigValue:
    """
    A configuration value with explicit provenance tracking.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.7
    """

    value: Any
    source: str  # "flag", "env", "file", "default"


# ---------------------------------------------------------------------
# Orchestration Config Model
# ---------------------------------------------------------------------


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
class DispatchConfig:
    """Dispatch-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-DISPATCH-AND-PROMPT-POLICY.md §8.4, §9.1

    Attributes:
        default_role_id: Role used for ordinary step dispatch when step.agent is
            absent.
    """

    default_role_id: str = DEFAULT_DISPATCH_ROLE_ID


@dataclass(frozen=True)
class RuntimeConfig:
    """
    Runtime-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

    Attributes:
        default_runner: Default runner selection.
        artifact_root: Root for run artifacts.
        workspace_root: Root for workspaces.
        isolation_default: Default step isolation mode.
        cleanup_policy: Workspace cleanup behavior.
    """

    default_runner: str = DEFAULT_RUNNER
    artifact_root: Path = Path(DEFAULT_ARTIFACT_ROOT)
    workspace_root: Path = Path(DEFAULT_WORKSPACE_ROOT)
    isolation_default: str = DEFAULT_ISOLATION_DEFAULT
    cleanup_policy: str = DEFAULT_CLEANUP_POLICY


@dataclass(frozen=True)
class ControlConfig:
    """
    Control-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """

    idle_poll_interval_ms: int = DEFAULT_IDLE_POLL_INTERVAL_MS
    max_resolution_attempts_per_case: int = DEFAULT_MAX_RESOLUTION_ATTEMPTS
    action_ack_timeout_seconds: float = DEFAULT_ACTION_ACK_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ResolverConfig:
    """
    Resolver-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

    Attributes:
        enabled: Enable resolver path.
        tool_allowlist: Explicit allowlist for resolver tool usage.
        invocation_timeout_seconds: Timeout for resolver invocation.
        max_tool_calls_per_invocation: Cap resolver tool-call volume.
        max_tool_argument_bytes: Max size for tool arguments.
    """

    enabled: bool = DEFAULT_RESOLVER_ENABLED
    default_role_id: str = DEFAULT_RESOLVER_ROLE_ID
    tool_allowlist: ResolverToolAllowlist = field(
        default_factory=lambda: ResolverToolAllowlist(allowed_tool_families=("orchestration",))
    )
    invocation_timeout_seconds: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS_CFG
    max_tool_calls_per_invocation: int = DEFAULT_MAX_TOOL_CALLS
    max_tool_argument_bytes: int = DEFAULT_MAX_TOOL_ARGUMENT_BYTES


@dataclass(frozen=True)
class _RoleFamilyPolicy:
    """Centralized invariants for known prompt/role families."""

    execution_context: Literal["linked_worktree", "main_worktree"]
    mutation_policy: Literal["read_only", "worktree_changes", "vectl_facade_only"] | None = None
    session_policy: Literal["reuse_allowed", "reuse_forbidden"] | None = None
    output_contract: RoleOutputContract | None = None


_ROLE_FAMILY_POLICY: dict[str, _RoleFamilyPolicy] = {
    "coder": _RoleFamilyPolicy(
        execution_context="linked_worktree",
        mutation_policy="worktree_changes",
        session_policy="reuse_allowed",
        output_contract="freeform_evidence",
    ),
    "planner": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="vectl_facade_mutation",
    ),
    "reviewer": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="read_only",
        session_policy="reuse_allowed",
        output_contract="structured_review_result",
    ),
    "resolver": _RoleFamilyPolicy(
        execution_context="main_worktree",
        mutation_policy="vectl_facade_only",
        session_policy="reuse_forbidden",
        output_contract="resolution_report",
    ),
}


# @shell_orchestration: Default role registry is a shell config compatibility surface consumed by dataclass factories.
def default_role_profiles() -> Result[tuple[RoleProfile, ...], Exception]:
    """Return built-in configuration defaults for role profiles."""

    return (
        RoleProfile(
            role_id="python-executor",
            agent_id="python-executor",
            prompt_family="coder",
            execution_context="linked_worktree",
            mutation_policy="worktree_changes",
            session_policy="reuse_allowed",
            output_contract="freeform_evidence",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="python-senior",
            agent_id="python-senior",
            prompt_family="coder",
            execution_context="linked_worktree",
            mutation_policy="worktree_changes",
            session_policy="reuse_allowed",
            output_contract="freeform_evidence",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="vectl-planner",
            agent_id="vectl-planner",
            prompt_family="planner",
            execution_context="main_worktree",
            mutation_policy="vectl_facade_only",
            session_policy="reuse_forbidden",
            output_contract="vectl_facade_mutation",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="gate-reviewer",
            agent_id="gate-reviewer",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="doc-reviewer",
            agent_id="doc-reviewer",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="spec-readiness-auditor",
            agent_id="spec-readiness-auditor",
            prompt_family="reviewer",
            execution_context="main_worktree",
            mutation_policy="read_only",
            session_policy="reuse_allowed",
            output_contract="structured_review_result",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="blocked-case-coordinator",
            agent_id="blocked-case-coordinator",
            prompt_family="resolver",
            execution_context="main_worktree",
            mutation_policy="vectl_facade_only",
            session_policy="reuse_forbidden",
            output_contract="resolution_report",
            default_runner="codex",
        ),
        RoleProfile(
            role_id="blocked-case-coordinator-tacit",
            agent_id="blocked-case-coordinator-tacit",
            prompt_family="resolver",
            execution_context="main_worktree",
            mutation_policy="vectl_facade_only",
            session_policy="reuse_forbidden",
            output_contract="resolution_report",
            default_runner="codex",
        ),
    )


# @shell_complexity: Branches mirror role-family invariant checks and preserve field-specific diagnostics.
# @shell_orchestration: Role validation stays with config loading to preserve public error accumulation behavior.
def validate_role_profile(profile: RoleProfile) -> Result[list[str], Exception]:
    """Validate one role profile against centralized family invariants."""

    policy = _ROLE_FAMILY_POLICY.get(profile.prompt_family)
    if policy is None:
        return []

    mismatches: list[str] = []
    if profile.execution_context != policy.execution_context:
        mismatches.append(
            f"execution_context={profile.execution_context!r} expected {policy.execution_context!r}"
        )
    if policy.mutation_policy is not None and profile.mutation_policy != policy.mutation_policy:
        mismatches.append(
            f"mutation_policy={profile.mutation_policy!r} expected {policy.mutation_policy!r}"
        )
    if policy.session_policy is not None and profile.session_policy != policy.session_policy:
        mismatches.append(
            f"session_policy={profile.session_policy!r} expected {policy.session_policy!r}"
        )
    if policy.output_contract is not None and profile.output_contract != policy.output_contract:
        mismatches.append(
            f"output_contract={profile.output_contract!r} expected {policy.output_contract!r}"
        )
    return mismatches


# ---------------------------------------------------------------------
# Role Profile Provenance (RFC §7.1)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RoleFieldProvenance:
    """Provenance for a single role profile field value.

    Authority: docs/RFC-role-profile-overrides.md §7.1

    Attributes:
        value: The effective field value.
        source: Provenance category — one of ``default``, ``override``, ``custom``.
    """

    value: str
    source: Literal["default", "override", "custom"]


# @shell_complexity: Branches distinguish built-in default/override/custom provenance per RFC-role-profile-overrides §7.1.
# @shell_orchestration: Provenance formatting is coupled to orchestration config inspection surfaces.
def build_role_profile_provenance(
    config: OrchestrationConfig,
) -> Result[dict[str, dict[str, RoleFieldProvenance]], Exception]:
    """Build per-field provenance for every role profile in *config*.

    Authority: docs/RFC-role-profile-overrides.md §7.1

    Provenance categories (per §7.1):

    - ``default`` — the value matches the built-in default for this role/field.
    - ``override`` — the role is built-in but the field value differs from the
      built-in default (i.e. a ``role_profile_overrides`` entry patched it).
    - ``custom`` — the role is a user-defined custom role (not a built-in).

    This function inspects the *effective* configuration and compares each
    role profile field against built-in defaults to determine provenance.
    It is a config inspection surface, not a runtime dispatch concern.

    Args:
        config: An effective ``OrchestrationConfig`` (already merged).

    Returns:
        Mapping of ``role_id -> {field_name -> RoleFieldProvenance}``.
    """
    builtin_ids = _builtin_role_ids()
    builtin_defaults = {p.role_id: p for p in default_role_profiles()}
    provenance: dict[str, dict[str, RoleFieldProvenance]] = {}

    # Fields to track provenance for (must match RoleProfile attributes)
    profile_fields = (
        "agent_id",
        "prompt_family",
        "execution_context",
        "mutation_policy",
        "session_policy",
        "output_contract",
        "default_runner",
    )

    for profile in config.role_profiles:
        role_id = profile.role_id
        field_provenance: dict[str, RoleFieldProvenance] = {}

        if role_id not in builtin_ids:
            # Custom role — all fields are "custom" provenance
            for field_name in profile_fields:
                field_value = getattr(profile, field_name)
                field_provenance[field_name] = RoleFieldProvenance(
                    value=str(field_value),
                    source="custom",
                )
        else:
            # Built-in role — compare against defaults
            default_profile = builtin_defaults[role_id]
            for field_name in profile_fields:
                field_value = getattr(profile, field_name)
                default_value = getattr(default_profile, field_name)
                if str(field_value) != str(default_value):
                    source: Literal["default", "override", "custom"] = "override"
                else:
                    source = "default"
                field_provenance[field_name] = RoleFieldProvenance(
                    value=str(field_value),
                    source=source,
                )

        provenance[role_id] = field_provenance

    return provenance


# @shell_orchestration: Registry validation is coupled to config admission diagnostics.
def validate_role_profiles(profiles: tuple[RoleProfile, ...]) -> Result[list[str], Exception]:
    """Validate registry-wide role profile constraints."""

    errors: list[str] = []
    seen_role_ids: set[str] = set()
    for profile in profiles:
        if profile.role_id in seen_role_ids:
            errors.append(f"duplicate role profile {profile.role_id!r}")
        else:
            seen_role_ids.add(profile.role_id)

        family_errors = validate_role_profile(profile)
        if family_errors:
            message = (
                f"Role profile {profile.role_id!r} violates "
                f"{profile.prompt_family!r} family policy: " + ", ".join(family_errors)
            )
            errors.append(message)
    return errors


# ---------------------------------------------------------------------
# Role Profile Override and Merge Logic
# Authority: docs/RFC-role-profile-overrides.md §5, §6
# ---------------------------------------------------------------------


# @shell_complexity: Branches validate role override shape while preserving spec-exact error paths.
# @shell_orchestration: Override parser is coupled to YAML config admission and error paths.
def _parse_role_profile_overrides(
    raw_overrides: dict[str, dict[str, Any]],
) -> Result[dict[str, dict[str, str]], Exception]:
    """Parse raw ``role_profile_overrides`` from config YAML.

    Authority: docs/RFC-role-profile-overrides.md §4, §6.1

    Each entry must be a mapping of field names to string values.
    Unknown fields are preserved here for validation to reject later.

    Args:
        raw_overrides: The ``role_profile_overrides`` dict from parsed YAML.

    Returns:
        Mapping of role_id -> field overrides (string values).

    Raises:
        ValueError: If the overrides structure is malformed.
    """
    if not isinstance(raw_overrides, dict):
        raise ValueError("orchestration.role_profile_overrides must be a mapping")

    parsed: dict[str, dict[str, str]] = {}
    for role_id, fields in raw_overrides.items():
        if not isinstance(fields, dict):
            raise ValueError(f"orchestration.role_profile_overrides.{role_id!r} must be a mapping")
        field_map: dict[str, str] = {}
        for field_name, field_value in fields.items():
            field_map[str(field_name)] = str(field_value)
        parsed[str(role_id)] = field_map

    return parsed


# @shell_orchestration: Custom role parser is coupled to orchestration config admission semantics.
def _parse_custom_role_profiles(
    raw_profiles: dict[str, dict[str, Any]],
) -> Result[tuple[RoleProfile, ...], Exception]:
    """Parse raw ``role_profiles`` entries as custom role definitions.

    Authority: docs/RFC-role-profile-overrides.md §4.2, §5, §6.2

    This parses ``role_profiles`` entries identically to the existing
    ``_dict_to_config`` parsing, producing ``RoleProfile`` instances.

    Args:
        raw_profiles: The ``role_profiles`` dict from parsed YAML.

    Returns:
        Tuple of parsed RoleProfile instances.
    """
    parsed_profiles: list[RoleProfile] = []
    for role_id, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            raise ValueError(f"role profile {role_id!r} must be a mapping")
        agent_id = raw_profile.get("agent_id", role_id)
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError(f"role profile {role_id!r} must define a non-empty agent_id")
        parsed_profiles.append(
            RoleProfile(
                role_id=role_id,
                agent_id=agent_id,
                prompt_family=str(raw_profile.get("prompt_family", "")),
                execution_context=cast(
                    ExecutionContext,
                    str(raw_profile.get("execution_context", "linked_worktree")),
                ),
                mutation_policy=cast(
                    MutationPolicy,
                    str(raw_profile.get("mutation_policy", "read_only")),
                ),
                session_policy=cast(
                    SessionPolicy,
                    str(raw_profile.get("session_policy", "reuse_forbidden")),
                ),
                output_contract=cast(
                    RoleOutputContract,
                    str(raw_profile.get("output_contract", "freeform_evidence")),
                ),
                default_runner=str(raw_profile.get("default_runner", DEFAULT_RUNNER)),
            )
        )
    return tuple(parsed_profiles)


# @shell_orchestration: Override application remains adjacent to config merge validation for RFC compatibility.
