#!/usr/bin/env python3
"""Build contract-compliant /app/output/result.json for Nanopublications."""

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


def _collect_sources(bundle: Dict[str, Any], docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for np_obj in bundle.get("nanopublications", []):
        loc = np_obj["provenance"].get("location", {})
        eid = loc.get("block_id") or loc.get("table_id") or np_obj["nanopub_id"]
        if eid not in seen:
            seen.add(eid)
            sources.append(
                {
                    "element_id": eid,
                    "kind": "nanopub_evidence",
                    "pdf_page": loc.get("pdf_page", 1),
                    "printed_page": loc.get("printed_page"),
                    "bbox": loc.get("bbox", [0, 0, 0, 0]),
                    "section_id": loc.get("section_id"),
                }
            )
    if not sources and docs:
        for b in docs[0].get("blocks", [])[:50]:
            sources.append(
                {
                    "element_id": b["block_id"],
                    "kind": b.get("role", "body"),
                    "pdf_page": b["page"],
                    "printed_page": b.get("printed_page"),
                    "bbox": b["bbox"],
                }
            )
    return sources


def build_nanopub_result(
    case_data: Dict[str, Any],
    bundle: Dict[str, Any],
    docs: Optional[List[Dict[str, Any]]] = None,
    trig_text: Optional[str] = None,
    jsonld_obj: Optional[Dict[str, Any]] = None,
    nq_text: Optional[str] = None,
    md_text: Optional[str] = None,
    override_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble final result.json aligning Nanopublication bundle to case output_contract."""
    case_id = case_data.get("case_id", "unknown_case")
    contract_keys = _extract_contract_keys(case_data)
    docs = docs or []
    sources = _collect_sources(bundle, docs)
    anomalies = [a for d in docs for a in d.get("diagnostics", {}).get("anomalies", [])]
    changes = [c for d in docs for c in d.get("change_log", [])]

    nps = bundle.get("nanopublications", [])
    field_catalog: Dict[str, Any] = {
        "case_id": case_id,
        "status": bundle.get("status", "partial"),
        "nanopub_count": bundle.get("nanopub_count", len(nps)),
        "nanopublications": nps,
        "assertions": [
            {
                "nanopub_id": n["nanopub_id"],
                "nanopub_uri": n["nanopub_uri"],
                **n["assertion"],
                "evidence_type": n["provenance"]["evidence_type"],
                "minimal_evidence_snippet": n["provenance"]["minimal_evidence_snippet"],
                "location": n["provenance"]["location"],
                "confidence_score": n["pubinfo"]["confidence_score"],
            }
            for n in nps
        ],
        "source_metadata": bundle.get("source_metadata", {}),
        "deduplication_log": bundle.get("deduplication_log", []),
        "conflicts": bundle.get("conflicts", []),
        "trig": trig_text or "",
        "jsonld": jsonld_obj or {},
        "nquads": nq_text or "",
        "markdown": md_text or "",
        "sources": sources,
        "evidence": sources,
        "provenance": sources,
        "uncertainties": list(bundle.get("uncertainties", [])),
        "anomalies": anomalies,
        "change_log": changes,
    }

    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": bundle.get("status", "partial"),
        "nanopub_count": bundle.get("nanopub_count", len(nps)),
        "source_metadata": bundle.get("source_metadata", {}),
        "nanopublications": nps,
        "deduplication_log": bundle.get("deduplication_log", []),
        "conflicts": bundle.get("conflicts", []),
        "sources": sources,
        "uncertainties": list(bundle.get("uncertainties", [])),
        "anomalies": anomalies,
        "change_log": changes,
    }

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
    parser = argparse.ArgumentParser(description="Build /app/output/result.json for Nanopublications.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--nanopubs", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--trig", default=None)
    parser.add_argument("--jsonld", default=None)
    parser.add_argument("--nquads", default=None)
    parser.add_argument("--markdown", default=None)
    parser.add_argument("--override-json", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    bundle = json.loads(Path(args.nanopubs).read_text(encoding="utf-8"))

    docs: List[Dict[str, Any]] = []
    if args.manifest and Path(args.manifest).exists():
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        for item in man.get("papers", []):
            dp = Path(item["document_json"])
            if dp.exists():
                docs.append(json.loads(dp.read_text(encoding="utf-8")))

    trig_t = Path(args.trig).read_text(encoding="utf-8") if args.trig and Path(args.trig).exists() else None
    jsonld_o = json.loads(Path(args.jsonld).read_text(encoding="utf-8")) if args.jsonld and Path(args.jsonld).exists() else None
    nq_t = Path(args.nquads).read_text(encoding="utf-8") if args.nquads and Path(args.nquads).exists() else None
    md_t = Path(args.markdown).read_text(encoding="utf-8") if args.markdown and Path(args.markdown).exists() else None
    ovr = json.loads(Path(args.override_json).read_text(encoding="utf-8")) if args.override_json else None

    res = build_nanopub_result(
        case_data,
        bundle,
        docs=docs,
        trig_text=trig_t,
        jsonld_obj=jsonld_o,
        nq_text=nq_t,
        md_text=md_t,
        override_data=ovr,
    )
    write_json_atomic(args.out, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
