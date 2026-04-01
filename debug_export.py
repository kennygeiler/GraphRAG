#!/usr/bin/env python3
"""
Export a QA sample of the Neo4j narrative graph to JSON (scenes 1, 40, 86).

Requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD in the environment (.env).
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from neo4j import GraphDatabase

_ROOT = Path(__file__).resolve().parent
OUT_PATH = _ROOT / "graph_qa_dump.json"

SAMPLE_SCENES = (1, 40, 86)


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"❌ Missing {name} in environment (.env).", flush=True)
        sys.exit(1)
    return v


def _get_driver():
    return GraphDatabase.driver(
        _require_env("NEO4J_URI"),
        auth=(_require_env("NEO4J_USER"), _require_env("NEO4J_PASSWORD")),
    )


def _export_scene(session, scene_number: int) -> dict[str, Any]:
    meta = session.run(
        "MATCH (e:Event {number: $n}) RETURN e.number AS scene_number, e.heading AS heading",
        n=int(scene_number),
    ).single()
    if meta is None:
        return {
            "scene_number": int(scene_number),
            "heading": None,
            "nodes": [],
            "relationships": [],
            "error": "No Event node for this scene_number",
        }

    node_rows = session.run(
        """
        MATCH (e:Event {number: $n})
        MATCH (x)-[:IN_SCENE]->(e)
        WITH DISTINCT x
        RETURN x.id AS id, labels(x)[0] AS label
        ORDER BY label, id
        """,
        n=int(scene_number),
    ).data()

    nodes = [{"id": r["id"], "label": r["label"]} for r in node_rows]
    # Anchor the scene (:Event has no `id` in the loader — use a synthetic QA id).
    nodes.append({"id": f"scene_{int(meta['scene_number'])}", "label": "Event"})
    nodes.sort(key=lambda x: (x["label"], x["id"]))

    rel_rows = session.run(
        """
        MATCH (e:Event {number: $n})
        MATCH (x)-[:IN_SCENE]->(e)
        WITH collect(DISTINCT elementId(x)) AS ids
        MATCH (a)-[r]-(b)
        WHERE elementId(a) IN ids AND elementId(b) IN ids
        RETURN coalesce(a.id, '') AS source,
               coalesce(b.id, '') AS target,
               type(r) AS type,
               r.source_quote AS source_quote
        """,
        n=int(scene_number),
    ).data()

    seen: set[tuple[str, str, str, str | None]] = set()
    relationships: list[dict[str, Any]] = []
    for r in rel_rows:
        sq = r.get("source_quote")
        if sq is None:
            sq = ""
        sid, tid = r["source"], r["target"]
        lo, hi = (sid, tid) if sid <= tid else (tid, sid)
        key = (lo, hi, r["type"], sq if isinstance(sq, str) else str(sq))
        if key in seen:
            continue
        seen.add(key)
        relationships.append(
            {
                "source": r["source"],
                "target": r["target"],
                "type": r["type"],
                "source_quote": sq if isinstance(sq, str) else "",
            }
        )
    relationships.sort(key=lambda x: (x["type"], x["source"], x["target"]))

    return {
        "scene_number": int(meta["scene_number"]),
        "heading": meta.get("heading") or "",
        "nodes": nodes,
        "relationships": relationships,
    }


def _redundancy_same_in_scene_count(session, min_group_size: int = 5) -> list[dict[str, Any]]:
    rows = session.run(
        """
        MATCH (c:Character)
        OPTIONAL MATCH (c)-[:IN_SCENE]->(e:Event)
        WITH c, count(DISTINCT e) AS in_scene_count
        RETURN c.id AS id, c.name AS name, in_scene_count
        ORDER BY in_scene_count DESC, c.id
        """
    ).data()

    by_count: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        cnt = int(r["in_scene_count"])
        by_count[cnt].append(
            {"id": r["id"], "name": r.get("name"), "in_scene_count": cnt}
        )

    groups: list[dict[str, Any]] = []
    for cnt in sorted(by_count.keys(), reverse=True):
        members = by_count[cnt]
        if len(members) >= min_group_size:
            groups.append(
                {
                    "in_scene_count": cnt,
                    "character_count": len(members),
                    "characters": members,
                }
            )
    return groups


def main() -> None:
    driver = _get_driver()
    try:
        with driver.session() as session:
            scenes = [_export_scene(session, n) for n in SAMPLE_SCENES]
            redundancy = {
                "description": (
                    "Characters grouped by identical total IN_SCENE count across the graph "
                    f"(groups with at least 5 characters)."
                ),
                "groups": _redundancy_same_in_scene_count(session, min_group_size=5),
            }
    finally:
        driver.close()

    payload = {
        "sample_scenes": SAMPLE_SCENES,
        "scenes": scenes,
        "redundancy_check": redundancy,
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
