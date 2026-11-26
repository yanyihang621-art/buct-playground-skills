---
name: reading-14-nanopublication
description: Atomic scientific claim decomposition and 4-graph Nanopublication extraction (Head, Assertion, Provenance, PubInfo) using a controlled 18-predicate vocabulary, minimal sufficient evidence grounding, source metadata/DOI tracking, deduplication, contradiction detection, zero-dependency W3C JSON-LD/TriG/N-Quads serialization, and 6-gate deterministic quality validation. Use under Harbor /input and /app/output contracts.
---

# Scientific Nanopublication Extraction (`reading-14-nanopublication`)

## Run a case

Read the case questions, target sections and named deliverables first. In Harbor run:

```sh
python3 -B /skills/weekly-skill/scripts/run_case.py
```

This writes an early extraction checkpoint and then exports and validates the result. Inspect `/app/output/result.json` and `/app/work/nanopubs/validation_report.json`. For each question compare the actual source with the extracted answer, including findings without a dictionary match. Record omissions and repairs. Use `--reviewed /app/work/reviewed.json` for evidence-checked claims and `--answers /app/work/answers.json` for case-specific result fields. Rerun the same command with those arguments to regenerate the JSON and RDF together. A nonzero validation exit preserves the partial result and reports which fields or evidence need work. Local runs accept `--input`, `--work` and `--out`.

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

## Runtime workflow

Read the case prompt, target sections, requested output fields and deliverable names. Work through the original sources and select the claims needed for the case. For a synthesis, include distinct findings, null results and stated limitations. A vocabulary match only supplies a candidate.

~~~bash
SKILL_DIR=/skills/weekly-skill
[ -f "$SKILL_DIR/scripts/decompose_assertions.py" ] || SKILL_DIR=/app/submission
export PYTHONDONTWRITEBYTECODE=1
WORK=/app/work/nanopubs
OUT=/app/output
mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/ingest_source.py" --input /input --case /input/case.json --out "$WORK/input" --ocr auto
python3 "$SKILL_DIR/scripts/decompose_assertions.py" --manifest "$WORK/input/ingest_manifest.json" --out "$WORK"
python3 "$SKILL_DIR/scripts/build_nanopub_result.py" --case /input/case.json --nanopubs "$WORK/nanopubs.json" --manifest "$WORK/input/ingest_manifest.json" --out "$OUT/result.json"
~~~

Inspect the ingestion manifest and its uncertainties. Discovery includes PDF, Markdown, TXT, JSON and JATS XML recursively. Named files take precedence. Article paragraphs embedded in case material fields can be used beside external papers. Text files provide physical lines. PDFs provide page and coordinates. JSON passages and regular data cells retain a JSON Pointer. JATS paragraphs and regular data cells retain an element path. Use the paper's paragraph ID, section, or page if supplied. Case instructions and reference lists are outside the primary evidence scope. Inspect unfamiliar JSON layouts, spanning tables or image-only figures directly before making claims from them.

### Review the claims against the sources

1. Read each requested section and any referenced table, including headers, units and operating conditions. Find important results that the deterministic patterns missed. Preserve the document's actual subject, relation direction, value, uncertainty and attribution. Distinguish an association from a causal assertion and a hypothesized relation from an observed relation.
2. Use the smallest exact supporting clause. If a preceding condition governs several clauses, store the condition's exact text separately in provenance.condition_quote. For table claims, identify the actual row, column and value. Each item carries the source file/hash and a block or table location. Inspect repeated appearances before merging; retain scope differences in the report.
3. Independently review every candidate and fill in missed claims. Supply a revised list for each changed paper in a JSON object keyed by its manifest paper_id, e.g. {"paper_1": [ ...full records... ]}. Copy the candidate structure or follow schemas/nanopublication.schema.json. For a prose assertion supply subject, controlled predicate, object, conditions, grammatical polarity (affirmative/negative), modality (asserted/hedged), and a faithful atomic_statement. The polarity field records the wording of the claim. Use the original phrase to preserve distinctions such as “no significant difference” versus proof of equivalence. Add a minimal_evidence_snippet, location, evidence_type, original source_metadata, and confidence basis. Omit an unsupported claim and record the gap. Provide the complete list for each changed paper, including unchanged claims. Papers absent from the review stay in the collection.
4. If revisions were required, save the review as /app/work/nanopubs/reviewed.json and regenerate the bundle. The program derives graph identifiers and recomputes duplicates and conflicts.

For new prose claims, the same per-paper list also accepts compact rows alongside unchanged full records. Read the actual block ids and text from the paper's `document_json` in the manifest. The following row illustrates the format with fictional material. The builder copies the real source metadata and coordinates and checks the quote and separate condition against that block. Use the original modal phrasing in the statement. Choose the evidence type from the field dictionary and justify confidence individually.

~~~json
{"paper_1":[{"block_id":"/paragraphs/0/text","quote":"Band C reached a width of 7.4 nm.",
  "subject":"Band C","predicate":"achieves_metric","object":"a width of 7.4 nm",
  "statement":"Band C reached a width of 7.4 nm.","conditions":[],
  "evidence_type":"direct_textual","confidence_score":0.9,
  "confidence_basis":"The authors explicitly give the band, the measured width and its unit in this paragraph."}]}
~~~

~~~bash
python3 "$SKILL_DIR/scripts/decompose_assertions.py" --manifest "$WORK/input/ingest_manifest.json" --reviewed "$WORK/reviewed.json" --out "$WORK"
~~~

### Deliver the verified collection

The bundle is the source of the final result and all renderings. Export the reviewed bundle, produce the final contract fields and run the auditor on that same bundle. Use --override-json with build_nanopub_result.py for supported, case-specific synthesis fields. Ground these in the reviewed claims and resolve missing_contract_field and contract_shape_mismatch entries, or record the concrete missing evidence and partial status.

~~~bash
python3 "$SKILL_DIR/scripts/serialize_nanopubs.py" --nanopubs "$WORK/nanopubs.json" --out "$OUT"
python3 "$SKILL_DIR/scripts/build_nanopub_result.py" --case /input/case.json --nanopubs "$WORK/nanopubs.json" --manifest "$WORK/input/ingest_manifest.json" --trig "$OUT/nanopubs.trig" --jsonld "$OUT/nanopubs.jsonld" --nquads "$OUT/nanopubs.nq" --markdown "$OUT/nanopubs.md" --out "$OUT/result.json"
python3 "$SKILL_DIR/scripts/validate_nanopubs.py" --case /input/case.json --nanopubs "$WORK/nanopubs.json" --manifest "$WORK/input/ingest_manifest.json" --result "$OUT/result.json" --nquads "$OUT/nanopubs.nq" --out "$WORK/validation_report.json"
~~~

Inspect any validation failure before delivery. Check exact source quotes and condition spans, entity direction, contradiction reports, and final artifact names. A reported disagreement can be correct when both independent observations are grounded. In RDF, the assertion graph reifies qualified statements so downstream consumers can inspect their subject, predicate, object, modality, polarity and scope. For a grounded unconditional affirmative claim, it also emits the direct relation.

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

The included `examples/sample_paper.md` and `examples/example_case.json` form a runnable synthetic case. From the Skill directory, run `python3 -B scripts/run_case.py --input examples --case examples/example_case.json --work /tmp/np-example/work --out /tmp/np-example/output`. The run produces the two optical-filter assertions illustrated in `examples/example_result.json`; generation timestamps vary. `examples/example_20_nanopubs.json` contains twenty short review cards with addressable source sentences.

```bash
# Illustrative local invocation example:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_source.py --input ./examples --case examples/example_case.json --out ./work/ingested --ocr auto
PYTHONDONTWRITEBYTECODE=1 python3 scripts/decompose_assertions.py --manifest ./work/ingested/ingest_manifest.json --out ./work/nanopubs
PYTHONDONTWRITEBYTECODE=1 python3 scripts/serialize_nanopubs.py --nanopubs ./work/nanopubs/nanopubs.json --out ./work/nanopubs
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_nanopub_result.py --case examples/example_case.json --nanopubs ./work/nanopubs/nanopubs.json --manifest ./work/ingested/ingest_manifest.json --nquads ./work/nanopubs/nanopubs.nq --out ./work/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_nanopubs.py --case examples/example_case.json --result ./work/result.json --nanopubs ./work/nanopubs/nanopubs.json --manifest ./work/ingested/ingest_manifest.json --nquads ./work/nanopubs/nanopubs.nq --out ./work/validation_report.json
```
