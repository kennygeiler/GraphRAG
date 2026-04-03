# Requirements — ScriptRAG (v1 GSD track)

Requirements for the **current planning milestone**: hardening, generalization, reconciliation expansion, and first Phase 3 signals. Shipped capabilities are listed as validated in `.planning/PROJECT.md`.

## v1 Requirements

### Configuration & generalization

- [ ] **CONFIG-01**: Operators can set protagonist/lead identifiers used for regression warnings and role-dependent analytics via environment variables or a small project config file, without editing `PROTAGONIST_ID` constants in `app.py`.
- [ ] **GEN-01**: Dashboard labels and takeaways that assume fixed character IDs default to config-driven or graph-derived identities so a new script does not require code edits for basic correctness.

### Reliability & UX

- [ ] **REL-01**: When Neo4j is empty, partially loaded, or query results lack expected columns, the Streamlit dashboard shows explicit empty states or safe fallbacks—no uncaught exceptions in metric/DataFrame code paths.

### Graph operations

- [ ] **REC-01**: Reconciliation workflows in `reconcile.py` are exposed or documented for operator use at a defined scope (CLI and/or dashboard), with safe merge behavior consistent with existing patterns.

### Analytics (Phase 3 slice)

- [ ] **MET-01**: An initial **production complexity / cost signal** derived from graph density (or related structural stats) is available in a form consumable from the app or CLI, without replacing existing structural metrics.

## v2 (deferred)

- **SENT-01**: Optional sentiment or subtext on edges—only if grounded in `source_quote` and secondary to structural metrics (`strategy.md`).

## Out of scope (v1)

- **STACK-01**: Migrating off Neo4j, Streamlit, or the current LLM extraction stack.
- **VIBE-01**: Scoring “feel” of scenes without graph-level evidence.

## Traceability

| REQ-ID  | Phase | Status   |
|---------|-------|----------|
| CONFIG-01 | 1 | Not started |
| GEN-01    | 1 | Not started |
| REL-01    | 2 | Not started |
| REC-01    | 3 | Not started |
| MET-01    | 4 | Not started |

*Aligned with `.planning/ROADMAP.md` (coarse v1 track).*
