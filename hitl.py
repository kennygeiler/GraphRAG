from __future__ import annotations

from typing import Any

import pandas as pd
from neo4j import Driver

from metrics import get_driver

NARRATIVE_REL_TYPES = frozenset(
    {"INTERACTS_WITH", "LOCATED_IN", "USES", "CONFLICTS_WITH", "POSSESSES"}
)


def list_events_with_status(*, driver: Driver | None = None) -> list[dict[str, Any]]:
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            return session.run(
                """
                MATCH (e:Event)
                RETURN e.number AS number,
                       e.heading AS heading,
                       coalesce(e.status, 'DRAFT') AS status
                ORDER BY e.number
                """
            ).data()
    finally:
        if own:
            drv.close()


def _primary_entity_label(labels: list[str] | None) -> str:
    if not labels:
        return "Character"
    for lab in ("Character", "Location", "Prop"):
        if lab in labels:
            return lab
    return str(labels[0])


def get_scene_hitl_nodes(scene_number: int, *, driver: Driver | None = None) -> list[dict[str, Any]]:
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (e:Event {number: $n})
                MATCH (x)-[:IN_SCENE]->(e)
                WHERE x:Character OR x:Location OR x:Prop
                WITH DISTINCT x, labels(x) AS labs
                RETURN labs AS labels, x.id AS id, coalesce(x.name, '') AS name
                ORDER BY toLower(coalesce(x.name, x.id)), x.id
                """,
                n=int(scene_number),
            ).data()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "kind": _primary_entity_label(r.get("labels")),
                    "id": r["id"],
                    "name": r.get("name") or "",
                }
            )
        return out
    finally:
        if own:
            drv.close()


def get_scene_hitl_relationships(scene_number: int, *, driver: Driver | None = None) -> list[dict[str, Any]]:
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            rows = session.run(
                """
                MATCH (e:Event {number: $n})
                MATCH (a)-[r]->(b)
                WHERE NOT type(r) = 'IN_SCENE'
                  AND (a)-[:IN_SCENE]->(e) AND (b)-[:IN_SCENE]->(e)
                RETURN elementId(r) AS rel_id,
                       a.id AS source_id,
                       type(r) AS rel_type,
                       b.id AS target_id,
                       coalesce(r.source_quote, '') AS source_quote
                ORDER BY rel_type, source_id, target_id, source_quote
                """,
                n=int(scene_number),
            ).data()
        return [
            {
                "rel_id": str(r["rel_id"]),
                "source_id": r["source_id"],
                "rel_type": r["rel_type"],
                "target_id": r["target_id"],
                "source_quote": r.get("source_quote") or "",
            }
            for r in rows
        ]
    finally:
        if own:
            drv.close()


def _rel_ids_from_df(df: pd.DataFrame) -> set[str]:
    if df.empty or "rel_id" not in df.columns:
        return set()
    out: set[str] = set()
    for v in df["rel_id"]:
        if pd.isna(v):
            continue
        s = str(v).strip()
        if s:
            out.add(s)
    return out


def _quote_map_from_df(df: pd.DataFrame) -> dict[str, str]:
    m: dict[str, str] = {}
    if df.empty:
        return m
    for _, row in df.iterrows():
        rid = row.get("rel_id")
        if pd.isna(rid) or not str(rid).strip():
            continue
        m[str(rid).strip()] = str(row.get("source_quote") or "")
    return m


def apply_hitl_scene_edits(
    scene_number: int,
    baseline_nodes: pd.DataFrame,
    edited_nodes: pd.DataFrame,
    baseline_rels: pd.DataFrame,
    edited_rels: pd.DataFrame,
    *,
    verify_event: bool = False,
    driver: Driver | None = None,
) -> list[str]:
    """
    Persist node renames, relationship deletes / quote patches / creates for one scene.
    If verify_event, set Event.status = 'VERIFIED'.
    Returns human-readable log lines (errors start with 'Error:').
    """
    logs: list[str] = []
    n = int(scene_number)
    orig_ids = _rel_ids_from_df(baseline_rels)
    cur_ids = _rel_ids_from_df(edited_rels)
    to_delete = orig_ids - cur_ids
    orig_quotes = _quote_map_from_df(baseline_rels)

    new_rows: list[tuple[str, str, str, str]] = []
    for _, row in edited_rels.iterrows():
        rid = row.get("rel_id")
        if pd.notna(rid) and str(rid).strip():
            continue
        sid = str(row.get("source_id") or "").strip()
        tid = str(row.get("target_id") or "").strip()
        rt = str(row.get("rel_type") or "").strip()
        quote = str(row.get("source_quote") or "").strip()
        if not sid and not tid and not rt and not quote:
            continue
        if not sid or not tid:
            logs.append("Error: New relationship missing source_id or target_id.")
            continue
        if rt not in NARRATIVE_REL_TYPES:
            logs.append(f"Error: Invalid rel_type {rt!r} (must be narrative type).")
            continue
        if not quote:
            logs.append("Error: New relationship needs a non-empty source_quote (proof).")
            continue
        new_rows.append((sid, tid, rt, quote))

    updates: list[tuple[str, str]] = []
    for _, row in edited_rels.iterrows():
        rid = row.get("rel_id")
        if pd.isna(rid) or not str(rid).strip():
            continue
        rs = str(rid).strip()
        if rs not in orig_ids:
            continue
        new_q = str(row.get("source_quote") or "")
        if orig_quotes.get(rs, "") != new_q:
            updates.append((rs, new_q))

    name_updates: list[tuple[str, str]] = []
    if not baseline_nodes.empty and not edited_nodes.empty:
        bmap = baseline_nodes.set_index("id")["name"].astype(str).to_dict()
        for _, row in edited_nodes.iterrows():
            oid = row.get("id")
            if pd.isna(oid):
                continue
            oid_s = str(oid)
            new_name = str(row.get("name") or "")
            if bmap.get(oid_s, "") != new_name:
                name_updates.append((oid_s, new_name))

    if any(msg.startswith("Error:") for msg in logs):
        return logs

    own = driver is None
    drv = driver or get_driver()

    def _tx(tx: Any) -> None:
        for rid in to_delete:
            tx.run(
                """
                MATCH ()-[r]->()
                WHERE elementId(r) = $rid
                DELETE r
                """,
                rid=rid,
            )
        for rid, quote in updates:
            tx.run(
                """
                MATCH ()-[r]->()
                WHERE elementId(r) = $rid
                SET r.source_quote = $quote
                """,
                rid=rid,
                quote=quote,
            )
        for sid, tid, rt, quote in new_rows:
            tx.run(
                f"""
                MATCH (e:Event {{number: $n}})
                MATCH (a {{id: $sid}}), (b {{id: $tid}})
                WHERE (a:Character OR a:Location OR a:Prop)
                  AND (b:Character OR b:Location OR b:Prop)
                  AND (a)-[:IN_SCENE]->(e) AND (b)-[:IN_SCENE]->(e)
                CREATE (a)-[r:`{rt}` {{source_quote: $quote}}]->(b)
                """,
                n=n,
                sid=sid,
                tid=tid,
                quote=quote,
            )
        for eid, new_name in name_updates:
            tx.run(
                """
                MATCH (n {id: $id})
                WHERE n:Character OR n:Location OR n:Prop
                SET n.name = $name
                """,
                id=eid,
                name=new_name,
            )
        if verify_event:
            tx.run(
                """
                MATCH (e:Event {number: $n})
                SET e.status = 'VERIFIED'
                """,
                n=n,
            )

    out: list[str] = []
    try:
        with drv.session() as session:
            session.execute_write(_tx)
    except Exception as exc:
        return [f"Error: Neo4j write failed: {exc}"]
    finally:
        if own:
            drv.close()

    if to_delete:
        out.append(f"Deleted {len(to_delete)} relationship(s).")
    if updates:
        out.append(f"Updated {len(updates)} relationship quote(s).")
    if new_rows:
        out.append(f"Created {len(new_rows)} relationship(s).")
    if name_updates:
        out.append(f"Updated {len(name_updates)} entity name(s).")
    if verify_event:
        out.append("Event marked status=VERIFIED (Gold).")
    if not out:
        out.append("No changes to apply.")
    return out
