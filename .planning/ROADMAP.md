# Roadmap: ScriptRAG (brownfield hardening)

## Overview

This milestone takes ScriptRAG from Cinema Four–centric defaults and brittle dashboard paths to **config-driven identity**, **script-agnostic UI copy**, **safe empty and partial-graph behavior**, **operator-visible reconciliation**, and a first **production-complexity signal** from the graph—without replacing core structural metrics. Phases run in dependency order: generalization first, then reliability on those code paths, then graph reconciliation exposure, then the density-based overlay.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, 4): Planned milestone work
- Decimal phases (e.g. 2.1): Urgent insertions via `/gsd-insert-phase`

- [ ] **Phase 1: Config & script-generalized dashboard** — Protagonist/lead IDs and role-dependent labels come from env or project config, not hardcoded constants.
- [ ] **Phase 2: Graph reliability & empty states** — Empty, partial, or schema-skewed Neo4j never takes down Streamlit metric paths.
- [ ] **Phase 3: Reconciliation for operators** — `reconcile.py` workflows are usable and documented at a defined scope with safe merge semantics.
- [ ] **Phase 4: Production complexity signal** — Initial density- or structure-derived complexity/cost signal in app or CLI, alongside existing metrics.

## Phase Details

### Phase 1: Config & script-generalized dashboard
**Goal**: Operators run analytics and dashboards for arbitrary scripts using configuration and graph-derived identity instead of editing Python constants or script-specific defaults.
**Depends on**: Nothing (first phase)
**Requirements**: CONFIG-01, GEN-01
**Success Criteria** (what must be TRUE):
  1. Operator sets protagonist/lead identifiers via environment variables or a small project config file; `PROTAGONIST_ID`-style constants in `app.py` are no longer the only way to get correct regression and role-dependent behavior.
  2. Dashboard labels and takeaways that previously assumed fixed character IDs use config-driven or graph-derived identities so a new script does not require code edits for basic correctness.
  3. After changing config, reloading the app shows updated lead-dependent charts or copy without redeploying code changes to constants.
**Plans**: TBD
**UI hint**: yes

### Phase 2: Graph reliability & empty states
**Goal**: The Streamlit product remains usable and explicit when the graph is missing, empty, incomplete, or returns unexpected shapes.
**Depends on**: Phase 1
**Requirements**: REL-01
**Success Criteria** (what must be TRUE):
  1. With Neo4j empty, unreachable, or partially loaded, affected tabs show explicit empty states, safe fallbacks, or clear operator-facing errors—not uncaught exceptions in the UI.
  2. When query results lack expected columns or rows, metric and DataFrame code paths avoid KeyErrors and broken tables; user sees a controlled message or degraded view.
  3. Operator can walk primary flows (e.g. pipeline, cleanup review, dashboard, investigate) without hitting traceback pages solely because graph data is absent or incomplete.
**Plans**: TBD
**UI hint**: yes

### Phase 3: Reconciliation for operators
**Goal**: Reconciliation is a first-class, understandable operator capability aligned with existing merge patterns.
**Depends on**: Phase 2
**Requirements**: REC-01
**Success Criteria** (what must be TRUE):
  1. Operator can run reconciliation at the agreed scope via CLI and/or Streamlit (as implemented), without spelunking undocumented code.
  2. In-app or operator-facing documentation describes what reconcile does, safe merge behavior, and when to use it—consistent with `reconcile.py` and `neo4j_loader` patterns.
  3. A dry-run or confirmation path (or equivalent guardrails documented in UI) makes unintended merges unlikely for the defined workflow.
**Plans**: TBD
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
| 1. Config & script-generalized dashboard | TBD | Not started | - |
| 2. Graph reliability & empty states | TBD | Not started | - |
| 3. Reconciliation for operators | TBD | Not started | - |
| 4. Production complexity signal | TBD | Not started | - |
