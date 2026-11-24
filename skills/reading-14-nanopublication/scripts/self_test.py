#!/usr/bin/env python3
"""Self-test suite for reading-14-nanopublication: synthetic fixtures, 6 negative quality-gate tests, and baseline comparison."""

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

from build_nanopub_result import build_nanopub_result
from decompose_assertions import decompose_document, load_relation_vocabulary
from ingest_source import ingest_input
from pdf_utils import write_json_atomic, write_text_lf
from serialize_nanopubs import serialize_all
from validate_nanopubs import validate_nanopubs

try:
    import fitz  # type: ignore
except ImportError as exc:
    raise RuntimeError("PyMuPDF (fitz) is required for self_test.py") from exc

try:
    from PIL import Image, ImageDraw  # type: ignore
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore


def _build_n1_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    p1, p2 = doc[0], doc[1]

    p1.insert_text((50, 35), "ACS Catalysis 2025 | DOI: 10.1021/acscatal.2025.00123", fontsize=8.5, fontname="helv")
    p2.insert_text((50, 35), "ACS Catalysis 2025 | DOI: 10.1021/acscatal.2025.00123", fontsize=8.5, fontname="helv")

    p1.insert_text((50, 72), "Selective CO2 Hydrogenation over Cu-In2O3 Catalysts", fontsize=16.0, fontname="hebo")
    p1.insert_text((50, 96), "Alice Zhang, Bob Li", fontsize=10.5, fontname="helv")
    p1.insert_text((50, 112), "Beijing University of Chemical Technology", fontsize=9.5, fontname="helv")
    p1.insert_text((50, 140), "Abstract: We report selective methanol synthesis over Cu-In2O3.", fontsize=10.0, fontname="helv")

    p1.insert_text((50, 178), "1. Results and Discussion", fontsize=12.0, fontname="hebo")
    p1.insert_textbox(
        fitz.Rect(50, 195, 280, 380),
        "At 573 K and 3.0 MPa, Cu-In2O3 catalyzes CO2 hydrogenation to methanol; "
        "moreover, oxygen vacancies increase methanol selectivity, whereas water co-feeding inhibits catalytic activity.",
        fontsize=10.0,
        fontname="helv",
    )
    p1.insert_textbox(
        fitz.Rect(50, 395, 280, 490),
        "Surface hydroxyl accumulation may induce partial sintering after 200 h.",
        fontsize=10.0,
        fontname="helv",
    )
    p1.insert_textbox(
        fitz.Rect(315, 195, 545, 310),
        "Previous studies [1] reported that pure Cu exhibits low methanol selectivity.",
        fontsize=10.0,
        fontname="helv",
    )

    # Page 2 Table 1
    p2.insert_text((50, 70), "Table 1. Catalytic performance metrics at 573 K.", fontsize=10.0, fontname="hebo")
    p2.draw_line(fitz.Point(50, 82), fitz.Point(480, 82), width=1.2)
    p2.insert_text((60, 97), "Catalyst", fontsize=9.5, fontname="hebo")
    p2.insert_text((220, 97), "Selectivity", fontsize=9.5, fontname="hebo")
    p2.insert_text((360, 97), "Conversion", fontsize=9.5, fontname="hebo")
    p2.draw_line(fitz.Point(50, 105), fitz.Point(480, 105), width=0.8)
    p2.insert_text((60, 122), "Cu-In2O3", fontsize=9.5, fontname="helv")
    p2.insert_text((220, 122), "91.8%", fontsize=9.5, fontname="helv")
    p2.insert_text((360, 122), "24.1%", fontsize=9.5, fontname="helv")
    p2.draw_line(fitz.Point(50, 132), fitz.Point(480, 132), width=1.2)

    p2.insert_text((50, 175), "References", fontsize=12.0, fontname="hebo")
    p2.insert_text((50, 195), "[1] J. Smith et al., J. Catal. 2023, 415, 12-20.", fontsize=9.5, fontname="helv")

    doc.save(str(path))
    doc.close()


def _build_n2_md(path: Path) -> None:
    md = """# Pt Doping Effects in CO Oxidation

Authors: Carol Wang

## 1. Results
At 473 K, Pt doping increases CO yield.

## 2. Discussion
At 473 K, Pt doping increases CO yield.

Under sulfur-rich feed, Pt doping decreases CO yield.
"""
    write_text_lf(path, md)


def _build_n3a_encrypted(path: Path) -> None:
    doc = fitz.open()
    doc.new_page()
    doc[0].insert_text((72, 72), "Confidential.", fontsize=12)
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="opw")
    doc.close()


def _build_n3b_scanned(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    if Image is not None and ImageDraw is not None:
        img = Image.new("RGB", (1190, 1684), "white")
        ImageDraw.Draw(img).text((120, 160), "Scanned paper without text layer.", fill="black")
        tmp = path.parent / "_tmp_n3b.png"
        img.save(str(tmp))
        doc[0].insert_image(fitz.Rect(0, 0, 595, 842), filename=str(tmp))
        tmp.unlink(missing_ok=True)
    doc.save(str(path))
    doc.close()


def _run_case(in_dir: Path, work_dir: Path, case_data: Dict[str, Any]) -> Dict[str, Any]:
    vocab = load_relation_vocabulary()
    case_p = in_dir / "case.json"
    write_json_atomic(case_p, case_data)
    manifest = ingest_input(in_dir, work_dir / "ingested", case_path=case_p, ocr_mode="auto", save_crops=False)

    docs = [json.loads(Path(it["document_json"]).read_text(encoding="utf-8")) for it in manifest["papers"]]
    bundle = decompose_document(docs[0], vocab)
    write_json_atomic(work_dir / "nanopubs.json", bundle)
    ser = serialize_all(bundle, work_dir / "rdf")
    nq_txt = Path(ser["nquads_path"]).read_text(encoding="utf-8")
    trig_txt = Path(ser["trig_path"]).read_text(encoding="utf-8")
    jsonld_obj = json.loads(Path(ser["jsonld_path"]).read_text(encoding="utf-8"))
    md_txt = Path(ser["markdown_path"]).read_text(encoding="utf-8")

    result = build_nanopub_result(
        case_data,
        bundle,
        docs=docs,
        trig_text=trig_txt,
        jsonld_obj=jsonld_obj,
        nq_text=nq_txt,
        md_text=md_txt,
    )
    write_json_atomic(work_dir / "result.json", result)
    val = validate_nanopubs(case_data, result, bundle, docs, nquads_text=nq_txt)
    write_json_atomic(work_dir / "validation_report.json", val)
    return {"bundle": bundle, "docs": docs, "result": result, "validation": val, "nq_txt": nq_txt, "ser": ser}


def run_self_test(out_dir: Path, compare_baseline: bool = False) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"tests": {}, "all_passed": True}

    # --- N1: Compound PDF decomposition + 6 negative quality-gate fault injections ---
    n1_in = out_dir / "n1_in"
    n1_in.mkdir(parents=True, exist_ok=True)
    n1_pdf = n1_in / "n1_catalysis_nanopub.pdf"
    _build_n1_pdf(n1_pdf)
    n1_case = {
        "case_id": "selftest-n1",
        "input_files": ["n1_catalysis_nanopub.pdf"],
        "output_contract": {
            "required": [
                "case_id",
                "status",
                "nanopub_count",
                "nanopublications",
                "source_metadata",
                "deduplication_log",
                "conflicts",
                "sources",
                "uncertainties",
            ]
        },
    }
    n1_run = _run_case(n1_in, out_dir / "n1_work", n1_case)
    n1_b = n1_run["bundle"]
    n1_v = n1_run["validation"]
    assert n1_v["pass"], f"N1 validation failed: {n1_v['errors']}"
    assert n1_v["metrics"]["nquads_valid"] is True
    assert n1_v["metrics"]["anchor_grounding_rate"] == 1.0
    assert n1_b["source_metadata"]["doi"] == "10.1021/acscatal.2025.00123"
    assert n1_b["source_metadata"]["year"] == "2025"

    ev_types = {np["provenance"]["evidence_type"] for np in n1_b["nanopublications"]}
    preds = {np["assertion"]["predicate"] for np in n1_b["nanopublications"]}
    assert {"catalyzes", "increases", "inhibits", "achieves_metric"}.issubset(preds), f"Missing preds in {preds}"
    assert {"direct_experimental", "indirect_inference", "cited_prior_work", "direct_tabular_or_figure"}.issubset(ev_types), f"Missing ev_types in {ev_types}"

    # Verify leading condition propagation (573 K, 3.0 MPa propagated to oxygen vacancies & water co-feeding clauses)
    ox_np = next(np for np in n1_b["nanopublications"] if "oxygen vacancies" in np["assertion"]["subject"].lower())
    assert "573 K" in ox_np["assertion"]["conditions"] and "3.0 MPa" in ox_np["assertion"]["conditions"]

    # 6 Negative Fault-Injection Quality Gate Tests
    neg_caught: Dict[str, bool] = {}

    # 1) over_broad_assertion
    b1 = copy.deepcopy(n1_b)
    b1["nanopublications"][0]["assertion"]["atomic_statement"] = "Sentence one claims X. Sentence two claims Y. Sentence three claims Z."
    v1 = validate_nanopubs(n1_case, n1_run["result"], b1, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v1["pass"] and not v1["gate_results"]["gate1_over_broad"]
    neg_caught["gate1_over_broad"] = True

    # 2) invalid_relation_type
    b2 = copy.deepcopy(n1_b)
    b2["nanopublications"][0]["assertion"]["predicate"] = "magically_transforms"
    v2 = validate_nanopubs(n1_case, n1_run["result"], b2, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v2["pass"] and not v2["gate_results"]["gate2_relation_or_contradiction"]
    neg_caught["gate2_invalid_relation"] = True

    # 3) assertion_evidence_contradiction
    b3 = copy.deepcopy(n1_b)
    b3["nanopublications"][0]["assertion"]["predicate"] = "increases"
    b3["nanopublications"][0]["provenance"]["minimal_evidence_snippet"] = "water co-feeding inhibits catalytic activity."
    v3 = validate_nanopubs(n1_case, n1_run["result"], b3, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v3["pass"] and not v3["gate_results"]["gate2_relation_or_contradiction"]
    neg_caught["gate2_contradiction"] = True

    # 4) evidence_not_grounded
    b4 = copy.deepcopy(n1_b)
    b4["nanopublications"][0]["provenance"]["location"]["block_id"] = "p9_b99"
    v4 = validate_nanopubs(n1_case, n1_run["result"], b4, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v4["pass"] and not v4["gate_results"]["gate3_grounding_and_entity"]
    neg_caught["gate3_ungrounded"] = True

    # 5) entity_confusion
    b5 = copy.deepcopy(n1_b)
    b5["nanopublications"][0]["assertion"]["subject"] = "Platinum Nanowire"
    v5 = validate_nanopubs(n1_case, n1_run["result"], b5, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v5["pass"] and not v5["gate_results"]["gate3_grounding_and_entity"]
    neg_caught["gate3_entity_confusion"] = True

    # 6) missing_provenance_metadata
    b6 = copy.deepcopy(n1_b)
    b6["source_metadata"] = {}
    v6 = validate_nanopubs(n1_case, n1_run["result"], b6, n1_run["docs"], nquads_text=n1_run["nq_txt"])
    assert not v6["pass"] and not v6["gate_results"]["gate4_provenance_metadata"]
    neg_caught["gate4_missing_metadata"] = True

    report["tests"]["n1_catalysis_nanopub"] = {
        "pass": True,
        "nanopub_count": n1_b["nanopub_count"],
        "nquads_quad_count": n1_v["metrics"]["nquads_quad_count"],
        "anchor_grounding_rate": n1_v["metrics"]["anchor_grounding_rate"],
        "negative_gates_caught": neg_caught,
    }

    # --- N2: Markdown Deduplication & Cross-Assertion Contradiction ---
    n2_in = out_dir / "n2_in"
    n2_in.mkdir(parents=True, exist_ok=True)
    _build_n2_md(n2_in / "n2_markdown_paper.md")
    n2_case = {
        "case_id": "selftest-n2-dedup-conflict",
        "input_files": ["n2_markdown_paper.md"],
        "output_contract": ["case_id", "status", "nanopub_count", "nanopublications", "deduplication_log", "conflicts", "uncertainties"],
    }
    n2_run = _run_case(n2_in, out_dir / "n2_work", n2_case)
    assert n2_run["validation"]["pass"], f"N2 validation failed: {n2_run['validation']['errors']}"
    assert len(n2_run["bundle"]["deduplication_log"]) >= 1, "Expected duplicate assertion merge!"
    assert len(n2_run["bundle"]["conflicts"]) >= 1, "Expected cross_assertion_conflict (increases vs decreases)!"
    report["tests"]["n2_dedup_and_conflict"] = {
        "pass": True,
        "nanopub_count": n2_run["bundle"]["nanopub_count"],
        "dedup_merges": len(n2_run["bundle"]["deduplication_log"]),
        "conflicts": len(n2_run["bundle"]["conflicts"]),
    }

    # --- N3: Encrypted & Scanned PDFs ---
    n3a_in = out_dir / "n3a_in"
    n3a_in.mkdir(parents=True, exist_ok=True)
    _build_n3a_encrypted(n3a_in / "enc.pdf")
    n3a_case = {"case_id": "selftest-n3a", "input_files": ["enc.pdf"], "output_contract": ["case_id", "status", "nanopublications", "uncertainties"]}
    n3a_run = _run_case(n3a_in, out_dir / "n3a_work", n3a_case)
    assert n3a_run["result"]["status"] == "refused" and n3a_run["bundle"]["nanopub_count"] == 0
    assert n3a_run["validation"]["pass"]

    n3b_in = out_dir / "n3b_in"
    n3b_in.mkdir(parents=True, exist_ok=True)
    _build_n3b_scanned(n3b_in / "scan.pdf")
    n3b_case = {"case_id": "selftest-n3b", "input_files": ["scan.pdf"], "output_contract": ["case_id", "status", "nanopublications", "uncertainties"]}
    n3b_run = _run_case(n3b_in, out_dir / "n3b_work", n3b_case)
    assert n3b_run["result"]["status"] == "unverifiable" and n3b_run["bundle"]["nanopub_count"] == 0
    assert n3b_run["validation"]["pass"]

    report["tests"]["n3_encrypted_and_scanned"] = {
        "pass": True,
        "encrypted_status": n3a_run["result"]["status"],
        "scanned_status": n3b_run["result"]["status"],
    }

    if compare_baseline:
        report["baseline_comparison"] = {
            "N1_catalysis_nanopub": {
                "baseline_single_paragraph_assertions": 1,
                "skill_atomic_nanopublications": n1_b["nanopub_count"],
                "baseline_controlled_vocab_predicates": 0,
                "skill_controlled_vocab_predicates": len(preds),
                "baseline_evidence_types_distinguished": 1,
                "skill_evidence_types_distinguished": len(ev_types),
                "baseline_valid_nquads_quads": 0,
                "skill_valid_nquads_quads": n1_v["metrics"]["nquads_quad_count"],
            }
        }

    write_json_atomic(out_dir / "self_test_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reading-14-nanopublication self-tests.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--compare-baseline", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="reading14_selftest_"))
    rep = run_self_test(out_dir, compare_baseline=args.compare_baseline)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
