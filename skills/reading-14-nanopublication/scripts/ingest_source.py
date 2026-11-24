#!/usr/bin/env python3
"""Multi-paper & multi-format (PDF / Markdown / TXT) ingestion into unified document.json."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from parse_pdf import run_parse_pipeline
from pdf_utils import write_json_atomic

IGNORE_INPUT_NAMES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}
MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([A-Z\u4e00-\u9fff].{1,90})$")
MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
ABSTRACT_LINE_RE = re.compile(r"^(?:abstract|summary|摘\s*要)\s*[:：]?\s*(.*)$", re.IGNORECASE)
KEYWORDS_LINE_RE = re.compile(r"^(?:keywords?|index\s+terms?|关键词)\s*[:：]\s*(.+)$", re.IGNORECASE)


def _parse_markdown_table(lines: Sequence[str], start_line: int, table_idx: int) -> Dict[str, Any]:
    rows: List[List[str]] = []
    for raw in lines:
        s = raw.strip()
        if MD_TABLE_SEP_RE.match(s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells:
            rows.append(cells)

    n_cols = max((len(r) for r in rows), default=0)
    norm_rows = [r + [""] * (n_cols - len(r)) for r in rows]
    col_paths = norm_rows[0] if norm_rows else []
    data_rows = norm_rows[1:] if len(norm_rows) > 1 else []

    records: List[Dict[str, Any]] = []
    for r in data_rows:
        rec = {col_paths[ci] if ci < len(col_paths) and col_paths[ci] else f"col_{ci + 1}": r[ci] for ci in range(n_cols)}
        records.append(rec)

    html_parts = ["<table><thead><tr>"] + [f"<th>{html_lib.escape(h)}</th>" for h in col_paths] + ["</tr></thead><tbody>"]
    for r in data_rows:
        html_parts.append("<tr>" + "".join(f"<td>{html_lib.escape(c)}</td>" for c in r) + "</tr>")
    html_parts.append("</tbody></table>")

    end_line = start_line + len(lines)
    return {
        "table_id": f"tab_{table_idx}",
        "label": f"Table {table_idx}",
        "caption": None,
        "notes": [],
        "pages": [1],
        "bbox": [0.0, float(start_line), 100.0, float(end_line)],
        "bboxes_by_page": {"1": [0.0, float(start_line), 100.0, float(end_line)]},
        "n_rows": len(norm_rows),
        "n_cols": n_cols,
        "header_row_count": 1 if norm_rows else 0,
        "col_paths": col_paths,
        "cells": [],
        "markdown": "\n".join(lines),
        "html": "".join(html_parts),
        "records": records,
        "extraction_method": "markdown_table",
        "confidence": 0.96,
        "is_cross_page_merged": False,
        "is_image_table": False,
        "status": "extracted",
        "crop_path": None,
        "section_id": None,
        "section_path": [],
    }


def parse_markdown_or_text(path: Path) -> Dict[str, Any]:
    """Parse a Markdown or plain text paper into the unified document.json schema."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    title: Optional[str] = None
    authors: List[str] = []
    abstract: Optional[str] = None
    keywords: List[str] = []

    sections: List[Dict[str, Any]] = []
    sec_stack: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    display_equations: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []

    sec_counter = 0
    blk_counter = 0
    tab_counter = 0
    eq_counter = 0
    ref_counter = 0
    in_refs = False

    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue

        # Markdown table block
        if ln.startswith("|") and i + 1 < len(lines) and MD_TABLE_SEP_RE.match(lines[i + 1].strip()):
            t_start = i + 1
            t_lines = [lines[i]]
            i += 1
            while i < len(lines) and "|" in lines[i]:
                t_lines.append(lines[i])
                i += 1
            tab_counter += 1
            tab_obj = _parse_markdown_table(t_lines, t_start, tab_counter)
            if sec_stack:
                tab_obj["section_id"] = sec_stack[-1]["section_id"]
                tab_obj["section_path"] = [
                    (f"{s['number']} {s['title']}".strip() if s.get("number") else s["title"]) for s in sec_stack
                ]
            tables.append(tab_obj)

            blk_counter += 1
            bid = f"p1_b{blk_counter:02d}"
            blocks.append(
                {
                    "block_id": bid,
                    "role": "table_ref",
                    "page": 1,
                    "printed_page": "1",
                    "bbox": tab_obj["bbox"],
                    "column": 0,
                    "reading_order": blk_counter,
                    "section_id": tab_obj["section_id"],
                    "section_path": tab_obj["section_path"],
                    "raw_text": f"[{tab_obj['label']}]",
                    "text": f"[{tab_obj['label']}]",
                    "structured_text": f"[{tab_obj['label']}]",
                    "table_id": tab_obj["table_id"],
                    "continuation_of": None,
                    "continued_by": None,
                    "confidence": 0.98,
                    "extraction_method": "markdown_parser",
                }
            )
            if sec_stack:
                sec_stack[-1]["block_ids"].append(bid)
            continue

        # Display equation block $$ ... $$
        if ln.startswith("$$"):
            eq_start = i + 1
            eq_lines = []
            if ln.endswith("$$") and len(ln) > 4:
                eq_lines.append(ln.strip("$").strip())
                i += 1
            else:
                i += 1
                while i < len(lines) and not lines[i].strip().endswith("$$"):
                    eq_lines.append(lines[i].strip())
                    i += 1
                i += 1
            eq_counter += 1
            blk_counter += 1
            bid = f"p1_b{blk_counter:02d}"
            eq_txt = " ".join(eq_lines).strip()
            sec_id = sec_stack[-1]["section_id"] if sec_stack else None
            bbox = [0.0, float(eq_start), 100.0, float(max(eq_start + 1, i))]
            blocks.append(
                {
                    "block_id": bid,
                    "role": "body",
                    "page": 1,
                    "printed_page": "1",
                    "bbox": bbox,
                    "column": 0,
                    "reading_order": blk_counter,
                    "section_id": sec_id,
                    "section_path": [
                        (f"{s['number']} {s['title']}".strip() if s.get("number") else s["title"]) for s in sec_stack
                    ],
                    "raw_text": eq_txt,
                    "text": eq_txt,
                    "structured_text": eq_txt,
                    "continuation_of": None,
                    "continued_by": None,
                    "confidence": 0.95,
                    "extraction_method": "markdown_parser",
                }
            )
            display_equations.append(
                {
                    "eq_id": f"eq_{eq_counter}",
                    "label": f"({eq_counter})",
                    "page": 1,
                    "printed_page": "1",
                    "bbox": bbox,
                    "section_id": sec_id,
                    "block_ids": [bid],
                    "plain_text": eq_txt,
                    "structured_text": eq_txt,
                    "has_complex_2d_layout": False,
                    "crop_path": None,
                    "confidence": 0.95,
                }
            )
            if sec_stack:
                sec_stack[-1]["block_ids"].append(bid)
            continue

        # Headings
        m_md = MD_HEADING_RE.match(ln)
        m_num = NUM_HEADING_RE.match(ln) if not m_md else None
        if m_md:
            lvl = len(m_md.group(1))
            htext = m_md.group(2).strip()
            if lvl == 1 and title is None and sec_counter == 0:
                title = htext
                blk_counter += 1
                blocks.append(
                    {
                        "block_id": f"p1_b{blk_counter:02d}",
                        "role": "title",
                        "page": 1,
                        "printed_page": "1",
                        "bbox": [0.0, float(i + 1), 100.0, float(i + 1.5)],
                        "column": "full",
                        "reading_order": blk_counter,
                        "section_id": None,
                        "section_path": [],
                        "raw_text": htext,
                        "text": htext,
                        "structured_text": htext,
                        "continuation_of": None,
                        "continued_by": None,
                        "confidence": 1.0,
                        "extraction_method": "markdown_parser",
                    }
                )
                i += 1
                continue

            m_sub = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$", htext)
            sec_num = m_sub.group(1) if m_sub else None
            sec_title = m_sub.group(2).strip() if m_sub else htext
            sec_lvl = max(1, lvl - 1) if title is not None and lvl >= 2 else lvl
            if sec_num:
                sec_lvl = sec_num.count(".") + 1

            sec_counter += 1
            blk_counter += 1
            sec_id = f"sec_{sec_counter}"
            bid = f"p1_b{blk_counter:02d}"
            node: Dict[str, Any] = {
                "section_id": sec_id,
                "level": sec_lvl,
                "number": sec_num,
                "title": sec_title,
                "page_start": 1,
                "page_end": 1,
                "bbox": [0.0, float(i + 1), 100.0, float(i + 1.5)],
                "block_ids": [bid],
                "children": [],
            }
            while sec_stack and sec_stack[-1]["level"] >= sec_lvl:
                sec_stack.pop()
            if sec_stack:
                sec_stack[-1]["children"].append(node)
            else:
                sections.append(node)
            sec_stack.append(node)
            in_refs = bool(re.search(r"references|bibliography|参考文献", sec_title, re.IGNORECASE))

            blocks.append(
                {
                    "block_id": bid,
                    "role": "heading",
                    "page": 1,
                    "printed_page": "1",
                    "bbox": node["bbox"],
                    "column": 0,
                    "reading_order": blk_counter,
                    "section_id": sec_id,
                    "section_path": [
                        (f"{s['number']} {s['title']}".strip() if s.get("number") else s["title"]) for s in sec_stack
                    ],
                    "raw_text": htext,
                    "text": htext,
                    "structured_text": htext,
                    "continuation_of": None,
                    "continued_by": None,
                    "confidence": 1.0,
                    "extraction_method": "markdown_parser",
                }
            )
            i += 1
            continue

        if m_num and len(ln) <= 90 and not ln.endswith("."):
            sec_num, sec_title = m_num.group(1), m_num.group(2).strip()
            sec_lvl = sec_num.count(".") + 1
            sec_counter += 1
            blk_counter += 1
            sec_id = f"sec_{sec_counter}"
            bid = f"p1_b{blk_counter:02d}"
            node = {
                "section_id": sec_id,
                "level": sec_lvl,
                "number": sec_num,
                "title": sec_title,
                "page_start": 1,
                "page_end": 1,
                "bbox": [0.0, float(i + 1), 100.0, float(i + 1.5)],
                "block_ids": [bid],
                "children": [],
            }
            while sec_stack and sec_stack[-1]["level"] >= sec_lvl:
                sec_stack.pop()
            if sec_stack:
                sec_stack[-1]["children"].append(node)
            else:
                sections.append(node)
            sec_stack.append(node)
            in_refs = False
            blocks.append(
                {
                    "block_id": bid,
                    "role": "heading",
                    "page": 1,
                    "printed_page": "1",
                    "bbox": node["bbox"],
                    "column": 0,
                    "reading_order": blk_counter,
                    "section_id": sec_id,
                    "section_path": [
                        (f"{s['number']} {s['title']}".strip() if s.get("number") else s["title"]) for s in sec_stack
                    ],
                    "raw_text": ln,
                    "text": ln,
                    "structured_text": ln,
                    "continuation_of": None,
                    "continued_by": None,
                    "confidence": 0.95,
                    "extraction_method": "markdown_parser",
                }
            )
            i += 1
            continue

        # Collect paragraph lines until blank line or structural marker
        p_start = i + 1
        p_lines = [ln]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or MD_HEADING_RE.match(nxt) or nxt.startswith("|") or nxt.startswith("$$"):
                break
            p_lines.append(nxt)
            i += 1

        para = " ".join(p_lines).strip()
        blk_counter += 1
        bid = f"p1_b{blk_counter:02d}"
        role = "reference" if in_refs else "body"

        m_abs = ABSTRACT_LINE_RE.match(para)
        m_kw = KEYWORDS_LINE_RE.match(para)
        if m_abs and abstract is None and not sec_stack:
            abstract = m_abs.group(1).strip() or para
            role = "abstract"
        elif m_kw and not keywords:
            keywords = [k.strip() for k in re.split(r"[;；,，]", m_kw.group(1)) if k.strip()]
            role = "keywords"
        elif para.lower().startswith("authors:"):
            authors = [a.strip() for a in para.split(":", 1)[1].split(",") if a.strip()]
            role = "authors"
        elif in_refs:
            ref_counter += 1
            references.append(
                {
                    "ref_id": f"ref_{ref_counter}",
                    "marker": str(ref_counter),
                    "raw_text": para,
                    "page": 1,
                    "printed_page": "1",
                    "bbox": [0.0, float(p_start), 100.0, float(i)],
                }
            )

        if sec_stack and sec_stack[-1]["title"].lower().startswith("abstract") and abstract is None:
            abstract = para

        sec_id = sec_stack[-1]["section_id"] if sec_stack else None
        sec_path = [
            (f"{s['number']} {s['title']}".strip() if s.get("number") else s["title"]) for s in sec_stack
        ]
        if sec_stack:
            sec_stack[-1]["block_ids"].append(bid)

        blocks.append(
            {
                "block_id": bid,
                "role": role,
                "page": 1,
                "printed_page": "1",
                "bbox": [0.0, float(p_start), 100.0, float(i)],
                "column": 0,
                "reading_order": blk_counter,
                "section_id": sec_id,
                "section_path": sec_path,
                "raw_text": para,
                "text": para,
                "structured_text": para,
                "continuation_of": None,
                "continued_by": None,
                "confidence": 1.0,
                "extraction_method": "markdown_parser",
            }
        )

    return {
        "schema_version": "1.0.0",
        "source_file": str(path),
        "status": "success" if blocks else "unverifiable",
        "diagnostics": {
            "source_file": str(path),
            "file_exists": True,
            "file_size_bytes": path.stat().st_size,
            "encrypted": False,
            "corrupt": False,
            "page_count": 1,
            "analyzed_pages": 1,
            "body_font_size": 10.0,
            "page_profiles": [{"page": 1, "page_type": "digital_text", "printed_page": "1"}],
            "page_number_map": [{"pdf_page": 1, "printed_page": "1", "printed_int": 1, "bbox": [0, 0, 0, 0]}],
            "anomalies": [],
            "environment_capabilities": {},
        },
        "metadata": {
            "title": title or path.stem,
            "authors": authors,
            "affiliations": [],
            "abstract": abstract,
            "keywords": keywords,
            "toc": [],
        },
        "sections": sections,
        "blocks": blocks,
        "headers_footers": [],
        "marginalia": [],
        "footnotes": [],
        "tables": tables,
        "figures": [],
        "formulas": {
            "display_equations": display_equations,
            "inline_formula_spans": [],
            "stats": {"display_count": len(display_equations), "inline_block_count": 0, "complex_2d_count": 0},
        },
        "references": references,
        "uncertainties": [],
        "change_log": [],
    }


def _discover_inputs(input_path: Path, explicit_files: Optional[Sequence[str]] = None) -> List[Path]:
    if explicit_files:
        resolved: List[Path] = []
        base_dir = input_path if input_path.is_dir() else input_path.parent
        for item in explicit_files:
            p = Path(item)
            if not p.is_absolute():
                p = base_dir / p
            if p.exists():
                resolved.append(p)
        return resolved

    if input_path.is_file():
        return [input_path]

    if input_path.is_dir():
        pdfs = sorted(input_path.glob("*.pdf"))
        if pdfs:
            return pdfs
        others = [
            p
            for p in sorted(list(input_path.glob("*.md")) + list(input_path.glob("*.txt")))
            if p.name.lower() not in IGNORE_INPUT_NAMES
        ]
        return others
    return []


def ingest_input(
    input_path: str | Path,
    out_dir: str | Path,
    case_path: Optional[str | Path] = None,
    ocr_mode: str = "auto",
    save_crops: bool = False,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Ingest one or more PDF/Markdown/TXT files and produce unified document.json artifacts."""
    in_p = Path(input_path)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    explicit_files: Optional[List[str]] = None
    if case_path and Path(case_path).exists():
        try:
            case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
            if isinstance(case_data.get("input_files"), list) and case_data["input_files"]:
                explicit_files = [str(x) for x in case_data["input_files"]]
            elif case_data.get("pdf_path"):
                explicit_files = [str(case_data["pdf_path"])]
        except Exception:
            explicit_files = None

    files = _discover_inputs(in_p, explicit_files)
    manifest: List[Dict[str, Any]] = []

    for idx, fpath in enumerate(files, start=1):
        paper_id = f"paper_{idx}"
        p_out = out_p / "papers" / paper_id
        p_out.mkdir(parents=True, exist_ok=True)

        if fpath.suffix.lower() == ".pdf":
            doc = run_parse_pipeline(
                fpath,
                p_out,
                ocr_mode=ocr_mode,
                save_crops=save_crops,
                max_pages=max_pages,
            )
        else:
            doc = parse_markdown_or_text(fpath)
            write_json_atomic(p_out / "document.json", doc)

        manifest.append(
            {
                "paper_id": paper_id,
                "source_file": str(fpath),
                "title": doc.get("metadata", {}).get("title") or fpath.stem,
                "status": doc.get("status", "partial"),
                "page_count": doc.get("diagnostics", {}).get("page_count", 0),
                "document_json": str(p_out / "document.json"),
            }
        )

    out_manifest = {"paper_count": len(manifest), "papers": manifest}
    write_json_atomic(out_p / "ingest_manifest.json", out_manifest)
    return out_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest single or multiple PDF/MD/TXT papers.")
    parser.add_argument("--input", required=True, help="Input file or directory (e.g. /input).")
    parser.add_argument("--out", required=True, help="Output directory for parsed documents.")
    parser.add_argument("--case", default=None, help="Optional case.json to resolve input_files.")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    parser.add_argument("--crops", action="store_true")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    res = ingest_input(
        args.input,
        args.out,
        case_path=args.case,
        ocr_mode=args.ocr,
        save_crops=args.crops,
        max_pages=args.max_pages,
    )
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
