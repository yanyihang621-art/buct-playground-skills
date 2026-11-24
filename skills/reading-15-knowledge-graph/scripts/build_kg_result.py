#!/usr/bin/env python3
"""Build contract-compliant /app/output/result.json for Literature Knowledge Graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic


def _extract_contract_keys(case_data: Dict[str, Any]) -> List[str]:
    contract = case_data.get("output_contract")
    if not contract:
        return []
    if isinstance(contract, list):
        return [str(k) for k in contract]
    if isinstance(contract, dict):
        if "properties" in contract and isinstance(contract["properties"], dict):
            keys = list(contract["properties"].keys())
            for req in contract.get("required", []) or []:
                if req not in keys:
                    keys.append(str(req))
            return keys
        if "required" in contract and isinstance(contract["required"], list):
            return [str(k) for k in contract["required"]]
        return [str(k) for k in contract.keys()]
    return []


def _collect_sources(kg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for e in kg.get("edges", []):
        key = (e["source_id"], e["paragraph_id"])
        if key not in seen:
            seen.add(key)
            sources.append(
                {
                    "source_id": e["source_id"],
                    "paragraph_id": e["paragraph_id"],
                    "location_type": e["location_type"],
                    "pdf_page": e.get("pdf_page"),
                    "quote_snippet": e.get("quote_snippet", "")[:160],
                }
            )
    for n in kg.get("nodes", []):
        for a in n.get("evidence_anchors", []):
            key = (a.get("source_id"), a.get("paragraph_id"))
            if key not in seen:
                seen.add(key)
                sources.append(
                    {
                        "source_id": a.get("source_id"),
                        "paragraph_id": a.get("paragraph_id"),
                        "location_type": a.get("location_type"),
                        "pdf_page": a.get("pdf_page"),
                        "quote_snippet": a.get("quote_snippet", "")[:160],
                    }
                )
    return sources


def build_kg_result(
    case_data: Dict[str, Any],
    kg: Dict[str, Any],
    queries: List[Dict[str, Any]],
    graphml_text: Optional[str] = None,
    markdown_text: Optional[str] = None,
    override_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble final /app/output/result.json matching /input/case.json output_contract."""
    case_id = case_data.get("case_id", "unknown_case")
    contract_keys = _extract_contract_keys(case_data)
    sources = _collect_sources(kg)

    field_catalog: Dict[str, Any] = {
        "case_id": case_id,
        "status": kg.get("status", "partial"),
        "sources_manifest": kg.get("sources_manifest", []),
        "nodes": kg.get("nodes", []),
        "edges": kg.get("edges", []),
        "entities": kg.get("nodes", []),
        "relations": kg.get("edges", []),
        "merge_decisions": kg.get("merge_decisions", []),
        "do_not_merge_records": kg.get("do_not_merge_records", []),
        "conflicts": kg.get("conflicts", []),
        "executed_queries": queries,
        "queries": queries,
        "incremental_changelog": kg.get("incremental_changelog"),
        "graphml": graphml_text or "",
        "markdown": markdown_text or "",
        "sources": sources,
        "evidence": sources,
        "provenance": sources,
        "uncertainties": list(kg.get("uncertainties", [])),
    }

    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": kg.get("status", "partial"),
        "sources_manifest": kg.get("sources_manifest", []),
        "nodes": kg.get("nodes", []),
        "edges": kg.get("edges", []),
        "merge_decisions": kg.get("merge_decisions", []),
        "do_not_merge_records": kg.get("do_not_merge_records", []),
        "conflicts": kg.get("conflicts", []),
        "executed_queries": queries,
        "sources": sources,
        "uncertainties": list(kg.get("uncertainties", [])),
    }
    if kg.get("incremental_changelog") is not None:
        result["incremental_changelog"] = kg["incremental_changelog"]
    if markdown_text:
        result["markdown"] = markdown_text

    for key in contract_keys:
        if key in field_catalog:
            result[key] = field_catalog[key]
        elif key not in result:
            result[key] = None
            result["uncertainties"].append(
                {
                    "code": "awaiting_task_synthesis",
                    "severity": "info",
                    "pages": [],
                    "message": f"Contract field '{key}' is not directly deterministically mapped and requires agent synthesis or override.",
                }
            )

    if override_data:
        for k, v in override_data.items():
            result[k] = v

    result["case_id"] = case_id
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build /app/output/result.json for Literature KG.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--kg", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--graphml", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--override-json", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    qobj = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    gml = Path(args.graphml).read_text(encoding="utf-8") if args.graphml and Path(args.graphml).exists() else None
    md = Path(args.markdown).read_text(encoding="utf-8") if args.markdown and Path(args.markdown).exists() else None
    ovr = json.loads(Path(args.override_json).read_text(encoding="utf-8")) if args.override_json else None

    res = build_kg_result(case_data, kg, qobj.get("queries", []), graphml_text=gml, markdown_text=md, override_data=ovr)
    write_json_atomic(args.out, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
