# Phase 3: Reconciliation for operators — Context

**Gathered:** 2026-04-03  
**Status:** Ready for planning (plan-phase; discuss-phase optional)

<domain>

## Phase boundary

Deliver **REC-01**: operators can run **reconciliation** (fuzzy duplicate detection + optional entity merge + ghost-character surfacing) from **documented CLI** and/or **Streamlit**, with **dry-run**, **explicit confirmation** before writes, and behavior **aligned** with `reconcile.py` (`merge_entities` / APOC-or-manual rewire) and `neo4j_loader` merge semantics.

**In scope:** `reconcile.py` (refactor for reusable scan API), `README.md` operator docs, new **Reconcile** Streamlit tab in `app.py`.

**Out of scope:** Prop merges, Event merges, automatic merges without confirmation, MET-01 complexity signals (Phase 4), changing loader JSON schema.

**Depends on:** Phase 2 (REL-01 empty-state patterns; reuse `get_driver` / cache / try-except patterns from `app.py`).

</domain>

<decisions>

## Implementation decisions

- **D-01:** **Scan API:** Add a pure **read-phase** function (e.g. `run_reconciliation_scan(driver, ...)`) returning structured **ghost characters**, **fuzzy Character pairs**, **fuzzy Location pairs** (scores included). CLI `main()` and Streamlit both call this—no duplicated Cypher.
- **D-02:** **CLI scope:** Add `--scope {all,characters,locations}` controlling what is **listed**; **`--dry-run`** remains list-only. **Interactive y/n merges** run for **Character** pairs when scope is `all` or `characters`; for **Location** pairs when scope is `all` or `locations` (same prompt/merge pattern as characters, using `merge_entities(..., "Location")`).
- **D-03:** **Streamlit:** New tab **Reconcile** (after **Cleanup Review**, before **Pipeline Efficiency Tracking**). **Operator doc** in an expander: what fuzzy matching is, what ghosts are, that merge **keeps one id** and **rewires** rels (APOC preferred, manual rewire fallback—plain language). **Default** view is scan results only; **writes** require a global **acknowledgment checkbox** plus **per-merge confirmation** (e.g. select pair + choose keep id + confirm button).
- **D-04:** **Driver:** Reconcile tab uses **`metrics.get_driver()`** (same as rest of app), `finally: drv.close()` per action batch; call `reconcile.merge_entities` / `merge_characters` with that driver.
- **D-05:** **Cache:** Use `@st.cache_data` for scan results keyed by artifact stamp (reuse `_neo4j_dashboard_cache_stamp()` from `app.py` or a one-liner duplicate—prefer **importing the existing helper** if no circular import; if `app` imports `reconcile` and `reconcile` does not import `app`, **`from app import _neo4j_dashboard_cache_stamp`** is wrong; **duplicate stamp helper** in reconcile or pass stamp from app only in tab—simplest: **define `_reconcile_cache_stamp()`** in `app.py` next to dashboard stamp or reuse `_neo4j_dashboard_cache_stamp` inside cached function in app only).

</decisions>

<canonical_refs>

- `reconcile.py` — `merge_entities`, `find_ghost_characters`, `find_fuzzy_character_pairs`, `fetch_locations`
- `neo4j_loader.py` — merge-on-load patterns (documentation alignment only)
- `.planning/ROADMAP.md` — Phase 3 success criteria

</canonical_refs>
