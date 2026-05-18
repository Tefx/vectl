"""Pure checklist Markdown inventory helpers.

Authority: docs/RFC-deterministic-checklists.md
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

from invar_runtime import post, pre

from vectl.models import Step


SupportedInventoryField = Literal["description", "verification"]
ChecklistInventoryRevision = str


@dataclass(frozen=True)
class InventoryRow:
    """Parsed checklist row before public API adaptation."""

    item_id: str
    field: SupportedInventoryField
    index: int
    text: str
    checked: bool


_CHECKLIST_ITEM_RE = re.compile(
    r"^(?P<prefix>\s*[-*+]\s+\[)(?P<marker>[ xX])(?P<suffix>\]\s*)(?P<text>.*)$"
)


@post(lambda result: isinstance(result, str))
def canonicalize_markdown(value: str | None) -> str:
    """Return Markdown with deterministic line endings.

    >>> canonicalize_markdown_impl(None)
    ''
    """
    if value is None:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n")


canonicalize_markdown_impl = canonicalize_markdown.__wrapped__


@pre(lambda step: step is not None)
@post(lambda result: result.startswith("description\0"))
def revision_material(step: Step) -> str:
    """Build the supported-field-only revision input.

    >>> from vectl.models import Step
    >>> "evidence" in revision_material_impl(Step(id="s", name="S", evidence_template="x"))
    False
    """
    description = canonicalize_markdown_impl(step.description)
    verification = canonicalize_markdown_impl(step.verification)
    return f"description\0{description}\0verification\0{verification}"


revision_material_impl = revision_material.__wrapped__


@pre(lambda step: step is not None)
@post(lambda result: result.startswith("sha256:"))
def calculate_revision(step: Step) -> ChecklistInventoryRevision:
    """Hash the canonical supported-field Markdown snapshot.

    >>> from vectl.models import Step
    >>> calculate_revision_impl(Step(id="s", name="S")) == calculate_revision_impl(Step(id="t", name="T"))
    True
    """
    digest = hashlib.sha256(revision_material_impl(step).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


calculate_revision_impl = calculate_revision.__wrapped__


@pre(
    lambda revision, field, index, line: revision != ""
    and field in ("description", "verification")
    and index >= 0
    and line is not None
)
@post(lambda result: result.count(":") == 2)
def item_id(
    revision: ChecklistInventoryRevision,
    field: SupportedInventoryField,
    index: int,
    line: str,
) -> str:
    """Return a deterministic snapshot-scoped item identifier.

    >>> item_id_impl("sha256:abc", "description", 0, "- [ ] A").startswith("description:0:")
    True
    """
    material = f"{revision}\0{field}\0{index}\0{line}".encode("utf-8")
    fragment = hashlib.sha256(material).hexdigest()[:12]
    return f"{field}:{index}:{fragment}"


item_id_impl = item_id.__wrapped__


@pre(
    lambda field, markdown, revision: field in ("description", "verification")
    and markdown is not None
    and revision != ""
)
@post(lambda result: all(row.index >= 0 for row in result))
def inventory_rows(
    field: SupportedInventoryField,
    markdown: str,
    revision: ChecklistInventoryRevision,
) -> list[InventoryRow]:
    """Parse checklist rows from one supported field.

    >>> inventory_rows_impl("description", "- [x] Done", "sha256:abc")[0].checked
    True
    """
    rows: list[InventoryRow] = []
    for line in markdown.split("\n"):
        match = _CHECKLIST_ITEM_RE.match(line)
        if match is None:
            continue
        index = len(rows)
        rows.append(
            InventoryRow(
                item_id=item_id_impl(revision, field, index, line),
                field=field,
                index=index,
                text=match.group("text"),
                checked=match.group("marker").lower() == "x",
            )
        )
    return rows


inventory_rows_impl = inventory_rows.__wrapped__


@pre(
    lambda markdown, target_index, checked: markdown is not None
    and target_index >= 0
    and isinstance(checked, bool)
)
@post(lambda result: isinstance(result[0], str) and isinstance(result[1], bool))
def set_item_marker(markdown: str, target_index: int, checked: bool) -> tuple[str, bool]:
    """Set a checklist marker while preserving non-marker text.

    >>> set_item_marker_impl("  - [ ]  A  ", 0, True)[0]
    '  - [x]  A  '
    """
    lines = markdown.splitlines(keepends=True)
    seen = 0
    replacement_marker = "x" if checked else " "
    for offset, line in enumerate(lines):
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        match = _CHECKLIST_ITEM_RE.match(line_body)
        if match is None:
            continue
        if seen != target_index:
            seen += 1
            continue
        if match.group("marker") == replacement_marker:
            return markdown, False
        changed_line = f"{match.group('prefix')}{replacement_marker}"
        lines[offset] = f"{changed_line}{match.group('suffix')}{match.group('text')}{newline}"
        return "".join(lines), True
    return markdown, False


set_item_marker_impl = set_item_marker.__wrapped__


@pre(lambda step: step is not None)
@post(lambda result: isinstance(result, tuple) and len(result) == 2)
def inventory_snapshot(step: Step) -> tuple[ChecklistInventoryRevision, list[InventoryRow]]:
    """Return revision and parsed rows for the supported fields.

    >>> from vectl.models import Step
    >>> inventory_snapshot_impl(Step(id="s", name="S", description="- [ ] A"))[1][0].text
    'A'
    """
    revision = calculate_revision_impl(step)
    description = canonicalize_markdown_impl(step.description)
    verification = canonicalize_markdown_impl(step.verification)
    rows = inventory_rows_impl("description", description, revision)
    rows.extend(inventory_rows_impl("verification", verification, revision))
    return revision, rows


inventory_snapshot_impl = inventory_snapshot.__wrapped__
