```
  ___         _      _   ___    _    ___
 / __| __ _ _(_)_ __| |_| _ \  /_\  / __|
 \__ \/ _| '_| | '_ \  _|   / / _ \| (_ |
 |___/\__|_| |_| .__/\__|_|_\/_/ \_\\___|
               |_|
           screenplay structure you can measure.
```

> upload a screenplay → self-healing AI extraction → human review → neo4j graph → export and query structured data. pacing, agency, and long-horizon props—with **verbatim quotes** on every narrative edge. built for writers who want **physics**, not vibes.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-graph-008cc1.svg)](https://neo4j.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B.svg)](https://streamlit.io/)
[![uv](https://img.shields.io/badge/uv-astral-915C83.svg)](https://github.com/astral-sh/uv)
[![Claude](https://img.shields.io/badge/extract-Claude%20%2B%20Instructor-D4A574.svg)](https://github.com/jxnl/instructor)
[![Live demo — Render](https://img.shields.io/badge/live%20demo-scriptrag.onrender.com-5a67d8.svg)](https://scriptrag.onrender.com/)

**Deployed app:** [https://scriptrag.onrender.com/](https://scriptrag.onrender.com/) (Streamlit; set secrets on Render per [deployment](#deployment)).

## the problem

coverage is subjective. "does act two drag?" "is my protagonist reactive?" "did we forget the gun?" you get opinions. you don't get **reproducible** answers tied to the actual script.

**scriptrag** turns a screenplay into a **queryable graph**: who conflicts with whom, in which scene, with **proof text** on the relationship. once the graph is in neo4j, **`metrics.py` / CLI** give reproducible **momentum**, **passivity-by-act windows**, **long-arc (payoff) props**, structural load, and more — the streamlit app focuses on extraction, audit & verify (HITL), exports, and efficiency, not those charts.

full detail lives in [`strategy.md`](strategy.md). quick context: [`MEMORY.md`](MEMORY.md). agents: [`AGENTS.md`](AGENTS.md).

## why graphrag?

**GraphRAG** (as an idea) means: put **structure first**—entities and relationships in a **graph**—then **retrieve and reason** through that graph (paths, neighborhoods, typed queries), with answers **grounded in evidence** on nodes and edges—not only “similar chunks” from embeddings.

**ScriptRAG** is **GraphRAG for screenplays**: the script becomes a **typed, evidence-backed knowledge graph** in Neo4j (`source_quote` on narrative edges). **Data out** (recipe Cypher + CSV) and **`metrics.py`** (CLI) **read through the graph** instead of unstructured text alone. Extraction is **vertical**: a fixed schema, self-healing pipeline, optional **gated semantic auto-apply**, and **Audit & Verify** (HITL on remaining warnings)—closer to **curated knowledge extraction** than to generic chunk-and-cluster graph builders.

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
3. **Optional demo layout** — Set `SCRIPTRAG_DEMO_LAYOUT=1` in `.env` so sections read **Audit & Verify → Data out → Reconcile → …** for walkthroughs.
4. **Run Streamlit** — `uv run streamlit run app.py` → open **http://localhost:8501**.
5. **Pipeline** — Run extraction in one go (live progress in the status panel). When auditors are on, high-confidence patches may **auto-apply** (`auditor_auto_apply`); everything else is queued for HITL. When finished, read **self-healing corrections** and the optional **semantic audit decisions** table on the same tab.
6. **Audit & Verify** — Approve or decline each **warning**; then **Approve & load** into Neo4j (graph JSON already reflects any auto-applied audit patches).
7. **Data out** — Pick a **recipe query** or download **CSV**; the app stays on this section when you change the query (horizontal **Section** control, not browser tabs).
8. **Pipeline Efficiency Tracking** — Inspect **:PipelineRun** history (tokens, cost, warnings).

**Reset without losing runs:** Sidebar **Reset graph data** clears the screenplay graph and local pipeline JSON but keeps **`:PipelineRun`** rows for **Pipeline Efficiency Tracking**.

## table of contents

- [why graphrag?](#why-graphrag)
- [sample scripts](#sample-scripts-samples)
- [demo walkthrough](#demo-walkthrough)
- [how it works](#how-it-works)
- [the pipeline](#the-pipeline)
- [the editor agent](#the-editor-agent-self-healing-extraction)
- [streamlit app](#streamlit-app)
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
| **ingest** | `ingest.py` + `schema.py` | **per scene**: claude + **instructor** → `SceneGraph`; **validate** (pydantic + 7 rules) ⇄ **fixer** (llm, up to 3); **optional** llm auditors (3 calls, one pass) + **`process_semantic_audit`**: gated **auto-apply** patches the graph; remaining findings → **audit & verify** warnings (**no** `audit_fixer` llm loop). edges need `source_id`, `target_id`, `type`, **`source_quote`**. |
| **load** | `neo4j_loader.py` | merge `:Character` `:Location` `:Prop` `:Event`, `IN_SCENE`, narrative rels. invoked from streamlit **audit & verify** (**approve & load**) or headless after `ingest.py`. |
| **analyze** | `metrics.py` | **cli / library** — parameterized cypher (momentum, payoff props, passivity windows, structural load, etc.). **not** used by streamlit tabs. |
| **ui** | `app.py` | streamlit: pipeline, audit & verify, reconcile, **data out**, pipeline efficiency (section radio + cached neo4j reads). **data out** uses `data_out.py` cypher, not `metrics.py`. |

neo4j does **not** read english. it stores **nodes and edges**. after load, **data out** and **reconcile** query neo4j directly; **`metrics.py`** is for terminal analytics and custom scripts.

### the pipeline

**file flow:** `.fdx` → `parser.py` → `raw_scenes.json` → `lexicon.py` → `master_lexicon.json` → `ingest.py` (per-scene langgraph) → **`validated_graph.json`** on disk. a streamlit **pipeline** run also holds results in **`st.session_state`** until reset.

**per scene (`etl_core/graph_engine.py` + `domains/screenplay/`):** **extract** → **validate** (pydantic + 7 rules) ⇄ **fixer** (llm repair, up to **3** validate/fix rounds). if **llm auditors** are enabled (`enable_audit=True`): after validate passes → **audit** (three claude calls, bundled) → **`audit_post_process`** (`process_semantic_audit`): high-confidence, gated patches update **`current_json`** and append **`auditor_auto_apply`** to **`audit_trail`**; decision rows append to **`audit_decisions.jsonl`** (local, gitignored); remaining findings merge into **`warnings`** for **audit & verify** (including former auditor “errors”, promoted to warnings). **no** `audit_fixer` loop. if auditors are **disabled**, validate pass **ends** the graph — **no** audit step.

**into neo4j:** extraction **does not** load neo4j. **streamlit:** **audit & verify → approve & load** → `neo4j_loader.load_entries()`. **headless:** `uv run python neo4j_loader.py` after `ingest.py`.

**downstream:** **data out** / **reconcile** / **efficiency** use `data_out.py`, `reconcile.py`, `pipeline_runs.py`. **`metrics.py`** is **cli-only** from the app’s perspective.

```
  FDX              PARSER              RAW JSON
   │                  │                    │
   └─────────────────▶│  ElementTree       │
                      │  scenes + text     │
                      └─────────┬──────────┘
                                ▼
                      ┌─────────────────┐
                      │  LEXICON        │◀── claude + pydantic
                      │  (all scenes)   │
                      └────────┬────────┘
                               ▼
                      ┌─────────────────────────────────────┐
                      │  INGEST (per scene, LangGraph)      │
                      │                                     │
                      │  EXTRACT ──▶ VALIDATE ⇄ FIXER       │
                      │                    (≤3)            │
                      │                      │ pass         │
                      │                      ▼             │
                      │              [if auditors ON]       │
                      │              AUDIT (3 calls)        │
                      │              → interpret: apply or   │
                      │                HITL warnings         │
                      │                      ▼             │
                      │              scene graph JSON       │
                      └─────────────────┬───────────────────┘
                                        ▼
                         validated_graph.json (+ UI session)
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              ▼                                                    ▼
   streamlit AUDIT & VERIFY → neo4j_loader                  headless neo4j_loader.py
   (approve & load)                                         after ingest.py
              │                                                    │
              └─────────────────────────┬──────────────────────────┘
                                        ▼
                                     NEO4J
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              ▼                                                    ▼
   app: data out · reconcile · efficiency                   metrics.py (CLI)
```

**important corrections** vs a lazy "ai tags the script" story:

- **`parser.py` never calls an api.** only **`lexicon.py`** and **`ingest.py`** use the model for extraction.
- pydantic + instructor **enforce** edge shape; bad structured output **retries or fails**—it doesn't silently save junk.
- **deterministic validation + optional fixer** run on every scene; **llm auditors** are an **optional** second phase when enabled. see below.

### the editor agent (self-healing extraction)

every scene runs **deterministic validation** (and the **fixer** llm when validation fails) until pass or max retries—you see corrections in the **pipeline** tab. **llm auditors** are a **separate pass** after validation succeeds in the **streamlit** path (**always on**). headless **`ingest.extract_scenes`** defaults to `enable_audit=True` (pass `enable_audit=False` from code if you need to skip auditors).

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

**phase 2 — llm auditor agents** (optional; **3** specialized claude calls per scene when enabled):

| agent | what it does |
|-------|-------------|
| **quote fidelity** | verifies that each `source_quote` actually *supports* the claimed relationship type—catches misclassification (e.g. "alan sits next to zev" tagged as `CONFLICTS_WITH`). prompts reserve **error** for hard failures (quote missing from scene text, wrong entities); debatable edge types default to **warning**. |
| **completeness** | reads the raw scene text and compares it to the extracted graph—finds significant interactions, conflicts, or prop uses the extractor missed |
| **attribution** | verifies `source_id` and `target_id` are the correct entities for the action described in each quote—catches swapped source/target. the model may propose structured **patches** (`mapping_decision`, `patch_*`, `confidence`); gated patches can **auto-apply**; the rest surface as **audit & verify** warnings. |

after the audit **llm** calls, **`process_semantic_audit`** runs: eligible high-confidence patches mutate the scene graph and **`audit_trail`** (`auditor_auto_apply`); remaining findings (and promoted model **errors**) go to **`warnings`** for **audit & verify**. there is **no** second llm **`audit_fixer`** pass. phase-1 **fixer** still handles only pydantic + deterministic rule failures.

**telemetry “cost” (usd):** the app sums **estimated** spend from token counts × a static **$/1m** table in **`etl_core/telemetry.py`** (`estimate_cost`) — **not** your anthropic invoice. actual spend depends on model, pricing changes, and retries. **pipeline** / **efficiency** show those estimates per run. with auditors on, scenes that previously burned **audit fixer** tokens no longer do — totals are typically **lower** than older runs for the same script. rough **order-of-magnitude** examples (86 scenes, typical lengths): **~$0.01/scene** without auditors vs **~$0.03/scene** with auditors — treat as **ballparks**, not guarantees.

## streamlit app

wide-layout streamlit. a **horizontal section radio** lists **Pipeline** (when enabled), **Audit & Verify**, **Reconcile**, **Data out**, and **Pipeline Efficiency Tracking**. this avoids `st.tabs()` snapping back to the first tab when widgets inside **Data out** rerun the script.

| section | what it is |
|---------|------------|
| **pipeline** | upload `.fdx`, parse + lexicon, then **full in-process** per-scene extraction (single **Run Pipeline** click; live progress). telemetry; saves **:PipelineRun** when the run completes. after a run: **self-healing corrections** (**fixer** + **`auditor_auto_apply`** before/after summaries) and optional **semantic audit decisions** table. |
| **audit & verify** | **HITL warnings** — filter/sort/bulk where supported; **approve & load** applies approved edits then loads neo4j. graph JSON from the pipeline already includes any **auto-applied** semantic patches. |
| **reconcile** | optional **post-load** hygiene: **ghost-like characters** (single scene, no conflicts) and **fuzzy duplicate names** for `:Character` and `:Location`; optional confirmed **merges** (rewire relationships, keep one id). |
| **data out** | **manipulable data** after load: schema card, live node-label / rel-type counts, fixed **recipe cypher** (read-only), **csv** downloads (narrative edges, characters, events). |
| **pipeline efficiency tracking** | past **:PipelineRun** rows: totals + E/F/A when **Token Agent** is **v1+** (**v0** = **N/A** for stage columns). **v2** = Phase 1 payload efficiency. log: **`Telemetry.md`**. |

**pipeline** is hidden when `DISABLE_PIPELINE=1` (read-only deployments).

structural analytics (momentum, payoff props, passivity windows, structural load MET-01) live in **`metrics.py`** and the CLI — not in the streamlit UI.

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

open **http://localhost:8501**. either **upload** a `.fdx` in **pipeline** or use a file already at **`target_script.fdx`** (e.g. `cp samples/ludwig/Ludwig.fdx target_script.fdx`). run **pipeline**, then open **audit & verify** for any remaining warnings and **approve & load** into neo4j. see **[demo walkthrough](#demo-walkthrough)** and [`samples/`](samples/).

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

# optional: programmatic / lead_resolution.py — primary id and cohort size (not used by streamlit UI)
# SCRIPTRAG_PRIMARY_LEAD_ID=
# SCRIPTRAG_TOP_CHARACTERS=

# optional: demo section order — audit & verify → data out → reconcile → … (ceo / pipeline storytelling)
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

- **url only:** deploy the app against a pre-loaded aura; share https link.
- **private git:** invite + `.env.example` → `.env` + `uv sync` + `streamlit run app.py`.
- screenplay / json may be sensitive—keep repos private and align with your nda.

## project structure

```
GraphRAG/
├── LICENSE                    # MIT
├── samples/                   # bundled .fdx (+ pdf companions); see samples/README.md
│   ├── cinema-four/
│   └── ludwig/
├── tools/                     # optional Neo4j QA scripts; see tools/README.md
│   ├── debug_export.py
│   ├── qa_entities.py
│   ├── producer_notes.py
│   └── README.md
├── etl_core/                  # domain-agnostic self-healing ETL engine
│   ├── config.py              #   .env + langsmith bootstrap
│   ├── state.py               #   langgraph ETLState (tokens, cost, audit, audit_decisions, lexicon_ids)
│   ├── audit_policy.py        #   semantic audit auto-apply thresholds / flags
│   ├── telemetry.py           #   anthropic pricing + accumulate_usage
│   ├── errors.py              #   MaxRetriesError
│   └── graph_engine.py        #   langgraph: extract → validate ⇄ fix; optional audit + audit_post_process
├── domains/
│   └── screenplay/            # screenplay-specific domain plug-in
│       ├── schemas.py         #   re-exports SceneGraph, Relationship
│       ├── rules.py           #   7 deterministic checks (no AI)
│       ├── auditors.py        #   3 LLM auditor agents + structured patch fields
│       ├── audit_patch.py     #   validate/apply semantic patches
│       ├── audit_pipeline.py  #   process_semantic_audit; audit_decisions.jsonl
│       └── adapter.py         #   DomainBundle: extract/fix + rules + audit_llm + audit_post_process
├── parser.py                  # .fdx → raw_scenes.json (xml only)
├── lexicon.py                 # claude → master_lexicon.json
├── ingest.py                  # per-scene extraction (extract_scenes, run_single_scene_extraction, SceneResult)
├── extraction_llm.py          # anthropic + instructor calls (with usage)
├── extraction_graph.py        # thin adapter → etl_core pipeline
├── neo4j_loader.py            # json → neo4j (exports load_entries)
├── schema.py                  # pydantic graph contract
├── metrics.py                 # cypher analytics
├── data_out.py                # schema card, recipe queries, csv-oriented exports for data out tab
├── app.py                     # streamlit: pipeline, audit & verify, reconcile, data out, efficiency
├── reconcile.py               # fuzzy duplicate + ghost scan; character/location merge (cli + tab)
├── lead_resolution.py         # optional SCRIPTRAG_* helpers for programmatic use
├── pipeline_runs.py           # :PipelineRun metrics in neo4j
├── cleanup_review.py          # plain-english correction summaries + warning paths
├── Dockerfile                 # production image; entrypoint runs streamlit
├── docker-compose.yml         # app → external neo4j / aura
├── docker-compose.stack.yml   # neo4j + app on one host
├── render.yaml                # render blueprint
├── strategy.md                # authoritative project brain
├── MEMORY.md
└── AGENTS.md
```

## license

[MIT](LICENSE). Copyright (c) 2026 Kenny Geiler.

To show the live URL on the GitHub repo **About** panel: **Settings → General → Website** → `https://scriptrag.onrender.com/`.
