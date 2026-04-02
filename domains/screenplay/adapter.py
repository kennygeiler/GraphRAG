"""
Screenplay domain adapter: wires SceneGraph models, business rules, and
Anthropic LLM calls into an ``etl_core.graph_engine.DomainBundle``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from domains.screenplay.auditors import run_audits
from domains.screenplay.rules import validate_business_logic
from domains.screenplay.schemas import SceneGraph
from etl_core.graph_engine import DomainBundle
from extraction_llm import (
    call_audit_llm_with_usage,
    call_fix_llm_with_usage,
    call_llm_primary_fallback_with_usage,
)


def _extract_llm(raw_text: str, system_prompt: str) -> tuple[BaseModel, dict[str, Any]]:
    return call_llm_primary_fallback_with_usage(raw_text, system_prompt)


def _fix_llm(
    bad_json: dict[str, Any],
    error_text: str,
    system_prompt: str,
    raw_text: str,
) -> tuple[BaseModel, dict[str, Any]]:
    return call_fix_llm_with_usage(bad_json, error_text, system_prompt, raw_text)


def _audit_llm(
    graph_json: dict[str, Any],
    raw_text: str,
    system_prompt: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run all three auditors and combine findings + usage."""
    return run_audits(graph_json, raw_text, call_audit_llm_with_usage)


def get_bundle(*, lexicon_ids: set[str] | None = None, enable_audit: bool = True) -> DomainBundle:
    ids = lexicon_ids or set()

    def _business_rules(
        graph: dict[str, Any], context: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        ctx = dict(context)
        ctx["lexicon_ids"] = ids
        return validate_business_logic(graph, ctx)

    return DomainBundle(
        pydantic_model=SceneGraph,
        business_rules=_business_rules,
        extract_llm=_extract_llm,
        fix_llm=_fix_llm,
        audit_llm=_audit_llm if enable_audit else None,
    )
