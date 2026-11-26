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


def _edited_fixture(materials: Dict[str, Any]):
    """Four original English fixture paragraphs and their links into synthetic inputs."""
    sources = materials["passages"]

    def ref(snippet):
        row = next(p for p in sources if snippet in p["text"] and p["role"] != "instruction")
        return {"source_id": row["source_id"], "quote": snippet}

    background = "Steam-induced sintering constrains thermal operation of copper catalysts."
    comparison = ("Whereas the oxide study reports a lower barrier for formate hydrogenation [1], "
                  "the PdZn study emphasizes selectivity over CO [2].")
    gap = "The stability reported for confined clusters does not establish long-duration operation."
    contribution = ("Our confined Cu-ZnO-Al2O3 catalyst delivers 91.8% methanol selectivity "
                    "at 573 K over 120 h.")
    paper1 = ref("rapid sintering above 593 K under industrial steam")
    mechanism = ref("Cu-ZnO synergy lowered the formate hydrogenation barrier")
    alloy = ref("PdZn alloy formation improved methanol selectivity over CO")
    framework = ref("framework hydrolysis above 120 h time-on-stream")
    original = ref("achieving 91.8% methanol selectivity at 573 K over 120 h.")
    claim_rows = [
        {"kind": "background", "claim": background, "cite_keys": ["zhang2023_cu"], "evidence": [paper1]},
        {"kind": "comparison", "claim": comparison, "cite_keys": ["zhang2023_cu", "li2024_pdzn"],
         "evidence": [mechanism, alloy]},
        {"kind": "gap", "claim": gap, "cite_keys": ["chen2024_mof"], "evidence": [framework]},
        {"kind": "contribution", "claim": contribution, "evidence": [original]},
    ]
    draft = (f"{background} Sintering accelerates above 593 K in industrial steam [1].\n\n"
             f"{comparison} Confinement raises a different durability question: hydrolysis was reported "
             f"beyond 120 h [3].\n\n{gap} Framework hydrolysis places a time limit on the test [3].\n\n"
             f"{contribution}\n\n## References\n")
    for i, paper in enumerate(materials["papers"], 1):
        draft += f"[{i}] {', '.join(paper['authors'])}. {paper['title']}. {paper['venue']}, {paper['year']}.\n"
    analysis = {
        "research_gaps": [{"gap_id": "G1", "gap_statement": gap, "derived_from_cite_keys": ["chen2024_mof"],
                           "evidence": [framework]}],
        "contributions": [{"contrib_id": "C1", "claim": contribution, "addresses_gap_id": "G1",
                           "evidence": [original]}],
        "claim_evidence": claim_rows,
        "section_spans": {"background": background, "related_work": comparison,
                          "gap": gap, "contribution": contribution},
    }
    return draft, analysis


def _run_negative_qa_tests(outline: Dict[str, Any], edited: Dict[str, Any]) -> Dict[str, bool]:
    md, m, c, gc = (outline[k] for k in ("draft_md", "materials", "clusters", "gc_map"))
    cliche = verify_intro_and_related_work("In recent years, with the rapid development of catalysis...\n" + md, m, c, gc)
    unknown = verify_intro_and_related_work("Unsupported claim [@ghost2029].\n\n" + md, m, c, gc)
    bad_gap = copy.deepcopy(gc)
    bad_gap["research_gaps"][0].update(derived_from_cite_keys=[], author_judgment=False)
    gap = verify_intro_and_related_work(md, m, c, bad_gap)
    bad_contrib = copy.deepcopy(gc)
    bad_contrib["contributions"][0]["evidence_anchor"] = {"figure_or_table_ref": "Table 99", "metric_value": "99.9%"}
    anchor = verify_intro_and_related_work(md, m, c, bad_contrib)
    bare_prose = "\n".join(line for line in md.splitlines() if not line.startswith("### "))
    stitched = verify_intro_and_related_work(bare_prose, m, c, gc)
    edit, m, c, gc = (edited[k] for k in ("draft_md", "materials", "clusters", "gc_map"))
    changed = verify_intro_and_related_work(edit.replace("91.8%", "99.9%"), m, c, gc)
    ghost_number = verify_intro_and_related_work(edit.replace("test [3]", "test [93]"), m, c, gc)
    return {
        "cliche_opening": not cliche["checks"]["anti_cliche_and_anti_stitching"],
        "invented_key_in_prose": not unknown["checks"]["citation_coverage_and_metadata"],
        "gap_missing_source": not gap["checks"]["gap_evidence_grounding"],
        "invented_table_and_metric": not anchor["checks"]["contribution_evidence_alignment"],
        "serial_summaries_without_comparison": not stitched["checks"]["anti_cliche_and_anti_stitching"],
        "number_changed_after_evidence_audit": not changed["passed"],
        "invented_numbered_citation": not ghost_number["checks"]["citation_coverage_and_metadata"],
    }


def _interpretation_regressions(edited: Dict[str, Any]) -> Dict[str, bool]:
    draft, materials, clusters, links = (edited[k] for k in ("draft_md", "materials", "clusters", "gc_map"))
    scope = "These group differences cannot establish a causal effect of assignment."
    design = {"source_id": "design_fixture", "path": "study.txt", "locator": "line 1",
              "role": "study", "text": "This observational comparison used groups defined by previous choices."}
    limits = [{"kind": "causal_identification", "manuscript_span": scope,
               "reason": "The groups were formed by previous choices."}]
    declared = {**materials, "passages": [*materials["passages"], design], "interpretation_limits": limits}
    hidden = verify_intro_and_related_work(f"<!-- {scope} -->\n" + draft + scope, declared, clusters, links)
    visible = verify_intro_and_related_work(scope + "\n\n" + draft, declared, clusters, links)
    stronger = verify_intro_and_related_work(scope + "\n\nGroup assignment causes the improvement.\n\n" + draft,
                                            declared, clusters, links)
    revised = {**declared, "passages": [*declared["passages"],
               {**design, "source_id": "trial_fixture", "text": "Participants were randomly allocated to the conditions."}]}
    mixed = verify_intro_and_related_work("Group assignment causes the improvement.\n" + scope + "\n" + draft,
                                          revised, clusters, links)
    code = "review_claim_strength_for_observational_design"
    return {
        "limit in comment and references only requires revision":
            any(v["reason"] == "interpretation_limit_absent_from_manuscript" for v in hidden["violations"]),
        "visible noncausal qualification passes":
            visible["passed"] and not any(w["reason"] == code and w["line"] == 1 for w in visible["warnings"]),
        "causal wording receives a located review without a hard failure":
            stronger["passed"] and any(w["reason"] == code and w["line"] == 3 and
                                       "design_fixture" in w["source_ids"] for w in stronger["warnings"]),
        "mixed designs do not imply observational-only evidence":
            mixed["passed"] and not any(w["reason"] == code for w in mixed["warnings"]),
    }


def _compare_baseline(edited: Dict[str, Any]) -> Dict[str, Any]:
    """Actually evaluate two fixture drafts with the same source package and verifier."""
    materials, clusters, evidence = (edited[k] for k in ("materials", "clusters", "gc_map"))
    bibliography = edited["draft_md"].split("## References", 1)[1]
    serial = "\n\n".join(f"{p['authors'][0]} reported {p['key_finding']} [@{p['cite_key']}]."
                         for p in materials["papers"])
    comparison_input = "## Related Work\n\n" + serial + "\n\n## References" + bibliography
    # Give the control its own true source links. It need not reproduce the edited draft's wording.
    control_cards = []
    for p in materials["papers"]:
        finding = p["key_finding"]
        source = next(s for s in materials["passages"] if finding in s["text"])
        control_cards.append({"kind": "literature_summary", "claim": finding, "cite_keys": [p["cite_key"]],
                              "evidence": [{"source_id": source["source_id"], "quote": finding}]})
    control_materials = {**materials, "claim_evidence": control_cards, "section_spans": {}}
    control = verify_intro_and_related_work(comparison_input, control_materials, clusters,
                                             {"research_gaps": [], "contributions": []})
    revised = verify_intro_and_related_work(edited["draft_md"], materials, clusters, evidence)

    def observed(report):
        return {"passed_checks": sum(report["checks"].values()), "checks": report["checks"],
                "resolved_keys": report["metrics"]["cited_in_manuscript"],
                "cross_work_comparisons": report["metrics"]["cross_work_comparisons"]}

    return {"scope": "Local synthetic draft comparison; the platform runs the official Agent comparison.",
            "control_recipe": "One source finding per paragraph, same input order and bibliography.",
            "source_count": len(materials["papers"]), "sequential_control": observed(control),
            "evidence_edited_draft": observed(revised)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic integration fixtures and audit regressions.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()
    root = Path(args.out_dir)

    def run(case, name, **options):
        return run_intro_rw_pipeline(case.parent, root / name / "work", root / name / "output",
                                     case_path=case, **options)

    c1 = _setup_w1_catalysis_en(root / "w1" / "input")
    r1 = run(c1, "w1")
    c2 = _setup_w2_zh_bib(root / "w2" / "input")
    r2 = run(c2, "w2")
    manuscript, notes = _edited_fixture(r1["materials"])
    draft_file, notes_file = root / "w3_agent_draft.md", root / "w3_notes.json"
    write_text_lf(draft_file, manuscript)
    write_json_atomic(notes_file, notes)
    r3 = run(c1, "w3", draft_file=draft_file, analysis_file=notes_file)
    r4 = run(_setup_w4_empty_corpus(root / "w4" / "input"), "w4")
    negative = _run_negative_qa_tests(r1, r3)
    tests = {
        "w1 English scaffold requires editing": r1["result"]["status"] == "partial" and r1["clusters"]["route_cluster_count"] == 3,
        "w2 Chinese BibTeX scaffold requires editing": r2["result"]["status"] == "partial" and r2["clusters"]["route_cluster_count"] == 2,
        "w3 continuous publication-style prose and numeric references": r3["result"]["status"] == "completed"
            and r3["qa_report"]["metrics"]["claim_evidence_rows"] == 4 and bool(r3["qa_report"]["metrics"]["cross_work_comparisons"]),
        "w4 empty input is explicit": r4["result"]["status"] == "refused" and bool(r4["result"]["uncertainties"]),
        **negative,
        **_interpretation_regressions(r3),
    }
    summary = {"all_passed": all(tests.values()), "suites": tests}
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_baseline(r3)
    write_json_atomic(root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
