---
name: writing-08-intro-related-work
description: Write and revise a publication-ready introduction and related work from supplied research materials. Compare methods on a common axis, derive a bounded gap, attach literature and author evidence to claims, and deliver the requested editable files.
---

# 引言与相关工作写作

Read the actual case, task instructions and source material. Deliver a manuscript in the requested language, genre, length, citation style and filenames. Use the process below for a new draft or revision. If the task asks for a narrower change, preserve the supplied manuscript and edit only the requested portion.

## Open the sources

```sh
S=/skills/weekly-skill
[ -f "$S/scripts/manuscript_workbench.py" ] || S=/app/submission
W=/app/work/intro_packet
O=/app/output
python3 "$S/scripts/manuscript_workbench.py" prepare --input-dir /input --work-dir "$W" --output-dir "$O"
```

Read the case's `output_contract` and the brief produced by `prepare`. Check the complete case and `task.md` for audience, output language, citation style, format, and requested revision. Separate prior literature from the authors' own records. The reading packet attaches an `E…` ID, source file and native location to each short excerpt. For long inputs use `read --find TERM --limit 6` or `read --source RELATIVE_PATH --offset N` with the same `--work-dir`. Read neighbouring units to interpret an excerpt. For ambiguous PDF tables, mathematical signs or an image-only page, inspect the original page. Preserve material shortfalls and conflicting evidence.

## Compose the actual manuscript once

Write `$W/draft.md`. Use the requested headings or continuous paragraphs. Write the paper yourself. Read the draft aloud as if its evidence ledger were unavailable.

- **Background.** Start with the scientific decision, observable or operating constraint. Bind externally testable statements and numbers to the relevant supplied work.
- **Related work.** Compare representative approaches under a named dimension such as population, measurement, supervision or evaluation protocol. Tell the reader what each design could establish, and what it held fixed. Explain incompatible metrics or endpoints before comparing values. Describe conflicting findings under their respective conditions. Cite each source next to its claim.
- **Gap.** Specify the missing comparison or condition that the cited designs leave unresolved. Explain how the limitations of two approaches fit together when there are two relevant works. Scope the inference to the reviewed sources. An unavailable full text is a material limitation.
- **Contribution.** Name the author's actual design, comparator, figure/table, result or protocol answering the gap. Retain units, sample and regime. State essential interpretation limits beside the result, especially for observational comparisons, proxy outcomes or single settings. When only a protocol is supplied, limit the contribution to study design.

End a supported sentence or short paragraph with an evidence comment. Copy the **actual IDs** from the current reading packet. The comment applies to the prose since the preceding comment or blank line. It is removed from the delivered manuscript. Schematic syntax (ellipses below stand for actual excerpt IDs):

```text
<background sentence and citation><!--ev background E…-->
<comparable findings with citations><!--ev comparison E… E…-->
<bounded question grounded in both studies><!--ev gap:G1 E… E…-->
<author's contribution and empirical setting><!--ev contribution:G1 E…-->
<essential boundary visible to the reader><!--ev limit E…-->
```

Use as many evidence links as the claims require. Include both sides of a comparison. A single-paper case may support a narrower design boundary. Support author contributions from the author's own evidence. Preserve supplied bibliography metadata. Reuse a packet citation key such as `[@key]` or the requested display style, and ensure the bibliography resolves each in-text citation. Keep exact source quotations in their original language.

If the packet identifies a source but cannot extract reliable bibliographic fields or route labels, add `paper_annotations` in `$W/notes.json`. Each card can use `units` with actual E IDs, plus `cite_key`, verified title, authors, year, venue, method route, evaluation setting, finding and limitation. Distinguish actual limitations from your synthesis. Include concise `change_records` for meaningful revisions and `contract_fields` for case-specific fields not produced automatically. Use `artifact_names` for named deliverables. Optional detailed card syntax is in `examples/editorial_notes.md`.

## Deliver and verify

To reuse an already-written passage in a case field, set e.g. `"contract_bindings":{"study_limit":"limit"}` in the notes when the case requires `study_limit`. The selector can also name a gap such as `gap:G1`. Use `contract_fields` for values that need a separate structure. The complete reading packet summary is saved to `$W/reading.md`.

```sh
python3 "$S/scripts/manuscript_workbench.py" finish --draft "$W/draft.md" --work-dir "$W" --output-dir "$O"
# When using notes, append: --notes "$W/notes.json"
# Explicit exports, if required: --format docx --format pdf --format png
```

Read `result.json` and the actual manuscript, reference list and requested artifacts in `/app/output`. Review whether each quote entails the sentence in context, whether comparisons share an endpoint and whether each contribution reaches only as far as the supporting experiment. Open editable documents and inspect actual rendered previews when requested. Resolve reported errors and run `finish` again in the same work directory. If the task is genuinely partial, keep the useful manuscript and identify the exact missing file, passage or tool. The case-specific contract determines the final fields and deliverables.

If the workbench or an optional tool cannot run, continue with the available materials and the shell or Python standard library. Save the authored manuscript under the requested filename, write the original case ID and case-specific fields to `/app/output/result.json`, and record actual sources, revisions and remaining limits. Existing external workflows can still use `scripts/build_intro_rw_result.py --draft-file … --analysis …`.
