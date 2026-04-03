---
phase: 04-production-complexity-signal
plan: 01
subsystem: analytics
tags: [metrics, neo4j, MET-01, streamlit]

provides:
  - get_structural_load_snapshot + NARRATIVE_REL_TYPES in metrics.py
  - metrics.py --structural-load CLI
  - Dashboard structural load metrics (cached)
executed: 2026-04-04
requirement: MET-01
---

# Phase 4 / MET-01 summary

**Structural load index:** `narrative_edge_count / max(scene_count, 1)` over rel types `INTERACTS_WITH`, `CONFLICTS_WITH`, `USES`, `LOCATED_IN`, `POSSESSES`. Entity counts included for context. Documented in `strategy.md` §4, README, MEMORY.
