# ScriptRAG — Project Strategy & AI Context

**Last updated:** April 2026  
**Owner:** Kenny Geiler  
**Purpose:** Single source of truth for what this repo is, where it stands, where it is going, and **non-negotiable rules** for humans and AI assistants. **Update this file when you pivot or complete a major milestone** so any tool can onboard without a full re-explanation.

---

## 1. What this project is

**ScriptRAG** is a **GraphRAG** system for screenplays: it turns structured script data into a **Neo4j** knowledge graph and exposes **structural "physics"** (agency, friction, prop load) through metrics and a **Streamlit** dashboard with a self-healing AI extraction pipeline.

**Core philosophy — "ruthless structuralism":**  
We do not infer vibes from prose alone. We map **narrative physics**: who acts on whom, where conflict is explicit, how passive a character is under a defined graph metric, and whether props earn their place. Evidence lives on edges as **verbatim `source_quote`** text from the script.

**Reference production:** The primary developed script is **Cinema Four** (~86 scenes). The **pipeline is script-agnostic** in principle (any `.fdx` → same JSON → Neo4j shape); some **dashboard copy and arc defaults** still name specific roles (e.g. Zev / Alan) and should be generalized over time.

---

## 2. Architecture (data flow)

| Stage | Artifact / system | Module(s) |
|--------|-------------------|-----------|
| Parse | `raw_scenes.json` | `parser.py` |
| Lexicon | `master_lexicon.json`, `lexicon.json` | `lexicon.py` |
| Extract | `validated_graph.json` (per-scene `SceneGraph`) | `ingest.py` (exports `extract_scenes()` generator; checkpoints each scene; `--fresh` to wipe) |
| Load | Neo4j nodes & relationships | `neo4j_loader.py` (exports `load_entries()` for in-memory data) |
| Analyze | Passivity, heat, Chekhov, QA queries | `metrics.py`, `reconcile.py` |
| Experience | Pipeline + Cleanup Review + efficiency log + Dashboard + Investigate | `app.py`, `agent.py`, `pipeline_runs.py`, `cleanup_review.py` |

**Graph model (Neo4j):**

- **Nodes:** `Character`, `Location`, `Prop`, `Event` (one event per scene number + heading).
- **Structural:** `(entity)-[:IN_SCENE]->(Event)` for entities present in that scene.
- **Narrative (typed, with `source_quote`):** `INTERACTS_WITH`, `LOCATED_IN`, `USES`, `CONFLICTS_WITH`, `POSSESSES` between Character / Location / Prop as loaded from validated JSON.

**Pipeline:** `parser.py` → `lexicon.py` → `ingest.py` (with `etl_core` self-healing loop: extract → validate → fix) → `neo4j_loader.py`. Runs in-process from Streamlit's **Pipeline** tab or via CLI. The `ingest.py` module exports `extract_scenes()` generator consumed by both paths.

**Schema contract:** `schema.py` — Pydantic models for `SceneGraph`, nodes, and `Relationship` (proof quote required).

**Secrets / env:** `.env` — `ANTHROPIC_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`. Optional **LangSmith** (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`) for optional trace export to LangSmith. Never commit secrets; use **`.env.example`** as a template.

**Efficiency persistence:** Each completed pipeline run creates a **`:PipelineRun`** node in Neo4j (`pipeline_runs.py`). Graph wipe for reload excludes `:PipelineRun` so history survives **Approve & Load**.

---

## 3. Current progress (milestone snapshot)

Use this as a checklist; flip items when reality changes.

### Done (representative)

- [x] **FDX → JSON** parsing with stable scene numbering and text payload.
- [x] **Lexicon + ingest** pipeline producing **validated** per-scene graphs (`instructor` + Pydantic).
- [x] **Neo4j loader** (merge events, entities, `IN_SCENE`, narrative edges with quotes).
- [x] **Metrics layer** (`metrics.py`): passivity (global and windowed), scene heat, load-bearing props, possessed-unused, Act I→III Chekhov-style audit, scene inspector quotes, character `IN_SCENE` counts.
- [x] **Scene heat refinement:** numerator = **distinct unordered conflict pairs** in-scene (not raw `CONFLICTS_WITH` edge count) to reduce dialogue-bloat skew.
- [x] **Streamlit dashboard** (`app.py`): **ScriptRAG** — **Pipeline** (upload FDX, in-process extraction; persists **:PipelineRun**), **Cleanup Review** (corrections as plain English + compact before/after; warnings with JSON path + approve/decline; approve & load to Neo4j), **Reconcile** (`reconcile.py` scan + optional confirmed merges; ghosts + fuzzy Character/Location pairs), **Pipeline Efficiency Tracking** (table from Neo4j; telemetry token/cost), **Dashboard** (momentum, payoff matrix, power shift, X/N scenes, protagonist regression), **Investigate** (ask the graph).
- [x] **Self-healing ETL pipeline:** `etl_core` LangGraph engine (extract → validate → fix loop), `ingest.py` exports `extract_scenes()` generator, Streamlit consumes it with live per-scene progress.
- [x] **Ask the graph** chat path (`agent.py`).
- [x] **Utilities:** `debug_export.py` → `graph_qa_dump.json`; `qa_entities.py` → `data_health_report.json`.

### In progress / known gaps

- [x] **Timeline empty states:** Narrative Timeline charts guard empty Cypher results and missing columns.
- [x] **Full script-agnostic UI (primary lead):** Regression uses `lead_resolution` — env override `SCRIPTRAG_PRIMARY_LEAD_ID` or analysis rank #1; cohort size `SCRIPTRAG_TOP_CHARACTERS`.
- [x] **Graph reliability (REL-01):** Dashboard `@st.cache_data` Neo4j loaders return empty shapes on connection/query errors (`logging.exception`, no `st.*` in cache). Momentum / payoff / power-shift guard DataFrame columns and character ids. **`agent.py`** builds `Neo4jGraph` / `GraphCypherQAChain` lazily (`_get_chain()`); import does not require Neo4j env at load time. **Investigate** tab wraps chat errors; **Cleanup Review** uses safe dict access on corrections.
- [x] **Reconciliation (REC-01):** **`run_reconciliation_scan`** + **`ReconciliationScan`** in `reconcile.py`; CLI **`--scope`** + **`--dry-run`**; README **Reconciliation** section; Streamlit **Reconcile** tab (cached scan, checkbox + pair picker before **`merge_characters` / `merge_entities`**).

### Explicitly not started (roadmap)

- **Phase 4 (v1 roadmap):** Production complexity / cost signal — **MET-01**.
- **Exploratory (not v1 roadmap):** Sentiment or subtext on edges **only** if grounded in `source_quote` and secondary to structural metrics.

---

## 4. Metric definitions (authoritative for implementation)

These definitions are what code should implement; if code diverges, fix code or update this section in the same PR.

| Metric | Definition |
|--------|------------|
| **Passivity** | For a character: `in_degree / (in_degree + out_degree)` on **CONFLICTS_WITH** and **USES** (including incoming **USES** on **POSSESSES**'d props). `None` if no qualifying edges. Windowed variants restrict edges to scenes in `[lo, hi]` (see `get_passivity_in_scene_window`). |
| **Scene heat** | For an `Event`: `(# of **unique unordered** entity pairs with ≥1 in-scene CONFLICTS_WITH between them, either direction) / (count of IN_SCENE links into that Event)`. Undefined heat when denominator is 0. Used in CLI (`metrics.py --heat`) and diagnostics — **not** the same formula as **narrative momentum** below. |
| **Narrative momentum (dashboard)** | Per `Event`: `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` counting in-scene typed edges among co-present entities (`get_narrative_momentum_by_scene`). UI applies a **3-scene** rolling mean (`ROLLING_SCENES` in `app.py`). |
| **Payoff / long-arc props** | `get_payoff_prop_timelines`: first intro vs last `USES`/`CONFLICTS_WITH`; include if `(last − first) > PAYOFF_MIN_SCENE_GAP` (default **10**). |
| **Power-shift cohort** | Top **K** characters by total **CONFLICTS_WITH + USES + INTERACTS_WITH** edge count, both directions (`get_top_characters_by_interaction_count`). |
| **Act buckets (dashboard)** | **Equal thirds** of inclusive scene span `min(:Event.number)…max(:Event.number)` (`get_script_act_bounds` in `metrics.py`). Vertical markers on momentum chart at first scene of Act 2 and Act 3 when structurally distinct. |
| **Primary-lead regression (UI)** | **Primary** = `SCRIPTRAG_PRIMARY_LEAD_ID` if set, else Character at rank **#1** by `get_top_characters_by_interaction_count(1)` (same edge mix as power-shift). If that id’s passivity in Act 3 **>** Act 1 → `st.warning` (fatal arc). If id missing from matrix or unresolved → informative message, no warning. |
| **Load-bearing props** | Props with **≥2** total **USES** or **CONFLICTS_WITH** touches (after set-dressing filter in `metrics.py`). Used in older Chekhov-style CLI audits, not the Payoff Matrix chart. |

---

## 5. Dashboard map (`app.py`)

**Layout:** `st.set_page_config(page_title="ScriptRAG", layout="wide")`.

**Top-level tabs**

1. **Pipeline** — Upload `.fdx`, run full extraction in-process (parse → lexicon → per-scene `extract_scenes()` with live progress). Stores results in `st.session_state`. On completion, writes a **`:PipelineRun`** row (efficiency metrics; in-app telemetry). Hidden when `DISABLE_PIPELINE=1`.
2. **Cleanup Review** — Corrections: plain-English explanation + compact before/after (not full JSON). Warnings: pointer into extracted graph + per-warning approve/decline. "Approve & Load to Neo4j" calls `neo4j_loader.load_entries()` (graph wipe spares `:PipelineRun`).
3. **Pipeline Efficiency Tracking** — Reads **`:PipelineRun`** from Neo4j (telemetry tokens/cost and run metadata).
4. **Dashboard** — X/N scenes banner, **Momentum** (Plotly line + area, dashed act-boundary vlines), **Payoff Matrix** (horizontal span bars for long-gap props), **Power shift** (multi-line passivity across three act buckets), protagonist regression warning.
5. **Investigate** — Narrative QA / Cypher path (`agent.py`).

**Sidebar:** "Reload metrics" clears cache; "Nuke database" in expander for full resets.

**Cache:** Dashboard queries use `@st.cache_data` keyed on pipeline artifact mtimes; "Reload metrics" clears cache.

---

## 6. Future strategy

1. **Hardening:** Empty Neo4j and partial JSON states; clear user messaging and no uncaught KeyErrors in DataFrame paths.
2. **Generalization:** Dynamic leads / antagonists for charts and takeaways; optional project config (YAML or env) for role IDs.
3. **Reconciliation at scale:** Expand `reconcile.py` workflows from the dashboard and CLI; keep merges safe (APOC or manual rewire patterns already referenced in UI).
4. **Producer overlays:** Phase 3 complexity metrics without diluting structural truth.
5. **Documentation hygiene:** After each milestone, update **`strategy.md`** (this file), then sync **`README.md`**, **`MEMORY.md`**, **`AGENTS.md`**, and **`.cursorrules`** so onboarding stays single-source (`strategy.md`) with short mirrors.

---

## 7. Strict rules for AI assistants

Follow these in every change unless the user explicitly overrides.

### Evidence & graph integrity

1. **Every narrative relationship** in extracted data must carry a **verbatim `source_quote`** from the script — no paraphrase as proof.
2. **Cypher:** Parameterized queries only; **never** interpolate user-controlled strings into query text.
3. **Python driver:** Match existing patterns in `metrics.py`, `neo4j_loader.py`, `reconcile.py` (`session.run`, transactions as already used).

### Code quality & scope

4. **Minimal diffs:** Touch only what the task requires; no drive-by refactors or unsolicited new docs (user-requested docs like this file are exceptions).
5. **Match local style:** Imports, typing, naming, and Streamlit patterns consistent with `app.py`.
6. **Package manager:** **`uv`** for runs (`uv run python …`, `uv run streamlit run app.py`).
7. **Do not add CLI entrypoints** unless the user asks.

### Product logic

8. **Structural metrics first;** sentiment/subtext are secondary and evidence-bound if added later.
9. **Heat** must use **unique conflict pairs** per scene (see §4).
10. **Pipeline order** for a cold start: `parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` (also orchestrated from **Pipeline** tab in `app.py`).

### When the user pivots or ships a milestone

11. **Update `strategy.md`** — Adjust §3 checkboxes, §4 if metrics change, §5–§6 if UI or roadmap changes, §7 if new non-negotiables appear.
12. **Sync `.cursorrules` and `MEMORY.md`** with dashboard/metric changes (full detail stays here).

---

## 8. Quick file reference

| Path | Role |
|------|------|
| `strategy.md` | **This file** — project brain |
| `MEMORY.md` | Compact snapshot for humans & AI |
| `AGENTS.md` | Onboarding checklist for coding agents |
| `.cursorrules` | Cursor-local concise rules + pointer here |
| `README.md` | Human onboarding & commands |
| `schema.py` | Pydantic graph contract |
| `ingest.py` | LLM extraction → `validated_graph.json` (exports `extract_scenes()` generator + `SceneResult`) |
| `metrics.py` | All graph analytics queries |
| `app.py` | Streamlit application (ScriptRAG) |
| `neo4j_loader.py` | JSON → Neo4j (exports `load_entries()`) |
| `debug_export.py` | Sample Neo4j → `graph_qa_dump.json` |
| `qa_entities.py` | Consistency audit → `data_health_report.json` |
| `pipeline_state.py` | `pipeline_state.json` + `filesystem_snapshot()` |

---

*End of strategy document. Prefer editing this file over scattering "project memory" across chat-only context.*
