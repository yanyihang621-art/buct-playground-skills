#!/usr/bin/env python3
"""Multi-paper & multi-format (PDF / DOCX / Markdown / TXT / JATS / HTML / TeX / CSV / JSON / BIB) ingestion."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic
from structured_sources import adapt_article, json_documents, material_path, read_structured

IGNORE_INPUT_NAMES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}
SUPPORTED = {
    ".pdf", ".md", ".txt", ".json", ".jsonl", ".xml", ".jats",
    ".docx", ".html", ".htm", ".tex", ".csv", ".tsv", ".bib",
}
CASE_FILE_KEYS = (
    "input_files", "material_files", "source_files", "pdf_path", "files",
    "papers", "documents", "materials", "sources", "attachments",
    "source_pdf", "paper_path", "input_path", "articles",
)
MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([A-Z\u4e00-\u9fff].{1,90})$")
MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
TABLE_CAP_LINE_RE = re.compile(r"^\s*(?:Table|Tab\.?|表)\s*[A-Za-z]?\d+(?:\.\d+)?\b", re.I)
ABSTRACT_LINE_RE = re.compile(r"^(?:abstract|summary|摘\s*要)\s*[:：]?\s*(.*)$", re.IGNORECASE)
KEYWORDS_LINE_RE = re.compile(r"^(?:keywords?|index\s+terms?|关键词)\s*[:：]\s*(.+)$", re.IGNORECASE)


def _parse_markdown_table(lines: Sequence[str], start_line: int, table_idx: int, caption: Optional[str] = None) -> Dict[str, Any]:
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

    end_line = start_line + len(lines) - 1
    return {
        "table_id": f"tab_{table_idx}",
        "label": f"Table {table_idx}",
        "caption": caption,
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
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
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
            caption = None
            if blocks and blocks[-1]["role"] == "body" and TABLE_CAP_LINE_RE.match(blocks[-1]["text"]):
                caption = blocks[-1]["text"]
            tab_obj = _parse_markdown_table(t_lines, t_start, tab_counter, caption=caption)
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
            if not nxt or MD_HEADING_RE.match(nxt) or nxt.startswith("|") or nxt.startswith("$$") or (
                NUM_HEADING_RE.match(nxt) and len(nxt) <= 90 and not nxt.endswith(".")
            ):
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


def _discover_inputs(
    input_path: Path,
    explicit_entries: Optional[Sequence[str]] = None,
    skip_dir: Optional[Path] = None,
) -> Tuple[List[Path], List[str]]:
    resolved: List[Path] = []
    missing: List[str] = []
    seen_res: set[Path] = set()
    base_dir = input_path if input_path.is_dir() else input_path.parent

    def add_file(p: Path) -> None:
        rp = p.resolve()
        if rp not in seen_res and p.name.lower() not in IGNORE_INPUT_NAMES:
            if skip_dir is not None and (rp == skip_dir or skip_dir in rp.parents):
                return
            seen_res.add(rp)
            resolved.append(p)

    if explicit_entries:
        for item in explicit_entries:
            p = Path(item)
            if not p.is_absolute():
                p = base_dir / p
            if p.is_file():
                add_file(p)
            elif p.is_dir():
                dir_files = sorted(
                    f for f in p.rglob("*")
                    if f.is_file() and f.suffix.lower() in SUPPORTED
                )
                if not dir_files:
                    missing.append(item)
                for f in dir_files:
                    add_file(f)
            else:
                missing.append(item)

    if input_path.is_file():
        add_file(input_path)
    elif input_path.is_dir():
        extra = sorted(
            (
                p for p in input_path.rglob("*")
                if p.is_file() and p.suffix.lower() in SUPPORTED and p.name.lower() not in IGNORE_INPUT_NAMES
            ),
            key=lambda p: (p.suffix.lower() != ".pdf", p.as_posix()),
        )
        for p in extra:
            add_file(p)

    return resolved, missing


def ingest_input(
    input_path: str | Path,
    out_dir: str | Path,
    case_path: Optional[str | Path] = None,
    ocr_mode: str = "auto",
    save_crops: bool = False,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Ingest one or more PDF/DOCX/Markdown/TXT/HTML/TeX/CSV/JATS/JSON files into unified document.json artifacts."""
    in_p = Path(input_path)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    explicit_files: List[str] = []
    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8-sig"))
        for key in CASE_FILE_KEYS:
            entries = case_data.get(key)
            if not entries:
                continue
            for entry in entries if isinstance(entries, list) else [entries]:
                name = None
                if isinstance(entry, str) and len(entry) < 260 and "\n" not in entry:
                    if Path(entry).suffix.lower() in SUPPORTED or "/" in entry or "\\" in entry or (in_p / entry).exists():
                        name = entry
                else:
                    name = material_path(entry)
                if name and name not in explicit_files:
                    explicit_files.append(name)

    skip_output = out_p.resolve() if (in_p.resolve() == out_p.resolve() or in_p.resolve() in out_p.resolve().parents) else None
    files, missing_entries = _discover_inputs(in_p, explicit_files, skip_output)
    manifest: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = [
        {"code": "declared_material_missing", "message": name} for name in missing_entries
    ]
    root = (in_p if in_p.is_dir() else in_p.parent).resolve()

    def add(fpath: Path, doc: Dict[str, Any]) -> None:
        paper_id = f"paper_{len(manifest) + 1}"
        p_out = out_p / "papers" / paper_id
        write_json_atomic(p_out / "document.json", doc)
        manifest.append(
            {
                "paper_id": paper_id,
                "source_file": str(fpath),
                "source_record": doc.get("source_record"),
                "title": doc.get("metadata", {}).get("title") or fpath.stem,
                "status": doc.get("status", "partial"),
                "page_count": doc.get("diagnostics", {}).get("page_count", 0),
                "document_json": str(p_out / "document.json"),
            }
        )

    visited: set[Path] = set()

    def ingest_file(fpath: Path) -> None:
        rp = fpath.resolve()
        if rp in visited:
            return
        visited.add(rp)
        ext = fpath.suffix.lower()
        if ext == ".pdf":
            from parse_pdf import run_parse_pipeline
            dest = out_p / "papers" / f"paper_{len(manifest) + 1}"
            add(fpath, run_parse_pipeline(fpath, dest, ocr_mode=ocr_mode, save_crops=save_crops, max_pages=max_pages))
        elif ext in {".md", ".txt"}:
            add(fpath, parse_markdown_or_text(fpath))
        elif ext in {".json", ".jsonl", ".jats", ".xml", ".docx", ".html", ".htm", ".tex", ".csv", ".tsv", ".bib"}:
            if ext == ".json":
                data = json.loads(fpath.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    for key in ("papers", "documents", "materials", "sources", "files", "input_files"):
                        entries = data.get(key, [])
                        for entry in entries if isinstance(entries, list) else []:
                            name = (
                                entry
                                if isinstance(entry, str) and len(entry) < 260 and "\n" not in entry
                                else material_path(entry)
                            )
                            if not name or Path(name).suffix.lower() not in SUPPORTED:
                                continue
                            linked = (fpath.parent / name).resolve()
                            if not linked.is_relative_to(root):
                                continue
                            if linked.is_file():
                                ingest_file(linked)
                            else:
                                issues.append({"code": "linked_material_missing", "message": str(linked)})
                docs = [adapt_article(a) for a in json_documents(fpath, data)]
            else:
                docs = read_structured(fpath)
            for doc in docs:
                if doc["blocks"] or doc["tables"]:
                    add(fpath, doc)
        else:
            issues.append({"code": "unsupported_material_format", "message": str(fpath)})

    for fpath in files:
        try:
            ingest_file(fpath)
        except (OSError, ValueError, SyntaxError, ImportError, RuntimeError) as exc:
            issues.append({"code": "material_read_error", "message": f"{fpath}: {exc}"})

    if case_path and case_data:
        for article in json_documents(Path(case_path), case_data, inline=True):
            add(Path(case_path), adapt_article(article))

    if not manifest:
        issues.append({"code": "no_readable_papers", "message": "Inspect the case and original materials for missing or unsupported content."})
    out_manifest = {"paper_count": len(manifest), "papers": manifest, "uncertainties": issues}
    write_json_atomic(out_p / "ingest_manifest.json", out_manifest)
    return out_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest single or multiple PDF/DOCX/MD/TXT/JATS/HTML/TeX/CSV/JSON papers.")
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
