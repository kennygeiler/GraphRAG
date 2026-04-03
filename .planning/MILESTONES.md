# Milestones — ScriptRAG (GSD)

## v1.0 — Brownfield hardening & operator surface (complete)

**Completed:** 2026-04-04  
**Phases:** 1–4 (see **Completed milestone v1.0** in `.planning/ROADMAP.md`, and phase dirs `01`–`04` under `.planning/phases/`).

**Shipped:**

- **CONFIG / GEN:** Analysis-derived primary lead + `SCRIPTRAG_PRIMARY_LEAD_ID` / `SCRIPTRAG_TOP_CHARACTERS`; script-agnostic operator copy (`lead_resolution.py`, `app.py`).
- **REL-01:** Empty / partial Neo4j and skewed query shapes do not crash Streamlit metric paths.
- **REC-01:** `reconcile.py` CLI (`--dry-run`, `--scope`) + Streamlit **Reconcile** tab; safe merge (APOC or manual rewire).
- **MET-01:** Structural load snapshot (`get_structural_load_snapshot`), `metrics.py --structural-load` (CLI).

---

## v1.1 — Quality, tests & open-source hygiene (parallel)

See `.planning/REQUIREMENTS.md` (QA/DOC/PERF) and `.planning/ROADMAP.md` (Phases 5–7).

---

## v1.2 — Demo & data-out flow (complete)

**Completed:** 2026-04-03  
**Phases:** 8–10

**Shipped:** **OUT-01** (**Data out** tab, `data_out.py`); **FLOW-01** (Cleanup HITL + Efficiency observability copy); **DEMO-01** (`SCRIPTRAG_DEMO_LAYOUT`).

See `.planning/ROADMAP.md` (**Milestone v1.2**), phase dirs `08-data-out-demo-flow/`, `09-hitl-observability-copy/`, `10-demo-layout-flag/`.
