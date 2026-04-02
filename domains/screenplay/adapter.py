"""
Screenplay domain adapter: wires SceneGraph models, business rules, and
Anthropic LLM calls into an ``etl_core.graph_engine.DomainBundle``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from domains.screenplay.rules import validate_business_logic
from domains.screenplay.schemas import SceneGraph
from etl_core.graph_engine import DomainBundle
from extraction_llm import call_fix_llm_with_usage, call_llm_primary_fallback_with_usage


def _extract_llm(raw_text: str, system_prompt: str) -> tuple[BaseModel, dict[str, Any]]:
    return call_llm_primary_fallback_with_usage(raw_text, system_prompt)


def _fix_llm(
    bad_json: dict[str, Any],
    error_text: str,
    system_prompt: str,
    raw_text: str,
) -> tuple[BaseModel, dict[str, Any]]:
    return call_fix_llm_with_usage(bad_json, error_text, system_prompt, raw_text)


def get_bundle() -> DomainBundle:
    return DomainBundle(
        pydantic_model=SceneGraph,
        business_rules=validate_business_logic,
        extract_llm=_extract_llm,
        fix_llm=_fix_llm,
    )
