#!/usr/bin/env python3
"""Search or inspect ingested corpus paragraphs, tables, abbreviations, boundaries, XML formulas, and PDF pages."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import parse_anchor


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _format_entry(entry: Dict[str, Any], multi_paper: bool) -> str:
    pid = entry.get("paper_id", "paper_1")
    anc = entry.get("anchor", "")
    tag = f"{pid}:{anc}" if multi_paper else anc
    page = entry.get("page")
    p_str = f" p.{page}" if page is not None else ""
    sec = entry.get("section_title") or ""
    return f"[{tag}{p_str} | {entry.get('location_type')} | {sec}] {entry.get('text', '')}"


def _render_pdf_page(meta: Dict[str, Any], corpus: Dict[str, Any], paper_filter: str | None, page_num: int, out_png: Path) -> int:
    papers = [p for p in corpus.get("papers", []) if not paper_filter or p.get("source_id") == paper_filter]
    if not papers:
        print("No matching paper.")
        return 1
    src_rel = papers[0].get("source_file") or ""
    src = Path(meta.get("input_dir") or ".") / src_rel
    if not src.is_file():
        src = Path(src_rel)
    if src.suffix.lower() != ".pdf" or not src.is_file():
        print(f"Source is not an existing PDF: {src}")
        return 1
    import fitz  # type: ignore
    doc = fitz.open(str(src))
    try:
        if page_num < 1 or page_num > doc.page_count:
            print(f"Page {page_num} out of bounds (1..{doc.page_count}).")
            return 1
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pix = doc[page_num - 1].get_pixmap(dpi=200)
        pix.save(str(out_png))
        print(f"Rendered {src.name} page {page_num} -> {out_png} ({pix.width}x{pix.height})")
        return 0
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Search or inspect Reading 15 corpus items.")
    parser.add_argument("query", nargs="?", default=None, help="Keyword or regex pattern")
    parser.add_argument("--work", default="/app/work/r15", help="Work directory created by prepare.py")
    parser.add_argument("--paper", default=None, help="Filter by source_id")
    parser.add_argument("--max", type=int, default=12)
    parser.add_argument("--anchor", nargs="+", default=None, help="Print exact paragraph_id or table_id")
    parser.add_argument("--section", default=None, help="Print full section by title substring")
    parser.add_argument("--tables", action="store_true", help="Print all tables")
    parser.add_argument("--abbreviations", action="store_true", help="Print per-paper abbreviations and conflicts")
    parser.add_argument("--boundaries", action="store_true", help="Print detected negation/inconclusive/unreported sentences")
    parser.add_argument("--xml-formulas", action="store_true", help="Print JATS XML table/figure/formula cross-check notes")
    parser.add_argument("--page", nargs="+", type=int, default=None, help="Print items on PDF page(s)")
    parser.add_argument("--raw", action="store_true", help="With --page, print raw PDF text")
    parser.add_argument("--render-page", type=int, default=None, help="Render PDF page to PNG")
    parser.add_argument("--out-png", default=None)
    parser.add_argument("--max-chars", type=int, default=15000)
    args = parser.parse_args()

    work_dir = Path(args.work)
    meta = _load_json(work_dir / "meta.json")
    corpus = _load_json(Path(meta["corpus_json"]))

    if args.render_page is not None:
        out_png = Path(args.out_png) if args.out_png else (work_dir / f"page_{args.render_page}.png")
        return _render_pdf_page(meta, corpus, args.paper, args.render_page, out_png)

    if args.abbreviations:
        defs = [d for d in corpus.get("abbreviation_definitions", []) if not args.paper or d["source_id"] == args.paper]
        print("=== ABBREVIATIONS ===")
        for d in defs:
            print(f"[{d['source_id']}:{d['paragraph_id']}] {d['short']} = {d['full']} | {d['quote_snippet']}")
        if corpus.get("abbreviation_conflicts"):
            print("=== CONFLICTS ===")
            print(json.dumps(corpus["abbreviation_conflicts"], ensure_ascii=False, indent=2))
        return 0

    if args.boundaries:
        rows = [b for b in corpus.get("boundary_records", []) if not args.paper or b["source_id"] == args.paper]
        for b in rows[: args.max]:
            print(f"[{b['source_id']}:{b['paragraph_id']} | {b['category']}] {b['quote_snippet']}")
        return 0

    if args.xml_formulas:
        for p in corpus.get("papers", []):
            if args.paper and p["source_id"] != args.paper:
                continue
            for n in p.get("xml_reference_notes", []):
                print(f"[{p['source_id']} | {n['kind']} {n['id']}] {n['text']}")
        return 0

    entries: List[Dict[str, Any]] = _load_json(work_dir / "index.json")
    multi_paper = len({e.get("paper_id") for e in entries}) > 1
    filtered = [e for e in entries if not args.paper or e.get("paper_id") == args.paper]

    if args.anchor:
        by_key = {(e.get("paper_id", "paper_1"), e.get("anchor", "")): e for e in entries}
        default_pid = args.paper or "paper_1"
        found = 0
        for raw_a in args.anchor:
            pid, eid = parse_anchor(raw_a, default_pid)
            hit = by_key.get((pid, eid))
            if hit:
                print(_format_entry(hit, multi_paper))
                print()
                found += 1
            else:
                print(f"[NOT FOUND] {raw_a}")
        return 0 if found else 1

    if args.tables:
        hits = [e for e in filtered if e.get("kind") == "table"]
        for e in hits:
            print(_format_entry(e, multi_paper))
            print()
        if not hits:
            print("No extracted tables.")
        return 0

    if args.page:
        wanted = set(args.page)
        for e in filtered:
            if e.get("page") in wanted:
                print(_format_entry(e, multi_paper))
                print()
        if args.raw:
            for p in corpus.get("papers", []):
                if args.paper and p["source_id"] != args.paper:
                    continue
                src = Path(meta.get("input_dir") or ".") / (p.get("source_file") or "")
                if src.suffix.lower() == ".pdf" and src.is_file():
                    import fitz  # type: ignore
                    with fitz.open(str(src)) as doc:
                        for pg in args.page:
                            if 1 <= pg <= doc.page_count:
                                print(f"--- RAW PDF ({p['source_id']} p.{pg}) ---")
                                print(doc[pg - 1].get_text("text"))
        return 0

    if args.section:
        target = args.section.strip().lower()
        hits = [
            e for e in filtered
            if target == str(e.get("section_id") or "").lower()
            or target in str(e.get("section_title") or "").lower()
        ]
        if not hits:
            print(f"No entries matched section '{args.section}'.")
            return 1
        total = 0
        for e in hits:
            line = _format_entry(e, multi_paper) + "\n"
            if total + len(line) > args.max_chars:
                print(f"...(truncated at {args.max_chars} chars)")
                break
            print(line)
            total += len(line)
        return 0

    if args.query:
        try:
            pat = re.compile(args.query, re.I)
        except re.error:
            pat = re.compile(re.escape(args.query), re.I)
        hits = [e for e in filtered if pat.search(e.get("text") or "") or pat.search(e.get("section_title") or "")]
        if not hits:
            print(f"No hits for '{args.query}'.")
            return 0
        for e in hits[: args.max]:
            print(_format_entry(e, multi_paper))
            print()
        if len(hits) > args.max:
            print(f"...({len(hits) - args.max} more hits)")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
