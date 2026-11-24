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

### Step 2: Run Deterministic Full-Document Parse
Run `parse_pdf.py` against each input PDF into `/app/work/parsed`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/parse_pdf.py" \
  --pdf /input/<paper.pdf> \
  --out /app/work/parsed \
  --ocr auto \
  --crops
```

Generated artifacts under `/app/work/parsed/`:
- `document.json`: Complete structured tree (`metadata`, `sections`, `blocks`, `tables`, `figures`, `formulas`, `references`, `diagnostics`, `uncertainties`, `change_log`).
- `document_summary.json`: Compact <= 8 KB overview (outline, counts, anomalies, uncertainties).
- `full_text.md`: Reading-order Markdown with `[p.<page> | <block_id> | col:<col> | bbox:<bbox>]` provenance tags.
- `tables.json` & `tables.html`: Structured tables with `col_paths`, `cells`, `markdown`, `html` (`rowspan`/`colspan`), and `records`.
- `formulas.json`: Display equations and inline formula spans.
- `crops/` (when `--crops` is set): 300 DPI PNGs for image-based tables, complex 2D equations, and scanned pages.

### Step 3: Read Compact Summary & Write Early Checkpoint
Inspect the compact summary first (do not dump the entire `document.json` into context):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/query_parsed.py" \
  --parsed /app/work/parsed/document.json \
  --mode summary
```

Immediately write an early valid `/app/output/result.json` checkpoint matching `output_contract`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_result.py" \
  --case /input/case.json \
  --parsed /app/work/parsed/document.json \
  --markdown /app/work/parsed/full_text.md \
  --out /app/output/result.json
```

### Step 4: Targeted Deep Inspection & Anomaly Handling
Use `query_parsed.py` to read only the sections, pages, tables, formulas, or search hits needed for `/input/case.json`:

```bash
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode sections
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode section --id sec_1
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode tables
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode formulas
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode search --query "CO2|catalyst"
python3 "$SKILL_DIR/scripts/query_parsed.py" --parsed /app/work/parsed/document.json --mode anomalies
```

Apply the exception rules strictly whenever `anomalies` or `uncertainties` are non-empty:

| Anomaly / Condition | Required Action & Honest Reporting |
|---|---|
| `encrypted_pdf` / `corrupt_pdf` | Keep `status: "refused"`; report exact page count/error; NEVER fabricate text. |
| `scanned_no_text` | Check OCR output & `mean_confidence`; if `ocr_unavailable`, keep `status: "unverifiable"` (or `"partial"` if mixed) and attach rendered page PNG paths. |
| `garbled_font_cid` | Preserve raw text layer warning in `uncertainties`, inspect OCR fallback, and mark unrecoverable spans explicitly. |
| `image_based_table` | Inspect saved 300 DPI crop (`crop_path`) visually or via OCR; if unverified, keep `status: "partial"` and cite `crop_path`. |
| `complex_2d_formula` | Inspect saved equation crop (`crop_path`) before writing stacked fractions/matrices into final answers. |
| `disordered_page_numbers` | Always anchor provenance to physical `pdf_page` while also reporting `printed_page`. |

### Step 5: Synthesize Final Contract Output
If `/input/case.json` requires task-specific answers, summaries, or custom contract fields beyond the deterministic extraction, prepare `/app/work/override.json` with those evidence-backed fields (citing `pdf_page`, `printed_page`, `bbox`, and `block_id`/`table_id`/`eq_id`), then rebuild `/app/output/result.json`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_result.py" \
  --case /input/case.json \
  --parsed /app/work/parsed/document.json \
  --markdown /app/work/parsed/full_text.md \
  --override-json /app/work/override.json \
  --out /app/output/result.json
```

### Step 6: Deterministic Self-Audit Before Finishing
Always validate `/app/output/result.json` before ending the run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/validate_output.py" \
  --case /input/case.json \
  --result /app/output/result.json \
  --parsed /app/work/parsed/document.json \
  --pdf /input/<paper.pdf> \
  --out /app/work/validation_report.json
```

Confirm `"pass": true`, zero errors, `provenance_coverage_ratio >= 0.95`, and honest `status` alignment before finishing.

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
