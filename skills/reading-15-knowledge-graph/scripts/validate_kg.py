#!/usr/bin/env python3
"""Deterministic KG validator: checks contract, per-paper isolation, no fake pdf_page, 100% grounding, >=3 queries, GraphML XML, and incremental preservation."""

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

from pdf_utils import write_json_atomic
from kg_contract import contract_issues
from query_kg import execute_query

VALID_STATUSES = {"success", "partial", "unverifiable", "refused"}
PLACEHOLDER_PATS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]


def _flat(text):
    return " ".join(text.split())


def validate_kg(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    kg: Dict[str, Any],
    corpus: Dict[str, Any],
    queries: List[Dict[str, Any]],
    graphml_text: Optional[str] = None,
    base_kg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate KG deliverables against Harbor revised provenance, alignment, query, and XML rules."""
    errors: List[str] = []
    warnings: List[str] = []

    expected_cid = case_data.get("case_id")
    if expected_cid and result.get("case_id") != expected_cid:
        errors.append(f"case_id mismatch: expected '{expected_cid}', got '{result.get('case_id')}'.")

    errors.extend(issue["message"] for issue in contract_issues(case_data.get("output_contract"), result))

    for key in ("nodes", "edges", "sources_manifest", "merge_decisions", "do_not_merge_records", "conflicts", "boundary_records"):
        if key in result and result[key] != kg.get(key, []):
            errors.append(f"Result {key} differs from the verified graph.")
    if result.get("executed_queries") != queries:
        errors.append("Result executed_queries differs from the submitted query log.")

    status = result.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"Invalid status '{status}'.")

    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATS:
        if pat.search(serialized):
            errors.append(f"Placeholder token matching {pat.pattern} found in result.")

    # Build source paragraph & table lookup from corpus
    para_lookup: Dict[tuple, Dict[str, Any]] = {}
    paper_meta_expected: Dict[str, Dict[str, Any]] = {}
    for p in corpus.get("papers", []):
        sid = p["source_id"]
        paper_meta_expected[sid] = p
        for para in p.get("paragraphs", []):
            para_lookup[(sid, para["paragraph_id"])] = para
        for tab in p.get("tables", []):
            para_lookup[(sid, tab["table_id"])] = {
                "location_type": "pdf_table",
                "pdf_page": tab["pages"][0] if tab.get("pages") else None,
                "text": tab.get("markdown") or "",
                "table": tab,
            }

    # 1. Per-paper provenance isolation check (root DOI/title must not leak across papers)
    s_manifest = kg.get("sources_manifest", [])
    if len(s_manifest) >= 2:
        dois = [m.get("doi") for m in s_manifest if m.get("doi") and m.get("doi") != "not_reported"]
        if len(dois) != len(set(dois)):
            errors.append(f"Per-paper provenance isolation failed: duplicate DOI across sources_manifest {dois}.")
        for sm in s_manifest:
            exp = paper_meta_expected.get(sm.get("source_id", ""))
            if exp and exp.get("doi") != "not_reported" and sm.get("doi") != exp.get("doi"):
                errors.append(f"DOI mismatch for {sm.get('source_id')}: expected '{exp.get('doi')}', got '{sm.get('doi')}'.")

    # 2. Anchor grounding & no-fake-pdf_page check across nodes and edges
    anchors_checked = 0
    anchors_valid = 0
    fake_page_errors: List[str] = []
    ungrounded_errors: List[str] = []

    def _check_anchor(anc: Dict[str, Any], ctx: str) -> None:
        nonlocal anchors_checked, anchors_valid
        anchors_checked += 1
        sid = anc.get("source_id")
        pid = anc.get("paragraph_id")
        ltype = anc.get("location_type")
        pdf_p = anc.get("pdf_page")

        if ltype not in ("pdf_block", "pdf_table") and pdf_p is not None:
            fake_page_errors.append(f"{ctx}: non-PDF location_type '{ltype}' must have pdf_page=None, got {pdf_p}.")
            return

        src_para = para_lookup.get((sid, pid))
        if src_para is None:
            ungrounded_errors.append(f"{ctx}: ungrounded (source_id={sid}, paragraph_id={pid}).")
            return

        if ltype != src_para.get("location_type") or pdf_p != src_para.get("pdf_page"):
            ungrounded_errors.append(f"{ctx}: location mismatch in ({sid}, {pid}).")
            return
        snip = _flat(anc.get("quote_snippet", ""))
        if not snip:
            ungrounded_errors.append(f"{ctx}: empty quote in ({sid}, {pid}).")
            return
        if ltype == "pdf_table":
            tab = src_para["table"]
            cols = tab.get("col_paths", [])
            matches_cell = bool(cols) and any(
                snip == _flat(f"{tab.get('caption') or tab.get('label')} | {cols[0]}={str(row.get(cols[0], '')).strip()}, {col}={str(row.get(col, '')).strip()}"[:240])
                for row in tab.get("records", []) for col in cols[1:]
            )
        else:
            matches_cell = False
        if not matches_cell and snip not in _flat(src_para["text"]):
            ungrounded_errors.append(f"{ctx}: quote is not verbatim in ({sid}, {pid}).")
            return

        anchors_valid += 1

    for n in kg.get("nodes", []):
        if not n.get("evidence_anchors"):
            ungrounded_errors.append(f"node:{n['node_id']}: missing evidence.")
        for a in n.get("evidence_anchors", []):
            _check_anchor(a, f"node:{n['node_id']}")
    for b in kg.get("boundary_records", []):
        _check_anchor(b, f"boundary:{b.get('source_id')}/{b.get('paragraph_id')}")

    node_map = {n["node_id"]: n for n in kg.get("nodes", [])}
    parent = {e["source_node"]: e["target_node"] for e in kg.get("edges", []) if e["relation_type"] == "instance_of"}
    for e in kg.get("edges", []):
        _check_anchor(e, f"edge:{e['edge_id']}")
        for endpoint in ("source_node", "target_node"):
            node = node_map.get(e[endpoint], {})
            if not node or e["source_id"] not in node.get("source_ids", []):
                errors.append(f"edge:{e['edge_id']}: missing {endpoint} or unrelated source.")
                continue
            concept = node_map.get(parent.get(node["node_id"]), node)
            names = [*concept.get("aliases", []), concept["label"]]
            # Input tables use key=value while display labels insert spaces around '='.
            quote = re.sub(r"\s*=\s*", "=", _flat(e["quote_snippet"])).casefold()
            if not any(re.sub(r"\s*=\s*", "=", _flat(name)).casefold() in quote for name in names):
                errors.append(f"edge:{e['edge_id']}: {endpoint} not mentioned in the cited span.")

    errors.extend(fake_page_errors[:10])
    errors.extend(ungrounded_errors[:10])

    # Replay queries against the same graph and count different evidence-bearing questions.
    answered = set()
    covered_sources = set()
    query_ids = set()
    for q in queries:
        query_id = q.get("query_id")
        if not query_id or query_id in query_ids:
            errors.append(f"Duplicate or missing query ID: {query_id}.")
            continue
        query_ids.add(query_id)
        params = q.get("parameters")
        if params is None:  # Older CLI logs place input fields directly in the result.
            params = {"query_type": q.get("query_type")}
            for name in ("entity_a", "entity_b", "source_id"):
                if q.get(name) is not None:
                    params[name] = q[name]
            if q.get("query_type") == "entity_neighborhood":
                params["entity"] = q.get("target_node", {}).get("node_id") or q.get("target")
        try:
            replay = execute_query(kg, query_id=query_id, **params)
        except (KeyError, TypeError, AttributeError) as exc:
            errors.append(f"{query_id}: invalid graph or query parameters: {exc}")
            continue
        if replay != {k: v for k, v in q.items() if k != "parameters"}:
            errors.append(f"{query_id}: answer differs from graph replay.")
            continue
        evidence = []
        for field in ("subgraph_edges", "comparison_rows", "evidence_edges", "boundary_evidence"):
            evidence.extend(replay.get(field, []))
        for node in replay.get("shared_concepts", []):
            evidence.extend(node.get("evidence_anchors", []))
        if evidence and replay.get("answer_status") == "supported":
            answered.add(json.dumps({k: v for k, v in params.items() if v is not None}, sort_keys=True))
            covered_sources.update(row["source_id"] for row in evidence)
    graph_sources = {e["source_id"] for e in kg.get("edges", []) if e["relation_type"] != "instance_of"}
    if graph_sources and (len(answered) < 3 or not graph_sources.issubset(covered_sources)):
        errors.append("Expected three distinct evidence-backed queries covering every graph source.")
    if status == "success":
        missing = {p["source_id"] for p in corpus.get("papers", []) if p.get("paragraphs")} - graph_sources
        if missing:
            errors.append(f"No substantive graph edges from sources: {sorted(missing)}.")

    # 4. GraphML XML well-formedness
    graphml_valid = None
    if graphml_text is not None:
        try:
            root = ET.fromstring(graphml_text)
            ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
            xml_edges = {e.get("id"): (e.get("source"), e.get("target")) for e in root.findall(".//g:edge", ns)}
            xml_nodes = {n.get("id") for n in root.findall(".//g:node", ns)}
            expected_edges = {e["edge_id"]: (e["source_node"], e["target_node"]) for e in kg.get("edges", [])}
            if root.tag != "{http://graphml.graphdrawing.org/xmlns}graphml" or xml_edges != expected_edges or xml_nodes != set(node_map):
                raise ValueError("GraphML nodes or directed edge endpoints differ from the graph.")
            graphml_valid = True
        except Exception as exc:
            graphml_valid = False
            errors.append(f"Invalid GraphML XML: {exc}")

    # 5. Incremental preservation check
    base_preserved = None
    if base_kg is not None:
        cur_nids = {n["node_id"] for n in kg.get("nodes", [])}
        missing = [n["node_id"] for n in base_kg.get("nodes", []) if n["node_id"] not in cur_nids]
        base_preserved = len(missing) == 0
        if missing:
            errors.append(f"Incremental update dropped base_kg node IDs: {missing[:10]}.")

    grounding_rate = round(anchors_valid / anchors_checked, 4) if anchors_checked > 0 else 1.0
    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "status": status,
            "sources_count": len(s_manifest),
            "node_count": len(kg.get("nodes", [])),
            "edge_count": len(kg.get("edges", [])),
            "anchors_checked": anchors_checked,
            "anchors_valid": anchors_valid,
            "evidence_grounding_rate": grounding_rate,
            "executed_queries_count": len(queries),
            "supported_distinct_queries": len(answered),
            "query_evidence_sources": sorted(covered_sources),
            "graphml_valid": graphml_valid,
            "base_node_ids_preserved": base_preserved,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate KG deliverables against revised provenance & grounding rules.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--kg", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--graphml", default=None)
    parser.add_argument("--base-kg", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    res_data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    qobj = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    gml = Path(args.graphml).read_text(encoding="utf-8") if args.graphml and Path(args.graphml).exists() else None
    base = json.loads(Path(args.base_kg).read_text(encoding="utf-8")) if args.base_kg and Path(args.base_kg).exists() else None

    rep = validate_kg(case_data, res_data, kg, corpus, qobj.get("queries", []), graphml_text=gml, base_kg=base)
    write_json_atomic(args.out, rep)
    print(json.dumps(rep, ensure_ascii=False))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
