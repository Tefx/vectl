from __future__ import annotations

if __name__.endswith("_config_impl_part02"):
    from vectl.orchestration import _config_impl as _impl

    for _name, _value in vars(_impl).items():
        if _name in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}:
            continue
        globals()[_name] = _value

    del _impl, _name, _value

def _apply_role_profile_override(
    base_profile: RoleProfile,
    overrides: dict[str, str],
) -> Result[RoleProfile, Exception]:
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


# @shell_complexity: Branches encode RFC-role-profile-overrides target, field, shadowing, and family-policy rejection rules.
# @shell_orchestration: Role merge coordinates config admission, custom roles, overrides, and family policy diagnostics.
def _merge_role_profiles(
    builtin_profiles: tuple[RoleProfile, ...],
    overrides: dict[str, dict[str, str]],
    custom_profiles: tuple[RoleProfile, ...],
) -> Result[tuple[RoleProfile, ...], Exception]:
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
) -> Result[Path, Exception]:
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


def load_frozen_snapshot(snapshot_path: Path) -> Result[OrchestrationConfig, Exception]:
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


# @shell_complexity: Branches preserve historical snapshot tolerance and built-in/custom role split semantics.
# @shell_orchestration: Legacy normalization is part of frozen snapshot shell compatibility loading.
def _normalize_legacy_frozen_snapshot_payload(payload: dict[str, Any]) -> Result[dict[str, Any], Exception]:
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


# @shell_orchestration: Allowlist serialization is coupled to config snapshot YAML compatibility.
def _serialize_tool_allowlist(
    allowlist: ResolverToolAllowlist | dict[str, tuple[str, ...]] | None,
) -> Result[dict[str, list[str]], Exception]:
    """Serialize tool allowlist to dict format for YAML."""
    if allowlist is None:
        return {}
    if isinstance(allowlist, ResolverToolAllowlist):
        # Convert families tuple to a dict with empty lists (tool names come from config)
        return {family: [] for family in allowlist.allowed_tool_families}
    # It's already a dict
    return {family: list(tools) for family, tools in allowlist.items()}


# @shell_complexity: Branches preserve built-in override versus custom-role serialization and optional section emission.
# @shell_orchestration: Config serializer coordinates the public YAML/snapshot schema for shell persistence callers.
