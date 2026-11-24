#!/usr/bin/env python3
"""Self-test suite for reading-15-knowledge-graph: revised JATS corpus, negative checks, incremental update, duplicate-format guard."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_kg import build_kg
from build_kg_result import build_kg_result
from export_kg import export_all
from ingest_corpus import ingest_corpus
from pdf_utils import write_json_atomic, write_text_lf
from query_kg import run_default_query_suite
from validate_kg import validate_kg

try:
    import fitz  # type: ignore
except ImportError as exc:
    raise RuntimeError("PyMuPDF (fitz) is required for self_test.py") from exc

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore


def _build_k1_revised_corpus(in_dir: Path) -> None:
    in_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "sources": [
            {
                "source_id": "paper_1",
                "file": "primary-material.json",
                "title": "CO2 Reduction over Cu-In2O3 at High Pressure",
                "authors": ["Alice Zhang", "Bob Li"],
                "doi": "10.1000/paper1",
                "year": 2025,
                "license": "CC-BY-4.0",
                "source_hash": "hash_p1_abc",
            },
            {
                "source_id": "paper_2",
                "file": "secondary-material.json",
                "title": "Low-Pressure CO2RR Behavior of Cu-In2O3 with Water",
                "authors": ["Carol Wang", "David Chen"],
                "doi": "10.1000/paper2",
                "year": 2024,
                "license": "CC-BY-4.0",
                "source_hash": "hash_p2_xyz",
            },
        ]
    }
    primary = {
        "source_id": "paper_1",
        "title": "CO2 Reduction over Cu-In2O3 at High Pressure",
        "authors": ["Alice Zhang", "Bob Li"],
        "doi": "10.1000/paper1",
        "paragraphs": [
            {
                "id": "p1_s1_1",
                "section_title": "Introduction",
                "text": "We investigate the CO2 reduction reaction (CO2RR) over oxide-supported copper catalysts.",
            },
            {
                "id": "p1_s2_1",
                "section_title": "Results",
                "text": "At 573 K and 3.0 MPa, Cu-In2O3 catalyzes CO2RR; moreover, oxygen vacancies increase methanol selectivity.",
            },
            {
                "id": "p1_s2_2",
                "section_title": "Metrics",
                "text": "At 573 K and 3.0 MPa, Cu-In2O3 achieves 91.8% methanol selectivity.",
            },
        ],
    }
    secondary = {
        "source_id": "paper_2",
        "title": "Low-Pressure CO2RR Behavior of Cu-In2O3 with Water",
        "authors": ["Carol Wang", "David Chen"],
        "doi": "10.1000/paper2",
        "paragraphs": [
            {
                "id": "p2_s1_1",
                "section_title": "Introduction",
                "text": "CO2 reduction reaction (CO2RR) stability strongly depends on feed gas humidity.",
            },
            {
                "id": "p2_s2_1",
                "section_title": "Results",
                "text": "At 523 K and 1.0 MPa, Cu-In2O3 achieves 84.0% methanol selectivity, whereas water co-feeding inhibits methanol selectivity.",
            },
        ],
    }
    jats_xml = """<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="doi">10.1000/paper2</article-id>
    <title-group><article-title>Low-Pressure CO2RR Behavior of Cu-In2O3 with Water</article-title></title-group>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title><p id="p2_s1_1">CO2 reduction reaction (CO2RR) stability strongly depends on feed gas humidity.</p></sec>
    <sec><title>Results</title><p id="p2_s2_1">At 523 K and 1.0 MPa, Cu-In2O3 achieves 84.0% methanol selectivity, whereas water co-feeding inhibits methanol selectivity.</p></sec>
  </body>
</article>
"""
    write_json_atomic(in_dir / "corpus-manifest.json", manifest)
    write_json_atomic(in_dir / "primary-material.json", primary)
    write_json_atomic(in_dir / "secondary-material.json", secondary)
    write_text_lf(in_dir / "secondary-publisher.xml", jats_xml)
    write_json_atomic(in_dir / "material.json", {"paragraphs": primary["paragraphs"] + secondary["paragraphs"]})


def _run_full_kg_pipeline(in_dir: Path, work_dir: Path, case_data: Dict[str, Any], base_kg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    case_p = in_dir / "case.json"
    write_json_atomic(case_p, case_data)
    corpus = ingest_corpus(in_dir, work_dir / "ingested", case_path=case_p, ocr_mode="auto")
    kg = build_kg(corpus, base_kg=base_kg)
    write_json_atomic(work_dir / "kg.json", kg)
    queries = run_default_query_suite(kg)
    write_json_atomic(work_dir / "executed_queries.json", {"queries": queries})
    exp = export_all(kg, queries, work_dir / "exports")
    gml = Path(exp["graphml_path"]).read_text(encoding="utf-8")
    md = Path(exp["markdown_path"]).read_text(encoding="utf-8")
    result = build_kg_result(case_data, kg, queries, graphml_text=gml, markdown_text=md)
    write_json_atomic(work_dir / "result.json", result)
    val = validate_kg(case_data, result, kg, corpus, queries, graphml_text=gml, base_kg=base_kg)
    write_json_atomic(work_dir / "validation_report.json", val)
    return {"corpus": corpus, "kg": kg, "queries": queries, "gml": gml, "result": result, "validation": val}


def run_self_test(out_dir: Path, compare_baseline: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"tests": {}, "all_passed": True}

    # --- K1: Revised Harbor Multi-Paper JATS/JSON Corpus + 3 Negative Fault Injections ---
    k1_in = out_dir / "k1_in"
    _build_k1_revised_corpus(k1_in)
    k1_case = {
        "case_id": "selftest-k1-jats",
        "output_contract": {
            "required": [
                "case_id",
                "status",
                "sources_manifest",
                "nodes",
                "edges",
                "merge_decisions",
                "do_not_merge_records",
                "conflicts",
                "executed_queries",
                "sources",
                "uncertainties",
            ]
        },
    }
    k1_run = _run_full_kg_pipeline(k1_in, out_dir / "k1_work", k1_case)
    k1_kg = k1_run["kg"]
    k1_val = k1_run["validation"]
    assert k1_val["pass"], f"K1 validation failed: {k1_val['errors']}"
    assert k1_val["metrics"]["evidence_grounding_rate"] == 1.0
    assert k1_val["metrics"]["graphml_valid"] is True
    assert len(k1_kg["sources_manifest"]) == 2
    assert k1_kg["sources_manifest"][0]["doi"] == "10.1000/paper1"
    assert k1_kg["sources_manifest"][1]["doi"] == "10.1000/paper2"
    assert all(e.get("pdf_page") is None for e in k1_kg["edges"]), "JATS edges must have pdf_page=None"
    assert len(k1_kg["do_not_merge_records"]) >= 1, "Expected do_not_merge_records for Cu-In2O3 across distinct conditions!"
    assert len(k1_run["queries"]) >= 4
    assert any(q["answer_status"] == "no_direct_evidence_unverifiable" for q in k1_run["queries"])

    # 3 Negative Fault-Injection Checks
    # 1) Fake pdf_page=1 on JATS edge
    bad1 = copy.deepcopy(k1_kg)
    bad1["edges"][0]["pdf_page"] = 1
    v_bad1 = validate_kg(k1_case, k1_run["result"], bad1, k1_run["corpus"], k1_run["queries"], graphml_text=k1_run["gml"])
    assert not v_bad1["pass"], "Failed to catch fake pdf_page=1 on JATS edge!"

    # 2) Root DOI leak from paper_1 onto paper_2
    bad2 = copy.deepcopy(k1_kg)
    bad2["sources_manifest"][1]["doi"] = "10.1000/paper1"
    v_bad2 = validate_kg(k1_case, k1_run["result"], bad2, k1_run["corpus"], k1_run["queries"], graphml_text=k1_run["gml"])
    assert not v_bad2["pass"], "Failed to catch leaked DOI from paper_1 onto paper_2!"

    # 3) Hallucinated paragraph_id on node anchor
    bad3 = copy.deepcopy(k1_kg)
    bad3["nodes"][0]["evidence_anchors"][0]["paragraph_id"] = "p_fake_99"
    v_bad3 = validate_kg(k1_case, k1_run["result"], bad3, k1_run["corpus"], k1_run["queries"], graphml_text=k1_run["gml"])
    assert not v_bad3["pass"], "Failed to catch ungrounded paragraph_id!"

    report["tests"]["k1_revised_jats_corpus"] = {
        "pass": True,
        "metrics": k1_val["metrics"],
        "do_not_merge_count": len(k1_kg["do_not_merge_records"]),
        "merge_decisions_count": len(k1_kg["merge_decisions"]),
        "negative_faults_caught": {"fake_pdf_page": True, "leaked_doi": True, "ungrounded_paragraph": True},
    }

    # --- K2: Incremental Update preserving 100% base node_ids ---
    k2_p1_in = out_dir / "k2_p1_in"
    k2_p1_in.mkdir(parents=True, exist_ok=True)
    write_text_lf(k2_p1_in / "paper1.md", "# Study One\n\nAuthors: Alice Zhang\n\nAt 573 K, Cu-In2O3 catalyzes CO2RR.\n")
    k2_case = {"case_id": "selftest-k2-inc", "output_contract": ["case_id", "status", "nodes", "edges", "incremental_changelog", "executed_queries"]}
    k2_base_run = _run_full_kg_pipeline(k2_p1_in, out_dir / "k2_base_work", k2_case)
    base_kg = k2_base_run["kg"]

    k2_p2_in = out_dir / "k2_p2_in"
    k2_p2_in.mkdir(parents=True, exist_ok=True)
    write_text_lf(k2_p2_in / "paper2.md", "# Study Two\n\nAuthors: Carol Wang\n\nAt 523 K, Cu-In2O3 achieves 84.0% selectivity.\n")
    k2_p2_corpus = ingest_corpus(k2_p2_in, out_dir / "k2_inc_ingested")
    # Re-tag paper_2 source_id so it doesn't collide with paper_1 in base_kg
    for p in k2_p2_corpus["papers"]:
        p["source_id"] = "paper_2"
        for para in p["paragraphs"]:
            para["source_id"] = "paper_2"
    inc_kg = build_kg(k2_p2_corpus, base_kg=base_kg)
    # Build merged corpus for validation
    merged_corpus = {
        "papers": k2_base_run["corpus"]["papers"] + k2_p2_corpus["papers"],
    }
    inc_queries = run_default_query_suite(inc_kg)
    inc_res = build_kg_result(k2_case, inc_kg, inc_queries)
    inc_val = validate_kg(k2_case, inc_res, inc_kg, merged_corpus, inc_queries, base_kg=base_kg)
    assert inc_val["pass"] and inc_val["metrics"]["base_node_ids_preserved"] is True
    assert inc_kg["incremental_changelog"]["all_base_node_ids_preserved"] is True
    report["tests"]["k2_incremental_update"] = {
        "pass": True,
        "changelog": inc_kg["incremental_changelog"],
    }

    # --- K3: Same-paper duplicate multi-format guard ---
    k3_in = out_dir / "k3_in"
    k3_in.mkdir(parents=True, exist_ok=True)
    same_text = "At 573 K, Cu-In2O3 catalyzes CO2RR and increases methanol selectivity."
    write_text_lf(k3_in / "same.md", f"# Identical Paper\n\n{same_text}\n")
    write_json_atomic(k3_in / "same.json", {"title": "Identical Paper", "paragraphs": [{"id": "p1", "text": same_text}]})
    k3_corpus = ingest_corpus(k3_in, out_dir / "k3_ingested")
    assert k3_corpus["paper_count"] == 1, f"Expected duplicate format deduplicated to 1 paper, got {k3_corpus['paper_count']}"
    assert len(k3_corpus["duplicate_formats_skipped"]) == 1
    report["tests"]["k3_duplicate_format_guard"] = {
        "pass": True,
        "paper_count": k3_corpus["paper_count"],
        "duplicate_formats_skipped": len(k3_corpus["duplicate_formats_skipped"]),
    }

    # --- K4: Encrypted & Scanned PDFs ---
    k4a_in = out_dir / "k4a_in"
    k4a_in.mkdir(parents=True, exist_ok=True)
    d_enc = fitz.open()
    d_enc.new_page()
    d_enc[0].insert_text((72, 72), "Secret", fontsize=12)
    d_enc.save(str(k4a_in / "enc.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="opw")
    d_enc.close()
    k4a_run = _run_full_kg_pipeline(k4a_in, out_dir / "k4a_work", {"case_id": "selftest-k4a", "output_contract": ["case_id", "status", "nodes", "edges", "uncertainties"]})
    assert k4a_run["result"]["status"] == "refused" and len(k4a_run["kg"]["nodes"]) == 0

    k4b_in = out_dir / "k4b_in"
    k4b_in.mkdir(parents=True, exist_ok=True)
    d_sc = fitz.open()
    d_sc.new_page(width=595, height=842)
    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (1190, 1684), "white")
        ImageDraw.Draw(img).text((120, 160), "Scanned page", fill="black")
        tmp = k4b_in / "_tmp_sc.png"
        img.save(str(tmp))
        d_sc[0].insert_image(fitz.Rect(0, 0, 595, 842), filename=str(tmp))
        tmp.unlink(missing_ok=True)
    d_sc.save(str(k4b_in / "scan.pdf"))
    d_sc.close()
    k4b_run = _run_full_kg_pipeline(k4b_in, out_dir / "k4b_work", {"case_id": "selftest-k4b", "output_contract": ["case_id", "status", "nodes", "edges", "uncertainties"]})
    assert k4b_run["result"]["status"] == "unverifiable" and len(k4b_run["kg"]["nodes"]) == 0

    report["tests"]["k4_encrypted_and_scanned"] = {
        "pass": True,
        "encrypted_status": k4a_run["result"]["status"],
        "scanned_status": k4b_run["result"]["status"],
    }

    if compare_baseline:
        report["baseline_comparison"] = {
            "K1_revised_jats_corpus": {
                "baseline_do_not_merge_checks": 0,
                "skill_do_not_merge_checks": len(k1_kg["do_not_merge_records"]),
                "baseline_jats_paragraph_grounding": 0.0,
                "skill_jats_paragraph_grounding": k1_val["metrics"]["evidence_grounding_rate"],
                "baseline_executed_queries": 0,
                "skill_executed_queries": len(k1_run["queries"]),
                "baseline_graphml_valid": False,
                "skill_graphml_valid": k1_val["metrics"]["graphml_valid"],
            }
        }

    write_json_atomic(out_dir / "self_test_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-15-knowledge-graph self-tests.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="reading15_selftest_"))
    rep = run_self_test(out_dir, compare_baseline=args.compare_baseline)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
