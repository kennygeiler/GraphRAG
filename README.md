```
  ___         _      _   ___    _    ___
 / __| __ _ _(_)_ __| |_| _ \  /_\  / __|
 \__ \/ _| '_| | '_ \  _|   / / _ \| (_ |
 |___/\__|_| |_| .__/\__|_|_\/_/ \_\\___|
               |_|
           screenplay structure you can measure.
```

> upload a screenplay → self-healing AI extraction → human review → neo4j graph → explore with charts and chat. pacing, agency, and long-horizon props—with **verbatim quotes** on every narrative edge. built for writers who want **physics**, not vibes.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-graph-008cc1.svg)](https://neo4j.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B.svg)](https://streamlit.io/)
[![uv](https://img.shields.io/badge/uv-astral-915C83.svg)](https://github.com/astral-sh/uv)
[![Claude](https://img.shields.io/badge/extract-Claude%20%2B%20Instructor-D4A574.svg)](https://github.com/jxnl/instructor)

## the problem

coverage is subjective. "does act two drag?" "is my protagonist reactive?" "did we forget the gun?" you get opinions. you don't get **reproducible** answers tied to the actual script.

**scriptrag** turns a screenplay into a **queryable graph**: who conflicts with whom, in which scene, with **proof text** on the relationship. from that graph you compute **momentum** (rolling friction), **passivity by act**, and **long-arc props**.

full detail lives in [`strategy.md`](strategy.md). quick context: [`MEMORY.md`](MEMORY.md). agents: [`AGENTS.md`](AGENTS.md).

## why graphrag?

**GraphRAG** (as an idea) means: put **structure first**—entities and relationships in a **graph**—then **retrieve and reason** through that graph (paths, neighborhoods, typed queries), with answers **grounded in evidence** on nodes and edges—not only “similar chunks” from embeddings.

**ScriptRAG** is **GraphRAG for screenplays**: the script becomes a **typed, evidence-backed knowledge graph** in Neo4j (`source_quote` on narrative edges). Dashboards, **Data out** (Cypher + CSV), and **Investigate** (NL→Cypher) all **read through the graph** instead of unstructured text alone. Extraction is **vertical**: a fixed schema, self-healing pipeline, and **Verify** (HITL)—closer to **curated knowledge extraction** than to generic chunk-and-cluster graph builders.

## sample scripts (`samples/`)

Bundled **Final Draft (`.fdx`)** files (plus **`.pdf`** reading companions) live under [`samples/`](samples/):

| Path | Use |
|------|-----|
| [`samples/cinema-four/Cinema_Four.fdx`](samples/cinema-four/Cinema_Four.fdx) | Full-length **Cinema Four** sample (~86 scenes). |
| [`samples/cinema-four/Cinema_Four.pdf`](samples/cinema-four/Cinema_Four.pdf) | Human-readable sidecar; pipeline uses the `.fdx`. |
| [`samples/ludwig/Ludwig.fdx`](samples/ludwig/Ludwig.fdx) | **Short original** 3-scene micro-sample for a **fast** first run. |
| [`samples/ludwig/Ludwig.pdf`](samples/ludwig/Ludwig.pdf) | Explains the micro-sample; not a feature screenplay. |

Details and copy commands: [`samples/README.md`](samples/README.md).

## demo walkthrough

Follow once **Neo4j** and **`.env`** are set ([quick start](#quick-start)):

1. **Pick a script** — For a **quick** pass, use `samples/ludwig/Ludwig.fdx`. For the full development reference, use `samples/cinema-four/Cinema_Four.fdx`.
2. **Install it as the pipeline target** (or upload in the UI instead):
   ```bash
   cp samples/ludwig/Ludwig.fdx target_script.fdx
   ```
3. **Optional demo layout** — Set `SCRIPTRAG_DEMO_LAYOUT=1` in `.env` so tabs read **Verify → Data out → …** for walkthroughs.
4. **Run Streamlit** — `uv run streamlit run app.py` → open **http://localhost:8501**.
5. **Pipeline** — Enable LLM auditors if you want the full story; run the pipeline and read **self-healing corrections** on the same tab when it finishes.
6. **Verify** — Approve or decline each **warning**; then **Approve & load** into Neo4j.
7. **Data out** — Download **CSV** or run **recipe Cypher** to show manipulable graph output.
8. **Dashboard / Investigate** — Charts and NL→Cypher on the loaded graph.

**Reset without losing runs:** Sidebar **Reset dashboard data** clears the screenplay graph and local pipeline JSON but keeps **`:PipelineRun`** rows for **Pipeline Efficiency Tracking**.

## table of contents

- [why graphrag?](#why-graphrag)
- [sample scripts](#sample-scripts-samples)
- [demo walkthrough](#demo-walkthrough)
- [how it works](#how-it-works)
- [the pipeline](#the-pipeline)
- [the editor agent](#the-editor-agent-self-healing-extraction)
- [dashboard](#dashboard)
- [reconciliation](#reconciliation)
- [quick start](#quick-start)
- [environment variables](#environment-variables)
- [deployment](#deployment)
- [project structure](#project-structure)
- [license](#license)

## how it works

### the flow (real modules, not a toy diagram)

| step | module | what happens |
|------|--------|----------------|
| **parse** | `parser.py` | `.fdx` xml → `raw_scenes.json`. **no llm.** |
| **lexicon** | `lexicon.py` | whole script text → claude + pydantic → `master_lexicon.json` (stable `snake_case` ids). |
| **ingest** | `ingest.py` + `schema.py` | **per scene**: claude + **instructor** → `SceneGraph`; two-phase self-healing (deterministic rules → llm auditors); edges need `source_id`, `target_id`, `type`, **`source_quote`**. |
| **load** | `neo4j_loader.py` | merge `:Character` `:Location` `:Prop` `:Event`, `IN_SCENE`, narrative rels. |
| **analyze** | `metrics.py` | parameterized cypher → momentum, payoff props, passivity windows, etc. |
| **ui** | `app.py` | streamlit + plotly: pipeline, verify, reconcile, **data out**, efficiency, dashboard, investigate. |

neo4j does **not** read english. it stores **nodes and edges**. streamlit asks **metrics**; metrics ask **cypher**.

### the pipeline

```
  FDX              PARSER              RAW JSON
   │                  │                    │
   │  screenplay.xml │                    │
   └─────────────────▶│  ElementTree       │
                      │  scenes + text     │
                      └─────────┬──────────┘
                                │
                                ▼
                      ┌─────────────────┐
                      │  LEXICON        │◀── claude + pydantic
                      │  (all scenes)   │     master cast/locs
                      └────────┬────────┘
                               │
                               ▼
                      ┌─────────────────────────────────────┐
                      │  INGEST (per scene)                 │
                      │                                     │
                      │  ┌───────────┐                      │
                      │  │ EXTRACT   │◀── claude+instructor │
                      │  └─────┬─────┘                      │
                      │        ▼                             │
                      │  ┌───────────┐    ┌────────┐        │
                      │  │ VALIDATE  │───▶│ FIXER  │──┐     │
                      │  │ 7 rules   │◀───┘        │  │     │
                      │  └─────┬─────┘   (×3 max)  │  │     │
                      │        │ pass              ◀──┘     │
                      │        ▼                             │
                      │  ┌───────────────────────┐          │
                      │  │ LLM AUDITORS (×3)     │          │
                      │  │ quote fidelity         │          │
                      │  │ completeness           │          │
                      │  │ attribution            │          │
                      │  └─────┬─────────────────┘          │
                      │        │ errors? → fixer (×2 max)   │
                      │        │ warnings → human review    │
                      │        ▼                             │
                      │  validated scene graph               │
                      └────────┬────────────────────────────┘
                               │
                               ▼
               validated_graph.json (checkpointed)
                               │
                               ▼
                      ┌─────────────────┐
                      │  NEO4J LOADER   │
                      │  MERGE graph    │
                      └────────┬────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │  NEO4J          │
                      │  bolt / aura    │
                      └────────┬────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │  STREAMLIT      │
                      │  app.py         │
                      │  (→ metrics.py) │
                      └─────────────────┘
```

**important corrections** vs a lazy "ai tags the script" story:

- **`parser.py` never calls an api.** only **`lexicon.py`** and **`ingest.py`** (and **`agent.py`** for chat) use the model.
- pydantic + instructor **enforce** edge shape; bad structured output **retries or fails**—it doesn't silently save junk.
- the **editor agent** doesn't just check schemas—it runs a **two-phase validation** on every scene. see below.

### the editor agent (self-healing extraction)

every scene goes through two layers of validation before it's accepted. the pipeline keeps looping until the graph is clean or retries are exhausted—you see every correction in the ui.

**phase 1 — deterministic rules** (zero llm cost, instant):

| check | type | what it catches |
|-------|------|-----------------|
| duplicate `LOCATED_IN` | error | character placed in two locations simultaneously |
| dangling edge ids | error | relationship references a node that doesn't exist |
| hallucinated quote | error | `source_quote` not found in the raw scene text (case-insensitive, whitespace-normalized) |
| self-referencing edge | error | `source_id == target_id` |
| relationship-kind validity | error | `LOCATED_IN` → location, `POSSESSES` → character→prop, `USES` → character source |
| lexicon compliance | warning | character/location id not in the master lexicon—flagged for human review |
| duplicate relationships | warning | same `(source, target, type)` tuple appears multiple times in one scene |

errors trigger the **fixer** (up to 3 retries). warnings are saved for human review but don't block the pipeline.

**phase 2 — llm auditor agents** (3 specialized claude calls per scene):

| agent | what it does |
|-------|-------------|
| **quote fidelity** | verifies that each `source_quote` actually *supports* the claimed relationship type—catches misclassification (e.g. "alan sits next to zev" tagged as `CONFLICTS_WITH`) |
| **completeness** | reads the raw scene text and compares it to the extracted graph—finds significant interactions, conflicts, or prop uses the extractor missed |
| **attribution** | verifies `source_id` and `target_id` are the correct entities for the action described in each quote—catches swapped source/target |

audit errors trigger the fixer (up to 2 retries, separate from phase 1). audit warnings go to **verify** for human review.

**cost:** ~$0.03/scene worst case (extraction + fixer + 3 auditors + audit fixer). deterministic checks are free. for an 86-scene script, roughly **$2.50 total**.

## dashboard

wide-layout streamlit. seven primary tabs (plus **pipeline** when enabled):

| tab | what it is |
|-----|------------|
| **pipeline** | upload `.fdx`, run full extraction in-process (live progress, telemetry); saves **:PipelineRun** in neo4j. after a run: **self-healing corrections** (why validation failed, before/after summaries). |
| **verify** | **warnings** only — check title, what **approve** does, json path; **approve & load** applies edits then loads neo4j. |
| **reconcile** | optional **post-load** hygiene: **ghost-like characters** (single scene, no conflicts) and **fuzzy duplicate names** for `:Character` and `:Location`; optional confirmed **merges** (rewire relationships, keep one id). |
| **data out** | **manipulable data** after load: schema card, live node-label / rel-type counts, fixed **recipe cypher** (read-only), **csv** downloads (narrative edges, characters, events). |
| **pipeline efficiency tracking** | table of past runs from neo4j: scenes, corrections, warnings, telemetry tokens/cost, agent opt. version. |
| **dashboard** | **structural load** (MET-01: narrative edges per scene + entity counts), **narrative momentum** (per-scene heat = `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)`, 3-scene rolling mean, dashed act boundaries), **payoff matrix** (long-horizon props > 10 scene gap), **power shift** (passivity index for top **K** characters by act; **K** = `SCRIPTRAG_TOP_CHARACTERS` or default **5**). x/n scenes banner. `st.warning` if **primary lead** regresses (see env vars). |
| **investigate** | ask questions about the script's structure via natural language → cypher (`agent.py`). |

pipeline tab is hidden when `DISABLE_PIPELINE=1` (read-only deployments).

## reconciliation

after loading a graph into neo4j, **`reconcile.py`** helps clean **duplicate entity names** and surface **low-signal characters** (one scene, no `CONFLICTS_WITH`). it compares normalized names with **fuzzy matching** (token sort ratio). a **merge** keeps one node **id** and moves relationships onto it: **APOC `mergeNodes`** when the plugin is available, otherwise a **manual rewire** (same pattern as the interactive cli tool).

**dry-run** (no writes, no prompts):

```bash
uv run python reconcile.py --dry-run
uv run python reconcile.py --dry-run --scope locations
```

**interactive merges** (y/n per fuzzy pair — characters first when `--scope all`, then locations):

```bash
uv run python reconcile.py --min-similarity 0.85
uv run python reconcile.py --scope characters
uv run python reconcile.py --scope locations
```

use **`--scope all`** (default) for ghosts + character pairs + location pairs. requires the same **`NEO4J_*`** env vars as the rest of the app. the **reconcile** tab in streamlit runs the same scan and supports **explicit checkbox + per-pair merge** if you prefer the ui.

### structural load (MET-01)

density-style **production signal** from neo4j: counts of `:Character` / `:Location` / `:Prop` / `:Event` and instances of narrative rel types (`INTERACTS_WITH`, `CONFLICTS_WITH`, `USES`, `LOCATED_IN`, `POSSESSES`). **load index** = narrative edges ÷ scene count. cli:

```bash
uv run python metrics.py --structural-load
```

same numbers appear on the **dashboard** tab (cached with other metrics).

## quick start

### five minutes: cold run

```bash
git clone https://github.com/kennygeiler/GraphRAG.git
cd GraphRAG
uv sync
cp .env.example .env
# fill ANTHROPIC_API_KEY + NEO4J_* (local desktop, docker, or aura)

uv run streamlit run app.py
```

open **http://localhost:8501**. either **upload** a `.fdx` in **pipeline** or use a file already at **`target_script.fdx`** (e.g. `cp samples/ludwig/Ludwig.fdx target_script.fdx`). run **pipeline**, then **verify** warnings and **approve & load** into neo4j. see **[demo walkthrough](#demo-walkthrough)** and [`samples/`](samples/).

### cli alternative (headless)

```bash
uv run python parser.py path/to/script.fdx
uv run python lexicon.py raw_scenes.json
uv run python ingest.py
uv run python neo4j_loader.py
uv run streamlit run app.py
```

ingest **checkpoints**; re-run or `ingest.py --resume` if it stops mid-script.

## environment variables

copy [`.env.example`](.env.example) → `.env`. **never commit `.env`.**

```env
ANTHROPIC_API_KEY=sk-ant-...
NEO4J_URI=neo4j://localhost:7687    # or neo4j+s://… for aura
NEO4J_USER=neo4j
NEO4J_PASSWORD=...

# read-only deployment: hide pipeline tab
# DISABLE_PIPELINE=1

# optional: pin primary lead for dashboard regression (snake_case Character id)
# SCRIPTRAG_PRIMARY_LEAD_ID=

# optional: power-shift chart — top N characters (default 5, max 50)
# SCRIPTRAG_TOP_CHARACTERS=

# optional: demo tab order — verify → data out → reconcile → … (ceo / pipeline storytelling)
# SCRIPTRAG_DEMO_LAYOUT=1

# optional: durable local files (e.g. pipeline log); Render → /var/data + disk
# PERSISTENT_DATA_DIR=/var/data

# optional: langsmith (see .env.example for full list)
# LANGCHAIN_API_KEY=...
# LANGCHAIN_TRACING_V2=false
```

Full list of variables (including LangSmith) is in [`.env.example`](.env.example).

## deployment

there is **no** single button that provisions **both** neo4j aura and the app. aura is always a short separate signup. after that, the repo is set up for **docker** + **render blueprint**.

### fastest cloud shape

| step | action |
|------|--------|
| 1 | create [neo4j aura](https://neo4j.com/cloud/) → copy bolt uri + password. |
| 2 | push repo → [render](https://dashboard.render.com) **new → blueprint** → select repo → [`render.yaml`](render.yaml) → set secret `NEO4J_*` + `ANTHROPIC_API_KEY`. |
| 3 | open the app, upload your `.fdx`, run the pipeline, approve, explore. |

the blueprint uses **starter** + a **1 gb persistent disk** at `/var/data` for optional file artifacts (`PERSISTENT_DATA_DIR=/var/data`). **pipeline efficiency** history is stored as **:PipelineRun** nodes in neo4j (not wiped when you load a screenplay). to use **free** tier instead, edit `render.yaml`: set `plan: free` and remove the `disk` block.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

### docker (local or any host)

```bash
docker build -t scriptrag .
docker run --rm -p 8501:8501 --env-file .env scriptrag
```

`Dockerfile` respects **`PORT`** for render/fly/railway. `docker compose` mounts a named volume at `/var/data` for **`PERSISTENT_DATA_DIR`**. pipeline efficiency rows live in neo4j as **:PipelineRun** (telemetry tokens/cost from the app run).

### one machine: neo4j + app

```bash
printf '%s\n' 'NEO4J_PASSWORD=your-secure-password' > .env
docker compose -f docker-compose.stack.yml up --build -d
```

open **http://localhost:8501**, upload your screenplay, and run the pipeline from the browser.

### reviewer handoff

- **url only:** deploy dashboard against a pre-loaded aura; share https link.
- **private git:** invite + `.env.example` → `.env` + `uv sync` + `streamlit run app.py`.
- screenplay / json may be sensitive—keep repos private and align with your nda.

## project structure

```
GraphRAG/
├── samples/                   # bundled .fdx (+ pdf companions); see samples/README.md
│   ├── cinema-four/
│   └── ludwig/
├── etl_core/                  # domain-agnostic self-healing ETL engine
│   ├── config.py              #   .env + langsmith bootstrap
│   ├── state.py               #   langgraph ETLState (tokens, cost, audit)
│   ├── telemetry.py           #   anthropic pricing + accumulate_usage
│   ├── errors.py              #   MaxRetriesError
│   └── graph_engine.py        #   langgraph: extract → validate → fix → audit → audit_fix
├── domains/
│   └── screenplay/            # screenplay-specific domain plug-in
│       ├── schemas.py         #   re-exports SceneGraph, Relationship
│       ├── rules.py           #   7 deterministic checks (no AI)
│       ├── auditors.py        #   3 LLM auditor agents (quote fidelity, completeness, attribution)
│       └── adapter.py         #   DomainBundle wiring LLM + rules + auditors
├── parser.py                  # .fdx → raw_scenes.json (xml only)
├── lexicon.py                 # claude → master_lexicon.json
├── ingest.py                  # per-scene extraction (exports extract_scenes generator)
├── extraction_llm.py          # anthropic + instructor calls (with usage)
├── extraction_graph.py        # thin adapter → etl_core pipeline
├── neo4j_loader.py            # json → neo4j (exports load_entries)
├── schema.py                  # pydantic graph contract
├── metrics.py                 # cypher analytics
├── data_out.py                # schema card, recipe queries, csv-oriented exports for data out tab
├── app.py                     # streamlit: pipeline, verify, reconcile, data out, efficiency, dashboard, investigate
├── reconcile.py               # fuzzy duplicate + ghost scan; character/location merge (cli + tab)
├── lead_resolution.py         # primary lead + top-K from graph; SCRIPTRAG_* overrides
├── pipeline_runs.py           # :PipelineRun metrics in neo4j
├── cleanup_review.py          # plain-english correction summaries + warning paths
├── agent.py                   # nl → cypher
├── Dockerfile
├── docker-compose.yml         # app → external neo4j / aura
├── docker-compose.stack.yml   # neo4j + app on one host
├── render.yaml                # render blueprint
├── strategy.md                # authoritative project brain
├── MEMORY.md
└── AGENTS.md
```

## license

add a license (e.g. mit) when you publish the repo.
