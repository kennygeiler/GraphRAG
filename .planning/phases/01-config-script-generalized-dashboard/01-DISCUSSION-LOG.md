# Phase 1: Config & script-generalized dashboard - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `01-CONTEXT.md`.

**Date:** 2026-04-03
**Phase:** 1 — Config & script-generalized dashboard
**Session note:** Single-turn chat — no interactive multi-select. Gray areas were identified by the orchestrator from `ROADMAP.md`, `PROJECT.md`, and a scout of `app.py`. **Recommended defaults** were recorded as decisions in CONTEXT; revise via `/gsd-discuss-phase 1` → “Update it” if any choice should change before execution.

**Areas covered:** Config source, scope of configurable IDs, reload semantics, missing-id behavior, GEN-01 copy scope

---

## Config source & deployment fit

| Option | Description | Selected |
|--------|-------------|----------|
| Env vars + `.env.example` | Matches Docker/Render and existing secrets pattern | ✓ |
| Streamlit secrets only | Worse for CLI/headless runs | |
| pyproject-only | Unusual for runtime ids; mixed with build metadata | |

**User's choice:** Recommended default (env-first)
**Notes:** Optional file-based map deferred to Claude discretion in CONTEXT (D-02).

---

## Reload / config change semantics

| Option | Description | Selected |
|--------|-------------|----------|
| Streamlit restart required | Simple, predictable with `st.cache_data` | ✓ |
| Hot reload via mtime watcher | Nice-to-have stretch in planning | |

**User's choice:** Recommended default (restart + document)
**Notes:** Optional cache invalidation left to planner.

---

## Missing protagonist in graph

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit operator message + skip warning | Clear, safe | ✓ |
| Silent skip | Hides misconfiguration | |
| Auto-pick top character | Scope creep / wrong semantics | |

**User's choice:** Recommended default
**Notes:** Aligns with structuralism (“no fake protagonist”).

---

## GEN-01 sweep scope

| Option | Description | Selected |
|--------|-------------|----------|
| Dashboard tab first | Highest visibility; matches ROADMAP | ✓ |
| Entire repo string audit | Too broad for Phase 1 boundary | |

**User's choice:** Recommended default
**Notes:** `schema.py` / auditor **examples** may stay as documentation samples unless they surface in UI.

---

## Claude's Discretion

- Env var naming prefix and whether to add small TOML/YAML file for display-name maps.
- Exact helper module split if `app.py` grows.

## Deferred Ideas

- Phase 2–4 scope items — explicitly out of this discussion.

---

## Amendment — 2026-04-03 (scope)

**User direction:** (1) **Leads discerned by analysis** (graph/metrics), not config-only; optional env/file **overrides**. (2) **GEN-01** applies to **all Streamlit tabs** and related operator-facing modules (`cleanup_review.py`, `agent.py`, `pipeline_runs.py` as needed)—not dashboard-only.

**Files updated:** `01-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md`.

---

## Amendment — primary lead rule (D-10)

**Recommendation for plan-phase:** Default primary lead = **rank 1** from `get_top_characters_by_interaction_count` (same definition as Power-shift cohort in `strategy.md`). Env pin overrides; deterministic tie-break; empty result → D-07. Recorded in `01-CONTEXT.md` §D-10.
