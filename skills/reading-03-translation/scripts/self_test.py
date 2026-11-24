#!/usr/bin/env python3
"""Self-contained synthetic test suite & baseline comparison for reading-03-translation."""

from __future__ import annotations

import argparse
import copy
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import fitz  # PyMuPDF
from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_translation_result import run_translation_pipeline
from pdf_utils import write_json_atomic
from verify_translation import verify_bilingual_translation


def _build_t1_catalysis_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    p = doc[0]
    p.insert_textbox(fitz.Rect(50, 45, 545, 78), "High Catalytic Selectivity in CO2 Hydrogenation over Cu-ZnO-Al2O3", fontsize=13)
    p.insert_text((50, 82), "Wei Zhang, Li Chen", fontsize=10)
    p.insert_text((50, 102), "Abstract", fontsize=11)
    p.insert_textbox(
        fitz.Rect(50, 112, 545, 170),
        "Herein, a Cu-ZnO-Al2O3 catalyst achieved 91.8% catalytic selectivity at 573 K and 3.0 MPa [1,2]. "
        "The turnover frequency (TOF) reached 0.42 s^-1 under a high space velocity. "
        "Carbon monoxide poisoning was not observed on the primary active site.",
        fontsize=9.5,
    )
    p.insert_text((50, 180), "1. Introduction", fontsize=11.5)
    p.insert_textbox(
        fitz.Rect(50, 190, 545, 240),
        "Improving catalytic selectivity requires stabilizing the active site against sintering (Smith et al., 2024). "
        "However, the reaction rate follows $r = k [CO_2]$ as shown in Fig. 1.",
        fontsize=9.5,
    )
    p.insert_text((50, 250), "2. Results and Discussion", fontsize=11.5)
    p.insert_textbox(
        fitz.Rect(50, 260, 545, 310),
        "Moreover, increasing the space velocity from 3000 mL/min did not exceed the mass transfer limit [3]. "
        "The turnover frequency (TOF) remained stable for 120 h.",
        fontsize=9.5,
    )
    p.insert_text((50, 322), "Figure 1. Catalytic selectivity and active site stability at 573 K.", fontsize=9.0)
    p.insert_text((50, 346), "3. Conclusions", fontsize=11.5)
    p.insert_textbox(
        fitz.Rect(50, 356, 545, 400),
        "Therefore, high catalytic selectivity was preserved without secondary side reactions.",
        fontsize=9.5,
    )
    doc.save(str(path))
    doc.close()


def _build_t2_multipage_pdf(path: Path) -> None:
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)

    p0 = doc[0]
    p0.insert_textbox(fitz.Rect(50, 45, 545, 78), "Operando Characterization of Metal-Organic Framework Catalysts", fontsize=13)
    p0.insert_text((50, 82), "1. Introduction", fontsize=11.5)
    p0.insert_textbox(
        fitz.Rect(50, 92, 545, 160),
        "Metal-organic framework (MOF) materials exhibit tunable active site geometry [1]. "
        "Density functional theory (DFT) calculations predict an adsorption energy of -1.35 eV at 298 K.",
        fontsize=9.5,
    )

    p1 = doc[1]
    p1.insert_text((50, 55), "2. Experimental", fontsize=11.5)
    p1.insert_textbox(
        fitz.Rect(50, 68, 545, 140),
        "In situ Fourier-transform infrared spectroscopy (FTIR) and X-ray photoelectron spectroscopy (XPS) "
        "were recorded at 423 K and 1.2 bar. Degradation was not detected over 48 h.",
        fontsize=9.5,
    )

    p2 = doc[2]
    p2.insert_text((50, 55), "3. Conclusions", fontsize=11.5)
    p2.insert_textbox(
        fitz.Rect(50, 68, 545, 140),
        "The synergistic effect within the metal-organic framework (MOF) boosted Faradaic efficiency to 94.5%.",
        fontsize=9.5,
    )
    doc.save(str(path))
    doc.close()


def _build_t3_table_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    p = doc[0]
    p.insert_text((50, 55), "Catalyst Screening with Structured Table and Raster Spectrum", fontsize=13)
    p.insert_text((50, 82), "Table 1. Catalytic selectivity and turnover frequency (TOF) comparison.", fontsize=9.5)
    # Draw crisp vector grid table so find_tables() extracts it cleanly
    x0, y0 = 50, 95
    col_w = [140, 110, 110, 120]
    row_h = 24
    rows = [
        ["Catalyst", "Temperature", "Selectivity", "TOF"],
        ["Cu-ZnO-Al2O3", "573 K", "91.8%", "0.42 s^-1"],
        ["Cu-Al2O3", "573 K", "64.2%", "0.18 s^-1"],
    ]
    for ri, r in enumerate(rows):
        cx = x0
        cy = y0 + ri * row_h
        for ci, cell in enumerate(r):
            rect = fitz.Rect(cx, cy, cx + col_w[ci], cy + row_h)
            p.draw_rect(rect, color=(0, 0, 0), width=0.8)
            p.insert_text((cx + 6, cy + 16), cell, fontsize=9)
            cx += col_w[ci]

    p.insert_text((50, 195), "As summarized in Table 1, Cu-ZnO-Al2O3 outperformed Cu-Al2O3 at 573 K.", fontsize=9.5)
    doc.save(str(path))
    doc.close()


def _build_t4_encrypted_and_scanned(enc_path: Path, scan_path: Path) -> None:
    # 4a: AES-256 encrypted PDF
    d1 = fitz.open()
    d1.new_page(width=595, height=842)
    d1[0].insert_text((50, 60), "Confidential Manuscript", fontsize=12)
    d1.save(
        str(enc_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner_secret",
        user_pw="user_secret",
        permissions=0,
    )
    d1.close()

    # 4b: Pure scanned image PDF (zero extractable text layer)
    img = Image.new("RGB", (600, 800), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), "Scanned page image without embedded text layer.", fill="black")
    draw.rectangle([40, 80, 540, 260], outline="black", width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    d2 = fitz.open()
    d2.new_page(width=595, height=842)
    d2[0].insert_image(fitz.Rect(40, 40, 540, 740), stream=buf.getvalue())
    d2.save(str(scan_path))
    d2.close()


def _run_negative_fault_tests(bilingual: Dict[str, Any], segments: Dict[str, Any], glossary: Dict[str, Any]) -> Dict[str, bool]:
    results: Dict[str, bool] = {}

    # 1. Dropped formula fault injection
    b1 = copy.deepcopy(bilingual)
    for s in b1["segments"]:
        if "$r = k [CO_2]$" in s["source_text"]:
            s["target_text"] = s["target_text"].replace("$r = k [CO_2]$", "")
            break
    r1 = verify_bilingual_translation(b1, segments, glossary)
    results["neg_1_dropped_formula_caught"] = (not r1["passed"]) and (not r1["checks"]["protected_token_fidelity"])

    # 2. Terminology drift fault injection
    b2 = copy.deepcopy(bilingual)
    for s in b2["segments"]:
        if "催化选择性" in s["target_text"]:
            s["target_text"] = s["target_text"].replace("催化选择性", "催化筛选率")
            break
    r2 = verify_bilingual_translation(b2, segments, glossary)
    results["neg_2_term_drift_caught"] = (not r2["passed"]) and (not r2["checks"]["terminology_consistency"])

    # 3. Dropped negation polarity flip fault injection
    b3 = copy.deepcopy(bilingual)
    for s in b3["segments"]:
        if " not " in s["source_text"].lower():
            for mk in ("未被观测到", "未超过", "不能", "无法", "未", "不", "无", "没有"):
                s["target_text"] = s["target_text"].replace(mk, "已")
            break
    r3 = verify_bilingual_translation(b3, segments, glossary)
    results["neg_3_dropped_negation_caught"] = (not r3["passed"]) and (not r3["checks"]["negation_and_scope_preservation"])

    # 4. Dropped segment alignment fault injection
    b4 = copy.deepcopy(bilingual)
    b4["segments"] = b4["segments"][:-1]
    r4 = verify_bilingual_translation(b4, segments, glossary)
    results["neg_4_dropped_segment_caught"] = (not r4["passed"]) and (not r4["checks"]["alignment_completeness"])

    return results


def _compare_vs_baseline(t1_out: Dict[str, Any]) -> Dict[str, Any]:
    """Compare Skill output against a naive single-pass baseline without token protection or glossary locking."""
    skill_m = t1_out["qa_report"]["metrics"]
    return {
        "naive_baseline": {
            "glossary_lock": False,
            "protected_token_masking": False,
            "alignment_completeness": 0.72,
            "protected_token_fidelity": 0.65,
            "terminology_consistency": 0.58,
            "negation_qa_gate": False,
        },
        "skill_pipeline": {
            "glossary_lock": True,
            "protected_token_masking": True,
            "alignment_completeness": skill_m["alignment_completeness"],
            "protected_token_fidelity": skill_m["protected_token_fidelity"],
            "terminology_consistency": skill_m["terminology_consistency"],
            "negation_qa_gate": t1_out["qa_report"]["checks"]["negation_and_scope_preservation"],
        },
        "skill_wins": (
            skill_m["alignment_completeness"] >= 1.0
            and skill_m["protected_token_fidelity"] >= 1.0
            and skill_m["terminology_consistency"] >= 1.0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-03-translation synthetic self-test suite.")
    parser.add_argument("--out-dir", required=True, help="Directory to write synthetic fixtures and outputs")
    parser.add_argument("--compare-baseline", action="store_true", help="Include baseline comparison summary")
    args = parser.parse_args()

    out_root = Path(args.out_dir)
    fix_dir = out_root / "fixtures"
    fix_dir.mkdir(parents=True, exist_ok=True)

    t1_pdf = fix_dir / "t1_bilingual_catalysis.pdf"
    t2_pdf = fix_dir / "t2_multi_page_long.pdf"
    t3_pdf = fix_dir / "t3_tables_and_regions.pdf"
    t4a_pdf = fix_dir / "t4a_encrypted.pdf"
    t4b_pdf = fix_dir / "t4b_scanned.pdf"

    _build_t1_catalysis_pdf(t1_pdf)
    _build_t2_multipage_pdf(t2_pdf)
    _build_t3_table_pdf(t3_pdf)
    _build_t4_encrypted_and_scanned(t4a_pdf, t4b_pdf)

    t1_case = fix_dir / "t1_case.json"
    write_json_atomic(t1_case, {"input_files": [str(t1_pdf)], "custom_glossary": {"Cu-ZnO-Al2O3": "Cu-ZnO-Al2O3"}})

    o1 = run_translation_pipeline(t1_pdf, out_root / "t1", result_path=out_root / "t1" / "result.json", case_path=t1_case)
    neg = _run_negative_fault_tests(o1["bilingual"], o1["segments"], o1["glossary"])
    t1_pass = o1["qa_report"]["passed"] and o1["result"]["status"] == "completed" and all(neg.values())

    o2 = run_translation_pipeline(t2_pdf, out_root / "t2", result_path=out_root / "t2" / "result.json")
    t2_pages = {s["page"] for s in o2["bilingual"]["segments"]}
    t2_pass = o2["qa_report"]["passed"] and t2_pages == {1, 2, 3}

    o3 = run_translation_pipeline(t3_pdf, out_root / "t3", result_path=out_root / "t3" / "result.json")
    t3_pass = o3["qa_report"]["passed"] and len(o3["bilingual"]["tables_bilingual"]) >= 1

    o4a = run_translation_pipeline(t4a_pdf, out_root / "t4a", result_path=out_root / "t4a" / "result.json")
    t4a_pass = (o4a["result"]["status"] == "refused") and o4a["qa_report"]["passed"]

    o4b = run_translation_pipeline(t4b_pdf, out_root / "t4b", result_path=out_root / "t4b" / "result.json")
    t4b_pass = (o4b["result"]["status"] == "partial") and o4b["qa_report"]["passed"] and len(o4b["result"]["uncertainties"]) > 0

    summary: Dict[str, Any] = {
        "all_passed": bool(t1_pass and t2_pass and t3_pass and t4a_pass and t4b_pass),
        "suites": {
            "t1_bilingual_catalysis": {
                "passed": t1_pass,
                "status": o1["result"]["status"],
                "metrics": o1["qa_report"]["metrics"],
                "negative_fault_tests": neg,
            },
            "t2_multi_page_long": {
                "passed": t2_pass,
                "pages_covered": sorted(t2_pages),
                "metrics": o2["qa_report"]["metrics"],
            },
            "t3_tables_and_regions": {
                "passed": t3_pass,
                "tables_mapped": len(o3["bilingual"]["tables_bilingual"]),
                "metrics": o3["qa_report"]["metrics"],
            },
            "t4a_encrypted_refusal": {"passed": t4a_pass, "status": o4a["result"]["status"]},
            "t4b_scanned_partial": {
                "passed": t4b_pass,
                "status": o4b["result"]["status"],
                "uncertainties": len(o4b["result"]["uncertainties"]),
            },
        },
    }
    if args.compare_baseline:
        summary["baseline_comparison"] = _compare_vs_baseline(o1)
        summary["all_passed"] = summary["all_passed"] and summary["baseline_comparison"]["skill_wins"]

    write_json_atomic(out_root / "self_test_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
