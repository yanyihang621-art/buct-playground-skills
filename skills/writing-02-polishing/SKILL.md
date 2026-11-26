---
name: writing-02-polishing
description: "Polish a supplied academic manuscript or specified sections in English or Chinese. Deliver the complete editable revision, source-aligned change reasons, and any requested bilingual or journal-specific materials. Use for manuscript editing, grant language, abstracts, and reviewer responses."
---

# Academic language polishing

You are the scientific editor. Read the actual draft, decide on every in-scope sentence, and deliver the usable manuscript. A grammar checker can preserve every number while assigning a number to the wrong group. Your second reading is responsible for meaning. For a pure scientific question, answer the question without rewriting a manuscript.

## 1. Identify the assignment and prepare the source

Read `/input/case.json`, the supplied `task.md`, and the actual materials. Determine the manuscript and requested sections, target language, genre/audience, required file names and formats, style guide, term list, and exact `output_contract` fields. If there are several versions, choose the designated draft by the instructions and record its path. Read reference sources only for terminology, definitions, or facts the caller asks you to check.

```sh
SKILL_DIR=/skills/weekly-skill
[ -f "$SKILL_DIR/scripts/build_polish_result.py" ] || SKILL_DIR=/app/submission
RUN="$SKILL_DIR/scripts/build_polish_result.py"
python3 "$RUN" --input-dir /input --work-dir /app/work --output-dir /app/output --prepare-only
```

Read `manuscript.txt`, `materials.json`, `glossary.json` and `language_review.json` under `/app/work/polishing/`. Check that the extraction reaches the last requested paragraph, contains the requested sections and preserves table order. If needed repeat with `--source relative/path` for each requested source. Keep the same source arguments at finalization. Optional language diagnostics locate patterns such as subject–verb disagreement. Confirm each in context and still read through the full text. Use the original PDF or Word page to resolve symbols, equations and table cells. Keep style guides, references and reviewer comments distinct from the author's draft.

## 2. Edit the actual prose

Write your decisions in `/app/work/polishing/edits.json` as UTF-8. Use one record per indexed prose span, including unchanged and out-of-scope spans. Keep their original text and explain briefly when unchanged. Headings can also receive a record with the original markup. Read contiguous spans as a paragraph before deciding. Keep stable IDs and exact source text as anchors. Work in batches, persist decisions, then merge by ID. If you cannot complete every section within budget, deliver the reviewed sections and list the remainder. A draft with missed decisions gets a partial status.

- First fix agreement, article use, dangling modifiers, ambiguous antecedents, nonparallel clauses and misleading punctuation. Then address calques, redundancy and tone. Replace the smallest phrase that solves the problem. Split a long sentence within its original row when this clarifies which condition governs which result.
- Build a term list from the case glossary and actual first uses. Check preferred spelling, capitalization, hyphenation, expansion at first use and short form thereafter across sections. An abbreviation and its expansion can coexist. For a conflicting supplied term, keep the source spelling, identify both rules and request a specific author decision. Never infer a definition solely from initials.
- **Journal or thesis**. Name the measured entity and comparator explicitly when the source supports it. Keep method tense and authorial voice consistent with the surrounding text and the target journal. **Abstract**. Make each sentence serve the stated aim, method, result or interpretation. Retain sample scope, estimates and uncertainty. **Grant**. Distinguish planned work, preliminary observations and expected outcomes. **Rebuttal**. Keep the reviewer's words intact and attribute the author's action and location only when documented. Explicit case guidance takes priority.
- **Semantic read-back**. For each revised claim compare actor/population, operation, comparator, direction, endpoint, value/unit, condition, negation, modality and citation attachment against the original. Check “respectively”, “only”, “at most”, subgroup restrictions, statistical vs practical significance and association vs causation. Prefer retaining the author's content and adding an author query over inventing the missing premise.
- For bilingual deliverables, keep source-language polishing in `polished_text` and populate `translation` with an actual counterpart, including unchanged in-scope sentences. Cross-read values, directionality, scope and term choices in both languages. A translation does not replace the language-polished source. Deliver any explicitly requested language version as a usable artifact.

For long chapters, read the four columns of each line in `review_packet.jsonl` (ID, role, source location, exact text) in batches. To avoid retyping unchanged text after reviewing it, copy the `source_sha256` shown by preparation into the manifest and list the individual IDs in `reviewed_unchanged`. Put edited sentences, bilingual decisions, and scope exceptions in `sentences`. Never mark unseen paragraphs as reviewed. A changed draft or brief requires a fresh review. Check the glossary for attested acronym expansions, especially the first occurrence. Read names and measurements as pairs whenever a clause uses “respectively” or “分别”.

Each changed row needs the exact original, the new sentence, a specific type and a concrete reason tied to the phrase, grammar or meaning. Use separate reasons for independent corrections. The following is a **synthetic interface example**. Real entries must use the supplied manuscript.

```json
{"sentences":[{"sent_id":"sent_0001","source_text":"The paired measurements was repeatable at 8 Hz.","polished_text":"The paired measurements were repeatable at 8 Hz.","edit_type":"grammar:agreement","rationale":"The plural subject ‘measurements’ takes ‘were’. The sampling frequency stays attached to the same measurement."}]}
```

Use a top-level `uncertainties` list for author queries with a source location, the issue, and a concrete question. Use `uncertain: true` on the corresponding sentence. If no register is specified, use the draft's existing genre and voice. A single unresolved issue should not prevent editing the rest of the document.

## 3. Build, inspect and repair

```sh
python3 "$RUN" --input-dir /input --work-dir /app/work --output-dir /app/output \
  --edits-json /app/work/polishing/edits.json
```

For case-specific fields supply grounded values in the top-level `contract_values` mapping. Match the types, language, nesting and filenames in the actual contract. If it requests a structured revision table or the full text, supply that data in the requested form. For bilingual alignment set `bilingual_required: true`. If a Word manuscript is requested set `deliverables: ["docx"]`. External glossary or style instructions still apply when a case uses different field names.

Set `artifact_names` in the manifest if the case gives exact filenames. For example, `{"polished_manuscript":"revised.md","revision_report_md":"changes.md"}` names the actual edit and audit files. Use `polished_docx` for Word, `preview_pdf` for the rendered preview, or `bilingual_md` for the alignment. If only a section needs translation, record other rows with `edit_type: "out_of_scope"` and unchanged wording. Report unresolved language diagnostics only after reading the corresponding passage and noting the reason.

Read `/app/output/result.json`, follow its artifact paths, and inspect the actual editable manuscript, change report, conflicts, QA violations and any requested preview. Use the sentence IDs to repair unsafe changes and rerun with the same source and corrected decisions. Reopen the source/chosen target pair where numerical values, units or groups repeat. Recheck subject–value attachment even when the token checker passes. Open rendered pages for Word output to spot broken tables, clipped symbols or a blank final page. When a reviewed correction is impossible, retain the affected source text and state the precise limitation. Finish by checking every requested output field and artifact against the case. Keep only the final version of each deliverable under `/app/output`.

The platform supplies the baseline and measures the comparison on its own cases. The synthetic tests in this package exercise the tooling; the official editing step requires an actual review of the supplied material.
