"""Shared Anthropic + instructor calls for scene extraction and fix-up."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import instructor
from anthropic import Anthropic, APIStatusError
from instructor.core.exceptions import InstructorRetryException
from pydantic import ValidationError

from schema import SceneGraph

_PRIMARY_MODEL = "claude-sonnet-4-6"
_FALLBACK_MODEL = "claude-3-haiku-20240307"
_MAX_TOKENS = 4096

_instructor_client: Any | None = None


def _get_anthropic_client() -> Any:
    global _instructor_client
    if _instructor_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key is None:
            print("❌ Missing ANTHROPIC_API_KEY. Please add it to your .env file.", flush=True)
            sys.exit(1)
        _instructor_client = instructor.from_anthropic(Anthropic(api_key=api_key))
    return _instructor_client


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


def call_llm_primary_fallback(user_text: str, *, system_prompt: str) -> SceneGraph:
    """Primary model with Haiku fallback (same behavior as legacy ingest)."""
    try:
        return call_llm(_PRIMARY_MODEL, user_text, system_prompt=system_prompt)
    except ValidationError:
        raise
    except APIStatusError:
        print("⚠️ Primary model request failed (see error above). Retrying with Haiku...", flush=True)
        return call_llm(_FALLBACK_MODEL, user_text, system_prompt=system_prompt)
    except Exception as e:
        print(
            f"⚠️ Primary model failed ({type(e).__name__}: {e}). Retrying with Haiku...",
            flush=True,
        )
        return call_llm(_FALLBACK_MODEL, user_text, system_prompt=system_prompt)


def call_fix_llm(
    bad_graph: dict[str, Any],
    validation_error: str,
    *,
    system_prompt: str,
    user_text: str,
) -> SceneGraph:
    """
    Fixer agent: receives invalid graph JSON + error text; returns a corrected SceneGraph.
    """
    fix_system = (
        "You are a Narrative Graph Repair Assistant. The scene graph JSON failed validation.\n\n"
        "Your job: output a corrected SceneGraph that satisfies ALL rules:\n"
        "- Same schema as before: nodes (Character, Location, Prop only — no Event nodes) and relationships.\n"
        "- Every relationship must have source_quote verbatim from the script.\n"
        "- Fix the specific validation error below. For duplicate LOCATED_IN: keep exactly one LOCATED_IN per "
        "character source (choose the best-supported location by source_quote); remove redundant LOCATED_IN edges.\n"
        "- Preserve all other valid edges and nodes where possible.\n"
        f"\nOriginal extraction instructions (for context):\n{system_prompt[:12000]}"
    )
    payload = {
        "validation_error": validation_error,
        "bad_graph": bad_graph,
        "scene_text": user_text,
    }
    user_msg = (
        "Fix this graph.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)[:100000]
    )
    try:
        return call_llm(_PRIMARY_MODEL, user_msg, system_prompt=fix_system)
    except APIStatusError:
        print("⚠️ Fixer primary failed; retrying with Haiku...", flush=True)
        return call_llm(_FALLBACK_MODEL, user_msg, system_prompt=fix_system)
