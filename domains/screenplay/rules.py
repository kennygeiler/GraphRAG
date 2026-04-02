"""
Deterministic business-logic validation for screenplay graph extractions.

Every function is pure Python — no LLM calls. Returns a list of human-readable
error strings; empty list means the extraction passed.
"""

from __future__ import annotations

from typing import Any


def _rels(graph: dict[str, Any]) -> list[dict[str, Any]]:
    rels = graph.get("relationships")
    return rels if isinstance(rels, list) else []


def _nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph.get("nodes")
    return nodes if isinstance(nodes, list) else []


def check_duplicate_located_in(graph: dict[str, Any]) -> list[str]:
    """A character may have at most one LOCATED_IN edge per extraction."""
    counts: dict[str, int] = {}
    for r in _rels(graph):
        if not isinstance(r, dict) or r.get("type") != "LOCATED_IN":
            continue
        sid = r.get("source_id")
        if isinstance(sid, str) and sid:
            counts[sid] = counts.get(sid, 0) + 1
    return [
        f"Duplicate LOCATED_IN: source_id={sid!r} appears {n} times (max 1)."
        for sid, n in counts.items()
        if n > 1
    ]


def check_dangling_edge_ids(graph: dict[str, Any]) -> list[str]:
    """Every edge source_id / target_id must reference an existing node id."""
    node_ids = {str(n.get("id")) for n in _nodes(graph) if isinstance(n, dict) and n.get("id")}
    errors: list[str] = []
    for r in _rels(graph):
        if not isinstance(r, dict):
            continue
        for field in ("source_id", "target_id"):
            val = r.get(field)
            if isinstance(val, str) and val and val not in node_ids:
                errors.append(
                    f"Dangling edge: {field}={val!r} in {r.get('type', '?')} edge not found in node list."
                )
    return errors


def validate_business_logic(graph: dict[str, Any]) -> list[str]:
    """
    Run all deterministic screenplay rules. Returns concatenated error strings.
    """
    errors: list[str] = []
    errors.extend(check_duplicate_located_in(graph))
    errors.extend(check_dangling_edge_ids(graph))
    return errors
