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

These surfaces are shared orchestration-plane concerns; they are not private
submodules of any single component.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast

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


def _builtin_role_ids() -> frozenset[str]:
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


def default_role_profiles() -> tuple[RoleProfile, ...]:
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


def validate_role_profile(profile: RoleProfile) -> list[str]:
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


def build_role_profile_provenance(
    config: OrchestrationConfig,
) -> dict[str, dict[str, RoleFieldProvenance]]:
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


def validate_role_profiles(profiles: tuple[RoleProfile, ...]) -> list[str]:
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


def _parse_role_profile_overrides(
    raw_overrides: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
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


def _parse_custom_role_profiles(
    raw_profiles: dict[str, dict[str, Any]],
) -> tuple[RoleProfile, ...]:
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


def _apply_role_profile_override(
    base_profile: RoleProfile,
    overrides: dict[str, str],
) -> RoleProfile:
    """Apply field overrides to a built-in role profile.

    Authority: docs/RFC-role-profile-overrides.md §5

    Only allowed fields are applied; unknown fields should have been
    rejected by validation prior to calling this function.

    Args:
        base_profile: The built-in RoleProfile to patch.
        overrides: Field name -> value overrides.

    Returns:
        A new RoleProfile with overrides applied.
    """
    profile_dict = {
        "agent_id": base_profile.agent_id,
        "prompt_family": base_profile.prompt_family,
        "execution_context": base_profile.execution_context,
        "mutation_policy": base_profile.mutation_policy,
        "session_policy": base_profile.session_policy,
        "output_contract": base_profile.output_contract,
        "default_runner": base_profile.default_runner,
    }

    for field_name, field_value in overrides.items():
        if field_name in _ALLOWED_OVERRIDE_FIELDS:
            profile_dict[field_name] = field_value

    return RoleProfile(
        role_id=base_profile.role_id,
        agent_id=profile_dict["agent_id"],
        prompt_family=profile_dict["prompt_family"],
        execution_context=cast(ExecutionContext, profile_dict["execution_context"]),
        mutation_policy=cast(MutationPolicy, profile_dict["mutation_policy"]),
        session_policy=cast(SessionPolicy, profile_dict["session_policy"]),
        output_contract=cast(RoleOutputContract, profile_dict["output_contract"]),
        default_runner=profile_dict["default_runner"],
    )


def _merge_role_profiles(
    builtin_profiles: tuple[RoleProfile, ...],
    overrides: dict[str, dict[str, str]],
    custom_profiles: tuple[RoleProfile, ...],
) -> tuple[RoleProfile, ...]:
    """Build the effective role profile registry per RFC §5.

    Authority: docs/RFC-role-profile-overrides.md §5

    Resolution order:
    1. Start from ``default_role_profiles()``
    2. Apply ``role_profile_overrides`` to matching built-in roles
    3. Add explicit custom roles from ``role_profiles``
    4. Validate the final registry

    Args:
        builtin_profiles: Built-in role profiles.
        overrides: Parsed overrides (role_id -> field -> value).
        custom_profiles: Custom role profiles from ``role_profiles``.

    Returns:
        Effective tuple of RoleProfile instances.

    Raises:
        RoleProfileOverrideError: If validation rules from §6 are violated.
    """
    builtin_ids = _builtin_role_ids()
    custom_ids = {p.role_id for p in custom_profiles}
    errors: list[RoleProfileOverrideError] = []

    # §6.1 rule 1: override targets must exist in built-ins
    for role_id in overrides:
        if role_id not in builtin_ids:
            errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}",
                    value=role_id,
                    reason=f"unknown built-in role in role_profile_overrides: {role_id!r}",
                )
            )

    # §6.1 rule 3: override targets cannot reference custom roles
    for role_id in overrides:
        if role_id in custom_ids:
            errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}",
                    value=role_id,
                    reason=(
                        f"role_profile_overrides.{role_id} cannot target custom role "
                        f"defined in role_profiles"
                    ),
                )
            )

    # §6.1 rule 2: override fields must be in allowed set
    for role_id, field_overrides in overrides.items():
        for field_name in field_overrides:
            if field_name not in _ALLOWED_OVERRIDE_FIELDS:
                errors.append(
                    RoleProfileOverrideError(
                        field=f"role_profile_overrides.{role_id}.{field_name}",
                        value=field_name,
                        reason=(
                            f"unknown override field {field_name!r}; "
                            f"allowed fields: {sorted(_ALLOWED_OVERRIDE_FIELDS)}"
                        ),
                    )
                )

    # §6.2 rule 2/4: custom role_ids must not shadow built-ins
    for profile in custom_profiles:
        if profile.role_id in builtin_ids:
            errors.append(
                RoleProfileOverrideError(
                    field=f"role_profiles.{profile.role_id}",
                    value=profile.role_id,
                    reason=(
                        f"role_profiles.{profile.role_id} shadows built-in role; "
                        f"use role_profile_overrides instead"
                    ),
                )
            )

    if errors:
        # Raise the first error with full context
        first = errors[0]
        all_messages = "; ".join(e.reason for e in errors)
        raise RoleProfileOverrideError(first.field, first.value, all_messages)

    # Step 1-2: Build effective built-in profiles with overrides applied
    profile_by_id: dict[str, RoleProfile] = {}
    for profile in builtin_profiles:
        if profile.role_id in overrides:
            profile = _apply_role_profile_override(profile, overrides[profile.role_id])
        profile_by_id[profile.role_id] = profile

    # Step 3: Add custom profiles (no shadowing since validated above)
    for profile in custom_profiles:
        if profile.role_id in profile_by_id:
            # Should not happen due to validation, but defensive check
            raise RoleProfileOverrideError(
                field=f"role_profiles.{profile.role_id}",
                value=profile.role_id,
                reason=(
                    f"role_profiles.{profile.role_id} shadows built-in role; "
                    f"use role_profile_overrides instead"
                ),
            )
        profile_by_id[profile.role_id] = profile

    # §6.1 rule 4 / §6.3: Family-policy validation for overridden profiles.
    # Overridden profiles must still satisfy family invariants.
    # Errors reference the override path per RFC §7.2.
    family_errors: list[RoleProfileOverrideError] = []
    for role_id in overrides:
        effective = profile_by_id[role_id]
        policy = _ROLE_FAMILY_POLICY.get(effective.prompt_family)
        if policy is None:
            continue
        # Check each overridden field against family policy
        if effective.execution_context != policy.execution_context:
            family_errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}.execution_context",
                    value=effective.execution_context,
                    reason=(
                        f"role_profile_overrides.{role_id}.execution_context "
                        f"violates {effective.prompt_family} family policy"
                    ),
                )
            )
        if (
            policy.mutation_policy is not None
            and effective.mutation_policy != policy.mutation_policy
        ):
            family_errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}.mutation_policy",
                    value=effective.mutation_policy,
                    reason=(
                        f"role_profile_overrides.{role_id}.mutation_policy "
                        f"violates {effective.prompt_family} family policy"
                    ),
                )
            )
        if policy.session_policy is not None and effective.session_policy != policy.session_policy:
            family_errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}.session_policy",
                    value=effective.session_policy,
                    reason=(
                        f"role_profile_overrides.{role_id}.session_policy "
                        f"violates {effective.prompt_family} family policy"
                    ),
                )
            )
        if (
            policy.output_contract is not None
            and effective.output_contract != policy.output_contract
        ):
            family_errors.append(
                RoleProfileOverrideError(
                    field=f"role_profile_overrides.{role_id}.output_contract",
                    value=effective.output_contract,
                    reason=(
                        f"role_profile_overrides.{role_id}.output_contract "
                        f"violates {effective.prompt_family} family policy"
                    ),
                )
            )

    if family_errors:
        first = family_errors[0]
        all_messages = "; ".join(e.reason for e in family_errors)
        raise RoleProfileOverrideError(first.field, first.value, all_messages)

    # Maintain ordering: built-in profiles first (in their original order),
    # then custom profiles (in their definition order)
    builtin_order = [p.role_id for p in builtin_profiles]
    ordered_ids = builtin_order + [
        p.role_id for p in custom_profiles if p.role_id not in builtin_order
    ]

    return tuple(profile_by_id[rid] for rid in ordered_ids if rid in profile_by_id)


@dataclass(frozen=True)
class ContinuityConfig:
    """
    Continuity-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """

    resume_enabled: bool = DEFAULT_RESUME_ENABLED
    stale_artifact_policy: str = DEFAULT_STALE_ARTIFACT_POLICY
    replay_safety: str = DEFAULT_REPLAY_SAFETY


@dataclass(frozen=True)
class ObservabilityConfig:
    """
    Observability-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """

    events_jsonl: bool = DEFAULT_EVENTS_JSONL
    text_log: bool = DEFAULT_TEXT_LOG
    projected_state: bool = DEFAULT_PROJECTED_STATE
    heartbeat_stale_threshold_seconds: int = DEFAULT_HEARTBEAT_STALE_THRESHOLD_SECONDS
    per_step_artifacts: bool = DEFAULT_PER_STEP_ARTIFACTS
    per_case_artifacts: bool = DEFAULT_PER_CASE_ARTIFACTS
    redact_env_keys: tuple[str, ...] = DEFAULT_REDACT_ENV_KEYS
    max_log_megabytes: int = DEFAULT_MAX_LOG_MEGABYTES
    retention_days: int = DEFAULT_RETENTION_DAYS


@dataclass(frozen=True)
class OperatorConfig:
    """
    Operator-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4
    """

    control_channel: str = DEFAULT_CONTROL_CHANNEL
    default_output: str = DEFAULT_DEFAULT_OUTPUT
    max_pending_actions: int = DEFAULT_MAX_PENDING_ACTIONS


@dataclass(frozen=True)
class DriveConfig:
    """Drive-component configuration fragment.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4.1

    Attributes:
        max_parallelism: Maximum concurrent step child runs. Must be in [1, 32].
        collect_poll_interval_ms: Polling interval for child-run collection. Must be > 0.
        resolver_timeout_seconds: Timeout for resolver child runs. Must be > 0.
        planner_timeout_seconds: Timeout for planner child runs. Must be > 0.
    """

    max_parallelism: int = DEFAULT_DRIVE_MAX_PARALLELISM
    collect_poll_interval_ms: int = DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS
    resolver_timeout_seconds: float = DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS
    planner_timeout_seconds: float = DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS


@dataclass(frozen=True)
class OrchestrationConfig:
    """
    Shared orchestration-plane configuration model.

    Authority: docs/ORCHESTRATION-PLANE-IMPLEMENTATION-DESIGN.md section 3.7
    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4

    This model is the canonical configuration boundary. It aggregates
    component-specific fragments under one frozen immutable envelope.

    Attributes:
        plan_path: Authoritative plan definition file path.
        roster: Roster-component configuration.
        runtime: Runtime-component configuration.
        control: Control-component configuration.
        resolver: Resolver-component configuration.
        continuity: Continuity-component configuration.
        observability: Observability-component configuration.
        operator: Operator-component configuration.
        drive: Drive-component configuration.
    """

    plan_path: Path = Path("plan.yaml")
    role_profiles: tuple[RoleProfile, ...] = field(default_factory=default_role_profiles)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
    roster: RosterConfig = field(default_factory=RosterConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    resolver: ResolverConfig = field(default_factory=ResolverConfig)
    continuity: ContinuityConfig = field(default_factory=ContinuityConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    drive: DriveConfig = field(default_factory=DriveConfig)

    def freeze(self) -> OrchestrationConfig:
        """
        Return this config as an immutable frozen instance.

        Returns:
            A frozen (immutable) view of this configuration.
        """
        return self


# ---------------------------------------------------------------------
# Frozen Config Snapshot
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenConfigSnapshot:
    """
    A frozen configuration snapshot with provenance metadata.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.8

    Attributes:
        config: The frozen OrchestrationConfig.
        snapshot_path: Path where the snapshot was written (if written).
        created_at: Timestamp when the snapshot was created.
        drive_provenance: Provenance metadata identifying which source each
            drive config value came from. Maps dotted key -> source.
    """

    config: OrchestrationConfig
    snapshot_path: Path | None = None
    created_at: float | None = None
    drive_provenance: dict[str, str] = field(default_factory=dict)


def write_frozen_snapshot(
    config: OrchestrationConfig,
    run_dir: Path,
) -> Path:
    """
    Write a frozen configuration snapshot to the run directory.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.8

    Writes .vectl/runs/<run_id>/config.snapshot.yaml

    Args:
        config: The frozen configuration to snapshot.
        run_dir: The run directory path.

    Returns:
        The path where the snapshot was written.

    Raises:
        OSError: If the snapshot cannot be written.
    """
    snapshot_path = run_dir / "config.snapshot.yaml"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize config to YAML
    snapshot_data = _config_to_dict(config)
    with snapshot_path.open("w") as f:
        yaml.dump(snapshot_data, f, sort_keys=False, default_flow_style=False)

    return snapshot_path


def load_frozen_snapshot(snapshot_path: Path) -> OrchestrationConfig:
    """Load a persisted frozen run snapshot.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.8

    Args:
        snapshot_path: Path to ``config.snapshot.yaml``.

    Returns:
        Reconstructed ``OrchestrationConfig`` from snapshot content.

    Raises:
        FileNotFoundError: If the snapshot file is missing.
        ValueError: If snapshot structure is not a mapping.
    """
    if not snapshot_path.exists():
        raise FileNotFoundError(f"frozen config snapshot not found: {snapshot_path}")
    payload = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
    if payload is None:
        return OrchestrationConfig()
    if not isinstance(payload, dict):
        raise ValueError(f"invalid frozen config snapshot payload: {snapshot_path}")
    payload = _normalize_legacy_frozen_snapshot_payload(payload)
    return _dict_to_config(payload)


def _normalize_legacy_frozen_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize pre-RFC frozen snapshots into current role-profile shape.

    Older ``config.snapshot.yaml`` files serialized the full effective registry
    under ``orchestration.role_profiles``, including built-in role IDs. Current
    config admission correctly rejects that shape for live config files because
    built-in changes must be represented as ``role_profile_overrides``. Frozen
    run snapshots are historical recovery artifacts, so recovery must tolerate
    the old shape by translating only built-in entries into override deltas.
    """

    orch = payload.get("orchestration")
    if not isinstance(orch, dict):
        return payload
    raw_profiles = orch.get("role_profiles")
    if not isinstance(raw_profiles, dict):
        return payload

    builtin_defaults = {profile.role_id: profile for profile in default_role_profiles()}
    builtin_legacy_profiles = {
        str(role_id): raw_profile
        for role_id, raw_profile in raw_profiles.items()
        if str(role_id) in builtin_defaults
    }
    if not builtin_legacy_profiles:
        return payload

    normalized_payload = dict(payload)
    normalized_orch = dict(orch)
    normalized_payload["orchestration"] = normalized_orch

    custom_profiles = {
        role_id: raw_profile
        for role_id, raw_profile in raw_profiles.items()
        if str(role_id) not in builtin_defaults
    }
    if custom_profiles:
        normalized_orch["role_profiles"] = custom_profiles
    else:
        normalized_orch.pop("role_profiles", None)

    raw_existing_overrides = normalized_orch.get("role_profile_overrides", {})
    existing_overrides = raw_existing_overrides if isinstance(raw_existing_overrides, dict) else {}
    normalized_overrides: dict[str, dict[str, str]] = {
        str(role_id): {
            str(field_name): str(field_value)
            for field_name, field_value in fields.items()
        }
        for role_id, fields in existing_overrides.items()
        if isinstance(fields, dict)
    }

    for role_id, raw_profile in builtin_legacy_profiles.items():
        if not isinstance(raw_profile, dict):
            continue
        default_profile = builtin_defaults[role_id]
        delta: dict[str, str] = {}
        for field_name in _ALLOWED_OVERRIDE_FIELDS:
            if field_name not in raw_profile:
                continue
            raw_value = str(raw_profile[field_name])
            if raw_value != str(getattr(default_profile, field_name)):
                delta[field_name] = raw_value
        if delta:
            normalized_overrides[role_id] = {
                **delta,
                **normalized_overrides.get(role_id, {}),
            }

    if normalized_overrides:
        normalized_orch["role_profile_overrides"] = normalized_overrides
    else:
        normalized_orch.pop("role_profile_overrides", None)

    return normalized_payload


def _serialize_tool_allowlist(
    allowlist: ResolverToolAllowlist | dict[str, tuple[str, ...]] | None,
) -> dict[str, list[str]]:
    """Serialize tool allowlist to dict format for YAML."""
    if allowlist is None:
        return {}
    if isinstance(allowlist, ResolverToolAllowlist):
        # Convert families tuple to a dict with empty lists (tool names come from config)
        return {family: [] for family in allowlist.allowed_tool_families}
    # It's already a dict
    return {family: list(tools) for family, tools in allowlist.items()}


def _config_to_dict(config: OrchestrationConfig) -> dict[str, Any]:
    """Convert OrchestrationConfig to a dict for YAML serialization.

    Authority: docs/RFC-role-profile-overrides.md §4, §5

    Serialization symmetry contract: the dict produced by this function
    MUST be consumable by ``_dict_to_config`` without validation errors.
    This requires separating built-in profile changes into
    ``role_profile_overrides`` and custom profiles into ``role_profiles``,
    matching the RFC clean-break semantics that ``_dict_to_config`` enforces.

    Built-in role IDs that differ from defaults are emitted as
    ``role_profile_overrides`` (only the changed fields), preserving
    the RFC §6.1/§6.2 invariant that ``role_profiles`` is custom-only.
    """
    # Separate built-in overrides from custom role profiles per RFC §4/§5.
    builtin_ids = _builtin_role_ids()
    builtin_defaults = {p.role_id: p for p in default_role_profiles()}

    # Track fields for provenance to determine which built-in fields changed.
    profile_fields = (
        "agent_id",
        "prompt_family",
        "execution_context",
        "mutation_policy",
        "session_policy",
        "output_contract",
        "default_runner",
    )

    role_profile_overrides: dict[str, dict[str, str]] = {}
    role_profiles: dict[str, dict[str, str]] = {}

    for profile in config.role_profiles:
        profile_dict = {
            "agent_id": profile.agent_id,
            "prompt_family": profile.prompt_family,
            "execution_context": profile.execution_context,
            "mutation_policy": profile.mutation_policy,
            "session_policy": profile.session_policy,
            "output_contract": profile.output_contract,
            "default_runner": profile.default_runner,
        }

        if profile.role_id in builtin_ids:
            # Built-in role: emit only changed fields as an override.
            default_profile = builtin_defaults[profile.role_id]
            overrides: dict[str, str] = {}
            for field_name in profile_fields:
                current_value = getattr(profile, field_name)
                default_value = getattr(default_profile, field_name)
                if str(current_value) != str(default_value):
                    overrides[field_name] = str(current_value)
            if overrides:
                role_profile_overrides[profile.role_id] = overrides
            # If no fields changed, the default is implicit — omit entirely.
        else:
            # Custom role: emit full profile under role_profiles.
            role_profiles[profile.role_id] = profile_dict

    orch: dict[str, Any] = {"plan_path": str(config.plan_path)}

    # Conditionally include role_profiles and role_profile_overrides
    # to preserve RFC §4/§5 clean-break semantics and enable round-trip.
    if role_profiles:
        orch["role_profiles"] = role_profiles
    if role_profile_overrides:
        orch["role_profile_overrides"] = role_profile_overrides

    orch["dispatch"] = {
        "default_role_id": config.dispatch.default_role_id,
    }
    orch["roster"] = {
        "default_ttl_seconds": config.roster.default_ttl_seconds,
        "max_reuse_window_seconds": config.roster.max_reuse_window_seconds,
    }
    orch["runtime"] = {
        "default_runner": config.runtime.default_runner,
        "artifact_root": str(config.runtime.artifact_root),
        "workspace_root": str(config.runtime.workspace_root),
        "isolation_default": config.runtime.isolation_default,
        "cleanup_policy": config.runtime.cleanup_policy,
    }
    orch["control"] = {
        "idle_poll_interval_ms": config.control.idle_poll_interval_ms,
        "max_resolution_attempts_per_case": config.control.max_resolution_attempts_per_case,
        "action_ack_timeout_seconds": config.control.action_ack_timeout_seconds,
    }
    orch["resolver"] = {
        "enabled": config.resolver.enabled,
        "default_role_id": config.resolver.default_role_id,
        "timeout_seconds": config.resolver.invocation_timeout_seconds,
        "max_tool_calls_per_invocation": config.resolver.max_tool_calls_per_invocation,
        "max_tool_argument_bytes": config.resolver.max_tool_argument_bytes,
        "tool_allowlist": _serialize_tool_allowlist(config.resolver.tool_allowlist),
    }
    orch["continuity"] = {
        "resume_enabled": config.continuity.resume_enabled,
        "stale_artifact_policy": config.continuity.stale_artifact_policy,
        "replay_safety": config.continuity.replay_safety,
    }
    orch["observability"] = {
        "events_jsonl": config.observability.events_jsonl,
        "text_log": config.observability.text_log,
        "projected_state": config.observability.projected_state,
        "heartbeat_stale_threshold_seconds": (
            config.observability.heartbeat_stale_threshold_seconds
        ),
        "per_step_artifacts": config.observability.per_step_artifacts,
        "per_case_artifacts": config.observability.per_case_artifacts,
        "redact_env_keys": list(config.observability.redact_env_keys),
        "max_log_megabytes": config.observability.max_log_megabytes,
        "retention_days": config.observability.retention_days,
    }
    orch["operator"] = {
        "control_channel": config.operator.control_channel,
        "default_output": config.operator.default_output,
        "max_pending_actions": config.operator.max_pending_actions,
    }
    orch["drive"] = {
        "max_parallelism": config.drive.max_parallelism,
        "collect_poll_interval_ms": config.drive.collect_poll_interval_ms,
        "resolver_timeout_seconds": config.drive.resolver_timeout_seconds,
        "planner_timeout_seconds": config.drive.planner_timeout_seconds,
    }

    return {"orchestration": orch}


# ---------------------------------------------------------------------
# Config Discovery and Loading
# ---------------------------------------------------------------------
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.1


def _discover_config_file(explicit_path: Path | str | None = None) -> tuple[Path | None, str]:
    """
    Discover the config file path using the spec-defined discovery order.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.1

    Discovery order:
    1. VECTL_CONFIG env var
    2. cwd ./vectl.yaml
    3. repository root vectl.yaml
    4. user config ~/.config/vectl/vectl.yaml

    Args:
        explicit_path: Optional explicit path (e.g., from --config CLI flag).

    Returns:
        Tuple of (config_path, source) where source indicates discovery method.
        Returns (None, "none") if no config file found.
    """
    import subprocess

    # Explicit path takes highest priority (--config CLI flag)
    if explicit_path is not None:
        p = Path(explicit_path)
        if p.exists() and p.is_file():
            return (p, "cli")
        raise FileNotFoundError(f"Config file explicitly specified but not found: {p}")

    # 1. VECTL_CONFIG env var
    env_path = os.environ.get("VECTL_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return (p, "env")
        raise FileNotFoundError(f"VECTL_CONFIG points to missing or unreadable file: {p}")

    # 2. cwd ./vectl.yaml
    cwd_config = Path.cwd() / CONFIG_FILE_NAME
    if cwd_config.exists() and cwd_config.is_file():
        return (cwd_config, "file")

    # 3. repository root vectl.yaml
    try:
        # Find git/worktree root
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            repo_root = Path(result.stdout.strip())
            repo_config = repo_root / CONFIG_FILE_NAME
            if repo_config.exists() and repo_config.is_file():
                # Cwd-local takes priority if both exist
                if cwd_config.exists() and cwd_config.resolve() != repo_config.resolve():
                    return (cwd_config, "file")
                return (repo_config, "file")
    except (subprocess.SubprocessError, OSError):
        pass

    # 4. user config ~/.config/vectl/vectl.yaml
    user_config = Path(os.path.expanduser(USER_CONFIG_DIR)) / CONFIG_FILE_NAME
    if user_config.exists() and user_config.is_file():
        return (user_config, "file")

    return (None, "none")


def _parse_config_yaml(yaml_content: str) -> dict[str, Any]:
    """Parse YAML content into a dict, returning empty dict for empty/missing content."""
    if not yaml_content.strip():
        return {}
    return yaml.safe_load(yaml_content) or {}


def _flatten_env_vars(prefix: str = ENV_PREFIX) -> dict[str, str]:
    """
    Extract orchestration-related environment variables with the given prefix.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.3

    Args:
        prefix: Environment variable prefix (default: VECTL_ORCH_).

    Returns:
        Dict mapping config keys (without prefix) to env values.
    """
    field_aliases = {
        "runtime_artifact_root": "runtime.artifact_root",
        "runtime_workspace_root": "runtime.workspace_root",
        "runtime_default_runner": "runtime.default_runner",
        "runtime_isolation_default": "runtime.isolation_default",
        "runtime_cleanup_policy": "runtime.cleanup_policy",
        "dispatch_default_role_id": "dispatch.default_role_id",
        "control_idle_poll_interval_ms": "control.idle_poll_interval_ms",
        "control_max_resolution_attempts_per_case": "control.max_resolution_attempts_per_case",
        "control_action_ack_timeout_seconds": "control.action_ack_timeout_seconds",
        "resolver_enabled": "resolver.enabled",
        "resolver_default_role_id": "resolver.default_role_id",
        "resolver_timeout_seconds": "resolver.timeout_seconds",
        "resolver_max_tool_calls_per_invocation": "resolver.max_tool_calls_per_invocation",
        "resolver_max_tool_argument_bytes": "resolver.max_tool_argument_bytes",
        "continuity_resume_enabled": "continuity.resume_enabled",
        "continuity_stale_artifact_policy": "continuity.stale_artifact_policy",
        "continuity_replay_safety": "continuity.replay_safety",
        "observability_events_jsonl": "observability.events_jsonl",
        "observability_text_log": "observability.text_log",
        "observability_projected_state": "observability.projected_state",
        "observability_heartbeat_stale_threshold_seconds": (
            "observability.heartbeat_stale_threshold_seconds"
        ),
        "observability_per_step_artifacts": "observability.per_step_artifacts",
        "observability_per_case_artifacts": "observability.per_case_artifacts",
        "observability_max_log_megabytes": "observability.max_log_megabytes",
        "observability_retention_days": "observability.retention_days",
        "operator_control_channel": "operator.control_channel",
        "operator_default_output": "operator.default_output",
        "operator_max_pending_actions": "operator.max_pending_actions",
        "plan_path": "plan_path",
        "drive_max_parallelism": "drive.max_parallelism",
        "drive_collect_poll_interval_ms": "drive.collect_poll_interval_ms",
        "drive_resolver_timeout_seconds": "drive.resolver_timeout_seconds",
        "drive_planner_timeout_seconds": "drive.planner_timeout_seconds",
    }
    result = {}
    for key, value in os.environ.items():
        if key.startswith(prefix):
            # Remove prefix and convert to config key format
            config_key = key[len(prefix) :].lower()
            result[field_aliases.get(config_key, config_key.replace("_", "."))] = value
    return result


def _apply_env_overrides(
    config: OrchestrationConfig,
    env_vars: dict[str, str],
) -> OrchestrationConfig:
    """
    Apply environment variable overrides to a config.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.2, §8.3

    Args:
        config: The base config to apply overrides to.
        env_vars: Dict of config keys to values from environment.

    Returns:
        New OrchestrationConfig with env overrides applied.
    """
    updates: dict[str, Any] = {}

    for key, value in env_vars.items():
        if key == "plan_path":
            updates["plan_path"] = Path(value)
        elif key == "dispatch.default_role_id":
            updates.setdefault("dispatch", {})["default_role_id"] = value
        elif key.startswith("runtime."):
            subkey = key[len("runtime.") :]
            if subkey == "artifact_root" or subkey == "workspace_root":
                updates.setdefault("runtime", {})[subkey] = Path(value)
            else:
                updates.setdefault("runtime", {})[subkey] = value
        elif key.startswith("control."):
            subkey = key[len("control.") :]
            if subkey.endswith("_seconds"):
                updates.setdefault("control", {})[subkey] = float(value)
            elif subkey.endswith("_ms"):
                updates.setdefault("control", {})[subkey] = int(value)
            else:
                updates.setdefault("control", {})[subkey] = (
                    int(value) if isinstance(_get_default(key), int) else value
                )
        elif key.startswith("resolver."):
            subkey = key[len("resolver.") :]
            if subkey == "timeout_seconds":
                # NOTE: _dict_to_config reads 'timeout_seconds' from the dict
                updates.setdefault("resolver", {})["timeout_seconds"] = float(value)
            elif subkey == "enabled":
                updates.setdefault("resolver", {})["enabled"] = value.lower() in (
                    "true",
                    "1",
                    "yes",
                )
            elif subkey == "default_role_id":
                updates.setdefault("resolver", {})["default_role_id"] = value
            else:
                updates.setdefault("resolver", {})[subkey] = (
                    int(value) if isinstance(_get_default(key), int) else value
                )
        elif key.startswith("continuity."):
            subkey = key[len("continuity.") :]
            updates.setdefault("continuity", {})[subkey] = value
        elif key.startswith("observability."):
            subkey = key[len("observability.") :]
            updates.setdefault("observability", {})[subkey] = value
        elif key.startswith("operator."):
            subkey = key[len("operator.") :]
            updates.setdefault("operator", {})[subkey] = value
        elif key.startswith("drive."):
            subkey = key[len("drive.") :]
            if subkey.endswith("_seconds"):
                updates.setdefault("drive", {})[subkey] = float(value)
            elif subkey.endswith("_ms") or subkey == "max_parallelism":
                updates.setdefault("drive", {})[subkey] = int(value)
            else:
                updates.setdefault("drive", {})[subkey] = value

    return _deep_update_config(config, {"orchestration": updates})


def _get_default(key: str) -> Any:
    """Get the default value for a config key for type coercion."""
    defaults = {
        "runtime.default_runner": DEFAULT_RUNNER,
        "runtime.artifact_root": DEFAULT_ARTIFACT_ROOT,
        "runtime.workspace_root": DEFAULT_WORKSPACE_ROOT,
        "runtime.isolation_default": DEFAULT_ISOLATION_DEFAULT,
        "runtime.cleanup_policy": DEFAULT_CLEANUP_POLICY,
        "dispatch.default_role_id": DEFAULT_DISPATCH_ROLE_ID,
        "control.idle_poll_interval_ms": DEFAULT_IDLE_POLL_INTERVAL_MS,
        "control.max_resolution_attempts_per_case": DEFAULT_MAX_RESOLUTION_ATTEMPTS,
        "control.action_ack_timeout_seconds": DEFAULT_ACTION_ACK_TIMEOUT_SECONDS,
        "resolver.enabled": DEFAULT_RESOLVER_ENABLED,
        "resolver.default_role_id": DEFAULT_RESOLVER_ROLE_ID,
        "resolver.timeout_seconds": DEFAULT_RESOLVER_TIMEOUT_SECONDS_CFG,
        "resolver.max_tool_calls_per_invocation": DEFAULT_MAX_TOOL_CALLS,
        "resolver.max_tool_argument_bytes": DEFAULT_MAX_TOOL_ARGUMENT_BYTES,
        "continuity.resume_enabled": DEFAULT_RESUME_ENABLED,
        "continuity.stale_artifact_policy": DEFAULT_STALE_ARTIFACT_POLICY,
        "continuity.replay_safety": DEFAULT_REPLAY_SAFETY,
        "observability.events_jsonl": DEFAULT_EVENTS_JSONL,
        "observability.text_log": DEFAULT_TEXT_LOG,
        "observability.projected_state": DEFAULT_PROJECTED_STATE,
        "observability.heartbeat_stale_threshold_seconds": (
            DEFAULT_HEARTBEAT_STALE_THRESHOLD_SECONDS
        ),
        "observability.per_step_artifacts": DEFAULT_PER_STEP_ARTIFACTS,
        "observability.per_case_artifacts": DEFAULT_PER_CASE_ARTIFACTS,
        "observability.max_log_megabytes": DEFAULT_MAX_LOG_MEGABYTES,
        "observability.retention_days": DEFAULT_RETENTION_DAYS,
        "operator.control_channel": DEFAULT_CONTROL_CHANNEL,
        "operator.default_output": DEFAULT_DEFAULT_OUTPUT,
        "operator.max_pending_actions": DEFAULT_MAX_PENDING_ACTIONS,
        "drive.max_parallelism": DEFAULT_DRIVE_MAX_PARALLELISM,
        "drive.collect_poll_interval_ms": DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS,
        "drive.resolver_timeout_seconds": DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS,
        "drive.planner_timeout_seconds": DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS,
    }
    return defaults.get(key)


def _deep_update_config(
    base: OrchestrationConfig,
    updates: dict[str, Any],
) -> OrchestrationConfig:
    """Apply a nested dict update to an OrchestrationConfig."""
    # Build new config by replacing affected sub-objects
    new_config_dict = _config_to_dict(base)

    def deep_merge(target: dict, source: dict) -> None:
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                deep_merge(target[k], v)
            else:
                target[k] = v

    deep_merge(new_config_dict, updates)

    # Reconstruct OrchestrationConfig
    return _dict_to_config(new_config_dict)


def _dict_to_config(data: dict[str, Any]) -> OrchestrationConfig:
    """Convert a dict to an OrchestrationConfig.

    Authority: docs/RFC-role-profile-overrides.md §5

    Resolution order for role profiles per the RFC:

    1. Start from ``default_role_profiles()``
    2. Apply ``role_profile_overrides`` to matching built-in roles
    3. Add explicit custom roles from ``role_profiles``
    4. Validate the final registry

    This keeps ``role_profiles`` as custom-role definitions only (§4).
    ``role_profile_overrides`` patches built-in roles (§4, §6.1).
    """
    orch = data.get("orchestration", {})

    # --- Role profile merge (RFC §5) ---
    raw_role_profile_overrides = orch.get("role_profile_overrides")
    raw_role_profiles = orch.get("role_profiles")

    if raw_role_profile_overrides is not None or raw_role_profiles is not None:
        # RFC §5: Build effective registry through merge
        # 1. Start from built-in defaults
        builtin_profiles = default_role_profiles()

        # 2. Parse overrides (may be empty)
        overrides: dict[str, dict[str, str]] = {}
        if raw_role_profile_overrides is not None:
            overrides = _parse_role_profile_overrides(raw_role_profile_overrides)

        # 3. Parse custom role profiles (may be empty)
        custom_profiles: tuple[RoleProfile, ...] = ()
        if raw_role_profiles is not None:
            if not isinstance(raw_role_profiles, dict):
                raise ValueError("orchestration.role_profiles must be a mapping")
            custom_profiles = _parse_custom_role_profiles(raw_role_profiles)

        # 4. Merge and validate (raises RoleProfileOverrideError on violations)
        role_profiles = _merge_role_profiles(builtin_profiles, overrides, custom_profiles)
    elif raw_role_profiles is None:
        # No overrides, no custom roles: use built-in defaults
        role_profiles = default_role_profiles()

    # Parse resolver tool_allowlist
    raw_allowlist = orch.get("resolver", {}).get("tool_allowlist", {})
    if isinstance(raw_allowlist, dict):
        # Convert dict format to ResolverToolAllowlist
        families = tuple(raw_allowlist.keys())
        resolved_allowlist = ResolverToolAllowlist(
            allowed_tool_families=families,
            deployment_narrowings=(),
        )
    elif isinstance(raw_allowlist, ResolverToolAllowlist):
        resolved_allowlist = raw_allowlist
    else:
        resolved_allowlist = ResolverToolAllowlist()

    return OrchestrationConfig(
        plan_path=Path(orch.get("plan_path", "plan.yaml")),
        role_profiles=role_profiles,
        dispatch=DispatchConfig(
            default_role_id=orch.get("dispatch", {}).get(
                "default_role_id", DEFAULT_DISPATCH_ROLE_ID
            ),
        ),
        roster=RosterConfig(
            default_ttl_seconds=float(
                orch.get("roster", {}).get("default_ttl_seconds", DEFAULT_ROSTER_TTL_SECONDS)
            ),
            max_reuse_window_seconds=float(
                orch.get("roster", {}).get("max_reuse_window_seconds", 600.0)
            ),
        ),
        runtime=RuntimeConfig(
            default_runner=orch.get("runtime", {}).get("default_runner", DEFAULT_RUNNER),
            artifact_root=Path(orch.get("runtime", {}).get("artifact_root", DEFAULT_ARTIFACT_ROOT)),
            workspace_root=Path(
                orch.get("runtime", {}).get("workspace_root", DEFAULT_WORKSPACE_ROOT)
            ),
            isolation_default=orch.get("runtime", {}).get(
                "isolation_default", DEFAULT_ISOLATION_DEFAULT
            ),
            cleanup_policy=orch.get("runtime", {}).get("cleanup_policy", DEFAULT_CLEANUP_POLICY),
        ),
        control=ControlConfig(
            idle_poll_interval_ms=int(
                orch.get("control", {}).get("idle_poll_interval_ms", DEFAULT_IDLE_POLL_INTERVAL_MS)
            ),
            max_resolution_attempts_per_case=int(
                orch.get("control", {}).get(
                    "max_resolution_attempts_per_case", DEFAULT_MAX_RESOLUTION_ATTEMPTS
                )
            ),
            action_ack_timeout_seconds=float(
                orch.get("control", {}).get(
                    "action_ack_timeout_seconds", DEFAULT_ACTION_ACK_TIMEOUT_SECONDS
                )
            ),
        ),
        resolver=ResolverConfig(
            enabled=bool(orch.get("resolver", {}).get("enabled", DEFAULT_RESOLVER_ENABLED)),
            default_role_id=orch.get("resolver", {}).get(
                "default_role_id", DEFAULT_RESOLVER_ROLE_ID
            ),
            tool_allowlist=resolved_allowlist,
            invocation_timeout_seconds=float(
                orch.get("resolver", {}).get(
                    "timeout_seconds", DEFAULT_RESOLVER_TIMEOUT_SECONDS_CFG
                )
            ),
            max_tool_calls_per_invocation=int(
                orch.get("resolver", {}).get(
                    "max_tool_calls_per_invocation", DEFAULT_MAX_TOOL_CALLS
                )
            ),
            max_tool_argument_bytes=int(
                orch.get("resolver", {}).get(
                    "max_tool_argument_bytes", DEFAULT_MAX_TOOL_ARGUMENT_BYTES
                )
            ),
        ),
        continuity=ContinuityConfig(
            resume_enabled=bool(
                orch.get("continuity", {}).get("resume_enabled", DEFAULT_RESUME_ENABLED)
            ),
            stale_artifact_policy=orch.get("continuity", {}).get(
                "stale_artifact_policy", DEFAULT_STALE_ARTIFACT_POLICY
            ),
            replay_safety=orch.get("continuity", {}).get("replay_safety", DEFAULT_REPLAY_SAFETY),
        ),
        observability=ObservabilityConfig(
            events_jsonl=bool(
                orch.get("observability", {}).get("events_jsonl", DEFAULT_EVENTS_JSONL)
            ),
            text_log=bool(orch.get("observability", {}).get("text_log", DEFAULT_TEXT_LOG)),
            projected_state=bool(
                orch.get("observability", {}).get("projected_state", DEFAULT_PROJECTED_STATE)
            ),
            heartbeat_stale_threshold_seconds=int(
                orch.get("observability", {}).get(
                    "heartbeat_stale_threshold_seconds", DEFAULT_HEARTBEAT_STALE_THRESHOLD_SECONDS
                )
            ),
            per_step_artifacts=bool(
                orch.get("observability", {}).get("per_step_artifacts", DEFAULT_PER_STEP_ARTIFACTS)
            ),
            per_case_artifacts=bool(
                orch.get("observability", {}).get("per_case_artifacts", DEFAULT_PER_CASE_ARTIFACTS)
            ),
            redact_env_keys=tuple(
                orch.get("observability", {}).get("redact_env_keys", DEFAULT_REDACT_ENV_KEYS)
            ),
            max_log_megabytes=int(
                orch.get("observability", {}).get("max_log_megabytes", DEFAULT_MAX_LOG_MEGABYTES)
            ),
            retention_days=int(
                orch.get("observability", {}).get("retention_days", DEFAULT_RETENTION_DAYS)
            ),
        ),
        operator=OperatorConfig(
            control_channel=orch.get("operator", {}).get(
                "control_channel", DEFAULT_CONTROL_CHANNEL
            ),
            default_output=orch.get("operator", {}).get("default_output", DEFAULT_DEFAULT_OUTPUT),
            max_pending_actions=int(
                orch.get("operator", {}).get("max_pending_actions", DEFAULT_MAX_PENDING_ACTIONS)
            ),
        ),
        drive=DriveConfig(
            max_parallelism=int(
                orch.get("drive", {}).get("max_parallelism", DEFAULT_DRIVE_MAX_PARALLELISM)
            ),
            collect_poll_interval_ms=int(
                orch.get("drive", {}).get(
                    "collect_poll_interval_ms", DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS
                )
            ),
            resolver_timeout_seconds=float(
                orch.get("drive", {}).get(
                    "resolver_timeout_seconds", DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS
                )
            ),
            planner_timeout_seconds=float(
                orch.get("drive", {}).get(
                    "planner_timeout_seconds", DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS
                )
            ),
        ),
    )


# ---------------------------------------------------------------------
# Config Loader / Freeze Boundary
# ---------------------------------------------------------------------


def freeze_config(
    config: OrchestrationConfig,
    run_dir: Path | None = None,
) -> FrozenConfigSnapshot:
    """
    Enforce immutability boundary on an orchestration configuration.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.8

    Args:
        config: The configuration to freeze.
        run_dir: Optional run directory to write the frozen snapshot to.

    Returns:
        A FrozenConfigSnapshot with the frozen config, snapshot metadata,
        and drive provenance metadata.

    Raises:
        TypeError: If config is not an OrchestrationConfig instance.
    """
    if not isinstance(config, OrchestrationConfig):
        raise TypeError(f"freeze_config requires OrchestrationConfig, got {type(config).__name__}")

    import time

    snapshot_path: Path | None = None
    if run_dir is not None:
        snapshot_path = write_frozen_snapshot(config, run_dir)

    drive_provenance = build_drive_config_provenance(config)

    return FrozenConfigSnapshot(
        config=config.freeze(),
        snapshot_path=snapshot_path,
        created_at=time.time(),
        drive_provenance=drive_provenance,
    )


def build_drive_config_provenance(
    config: OrchestrationConfig,
    ambient_config: OrchestrationConfig | None = None,
) -> dict[str, str]:
    """Build provenance metadata for drive config values.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.7,
              docs/RFC-orch-drive.md §8.1 (frozen drive creation parameters)

    Compares the effective config's drive section against built-in defaults
    and an optional ambient config (e.g. current live config). This detects
    precedence drift between frozen and ambient values.

    Provenance sources:
        - ``default``: Value matches the built-in default.
        - ``file``: Value came from a config file (differs from default).
        - ``env``: Value came from an environment variable override.
        - ``flag``: Value came from a CLI flag override.
        - ``drift``: Frozen value differs from current ambient value.

    Args:
        config: The effective (possibly frozen) OrchestrationConfig.
        ambient_config: Optional current ambient config for drift detection.
            When provided, any frozen value that differs from ambient is
            annotated with ``drift`` provenance.

    Returns:
        Mapping of dotted drive config key -> provenance source tag.
    """
    drive = config.drive
    defaults = DriveConfig()

    provenance: dict[str, str] = {}

    drive_fields: list[tuple[str, object, object]] = [
        ("drive.max_parallelism", drive.max_parallelism, defaults.max_parallelism),
        (
            "drive.collect_poll_interval_ms",
            drive.collect_poll_interval_ms,
            defaults.collect_poll_interval_ms,
        ),
        (
            "drive.resolver_timeout_seconds",
            drive.resolver_timeout_seconds,
            defaults.resolver_timeout_seconds,
        ),
        (
            "drive.planner_timeout_seconds",
            drive.planner_timeout_seconds,
            defaults.planner_timeout_seconds,
        ),
    ]

    for key, value, default_value in drive_fields:
        if value != default_value:
            provenance[key] = "file"
        else:
            provenance[key] = "default"

    # Detect drift between frozen and ambient
    if ambient_config is not None:
        ambient_drive = ambient_config.drive
        if drive.max_parallelism != ambient_drive.max_parallelism:
            provenance["drive.max_parallelism"] = "drift"
        if drive.collect_poll_interval_ms != ambient_drive.collect_poll_interval_ms:
            provenance["drive.collect_poll_interval_ms"] = "drift"
        if drive.resolver_timeout_seconds != ambient_drive.resolver_timeout_seconds:
            provenance["drive.resolver_timeout_seconds"] = "drift"
        if drive.planner_timeout_seconds != ambient_drive.planner_timeout_seconds:
            provenance["drive.planner_timeout_seconds"] = "drift"

    return provenance


def freeze_drive_config(
    config: OrchestrationConfig,
    drive_id: str,
) -> DriveConfigFrozen:
    """Freeze drive creation parameters from an OrchestrationConfig.

    Authority: docs/RFC-orch-drive.md §8.1 (DriveConfigFrozen),
              docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.4.1

    Freezes drive-critical config values at creation time so that
    resume/recover operations use the same parameters the drive was
    created with, rather than re-reading potentially-changed ambient
    config.

    Args:
        config: The effective orchestration config at drive creation time.
        drive_id: The drive identifier to associate with this frozen config.

    Returns:
        A ``DriveConfigFrozen`` with values frozen from config.
    """
    import time

    return DriveConfigFrozen(
        drive_id=drive_id,
        max_parallelism=config.drive.max_parallelism,
        control_idle_poll_interval_ms=config.control.idle_poll_interval_ms,
        control_action_ack_timeout_seconds=config.control.action_ack_timeout_seconds,
        resolver_invocation_timeout_seconds=config.resolver.invocation_timeout_seconds,
        resolver_max_tool_calls_per_invocation=config.resolver.max_tool_calls_per_invocation,
        frozen_at=time.time(),
    )


def load_orchestration_config(
    plan_path: Path | str | None = None,
) -> tuple[OrchestrationConfig, Path | None]:
    """
    Load orchestration configuration from standard locations.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.1, §8.2

    Discovery order (§8.1):
    1. Explicit plan_path argument (--config equivalent)
    2. VECTL_CONFIG environment variable
    3. Current working directory ./vectl.yaml
    4. Repository root vectl.yaml
    5. User config ~/.config/vectl/vectl.yaml

    Precedence (§8.2):
    1. CLI flags (not modeled here; caller applies)
    2. Environment variables (VECTL_ORCH_*)
    3. Config file
    4. Built-in defaults

    Args:
        plan_path: Optional explicit plan path override.

    Returns:
        Tuple of (OrchestrationConfig, config_file_path) where config_file_path
        is the path to the discovered config file (or None for defaults-only).

    Raises:
        FileNotFoundError: If a specified config file does not exist.
        OSError: If a config file cannot be read.
    """
    # Discover config file
    discovered_path, discovery_source = _discover_config_file(plan_path)

    # Load base config from file or defaults
    if discovered_path is not None:
        yaml_content = discovered_path.read_text()
        raw_config = _parse_config_yaml(yaml_content)
        base_config = _dict_to_config(raw_config)
    else:
        base_config = OrchestrationConfig()

    # Apply environment variable overrides
    env_vars = _flatten_env_vars()
    if env_vars:
        base_config = _apply_env_overrides(base_config, env_vars)

    return (base_config, discovered_path)


# ---------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------
# Spec: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.5


class ConfigValidationError(ValueError):
    """
    Raised when configuration validation fails.

    Attributes:
        field: The configuration field that failed validation.
        value: The invalid value.
        reason: Human-readable reason for the validation failure.
    """

    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"config validation failed: {field} = {value!r}: {reason}")


class RoleProfileOverrideError(ValueError):
    """Raised when role profile override or custom-role validation fails.

    Authority: docs/RFC-role-profile-overrides.md §6.1, §6.2

    This error covers the rejection cases defined by the RFC:

    - Override targets a role ID not present in built-ins (§6.1 rule 1)
    - Override contains unknown fields (§6.1 rule 2)
    - Override targets a custom role from ``role_profiles`` (§6.1 rule 3)
    - ``role_profiles`` entry shadows a built-in role ID (§6.2 rule 2/4)

    Attributes:
        field: Dot-separated config path (e.g. ``role_profile_overrides.python-executor``).
        value: The invalid value encountered.
        reason: Human-readable reason with migration guidance.
    """

    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"role profile override validation failed: {field}: {reason}")


def validate_orchestration_config(
    config: OrchestrationConfig,
    *,
    check_paths_exist: bool = False,
) -> list[ConfigValidationError]:
    """
    Validate an OrchestrationConfig against spec rules.

    Authority: docs/ORCHESTRATION-PLANE-CLI-CONFIG-OBSERVABILITY-DESIGN.md §8.5

    Validation rules:
    - cleanup_policy must be one of: never, on-success, always
    - isolation_default must be a valid IsolationMode
    - timeout_seconds > 0
    - max_tool_calls_per_invocation > 0
    - max_tool_argument_bytes > 0
    - action_ack_timeout_seconds > 0
    - heartbeat_stale_threshold_seconds > 0
    - retention_days >= 0
    - max_log_megabytes > 0
    - max_pending_actions > 0
    - tool_allowlist entries must resolve in canonical tool registry

    Args:
        config: The configuration to validate.
        check_paths_exist: If True, validate that paths exist and are writable.

    Returns:
        List of validation errors (empty if all valid).
    """
    errors: list[ConfigValidationError] = []

    # cleanup_policy validation
    valid_cleanup_policies = ("never", "on-success", "always")
    if config.runtime.cleanup_policy not in valid_cleanup_policies:
        errors.append(
            ConfigValidationError(
                field="runtime.cleanup_policy",
                value=config.runtime.cleanup_policy,
                reason=f"expected one of: {', '.join(valid_cleanup_policies)}",
            )
        )

    # isolation_default validation
    valid_isolation_modes = ("default", "workspace", "independent")
    if config.runtime.isolation_default not in valid_isolation_modes:
        errors.append(
            ConfigValidationError(
                field="runtime.isolation_default",
                value=config.runtime.isolation_default,
                reason=f"expected one of: {', '.join(valid_isolation_modes)}",
            )
        )

    # Timeout validations
    if config.resolver.invocation_timeout_seconds <= 0:
        errors.append(
            ConfigValidationError(
                field="resolver.timeout_seconds",
                value=config.resolver.invocation_timeout_seconds,
                reason="must be > 0",
            )
        )

    if config.resolver.max_tool_calls_per_invocation <= 0:
        errors.append(
            ConfigValidationError(
                field="resolver.max_tool_calls_per_invocation",
                value=config.resolver.max_tool_calls_per_invocation,
                reason="must be > 0",
            )
        )

    if config.resolver.max_tool_argument_bytes <= 0:
        errors.append(
            ConfigValidationError(
                field="resolver.max_tool_argument_bytes",
                value=config.resolver.max_tool_argument_bytes,
                reason="must be > 0",
            )
        )

    if config.control.action_ack_timeout_seconds <= 0:
        errors.append(
            ConfigValidationError(
                field="control.action_ack_timeout_seconds",
                value=config.control.action_ack_timeout_seconds,
                reason="must be > 0",
            )
        )

    if config.observability.heartbeat_stale_threshold_seconds <= 0:
        errors.append(
            ConfigValidationError(
                field="observability.heartbeat_stale_threshold_seconds",
                value=config.observability.heartbeat_stale_threshold_seconds,
                reason="must be > 0",
            )
        )

    if config.observability.retention_days < 0:
        errors.append(
            ConfigValidationError(
                field="observability.retention_days",
                value=config.observability.retention_days,
                reason="must be >= 0",
            )
        )

    if config.observability.max_log_megabytes <= 0:
        errors.append(
            ConfigValidationError(
                field="observability.max_log_megabytes",
                value=config.observability.max_log_megabytes,
                reason="must be > 0",
            )
        )

    if config.operator.max_pending_actions <= 0:
        errors.append(
            ConfigValidationError(
                field="operator.max_pending_actions",
                value=config.operator.max_pending_actions,
                reason="must be > 0",
            )
        )

    # Drive config validation (§8.4.1)
    if config.drive.max_parallelism < 1 or config.drive.max_parallelism > 32:
        errors.append(
            ConfigValidationError(
                field="drive.max_parallelism",
                value=config.drive.max_parallelism,
                reason="must be between 1 and 32 inclusive",
            )
        )

    if config.drive.collect_poll_interval_ms <= 0:
        errors.append(
            ConfigValidationError(
                field="drive.collect_poll_interval_ms",
                value=config.drive.collect_poll_interval_ms,
                reason="must be > 0",
            )
        )

    if config.drive.resolver_timeout_seconds <= 0:
        errors.append(
            ConfigValidationError(
                field="drive.resolver_timeout_seconds",
                value=config.drive.resolver_timeout_seconds,
                reason="must be > 0",
            )
        )

    if config.drive.planner_timeout_seconds <= 0:
        errors.append(
            ConfigValidationError(
                field="drive.planner_timeout_seconds",
                value=config.drive.planner_timeout_seconds,
                reason="must be > 0",
            )
        )

    # Tool allowlist validation
    allowlist = config.resolver.tool_allowlist
    if isinstance(allowlist, ResolverToolAllowlist):
        # ResolverToolAllowlist only stores families, not individual tools
        # Validate that all families are known
        for family in allowlist.allowed_tool_families:
            if family not in CANONICAL_TOOL_FAMILIES:
                errors.append(
                    ConfigValidationError(
                        field="resolver.tool_allowlist",
                        value=family,
                        reason=(
                            f"unknown tool family {family!r}; "
                            f"expected one of {list(CANONICAL_TOOL_FAMILIES)}"
                        ),
                    )
                )
    else:
        # dict format with family -> tool names
        allowlist_dict: dict[str, tuple[str, ...]] = allowlist or {}
        tool_errors = validate_tool_allowlist(allowlist_dict)
        for tool_error in tool_errors:
            errors.append(
                ConfigValidationError(
                    field=f"resolver.tool_allowlist.{tool_error.family}",
                    value=tool_error.tool,
                    reason=tool_error.reason,
                )
            )

    role_profile_errors = validate_role_profiles(config.role_profiles)
    for reason in role_profile_errors:
        errors.append(
            ConfigValidationError(
                field="role_profiles",
                value="<registry>",
                reason=reason,
            )
        )

    profile_by_id = {profile.role_id: profile for profile in config.role_profiles}
    if config.dispatch.default_role_id not in profile_by_id:
        errors.append(
            ConfigValidationError(
                field="dispatch.default_role_id",
                value=config.dispatch.default_role_id,
                reason="must reference a configured role profile",
            )
        )

    resolver_profile = profile_by_id.get(config.resolver.default_role_id)
    if resolver_profile is None:
        errors.append(
            ConfigValidationError(
                field="resolver.default_role_id",
                value=config.resolver.default_role_id,
                reason="must reference a configured role profile",
            )
        )
    elif resolver_profile.prompt_family != "resolver":
        errors.append(
            ConfigValidationError(
                field="resolver.default_role_id",
                value=config.resolver.default_role_id,
                reason="must reference a resolver-family role profile",
            )
        )
    elif config.resolver.default_role_id not in {
        "blocked-case-coordinator",
        "blocked-case-coordinator-tacit",
    }:
        errors.append(
            ConfigValidationError(
                field="resolver.default_role_id",
                value=config.resolver.default_role_id,
                reason=("must be 'blocked-case-coordinator' or 'blocked-case-coordinator-tacit'"),
            )
        )

    # Path existence checks (optional)
    if check_paths_exist:
        if not config.plan_path.exists():
            errors.append(
                ConfigValidationError(
                    field="plan_path",
                    value=str(config.plan_path),
                    reason="file does not exist",
                )
            )

        # Check artifact_root is writable
        artifact_root = config.runtime.artifact_root
        try:
            artifact_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            errors.append(
                ConfigValidationError(
                    field="runtime.artifact_root",
                    value=str(artifact_root),
                    reason=f"cannot be created or is not writable: {e}",
                )
            )

    return errors


__all__ = [
    "DEFAULT_ROSTER_TTL_SECONDS",
    "DEFAULT_RESOLVER_TIMEOUT_SECONDS",
    "DEFAULT_DISPATCH_ROLE_ID",
    "DEFAULT_RESOLVER_ROLE_ID",
    "DEFAULT_DRIVE_MAX_PARALLELISM",
    "DEFAULT_DRIVE_COLLECT_POLL_INTERVAL_MS",
    "DEFAULT_DRIVE_RESOLVER_TIMEOUT_SECONDS",
    "DEFAULT_DRIVE_PLANNER_TIMEOUT_SECONDS",
    "OrchestrationConfig",
    "ResolverToolAllowlist",
    "DispatchConfig",
    "RosterConfig",
    "RuntimeConfig",
    "ControlConfig",
    "ResolverConfig",
    "ContinuityConfig",
    "ObservabilityConfig",
    "OperatorConfig",
    "DriveConfig",
    "FrozenConfigSnapshot",
    "ConfigValue",
    "ConfigValidationError",
    "RoleProfileOverrideError",
    "default_role_profiles",
    "build_role_profile_provenance",
    "build_drive_config_provenance",
    "RoleFieldProvenance",
    "freeze_config",
    "freeze_drive_config",
    "load_frozen_snapshot",
    "load_orchestration_config",
    "validate_role_profile",
    "validate_role_profiles",
    "validate_orchestration_config",
    "write_frozen_snapshot",
]
