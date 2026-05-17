"""Compatibility facade plus pure extraction namespace for vectl core.

The orchestration Core extraction introduced ``vectl.core.orchestration`` as a
package namespace.  Python packages take precedence over the legacy
``vectl/core.py`` module, so this package must also preserve the historical
``from vectl import core`` facade used by the orchestration shell adapter.
"""

from __future__ import annotations

from vectl.semantics import is_step_locked as _is_step_locked_shared
from vectl.core_duplicate_step_id import *  # noqa: F403
from vectl.core_plan_mutations import *  # noqa: F403
from vectl.core_plan_queries import *  # noqa: F403
from vectl.core_plan_mutations import (
    _SENTINEL,
    _Unset,
    _clipboard_expired,
    _slugify,
    _toggle_checklist_item,
    _unique_phase_id,
    _unique_step_id,
)
from vectl.core_plan_queries import (
    _detect_cycle,
    _extract_snippet,
    _first_line,
    _get_active_phase_ids,
    _mermaid_node_id,
    _mermaid_phase_dag,
    _mermaid_step_dag,
    _render_phase,
)
