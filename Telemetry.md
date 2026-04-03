# Telemetry & pipeline efficiency — version log

**Purpose:** Track which **efficiency roadmap phase** the code was on when each **`:PipelineRun`** was written, and record **before/after** results when you ship improvements. Authoritative KPI definitions and phases **0–5** live in **`strategy.md`** (section *Telemetry & efficiency*).

---

## `telemetry_version` on `:PipelineRun`

Each run stores integer **`telemetry_version`** in Neo4j. **Pipeline Efficiency Tracking** shows it under **Token Agent** as **`v0`**, **`v1`**, … (legacy **`v0`** rows show **N/A** for per-stage token/$ columns).

| Version | Meaning |
|---------|---------|
| **0** | **Legacy** — rows created before **`telemetry_version`** existed. The UI shows **0** when the property is missing. Stage columns (**E/F/A**) are **unreliable** (often all zero even if totals exist). |
| **1** | **Roadmap Phase 0 shipped** (`strategy.md` *Telemetry Phase 0*): per-stage **extract / fix / audit** token and USD buckets end-to-end. (**Version numbers are offset from phase labels so 0 stays “unknown/legacy.”**) |
| **2** | Reserved for **Phase 1** (e.g. prompt/payload efficiency): bump when you change attribution or add columns—then log results below. |
| **3+** | Later phases (conditional audit, routing, etc.) — same bump + document discipline. |

**Operator rule:** When you complete a new efficiency phase in code, **increment `PIPELINE_TELEMETRY_VERSION`**, update this table, and append a row under **Results log**.

---

## How to bump the version

1. Edit **`etl_core/telemetry.py`** → raise **`PIPELINE_TELEMETRY_VERSION`** (and comment what changed).
2. If Neo4j needs new properties, extend **`pipeline_runs.save_pipeline_run`** and the Efficiency table in **`app.py`**.
3. Add a row to **Results log** (below) with a baseline run **before** merge and a run **after** merge (same script/range if possible).
4. Summarize the mapping here in the version table.

---

## Results log (manual)

Add rows as you ship phases. Copy from **Pipeline Efficiency Tracking** or export.

| Date (UTC) | Token Agent | Strategy phase | Script / range | Scenes | Total tok | Total $ | E / F / A tok (if v≥1) | Notes |
|------------|---------------|----------------|----------------|--------|-----------|---------|------------------------|-------|
| *(example)* | 1 | Phase 0 shipped | Cinema Four smoke | 5 | … | … | … / … / … | Baseline post-instrumentation |

---

### Optional: normalize legacy nodes in Neo4j

If you want the property present on every row (same meaning as “missing”):

```cypher
MATCH (p:PipelineRun)
WHERE p.telemetry_version IS NULL
SET p.telemetry_version = 0
```

---

## Related code

| Piece | Location |
|--------|-----------|
| Version constant | `PIPELINE_TELEMETRY_VERSION` in **`etl_core/telemetry.py`** |
| Persist + list | **`pipeline_runs.py`** |
| UI table | **`app.py`** → Pipeline Efficiency Tracking |
| Per-stage accumulation | **`etl_core/graph_engine.py`**, **`etl_core/telemetry.py`** `accumulate_usage` |
