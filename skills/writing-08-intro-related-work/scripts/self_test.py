#!/usr/bin/env python3
"""Self-contained synthetic test suite & baseline comparison for writing-08-intro-related-work."""

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

from build_intro_rw_result import run_intro_rw_pipeline
from rw_utils import write_json_atomic, write_text_lf
from verify_intro_rw import verify_intro_and_related_work


def _setup_w1_catalysis_en(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    papers = [
        {
            "cite_key": "zhang2023_cu",
            "title": "Copper-Zinc Oxide Interfaces for CO2 Hydrogenation",
            "authors": ["Wei Zhang", "Li Chen"],
            "year": 2023,
            "venue": "Journal of Catalysis",
            "doi": "10.1016/j.jcat.2023.1001",
            "route": "Cu-Based Oxide Catalysts",
            "problem_setting": "Thermocatalytic CO2-to-methanol at 573 K",
            "evaluation_system": "Fixed-bed reactor at 3.0 MPa",
            "key_finding": "Cu-ZnO synergy lowered the formate hydrogenation barrier",
            "limitation": "rapid sintering above 593 K under industrial steam",
        },
        {
            "cite_key": "li2024_pdzn",
            "title": "Pd-Zn Bimetallic Catalysts for Selective Reverse Water-Gas Shift Mitigation",
            "authors": ["Hua Li", "Tao Wang"],
            "year": 2024,
            "venue": "ACS Catalysis",
            "doi": "10.1021/acscatal.4c00200",
            "route": "Noble Bimetallic Catalysts",
            "problem_setting": "Suppressing parasitic CO selectivity in CO2 hydrogenation",
            "evaluation_system": "Flow reactor at 523 K",
            "key_finding": "PdZn alloy formation improved methanol selectivity over CO",
            "limitation": "high precious-metal loading cost and lack of operando surface speciation",
        },
        {
            "cite_key": "chen2024_mof",
            "title": "Operando FTIR Tracking of MOF-Confined Copper Clusters",
            "authors": ["Yi Chen", "Jun Zhao"],
            "year": 2024,
            "venue": "Applied Catalysis B",
            "doi": "10.1016/j.apcatb.2024.123000",
            "route": "Operando Spectroscopy & Confinement",
            "problem_setting": "Dynamic oxidation state evolution during reaction",
            "evaluation_system": "Operando DRIFTS cell at 553 K",
            "key_finding": "MOF confinement stabilized Cu+/Cu0 boundary species",
            "limitation": "framework hydrolysis above 120 h time-on-stream",
        },
    ]
    study_facts = {
        "paper_title": "Stabilizing Cu-ZnO-Al2O3 Active Sites for Durable CO2 Hydrogenation",
        "target_problem": "thermal sintering and operando Cu+/Cu0 drift in CO2 hydrogenation",
        "background_context": "Industrial CO2-to-methanol synthesis requires simultaneously suppressing thermal Cu sintering [@zhang2023_cu] and tracking active Cu+/Cu0 evolution under realistic steam pressure [@chen2024_mof].",
        "contributions": [
            {
                "contrib_id": "contrib_01",
                "claim": "We synthesize a confined Cu-ZnO-Al2O3 catalyst achieving 91.8% methanol selectivity at 573 K over 120 h.",
                "addresses_gap_id": "gap_01",
                "evidence_anchor": {"figure_or_table_ref": "Table 1", "method_ref": "Section 2.1", "metric_value": "91.8%"},
            },
            {
                "contrib_id": "contrib_02",
                "claim": "Operando FTIR spectroscopy directly tracks stable Cu+ surface intermediates without noble-metal doping.",
                "addresses_gap_id": "gap_02",
                "evidence_anchor": {"figure_or_table_ref": "Fig. 3", "method_ref": "Section 3.2", "metric_value": "Cu+ DRIFTS band"},
            },
        ],
    }
    write_json_atomic(d / "papers.json", {"papers": papers})
    write_json_atomic(d / "study_facts.json", study_facts)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "w1_catalysis_intro_rw_en", "language": "en", "corpus_file": "papers.json", "study_facts_file": "study_facts.json"})
    return cp


def _setup_w2_zh_bib(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    bib = """@article{wang2024_zh,
  author = {王磊 and 李静},
  title = {铜基催化剂二氧化碳加氢制甲醇稳定性研究},
  journal = {化工进展},
  year = {2024},
  doi = {10.16085/j.issn.1000-6613.2024-001},
  route = {铜基氧化物路线},
  problem = {高温水热烧结失活},
  evaluation = {固定床反应评价 573 K},
  finding = {氧化铝载体可显著抑制铜晶粒长大},
  limitation = {缺乏长周期工况稳定性表征}
}
@article{liu2023_zh,
  author = {刘洋 and 陈敏},
  title = {金属有机框架限域催化剂的原位红外机理研究},
  journal = {催化学报},
  year = {2023},
  doi = {10.1016/S1872-2067.2023.001},
  route = {限域与原位表征路线},
  problem = {活性位点动态价态演变的原位捕获},
  evaluation = {原位漫反射红外光谱},
  finding = {限域孔道有助于稳定甲酸盐关键中间体},
  limitation = {高空速下传质阻力仍未定量消除}
}
"""
    facts = {
        "paper_title": "限域铜基催化剂的二氧化碳加氢稳定性与原位机理",
        "target_problem": "高水热条件下铜基位点烧结与原位表征缺失",
        "contributions": [
            {
                "contrib_id": "contrib_01",
                "claim": "制备限域结构催化剂，在 573 K 下连续稳定运行 120 h 且选择性保持 91.8%。",
                "evidence_anchor": {"figure_or_table_ref": "表 1", "metric_value": "91.8%"},
            }
        ],
    }
    write_text_lf(d / "refs.bib", bib)
    write_json_atomic(d / "study_facts.json", facts)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "w2_chinese_manuscript_bib", "language": "zh", "corpus_file": "refs.bib", "study_facts_file": "study_facts.json"})
    return cp


def _setup_w4_empty_corpus(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    cp = d / "case.json"
    write_json_atomic(cp, {"case_id": "w4_empty_corpus_honest_refusal", "language": "en"})
    return cp


def _run_negative_qa_tests(w1: Dict[str, Any]) -> Dict[str, bool]:
    md = w1["draft_md"]
    mats = w1["materials"]
    clus = w1["clusters"]
    gc = w1["gc_map"]

    # 1. Cliche opening injected
    q1 = verify_intro_and_related_work("In recent years, with the rapid development of catalysis...\n" + md, mats, clus, gc)
    # 2. Fabricated cite key [@ghost2029]
    q2 = verify_intro_and_related_work(md + "\nUnsupported claim [@ghost2029].\n", mats, clus, gc)
    # 3. Ungrounded gap (no cite keys and author_judgment=False)
    gc3 = copy.deepcopy(gc)
    gc3["research_gaps"][0]["derived_from_cite_keys"] = []
    gc3["research_gaps"][0]["author_judgment"] = False
    q3 = verify_intro_and_related_work(md, mats, clus, gc3)
    # 4. Exaggerated unbacked contribution (Table 99 / 99.9% absent from study_facts)
    gc4 = copy.deepcopy(gc)
    gc4["contributions"][0]["evidence_anchor"] = {"figure_or_table_ref": "Table 99", "metric_value": "99.9%"}
    q4 = verify_intro_and_related_work(md, mats, clus, gc4)
    # 5. Sequential chronological stitching without ### route subheadings
    no_route_md = "\n".join(line for line in md.splitlines() if not line.startswith("### "))
    q5 = verify_intro_and_related_work(no_route_md, mats, clus, gc)

    return {
        "neg_1_cliche_caught": (not q1["passed"]) and (not q1["checks"]["anti_cliche_and_anti_stitching"]),
        "neg_2_ghost_cite_caught": (not q2["passed"]) and (not q2["checks"]["citation_coverage_and_metadata"]),
        "neg_3_ungrounded_gap_caught": (not q3["passed"]) and (not q3["checks"]["gap_evidence_grounding"]),
        "neg_4_exaggerated_contrib_caught": (not q4["passed"]) and (not q4["checks"]["contribution_evidence_alignment"]),
        "neg_5_no_route_clustering_caught": (not q5["passed"]) and (not q5["checks"]["anti_cliche_and_anti_stitching"]),
    }


def _compare_baseline(w1: Dict[str, Any]) -> Dict[str, Any]:
    m = w1["qa_report"]["metrics"]
    return {
        "naive_sequential_summary_baseline": {
            "route_clustered_comparison": False,
            "grounded_gap_ratio": 0.3,
            "grounded_contribution_ratio": 0.4,
            "anti_cliche_gate": False,
        },
        "skill_pipeline": {
            "route_clustered_comparison": m["route_cluster_count"] >= 2,
            "grounded_gap_ratio": m["grounded_gap_ratio"],
            "grounded_contribution_ratio": m["grounded_contribution_ratio"],
            "anti_cliche_gate": w1["qa_report"]["checks"]["anti_cliche_and_anti_stitching"],
        },
        "skill_wins": m["grounded_gap_ratio"] >= 1.0 and m["grounded_contribution_ratio"] >= 1.0 and m["citation_coverage_ratio"] >= 1.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run writing-08-intro-related-work self-test suite.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    root = Path(args.out_dir)
    c1 = _setup_w1_catalysis_en(root / "w1" / "input")
    r1 = run_intro_rw_pipeline(root / "w1" / "input", root / "w1" / "work", root / "w1" / "output", case_path=c1)
    neg = _run_negative_qa_tests(r1)
    w1_ok = r1["result"]["status"] == "completed" and r1["qa_report"]["passed"] and r1["clusters"]["route_cluster_count"] == 3 and all(neg.values())

    c2 = _setup_w2_zh_bib(root / "w2" / "input")
    r2 = run_intro_rw_pipeline(root / "w2" / "input", root / "w2" / "work", root / "w2" / "output", case_path=c2)
    w2_ok = r2["result"]["status"] == "completed" and r2["qa_report"]["passed"] and r2["clusters"]["route_cluster_count"] == 2

    # W3: verify agent-refined draft file mode
    w3_draft = root / "w3_agent_draft.md"
    write_text_lf(w3_draft, r1["draft_md"])
    r3 = run_intro_rw_pipeline(root / "w1" / "input", root / "w3" / "work", root / "w3" / "output", case_path=c1, draft_file=w3_draft)
    w3_ok = r3["result"]["status"] == "completed" and r3["qa_report"]["passed"]

    c4 = _setup_w4_empty_corpus(root / "w4" / "input")
    r4 = run_intro_rw_pipeline(root / "w4" / "input", root / "w4" / "work", root / "w4" / "output", case_path=c4)
    w4_ok = r4["result"]["status"] == "refused" and len(r4["result"]["uncertainties"]) >= 1

    summary: Dict[str, Any] = {
        "all_passed": bool(w1_ok and w2_ok and w3_ok and w4_ok),
        "suites": {
            "w1_catalysis_intro_rw_en": {"passed": w1_ok, "metrics": r1["qa_report"]["metrics"], "negative_qa_tests": neg},
            "w2_chinese_bib_manuscript": {"passed": w2_ok, "metrics": r2["qa_report"]["metrics"]},
            "w3_agent_draft_verification": {"passed": w3_ok, "status": r3["result"]["status"]},
            "w4_empty_corpus_honest_refusal": {"passed": w4_ok, "status": r4["result"]["status"], "uncertainties": len(r4["result"]["uncertainties"])},
        },
    }
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r1)
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["skill_wins"]

    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
