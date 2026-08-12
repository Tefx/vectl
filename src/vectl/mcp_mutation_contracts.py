"""Typed public request contracts for ``vectl_mutate`` batch mode.

FastMCP derives the action-discriminated JSON Schema from these ``TypedDict``
classes. Runtime extra fields remain present long enough for Vectl's pre-load
validator to return indexed, actionable errors; the advertised schema remains
closed through ``additionalProperties: false``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias, get_origin, get_type_hints

from pydantic import ConfigDict, Field, TypeAdapter, WithJsonSchema
from typing_extensions import Required, TypedDict

MutationAction: TypeAlias = Literal[
    "add-step",
    "add-steps",
    "edit-step",
    "remove-step",
    "move-step",
    "add-phase",
    "edit-phase",
    "edit-plan",
]

_CLOSED_SCHEMA_CONFIG = ConfigDict(
    extra="allow",
    json_schema_extra={"additionalProperties": False},
)

UNKNOWN_FIELD_HINTS = {
    ("add-phase", "step_id"): "Use 'phase_id' as the explicit ID for add-phase.",
    ("add-step", "add_refs"): (
        "Use 'refs' when creating a step; 'add_refs' and 'remove_refs' are edit-step only."
    ),
    ("add-step", "remove_refs"): (
        "Use 'refs' when creating a step; 'add_refs' and 'remove_refs' are edit-step only."
    ),
    ("add-steps", "add_refs"): (
        "Use 'refs' when creating a step; 'add_refs' and 'remove_refs' are edit-step only."
    ),
    ("add-steps", "remove_refs"): (
        "Use 'refs' when creating a step; 'add_refs' and 'remove_refs' are edit-step only."
    ),
}


class AddPhaseRequest(TypedDict, total=False):
    """Create one phase; ``phase_id`` is the explicit batch identifier."""

    action: Required[Literal["add-phase"]]
    phase_id: Required[str]
    name: Required[str]
    depends_on: list[str]
    gate: str


class AddStepRequest(TypedDict, total=False):
    """Create one step; ``step_id`` is explicit and ``refs`` are persisted."""

    action: Required[Literal["add-step"]]
    phase_id: Required[str]
    step_id: Required[str]
    name: Required[str]
    description: str
    depends_on: list[str]
    verification: str
    evidence_template: str
    refs: list[str]
    status: str
    evidence: str
    skipped_reason: str
    agent: str


class AddStepsEntry(TypedDict, total=False):
    """One explicit-ID entry in an ``add-steps`` request."""

    id: Required[str]
    name: Required[str]
    description: str
    desc: str
    depends_on: list[str]
    after: str | list[str]
    verification: str
    verify: str
    refs: list[str]
    status: str
    evidence: str
    skipped_reason: str
    agent: str


class AddStepsRequest(TypedDict, total=False):
    """Create multiple explicit-ID steps in one phase."""

    action: Required[Literal["add-steps"]]
    phase_id: Required[str]
    steps: Required[list[AddStepsEntry]]


class EditPhaseRequest(TypedDict, total=False):
    """Edit one existing phase."""

    action: Required[Literal["edit-phase"]]
    phase_id: Required[str]
    name: str
    context: str
    gate: str
    depends_on: list[str]


class EditStepRequest(TypedDict, total=False):
    """Edit one existing step, including replacement or incremental refs."""

    action: Required[Literal["edit-step"]]
    step_id: Required[str]
    new_step_id: str
    name: str
    description: str
    depends_on: list[str]
    verification: str
    evidence_template: str
    agent: str
    add_refs: list[str]
    remove_refs: list[str]
    refs: list[str]


class RemoveStepRequest(TypedDict, total=False):
    """Remove one existing step."""

    action: Required[Literal["remove-step"]]
    step_id: Required[str]
    force: bool


class MoveStepRequest(TypedDict, total=False):
    """Move one existing step to another phase."""

    action: Required[Literal["move-step"]]
    step_id: Required[str]
    target_phase: Required[str]


class EditPlanRequest(TypedDict, total=False):
    """Edit Plan-level guidance, strategy reference, or context."""

    action: Required[Literal["edit-plan"]]
    project_guidance: str
    strategy_ref: str
    plan_context: str


_SCHEMA_TYPES: tuple[type[Any], ...] = (
    AddPhaseRequest,
    AddStepRequest,
    AddStepsEntry,
    AddStepsRequest,
    EditPhaseRequest,
    EditStepRequest,
    RemoveStepRequest,
    MoveStepRequest,
    EditPlanRequest,
)
for _schema_type in _SCHEMA_TYPES:
    _schema_type.__pydantic_config__ = _CLOSED_SCHEMA_CONFIG


def _inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local Pydantic definitions so wire middleware preserves the discriminator."""
    definitions = schema.get("$defs", {})

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            target = definitions[reference.removeprefix("#/$defs/")]
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            return inline({**target, **siblings})
        return {
            key: inline(item)
            for key, item in value.items()
            if key != "$defs"
        }

    inlined = inline(schema)
    assert isinstance(inlined, dict)
    return inlined


_BATCH_WIRE_SCHEMA = {
    "oneOf": [
        _inline_local_refs(TypeAdapter(schema_type).json_schema())
        for schema_type in _SCHEMA_TYPES
        if schema_type is not AddStepsEntry
    ],
    "discriminator": {"propertyName": "action"},
}

BatchMutationRequest: TypeAlias = Annotated[
    AddPhaseRequest
    | AddStepRequest
    | AddStepsRequest
    | EditPhaseRequest
    | EditStepRequest
    | RemoveStepRequest
    | MoveStepRequest
    | EditPlanRequest,
    Field(discriminator="action"),
    WithJsonSchema(_BATCH_WIRE_SCHEMA),
]

_BATCH_REQUEST_TYPES: dict[str, type[Any]] = {
    "add-phase": AddPhaseRequest,
    "add-step": AddStepRequest,
    "add-steps": AddStepsRequest,
    "edit-phase": EditPhaseRequest,
    "edit-step": EditStepRequest,
    "remove-step": RemoveStepRequest,
    "move-step": MoveStepRequest,
    "edit-plan": EditPlanRequest,
}

_BATCH_REQUEST_HINTS = {
    action: get_type_hints(request_type, include_extras=True)
    for action, request_type in _BATCH_REQUEST_TYPES.items()
}
BATCH_FIELDS = {
    action: frozenset(field_hints) for action, field_hints in _BATCH_REQUEST_HINTS.items()
}
BATCH_REQUIRED_FIELDS = {
    action: tuple(
        field
        for field, annotation in field_hints.items()
        if get_origin(annotation) is Required
    )
    for action, field_hints in _BATCH_REQUEST_HINTS.items()
}
_BATCH_STEP_HINTS = get_type_hints(AddStepsEntry, include_extras=True)
BATCH_STEP_FIELDS = frozenset(_BATCH_STEP_HINTS)
BATCH_STEP_REQUIRED_FIELDS = tuple(
    field
    for field, annotation in _BATCH_STEP_HINTS.items()
    if get_origin(annotation) is Required
)
