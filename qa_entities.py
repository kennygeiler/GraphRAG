#!/usr/bin/env python3
"""
Audit Neo4j narrative graph for physical / data consistency.

Outputs data_health_report.json with consistency breaks keyed by scene number.

Requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD (.env).
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from neo4j import GraphDatabase

_ROOT = Path(__file__).resolve().parent
OUT_PATH = _ROOT / "data_health_report.json"

FUZZY_MIN_RATIO = 0.85
ENTITY_LABELS_FOR_FUZZY = ("Character", "Location", "Prop")


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"❌ Missing {name} in environment (.env).", flush=True)
        sys.exit(1)
    return v


def _driver():
    return GraphDatabase.driver(
        _require_env("NEO4J_URI"),
        auth=(_require_env("NEO4J_USER"), _require_env("NEO4J_PASSWORD")),
    )


def _name_ratio(a: str, b: str) -> float:
    x = (a or "").strip().lower()
    y = (b or "").strip().lower()
    if not x or not y:
        return 0.0
    return float(SequenceMatcher(None, x, y).ratio())


def _fetch_uses_prop_scenes(session) -> list[dict[str, Any]]:
    """Character USES Prop co-present scenes (both IN_SCENE same Event)."""
    return session.run(
        """
        MATCH (u:Character)-[:USES]->(p:Prop)
        MATCH (u)-[:IN_SCENE]->(e:Event)
        MATCH (p)-[:IN_SCENE]->(e)
        RETURN DISTINCT u.id AS user_id, coalesce(u.name, '') AS user_name,
               p.id AS prop_id, coalesce(p.name, '') AS prop_name,
               e.number AS scene_number
        ORDER BY scene_number, prop_id, user_id
        """
    ).data()


def _prior_possession_scene_exists(session, prop_id: str, before_scene: int) -> bool:
    row = session.run(
        """
        MATCH (c:Character)-[:POSSESSES]->(p:Prop {id: $pid})
        MATCH (c)-[:IN_SCENE]->(em:Event)
        MATCH (p)-[:IN_SCENE]->(em)
        WHERE em.number < $n
        RETURN 1 AS ok LIMIT 1
        """,
        pid=prop_id,
        n=int(before_scene),
    ).single()
    return row is not None


def _prop_located_in_scene_locations(session, prop_id: str, scene_number: int) -> bool:
    """Prop -> Location LOCATED_IN, Location in this scene."""
    row = session.run(
        """
        MATCH (p:Prop {id: $pid})-[:LOCATED_IN]->(loc:Location)
        MATCH (loc)-[:IN_SCENE]->(e:Event {number: $n})
        RETURN 1 AS ok LIMIT 1
        """,
        pid=prop_id,
        n=int(scene_number),
    ).single()
    if row is not None:
        return True
    # Reverse direction if ever loaded that way
    row = session.run(
        """
        MATCH (loc:Location)-[:LOCATED_IN]->(p:Prop {id: $pid})
        MATCH (loc)-[:IN_SCENE]->(e:Event {number: $n})
        RETURN 1 AS ok LIMIT 1
        """,
        pid=prop_id,
        n=int(scene_number),
    ).single()
    return row is not None


def _run_teleportation_check(session) -> list[dict[str, Any]]:
    breaks: list[dict[str, Any]] = []
    rows = _fetch_uses_prop_scenes(session)
    for r in rows:
        n = int(r["scene_number"])
        pid = r["prop_id"]
        prior = _prior_possession_scene_exists(session, pid, n)
        located = _prop_located_in_scene_locations(session, pid, n)
        if not prior and not located:
            breaks.append(
                {
                    "break_type": "teleportation",
                    "scene_number": n,
                    "prop_id": pid,
                    "prop_name": r.get("prop_name"),
                    "user_id": r["user_id"],
                    "user_name": r.get("user_name"),
                    "detail": (
                        "USES in this scene but no prior joint POSSESSES scene (strictly before this "
                        "scene number) and no Prop LOCATED_IN a Location present in this scene."
                    ),
                }
            )
    return breaks


def _run_orphan_detection(session) -> list[dict[str, Any]]:
    rows = session.run(
        """
        MATCH (c:Character)
        OPTIONAL MATCH (c)-[:IN_SCENE]->(e:Event)
        WITH c, count(DISTINCT e) AS sc, collect(DISTINCT e.number) AS scene_nums
        WHERE sc = 1
        OPTIONAL MATCH (c)-[rx:INTERACTS_WITH|CONFLICTS_WITH|USES|LOCATED_IN|POSSESSES]-()
        WITH c, scene_nums, count(rx) AS rc
        WHERE rc = 0
        RETURN c.id AS id, coalesce(c.name, '') AS name, scene_nums[0] AS scene_number
        ORDER BY c.id
        """
    ).data()
    out: list[dict[str, Any]] = []
    for r in rows:
        sn = r.get("scene_number")
        if sn is None:
            continue
        out.append(
            {
                "break_type": "orphan_character",
                "scene_number": int(sn),
                "character_id": r["id"],
                "character_name": r.get("name"),
                "detail": (
                    "Exactly one IN_SCENE event and zero narrative edges "
                    "(INTERACTS_WITH, CONFLICTS_WITH, USES, LOCATED_IN, POSSESSES — any direction)."
                ),
            }
        )
    return out


def _fetch_nodes_for_fuzzy(session, label: str) -> list[dict[str, str]]:
    q = f"""
    MATCH (n:{label})
    RETURN n.id AS id, coalesce(n.name, '') AS name
    ORDER BY n.id
    """
    return session.run(q).data()


def _run_fuzzy_identity(session) -> tuple[list[dict[str, Any]], dict[str, set[int]]]:
    """
    Pairs with similarity > FUZZY_MIN_RATIO (same label), excluding identical normalized names.
    Returns (pairs, scene_map: entity_id -> set of scene numbers) for attribution.
    """
    pairs: list[dict[str, Any]] = []
    id_to_scenes: dict[str, set[int]] = defaultdict(set)

    scene_rows = session.run(
        """
        MATCH (n)
        WHERE n:Character OR n:Location OR n:Prop
        MATCH (n)-[:IN_SCENE]->(e:Event)
        RETURN labels(n)[0] AS label, n.id AS id, collect(DISTINCT e.number) AS scenes
        """
    ).data()
    for sr in scene_rows:
        for num in sr["scenes"]:
            if num is not None:
                id_to_scenes[str(sr["id"])].add(int(num))

    for label in ENTITY_LABELS_FOR_FUZZY:
        nodes = _fetch_nodes_for_fuzzy(session, label)
        n = len(nodes)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = nodes[i], nodes[j]
                na, nb = a["name"], b["name"]
                if not na or not nb:
                    continue
                if na.strip().lower() == nb.strip().lower():
                    continue
                ratio = _name_ratio(na, nb)
                if ratio > FUZZY_MIN_RATIO:
                    pairs.append(
                        {
                            "break_type": "fuzzy_identity",
                            "label": label,
                            "id_a": a["id"],
                            "name_a": na,
                            "id_b": b["id"],
                            "name_b": nb,
                            "similarity_ratio": round(ratio, 4),
                            "detail": (
                                f"Name similarity {ratio:.2%} — possible duplicate entity "
                                f"('{na}' vs '{nb}')."
                            ),
                        }
                    )
    return pairs, dict(id_to_scenes)


def _merge_by_scene(
    teleportation: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    fuzzy_pairs: list[dict[str, Any]],
    id_to_scenes: dict[str, set[int]],
) -> tuple[dict[str, dict[str, list[Any]]], list[dict[str, Any]]]:
    by_scene: dict[str, dict[str, list[Any]]] = defaultdict(
        lambda: {"teleportation": [], "orphan_character": [], "fuzzy_identity": []}
    )
    fuzzy_no_scene: list[dict[str, Any]] = []

    for item in teleportation:
        k = str(item["scene_number"])
        by_scene[k]["teleportation"].append(item)

    for item in orphans:
        k = str(item["scene_number"])
        by_scene[k]["orphan_character"].append(item)

    for item in fuzzy_pairs:
        scenes = id_to_scenes.get(item["id_a"], set()) | id_to_scenes.get(item["id_b"], set())
        if not scenes:
            fuzzy_no_scene.append(item)
            continue
        for sn in sorted(scenes):
            by_scene[str(sn)]["fuzzy_identity"].append(item)

    sorted_keys = sorted(
        by_scene.keys(),
        key=lambda k: int(k) if k.isdigit() else 10**9,
    )
    ordered = {k: dict(by_scene[k]) for k in sorted_keys}
    return ordered, fuzzy_no_scene


def main() -> None:
    drv = _driver()
    try:
        with drv.session() as session:
            teleportation = _run_teleportation_check(session)
            orphans = _run_orphan_detection(session)
            fuzzy_pairs, id_to_scenes = _run_fuzzy_identity(session)
            by_scene, fuzzy_no_scene = _merge_by_scene(
                teleportation, orphans, fuzzy_pairs, id_to_scenes
            )
    finally:
        drv.close()

    report = {
        "description": (
            "Consistency breaks grouped by scene number. Teleportation: USES on a Prop in a scene "
            "without prior joint POSSESSES in an earlier scene and without Prop LOCATED_IN a Location "
            "in that scene. Orphans: single-scene characters with no narrative edges "
            "(INTERACTS_WITH, CONFLICTS_WITH, USES, LOCATED_IN, POSSESSES). "
            f"Fuzzy: same-label names with difflib ratio > {FUZZY_MIN_RATIO}."
        ),
        "summary": {
            "teleportation_breaks": len(teleportation),
            "orphan_characters": len(orphans),
            "fuzzy_pairs": len(fuzzy_pairs),
            "scenes_with_any_break": len(by_scene),
        },
        "by_scene_number": by_scene,
        "global": {
            "fuzzy_pairs_without_in_scene": fuzzy_no_scene,
            "all_teleportation_breaks": teleportation,
            "all_orphan_characters": orphans,
            "all_fuzzy_pairs": fuzzy_pairs,
        },
    }

    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH}", flush=True)
    print(
        f"  teleportation={len(teleportation)} orphans={len(orphans)} fuzzy_pairs={len(fuzzy_pairs)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
