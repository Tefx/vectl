"""Contracts for resolver/review routing presenter extraction.

Decision row: ``src/vectl/orch_app.py`` structural Core extraction.

The normal path returns resolver-intake data while preserving public labels:

>>> build_resolution_case_data(kind="artifact_ref_mediation", source="review", status="needs_mediation", step_id="phase.step")
{'kind': 'artifact_ref_mediation', 'source': 'review', 'status': 'needs_mediation', 'step_id': 'phase.step', 'artifact_refs': (), 'diagnostics': ()}

The edge path documents the required identifier precondition:

>>> build_resolution_case_data(kind="artifact_ref_mediation", source="review", status="needs_mediation", step_id="")  # doctest: +ELLIPSIS
Traceback (most recent call last):
...
deal.PreContractError: expected...
"""

from collections.abc import Mapping, Sequence

from deal import post, pre


@pre(lambda artifact_refs: isinstance(artifact_refs, tuple) and all(ref.strip() for ref in artifact_refs))
@post(lambda result: isinstance(result, tuple) and all({"family", "name", "surface"}.issubset(call.keys()) for call in result))
def artifact_ref_mediation_calls(artifact_refs: tuple[str, ...]) -> tuple[Mapping[str, object], ...]:
    """Build resolver tool-call data for non-empty artifact references.
    
    >>> artifact_ref_mediation_calls(("artifact://one",))
    ()
    >>> artifact_ref_mediation_calls(("resolver_tool=orchestration.read_case:read",))
    ({'family': 'orchestration', 'name': 'read_case', 'surface': 'read'},)
    """
    calls: list[Mapping[str, object]] = []
    prefix = "resolver_tool="
    for ref in artifact_refs:
        if not ref.startswith(prefix):
            continue
        directive = ref[len(prefix) :]
        tool_path, separator, surface_text = directive.partition(":")
        family, dot, tool_name = tool_path.partition(".")
        if not family or not dot or not tool_name:
            continue
        surface = "read"
        if separator:
            if surface_text not in {"read", "write"}:
                continue
            surface = surface_text
        calls.append({"family": family, "name": tool_name, "surface": surface})
    return tuple(calls)


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
    {'kind': 'kind', 'source': 'source', 'status': 'status', 'step_id': 'step', 'artifact_refs': (), 'diagnostics': ()}
    """
    return {
        "kind": kind,
        "source": source,
        "status": status,
        "step_id": step_id,
        "artifact_refs": tuple(artifact_refs),
        "diagnostics": tuple(diagnostics),
    }


@pre(lambda case_data, fallback_source="review": isinstance(case_data, Mapping) and bool(fallback_source.strip()) and bool(str(case_data.get("kind", "")).strip()))
@post(lambda result: isinstance(result, Mapping) and all(result.get(key) for key in ("kind", "source", "status")))
def normalize_review_resolution_case_data(
    case_data: Mapping[str, object],
    fallback_source: str = "review",
) -> Mapping[str, object]:
    """Normalize review case data while preserving resolver-visible identity fields.
    
    >>> normalize_review_resolution_case_data({"kind": "review", "status": "ok"})
    {'kind': 'review', 'source': 'review', 'status': 'ok'}
    """
    return {
        "kind": case_data["kind"],
        "source": case_data.get("source", fallback_source),
        "status": case_data.get("status", "unknown"),
        **{key: value for key, value in case_data.items() if key not in {"kind", "source", "status"}},
    }


@pre(lambda step_id, raw_response, error_message: bool(step_id.strip()) and bool(raw_response.strip()) and bool(error_message.strip()))
@post(lambda result: isinstance(result, Mapping) and result.get("kind") == "review_parse_failure" and bool(result.get("status")))
def build_review_parse_failure_case_data(
    step_id: str,
    raw_response: str,
    error_message: str,
) -> Mapping[str, object]:
    """Build parse-failure resolver case data with stable public labels.
    
    >>> build_review_parse_failure_case_data("step", "raw", "error")
    {'kind': 'review_parse_failure', 'source': 'review', 'status': 'needs_resolution', 'step_id': 'step', 'raw_response': 'raw', 'error_message': 'error'}
    """
    return {
        "kind": "review_parse_failure",
        "source": "review",
        "status": "needs_resolution",
        "step_id": step_id,
        "raw_response": raw_response,
        "error_message": error_message,
    }
