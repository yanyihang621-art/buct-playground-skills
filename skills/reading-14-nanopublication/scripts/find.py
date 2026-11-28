"""Search or read sections/anchors from prepare.py's work/index.json."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Search or inspect indexed paper passages and tables.")
    parser.add_argument("query", nargs="?", default=None, help="Keyword or regex pattern to search")
    parser.add_argument("--work", default=_default_work_dir(), help="Work directory containing index.json")
    parser.add_argument("--index", default=None, help="Explicit path to index.json")
    parser.add_argument("--paper", default=None, help="Filter by paper_id (e.g. paper_1)")
    parser.add_argument("--max", type=int, default=12, help="Max search hits to print (default 12)")
    parser.add_argument("--anchor", nargs="+", default=None, help="Print exact block/table/figure anchor(s)")
    parser.add_argument("--section", default=None, help="Print full section by section_id or title substring")
    parser.add_argument("--max-chars", type=int, default=15000, help="Max output chars for --section")
    args = parser.parse_args()

    entries = _load_index(Path(args.work), args.index)
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
