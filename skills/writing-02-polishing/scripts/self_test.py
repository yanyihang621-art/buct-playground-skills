#!/usr/bin/env python3
"""Self-contained synthetic test suite & baseline comparison for writing-02-polishing."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_polish_result import run_polishing_pipeline
from segment_and_guard import segment_and_guard_manuscript
from text_utils import write_json_atomic, write_text_lf
from verify_polish import language_diagnostics, verify_polish


def _setup_p1_journal_en(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    ms = """# Abstract

More and more researchers studied Cu-ZnO-Al2O3 catalysts [1].

As is known to all, catalytic selectivity reached 91.8% at 573 K with turnover frequency (TOF) of 0.42 s^-1 (Smith et al., 2024).

The results shows that carbon monoxide poisoning was not observed in Fig. 1.

In order to investigate the kinetic barrier, the reaction rate follows $r = k [CO_2]$.

High dispersion may suggest improved active-site stability, which was associated with lower sintering.
"""
    case = {
        "case_id": "p1_journal_article_en",
        "register": "journal_article",
        "manuscript_file": "draft.md",
        "custom_glossary": {"catalytic selectivity": "catalytic selectivity", "Cu-ZnO-Al2O3": "Cu-ZnO-Al2O3"},
    }
    write_text_lf(d / "draft.md", ms)
    cp = d / "case.json"
    write_json_atomic(cp, case)
    return cp


def _setup_p2_latex_fragment(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    tex = r"""\section{Introduction}
In a word, a lot of catalyst samples showed pretty good stability under 3.0 MPa~\cite{zhang2024}.
\begin{equation}
  r = k \, p_{\mathrm{CO_2}} \label{eq:1}
\end{equation}
The adsorption energy did not exceed -1.35 eV.
"""
    case = {"case_id": "p2_latex_fragment_protection", "register": "journal_article", "manuscript_file": "sec1.tex"}
    write_text_lf(d / "sec1.tex", tex)
    cp = d / "case.json"
    write_json_atomic(cp, case)
    return cp


def _setup_p3_zh_grant(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    zh = """# 立项依据

众所周知，二氧化碳催化加氢工艺具有重要的战略意义 [1]。

本项目拟开发的Cu-ZnO-Al2O3催化剂在573 K下表现出较为优异的催化选择性（91.8%），有效避免了划时代突破式的副反应失活。

毋庸置疑，现有反应装置在工况原位表征方面仍未完全实现高时空产率 [2]。
"""
    case = {"case_id": "p3_bilingual_zh_grant_proposal", "register": "grant_proposal", "manuscript_file": "grant.md"}
    write_text_lf(d / "grant.md", zh)
    cp = d / "case.json"
    write_json_atomic(cp, case)
    return cp


def _setup_p4_glossary_conflict(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    md = "Turnover frequency (TOF) was measured at 573 K.\n"
    case = {
        "case_id": "p4_conflicting_glossary_partial",
        "register": "journal_article",
        "manuscript_file": "ms.md",
        "custom_glossary": {"TOF": "turnover frequency"},
        "glossary_overrides": [{"term": "TOF", "preferred": "turnover rate"}],
    }
    write_text_lf(d / "ms.md", md)
    cp = d / "case.json"
    write_json_atomic(cp, case)
    return cp


def _run_negative_qa_tests(p1: Dict[str, Any]) -> Dict[str, bool]:
    doc = p1["polished_doc"]
    grd = p1["guarded"]
    gls = p1["glossary"]

    # 1. Altered number (91.8% -> 95.0%)
    d1 = copy.deepcopy(doc)
    for s in d1["sentences"]:
        if "91.8%" in s["source_text"]:
            s["polished_text"] = s["polished_text"].replace("91.8%", "95.0%")
            break
    q1 = verify_polish(d1, grd, gls)

    # 2. Hedge escalation ("may suggest" -> "proves")
    d2 = copy.deepcopy(doc)
    for s in d2["sentences"]:
        if "may suggest" in s["source_text"]:
            s["polished_text"] = s["polished_text"].replace("may suggest", "proves")
            break
    q2 = verify_polish(d2, grd, gls)

    # 3. Correlational -> causal escalation ("associated with" -> "caused")
    d3 = copy.deepcopy(doc)
    for s in d3["sentences"]:
        if "associated with" in s["source_text"]:
            s["polished_text"] = s["polished_text"].replace("was associated with", "caused")
            break
    q3 = verify_polish(d3, grd, gls)

    # 4. Dropped negation ("was not observed" -> "was observed")
    d4 = copy.deepcopy(doc)
    for s in d4["sentences"]:
        if "not observed" in s["source_text"]:
            s["polished_text"] = s["polished_text"].replace("was not observed", "was observed")
            break
    q4 = verify_polish(d4, grd, gls)

    # 5. Dropped citation / hype phrase reintroduced
    d5 = copy.deepcopy(doc)
    d5["sentences"][1]["polished_text"] = d5["sentences"][1]["polished_text"].replace("[1]", "")
    q5 = verify_polish(d5, grd, gls)

    return {
        "neg_1_altered_number_caught": (not q1["passed"]) and (not q1["checks"]["numeric_formula_citation_invariance"]),
        "neg_2_hedge_escalation_caught": (not q2["passed"]) and (not q2["checks"]["hedging_and_causality_conservation"]),
        "neg_3_causal_flip_caught": (not q3["passed"]) and (not q3["checks"]["hedging_and_causality_conservation"]),
        "neg_4_dropped_negation_caught": (not q4["passed"]) and (not q4["checks"]["negation_and_scope_invariance"]),
        "neg_5_dropped_citation_caught": (not q5["passed"]) and (not q5["checks"]["numeric_formula_citation_invariance"]),
    }


def _editorial_regressions():
    def check(source, target):
        packet = segment_and_guard_manuscript(source)
        span = packet["sentences"][0]
        decision = {**span, "polished_text": target, "edit_type": "copyedit",
                    "rationale": "Preserve the actor, limit and result when tightening the phrasing."}
        return verify_polish({"sentences": [decision]}, packet, {"entries": []})

    bound = "There were no more than 10 samples."
    limit = "There were at most 10 samples."
    extended = "The treatment resulted in an increase in yield."
    simple = "The treatment increased yield."
    rough = "The measurements was taken in two chamber."
    smooth = "The measurements were taken in two chambers."
    revised = check(rough, smooth)
    unresolved = check(rough, rough)
    return {
        "equivalent_compound_bounds": check(bound, limit)["passed"]
            and check("值不低于 5。", "值至少为 5。")["passed"],
        "bound_reversal_rejected": not check(bound, "There were at least 10 samples.")["passed"],
        "outside_negation_preserved": not check("It was not at most 10.", "It was at most 10.")["passed"],
        "causal_nominalization_equivalent": check(extended, simple)["passed"],
        "swapped_causal_arguments_rejected": not check(extended, "The yield increased treatment.")["passed"],
        "causal_to_association_rejected": not check(extended, "The treatment was associated with an increase in yield.")["passed"],
        "language_warnings_advisory": unresolved["passed"] and unresolved["metrics"]["remaining_language_flags"] == 2,
        "language_repairs_measured": revised["passed"] and revised["metrics"]["cleared_language_flags"] == 2
            and revised["metrics"]["remaining_language_flags"] == 0,
        "diagnostic_offsets_match": all(rough[x["start"]:x["end"]] == x["excerpt"] for x in language_diagnostics(rough)),
        "compound_noun_and_embedded_subject_clean": not language_diagnostics("Each of the samples is stored in two chamber types."),
        "repetition_in_prose_only": len(language_diagnostics("Check the the method and \x60the the\x60 literal.")) == 1,
    }


def _compare_baseline(p1: Dict[str, Any]) -> Dict[str, Any]:
    # Measure two outputs against identical source spans, terminology and verifier.
    original = copy.deepcopy(p1["polished_doc"])
    for row in original["sentences"]:
        row.update(polished_text=row["source_text"], edit_type="unchanged", rationale="Verbatim input control.")
    original["edited_count"] = 0
    control = verify_polish(original, p1["guarded"], p1["glossary"])
    return {
        "comparison": "Local fixture only: verbatim input versus deterministic demo rules, same verifier.",
        "verbatim_control": {"checks": control["checks"], "metrics": control["metrics"]},
        "edited_output": {"checks": p1["qa_report"]["checks"], "metrics": p1["qa_report"]["metrics"]},
        "regression_pass": p1["qa_report"]["passed"] and p1["qa_report"]["metrics"]["edited_count"] > 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run writing-02-polishing synthetic test suite.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_dir)
    c1 = _setup_p1_journal_en(root / "p1" / "input")
    r1 = run_polishing_pipeline(root / "p1" / "input", root / "p1" / "work", root / "p1" / "output", case_path=c1, demo_rules=True)
    neg = _run_negative_qa_tests(r1)
    p1_ok = r1["result"]["status"] == "completed" and r1["result"]["metrics"]["edited_count"] >= 3 and r1["qa_report"]["passed"] and all(neg.values())

    c2 = _setup_p2_latex_fragment(root / "p2" / "input")
    r2 = run_polishing_pipeline(root / "p2" / "input", root / "p2" / "work", root / "p2" / "output", case_path=c2, demo_rules=True)
    p2_ms = Path(r2["result"]["artifacts"]["polished_manuscript"]).read_text(encoding="utf-8")
    p2_ok = r2["result"]["status"] == "completed" and r"\cite{zhang2024}" in p2_ms and "3.0 MPa" in p2_ms

    c3 = _setup_p3_zh_grant(root / "p3" / "input")
    r3 = run_polishing_pipeline(root / "p3" / "input", root / "p3" / "work", root / "p3" / "output", case_path=c3, demo_rules=True)
    p3_ok = r3["result"]["status"] == "completed" and r3["result"]["metrics"]["edited_count"] >= 2

    c4 = _setup_p4_glossary_conflict(root / "p4" / "input")
    r4 = run_polishing_pipeline(root / "p4" / "input", root / "p4" / "work", root / "p4" / "output", case_path=c4, demo_rules=True)
    p4_ok = r4["result"]["status"] == "partial" and len(r4["result"]["uncertainties"]) >= 1

    editorial = _editorial_regressions()
    summary: Dict[str, Any] = {
        "all_passed": bool(p1_ok and p2_ok and p3_ok and p4_ok and all(editorial.values())),
        "suites": {
            "p1_journal_article_en": {"passed": p1_ok, "metrics": r1["qa_report"]["metrics"], "negative_qa_tests": neg},
            "p2_latex_fragment_protection": {"passed": p2_ok, "status": r2["result"]["status"]},
            "p3_bilingual_zh_grant_proposal": {"passed": p3_ok, "metrics": r3["qa_report"]["metrics"]},
            "p4_conflicting_glossary_partial": {"passed": p4_ok, "status": r4["result"]["status"], "uncertainties": len(r4["result"]["uncertainties"])},
            "editorial_regressions": editorial,
        },
    }
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r1)
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["regression_pass"]

    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
