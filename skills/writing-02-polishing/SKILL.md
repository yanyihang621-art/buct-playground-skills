---
name: writing-02-polishing
description: "Academic language polishing Skill (EN & ZH-CN) across registers (journal_article, thesis, grant_proposal, rebuttal, abstract) enforcing numeric/formula/citation invariance, hedging/causality/negation conservation, locked terminology, minimal edits, and sentence-level diff rationales."
---

# Writing 02 — Academic Language Polishing (`写作02｜学术语言润色`)

This Skill solves the four core requirements of verifiable academic language polishing in Harbor execution environments:

1. **改得对 (Grammar, Collocation & Register)**: Fixes subject-verb agreement, Chinglish calques (`More and more`, `As is known to all`, `众所周知`), colloquialisms (`a lot of`, `pretty good`), and promotional hype (`revolutionary breakthrough`, `彻底解决`).
2. **说得像 (Register & Terminology Consistency)**: Extracts domain terms and acronyms (`scripts/extract_manuscript_terms.py`), merges `custom_glossary` from `case.json`, flags conflicting definitions, and adapts phrasing to the target register (`schemas/style_profiles.yaml`).
3. **没改坏 (Semantic & Quantitative Invariance)**: Locks numbers+units, equations (`$...$`), citations (`[1]`, `\cite{...}`), cross-references (`Fig. 1`, `Table 2`), negation polarity (`not`, `never`, `without`, `未`, `不`), hedging intensity (`may`/`suggest` != `prove`), and correlational vs causal claims (`associated with` != `caused`).
4. **讲得清 (Traceable Sentence Diffs)**: Outputs a sentence-by-sentence `revision_report.md` (`原文 / 改文 / 修改类型 / 修改理由`) and `patch.diff` alongside editable `polished_manuscript` (`scripts/polish_sentences.py`, `scripts/verify_polish.py`).

## Harbor Execution Workflow

- **Mode A (Two-Stage Agent-Refined Polishing, Recommended for Complex Prose)**:
  1. Run `extract_manuscript_terms.py` + `segment_and_guard.py` to generate `/app/work/polishing/glossary.json` and `guarded_sentences.json`.
  2. Author refined `polished_text` entries per `sent_id` in `/app/work/polishing/edits.json` preserving all `protected_tokens`, `hedge_tier`, and `relation_type`.
  3. Run `build_polish_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json --edits-json /app/work/polishing/edits.json`.
- **Mode B (Deterministic End-to-End Pipeline)**:
  ```bash
  python scripts/build_polish_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json
  ```

## Integrity Guardrails

- NEVER strengthen tentative conclusions (`may suggest` -> `proves`), convert correlation into causation, drop negation, or alter numerical data/units/citations.
- Conflicting glossary rules or unresolved QA violations downgrade `status` to `"partial"` with explicit `uncertainties`; missing manuscript input returns `"refused"`.
