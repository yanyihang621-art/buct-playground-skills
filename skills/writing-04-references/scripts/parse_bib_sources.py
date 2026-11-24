#!/usr/bin/env python3
"""Pure-Python multi-format bibliographic parser (.bib, .ris, .enw, .json, .txt)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import (
    detect_language,
    extract_arxiv_id,
    normalize_doi,
    parse_year,
    split_authors_raw,
    write_json_atomic,
)

BIB_ENTRY_HEAD_RE = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")
PLAIN_REF_HEAD_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s+(.+)$")


def _extract_balanced_braces(text: str, pos: int) -> Tuple[str, int]:
    depth = 0
    start = pos
    i = pos
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _extract_quoted_value(text: str, pos: int) -> Tuple[str, int]:
    i = pos + 1
    out: List[str] = []
    while i < len(text):
        ch = text[i]
        if ch == '"' and text[i - 1] != "\\":
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    return "".join(out), len(text)


def _parse_bib_fields(body: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        m = re.match(r"([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*", body[i:])
        if not m:
            break
        key = m.group(1).lower()
        i += m.end()
        if i >= n:
            break
        if body[i] == "{":
            val, i = _extract_balanced_braces(body, i)
        elif body[i] == '"':
            val, i = _extract_quoted_value(body, i)
        else:
            m2 = re.match(r"([^,\r\n}]+)", body[i:])
            val = m2.group(1) if m2 else ""
            i += len(val)
        fields[key] = re.sub(r"\s+", " ", val).strip()
    return fields


def _make_record(
    *,
    cite_key: str,
    entry_type: str,
    title: str,
    authors: List[str],
    year: Optional[int],
    venue: Optional[str],
    volume: Optional[str],
    issue: Optional[str],
    pages: Optional[str],
    doi: Optional[str],
    arxiv_id: Optional[str],
    url: Optional[str],
    keywords: Optional[List[str]],
    abstract: Optional[str],
    source_file: str,
    line_no: int,
    raw_format: str,
) -> Dict[str, Any]:
    missing: List[str] = []
    if not title:
        missing.append("title")
    if not authors:
        missing.append("authors")
    if year is None:
        missing.append("year")
    if not venue and not arxiv_id:
        missing.append("venue")
    if not doi and not arxiv_id and not url:
        missing.append("identifier")

    return {
        "raw_key": cite_key,
        "entry_type": entry_type.lower(),
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue or None,
        "volume": volume or None,
        "issue": issue or None,
        "pages": pages or None,
        "doi": normalize_doi(doi),
        "arxiv_id": extract_arxiv_id(arxiv_id or url),
        "url": url or None,
        "keywords": keywords or [],
        "abstract": abstract or None,
        "language": detect_language(title + " " + " ".join(authors)),
        "missing_fields": missing,
        "source_provenance": [
            {
                "source_file": source_file,
                "format": raw_format,
                "line": line_no,
                "raw_key": cite_key,
            }
        ],
    }


def parse_bibtex_file(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records: List[Dict[str, Any]] = []
    pos = 0
    while True:
        m = BIB_ENTRY_HEAD_RE.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower()
        key = m.group(2).strip()
        line_no = text.count("\n", 0, m.start()) + 1
        brace_pos = text.find("{", m.start())
        full_entry, next_pos = _extract_balanced_braces(text, brace_pos)
        pos = next_pos
        if etype in ("string", "preamble", "comment"):
            continue
        comma_idx = full_entry.find(",")
        body = full_entry[comma_idx + 1 :] if comma_idx >= 0 else ""
        f = _parse_bib_fields(body)
        title = f.get("title", "").replace("{", "").replace("}", "")
        authors = split_authors_raw(f.get("author", ""))
        venue = f.get("journal") or f.get("booktitle") or f.get("publisher") or ""
        if f.get("archiveprefix", "").lower() == "arxiv" or "arxiv" in venue.lower():
            etype = "preprint"
        arxiv_id = extract_arxiv_id(f.get("eprint") or f.get("arxivid") or venue or f.get("url"))
        kw = [k.strip() for k in re.split(r"[,;]", f.get("keywords", "")) if k.strip()]
        records.append(
            _make_record(
                cite_key=key,
                entry_type=etype,
                title=title,
                authors=authors,
                year=parse_year(f.get("year")),
                venue=venue,
                volume=f.get("volume"),
                issue=f.get("number") or f.get("issue"),
                pages=f.get("pages"),
                doi=f.get("doi"),
                arxiv_id=arxiv_id,
                url=f.get("url"),
                keywords=kw,
                abstract=f.get("abstract"),
                source_file=path.name,
                line_no=line_no,
                raw_format="bibtex",
            )
        )
    return records


def parse_ris_file(path: Path) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    start_line = 1
    ris_type_map = {"JOUR": "article", "CONF": "inproceedings", "CPAPER": "inproceedings", "BOOK": "book", "THES": "thesis"}

    for idx, line in enumerate(lines, start=1):
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"authors": [], "keywords": [], "type": ris_type_map.get(val.upper(), "article")}
            start_line = idx
        elif tag == "ER" and cur is not None:
            idx_num = len(records) + 1
            pages = cur.get("sp") or ""
            if cur.get("ep") and pages:
                pages = f"{pages}-{cur['ep']}"
            records.append(
                _make_record(
                    cite_key=cur.get("id") or f"ris_{idx_num}",
                    entry_type=cur["type"],
                    title=cur.get("title", ""),
                    authors=cur["authors"],
                    year=parse_year(cur.get("year")),
                    venue=cur.get("venue"),
                    volume=cur.get("vl"),
                    issue=cur.get("is"),
                    pages=pages or None,
                    doi=cur.get("do"),
                    arxiv_id=extract_arxiv_id(cur.get("url") or cur.get("venue")),
                    url=cur.get("url"),
                    keywords=cur["keywords"],
                    abstract=cur.get("ab"),
                    source_file=path.name,
                    line_no=start_line,
                    raw_format="ris",
                )
            )
            cur = None
        elif cur is not None:
            if tag in ("AU", "A1"):
                cur["authors"].append(val)
            elif tag in ("TI", "T1"):
                cur["title"] = val
            elif tag in ("JO", "T2", "JF"):
                cur["venue"] = val
            elif tag in ("PY", "Y1"):
                cur["year"] = val
            elif tag == "VL":
                cur["vl"] = val
            elif tag == "IS":
                cur["is"] = val
            elif tag == "SP":
                cur["sp"] = val
            elif tag == "EP":
                cur["ep"] = val
            elif tag == "DO":
                cur["do"] = val
            elif tag in ("UR", "L2"):
                cur["url"] = val
            elif tag == "ID":
                cur["id"] = val
            elif tag == "KW":
                cur["keywords"].append(val)
            elif tag == "AB":
                cur["ab"] = val
    return records


def parse_enw_file(path: Path) -> List[Dict[str, Any]]:
    """Parse EndNote Tagged (.enw) exports (used by CNKI / Web of Science / Google Scholar)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    start_line = 1

    def _flush() -> None:
        if not cur or not cur.get("title"):
            return
        idx_num = len(records) + 1
        records.append(
            _make_record(
                cite_key=cur.get("label") or f"enw_{idx_num}",
                entry_type=cur.get("type", "article"),
                title=cur.get("title", ""),
                authors=cur["authors"],
                year=parse_year(cur.get("year")),
                venue=cur.get("venue"),
                volume=cur.get("vl"),
                issue=cur.get("is"),
                pages=cur.get("pages"),
                doi=cur.get("doi"),
                arxiv_id=extract_arxiv_id(cur.get("url") or cur.get("venue")),
                url=cur.get("url"),
                keywords=cur["keywords"],
                abstract=cur.get("ab"),
                source_file=path.name,
                line_no=start_line,
                raw_format="enw",
            )
        )

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            _flush()
            cur = None
            continue
        m = re.match(r"^%([A-Za-z0-9])\s+(.*)$", s)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if cur is None:
            cur = {"authors": [], "keywords": [], "type": "article"}
            start_line = idx
        if tag == "0":
            cur["type"] = "inproceedings" if "conference" in val.lower() else ("thesis" if "thesis" in val.lower() else "article")
        elif tag == "A":
            cur["authors"].append(val)
        elif tag == "T":
            cur["title"] = val
        elif tag in ("J", "B"):
            cur["venue"] = val
        elif tag == "D":
            cur["year"] = val
        elif tag == "V":
            cur["vl"] = val
        elif tag == "N":
            cur["is"] = val
        elif tag == "P":
            cur["pages"] = val
        elif tag == "R":
            cur["doi"] = val
        elif tag == "U":
            cur["url"] = val
        elif tag == "K":
            cur["keywords"].append(val)
        elif tag == "F":
            cur["label"] = val
        elif tag == "X":
            cur["ab"] = val
    _flush()
    return records


def parse_plain_or_json(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        items = data.get("records", data) if isinstance(data, dict) else data
        out: List[Dict[str, Any]] = []
        if isinstance(items, list):
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                out.append(
                    _make_record(
                        cite_key=str(item.get("cite_key") or item.get("raw_key") or f"json_{idx}"),
                        entry_type=str(item.get("entry_type", "article")),
                        title=str(item.get("title", "")),
                        authors=list(item.get("authors", [])) if isinstance(item.get("authors"), list) else split_authors_raw(str(item.get("authors", ""))),
                        year=parse_year(item.get("year")),
                        venue=item.get("venue"),
                        volume=item.get("volume"),
                        issue=item.get("issue"),
                        pages=item.get("pages"),
                        doi=item.get("doi"),
                        arxiv_id=item.get("arxiv_id"),
                        url=item.get("url"),
                        keywords=list(item.get("keywords", [])),
                        abstract=item.get("abstract"),
                        source_file=path.name,
                        line_no=idx,
                        raw_format="json",
                    )
                )
        return out

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        m = PLAIN_REF_HEAD_RE.match(line)
        if not m:
            continue
        num = m.group(1) or m.group(2)
        body = m.group(3).strip()
        doi_m = re.search(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", body)
        yr = parse_year(body)
        parts = [p.strip() for p in re.split(r"[.。]", body) if p.strip()]
        authors = split_authors_raw(parts[0]) if parts else []
        title = parts[1] if len(parts) > 1 else body
        venue = parts[2] if len(parts) > 2 else None
        records.append(
            _make_record(
                cite_key=f"txt_{num}",
                entry_type="article",
                title=title,
                authors=authors,
                year=yr,
                venue=venue,
                volume=None,
                issue=None,
                pages=None,
                doi=doi_m.group(1) if doi_m else None,
                arxiv_id=extract_arxiv_id(body),
                url=None,
                keywords=[],
                abstract=None,
                source_file=path.name,
                line_no=idx,
                raw_format="plain_txt",
            )
        )
    return records


def parse_library_sources(paths: List[Path]) -> Dict[str, Any]:
    """Parse multiple bibliographic source files into a unified raw_library object."""
    all_records: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for p in paths:
        if not p.exists():
            errors.append({"file": str(p), "error": "file_not_found"})
            continue
        suf = p.suffix.lower()
        try:
            if suf == ".bib":
                recs = parse_bibtex_file(p)
            elif suf == ".ris":
                recs = parse_ris_file(p)
            elif suf == ".enw":
                recs = parse_enw_file(p)
            else:
                recs = parse_plain_or_json(p)
            if not recs:
                errors.append({"file": str(p), "error": "zero_records_parsed"})
            all_records.extend(recs)
        except Exception as exc:
            errors.append({"file": str(p), "error": str(exc)})

    for idx, rec in enumerate(all_records, start=1):
        rec["raw_id"] = f"raw_{idx:04d}"

    return {
        "schema_version": "writing-04-raw-library/v1",
        "source_files": [str(p) for p in paths],
        "record_count": len(all_records),
        "parse_errors": errors,
        "records": all_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse BibTeX, RIS, EndNote, JSON, or plain reference files.")
    parser.add_argument("--inputs", nargs="+", required=True, help="One or more bibliography files")
    parser.add_argument("--out", required=True, help="Output raw_library.json path")
    args = parser.parse_args()

    res = parse_library_sources([Path(x) for x in args.inputs])
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"record_count": res["record_count"], "parse_errors": res["parse_errors"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
