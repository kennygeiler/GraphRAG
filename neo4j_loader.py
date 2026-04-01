from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from pipeline_state import record_neo4j_loader_ok

_ROOT = Path(__file__).resolve().parent
DEFAULT_GRAPH = _ROOT / "validated_graph.json"

ENTITY_LABELS = frozenset({"Character", "Location", "Prop"})
NARRATIVE_REL_TYPES = frozenset(
    {"INTERACTS_WITH", "LOCATED_IN", "USES", "CONFLICTS_WITH", "POSSESSES"}
)

_DEDUP_BY_ENDPOINT_TYPES = frozenset({"LOCATED_IN", "INTERACTS_WITH", "CONFLICTS_WITH"})
_SOURCE_QUOTE_MERGE_SEP = "\n---\n"


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"❌ Missing {name} in environment (.env).", flush=True)
        sys.exit(1)
    return v


def _wipe_graph(tx: Any) -> None:
    tx.run("MATCH (n) DETACH DELETE n")


def _merge_event(tx: Any, scene_number: int, heading: str) -> None:
    num = int(scene_number)
    tx.run(
        """
        MERGE (e:Event {number: $number})
        SET e.number = toInteger($number),
            e.heading = $heading
        """,
        number=num,
        heading=heading or "",
    )


def _merge_entity(tx: Any, label: str, entity_id: str, name: str) -> None:
    if label not in ENTITY_LABELS:
        raise ValueError(f"Invalid entity label: {label!r}")
    tx.run(
        f"MERGE (n:{label} {{id: $id}}) SET n.name = $name",
        id=entity_id,
        name=name,
    )


def _merge_in_scene(tx: Any, label: str, entity_id: str, scene_number: int) -> None:
    if label not in ENTITY_LABELS:
        return
    tx.run(
        f"""
        MATCH (n:{label} {{id: $id}})
        MATCH (e:Event {{number: $number}})
        MERGE (n)-[:IN_SCENE]->(e)
        """,
        id=entity_id,
        number=int(scene_number),
    )


def _dedupe_relationships(
    relationships: list[dict[str, Any]],
    *,
    scene_number: int | None = None,
) -> list[dict[str, Any]]:
    """
    Per scene: for LOCATED_IN / INTERACTS_WITH / CONFLICTS_WITH, one edge per
    (source_id, target_id, type). Concatenate source_quote when merging duplicates.
    USES and POSSESSES pass through unchanged.
    """
    if not relationships:
        return []

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    group_order: list[tuple[str, str, str]] = []

    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        st = rel.get("type")
        sid = rel.get("source_id")
        tid = rel.get("target_id")
        if st not in _DEDUP_BY_ENDPOINT_TYPES or not sid or not tid:
            continue
        key = (str(sid), str(tid), str(st))
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(rel)

    merged_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key in group_order:
        rels = groups[key]
        if len(rels) == 1:
            merged_by_key[key] = dict(rels[0])
            continue
        merged = dict(rels[0])
        parts = [(r.get("source_quote") or "").strip() for r in rels]
        parts = [p for p in parts if p]
        merged["source_quote"] = (
            _SOURCE_QUOTE_MERGE_SEP.join(parts) if parts else (rels[0].get("source_quote") or "")
        )
        merged_by_key[key] = merged
        sn = f"scene {scene_number}" if scene_number is not None else "scene"
        print(
            f"  ({sn}) merged {len(rels)} duplicate `{key[2]}` edges {key[0]} → {key[1]}",
            flush=True,
        )

    out: list[dict[str, Any]] = []
    seen_dedup: set[tuple[str, str, str]] = set()
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        st = rel.get("type")
        sid = rel.get("source_id")
        tid = rel.get("target_id")
        if st in _DEDUP_BY_ENDPOINT_TYPES and sid and tid:
            key = (str(sid), str(tid), str(st))
            if key in seen_dedup:
                continue
            seen_dedup.add(key)
            out.append(merged_by_key[key])
        else:
            out.append(rel)

    return out


def _create_narrative_edge(
    tx: Any,
    source_id: str,
    target_id: str,
    rel_type: str,
    source_quote: str,
) -> None:
    assert rel_type in NARRATIVE_REL_TYPES
    tx.run(
        f"""
        MATCH (a {{id: $sid}})
        WHERE a:Character OR a:Location OR a:Prop
        MATCH (b {{id: $tid}})
        WHERE b:Character OR b:Location OR b:Prop
        CREATE (a)-[r:`{rel_type}` {{source_quote: $quote}}]->(b)
        """,
        sid=source_id,
        tid=target_id,
        quote=source_quote,
    )


def _load_validated_graph(tx: Any, entries: list[dict[str, Any]]) -> None:
    _wipe_graph(tx)
    for entry in entries:
        scene_number = entry.get("scene_number")
        if scene_number is None:
            print("⚠️ Skipping entry without scene_number", flush=True)
            continue
        heading = entry.get("heading") or ""
        graph = entry.get("graph") or {}
        nodes = graph.get("nodes") or []
        rel_raw = graph.get("relationships") or []
        relationships = _dedupe_relationships(
            rel_raw if isinstance(rel_raw, list) else [],
            scene_number=int(scene_number),
        )

        _merge_event(tx, int(scene_number), str(heading))

        for node in nodes:
            kind = node.get("kind")
            if kind not in ENTITY_LABELS:
                continue
            eid = node.get("id")
            name = node.get("name") or ""
            if not eid:
                continue
            _merge_entity(tx, kind, str(eid), str(name))
            _merge_in_scene(tx, kind, str(eid), int(scene_number))

        for rel in relationships:
            st = rel.get("type")
            if st not in NARRATIVE_REL_TYPES:
                print(f"⚠️ Skipping unknown relationship type: {st!r}", flush=True)
                continue
            sid = rel.get("source_id")
            tid = rel.get("target_id")
            quote = rel.get("source_quote") or ""
            if not sid or not tid:
                continue
            _create_narrative_edge(tx, str(sid), str(tid), st, str(quote))


def _print_graph_stats(session: Any) -> None:
    print("--- Node counts by label ---", flush=True)
    known = ["Event", "Character", "Location", "Prop"]
    for lab in known:
        c = session.run(f"MATCH (n:{lab}) RETURN count(n) AS c").single()
        assert c is not None
        print(f"  {lab}: {c['c']}", flush=True)

    print("--- Relationship counts by type ---", flush=True)
    rel_rows = session.run(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS c
        ORDER BY rel_type
        """
    )
    for record in rel_rows:
        print(f"  {record['rel_type']}: {record['c']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load validated_graph.json into Neo4j.")
    parser.add_argument(
        "graph_path",
        type=Path,
        nargs="?",
        default=DEFAULT_GRAPH,
        help=f"Path to validated_graph.json (default: {DEFAULT_GRAPH})",
    )
    args = parser.parse_args()

    if not args.graph_path.is_file():
        print(f"❌ File not found: {args.graph_path}", flush=True)
        sys.exit(1)

    raw = json.loads(args.graph_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print("❌ validated_graph.json must be a JSON array.", flush=True)
        sys.exit(1)
    print(
        f"JSON: {len(raw)} scene record(s) in {args.graph_path.name} — one :Event per record after load.",
        flush=True,
    )

    uri = _require_env("NEO4J_URI")
    user = _require_env("NEO4J_USER")
    password = _require_env("NEO4J_PASSWORD")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            session.execute_write(_load_validated_graph, raw)
        with driver.session() as session:
            _print_graph_stats(session)
        print("Load complete.", flush=True)
        try:
            record_neo4j_loader_ok(entries_loaded=len(raw), path_name=args.graph_path.name)
        except OSError:
            pass
    finally:
        driver.close()


if __name__ == "__main__":
    main()
