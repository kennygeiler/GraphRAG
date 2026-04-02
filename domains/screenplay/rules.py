"""
Deterministic business-logic validation for screenplay graph extractions.

Every function is pure Python — no LLM calls.  Error checks return error
strings that trigger the fixer loop.  Warning checks return structured dicts
that are surfaced in the Editor Agent tab for human review.
"""

from __future__ import annotations

import re
from typing import Any


def _rels(graph: dict[str, Any]) -> list[dict[str, Any]]:
    rels = graph.get("relationships")
    return rels if isinstance(rels, list) else []


def _nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _node_kind_map(graph: dict[str, Any]) -> dict[str, str]:
    """Return {node_id: kind} for all nodes."""
    out: dict[str, str] = {}
    for n in _nodes(graph):
        if isinstance(n, dict) and n.get("id") and n.get("kind"):
            out[str(n["id"])] = str(n["kind"])
    return out


_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


# ---------------------------------------------------------------------------
# Error checks (trigger fixer)
# ---------------------------------------------------------------------------

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


def check_self_referencing_edges(graph: dict[str, Any]) -> list[str]:
    """source_id must differ from target_id on every relationship."""
    errors: list[str] = []
    for r in _rels(graph):
        if not isinstance(r, dict):
            continue
        sid = r.get("source_id")
        tid = r.get("target_id")
        if sid and tid and str(sid) == str(tid):
            errors.append(
                f"Self-referencing edge: {r.get('type', '?')} has source_id=target_id={sid!r}."
            )
    return errors


def check_source_quote_in_text(graph: dict[str, Any], raw_text: str) -> list[str]:
    """Every source_quote must appear (case-insensitive, whitespace-normalized) in the raw scene text."""
    if not raw_text:
        return []
    norm_text = _normalize(raw_text)
    errors: list[str] = []
    for r in _rels(graph):
        if not isinstance(r, dict):
            continue
        quote = r.get("source_quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        if _normalize(quote) not in norm_text:
            errors.append(
                f"Hallucinated quote: source_quote={quote[:80]!r}… not found in scene text "
                f"(edge {r.get('type', '?')} {r.get('source_id', '?')}→{r.get('target_id', '?')})."
            )
    return errors


_VALID_TARGET_KIND: dict[str, set[str]] = {
    "LOCATED_IN": {"Location"},
    "POSSESSES": {"Prop"},
}
_VALID_SOURCE_KIND: dict[str, set[str]] = {
    "POSSESSES": {"Character"},
    "USES": {"Character"},
}


def check_relationship_kind_validity(graph: dict[str, Any]) -> list[str]:
    """Relationship types must connect sensible node kinds."""
    kind_map = _node_kind_map(graph)
    errors: list[str] = []
    for r in _rels(graph):
        if not isinstance(r, dict):
            continue
        rtype = r.get("type")
        sid = str(r.get("source_id", ""))
        tid = str(r.get("target_id", ""))
        s_kind = kind_map.get(sid)
        t_kind = kind_map.get(tid)

        if rtype in _VALID_TARGET_KIND and t_kind and t_kind not in _VALID_TARGET_KIND[rtype]:
            errors.append(
                f"Invalid target kind: {rtype} target {tid!r} is {t_kind}, expected {_VALID_TARGET_KIND[rtype]}."
            )
        if rtype in _VALID_SOURCE_KIND and s_kind and s_kind not in _VALID_SOURCE_KIND[rtype]:
            errors.append(
                f"Invalid source kind: {rtype} source {sid!r} is {s_kind}, expected {_VALID_SOURCE_KIND[rtype]}."
            )
    return errors


# ---------------------------------------------------------------------------
# Warning checks (human review, do not trigger fixer)
# ---------------------------------------------------------------------------

def check_lexicon_compliance(
    graph: dict[str, Any],
    lexicon_ids: set[str],
) -> list[dict[str, Any]]:
    """Warn when Character/Location IDs are not in the master lexicon."""
    if not lexicon_ids:
        return []
    warnings: list[dict[str, Any]] = []
    for n in _nodes(graph):
        if not isinstance(n, dict):
            continue
        kind = n.get("kind")
        nid = n.get("id")
        if kind in ("Character", "Location") and isinstance(nid, str) and nid not in lexicon_ids:
            warnings.append({
                "check": "lexicon_compliance",
                "severity": "warning",
                "detail": f"{kind} id={nid!r} not found in master lexicon.",
            })
    return warnings


def check_duplicate_relationships(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Warn on identical (source_id, target_id, type) tuples in one scene."""
    seen: dict[tuple[str, str, str], int] = {}
    for r in _rels(graph):
        if not isinstance(r, dict):
            continue
        key = (str(r.get("source_id", "")), str(r.get("target_id", "")), str(r.get("type", "")))
        seen[key] = seen.get(key, 0) + 1
    return [
        {
            "check": "duplicate_relationship",
            "severity": "warning",
            "detail": f"Duplicate relationship: ({k[0]}, {k[1]}, {k[2]}) appears {n} times.",
        }
        for k, n in seen.items()
        if n > 1
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_business_logic(
    graph: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Run all deterministic screenplay rules.

    Returns ``(errors, warnings)`` — errors trigger the fix loop, warnings
    are surfaced for human review.

    *context* may carry ``raw_text`` (str) and ``lexicon_ids`` (set[str]).
    """
    ctx = context or {}
    raw_text: str = ctx.get("raw_text", "")
    lexicon_ids: set[str] = ctx.get("lexicon_ids", set())

    errors: list[str] = []
    errors.extend(check_duplicate_located_in(graph))
    errors.extend(check_dangling_edge_ids(graph))
    errors.extend(check_self_referencing_edges(graph))
    errors.extend(check_source_quote_in_text(graph, raw_text))
    errors.extend(check_relationship_kind_validity(graph))

    warnings: list[dict[str, Any]] = []
    warnings.extend(check_lexicon_compliance(graph, lexicon_ids))
    warnings.extend(check_duplicate_relationships(graph))

    return errors, warnings
