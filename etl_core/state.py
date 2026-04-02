"""LangGraph state schema for the ETL pipeline (domain-agnostic)."""

from __future__ import annotations

from typing import Any, TypedDict


class ETLState(TypedDict, total=False):
    # Input
    raw_text: str
    system_prompt: str
    doc_id: str | int

    # Working data
    current_json: dict[str, Any]
    retry_count: int

    # Observability
    audit_trail: list[dict[str, Any]]
    total_tokens: int
    total_cost: float

    # Terminal
    last_error: str | None
