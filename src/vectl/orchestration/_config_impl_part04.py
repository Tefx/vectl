from __future__ import annotations

if __name__.endswith("_config_impl_part04"):
    from vectl.orchestration import _config_impl as _impl

    for _name, _value in vars(_impl).items():
        if _name in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__"}:
            continue
        globals()[_name] = _value

    del _impl, _name, _value

def freeze_config(
    config: OrchestrationConfig,
    run_dir: Path | None = None,
) -> Result[FrozenConfigSnapshot, Exception]:
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


# @shell_complexity: Branches preserve default/file/drift source classification for each drive field.
# @shell_orchestration: Drive provenance is coupled to frozen config snapshot inspection.
def build_drive_config_provenance(
    config: OrchestrationConfig,
    ambient_config: OrchestrationConfig | None = None,
) -> Result[dict[str, str], Exception]:
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


# @shell_orchestration: Drive config freeze maps orchestration config into persisted drive shell contract.
def freeze_drive_config(
    config: OrchestrationConfig,
    drive_id: str,
) -> Result[DriveConfigFrozen, Exception]:
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
) -> Result[tuple[OrchestrationConfig, Path | None], Exception]:
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
    discovered_path, _discovery_source = _discover_config_file(plan_path)

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


# @shell_complexity: Branches preserve ordered diagnostics for scalar, allowlist, role-profile, resolver, and path validations.
def validate_orchestration_config(
    config: OrchestrationConfig,
    *,
    check_paths_exist: bool = False,
) -> Result[list[ConfigValidationError], Exception]:
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
