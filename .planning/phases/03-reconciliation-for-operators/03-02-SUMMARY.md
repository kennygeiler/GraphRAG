---
phase: 03-reconciliation-for-operators
plan: 02
subsystem: ui
tags: [streamlit, reconcile, neo4j]

requires:
  - plan: 03-01
provides:
  - Reconcile tab after Cleanup Review
  - _cached_reconciliation_scan + tables + merge UI (checkbox, pair type, selectbox, keep id)
  - MEMORY.md tab row
modified: [app.py, MEMORY.md]
executed: 2026-04-03
---

# Plan 03-02 summary

- Tab order: Cleanup → **Reconcile** → Efficiency → Dashboard → Investigate.
- Cached scan shares artifact stamp pattern with dashboard loaders; empty `ReconciliationScan` on failure.
- Merge path: `merge_characters` / `merge_entities(..., "Location")` with `get_driver()`, then `st.cache_data.clear()` + flash + rerun.
