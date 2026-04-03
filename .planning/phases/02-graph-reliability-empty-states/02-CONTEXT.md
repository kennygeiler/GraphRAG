# Phase 2: Graph reliability & empty states — Context

**Gathered:** 2026-04-03  
**Status:** Ready for planning

<domain>

## Phase boundary

Deliver **REL-01**: when Neo4j is **empty**, **partially loaded**, **unreachable**, or queries return **unexpected shapes**, the Streamlit app shows **explicit empty states**, **safe fallbacks**, or **clear operator-facing errors**—not uncaught exceptions in metric/DataFrame paths.

**In scope:** Pipeline, Cleanup Review, Pipeline Efficiency Tracking, Dashboard, Investigate (and shared helpers in `app.py`, `metrics.py`, `lead_resolution.py`, `cleanup_review.py`, `pipeline_runs.py`, `agent.py` as needed).

**Out of scope:** Reconciliation UX (Phase 3 / REC-01), complexity signals (Phase 4 / MET-01), changing metric definitions, new CLI entrypoints.

**Depends on:** Phase 1 (resolved primary lead + script-agnostic copy).

</domain>

<decisions>

## Implementation decisions

### Neo4j failures in `@st.cache_data` loaders

- **D-01:** **Catch at the loader boundary.** Each dashboard-related cached function that opens a driver and runs Cypher (`_cached_momentum_rows`, `_cached_payoff_props`, `_cached_top_characters`, `_cached_primary_lead`, `_cached_act_bounds`, `_cached_act_passivity_matrix`, `_cached_event_count`) should wrap the **entire** body in `try`/`finally` (close driver in `finally` when the function owns it). On **connection/auth/service** failures (`neo4j.exceptions` where appropriate, plus generic `Exception` as a last resort for unexpected transport errors), **return the same empty shape** the code already uses for “no data” (`[]`, `{}`, `0`, `(None, False)`, etc.) so existing `if not rows` / `st.info` paths still run. **Do not** call `st.*` from inside cached functions (session context is unreliable there). Use **`logging.exception`** (or `logging.warning` with `exc_info=True`) for operator/server logs—not bare prints of secrets.

- **D-02:** **Optional consolidation.** If duplication is high, introduce a small internal helper (e.g. `_with_driver[T](fn, default: T) -> T`) in `app.py` or a thin `neo4j_safe.py` module—planner chooses; keep imports minimal and match existing patterns.

### DataFrame and column assumptions

- **D-03:** **Guard before column access.** Any path that builds a `pd.DataFrame` and then indexes columns (e.g. `df["scene_number"]`, payoff/power-shift tables) must verify **required columns exist** and **non-empty** before plotting or tabular display; otherwise show **`st.warning`** + skip the chart/table (same tone as existing momentum “No momentum data” path).

### Tabs already partially hardened

- **D-04:** **Pipeline Efficiency** tab already uses `try`/`except` around `get_driver()` + `list_pipeline_runs` with `st.error` and empty `rows` → `st.info`—**keep as reference pattern** for other tabs that talk to Neo4j outside cache.

- **D-05:** **Cleanup Review** and **Pipeline** already have some `try`/`except` around load paths; Phase 2 **audits** for remaining bare failures (e.g. malformed JSON, missing keys) and aligns messaging with REL-01 without changing business logic.

### Investigate / `agent.py`

- **D-06:** **Lazy or guarded graph init.** If `Neo4jGraph` (or equivalent) is constructed at **import** time and can raise when Neo4j is down, Phase 2 should **defer connection** to first use or wrap in a **clear user-facing error** in the Investigate tab so the rest of the app still loads. Planner confirms current import-time behavior and picks the smallest fix.

### UX consistency

- **D-07:** **Operator-facing copy** stays plain English, no stack traces in the main UI. Technical detail may appear in **expanders** or `st.error` one-liners where today we already surface exceptions (e.g. efficiency tab).

### Verification

- **D-08:** **Manual UAT** primary flows: Neo4j stopped / wrong password / empty DB / DB with only `PipelineRun` (no `Event`)—each tab should degrade gracefully. Automated tests optional if low-cost; not required by REL-01 text alone.

</decisions>

<artifacts>

## Code touchpoints (scout)

| Area | File / symbol | Risk |
|------|----------------|------|
| Cached dashboard loaders | `app.py` `_cached_*` | Uncaught driver/session errors propagate to Streamlit traceback |
| Momentum chart | `app.py` `_render_momentum_chart`, `df["scene_number"]` | Partial guard exists; align with D-03 |
| Payoff / power shift | `app.py` payoff matrix, power shift sections | DataFrame column assumptions |
| Sidebar regression | `_primary_lead_regression_warning` | Depends on cached primary + counts |
| Efficiency tab | `tab_efficiency` | Already try/except + empty state |
| Metrics | `metrics.py` `get_driver`, list/query functions | Used by app; failures often surface in callers |
| Investigate | `agent.py` | Possible import-time Neo4j dependency |

</artifacts>

<open_questions>

## Resolved for planning (defaults)

- **Q1:** Should cached loaders set `st.session_state` for a global “Neo4j unavailable” banner? **→ No for v1;** return empty data + rely on per-tab `st.info` / existing warnings; efficiency tab keeps explicit `st.error` on read failure.
- **Q2:** Log level for swallowed errors? **→ `logging.exception` once per failure path** (or warning with exc_info) so operators can tail logs without exposing secrets in UI.

</open_questions>
