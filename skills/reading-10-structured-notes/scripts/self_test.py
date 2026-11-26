#!/usr/bin/env python3
"""Self-test suite for reading-10-structured-notes (4 synthetic suites, negative anchor test, baseline comparison)."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audit_notes import audit_notes
from build_note_result import build_note_result
from ingest_source import ingest_input
from pdf_utils import write_json_atomic, write_text_lf
from render_notes import apply_section_reviews, render_notes_bundle, render_manifest

try:
    import fitz  # type: ignore
except ImportError as exc:
    raise RuntimeError("PyMuPDF (fitz) is required for self_test.py") from exc

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore


def _build_s1_catalysis_paper(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    p1, p2 = doc[0], doc[1]

    for idx, pg in enumerate((p1, p2), start=1):
        pg.insert_text((50, 34), "Journal of Synthetic Catalysis", fontsize=8.5, fontname="helv")
        pg.insert_text((285, 815), f"- {idx} -", fontsize=8.5, fontname="helv")

    p1.insert_text((50, 72), "Selective CO2 Reduction over Cu-In2O3 Catalysts", fontsize=16.0, fontname="hebo")
    p1.insert_text((50, 96), "Alice Zhang, Bob Li", fontsize=10.5, fontname="helv")
    p1.insert_text((50, 112), "Beijing University of Chemical Technology", fontsize=9.5, fontname="helv")
    p1.insert_text(
        (50, 138),
        "Abstract: We investigate CO2 reduction reaction (CO2RR) selectivity at 573 K, achieving 92.5% selectivity.",
        fontsize=9.8,
        fontname="helv",
    )

    # Left column
    p1.insert_text((50, 178), "1. Introduction", fontsize=12.0, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(50, 194, 280, 320),
        "We aim to improve methanol selectivity during CO2RR over oxide-supported copper catalysts. "
        "Next, we examine synthesis parameters in Section 2.",
        fontsize=10.0,
        fontname="helv",
    )
    p1.insert_text((50, 340), "2. Experimental Methods", fontsize=12.0, fontname="hebo")
    p1.insert_text((50, 365), "2.1 Catalyst Synthesis", fontsize=10.8, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(50, 382, 280, 500),
        "Catalyst samples were prepared by co-precipitation. Testing was operated at 573 K, "
        "3.0 MPa, and GHSV = 6000 mL g-1 h-1 under dry feed gas.",
        fontsize=10.0,
        fontname="helv",
    )

    # Right column
    p1.insert_text((315, 178), "3. Results and Discussion", fontsize=12.0, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(315, 194, 545, 310),
        "As shown in Table 1 and Figure 1, Cu-In2O3 achieves high CO2RR stability across 100 h. "
        "However, the reaction performance is restricted to dry feed gas without water co-feeding.",
        fontsize=10.0,
        fontname="helv",
    )
    p1.draw_rect(fitz.Rect(330, 330, 520, 410))
    p1.insert_text((320, 428), "Figure 1. CO2RR selectivity over 100 h.", fontsize=9.0, fontname="helv")

    # Page 2: Table 1 (selectivity = 91.8% vs 92.5% in abstract to trigger conflict detector) + Conclusion + Refs
    p2.insert_text((50, 70), "Table 1. Catalytic CO2RR metrics summary.", fontsize=10.0, fontname="hebo")
    p2.draw_line(fitz.Point(50, 82), fitz.Point(480, 82), width=1.2)
    p2.insert_text((60, 97), "Catalyst", fontsize=9.5, fontname="hebo")
    p2.insert_text((220, 97), "Selectivity", fontsize=9.5, fontname="hebo")
    p2.insert_text((360, 97), "Conversion", fontsize=9.5, fontname="hebo")
    p2.draw_line(fitz.Point(50, 105), fitz.Point(480, 105), width=0.8)
    p2.insert_text((60, 122), "Cu-In2O3", fontsize=9.5, fontname="helv")
    p2.insert_text((220, 122), "91.8%", fontsize=9.5, fontname="helv")
    p2.insert_text((360, 122), "24.1%", fontsize=9.5, fontname="helv")
    p2.draw_line(fitz.Point(50, 132), fitz.Point(480, 132), width=1.2)

    p2.insert_text((50, 175), "4. Conclusion", fontsize=12.0, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(50, 192, 480, 260),
        "Therefore, Cu-In2O3 is effective for CO2RR at 573 K, though restricted to dry feed conditions.",
        fontsize=10.0,
        fontname="helv",
    )
    p2.insert_text((50, 285), "References", fontsize=12.0, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(50, 305, 480, 370),
        "[1] A. Smith et al., J. Catal. 2024, 420, 11-20.\n[2] B. Li et al., ACS Catal. 2025, 15, 220-231.",
        fontsize=9.5,
        fontname="helv",
    )
    doc.save(str(path))
    doc.close()


def _build_s2_paper_b_md(path: Path) -> None:
    content = """# Ni-CeO2 Methanation under Mild Pressure

Authors: Carol Wang, David Chen

Abstract: We report methanation activity over Ni-CeO2 catalysts tested at 523 K and 1.0 MPa.

## 1. Introduction
Our objective is to lower the light-off temperature for CO2 methanation using oxygen-vacancy-rich supports.

## 2. Methods and Materials
Catalyst samples were synthesized via wet impregnation using CeO2 nanorods. Reaction measurements were conducted at 523 K and 1.0 MPa.

## 3. Results and Limitations
| Catalyst | Selectivity | Yield |
| --- | --- | --- |
| Ni-CeO2 | 96.4% | 78.2% |

Ni-CeO2 achieves 96.4% selectivity at 523 K. However, a key limitation is sulfur poisoning above 10 ppm H2S.
"""
    write_text_lf(path, content)


def _build_s3_long_16pages(path: Path) -> None:
    doc = fitz.open()
    for _ in range(16):
        doc.new_page(width=595, height=842)
    for i in range(16):
        pg = doc[i]
        pnum = i + 1
        pg.insert_text((285, 815), f"- {pnum} -", fontsize=8.5, fontname="helv")
        if pnum == 1:
            pg.insert_text((50, 75), "Long-Form Multi-Page Benchmark Study", fontsize=15.0, fontname="hebo")
            pg.insert_text((50, 110), "Abstract: We benchmark structured note generation over 16 pages.", fontsize=10.0, fontname="helv")
            pg.insert_text((50, 145), "1. Introduction", fontsize=12.0, fontname="hebo")
        elif pnum in (4, 8, 12, 16):
            sec_idx = {4: "2. Methods", 8: "3. Experiments", 12: "4. Discussion", 16: "5. Conclusion"}[pnum]
            pg.insert_text((50, 80), sec_idx, fontsize=12.0, fontname="hebo")
        pg.insert_textbox(
            fitz.Rect(50, 175, 520, 320),
            f"Page {pnum} analysis describes dataset evaluation at 300 K with accuracy reaching {80 + (pnum % 10)}.5%.",
            fontsize=10.0,
            fontname="helv",
        )
    doc.save(str(path))
    doc.close()


def _build_s4a_encrypted(path: Path) -> None:
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((72, 72), "Encrypted manuscript.", fontsize=12)
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw123", owner_pw="opw123")
    doc.close()


def _build_s4b_scanned(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (1190, 1684), "white")
        draw = ImageDraw.Draw(img)
        draw.text((120, 160), "Scanned Paper Without Text Layer", fill="black")
        tmp_png = path.parent / "_tmp_s4b.png"
        img.save(str(tmp_png))
        doc[0].insert_image(fitz.Rect(0, 0, 595, 842), filename=str(tmp_png))
        tmp_png.unlink(missing_ok=True)
    doc.save(str(path))
    doc.close()


def _run_pipeline_for_dir(in_dir: Path, work_dir: Path, case_data: Dict[str, Any]) -> Dict[str, Any]:
    case_path = in_dir / "case.json"
    write_json_atomic(case_path, case_data)
    manifest = ingest_input(in_dir, work_dir / "ingested", case_path=case_path, ocr_mode="auto", save_crops=False)

    docs: List[Dict[str, Any]] = []
    docs_by_id: Dict[str, Dict[str, Any]] = {}

    for item in manifest["papers"]:
        doc = json.loads(Path(item["document_json"]).read_text(encoding="utf-8"))
        pid = item["paper_id"]
        docs.append(doc)
        docs_by_id[pid] = doc

    notes_dir = work_dir / "notes"
    bundle = render_manifest(manifest, notes_dir, {})
    md_txt = (notes_dir / "structured_notes.md").read_text(encoding="utf-8")
    comp_md = (notes_dir / "comparison_matrix.md").read_text(encoding="utf-8")
    result = build_note_result(case_data, bundle, docs=docs, markdown_text=md_txt, comparison_md=comp_md)
    write_json_atomic(work_dir / "result.json", result)
    audit = audit_notes(case_data, result, docs_by_id, bundle)
    write_json_atomic(work_dir / "audit_report.json", audit)
    return {"manifest": manifest, "bundle": bundle, "result": result, "audit": audit, "docs_by_id": docs_by_id}


def run_self_test(out_dir: Path, compare_baseline: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"tests": {}, "all_passed": True}

    # --- S1: Single paper + negative hallucinated-anchor audit test ---
    s1_in = out_dir / "s1_in"
    s1_in.mkdir(parents=True, exist_ok=True)
    s1_pdf = s1_in / "s1_catalysis_paper.pdf"
    _build_s1_catalysis_paper(s1_pdf)
    s1_case = {
        "case_id": "selftest-s1",
        "input_files": ["s1_catalysis_paper.pdf"],
        "output_contract": {
            "required": [
                "case_id",
                "status",
                "paper_card",
                "section_notes",
                "knowledge_entries",
                "terminology_glossary",
                "conflicts",
                "sources",
                "verification_ledger",
                "uncertainties",
            ]
        },
    }
    s1_run = _run_pipeline_for_dir(s1_in, out_dir / "s1_work", s1_case)
    s1_res = s1_run["result"]
    s1_aud = s1_run["audit"]
    assert s1_aud["pass"], f"S1 audit failed: {s1_aud['errors']}"
    assert s1_aud["metrics"]["anchor_verification_rate"] == 1.0
    assert s1_aud["metrics"]["section_coverage_ratio"] == 1.0
    assert any(g["acronym"] == "CO2RR" and g["occurrence_count"] >= 3 for g in s1_res["terminology_glossary"])
    assert any(c["metric"] == "selectivity" for c in s1_res["conflicts"]), f"Expected selectivity conflict, got {s1_res['conflicts']}"
    assert "573 K" in s1_res["paper_card"]["operating_conditions_and_preconditions"]["summary"]
    assert "dry feed" in s1_res["paper_card"]["limitations_and_threats_to_validity"]["summary"]

    # Negative test: mutate an anchor to non-existent block_id and verify audit catches it
    bad_bundle = copy.deepcopy(s1_run["bundle"])
    bad_bundle["papers"][0]["paper_card"]["research_question"]["anchors"][0]["block_id"] = "p9_b99"
    bad_res = copy.deepcopy(s1_res)
    bad_res["paper_card"]["research_question"]["anchors"][0]["block_id"] = "p9_b99"
    neg_aud = audit_notes(s1_case, bad_res, s1_run["docs_by_id"], bad_bundle)
    assert not neg_aud["pass"], "Negative audit test failed to detect hallucinated anchor!"

    report["tests"]["s1_catalysis_paper"] = {
        "pass": True,
        "audit_metrics": s1_aud["metrics"],
        "conflicts_detected": len(s1_res["conflicts"]),
        "acronyms": [g["acronym"] for g in s1_res["terminology_glossary"]],
        "negative_anchor_caught": True,
    }

    # --- S2: Multi-paper mixed format (PDF + Markdown) + comparison matrix ---
    s2_in = out_dir / "s2_in"
    s2_in.mkdir(parents=True, exist_ok=True)
    _build_s1_catalysis_paper(s2_in / "paper_a.pdf")
    _build_s2_paper_b_md(s2_in / "paper_b.md")
    s2_case = {
        "case_id": "selftest-s2-multi",
        "input_files": ["paper_a.pdf", "paper_b.md"],
        "output_contract": {
            "required": ["case_id", "status", "paper_count", "paper_cards", "comparison_matrix", "search_index", "sources", "uncertainties"]
        },
    }
    s2_run = _run_pipeline_for_dir(s2_in, out_dir / "s2_work", s2_case)
    assert s2_run["audit"]["pass"], f"S2 audit failed: {s2_run['audit']['errors']}"
    assert s2_run["result"]["paper_count"] == 2
    assert len(s2_run["result"]["comparison_matrix"]) == 2
    assert (out_dir / "s2_work" / "notes" / "comparison_matrix.md").exists()
    report["tests"]["s2_multi_paper"] = {
        "pass": True,
        "paper_count": s2_run["result"]["paper_count"],
        "comparison_rows": len(s2_run["result"]["comparison_matrix"]),
        "audit_metrics": s2_run["audit"]["metrics"],
    }

    # --- S3: Long 16-page document ---
    s3_in = out_dir / "s3_in"
    s3_in.mkdir(parents=True, exist_ok=True)
    _build_s3_long_16pages(s3_in / "long_16p.pdf")
    s3_case = {
        "case_id": "selftest-s3-long",
        "input_files": ["long_16p.pdf"],
        "output_contract": {"required": ["case_id", "status", "paper_card", "section_notes", "sources", "uncertainties"]},
    }
    s3_run = _run_pipeline_for_dir(s3_in, out_dir / "s3_work", s3_case)
    assert s3_run["audit"]["pass"], f"S3 audit failed: {s3_run['audit']['errors']}"
    assert len(s3_run["result"]["section_notes"]) >= 5
    report["tests"]["s3_long_16pages"] = {
        "pass": True,
        "sections": len(s3_run["result"]["section_notes"]),
        "audit_metrics": s3_run["audit"]["metrics"],
    }

    # --- S4: Encrypted & Scanned boundary cases ---
    s4a_in = out_dir / "s4a_in"
    s4a_in.mkdir(parents=True, exist_ok=True)
    _build_s4a_encrypted(s4a_in / "enc.pdf")
    s4a_case = {"case_id": "selftest-s4a-enc", "input_files": ["enc.pdf"], "output_contract": ["case_id", "status", "paper_card", "uncertainties"]}
    s4a_run = _run_pipeline_for_dir(s4a_in, out_dir / "s4a_work", s4a_case)
    assert s4a_run["result"]["status"] == "refused"
    assert s4a_run["audit"]["pass"]

    s4b_in = out_dir / "s4b_in"
    s4b_in.mkdir(parents=True, exist_ok=True)
    _build_s4b_scanned(s4b_in / "scan.pdf")
    s4b_case = {"case_id": "selftest-s4b-scan", "input_files": ["scan.pdf"], "output_contract": ["case_id", "status", "paper_card", "uncertainties"]}
    s4b_run = _run_pipeline_for_dir(s4b_in, out_dir / "s4b_work", s4b_case)
    assert s4b_run["result"]["status"] in ("unverifiable", "partial")
    assert s4b_run["audit"]["pass"]

    report["tests"]["s4_encrypted_and_scanned"] = {
        "pass": True,
        "encrypted_status": s4a_run["result"]["status"],
        "scanned_status": s4b_run["result"]["status"],
    }

    # S5: nested mixed inputs without an explicit list; the claim sits beyond the lead excerpt.
    s5_in = out_dir / "s5_in"
    papers_dir = s5_in / "materials"
    papers_dir.mkdir(parents=True, exist_ok=True)
    _build_s1_catalysis_paper(papers_dir / "paper.pdf")
    boundary = "Under humid conditions, treatment A did not improve accuracy by 4% compared with treatment B."
    source = "# Sensor replication\n\n## Results\n\n" + (
        "The measurement protocol describes preparation and sample handling. " * 10
    ) + "\n\n" + boundary
    write_text_lf(papers_dir / "replication.md", source)
    write_text_lf(s5_in / "task.md", "Describe the supplied papers.")
    s5_case = {"case_id": "selftest-s5", "output_contract": ["case_id", "paper_cards", "section_notes"]}
    s5 = _run_pipeline_for_dir(s5_in, out_dir / "s5_work", s5_case)
    assert s5["audit"]["pass"], s5["audit"]["errors"]
    assert s5["manifest"]["paper_count"] == 2
    note = s5["bundle"]["papers"][1]
    section = next(sn for sn in note["section_notes"] if any(p["text"] == boundary for p in sn["key_points"]))
    assert boundary not in section["lead_excerpt"]
    point = next(p for p in section["key_points"] if p["text"] == boundary)
    assert point["anchor"]["source_lines"] == [7, 7]
    sid = section["section_id"]
    review = {sid: {"summary": "潮湿条件下，相比 B，A 未能将准确率提高 4%。",
                    "evidence": [{"block_id": point["anchor"]["block_id"], "quote_snippet": boundary}]}}
    apply_section_reviews(note, s5["docs_by_id"]["paper_2"], review)
    reviewed = render_notes_bundle(s5["bundle"]["papers"], out_dir / "s5_reviewed")
    markdown = (out_dir / "s5_reviewed" / "structured_notes.md").read_text(encoding="utf-8")
    assert review[sid]["summary"] in markdown and "lines 7–7" in markdown
    assert audit_notes(s5_case, s5["result"], s5["docs_by_id"], reviewed)["pass"]
    mutations = {
        "negation": boundary.replace("did not improve", "did improve"),
        "unit": boundary.replace("4%", "4 K"),
        "number": boundary.replace("4%", "14%"),
        "comparator": boundary.replace("treatment A", "treatment X").replace("treatment B", "treatment A").replace("treatment X", "treatment B"),
    }
    for label, changed in mutations.items():
        bad_result = copy.deepcopy(s5["result"])
        # Alter only the exported second paper's card, leaving its real source quote intact.
        slot = bad_result["paper_cards"][1]["key_findings_and_metrics"]
        slot["summary"] = changed
        assert not audit_notes(s5_case, bad_result, s5["docs_by_id"], reviewed)["pass"], label
    forged = copy.deepcopy(review)
    forged[sid]["evidence"][0]["quote_snippet"] = mutations["negation"]
    try:
        apply_section_reviews(note, s5["docs_by_id"]["paper_2"], forged)
    except ValueError as exc:
        assert "quote differs" in str(exc)
    else:
        raise AssertionError("A quotation with the negation removed passed the review")
    report["tests"]["s5_nested_materials_and_final_claims"] = {
        "pass": True, "papers": 2, "late_finding_source_lines": [7, 7],
        "corrupted_claims_rejected": list(mutations), "forged_quote_rejected": True,
    }

    if compare_baseline:
        d = fitz.open(str(s1_pdf))
        raw_txt = "\n".join(pg.get_text("text") for pg in d)
        d.close()
        report["baseline_comparison"] = {
            "S1_catalysis_paper": {
                "baseline_section_aligned_notes": 0,
                "skill_section_aligned_notes": len(s1_res["section_notes"]),
                "baseline_structured_anchors": 0,
                "skill_verified_anchors": s1_aud["metrics"]["anchors_valid"],
                "baseline_acronym_glossary_entries": 0,
                "skill_acronym_glossary_entries": len(s1_res["terminology_glossary"]),
                "baseline_intra_paper_conflicts_flagged": 0,
                "skill_intra_paper_conflicts_flagged": len(s1_res["conflicts"]),
                "baseline_raw_chars": len(raw_txt),
            }
        }

    write_json_atomic(out_dir / "self_test_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-10-structured-notes self-tests.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="reading10_selftest_"))
    rep = run_self_test(out_dir, compare_baseline=args.compare_baseline)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
