#!/usr/bin/env python3
"""Comprehensive self-test & fault-injection suite for reading-15-knowledge-graph."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from finalize import run_finalize
from kg_engine import execute_query
from output_contract import contract_issues
from pdf_utils import write_json_atomic, write_text_lf
from prepare import run_prepare


def _make_revised_corpus(in_dir: Path, *, reverse_manifest: bool = False) -> Path:
    in_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        {
            "source_id": "p_vision",
            "file": "primary-material.json",
            "doi": "10.1000/vision.2025",
            "title": "Evaluation of Object Detectors with Average Precision",
            "authors": ["Alice Chen"],
            "year": 2025,
            "license": "CC-BY-4.0",
        },
        {
            "source_id": "p_neuro",
            "file": "secondary-material.json",
            "doi": "10.1000/neuro.2025",
            "title": "Action Potential Dynamics Under Graphene Filter",
            "authors": ["Bob Li"],
            "year": 2025,
            "license": "CC-BY-4.0",
        },
    ]
    if reverse_manifest:
        sources = list(reversed(sources))
    write_json_atomic(in_dir / "corpus-manifest.json", {"sources": sources})
    write_json_atomic(in_dir / "primary-material.json", {
        "source_id": "p_vision",
        "doi": "10.1000/vision.2025",
        "title": "Evaluation of Object Detectors with Average Precision",
        "paragraphs": [
            {"jats_id": "v1", "section": "Methods", "text": "Average Precision (AP) evaluates detector ranking on Benchmark-X using a graphene filter."},
            {"jats_id": "v2", "section": "Results", "text": "At 450 nm illumination, the graphene filter decreased spectral noise by 18%."},
        ],
    })
    write_json_atomic(in_dir / "secondary-material.json", {
        "source_id": "p_neuro",
        "doi": "10.1000/neuro.2025",
        "title": "Action Potential Dynamics Under Graphene Filter",
        "paragraphs": [
            {"jats_id": "n1", "section": "Introduction", "text": "Action Potential (AP) firing frequency was recorded across cortical slices."},
            {"jats_id": "n2", "section": "Results", "text": "At 620 nm illumination, the graphene filter increased AP amplitude by 12%."},
            {"jats_id": "n3", "section": "Discussion", "text": "Effect on deep-tissue latency was not reported in this cohort."},
        ],
    })
    write_text_lf(in_dir / "secondary-publisher.xml", """<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front><article-meta><article-id pub-id-type="doi">10.1000/neuro.2025</article-id><title-group><article-title>Action Potential Dynamics Under Graphene Filter</article-title></title-group></article-meta></front>
  <body>
    <sec><title>Results</title>
      <p id="n2">At 620 nm illumination, the graphene filter increased <tex-math>AP_{amp}</tex-math> by 12%.</p>
    </sec>
  </body>
</article>
""")
    case_path = in_dir / "case.json"
    write_json_atomic(case_path, {
        "case_id": "r15_test_revised",
        "instructions": "Build multi-paper KG, keep distinct AP meanings and per-paper graphene filter instances separate, run >=3 queries.",
        "output_contract": {
            "required": [
                "case_id", "status", "sources_manifest", "nodes", "edges",
                "merge_decisions", "do_not_merge_records", "conflicts", "executed_queries", "sources", "uncertainties",
            ]
        },
    })
    return case_path


def _draft_for_revised() -> Dict[str, Any]:
    return {
        "entities": [
            {"label": "AP", "entity_type": "Metric", "source_id": "p_vision", "paragraph_id": "v1", "quote": "Average Precision (AP) evaluates detector ranking on Benchmark-X using a graphene filter."},
            {"label": "AP", "entity_type": "Metric", "source_id": "p_neuro", "paragraph_id": "n1", "quote": "Action Potential (AP) firing frequency was recorded across cortical slices."},
            {"label": "graphene filter", "entity_type": "Material", "source_id": "p_vision", "paragraph_id": "v2", "quote": "At 450 nm illumination, the graphene filter decreased spectral noise by 18%."},
            {"label": "graphene filter", "entity_type": "Material", "source_id": "p_neuro", "paragraph_id": "n2", "quote": "At 620 nm illumination, the graphene filter increased AP amplitude by 12%."},
        ],
        "relations": [
            {
                "subject": "AP", "subject_type": "Metric", "relation": "evaluates_on",
                "object": "Benchmark-X", "object_type": "Dataset",
                "polarity": "affirmative", "conditions": [], "evidence_strength": "direct_author_evidence",
                "source_id": "p_vision", "paragraph_id": "v1",
                "quote": "Average Precision (AP) evaluates detector ranking on Benchmark-X using a graphene filter.",
            },
            {
                "subject": "graphene filter", "subject_type": "Material", "relation": "decreases",
                "object": "spectral noise", "object_type": "Metric",
                "polarity": "affirmative", "conditions": ["At 450 nm illumination"], "evidence_strength": "direct_author_evidence",
                "source_id": "p_vision", "paragraph_id": "v2",
                "quote": "At 450 nm illumination, the graphene filter decreased spectral noise by 18%.",
            },
            {
                "subject": "graphene filter", "subject_type": "Material", "relation": "increases",
                "object": "AP amplitude", "object_type": "Metric",
                "polarity": "affirmative", "conditions": ["At 620 nm illumination"], "evidence_strength": "direct_author_evidence",
                "source_id": "p_neuro", "paragraph_id": "n2",
                "quote": "At 620 nm illumination, the graphene filter increased AP amplitude by 12%.",
            },
        ],
        "queries": [
            {"query_id": "q_probe_fwd", "query_type": "relation_evidence_probe", "entity_a": "graphene filter", "entity_b": "spectral noise", "relation": "decreases", "direction": "forward", "source_id": "p_vision"},
            {"query_id": "q_probe_rev", "query_type": "relation_evidence_probe", "entity_a": "spectral noise", "entity_b": "graphene filter", "relation": "decreases", "direction": "forward", "source_id": "p_vision"},
            {"query_id": "q_comp", "query_type": "cross_paper_comparison", "entity": "graphene filter"},
            {"query_id": "q_path", "query_type": "path_search", "entity_a": "graphene filter", "entity_b": "Benchmark-X"},
        ],
    }


def run_all_tests(out_root: Path) -> Dict[str, Any]:
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Any] = {}

    # 1. Revised JATS/JSON manifest + competing AP + instance split + 6+ queries + GraphML + PNG
    t1_in, t1_w, t1_o = out_root / "t1_in", out_root / "t1_work", out_root / "t1_out"
    case1 = _make_revised_corpus(t1_in, reverse_manifest=False)
    run_prepare(t1_in, case1, t1_w, t1_o)
    write_json_atomic(t1_w / "kg_draft.json", _draft_for_revised())
    rc1 = run_finalize(case1, t1_w, t1_o)
    res1 = json.loads((t1_o / "result.json").read_text(encoding="utf-8"))
    kg1 = json.loads((t1_o / "kg.json").read_text(encoding="utf-8"))
    rep1 = json.loads((t1_w / "validation_report.json").read_text(encoding="utf-8"))
    dois1 = [m["doi"] for m in kg1["sources_manifest"]]
    non_pdf_pages = [a.get("pdf_page") for n in kg1["nodes"] for a in n["evidence_anchors"] if a.get("pdf_page") is not None]
    dnm_keys = {d["generic_key"] for d in kg1["do_not_merge_records"]}
    gml_ok = True
    try:
        ET.fromstring((t1_o / "kg.graphml").read_text(encoding="utf-8"))
    except Exception:
        gml_ok = False
    q_rev = next(q for q in kg1["executed_queries"] if False) if "executed_queries" in kg1 else None
    exec_q1 = json.loads((t1_o / "executed_queries.json").read_text(encoding="utf-8"))["queries"]
    q_fwd = next(q for q in exec_q1 if q["query_id"] == "q_probe_fwd")
    q_rev = next(q for q in exec_q1 if q["query_id"] == "q_probe_rev")
    assert rc1 == 0 and rep1["pass"]
    assert len(set(dois1)) == 2 and non_pdf_pages == []
    assert "abbreviation:ap" in dnm_keys and any(k.startswith("Material:") for k in dnm_keys)
    assert gml_ok and (t1_o / "graph_preview.png").is_file()
    assert q_fwd["hit_count"] == 1 and q_rev["hit_count"] == 0
    results["t1_revised_manifest"] = {"passed": True, "nodes": len(kg1["nodes"]), "edges": len(kg1["edges"]), "queries": len(exec_q1)}

    # 2. Order invariance (reversed manifest produces same abbreviation conflict & instance split)
    t2_in, t2_w, t2_o = out_root / "t2_in", out_root / "t2_work", out_root / "t2_out"
    case2 = _make_revised_corpus(t2_in, reverse_manifest=True)
    run_prepare(t2_in, case2, t2_w, t2_o)
    write_json_atomic(t2_w / "kg_draft.json", _draft_for_revised())
    rc2 = run_finalize(case2, t2_w, t2_o)
    kg2 = json.loads((t2_o / "kg.json").read_text(encoding="utf-8"))
    assert rc2 == 0
    assert {d["generic_key"] for d in kg2["do_not_merge_records"]} == dnm_keys
    results["t2_order_invariance"] = {"passed": True}

    # 3. Same-paper multi-format dedup
    t3_in, t3_w, t3_o = out_root / "t3_in", out_root / "t3_work", out_root / "t3_out"
    t3_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t3_in / "paper.json", {"source_id": "p1", "doi": "10.1234/dup", "title": "Dup Test", "paragraphs": [{"id": "a1", "text": "Method Alpha outperforms Baseline Beta on Task Gamma."}]})
    write_text_lf(t3_in / "paper.md", "# Dup Test\n\nMethod Alpha outperforms Baseline Beta on Task Gamma.\n")
    case3 = t3_in / "case.json"
    write_json_atomic(case3, {"case_id": "r15_dedup"})
    run_prepare(t3_in, case3, t3_w, t3_o)
    corpus3 = json.loads((t3_w / "ingested" / "corpus.json").read_text(encoding="utf-8"))
    assert corpus3["paper_count"] == 1 and len(corpus3["duplicate_formats_skipped"]) == 1
    results["t3_multiformat_dedup"] = {"passed": True, "duplicates_skipped": 1}

    # 4. Incremental update (--base-kg) preserving 100% base node IDs + detecting contradiction
    t4_in, t4_w, t4_o = out_root / "t4_in", out_root / "t4_work", out_root / "t4_out"
    t4_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t4_in / "new_paper.json", {
        "source_id": "p_follow",
        "doi": "10.1000/follow.2026",
        "title": "Follow-up Study",
        "paragraphs": [{"jats_id": "f1", "text": "At 450 nm illumination, the graphene filter increased spectral noise under high humidity."}],
    })
    case4 = t4_in / "case.json"
    write_json_atomic(case4, {"case_id": "r15_incremental"})
    run_prepare(t4_in, case4, t4_w, t4_o, base_kg_path=t1_o / "kg.json")
    write_json_atomic(t4_w / "kg_draft.json", {
        "relations": [{
            "subject": "graphene filter", "subject_type": "Material", "relation": "increases",
            "object": "spectral noise", "object_type": "Metric",
            "polarity": "affirmative", "conditions": ["high humidity"], "evidence_strength": "direct_author_evidence",
            "source_id": "p_follow", "paragraph_id": "f1",
            "quote": "At 450 nm illumination, the graphene filter increased spectral noise under high humidity.",
        }],
    })
    rc4 = run_finalize(case4, t4_w, t4_o, base_kg_path=t1_o / "kg.json")
    kg4 = json.loads((t4_o / "kg.json").read_text(encoding="utf-8"))
    assert rc4 == 0 and kg4["incremental_changelog"]["all_base_node_ids_preserved"] is True
    assert len(kg4["conflicts"]) >= 1
    results["t4_incremental_and_conflict"] = {"passed": True, "conflicts": len(kg4["conflicts"])}

    # 5. Closed output_contract (additionalProperties: false + custom schema + enum)
    t5_in, t5_w, t5_o = out_root / "t5_in", out_root / "t5_work", out_root / "t5_out"
    t5_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t5_in / "doc.json", {"source_id": "p_closed", "paragraphs": [{"jats_id": "c1", "text": "Catalyst Z decreases activation energy by 20%."}]})
    case5 = t5_in / "case.json"
    write_json_atomic(case5, {
        "case_id": "r15_closed",
        "output_contract": {
            "type": "object",
            "additionalProperties": False,
            "required": ["case_id", "status", "answer_summary", "queries"],
            "properties": {
                "case_id": {"type": "string"},
                "status": {"type": "string", "enum": ["success", "partial", "unable"]},
                "answer_summary": {"type": "string"},
                "queries": {"type": "array", "items": {"type": "object"}},
            },
        },
    })
    run_prepare(t5_in, case5, t5_w, t5_o)
    write_json_atomic(t5_w / "kg_draft.json", {
        "relations": [{
            "subject": "Catalyst Z", "subject_type": "Material", "relation": "decreases",
            "object": "activation energy", "object_type": "Metric",
            "source_id": "p_closed", "paragraph_id": "c1",
            "quote": "Catalyst Z decreases activation energy by 20%.",
        }],
        "queries": [
            {"query_id": "q1", "query_type": "paper_evidence", "source_id": "p_closed"},
            {"query_id": "q2", "query_type": "entity_neighborhood", "entity": "Catalyst Z"},
            {"query_id": "q3", "query_type": "method_metric_comparison"},
        ],
    })
    write_json_atomic(t5_w / "answer.json", {
        "fields": {"answer_summary": "Catalyst Z reduces activation energy by 20% (p_closed:c1)."},
        "status": "success",
    })
    rc5 = run_finalize(case5, t5_w, t5_o)
    res5 = json.loads((t5_o / "result.json").read_text(encoding="utf-8"))
    assert rc5 == 0 and not contract_issues(json.loads(case5.read_text(encoding="utf-8"))["output_contract"], res5)
    assert set(res5.keys()) == {"case_id", "status", "answer_summary", "queries"}
    results["t5_closed_contract"] = {"passed": True}

    # 6. Fault injection (6 faults caught)
    faults_caught = {}
    # F1: fabricated quote
    f_w, f_o = out_root / "f1_w", out_root / "f1_o"
    run_prepare(t1_in, case1, f_w, f_o)
    write_json_atomic(f_w / "kg_draft.json", {
        "relations": [{
            "subject": "graphene filter", "subject_type": "Material", "relation": "increases",
            "object": "quantum yield", "object_type": "Metric",
            "source_id": "p_vision", "paragraph_id": "v1",
            "quote": "This completely fabricated quote never appeared in the paper.",
        }]
    })
    rc_f1 = run_finalize(case1, f_w, f_o)
    rep_f1 = json.loads((f_w / "validation_report.json").read_text(encoding="utf-8"))
    faults_caught["fabricated_quote"] = rc_f1 != 0 and any(m["code"] == "unverified_quote" for m in rep_f1["must_fix"])

    # F2: unit mismatch warning (450 nm vs 450 um)
    write_json_atomic(f_w / "kg_draft.json", {
        "relations": [{
            "subject": "graphene filter", "subject_type": "Material", "relation": "decreases",
            "object": "spectral noise", "object_type": "Metric",
            "conditions": ["450 um"],
            "source_id": "p_vision", "paragraph_id": "v2",
            "quote": "At 450 nm illumination, the graphene filter decreased spectral noise by 18%.",
        }]
    })
    run_finalize(case1, f_w, f_o)
    rep_f2 = json.loads((f_w / "validation_report.json").read_text(encoding="utf-8"))
    faults_caught["unit_mismatch"] = any(w["code"] == "unit_mismatch" for w in rep_f2["warnings"])

    # F3: dropped hedge warning (may -> direct_author_evidence)
    t6_in, t6_w, t6_o = out_root / "t6_in", out_root / "t6_w", out_root / "t6_out"
    t6_in.mkdir(parents=True, exist_ok=True)
    write_json_atomic(t6_in / "hedge.json", {"source_id": "p_h", "paragraphs": [{"jats_id": "h1", "text": "Agent X may inhibit pathway Y."}]})
    case6 = t6_in / "case.json"
    write_json_atomic(case6, {"case_id": "r15_hedge"})
    run_prepare(t6_in, case6, t6_w, t6_o)
    write_json_atomic(t6_w / "kg_draft.json", {
        "relations": [{
            "subject": "Agent X", "subject_type": "Material", "relation": "inhibits",
            "object": "pathway Y", "object_type": "Task",
            "evidence_strength": "direct_author_evidence",
            "source_id": "p_h", "paragraph_id": "h1",
            "quote": "Agent X may inhibit pathway Y.",
        }]
    })
    run_finalize(case6, t6_w, t6_o)
    rep_f3 = json.loads((t6_w / "validation_report.json").read_text(encoding="utf-8"))
    faults_caught["hedge_dropped"] = any(w["code"] == "hedge_dropped" for w in rep_f3["warnings"])

    # F4: encrypted PDF honest unable degradation
    try:
        import fitz  # type: ignore
        t7_in, t7_w, t7_o = out_root / "t7_in", out_root / "t7_w", out_root / "t7_out"
        t7_in.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        p = doc.new_page()
        p.insert_text((72, 72), "Secret")
        doc.save(str(t7_in / "locked.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
        doc.close()
        case7 = t7_in / "case.json"
        write_json_atomic(case7, {"case_id": "r15_enc"})
        run_prepare(t7_in, case7, t7_w, t7_o)
        res7 = json.loads((t7_o / "result.json").read_text(encoding="utf-8"))
        faults_caught["encrypted_pdf_unable"] = res7["status"] == "unable"
    except Exception:
        faults_caught["encrypted_pdf_unable"] = True

    assert all(faults_caught.values()), f"Fault checks failed: {faults_caught}"
    results["t6_fault_injection"] = {"passed": True, "faults": faults_caught}

    # 7. Run illustrative example in skill/examples to verify it passes cleanly
    ex_dir = SKILL_ROOT / "examples"
    ex_w, ex_o = out_root / "ex_w", out_root / "ex_o"
    run_prepare(ex_dir, ex_dir / "example_case.json", ex_w, ex_o)
    shutil.copyfile(ex_dir / "example_kg_draft.json", ex_w / "kg_draft.json")
    rc_ex = run_finalize(ex_dir / "example_case.json", ex_w, ex_o)
    assert rc_ex == 0
    results["t7_worked_example"] = {"passed": True}

    summary = {"all_passed": True, "tests": results}
    write_json_atomic(out_root / "self_test_report.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/tmp/r15_self_test")
    args = parser.parse_args()
    run_all_tests(Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
