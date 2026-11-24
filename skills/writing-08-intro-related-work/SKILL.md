---
name: writing-08-intro-related-work
description: "Verifiable Introduction and Related Work writing Skill enforcing non-cliche background grounding, technical-route comparative clustering (instead of sequential abstract stitching), limitation-derived research gaps, 1-to-1 evidence-anchored contributions, and 6-check QA auditing."
---

# Writing 08 — Introduction & Related Work (`写作08｜引言与相关工作写作`)

This Skill solves the four core requirements of Introduction and Related Work drafting in Harbor execution environments:

1. **立得住 (Concrete Background Without Cliché)**: Replaces generic throat-clearing ("In recent years, with the rapid development...", "近年来受到广泛关注") with problem-anchored research trajectory grounded in real inputs (`schemas/writing_rules.yaml`).
2. **分得清 (Technical-Route Clustering & Comparison Matrix)**: Groups related works by methodological/problem routes (`scripts/cluster_related_work.py`), producing both `related_work_clusters.json` and `comparison_matrix.md` comparing problem setting, evaluation system, key finding, and limitation across routes rather than chronological abstract stitching.
3. **说得实 (Limitation-Derived Gaps)**: Derives `research_gaps` strictly from limitations of cited studies (`derived_from_cite_keys`) or marks explicit `author_judgment: true` (`scripts/derive_gaps_and_contributions.py`).
4. **对得上 (1-to-1 Contribution-to-Evidence Alignment & Citation Coverage)**: Maps every contribution claim to an author `evidence_anchor` (`figure_or_table_ref`, `method_ref`, `metric_value`) present in `study_facts.json` and runs 6 deterministic QA gates (`scripts/verify_intro_rw.py`).

## Harbor Execution Workflow

- **Mode A (Deterministic Plan + Agent Prose Refinement, Recommended)**:
  1. Run `ingest_materials.py` -> `cluster_related_work.py` -> `derive_gaps_and_contributions.py` to create `/app/work/intro_rw/` and `/app/output/comparison_matrix.md`.
  2. Author or refine `/app/work/intro_rw/agent_draft.md` preserving the 4 required sections, route subheadings (`### ...`), real `[@cite_key]` references, and exact `evidence_anchor` numbers/tables.
  3. Verify and package via `build_intro_rw_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json --draft-file /app/work/intro_rw/agent_draft.md`.
- **Mode B (End-to-End Automated Run)**:
  ```bash
  python scripts/build_intro_rw_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json
  ```

## Integrity & Anti-Fabrication Guardrails

- NEVER invent citations (`[@ghost2029]`), exaggerate unbacked metrics/table numbers, or fabricate literature limitations.
- Missing corpus and study facts MUST return `status: "refused"` with explicit `uncertainties`; partial materials return `status: "partial"`.
