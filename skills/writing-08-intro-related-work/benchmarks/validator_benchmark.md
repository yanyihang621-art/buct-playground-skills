# Writing 08 Local Validator & Real-Paper Verification Benchmark

> **Disclaimer**: All metrics below are from local synthetic test suites and local published-paper dogfooding. They are **not** official Bohrium platform scores.

## 1. Self-Test Suite (`scripts/self_test.py`, 6/6 Suites + Surname + 7/7 Faults Passed)

| Suite | Scope Verified | Outcome |
|---|---|---|
| `t1_en_twocol_docx` | Two-column PDF (`parse_pdf.py` column-order preservation) + BibTeX override of earlier PDF preprint year/ALL-CAPS title (`2024` overrides `2022`) + `.md` + `.docx` study notes; `doc_id:anchor` & `source_id` evidence lookup; numeric citation rendering; native `python-docx` export (`11,101` bytes) | **Pass** (`citation_coverage_ratio=1.0`, `must_fix=0`) |
| `t2_zh_observational` | Chinese manuscript + GB/T-style citations + observational study interpretation boundary | **Pass** (`must_fix=0`) |
| `t3_revision_no_false_block` | Targeted `related_work_only` revision with `change_records`; zero false-positive 4-section blockers | **Pass** (`must_fix=0`) |
| `t4_closed_contract` | Closed `output_contract` (`additionalProperties: false`, `status.enum`, `answer.json` custom array fields) | **Pass** (`0` contract errors) |
| `t5_fault_injection` & `t5b_surname_parsing` | Fabricated quote (`unverified_quote`), unknown `[@nonexistent_key]`, `[TODO]` placeholder, cliché opening, weak stitching, fabricated number (`9.9%`), empty corpus (`status="unable"`); 2-letter surnames (`Jimmy Ba` -> `Ba`, `Wei Li` -> `Li`) vs initials (`Smith AB` -> `Smith`) | **7/7 + 4/4 Caught** |
| `t6_worked_example` | End-to-end execution of `examples/example_case.json` + `literature.json` + `field_protocol.md` + `example_draft.md` + `example_ledger.json` + `example_answer.json` | **Pass** (`must_fix=0`, `warnings_count=0`) |

## 2. Real Published-Paper Dogfooding (`N19-1423.pdf` + `ba2016-layer-normalization.pdf` + `adam.pdf` + `.bib` + Study Notes)

- **Corpus**: Three real published PDFs (BERT 16p 2-col, LayerNorm 14p, Adam 15p) merged cleanly with `.bib` metadata (deduplicated from 5 raw records to **3** canonical papers, with authoritative `.bib` years/titles overriding earlier PDF guesses) plus synthetic author study notes (`own_study.md`).
- **Execution**: `finalize.py` completed with `pass: true`, `status: "success"`, `must_fix: []`, `warnings_count: 0`.
- **Output Metrics**: Proper author-year citations rendered cleanly (`(Devlin et al., 2019; Ba et al., 2016)`, `(Kingma & Ba, 2015)`), proper-cased `"Adam: A Method for Stochastic Optimization"`, `citation_coverage_ratio=1.0` (3/3 cited), `verified_quotes_count=4` (100% verbatim-grounded across PDF blocks, `tab_3`, and study lines), `cross_work_comparisons=[["ba2016", "devlin2019", "kingma2015"]]`, and complete `comparison_matrix.md`.
