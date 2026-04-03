```
  ___         _      _   ___    _    ___
 / __| __ _ _(_)_ __| |_| _ \  /_\  / __|
 \__ \/ _| '_| | '_ \  _|   / / _ \| (_ |
 |___/\__|_| |_| .__/\__|_|_\/_/ \_\\___|
               |_|
           screenplay structure you can measure.
```

# ScriptRAG

0 lines of human code. Cursor + Claude Opus 4.6 + GSD Skills add-on + Kenny's Brain

**ScriptRAG** is an end-to-end **GraphRAG** system for **Final Draft** screenplays: it parses `.fdx`, uses **LLM + structured output** to extract a **validated narrative graph**, optional **human-in-the-loop** review, loads **Neo4j**, and exposes **recipe Cypher**, **CSV exports**, and a **CLI metrics** layer. Narrative edges carry **`source_quote`**—evidence tied to the script, not inferred “vibes.”

**Why I built it:** This repo started as a **sandbox** to experiment with **structured data extraction** from creative text, **token and dollar behavior** across pipeline stages, and **efficiency tradeoffs** (prompt shape, model choice per stage, what to automate vs send to a human). The screenplay domain is the vehicle; the thread running through the work is **measurable pipelines**: know what ran, what it cost, and what changed when you ship a new extraction or audit strategy.

**Pipeline Efficiency Tracking** is the operational side of that experiment. Each finished run writes a **`:PipelineRun`** in Neo4j with **total** and **per-stage** buckets (**extract / fix / audit**), plus a **Token Agent** label (**`v0`–`v3`**) that maps to **`telemetry_version`**—so a row from last month is comparable to one from today. [**`Telemetry.md`**](Telemetry.md) is the version changelog and A/B log (e.g. Ludwig micro-sample: Phase 1 vs Phase 2 audit routing). **Reserved `v4` / `v5`** in the UI describe the next roadmap waves before the code bumps the constant.

**At scale**, that discipline matters: many scripts or many re-runs make **“roughly cheaper”** useless without **attribution** (which stage moved, after which deploy). Persisting runs in the graph (and keeping them when you **clear** screenplay data) supports **budget planning**, **regression checks** after prompt or model changes, and **comparing** behavior across **versions** the same way you compare **accuracy**—with rows you can filter and export. The roadmap in **`strategy.md`** ties each Token Agent bump to a concrete phase (instrumentation → payload shrink → audit model routing → future conditional audit, caching, etc.).

| | |
|--|--|
| **Live demo** | [scriptrag.onrender.com](https://scriptrag.onrender.com/) |
| **Repo** | [github.com/kennygeiler/GraphRAG](https://github.com/kennygeiler/GraphRAG) |
| **Deep dive** | [`strategy.md`](strategy.md) (architecture, metrics, roadmap) · [`AGENTS.md`](AGENTS.md) (conventions) · [`Telemetry.md`](Telemetry.md) (pipeline cost / Token Agent versions) |

[![100% Agentic Codebase](https://img.shields.io/badge/100%25-Agentic%20codebase-5a67d8.svg)](https://github.com/kennygeiler/GraphRAG)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Neo4j](https://img.shields.io/badge/Neo4j-graph-008cc1.svg)](https://neo4j.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B.svg)](https://streamlit.io/)
[![uv](https://img.shields.io/badge/uv-astral-915C83.svg)](https://github.com/astral-sh/uv)
[![Claude + Instructor](https://img.shields.io/badge/extract-Claude%20%2B%20Instructor-D4A574.svg)](https://github.com/jxnl/instructor)
[![LangGraph](https://img.shields.io/badge/LangGraph-ETL-111827.svg)](https://github.com/langchain-ai/langgraph)

---

## What this project is

1. **Ingestion pipeline** — `.fdx` → scenes JSON → script-wide **lexicon** (stable entity IDs) → **per-scene extraction** through a **LangGraph** state machine (`etl_core/graph_engine.py`).
2. **Quality gates** — **Pydantic** schema + **deterministic rules** (quotes, referential integrity, relationship kinds). Failures invoke a **fixer** LLM (bounded retries), not silent bad data.
3. **Semantic audit** — Three specialized **auditor** LLM passes (quote fidelity, completeness, attribution); **gated auto-apply** can patch the graph; the rest go to **Audit & Verify** for humans.
4. **Persistence & ops** — **Neo4j** graph load, optional **reconciliation** (fuzzy duplicates / ghosts), **`:PipelineRun`** telemetry (tokens + estimated USD by stage), **Streamlit** operator UI.
5. **Downstream analytics** — **`metrics.py`** (library + CLI) for reproducible structural metrics; **not** embedded in Streamlit charts today.

This is closer to **curated knowledge extraction** (fixed schema, validation, HITL) than to “chunk the script and embed everything.”

---

## Why it’s valuable

| For a product or story team | For engineering review |
|------------------------------|-------------------------|
| **Reproducible** graph queries over **who** interacts, **conflicts**, **uses** props, **where**—with **verbatim quotes** on edges. | **Separation of concerns**: XML parse (no LLM) vs lexicon vs per-scene graph ETL vs Neo4j load vs analytics. |
| **Human control** where the model is uncertain: **Audit & Verify** on structured warnings, not opaque blobs. | **Domain plug-in** pattern: `etl_core` is domain-agnostic; `domains/screenplay/` supplies schema, rules, auditors. |
| **Run history**: per-run **extract / fix / audit** tokens and **estimated** cost in Neo4j, keyed by **Token Agent** version (`Telemetry.md`). | **Parameterized Cypher** only in loaders and metrics; no string-built queries from user text in analytics paths. |

**Problem it solves:** Coverage and structure questions (“is the lead passive in Act II?”, “did we pay off the gun?”) usually get subjective answers. ScriptRAG grounds discussion in **typed graph facts** and **quoted evidence**, then optionally quantifies structure via **`metrics.py`**.

---

## Tech stack

| Layer | Choices |
|--------|---------|
| **Language & tooling** | Python **3.12**, **[uv](https://github.com/astral-sh/uv)** (`uv sync`, `uv run …`) |
| **LLM** | **Anthropic** API, structured outputs via **[Instructor](https://github.com/jxnl/instructor)** + **Pydantic v2** |
| **Orchestration** | **[LangGraph](https://github.com/langchain-ai/langgraph)** (`StateGraph`: extract → validate ⇄ fix → optional audit) |
| **Graph DB** | **Neo4j** 6.x driver; labels `Character`, `Location`, `Prop`, `Event` + `IN_SCENE` + narrative rel types |
| **UI** | **Streamlit** (wide layout; horizontal section radio—not tabs—to avoid rerun UX bugs) |
| **Optional** | **LangSmith** tracing (env-driven), **Docker** / **Render** blueprint |

Declared dependencies: see [`pyproject.toml`](pyproject.toml).

---

## End-to-end flow

```
Final Draft (.fdx)
      → parser.py (XML only, no API)
      → raw_scenes.json
      → lexicon.py (Claude + Pydantic → master_lexicon.json)
      → ingest.py / extraction_graph.py (per scene: LangGraph)
      → validated_graph.json + session state (Streamlit)
      → Audit & Verify → Approve & load → neo4j_loader.py → Neo4j
      → Data out (recipe Cypher, CSV) · Reconcile · PipelineRun telemetry
      → metrics.py (CLI / import) for structural analytics
```

Detailed module table and ASCII diagram: [**How it works**](#how-it-works) below.

---

## Quick start

```bash
git clone https://github.com/kennygeiler/GraphRAG.git
cd GraphRAG
uv sync
cp .env.example .env
# Set ANTHROPIC_API_KEY and NEO4J_* (local, Docker, or Neo4j Aura)

uv run streamlit run app.py
```

Open **http://localhost:8501**. Upload a `.fdx` in **Pipeline** or copy a sample to `target_script.fdx` (e.g. `cp samples/ludwig/Ludwig.fdx target_script.fdx`). Run **Pipeline**, then **Audit & Verify** → **Approve & load** → **Data out**.

**Headless path:** `parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` (see [CLI alternative](#cli-alternative-headless)).

**Environment variables:** [`.env.example`](.env.example) — never commit `.env`.

---

## Demo walkthrough (Streamlit)

1. **Sample scripts** — [`samples/`](samples/) (e.g. **Ludwig** micro-sample for speed, **Cinema Four** for scale). Pipeline consumes **`.fdx`** only.
2. **Optional** `SCRIPTRAG_DEMO_LAYOUT=1` — section order **Audit & Verify → Data out → Reconcile → …** for demos.
3. **Pipeline** — Full in-process run; **:PipelineRun** saved when Neo4j is reachable; **self-healing corrections** + optional audit decisions table.
4. **Audit & Verify** — HITL on warnings; **Approve & load** persists to Neo4j.
5. **Data out** — Schema, live counts, recipe Cypher, CSV downloads.
6. **Pipeline Efficiency Tracking** — Table of **`:PipelineRun`** rows: timestamps, script name, scene counts, warnings, **total** tokens and estimated $, **E / F / A** columns when Token Agent is **v1+** (**v0** = legacy, stage split **N/A**). Expander text mirrors **`TOKEN_AGENT_SUMMARY_MD`** in `etl_core/telemetry.py`. Full version notes and benchmark tables: [`Telemetry.md`](Telemetry.md).

**Clear screenplay without losing telemetry:** Sidebar **Clear screenplay & pipeline files** wipes Neo4j story data and local pipeline JSON; **`:PipelineRun`** rows remain.

---

## Streamlit app (sections)

| Section | Purpose |
|---------|---------|
| **Pipeline** | Upload `.fdx`, parse, lexicon, per-scene LangGraph extraction, live progress, session metrics, corrections trail. Hidden if `DISABLE_PIPELINE=1`. |
| **Audit & Verify** | Filter/sort/bulk warnings; approve or decline; **Approve & load** → Neo4j; decision log export. |
| **Reconcile** | Post-load: ghost-style characters, fuzzy **Character** / **Location** pairs; optional merge (with acknowledgment). |
| **Data out** | Schema card, label/rel counts, read-only recipe queries (`data_out.py`), CSV exports. |
| **Pipeline Efficiency Tracking** | **:PipelineRun** history: **Token Agent** = shipped **`telemetry_version`** (**v1–v3** today); **v4** / **v5** described in-app as upcoming roadmap only. Use it to compare runs over time and after deploys. |

Structural analytics (**passivity**, **payoff props**, **structural load** MET-01, etc.) live in **`metrics.py`** and the CLI—not in Streamlit tabs.

---

## How it works

### Module responsibilities

| Step | Module | What happens |
|------|--------|----------------|
| **Parse** | `parser.py` | `.fdx` XML → `raw_scenes.json`. **No LLM.** |
| **Lexicon** | `lexicon.py` | Full script → Claude + Pydantic → `master_lexicon.json` (stable IDs). |
| **Extract** | `ingest.py`, `extraction_graph.py`, `etl_core/graph_engine.py`, `domains/screenplay/` | Per scene: **extract** → **validate** ⇄ **fixer** (≤ **3** attempts) → optional **audit** (3 LLM calls) → **`process_semantic_audit`** (gated patches + warnings). |
| **Load** | `neo4j_loader.py` | Merge graph into Neo4j from Streamlit **Approve & load** or CLI. |
| **Analyze** | `metrics.py` | Parameterized Cypher; CLI entrypoints for structural metrics. |
| **UI** | `app.py`, `cleanup_review.py`, `data_out.py`, `pipeline_runs.py`, `reconcile.py` | Operator workflows and exports. |

### Per-scene graph engine

- **Validate** runs **Pydantic** `SceneGraph` validation, then **business rules** in `domains/screenplay/rules.py`:
  - **Five error-class checks** (trigger **fixer**): e.g. dangling edge IDs, self-edges, invalid relationship kinds for node types, duplicate `LOCATED_IN` per character, **hallucinated** `source_quote` (normalized substring vs scene text).
  - **Two warning-only checks** (HITL later): lexicon drift, duplicate relationship tuples.
- **Audit** (when enabled): three auditors, **Haiku-first** / Sonnet fallback on audit calls (`extraction_llm.py`); **no** separate LLM “audit fixer” loop—interpretation is **Python** (`audit_pipeline.py`).
- **Into Neo4j:** extraction does **not** auto-load the DB. **Streamlit:** **Audit & Verify → Approve & load**. **CLI:** `neo4j_loader.py` after ingest.

### File-flow diagram

```
  FDX              PARSER              RAW JSON
   │                  │                    │
   └─────────────────▶│  ElementTree       │
                      │  scenes + text     │
                      └─────────┬──────────┘
                                ▼
                      ┌─────────────────┐
                      │  LEXICON        │◀── Claude + Pydantic
                      │  (all scenes)   │
                      └────────┬────────┘
                               ▼
                      ┌─────────────────────────────────────┐
                      │  INGEST (per scene, LangGraph)      │
                      │  EXTRACT → VALIDATE ⇄ FIXER (≤3)    │
                      │       pass → [AUDIT: 3 LLM + interpret]
                      │       → scene graph JSON + warnings   │
                      └─────────────────┬───────────────────┘
                                        ▼
                         validated_graph.json (+ UI session)
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              ▼                                                    ▼
   Streamlit Audit & Verify → neo4j_loader                  Headless neo4j_loader.py
              │                                                    │
              └─────────────────────────┬──────────────────────────┘
                                        ▼
                                     NEO4J
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              ▼                                                    ▼
   Data out · Reconcile · Efficiency (:PipelineRun)          metrics.py (CLI)
```

---

## Deterministic validation vs semantic audit

**Phase 1 — rules (no LLM)**  

| Check | Role | What it catches |
|-------|------|-----------------|
| Duplicate `LOCATED_IN` | Error | Same character placed in multiple locations in one extraction. |
| Dangling edge IDs | Error | `source_id` / `target_id` not in node list. |
| Hallucinated quote | Error | `source_quote` not found in raw scene text (normalized). |
| Self-referencing edge | Error | `source_id == target_id`. |
| Relationship-kind validity | Error | e.g. `LOCATED_IN` target must be **Location**; `USES` source **Character**. |
| Lexicon compliance | Warning | Character/location ID not in master lexicon. |
| Duplicate relationships | Warning | Duplicate `(source, target, type)` in one scene. |

**Phase 2 — LLM auditors (optional; on in Streamlit pipeline)**  

| Agent | Role |
|-------|------|
| **Quote fidelity** | Whether the quote supports the claimed relationship type. |
| **Completeness** | Omitted significant interactions / props / conflicts. |
| **Attribution** | Correct `source_id` / `target_id`; may propose structured patches for gated auto-apply. |

After auditor calls, **`process_semantic_audit`** applies allowed patches under **`etl_core/audit_policy.py`**, logs **`audit_decisions.jsonl`**, and pushes the rest to **Audit & Verify**.

**Telemetry:** Estimated USD is from **`etl_core/telemetry.py`** (static $/1M token table)—not an invoice. Per-stage **extract / fix / audit** buckets and Token Agent versions are documented in [`Telemetry.md`](Telemetry.md).

---

## Reconciliation

`reconcile.py` scans Neo4j for **fuzzy duplicate** character/location names and **ghost-like** characters (single scene, no conflicts). Merges rewire relationships (APOC `mergeNodes` when available). **Dry-run:**

```bash
uv run python reconcile.py --dry-run
```

Streamlit **Reconcile** mirrors the scan with explicit merge controls.

**Structural load (MET-01):** `uv run python metrics.py --structural-load`

---

## CLI alternative (headless)

```bash
uv run python parser.py path/to/script.fdx
uv run python lexicon.py raw_scenes.json
uv run python ingest.py
uv run python neo4j_loader.py
```

Ingest supports checkpoints / resume (`ingest.py --resume`).

---

## Environment variables

See [`.env.example`](.env.example). Common keys:

- **`ANTHROPIC_API_KEY`**, **`NEO4J_URI`**, **`NEO4J_USER`**, **`NEO4J_PASSWORD`**
- **`DISABLE_PIPELINE=1`** — hide Pipeline tab (read-only demos)
- **`SCRIPTRAG_DEMO_LAYOUT=1`** — demo section order
- **`PERSISTENT_DATA_DIR`** — optional durable paths (e.g. Render disk)
- **LangSmith** — optional tracing (`LANGCHAIN_*` in `.env.example`)

---

## Deployment

- **Render:** Blueprint [`render.yaml`](render.yaml); set secrets for Neo4j + Anthropic. [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)
- **Docker:** `docker build` / `docker compose` — see [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml), [`docker-compose.stack.yml`](docker-compose.stack.yml) (Neo4j + app on one host).

Neo4j Aura is provisioned separately; point **`NEO4J_URI`** at your instance.

---

## Navigating the codebase (file map)

| Path | Role |
|------|------|
| **`app.py`** | Streamlit: Pipeline, Audit & Verify, Reconcile, Data out, Efficiency. |
| **`etl_core/graph_engine.py`** | LangGraph: extract, validate, fixer, optional audit node. |
| **`etl_core/telemetry.py`** | Token/cost accumulation; **`PIPELINE_TELEMETRY_VERSION`**, UI summary markdown. |
| **`domains/screenplay/adapter.py`** | Wires `DomainBundle`: LLM calls, rules, auditors, `audit_post_process`. |
| **`domains/screenplay/rules.py`** | Deterministic validation (errors + warnings). |
| **`domains/screenplay/auditors.py`** | Three auditor agents + structured findings. |
| **`domains/screenplay/audit_pipeline.py`** | `process_semantic_audit` — gates, auto-apply, HITL warnings. |
| **`ingest.py`** | `extract_scenes` generator, `SceneResult`, CLI ingest. |
| **`extraction_llm.py`** | Anthropic + Instructor; extract/fix/audit call paths + usage. |
| **`extraction_graph.py`** | Thin adapter to compiled LangGraph + `run_pipeline`. |
| **`schema.py`** | Pydantic graph contract (`SceneGraph`, relationships, `source_quote`). |
| **`neo4j_loader.py`** | Merge JSON into Neo4j; wipe screenplay keeping **:PipelineRun**. |
| **`pipeline_runs.py`** | Persist / list **:PipelineRun** nodes. |
| **`data_out.py`** | Recipe Cypher specs, exports for Data out tab. |
| **`metrics.py`** | Structural analytics CLI/library. |
| **`reconcile.py`** | Fuzzy scan + merge helpers. |
| **`cleanup_review.py`** | HITL warning helpers, apply approved edits. |
| **`parser.py`**, **`lexicon.py`** | Parse and lexicon build. |
| **`strategy.md`** | Architecture, metric definitions, roadmap. |
| **`Telemetry.md`** | Token Agent versions, Ludwig A/B results. |
| **`AGENTS.md`** | Contributor/agent conventions. |

Full tree: [Project tree](#project-tree) below.

### Project tree

```
GraphRAG/
├── LICENSE
├── samples/                   # Bundled .fdx (+ PDF companions)
├── tools/                     # Optional Neo4j QA helpers
├── etl_core/                  # Domain-agnostic LangGraph ETL + telemetry + audit policy
├── domains/screenplay/        # Schema, rules, auditors, audit patch + pipeline
├── parser.py
├── lexicon.py
├── ingest.py
├── extraction_llm.py
├── extraction_graph.py
├── neo4j_loader.py
├── schema.py
├── metrics.py
├── data_out.py
├── app.py
├── reconcile.py
├── lead_resolution.py         # Optional programmatic helpers
├── pipeline_runs.py
├── cleanup_review.py
├── pipeline_state.py
├── Dockerfile
├── docker-compose.yml
├── docker-compose.stack.yml
├── render.yaml
├── strategy.md
├── Telemetry.md
├── MEMORY.md
└── AGENTS.md
```

---

## Documentation map

| Doc | Use |
|-----|-----|
| **`strategy.md`** | Architecture, dashboard behavior, metric definitions, ETL rules, efficiency roadmap. |
| **`Telemetry.md`** | Pipeline efficiency versions (**v1–v3** shipped; **v4–v5** reserved), benchmark log. |
| **`AGENTS.md`** | How to work in the repo (`uv`, parameterized Cypher, where to edit). |
| **`MEMORY.md`** | Short snapshot for quick orientation. |

---

## License

[MIT](LICENSE). Copyright (c) 2026 Kenny Geiler.

To show the demo URL on the GitHub **About** card: **Settings → General → Website** → `https://scriptrag.onrender.com/`.
