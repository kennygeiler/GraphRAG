# ScriptRAG (GraphRAG)

## What This Is

**ScriptRAG** is a GraphRAG system for screenplays: Final Draft (`.fdx`) is parsed into JSON, a self-healing LLM pipeline extracts a validated scene graph (Pydantic + instructor) with **verbatim `source_quote`** on narrative edges, and **Neo4j** stores `Character`, `Location`, `Prop`, and `Event` nodes with structural and narrative relationships. A **Streamlit** app runs the pipeline, verify (HITL), **reconciliation** (`reconcile.py`), **Data out** (recipe Cypher + CSV), and efficiency tracking. Structural analytics (including **structural load** MET-01) live in **`metrics.py`** / CLI. Optional **`SCRIPTRAG_*`** + **`lead_resolution.py`** remain for programmatic use.

**Audience:** Writers, producers, and developers analyzing narrative “physics” (agency, friction, props, conflict structure)—not vibes without evidence.

## Core Value

End-to-end, **evidence-backed** structural analysis of a screenplay in a queryable graph—with a working pipeline from upload → validated extraction → Neo4j → exports and **metrics.py** analytics.

## Current milestones

### v1.2 — Demo & data-out flow (execution focus)

**Goal:** Linear story for technical demos: **self-healing extraction → HITL → manipulable graph data** (schema, recipe SQL/Cypher, CSV), with analytics tabs as follow-ons.

**Target features:** **OUT-01** (Data out tab + `data_out.py`), **FLOW-01** (Cleanup + Efficiency narrative), **DEMO-01** (optional demo tab layout). See `.planning/ROADMAP.md` Phases 8–10.

### v1.1 — Quality, tests & open-source hygiene (parallel)

**Goal:** Pytest for `metrics` / `reconcile` (**QA-01**, **QA-02**), **LICENSE** (**DOC-01**), **Levenshtein** polish (**PERF-01**).

## Requirements

### Validated

- ✓ **FDX → JSON parsing** with stable scene numbering — existing (`parser.py`)
- ✓ **Lexicon + per-scene extraction** with self-healing LangGraph ETL (`etl_core`, `ingest.py`, `domains/screenplay/`) — existing
- ✓ **Neo4j load** with merge semantics and `source_quote` on narrative rels (`neo4j_loader.py`) — existing
- ✓ **Metrics layer** (passivity, scene heat, momentum, payoff props, act buckets, power shift, structural load MET-01, etc.) — existing (`metrics.py`)
- ✓ **Streamlit product** — Pipeline, Verify, **Reconcile**, **Data out**, Efficiency Tracking (`app.py`, `cleanup_review.py`, `reconcile.py`, `data_out.py`, `pipeline_runs.py`) — existing; **Dashboard / Investigate removed** (2026-04-03 product trim).
- ✓ **Pipeline run telemetry** as `:PipelineRun` nodes — existing
- ✓ **Programmatic lead helpers** — `lead_resolution.py`, optional `SCRIPTRAG_*` (not used by Streamlit UI after trim).
- ✓ **Script-agnostic operator copy** — no hardcoded production character IDs in `app.py` UI strings; docs aligned (`MEMORY.md`, `strategy.md`).
- ✓ **Empty / partial graph hardening (REL-01)** — explicit empty states and safe metric paths when Neo4j is missing or incomplete (Phase 2, 2026-04-03).
- ✓ **Reconciliation for operators (REC-01)** — `reconcile.py` CLI + Streamlit tab; documented merge semantics (Phase 3, 2026-04-03).
- ✓ **Structural load / production signal (MET-01)** — `get_structural_load_snapshot`, CLI (Phase 4, 2026-04-04).
- ✓ **Data out / manipulable sink (OUT-01)** — `data_out.py`, **Data out** tab: schema card, recipe Cypher, CSV exports (v1.2 Phase 8, 2026-04-03).
- ✓ **HITL + observability copy (FLOW-01)** — Cleanup Review HITL banner; Efficiency observability caption (v1.2 Phase 9, 2026-04-03).
- ✓ **Demo layout flag (DEMO-01)** — `SCRIPTRAG_DEMO_LAYOUT` tab order + copy (v1.2 Phase 10, 2026-04-03).

### Active

- (v1.2 Phases 8–10 complete — see **v1.1** for QA/LICENSE/Levenshtein.)
- [ ] **QA-01 / QA-02** — Unit tests for structural load and reconcile scan (mocked driver); see `.planning/REQUIREMENTS.md` v1.1.
- [ ] **DOC-01** — LICENSE + README license alignment.
- [ ] **PERF-01** — Optional Levenshtein acceleration for fuzzy reconcile; document in README.

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
| Brownfield GSD init without parallel web research | Domain and stack already documented in `strategy.md` and `.planning/codebase/*` | Applied for v1.0 |
| Coarse roadmap granularity | Few broad phases for solo/small-team execution | Applied v1.0; v1.1 continues phase numbering at 5 |
| Track planning docs in git | `commit_docs: true` in GSD config | Applied |
| v1.1 scope: tests before CI | QA-01/02 justify later CI-01 | — Open |

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
*Last updated: 2026-04-03 — v1.2 demo/data-out roadmap added; Phase 8 (`data_out.py`, **Data out** tab) started.*
