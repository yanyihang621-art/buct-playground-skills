---
name: writing-08-intro-related-work
description: Write or revise a source-grounded academic introduction and related work, organize competing approaches, attach citations to claims, and connect a scoped research gap to the author's actual method and results. Supports Chinese and English, draft revision, citation checks, and missing or conflicting materials.
---

# 引言与相关工作写作

Deliver a manuscript an author can edit, in the case's requested language, length, citation style and format. The executing agent reads the sources, decides what follows from them and writes the prose. The scripts index materials, preserve quotations, build the comparison table and check the deliverables.

## 1. Read the case before choosing a structure

Read `/input/case.json`, the supplied `task.md`, and the input tree. Note the audience, journal, language, exact filenames, original `output_contract` keys/types and which artifacts need a preview. Identify which materials describe the **present study** and which describe **prior work**. A domain label, a filename, or an instruction to demonstrate improvement supplies no research result.

```sh
SKILL_DIR=/skills/weekly-skill
if [ ! -f "$SKILL_DIR/scripts/ingest_materials.py" ]; then SKILL_DIR=/app/submission; fi
WORK=/app/work/intro_rw
mkdir -p "$WORK" /app/output
python3 "$SKILL_DIR/scripts/ingest_materials.py" --input-dir /input --case /input/case.json --out "$WORK/materials_index.json"
```

The index contains `papers`, `study_facts` and `passages`. Each passage has a stable `source_id`, relative `path`, `locator` and `text`. Read relevant passages in context. For a long index, use Python to select file/locator and a few adjacent passages at a time. Open original tables, figure captions, PDF pages and DOCX tables when reading order, signs, superscripts or units are uncertain. For scans, use available page rendering/OCR and inspect the image. If a number remains unreadable, leave it out and identify the page in the uncertainty record. Bibliography-only records establish metadata; look for the supporting abstract, full text or supplied notes before asserting a result.

## 2. Build the argument from the materials

Work in `$WORK/analysis.json`. The field shapes and a worked comparison are in `examples/editorial_notes.md`. Keep evidence rows short. An anchor of the form `{"source_id":"…","quote":"…"}` must quote a contiguous passage from the index. Use the source's own language in the quote and the requested language in the manuscript.

1. **Background.** Name a specific decision or phenomenon, the observable that would resolve it, and a documented constraint that makes existing evidence insufficient. Open on that constraint. Tie each external fact, number, and term whose definition matters to its source.
2. **Related work.** Choose a comparison axis relevant to the target problem (e.g. measurement scale, supervision, acquisition setting). For each representative approach record the setting, outcome measured, finding and scope. Write a synthesis sentence answering a concrete question across studies, followed by the condition under which the comparison changes. Do not rank methods across different datasets, endpoints or units without a supplied common comparator. With one source describe its tested boundary. With conflicting findings give the respective conditions and leave any unexplained conflict open.
3. **Gap.** Write the smallest unanswered question justified by the reviewed studies. Identify the boundary of each supporting source and how their combined coverage leaves a specific decision unresolved. Distinguish a study's explicit limitation from your inference about what its design tested. Qualify claims to the supplied literature. Missing text or an absent DOI is a material gap, never evidence of scientific novelty.
4. **Contribution.** Connect each contribution to a particular gap and to an author-owned method section, table, figure or result. Preserve the population, comparator, units, uncertainty and regime. If only a protocol is supplied, describe what it tests. If there is no author evidence, deliver the literature-supported sections and flag the missing contribution evidence. Do not promote proposed work to a demonstrated finding.

Use `paper_annotations` for raw sources or refined reading notes, reusing existing `cite_key` where possible. Write `research_gaps`, `contributions`, and sentence-level `claim_evidence` for the substantive assertions. Capture both sides of a comparison and keep prior work separate from the author's evidence. Preserve actual metadata; identify missing metadata explicitly.

## 3. Write the manuscript, then audit it

Write `$WORK/agent_draft.md` yourself. Remove working IDs, QA commentary and ledger prose from publishable paragraphs. Connect background, comparison, gap and contribution with transitions. Follow the case if it requires continuous paragraphs, a combined Introduction, numbered citations, author–year citations or bilingual text. For unconventional headings use `section_spans` in the analysis to point to exact sentences in the manuscript. Place citations immediately after the clauses they support. Verify that every in-text citation resolves to the correct work and references agree on author, year, title and venue. Keep a concise record of what was revised and which uncertainties the author must resolve.

Save useful partial work early. Fill case-specific output fields in `contract_fields` with real contents or actual artifact names, respecting their requested types. For required named manuscript outputs, set `artifact_names` and pass requested format flags. Package using the actual authored draft:

```sh
python3 "$SKILL_DIR/scripts/build_intro_rw_result.py" \
  --input-dir /input --case /input/case.json --work-dir /app/work --output-dir /app/output \
  --materials-file "$WORK/materials_index.json" --analysis "$WORK/analysis.json" \
  --draft-file "$WORK/agent_draft.md"
```

Inspect `/app/output/result.json`, the manuscript, comparison matrix, evidence and QA artifacts. Read the cited passage for each flagged sentence and amend prose or evidence, then rerun. A matching quotation locates the source; **check entailment in context**, including negation and scope. Reopen requested editable files and inspect real rendered previews. Compare every original contract field and artifact path against the files on disk. For unmet requirements keep useful work, record why and return an honest partial result. The command without an authored draft is a diagnostic scaffold for local development.
