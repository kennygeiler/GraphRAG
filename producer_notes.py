from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from neo4j import Driver

from metrics import get_driver

META_KIND = "producer_director_notes"


def fetch_producer_director_notes(*, driver: Driver | None = None) -> dict[str, str]:
    """
    Load Director’s notes. Primary store: :MRIMeta; mirror: lowest-number :Event
    (`mri_producer_note_*` — producer overlay, separate from extraction `source_quote` edges).
    """
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as session:
            row = session.run(
                """
                OPTIONAL MATCH (m:MRIMeta {kind: $k})
                OPTIONAL MATCH (ev:Event)
                WITH m, min(ev.number) AS mn
                OPTIONAL MATCH (a:Event {number: mn})
                RETURN coalesce(m.note_heartbeat, a.mri_producer_note_heartbeat, '') AS hb,
                       coalesce(m.note_passivity, a.mri_producer_note_passivity, '') AS np,
                       coalesce(m.note_chekhov, a.mri_producer_note_chekhov, '') AS nc
                """,
                k=META_KIND,
            ).single()
        if row is None:
            return {"heartbeat": "", "passivity": "", "chekhov": ""}
        return {
            "heartbeat": str(row["hb"] or ""),
            "passivity": str(row["np"] or ""),
            "chekhov": str(row["nc"] or ""),
        }
    finally:
        if own:
            drv.close()


def upsert_producer_director_notes(
    heartbeat: str,
    passivity: str,
    chekhov: str,
    *,
    driver: Driver | None = None,
) -> None:
    """Persist notes on :MRIMeta (single overlay node per dashboard)."""
    own = driver is None
    drv = driver or get_driver()
    ts = datetime.now(UTC).isoformat()

    def _tx(tx: Any) -> None:
        tx.run(
            """
            MERGE (m:MRIMeta {kind: $k})
            SET m.note_heartbeat = $hb,
                m.note_passivity = $np,
                m.note_chekhov = $nc,
                m.updated_at = $ts
            """,
            k=META_KIND,
            hb=heartbeat,
            np=passivity,
            nc=chekhov,
            ts=ts,
        )
        mn_row = tx.run("MATCH (e:Event) RETURN min(e.number) AS mn LIMIT 1").single()
        if mn_row is not None and mn_row.get("mn") is not None:
            tx.run(
                """
                MATCH (anchor:Event {number: $mn})
                SET anchor.mri_producer_note_heartbeat = $hb,
                    anchor.mri_producer_note_passivity = $np,
                    anchor.mri_producer_note_chekhov = $nc
                """,
                mn=int(mn_row["mn"]),
                hb=heartbeat,
                np=passivity,
                nc=chekhov,
            )

    try:
        with drv.session() as session:
            session.execute_write(_tx)
    finally:
        if own:
            drv.close()
