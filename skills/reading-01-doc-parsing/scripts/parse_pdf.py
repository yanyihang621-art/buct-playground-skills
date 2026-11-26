#!/usr/bin/env python3
"""Main orchestrator CLI for deterministic scientific PDF parsing."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from diagnose_pdf import diagnose_pdf
from extract_formulas import extract_formulas
from extract_tables import extract_tables
from layout_order import extract_ordered_blocks
from ocr_pipeline import run_ocr_on_image, run_ocr_on_page
from pdf_utils import write_json_atomic, write_text_lf
from structure_parser import parse_document_structure


def _render_markdown(doc_data: Dict[str, Any]) -> str:
    lines: List[str] = []
    meta = doc_data.get("metadata", {})
    title = meta.get("title") or Path(doc_data.get("source_file", "document.pdf")).name
    lines.append(f"# {title}")
    lines.append("")
    if meta.get("authors"):
        lines.append(f"**Authors:** {', '.join(meta['authors'])}")
    if meta.get("affiliations"):
        lines.append(f"**Affiliations:** {'; '.join(meta['affiliations'])}")
    if meta.get("keywords"):
        lines.append(f"**Keywords:** {', '.join(meta['keywords'])}")
    lines.append(f"**Status:** `{doc_data.get('status')}` | **Pages:** {doc_data.get('diagnostics', {}).get('page_count', 0)}")
    lines.append("")

    if doc_data.get("uncertainties"):
        lines.append("## Parsing Uncertainties & Anomalies")
        for u in doc_data["uncertainties"]:
            lines.append(f"- **[{u.get('severity', 'info')}] {u.get('code', 'uncertainty')}** (pages {u.get('pages', [])}): {u.get('message', '')}")
        lines.append("")

    if meta.get("abstract"):
        lines.append("## Abstract")
        lines.append(meta["abstract"])
        lines.append("")

    tables_by_id = {t["table_id"]: t for t in doc_data.get("tables", [])}
    eqs_by_block: Dict[str, List[Dict[str, Any]]] = {}
    for eq in doc_data.get("formulas", {}).get("display_equations", []):
        for bid in eq.get("block_ids", []):
            eqs_by_block.setdefault(bid, []).append(eq)

    emitted_eqs = set()
    lines.append("## Document Content (Reading Order)")
    lines.append("")
    for blk in doc_data.get("blocks", []):
        role = blk.get("role", "body")
        if role in ("title", "authors", "affiliation", "keywords"):
            continue
        prov = f"[p.{blk['page']} | {blk['block_id']} | col:{blk.get('column')} | bbox:{blk['bbox']}]"
        if role == "heading":
            lines.append(f"### {blk['text']}  \n<sub>{prov}</sub>")
            lines.append("")
        elif role == "table_ref":
            tid = blk.get("table_id")
            tab = tables_by_id.get(tid)
            if tab:
                cap = tab.get("caption") or tab.get("label") or tid
                lines.append(f"#### {cap}  \n<sub>[pages {tab.get('pages')} | {tid} | bbox:{tab.get('bbox')}]</sub>")
                if tab.get("markdown"):
                    lines.append(tab["markdown"])
                elif tab.get("ocr_text"):
                    lines.append(f"> *[Image Table OCR fallback]* {tab['ocr_text']}")
                if tab.get("notes"):
                    lines.append(f"*Notes:* {' '.join(tab['notes'])}")
                lines.append("")
        elif role == "footnote":
            lines.append(f"> *Footnote {prov}:* {blk['structured_text']}")
            lines.append("")
        else:
            eqs = eqs_by_block.get(blk["block_id"], [])
            if eqs:
                for eq in eqs:
                    if eq["eq_id"] not in emitted_eqs:
                        emitted_eqs.add(eq["eq_id"])
                        lines.append(f"$$\n{eq['structured_text']}\n$$\n<sub>[p.{eq['page']} | {eq['eq_id']} | bbox:{eq['bbox']}]</sub>")
                        lines.append("")
            else:
                lines.append(f"{blk['structured_text']}  \n<sub>{prov}</sub>")
                lines.append("")

    # Emit any image tables that did not have a table_ref anchor
    for tab in doc_data.get("tables", []):
        if tab.get("is_image_table"):
            lines.append(f"#### {tab.get('caption') or tab.get('label')} (Image Table)")
            lines.append(f"- Status: `{tab.get('status')}` | Pages: {tab.get('pages')} | BBox: {tab.get('bbox')}")
            if tab.get("crop_path"):
                lines.append(f"- Crop: `{tab['crop_path']}`")
            if tab.get("ocr_text"):
                lines.append(f"- OCR preview: {tab['ocr_text']}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_tables_html(tables: Sequence[Dict[str, Any]]) -> str:
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Extracted Tables</title>",
        "<style>body{font-family:sans-serif;margin:24px;}table{border-collapse:collapse;margin:12px 0;}th,td{border:1px solid #888;padding:6px 10px;text-align:left;}th{background:#f2f4f7;}</style>",
        "</head><body><h1>Extracted Tables</h1>",
    ]
    for tab in tables:
        cap = html_lib.escape(str(tab.get("caption") or tab.get("label") or tab["table_id"]))
        parts.append(f"<h2>{html_lib.escape(tab['table_id'])}: {cap} (Pages {tab.get('pages')})</h2>")
        if tab.get("html"):
            parts.append(tab["html"])
        else:
            parts.append(f"<p><em>Status: {html_lib.escape(str(tab.get('status')))}</em></p>")
        if tab.get("notes"):
            parts.append(f"<p><small>{html_lib.escape(' '.join(tab['notes']))}</small></p>")
    parts.append("</body></html>\n")
    return "\n".join(parts)


def _build_compact_summary(doc_data: Dict[str, Any]) -> Dict[str, Any]:
    def _trim_sec(s: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "section_id": s["section_id"],
            "level": s["level"],
            "number": s.get("number"),
            "title": s["title"],
            "page_start": s["page_start"],
            "page_end": s["page_end"],
            "block_count": len(s.get("block_ids", [])),
            "children": [_trim_sec(c) for c in s.get("children", [])],
        }

    summary: Dict[str, Any] = {
        "schema_version": doc_data["schema_version"],
        "source_file": doc_data["source_file"],
        "status": doc_data["status"],
        "page_count": doc_data["diagnostics"]["page_count"],
        "analyzed_pages": doc_data["diagnostics"]["analyzed_pages"],
        "encrypted": doc_data["diagnostics"]["encrypted"],
        "body_font_size": doc_data["diagnostics"]["body_font_size"],
        "metadata": {
            "title": doc_data["metadata"].get("title"),
            "authors": doc_data["metadata"].get("authors", [])[:12],
            "affiliations": doc_data["metadata"].get("affiliations", [])[:6],
            "abstract_preview": (doc_data["metadata"].get("abstract") or "")[:400],
            "keywords": doc_data["metadata"].get("keywords", [])[:12],
        },
        "sections_outline": [_trim_sec(s) for s in doc_data.get("sections", [])],
        "counts": {
            "blocks": len(doc_data.get("blocks", [])),
            "headers_footers": len(doc_data.get("headers_footers", [])),
            "marginalia": len(doc_data.get("marginalia", [])),
            "footnotes": len(doc_data.get("footnotes", [])),
            "tables": len(doc_data.get("tables", [])),
            "figures": len(doc_data.get("figures", [])),
            "display_equations": len(doc_data.get("formulas", {}).get("display_equations", [])),
            "references": len(doc_data.get("references", [])),
            "uncertainties": len(doc_data.get("uncertainties", [])),
            "change_log": len(doc_data.get("change_log", [])),
        },
        "anomalies": doc_data["diagnostics"].get("anomalies", []),
        "uncertainties": doc_data.get("uncertainties", [])[:12],
    }

    # Ensure compact size <= 8 KB
    raw = json.dumps(summary, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 7800:
        summary["sections_outline"] = summary["sections_outline"][:20]
        summary["uncertainties"] = summary["uncertainties"][:6]
    return summary


def run_parse_pipeline(
    pdf_path: str | Path,
    out_dir: str | Path,
    ocr_mode: str = "auto",
    save_crops: bool = False,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the complete deterministic PDF parsing pipeline and write output artifacts."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    crops_dir = (out_path / "crops") if save_crops else None

    diagnostics = diagnose_pdf(pdf_path, max_pages=max_pages)
    uncertainties: List[Dict[str, Any]] = []
    if 0 < diagnostics["analyzed_pages"] < diagnostics["page_count"]:
        first_missing = diagnostics["analyzed_pages"] + 1
        uncertainties.append({"code": "page_limit", "severity": "high", "pages": [],
                              "message": f"Pages {first_missing}–{diagnostics['page_count']} were outside this pass."})

    if diagnostics.get("encrypted") or diagnostics.get("corrupt") or not diagnostics.get("file_exists"):
        for anom in diagnostics.get("anomalies", []):
            uncertainties.append(
                {
                    "code": anom["code"],
                    "severity": anom["severity"],
                    "pages": anom["pages"],
                    "message": anom["detail"],
                }
            )
        doc_data: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "source_file": str(pdf_path),
            "status": "refused",
            "diagnostics": diagnostics,
            "metadata": {"title": None, "authors": [], "affiliations": [], "abstract": None, "keywords": [], "toc": []},
            "sections": [],
            "blocks": [],
            "headers_footers": [],
            "marginalia": [],
            "footnotes": [],
            "tables": [],
            "figures": [],
            "formulas": {"display_equations": [], "inline_formula_spans": [], "stats": {"display_count": 0, "inline_block_count": 0, "complex_2d_count": 0}},
            "references": [],
            "uncertainties": uncertainties,
            "change_log": [],
        }
    else:
        ocr_cb = None
        if ocr_mode != "off":
            def _img_ocr_cb(pil_img: Any, pnum: int, bbox: Sequence[float], save_p: Optional[Path]) -> Dict[str, Any]:
                return run_ocr_on_image(pil_img, page_num=pnum, offset_bbox=bbox, dpi=300, psm=6, save_path=save_p)

            ocr_cb = _img_ocr_cb

        tables = extract_tables(
            pdf_path,
            diagnostics,
            crops_dir=crops_dir,
            ocr_fn=ocr_cb,
            max_pages=max_pages,
        )

        # Adaptive OCR for scanned or garbled pages
        ocr_results: Dict[int, Dict[str, Any]] = {}
        if ocr_mode != "off":
            for prof in diagnostics.get("page_profiles", []):
                pnum = prof["page"]
                ptype = prof["page_type"]
                if ocr_mode == "always" or ptype in ("scanned_no_text", "garbled_font_cid"):
                    ocr_res = run_ocr_on_page(pdf_path, page_num=pnum, crops_dir=crops_dir, dpi=300)
                    ocr_results[pnum] = ocr_res
                    if ocr_res.get("ocr_status") in ("unavailable", "failed", "empty"):
                        uncertainties.append(
                            {
                                "code": f"ocr_{ocr_res.get('ocr_status')}",
                                "severity": "high",
                                "pages": [pnum],
                                "message": ocr_res.get("reason") or f"OCR yielded no usable text on page {pnum}.",
                                "image_path": ocr_res.get("image_path"),
                            }
                        )
                    elif ocr_res.get("low_confidence_spans"):
                        uncertainties.append(
                            {
                                "code": "ocr_low_confidence",
                                "severity": "medium",
                                "pages": [pnum],
                                "message": f"OCR mean_confidence={ocr_res['mean_confidence']} with {len(ocr_res['low_confidence_spans'])} low-confidence words (<60).",
                            }
                        )

        layout_data = extract_ordered_blocks(
            pdf_path,
            diagnostics,
            tables=tables,
            ocr_results=ocr_results,
            max_pages=max_pages,
        )
        formulas = extract_formulas(
            pdf_path,
            blocks=layout_data["blocks"],
            diagnostics=diagnostics,
            crops_dir=crops_dir,
            max_pages=max_pages,
        )
        structure = parse_document_structure(
            pdf_path,
            layout_data=layout_data,
            tables=tables,
            formulas=formulas,
            diagnostics=diagnostics,
            max_pages=max_pages,
        )

        # Record uncertainties from image tables, 2D formulas, garbled pages, disordered pages
        for anom in diagnostics.get("anomalies", []):
            uncertainties.append(
                {
                    "code": anom["code"],
                    "severity": anom["severity"],
                    "pages": anom["pages"],
                    "message": anom["detail"],
                }
            )
        for tab in tables:
            if tab.get("is_image_table"):
                uncertainties.append(
                    {
                        "code": "image_based_table",
                        "severity": "high",
                        "pages": tab["pages"],
                        "message": f"{tab['table_id']} ({tab.get('caption') or 'unlabeled'}) is a raster image table requiring OCR/vision review.",
                        "crop_path": tab.get("crop_path"),
                    }
                )
        for eq in formulas.get("display_equations", []):
            if eq.get("has_complex_2d_layout"):
                uncertainties.append(
                    {
                        "code": "complex_2d_formula",
                        "severity": "medium",
                        "pages": [eq["page"]],
                        "message": f"{eq['eq_id']} has multi-row 2D math layout; verify stacked fractions/matrices against crop.",
                        "crop_path": eq.get("crop_path"),
                    }
                )

        # Determine honest overall status
        total_blocks = len(layout_data.get("blocks", []))
        high_unverified = any(
            u["code"] in ("ocr_unavailable", "ocr_failed", "image_based_table", "garbled_font_cid")
            for u in uncertainties
        )
        all_pages_unreadable = (
            diagnostics["analyzed_pages"] > 0
            and total_blocks == 0
            and any(u["code"] in ("scanned_no_text", "ocr_unavailable", "ocr_failed") for u in uncertainties)
        )
        if all_pages_unreadable:
            status = "unverifiable"
        elif uncertainties or high_unverified:
            status = "partial"
        else:
            status = "success"

        doc_data = {
            "schema_version": "1.0.0",
            "source_file": str(pdf_path),
            "status": status,
            "diagnostics": diagnostics,
            "metadata": structure["metadata"],
            "sections": structure["sections"],
            "blocks": layout_data["blocks"],
            "headers_footers": layout_data["headers_footers"],
            "marginalia": layout_data["marginalia"],
            "footnotes": layout_data["footnotes"],
            "tables": tables,
            "figures": structure["figures"],
            "formulas": formulas,
            "references": structure["references"],
            "uncertainties": uncertainties,
            "change_log": layout_data["change_log"],
        }

    # Write all output artifacts
    write_json_atomic(out_path / "document.json", doc_data)
    write_json_atomic(out_path / "document_summary.json", _build_compact_summary(doc_data))
    write_text_lf(out_path / "full_text.md", _render_markdown(doc_data))
    write_json_atomic(out_path / "tables.json", doc_data["tables"])
    write_text_lf(out_path / "tables.html", _render_tables_html(doc_data["tables"]))
    write_json_atomic(out_path / "formulas.json", doc_data["formulas"])
    return doc_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a scientific PDF into structured provenance-bound outputs.")
    parser.add_argument("--pdf", required=True, help="Input PDF path.")
    parser.add_argument("--out", required=True, help="Output directory for parsed artifacts.")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto", help="Adaptive OCR policy.")
    parser.add_argument("--crops", action="store_true", help="Save PNG crops for image tables, complex equations, and OCR pages.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional maximum page limit.")
    args = parser.parse_args()

    doc = run_parse_pipeline(
        args.pdf,
        args.out,
        ocr_mode=args.ocr,
        save_crops=args.crops,
        max_pages=args.max_pages,
    )
    print(json.dumps({"status": doc["status"], "out_dir": str(args.out), "blocks": len(doc["blocks"]), "tables": len(doc["tables"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
