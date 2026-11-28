#!/usr/bin/env python3
"""Compile Agent kg_draft.json + optional answer.json, run deterministic KG queries & exports, audit MUST_FIX/WARNINGS, and write contract-compliant result.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from kg_engine import _load_draft_parts, compile_kg, execute_all_queries, execute_query, export_all
from output_contract import (
    allows_null,
    contract_issues,
    contract_specs,
    field_conforms,
    is_closed_contract,
    pick_status_value,
    required_contract_fields,
)
from pdf_utils import write_json_atomic

PLACEHOLDER_PATS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]


def _collect_sources(kg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for e in kg.get("edges", []):
        key = (e["source_id"], e["paragraph_id"])
        if key not in seen:
            seen.add(key)
            sources.append({
                "source_id": e["source_id"],
                "paragraph_id": e["paragraph_id"],
                "location_type": e["location_type"],
                "pdf_page": e.get("pdf_page"),
                "quote_snippet": e.get("quote_snippet", "")[:160],
            })
    for n in kg.get("nodes", []):
        for a in n.get("evidence_anchors", []):
            key = (a.get("source_id"), a.get("paragraph_id"))
            if key not in seen:
                seen.add(key)
                sources.append({
                    "source_id": a.get("source_id"),
                    "paragraph_id": a.get("paragraph_id"),
                    "location_type": a.get("location_type"),
                    "pdf_page": a.get("pdf_page"),
                    "quote_snippet": a.get("quote_snippet", "")[:160],
                })
    return sources


def _audit_deliverables(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    kg: Dict[str, Any],
    corpus: Dict[str, Any],
    queries: List[Dict[str, Any]],
    graphml_path: Path,
    base_kg: Optional[Dict[str, Any]],
    compile_must_fix: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    must_fix = list(compile_must_fix)

    # 1. Contract compliance
    for issue in contract_issues(case_data.get("output_contract"), result):
        must_fix.append({"code": issue["code"], "field": issue.get("field"), "message": issue["message"]})

    # 2. Per-paper DOI isolation check
    s_manifest = kg.get("sources_manifest", [])
    if len(s_manifest) >= 2:
        dois = [m.get("doi") for m in s_manifest if m.get("doi") and m.get("doi") != "not_reported"]
        if len(dois) != len(set(dois)):
            must_fix.append({"code": "doi_leak", "message": f"Duplicate DOI across sources_manifest: {dois}"})

    # 3. Non-PDF fake pdf_page check
    for n in kg.get("nodes", []):
        for a in n.get("evidence_anchors", []):
            if a.get("location_type") not in ("pdf_block", "pdf_table") and a.get("pdf_page") is not None:
                must_fix.append({"code": "fake_pdf_page", "node_id": n["node_id"], "message": f"Non-PDF anchor has pdf_page={a.get('pdf_page')}"})
    for e in kg.get("edges", []):
        if e.get("location_type") not in ("pdf_block", "pdf_table") and e.get("pdf_page") is not None:
            must_fix.append({"code": "fake_pdf_page", "edge_id": e["edge_id"], "message": f"Non-PDF edge has pdf_page={e.get('pdf_page')}"})

    # 4. Placeholder check
    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATS:
        if pat.search(serialized):
            must_fix.append({"code": "placeholder_found", "message": f"Placeholder matching {pat.pattern}"})

    # 5. >= 3 distinct replayable evidence-backed queries covering graph sources
    answered = set()
    covered_sources = set()
    for q in queries:
        qid = q.get("query_id")
        params = q.get("parameters") or {"query_type": q.get("query_type")}
        replay = execute_query(kg, query_id=qid, **params)
        if replay != {k: v for k, v in q.items() if k != "parameters"}:
            must_fix.append({"code": "query_replay_mismatch", "query_id": qid, "message": "Query answer differs from graph replay."})
            continue
        ev = []
        for f in ("subgraph_edges", "comparison_rows", "evidence_edges", "boundary_evidence"):
            ev.extend(replay.get(f, []))
        for n in replay.get("shared_concepts", []):
            ev.extend(n.get("evidence_anchors", []))
        for c in replay.get("comparisons", []):
            for rows in c.get("per_paper_evidence", {}).values():
                ev.extend(rows)
        for path in replay.get("paths", []):
            ev.extend(path)
        if ev and replay.get("answer_status") == "supported":
            answered.add(json.dumps({k: v for k, v in params.items() if v is not None}, sort_keys=True))
            covered_sources.update(row["source_id"] for row in ev if isinstance(row, dict) and row.get("source_id"))

    graph_sources = {e["source_id"] for e in kg.get("edges", []) if e["relation_type"] != "instance_of"}
    if graph_sources and (len(answered) < 3 or not graph_sources.issubset(covered_sources)):
        must_fix.append({"code": "insufficient_queries", "message": "Expected >=3 distinct evidence-backed queries covering every graph source."})

    # 6. GraphML XML round-trip check
    if graphml_path.is_file():
        try:
            root = ET.fromstring(graphml_path.read_text(encoding="utf-8"))
            ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
            xml_nodes = {n.get("id") for n in root.findall(".//g:node", ns)}
            xml_edges = {e.get("id"): (e.get("source"), e.get("target")) for e in root.findall(".//g:edge", ns)}
            exp_nodes = {n["node_id"] for n in kg.get("nodes", [])}
            exp_edges = {e["edge_id"]: (e["source_node"], e["target_node"]) for e in kg.get("edges", [])}
            if xml_nodes != exp_nodes or xml_edges != exp_edges:
                must_fix.append({"code": "graphml_mismatch", "message": "GraphML nodes/edges differ from kg.json."})
        except Exception as exc:
            must_fix.append({"code": "invalid_graphml", "message": str(exc)})

    # 7. Incremental base_kg node ID preservation
    if base_kg is not None:
        cur_nids = {n["node_id"] for n in kg.get("nodes", [])}
        dropped = [n["node_id"] for n in base_kg.get("nodes", []) if n["node_id"] not in cur_nids]
        if dropped:
            must_fix.append({"code": "base_node_ids_dropped", "dropped": dropped[:10], "message": "Incremental update dropped base_kg node IDs."})

    return must_fix


def _assemble_result(
    case_data: Dict[str, Any],
    kg: Dict[str, Any],
    queries: List[Dict[str, Any]],
    graphml_text: str,
    markdown_text: str,
    artifacts: Dict[str, str],
    answer_data: Optional[Dict[str, Any]],
    must_fix: List[Dict[str, Any]],
) -> Dict[str, Any]:
    case_id = str(case_data.get("case_id") or "unknown_case")
    contract = case_data.get("output_contract")
    closed = is_closed_contract(contract)
    specs = contract_specs(contract)
    required = required_contract_fields(contract)
    sources = _collect_sources(kg)

    catalog: Dict[str, Any] = {
        "case_id": case_id,
        "sources_manifest": kg.get("sources_manifest", []),
        "nodes": kg.get("nodes", []),
        "edges": kg.get("edges", []),
        "entities": kg.get("nodes", []),
        "relations": kg.get("edges", []),
        "merge_decisions": kg.get("merge_decisions", []),
        "do_not_merge_records": kg.get("do_not_merge_records", []),
        "conflicts": kg.get("conflicts", []),
        "boundary_records": kg.get("boundary_records", []),
        "executed_queries": queries,
        "queries": queries,
        "incremental_changelog": kg.get("incremental_changelog"),
        "abbreviation_definitions": kg.get("abbreviation_definitions", []),
        "artifacts": artifacts,
        "graphml": graphml_text,
        "markdown": markdown_text,
        "sources": sources,
        "evidence": sources,
        "provenance": sources,
        "uncertainties": list(kg.get("uncertainties", [])),
    }

    ans_fields: Dict[str, Any] = {}
    ans_status: Optional[str] = None
    ans_uncertainties: List[Any] = []
    if isinstance(answer_data, dict):
        if isinstance(answer_data.get("fields"), dict):
            ans_fields = dict(answer_data["fields"])
        else:
            ans_fields = {k: v for k, v in answer_data.items() if k not in ("status", "status_reason", "uncertainties")}
        if isinstance(answer_data.get("status"), str):
            ans_status = answer_data["status"]
        if isinstance(answer_data.get("uncertainties"), list):
            ans_uncertainties = list(answer_data["uncertainties"])

    has_edges = any(e.get("relation_type") != "instance_of" for e in kg.get("edges", []))
    intent = "unable" if not kg.get("sources_manifest") else ("partial" if (must_fix or not has_edges or kg.get("uncertainties")) else "success")
    if ans_status in ("unable", "unverifiable", "refused", "partial"):
        intent = ans_status if ans_status in ("unable", "partial") else "unable"
    status_val = pick_status_value(contract, intent)

    result: Dict[str, Any] = {}
    if not closed:
        result.update({
            "case_id": case_id,
            "status": status_val,
            "sources_manifest": kg.get("sources_manifest", []),
            "nodes": kg.get("nodes", []),
            "edges": kg.get("edges", []),
            "merge_decisions": kg.get("merge_decisions", []),
            "do_not_merge_records": kg.get("do_not_merge_records", []),
            "conflicts": kg.get("conflicts", []),
            "executed_queries": queries,
            "sources": sources,
            "artifacts": artifacts,
            "uncertainties": list(kg.get("uncertainties", [])) + ans_uncertainties,
        })
        if kg.get("boundary_records"):
            result["boundary_records"] = kg["boundary_records"]
        if kg.get("incremental_changelog") is not None:
            result["incremental_changelog"] = kg["incremental_changelog"]

    # Fill contract-declared fields cleanly without clashing with custom shapes
    for key, spec in specs.items():
        if key in ans_fields:
            result[key] = ans_fields[key]
            continue
        if key == "case_id":
            result[key] = case_id
            continue
        if key == "status":
            result[key] = status_val
            continue
        if key in catalog and catalog[key] is not None:
            val = catalog[key]
            if field_conforms(spec, val, contract):
                result[key] = val
            elif not closed and key in result:
                # Preserve KG data under kg_<key> if contract gave <key> a custom shape
                result[f"kg_{key}"] = result.pop(key)
        if key not in result and key in required:
            result[key] = None if allows_null(spec, contract) else ([] if isinstance(spec, dict) and spec.get("type") == "array" else "")

    if not closed:
        for k, v in ans_fields.items():
            if k not in result:
                result[k] = v
    else:
        allowed = set(specs.keys())
        result = {k: v for k, v in result.items() if k in allowed}

    return result


def run_finalize(case_path: Path, work_dir: Path, out_dir: Path, base_kg_path: Optional[Path] = None) -> int:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_data = json.loads(case_path.read_text(encoding="utf-8-sig")) if case_path.is_file() else {}
    meta = json.loads((work_dir / "meta.json").read_text(encoding="utf-8-sig"))
    corpus = json.loads(Path(meta["corpus_json"]).read_text(encoding="utf-8-sig"))
    index_entries = json.loads((work_dir / "index.json").read_text(encoding="utf-8-sig"))

    if base_kg_path is None and meta.get("base_kg_path"):
        base_kg_path = Path(meta["base_kg_path"])
    base_kg = json.loads(base_kg_path.read_text(encoding="utf-8-sig")) if base_kg_path and base_kg_path.is_file() else None

    draft = _load_draft_parts(work_dir)
    ans_path = work_dir / "answer.json"
    answer_data = json.loads(ans_path.read_text(encoding="utf-8-sig")) if ans_path.is_file() else None

    kg, compile_must_fix, warnings = compile_kg(corpus, index_entries, draft, base_kg=base_kg)
    queries = execute_all_queries(kg, draft.get("queries", []))

    write_json_atomic(out_dir / "kg.json", kg)
    write_json_atomic(out_dir / "executed_queries.json", {"queries": queries})
    artifacts = export_all(kg, queries, out_dir)

    gml_text = (out_dir / "kg.graphml").read_text(encoding="utf-8") if (out_dir / "kg.graphml").is_file() else ""
    md_text = (out_dir / "kg_report.md").read_text(encoding="utf-8") if (out_dir / "kg_report.md").is_file() else ""

    result = _assemble_result(case_data, kg, queries, gml_text, md_text, artifacts, answer_data, compile_must_fix)
    must_fix = _audit_deliverables(case_data, result, kg, corpus, queries, out_dir / "kg.graphml", base_kg, compile_must_fix)
    if must_fix:
        # Downgrade status if post-assembly audit caught issues
        contract = case_data.get("output_contract")
        if "status" in result:
            result["status"] = pick_status_value(contract, "partial")

    write_json_atomic(out_dir / "result.json", result)
    report = {
        "pass": len(must_fix) == 0,
        "must_fix": must_fix,
        "warnings": warnings,
        "metrics": {
            "status": result.get("status"),
            "sources_count": len(kg.get("sources_manifest", [])),
            "node_count": len(kg.get("nodes", [])),
            "edge_count": len(kg.get("edges", [])),
            "substantive_edges": sum(1 for e in kg.get("edges", []) if e["relation_type"] != "instance_of"),
            "do_not_merge_count": len(kg.get("do_not_merge_records", [])),
            "conflict_count": len(kg.get("conflicts", [])),
            "executed_queries_count": len(queries),
        },
    }
    write_json_atomic(work_dir / "validation_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile kg_draft.json, run queries & exports, and package validated result.json.")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--work", default="/app/work/r15")
    parser.add_argument("--out", default="/app/output")
    parser.add_argument("--base-kg", default=None)
    args = parser.parse_args()

    return run_finalize(
        Path(args.case),
        Path(args.work),
        Path(args.out),
        base_kg_path=Path(args.base_kg) if args.base_kg else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
