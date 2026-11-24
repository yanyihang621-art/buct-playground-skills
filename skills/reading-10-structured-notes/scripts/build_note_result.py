#!/usr/bin/env python3
"""Build contract-compliant /app/output/result.json for structured literature notes."""

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


def _collect_sources(docs: List[Dict[str, Any]], max_items: int = 250) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for p_idx, doc in enumerate(docs, start=1):
        pid = f"paper_{p_idx}"
        for blk in doc.get("blocks", [])[:max_items]:
            sources.append(
                {
                    "paper_id": pid,
                    "element_id": blk["block_id"],
                    "kind": blk.get("role", "body"),
                    "pdf_page": blk["page"],
                    "printed_page": blk.get("printed_page"),
                    "bbox": blk["bbox"],
                    "section_id": blk.get("section_id"),
                }
            )
        for tab in doc.get("tables", []):
            sources.append(
                {
                    "paper_id": pid,
                    "element_id": tab["table_id"],
                    "kind": "table",
                    "pdf_page": tab["pages"][0] if tab.get("pages") else 1,
                    "bbox": tab["bbox"],
                    "section_id": tab.get("section_id"),
                }
            )
    return sources


def build_note_result(
    case_data: Dict[str, Any],
    notes_bundle: Dict[str, Any],
    docs: Optional[List[Dict[str, Any]]] = None,
    markdown_text: Optional[str] = None,
    comparison_md: Optional[str] = None,
    override_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble final /app/output/result.json matching /input/case.json output_contract."""
    case_id = case_data.get("case_id", "unknown_case")
    contract_keys = _extract_contract_keys(case_data)
    papers = notes_bundle.get("papers", [])
    primary = papers[0] if papers else {}
    docs = docs or []
    primary_doc = docs[0] if docs else {}

    sources = _collect_sources(docs)
    all_sections = primary.get("section_notes", [])
    all_ke = [ke for p in papers for ke in p.get("knowledge_entries", [])]
    all_terms = [g for p in papers for g in p.get("terminology_glossary", [])]
    all_conflicts = [c for p in papers for c in p.get("conflicts", [])]
    all_cards = [p.get("paper_card", {}) for p in papers]
    primary_card = primary.get("paper_card", {})
    all_anomalies = [a for d in docs for a in d.get("diagnostics", {}).get("anomalies", [])]
    all_changes = [c for d in docs for c in d.get("change_log", [])]

    md_val = markdown_text or ""
    field_catalog: Dict[str, Any] = {
        "case_id": case_id,
        "status": notes_bundle.get("status", "partial"),
        "paper_count": notes_bundle.get("paper_count", len(papers)),
        "paper_card": primary_card,
        "paper_cards": all_cards,
        "papers": papers,
        "section_notes": all_sections,
        "section_summaries": all_sections,
        "sections": primary_doc.get("sections", all_sections),
        "knowledge_entries": all_ke,
        "knowledge_points": all_ke,
        "terminology_glossary": all_terms,
        "terminology": all_terms,
        "conflicts": all_conflicts,
        "search_index": notes_bundle.get("search_index", {}),
        "comparison_matrix": notes_bundle.get("comparison_matrix", []),
        "comparison_markdown": comparison_md or "",
        "markdown": md_val,
        "notes_markdown": md_val,
        "summary": primary_card.get("research_question", {}).get("summary", "not_reported"),
        "metadata": primary_doc.get("metadata", primary_card.get("bibliographic", {})),
        "tables": primary_doc.get("tables", []),
        "figures": primary_doc.get("figures", []),
        "formulas": primary_doc.get("formulas", {}),
        "references": primary_doc.get("references", []),
        "sources": sources,
        "evidence": sources,
        "provenance": sources,
        "verification_ledger": notes_bundle.get("verification_ledger", {}),
        "uncertainties": list(notes_bundle.get("uncertainties", [])),
        "limitations": primary_card.get("limitations_and_threats_to_validity", {}),
        "anomalies": all_anomalies,
        "change_log": all_changes,
    }

    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": notes_bundle.get("status", "partial"),
        "paper_count": notes_bundle.get("paper_count", len(papers)),
        "paper_card": primary_card,
        "paper_cards": all_cards,
        "section_notes": all_sections,
        "knowledge_entries": all_ke,
        "terminology_glossary": all_terms,
        "conflicts": all_conflicts,
        "search_index": notes_bundle.get("search_index", {}),
        "comparison_matrix": notes_bundle.get("comparison_matrix", []),
        "sources": sources,
        "verification_ledger": notes_bundle.get("verification_ledger", {}),
        "uncertainties": list(notes_bundle.get("uncertainties", [])),
        "anomalies": all_anomalies,
        "change_log": all_changes,
    }
    if md_val:
        result["notes_markdown"] = md_val

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
    parser = argparse.ArgumentParser(description="Build /app/output/result.json for structured literature notes.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--notes", required=True, help="Path to structured_notes.json.")
    parser.add_argument("--manifest", default=None, help="Optional ingest_manifest.json to collect document.json sources.")
    parser.add_argument("--markdown", default=None, help="Optional structured_notes.md path.")
    parser.add_argument("--comparison-md", default=None, help="Optional comparison_matrix.md path.")
    parser.add_argument("--override-json", default=None, help="Optional override JSON patch.")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    notes_bundle = json.loads(Path(args.notes).read_text(encoding="utf-8"))

    docs: List[Dict[str, Any]] = []
    if args.manifest and Path(args.manifest).exists():
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        for item in man.get("papers", []):
            dp = Path(item["document_json"])
            if dp.exists():
                docs.append(json.loads(dp.read_text(encoding="utf-8")))

    md_txt = Path(args.markdown).read_text(encoding="utf-8") if args.markdown and Path(args.markdown).exists() else None
    comp_md = Path(args.comparison_md).read_text(encoding="utf-8") if args.comparison_md and Path(args.comparison_md).exists() else None
    override = json.loads(Path(args.override_json).read_text(encoding="utf-8")) if args.override_json else None

    res = build_note_result(
        case_data,
        notes_bundle,
        docs=docs,
        markdown_text=md_txt,
        comparison_md=comp_md,
        override_data=override,
    )
    write_json_atomic(args.out, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
