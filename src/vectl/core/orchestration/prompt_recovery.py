"""Pure prompt recovery validation helpers.

>>> validate_prompt_bundle_fields({"role_id": "r", "agent_id": "a", "runner": "opencode", "system_prompt": "s", "task_prompt": "t", "prompt_bundle_sha256": "abc"})
''
>>> validate_prompt_content("  ", "/tmp/runner_prompt.md")
'runner_prompt.md is empty at /tmp/runner_prompt.md'
>>> recovery_validation_reason({"role_id": "r", "agent_id": "a", "runner": "opencode", "system_prompt": "s", "task_prompt": "t", "prompt_bundle_sha256": "abc"}, "# ok", "/tmp/runner_prompt.md")
''
"""

from __future__ import annotations

from collections.abc import Mapping

from deal import post, pre

REQUIRED_BUNDLE_FIELDS = (
    "role_id",
    "agent_id",
    "runner",
    "system_prompt",
    "task_prompt",
    "prompt_bundle_sha256",
)


@pre(lambda bundle_data: "\x00" not in "".join(str(key) for key in bundle_data.keys()))
@post(lambda result: isinstance(result, str))
def validate_prompt_bundle_fields(bundle_data: Mapping[str, object]) -> str:
    """Return an empty string when required recovery fields are present.

    >>> validate_prompt_bundle_fields({"role_id": "", "agent_id": "a"})
    "prompt_bundle.json field 'role_id' is missing or empty; recovery requires all of ('role_id', 'agent_id', 'runner', 'system_prompt', 'task_prompt', 'prompt_bundle_sha256')"
    """
    for field_name in REQUIRED_BUNDLE_FIELDS:
        value = bundle_data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return (
                f"prompt_bundle.json field {field_name!r} is missing or empty; "
                f"recovery requires all of {REQUIRED_BUNDLE_FIELDS}"
            )
    return ""


@pre(lambda prompt_content, prompt_path: "\x00" not in prompt_content and bool(prompt_path.strip()))
@post(lambda result: isinstance(result, str))
def validate_prompt_content(prompt_content: str, prompt_path: str) -> str:
    """Return an empty string when runner prompt content is usable.

    >>> validate_prompt_content("# Task\nDo it.", "/tmp/runner_prompt.md")
    ''
    """
    if not prompt_content.strip():
        return f"runner_prompt.md is empty at {prompt_path}"
    return ""


@pre(
    lambda bundle_data, prompt_content, prompt_path: "\x00" not in prompt_content
    and bool(prompt_path.strip())
    and "\x00" not in "".join(str(key) for key in bundle_data.keys())
)
@post(lambda result: isinstance(result, str))
def recovery_validation_reason(
    bundle_data: Mapping[str, object], prompt_content: str, prompt_path: str
) -> str:
    """Return the first recovery validation failure reason, or empty string.

    >>> recovery_validation_reason({"role_id": ""}, "# ok", "/tmp/runner_prompt.md")
    "prompt_bundle.json field 'role_id' is missing or empty; recovery requires all of ('role_id', 'agent_id', 'runner', 'system_prompt', 'task_prompt', 'prompt_bundle_sha256')"
    """
    bundle_reason = validate_prompt_bundle_fields(bundle_data)
    if bundle_reason:
        return bundle_reason
    return validate_prompt_content(prompt_content, prompt_path)
