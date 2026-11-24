---
name: writing-13-latex
description: "LaTeX writing, log diagnosis, deterministic safe source fixing, and reproducible multi-pass compilation Skill (pdflatex/xelatex + bibtex/biber) with PDF/PNG visual inspection and honest failure reporting."
---

# Writing 13 — LaTeX Writing & Reproducible Compilation (`写作13｜LaTeX 写作与编译`)

This Skill solves the four core challenges of academic LaTeX compilation and maintenance in Harbor execution environments:

1. **编译得通 (Root-Cause Log Diagnosis & Multi-Pass Build)**: Parses `.log` and `.blg` outputs (`scripts/parse_tex_log.py`) using `-interaction=nonstopmode -file-line-error` to pinpoint exact `file`, `line`, and `is_cascade` root causes instead of halting blindly at the first error, and runs full `engine -> bibtex/biber -> engine -> engine` convergence (`scripts/compile_latex.py`).
2. **改得对 (Deterministic Safe Fixes with Diffs)**: Audits environment balance, `tabular` column alignment, unescaped `%` after digits, missing `booktabs`/`amsmath`/`graphicx` packages, and `hyperref` ordering (`scripts/lint_and_fix_tex.py`), emitting unified `patch.diff` and structured `change_records`.
3. **交得可用 (Editable Project + Audited PDF & PNG Previews)**: Copies source files cleanly into `/app/output/latex_project/` (leaving `/input` untouched), audits rendered PDF pages with PyMuPDF for zero `??`/`[?]` unresolved markers, and exports 150-DPI PNG previews under `/app/output/previews/`.
4. **复现得了 (Build Manifest & Shell Script)**: Records exact TeX engine & bib tool binary versions, compiler pass exit codes, PDF SHA-256, and emits an executable `build_repro.sh` alongside `/app/output/result.json` (`scripts/build_latex_result.py`).

## Harbor Execution Workflow

- **Inputs**: `/input/case.json`, `/input/task.md`, and `.tex`/`.bib`/`.cls`/figure project files.
- **Command**:
  ```bash
  python scripts/build_latex_result.py --input-dir /input --work-dir /app/work --output-dir /app/output --case /input/case.json
  ```
- **Deliverables under `/app/output/`**:
  - `latex_project/` — Editable repaired `.tex`/`.bib` sources + `build_repro.sh`
  - `<main_stem>.pdf` — Compiled PDF artifact
  - `previews/page_*.png` — 150-DPI rendered PNG previews for visual verification
  - `result.json` — Harbor contract deliverable with `status`, `metrics`, `change_records`, `uncertainties`, `verification`.

## Cross-Platform & Honesty Guardrails

- Uses portable TeX flags (`-interaction=nonstopmode -file-line-error`) and strict timeouts (`60s` per pass); never assumes OS-specific binaries without probing via `shutil.which`.
- Missing `.cls` templates, missing TeX engines, or broken builds without a non-empty PDF MUST report `status: "failed"` with exact diagnostics; partial PDFs with unresolved `??`/`[?]` or remaining compile errors MUST report `status: "partial"`.
