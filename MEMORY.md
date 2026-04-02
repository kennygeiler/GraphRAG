# Project memory (compact)

**Last aligned:** April 2026. For full detail use **`strategy.md`**.

## What this is

Screenplay **GraphRAG**: `.fdx` → JSON → **Neo4j** (`Character`, `Location`, `Prop`, `Event` + `IN_SCENE` + narrative rels with `source_quote`). **Streamlit** app = **Narrative Timeline Analyzer** + HITL + graph chat + pipeline UI.

## Dashboard tabs (`app.py`)

| Tab | Purpose |
|-----|---------|
| **Engine Room** | Live self-healing ETL demo: paste text → extract→validate→fix via `etl_core` LangGraph engine; `st.metric` for tokens/cost; hallucination audit log |
| **Narrative Timeline** | Momentum line (rolling heat), Payoff Matrix (long-gap props), Power shift (top 5 × 3 acts), protagonist regression warning |
| **Ask the graph** | Natural language → Cypher (`agent.py`) |
| **AI Audit Log** | File-based `extraction_audit.jsonl` viewer (written by `ingest.py`) |
| **Pipeline Engine** | Wipe DB/JSONs, upload `.fdx`, run parser → lexicon → ingest → loader with logs (hidden on cloud) |

## Act structure (dynamic)

From Neo4j: **`get_script_act_bounds`** in `metrics.py` — `min(:Event.number)` … `max(:Event.number)` split into **three as-equal-as-possible** buckets. Not fixed to “scene 21 / 65”; changes with whatever script is loaded.

## Key metrics (current UI)

- **Momentum heat (per scene):** `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` among entities both `IN_SCENE` to that `Event`; UI smooths with a **3-scene** trailing mean.
- **Payoff props:** First intro (earliest `IN_SCENE` or co-scene `POSSESSES`) vs last `USES` / `CONFLICTS_WITH`; keep if gap **> 10** scenes.
- **Passivity (per act window):** `in / (in + out)` on `CONFLICTS_WITH` + `USES` (incl. incoming `USES` on possessed props), edges attributed to scenes in the act range. **Power shift** uses top **5** characters by **CONFLICTS_WITH + USES + INTERACTS_WITH** count (both directions).
- **Protagonist check:** If **`zev`** (see `PROTAGONIST_ID` in `app.py`) has **Act 3 passivity > Act 1**, UI shows a regression warning.

## Separate: “scene heat” in `metrics.py`

**Distinct** from momentum heat: **unique unordered conflict pairs** in-scene ÷ `IN_SCENE` count (`get_scene_heat`). Still used in CLI / diagnostics; not the same formula as the momentum chart.

## Architecture: engine vs domain

Generic ETL engine lives in `etl_core/` (LangGraph state machine, telemetry, cost tracking). Screenplay-specific models and rules live in `domains/screenplay/`. The engine accepts a pluggable `DomainBundle` so it can be reused for other domains without touching core logic.

## Pipeline order (cold start)

`parser.py` → `lexicon.py` → `ingest.py` → `neo4j_loader.py` → `streamlit run app.py`

## Secrets

**`.env`** only; never commit. Template: **`.env.example`**.
