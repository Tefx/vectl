"""Contracts for resolver/review routing presenter extraction.

Decision row: ``src/vectl/orch_app.py`` structural Core extraction.

The normal path is intentionally red until implementation fills the presenter:

>>> build_resolution_case_data(kind="artifact_ref_mediation", source="review", status="needs_mediation", step_id="phase.step")
Traceback (most recent call last):
...
NotImplementedError: contract stub: build_resolution_case_data

The edge path documents the required identifier precondition:

>>> build_resolution_case_data(kind="artifact_ref_mediation", source="review", status="needs_mediation", step_id="")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda artifact_refs: isinstance(artifact_refs, tuple) and all(ref.strip() for ref in artifact_refs))
@post(lambda result: isinstance(result, tuple) and all("name" in call and "arguments" in call for call in result))
def artifact_ref_mediation_calls(artifact_refs: tuple[str, ...]) -> tuple[Mapping[str, object], ...]:
    """Build resolver tool-call data for non-empty artifact references.
    
    >>> artifact_ref_mediation_calls(("artifact://one",))
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: artifact_ref_mediation_calls
    """
    raise NotImplementedError("contract stub: artifact_ref_mediation_calls")


@pre(lambda kind, source, status, step_id, artifact_refs=(), diagnostics=(): bool(kind.strip() and source.strip() and status.strip() and step_id.strip()) and all(ref.strip() for ref in artifact_refs) and all(item.strip() for item in diagnostics))
@post(lambda result: isinstance(result, Mapping) and all(result.get(key) for key in ("kind", "source", "status", "step_id")))
def build_resolution_case_data(
    kind: str,
    source: str,
    status: str,
    step_id: str,
    artifact_refs: Sequence[str] = (),
    diagnostics: Sequence[str] = (),
) -> Mapping[str, object]:
    """Return model-visible resolver intake data without changing field labels.
    
    >>> build_resolution_case_data("kind", "source", "status", "step")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_resolution_case_data
    """
    raise NotImplementedError("contract stub: build_resolution_case_data")


@pre(lambda case_data, fallback_source="review": isinstance(case_data, Mapping) and bool(fallback_source.strip()) and bool(str(case_data.get("kind", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and all(result.get(key) for key in ("kind", "source", "status")))
def normalize_review_resolution_case_data(
    case_data: Mapping[str, object],
    fallback_source: str = "review",
) -> Mapping[str, object]:
    """Normalize review case data while preserving resolver-visible identity fields.
    
    >>> normalize_review_resolution_case_data({"kind": "review", "status": "ok"})
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: normalize_review_resolution_case_data
    """
    raise NotImplementedError("contract stub: normalize_review_resolution_case_data")


@pre(lambda step_id, raw_response, error_message: bool(step_id.strip()) and bool(raw_response.strip()) and bool(error_message.strip()))
@post(lambda result: isinstance(result, Mapping) and result.get("kind") == "review_parse_failure" and bool(result.get("status")))
def build_review_parse_failure_case_data(
    step_id: str,
    raw_response: str,
    error_message: str,
) -> Mapping[str, object]:
    """Build parse-failure resolver case data with stable public labels.
    
    >>> build_review_parse_failure_case_data("step", "raw", "error")
    Traceback (most recent call last):
    ...
    NotImplementedError: contract stub: build_review_parse_failure_case_data
    """
    raise NotImplementedError("contract stub: build_review_parse_failure_case_data")
