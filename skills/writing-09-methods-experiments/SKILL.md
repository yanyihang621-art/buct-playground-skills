---
name: writing-09-methods-experiments
description: "Verifiable Methods & Experiments section writing Skill extracting steps, setup, datasets/splits, baselines, and parameter provenance across configs, logs, and source code while flagging cross-source conflicts, missing reproducibility fields, and concrete failure modes."
---

# Writing 09 — Methods & Experiments Writing (`写作09｜方法与实验章节写作`)

This Skill solves the four core challenges of Methods & Experiments drafting in Harbor execution environments:

1. **写得全 (Six-Element Completeness)**: Covers Method Steps (input -> operation -> output -> criterion), Experimental Setup (train/val/test separation), Parameters & Provenance, Datasets & Splits, Baselines & Controls, and Reproducibility & Limitations (`schemas/methods_rules.yaml`).
2. **写得准 (Cross-Source Config/Log/Code Consistency)**: Parses `.yaml`/`.json` configs, `.log`/`.txt` experiment logs, `.py` scripts, and `experiment_spec.json` (`scripts/extract_experiment_facts.py`), aligns every prose value to `parameter_table.md`, and flags cross-source conflicts instead of guessing.
3. **做得着 (Executable Reproducibility)**: Generates a structured reproducibility checklist (`software`, `hardware`, `seeds`, `repeats`, `command`, `dataset_version`) and baseline matched-budget comparison (`scripts/draft_methods_section.py`).
4. **说得住 (Explicit Author Questions & Concrete Limitations)**: Emits targeted `author_questions` for every conflict or missing reproducibility item, rejects cliché limitations (`there is still room for improvement`), and audits all 6 invariants (`scripts/verify_methods_exp.py`).

## Harbor Execution Workflow

- **Mode A (Two-Stage Fact Extraction + Agent Prose Refinement, Recommended)**:
  1. Run `extract_experiment_facts.py` to generate `/app/work/methods/experiment_facts.json` and `parameter_table.json`.
  2. Author refined prose in `/app/work/methods/agent_draft.md` preserving all 6 required headings, exact parameter values, conflict disclosures, and concrete failure-mode limitations.
  3. Validate and package via `build_methods_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json --draft-file /app/work/methods/agent_draft.md`.
- **Mode B (End-to-End Automated Run)**:
  ```bash
  python scripts/build_methods_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json
  ```

## Integrity & Anti-Fabrication Guardrails

- NEVER invent unrecorded seeds, hardware specs, dataset versions, or parameter values to make prose smoother (`missing_repro_fields` -> `author_questions` + `status: "partial"`).
- Conflicting config vs log parameter values MUST be disclosed explicitly and downgrade `status` to `"partial"`; empty inputs MUST return `status: "refused"`.
