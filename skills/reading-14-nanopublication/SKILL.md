---
name: reading-14-nanopublication
description: Atomic scientific claim decomposition and 4-graph Nanopublication extraction (Head, Assertion, Provenance, PubInfo) using a controlled 18-predicate vocabulary, minimal sufficient evidence grounding, source metadata/DOI tracking, deduplication, contradiction detection, zero-dependency W3C JSON-LD/TriG/N-Quads serialization, and 6-gate deterministic quality validation. Use under Harbor /input and /app/output contracts.
---

# Scientific Nanopublication Extraction (`reading-14-nanopublication`)

Deterministic Python pipeline for scientific Nanopublications focused on four core guarantees:
1. **拆得细 (Atomic Decomposition)**: Splits compound, causal, and conditional sentences into single, independently truth-evaluable `(subject, predicate, object, conditions)` claims while propagating shared leading operating conditions.
2. **对得上 (Minimal Sufficient Evidence)**: Binds every assertion to its minimal evidence snippet and exact source location (`pdf_page`, `printed_page`, `block_id`/`table_id`, `bbox`), distinguishing `direct_experimental`, `direct_tabular_or_figure`, `indirect_inference`, and `cited_prior_work`.
3. **追得回 (Complete Provenance Chain)**: Captures `paper_title`, `authors`, `year`, `doi`, `arxiv_id`, `version`, `source_sha256`, and deterministic content-addressed `urn:nanopub:sha256:...` URIs for deduplication.
4. **读得懂 (Standards-Compliant Exports & 6 Quality Gates)**: Outputs JSON, W3C JSON-LD 1.1, W3C TriG, W3C N-Quads, and Markdown, verified by 6 deterministic quality gates (`validate_nanopubs.py`).

---

## Path Discovery & Runtime Contract

- Resolve `SKILL_DIR` dynamically: check `/skills/weekly-skill` first; if absent, use the directory containing this `SKILL.md` (or `/app/submission`). Never hardcode Windows drive letters or personal directories.
- Prefer `python3` (fallback `python`) with `PYTHONDONTWRITEBYTECODE=1`.
- Standard Harbor paths: `/input/case.json`, `/input/task.md`, `/app/work/`, `/app/output/result.json`.

---

## Standard 6-Step Execution Workflow

### Step 1: Inspect Task & Contract
Read `/input/case.json` and `/input/task.md` to confirm `case_id`, input document paths, and `output_contract` fields.

### Step 2: Ingest Source Document(s)
Run `ingest_source.py` on `/input` (supports PDF, Markdown, and TXT):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/ingest_source.py" \
  --input /input \
  --case /input/case.json \
  --out /app/work/ingested \
  --ocr auto \
  --crops
```

### Step 3: Decompose Claims & Build 4-Graph Nanopublications
Run `decompose_assertions.py` to split compound/causal sentences, extract tabular metrics, map predicates strictly to `schemas/relation_vocabulary.yaml`, classify `evidence_type`, deduplicate, and detect cross-assertion conflicts:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/decompose_assertions.py" \
  --manifest /app/work/ingested/ingest_manifest.json \
  --out /app/work/nanopubs
```

### Step 4: Serialize to W3C JSON-LD, TriG, N-Quads & Markdown
Run zero-dependency serializers (`serialize_nanopubs.py`) and verify N-Quads grammar:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/serialize_nanopubs.py" \
  --nanopubs /app/work/nanopubs/nanopubs.json \
  --out /app/work/nanopubs
```

Generated artifacts under `/app/work/nanopubs/`:
- `nanopubs.json`, `nanopubs.jsonld`, `nanopubs.trig`, `nanopubs.nq`, `nanopubs.md`.

### Step 5: Build Early Checkpoint & Optional Deep Refinement
Write `/app/output/result.json` matching `/input/case.json`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_nanopub_result.py" \
  --case /input/case.json \
  --nanopubs /app/work/nanopubs/nanopubs.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --trig /app/work/nanopubs/nanopubs.trig \
  --jsonld /app/work/nanopubs/nanopubs.jsonld \
  --nquads /app/work/nanopubs/nanopubs.nq \
  --markdown /app/work/nanopubs/nanopubs.md \
  --out /app/output/result.json
```

If subtle claims require domain synthesis beyond regex patterns, add validated entries via `--override-json /app/work/override.json` (strictly using predicates from `schemas/relation_vocabulary.yaml` and grounded `block_id`/`table_id` anchors) and re-run `build_nanopub_result.py`.

### Step 6: Run 6-Gate Quality Validation
Always run `validate_nanopubs.py` before finishing:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/validate_nanopubs.py" \
  --case /input/case.json \
  --result /app/output/result.json \
  --nanopubs /app/work/nanopubs/nanopubs.json \
  --manifest /app/work/ingested/ingest_manifest.json \
  --nquads /app/work/nanopubs/nanopubs.nq \
  --out /app/work/validation_report.json
```

Verify `"pass": true`, `anchor_grounding_rate == 1.0`, `nquads_valid == true`, and all 6 gates green:
1. `over_broad_assertion` (single claim, `<= 280` chars)
2. `invalid_relation_type` & `assertion_evidence_contradiction`
3. `evidence_not_grounded` & `entity_confusion`
4. `missing_provenance_metadata` (4 graphs + `source_metadata` + `confidence_basis` + `status` honesty)
5. `cross_assertion_conflict` scan
6. `nquads_syntax_valid`

---

## Exception Handling Matrix

| Anomaly / Condition | Required Action & Honest Output |
|---|---|
| `encrypted_pdf` / `corrupt_pdf` | Keep `status: "refused"`, `nanopub_count: 0`; NEVER invent claims. |
| `scanned_no_text` | Run OCR if available; if `ocr_unavailable`, keep `status: "unverifiable"` (or `"partial"` if mixed) and `nanopub_count: 0` on scanned pages. |
| Missing DOI / Year | Explicitly emit `"not_reported"` rather than guessing metadata. |
| Unsupported / Contradictory Claim | Rejected by Gate 2/3; never emit ungrounded triples. |
| Cross-Assertion Conflict | Preserve both grounded nanopublications and emit explicit `cross_assertion_conflict` entry in `conflicts[]`. |

---

## Minimal Illustrative Example (Example Only — Not Formal Test Data)

See [`examples/example_case.json`](examples/example_case.json), [`examples/example_result.json`](examples/example_result.json), and [`examples/example_20_nanopubs.json`](examples/example_20_nanopubs.json) (all explicitly marked as synthetic illustrative examples).

```bash
# Illustrative local invocation example:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_source.py --input ./sample_dir --case examples/example_case.json --out ./work/ingested --ocr auto
PYTHONDONTWRITEBYTECODE=1 python3 scripts/decompose_assertions.py --manifest ./work/ingested/ingest_manifest.json --out ./work/nanopubs
PYTHONDONTWRITEBYTECODE=1 python3 scripts/serialize_nanopubs.py --nanopubs ./work/nanopubs/nanopubs.json --out ./work/nanopubs
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_nanopub_result.py --case examples/example_case.json --nanopubs ./work/nanopubs/nanopubs.json --manifest ./work/ingested/ingest_manifest.json --nquads ./work/nanopubs/nanopubs.nq --out ./work/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_nanopubs.py --case examples/example_case.json --result ./work/result.json --nanopubs ./work/nanopubs/nanopubs.json --manifest ./work/ingested/ingest_manifest.json --nquads ./work/nanopubs/nanopubs.nq --out ./work/validation_report.json
```
