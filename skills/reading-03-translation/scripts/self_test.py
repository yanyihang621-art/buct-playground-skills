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

from apply_translation import apply_translations
from segment_and_protect import segment_document, select_scope
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


# Reference translations for the local synthetic regression fixtures. Match by source
# content so accidentally omitted, split or newly introduced source text fails the test.
FIXTURE_ZH = {
    "High Catalytic": "Cu-ZnO-Al2O3 催化 CO2 加氢的高催化选择性",
    "Abstract": "摘要",
    "Herein,": "本研究中，Cu-ZnO-Al2O3 催化剂在 __NUM_002__ 和 __NUM_003__ 下实现了 __NUM_001__ 的催化选择性 __CIT_001__。在高空速下，转换频率 (TOF) 达 __NUM_004__。主要活性位点上未观察到一氧化碳中毒。",
    "1. Introduction": "__NUM_001__. 引言",
    "Improving catalytic": "提高催化选择性需要稳定活性位点以抵抗烧结 __CIT_001__。然而，如 __REF_001__ 所示，反应速率遵循 __EQ_001__。",
    "2. Results": "__NUM_001__. 结果与讨论",
    "Moreover,": "此外，从 __NUM_001__ 起提高空速并未超过传质极限 __CIT_001__。转换频率 (TOF) 在 __NUM_002__ 内保持稳定。",
    "Figure 1.": "__REF_001__. __NUM_001__ 下的催化选择性与活性位点稳定性。",
    "3. Conclusions": "__NUM_001__. 结论",
    "Therefore,": "因此，体系保持了高催化选择性，且无次生副反应。",
    "Operando Characterization": "金属有机框架 (MOF) 催化剂的工况原位表征",
    "Metal-organic framework": "金属有机框架 (MOF) 材料的活性位点几何构型可调 __CIT_001__。密度泛函理论 (DFT) 计算预测，在 __NUM_002__ 下吸附能为 __NUM_001__。",
    "2. Experimental": "__NUM_001__. 实验部分",
    "In situ Fourier": "在 __NUM_001__ 和 __NUM_002__ 下记录了原位傅里叶变换红外光谱 (FTIR) 和 X 射线光电子能谱 (XPS)。在 __NUM_003__ 内未检测到降解。",
    "The synergistic effect": "金属有机框架 (MOF) 内的协同效应使法拉第效率升至 __NUM_001__。",
    "Catalyst Screening": "结合结构化表格和栅格光谱的催化剂筛选",
    "Table 1.": "__REF_001__. 催化选择性与转换频率 (TOF) 比较。",
    "As summarized": "如 __REF_001__ 所示，在 __NUM_001__ 下 Cu-ZnO-Al2O3 的性能优于 Cu-Al2O3。",
    "Catalyst": "催化剂", "Temperature": "温度", "Selectivity": "催化选择性", "TOF": "转换频率 (TOF)",
}


def translated_fixture(pdf: Path, out: Path, case: Path | None = None) -> Dict[str, Any]:
    checkpoint = run_translation_pipeline(pdf, out, case_path=case)
    translations = {}
    for seg in checkpoint["segments"]["segments"]:
        for src_prefix, zh in FIXTURE_ZH.items():
            if seg["source_text"].startswith(src_prefix):
                translations[seg["segment_id"]] = {"target_text": zh}
                break
    path = out / "fixture_translations.json"
    write_json_atomic(path, translations)
    return run_translation_pipeline(pdf, out, case_path=case, translations_json=path, result_path=out / "result.json")


def check_semantic_faults() -> Dict[str, bool]:
    original = "Rainfall reshapes coastal sediment."
    contrast = "Rainfall did not increase runoff, but irrigation increased it."
    sources = [original, contrast, "The change may reach -3.2 mm in 12 samples [2].", "$x=1$", "The sensor was sampled at 12 Hz."]
    doc = {"blocks": [{"block_id": f"b{i}", "page": 1, "role": "body", "text": src}
                      for i, src in enumerate(sources, 1)]}
    segments = segment_document(doc)
    for i, item in enumerate(segments["segments"], 1):
        item["source_locator"] = {"paragraph_id": f"p{i}", "source": "synthetic.json"}
    ids = [s["segment_id"] for s in segments["segments"]]
    glossary = {"entries": []}
    good = dict(zip(ids, ["降雨重塑海岸沉积物。", "降雨未增加径流，但灌溉增加了径流。",
                          "__NUM_002__ 个样本中的变化可能达到 __NUM_001__ __CIT_001__。", "$x=1$", "传感器的采样频率为 __NUM_001__。"]))

    def check(translations):
        bi = apply_translations(segments, glossary, translations)
        return bi, verify_bilingual_translation(bi, segments, glossary)

    valid, qa = check(good)
    missing, missing_qa = check({})
    copied, copied_qa = check({**good, ids[0]: original})
    _, flipped = check({**good, ids[1]: "降雨增加了径流，但灌溉未增加径流。"})
    _, extra = check({**good, ids[2]: good[ids[2]] + "另有 55 个样本。"})
    _, wrong_unit = check({**good, ids[4]: "传感器的采样频率为 12 kHz。"})
    _, strong = check({**good, ids[2]: good[ids[2]].replace("可能", "必然")})
    scoped = select_scope(segments, {"translation_scope": {"paragraph_ids": ["p2"]}})
    absent = select_scope(segments, {"translation_scope": {"paragraph_ids": ["p2", "p99"]}})
    return {
        "passage_limit_uses_original_paragraph": [s["segment_id"] for s in scoped["segments"]] == [ids[1]],
        "missing_requested_paragraph_is_reported": absent["doc_status"] == "partial" and
            any(u.get("requested") == ["p99"] for u in absent["uncertainties"]),
        "genuine_translation_passes": valid["status"] == "completed" and qa["passed"],
        "empty_map_is_partial": missing["status"] == "partial" and not missing_qa["passed"],
        "verbatim_formula_is_retained": missing["segments"][3]["status"] == "verbatim",
        "copied_body_is_not_a_translation": copied["status"] == "partial" and not copied_qa["passed"],
        "negation_moved_to_wrong_clause_is_caught": not flipped["checks"]["negation_and_scope_preservation"],
        "frequency_unit_change_is_caught": not wrong_unit["checks"]["protected_token_fidelity"],
        "invented_quantity_is_caught": not extra["checks"]["numeric_fidelity"],
        "lost_modality_is_caught": not strong["checks"]["negation_and_scope_preservation"],
    }


def check_sentence_and_table_coverage(root: Path) -> dict:
    """One synthetic source, audited draft, three one-change faults, and a valid joined rendering."""
    inputs = root / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    paragraph = "The north transect shifted by 12 mm. The south transect shifted by 24 mm. Dark sand remained visible."
    source = f"## Results\n\n{paragraph}\n\n| Site | Observation |\n| --- | --- |\n| North shore | Fine sand only |\n| South shore | No visible change |\n"
    (inputs / "shoreline.md").write_text(source, encoding="utf-8")
    write_json_atomic(inputs / "case.json", {"case_id": "synthetic-shore", "target_file": "shoreline.md",
                                            "translation_scope": {"sections": ["Results"]}})
    prepared = run_translation_pipeline(inputs, root / "work", prepare_only=True)
    rows = json.loads(Path(prepared["segments_path"]).read_text(encoding="utf-8"))["segments"]
    targets = {"Results": "结果", "Table 1": "Table 1", "Site": "地点", "Observation": "观测结果",
               "North shore": "北岸", "Fine sand only": "仅有细沙", "South shore": "南岸", "No visible change": "未见明显变化"}
    clauses = ["北侧样线移动了 12 mm。", "南侧样线移动了 24 mm。", "深色沙仍清晰可见。"]
    targets[paragraph] = "".join(clauses)
    draft = {r["segment_id"]: {"target_text": targets[r["source_text"]]} for r in rows}
    ids = {r["source_text"]: r["segment_id"] for r in rows}

    def publish(name, replacement=None, missing=None):
        authoring = dict(draft)
        if replacement:
            authoring[ids[paragraph]] = replacement
        if missing:
            authoring.pop(ids[missing])
        path = root / (name + ".json")
        write_json_atomic(path, authoring)
        return run_translation_pipeline(inputs, root / "work", root / name / "result.json",
                                        translations_json=path)["result"]

    reviewed = publish("reviewed")
    pairs = next(s["sentence_alignment"] for s in reviewed["segments"] if s["source_text"] == paragraph)
    omitted = publish("omitted", {"target_text": "".join(clauses[:2])})
    swapped = publish("swapped", {"target_text": "北侧样线移动了 24 mm。南侧样线移动了 12 mm。" + clauses[2]})
    gap = publish("table-gap", missing="No visible change")
    joined = publish("joined", {"sentence_targets": [c[:-1] + "，" for c in clauses[:2]] + [clauses[2]]})
    return {
        "reviewed_complete": reviewed["status"] == "completed" and reviewed["verification"]["passed"],
        "sentence_quotes_resolve": len(pairs) == 3 and all(paragraph[p["source_start"]:p["source_end"]] == p["source_text"] for p in pairs),
        "table_in_section_has_four_anchored_values": reviewed["tables_bilingual"][0]["rows_zh"] ==
            [["北岸", "仅有细沙"], ["南岸", "未见明显变化"]] and
            [(s["source_locator"]["row"], s["source_locator"]["column"]) for s in reviewed["segments"]
             if s["role"] == "table_cell"] == [(2, 1), (2, 2), (3, 1), (3, 2)],
        "omitted_sentence_detected": omitted["status"] == "partial" and "sentence_alignment" in omitted["verification"]["failed_checks"],
        "swapped_quantities_located": swapped["status"] == "partial" and
            [v.get("sentence_id") for v in swapped["qa_report"]["violations"] if v["check"] == "sentence_alignment"] ==
            [p["sentence_id"] for p in pairs[:2]],
        "untranslated_table_cell_located": gap["status"] == "partial" and
            any(u.get("segment_id") == ids["No visible change"] for u in gap["uncertainties"]),
        "natural_join_with_clause_map": joined["status"] == "completed" and joined["verification"]["passed"],
    }


def _compare_vs_baseline(t1_out: Dict[str, Any]) -> Dict[str, Any]:
    segments, glossary = t1_out["segments"], t1_out["glossary"]
    # Use exactly the same source segments and glossary, with an explicit source-copy control.
    direct_copy = {s["segment_id"]: s["source_text"] for s in segments["segments"]}
    control = apply_translations(segments, glossary, direct_copy)
    control_qa = verify_bilingual_translation(control, segments, glossary)
    reviewed = t1_out["qa_report"]
    return {"comparison": "Synthetic fixture, source copy control vs authored translation, identical inputs",
            "source_copy": control_qa["metrics"], "authored": reviewed["metrics"],
            "control_passed": control_qa["passed"], "authored_passed": reviewed["passed"],
            "skill_wins": not control_qa["passed"] and reviewed["passed"]}


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
    write_json_atomic(t1_case, {"case_id": "synthetic-catalysis", "input_files": [str(t1_pdf)], "custom_glossary": {"Cu-ZnO-Al2O3": "Cu-ZnO-Al2O3"}})

    o1 = translated_fixture(t1_pdf, out_root / "t1", t1_case)
    neg = _run_negative_fault_tests(o1["bilingual"], o1["segments"], o1["glossary"])
    t1_pass = o1["qa_report"]["passed"] and o1["result"]["status"] == "completed" and all(neg.values())

    o2 = translated_fixture(t2_pdf, out_root / "t2")
    t2_pages = {s["page"] for s in o2["bilingual"]["segments"]}
    t2_pass = o2["qa_report"]["passed"] and t2_pages == {1, 2, 3}

    o3 = translated_fixture(t3_pdf, out_root / "t3")
    t3_pass = o3["qa_report"]["passed"] and len(o3["bilingual"]["tables_bilingual"]) >= 1

    o4a = run_translation_pipeline(t4a_pdf, out_root / "t4a", result_path=out_root / "t4a" / "result.json")
    t4a_pass = (o4a["result"]["status"] == "refused") and o4a["qa_report"]["passed"]

    o4b = run_translation_pipeline(t4b_pdf, out_root / "t4b", result_path=out_root / "t4b" / "result.json")
    t4b_pass = (o4b["result"]["status"] == "partial") and o4b["qa_report"]["passed"] and len(o4b["result"]["uncertainties"]) > 0

    semantic = check_semantic_faults()
    coverage = check_sentence_and_table_coverage(out_root / "t5")
    summary: Dict[str, Any] = {
        "semantic_regressions": semantic,
        "sentence_and_table_regressions": coverage,
        "all_passed": bool(t1_pass and t2_pass and t3_pass and t4a_pass and t4b_pass and all(semantic.values()) and all(coverage.values())),
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
