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

VALID_STATUSES = {"success", "partial", "unverifiable", "refused"}
PLACEHOLDER_PATS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]


def _norm_words(s: str) -> List[str]:
    return re.findall(r"[a-z0-9\u4e00-\u9fff]+", s.lower())


def _required_keys(case_data: Dict[str, Any]) -> List[str]:
    contract = case_data.get("output_contract")
    if not contract:
        return ["case_id", "status"]
    if isinstance(contract, list):
        return [str(k) for k in contract]
    if isinstance(contract, dict):
        if "required" in contract and isinstance(contract["required"], list):
            return [str(k) for k in contract["required"]]
        return [str(k) for k in contract.keys()]
    return ["case_id", "status"]


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

    for rk in _required_keys(case_data):
        if rk not in result:
            errors.append(f"Missing required output_contract key '{rk}'.")

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
                "text": f"{tab.get('caption') or ''} {tab.get('label') or ''} {tab.get('markdown') or ''}",
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

        snip = anc.get("quote_snippet", "")
        if snip:
            sw = set(_norm_words(snip))
            tw = set(_norm_words(src_para["text"]))
            ov = len(sw & tw) / max(1, len(sw))
            if ov < 0.72:
                ungrounded_errors.append(f"{ctx}: quote_snippet not grounded in ({sid}, {pid}), overlap={ov:.2f}.")
                return

        anchors_valid += 1

    for n in kg.get("nodes", []):
        for a in n.get("evidence_anchors", []):
            _check_anchor(a, f"node:{n['node_id']}")

    for e in kg.get("edges", []):
        _check_anchor(e, f"edge:{e['edge_id']}")

    errors.extend(fake_page_errors[:10])
    errors.extend(ungrounded_errors[:10])

    # 3. Executed queries check (>= 3 when KG is non-empty)
    if kg.get("nodes") and len(queries) < 3:
        errors.append(f"Expected >= 3 executed queries, got {len(queries)}.")

    # 4. GraphML XML well-formedness
    graphml_valid = None
    if graphml_text is not None:
        try:
            ET.fromstring(graphml_text)
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
