#!/usr/bin/env python3
"""Token-efficient query CLI for inspecting parsed document.json without blowing context."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


def _flatten_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sec in sections:
        out.append(sec)
        out.extend(_flatten_sections(sec.get("children", [])))
    return out


def _truncate_output(payload: Any, max_chars: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 64] + "\n... [TRUNCATED to fit --max-chars] ...\n"


def query_parsed(
    doc: Dict[str, Any],
    mode: str,
    target_id: str | None = None,
    page: int | None = None,
    query: str | None = None,
) -> Any:
    """Return a targeted slice of parsed document.json."""
    if mode == "summary":
        return {
            "status": doc.get("status"),
            "source_file": doc.get("source_file"),
            "page_count": doc.get("diagnostics", {}).get("page_count"),
            "metadata": doc.get("metadata"),
            "counts": {
                "sections": len(_flatten_sections(doc.get("sections", []))),
                "blocks": len(doc.get("blocks", [])),
                "tables": len(doc.get("tables", [])),
                "figures": len(doc.get("figures", [])),
                "display_equations": len(doc.get("formulas", {}).get("display_equations", [])),
                "references": len(doc.get("references", [])),
            },
            "uncertainties": doc.get("uncertainties", []),
        }

    if mode == "sections":
        flat = _flatten_sections(doc.get("sections", []))
        return [
            {
                "section_id": s["section_id"],
                "level": s["level"],
                "number": s.get("number"),
                "title": s["title"],
                "pages": [s["page_start"], s["page_end"]],
                "block_count": len(s.get("block_ids", [])),
            }
            for s in flat
        ]

    if mode == "section":
        if not target_id:
            return {"error": "--id is required for mode=section"}
        flat = _flatten_sections(doc.get("sections", []))
        sec = next((s for s in flat if s["section_id"] == target_id), None)
        if sec is None:
            return {"error": f"Section '{target_id}' not found"}
        bids = set(sec.get("block_ids", []))
        sec_blocks = [
            {
                "block_id": b["block_id"],
                "role": b["role"],
                "page": b["page"],
                "printed_page": b.get("printed_page"),
                "bbox": b["bbox"],
                "text": b["structured_text"],
            }
            for b in doc.get("blocks", [])
            if b["block_id"] in bids
        ]
        return {"section": sec, "blocks": sec_blocks}

    if mode == "page":
        if page is None:
            return {"error": "--page is required for mode=page"}
        return {
            "page": page,
            "profile": next((p for p in doc.get("diagnostics", {}).get("page_profiles", []) if p["page"] == page), None),
            "blocks": [
                {
                    "block_id": b["block_id"],
                    "role": b["role"],
                    "column": b.get("column"),
                    "bbox": b["bbox"],
                    "text": b["structured_text"],
                }
                for b in doc.get("blocks", [])
                if b["page"] == page
            ],
            "headers_footers": [h for h in doc.get("headers_footers", []) if h["page"] == page],
        }

    if mode == "tables":
        if target_id:
            return next((t for t in doc.get("tables", []) if t["table_id"] == target_id), {"error": f"Table '{target_id}' not found"})
        return doc.get("tables", [])

    if mode == "formulas":
        return doc.get("formulas", {})

    if mode == "figures":
        return doc.get("figures", [])

    if mode == "references":
        return doc.get("references", [])

    if mode == "anomalies":
        return {
            "status": doc.get("status"),
            "anomalies": doc.get("diagnostics", {}).get("anomalies", []),
            "uncertainties": doc.get("uncertainties", []),
            "change_log_count": len(doc.get("change_log", [])),
        }

    if mode == "search":
        if not query:
            return {"error": "--query is required for mode=search"}
        pat = re.compile(query, flags=re.IGNORECASE)
        hits = []
        for b in doc.get("blocks", []):
            if pat.search(b.get("structured_text", "")) or pat.search(b.get("text", "")):
                hits.append(
                    {
                        "kind": "block",
                        "block_id": b["block_id"],
                        "page": b["page"],
                        "printed_page": b.get("printed_page"),
                        "bbox": b["bbox"],
                        "section_path": b.get("section_path", []),
                        "snippet": b["structured_text"][:400],
                    }
                )
        for t in doc.get("tables", []):
            if pat.search(t.get("caption") or "") or pat.search(t.get("markdown") or ""):
                hits.append(
                    {
                        "kind": "table",
                        "table_id": t["table_id"],
                        "pages": t["pages"],
                        "bbox": t["bbox"],
                        "caption": t.get("caption"),
                    }
                )
        return {"query": query, "hit_count": len(hits), "hits": hits}

    return {"error": f"Unknown mode '{mode}'"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Query structured elements from document.json.")
    parser.add_argument("--parsed", required=True, help="Path to document.json.")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("summary", "sections", "section", "page", "tables", "formulas", "figures", "references", "anomalies", "search"),
    )
    parser.add_argument("--id", default=None, help="Section or table ID.")
    parser.add_argument("--page", type=int, default=None, help="1-based PDF page number.")
    parser.add_argument("--query", default=None, help="Regex or substring for mode=search.")
    parser.add_argument("--max-chars", type=int, default=6000, help="Max output characters.")
    args = parser.parse_args()

    doc = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    res = query_parsed(doc, mode=args.mode, target_id=args.id, page=args.page, query=args.query)
    print(_truncate_output(res, args.max_chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
