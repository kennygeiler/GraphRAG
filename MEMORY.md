# Project memory (compact)

**Last aligned:** April 2026. For full detail use **`strategy.md`**. **GSD:** active milestone **v1.1** (tests, LICENSE, dep polish) — `.planning/ROADMAP.md`.

## What this is

**ScriptRAG**: `.fdx` → JSON → **Neo4j** (`Character`, `Location`, `Prop`, `Event` + `IN_SCENE` + narrative rels with `source_quote`). **Streamlit** app = upload screenplay → self-healing extraction (**Pipeline** shows corrections) → **Verify** (warnings + load) → optional **Reconcile** → data exploration. Each pipeline run writes a **:PipelineRun** node (efficiency metrics; in-app telemetry tokens/cost). **Bundled scripts:** `samples/` (Cinema Four full + Ludwig micro-sample, each `.fdx` + companion `.pdf`); root **README** has a **demo walkthrough**.

## Dashboard tabs (`app.py`)

| Tab | Purpose |
|-----|---------|
| **Pipeline** | Upload FDX, run full extraction in-process (parse → lexicon → per-scene LangGraph); live progress; persists **:PipelineRun** to Neo4j after each run |
| **Verify** | Warning cards (title + what Approve does + JSON path); **Approve & Load** applies approved edits then loads Neo4j. Fixer **corrections** live under **Pipeline** |
| **Reconcile** | Optional post-load hygiene: ghost characters + fuzzy **Character** / **Location** name pairs (`reconcile.py`); optional merge with checkbox + pair picker (APOC or manual rewire) |
| **Data out** | Schema card, live Neo4j label/rel counts, fixed recipe Cypher (`data_out.py`), CSV downloads (narrative edges, characters, events) |
| **Pipeline Efficiency Tracking** | Table of **:PipelineRun** rows: telemetry tokens/cost, corrections/warnings counts, agent opt. version |
| **Dashboard** | **Structural load** (MET-01: narrative edges ÷ scenes + entity counts), momentum line (rolling heat), Payoff Matrix (long-gap props), Power shift (top **K** × 3 acts, **K** from `SCRIPTRAG_TOP_CHARACTERS` or default 5), primary-lead regression warning; X/N scenes banner |
| **Investigate** | Natural language → Cypher (`agent.py`); Neo4j graph/chain init is **lazy** — app loads if DB is down; user gets a plain message |

Pipeline tab hidden when `DISABLE_PIPELINE=1` (read-only deployments). **`SCRIPTRAG_DEMO_LAYOUT=1`** reorders tabs so **Data out** sits right after **Verify** (CEO / pipeline demos).

**Resilience (REL-01):** Cached dashboard Neo4j reads log failures and return empty data so charts hit existing `st.info` / `st.warning` paths. Payoff/momentum/power-shift check columns/ids before plotting.

## Act structure (dynamic)

From Neo4j: **`get_script_act_bounds`** in `metrics.py` — `min(:Event.number)` … `max(:Event.number)` split into **three as-equal-as-possible** buckets. Not fixed to "scene 21 / 65"; changes with whatever script is loaded.

## Key metrics (current UI)

- **Momentum heat (per scene):** `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` among entities both `IN_SCENE` to that `Event`; UI smooths with a **3-scene** trailing mean.
- **Payoff props:** First intro (earliest `IN_SCENE` or co-scene `POSSESSES`) vs last `USES` / `CONFLICTS_WITH`; keep if gap **> 10** scenes.
- **Passivity (per act window):** `in / (in + out)` on `CONFLICTS_WITH` + `USES` (incl. incoming `USES` on possessed props), edges attributed to scenes in the act range. **Power shift** uses top **K** characters (env `SCRIPTRAG_TOP_CHARACTERS`, default **5**) by **CONFLICTS_WITH + USES + INTERACTS_WITH** count (both directions).
- **Primary-lead regression:** **Primary** = `SCRIPTRAG_PRIMARY_LEAD_ID` if set, else **rank #1** by that same interaction total (`lead_resolution.resolve_primary_character_id`). If **Act 3 passivity > Act 1** for that id, Dashboard shows a **FATAL ARC** warning. Sidebar **Primary lead** expander shows the resolved id and source.
- **Structural load (MET-01):** `get_structural_load_snapshot` — counts narrative rel instances (`INTERACTS_WITH`, `CONFLICTS_WITH`, `USES`, `LOCATED_IN`, `POSSESSES`) and divides by **:Event** count for an **edges-per-scene** index; also surfaces entity counts. CLI: `uv run python metrics.py --structural-load`. Not a story-quality score.

## Separate: "scene heat" in `metrics.py`

**Distinct** from momentum heat: **unique unordered conflict pairs** in-scene ÷ `IN_SCENE` count (`get_scene_heat`). Still used in CLI / diagnostics; not the same formula as the momentum chart.

## Architecture: engine vs domain

Generic ETL engine lives in `etl_core/` (LangGraph state machine, telemetry, cost tracking). Screenplay-specific models and rules live in `domains/screenplay/`. The engine accepts a pluggable `DomainBundle` so it can be reused for other domains without touching core logic.

The `ingest.py` module exposes `extract_scenes()` — a generator that yields per-scene results, consumed by both the CLI (`main()`) and the Streamlit Pipeline tab.

## Pipeline order (cold start)

`parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` → `streamlit run app.py`

## Secrets

**`.env`** only; never commit. Template: **`.env.example`**.
