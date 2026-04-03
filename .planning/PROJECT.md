# ScriptRAG (GraphRAG)

## What This Is

**ScriptRAG** is a GraphRAG system for screenplays: Final Draft (`.fdx`) is parsed into JSON, a self-healing LLM pipeline extracts a validated scene graph (Pydantic + instructor) with **verbatim `source_quote`** on narrative edges, and **Neo4j** stores `Character`, `Location`, `Prop`, and `Event` nodes with structural and narrative relationships. A **Streamlit** app runs the pipeline, cleanup review, efficiency tracking, structural metrics dashboards, and natural-language graph investigation.

**Audience:** Writers, producers, and developers analyzing narrative “physics” (agency, friction, props, conflict structure)—not vibes without evidence.

## Core Value

End-to-end, **evidence-backed** structural analysis of a screenplay in a queryable graph—with a working pipeline from upload → validated extraction → Neo4j → metrics and investigation.

## Requirements

### Validated

- ✓ **FDX → JSON parsing** with stable scene numbering — existing (`parser.py`)
- ✓ **Lexicon + per-scene extraction** with self-healing LangGraph ETL (`etl_core`, `ingest.py`, `domains/screenplay/`) — existing
- ✓ **Neo4j load** with merge semantics and `source_quote` on narrative rels (`neo4j_loader.py`) — existing
- ✓ **Metrics layer** (passivity, scene heat, momentum, payoff props, act buckets, power shift, etc.) — existing (`metrics.py`)
- ✓ **Streamlit product** — Pipeline, Cleanup Review, Efficiency Tracking, Dashboard, Investigate (`app.py`, `cleanup_review.py`, `agent.py`, `pipeline_runs.py`) — existing
- ✓ **Pipeline run telemetry** as `:PipelineRun` nodes — existing
- ✓ **Ask-the-graph** NL → Cypher path — existing (`agent.py`)
- ✓ **Analysis-derived primary lead + env overrides** — `lead_resolution.py`, `SCRIPTRAG_PRIMARY_LEAD_ID` / `SCRIPTRAG_TOP_CHARACTERS`, Dashboard regression + sidebar (Phase 1 execute, 2026-04-03).
- ✓ **Script-agnostic operator copy (Phase 1 scope)** — no hardcoded production character IDs in `app.py` UI strings; docs aligned (`MEMORY.md`, `strategy.md`).

### Active

- [ ] **Empty / partial graph hardening** — clear user messaging; no uncaught KeyErrors or broken DataFrame paths when Neo4j or artifacts are missing or incomplete (`strategy.md` §6).
- [x] **Reconciliation at scale** — expand `reconcile.py` usage from CLI/dashboard with safe merge patterns documented in UI (`strategy.md` §6). (REC-01: `run_reconciliation_scan`, CLI `--scope`, README, Streamlit **Reconcile** tab.)
- [x] **Phase 3 complexity signals** — initial production-oriented overlays from graph density without diluting structural truth (`strategy.md` §3 roadmap). (MET-01: `get_structural_load_snapshot`, Dashboard + CLI.)

### Out of Scope

- **Sentiment / subtext on edges** unless strictly grounded in `source_quote` and secondary to structural metrics — exploratory future phase (`strategy.md` §3).
- **Replacing** the core stack (Neo4j, Streamlit, Anthropic/instructor) — not this milestone.
- **Vibe-only scoring** without graph evidence — conflicts with project philosophy.

## Context

- **Authoritative product doc:** `strategy.md` (metrics definitions, dashboard map, AI rules). **Short mirror:** `MEMORY.md`, `.cursorrules`, `AGENTS.md`.
- **Code layout:** Flat Python modules at repo root; generic ETL in `etl_core/`; screenplay domain in `domains/screenplay/`. See `.planning/codebase/ARCHITECTURE.md` and `STRUCTURE.md`.
- **Package manager:** `uv` (`uv sync`, `uv run`).
- **Secrets:** `.env` only; template `.env.example`.
- **GSD:** Codebase mapped under `.planning/codebase/`; this file initializes the GSD project track for forward work aligned with `strategy.md`.

## Constraints

- **Tech:** Python 3.12, Neo4j, Streamlit, Plotly, parameterized Cypher only for user-facing query paths.
- **Evidence:** Narrative edges must retain verbatim `source_quote` from the script.
- **Documentation:** On major milestones, update `strategy.md` first, then mirrors—per repo rules.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Brownfield GSD init without parallel web research | Domain and stack already documented in `strategy.md` and `.planning/codebase/*` | — Pending |
| Coarse roadmap granularity | Few broad phases for solo/small-team execution | — Pending |
| Track planning docs in git | `commit_docs: true` in GSD config | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):

1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):

1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-03 after GSD project initialization (`/gsd-new-project`, brownfield)*
