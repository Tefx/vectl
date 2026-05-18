"""CLI checklist command surfaces backed by the core checklist service."""

from __future__ import annotations

from vectl.cli_plan_common import *


def check_cmd(
    step_id: str = typer.Argument(help="Step ID containing the checklist."),
    keyword: str | None = typer.Argument(None, help="Keyword to toggle a checklist item."),
    add: str | None = typer.Option(None, "--add", help="Text for a new checklist item."),
    revision: str | None = typer.Option(
        None,
        "--revision",
        "--checklist-inventory-revision",
        help="Checklist inventory revision required for deterministic mutation.",
    ),
    item_id: str | None = typer.Option(None, "--item-id", help="Deterministic item_id selector."),
    field: str | None = typer.Option(None, "--field", help="description or verification."),
    index: int | None = typer.Option(None, "--index", help="Zero-based checklist index."),
    checked: str | None = typer.Option(None, "--checked", help="Desired state: true or false."),
    batch: str | None = typer.Option(None, "--batch", help="JSON array of mutation requests."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    plan: Path | None = PlanOption,
) -> None:
    """Toggle/add a checklist item or run deterministic checklist mutation."""
    _check_not_linked_worktree(plan)
    p, def_h, plan_path = _load(plan)
    deterministic_supplied = any(
        value is not None for value in (revision, item_id, field, index, checked, batch)
    )
    if not deterministic_supplied:
        _run_legacy_check(p, def_h, plan_path, step_id, keyword, add)
        return
    try:
        p, payload = _run_deterministic_check_mutation(
            p,
            step_id=step_id,
            keyword=keyword,
            revision=revision,
            item_id=item_id,
            field=field,
            index=index,
            checked=checked,
            batch=batch,
        )
    except Exception as exc:
        payload = _checklist_exception_payload(exc)
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
            if batch is not None:
                return
        else:
            _print_checklist_error(payload)
        raise typer.Exit(1) from None

    _save_plan(p, plan_path, def_h, f"vectl: deterministic checklist {step_id}")
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    out.print(f"[green]Updated checklist:[/] {step_id}")
    out.print(f"checklist_inventory_revision: {payload['checklist_inventory_revision']}")
    out.print()
    out.print(f"[dim]→ vectl check-inventory {step_id} --json[/]")


def _run_legacy_check(p: Plan, def_h: str, plan_path: Path, step_id: str, keyword: str | None, add: str | None) -> None:
    try:
        updated = update_checklist(p, step_id, check=keyword, append=add)
    except PlanError as e:
        _die(str(e))
    _save_plan(updated, plan_path, def_h, f"vectl: update checklist {step_id}")
    out.print(f"[green]Updated checklist:[/] {step_id}")
    out.print()
    out.print(f"[dim]→ vectl show {step_id}[/]")


def check_inventory_cmd(
    step_id: str = typer.Argument(help="Step ID containing the checklist."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    plan: Path | None = PlanOption,
) -> None:
    """Show deterministic checklist inventory for a step."""
    p, _, _plan_path = _load(plan)
    found = p.find_step(step_id)
    if found is None:
        _die(f"Step not found: {step_id}")
    _phase, step = found
    from vectl.core_checklist import get_inventory

    revision_value, items = get_inventory.__wrapped__(step)
    payload = {
        "step_id": step_id,
        "checklist_inventory_revision": revision_value,
        "items": [_checklist_item_payload(item) for item in items],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    out.print(f"[bold]Checklist inventory:[/] {step_id}")
    out.print(f"checklist_inventory_revision: {revision_value}")
    for item in payload["items"]:
        mark = "x" if item["checked"] else " "
        out.print(f"  - [{mark}] {item['field']}[{item['index']}] {item['item_id']} — {item['text']}")


def _run_deterministic_check_mutation(
    p: Plan,
    *,
    step_id: str,
    keyword: str | None,
    revision: str | None,
    item_id: str | None,
    field: str | None,
    index: int | None,
    checked: str | None,
    batch: str | None,
) -> tuple[Plan, dict[str, Any]]:
    from vectl.core_checklist import get_inventory, mutate_checklist

    found = p.find_step(step_id)
    if found is None:
        raise PlanError(f"Step not found: {step_id}")
    if keyword is not None:
        from vectl.core_checklist import InvalidSelectorError, _error_payload_impl

        error = InvalidSelectorError("Cannot combine legacy keyword with deterministic selectors.")
        error.payload = _error_payload_impl("invalid_selector", str(error))  # type: ignore[attr-defined]
        raise error
    if revision is None:
        raise ValueError("Missing required parameter: checklist_inventory_revision")
    phase, step = found
    result = mutate_checklist.__wrapped__(
        step,
        revision,
        _parse_checklist_requests(batch=batch, item_id=item_id, field=field, index=index, checked=checked),
    )
    phase.steps = [result.step if existing is step else existing for existing in phase.steps]
    _current_revision, items = get_inventory.__wrapped__(result.step)
    return p, {
        "ok": True,
        "step_id": step_id,
        "checklist_inventory_revision": result.revision,
        "items": [_checklist_item_payload(item) for item in items],
        "diagnostics": _batch_diagnostics_payload(result.diagnostics),
    }


def _parse_checklist_requests(
    *, batch: str | None, item_id: str | None, field: str | None, index: int | None, checked: str | None
) -> list[Any]:
    from vectl.core_checklist import FieldIndexSelector, ItemIdSelector, MutationRequest

    if batch is not None:
        loaded = json.loads(batch)
        if not isinstance(loaded, list):
            raise ValueError("--batch must be a JSON array")
        return [_request_from_mapping(entry) for entry in loaded]
    if checked is None:
        raise ValueError("Missing required parameter: checked")
    checked_value = _parse_checked(checked)
    if item_id is not None:
        return [MutationRequest(ItemIdSelector(item_id), checked_value)]
    if field is not None and index is not None:
        return [MutationRequest(FieldIndexSelector(_supported_field(field), index), checked_value)]
    raise ValueError("Missing deterministic selector: provide --item-id or --field plus --index")


def _request_from_mapping(entry: Any) -> Any:
    from vectl.core_checklist import FieldIndexSelector, ItemIdSelector, LegacyKeywordSelector, MutationRequest

    if not isinstance(entry, dict):
        raise ValueError("Each batch request must be an object")
    selector_obj = entry.get("selector", {})
    if not isinstance(selector_obj, dict):
        raise ValueError("Each batch request selector must be an object")
    checked_obj = entry.get("checked")
    checked_value = checked_obj if isinstance(checked_obj, bool) else None
    if "item_id" in selector_obj:
        return MutationRequest(ItemIdSelector(str(selector_obj["item_id"])), checked_value)
    if "field" in selector_obj and "index" in selector_obj:
        return MutationRequest(
            FieldIndexSelector(_supported_field(str(selector_obj["field"])), int(selector_obj["index"])),
            checked_value,
        )
    if "keyword" in selector_obj:
        return MutationRequest(LegacyKeywordSelector(str(selector_obj["keyword"])), checked_value)
    raise ValueError("Each batch request requires item_id or field+index selector")


def _supported_field(field: str) -> Any:
    if field not in {"description", "verification"}:
        from vectl.core_checklist import UnsupportedFieldError, _error_payload_impl

        error = UnsupportedFieldError(f"Unsupported checklist field: {field}")
        error.payload = _error_payload_impl("unsupported_field", str(error), field=field)  # type: ignore[attr-defined]
        raise error
    return field


def _parse_checked(value: str) -> bool:
    lowered = value.casefold()
    if lowered in {"true", "1", "yes", "y", "on"}:
        return True
    if lowered in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError("--checked must be true or false")


def _checklist_item_payload(item: Any) -> dict[str, Any]:
    return {"item_id": item.item_id, "field": item.field, "index": item.index, "text": item.text, "checked": item.checked}


def _batch_diagnostics_payload(diagnostics: Any) -> dict[str, Any]:
    return {
        "total_requested": diagnostics.total_requested,
        "matched_items": diagnostics.matched_items,
        "changed_items": diagnostics.changed_items,
        "selector_diagnostics": [
            {k: v for k, v in asdict(diagnostic).items() if v is not None}
            for diagnostic in diagnostics.selector_diagnostics
        ],
    }


def _checklist_exception_payload(exc: Exception) -> dict[str, Any]:
    payload = getattr(exc, "payload", None)
    diagnostics = getattr(exc, "diagnostics", None)
    if payload is not None and is_dataclass(payload):
        error = asdict(payload)
        error["type"] = exc.__class__.__name__
        result = {"ok": False, "error": error}
        if diagnostics is not None:
            result["diagnostics"] = [asdict(diagnostic) for diagnostic in diagnostics]
        return result
    return {
        "ok": False,
        "error": {
            "type": exc.__class__.__name__,
            "code": "invalid_selector",
            "message": str(exc),
            "retry": {"retryable": True, "action": "fix_request", "message": "Fix the selector or request shape before retrying."},
        },
    }


def _print_checklist_error(payload: dict[str, Any]) -> None:
    error = payload["error"]
    out.print(f"[red bold]Error:[/] {error['type']}: {error['message']}")
    retry = error.get("retry", {})
    if retry:
        out.print(f"retry.action: {retry.get('action')}")
        out.print(f"retry.message: {retry.get('message')}")
