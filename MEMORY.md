# Project memory (compact)

**Last aligned:** April 2026. For full detail use **`strategy.md`**. **GSD:** **v1.3** Verify HITL depth **complete** (Phases 11–13); **v1.1** tests/LICENSE parallel — `.planning/ROADMAP.md`.

## What this is

**ScriptRAG**: `.fdx` → JSON → **Neo4j** (`Character`, `Location`, `Prop`, `Event` + `IN_SCENE` + narrative rels with `source_quote`). **Streamlit** app = upload screenplay → self-healing extraction (**Pipeline** shows corrections) → **Verify** (warnings + load) → optional **Reconcile** → **Data out** (recipe Cypher + CSV) and **Pipeline Efficiency Tracking**. Each pipeline run writes a **:PipelineRun** node (efficiency metrics; in-app telemetry tokens/cost). **Bundled scripts:** `samples/` (Cinema Four full + Ludwig micro-sample, each `.fdx` + companion `.pdf`); root **README** has a **demo walkthrough**.

## App sections (`app.py`)

Navigation is a **horizontal radio** (`scriptrag_section`), not `st.tabs`, so interactions inside **Data out** do not reset the visible section on rerun.

| Section | Purpose |
|---------|---------|
| **Pipeline** | Upload FDX, run full extraction in-process (parse → lexicon → per-scene LangGraph); live progress; persists **:PipelineRun** to Neo4j after each run |
| **Verify** | **Filter/sort/bulk** duplicates; **preview** + **evidence**; optional **notes**; **Decision log** CSV/JSON + **last-load** snapshot; **Approve & Load** → Neo4j. **Corrections** under **Pipeline** |
| **Reconcile** | Optional post-load hygiene: ghost characters + fuzzy **Character** / **Location** name pairs (`reconcile.py`); optional merge with checkbox + pair picker (APOC or manual rewire) |
| **Data out** | Schema card, live Neo4j label/rel counts, fixed recipe Cypher (`data_out.py`), CSV downloads (narrative edges, characters, events) |
| **Pipeline Efficiency Tracking** | Table of **:PipelineRun** rows: telemetry tokens/cost, corrections/warnings counts, agent opt. version |

**Pipeline** hidden when `DISABLE_PIPELINE=1` (read-only deployments). **`SCRIPTRAG_DEMO_LAYOUT=1`** reorders **Verify → Data out → Reconcile → …** (default is **Verify → Reconcile → Data out → …**).

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

Generic ETL engine lives in `etl_core/` (LangGraph state machine, telemetry, cost tracking). Screenplay-specific models and rules live in `domains/screenplay/`. The engine accepts a pluggable `DomainBundle` so it can be reused for other domains without touching core logic.

The `ingest.py` module exposes `extract_scenes()` — a generator that yields per-scene results, consumed by both the CLI (`main()`) and the Streamlit Pipeline tab.

## Pipeline order (cold start)

`parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` → `streamlit run app.py`

## Secrets

**`.env`** only; never commit. Template: **`.env.example`**.
