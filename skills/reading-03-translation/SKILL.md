---
name: reading-03-translation
description: "Translate English research papers into aligned academic Chinese. Preserve paragraph, evidence and table locations, terminology, numbers, formulae, citations, negation and uncertainty. Use for verifiable bilingual scientific reading."
---

# Translate a research paper into aligned Chinese

Read `/input/task.md` and `/input/case.json`. Identify the assigned paper(s), requested sections, target language, deliverable types and `output_contract`. Source filenames, DOI, headings and passage IDs disambiguate the paper from reference attachments. Use the original wording for every result and mark unreadable material at its location.

## Prepare the source and terminology

Run from any working directory. Set `SKILL_DIR` to the directory containing this `SKILL.md` when running locally.

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
python3 "$SKILL_DIR/scripts/build_translation_result.py" --input /input --case /input/case.json \
  --work-dir /app/work --prepare-only --result-out /app/output/result.json
```

Read the selected paper list, `/app/work/translation/segments.json` and `glossary.json`. Check the requested scope against the index, including titles, captions, column labels, notes, cross-page paragraphs and image/table warnings. The preparer supports a case’s structured `translation_scope` with `paragraph_ids`, `segment_ids`, `block_ids`, `sections`, `pages` or inclusive `page_range: [first, last]`. For a range described only in prose, save a working copy of the case with the corresponding selector derived from the initial segment index and use the same case copy for both commands. Keep the original case ID, contract and source references. Inspect the PDF or source files when a clipped equation, table cell, pronoun or ambiguous acronym affects interpretation. PDF page numbers refer to physical pages; for editable/text sources use the given paragraph, line, JSON pointer or table coordinates.

Preparation publishes a first, source-anchored result in a fresh output directory, even when prose is still untranslated. Reopening the index preserves an existing result. On a long paper, read each window and its adjacent context:

```bash
python3 "$SKILL_DIR/scripts/read_batch.py" --segments /app/work/translation/segments.json --start 0 --count 16
```

Translate the listed IDs, then use `next_start` for the next window. The two context excerpts resolve pronouns across the window boundary. Each segment's token map identifies its original numbers, units and references.

## Author the translation

Use the segment order to translate paragraph by paragraph. Read adjacent segments together to resolve antecedents, then write each translation back to its original ID. For longer papers, accumulate completed sections in a single JSON draft and save it after each section. Keep every sentence, including the object of a contrast or negation, the direction of comparisons, modal force, units and qualifying conditions. Retain names and formula-only material verbatim when appropriate. Consult the case glossary first and choose consistent domain-specific equivalents for new terms. An abbreviation may have different expansions in different papers; lock only unambiguous document-wide terms.

Write `/app/work/translations.json` with a `segments` list. Use each segment's `masked_text` as the source for drafting. Keep its `__NUM_001__`, `__EQ_001__`, `__CIT_001__` and `__REF_001__` placeholders at the matching fact, including repeated values. Placeholders are local to each segment. Supply translations for table labels in the same list as body prose. Use `rationale` for a significant terminology choice, referent or ambiguity. Mark a doubtful translation with `uncertain: true` and an explanation. The assembler retains missing prose as an anchored gap.

Illustrative entry for the synthetic source “Only the upper layer changed at 12 Hz.” Replace IDs, texts and terms with the actual case values.

```json
{
  "segments": [
    {"segment_id": "seg_0001_p1_b01", "target_text": "在 __NUM_001__ 下仅上层发生了变化。", "rationale": "only 限定发生变化的层。"}
  ],
  "glossary_additions": {"upper layer": "上层"}
}
```

For a multi-sentence source, you can use `sentence_targets` in place of `target_text`: give one Chinese string per source sentence, in the same order, with its punctuation. This preserves alignment when Chinese needs different punctuation. If you supply both forms, the joined sentence translations must agree with the passage. Include table body text as well as captions and column headers.

For a case-specific required field with no built-in mapping, use `contract_values` in this JSON and populate it with the requested type and content from the source. Include actual answers and passage locations if the case requests them.

For longer assignments, save each completed window in a separate file, e.g. `/app/work/drafts/001.json`, with the same `segments` format. Pass the directory as `--translations-json /app/work/drafts`. Run the build after the first batch and at section boundaries. To revise a passage, edit its original batch file so that each ID occurs once. The current bilingual draft, recoverable gaps and QA findings will be available throughout the run.

## Build and verify

```bash
python3 "$SKILL_DIR/scripts/build_translation_result.py" --input /input --case /input/case.json \
  --work-dir /app/work --translations-json /app/work/translations.json \
  --result-out /app/output/result.json --summary
```

Open `result.json` and its referenced `translation/qa_report.json` and `translation/bilingual.md`. Resolve flagged IDs against the source, update the draft, and rerun the command. Check the subject, predicate, comparison group and condition in each high-risk sentence manually. The deterministic checks compare anchors, terms, quantities, placeholders and polarity cues; fluent but inaccurate translations still require semantic review. For translated captions, compare column order, numerical values and citations with the table source. Verify that all required case artifacts exist under `/app/output`. If a source is inaccessible, run the final command with an empty segment list to preserve the reason and any recoverable evidence.
