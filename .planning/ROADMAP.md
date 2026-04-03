# Roadmap: ScriptRAG

## Milestone v1.2 — Demo & data-out flow (**complete**)

**Goal:** Optimize the **agentic pipeline story** for technical demos: self-heal → HITL → **visible manipulable data** (schema, recipe queries, exports) → analytics/ops tabs as secondary.

**Execution order:** 8 → 9 → 10 (parallel with v1.1 Phases 5–7 as bandwidth allows).

### Phases

- [x] **Phase 8: Data out surface** — **OUT-01**: **Data out** tab (`data_out.py` + `app.py`): live label/rel counts, fixed recipe Cypher, CSV downloads (narrative edges, characters, events); Reconcile caption = optional post-load hygiene. Plans: `08-01-PLAN.md` — summary `08-01-SUMMARY.md`.
- [x] **Phase 9: HITL & observability copy** — **FLOW-01**: Cleanup HITL banner; Efficiency **agentic observability** caption. Plan `09-01-PLAN.md` — summary `09-01-SUMMARY.md`.
- [x] **Phase 10: Demo layout (optional)** — **DEMO-01**: `SCRIPTRAG_DEMO_LAYOUT` reorders tabs (Cleanup → Data out → Reconcile…); sidebar + Investigate copy. Plan `10-01-PLAN.md` — summary `10-01-SUMMARY.md`.

### Progress (v1.2)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 8. Data out surface | 1/1 | Complete | 2026-04-03 |
| 9. HITL & observability copy | 1/1 | Complete | 2026-04-03 |
| 10. Demo layout (optional) | 1/1 | Complete | 2026-04-03 |

---

## Milestone v1.1 (parallel) — Quality, tests & open-source hygiene

**Goal:** Lock in **regression safety** for `metrics` / `reconcile` with pytest + mocks, make the repo **license-clear** for publication, and remove **fuzzywuzzy** speed warnings via an optional native Levenshtein dependency.

**Execution order:** 5 → 6 → 7 (decimal insertions, if any, run between surrounding integers).

### Phases

- [ ] **Phase 5: Test foundations** — **QA-01**, **QA-02**: pytest with mocked Neo4j driver/session for `get_structural_load_snapshot` (or stable public wrapper) and `reconcile.py` scan / pure helpers; no mandatory integration DB in CI.
- [ ] **Phase 6: Open-source hygiene** — **DOC-01**: add root **LICENSE** (e.g. MIT), update **README** license section; optional **CONTRIBUTING.md** only if substantive.
- [ ] **Phase 7: Dependency polish** — **PERF-01**: optional **`python-Levenshtein`** in dependencies + README / reconcile note for operators.

### Progress (v1.1)

| Phase | Plans Complete | Status | Completed |
|-------|------------------|--------|-----------|
| 5. Test foundations | 0/? | Not started | — |
| 6. Open-source hygiene | 0/? | Not started | — |
| 7. Dependency polish | 0/? | Not started | — |

---

## Milestone v1.3 — Verify HITL depth (**complete**)

**Goal:** Make **Verify** decisions faster and safer: show **graph evidence** on each warning, **human-readable entity labels**, an explicit **Approve preview**, **scene grouping**, and clear **no-auto-edit** treatment for informational checks.

**Execution order:** 11 → 12 → 13.

### Phases

- [x] **Phase 11: HITL evidence cards** — **HITL-01**: `cleanup_review.py` helpers (`graph_entity_labels`, `warning_hitl_evidence_markdown`, `warning_hitl_approve_preview`); Verify tab: scene-grouped cards, evidence expander, approve preview line, banners for `completeness` / `audit_skipped`. Plan `11-01-PLAN.md` — summary `11-01-SUMMARY.md`.
- [x] **Phase 12: HITL workflow scale** — **HITL-02**: filter/sort warnings by check type or scene; bulk **Approve** for visible `duplicate_relationship` (whole extract + per-scene) with confirmation. Plan `12-01-PLAN.md` — summary `12-01-SUMMARY.md`.
- [x] **Phase 13: HITL audit trail** — **HITL-03**: CSV/JSON decision log + optional per-warning notes; last-load snapshot with `neo4j_load_completed_at`. Plan `13-01-PLAN.md` — summary `13-01-SUMMARY.md`.

### Progress (v1.3)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 11. HITL evidence cards | 1/1 | Complete | 2026-04-03 |
| 12. HITL workflow scale | 1/1 | Complete | 2026-04-03 |
| 13. HITL audit trail | 1/1 | Complete | 2026-04-03 |

---

## Completed milestone v1.0 — Brownfield hardening (2026-04-03 — 2026-04-04)

This milestone took ScriptRAG from Cinema Four–centric defaults and brittle dashboard paths to **config-driven identity**, **script-agnostic UI copy**, **safe empty and partial-graph behavior**, **operator-visible reconciliation**, and a first **production-complexity signal** from the graph—without replacing core structural metrics.

### Phases (all complete)

- [x] **Phase 1: Config & script-generalized dashboard** — Leads are **analysis-discerned** from the graph/metrics; optional env/project config **overrides**; script-agnostic copy across **all** Streamlit tabs (not dashboard-only).
- [x] **Phase 2: Graph reliability & empty states** — Empty, partial, or schema-skewed Neo4j never takes down Streamlit metric paths.
- [x] **Phase 3: Reconciliation for operators** — `reconcile.py` workflows are usable and documented at a defined scope with safe merge semantics.
- [x] **Phase 4: Production complexity signal** — Initial density- or structure-derived complexity/cost signal in app or CLI, alongside existing metrics.

### Phase details (v1.0 archive)

#### Phase 1: Config & script-generalized dashboard
**Goal**: Operators run the full Streamlit app for arbitrary scripts using **analysis-derived lead identity** (structural metrics) with **optional overrides**, instead of hardcoded constants or production-specific defaults.  
**Depends on**: Nothing (first phase)  
**Requirements**: CONFIG-01, GEN-01  
**Success criteria**:
  1. Primary (and any UI-relevant) leads for regression warnings and role-dependent analytics come from **documented graph/metrics analysis**; environment or small project config can **override or pin** when needed; `PROTAGONIST_ID`-style constants are not the sole source of truth.
  2. User-visible copy and labels across **Pipeline, Cleanup Review, Pipeline Efficiency Tracking, Dashboard, and Investigate** (and related operator-facing modules) use analysis- or graph-derived identities (plus overrides)—not fixed script-specific character IDs in code.
  3. After changing **override** config, a Streamlit restart (or redeploy) reflects updated lead-dependent behavior; changing loaded graph data continues to drive analysis through existing cache/data paths.  
**Plans**: 2 (`01-01-PLAN.md` CONFIG-01, `01-02-PLAN.md` GEN-01)

#### Phase 2: Graph reliability & empty states
**Goal**: The Streamlit product remains usable and explicit when the graph is missing, empty, incomplete, or returns unexpected shapes.  
**Depends on**: Phase 1  
**Requirements**: REL-01  
**Success criteria**:
  1. With Neo4j empty, unreachable, or partially loaded, affected tabs show explicit empty states, safe fallbacks, or clear operator-facing errors—not uncaught exceptions in the UI.
  2. When query results lack expected columns or rows, metric and DataFrame code paths avoid KeyErrors and broken tables; user sees a controlled message or degraded view.
  3. Operator can walk primary flows (e.g. pipeline, cleanup review, dashboard, investigate) without hitting traceback pages solely because graph data is absent or incomplete.  
**Plans**: `02-01-PLAN.md` (executed 2026-04-03; summary `02-01-SUMMARY.md`)

#### Phase 3: Reconciliation for operators
**Goal**: Reconciliation is a first-class, understandable operator capability aligned with existing merge patterns.  
**Depends on**: Phase 2  
**Requirements**: REC-01  
**Success criteria**:
  1. Operator can run reconciliation at the agreed scope via CLI and/or Streamlit (as implemented), without spelunking undocumented code.
  2. In-app or operator-facing documentation describes what reconcile does, safe merge behavior, and when to use it—consistent with `reconcile.py` and `neo4j_loader` patterns.
  3. A dry-run or confirmation path (or equivalent guardrails documented in UI) makes unintended merges unlikely for the defined workflow.  
**Plans**: `03-01-PLAN.md`, `03-02-PLAN.md` (executed 2026-04-03; summaries `03-01-SUMMARY.md`, `03-02-SUMMARY.md`)

#### Phase 4: Production complexity signal
**Goal**: A first Phase 3–style production/cost signal from graph structure is available without diluting or replacing existing structural metrics.  
**Depends on**: Phase 3  
**Requirements**: MET-01  
**Success criteria**:
  1. A complexity or production-cost-oriented signal derived from graph density (or closely related structural statistics) is visible in the Streamlit app and/or callable from a documented CLI path.
  2. Passivity, momentum, scene heat, payoff props, act buckets, power shift, and other existing structural metrics remain defined and presented as before; the new signal is additive.
  3. Operator can read the new signal next to existing dashboard analytics and understand that it reflects structural load/density, not a replacement “quality score.”  
**Plans**: Implemented 2026-04-04 (`get_structural_load_snapshot`, Dashboard + `metrics.py --structural-load`; summary `04-01-SUMMARY.md`)

### Progress (v1.0)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Config & script-generalized dashboard | 2/2 | Complete | 2026-04-03 |
| 2. Graph reliability & empty states | 1/1 | Complete | 2026-04-03 |
| 3. Reconciliation for operators | 2/2 | Complete | 2026-04-03 |
| 4. Production complexity signal | 1 (inline) | Complete | 2026-04-04 |
