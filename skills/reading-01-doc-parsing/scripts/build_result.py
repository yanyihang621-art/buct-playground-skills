#!/usr/bin/env python3
"""Build contract-compliant /app/output/result.json from case.json and parsed document.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_contract import contract_fields, output_field_type, record_unresolved_fields, strict_fields
from pdf_utils import write_json_atomic


def _extract_contract_keys(case_data: Dict[str, Any]) -> List[str]:
    return contract_fields(case_data)


def _collect_sources(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for blk in doc.get("blocks", []):
        sources.append(
            {
                "element_id": blk["block_id"],
                "kind": blk.get("role", "body"),
                "pdf_page": blk["page"],
                "printed_page": blk.get("printed_page"),
                "bbox": blk["bbox"],
                "section_id": blk.get("section_id"),
                "excerpt": blk.get("text", "")[:200],
            }
        )
    for tab in doc.get("tables", []):
        sources.append(
            {
                "element_id": tab["table_id"],
                "kind": "table",
                "pdf_page": tab["pages"][0] if tab.get("pages") else 1,
                "pages": tab.get("pages", []),
                "bbox": tab["bbox"],
                "bboxes_by_page": tab.get("bboxes_by_page", {}),
                "excerpt": (tab.get("caption") or "")[:200],
                "section_id": tab.get("section_id"),
            }
        )
    for eq in doc.get("formulas", {}).get("display_equations", []):
        sources.append(
            {
                "element_id": eq["eq_id"],
                "kind": "formula",
                "pdf_page": eq["page"],
                "printed_page": eq.get("printed_page"),
                "bbox": eq["bbox"],
                "section_id": eq.get("section_id"),
                "excerpt": (eq.get("structured_text") or "")[:200],
            }
        )
    for fig in doc.get("figures", []):
        if fig.get("caption_bbox"):
            sources.append({"element_id": fig["figure_id"], "kind": "figure_caption",
                            "pdf_page": fig["page"], "printed_page": fig.get("printed_page"),
                            "bbox": fig["caption_bbox"], "excerpt": fig.get("caption", "")[:200]})
    return sources


def build_result(
    case_data: Dict[str, Any],
    doc: Dict[str, Any],
    markdown_text: Optional[str] = None,
    override_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble final result.json aligning parsed document data to case output_contract."""
    case_id = case_data.get("case_id", "unknown_case")
    contract_keys = _extract_contract_keys(case_data)

    meta = doc.get("metadata", {})
    sources = _collect_sources(doc)
    md_val = markdown_text if markdown_text is not None else ""
    plain_paragraphs = [
        {
            "block_id": b["block_id"],
            "role": b["role"],
            "page": b["page"],
            "printed_page": b.get("printed_page"),
            "bbox": b["bbox"],
            "section_id": b.get("section_id"),
            "text": b["structured_text"],
        }
        for b in doc.get("blocks", [])
    ]

    summary_obj = {
        "title": meta.get("title"),
        "page_count": doc.get("diagnostics", {}).get("page_count", 0),
        "block_count": len(doc.get("blocks", [])),
        "table_count": len(doc.get("tables", [])),
        "formula_count": len(doc.get("formulas", {}).get("display_equations", [])),
        "figure_count": len(doc.get("figures", [])),
        "reference_count": len(doc.get("references", [])),
        "status": doc.get("status", "partial"),
    }

    field_catalog: Dict[str, Any] = {
        "case_id": case_id,
        "status": doc.get("status", "partial"),
        "source_file": doc.get("source_file"),
        "summary": summary_obj,
        "metadata": meta,
        "title": meta.get("title"),
        "authors": meta.get("authors", []),
        "affiliations": meta.get("affiliations", []),
        "abstract": meta.get("abstract"),
        "keywords": meta.get("keywords", []),
        "sections": doc.get("sections", []),
        "blocks": doc.get("blocks", []),
        "paragraphs": plain_paragraphs,
        "full_text": md_val or "\n\n".join(b["structured_text"] for b in doc.get("blocks", [])),
        "markdown": md_val or "\n\n".join(b["structured_text"] for b in doc.get("blocks", [])),
        "tables": doc.get("tables", []),
        "figures": doc.get("figures", []),
        "formulas": doc.get("formulas", {}).get("display_equations", []),
        "equations": doc.get("formulas", {}).get("display_equations", []),
        "inline_formulas": doc.get("formulas", {}).get("inline_formula_spans", []),
        "references": doc.get("references", []),
        "footnotes": doc.get("footnotes", []),
        "headers_footers": doc.get("headers_footers", []),
        "marginalia": doc.get("marginalia", []),
        "diagnostics": doc.get("diagnostics", {}),
        "evidence": sources,
        "sources": sources,
        "provenance": sources,
        "uncertainties": list(doc.get("uncertainties", [])),
        "limitations": list(doc.get("uncertainties", [])),
        "anomalies": doc.get("diagnostics", {}).get("anomalies", []),
        "change_log": doc.get("change_log", []),
        "parsed_document": {
            "schema_version": doc.get("schema_version"),
            "sections": doc.get("sections", []),
            "tables": doc.get("tables", []),
            "figures": doc.get("figures", []),
            "formulas": doc.get("formulas", {}),
            "references": doc.get("references", []),
        },
    }

    # Always emit core provenance & status fields
    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": doc.get("status", "partial"),
        "metadata": meta,
        "sections": doc.get("sections", []),
        "tables": doc.get("tables", []),
        "figures": doc.get("figures", []),
        "formulas": doc.get("formulas", {}),
        "references": doc.get("references", []),
        "sources": sources,
        "uncertainties": list(doc.get("uncertainties", [])),
        "anomalies": doc.get("diagnostics", {}).get("anomalies", []),
        "change_log": doc.get("change_log", []),
    }

    # Populate all requested contract fields
    for key in contract_keys:
        if key == "formulas" and output_field_type(case_data, key) == "object":
            result[key] = doc.get("formulas", {})
        elif key in field_catalog:
            result[key] = field_catalog[key]
        else:
            result.setdefault(key, None)

    if override_data:
        for k, v in override_data.items():
            result[k] = v

    record_unresolved_fields(case_data, result, field_catalog, override_data or {})
    result["case_id"] = case_id
    return result


def publish_result(path, case, result):
    """Deliver only declared keys for a closed schema; keep the full source index adjacent."""
    path = Path(path)
    allowed = strict_fields(case)
    if allowed is not None:
        write_json_atomic(path.with_name("parse_manifest.json"), result)
        result = {key: value for key, value in result.items() if key in allowed}
    write_json_atomic(path, result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build /app/output/result.json matching case.json contract.")
    parser.add_argument("--case", required=True, help="Path to /input/case.json.")
    parser.add_argument("--parsed", required=True, help="Path to parsed document.json.")
    parser.add_argument("--out", required=True, help="Output result.json path.")
    parser.add_argument("--markdown", default=None, help="Optional full_text.md path.")
    parser.add_argument("--override-json", default=None, help="Optional JSON patch with task-specific answers.")
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    doc_data = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    md_text = Path(args.markdown).read_text(encoding="utf-8") if args.markdown and Path(args.markdown).exists() else None
    override = json.loads(Path(args.override_json).read_text(encoding="utf-8")) if args.override_json else None

    res = build_result(case_data, doc_data, markdown_text=md_text, override_data=override)
    publish_result(args.out, case_data, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
