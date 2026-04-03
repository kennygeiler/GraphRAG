# Telemetry & pipeline efficiency — version log

**Purpose:** Track which **efficiency roadmap phase** the code was on when each **`:PipelineRun`** was written, and record **before/after** results when you ship improvements. Authoritative KPI definitions and phases **0–5** live in **`strategy.md`** (section *Telemetry & efficiency*).

---

## `telemetry_version` on `:PipelineRun`

Each run stores integer **`telemetry_version`** in Neo4j. **Pipeline Efficiency Tracking** shows it under **Token Agent** as **`v0`**, **`v1`**, … (legacy **`v0`** rows show **N/A** for per-stage token/$ columns).

| Version | Meaning |
|---------|---------|
| **0** | **Legacy** — rows created before **`telemetry_version`** existed. The UI shows **0** when the property is missing. Stage columns (**E/F/A**) are **unreliable** (often all zero even if totals exist). |
| **1** | **Roadmap Phase 0 shipped** (`strategy.md` *Telemetry Phase 0*): per-stage **extract / fix / audit** token and USD buckets end-to-end. (**Version numbers are offset from phase labels so 0 stays “unknown/legacy.”**) |
| **2** | **Phase 1 shipped:** compact lexicon in extraction **system** prompt (`ingest.compact_lexicon_for_prompt`); **compact JSON** (no indent) for **auditor** and **fixer** user payloads; **audit** `max_tokens=2048`; fixer includes trimmed original instructions (**8k** chars) and **120k**-char user cap. Log a **post** run next to the v1 baseline to measure savings. |
| **3+** | Phase 2+ (model routing, conditional audit, …) — bump **`PIPELINE_TELEMETRY_VERSION`** when storage/attribution contract changes again. |

**Operator rule:** When you complete a new efficiency phase in code, **increment `PIPELINE_TELEMETRY_VERSION`**, update this table, and append a row under **Results log**.

---

## How to bump the version

1. Edit **`etl_core/telemetry.py`** → raise **`PIPELINE_TELEMETRY_VERSION`** (and comment what changed).
2. If Neo4j needs new properties, extend **`pipeline_runs.save_pipeline_run`** and the Efficiency table in **`app.py`**.
3. Add a row to **Results log** (below) with a baseline run **before** merge and a run **after** merge (same script/range if possible).
4. Summarize the mapping here in the version table.

---

## Baseline marker — entering Phase 1 (2026-04-03 UTC)

**Milestone:** Last calibrated run under **Token Agent v1** (roadmap **Phase 0** complete). **Phase 1** = prompt/payload shrink (`strategy.md` roadmap). Use this row as the **before** snapshot when comparing a future run after Phase 1 work (ideally same script + scene count).

| | |
|--|--|
| **Scenes extracted / total** | 5 / 5 |
| **Corrections** | 5 |
| **Warnings** | 10 |
| **Failed scenes** | 0 |
| **Total tokens** | 150,957 |
| **Total $ (estimated)** | 0.9114 |
| **Tok E / F / A** | 25,078 / 32,553 / 93,326 |
| **$ E / F / A** | 0.1752 / 0.1903 / 0.5459 |

**Interpretation (for prioritization):** **Audit ~62%** of tokens and **~60%** of estimated $ on this run; **fix** is mid-single-digit re **extract**; primary Phase 1 levers are **auditor payload size** and shared prompt bulk; Phase 2 adds **model routing** (especially audit).

---

## Results log (manual)

Add rows as you ship phases. Copy from **Pipeline Efficiency Tracking** or export. **Token Agent** uses **`v0` / `v1` / `v2`** …

| Date (UTC) | Token Agent | Strategy phase | Script / range | Scenes ext. | Corr. | Warn. | Fail | Total tok | Total $ | E / F / A tok | $ E / F / A | Notes |
|------------|-------------|----------------|----------------|------------|-------|-------|------|-----------|---------|---------------|-------------|-------|
| 2026-04-03 | v1 | Phase 0 **done**; pre–Phase 1 | *(run in baseline table above)* | 5/5 | 5 | 10 | 0 | 150,957 | 0.9114 | 25,078 / 32,553 / 93,326 | 0.1752 / 0.1903 / 0.5459 | Baseline before Phase 1 code |
| *(add)* | v2 | **Phase 1 shipped** | same script/range as v1 row if possible | | | | | | | | | *Fill after first **v2** pipeline run for A/B* |

---

## Phase 1 implementation summary (Token Agent v2)

Shipped in repo: **`PIPELINE_TELEMETRY_VERSION = 2`**. Compare **v2** Efficiency rows to the **v1** baseline (same scene count when benchmarking).

| Change | Files |
|--------|--------|
| Lexicon: one-line-per-entity prompt block vs pretty-printed JSON | **`ingest.py`** `compact_lexicon_for_prompt`, **`app.py`** / CLI ingest `main` |
| Audit user msg: `json.dumps(..., separators=(',', ':'))`, cap **120k** | **`domains/screenplay/auditors.py`** |
| Fixer user msg: compact JSON, cap **120k**; fixer system embed **8k** chars of original instructions | **`extraction_llm.py`** |
| Auditor completions: **2048** max output tokens | **`extraction_llm.py`** `call_audit_llm_with_usage` |

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
| Phase 1 prompt/payload | **`ingest.py`**, **`extraction_llm.py`**, **`domains/screenplay/auditors.py`** |
