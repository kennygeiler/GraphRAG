"""
LangGraph extraction pipeline: Extract → Validate → (Fixer → Validate)*.

Validator applies deterministic rules (duplicate LOCATED_IN per character) plus Pydantic.
Fixer LLM repairs JSON when validation fails (bounded attempts).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from extract_validation import ValidationException, validate_no_duplicate_located_in_for_same_character
from extraction_llm import call_fix_llm, call_llm_primary_fallback
from schema import SceneGraph

MAX_FIX_ATTEMPTS = 3


class ExtractionState(TypedDict, total=False):
    scene_number: int
    user_text: str
    system_prompt: str
    graph_json: dict[str, Any]
    last_error: str | None
    fix_count: int
    audit: list[dict[str, Any]]


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_node(state: ExtractionState) -> dict[str, Any]:
    sn = int(state["scene_number"])
    graph = call_llm_primary_fallback(state["user_text"], system_prompt=state["system_prompt"])
    gjson = graph.model_dump(mode="json")
    audit = list(state.get("audit") or [])
    audit.append(
        {
            "ts": _ts(),
            "scene_number": sn,
            "node": "extract",
            "detail": "llm_scene_graph",
        }
    )
    return {
        "graph_json": gjson,
        "audit": audit,
        "last_error": None,
        "fix_count": int(state.get("fix_count") or 0),
    }


def _validate_node(state: ExtractionState) -> dict[str, Any]:
    sn = int(state["scene_number"])
    audit = list(state.get("audit") or [])
    gj = state.get("graph_json") or {}
    try:
        validate_no_duplicate_located_in_for_same_character(gj)
        SceneGraph.model_validate(gj)
        audit.append(
            {
                "ts": _ts(),
                "scene_number": sn,
                "node": "validator",
                "detail": "pass",
            }
        )
        return {"last_error": None, "audit": audit}
    except ValidationException as e:
        msg = str(e)
        audit.append(
            {
                "ts": _ts(),
                "scene_number": sn,
                "node": "validator",
                "detail": "rule_failed",
                "error": msg,
            }
        )
        return {"last_error": msg, "audit": audit}
    except ValidationError as e:
        msg = str(e)
        audit.append(
            {
                "ts": _ts(),
                "scene_number": sn,
                "node": "validator",
                "detail": "pydantic_failed",
                "error": msg,
            }
        )
        return {"last_error": msg, "audit": audit}


def _fixer_node(state: ExtractionState) -> dict[str, Any]:
    sn = int(state["scene_number"])
    fc = int(state.get("fix_count") or 0) + 1
    fixed = call_fix_llm(
        state.get("graph_json") or {},
        state.get("last_error") or "",
        system_prompt=state["system_prompt"],
        user_text=state["user_text"],
    )
    audit = list(state.get("audit") or [])
    audit.append(
        {
            "ts": _ts(),
            "scene_number": sn,
            "node": "fixer",
            "detail": "llm_repair",
            "attempt": fc,
        }
    )
    return {
        "graph_json": fixed.model_dump(mode="json"),
        "fix_count": fc,
        "last_error": None,
        "audit": audit,
    }


def _route_after_validate(state: ExtractionState) -> Literal["fixer", "__end__"]:
    err = state.get("last_error")
    if not err:
        return END  # type: ignore[return-value]
    if int(state.get("fix_count") or 0) >= MAX_FIX_ATTEMPTS:
        return END  # type: ignore[return-value]
    return "fixer"


def build_extraction_graph() -> Any:
    g = StateGraph(ExtractionState)
    g.add_node("extract", _extract_node)
    g.add_node("validate", _validate_node)
    g.add_node("fixer", _fixer_node)
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"fixer": "fixer", END: END},
    )
    g.add_edge("fixer", "validate")
    return g.compile()


_compiled: Any | None = None


def get_compiled_graph() -> Any:
    global _compiled
    if _compiled is None:
        _compiled = build_extraction_graph()
    return _compiled


def run_extraction_pipeline(
    scene_number: int,
    user_text: str,
    system_prompt: str,
) -> tuple[SceneGraph | None, list[dict[str, Any]], str | None]:
    """
    Run LangGraph extract→validate→fix loop.

    Returns (SceneGraph, audit_entries, error_message).
    error_message is set if validation still fails after max fix attempts.
    """
    app = get_compiled_graph()
    out: ExtractionState = app.invoke(
        {
            "scene_number": scene_number,
            "user_text": user_text,
            "system_prompt": system_prompt,
            "audit": [],
            "fix_count": 0,
        }
    )
    audit = list(out.get("audit") or [])
    err = out.get("last_error")
    gj = out.get("graph_json")
    if err:
        return None, audit, err
    if not gj:
        return None, audit, "empty graph_json after pipeline"
    try:
        sg = SceneGraph.model_validate(gj)
    except ValidationError as e:
        return None, audit, str(e)
    return sg, audit, None
