---
name: reading-01-doc-parsing
description: Deterministic scientific PDF document parsing with multi-column XY-cut reading order, header/footer/marginalia isolation, three-line/multi-header/cross-page table reconstruction, subscript/superscript/Greek formula fidelity, adaptive OCR fallback, and page/bbox provenance validation. Use whenever parsing academic PDFs or answering evidence-grounded scientific reading questions under Harbor /input and /app/output contracts.
---

# Scientific Document Parsing (`reading-01-doc-parsing`)

Deterministic Python pipeline for scientific PDF parsing focused on four core guarantees:
1. **提得全 (Complete Extraction)**: Verbatim body text, captions, footnotes, and references without header/footer/marginalia contamination.
2. **排得对 (Accurate Reading Order)**: Recursive XY-cut column detection, full-width spanning band segmentation, soft-hyphen repair, and cross-column/cross-page paragraph continuation linking.
3. **拆得清 (Clean Structure)**: Hierarchical numbered sections, three-line (`booktabs`) and lattice tables with `rowspan`/`colspan`, cross-page table merging, and `_{}`/`^{}` + Symbol-font Greek reconstruction.
4. **核得回 (Verifiable Provenance & Honest Boundaries)**: Every element carries `pdf_page`, `printed_page`, `bbox`, and `block_id`/`table_id`/`eq_id`; anomalies (`encrypted_pdf`, `scanned_no_text`, `garbled_font_cid`, `image_based_table`, `disordered_page_numbers`) trigger explicit `status` and `uncertainties` without guessing.

---

## Path Discovery & Runtime Contract

Locate the Skill directory dynamically before running any script (never assume a fixed user home or drive letter):
- Check `/skills/weekly-skill` first; if absent, use the directory containing this `SKILL.md` (or `/app/submission`). Let `SKILL_DIR` denote that resolved directory.
- Prefer `python3` (fallback `python`) with `PYTHONDONTWRITEBYTECODE=1`.
- Standard Harbor paths:
  - Case contract & prompt: `/input/case.json`, `/input/task.md`
  - Workspace scratch outputs: `/app/work/parsed/`
  - Final deliverables: `/app/output/result.json` (plus supporting artifacts/crops as needed)

---

## Standard 6-Step Execution Workflow

### Step 1: Inspect Task & Contract
Read `/input/case.json` and `/input/task.md` first to determine:
- `case_id` and exact `output_contract` fields
- Input PDF path(s) under `/input`
- Any specific extraction, QA, table, formula, or section focus

### Step 2: Parse the selected PDFs into separate source directories
List the actual material paths under `/input`, including nested supplements. Select the primary manuscript from the task, then include each requested supplement in its own `--pdf` argument. Relative PDF paths resolve against the directory of `case.json`. The paths below are illustrative; replace them with the discovered paths.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/parse_case.py" \
  --case /input/case.json --out /app/output --budget 600 \
  --pdf materials/primary.pdf --pdf supplements/appendix.pdf
```

Use one `--pdf` for a single manuscript. Reserve time for case-specific answers, citations and final inspection inside the total case budget. The command writes first-page text previews and a partial `result.json` before full layout/OCR, then checkpoints after each source. A timed-out or unreadable file keeps its own reason. `documents[].parsed_path` points to that source's actual JSON relative to the output root. `primary_source_id` identifies the article supplying the top-level descriptive metadata. Use the pair `(source_id, element_id)` for citations because each PDF independently numbers its blocks, tables and equations.

### Step 3: Select evidence for each requested field
Read each source's `document_summary.json` beside its completed `document.json`. Write down each task.md question, its requested unit or format, the relevant source and what evidence would answer it. A section list alone will not answer a question about a particular measurement. Query the parsed PDF for each item:

```bash
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed "$PARSED_DOC" --mode tables
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed "$PARSED_DOC" --mode search --query "$SEARCH_TERM"
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed "$PARSED_DOC" --mode cells \
  --id "$TABLE_LABEL" --row "$ROW_LABEL" --column "$COLUMN_NAME" --limit 5 --readback
```

Use physical page and printed page when known. Keep the bbox, element ID and exact text or cell value with the document identifier. For tables, verify header hierarchy, case-sensitive units and continuation captions. Cite each cross-page value at its **own cell's page**. Inspect `bboxes_by_page` and nearby captions when two tables share a template. Compare `structured_text` to surrounding text or the image when a formula has ambiguous subscripts, superscripts or Greek symbols.

The cell query returns the row label, parent/leaf column, group, physical page and rectangle. `--readback` checks the selected cell against the original text layer. Verify the requested row, column and any table notes separately. Multiple matches require a narrower filter or a comparison, depending on the question. For a calculated answer, retain the source cells, operation, units and rounding. For an image cell, inspect its crop and report unresolved digits explicitly.

### Step 4: Resolve extraction limits against the originals
Review `uncertainties` and the corresponding `document.json`, PDF or crop. For OCR or image tables, visually check required values and units. Qualify unverifiable items with a source and page. When sources conflict, report both locations and keep their values distinct. For a partial preview, use only the content actually present and report the uncovered pages or files.

### Step 5: Complete the task-specific contract
Read every requested question or field again. Use actual manuscript and supplement evidence to write `/app/work/answers.json`, a JSON object with exactly the requested field names and correctly typed values. Attach source-qualified evidence to the answers in the form the case requires. Where the paper does not report an item, follow the contract's missing-value convention and state why in the answer. Use this command to apply the evidence-backed values while retaining extraction status and source artifacts:

```bash
python3 "$SKILL_DIR/scripts/parse_case.py" --case /input/case.json --out /app/output \
  --answers /app/work/answers.json
```

If the prompt asks questions beyond the enumerated output fields, include `task_answers` with one entry per question, its answer and its cited evidence. Include `uncertainties` for unanswered or conflicting items, with a message, field and sources checked. A closed JSON Schema receives only its fields and `case_id` in `result.json`; `parse_manifest.json` next to it retains the source index, supplementary answers, diagnostics and paths. For such a schema, open this manifest to locate each source after Step 2.

### Step 6: Verify the deliverable
Reopen `/app/output/result.json` and, when present, `parse_manifest.json`. Review every `contract_field_invalid` or `contract_field_unfilled` entry. Correct the named field and reapply `--answers`. Check the answers against the original question list and confirm that each requested table, unit, comparison or exception is addressed. Inspect each source's `validation.json` for extraction checks. For an independent text-recall check on a selected original, use the single-document commands in the README with that PDF's completed `document.json`. Check citations against the correct original file, physical page and rectangle.

---

## Self-Check Checklist

- [ ] `result.json` exists in `/app/output/result.json` with exact `case_id` and every `output_contract` key.
- [ ] `status` is one of `success | partial | unverifiable | refused` and matches actual anomalies.
- [ ] Every factual claim, table cell/summary, and formula cites `pdf_page` (and `printed_page` if available), `bbox`, and element ID.
- [ ] No fabricated data, no `TODO`/`TBD`/`PLACEHOLDER`, and no missing uncertainties.

---

## Minimal Illustrative Example (Example Only — Not Formal Test Data)

See [`examples/example_case.json`](examples/example_case.json) and [`examples/example_result.json`](examples/example_result.json) (both explicitly marked as synthetic illustrative examples).

```bash
# Illustrative local invocation example:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/parse_pdf.py --pdf sample_paper.pdf --out ./work/parsed --ocr auto --crops
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_result.py --case examples/example_case.json --parsed ./work/parsed/document.json --markdown ./work/parsed/full_text.md --out ./work/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_output.py --case examples/example_case.json --result ./work/result.json --parsed ./work/parsed/document.json --pdf sample_paper.pdf --out ./work/validation_report.json
```
