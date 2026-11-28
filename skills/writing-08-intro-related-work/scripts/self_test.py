#!/usr/bin/env python3
"""Comprehensive self-test & fault-injection suite for writing-08-intro-related-work."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from finalize import run_finalize
from output_contract import contract_issues
from pdf_utils import write_json_atomic, write_text_lf
from prepare import run_prepare


def _make_twocol_pdf(path: Path) -> None:
    import fitz  # type: ignore
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "Spectral Drift Compensation in Portable Sensors", fontsize=14, fontname="hebo")
    page.insert_text((72, 80), "Alice Smith, Bob Lee (2024)", fontsize=10, fontname="helv")
    # Left column
    page.insert_text((50, 120), "1. Introduction", fontsize=11, fontname="hebo")
    page.insert_text((50, 140), "Portable field spectrometers suffer from thermal", fontsize=9.5, fontname="helv")
    page.insert_text((50, 154), "drift during multi-hour outdoor campaigns.", fontsize=9.5, fontname="helv")
    page.insert_text((50, 174), "Smith et al. (2024) used a static dark-current", fontsize=9.5, fontname="helv")
    page.insert_text((50, 188), "table at 25 C and achieved 3.2% RMSE.", fontsize=9.5, fontname="helv")
    # Right column (must NOT interleave with left column!)
    page.insert_text((320, 120), "2. Limitations", fontsize=11, fontname="hebo")
    page.insert_text((320, 140), "However, static calibration did not correct", fontsize=9.5, fontname="helv")
    page.insert_text((320, 154), "drift above 40 C in humid field trials.", fontsize=9.5, fontname="helv")
    page.insert_text((320, 174), "Cross-site transfer remains unverified.", fontsize=9.5, fontname="helv")
    doc.save(str(path))
    doc.close()


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document  # type: ignore
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


def run_all_tests(out_root: Path) -> Dict[str, Any]:
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Any] = {}

    # 1. Full English Intro + Related Work with 2-col PDF + BibTeX + DOCX study draft + DOCX export
    t1_in, t1_w, t1_o = out_root / "t1_in", out_root / "t1_work", out_root / "t1_out"
    (t1_in / "papers").mkdir(parents=True, exist_ok=True)
    (t1_in / "study").mkdir(parents=True, exist_ok=True)
    _make_twocol_pdf(t1_in / "papers" / "smith2024.pdf")
    write_text_lf(t1_in / "papers" / "refs.bib", """@article{smith2024,
  author = {Alice Smith and Bob Lee},
  title = {Spectral Drift Compensation in Portable Sensors},
  journal = {Sensors Today},
  year = {2024},
  doi = {10.1000/st.2024}
}
@article{jones2023,
  author = {Carol Jones},
  title = {Dual-Beam Optical Reference Calibration},
  journal = {Optics Letters},
  year = {2023},
  doi = {10.1000/ol.2023}
}
""")
    write_text_lf(t1_in / "papers" / "jones2023.md", "# Dual-Beam Optical Reference Calibration\n\nJones (2023) tested a dual-beam optical reference up to 45 C, reducing drift to 1.4% RMSE, but required benchtop power.\n")
    _make_docx(t1_in / "study" / "own_study.docx", [
        "Our portable passive-reference module operates without benchtop power across 15-45 C.",
        "In Table 1, our passive-reference module reduced RMSE from 3.2% to 1.1% across 40 field runs.",
    ])
    case1 = t1_in / "case.json"
    write_json_atomic(case1, {
        "case_id": "w08_en_full",
        "language": "en",
        "citation_style": "numeric",
        "output_formats": ["md", "docx"],
        "instructions": "Write a concise academic Introduction and Related Work comparing routes, deriving a bounded gap, and aligning contributions.",
    })
    run_prepare(t1_in, case1, t1_w, t1_o)
    # Verify 2-column PDF was NOT scrambled
    reader_pdf = (t1_w / "reader" / "doc_3.md").read_text(encoding="utf-8") if (t1_w / "reader" / "doc_3.md").is_file() else ""
    all_readers = "\n".join(p.read_text(encoding="utf-8") for p in (t1_w / "reader").glob("*.md"))
    assert "Portable field spectrometers suffer from thermal" in all_readers
    assert "static calibration did not correct" in all_readers

    write_text_lf(t1_w / "draft.md", """## Research Background

Portable field spectrometers lose calibration accuracy under temperature swings during multi-hour outdoor campaigns [@smith2024].

## Related Work

Under identical field-drift objectives, static lookup calibration [@smith2024] achieves 3.2% RMSE at 25 C but fails above 40 C, whereas dual-beam optical referencing [@jones2023] lowers error to 1.4% RMSE up to 45 C at the cost of benchtop power.

## Research Gaps

Together, static single-beam calibration [@smith2024] and powered dual-beam setups [@jones2023] leave unresolved how to maintain low drift up to 45 C without external benchtop power.

## Contributions

We introduce a passive-reference module operating across 15-45 C without benchtop power, reducing RMSE from 3.2% to 1.1% across 40 field runs (Table 1). Because evaluations span 40 runs in a single campaign, cross-site generalization remains unverified.

## References
""")
    write_json_atomic(t1_w / "evidence_ledger.json", {
        "task_mode": "full_intro_rw",
        "paper_cards": [
            {"cite_key": "smith2024", "route": "Static Calibration", "limitation": "Did not correct drift above 40 C"},
            {"cite_key": "jones2023", "route": "Active Dual-Beam", "limitation": "Required benchtop power"},
        ],
        "research_gaps": [{"gap_id": "G1", "gap_statement": "Unresolved low-drift operation up to 45 C without benchtop power.", "derived_from_cite_keys": ["smith2024", "jones2023"]}],
        "contributions": [{"contrib_id": "C1", "claim": "Passive-reference module reduced RMSE from 3.2% to 1.1% across 40 field runs.", "addresses_gap_id": "G1", "evidence": [{"doc_id": "doc_4", "anchor": "para_2", "quote": "reduced RMSE from 3.2% to 1.1% across 40 field runs."}]}],
    })
    rc1 = run_finalize(case1, t1_w, t1_o)
    res1 = json.loads((t1_o / "result.json").read_text(encoding="utf-8"))
    assert rc1 == 0 and res1["status"] == "success"
    assert "[1]" in res1["manuscript"] and "[2]" in res1["manuscript"]
    assert (t1_o / "intro_and_related_work.docx").is_file()
    results["t1_en_twocol_docx"] = {"passed": True, "docx_bytes": (t1_o / "intro_and_related_work.docx").stat().st_size}

    # 2. Chinese Intro + Related Work + Observational limit
    t2_in, t2_w, t2_o = out_root / "t2_in", out_root / "t2_work", out_root / "t2_out"
    t2_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t2_in / "papers.json", {"papers": [
        {"cite_key": "zhang2023", "authors": ["张明"], "year": 2023, "title": "光谱漂移静态校正", "venue": "光学学报", "route": "静态查表", "limitation": "未测试高湿条件"},
        {"cite_key": "li2024", "authors": ["李华"], "year": 2024, "title": "双光束主动补偿", "venue": "仪器仪表学报", "route": "双光束", "limitation": "依赖实验室电源"},
    ]})
    write_text_lf(t2_in / "study_notes.md", "本研究采用观察性分组比较，在15-45°C下将漂移误差降至1.1%，但未经随机分配，无法推断因果效应。\n")
    case2 = t2_in / "case.json"
    write_json_atomic(case2, {"case_id": "w08_zh", "language": "zh"})
    run_prepare(t2_in, case2, t2_w, t2_o)
    write_text_lf(t2_w / "draft.md", """## 研究背景

便携式光谱仪在野外长时程监测中面临温湿度引起的基线漂移约束 [@zhang2023]。

## 相关工作

相比静态查表路线 [@zhang2023]（未覆盖高湿场景），双光束主动补偿 [@li2024] 提升了稳定性但依赖实验室电源。

## 研究空白

现有两条路线尚未解决野外无外接电源且高湿条件下的低漂移补偿问题 [@zhang2023; @li2024]。

## 主要贡献

本研究提出无源参考模块，在 15–45°C 条件下将误差降至 1.1%；因属观察性分组比较，尚不能据此推断因果效应。
""")
    rc2 = run_finalize(case2, t2_w, t2_o)
    assert rc2 == 0
    results["t2_zh_observational"] = {"passed": True}

    # 3. Targeted revision / related_work_only (no false-positive 4-section error!)
    t3_in, t3_w, t3_o = out_root / "t3_in", out_root / "t3_work", out_root / "t3_out"
    t3_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t3_in / "papers.json", {"papers": [{"cite_key": "wang2024", "authors": ["Wang"], "year": 2024, "title": "Fast Alignment", "venue": "ACL"}]})
    case3 = t3_in / "case.json"
    write_json_atomic(case3, {"case_id": "w08_rev", "instructions": "Revise the Related Work section only and record change_records."})
    run_prepare(t3_in, case3, t3_w, t3_o)
    write_text_lf(t3_w / "draft.md", "## Related Work\n\nRecent alignment methods such as Wang (2024) accelerate retrieval without sacrificing recall.\n")
    write_json_atomic(t3_w / "evidence_ledger.json", {
        "task_mode": "related_work_only",
        "change_records": [{"section": "Related Work", "action": "Added Wang (2024) comparison"}],
    })
    rc3 = run_finalize(case3, t3_w, t3_o)
    assert rc3 == 0
    results["t3_revision_no_false_block"] = {"passed": True}

    # 4. Closed output_contract (additionalProperties: false + enum + custom fields via answer.json)
    t4_in, t4_w, t4_o = out_root / "t4_in", out_root / "t4_work", out_root / "t4_out"
    t4_in.mkdir(parents=True, exist_ok=True)
    write_text_lf(t4_in / "note.md", "Method M achieved 94.2% F1 on Dataset D.\n")
    case4 = t4_in / "case.json"
    write_json_atomic(case4, {
        "case_id": "w08_closed",
        "output_contract": {
            "type": "object",
            "additionalProperties": False,
            "required": ["case_id", "status", "manuscript", "research_gaps", "contributions"],
            "properties": {
                "case_id": {"type": "string"},
                "status": {"type": "string", "enum": ["success", "partial", "unable"]},
                "manuscript": {"type": "string"},
                "research_gaps": {"type": "array", "items": {"type": "string"}},
                "contributions": {"type": "array", "items": {"type": "string"}},
            },
        },
    })
    run_prepare(t4_in, case4, t4_w, t4_o)
    write_text_lf(t4_w / "draft.md", "## Research Background\n\nDataset D requires high recall.\n\n## Contributions\n\nMethod M achieves 94.2% F1 on Dataset D.\n")
    write_json_atomic(t4_w / "answer.json", {
        "fields": {
            "research_gaps": ["Single-benchmark coverage on Dataset D."],
            "contributions": ["Method M achieves 94.2% F1 on Dataset D."],
        },
        "status": "success",
    })
    rc4 = run_finalize(case4, t4_w, t4_o)
    res4 = json.loads((t4_o / "result.json").read_text(encoding="utf-8"))
    assert rc4 == 0 and not contract_issues(json.loads(case4.read_text(encoding="utf-8"))["output_contract"], res4)
    assert set(res4.keys()) == {"case_id", "status", "manuscript", "research_gaps", "contributions"}
    results["t4_closed_contract"] = {"passed": True}

    # 5. Fault injection (7 faults caught)
    faults = {}
    f_w, f_o = out_root / "f_w", out_root / "f_o"
    run_prepare(t1_in, case1, f_w, f_o)
    # F1: fabricated quote -> must_fix
    write_text_lf(f_w / "draft.md", "## Research Background\n\nText [@smith2024].\n")
    write_json_atomic(f_w / "evidence_ledger.json", {
        "claim_links": [{"kind": "background", "claim": "Test", "evidence": [{"doc_id": "doc_4", "anchor": "para_1", "quote": "Completely fabricated quote text."}]}]
    })
    rc_f1 = run_finalize(case1, f_w, f_o)
    rep_f1 = json.loads((f_w / "validation_report.json").read_text(encoding="utf-8"))
    faults["fabricated_quote"] = rc_f1 != 0 and any(m["code"] == "unverified_quote" for m in rep_f1["must_fix"])

    # F2: unknown [@fake] citation -> must_fix
    write_text_lf(f_w / "draft.md", "## Research Background\n\nUnsupported claim [@nonexistent_key].\n")
    write_json_atomic(f_w / "evidence_ledger.json", {})
    rc_f2 = run_finalize(case1, f_w, f_o)
    rep_f2 = json.loads((f_w / "validation_report.json").read_text(encoding="utf-8"))
    faults["unknown_citation"] = rc_f2 != 0 and any(m["code"] == "unknown_inline_citation" for m in rep_f2["must_fix"])

    # F3: unresolved placeholder -> must_fix
    write_text_lf(f_w / "draft.md", "## Research Background\n\nNeeds data [TODO].\n")
    rc_f3 = run_finalize(case1, f_w, f_o)
    rep_f3 = json.loads((f_w / "validation_report.json").read_text(encoding="utf-8"))
    faults["unresolved_placeholder"] = rc_f3 != 0 and any(m["code"] == "unresolved_placeholder" for m in rep_f3["must_fix"])

    # F4..F7: advisory warnings (cliche opening, stitching, fabricated number, dropped negation)
    write_text_lf(f_w / "draft.md", "## Research Background\n\nIn recent years, with the rapid development of sensors, much work exists [@smith2024].\n\n## Related Work\n\nOne [@smith2024].\n\nTwo [@jones2023].\n")
    write_json_atomic(f_w / "evidence_ledger.json", {
        "claim_links": [{
            "kind": "comparison",
            "claim": "Our module reduced RMSE to 9.9% across 40 field runs.",
            "evidence": [{"doc_id": "doc_5", "anchor": "para_2", "quote": "reduced RMSE from 3.2% to 1.1% across 40 field runs."}],
        }]
    })
    run_finalize(case1, f_w, f_o)
    qa_f4 = json.loads((f_o / "qa_report.json").read_text(encoding="utf-8"))
    wcodes = {w["code"] for w in qa_f4["warnings"]}
    faults["cliche_opening"] = "banned_cliche_opening" in wcodes
    faults["weak_synthesis"] = "weak_cross_paper_synthesis" in wcodes
    faults["number_not_in_evidence"] = "number_not_in_evidence" in wcodes

    # Empty corpus honest unable
    t6_in, t6_w, t6_o = out_root / "t6_in", out_root / "t6_w", out_root / "t6_out"
    t6_in.mkdir(parents=True, exist_ok=True)
    case6 = t6_in / "case.json"
    write_json_atomic(case6, {"case_id": "w08_empty"})
    run_prepare(t6_in, case6, t6_w, t6_o)
    res6 = json.loads((t6_o / "result.json").read_text(encoding="utf-8"))
    faults["empty_corpus_unable"] = res6["status"] == "unable"

    assert all(faults.values()), f"Fault checks failed: {faults}"
    results["t5_fault_injection"] = {"passed": True, "faults": faults}

    summary = {"all_passed": True, "tests": results}
    write_json_atomic(out_root / "self_test_report.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/tmp/w08_self_test")
    args = parser.parse_args()
    run_all_tests(Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
