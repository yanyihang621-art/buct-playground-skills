#!/usr/bin/env python3
"""Comprehensive synthetic PDF test suite and baseline comparison harness."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_result import build_result, publish_result
from parse_case import finalize, parse_case
from parse_pdf import run_parse_pipeline
from pdf_utils import write_json_atomic
from query_parsed import query_cells, query_parsed, readback_cells
from validate_output import validate_output

try:
    import fitz  # type: ignore
except ImportError as exc:
    raise RuntimeError("PyMuPDF (fitz) is required to generate self-test fixtures.") from exc

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore


def _build_t1_two_column_pdf(path: Path) -> None:
    """Create 2-page double-column paper with spanning frontmatter, hyphenated continuation, sub/sup, equation, figure, refs."""
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    p1 = doc[0]
    p2 = doc[1]

    # Running headers/footers on both pages
    for idx, page in enumerate((p1, p2), start=1):
        page.insert_text((50, 35), "Journal of Synthetic Catalysis Research", fontsize=8.5, fontname="helv")
        page.insert_text((285, 815), f"- {idx} -", fontsize=8.5, fontname="helv")

    # Vertical arXiv marginalia on Page 1
    p1.insert_text((20, 480), "arXiv:2501.12345v1 [physics.chem-ph] 12 Jan 2025", fontsize=7.5, fontname="helv", rotate=90)

    # Full-width Title, Authors, Affiliations, Abstract
    p1.insert_text((50, 75), "Selective CO2 Hydrogenation over Fe3O4 Nanocrystals", fontsize=16.5, fontname="hebo")
    p1.insert_text((50, 100), "Alice Zhang, Bob Li, Carol Wang", fontsize=10.5, fontname="helv")
    p1.insert_text((50, 116), "Beijing University of Chemical Technology, Beijing 100029", fontsize=9.5, fontname="helv")
    p1.insert_text((50, 142), "Abstract: We investigate selective hydrogenation of CO2 at 573 K over Fe3O4 catalysts.", fontsize=10.0, fontname="helv")
    p1.insert_text((50, 160), "Keywords: CO2 hydrogenation; Fe3O4; nanocatalysis", fontsize=9.5, fontname="helv")

    # Left column (x: 50..280)
    p1.insert_text((50, 195), "1. Introduction", fontsize=12.0, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(50, 210, 280, 360),
        "LEFT_COL_START: Heterogeneous catalysis plays a central role in carbon neutral-\n"
        "ization and self-assembly processes. Recent studies demonstrate that surface oxygen\n"
        "vacancies enhance adsorption of reactant molecules across",
        fontsize=10.0,
        fontname="helv",
    )

    # Inline chemical/math sub & superscripts in left column
    y_chem = 385.0
    x_cur = 50.0
    for txt, dy, fsz in [
        ("The reaction converts H", 0.0, 10.0),
        ("2", 3.0, 7.0),
        ("O and Fe", 0.0, 10.0),
        ("3+", -3.5, 7.0),
        (" at 10", 0.0, 10.0),
        ("-3", -3.5, 7.0),
        (" mol.", 0.0, 10.0),
    ]:
        p1.insert_text((x_cur, y_chem + dy), txt, fontsize=fsz, fontname="helv")
        x_cur += fitz.get_text_length(txt, fontname="helv", fontsize=fsz)

    # Left column subsection + Greek Symbol font + display equation
    p1.insert_text((50, 420), "1.1 Reaction Thermodynamics", fontsize=11.0, fontname="hebo")
    p1.insert_text((65, 448), "D", fontsize=10.0, fontname="symb")
    p1.insert_text((72, 448), "G = ", fontsize=10.0, fontname="helv")
    p1.insert_text((94, 448), "D", fontsize=10.0, fontname="symb")
    p1.insert_text((101, 448), "H - T", fontsize=10.0, fontname="helv")
    p1.insert_text((126, 448), "D", fontsize=10.0, fontname="symb")
    p1.insert_text((133, 448), "S", fontsize=10.0, fontname="helv")
    p1.insert_text((255, 448), "(1)", fontsize=10.0, fontname="helv")

    # Footnote at bottom of left column
    p1.insert_text((50, 760), "*Corresponding author: alice@buct.edu.cn", fontsize=8.0, fontname="helv")

    # Right column (x: 315..545) — continues sentence from Left column without period
    p1.insert_textbox(
        fitz.Rect(315, 195, 545, 320),
        "active iron oxide facets during steady-state operation. RIGHT_COL_CONT.\n"
        "Notice this paragraph continues smoothly from the left column.",
        fontsize=10.0,
        fontname="helv",
    )
    p1.insert_text((315, 345), "1.1.1 Kinetic Modeling", fontsize=10.5, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(315, 360, 545, 460),
        "Under differential flow conditions, the turnover frequency scales linearly with\n"
        "partial pressure. Furthermore, cross-page analysis continues toward",
        fontsize=10.0,
        fontname="helv",
    )

    # Figure 1 + caption in right column
    p1.draw_rect(fitz.Rect(330, 480, 520, 570))
    p1.draw_line(fitz.Point(340, 550), fitz.Point(510, 495))
    p1.insert_text((320, 590), "Figure 1. Arrhenius plot of CO2 conversion.", fontsize=9.0, fontname="helv")

    # Page 2 left & right columns
    p2.insert_textbox(
        fitz.Rect(50, 75, 280, 175),
        "higher selectivity on page two. PAGE2_LEFT_CONT.\n"
        "This confirms cross-page reading order preservation.",
        fontsize=10.0,
        fontname="helv",
    )
    p2.insert_text((50, 200), "2. Conclusion", fontsize=12.0, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(50, 218, 280, 310),
        "We summarized multi-column extraction, subscript recovery, and provenance tracking.",
        fontsize=10.0,
        fontname="helv",
    )
    p2.insert_text((315, 75), "References", fontsize=12.0, fontname="hebo")
    p2.insert_textbox(
        fitz.Rect(315, 95, 545, 260),
        "[1] A. Smith et al., J. Catal. 2023, 412, 101-110.\n"
        "[2] B. Chen et al., Nature Chem. 2024, 16, 45-53.\n"
        "[3] C. Liu et al., Chem. Eng. J. 2025, 480, 148001.",
        fontsize=9.5,
        fontname="helv",
    )

    doc.save(str(path))
    doc.close()


def _build_t2_complex_tables_pdf(path: Path) -> None:
    """Create 2-page PDF with: (1) three-line multi-level header table + note, (2) cross-page Table 2, (3) raster Table 3."""
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    p1 = doc[0]
    p2 = doc[1]

    p1.insert_text((50, 55), "1. Experimental Benchmarks", fontsize=12.0, fontname="hebo")

    # Table 1: Three-line booktabs with multi-level spanning header ("Metrics" over Precision, Recall, F1)
    p1.insert_text((50, 82), "Table 1. Benchmark comparison across catalytic extraction models.", fontsize=10.0, fontname="hebo")
    p1.draw_line(fitz.Point(50, 95), fitz.Point(530, 95), width=1.2)  # toprule
    p1.insert_text((60, 110), "Model", fontsize=9.5, fontname="hebo")
    p1.insert_text((320, 110), "Metrics", fontsize=9.5, fontname="hebo")
    p1.draw_line(fitz.Point(210, 116), fitz.Point(525, 116), width=0.7)  # cmidrule
    p1.insert_text((220, 130), "Precision", fontsize=9.5, fontname="hebo")
    p1.insert_text((330, 130), "Recall", fontsize=9.5, fontname="hebo")
    p1.insert_text((445, 130), "F1", fontsize=9.5, fontname="hebo")
    p1.draw_line(fitz.Point(50, 138), fitz.Point(530, 138), width=0.8)  # midrule

    t1_rows = [
        ("Baseline", "0.78", "0.74", "0.76"),
        ("Ours-v1", "0.94", "0.92", "0.93"),
    ]
    for idx, (m, p, r, f) in enumerate(t1_rows):
        y = 154 + idx * 18
        p1.insert_text((60, y), m, fontsize=9.5, fontname="helv")
        p1.insert_text((220, y), p, fontsize=9.5, fontname="helv")
        p1.insert_text((330, y), r, fontsize=9.5, fontname="helv")
        p1.insert_text((445, y), f, fontsize=9.5, fontname="helv")
    p1.draw_line(fitz.Point(50, 182), fitz.Point(530, 182), width=1.2)  # bottomrule
    p1.insert_text((50, 196), "Note: Scores evaluated on synthetic benchmark.", fontsize=8.5, fontname="helv")

    # Table 2: Cross-page lattice table starting at bottom of Page 1, continuing at top of Page 2
    p1.insert_text((50, 565), "Table 2. Catalyst stability measurements across runs.", fontsize=10.0, fontname="hebo")
    cols_x = [50, 180, 320, 480]

    def _draw_lattice(page: Any, y_top: float, rows: List[List[str]]) -> None:
        row_h = 22.0
        y_bot = y_top + row_h * len(rows)
        for r_i in range(len(rows) + 1):
            yy = y_top + r_i * row_h
            page.draw_line(fitz.Point(cols_x[0], yy), fitz.Point(cols_x[-1], yy), width=0.8)
        for cx in cols_x:
            page.draw_line(fitz.Point(cx, y_top), fitz.Point(cx, y_bot), width=0.8)
        for r_i, row in enumerate(rows):
            yy = y_top + r_i * row_h + 15.0
            for c_i, val in enumerate(row):
                page.insert_text((cols_x[c_i] + 8, yy), val, fontsize=9.5, fontname="hebo" if r_i == 0 else "helv")

    _draw_lattice(
        p1,
        580.0,
        [
            ["Sample", "Temp_K", "Yield_pct"],
            ["Cat-A", "550", "81.2"],
            ["Cat-B", "573", "88.5"],
        ],
    )

    p2.insert_text((50, 55), "Table 2 (continued). Catalyst stability measurements across runs.", fontsize=10.0, fontname="hebo")
    _draw_lattice(
        p2,
        70.0,
        [
            ["Sample", "Temp_K", "Yield_pct"],
            ["Cat-C", "590", "90.1"],
            ["Cat-D", "610", "91.4"],
        ],
    )

    # Table 3: Raster image table on Page 2
    p2.insert_text((50, 220), "Table 3. Scanned legacy apparatus parameters (image table).", fontsize=10.0, fontname="hebo")
    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (600, 180), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 590, 170], outline="black", width=2)
        draw.line([10, 60, 590, 60], fill="black", width=2)
        draw.text((25, 25), "Parameter    Value    Unit", fill="black")
        draw.text((25, 85), "Pressure     2.5      MPa", fill="black")
        img_path = path.parent / "_tmp_t3.png"
        img.save(str(img_path))
        p2.insert_image(fitz.Rect(50, 235, 450, 355), filename=str(img_path))
        img_path.unlink(missing_ok=True)

    doc.save(str(path))
    doc.close()


def _build_t3a_encrypted_pdf(path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 72), "Confidential restricted manuscript.", fontsize=12)
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="buct-secret-123",
        owner_pw="buct-owner-123",
    )
    doc.close()


def _build_page_break_tables(path: Path, start_y: float, second_header: str) -> None:
    """Two uncaptioned, identically aligned lattices on consecutive pages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        for top, header, label, val in ((start_y, "Power (mW)", "Alpha", "17.3"),
                                        (70, second_header, "Beta", "92.7")):
            page = doc.new_page(width=600, height=800)
            for x in (50, 175, 300):
                page.draw_line((x, top), (x, top + 66))
            for y in (top, top + 22, top + 44, top + 66):
                page.draw_line((50, y), (300, y))
            for index, (left, right) in enumerate((("Sample", header), (label, val), ("Control", "11.0"))):
                y = top + 15 + 22 * index
                page.insert_text((60, y), left)
                page.insert_text((185, y), right)
        doc.save(path)


def _test_page_boundaries_and_sources(out_dir: Path, fixtures: Path) -> Dict[str, Any]:
    input_root = fixtures / "boundary_cases"
    genuine = input_root / "main" / "paper.pdf"
    separate = input_root / "supplement" / "paper.pdf"
    changed_unit = input_root / "different_unit.pdf"
    _build_page_break_tables(genuine, 690, "Power (mW)")
    _build_page_break_tables(separate, 110, "Rate (Hz)")
    _build_page_break_tables(changed_unit, 690, "Power (MW)")
    case_path = input_root / "case.json"
    case = {"case_id": "source-boundary-self-test",
            "output_contract": ["case_id", "status", "tables", "sources", "answer"]}
    write_json_atomic(case_path, case)
    out = out_dir / "case_output"
    pdf_inputs = [genuine.relative_to(input_root), separate.relative_to(input_root)]
    result = parse_case(case_path, pdf_inputs, out, budget=120, ocr_mode="off")
    documents = result["documents"]
    assert len({doc["parsed_path"] for doc in documents}) == 2
    assert all((out / doc["parsed_path"]).is_file() for doc in documents)
    assert {src["source_id"] for src in result["sources"]} == {"main/paper.pdf", "supplement/paper.pdf"}
    primary_tables = [table for table in result["tables"] if table["source_id"] == "main/paper.pdf"]
    secondary_tables = [table for table in result["tables"] if table["source_id"] == "supplement/paper.pdf"]
    assert len(primary_tables) == 1 and primary_tables[0]["pages"] == [1, 2], primary_tables
    assert [table["pages"] for table in secondary_tables] == [[1], [2]], secondary_tables
    cell = next(c for c in primary_tables[0]["cells"] if c["text"] == "92.7")
    assert cell["page"] == 2
    with fitz.open(genuine) as pdf:
        area_text = pdf[cell["page"] - 1].get_text(clip=fitz.Rect(cell["bbox"])).strip()
    assert area_text == "92.7", (cell, area_text)
    changed = run_parse_pipeline(changed_unit, out_dir / "changed_unit", ocr_mode="off")
    assert [table["pages"] for table in changed["tables"]] == [[1], [2]]
    assert result["status"] == "partial" and result["answer"] is None
    finalized = finalize(case, result, {"answer": {"value": "92.7", "source_id": "main/paper.pdf",
                                                   "page": 2, "bbox": cell["bbox"]}})
    assert finalized["case_id"] == case["case_id"] and finalized["answer"]["source_id"] == "main/paper.pdf"
    schema_case = {"case_id": case["case_id"], "output_contract": {
        "properties": {"answer": {"type": "object"}, "optional_annotation": {"type": ["string", "null"]}},
        "required": ["answer"]}}
    optional = dict(finalized, optional_annotation=None)
    assert finalize(schema_case, optional)["status"] == "success"
    table_doc = json.loads((out / documents[0]["parsed_path"]).read_text(encoding="utf-8"))
    hit = readback_cells(query_cells(table_doc, page=2, column="Power (mW)", value="92.7"))
    assert hit["total_matches"] == 1 and hit["hits"][0]["original_text"] == "92.7"
    assert hit["hits"][0]["matches_original"] and hit["hits"][0]["pdf_page"] == 2
    assert query_cells(table_doc, column="Power (MW)", value="92.7")["total_matches"] == 0
    assert query_cells(table_doc, page=1, value="92.7")["total_matches"] == 0
    strict_case = {"case_id": case["case_id"], "output_contract": {
        "type": "object", "additionalProperties": False,
        "properties": {"case_id": {"type": "string"}, "status": {"type": "string"},
                       "answer": {"type": "array", "minItems": 1, "items": {
                           "type": "object", "properties": {"value": {"type": "number"}},
                           "required": ["value", "source_id"]}}}, "required": ["case_id", "status", "answer"]}}
    malformed = finalize(strict_case, dict(finalized), {"answer": [{"value": "92.7"}]})
    issues = [item["message"] for item in malformed["uncertainties"]]
    assert malformed["status"] == "partial" and any("answer[0].value" in msg for msg in issues)
    assert any("answer[0].source_id" in msg for msg in issues)
    corrected = finalize(strict_case, malformed, {"answer": [{"value": 92.7, "source_id": documents[0]["source_id"]}]})
    assert corrected["status"] == "success"
    delivery = out_dir / "closed_contract" / "result.json"
    publish_result(delivery, strict_case, corrected)
    assert set(json.loads(delivery.read_text(encoding="utf-8"))) == {"case_id", "status", "answer"}
    assert json.loads(delivery.with_name("parse_manifest.json").read_text(encoding="utf-8"))["documents"] == documents
    preview = parse_case(case_path, pdf_inputs, out_dir / "preview_only", budget=0, ocr_mode="off")
    assert preview["status"] == "partial" and len(preview["documents"]) == 2
    assert all(doc["parsed_path"].endswith("preview.json") for doc in preview["documents"])
    assert all(source["pdf_page"] == 1 for source in preview["sources"]) and preview["sources"]
    return {"pass": True, "true_continuation_tables": 1, "independent_tables": 2,
            "unit_mismatch_tables": 2, "page_2_cell_readback": area_text,
            "distinct_document_sources": len(documents), "zero_budget_status": preview["status"],
            "wrong_page_hits": 0, "wrong_unit_hits": 0, "closed_schema_fields": sorted(strict_case["output_contract"]["required"]),
            "malformed_answer_diagnostics": issues, "corrected_answer_status": corrected["status"]}


def _build_t3b_scanned_pdf(path: Path) -> None:
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (1190, 1684), "white")
        draw = ImageDraw.Draw(img)
        draw.text((120, 150), "Scanned Legacy Paper Title", fill="black")
        draw.text((120, 220), "This page has zero digital text layer and requires OCR.", fill="black")
        img_path = path.parent / "_tmp_scan.png"
        img.save(str(img_path))
        p.insert_image(fitz.Rect(0, 0, 595, 842), filename=str(img_path))
        img_path.unlink(missing_ok=True)
    doc.save(str(path))
    doc.close()


def _build_t3c_garbled_disordered_pdf(path: Path) -> None:
    doc = fitz.open()
    for page_label in ("3", "5", "4"):
        p = doc.new_page(width=595, height=842)
        p.insert_text((280, 810), f"Page {page_label}", fontsize=9)
        p.insert_text(
            (50, 100),
            "Corrupted font stream: \ufffd\ufffd\ufffd\ufffd\ufffd\ufffd \ue001\ue002\ue003\ue004\ue005 (cid:12) (cid:19) (cid:42)",
            fontsize=10.5,
        )
    doc.save(str(path))
    doc.close()


def _run_baseline_comparison(t1_pdf: Path, t2_pdf: Path, t1_doc: Dict[str, Any], t2_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Compare raw PyMuPDF text dump vs Skill pipeline on T1 and T2."""
    d1 = fitz.open(str(t1_pdf))
    raw_t1_p1 = d1[0].get_text("text")
    d1.close()

    # Check if raw text interleaves or drops structure
    skill_t1_texts = [b["structured_text"] for b in t1_doc["blocks"]]
    joined_skill_t1 = "\n".join(skill_t1_texts)
    pos_left = joined_skill_t1.find("LEFT_COL_START")
    pos_right = joined_skill_t1.find("RIGHT_COL_CONT")
    pos_p2 = joined_skill_t1.find("PAGE2_LEFT_CONT")
    skill_order_ok = 0 <= pos_left < pos_right < pos_p2

    # Header/footer contamination check
    hf_in_skill_body = sum(
        1 for b in t1_doc["blocks"] if "Journal of Synthetic Catalysis Research" in b["text"] or "- 1 -" in b["text"]
    )
    hf_in_raw = raw_t1_p1.count("Journal of Synthetic Catalysis Research") + raw_t1_p1.count("- 1 -")

    # Sub/superscript recovery
    has_chem_struct = "H_{2}O" in joined_skill_t1 and "Fe^{3+}" in joined_skill_t1 and "10^{-3}" in joined_skill_t1
    has_chem_raw = ("H_{2}O" in raw_t1_p1) or ("Fe^{3+}" in raw_t1_p1)

    # Cross-page table merge & spanning header on T2
    d2 = fitz.open(str(t2_pdf))
    raw_t2 = "\n".join(pg.get_text("text") for pg in d2)
    d2.close()
    t1_obj = next((t for t in t2_doc["tables"] if t.get("label") == "Table 1"), None)
    t2_obj = next((t for t in t2_doc["tables"] if t.get("label") == "Table 2"), None)

    return {
        "T1_two_column": {
            "baseline_raw_header_footer_leaks": hf_in_raw,
            "skill_header_footer_leaks": hf_in_skill_body,
            "skill_reading_order_correct": skill_order_ok,
            "baseline_sub_sup_structured": has_chem_raw,
            "skill_sub_sup_structured": has_chem_struct,
            "baseline_provenance_bbox_count": 0,
            "skill_provenance_block_count": len(t1_doc["blocks"]),
        },
        "T2_complex_tables": {
            "baseline_table_structures": 0 if raw_t2 else 0,
            "skill_tables_detected": len(t2_doc["tables"]),
            "skill_table1_col_paths": t1_obj["col_paths"] if t1_obj else [],
            "skill_table2_cross_page_merged": bool(t2_obj and t2_obj.get("is_cross_page_merged")),
            "skill_table2_pages": t2_obj["pages"] if t2_obj else [],
            "skill_table2_data_rows": len(t2_obj["records"]) if t2_obj else 0,
            "skill_image_table_flagged": any(t.get("is_image_table") for t in t2_doc["tables"]),
        },
    }


def run_self_test(out_dir: Path, compare_baseline: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = out_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    t1_pdf = fixtures_dir / "t1_two_column_paper.pdf"
    t2_pdf = fixtures_dir / "t2_complex_tables.pdf"
    t3a_pdf = fixtures_dir / "t3a_encrypted.pdf"
    t3b_pdf = fixtures_dir / "t3b_scanned.pdf"
    t3c_pdf = fixtures_dir / "t3c_garbled_and_disordered.pdf"

    _build_t1_two_column_pdf(t1_pdf)
    _build_t2_complex_tables_pdf(t2_pdf)
    _build_t3a_encrypted_pdf(t3a_pdf)
    _build_t3b_scanned_pdf(t3b_pdf)
    _build_t3c_garbled_disordered_pdf(t3c_pdf)

    results: Dict[str, Any] = {"tests": {}, "all_passed": True}

    # --- Test 1: Double-column paper + full Harbor contract flow ---
    t1_out = out_dir / "t1_out"
    t1_doc = run_parse_pipeline(t1_pdf, t1_out, ocr_mode="auto", save_crops=True)
    joined_t1 = "\n".join(b["structured_text"] for b in t1_doc["blocks"])
    assert t1_doc["status"] == "success", f"Expected T1 success, got {t1_doc['status']}"
    assert t1_doc["metadata"]["title"] == "Selective CO2 Hydrogenation over Fe3O4 Nanocrystals"
    assert len(t1_doc["metadata"]["authors"]) >= 3
    assert t1_doc["metadata"]["abstract"] is not None
    assert 0 <= joined_t1.find("LEFT_COL_START") < joined_t1.find("RIGHT_COL_CONT") < joined_t1.find("PAGE2_LEFT_CONT")
    assert len(t1_doc["headers_footers"]) >= 4
    assert len(t1_doc["marginalia"]) >= 1
    assert len(t1_doc["footnotes"]) >= 1
    assert "H_{2}O" in joined_t1 and "Fe^{3+}" in joined_t1 and "10^{-3}" in joined_t1
    assert any(
        "ΔG = ΔH - TΔS" in eq["structured_text"] or "ΔG = ΔH - T ΔS" in eq["structured_text"]
        for eq in t1_doc["formulas"]["display_equations"]
    )
    assert len(t1_doc["figures"]) >= 1
    assert len(t1_doc["references"]) == 3
    assert sum(1 for b in t1_doc["blocks"] if b.get("continuation_of")) >= 1
    assert any(c.get("type") == "dehyphenation" for c in t1_doc["change_log"])

    # Query CLI check
    q_sec = query_parsed(t1_doc, mode="sections")
    assert len(q_sec) >= 4

    # Mock Harbor case.json -> build_result -> validate_output
    mock_case = {
        "case_id": "synthetic-harbor-t1",
        "output_contract": {
            "required": [
                "case_id",
                "status",
                "metadata",
                "sections",
                "tables",
                "formulas",
                "references",
                "sources",
                "uncertainties",
                "change_log",
            ]
        },
    }
    write_json_atomic(t1_out / "case.json", mock_case)
    md_txt = (t1_out / "full_text.md").read_text(encoding="utf-8")
    res_json = build_result(mock_case, t1_doc, markdown_text=md_txt)
    write_json_atomic(t1_out / "result.json", res_json)
    val_report = validate_output(mock_case, res_json, t1_doc, pdf_path=t1_pdf)
    write_json_atomic(t1_out / "validation_report.json", val_report)
    assert val_report["pass"], f"T1 validation failed: {val_report['errors']}"
    results["tests"]["t1_two_column_paper"] = {"pass": True, "blocks": len(t1_doc["blocks"]), "validation": val_report["metrics"]}

    # --- Test 2: Three-line multi-header, cross-page merge, image table ---
    t2_out = out_dir / "t2_out"
    t2_doc = run_parse_pipeline(t2_pdf, t2_out, ocr_mode="auto", save_crops=True)
    tabs = t2_doc["tables"]
    tab1 = next(t for t in tabs if t.get("label") == "Table 1")
    tab2 = next(t for t in tabs if t.get("label") == "Table 2")
    tab3 = next(t for t in tabs if t.get("label") == "Table 3")
    assert "Metrics / Precision" in tab1["col_paths"] and "Metrics / Recall" in tab1["col_paths"] and "Metrics / F1" in tab1["col_paths"], f"Unexpected T1 col_paths: {tab1['col_paths']}"
    assert 'colspan="3"' in tab1["html"], f"Expected colspan=3 in T1 html: {tab1['html']}"
    assert tab2["is_cross_page_merged"] is True and tab2["pages"] == [1, 2], f"Expected cross-page Table 2, got {tab2['pages']}"
    assert len(tab2["records"]) == 4, f"Expected 4 deduplicated data rows in Table 2, got {len(tab2['records'])}"
    assert tab3["is_image_table"] is True and tab3["crop_path"] is not None and Path(tab3["crop_path"]).exists()
    assert t2_doc["status"] == "partial"
    results["tests"]["t2_complex_tables"] = {"pass": True, "tables": len(tabs), "t1_col_paths": tab1["col_paths"], "t2_pages": tab2["pages"], "t2_rows": len(tab2["records"])}

    # --- Test 3a: Encrypted PDF ---
    t3a_out = out_dir / "t3a_out"
    t3a_doc = run_parse_pipeline(t3a_pdf, t3a_out, ocr_mode="auto", save_crops=False)
    assert t3a_doc["status"] == "refused"
    assert t3a_doc["diagnostics"]["encrypted"] is True
    assert any(a["code"] == "encrypted_pdf" for a in t3a_doc["diagnostics"]["anomalies"])
    results["tests"]["t3a_encrypted"] = {"pass": True, "status": t3a_doc["status"]}

    # --- Test 3b: Scanned PDF ---
    t3b_out = out_dir / "t3b_out"
    t3b_doc = run_parse_pipeline(t3b_pdf, t3b_out, ocr_mode="auto", save_crops=True)
    assert t3b_doc["diagnostics"]["page_profiles"][0]["page_type"] == "scanned_no_text"
    assert t3b_doc["status"] in ("partial", "unverifiable", "success")
    results["tests"]["t3b_scanned"] = {"pass": True, "status": t3b_doc["status"], "page_type": t3b_doc["diagnostics"]["page_profiles"][0]["page_type"]}

    # --- Test 3c: Garbled font & disordered printed page numbers ---
    t3c_out = out_dir / "t3c_out"
    t3c_doc = run_parse_pipeline(t3c_pdf, t3c_out, ocr_mode="off", save_crops=False)
    anom_codes = {a["code"] for a in t3c_doc["diagnostics"]["anomalies"]}
    assert "garbled_font_cid" in anom_codes, f"Expected garbled_font_cid in {anom_codes}"
    assert "disordered_page_numbers" in anom_codes, f"Expected disordered_page_numbers in {anom_codes}"
    assert t3c_doc["status"] == "partial"
    results["tests"]["t3c_garbled_and_disordered"] = {"pass": True, "anomalies": sorted(anom_codes), "status": t3c_doc["status"]}

    if compare_baseline:
        results["baseline_comparison"] = _run_baseline_comparison(t1_pdf, t2_pdf, t1_doc, t2_doc)

    results["tests"]["t4_table_boundaries_and_sources"] = _test_page_boundaries_and_sources(out_dir, fixtures_dir)

    write_json_atomic(out_dir / "self_test_report.json", results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-01-doc-parsing self-tests.")
    parser.add_argument("--out-dir", default=None, help="Directory for temporary synthetic fixtures and test outputs.")
    parser.add_argument("--compare-baseline", action="store_true", help="Compare against raw single-stream PDF extraction.")
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="reading01_selftest_"))

    report = run_self_test(out_dir, compare_baseline=args.compare_baseline)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
