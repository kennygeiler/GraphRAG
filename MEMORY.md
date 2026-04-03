# Project memory (compact)

**Last aligned:** April 2026. For full detail use **`strategy.md`**. **GSD:** **v1.3** Verify HITL depth **complete** (Phases 11–13); **v1.1** tests/LICENSE parallel — `.planning/ROADMAP.md`.

## What this is

**ScriptRAG**: `.fdx` → JSON → **Neo4j** (`Character`, `Location`, `Prop`, `Event` + `IN_SCENE` + narrative rels with `source_quote`). **Streamlit** app = upload screenplay → self-healing extraction (**Pipeline**: full in-process run via **`extract_scenes`**; corrections include **fixer** + **auditor_auto_apply**) → **Audit & Verify** (HITL warnings + load) → optional **Reconcile** → **Data out** (recipe Cypher + CSV) and **Pipeline Efficiency Tracking**. Each pipeline run writes a **:PipelineRun** with **`telemetry_version`** (0 = legacy, 1 = Phase 0 per-stage buckets) plus totals; see **`Telemetry.md`** and **`strategy.md`**. Semantic audit may **auto-apply** gated patches; decisions log to **`audit_decisions.jsonl`** (gitignored). **Bundled scripts:** `samples/` (Cinema Four full + Ludwig micro-sample, each `.fdx` + companion `.pdf`); root **README** has a **demo walkthrough**.

## App sections (`app.py`)

Navigation is a **horizontal radio** (`scriptrag_section`), not `st.tabs`, so interactions inside **Data out** do not reset the visible section on rerun.

| Section | Purpose |
|---------|---------|
| **Pipeline** | Upload FDX → parse/lexicon → **full extraction** in one run (live progress); **:PipelineRun** when the run finishes; audit **decisions** table when present; corrections = fixer + **auditor_auto_apply** |
| **Audit & Verify** | **Filter/sort/bulk** duplicates; **preview** + **evidence**; optional **notes**; **Decision log** CSV/JSON + **last-load** snapshot; **Approve & Load** → Neo4j (HITL warnings only; auto-applied audit edits already in graph JSON) |
| **Reconcile** | Optional post-load hygiene: ghost characters + fuzzy **Character** / **Location** name pairs (`reconcile.py`); optional merge with checkbox + pair picker (APOC or manual rewire) |
| **Data out** | Schema card, live Neo4j label/rel counts, fixed recipe Cypher (`data_out.py`), CSV downloads (narrative edges, characters, events) |
| **Pipeline Efficiency Tracking** | **:PipelineRun** rows: **Token Agent** **v0**/v1/…, totals; **v0** shows **N/A** for E/F/A split columns |

**Pipeline** hidden when `DISABLE_PIPELINE=1` (read-only deployments). **`SCRIPTRAG_DEMO_LAYOUT=1`** reorders **Audit & Verify → Data out → Reconcile → …** (default is **Audit & Verify → Reconcile → Data out → …**).

**Resilience (REL-01):** Cached Neo4j reads for **Reconcile** / **Data out** log failures and return empty shapes.

## Act structure (dynamic)

From Neo4j: **`get_script_act_bounds`** in `metrics.py` — `min(:Event.number)` … `max(:Event.number)` split into **three as-equal-as-possible** buckets. Not fixed to "scene 21 / 65"; changes with whatever script is loaded.

## Key metrics (`metrics.py` / CLI)

Charts were removed from the app; definitions remain for **CLI** and programmatic use:

- **Momentum heat (per scene):** `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` among entities both `IN_SCENE` to that `Event`; a **3-scene** rolling mean was used by the old chart (`get_narrative_momentum_by_scene`).
- **Payoff props:** `get_payoff_prop_timelines` — first intro vs last `USES` / `CONFLICTS_WITH`; keep if gap **> 10** scenes.
- **Passivity (per act window):** `in / (in + out)` on `CONFLICTS_WITH` + `USES` (incl. incoming `USES` on possessed props), edges attributed to scenes in the act range.
- **Structural load (MET-01):** `get_structural_load_snapshot` — narrative rel instances ÷ **:Event** count; CLI: `uv run python metrics.py --structural-load`. Not a story-quality score.

**`lead_resolution.py`** + **`SCRIPTRAG_*`** env vars still exist for **programmatic** use (not wired into Streamlit after dashboard removal).

## Separate: "scene heat" in `metrics.py`

**Distinct** from momentum heat: **unique unordered conflict pairs** in-scene ÷ `IN_SCENE` count (`get_scene_heat`). CLI / diagnostics; different formula than narrative momentum.

## Optional `tools/`

Neo4j QA exports and related helpers live under **`tools/`** (`tools/README.md`). Run scripts from the repo root (`uv run python tools/debug_export.py`, etc.); outputs default to the repo root (`graph_qa_dump.json`, `data_health_report.json`).

## Architecture: engine vs domain

Generic ETL engine lives in `etl_core/` (LangGraph state machine, telemetry, cost tracking, optional **`audit_post_process`**). Screenplay wiring: **`domains/screenplay/adapter.py`**, **`audit_pipeline.py`** (`process_semantic_audit`), **`audit_patch.py`**, **`audit_policy.py`**, **`auditors.py`**. **Optional LLM auditors** run one pass after validate; gated **auto-apply** mutates the graph and **`audit_trail`**; remaining findings are **warnings** for **Audit & Verify**. No **`audit_fixer`** LLM loop.

The `ingest.py` module exposes **`extract_scenes()`** (and **`run_single_scene_extraction()`** for reuse) — CLI and Streamlit **Pipeline** iterate the full generator in one request.

## Pipeline order (cold start)

`parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` → `streamlit run app.py`

## Secrets

**`.env`** only; never commit. Template: **`.env.example`**.
