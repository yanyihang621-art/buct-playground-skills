# Reading 15 Local Validator & Real-Paper Verification Benchmark

> **Disclaimer**: All metrics below are from local synthetic test suites and local published-paper dogfooding. They are **not** official Bohrium platform scores.

## 1. Self-Test Suite (`scripts/self_test.py`, 7/7 Passed)

| Suite | Scope Verified | Outcome |
|---|---|---|
| `t1_revised_manifest` | Harbor revised manifest (`corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `secondary-publisher.xml`), competing `AP` definitions, `MaterialInstance` split via `instance_of`, `pdf_page=null`, GraphML XML round-trip, PNG preview, 12 replayable queries (`relation_evidence_probe` forward=1 / reverse=0, `cross_paper_comparison`, `path_search`) | **Pass** |
| `t2_order_invariance` | Reversed manifest order produces identical `do_not_merge_records` and abbreviation isolation | **Pass** |
| `t3_multiformat_dedup` | Same-paper JSON + Markdown deduplicated (`paper_count=1`, `duplicate_formats_skipped=1`) | **Pass** |
| `t4_incremental_and_conflict` | `--base-kg` incremental update preserves 100% of prior `node_id`s and flags cross-paper predicate contradiction | **Pass** |
| `t5_closed_contract` | Closed `output_contract` (`additionalProperties: false`, `status.enum`, `answer.json` override) with 0 contract issues | **Pass** |
| `t6_fault_injection` | Fabricated quote (`unverified_quote` blocker), unit mismatch `450 nm` vs `450 um` (`unit_mismatch`), dropped hedge (`hedge_dropped`), encrypted PDF (`status="unable"`) | **4/4 Caught** |
| `t7_worked_example` | End-to-end execution of `examples/example_case.json` + `examples/example_kg_draft.json` | **Pass** |

## 2. Real Published-Paper Dogfooding (`N19-1423.pdf` + `ba2016-layer-normalization.pdf`)

- **Corpus**: 16-page two-column BERT (`N19-1423.pdf`, 284 blocks + 8 tables) and 14-page Layer Normalization (`ba2016-layer-normalization.pdf`, 397 blocks + 3 tables).
- **Execution**: `finalize.py` completed in **0.29s** (`must_fix: []`, `pass: true`).
- **Output Metrics**: 22 verified nodes, 12 substantive directed edges (all verbatim-grounded in PDF blocks and `tab_3`), 12 executed replayable queries (covering `paper_1`, `paper_2`, `cross_paper_comparison`, `path_search`, `conflict_and_boundary_audit`), valid GraphML XML, DOT, Markdown report, and `graph_preview.png`.
