#!/usr/bin/env python3
"""Format canonical bibliographic records into GB/T 7714-2015, APA 7th, IEEE, BibTeX, or RIS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import CJK_RE, write_text_lf

GBT_TYPE_CODES = {
    "article": "J",
    "journal": "J",
    "inproceedings": "C",
    "conference": "C",
    "book": "M",
    "thesis": "D",
    "phdthesis": "D",
    "mastersthesis": "D",
    "preprint": "EB/OL",
    "electronic": "EB/OL",
    "misc": "Z",
}


def _format_authors_gbt(authors: List[str], lang: str) -> str:
    if not authors:
        return "[佚名]" if lang == "zh" else "[Anon.]"
    etal = "等" if (lang == "zh" or any(CJK_RE.search(a) for a in authors)) else "et al"
    shown = authors[:3]
    joined = ", ".join(shown)
    return f"{joined}, {etal}" if len(authors) > 3 else joined


def _surname_first_apa(author: str) -> str:
    s = author.strip()
    if CJK_RE.search(s):
        return s
    if "," in s:
        parts = [p.strip() for p in s.split(",", 1)]
        initials = " ".join(f"{w[0].upper()}." for w in parts[1].split() if w)
        return f"{parts[0]}, {initials}".strip().rstrip(",")
    words = s.split()
    if len(words) <= 1:
        return s
    surname = words[-1]
    initials = " ".join(f"{w[0].upper()}." for w in words[:-1] if w)
    return f"{surname}, {initials}"


def _format_authors_apa(authors: List[str]) -> str:
    if not authors:
        return "Anonymous"
    formatted = [_surname_first_apa(a) for a in authors[:20]]
    if len(formatted) == 1:
        return formatted[0]
    return ", ".join(formatted[:-1]) + ", & " + formatted[-1]


def format_single_gbt7714(rec: Dict[str, Any], index: int) -> str:
    lang = rec.get("language", "en")
    authors = _format_authors_gbt(rec.get("authors", []), lang)
    title = (rec.get("title") or "[Untitled]").rstrip(".")
    tcode = GBT_TYPE_CODES.get(rec.get("entry_type", "article").lower(), "J")
    venue = rec.get("venue") or (f"arXiv:{rec['arxiv_id']}" if rec.get("arxiv_id") else "[Source Unknown]")
    yr = str(rec.get("year") or "s.a.")

    if tcode == "EB/OL":
        ref_url = rec.get("url") or (f"https://arxiv.org/abs/{rec['arxiv_id']}" if rec.get("arxiv_id") else "")
        body = f"[{index}] {authors}. {title}[{tcode}]. {venue}, {yr}."
        if ref_url:
            body += f" {ref_url}."
        return body

    tail = yr
    if rec.get("volume"):
        tail += f", {rec['volume']}"
        if rec.get("issue"):
            tail += f"({rec['issue']})"
    elif rec.get("issue"):
        tail += f"({rec['issue']})"
    if rec.get("pages"):
        tail += f": {rec['pages']}"

    out = f"[{index}] {authors}. {title}[{tcode}]. {venue}, {tail}."
    if rec.get("doi"):
        out += f" DOI:{rec['doi']}."
    return out


def format_single_apa7(rec: Dict[str, Any]) -> str:
    authors = _format_authors_apa(rec.get("authors", []))
    yr = str(rec.get("year") or "n.d.")
    title = (rec.get("title") or "Untitled").rstrip(".")
    venue = rec.get("venue") or (f"arXiv preprint arXiv:{rec['arxiv_id']}" if rec.get("arxiv_id") else "")
    parts = [f"{authors} ({yr}). {title}."]
    if venue:
        v_str = venue
        if rec.get("volume"):
            v_str += f", {rec['volume']}"
            if rec.get("issue"):
                v_str += f"({rec['issue']})"
        if rec.get("pages"):
            v_str += f", {rec['pages']}"
        parts.append(f"{v_str}.")
    if rec.get("doi"):
        parts.append(f"https://doi.org/{rec['doi']}")
    elif rec.get("url"):
        parts.append(rec["url"])
    return " ".join(parts)


def format_single_ieee(rec: Dict[str, Any], index: int) -> str:
    authors = rec.get("authors", [])
    auth_str = f"{authors[0]} et al." if len(authors) > 6 else ", ".join(authors) if authors else "Anon."
    title = (rec.get("title") or "Untitled").rstrip(".")
    venue = rec.get("venue") or "Preprint"
    segs = [f'[{index}] {auth_str}, "{title},"', f"{venue}"]
    if rec.get("volume"):
        segs.append(f"vol. {rec['volume']}")
    if rec.get("issue"):
        segs.append(f"no. {rec['issue']}")
    if rec.get("pages"):
        segs.append(f"pp. {rec['pages']}")
    if rec.get("year"):
        segs.append(str(rec["year"]))
    out = ", ".join(segs) + "."
    if rec.get("doi"):
        out += f" doi: {rec['doi']}."
    return out


def format_single_bibtex(rec: Dict[str, Any]) -> str:
    etype = rec.get("entry_type", "article")
    if etype == "preprint":
        etype = "misc"
    fields: List[str] = []
    if rec.get("authors"):
        fields.append(f"  author = {{{' and '.join(rec['authors'])}}}")
    if rec.get("title"):
        fields.append(f"  title = {{{rec['title']}}}")
    if rec.get("venue"):
        vkey = "booktitle" if etype == "inproceedings" else "journal"
        fields.append(f"  {vkey} = {{{rec['venue']}}}")
    if rec.get("year"):
        fields.append(f"  year = {{{rec['year']}}}")
    if rec.get("volume"):
        fields.append(f"  volume = {{{rec['volume']}}}")
    if rec.get("issue"):
        fields.append(f"  number = {{{rec['issue']}}}")
    if rec.get("pages"):
        fields.append(f"  pages = {{{rec['pages']}}}")
    if rec.get("doi"):
        fields.append(f"  doi = {{{rec['doi']}}}")
    if rec.get("arxiv_id"):
        fields.append(f"  eprint = {{{rec['arxiv_id']}}}")
    return f"@{etype}{{{rec['cite_key']},\n" + ",\n".join(fields) + "\n}"


def format_library(records: List[Dict[str, Any]], style: str = "gbt7714") -> str:
    """Format an ordered list of canonical records in the requested style."""
    st = style.lower()
    if st == "apa7":
        ordered = sorted(records, key=lambda r: ((r.get("authors") or ["ZZZ"])[0].lower(), r.get("year") or 9999))
        return "\n".join(format_single_apa7(r) for r in ordered) + "\n"
    if st == "ieee":
        return "\n".join(format_single_ieee(r, i) for i, r in enumerate(records, start=1)) + "\n"
    if st == "bibtex":
        return "\n\n".join(format_single_bibtex(r) for r in records) + "\n"
    return "\n".join(format_single_gbt7714(r, i) for i, r in enumerate(records, start=1)) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Format canonical_library.json into target citation style.")
    parser.add_argument("--lib", required=True, help="Path to canonical_library.json")
    parser.add_argument("--style", choices=("gbt7714", "apa7", "ieee", "bibtex"), default="gbt7714")
    parser.add_argument("--out", required=True, help="Output formatted bibliography file")
    args = parser.parse_args()

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    rendered = format_library(lib.get("records", []), style=args.style)
    write_text_lf(Path(args.out), rendered)
    print(json.dumps({"style": args.style, "record_count": len(lib.get("records", [])), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
