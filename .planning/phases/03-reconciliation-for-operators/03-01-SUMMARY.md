---
phase: 03-reconciliation-for-operators
plan: 01
subsystem: cli
tags: [neo4j, reconcile, argparse, fuzzywuzzy]

provides:
  - ReconciliationScan dataclass + run_reconciliation_scan(driver, min_similarity)
  - CLI --scope all|characters|locations; location interactive merge loop
  - README ## reconciliation with examples
modified: [reconcile.py, README.md]
executed: 2026-04-03
---

# Plan 03-01 summary

- **`ReconciliationScan`** holds ghosts, fuzzy character pairs, fuzzy location pairs.
- **`run_reconciliation_scan`** performs read-only session work; **`main()`** calls it once.
- **`_merge_pair_loop_cli`** deduplicates Character vs Location merge prompts.
- **README:** TOC link, dashboard table row for Reconcile, full reconciliation section.
