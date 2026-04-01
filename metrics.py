from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import os
import sys
from typing import Any

from neo4j import Driver, GraphDatabase

__all__ = [
    "get_driver",
    "get_passivity_score",
    "get_passivity_in_scene_window",
    "get_load_bearing_props",
    "get_possessed_but_unused_props",
    "get_props_possession_only_early_uses",
    "get_props_act1_possess_no_act3_payoff",
    "get_character_agency_trajectory",
    "get_scene_heat",
    "get_scene_inspector_data",
    "list_characters",
    "get_character_in_scene_counts",
    "build_sequence_ranges",
    "list_event_numbers",
    "get_script_act_bounds",
    "get_narrative_momentum_by_scene",
    "get_payoff_prop_timelines",
    "get_top_characters_by_interaction_count",
]


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"❌ Missing {name} in environment (.env).", flush=True)
        sys.exit(1)
    return v


def get_driver() -> Driver:
    uri = _require_env("NEO4J_URI")
    user = _require_env("NEO4J_USER")
    password = _require_env("NEO4J_PASSWORD")
    return GraphDatabase.driver(uri, auth=(user, password))


def list_characters(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """Return all Character nodes as {id, name}, sorted by display name."""
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            return session.run(
                """
                MATCH (c:Character)
                RETURN c.id AS id, c.name AS name
                ORDER BY toLower(c.name), c.id
                """
            ).data()
    finally:
        if own:
            drv.close()


def get_character_in_scene_counts(*, driver: Driver | None = None) -> dict[str, int]:
    """Map Character id → number of distinct :Event nodes linked via IN_SCENE."""
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (c:Character)
                OPTIONAL MATCH (c)-[:IN_SCENE]->(e:Event)
                RETURN c.id AS id, count(DISTINCT e) AS cnt
                """
            ).data()
        return {str(r["id"]): int(r["cnt"]) for r in rows}
    finally:
        if own:
            drv.close()


def get_passivity_score(character_id: str, *, driver: Driver | None = None) -> float | None:
    """
    Protagonist Passivity Index for a character.

    In-degree (things done *to* the character in this metric):
      - Incoming CONFLICTS_WITH onto the character.
      - USES relationships targeting a Prop the character POSSESSES.

    Out-degree (things the character *does*):
      - Outgoing CONFLICTS_WITH from the character.
      - Outgoing USES from the character.

    Passivity = In / (In + Out). Returns None if the character is missing or has no such edges.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            row = session.run(
                """
                MATCH (c:Character {id: $id})
                CALL (c) {
                  OPTIONAL MATCH ()-[ic:CONFLICTS_WITH]->(c)
                  RETURN count(ic) AS in_conf
                }
                CALL (c) {
                  OPTIONAL MATCH (c)-[:POSSESSES]->(p)<-[iu:USES]-()
                  RETURN count(iu) AS in_uses
                }
                CALL (c) {
                  OPTIONAL MATCH (c)-[oc:CONFLICTS_WITH]->()
                  RETURN count(oc) AS out_conf
                }
                CALL (c) {
                  OPTIONAL MATCH (c)-[ou:USES]->()
                  RETURN count(ou) AS out_uses
                }
                WITH in_conf + in_uses AS in_deg, out_conf + out_uses AS out_deg
                WITH in_deg, out_deg, in_deg + out_deg AS total
                RETURN CASE
                  WHEN total = 0 THEN NULL
                  ELSE toFloat(in_deg) / toFloat(total)
                END AS passivity
                """,
                id=character_id,
            ).single()
            if row is None or row["passivity"] is None:
                return None
            return float(row["passivity"])
    finally:
        if own:
            drv.close()


def list_event_numbers(*, driver: Driver | None = None) -> list[int]:
    """Distinct Event.scene numbers, sorted ascending."""
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                "MATCH (e:Event) RETURN DISTINCT e.number AS n ORDER BY n"
            ).data()
        return [int(r["n"]) for r in rows]
    finally:
        if own:
            drv.close()


def get_script_act_bounds(*, driver: Driver | None = None) -> dict[str, Any] | None:
    """
    Derive act windows from :Event.number in Neo4j (min..max inclusive).

    Splits the inclusive scene span into **three as-equal-as-possible** buckets
    (same idea as first/middle/final “thirds” of the loaded script). Also returns
    vertical-line anchors at the first scene of Act 2 and Act 3 when those are
    distinct structural breaks.

    Returns None when there are no Event nodes.
    """
    nums = list_event_numbers(driver=driver)
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    n = hi - lo + 1
    if n <= 0:
        return None

    if n == 1:
        act1 = act2 = act3 = (lo, hi)
        b1 = b2 = lo
    elif n == 2:
        act1 = (lo, lo)
        act2 = act3 = (hi, hi)
        b1 = b2 = hi
    else:
        len1 = (n + 2) // 3
        rem = n - len1
        len2 = (rem + 1) // 2
        len3 = rem - len2
        act1_lo, act1_hi = lo, lo + len1 - 1
        act2_lo, act2_hi = act1_hi + 1, act1_hi + len2
        act3_lo, act3_hi = act2_hi + 1, hi
        act1, act2, act3 = (act1_lo, act1_hi), (act2_lo, act2_hi), (act3_lo, act3_hi)
        b1, b2 = act2_lo, act3_lo

    return {
        "min_scene": lo,
        "max_scene": hi,
        "scene_count": n,
        "act1": act1,
        "act2": act2,
        "act3": act3,
        "break_after_act1_scene": b1,
        "break_after_act2_scene": b2,
    }


def build_sequence_ranges(scene_numbers: list[int], window: int = 10) -> list[tuple[int, int, str]]:
    """
    Bucket scene numbers into fixed-width windows (default 10), aligned so the first
    window starts at 1, 11, 21, … for window=10. Labels like 'Scenes 1–10'.
    """
    if not scene_numbers or window < 1:
        return []
    mn, mx = min(scene_numbers), max(scene_numbers)
    lo = ((mn - 1) // window) * window + 1
    ranges: list[tuple[int, int, str]] = []
    while lo <= mx:
        hi = min(lo + window - 1, mx)
        label = f"Scenes {lo}–{hi}"
        ranges.append((lo, hi, label))
        lo = hi + 1
    return ranges


def get_passivity_in_scene_window(
    character_id: str,
    lo: int,
    hi: int,
    *,
    driver: Driver | None = None,
) -> dict[str, Any]:
    """
    Same passivity definition as ``get_passivity_score``, but only narrative edges
    that share at least one :Event in [lo, hi] with *both* endpoints (see loader:
    edges are created per scene; co-presence on that Event attributes the edge).
    """
    empty: dict[str, Any] = {
        "passivity": None,
        "in_deg": 0,
        "out_deg": 0,
        "in_conf": 0,
        "in_uses": 0,
        "out_conf": 0,
        "out_uses": 0,
    }
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            row = session.run(
                """
                MATCH (c:Character {id: $id})
                OPTIONAL MATCH (src)-[ic:CONFLICTS_WITH]->(c),
                              (src)-[:IN_SCENE]->(e1:Event),
                              (c)-[:IN_SCENE]->(e1)
                WHERE e1.number >= $lo AND e1.number <= $hi
                WITH c, count(DISTINCT ic) AS in_conf
                OPTIONAL MATCH (c)-[:POSSESSES]->(p:Prop),
                              (src2)-[iu:USES]->(p),
                              (src2)-[:IN_SCENE]->(e2:Event),
                              (p)-[:IN_SCENE]->(e2)
                WHERE e2.number >= $lo AND e2.number <= $hi
                WITH c, in_conf, count(DISTINCT iu) AS in_uses
                OPTIONAL MATCH (c)-[oc:CONFLICTS_WITH]->(tgt),
                              (c)-[:IN_SCENE]->(e3:Event),
                              (tgt)-[:IN_SCENE]->(e3)
                WHERE e3.number >= $lo AND e3.number <= $hi
                WITH c, in_conf, in_uses, count(DISTINCT oc) AS out_conf
                OPTIONAL MATCH (c)-[ou:USES]->(tgt2),
                              (c)-[:IN_SCENE]->(e4:Event),
                              (tgt2)-[:IN_SCENE]->(e4)
                WHERE e4.number >= $lo AND e4.number <= $hi
                WITH in_conf, in_uses, out_conf, count(DISTINCT ou) AS out_uses
                WITH in_conf + in_uses AS in_deg,
                     out_conf + out_uses AS out_deg,
                     in_conf,
                     in_uses,
                     out_conf,
                     out_uses
                RETURN in_deg, out_deg, in_conf, in_uses, out_conf, out_uses,
                       CASE WHEN in_deg + out_deg = 0 THEN NULL
                            ELSE toFloat(in_deg) / toFloat(in_deg + out_deg)
                       END AS passivity
                """,
                id=character_id,
                lo=int(lo),
                hi=int(hi),
            ).single()
        if row is None:
            return dict(empty)
        tot_in = int(row["in_deg"])
        tot_out = int(row["out_deg"])
        if tot_in + tot_out == 0:
            return {
                **empty,
                "in_conf": int(row["in_conf"]),
                "in_uses": int(row["in_uses"]),
                "out_conf": int(row["out_conf"]),
                "out_uses": int(row["out_uses"]),
            }
        return {
            "passivity": float(row["passivity"]),
            "in_deg": tot_in,
            "out_deg": tot_out,
            "in_conf": int(row["in_conf"]),
            "in_uses": int(row["in_uses"]),
            "out_conf": int(row["out_conf"]),
            "out_uses": int(row["out_uses"]),
        }
    finally:
        if own:
            drv.close()


def get_character_agency_trajectory(
    character_ids: list[str] | tuple[str, ...],
    *,
    scene_window: int = 10,
    driver: Driver | None = None,
) -> dict[str, Any]:
    """Per-sequence passivity rows for each character id (for trajectory charts)."""
    ids = list(character_ids)
    own = driver is None
    drv = driver or get_driver()
    try:
        nums = list_event_numbers(driver=drv)
        ranges = build_sequence_ranges(nums, scene_window)
        by_id: dict[str, list[dict[str, Any]]] = {cid: [] for cid in ids}
        for lo, hi, label in ranges:
            for cid in ids:
                m = get_passivity_in_scene_window(cid, lo, hi, driver=drv)
                by_id[cid].append(
                    {
                        "sequence": label,
                        "lo": lo,
                        "hi": hi,
                        "passivity": m["passivity"],
                        "in_deg": m["in_deg"],
                        "out_deg": m["out_deg"],
                    }
                )
        return {
            "scene_numbers": nums,
            "ranges": [{"lo": a, "hi": b, "label": l} for a, b, l in ranges],
            "by_id": by_id,
        }
    finally:
        if own:
            drv.close()


def _is_set_dressing_prop(name: str | None, record_props: dict[str, Any]) -> bool:
    if record_props.get("set_dressing") is True:
        return True
    if record_props.get("category") == "Set Dressing":
        return True
    n = (name or "").strip().lower()
    return n == "set dressing" or "set dressing" in n


def get_load_bearing_props(*, driver: Driver | None = None, min_edges: int = 2) -> list[dict[str, Any]]:
    """
    Props that are 'load-bearing': at least `min_edges` total USES or CONFLICTS_WITH
    touches (any direction) on the Prop node. Excludes set-dressing props by name (and by
    set_dressing/category when those properties exist on Prop nodes).
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (p:Prop)
                OPTIONAL MATCH (p)-[r1:USES|CONFLICTS_WITH]-()
                WITH p, count(r1) AS total_touches
                WHERE total_touches >= $min
                RETURN p.id AS id, p.name AS name, total_touches AS touch_count
                ORDER BY total_touches DESC, id
                """,
                min=min_edges,
            ).data()
    finally:
        if own:
            drv.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        # Prop nodes from neo4j_loader only store id/name; avoid RETURNing missing keys (DBMS warnings).
        if _is_set_dressing_prop(row.get("name"), {}):
            continue
        out.append(
            {
                "id": row["id"],
                "name": row.get("name"),
                "uses_and_conflicts_touches": int(row["touch_count"]),
            }
        )
    return out


def get_possessed_but_unused_props(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """
    Props that appear in at least one POSSESSES edge but have no USES relationships
    (incoming or outgoing on the Prop). Set-dressing names excluded.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (:Character)-[:POSSESSES]->(p:Prop)
                WHERE NOT (p)-[:USES]-()
                RETURN DISTINCT p.id AS id, p.name AS name
                ORDER BY toLower(coalesce(p.name, p.id)), p.id
                """
            ).data()
    finally:
        if own:
            drv.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_set_dressing_prop(row.get("name"), {}):
            continue
        out.append({"id": row["id"], "name": row.get("name")})
    return out


def get_props_possession_only_early_uses(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """
    Props with at least one POSSESSES, at least one co-located USES in the script, but **no**
    USES in scenes strictly after the midpoint (by :Event.number). Flags setups that never
    pay off in the back half. Set-dressing names excluded.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                OPTIONAL MATCH (ev:Event)
                WITH max(ev.number) AS mx
                WITH CASE WHEN mx IS NULL THEN 0.0 ELSE toFloat(mx) / 2.0 END AS midf
                MATCH (p:Prop)
                MATCH ()-[:POSSESSES]->(p)
                WITH DISTINCT p, midf
                MATCH (a)-[u:USES]->(p)
                MATCH (a)-[:IN_SCENE]->(e:Event)
                WITH p, midf, collect(DISTINCT e.number) AS use_scenes
                WHERE size(use_scenes) >= 1
                  AND none(x IN use_scenes WHERE toFloat(x) > midf)
                OPTIONAL MATCH ()-[pos:POSSESSES]->(p)
                WITH p, size(use_scenes) AS use_scene_count, count(DISTINCT pos) AS poss_ct
                WHERE poss_ct >= 1
                RETURN p.id AS id, p.name AS name,
                       poss_ct AS possession_edges,
                       use_scene_count AS early_use_scene_count
                ORDER BY poss_ct DESC, toLower(coalesce(p.name, p.id)), p.id
                """
            ).data()
    finally:
        if own:
            drv.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_set_dressing_prop(row.get("name"), {}):
            continue
        out.append(
            {
                "id": row["id"],
                "name": row.get("name"),
                "possession_edges": int(row["possession_edges"]),
                "early_use_scene_count": int(row["early_use_scene_count"]),
            }
        )
    return out


def get_props_act1_possess_no_act3_payoff(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """
    Props with POSSESSES co-present in Act I (first third of :Event.number) but **no**
    `USES` or `CONFLICTS_WITH` involving the prop in Act III (final third), both endpoints
    `IN_SCENE` to the same Act III event. Set-dressing names excluded.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (ev:Event)
                WITH coalesce(max(ev.number), 0) AS nmx
                WHERE nmx >= 1
                WITH nmx,
                     toInteger(nmx / 3) AS a1_hi,
                     toInteger(2 * nmx / 3) + 1 AS a3_lo
                MATCH (e1:Event)
                WHERE e1.number <= a1_hi
                MATCH (c:Character)-[:POSSESSES]->(p:Prop)
                MATCH (c)-[:IN_SCENE]->(e1)
                MATCH (p)-[:IN_SCENE]->(e1)
                WITH DISTINCT p, a3_lo, nmx
                WHERE a3_lo <= nmx
                  AND NOT EXISTS {
                    MATCH (e3:Event)
                    WHERE e3.number >= a3_lo AND e3.number <= nmx
                    MATCH (p)-[r:USES|CONFLICTS_WITH]-(x)
                    MATCH (p)-[:IN_SCENE]->(e3)
                    MATCH (x)-[:IN_SCENE]->(e3)
                  }
                RETURN p.id AS id, p.name AS name
                ORDER BY toLower(coalesce(p.name, p.id)), p.id
                """
            ).data()
    finally:
        if own:
            drv.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        if _is_set_dressing_prop(row.get("name"), {}):
            continue
        out.append({"id": row["id"], "name": row.get("name")})
    return out


def get_scene_inspector_data(scene_number: int, *, driver: Driver | None = None) -> dict[str, Any] | None:
    """
    Heading for the Event plus all relationship `source_quote` values where both endpoints
    belong to the same scene (IN_SCENE to that Event).
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            meta = session.run(
                "MATCH (e:Event {number: $n}) RETURN e.number AS number, e.heading AS heading",
                n=int(scene_number),
            ).single()
            if meta is None:
                return None
            quotes = session.run(
                """
                MATCH (e:Event {number: $n})
                MATCH (x)-[:IN_SCENE]->(e)
                WITH collect(DISTINCT elementId(x)) AS ids
                MATCH (a)-[r]-(b)
                WHERE elementId(a) IN ids AND elementId(b) IN ids
                  AND r.source_quote IS NOT NULL
                RETURN DISTINCT type(r) AS rel_type,
                       r.source_quote AS source_quote,
                       coalesce(a.id, '') AS source_id,
                       coalesce(b.id, '') AS target_id
                ORDER BY rel_type, source_quote
                """,
                n=int(scene_number),
            ).data()
            return {
                "scene_number": meta["number"],
                "heading": meta.get("heading") or "",
                "quotes": quotes,
            }
    finally:
        if own:
            drv.close()


def get_scene_heat(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """
    Scene Heat Index per Event: **unique conflict pairs** in the scene divided by IN_SCENE count.

    Conflicts are counted as **unordered pairs** of entities both IN_SCENE to the Event such that
    at least one ``CONFLICTS_WITH`` exists between them (either direction). Multiple parallel
    edges between the same two nodes (e.g. dialogue bloat) contribute **one** to the numerator.

    Low values highlight 'dead air' — many in-scene links but few distinct opposing pairs.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            events = session.run(
                "MATCH (e:Event) RETURN e.number AS number, e.heading AS heading ORDER BY e.number"
            ).data()
            results: list[dict[str, Any]] = []
            for ev in events:
                num = ev["number"]
                heading = ev.get("heading") or ""
                in_row = session.run(
                    """
                    MATCH ()-[i:IN_SCENE]->(e:Event {number: $n})
                    RETURN count(i) AS c
                    """,
                    n=num,
                ).single()
                in_ct = int(in_row["c"]) if in_row else 0

                cf_row = session.run(
                    """
                    MATCH (a)-[:IN_SCENE]->(e:Event {number: $n})
                    MATCH (b)-[:IN_SCENE]->(e)
                    WHERE elementId(a) < elementId(b)
                      AND ( (a)-[:CONFLICTS_WITH]->(b) OR (b)-[:CONFLICTS_WITH]->(a) )
                    RETURN count(*) AS c
                    """,
                    n=num,
                ).single()
                cf_ct = int(cf_row["c"]) if cf_row else 0

                if in_ct == 0:
                    heat: float | None = None
                else:
                    heat = cf_ct / in_ct

                results.append(
                    {
                        "scene_number": num,
                        "heading": heading,
                        "in_scene_count": in_ct,
                        "conflicts_within_scene": cf_ct,
                        "heat": heat,
                    }
                )
            return results
    finally:
        if own:
            drv.close()


def get_narrative_momentum_by_scene(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    """
    Per Event (scene): heat = CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)
    counting only edges whose endpoints are both IN_SCENE to that Event.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (e:Event)
                OPTIONAL MATCH (a)-[c:CONFLICTS_WITH]->(b)
                WHERE (a)-[:IN_SCENE]->(e) AND (b)-[:IN_SCENE]->(e)
                WITH e, count(c) AS conf_ct
                OPTIONAL MATCH (a2)-[i:INTERACTS_WITH]->(b2)
                WHERE (a2)-[:IN_SCENE]->(e) AND (b2)-[:IN_SCENE]->(e)
                WITH e, conf_ct, count(i) AS inter_ct
                RETURN e.number AS scene_number,
                       coalesce(e.heading, '') AS heading,
                       conf_ct AS conflicts,
                       inter_ct AS interacts,
                       CASE WHEN conf_ct + inter_ct = 0 THEN NULL
                            ELSE toFloat(conf_ct) / toFloat(conf_ct + inter_ct)
                       END AS heat
                ORDER BY e.number
                """
            ).data()
        out: list[dict[str, Any]] = []
        for r in rows:
            sn = r.get("scene_number")
            if sn is None:
                continue
            out.append(
                {
                    "scene_number": int(sn),
                    "heading": r.get("heading") or "",
                    "conflicts": int(r.get("conflicts") or 0),
                    "interacts": int(r.get("interacts") or 0),
                    "heat": float(r["heat"]) if r.get("heat") is not None else None,
                }
            )
        return out
    finally:
        if own:
            drv.close()


def get_payoff_prop_timelines(
    *,
    min_scene_gap: int = 10,
    driver: Driver | None = None,
) -> list[dict[str, Any]]:
    """
    Long-arc props: first_intro = earliest scene with IN_SCENE or co-scene POSSESSES;
    last_use = latest scene with USES or CONFLICTS_WITH involving the prop in-scene.
    Returns rows where (last_use - first_intro) > min_scene_gap.
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            in_rows = session.run(
                """
                MATCH (p:Prop)-[:IN_SCENE]->(e:Event)
                RETURN p.id AS id, coalesce(p.name, '') AS name, min(e.number) AS mn
                """
            ).data()
            poss_rows = session.run(
                """
                MATCH (c:Character)-[:POSSESSES]->(p:Prop)
                MATCH (c)-[:IN_SCENE]->(e:Event), (p)-[:IN_SCENE]->(e)
                RETURN p.id AS id, min(e.number) AS mn
                """
            ).data()
            last_rows = session.run(
                """
                MATCH (p:Prop)-[:IN_SCENE]->(e:Event)
                WHERE exists {
                  MATCH (x)-[:USES]->(p) WHERE (x)-[:IN_SCENE]->(e)
                }
                OR exists {
                  MATCH (p)-[:USES]->(y) WHERE (y)-[:IN_SCENE]->(e)
                }
                OR exists {
                  MATCH (x)-[:CONFLICTS_WITH]->(p) WHERE (x)-[:IN_SCENE]->(e)
                }
                OR exists {
                  MATCH (p)-[:CONFLICTS_WITH]->(y) WHERE (y)-[:IN_SCENE]->(e)
                }
                RETURN p.id AS id, max(e.number) AS last_use
                """
            ).data()

        by_id: dict[str, dict[str, Any]] = {}
        for r in in_rows:
            pid = str(r["id"])
            mn = r.get("mn")
            if mn is None:
                continue
            n = int(mn)
            if pid not in by_id:
                by_id[pid] = {"id": pid, "name": r.get("name") or pid, "first": n}
            else:
                by_id[pid]["first"] = min(by_id[pid]["first"], n)
        for r in poss_rows:
            pid = str(r["id"])
            mn = r.get("mn")
            if mn is None:
                continue
            n = int(mn)
            if pid not in by_id:
                by_id[pid] = {"id": pid, "name": pid, "first": n}
            else:
                by_id[pid]["first"] = min(by_id[pid]["first"], n)
        last_map = {str(r["id"]): int(r["last_use"]) for r in last_rows if r.get("last_use") is not None}

        result: list[dict[str, Any]] = []
        for pid, meta in by_id.items():
            first = meta["first"]
            last = last_map.get(pid)
            if last is None:
                continue
            gap = last - first
            if gap > min_scene_gap:
                result.append(
                    {
                        "id": pid,
                        "name": meta.get("name") or pid,
                        "first_scene": first,
                        "last_scene": last,
                        "gap": gap,
                    }
                )
        result.sort(key=lambda x: (-x["gap"], x["id"]))
        return result
    finally:
        if own:
            drv.close()


def get_top_characters_by_interaction_count(
    k: int = 5,
    *,
    driver: Driver | None = None,
) -> list[dict[str, Any]]:
    """Rank characters by total CONFLICTS_WITH + USES + INTERACTS_WITH edge count (both directions)."""
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (c:Character)
                OPTIONAL MATCH (c)-[oc:CONFLICTS_WITH]->()
                OPTIONAL MATCH ()-[ic:CONFLICTS_WITH]->(c)
                OPTIONAL MATCH (c)-[ou:USES]->()
                OPTIONAL MATCH ()-[iu:USES]->(c)
                OPTIONAL MATCH (c)-[oi:INTERACTS_WITH]->()
                OPTIONAL MATCH ()-[ii:INTERACTS_WITH]->(c)
                WITH c,
                     count(oc) + count(ic) + count(ou) + count(iu) + count(oi) + count(ii) AS tot
                WHERE tot > 0
                RETURN c.id AS id, coalesce(c.name, '') AS name, tot
                ORDER BY tot DESC
                LIMIT $k
                """,
                k=int(k),
            ).data()
        return [
            {"id": str(r["id"]), "name": r.get("name") or r["id"], "interactions": int(r["tot"])}
            for r in rows
        ]
    finally:
        if own:
            drv.close()


def _print_scene_heat_summary(rows: list[dict[str, Any]], *, top_dead: int = 8) -> None:
    with_heat = [r for r in rows if r["heat"] is not None]
    deadest = sorted(with_heat, key=lambda r: r["heat"])[:top_dead]
    print("\n--- Lowest heat (possible 'dead air') ---", flush=True)
    for r in deadest:
        h = r["heat"]
        print(
            f"  Scene {r['scene_number']}: heat={h:.4f}  "
            f"conflicts={r['conflicts_within_scene']} / in_scene={r['in_scene_count']}  "
            f"{r['heading'][:64]!r}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Narrative MRI metrics from Neo4j.")
    parser.add_argument(
        "--character",
        type=str,
        default=None,
        help="Print passivity score for this character id (e.g. alan).",
    )
    parser.add_argument(
        "--props",
        action="store_true",
        help="Print load-bearing props.",
    )
    parser.add_argument(
        "--heat",
        action="store_true",
        help="Print scene heat table and dead-air highlights.",
    )
    args = parser.parse_args()

    if not args.character and not args.props and not args.heat:
        args.heat = True
        args.props = True

    driver = get_driver()
    try:
        if args.character:
            p = get_passivity_score(args.character, driver=driver)
            if p is None:
                print(
                    f"Passivity for {args.character!r}: N/A (missing character or no CONFLICTS_WITH/USES edges).",
                    flush=True,
                )
            else:
                print(f"Passivity for {args.character!r}: {p:.4f}", flush=True)

        if args.props:
            props = get_load_bearing_props(driver=driver)
            print(f"\nLoad-bearing props (>={2} USES/CONFLICTS touches, excl. set dressing): {len(props)}", flush=True)
            for row in props[:25]:
                print(f"  {row['id']}: {row['name']!r}  (touches={row['uses_and_conflicts_touches']})", flush=True)
            if len(props) > 25:
                print(f"  ... {len(props) - 25} more", flush=True)

        if args.heat:
            heat = get_scene_heat(driver=driver)
            print("\n--- Scene heat (CONFLICTS_WITH in scene / IN_SCENE count) ---", flush=True)
            for r in heat:
                h = r["heat"]
                hs = f"{h:.4f}" if h is not None else "n/a"
                print(
                    f"  Scene {r['scene_number']}: {hs}  "
                    f"({r['conflicts_within_scene']}/{r['in_scene_count']})",
                    flush=True,
            )
            _print_scene_heat_summary(heat)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
