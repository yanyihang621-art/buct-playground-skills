---
name: writing-04-references
description: "Academic reference and citation management Skill supporting multi-format ingestion (.bib/.ris/.enw/.txt/.json), explainable deduplication with anti-false-merge guards, claim-level citation insertion, sequential renumbering & bidirectional sync (GB/T 7714-2015, APA 7th, IEEE, BibTeX), and 6-check verification."
---

# Writing 04 — Reference & Citation Management (`写作04｜参考文献与引用管理`)

This Skill solves the four core challenges of academic reference management in Harbor execution environments:

1. **挂得对 (Precise Claim-Level Placement)**: Inserts citation tags directly at the specific scientific claim clause (`insert_claims` in `scripts/sync_manuscript_citations.py`) rather than lazily appending them to paragraph ends, and audits sentence-vs-reference topic alignment (`claim_reference_alignment`).
2. **编得顺 (Sequential Renumbering & Cross-Ref Sync)**: Re-orders first-appearance numbering across body text, captions, and appendices (`[1–3]` compression in numeric styles, `\cite{...}` in `.tex`, or `(Author, Year)` in APA 7th) and regenerates a 1-to-1 synchronized bibliography.
3. **收得净 (Explainable Deduplication)**: Parses BibTeX, RIS, EndNote `.enw` (including CNKI), and JSON exports (`scripts/parse_bib_sources.py`), merges duplicates via `doi_exact`, `arxiv_exact`, `preprint_to_published` (preferring published venue while preserving `arxiv_id`), and `title_author_year_fuzzy`, while guarding against false merges of distinct papers by the same author in the same year (`scripts/dedup_references.py`).
4. **对得上 (Verifiable Sources & Honest Limits)**: Executes 6 deterministic QA checks (`scripts/verify_citations.py`) and produces explicit `sources`, `change_records`, and `uncertainties` in `/app/output/result.json` (`scripts/build_reference_result.py`), honestly declaring `"offline_syntax_only"` unless live DOI checking (`--verify-doi-online`) is run.

## Harbor Execution Workflow

- **Inputs**: `/input/case.json`, `/input/task.md`, manuscript file (`.md` / `.tex` / `.txt`), and bibliography exports (`.bib` / `.ris` / `.enw` / `.json`).
- **Command**:
  ```bash
  python scripts/build_reference_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json
  ```
- **Deliverables under `/app/output/`**:
  - `synced_manuscript.md` (or `.tex`) — editable synchronized manuscript
  - `bibliography_<style>.txt` (or `.bib`) — formatted reference list
  - `result.json` — Harbor contract deliverable matching `schemas/citation_result_schema.json`
- **Intermediate Evidence under `/app/work/references/`**:
  - `raw_library.json`, `canonical_library.json`, `dedup_report.json`, `citation_map.json`, `change_log.json`, `qa_report.json`.

## Integrity & Error Handling

- NEVER fabricate DOIs, venues, authors, or years to fill missing metadata (`missing_fields` + `uncertainties` must be preserved and `status` downgraded to `"partial"`).
- Reject synthetic placeholder DOIs (`10.0000/...`, `10.9999/...`) and unresolved `[CITE:...]` / `[@...]` tags in final verification.
- Missing manuscript or bibliography inputs return honest `status: "refused"`.
