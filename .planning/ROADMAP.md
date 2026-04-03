# Roadmap: ScriptRAG (brownfield hardening)

## Overview

This milestone takes ScriptRAG from Cinema Four–centric defaults and brittle dashboard paths to **config-driven identity**, **script-agnostic UI copy**, **safe empty and partial-graph behavior**, **operator-visible reconciliation**, and a first **production-complexity signal** from the graph—without replacing core structural metrics. Phases run in dependency order: generalization first, then reliability on those code paths, then graph reconciliation exposure, then the density-based overlay.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, 4): Planned milestone work
- Decimal phases (e.g. 2.1): Urgent insertions via `/gsd-insert-phase`

- [x] **Phase 1: Config & script-generalized dashboard** — Leads are **analysis-discerned** from the graph/metrics; optional env/project config **overrides**; script-agnostic copy across **all** Streamlit tabs (not dashboard-only).
- [x] **Phase 2: Graph reliability & empty states** — Empty, partial, or schema-skewed Neo4j never takes down Streamlit metric paths.
- [ ] **Phase 3: Reconciliation for operators** — `reconcile.py` workflows are usable and documented at a defined scope with safe merge semantics.
- [ ] **Phase 4: Production complexity signal** — Initial density- or structure-derived complexity/cost signal in app or CLI, alongside existing metrics.

## Phase Details

### Phase 1: Config & script-generalized dashboard
**Goal**: Operators run the full Streamlit app for arbitrary scripts using **analysis-derived lead identity** (structural metrics) with **optional overrides**, instead of hardcoded constants or production-specific defaults.
**Depends on**: Nothing (first phase)
**Requirements**: CONFIG-01, GEN-01
**Success Criteria** (what must be TRUE):
  1. Primary (and any UI-relevant) leads for regression warnings and role-dependent analytics come from **documented graph/metrics analysis**; environment or small project config can **override or pin** when needed; `PROTAGONIST_ID`-style constants are not the sole source of truth.
  2. User-visible copy and labels across **Pipeline, Cleanup Review, Pipeline Efficiency Tracking, Dashboard, and Investigate** (and related operator-facing modules) use analysis- or graph-derived identities (plus overrides)—not fixed script-specific character IDs in code.
  3. After changing **override** config, a Streamlit restart (or redeploy) reflects updated lead-dependent behavior; changing loaded graph data continues to drive analysis through existing cache/data paths.
**Plans**: 2 (`01-01-PLAN.md` CONFIG-01, `01-02-PLAN.md` GEN-01)
**UI hint**: yes

### Phase 2: Graph reliability & empty states
**Goal**: The Streamlit product remains usable and explicit when the graph is missing, empty, incomplete, or returns unexpected shapes.
**Depends on**: Phase 1
**Requirements**: REL-01
**Success Criteria** (what must be TRUE):
  1. With Neo4j empty, unreachable, or partially loaded, affected tabs show explicit empty states, safe fallbacks, or clear operator-facing errors—not uncaught exceptions in the UI.
  2. When query results lack expected columns or rows, metric and DataFrame code paths avoid KeyErrors and broken tables; user sees a controlled message or degraded view.
  3. Operator can walk primary flows (e.g. pipeline, cleanup review, dashboard, investigate) without hitting traceback pages solely because graph data is absent or incomplete.
**Plans**: `02-01-PLAN.md` (executed 2026-04-03; summary `02-01-SUMMARY.md`)
**UI hint**: yes

### Phase 3: Reconciliation for operators
**Goal**: Reconciliation is a first-class, understandable operator capability aligned with existing merge patterns.
**Depends on**: Phase 2
**Requirements**: REC-01
**Success Criteria** (what must be TRUE):
  1. Operator can run reconciliation at the agreed scope via CLI and/or Streamlit (as implemented), without spelunking undocumented code.
  2. In-app or operator-facing documentation describes what reconcile does, safe merge behavior, and when to use it—consistent with `reconcile.py` and `neo4j_loader` patterns.
  3. A dry-run or confirmation path (or equivalent guardrails documented in UI) makes unintended merges unlikely for the defined workflow.
**Plans**: `03-01-PLAN.md` (scan API + CLI + README), `03-02-PLAN.md` (Streamlit Reconcile tab + MEMORY)
**UI hint**: yes

### Phase 4: Production complexity signal
**Goal**: A first Phase 3–style production/cost signal from graph structure is available without diluting or replacing existing structural metrics.
**Depends on**: Phase 3
**Requirements**: MET-01
**Success Criteria** (what must be TRUE):
  1. A complexity or production-cost-oriented signal derived from graph density (or closely related structural statistics) is visible in the Streamlit app and/or callable from a documented CLI path.
  2. Passivity, momentum, scene heat, payoff props, act buckets, power shift, and other existing structural metrics remain defined and presented as before; the new signal is additive.
  3. Operator can read the new signal next to existing dashboard analytics and understand that it reflects structural load/density, not a replacement “quality score.”
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:** 1 → 2 → 3 → 4 (decimal insertions, if any, run between their surrounding integers).

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Config & script-generalized dashboard | 2/2 | Complete | 2026-04-03 |
| 2. Graph reliability & empty states | 1/1 | Complete | 2026-04-03 |
| 3. Reconciliation for operators | 2 planned (`03-01`, `03-02`) | Planned | - |
| 4. Production complexity signal | TBD | Not started | - |
