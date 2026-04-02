"""
Domain-agnostic LangGraph engine: extract → validate → (fix → validate)*.

All screenplay-specific logic is injected via a DomainBundle so this module
never imports from ``domains.*`` or ``schema``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from etl_core.errors import MaxRetriesError
from etl_core.state import ETLState
from etl_core.telemetry import accumulate_usage

MAX_FIX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class DomainBundle:
    """Everything the engine needs from a specific domain (screenplay, legal, …)."""

    pydantic_model: type[BaseModel]
    business_rules: Callable[[dict[str, Any]], list[str]]

    extract_llm: Callable[[str, str], tuple[BaseModel, dict[str, Any]]]
    """(raw_text, system_prompt) → (parsed model, usage_dict).
    usage_dict keys: model, input_tokens, output_tokens."""

    fix_llm: Callable[[dict[str, Any], str, str, str], tuple[BaseModel, dict[str, Any]]]
    """(bad_json, error_text, system_prompt, raw_text) → (fixed model, usage_dict)."""

    system_prompt_fn: Callable[..., str] = field(default=lambda: "")
    """Returns the system prompt; called with no args (or keyword args) by the engine."""


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_extractor(bundle: DomainBundle):
    def _extract(state: ETLState) -> dict[str, Any]:
        model_obj, usage = bundle.extract_llm(state["raw_text"], state["system_prompt"])
        gjson = model_obj.model_dump(mode="json")
        audit = list(state.get("audit_trail") or [])
        audit.append({"ts": _ts(), "doc_id": state.get("doc_id"), "node": "extract", "detail": "llm_extract"})
        updates: dict[str, Any] = {
            "current_json": gjson,
            "audit_trail": audit,
            "last_error": None,
            "retry_count": int(state.get("retry_count") or 0),
        }
        updates.update(accumulate_usage(state, **usage))
        return updates
    return _extract


def _build_validator(bundle: DomainBundle):
    def _validate(state: ETLState) -> dict[str, Any]:
        audit = list(state.get("audit_trail") or [])
        gj = state.get("current_json") or {}
        errors: list[str] = []

        try:
            bundle.pydantic_model.model_validate(gj)
        except ValidationError as e:
            errors.append(str(e))

        errors.extend(bundle.business_rules(gj))

        if errors:
            combined = "; ".join(errors)
            audit.append({"ts": _ts(), "doc_id": state.get("doc_id"), "node": "validate", "detail": "fail", "error": combined})
            return {"last_error": combined, "audit_trail": audit}

        audit.append({"ts": _ts(), "doc_id": state.get("doc_id"), "node": "validate", "detail": "pass"})
        return {"last_error": None, "audit_trail": audit}
    return _validate


def _build_fixer(bundle: DomainBundle):
    def _fix(state: ETLState) -> dict[str, Any]:
        rc = int(state.get("retry_count") or 0) + 1
        bad_json = state.get("current_json") or {}
        error_text = state.get("last_error") or ""
        before_snapshot = dict(bad_json)

        fixed_obj, usage = bundle.fix_llm(bad_json, error_text, state["system_prompt"], state["raw_text"])
        fixed_json = fixed_obj.model_dump(mode="json")

        audit = list(state.get("audit_trail") or [])
        audit.append({
            "ts": _ts(),
            "doc_id": state.get("doc_id"),
            "node": "fixer",
            "detail": "llm_repair",
            "attempt": rc,
            "before": before_snapshot,
            "after": fixed_json,
            "reason": error_text,
        })
        updates: dict[str, Any] = {
            "current_json": fixed_json,
            "retry_count": rc,
            "last_error": None,
            "audit_trail": audit,
        }
        updates.update(accumulate_usage(state, **usage))
        return updates
    return _fix


def _route_after_validate(state: ETLState) -> Literal["fixer", "__end__"]:
    if not state.get("last_error"):
        return END  # type: ignore[return-value]
    if int(state.get("retry_count") or 0) >= MAX_FIX_ATTEMPTS:
        return END  # type: ignore[return-value]
    return "fixer"


def build_graph(bundle: DomainBundle):
    g = StateGraph(ETLState)
    g.add_node("extract", _build_extractor(bundle))
    g.add_node("validate", _build_validator(bundle))
    g.add_node("fixer", _build_fixer(bundle))
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", _route_after_validate, {"fixer": "fixer", END: END})
    g.add_edge("fixer", "validate")
    return g.compile()


def run_pipeline(
    bundle: DomainBundle,
    *,
    raw_text: str,
    system_prompt: str,
    doc_id: str | int = "",
    compiled=None,
) -> ETLState:
    """
    Execute the full extract→validate→fix loop, returning the final ETLState.

    Raises MaxRetriesError if validation still fails after MAX_FIX_ATTEMPTS.
    """
    app = compiled or build_graph(bundle)
    state: ETLState = app.invoke({
        "raw_text": raw_text,
        "system_prompt": system_prompt,
        "doc_id": doc_id,
        "audit_trail": [],
        "retry_count": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
    })
    if state.get("last_error"):
        raise MaxRetriesError(int(state.get("retry_count") or 0), state["last_error"])
    return state
