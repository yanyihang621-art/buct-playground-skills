---
name: reading-10-structured-notes
description: Section-aligned scientific literature summarization and structured note-taking with a unified 12-field Paper Card, preconditions/limitations preservation, acronym canonicalization, intra-paper numeric conflict detection, cross-paper comparison matrices, searchable inverted index, and deterministic anchor/quote grounding audit. Use under Harbor /input and /app/output contracts for single or multi-paper PDF/Markdown reading notes.
---

# Literature Summary & Structured Notes (`reading-10-structured-notes`)

Deterministic Python pipeline for scientific literature reading notes focused on four guarantees:
1. **对得上 (Section-Aligned Skeleton)**: Preserves the paper's actual hierarchical section tree, reading order, discourse roles, cross-references, and section-to-section transitions instead of forcing a generic abstract template.
2. **留得住 (Conditions, Findings & Limitations)**: Captures unified 12-field Paper Cards explicitly recording research questions, data/materials, methods, operating conditions/preconditions, quantitative findings, limitations, and `not_reported` boundaries.
3. **找得回 (Fine-Grained Anchors)**: Binds every section note, Paper Card slot, and knowledge entry to `[p.<pdf_page> | <section_id> | <block_id/table_id> | bbox:<bbox>]` with verbatim `quote_snippet` grounding.
4. **用得上 (Searchable & Comparable)**: Outputs Obsidian-ready `structured_notes.md`, JSON `structured_notes.json`, `comparison_matrix.md/.json`, `notes_index.json`, and an independent deterministic `audit_notes.py` report.

---

## Path Discovery & Runtime Contract

- Resolve `SKILL_DIR` dynamically: check `/skills/weekly-skill` first; if absent, use the directory containing this `SKILL.md` (or `/app/submission`). Never hardcode Windows drive letters or personal home directories.
- Prefer `python3` (fallback `python`) with `PYTHONDONTWRITEBYTECODE=1`.
- Standard Harbor paths:
  - Case contract & instructions: `/input/case.json`, `/input/task.md`
  - Workspace scratch outputs: `/app/work/notes/`
  - Final deliverable: `/app/output/result.json`

---

## Standard 6-Step Execution Workflow

### Step 1: Inspect Task & Contract
Read `/input/case.json` and `/input/task.md` to confirm `case_id`, input document paths (`.pdf`, `.md`, or `.txt`), and required `output_contract` keys.

### Step 2: Ingest Input Document(s)
Run `ingest_source.py` on `/input` (supports single or multiple PDF/Markdown/TXT files):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/ingest_source.py" \
  --input /input \
  --case /input/case.json \
  --out /app/work/ingested \
  --ocr auto \
  --crops
```

### Step 3: Build Section-Aligned Skeletons, Paper Cards & Knowledge Entries
For each paper in `/app/work/ingested/ingest_manifest.json`, extract its section-aligned skeleton and knowledge entries, then render the combined notes bundle:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/render_notes.py" \
  --manifest /app/work/ingested/ingest_manifest.json \
  --out /app/work/notes
```

(To inspect or process a single paper step-by-step, invoke `extract_note_skeleton.py` followed by `extract_knowledge_entries.py`.)

Generated artifacts under `/app/work/notes/`:
- `structured_notes.json`: Full machine-readable notes, Paper Cards, terminology glossary, conflicts, and verification ledger.
- `structured_notes.md`: Human/Obsidian-ready Markdown notes with YAML frontmatter and anchor tags.
- `comparison_matrix.json` & `comparison_matrix.md`: Side-by-side Paper Card matrix across all ingested papers.
- `notes_index.json`: Searchable inverted index over terms, acronyms, sections, and pages.

### Step 4: Write Early Checkpoint & Review `needs_human_review` Boundaries
Immediately generate an early contract-compliant `/app/output/result.json`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_note_result.py" \
  --case /input/case.json \
  --notes /app/work/notes/structured_notes.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --markdown /app/work/notes/structured_notes.md \
  --comparison-md /app/work/notes/comparison_matrix.md \
  --out /app/output/result.json
```

Inspect `verification_ledger`, `conflicts`, `unreported_or_missing_items`, and `uncertainties` in `structured_notes.json`. Distinguish clearly between:
- **`machine_verified`**: Bibliographic metadata, section structure, extracted tables/figures/equations, acronym counts, and verbatim numeric findings verified against source anchors.
- **`needs_human_review`**: High-level research question synthesis, implicit preconditions/limitations, `not_reported` confirmation, and flagged intra-paper numeric discrepancies (`conflicts`).

### Step 5: Refine Task-Specific Fields (Optional Override) & Finalize `result.json`
If `/input/case.json` asks specific reading/summarization questions or custom `output_contract` fields, synthesize evidence-backed answers citing exact `[pdf_page, section_id, block_id, bbox, quote_snippet]` into `/app/work/override.json`, then rebuild `/app/output/result.json`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_note_result.py" \
  --case /input/case.json \
  --notes /app/work/notes/structured_notes.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --markdown /app/work/notes/structured_notes.md \
  --comparison-md /app/work/notes/comparison_matrix.md \
  --override-json /app/work/override.json \
  --out /app/output/result.json
```

### Step 6: Run Deterministic Anchor & Grounding Audit
Always run `audit_notes.py` before finishing:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/audit_notes.py" \
  --case /input/case.json \
  --result /app/output/result.json \
  --notes /app/work/notes/structured_notes.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --out /app/work/audit_report.json
```

Confirm `"pass": true`, `anchor_verification_rate == 1.0`, `section_coverage_ratio >= 0.90`, and zero placeholder tokens.

---

## Exception Handling Matrix

| Condition / Anomaly | Required Action & Honest Output |
|---|---|
| `encrypted_pdf` / `corrupt_pdf` | Keep `status: "refused"`; record `all_fields_unreadable_due_to_encryption_or_corruption`; NEVER fabricate notes. |
| `scanned_no_text` | Check OCR output; if OCR binary unavailable, keep `status: "unverifiable"` (or `"partial"` if mixed) and cite rendered page PNGs. |
| `garbled_font_cid` | Keep raw-layer warning in `uncertainties`, apply OCR fallback if available, and set `status: "partial"`. |
| Double-column / Long paper | Multi-column XY-cut reading order runs automatically across all pages; use `section_notes` outline rather than dumping raw full text. |
| Missing figures / equations | Record `table_count`/`figure_count`/`equation_count: 0` honestly; never invent equations or captions. |
| Unreported Paper Card fields | Record `"status": "not_reported"` and list field names in `unreported_or_missing_items`. |

---

## Self-Check Checklist

- [ ] `/app/output/result.json` contains `case_id` and every required `output_contract` key.
- [ ] `section_notes` follows the paper's actual section order (`section_order_monotonic: true`).
- [ ] Every anchor cites valid `pdf_page`, `block_id`/`table_id`/`eq_id`, `bbox`, and grounded `quote_snippet`.
- [ ] `operating_conditions_and_preconditions`, `limitations_and_threats_to_validity`, and `unreported_or_missing_items` are explicitly populated.
- [ ] `audit_notes.py` exits 0 with `"pass": true`.

---

## Minimal Illustrative Example (Example Only — Not Formal Test Data)

See [`examples/example_case.json`](examples/example_case.json) and [`examples/example_result.json`](examples/example_result.json) (both explicitly marked as synthetic illustrative examples).

```bash
# Illustrative local invocation example:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_source.py --input ./sample_dir --case examples/example_case.json --out ./work/ingested --ocr auto
PYTHONDONTWRITEBYTECODE=1 python3 scripts/render_notes.py --manifest ./work/ingested/ingest_manifest.json --out ./work/notes
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_note_result.py --case examples/example_case.json --notes ./work/notes/structured_notes.json --manifest ./work/ingested/ingest_manifest.json --markdown ./work/notes/structured_notes.md --out ./work/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/audit_notes.py --case examples/example_case.json --result ./work/result.json --notes ./work/notes/structured_notes.json --manifest ./work/ingested/ingest_manifest.json --out ./work/audit_report.json
```
