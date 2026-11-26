#!/usr/bin/env python3
"""Real TeX compilation synthetic test suite & baseline comparison for writing-13-latex."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_latex_result import run_latex_pipeline
from build_evidence import apply_contract, project_contract_result
from compile_latex import compile_latex_project
from parse_tex_log import parse_tex_logs
from prepare_case import pdf_text, stage
from tex_utils import write_json_atomic, write_text_lf
from verify_manuscript import verify


def _setup_l1_clean_bibtex(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = r"""@article{zhang2024_co2,
  author = {Wei Zhang and Li Chen},
  title = {Catalytic Selectivity in CO2 Hydrogenation},
  journal = {Journal of Catalysis},
  year = {2024},
  volume = {430},
  pages = {115--124}
}
"""
    tex = r"""\documentclass[11pt]{article}
\usepackage{amsmath}
\usepackage{booktabs}
\begin{document}
\title{Reproducible Catalytic Kinetics Study}
\author{Wei Zhang}
\maketitle
\section{Introduction}
Prior work established high selectivity over Cu-ZnO-Al$_2$O$_3$~\cite{zhang2024_co2}.
The rate law is expressed in Eq.~\eqref{eq:rate}:
\begin{equation}
  r = k \, p_{\mathrm{CO_2}}
  \label{eq:rate}
\end{equation}
\begin{table}[h]
\centering
\caption{Catalytic performance comparison.}
\begin{tabular}{lcc}
\toprule
Catalyst & Temp (K) & Selectivity (\%) \\
\midrule
Cu-ZnO-Al$_2$O$_3$ & 573 & 91.8 \\
\bottomrule
\end{tabular}
\end{table}
\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""
    write_text_lf(d / "refs.bib", bib)
    write_text_lf(d / "main.tex", tex)
    case_p = d / "case.json"
    write_json_atomic(case_p, {"case_id": "l1_clean_article_with_bibtex", "main_tex": "main.tex", "auto_fix": True, "render_previews": True})
    return case_p


def _setup_l2_broken_autofix(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    # Intentionally broken: hyperref before amsmath, missing booktabs package, unescaped 91.8% in table/prose
    tex = r"""\documentclass{article}
\usepackage{hyperref}
\begin{document}
\section{Results}
The measured selectivity reached 91.8% at 573 K, as computed by Eq.~\eqref{eq:bal}.
\begin{align}
  E_a &= 45.2\,\mathrm{kJ/mol} \label{eq:bal}
\end{align}
\begin{table}[h]
\centering
\begin{tabular}{lc}
\toprule
Metric & Value \\
\midrule
Selectivity & 91.8% \\
\bottomrule
\end{tabular}
\end{table}
\end{document}
"""
    write_text_lf(d / "broken.tex", tex)
    case_p = d / "case.json"
    write_json_atomic(case_p, {"case_id": "l2_broken_tex_diagnose_and_autofix", "main_tex": "broken.tex", "auto_fix": True, "render_previews": True})
    return case_p


def _setup_l3_xelatex_unicode(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    # Minimal deterministic xelatex test (uses fontspec without exotic third-party font dependency)
    tex = r"""\documentclass{article}
\usepackage{fontspec}
\begin{document}
\section{Unicode Engine Check}
XeLaTeX deterministic build verification with UTF-8 punctuation --- em-dash and $\Delta H^{\circ} = -49.5\,\mathrm{kJ/mol}$.
\end{document}
"""
    write_text_lf(d / "unicode.tex", tex)
    case_p = d / "case.json"
    write_json_atomic(case_p, {"case_id": "l3_xelatex_unicode_article", "main_tex": "unicode.tex", "auto_fix": True, "render_previews": True})
    return case_p


def _setup_l4_missing_cls(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    tex = r"""\documentclass{nonexistent_journal_2099}
\begin{document}
Hello world.
\end{document}
"""
    write_text_lf(d / "fail.tex", tex)
    case_p = d / "case.json"
    write_json_atomic(case_p, {"case_id": "l4_missing_cls_honest_failure", "main_tex": "fail.tex", "auto_fix": True, "render_previews": False})
    return case_p


def _run_negative_diag_tests(tmp_dir: Path) -> Dict[str, bool]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    bad_log = tmp_dir / "synthetic_bad.log"
    bad_log.write_text(
        "./main.tex:12: LaTeX Error: Environment tablex undefined.\n"
        "./main.tex:14: Undefined control sequence.\n"
        "LaTeX Warning: Reference `fig:missing' on page 1 undefined on input line 18.\n"
        "Overfull \\hbox (24.5pt too wide) in paragraph at lines 22--22\n",
        encoding="utf-8",
    )
    diag = parse_tex_logs(bad_log)
    cats = {d["category"] for d in diag["diagnostics"]}
    return {
        "neg_1_error_and_cascade_detected": diag["error_count"] == 2 and any(d["is_cascade"] for d in diag["diagnostics"]),
        "neg_2_undefined_ref_detected": diag["has_undefined_refs"],
        "neg_3_overfull_hbox_detected": diag["overfull_hbox_count"] == 1 and "overfull_hbox" in cats,
    }


def _compare_baseline(l2: Dict[str, Any]) -> Dict[str, Any]:
    """Same input source and compiler; contrast the original with the reviewed repair."""
    return {
        "original_project_same_compiler": {
            "initial_error_count": l2["init_diagnostics"]["error_count"],
        },
        "skill_pipeline": {
            "initial_error_count": l2["init_diagnostics"]["error_count"],
            "final_error_count": l2["diagnostics"]["error_count"],
            "changed_files": sum("operation" in row for row in l2["result"]["change_records"]),
            "compiled_after_fix": l2["result"]["status"] == "completed",
            "tools_executed": [p["tool"] for p in l2["manifest"]["passes_executed"]],
            "png_visual_audit": len(l2["manifest"]["previews"]) >= 1,
        },
        "skill_wins": l2["init_diagnostics"]["error_count"] > 0 and l2["diagnostics"]["error_count"] == 0 and l2["result"]["status"] == "completed",
    }


def _run_table_checks(root: Path) -> Dict[str, bool]:
    """Build two genuine PDFs with identical values and different row associations."""
    import tempfile
    examples = SCRIPT_DIR.parent / "examples"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        staged = Path(tmp) / "case"
        stage(examples / "project", staged)
        project = staged / "project"
        report = examples / "example_report.json"
        def check():
            build = compile_latex_project(project, "main.tex")
            assert build["manifest"]["status"] == "completed"
            return verify(examples / "project", staged, project, report, Path(build["manifest"]["output_pdf"]))
        original = check()
        table = project / "sections" / "results.tex"
        revised = table.read_text(encoding="utf-8").replace("1.0 & 12.4", "1.0 & 21.7").replace("2.0 & 21.7", "2.0 & 12.4")
        write_text_lf(table, revised)
        swapped = check()
        return {
            "matches_both_rendered_rows": original["verified"] == 2 and all(
                row.get("pdf_row_matches") for row in original["checks"]),
            "swapped_values_fail_rendered_row_checks": all(
                not row.get("pdf_row_matches") and row.get("pdf_pages") for row in swapped["checks"]
            ) and swapped["failed"] == 2,
        }


def _check_scientific_delivery(result, output_dir):
    """Exercise a closed case contract using the compiled manuscript from suite 1."""
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "type": "object", "additionalProperties": False, "required": ["case_id", "paper"],
              "properties": {"case_id": {"type": "string"}, "paper": {
                  "type": "object", "required": ["pdf_path", "temperatures"],
                  "properties": {"pdf_path": {"type": "string"},
                                 "temperatures": {"type": "array", "items": {"$ref": "#/$defs/value"}}}}},
              "$defs": {"value": {"type": "number"}}}
    cases = {"valid_contract_and_file": (result["artifacts"]["output_pdf"], [573], True),
             "missing_requested_pdf": ("absent-manuscript.pdf", [573], False),
             "wrong_nested_fact_type": (result["artifacts"]["output_pdf"], ["573 K"], False)}
    checks = {}
    for name, (path, values, valid) in cases.items():
        trial = copy.deepcopy(result)
        apply_contract(trial, schema, {"paper": {"pdf_path": path, "temperatures": values}},
                       output_dir=output_dir)
        projected = project_contract_result(trial, schema)
        checks[name] = trial["verification"]["passed"] is valid and set(projected) == {"case_id", "paper"}
    return checks


def _check_nested_draft(root):
    """Exercise the normal build entry in draft and final mode on a nested project."""
    original = root / "source with spaces"
    write_text_lf(original / "paper" / "main.tex",
                  "\\documentclass{article}\n\\begin{document}\n"
                  "\\input{sections/results}\n\\end{document}\n")
    write_text_lf(original / "sections" / "results.tex", "The reference temperature was 573 K.\n")
    case = original / "case.json"
    write_json_atomic(case, {"case_id": "nested-draft", "main_tex": "paper/main.tex"})
    output = root / "output"
    draft = run_latex_pipeline(original, root / "work", output, case_path=case, draft=True)
    draft_result = draft["result"]
    source_path = draft_result["artifacts"]["main_tex"]
    pdf_path = draft_result["artifacts"]["output_pdf"]
    draft_reviewable = (
        draft_result["status"] == "partial" and draft_result["metrics"]["initial_error_count"] is None
        and bool(source_path and (output / source_path).is_file())
        and bool(pdf_path and "573 K" in pdf_text(output / pdf_path)))
    final = run_latex_pipeline(original, root / "work", output, case_path=case, build_cwd=".")
    return {
        "draft_published_without_claiming_review_complete": draft_reviewable,
        "nested_entry_builds_with_root_relative_dependencies": final["result"]["status"] == "completed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real MiKTeX/TeX compilation self-tests for writing-13-latex.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_dir)
    c1 = _setup_l1_clean_bibtex(root / "l1" / "input")
    r1 = run_latex_pipeline(root / "l1" / "input", root / "l1" / "work", root / "l1" / "output", case_path=c1)
    l1_ok = (
        r1["result"]["status"] == "completed"
        and r1["result"]["metrics"]["page_count"] >= 1
        and r1["result"]["metrics"]["rendered_unresolved_count"] == 0
        and len(r1["manifest"]["previews"]) >= 1
    )

    c2 = _setup_l2_broken_autofix(root / "l2" / "input")
    l2 = root / "l2"
    write_text_lf(l2 / "input" / "lab_note.txt", "The measured selectivity reached 91.8% at 573 K.")
    first = run_latex_pipeline(l2 / "input", l2 / "work", l2 / "initial_output", case_path=c2)
    # This fixture loses an entire measured condition to an ambiguous prose comment.
    # Repeat the human/agent review path using the fixture's supplied lab note.
    reviewed = l2 / "reviewed"
    shutil.copytree(l2 / "input", reviewed, dirs_exist_ok=True)
    manuscript = reviewed / "broken.tex"
    write_text_lf(manuscript, manuscript.read_text(encoding="utf-8").replace("91.8% at 573 K", r"91.8\% at 573 K"))
    r2 = run_latex_pipeline(l2 / "input", l2 / "work", l2 / "output", case_path=c2, project_dir=reviewed)
    final_text = pdf_text(l2 / "output" / r2["result"]["artifacts"]["output_pdf"])
    l2_ok = (
        first["result"]["status"] == "partial"
        and r2["init_diagnostics"]["error_count"] >= 1
        and r2["result"]["status"] == "completed"
        and r2["result"]["metrics"]["final_error_count"] == 0
        and len(r2["result"]["change_records"]) >= 2
        and "91.8% at 573 K" in " ".join(final_text.split())
    )

    c3 = _setup_l3_xelatex_unicode(root / "l3" / "input")
    r3 = run_latex_pipeline(root / "l3" / "input", root / "l3" / "work", root / "l3" / "output", case_path=c3)
    l3_ok = r3["result"]["status"] == "completed" and r3["manifest"]["engine"] == "xelatex" and r3["result"]["metrics"]["page_count"] >= 1

    c4 = _setup_l4_missing_cls(root / "l4" / "input")
    r4 = run_latex_pipeline(root / "l4" / "input", root / "l4" / "work", root / "l4" / "output", case_path=c4)
    l4_ok = r4["result"]["status"] == "failed" and r4["result"]["metrics"]["page_count"] == 0 and len(r4["result"]["uncertainties"]) >= 1

    neg = _run_negative_diag_tests(root / "neg_diag")
    render_checks = _run_table_checks(root / "rendered_tables")
    contract_checks = _check_scientific_delivery(r1["result"], root / "l1" / "output")
    nested_checks = _check_nested_draft(root / "nested-draft")

    summary: Dict[str, Any] = {
        "all_passed": all([l1_ok, l2_ok, l3_ok, l4_ok, *neg.values(), *render_checks.values(),
                           *contract_checks.values(), *nested_checks.values()]),
        "suites": {
            "l1_clean_article_with_bibtex": {"passed": l1_ok, "status": r1["result"]["status"], "metrics": r1["result"]["metrics"], "engine_version": r1["manifest"]["engine_version"]},
            "l2_broken_tex_diagnose_and_autofix": {"passed": l2_ok, "unreviewed_status": first["result"]["status"], "status": r2["result"]["status"], "metrics": r2["result"]["metrics"], "restored_measurement_visible": "91.8% at 573 K" in " ".join(final_text.split())},
            "l3_xelatex_unicode_article": {"passed": l3_ok, "status": r3["result"]["status"], "engine": r3["manifest"]["engine"], "metrics": r3["result"]["metrics"]},
            "l4_missing_cls_honest_failure": {"passed": l4_ok, "status": r4["result"]["status"], "uncertainties": len(r4["result"]["uncertainties"])},
        },
        "negative_diag_tests": neg,
        "rendered_table_tests": render_checks,
        "output_contract_tests": contract_checks,
        "nested_build_tests": nested_checks,
    }
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r2)
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["skill_wins"]

    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
