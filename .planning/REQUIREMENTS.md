# Requirements — ScriptRAG (v1 GSD track)

Requirements for the **current planning milestone**: hardening, generalization, reconciliation expansion, and first Phase 3 signals. Shipped capabilities are listed as validated in `.planning/PROJECT.md`.

## v1 Requirements

### Configuration & generalization

- [x] **CONFIG-01**: Primary lead(s) for regression warnings and role-dependent analytics are **derived from graph/metrics analysis** (structural signals in `metrics.py` / Neo4j); operators may **override or pin** via environment variables or a small project config file—no hardcoded `PROTAGONIST_ID`-style constants as the only source of truth in `app.py`.
- [x] **GEN-01**: User-visible copy across **all Streamlit tabs** (and related modules such as `cleanup_review.py`, `agent.py`, `pipeline_runs.py` where operator-facing) avoids fixed production-specific character IDs; labels use analysis- or graph-derived identities (plus overrides) so a new script does not require code edits for basic correctness.

### Reliability & UX

- [x] **REL-01**: When Neo4j is empty, partially loaded, or query results lack expected columns, the Streamlit dashboard shows explicit empty states or safe fallbacks—no uncaught exceptions in metric/DataFrame code paths.

### Graph operations

- [x] **REC-01**: Reconciliation workflows in `reconcile.py` are exposed or documented for operator use at a defined scope (CLI and/or dashboard), with safe merge behavior consistent with existing patterns.

### Analytics (Phase 4)

- [x] **MET-01**: An initial **production complexity / cost signal** derived from graph density (or related structural stats) is available in a form consumable from the app or CLI, without replacing existing structural metrics.

## v2 (deferred)

- **SENT-01**: Optional sentiment or subtext on edges—only if grounded in `source_quote` and secondary to structural metrics (`strategy.md`).

## Out of scope (v1)

- **STACK-01**: Migrating off Neo4j, Streamlit, or the current LLM extraction stack.
- **VIBE-01**: Scoring “feel” of scenes without graph-level evidence.

## Traceability

| REQ-ID  | Phase | Status   |
|---------|-------|----------|
| CONFIG-01 | 1 | Done (2026-04-03) |
| GEN-01    | 1 | Done (2026-04-03) |
| REL-01    | 2 | Done (2026-04-03) |
| REC-01    | 3 | Done (2026-04-03) |
| MET-01    | 4 | Done (2026-04-04) |

*Aligned with `.planning/ROADMAP.md` (coarse v1 track).*
