"""Shared Anthropic + instructor calls for scene extraction and fix-up.

Also exposes ``*_with_usage`` variants that return ``(model, usage_dict)`` for
``etl_core`` telemetry integration.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import instructor
from anthropic import Anthropic, APIStatusError
from instructor.core.exceptions import InstructorRetryException
from pydantic import BaseModel, ValidationError

from schema import SceneGraph

PRIMARY_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-3-haiku-20240307"
_MAX_TOKENS = 4096

_anthropic_raw: Anthropic | None = None
_instructor_client: Any | None = None


def _ensure_clients() -> tuple[Anthropic, Any]:
    global _anthropic_raw, _instructor_client
    if _anthropic_raw is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key is None:
            print("❌ Missing ANTHROPIC_API_KEY. Please add it to your .env file.", flush=True)
            sys.exit(1)
        _anthropic_raw = Anthropic(api_key=api_key)
        _instructor_client = instructor.from_anthropic(_anthropic_raw)
    return _anthropic_raw, _instructor_client  # type: ignore[return-value]


def _get_anthropic_client() -> Any:
    """Return the instructor-wrapped client (backward compat)."""
    _, client = _ensure_clients()
    return client


def _usage_dict(model: str, completion: Any) -> dict[str, Any]:
    """Extract a usage dict from an Anthropic Message (raw or completion)."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return {"model": model, "input_tokens": 0, "output_tokens": 0}
    return {
        "model": model,
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }


def call_llm(model: str, prompt: str, *, system_prompt: str) -> SceneGraph:
    """Run one structured-output completion."""
    client = _get_anthropic_client()
    try:
        return client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            response_model=SceneGraph,
            max_retries=1,
        )
    except APIStatusError:
        raise
    except InstructorRetryException:
        raise


def call_llm_with_usage(
    model: str,
    prompt: str,
    *,
    system_prompt: str,
    response_model: type[BaseModel] = SceneGraph,
) -> tuple[BaseModel, dict[str, Any]]:
    """Like ``call_llm`` but returns ``(parsed_model, usage_dict)``."""
    client = _get_anthropic_client()
    try:
        result, completion = client.messages.create_with_completion(
            model=model,
            max_tokens=_MAX_TOKENS,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            response_model=response_model,
            max_retries=1,
        )
        return result, _usage_dict(model, completion)
    except APIStatusError:
        raise
    except InstructorRetryException:
        raise


def call_llm_primary_fallback(user_text: str, *, system_prompt: str) -> SceneGraph:
    """Primary model with Haiku fallback (same behavior as legacy ingest)."""
    try:
        return call_llm(PRIMARY_MODEL, user_text, system_prompt=system_prompt)
    except ValidationError:
        raise
    except APIStatusError:
        print("⚠️ Primary model request failed (see error above). Retrying with Haiku...", flush=True)
        return call_llm(FALLBACK_MODEL, user_text, system_prompt=system_prompt)
    except Exception as e:
        print(
            f"⚠️ Primary model failed ({type(e).__name__}: {e}). Retrying with Haiku...",
            flush=True,
        )
        return call_llm(FALLBACK_MODEL, user_text, system_prompt=system_prompt)


def call_llm_primary_fallback_with_usage(
    user_text: str,
    system_prompt: str,
) -> tuple[BaseModel, dict[str, Any]]:
    """Primary→Haiku fallback, returning ``(model, usage_dict)`` for telemetry."""
    try:
        return call_llm_with_usage(PRIMARY_MODEL, user_text, system_prompt=system_prompt)
    except (APIStatusError, Exception):
        return call_llm_with_usage(FALLBACK_MODEL, user_text, system_prompt=system_prompt)


def call_fix_llm(
    bad_graph: dict[str, Any],
    validation_error: str,
    *,
    system_prompt: str,
    user_text: str,
) -> SceneGraph:
    """Fixer agent: receives invalid graph JSON + error text; returns a corrected SceneGraph."""
    fix_system = _build_fix_system_prompt(system_prompt)
    user_msg = _build_fix_user_msg(bad_graph, validation_error, user_text)
    try:
        return call_llm(PRIMARY_MODEL, user_msg, system_prompt=fix_system)
    except APIStatusError:
        print("⚠️ Fixer primary failed; retrying with Haiku...", flush=True)
        return call_llm(FALLBACK_MODEL, user_msg, system_prompt=fix_system)


def call_fix_llm_with_usage(
    bad_graph: dict[str, Any],
    validation_error: str,
    system_prompt: str,
    user_text: str,
) -> tuple[BaseModel, dict[str, Any]]:
    """Fixer with telemetry: returns ``(fixed_model, usage_dict)``."""
    fix_system = _build_fix_system_prompt(system_prompt)
    user_msg = _build_fix_user_msg(bad_graph, validation_error, user_text)
    try:
        return call_llm_with_usage(PRIMARY_MODEL, user_msg, system_prompt=fix_system)
    except (APIStatusError, Exception):
        return call_llm_with_usage(FALLBACK_MODEL, user_msg, system_prompt=fix_system)


def _build_fix_system_prompt(original_system: str) -> str:
    return (
        "You are a Narrative Graph Repair Assistant. The scene graph JSON failed validation.\n\n"
        "Your job: output a corrected SceneGraph that satisfies ALL rules:\n"
        "- Same schema as before: nodes (Character, Location, Prop only — no Event nodes) and relationships.\n"
        "- Every relationship must have source_quote verbatim from the script.\n"
        "- Fix the specific validation error below. For duplicate LOCATED_IN: keep exactly one LOCATED_IN per "
        "character source (choose the best-supported location by source_quote); remove redundant LOCATED_IN edges.\n"
        "- Preserve all other valid edges and nodes where possible.\n"
        f"\nOriginal extraction instructions (for context):\n{original_system[:12000]}"
    )


def _build_fix_user_msg(bad_graph: dict[str, Any], error: str, user_text: str) -> str:
    payload = {"validation_error": error, "bad_graph": bad_graph, "scene_text": user_text}
    return "Fix this graph.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)[:100000]
