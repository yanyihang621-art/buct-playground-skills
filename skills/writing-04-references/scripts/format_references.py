#!/usr/bin/env python3
"""Format canonical bibliographic records into GB/T 7714-2015, APA 7th, IEEE, BibTeX, or RIS."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import CJK_RE, normalize_doi, write_text_lf

GBT_TYPE_CODES = {
    "article": "J",
    "journal": "J",
    "inproceedings": "C",
    "conference": "C",
    "book": "M",
    "thesis": "D",
    "phdthesis": "D",
    "mastersthesis": "D",
    "dissertation": "D",
    "report": "R",
    "techreport": "R",
    "standard": "S",
    "patent": "P",
    "dataset": "DS",
    "webpage": "EB/OL",
    "online": "EB/OL",
    "preprint": "EB/OL",
    "electronic": "EB/OL",
    "misc": "Z",
}


def _organization(name: str) -> bool:
    return bool(re.search(r"\b(?:organization|organisation|association|institute|university|consortium|committee|"
                          r"collaboration|agency|society|group|team|council|department|laboratory|network)\b", name, re.I))


def _format_authors_gbt(authors: List[str], lang: str) -> str:
    if not authors:
        return "[佚名]" if lang == "zh" else "[Anon.]"
    etal = "等" if (lang == "zh" or any(CJK_RE.search(a) for a in authors)) else "et al"
    shown = []
    for author in authors[:3]:
        if CJK_RE.search(author) or _organization(author):
            shown.append(author)
        else:
            surname, given = _western_name(author)
            shown.append(" ".join(filter(None, [surname.upper(), " ".join(_initials(given))])))
    joined = ", ".join(shown)
    return f"{joined}, {etal}" if len(authors) > 3 else joined


def _western_name(author: str) -> tuple[str, str]:
    s = author.strip()
    if "," in s:
        surname, given = s.split(",", 1)
        return surname.strip(), given.strip()
    words = s.split()
    if not words:
        return "", ""
    start = len(words) - 1
    while start > 0 and words[start - 1] in ("al", "de", "du", "del", "der", "di", "la", "van", "von"):
        start -= 1
    return " ".join(words[start:]), " ".join(words[:start])


def _initials(given: str) -> List[str]:
    return [part[0].upper() for part in re.findall(r"[^\W\d_]+", given) if part]


def apa_author_family(author: str) -> str:
    """Use the full group name or a personal surname, including lowercase particles."""
    if _organization(author) or CJK_RE.search(author):
        return author.strip()
    return _western_name(author)[0]


def _surname_first_apa(author: str) -> str:
    if CJK_RE.search(author) or _organization(author):
        return author.strip()
    surname, given = _western_name(author)
    initials = " ".join("-".join(" ".join(letter + "." for letter in _initials(segment))
                                  for segment in re.split(r"[-‐‑]", word)) for word in given.split())
    return f"{surname}, {initials}" if initials else surname


def _format_authors_apa(authors: List[str]) -> str:
    if not authors:
        return "Anonymous"
    formatted = [_surname_first_apa(a) for a in authors]
    if len(formatted) > 20:
        return ", ".join(formatted[:19]) + ", … " + formatted[-1]
    if len(formatted) == 1:
        return formatted[0]
    return ", ".join(formatted[:-1]) + ", & " + formatted[-1]


def format_single_gbt7714(rec: Dict[str, Any], index: int) -> str:
    lang = rec.get("language", "en")
    authors = _format_authors_gbt(rec.get("authors", []), lang)
    title = (rec.get("title") or "[Untitled]").rstrip(".")
    tcode = GBT_TYPE_CODES.get(rec.get("entry_type", "article").lower(), "Z")
    venue = rec.get("venue") or (f"arXiv:{rec['arxiv_id']}" if rec.get("arxiv_id") else "[Source Unknown]")
    yr = str(rec.get("year") or "s.a.")

    if tcode == "EB/OL":
        ref_url = rec.get("url") or (f"https://arxiv.org/abs/{rec['arxiv_id']}" if rec.get("arxiv_id") else "")
        body = f"[{index}] {authors}. {title}[{tcode}]. {venue}, {yr}."
        if ref_url:
            body += f" {ref_url}."
        if rec.get("doi"):
            body += f" DOI:{rec['doi']}."
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
    elif rec.get("url"):
        out += f" {rec['url']}."
    return out


def _apa_order(rec: Dict[str, Any]) -> tuple:
    names = tuple(_surname_first_apa(a).casefold() for a in rec.get("authors", []))
    title = re.sub(r"^(the|an|a)\s+", "", str(rec.get("title") or "").casefold())
    return names or (title,), str(rec.get("year") or ""), title


def apa_year_labels(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """Return consistent year suffixes for identical author lists and publication year."""
    labels = {r["cite_key"]: str(r.get("year") or "n.d.") for r in records}
    groups = defaultdict(list)
    for rec in sorted(records, key=_apa_order):
        groups[_apa_order(rec)[:2]].append(rec["cite_key"])
    for keys in groups.values():
        if len(keys) < 2:
            continue
        for index, key in enumerate(keys, 1):
            suffix = ""
            while index:
                index, remainder = divmod(index - 1, 26)
                suffix = chr(97 + remainder) + suffix
            labels[key] += ("-" if labels[key] == "n.d." else "") + suffix
    return labels


def apa_segments(rec: Dict[str, Any], year_label: str = "") -> list[tuple[str, str]]:
    """Produce editable typography and plain text from the same APA reference elements."""
    authors = rec.get("authors") or []
    year = year_label or str(rec.get("year") or "n.d.")
    title = str(rec.get("title") or "Untitled").strip().rstrip(".")
    kind = str(rec.get("entry_type") or "article").lower()
    standalone = kind in {"book", "ebook", "monograph", "dataset", "report", "techreport", "standard",
                          "preprint", "thesis", "dissertation", "phdthesis", "mastersthesis", "online", "webpage"}
    pieces = [("plain", _format_authors_apa(authors) + f" ({year}). ")] if authors else []
    pieces.append(("italic" if standalone else "plain", title))
    details = []
    if kind in {"book", "ebook", "monograph"} and rec.get("edition"):
        edition = str(rec["edition"])
        if edition.isdigit():
            number = int(edition)
            suffix = "th" if 11 <= number % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
            edition = f"{number}{suffix} ed."
        details.append(f" ({edition})")
    if kind in {"report", "techreport"} and rec.get("report_number"):
        details.append(f" (Report No. {rec['report_number']})")
    descriptor = {"dataset": "Data set", "preprint": "Preprint", "phdthesis": "Doctoral dissertation",
                  "mastersthesis": "Master’s thesis"}.get(kind)
    if descriptor:
        details.append(f" [{descriptor}]")
    end = "" if not details and title.endswith(("?", "!", "。", "？", "！", "…")) else "."
    pieces.append(("plain", "".join(details) + end + (f" ({year})." if not authors else "")))
    venue = str(rec.get("venue") or "").strip()
    pages = re.sub(r"(?<=[\w])\s*[-–—]+\s*(?=[\w])", "–", str(rec.get("pages") or ""))
    if standalone:
        source = str(rec.get("publisher") or venue or ("arXiv" if rec.get("arxiv_id") else "")).strip()
        if source and not (len(authors) == 1 and source.casefold() == authors[0].strip().casefold()):
            pieces.append(("plain", " " + source.rstrip(".") + "."))
    elif kind in {"inbook", "incollection", "chapter"} and venue:
        pieces.extend([("plain", " In "), ("italic", venue)])
        pieces.append(("plain", (f" (pp. {pages})" if pages else "") + "."))
        if rec.get("publisher") and rec["publisher"] != venue:
            pieces.append(("plain", f" {rec['publisher']}."))
    elif venue:
        pieces.extend([("plain", " "), ("italic", venue)])
        if rec.get("volume"):
            pieces.extend([("plain", ", "), ("italic", str(rec["volume"]))])
        if rec.get("issue"):
            pieces.append(("plain", ("(" if rec.get("volume") else " (") + str(rec["issue"]) + ")"))
        location = f"Article {rec['article_number']}" if rec.get("article_number") else pages
        pieces.append(("plain", (", " + location if location else "") + "."))
    doi = normalize_doi(rec.get("doi"))
    url = "https://doi.org/" + doi if doi else rec.get("url")
    if url:
        pieces.extend([("plain", " "), ("link", str(url))])
    return pieces


def format_single_apa7(rec: Dict[str, Any], year_label: str = "") -> str:
    return "".join(text for _, text in apa_segments(rec, year_label))


def apa_entries(records: list[dict]) -> list[tuple[dict, list]]:
    labels = apa_year_labels(records)
    return [(record, apa_segments(record, labels[record["cite_key"]])) for record in sorted(records, key=_apa_order)]


def _markdown_apa(parts):
    def escape(text):
        return re.sub(r"([\\*`_\[\]<>])", r"\\\1", text)
    return "".join(f"*{escape(text)}*" if role == "italic" else
                   f"<{text}>" if role == "link" and re.fullmatch(r"https?://[^\s<>]+", text) else escape(text)
                   for role, text in parts)


def format_single_ieee(rec: Dict[str, Any], index: int) -> str:
    authors = rec.get("authors", [])
    names = []
    for author in authors:
        surname, given = _western_name(author)
        initials = " ".join(f"{c}." for c in _initials(given))
        names.append(author if CJK_RE.search(author) or _organization(author) else f"{initials} {surname}".strip())
    if len(names) > 6:
        auth_str = names[0] + " et al."
    elif len(names) > 2:
        auth_str = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        auth_str = " and ".join(names) or "Anon."
    title = (rec.get("title") or "Untitled").rstrip(".")
    venue = rec.get("venue") or ""
    segs = [venue] if venue else []
    if rec.get("volume"):
        segs.append(f"vol. {rec['volume']}")
    if rec.get("issue"):
        segs.append(f"no. {rec['issue']}")
    if rec.get("pages"):
        segs.append(f"pp. {rec['pages']}")
    if rec.get("year"):
        segs.append(str(rec["year"]))
    out = f'[{index}] {auth_str}, “{title},” ' + ", ".join(segs) + "." if segs else f'[{index}] {auth_str}, “{title}.”'
    if rec.get("doi"):
        out += f" doi: {rec['doi']}."
    elif rec.get("url"):
        out += f" [Online]. Available: {rec['url']}"
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
        vkey = {"inproceedings": "booktitle", "conference": "booktitle", "book": "publisher",
                "phdthesis": "school", "mastersthesis": "school", "techreport": "institution"}.get(etype, "journal")
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
    if rec.get("url"):
        fields.append(f"  url = {{{rec['url']}}}")
    return f"@{etype}{{{rec['cite_key']},\n" + ",\n".join(fields) + "\n}"


def format_single_ris(rec: Dict[str, Any]) -> str:
    kind = {"article": "JOUR", "journal": "JOUR", "book": "BOOK", "inproceedings": "CPAPER",
            "conference": "CPAPER", "thesis": "THES", "dataset": "DATA", "report": "RPRT"}.get(rec.get("entry_type"), "GEN")
    rows = [f"TY  - {kind}", f"ID  - {rec['cite_key']}"]
    rows += ["AU  - " + name for name in rec.get("authors", [])]
    for field, tag in (("title", "TI"), ("venue", "T2"), ("year", "PY"), ("volume", "VL"), ("issue", "IS"),
                       ("doi", "DO"), ("url", "UR"), ("abstract", "AB")):
        if rec.get(field):
            rows.append(f"{tag}  - {rec[field]}")
    if rec.get("pages"):
        parts = re.split(r"\s*[-–—]+\s*", str(rec["pages"]), maxsplit=1)
        rows.append("SP  - " + parts[0])
        if len(parts) > 1:
            rows.append("EP  - " + parts[1])
    rows.extend("KW  - " + word for word in rec.get("keywords", []))
    return "\n".join(rows + ["ER  - "])


def format_library(records: List[Dict[str, Any]], style: str = "gbt7714") -> str:
    """Format an ordered list of canonical records in the requested style."""
    st = style.lower()
    if st in ("apa7", "apa7-markdown"):
        entries = apa_entries(records)
        if st == "apa7-markdown":
            return "\n\n".join(_markdown_apa(parts) for _, parts in entries) + "\n"
        return "\n".join("".join(text for _, text in parts) for _, parts in entries) + "\n"
    if st == "ieee":
        return "\n".join(format_single_ieee(r, i) for i, r in enumerate(records, start=1)) + "\n"
    if st == "bibtex":
        return "\n\n".join(format_single_bibtex(r) for r in records) + "\n"
    if st == "ris":
        return "\n\n".join(format_single_ris(r) for r in records) + "\n"
    return "\n".join(format_single_gbt7714(r, i) for i, r in enumerate(records, start=1)) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Format canonical_library.json into target citation style.")
    parser.add_argument("--lib", required=True, help="Path to canonical_library.json")
    parser.add_argument("--style", choices=("gbt7714", "apa7", "apa7-markdown", "ieee", "bibtex", "ris"), default="gbt7714")
    parser.add_argument("--out", required=True, help="Output formatted bibliography file")
    args = parser.parse_args()

    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    rendered = format_library(lib.get("records", []), style=args.style)
    write_text_lf(Path(args.out), rendered)
    print(json.dumps({"style": args.style, "record_count": len(lib.get("records", [])), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
