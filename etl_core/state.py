"""LangGraph state schema for the ETL pipeline (domain-agnostic)."""

from __future__ import annotations

from typing import TypedDict

# Use object (not Any) for JSON-shaped dicts so LangGraph's get_type_hints(ETLState)
# never needs to resolve typing.Any from this module's globals (avoids rare NameError).
class ETLState(TypedDict, total=False):
    # Input
    raw_text: str
    system_prompt: str
    doc_id: str | int

    # Working data
    current_json: dict[str, object]
    retry_count: int
    audit_retry_count: int

    # Observability
    audit_trail: list[dict[str, object]]
    warnings: list[dict[str, object]]
    audit_decisions: list[dict[str, object]]
    lexicon_ids: list[str]
    total_tokens: int
    total_cost: float

    # Terminal
    last_error: str | None
