"""Search or read sections, tables, figures, anchors, or PDF pages from prepare.py's work artifacts."""

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


def _default_work_dir() -> str:
    skill_name = SCRIPT_DIR.parent.name
    if "reading-14" in skill_name:
        return "/app/work/r14"
    return "/app/work/r10"


def _load_index(work_dir: Path, index_path: str | None) -> List[Dict[str, Any]]:
    path = Path(index_path) if index_path else (work_dir / "index.json")
    if not path.is_file():
        raise FileNotFoundError(f"Index file not found: {path}. Run prepare.py first.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_meta(work_dir: Path) -> Dict[str, Any]:
    path = work_dir / "meta.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _format_entry(entry: Dict[str, Any], multi_paper: bool) -> str:
    anc = entry.get("anchor", "")
    pid = entry.get("paper_id", "paper_1")
    tag = f"{pid}:{anc}" if multi_paper else anc
    page = entry.get("page")
    p_str = f" p.{page}" if page is not None else ""
    sec = entry.get("section_id") or ""
    stitle = entry.get("section_title") or ""
    sec_str = f" §{sec} {stitle}".strip()
    return f"[{tag}{p_str} {sec_str}] {entry.get('text', '')}"


def _dump_raw_pdf_page(meta: Dict[str, Any], paper_filter: str | None, page_num: int) -> None:
    papers = meta.get("papers") or []
    if paper_filter:
        papers = [p for p in papers if p.get("paper_id") == paper_filter]
    elif papers:
        papers = papers[:1]
    for p in papers:
        src = Path(p.get("source_file") or "")
        if src.suffix.lower() != ".pdf" or not src.is_file():
            continue
        print(f"--- RAW PDF TEXT ({p.get('paper_id')} p.{page_num}) ---")
        try:
            import fitz  # type: ignore
            doc = fitz.open(str(src))
            try:
                if 1 <= page_num <= doc.page_count:
                    print(doc[page_num - 1].get_text("text"))
                else:
                    print(f"(page {page_num} out of bounds 1..{doc.page_count})")
            finally:
                doc.close()
            continue
        except Exception:
            pass
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            proc = subprocess.run(
                [pdftotext, "-layout", "-f", str(page_num), "-l", str(page_num), str(src), "-"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            print(proc.stdout)


def _render_pdf_page(meta: Dict[str, Any], paper_filter: str | None, page_num: int, out_png: Path) -> int:
    papers = meta.get("papers") or []
    if paper_filter:
        papers = [p for p in papers if p.get("paper_id") == paper_filter]
    if not papers:
        print("No matching paper in meta.json.")
        return 1
    src = Path(papers[0].get("source_file") or "")
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
    parser = argparse.ArgumentParser(description="Search or inspect indexed paper passages, tables, figures, and PDF pages.")
    parser.add_argument("query", nargs="?", default=None, help="Keyword or regex pattern to search")
    parser.add_argument("--work", default=_default_work_dir(), help="Work directory containing index.json")
    parser.add_argument("--index", default=None, help="Explicit path to index.json")
    parser.add_argument("--paper", default=None, help="Filter by paper_id (e.g. paper_1)")
    parser.add_argument("--max", type=int, default=12, help="Max search hits to print (default 12)")
    parser.add_argument("--anchor", nargs="+", default=None, help="Print exact block/table/figure anchor(s)")
    parser.add_argument("--section", default=None, help="Print full section by section_id or title substring")
    parser.add_argument("--tables", action="store_true", help="Print all extracted tables")
    parser.add_argument("--figures", action="store_true", help="Print all figure captions")
    parser.add_argument("--page", nargs="+", type=int, default=None, help="Print all items on page number(s)")
    parser.add_argument("--raw", action="store_true", help="With --page, also dump raw PDF page text")
    parser.add_argument("--render-page", type=int, default=None, help="Render a PDF page to PNG for visual inspection")
    parser.add_argument("--out-png", default=None, help="Output PNG path for --render-page")
    parser.add_argument("--max-chars", type=int, default=15000, help="Max output chars")
    args = parser.parse_args()

    work_dir = Path(args.work)
    if args.render_page is not None:
        meta = _load_meta(work_dir)
        out_png = Path(args.out_png) if args.out_png else (work_dir / f"page_{args.render_page}.png")
        return _render_pdf_page(meta, args.paper, args.render_page, out_png)

    entries = _load_index(work_dir, args.index)
    multi_paper = len({e.get("paper_id") for e in entries}) > 1
    filtered = [e for e in entries if not args.paper or e.get("paper_id") == args.paper]

    if args.anchor:
        by_key = {(e.get("paper_id", "paper_1"), e.get("anchor", "")): e for e in entries}
        default_pid = args.paper or "paper_1"
        found = 0
        for raw_a in args.anchor:
            pid, eid = parse_anchor(raw_a, default_pid)
            canon = re.sub(r"^p\d+_((?:tab|fig|eq)_\d+)$", r"\1", eid)
            hit = by_key.get((pid, eid)) or by_key.get((pid, canon))
            if hit:
                print(_format_entry(hit, multi_paper))
                print()
                found += 1
            else:
                print(f"[NOT FOUND] {raw_a}")
        return 0 if found else 1

    if args.tables:
        hits = [e for e in filtered if e.get("kind") == "table"]
        if not hits:
            print("No tables indexed.")
            return 0
        for e in hits:
            print(_format_entry(e, multi_paper))
            print()
        return 0

    if args.figures:
        hits = [e for e in filtered if e.get("kind") == "figure"]
        if not hits:
            print("No figures indexed.")
            return 0
        for e in hits:
            print(_format_entry(e, multi_paper))
            print()
        return 0

    if args.page:
        wanted = set(args.page)
        hits = [e for e in filtered if e.get("page") in wanted]
        for e in hits:
            print(_format_entry(e, multi_paper))
            print()
        if args.raw:
            meta = _load_meta(work_dir)
            for pnum in args.page:
                _dump_raw_pdf_page(meta, args.paper, pnum)
        return 0

    if args.section:
        target = args.section.strip().lower()
        hits = [
            e for e in filtered
            if (e.get("section_id") or "").lower() == target
            or target in (e.get("section_title") or "").lower()
        ]
        if not hits:
            print(f"No entries matched section '{args.section}'.")
            return 1
        out_chunks: List[str] = []
        total = 0
        for e in hits:
            line = _format_entry(e, multi_paper) + "\n"
            if total + len(line) > args.max_chars:
                out_chunks.append(f"...(truncated at {args.max_chars} chars; {len(hits)} total items in section)")
                break
            out_chunks.append(line)
            total += len(line)
        print("\n".join(out_chunks))
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
            print(f"...({len(hits) - args.max} more hits; refine query or raise --max)")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
