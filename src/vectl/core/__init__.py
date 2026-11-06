"""Compatibility facade for vectl core plan operations.

Python packages take precedence over the legacy ``vectl/core.py`` module, so
this package preserves the historical ``from vectl import core`` facade.
"""

from __future__ import annotations

from vectl.core.batch_claim import *  # noqa: F403
from vectl.core_duplicate_step_id import *  # noqa: F403
from vectl.core_plan_mutations import *  # noqa: F403
from vectl.core_plan_mutations import (
    _SENTINEL as _SENTINEL,
    _clipboard_expired as _clipboard_expired,
    _slugify as _slugify,
    _toggle_checklist_item as _toggle_checklist_item,
    _unique_phase_id as _unique_phase_id,
    _unique_step_id as _unique_step_id,
    _Unset as _Unset,
)
from vectl.core_plan_queries import *  # noqa: F403
from vectl.core_plan_queries import (
    _detect_cycle as _detect_cycle,
    _extract_snippet as _extract_snippet,
    _first_line as _first_line,
    _get_active_phase_ids as _get_active_phase_ids,
    _mermaid_node_id as _mermaid_node_id,
    _mermaid_phase_dag as _mermaid_phase_dag,
    _mermaid_step_dag as _mermaid_step_dag,
    _render_phase as _render_phase,
)
from vectl.semantics import is_step_locked

_is_step_locked_shared = is_step_locked
