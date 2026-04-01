# Narrative MRI — Project Strategy & AI Context

**Last updated:** March 2026  
**Owner:** Kenny Geiler  
**Purpose:** Single source of truth for what this repo is, where it stands, where it is going, and **non-negotiable rules** for humans and AI assistants. **Update this file when you pivot or complete a major milestone** so any tool can onboard without a full re-explanation.

---

## 1. What this project is

**Narrative MRI** is a **GraphRAG** system for screenplays: it turns structured script data into a **Neo4j** knowledge graph and exposes **structural “physics”** (agency, friction, prop load) through metrics and a **Streamlit** producer dashboard.

**Core philosophy — “ruthless structuralism”:**  
We do not infer vibes from prose alone. We map **narrative physics**: who acts on whom, where conflict is explicit, how passive a character is under a defined graph metric, and whether props earn their place. Evidence lives on edges as **verbatim `source_quote`** text from the script.

**Reference production:** The primary developed script is **Cinema Four** (~86 scenes). The **pipeline is script-agnostic** in principle (any `.fdx` → same JSON → Neo4j shape); some **dashboard copy and arc defaults** still name specific roles (e.g. Zev / Alan) and should be generalized over time.

---

## 2. Architecture (data flow)

| Stage | Artifact / system | Module(s) |
|--------|-------------------|-----------|
| Parse | `raw_scenes.json` | `parser.py` |
| Lexicon | `master_lexicon.json`, `lexicon.json` | `lexicon.py` |
| Extract | `validated_graph.json` (per-scene `SceneGraph`) | `ingest.py` (checkpoints each scene; auto-continues partial runs; `--fresh` to wipe) |
| Load | Neo4j nodes & relationships | `neo4j_loader.py` |
| Analyze | Passivity, heat, Chekhov, QA queries | `metrics.py`, `reconcile.py` |
| Experience | Dashboard, HITL, chat, pipeline UI | `app.py`, `hitl.py`, `agent.py` |

**Graph model (Neo4j):**

- **Nodes:** `Character`, `Location`, `Prop`, `Event` (one event per scene number + heading).
- **Structural:** `(entity)-[:IN_SCENE]->(Event)` for entities present in that scene.
- **Narrative (typed, with `source_quote`):** `INTERACTS_WITH`, `LOCATED_IN`, `USES`, `CONFLICTS_WITH`, `POSSESSES` between Character / Location / Prop as loaded from validated JSON.

**Canonical ingestion (Option A):** There is no alternate LangGraph ingest. **`pipeline.py`**, **`extractor.py`**, and **`main.py` are removed.** The only supported path is `parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` (CLI or **Pipeline Engine** in `app.py`). **`langchain` / `langchain-community` / `langgraph`** are not direct project dependencies; **`langgraph` may still install transitively** via `langchain-neo4j` (Ask-the-graph tab).

**Schema contract:** `schema.py` — Pydantic models for `SceneGraph`, nodes, and `Relationship` (proof quote required).

**Secrets / env:** `.env` — `ANTHROPIC_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`. Never commit secrets.

---

## 3. Current progress (milestone snapshot)

Use this as a checklist; flip items when reality changes.

### Done (representative)

- [x] **FDX → JSON** parsing with stable scene numbering and text payload.
- [x] **Lexicon + ingest** pipeline producing **validated** per-scene graphs (`instructor` + Pydantic).
- [x] **Neo4j loader** (merge events, entities, `IN_SCENE`, narrative edges with quotes).
- [x] **Metrics layer** (`metrics.py`): passivity (global and windowed), scene heat, load-bearing props, possessed-unused, Act I→III Chekhov-style audit, scene inspector quotes, character `IN_SCENE` counts.
- [x] **Scene heat refinement:** numerator = **distinct unordered conflict pairs** in-scene (not raw `CONFLICTS_WITH` edge count) to reduce dialogue-bloat skew.
- [x] **Streamlit dashboard** (`app.py`): **Narrative Radiologist** with nested tabs — **Story Analytics** (radiology report, pacing heartbeat, slump engine), **Character Arcs** (ensemble passivity chart with **≥5 scenes** filter, power-cross trajectory, Act I vs III takeaway), **Production Logistics** (Chekhov table + audit, entity health, scene inspector, director notes persistence).
- [x] **Human-in-the-loop** tab for non-VERIFIED scenes (`hitl.py`).
- [x] **Ask the graph** chat path (`agent.py`).
- [x] **Pipeline Engine** tab: Neo4j + JSON **nuke**, `.fdx` upload → `target_script.fdx`, four-stage `uv run` chain with streamed logs.
- [x] **Utilities:** `debug_export.py` → `graph_qa_dump.json`; `qa_entities.py` → `data_health_report.json`.
- [x] **Option A consolidation:** Removed LangGraph / duplicate loader path; dependencies trimmed; Cypher prompts and QA scripts aligned to `:Character`/`:Location`/`:Prop`/`:Event` + `source_quote` + `IN_SCENE`.

### In progress / known gaps

- [ ] **Empty-graph hardening:** Dashboard must never assume `pass_df` has an `id` column when `pass_rows` is empty; guard all chart paths when Neo4j has no `Character` / `Event` data (point users to **Pipeline Engine**).
- [ ] **Full script-agnostic UI:** Replace hardcoded lead/antagonist IDs in trajectory and takeaways with dynamic “key players” or configurable IDs (`metrics.py` / `app.py`).

### Explicitly not started (roadmap)

- **Phase 3:** Production complexity / cost signals from graph density.
- **Phase 4 (exploratory):** Sentiment or subtext on edges **only** if grounded in `source_quote` and secondary to structural metrics.

---

## 4. Metric definitions (authoritative for implementation)

These definitions are what code should implement; if code diverges, fix code or update this section in the same PR.

| Metric | Definition |
|--------|------------|
| **Passivity** | For a character: `in_degree / (in_degree + out_degree)` on **CONFLICTS_WITH** and **USES** (including incoming **USES** on **POSSESSES**’d props). `None` if no qualifying edges. |
| **Scene heat** | For an `Event`: `(# of **unique unordered** entity pairs with ≥1 in-scene CONFLICTS_WITH between them, either direction) / (count of IN_SCENE links into that Event)`. Undefined heat when denominator is 0. |
| **Slump alert** | From heat series: **≥3 consecutive** scene numbers (with defined heat) each **&lt; 0.1** (`SLUMP_HEAT_THRESHOLD`, `SLUMP_MIN_SCENES` in `app.py`). |
| **Load-bearing props** | Props with **≥2** total **USES** or **CONFLICTS_WITH** touches (after set-dressing filter in `metrics.py`). Threshold may be tuned toward **relative density** per dataset over time. |
| **Act I / III** | Scene thirds from `max(Event.number)` (see `_script_act_bounds` / Cypher mirrors in `metrics.py`). |
| **Arc takeaway** | Compare lead passivity Act I vs Act III windows; flag if drop **&lt; 20%** from Act I baseline (`ACT_PASSIVITY_DROP_MIN`). |
| **Agency bar chart filter** | Only characters with **`IN_SCENE` count ≥ 5** (`AGENCY_CHART_MIN_IN_SCENE`) to reduce bit-part noise. |

---

## 5. Dashboard map (`app.py`)

**Top-level tabs**

1. **Narrative Radiologist** — Nested **Story Analytics | Character Arcs | Production Logistics** (Legend → Data → Takeaway pattern where applicable).
2. **Human-in-the-Loop validation** — Draft vs Gold, edit nodes/edges, verify scenes.
3. **Ask the graph** — Narrative QA over the graph.
4. **Pipeline Engine** — Wipe DB + pipeline JSONs, upload `.fdx`, run staged extraction with live logs.

**Director notes:** Stored on `:MRIMeta` and mirrored on the first `:Event` (`producer_notes.py`).

---

## 6. Future strategy

1. **Hardening:** Empty Neo4j and partial JSON states; clear user messaging and no uncaught KeyErrors in DataFrame paths.
2. **Generalization:** Dynamic leads / antagonists for charts and takeaways; optional project config (YAML or env) for role IDs.
3. **Reconciliation at scale:** Expand `reconcile.py` workflows from the dashboard and CLI; keep merges safe (APOC or manual rewire patterns already referenced in UI).
4. **Producer overlays:** Phase 3 complexity metrics without diluting structural truth.
5. **Documentation hygiene:** After each milestone, update **`strategy.md`** (this file), then trim **`README.md`** / **`.cursorrules`** if they duplicate—avoid three divergent truths.

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
10. **Pipeline order** for a cold start: `parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` (also orchestrated from **Pipeline Engine**).

### When the user pivots or ships a milestone

11. **Update `strategy.md`** — Adjust §3 checkboxes, §4 if metrics change, §5–§6 if UI or roadmap changes, §7 if new non-negotiables appear.
12. **Optionally sync `.cursorrules`** with a one-paragraph summary so Cursor’s auto-loaded rules stay aligned (full detail stays here).

---

## 8. Quick file reference

| Path | Role |
|------|------|
| `strategy.md` | **This file** — project brain |
| `.cursorrules` | Cursor-local concise rules + pointer here |
| `README.md` | Human onboarding & commands |
| `schema.py` | Pydantic graph contract |
| `ingest.py` | LLM extraction → `validated_graph.json` |
| `metrics.py` | All graph analytics queries |
| `app.py` | Streamlit application |
| `neo4j_loader.py` | JSON → Neo4j |
| `debug_export.py` | Sample Neo4j → `graph_qa_dump.json` |
| `qa_entities.py` | Consistency audit → `data_health_report.json` |
| `pipeline_state.py` | `pipeline_state.json` + `filesystem_snapshot()` for Engine Room |

---

*End of strategy document. Prefer editing this file over scattering “project memory” across chat-only context.*
