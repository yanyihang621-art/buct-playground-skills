#!/usr/bin/env python3
"""Self-contained synthetic verification suite & baseline comparison for writing-04-references."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_reference_result import run_reference_pipeline
from io_utils import write_json_atomic, write_text_lf
from verify_citations import verify_citations


def _setup_r1_multi_source(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = """@article{zhang2024_cu,
  author = {Wei Zhang and Li Chen and Ming Wang and Tao Liu},
  title = {High Catalytic Selectivity in CO2 Hydrogenation over Cu-ZnO-Al2O3},
  journal = {Journal of Catalysis},
  year = {2024},
  volume = {430},
  pages = {115--124},
  doi = {10.1016/j.jcat.2024.115124}
}

@misc{zhang2023_preprint,
  author = {Wei Zhang and Li Chen and Ming Wang and Tao Liu},
  title = {High Catalytic Selectivity in CO2 Hydrogenation over Cu-ZnO-Al2O3},
  journal = {arXiv preprint arXiv:2311.09876},
  year = {2023},
  eprint = {2311.09876}
}

@article{zhang2024_kinetics,
  author = {Wei Zhang and Hua Li},
  title = {Kinetic Isotope Effects in Methanol Steam Reforming over Pd-Zn},
  journal = {ACS Catalysis},
  year = {2024},
  volume = {14},
  pages = {410--419},
  doi = {10.1021/acscatal.4c00410}
}
"""
    ris = """TY  - JOUR
ID  - chen2023_mof
AU  - Chen, Yi
AU  - Zhao, Jun
TI  - Operando FTIR Study of MOF Active Sites
JO  - Applied Catalysis B
PY  - 2023
VL  - 325
SP  - 122300
DO  - 10.1016/j.apcatb.2023.122300
ER  -
"""
    enw = """%0 Journal Article
%A 王磊
%A 李静
%T 基于金属有机框架材料的二氧化碳加氢制甲醇研究进展
%J 化工进展
%D 2024
%V 43
%N 2
%P 512-520
%R 10.16085/j.issn.1000-6613.2024-0012
%F wang2024_cnki
"""
    draft = """# CO2 加氢催化研究进展

Cu-ZnO-Al2O3 催化剂在 CO2 加氢反应中展现出极高的选择性与稳定性，但其活性位点机制仍需定量表征 [CITE:zhang2023_preprint]。
最新研究表明 Cu-ZnO-Al2O3 catalysts exhibit high CO2 conversion selectivity。
同时，Methanol steam reforming kinetics over Pd-Zn 同样受表面氢溢流控制 [@zhang2024_kinetics]。
在金属有机框架催化方面，已有学者开展了系统的文献综述 [@wang2024_cnki; @chen2023_mof]。
"""
    case = {
        "case_id": "r1_multi_source_dedup_gbt7714",
        "style": "gbt7714",
        "manuscript_file": "draft.md",
        "bib_files": ["lib1.bib", "lib2.ris", "lib3.enw"],
        "insert_claims": [
            {
                "target_phrase": "Cu-ZnO-Al2O3 catalysts exhibit high CO2 conversion selectivity",
                "cite_keys": ["zhang2024_cu"],
            }
        ],
    }
    write_text_lf(d / "lib1.bib", bib)
    write_text_lf(d / "lib2.ris", ris)
    write_text_lf(d / "lib3.enw", enw)
    write_text_lf(d / "draft.md", draft)
    case_p = d / "case.json"
    write_json_atomic(case_p, case)
    return case_p


def _setup_r2_latex_apa(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = """@article{smith2022_dft,
  author = {John Smith and Alice Brown},
  title = {Density Functional Theory Study of Copper Surfaces for CO2 Activation},
  journal = {Physical Review B},
  year = {2022},
  volume = {105},
  pages = {125401},
  doi = {10.1103/PhysRevB.105.125401}
}
"""
    tex = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Copper surfaces facilitate CO2 activation via bent carbonate intermediates [@smith2022_dft].
\end{document}
"""
    case = {"case_id": "r2_latex_apa", "style": "apa7", "manuscript_file": "paper.tex", "bib_files": ["refs.bib"]}
    write_text_lf(d / "refs.bib", bib)
    write_text_lf(d / "paper.tex", tex)
    case_p = d / "case.json"
    write_json_atomic(case_p, case)
    return case_p


def _setup_r3_reorder_sync(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = """@article{ref_alpha,
  author = {Alan Alpha},
  title = {Thermodynamic Limits of Carbon Capture},
  journal = {Energy},
  year = {2021},
  doi = {10.1016/j.energy.2021.120001}
}
@article{ref_beta,
  author = {Betty Beta},
  title = {Reactor Design for Continuous Hydrogenation},
  journal = {AIChE Journal},
  year = {2022},
  doi = {10.1002/aic.17500}
}
@article{ref_gamma,
  author = {Carl Gamma},
  title = {Catalyst Stability Under Industrial Pressure},
  journal = {Ind. Eng. Chem. Res.},
  year = {2023},
  doi = {10.1021/acs.iecr.3c00100}
}
"""
    md = """# Reordered Section Draft

First we discuss industrial reactor design [@ref_beta] and catalyst stability [@ref_gamma].
Figure 1 caption: continuous reactor design for carbon capture and catalyst stability [CITE:ref_alpha,ref_beta,ref_gamma].
"""
    case = {"case_id": "r3_reorder_sync", "style": "gbt7714", "manuscript_file": "reordered.md", "bib_files": ["refs.bib"]}
    write_text_lf(d / "refs.bib", bib)
    write_text_lf(d / "reordered.md", md)
    case_p = d / "case.json"
    write_json_atomic(case_p, case)
    return case_p


def _setup_r4_missing_fields(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = """@article{incomplete2024,
  author = {Unknown Team},
  title = {Incomplete Record Missing Venue And Year}
}
"""
    md = "An incomplete record missing venue and year needs human review [@incomplete2024].\n"
    case = {"case_id": "r4_missing_fields", "style": "gbt7714", "manuscript_file": "ms.md", "bib_files": ["bad.bib"]}
    write_text_lf(d / "bad.bib", bib)
    write_text_lf(d / "ms.md", md)
    case_p = d / "case.json"
    write_json_atomic(case_p, case)
    return case_p


def _run_negative_qa_tests(r1: Dict[str, Any]) -> Dict[str, bool]:
    ms = r1["sync_res"]["synced_manuscript"]
    cmap = r1["sync_res"]["citation_map"]
    lib = r1["canonical_library"]
    dedup = r1["dedup_report"]
    raw = r1["raw_library"]

    # 1. Numbering gap (1, 3 instead of 1, 2)
    c1 = copy.deepcopy(cmap)
    c1["cited_order"][1]["number"] = 99
    q1 = verify_citations(ms, c1, lib, dedup, raw)

    # 2. Dangling / unresolved citation
    c2 = copy.deepcopy(cmap)
    c2["unresolved_keys"].append({"raw_key": "nonexistent_2025", "line": 3})
    q2 = verify_citations(ms, c2, lib, dedup, raw)

    # 3. Fabricated DOI 10.0000/fake
    l3 = copy.deepcopy(lib)
    l3["records"][0]["doi"] = "10.0000/fabricated-doi"
    q3 = verify_citations(ms, cmap, l3, dedup, raw)

    # 4. Claim-reference topic mismatch
    c4 = copy.deepcopy(cmap)
    c4["in_text_occurrences"][1]["context_sentence"] = "Quantum entanglement in photonic qubits enables fault-tolerant teleportation."
    c4["in_text_occurrences"][1]["cite_keys"] = ["zhang2024_kinetics"]
    q4 = verify_citations(ms, c4, lib, dedup, raw)
    context = c4["in_text_occurrences"][1]
    cited = next(r for r in lib["records"] if r["cite_key"] == "zhang2024_kinetics")
    audit = {"line": context["line"], "cite_key": cited["cite_key"], "claim_quote": "photonic qubits",
             "source_field": "title", "source_quote": cited["title"], "verdict": "mismatch",
             "reason": "The cited paper studies methanol reforming, not quantum teleportation."}
    reviewed = verify_citations(ms, c4, lib, dedup, raw, semantic_reviews=[audit])
    audit["source_quote"] = "This invented quote is absent."
    tampered = verify_citations(ms, c4, lib, dedup, raw, semantic_reviews=[audit])

    # 5. Unresolved placeholder marker in final text
    q5 = verify_citations(ms + "\n[CITE:todo_fix]", cmap, lib, dedup, raw)

    return {
        "neg_1_numbering_gap_caught": (not q1["passed"]) and (not q1["checks"]["numbering_continuity"]),
        "neg_2_dangling_ref_caught": (not q2["passed"]) and (not q2["checks"]["bidirectional_sync"]),
        "neg_3_fake_doi_caught": (not q3["passed"]) and (not q3["checks"]["metadata_and_doi_validity"]),
        "neg_4a_lexical_warning": q4["checks"]["claim_reference_alignment"] and any(
            row.get("reason") == "no_lexical_overlap_review" for row in q4["review_queue"]),
        "neg_4b_reviewed_mismatch": any(v.get("reason") == "reviewed_mismatch" for v in reviewed["violations"]),
        "neg_4c_unlocated_quote": any(v.get("reason") == "unsupported_review_or_unlocated_quote" for v in tampered["violations"]),
        "neg_5_placeholder_leak_caught": (not q5["passed"]) and (not q5["checks"]["placeholder_and_honesty"]),
    }


def _compare_baseline(r1, input_file):
    """Observe citation markers in the actual input and the output of this fixture."""
    tag = re.compile(r"\[(?:@|CITE:)[^\]]+\]")
    before = len(tag.findall(input_file.read_text(encoding="utf-8")))
    after = len(tag.findall(r1["sync_res"]["synced_manuscript"]))
    return {"comparison": "Original input vs edited output on the same local fixture",
            "input_keyed_tags": before, "output_keyed_tags": after,
            "observed_change": before > 0 and after == 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run writing-04-references synthetic test suite.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_dir)
    c1 = _setup_r1_multi_source(root / "r1" / "input")
    r1 = run_reference_pipeline(root / "r1" / "input", root / "r1" / "work", root / "r1" / "output", case_path=c1)
    neg = _run_negative_qa_tests(r1)
    r1_ok = (
        r1["qa_report"]["passed"] and r1["result"]["verification"]["pending_claim_source_pairs"] == 5
        and r1["dedup_report"]["raw_record_count"] == 5
        and r1["dedup_report"]["canonical_record_count"] == 4
        and len(r1["dedup_report"]["anti_false_merge_guards"]) >= 1
        and "[1–3]" not in r1["sync_res"]["synced_manuscript"]  # r1 has [1], [2], [3,4]
        and all(neg.values())
    )

    c2 = _setup_r2_latex_apa(root / "r2" / "input")
    r2 = run_reference_pipeline(root / "r2" / "input", root / "r2" / "work", root / "r2" / "output", case_path=c2)
    r2_ok = r2["qa_report"]["passed"] and "\\cite{smith2022_dft}" in r2["sync_res"]["synced_manuscript"]

    c3 = _setup_r3_reorder_sync(root / "r3" / "input")
    r3 = run_reference_pipeline(root / "r3" / "input", root / "r3" / "work", root / "r3" / "output", case_path=c3)
    r3_ok = r3["qa_report"]["passed"] and "[1–3]" in r3["sync_res"]["synced_manuscript"]

    c4 = _setup_r4_missing_fields(root / "r4" / "input")
    r4 = run_reference_pipeline(root / "r4" / "input", root / "r4" / "work", root / "r4" / "output", case_path=c4)
    r4_ok = r4["result"]["status"] == "partial" and len(r4["result"]["uncertainties"]) >= 1

    summary: Dict[str, Any] = {
        "all_passed": bool(r1_ok and r2_ok and r3_ok and r4_ok),
        "suites": {
            "r1_multi_source_dedup_gbt7714": {
                "passed": r1_ok,
                "metrics": r1["qa_report"]["metrics"],
                "anti_false_merge_guards": len(r1["dedup_report"]["anti_false_merge_guards"]),
                "negative_qa_tests": neg,
            },
            "r2_latex_apa": {"passed": r2_ok, "status": r2["result"]["status"]},
            "r3_reorder_range_compression": {"passed": r3_ok, "status": r3["result"]["status"]},
            "r4_missing_fields_honest_partial": {"passed": r4_ok, "status": r4["result"]["status"], "uncertainties": len(r4["result"]["uncertainties"])},
        },
    }
    example = SCRIPT_DIR.parent / "examples"
    tutorial = root / "tutorial"
    case_path = tutorial / "input" / "case.json"
    case = json.loads((example / "example_case.json").read_text(encoding="utf-8"))
    write_json_atomic(case_path, case)
    review = json.loads((example / "example_review.json").read_text(encoding="utf-8"))
    final = run_reference_pipeline(case_path.parent, tutorial / "work", tutorial / "output", review=review)["result"]
    valid = (final["status"] == "completed" and final["metrics"]["reviewed_claim_source_pairs"] == 1
             and final["claim_evidence"][0]["quote_located"] and "[1]" in final["revised_manuscript"]
             and final["editor_note"] == review["contract_values"]["editor_note"])
    summary["suites"]["tutorial_reviewed_and_contract_filled"] = {"passed": valid}
    summary["all_passed"] &= valid
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r1, c1.parent / "draft.md")
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["observed_change"]

    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
