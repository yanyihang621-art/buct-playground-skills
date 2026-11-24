---
name: reading-15-knowledge-graph
description: Evidence-grounded multi-paper scientific Knowledge Graph construction supporting Harbor revised JATS/JSON manifests (corpus-manifest.json, primary-material.json, secondary-material.json, secondary-publisher.xml) and PDFs, per-paper provenance isolation, conservative entity alignment with explicit do-not-merge instance protection, incremental updates preserving existing node IDs, >= 3 executable graph queries, GraphML/DOT/Mermaid exports, and deterministic grounding validation. Use under Harbor /input and /app/output contracts.
---

# Scientific Literature Knowledge Graph (`reading-15-knowledge-graph`)

Deterministic Python pipeline for multi-paper scientific Knowledge Graph construction focused on four guarantees:
1. **抽得准 (Grounded Entities)**: Extracts verbatim `Researcher`, `Material`, `Method`, `Task`, `Metric`, `Condition`, and `Finding` nodes strictly from input text/tables without fabricating concepts.
2. **连得对 (Typed Evidence-Backed Relations)**: Emits directed relations with `evidence_strength` (`direct_author_evidence`, `direct_tabular_evidence`, `prior_work_citation`, `indirect_speculation`) and operating conditions — never promoting citations or speculation to direct author evidence.
3. **对得齐 (Conservative Alignment & Do-Not-Merge Protection)**: Merges exact acronym/casing equivalents (`merge_decisions`) while keeping paper-scoped experimental instances (`MaterialInstance` / `MethodInstance` + `do_not_merge_records`) separate when generic terms appear across papers with distinct conditions or outcomes.
4. **查得到、回得去 (Executable Queries & Strict Provenance)**: Executes >= 3 real graph queries returning subgraphs + evidence (and honest `no_direct_evidence_unverifiable` results), preserves base node IDs during incremental updates, and binds every node/edge to its own paper's `(source_id, paragraph_id, location_type, quote_snippet)`.

---

## Critical Provenance & Revised Input Rules

- **Multi-Paper Revised Input Format**: When `/input` contains `corpus-manifest.json`, `primary-material.json`, `secondary-material.json`, `material.json`, or JATS XML (`secondary-publisher.xml`), `ingest_corpus.py` binds each paper's own `source_id`, `doi`, `title`, `authors`, `source_hash`, and `license` independently. **Never apply Paper 1's root DOI/title/authors to Paper 2.**
- **No Fake PDF Pages**: For JATS XML or JSON paragraph inputs (`location_type: "jats_paragraph_id"` / `"json_paragraph_id"`), `pdf_page` MUST remain `null`. Only actual `.pdf` inputs populate `pdf_page`.
- **Same-Paper Multi-Format Guard**: Multiple file formats (e.g., JSON + Markdown) of the same paper are deduplicated and never counted as cross-paper corroboration.

---

## Path Discovery & Standard 6-Step Workflow

- Resolve `SKILL_DIR` dynamically: check `/skills/weekly-skill` first; if absent, use the directory containing this `SKILL.md` (or `/app/submission`). Never hardcode Windows paths.
- Prefer `python3` (fallback `python`) with `PYTHONDONTWRITEBYTECODE=1`.

### Step 1: Ingest Corpus (`/input`)
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/ingest_corpus.py" \
  --input /input \
  --case /input/case.json \
  --out /app/work/ingested \
  --ocr auto
```

### Step 2: Build (or Incrementally Update) Knowledge Graph
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_kg.py" \
  --corpus /app/work/ingested/corpus.json \
  --out /app/work/kg
# For incremental updates preserving base node IDs, add: --base-kg /app/work/base_kg.json
```

### Step 3: Execute >= 3 Real Graph Queries
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/query_kg.py" \
  --kg /app/work/kg/kg.json \
  --run-default-suite \
  --out /app/work/kg/executed_queries.json
```
Additional targeted queries can be run with `--query-type entity_neighborhood --entity "<Name>"` or `--query-type relation_evidence_probe --entity-a "<A>" --entity-b "<B>"`.

### Step 4: Export GraphML, DOT & Markdown Report
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/export_kg.py" \
  --kg /app/work/kg/kg.json \
  --queries /app/work/kg/executed_queries.json \
  --out /app/work/kg/exports
```

### Step 5: Assemble `/app/output/result.json`
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/build_kg_result.py" \
  --case /input/case.json \
  --kg /app/work/kg/kg.json \
  --queries /app/work/kg/executed_queries.json \
  --graphml /app/work/kg/exports/kg.graphml \
  --markdown /app/work/kg/exports/kg_report.md \
  --out /app/output/result.json
```
(Add `--override-json /app/work/override.json` if `/input/case.json` requires custom task answers.)

### Step 6: Validate Grounding, Isolation, Queries & GraphML XML
```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$SKILL_DIR/scripts/validate_kg.py" \
  --case /input/case.json \
  --result /app/output/result.json \
  --kg /app/work/kg/kg.json \
  --corpus /app/work/ingested/corpus.json \
  --queries /app/work/kg/executed_queries.json \
  --graphml /app/work/kg/exports/kg.graphml \
  --out /app/work/validation_report.json
```

---

## Exception Handling & Self-Check Checklist

- `encrypted_pdf` / `corrupt_pdf` -> `status: "refused"`, 0 nodes/edges.
- `scanned_no_text` without OCR -> `status: "unverifiable"`, 0 nodes/edges.
- Verify `"pass": true`, `evidence_grounding_rate == 1.0`, `graphml_valid == true`, `executed_queries_count >= 3`, no leaked Paper 1 DOI on Paper 2, and no fake `pdf_page` on JATS paragraphs.

---

## Minimal Illustrative Example (Example Only — Not Formal Test Data)

See [`examples/example_case.json`](examples/example_case.json), [`examples/example_result.json`](examples/example_result.json), and [`examples/example_kg_queries.json`](examples/example_kg_queries.json).

```bash
# Illustrative local invocation example:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/ingest_corpus.py --input ./sample_corpus --case examples/example_case.json --out ./work/ingested
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_kg.py --corpus ./work/ingested/corpus.json --out ./work/kg
PYTHONDONTWRITEBYTECODE=1 python3 scripts/query_kg.py --kg ./work/kg/kg.json --run-default-suite --out ./work/kg/executed_queries.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/export_kg.py --kg ./work/kg/kg.json --queries ./work/kg/executed_queries.json --out ./work/kg/exports
PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_kg_result.py --case examples/example_case.json --kg ./work/kg/kg.json --queries ./work/kg/executed_queries.json --graphml ./work/kg/exports/kg.graphml --markdown ./work/kg/exports/kg_report.md --out ./work/result.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_kg.py --case examples/example_case.json --result ./work/result.json --kg ./work/kg/kg.json --corpus ./work/ingested/corpus.json --queries ./work/kg/executed_queries.json --graphml ./work/kg/exports/kg.graphml --out ./work/validation_report.json
```
