from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Literal

from fuzzywuzzy import fuzz
from neo4j import GraphDatabase

EntityMergeLabel = Literal["Character", "Location"]
_MERGE_LABELS: frozenset[str] = frozenset({"Character", "Location"})

# Word digits ↔ spellings for names like "Granny 1" vs "Granny One"
_WORD_TO_DIGIT = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}
_REL_TYPE_OK = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"❌ Missing {name} in environment (.env).", flush=True)
        sys.exit(1)
    return v


def get_driver() -> Any:
    return GraphDatabase.driver(
        _require_env("NEO4J_URI"),
        auth=(_require_env("NEO4J_USER"), _require_env("NEO4J_PASSWORD")),
    )


def normalize_entity_name(name: str | None) -> str:
    """Normalize for fuzzy compare: lowercase, punctuation stripped, 1↔one unified."""
    if not name:
        return ""
    t = name.lower().strip()
    t = re.sub(r"[\s_\-]+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)
    parts: list[str] = []
    for tok in t.split():
        if tok in _WORD_TO_DIGIT:
            parts.append(_WORD_TO_DIGIT[tok])
        elif tok.isdigit():
            parts.append(tok)
        else:
            parts.append(tok)
    return " ".join(parts)


def normalize_character_name(name: str | None) -> str:
    """Backward-compatible alias for :Character and :Location name normalization."""
    return normalize_entity_name(name)


def fuzzy_name_similarity(
    name_a: str | None,
    name_b: str | None,
    id_a: str,
    id_b: str,
) -> float:
    """
    fuzzywuzzy token_sort_ratio on normalized strings (0–1 scale).
    Falls back to id slug if display name is empty.
    """
    na = normalize_entity_name(name_a) or normalize_entity_name(id_a.replace("_", " "))
    nb = normalize_entity_name(name_b) or normalize_entity_name(id_b.replace("_", " "))
    if not na or not nb:
        return 0.0
    return fuzz.token_sort_ratio(na, nb) / 100.0


def fetch_characters(session: Any) -> list[dict[str, Any]]:
    return session.run(
        "MATCH (c:Character) RETURN c.id AS id, c.name AS name ORDER BY c.id"
    ).data()


def fetch_locations(session: Any) -> list[dict[str, Any]]:
    return session.run(
        "MATCH (l:Location) RETURN l.id AS id, l.name AS name ORDER BY l.id"
    ).data()


def find_fuzzy_duplicate_pairs(
    entities: list[dict[str, Any]],
    *,
    min_ratio: float = 0.78,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Distinct entity pairs whose names look alike (fuzzywuzzy token_sort_ratio)."""
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    n = len(entities)
    for i in range(n):
        ci = entities[i]
        ia = str(ci.get("id") or "")
        for j in range(i + 1, n):
            cj = entities[j]
            ib = str(cj.get("id") or "")
            if ia == ib:
                continue
            r = fuzzy_name_similarity(ci.get("name"), cj.get("name"), ia, ib)
            if r >= min_ratio:
                pairs.append((ci, cj, r))
    pairs.sort(key=lambda t: (-t[2], t[0]["id"], t[1]["id"]))
    return pairs


def find_fuzzy_character_pairs(
    characters: list[dict[str, Any]],
    *,
    min_ratio: float = 0.78,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Pairs of distinct characters with similar names (fuzzywuzzy)."""
    return find_fuzzy_duplicate_pairs(characters, min_ratio=min_ratio)


def find_ghost_characters(session: Any) -> list[dict[str, Any]]:
    """
    Characters in exactly one scene (one IN_SCENE target) with no CONFLICTS_WITH
    (either direction) — likely clutter / under-connected extras.
    """
    return session.run(
        """
        MATCH (c:Character)
        OPTIONAL MATCH (c)-[:IN_SCENE]->(e:Event)
        WITH c, count(DISTINCT e) AS n_scenes
        WHERE n_scenes = 1
        OPTIONAL MATCH (c)-[x:CONFLICTS_WITH]-()
        WITH c, n_scenes, count(x) AS n_conf
        WHERE n_conf = 0
        RETURN c.id AS id, c.name AS name, n_scenes AS scenes, n_conf AS conflicts
        ORDER BY toLower(c.name), c.id
        """
    ).data()


@dataclass
class ReconciliationScan:
    ghost_characters: list[dict[str, Any]]
    fuzzy_character_pairs: list[tuple[dict[str, Any], dict[str, Any], float]]
    fuzzy_location_pairs: list[tuple[dict[str, Any], dict[str, Any], float]]


def run_reconciliation_scan(driver: Any, *, min_similarity: float = 0.78) -> ReconciliationScan:
    """Read-only scan: ghosts, fuzzy Character pairs, fuzzy Location pairs. No printing."""
    with driver.session() as session:
        chars = fetch_characters(session)
        locs = fetch_locations(session)
        ghosts = find_ghost_characters(session)
        char_pairs = find_fuzzy_character_pairs(chars, min_ratio=min_similarity)
        loc_pairs = find_fuzzy_duplicate_pairs(locs, min_ratio=min_similarity)
    return ReconciliationScan(
        ghost_characters=ghosts,
        fuzzy_character_pairs=char_pairs,
        fuzzy_location_pairs=loc_pairs,
    )


def _safe_rel_type(typ: str) -> bool:
    return bool(_REL_TYPE_OK.fullmatch(typ))


def _merge_with_apoc_entity(tx: Any, keep_id: str, drop_id: str, label: str) -> bool:
    if label not in _MERGE_LABELS:
        raise ValueError(f"Unsupported merge label: {label!r}")
    try:
        tx.run(
            f"""
            MATCH (a:{label} {{id: $keep}}), (b:{label} {{id: $drop}})
            WITH a, b
            CALL apoc.refactor.mergeNodes([a, b])
            YIELD node
            RETURN count(node) AS n
            """,
            keep=keep_id,
            drop=drop_id,
        )
        return True
    except Exception as exc:
        print(f"   (APOC merge not used: {type(exc).__name__})", flush=True)
        return False


def _merge_manual_entity(tx: Any, keep_id: str, drop_id: str, label: str) -> None:
    """Rewire all relationships from `drop` onto `keep`, then delete `drop`."""
    if label not in _MERGE_LABELS:
        raise ValueError(f"Unsupported merge label: {label!r}")
    rows = list(
        tx.run(
            f"""
            MATCH (keep:{label} {{id: $keep}}), (drop:{label} {{id: $drop}})
            MATCH (drop)-[r]-(other)
            RETURN elementId(r) AS rid,
                   type(r) AS typ,
                   properties(r) AS props,
                   startNode(r) = drop AS drop_is_start,
                   elementId(other) AS e_other
            """,
            keep=keep_id,
            drop=drop_id,
        )
    )
    for row in rows:
        typ = row["typ"]
        if not _safe_rel_type(typ):
            raise ValueError(f"Unsafe relationship type from database: {typ!r}")
        rid = row["rid"]
        props = dict(row["props"] or {})
        e_other = row["e_other"]
        drop_is_start = row["drop_is_start"]
        tx.run(
            "MATCH ()-[r]-() WHERE elementId(r) = $rid DELETE r",
            rid=rid,
        )
        merge_kw = "MERGE" if typ in ("IN_SCENE", "POSSESSES") else "CREATE"
        if drop_is_start:
            q = (
                f"MATCH (k:{label} {{id: $keep}}), (o) WHERE elementId(o) = $e_other "
                f"{merge_kw} (k)-[nr:`{typ}`]->(o) SET nr += $props"
            )
        else:
            q = (
                f"MATCH (k:{label} {{id: $keep}}), (o) WHERE elementId(o) = $e_other "
                f"{merge_kw} (o)-[nr:`{typ}`]->(k) SET nr += $props"
            )
        tx.run(q, keep=keep_id, e_other=e_other, props=props)

    tx.run(
        f"""
        MATCH (keep:{label} {{id: $keep}}), (drop:{label} {{id: $drop}})
        SET keep.name = CASE
          WHEN trim(coalesce(keep.name, '')) = '' THEN drop.name
          ELSE keep.name
        END
        """,
        keep=keep_id,
        drop=drop_id,
    )
    tx.run(
        f"MATCH (d:{label} {{id: $drop}}) DETACH DELETE d",
        drop=drop_id,
    )


def merge_entities(
    driver: Any,
    keep_id: str,
    drop_id: str,
    entity_label: EntityMergeLabel,
) -> None:
    """
    Merge `drop` into `keep` (keep survives). Tries APOC mergeNodes first, else manual
    MATCH/DELETE + rewire (same pattern as the CLI reconcile tool).
    """
    if keep_id == drop_id:
        raise ValueError("keep_id and drop_id must differ")
    if entity_label not in _MERGE_LABELS:
        raise ValueError(f"entity_label must be Character or Location, got {entity_label!r}")

    def work(tx: Any) -> None:
        chk = tx.run(
            f"MATCH (n:{entity_label}) WHERE n.id IN [$a, $b] RETURN count(n) AS n",
            a=keep_id,
            b=drop_id,
        ).single()
        if not chk or int(chk["n"]) != 2:
            raise ValueError(
                f"Expected two {entity_label} nodes; found {chk['n'] if chk else 0}."
            )

        if _merge_with_apoc_entity(tx, keep_id, drop_id, entity_label):
            return
        _merge_manual_entity(tx, keep_id, drop_id, entity_label)

    with driver.session() as session:
        session.execute_write(work)


def merge_characters(driver: Any, keep_id: str, drop_id: str) -> None:
    """Merge `drop` Character into `keep`. Tries APOC first, then manual rewire."""
    merge_entities(driver, keep_id, drop_id, "Character")


def _prompt_yes_no(msg: str) -> bool:
    while True:
        r = input(f"{msg} (y/n): ").strip().lower()
        if r in ("y", "yes"):
            return True
        if r in ("n", "no"):
            return False
        print("Please enter y or n.", flush=True)


def _choose_canonical(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, str]:
    """Return (keep_id, drop_id) — prefer lexicographically smaller id as canonical."""
    ia, ib = str(a["id"]), str(b["id"])
    if ia <= ib:
        return ia, ib
    return ib, ia


def _merge_pair_loop_cli(
    driver: Any,
    pairs: list[tuple[dict[str, Any], dict[str, Any], float]],
    *,
    label: str,
    merge_fn: Any,
) -> None:
    print(f"\n=== Merge tool ({label}) ===", flush=True)
    for a, b, score in pairs:
        keep_id, drop_id = _choose_canonical(a, b)
        name_a = f"{a.get('name')} ({a['id']})"
        name_b = f"{b.get('name')} ({b['id']})"
        print(
            f"\nFound [{name_a}] and [{name_b}]  (similarity {score:.3f}).\n"
            f"Will keep id={keep_id!r}, remove id={drop_id!r}.",
            flush=True,
        )
        if not _prompt_yes_no("Merge?"):
            print("Skipped.", flush=True)
            continue
        try:
            merge_fn(driver, keep_id, drop_id)
            print("Merged.", flush=True)
        except Exception as exc:
            print(f"❌ Merge failed: {exc}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile Character / Location nodes in Neo4j (ghosts, fuzzy dupes, merge)."
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.78,
        help="Minimum normalized-name similarity (0–1) to flag a fuzzy pair.",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "characters", "locations"),
        default="all",
        help="What to list (and which interactive merges to offer): all, characters-only, or locations-only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not prompt for merges; only print scan results.",
    )
    args = parser.parse_args()

    driver = get_driver()
    try:
        scan = run_reconciliation_scan(driver, min_similarity=args.min_similarity)

        if args.scope in ("all", "characters"):
            print("\n=== Ghost nodes (1 scene, 0 conflicts) ===", flush=True)
            if not scan.ghost_characters:
                print("None found.", flush=True)
            else:
                print(f"Found {len(scan.ghost_characters)}:", flush=True)
                for g in scan.ghost_characters:
                    print(f"  - {g.get('name')!r} ({g['id']})", flush=True)

            print("\n=== Fuzzy Character name matches ===", flush=True)
            if not scan.fuzzy_character_pairs:
                print("None above threshold.", flush=True)
            else:
                for a, b, score in scan.fuzzy_character_pairs:
                    print(
                        f"  ~{score:.3f}  {a.get('name')!r} ({a['id']})  <->  "
                        f"{b.get('name')!r} ({b['id']})",
                        flush=True,
                    )

        if args.scope in ("all", "locations"):
            print("\n=== Fuzzy Location name matches ===", flush=True)
            if not scan.fuzzy_location_pairs:
                print("None above threshold.", flush=True)
            else:
                for a, b, score in scan.fuzzy_location_pairs:
                    print(
                        f"  ~{score:.3f}  {a.get('name')!r} ({a['id']})  <->  "
                        f"{b.get('name')!r} ({b['id']})",
                        flush=True,
                    )

        if args.dry_run:
            return

        if args.scope in ("all", "characters") and scan.fuzzy_character_pairs:
            _merge_pair_loop_cli(
                driver,
                scan.fuzzy_character_pairs,
                label="Character pairs",
                merge_fn=merge_characters,
            )

        if args.scope in ("all", "locations") and scan.fuzzy_location_pairs:

            def _merge_loc(d: Any, keep: str, drop: str) -> None:
                merge_entities(d, keep, drop, "Location")

            _merge_pair_loop_cli(
                driver,
                scan.fuzzy_location_pairs,
                label="Location pairs",
                merge_fn=_merge_loc,
            )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
