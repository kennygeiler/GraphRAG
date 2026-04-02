"""
Thin adapter: preserves the ``run_extraction_pipeline`` signature that
``ingest.py`` relies on, while delegating to ``etl_core.graph_engine``.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from domains.screenplay.adapter import get_bundle
from etl_core.errors import MaxRetriesError
from etl_core.graph_engine import build_graph, run_pipeline
from schema import SceneGraph

_compiled = None
_cached_lexicon_ids: set[str] | None = None


def _get_compiled(lexicon_ids: set[str] | None = None):
    global _compiled, _cached_lexicon_ids
    ids = lexicon_ids or set()
    if _compiled is None or ids != _cached_lexicon_ids:
        _compiled = build_graph(get_bundle(lexicon_ids=ids))
        _cached_lexicon_ids = ids
    return _compiled


def run_extraction_pipeline(
    scene_number: int,
    user_text: str,
    system_prompt: str,
    *,
    lexicon_ids: set[str] | None = None,
) -> tuple[SceneGraph | None, list[dict[str, Any]], str | None, dict[str, Any], list[dict[str, Any]]]:
    """
    Run extract→validate→fix via etl_core.

    Returns ``(SceneGraph | None, audit_entries, error_msg | None, telemetry_dict, warnings)``.
    ``telemetry_dict`` has keys ``total_tokens`` and ``total_cost``.
    """
    bundle = get_bundle(lexicon_ids=lexicon_ids)
    empty_telem = {"total_tokens": 0, "total_cost": 0.0}
    try:
        state = run_pipeline(
            bundle,
            raw_text=user_text,
            system_prompt=system_prompt,
            doc_id=scene_number,
            compiled=_get_compiled(lexicon_ids),
        )
    except MaxRetriesError as e:
        return None, [], str(e), empty_telem, []

    audit = list(state.get("audit_trail") or [])
    warnings = list(state.get("warnings") or [])
    gj = state.get("current_json")
    telem = {"total_tokens": state.get("total_tokens", 0), "total_cost": state.get("total_cost", 0.0)}

    if not gj:
        return None, audit, "empty current_json after pipeline", telem, warnings
    try:
        sg = SceneGraph.model_validate(gj)
    except ValidationError as e:
        return None, audit, str(e), telem, warnings
    return sg, audit, None, telem, warnings
