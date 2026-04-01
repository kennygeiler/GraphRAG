```
  _ __   ___ _ __ _ __ ___  ___  _ __ ___
 | '_ \ / _ \ '__| '__/ _ \/ _ \| '_ ` _ \
 | | | |  __/ |  | | |  __/ (_) | | | | | |
 |_| |_|\___|_|  |_|  \___|\___/|_| |_| |_|

           screenplay structure you can measure.
```

> final draft → validated graph → neo4j → streamlit. pacing, agency, and long-horizon props—with **verbatim quotes** on every narrative edge. built for writers who want **physics**, not vibes.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-graph-008cc1.svg)](https://neo4j.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B.svg)](https://streamlit.io/)
[![uv](https://img.shields.io/badge/uv-astral-915C83.svg)](https://github.com/astral-sh/uv)
[![Claude](https://img.shields.io/badge/extract-Claude%20%2B%20Instructor-D4A574.svg)](https://github.com/jxnl/instructor)

## the problem

coverage is subjective. “does act two drag?” “is my protagonist reactive?” “did we forget the gun?” you get opinions. you don’t get **reproducible** answers tied to the actual script.

**narrative mri** turns a screenplay into a **queryable graph**: who conflicts with whom, in which scene, with **proof text** on the relationship. from that graph you compute **momentum** (rolling friction), **passivity by act**, and **long-arc props**—and you can **human-in-the-loop** verify scenes before you trust the metrics.

full detail lives in [`strategy.md`](strategy.md). quick context: [`MEMORY.md`](MEMORY.md). agents: [`AGENTS.md`](AGENTS.md).

## table of contents

- [how it works](#how-it-works)
- [the pipeline](#the-pipeline)
- [dashboard](#dashboard)
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
| **ingest** | `ingest.py` + `schema.py` | **per scene**: claude + **instructor** → `SceneGraph`; edges need `source_id`, `target_id`, `type`, **`source_quote`**. |
| **load** | `neo4j_loader.py` | merge `:Character` `:Location` `:Prop` `:Event`, `IN_SCENE`, narrative rels. |
| **analyze** | `metrics.py` | parameterized cypher → momentum, payoff props, passivity windows, etc. |
| **ui** | `app.py` | streamlit + plotly. optional: `agent.py` (ask the graph). |

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
                      ┌─────────────────┐
                      │  INGEST         │◀── claude + instructor
                      │  (per scene)    │     SceneGraph + quotes
                      └────────┬────────┘
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
                      │  metrics.py     │
                      └─────────────────┘
```

**important corrections** vs a lazy “ai tags the script” story:

- **`parser.py` never calls an api.** only **`lexicon.py`** and **`ingest.py`** (and **`agent.py`** for chat) use the model.
- pydantic + instructor **enforce** edge shape; bad structured output **retries or fails**—it doesn’t silently save junk.

## dashboard

wide-layout streamlit. main analytics: **narrative timeline**.

| chart | what it is |
|-------|------------|
| **narrative momentum** | per-scene heat = `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` among co-present entities; **3-scene rolling mean**; shaded area; dashed act boundaries from **equal thirds** of `min..max(:Event.number)` in the db. |
| **payoff matrix** | long-horizon props: first intro vs last `USES` / `CONFLICTS_WITH` separated by **> 10** scene numbers (drops noise). |
| **power shift** | passivity index (in / total on `CONFLICTS_WITH` + `USES` in act windows) for **top 5** characters by interaction volume. **`st.warning`** if configured protagonist (**`zev`** in code) is **more** passive in act 3 than act 1. |

other tabs: **human-in-the-loop** (`hitl.py`), **ask the graph** (`agent.py`), **pipeline engine** (local `uv` chain—hidden in cloud when `DISABLE_PIPELINE_ENGINE=1`).

## quick start

### five minutes: cold run

```bash
git clone https://github.com/kennygeiler/GraphRAG.git
cd GraphRAG
uv sync
cp .env.example .env
# fill ANTHROPIC_API_KEY + NEO4J_* (local desktop, docker, or aura)

uv run python parser.py path/to/script.fdx
uv run python lexicon.py raw_scenes.json
uv run python ingest.py
uv run python neo4j_loader.py
uv run streamlit run app.py
```

open **http://localhost:8501**. ingest **checkpoints**; re-run or `ingest.py --resume` if it stops mid-script.

### optional cli

```bash
uv run python metrics.py --help
uv run python reconcile.py --dry-run
```

## environment variables

copy [`.env.example`](.env.example) → `.env`. **never commit `.env`.**

```env
ANTHROPIC_API_KEY=sk-ant-...
NEO4J_URI=neo4j://localhost:7687    # or neo4j+s://… for aura
NEO4J_USER=neo4j
NEO4J_PASSWORD=...

# hosted / docker: hide pipeline tab (subprocess + disk)
# DISABLE_PIPELINE_ENGINE=1

# optional: langsmith
# LANGCHAIN_API_KEY=...
# LANGCHAIN_TRACING_V2=false
```

## deployment

there is **no** single button that provisions **both** neo4j aura and the app. aura is always a short separate signup. after that, the repo is set up for **docker** + **render blueprint**.

### fastest cloud shape

| step | action |
|------|--------|
| 1 | create [neo4j aura](https://neo4j.com/cloud/) → copy bolt uri + password. |
| 2 | from a machine with `validated_graph.json`: point `.env` at aura → `uv run python neo4j_loader.py`. |
| 3 | push repo → [render](https://dashboard.render.com) **new → blueprint** → select repo → [`render.yaml`](render.yaml) → set secret `NEO4J_*` (+ optional `ANTHROPIC_API_KEY`). |

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

### docker (local or any host)

```bash
docker build -t narrative-mri .
docker run --rm -p 8501:8501 --env-file .env -e DISABLE_PIPELINE_ENGINE=1 narrative-mri
```

`Dockerfile` respects **`PORT`** for render/fly/railway.

### one machine: neo4j + app

```bash
printf '%s\n' 'NEO4J_PASSWORD=your-secure-password' > .env
docker compose -f docker-compose.stack.yml up --build -d
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD='your-secure-password' uv run python neo4j_loader.py
```

### reviewer handoff

- **url only:** deploy dashboard against a pre-loaded aura; share https link.
- **private git:** invite + `.env.example` → `.env` + `uv sync` + `streamlit run app.py`.
- screenplay / json may be sensitive—keep repos private and align with your nda.

## project structure

```
GraphRAG/
├── parser.py              # .fdx → raw_scenes.json (xml only)
├── lexicon.py             # claude → master_lexicon.json
├── ingest.py              # per-scene SceneGraph → validated_graph.json
├── neo4j_loader.py        # json → neo4j
├── schema.py              # pydantic graph contract
├── metrics.py             # cypher analytics
├── app.py                 # streamlit + plotly
├── hitl.py                # draft vs gold scene review
├── agent.py               # nl → cypher (optional)
├── Dockerfile
├── docker-compose.yml     # app → external neo4j / aura
├── docker-compose.stack.yml   # neo4j + app on one host
├── render.yaml            # render blueprint
├── strategy.md            # authoritative project brain
├── MEMORY.md
└── AGENTS.md
```

## license

add a license (e.g. mit) when you publish the repo.
