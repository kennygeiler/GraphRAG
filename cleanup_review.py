"""Plain-language summaries for Verify tab (warnings) and pipeline correction copy."""

from __future__ import annotations

import copy
import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

_QUOTE_MERGE_SEP = "\n---\n"

_DUPLICATE_DETAIL_RE = re.compile(
    r"Duplicate relationship:\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*appears",
    re.IGNORECASE,
)


def warning_check_title(check: str) -> str:
    """Human-readable title for a warning `check` field."""
    key = str(check or "unknown").strip()
    titles: dict[str, str] = {
        "lexicon_compliance": "Lexicon compliance",
        "duplicate_relationship": "Duplicate relationship",
        "quote_fidelity": "Quote fidelity (semantic check)",
        "attribution": "Attribution (semantic check)",
        "completeness": "Possible missing edges (semantic check)",
        "audit_skipped": "Extra validation did not run",
    }
    return titles.get(key, key.replace("_", " ").title())


def warning_verify_guidance(check: str) -> str:
    """Short hint so the reviewer knows what Approve does."""
    key = str(check or "").strip()
    hints: dict[str, str] = {
        "lexicon_compliance": "Approve removes a Character/Location node that is not in the lexicon (and its edges). Use if the extractor invented or misspelled an id.",
        "duplicate_relationship": "Approve merges duplicate rows for the same (source, target, type) into one edge with combined quotes.",
        "quote_fidelity": "Approve removes one relationship flagged as weakly supported by the quote. Decline if you think the edge is still valid.",
        "attribution": "Approve removes one relationship where source/target may be swapped or wrong. Decline if attribution looks correct.",
        "completeness": "The check suggests something may be missing from the graph. Approve only records acknowledgment — you must edit the JSON yourself if you want new edges.",
        "audit_skipped": "Technical failure during an extra validation step; no automatic graph change. Approve/decline is informational only.",
    }
    return hints.get(
        key,
        "Approve applies the documented edit for this warning type before Neo4j load, if any; otherwise it is logged only.",
    )


def _truncate_hitl_text(text: str, max_chars: int = 420) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


def graph_entity_labels(graph: dict[str, Any]) -> dict[str, str]:
    """Map node id → short markdown label (display name + kind)."""
    out: dict[str, str] = {}
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return out
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id", "")).strip()
        if not nid:
            continue
        kind = str(n.get("kind", "?"))
        name = str(n.get("name", "")).strip()
        if name:
            out[nid] = f"**{name}** (`{nid}`, {kind})"
        else:
            out[nid] = f"`{nid}` ({kind})"
    return out


def _graph_for_warning_scene(
    warning: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any] | None:
    sn = warning.get("scene_number")
    if sn is None:
        return None
    try:
        sn_i = int(sn)
    except (TypeError, ValueError):
        return None
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            if int(e.get("scene_number") or -1) != sn_i:
                continue
        except (TypeError, ValueError):
            continue
        g = e.get("graph")
        if isinstance(g, dict):
            return g
    return None


def _relationship_rows_matching_key(
    graph: dict[str, Any], key: tuple[str, str, str]
) -> list[tuple[int, dict[str, Any]]]:
    rels = graph.get("relationships")
    if not isinstance(rels, list):
        return []
    out: list[tuple[int, dict[str, Any]]] = []
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            continue
        t = (str(r.get("source_id", "")), str(r.get("target_id", "")), str(r.get("type", "")))
        if t == key:
            out.append((i, r))
    return out


def warning_hitl_approve_preview(
    warning: dict[str, Any], entries: list[dict[str, Any]]
) -> str:
    """One-line description of what Approve does when loading (Verify tab)."""
    check = str(warning.get("check", ""))
    detail = str(warning.get("detail", ""))
    graph = _graph_for_warning_scene(warning, entries)

    if check == "duplicate_relationship" and graph:
        key = _parse_duplicate_key(detail)
        if key:
            rows = _relationship_rows_matching_key(graph, key)
            n = len(rows)
            if n >= 2:
                return (
                    f"Merges **{n}** duplicate `{key[2]}` rows "
                    f"(`{key[0]}` → `{key[1]}`) into **one** edge; combines `source_quote` with `---`."
                )
        return "Merges duplicate rows for the same (source, target, type) into one edge with combined quotes."

    if check == "lexicon_compliance" and graph:
        nid = _lexicon_id_from_detail(detail)
        labels = graph_entity_labels(graph)
        if nid:
            lab = labels.get(nid, f"`{nid}`")
            return f"Removes {lab} and every relationship connected to that id."
        return "Removes the non-lexicon node and all incident edges."

    if check in ("quote_fidelity", "attribution"):
        sn = warning.get("scene_number")
        if sn is None:
            return "Removes one relationship flagged by this warning (see evidence below)."
        try:
            sn_i = int(sn)
        except (TypeError, ValueError):
            return "Removes one relationship flagged by this warning (see evidence below)."
        orig_e = None
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                if int(e.get("scene_number") or -1) == sn_i:
                    orig_e = e
                    break
            except (TypeError, ValueError):
                continue
        if not orig_e or not isinstance(orig_e.get("graph"), dict):
            return "Removes one relationship identified by this warning."
        ident = _rel_identity_from_original(orig_e["graph"], warning.get("relationship_index"))
        if ident:
            t, s, tid = ident
            return f"Removes **one** `{t}` edge: `{s}` → `{tid}`."
        return "Removes one relationship identified by this warning."

    if check == "completeness":
        return "**No graph change** — acknowledgment only; edit JSON manually if you add edges."

    if check == "audit_skipped":
        return "**No graph change** — informational only."

    return warning_verify_guidance(check)


def warning_hitl_evidence_markdown(
    warning: dict[str, Any], entries: list[dict[str, Any]]
) -> str:
    """Markdown body for the Verify tab evidence expander."""
    check = str(warning.get("check", ""))
    detail = str(warning.get("detail", ""))
    graph = _graph_for_warning_scene(warning, entries)
    if graph is None:
        return "_No scene graph found in the current extract for this warning._"

    labels = graph_entity_labels(graph)

    if check == "duplicate_relationship":
        key = _parse_duplicate_key(detail)
        if not key:
            return "_Could not parse duplicate tuple from pipeline detail._\n\n" f"> {detail}"
        rows = _relationship_rows_matching_key(graph, key)
        if not rows:
            return f"_No matching relationships in graph for `{key}`._\n\n> {detail}"
        parts: list[str] = [
            f"**Duplicate tuple:** `{key[0]}` —(`{key[2]}`)→ `{key[1]}` "
            f"({len(rows)} row(s) in this scene)."
        ]
        for i, r in rows:
            sq = _truncate_hitl_text(str(r.get("source_quote", "")), 500)
            sid = str(r.get("source_id", ""))
            tid = str(r.get("target_id", ""))
            parts.append(f"- **Index `{i}`** · {labels.get(sid, f'`{sid}`')} → {labels.get(tid, f'`{tid}`')}")
            parts.append("")
            parts.append("```text")
            parts.append(sq if sq else "(empty source_quote)")
            parts.append("```")
        return "\n".join(parts)

    if check == "lexicon_compliance":
        nid = _lexicon_id_from_detail(detail)
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        if not nid:
            return f"_Could not parse node id._\n\n> {detail}"
        for i, n in enumerate(nodes):
            if isinstance(n, dict) and str(n.get("id")) == nid:
                kind = str(n.get("kind", "?"))
                name = str(n.get("name", ""))
                return (
                    f"**Node** `graph.nodes[{i}]` · id `{nid}` · kind **{kind}** · "
                    f"name **{name or '—'}**\n\n> {detail}"
                )
        return f"_Node `{nid}` not listed in this scene graph._\n\n> {detail}"

    if check in ("quote_fidelity", "attribution", "completeness"):
        idx = warning.get("relationship_index")
        rels = graph.get("relationships") if isinstance(graph.get("relationships"), list) else None
        if rels is not None and idx is not None:
            try:
                ix = int(idx)
            except (TypeError, ValueError):
                ix = -1
            if 0 <= ix < len(rels):
                r = rels[ix]
                if isinstance(r, dict):
                    sq = _truncate_hitl_text(str(r.get("source_quote", "")), 500)
                    sid = str(r.get("source_id", ""))
                    tid = str(r.get("target_id", ""))
                    typ = str(r.get("type", ""))
                    return (
                        f"**Relationship** `graph.relationships[{ix}]` · "
                        f"{labels.get(sid, f'`{sid}`')} —(`{typ}`)→ {labels.get(tid, f'`{tid}`')}\n\n"
                        "```text\n"
                        f"{sq if sq else '(empty source_quote)'}\n"
                        "```\n\n"
                        f"> {detail}"
                    )
        body = _truncate_hitl_text(detail, 800)
        return f"> {body}" if body else "_No additional structured evidence._"

    if check == "audit_skipped":
        return f"> {detail}" if detail else "_No detail._"

    body = _truncate_hitl_text(detail, 800)
    return f"> {body}" if body else "_No pipeline detail for this check._"


def cleanup_warning_widget_id(warning: dict[str, Any], index: int) -> str:
    """Stable key for Streamlit widgets and session decisions (must stay in sync with app)."""
    wid = f"s{warning.get('scene_number', 0)}_i{index}_{warning.get('check', 'x')}"
    return "".join(c if c.isalnum() else "_" for c in wid)


def _lexicon_id_from_detail(detail: str) -> str | None:
    m = re.search(r"id=(['\"])([^'\"]+)\1", detail)
    return m.group(2) if m else None


def _parse_duplicate_key(detail: str) -> tuple[str, str, str] | None:
    m = _DUPLICATE_DETAIL_RE.search(detail)
    if not m:
        return None
    return (m.group(1).strip(), m.group(2).strip(), m.group(3).strip())


def _collapse_duplicate_rel_tuple(rels: list[Any], key: tuple[str, str, str]) -> list[Any]:
    """Keep one relationship per key, merge source_quote across duplicates; order preserved."""
    matches: list[tuple[int, dict[str, Any]]] = []
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            continue
        k = (str(r.get("source_id", "")), str(r.get("target_id", "")), str(r.get("type", "")))
        if k == key:
            matches.append((i, r))
    if len(matches) <= 1:
        return [dict(x) if isinstance(x, dict) else x for x in rels]
    merged = dict(matches[0][1])
    parts = [(r.get("source_quote") or "").strip() for _, r in matches]
    parts = [p for p in parts if p]
    merged["source_quote"] = (
        _QUOTE_MERGE_SEP.join(parts) if parts else (merged.get("source_quote") or "")
    )
    skip = {i for i, _ in matches[1:]}
    first_i = matches[0][0]
    new: list[Any] = []
    for i, r in enumerate(rels):
        if i in skip:
            continue
        if i == first_i:
            new.append(merged)
        elif isinstance(r, dict):
            new.append(dict(r))
        else:
            new.append(r)
    return new


def _remove_lexicon_node(graph: dict[str, Any], node_id: str) -> bool:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return False
    new_nodes = [n for n in nodes if not (isinstance(n, dict) and str(n.get("id")) == node_id)]
    if len(new_nodes) == len(nodes):
        return False
    graph["nodes"] = new_nodes
    rels = graph.get("relationships")
    if isinstance(rels, list):
        graph["relationships"] = [
            dict(r)
            for r in rels
            if isinstance(r, dict)
            and str(r.get("source_id")) != node_id
            and str(r.get("target_id")) != node_id
        ]
    return True


def _remove_rel_matching_identity(
    graph: dict[str, Any],
    identity: tuple[str, str, str],
) -> bool:
    rels = graph.get("relationships")
    if not isinstance(rels, list):
        return False
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            continue
        rid = (str(r.get("type", "")), str(r.get("source_id", "")), str(r.get("target_id", "")))
        if rid == identity:
            graph["relationships"] = [dict(x) for j, x in enumerate(rels) if j != i]
            return True
    return False


def _rel_identity_from_original(
    original_graph: dict[str, Any], index: int | None
) -> tuple[str, str, str] | None:
    if index is None:
        return None
    rels = original_graph.get("relationships")
    if not isinstance(rels, list) or not (0 <= int(index) < len(rels)):
        return None
    r = rels[int(index)]
    if not isinstance(r, dict):
        return None
    return (str(r.get("type", "")), str(r.get("source_id", "")), str(r.get("target_id", "")))


def apply_approved_warning_edits(
    entries: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    decisions: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Deep-copy *entries* and apply graph edits for each warning marked **approved** in *decisions*.

    Relationship indices in audit warnings refer to *entries* as passed in (pre-edit). Lexicon and
    duplicate edits use the working copy so multiple approvals compose in one pass.

    - **lexicon_compliance**: remove the flagged Character/Location node and incident edges.
    - **duplicate_relationship**: merge duplicate edges for the parsed (source, target, type) tuple.
    - **quote_fidelity** / **attribution** (warnings): remove the relationship identified by
      *relationship_index* on the **original** graph (matched by type, source_id, target_id on the copy).
    - **completeness** / **audit_skipped**: no automatic edit (logged in messages).

    Returns ``(mutated_entries, human_readable_log_lines)``.
    """
    out = copy.deepcopy(entries)
    orig_by_num: dict[int, dict[str, Any]] = {}
    for e in entries:
        if not isinstance(e, dict) or e.get("scene_number") is None:
            continue
        orig_by_num[int(e["scene_number"])] = e

    by_num: dict[int, dict[str, Any]] = {}
    for e in out:
        if not isinstance(e, dict) or e.get("scene_number") is None:
            continue
        by_num[int(e["scene_number"])] = e

    log: list[str] = []

    for wi, w in enumerate(warnings):
        if not isinstance(w, dict):
            continue
        if decisions.get(cleanup_warning_widget_id(w, wi)) != "approved":
            continue
        sn = w.get("scene_number")
        if sn is None:
            continue
        sn_int = int(sn)
        entry = by_num.get(sn_int)
        if not entry or not isinstance(entry.get("graph"), dict):
            log.append(f"Scene {sn_int}: skip (no graph in working copy).")
            continue
        graph: dict[str, Any] = entry["graph"]
        check = str(w.get("check", ""))
        detail = str(w.get("detail", ""))

        if check == "lexicon_compliance":
            nid = _lexicon_id_from_detail(detail)
            if not nid:
                log.append(f"Scene {sn_int}: lexicon_compliance — could not parse node id.")
                continue
            if _remove_lexicon_node(graph, nid):
                log.append(f"Scene {sn_int}: removed non-lexicon node `{nid}` and incident edges.")
            else:
                log.append(f"Scene {sn_int}: lexicon_compliance — node `{nid}` not found (maybe already removed).")

        elif check == "duplicate_relationship":
            key = _parse_duplicate_key(detail)
            if not key:
                log.append(f"Scene {sn_int}: duplicate_relationship — could not parse tuple from detail.")
                continue
            rels = graph.get("relationships")
            if not isinstance(rels, list):
                continue
            before = len(rels)
            graph["relationships"] = _collapse_duplicate_rel_tuple(rels, key)
            after = len(graph["relationships"])
            if after < before:
                log.append(
                    f"Scene {sn_int}: merged duplicate `{key[2]}` edge `{key[0]}` → `{key[1]}` "
                    f"({before - after + 1} → 1)."
                )
            else:
                log.append(f"Scene {sn_int}: duplicate_relationship — no duplicate rows left for `{key}`.")

        elif check in ("quote_fidelity", "attribution"):
            orig_e = orig_by_num.get(sn_int)
            orig_g = orig_e.get("graph") if isinstance(orig_e, dict) else None
            if not isinstance(orig_g, dict):
                log.append(f"Scene {sn_int}: {check} — missing original graph for index lookup.")
                continue
            ident = _rel_identity_from_original(orig_g, w.get("relationship_index"))
            if not ident:
                log.append(f"Scene {sn_int}: {check} — invalid relationship_index.")
                continue
            if _remove_rel_matching_identity(graph, ident):
                log.append(
                    f"Scene {sn_int}: removed `{ident[0]}` `{ident[1]}` → `{ident[2]}` ({check})."
                )
            else:
                log.append(
                    f"Scene {sn_int}: {check} — edge `{ident[0]}` `{ident[1]}` → `{ident[2]}` already absent."
                )

        elif check == "completeness":
            log.append(
                f"Scene {sn_int}: completeness — no automatic edit (missing edges require manual graph edit)."
            )

        elif check == "audit_skipped":
            log.append(f"Scene {sn_int}: audit_skipped — no edit.")

        else:
            log.append(f"Scene {sn_int}: **{check}** — no automatic mutation rule; graph unchanged.")

    return out, log


def plain_english_fix_reason(reason: str) -> str:
    """Turn validator / fixer error text into a short human explanation."""
    if not reason or not reason.strip():
        return "The extraction did not pass automated checks; the model rewrote the graph."
    low = reason.lower()
    if "hallucinated quote" in low or "not found in scene text" in low:
        return (
            "At least one relationship cited script text that does not appear verbatim in the "
            "scene (or normalizes differently). Quotes must be copy-pasteable from the scene."
        )
    if "duplicate located_in" in low:
        return (
            "The same character was placed in more than one location in this scene. "
            "Only one LOCATED_IN edge per character is allowed."
        )
    if "dangling edge" in low:
        return (
            "A relationship pointed to a character, location, or prop id that was not listed "
            "in the scene's node list."
        )
    if "self-referencing edge" in low:
        return "A relationship had the same entity as both source and target, which is invalid."
    if "invalid target kind" in low or "invalid source kind" in low:
        return (
            "A relationship type was wired to the wrong kind of node "
            "(for example, LOCATED_IN must end at a Location)."
        )
    if "validation error" in low or "field required" in low:
        return "The JSON did not match the required schema (missing fields or wrong types)."
    if "1 validation error" in low or "validationerror" in low.replace(" ", ""):
        return "Pydantic schema validation failed on the extracted graph structure."
    # Audit-phase combined messages (free text)
    if len(reason) > 380:
        return reason[:380].rstrip() + "…"
    return reason


def summarize_graph_delta(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, str]:
    """Compact before/after descriptions (not full JSON)."""
    def _shape(g: dict[str, Any]) -> tuple[dict[str, int], list[tuple[str, str, str]]]:
        nodes = g.get("nodes") if isinstance(g.get("nodes"), list) else []
        rels = g.get("relationships") if isinstance(g.get("relationships"), list) else []
        nk: dict[str, int] = {}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            k = str(n.get("kind") or "?")
            nk[k] = nk.get(k, 0) + 1
        edges: list[tuple[str, str, str]] = []
        for r in rels:
            if not isinstance(r, dict):
                continue
            edges.append(
                (
                    str(r.get("type") or "?"),
                    str(r.get("source_id") or "?"),
                    str(r.get("target_id") or "?"),
                )
            )
        return nk, edges

    bn, br = _shape(before)
    an, ar = _shape(after)

    def _fmt_nodes(d: dict[str, int]) -> str:
        return ", ".join(f"{k}: {v}" for k, v in sorted(d.items())) or "(none)"

    before_lines = [
        f"**Nodes** — {_fmt_nodes(bn)}",
        f"**Relationships** — {len(br)} edge(s)",
    ]
    after_lines = [
        f"**Nodes** — {_fmt_nodes(an)}",
        f"**Relationships** — {len(ar)} edge(s)",
    ]

    bset, aset = set(br), set(ar)
    removed = sorted(bset - aset)
    added = sorted(aset - bset)
    if removed:
        before_lines.append("**Removed edges** (type, source → target):")
        for t, s, tid in removed[:12]:
            before_lines.append(f"- `{t}`: `{s}` → `{tid}`")
        if len(removed) > 12:
            before_lines.append(f"- … and {len(removed) - 12} more")
    if added:
        after_lines.append("**Added edges**:")
        for t, s, tid in added[:12]:
            after_lines.append(f"- `{t}`: `{s}` → `{tid}`")
        if len(added) > 12:
            after_lines.append(f"- … and {len(added) - 12} more")
    if not removed and not added and br == ar:
        before_lines.append("_Edge list unchanged; node or quote text may have been edited._")

    return "\n".join(before_lines), "\n".join(after_lines)


def warning_json_location(warning: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    """Human-readable path into the extracted scene graph for a warning."""
    sn = warning.get("scene_number")
    check = str(warning.get("check", "unknown"))
    detail = str(warning.get("detail", ""))
    idx = warning.get("relationship_index")

    entry: dict[str, Any] | None = None
    if sn is not None:
        for e in entries:
            if not isinstance(e, dict):
                continue
            if int(e.get("scene_number") or -1) == int(sn):
                entry = e
                break
    graph = (entry or {}).get("graph") if entry else None
    if not isinstance(graph, dict):
        return f"Scene **{sn}** / `graph` — _scene not found in current extract_"

    base = f"Scene **{sn}**"

    if idx is not None and isinstance(graph.get("relationships"), list):
        rels = graph["relationships"]
        if 0 <= int(idx) < len(rels):
            r = rels[int(idx)]
            if isinstance(r, dict):
                return (
                    f"{base} → `graph.relationships[{idx}]` "
                    f"(`{r.get('type', '?')}`: `{r.get('source_id', '?')}` → `{r.get('target_id', '?')}`)"
                )
        return f"{base} → `graph.relationships` (index {idx} out of range)"

    if check == "lexicon_compliance":
        m = re.search(r"id='([^']+)'|id=([^\\s)]+)", detail)
        nid = (m.group(1) or m.group(2)).strip("'\"") if m else None
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        for i, n in enumerate(nodes):
            if isinstance(n, dict) and nid and str(n.get("id")) == nid:
                return f"{base} → `graph.nodes[{i}]` (id `{nid}`)"
        return f"{base} → `graph.nodes` — {detail[:120]}"

    if check == "duplicate_relationship":
        return f"{base} → `graph.relationships` — duplicate tuple in this scene"

    if check == "audit_skipped":
        return f"{base} — _Extra validation step failed; deterministic checks only_"

    if check in ("quote_fidelity", "completeness", "attribution"):
        if idx is not None and isinstance(graph.get("relationships"), list):
            return f"{base} → `graph.relationships[{idx}]` — **{check}**"
        return f"{base} → `graph` — **{check}**"

    return f"{base} → `graph` — **{check}**"


def build_verify_audit_payload(
    warnings: list[Any],
    decisions: dict[str, str],
    note_by_widget_id: dict[str, str],
    *,
    neo4j_loaded_at_iso: str | None = None,
    pipeline_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured audit document for Verify HITL exports (CSV/JSON)."""
    exported_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for wi, w in enumerate(warnings):
        if not isinstance(w, dict):
            continue
        wid = cleanup_warning_widget_id(w, wi)
        rows.append(
            {
                "warning_index": wi,
                "widget_id": wid,
                "scene_number": w.get("scene_number"),
                "check": w.get("check"),
                "severity": w.get("severity"),
                "relationship_index": w.get("relationship_index"),
                "decision": decisions.get(wid, "unset"),
                "note": (note_by_widget_id.get(wid) or "").strip(),
                "detail": str(w.get("detail") or ""),
                "exported_at": exported_at,
                "neo4j_load_completed_at": neo4j_loaded_at_iso or "",
            }
        )
    meta: dict[str, Any] = {
        "exported_at": exported_at,
        "neo4j_load_completed_at": neo4j_loaded_at_iso,
    }
    if pipeline_meta:
        meta.update(pipeline_meta)
    return {"meta": meta, "decisions": rows}


def verify_audit_to_csv(payload: dict[str, Any]) -> str:
    """CSV string for ``build_verify_audit_payload`` output."""
    rows = list(payload.get("decisions") or [])
    fieldnames = [
        "warning_index",
        "widget_id",
        "scene_number",
        "check",
        "severity",
        "relationship_index",
        "decision",
        "note",
        "detail",
        "exported_at",
        "neo4j_load_completed_at",
    ]
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    wr.writeheader()
    for r in rows:
        line: dict[str, Any] = {}
        for k in fieldnames:
            v = r.get(k)
            s = "" if v is None else str(v)
            if k in ("detail", "note"):
                s = s.replace("\r\n", " ").replace("\n", " ⏎ ").replace("\r", " ")
            line[k] = s
        wr.writerow(line)
    return buf.getvalue()


def verify_audit_to_json(payload: dict[str, Any]) -> str:
    """Pretty JSON string (UTF-8 safe when encoded)."""
    return json.dumps(payload, ensure_ascii=False, indent=2)
