#!/usr/bin/env python3
"""Search or inspect indexed source passages, bibliography entries, tables, and PDF pages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import parse_anchor


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _format_entry(entry: Dict[str, Any]) -> str:
    did = entry.get("doc_id", "doc_1")
    anc = entry.get("anchor", "")
    pg = f" p.{entry['page']}" if entry.get("page") is not None else ""
    sec = entry.get("section_title") or ""
    return f"[{did}:{anc}{pg} | {entry.get('role')} | {entry.get('path')} | {sec}] {entry.get('text', '')}"


def _render_pdf_page(meta: Dict[str, Any], materials: Dict[str, Any], doc_filter: str | None, page_num: int, out_png: Path) -> int:
    srcs = [s for s in materials.get("sources", []) if s.get("format") == "pdf" and (not doc_filter or doc_filter in (s["doc_id"], s["path"]))]
    if not srcs:
        print("No matching PDF source.")
        return 1
    src = Path(meta.get("input_dir") or ".") / srcs[0]["path"]
    if not src.is_file():
        print(f"PDF not found: {src}")
        return 1
    import fitz  # type: ignore
    with fitz.open(str(src)) as doc:
        if page_num < 1 or page_num > doc.page_count:
            print(f"Page {page_num} out of bounds (1..{doc.page_count}).")
            return 1
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pix = doc[page_num - 1].get_pixmap(dpi=200)
        pix.save(str(out_png))
        print(f"Rendered {src.name} page {page_num} -> {out_png} ({pix.width}x{pix.height})")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Search or inspect Writing 08 prepared materials.")
    parser.add_argument("query", nargs="?", default=None, help="Keyword or regex pattern")
    parser.add_argument("--work", default="/app/work/w08", help="Work directory created by prepare.py")
    parser.add_argument("--doc", default=None, help="Filter by doc_id or relative path substring")
    parser.add_argument("--role", default=None, choices=("literature", "study", "material", "instruction"))
    parser.add_argument("--bib", action="store_true", help="List indexed bibliography entries")
    parser.add_argument("--tables", action="store_true", help="Print extracted tables")
    parser.add_argument("--anchor", nargs="+", default=None, help="Print exact doc_id:anchor(s)")
    parser.add_argument("--section", default=None, help="Print passages matching section title")
    parser.add_argument("--page", nargs="+", type=int, default=None, help="Print passages on PDF page(s)")
    parser.add_argument("--raw", action="store_true", help="With --page, also dump raw PDF text")
    parser.add_argument("--render-page", type=int, default=None, help="Render a PDF page to PNG")
    parser.add_argument("--out-png", default=None)
    parser.add_argument("--max", type=int, default=12)
    parser.add_argument("--max-chars", type=int, default=15000)
    args = parser.parse_args()

    work_dir = Path(args.work)
    meta = _load_json(work_dir / "meta.json")
    materials = _load_json(work_dir / "materials_index.json")

    if args.render_page is not None:
        out_png = Path(args.out_png) if args.out_png else (work_dir / f"page_{args.render_page}.png")
        return _render_pdf_page(meta, materials, args.doc, args.render_page, out_png)

    if args.bib:
        for p in materials.get("papers", []):
            print(f"@{p['cite_key']} | {p.get('authors')} ({p.get('year')}) | {p.get('title')} | {p.get('venue')} | DOI:{p.get('doi')}")
        if not materials.get("papers"):
            print("No structured bibliography entries indexed.")
        return 0

    entries: List[Dict[str, Any]] = _load_json(work_dir / "index.json")
    filtered = [
        e for e in entries
        if (not args.doc or args.doc in (e.get("doc_id"), e.get("path", "")))
        and (not args.role or e.get("role") == args.role)
    ]

    if args.anchor:
        by_key = {(e.get("doc_id", "doc_1"), e.get("anchor", "")): e for e in entries}
        by_sid = {e.get("source_id", ""): e for e in entries}
        found = 0
        for raw_a in args.anchor:
            if raw_a in by_sid:
                print(_format_entry(by_sid[raw_a]))
                found += 1
                continue
            did, eid = parse_anchor(raw_a, args.doc or "doc_1")
            hit = by_key.get((did, eid))
            if hit:
                print(_format_entry(hit))
                found += 1
            else:
                print(f"[NOT FOUND] {raw_a}")
        return 0 if found else 1

    if args.tables:
        hits = [e for e in filtered if e.get("kind") == "table"]
        for e in hits:
            print(_format_entry(e))
            print()
        if not hits:
            print("No tables indexed.")
        return 0

    if args.page:
        wanted = set(args.page)
        for e in filtered:
            if e.get("page") in wanted:
                print(_format_entry(e))
                print()
        if args.raw:
            import fitz  # type: ignore
            for s in materials.get("sources", []):
                if s.get("format") == "pdf" and (not args.doc or args.doc in (s["doc_id"], s["path"])):
                    src = Path(meta.get("input_dir") or ".") / s["path"]
                    if src.is_file():
                        with fitz.open(str(src)) as doc:
                            for pg in args.page:
                                if 1 <= pg <= doc.page_count:
                                    print(f"--- RAW PDF ({s['doc_id']} p.{pg}) ---")
                                    print(doc[pg - 1].get_text("text"))
        return 0

    if args.section:
        target = args.section.strip().lower()
        hits = [e for e in filtered if target in str(e.get("section_title") or "").lower()]
        total = 0
        for e in hits:
            ln = _format_entry(e) + "\n"
            if total + len(ln) > args.max_chars:
                print(f"...(truncated at {args.max_chars} chars)")
                break
            print(ln)
            total += len(ln)
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
            print(_format_entry(e))
            print()
        if len(hits) > args.max:
            print(f"...({len(hits) - args.max} more hits)")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
