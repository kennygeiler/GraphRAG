# Phase 1: Config & script-generalized dashboard - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver **analysis-discerned lead character(s)** (primary + optional cohort) for regression and role-dependent analytics, with **optional env/config overrides** when operators want to pin or adjust what the graph implies—so arbitrary screenplays work without hardcoded constants in `app.py` (CONFIG-01, GEN-01).

Deliver **script-agnostic, graph-grounded presentation across all Streamlit tabs** (Pipeline, Cleanup Review, Pipeline Efficiency Tracking, Dashboard, Investigate)—not only the Dashboard tab.

Does **not** include empty-state hardening (Phase 2), reconciliation UX (Phase 3), or complexity signals (Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Analysis vs override (precedence)

- **D-01:** **Primary source — graph analysis.** Derive the **primary lead** (and any **secondary leads** the UI needs) from **existing structural metrics** in `metrics.py` / Neo4j (e.g. interaction counts, co-presence, patterns already used for “top characters”—planner picks the exact callable(s) and documents the rule). Must stay consistent with **strategy.md** metric definitions (structural, not vibes).
- **D-02:** **Override layer — env (and optional small file).** Document env vars (e.g. pin primary lead id, optional comma-separated lead list, optional integer for top-K) in `.env.example` so operators can **force** or **adjust** analysis output when they disagree with the automatic ranking. Optional repo-root `scriptrag.toml` / YAML **example** only if multiple knobs are cleaner than env alone.

### Which identifiers and constants move out of code

- **D-03:** Remove hardcoded **`PROTAGONIST_ID = "zev"`** as the sole source of truth. **Default behavior:** use **analysis-derived primary lead** for regression warning and `_extra`-style wiring; **env override** applies when set. Do not invent arbitrary characters—ranking must come from loaded graph data (or clear empty-graph path in Phase 2 if no data).
- **D-04:** **`TOP_INTERACTION_CHARACTERS` (currently 5)** — make overridable via env (integer) in same pass if low cost; align with analysis-derived cohort where applicable.
- **D-05:** **GEN-01 scope — all Streamlit tabs** in `app.py` **plus** strings in **`cleanup_review.py`**, **`agent.py`**, **`pipeline_runs.py`** if they expose user-visible script-specific names or fixed IDs tied to one production. Do **not** expand to test fixtures, auditor prompt **examples**, or `schema.py` sample payloads unless they surface in the live app.

### Reload & caching

- **D-06:** **Document** that changing **override** config requires **Streamlit restart** (or redeploy). Analysis output changes when **graph data** changes (existing cache stamps). **Optional stretch:** invalidate `@st.cache_data` when override file mtime changes.

### Behavior when leads cannot be resolved

- **D-07:** If the graph yields **no viable lead candidate** (empty graph, degenerate metrics) **or** an **override id** is set but **absent** from the act matrix, show **`st.info` / sidebar notice** and **skip** the regression warning — no silent failure, no random pick.

### Display names & GEN-01 copy

- **D-08:** User-facing labels use **Neo4j / lexicon / analysis result** naming everywhere tabs show character identity; avoid hardcoded production-specific proper names in operator-visible strings.
- **D-09:** Regression warning stays **generic** (“primary lead” / “the lead”) with **resolved id/label** from analysis or override — no script-specific flavor text.

### Primary lead selection rule (for Phase 1 implementation)

- **D-10:** Use **one structural rule** already defined in product docs—no new “protagonist detector” heuristic.
  - **Default primary lead:** the **#1 character** by **`get_top_characters_by_interaction_count(k=1)`** in `metrics.py` (same edge mix as **Power-shift cohort** in `strategy.md` §4: **CONFLICTS_WITH + USES + INTERACTS_WITH**, both directions). This is the character with the highest total narrative edge count; it matches how the dashboard already thinks about “who drives structural interaction load.”
  - **Override:** if the operator sets an env pin (name TBD in plan), use that **Character `id`** as **primary** for the regression check instead of rank 1. If the pin is missing from the loaded graph / act matrix → **D-07** (notice, skip warning).
  - **Cohort (“leads” plural for UI):** keep using **`get_top_characters_by_interaction_count(k)`** with **`k` from env** when implemented (**D-04**); the **primary** for regression is always **either** the pin **or** rank 1 from the **same** ranking function (do not mix unrelated metrics for primary vs cohort).
  - **Ties:** ensure **deterministic** ordering when two characters share the same total (e.g. secondary sort by **`c.id` ascending**) so reruns and screenshots match.
  - **Empty / no edges:** if the query returns **no** character with `tot > 0`, there is **no** analysis primary → **D-07**.

### Claude's Discretion

- Exact env var names (`SCRIPTRAG_*` vs `SCRIPT_RAG_*`) — follow shortest consistent prefix and update `.env.example` once.
- Whether to introduce `pydantic-settings` vs manual `os.environ` — match **existing** `app.py` / project patterns (`CONVENTIONS.md`); avoid new dependency unless already justified.

### Folded Todos

(None — `todo match-phase` returned no matches.)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Roadmap & requirements

- `.planning/ROADMAP.md` — Phase 1 goal, success criteria, UI hint
- `.planning/REQUIREMENTS.md` — CONFIG-01, GEN-01
- `.planning/PROJECT.md` — vision, constraints, validated stack

### Product authority

- `strategy.md` — metric definitions, dashboard map, protagonist regression behavior, script-agnostic direction
- `MEMORY.md` — compact dashboard/metric notes

### Codebase map

- `.planning/codebase/ARCHITECTURE.md` — Streamlit shell, metrics, `app.py` entry
- `.planning/codebase/STRUCTURE.md` — where new config helpers should live
- `.planning/codebase/CONVENTIONS.md` — Python / project patterns

### Implementation touchpoints

- `app.py` — all tabs: Pipeline, Cleanup Review, Efficiency, Dashboard, Investigate; `PROTAGONIST_ID`, `_protagonist_regression_warning`, `_extra` / act matrix, captions and chart copy
- `metrics.py` — **`get_top_characters_by_interaction_count`** (D-10) for primary lead and cohort; do not duplicate its Cypher for regression
- `cleanup_review.py`, `agent.py`, `pipeline_runs.py` — user-visible copy only where script-specific
- `.env.example` — override variable names (no secrets)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable assets

- **`_env_truthy`** and env patterns in `app.py` for feature flags (`DISABLE_PIPELINE`).
- **Cached dashboard loaders** — `_cached_act_passivity_matrix`, `_DASH_STAMP` / `_neo4j_dashboard_cache_stamp`; config changes must interact correctly with cache keys (restart or explicit clear).

### Established patterns

- **Single-module Streamlit app** — wide layout, `st.session_state`, Plotly charts; new config should stay readable at module top or small helper module at repo root if `app.py` grows.
- **Parameterized Cypher** in `metrics.py` — lead analysis must remain parameterized; no string-built Cypher from UI.

### Integration points

- **Lead resolution** feeds: act passivity matrix → regression warning; power-shift / top-character composition (`_extra` tuple); any tab that names a “focus” character.
- **Sidebar** — show **resolved primary lead** (analysis vs override) for operator clarity.

</code_context>

<specifics>
## Specific Ideas

- **strategy.md** explicitly calls out `PROTAGONIST_ID` constant and Cinema Four–centric defaults as technical debt to generalize.
- **2026-04-03 (user):** Leads should be **discerned by analysis** (not config-only); **GEN-01** covers **all Streamlit tabs** and related modules with user-visible copy. Reload/override semantics and TOP_K env tweak remain as before.

</specifics>

<deferred>
## Deferred Ideas

- **Phase 2:** Empty / partial Neo4j and DataFrame hardening (REL-01).
- **Phase 3:** Reconciliation operator surfaces (REC-01).
- **Phase 4:** Production complexity signal (MET-01).

### Reviewed Todos (not folded)

(None.)

</deferred>

---

*Phase: 01-config-script-generalized-dashboard*
*Context gathered: 2026-04-03*
