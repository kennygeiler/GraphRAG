"""Deterministic validation rules for extracted scene graphs (before / beside Pydantic)."""

from __future__ import annotations

from typing import Any


class ValidationException(Exception):
    """Raised when a deterministic extraction rule fails (e.g. duplicate edges)."""


def validate_no_duplicate_located_in_for_same_character(graph: dict[str, Any]) -> None:
    """
    In a single scene, a character must not have more than one LOCATED_IN edge.

    Multiple LOCATED_IN from the same source_id (character) indicates conflicting
    location placement for one beat — invalid for downstream Neo4j co-location logic.
    """
    rels = graph.get("relationships") or []
    if not isinstance(rels, list):
        raise ValidationException("relationships must be a list")

    counts: dict[str, int] = {}
    for r in rels:
        if not isinstance(r, dict):
            continue
        if r.get("type") != "LOCATED_IN":
            continue
        sid = r.get("source_id")
        if not isinstance(sid, str) or not sid:
            continue
        counts[sid] = counts.get(sid, 0) + 1

    for source_id, n in counts.items():
        if n > 1:
            raise ValidationException(
                f"Duplicate LOCATED_IN for the same character in the same scene: "
                f"source_id={source_id!r} has {n} LOCATED_IN edges (expected at most 1)."
            )
