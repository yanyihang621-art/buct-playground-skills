#!/usr/bin/env python3
"""Pure-Python multi-format bibliographic parser (.bib, .ris, .enw, .json, .txt)."""

from __future__ import annotations

import argparse
import csv
import io
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

BIB_ENTRY_HEAD_RE = re.compile(r"@([A-Za-z]+)\s*([({])\s*([^,\s]+)\s*,")
PLAIN_REF_HEAD_RE = re.compile(r"^\s*(?:[\[［](\d+)[\]］]|(\d{1,4})[.、)]\s+)\s*(\S.*)$")


def _extract_balanced_braces(text: str, pos: int) -> Tuple[str, int]:
    depth = 0
    start = pos
    i = pos
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
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
    return parse_bibtex_text(read_source_text(path), path.name)


def parse_bibtex_text(text: str, source: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    pos = 0
    while True:
        m = BIB_ENTRY_HEAD_RE.search(text, pos)
        if not m:
            break
        etype = m.group(1).lower()
        key = m.group(3).strip()
        line_no = text.count("\n", 0, m.start()) + 1
        if m.group(2) == "{":
            full_entry, pos = _extract_balanced_braces(text, m.start(2))
            body = full_entry.partition(",")[2]
        else:
            # A parenthesized outer entry may still use nested {braced values}.
            start = pos = m.end()
            while pos < len(text):
                if text[pos] == "{":
                    _, pos = _extract_balanced_braces(text, pos)
                elif text[pos] == '"':
                    _, pos = _extract_quoted_value(text, pos)
                elif text[pos] == ")":
                    break
                else:
                    pos += 1
            body = text[start:pos]
            pos += 1
        if etype in ("string", "preamble", "comment"):
            continue
        f = _parse_bib_fields(body)
        title = f.get("title", "").replace("{", "").replace("}", "")
        authors = split_authors_raw(f.get("author", ""))
        venue = f.get("journal") or f.get("journaltitle") or f.get("booktitle") or f.get("publisher") or f.get("school") or ""
        if f.get("archiveprefix", "").lower() == "arxiv" or "arxiv" in venue.lower():
            etype = "preprint"
        arxiv_id = extract_arxiv_id(f.get("eprint") or f.get("arxivid")) or extract_arxiv_id(venue) or extract_arxiv_id(f.get("url"))
        kw = [k.strip() for k in re.split(r"[,;]", f.get("keywords", "")) if k.strip()]
        records.append(
            _make_record(
                cite_key=key,
                entry_type=etype,
                title=title,
                authors=authors,
                year=parse_year(f.get("year") or f.get("date")),
                venue=venue,
                volume=f.get("volume"),
                issue=f.get("number") or f.get("issue"),
                pages=f.get("pages"),
                doi=f.get("doi"),
                arxiv_id=arxiv_id,
                url=f.get("url"),
                keywords=kw,
                abstract=f.get("abstract"),
                source_file=source,
                line_no=line_no,
                raw_format="bibtex",
            )
        )
    return records


def parse_ris_file(path: Path) -> List[Dict[str, Any]]:
    return parse_ris_text(read_source_text(path), path.name)


def parse_ris_text(text: str, source: str) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    start_line = 1
    ris_type_map = {"JOUR": "article", "CONF": "inproceedings", "CPAPER": "inproceedings", "BOOK": "book", "THES": "thesis"}

    if lines and not lines[-1].strip().startswith("ER"):
        lines.append("ER  -")
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
                    source_file=source,
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
    return parse_enw_text(read_source_text(path), path.name)


def parse_enw_text(text: str, source: str) -> List[Dict[str, Any]]:
    """Parse EndNote Tagged (.enw) exports (used by CNKI / Web of Science / Google Scholar)."""
    lines = text.splitlines()
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
                source_file=source,
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
        if tag == "0" and cur and cur.get("title"):
            _flush()
            cur = None
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


def read_source_text(path: Path) -> str:
    payload = path.read_bytes()
    encodings = ("utf-16",) if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030", "cp1252")
    for encoding in encodings:
        try:
            return payload.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise ValueError("undecodable_text")


def _string(value: Any) -> str:
    if isinstance(value, list):
        return _string(value[0]) if value else ""
    return str(value).strip() if isinstance(value, (int, str)) else ""


def _author_names(value: Any) -> List[str]:
    if isinstance(value, str):
        return split_authors_raw(value)
    names = []
    for author in value if isinstance(value, list) else []:
        if isinstance(author, dict):
            name = _string(author.get("literal") or author.get("name"))
            if not name:
                name = ", ".join(filter(None, (_string(author.get("family")), _string(author.get("given")))))
        else:
            name = _string(author)
        if name:
            names.append(name)
    return names


def parse_json_records(data: Any, source: str, pointer: str = "") -> List[Dict[str, Any]]:
    """Keep original case-sensitive cite keys and exact JSON locations, including CSL-JSON."""
    if isinstance(data, dict):
        field_names = {str(k).casefold() for k in data}
        if not field_names.intersection({"title", "标题", "题名", "doi", "arxiv_id"}):
            for wrapper in ("records", "references", "bibliography", "items", "entries", "library", "message"):
                if wrapper in data:
                    return parse_json_records(data[wrapper], source, f"{pointer}/{wrapper}")
            out = []
            for key, value in data.items():
                if isinstance(value, dict) and any(str(k).casefold() in ("title", "题名", "标题") for k in value):
                    token = str(key).replace("~", "~0").replace("/", "~1")
                    row = {"cite_key": key, **value}
                    out.extend(parse_json_records(row, source, f"{pointer}/{token}"))
            return out
        indexed = [(pointer, data)]
    elif isinstance(data, list):
        indexed = [(f"{pointer}/{i}", item) for i, item in enumerate(data)]
    else:
        return []
    out = []
    for location, item in indexed:
        if isinstance(item, str):
            recs = parse_plain_text(item, source)
            for rec in recs:
                rec["source_provenance"][0]["json_pointer"] = location
            out.extend(recs)
            continue
        if not isinstance(item, dict):
            continue
        row = {str(k).lower(): v for k, v in item.items()}
        if not any(k in row for k in ("title", "题名", "标题", "doi", "arxiv_id")):
            continue
        pick = lambda *keys: next((row[k] for k in keys if row.get(k) not in (None, "", [])), None)
        year_value = pick("year", "issued", "published", "date", "年份", "年")
        if isinstance(year_value, dict):
            year_value = year_value.get("date-parts") or year_value.get("raw")
        keywords = pick("keywords", "keyword", "关键词") or []
        if not isinstance(keywords, list):
            keywords = re.split(r"\s*[;；,]\s*", str(keywords))
        entry_type = _string(pick("entry_type", "type")) or "article"
        entry_type = {"article-journal": "article", "paper-conference": "inproceedings"}.get(entry_type, entry_type)
        rec = _make_record(
            cite_key=_string(pick("cite_key", "raw_key", "citation-key", "id", "key", "ref_id", "citation_key")) or f"json_{len(out) + 1}",
            entry_type=entry_type,
            title=_string(pick("title", "题名", "标题")),
            authors=_author_names(pick("authors", "author", "作者")),
            year=parse_year(year_value),
            venue=_string(pick("venue", "container-title", "journal", "booktitle", "publisher", "期刊", "刊名")),
            volume=_string(pick("volume", "卷")),
            issue=_string(pick("issue", "number", "期")),
            pages=_string(pick("pages", "page", "页码")),
            doi=_string(pick("doi")),
            arxiv_id=_string(pick("arxiv_id", "eprint")),
            url=_string(pick("url")),
            keywords=[str(word) for word in keywords if word],
            abstract=_string(pick("abstract", "摘要")),
            source_file=source, line_no=len(out) + 1, raw_format="json",
        )
        origin = rec["source_provenance"][0]
        origin.pop("line")  # The array ordinal is not a source line number.
        origin["json_pointer"] = location
        label = pick("reference_number", "original_number", "label_number", "序号")
        if label and str(label).isdigit():
            origin["original_number"] = int(label)
        out.append(rec)
    return out


def parse_plain_text(text: str, source: str) -> List[Dict[str, Any]]:
    """Recover explicitly numbered or APA author-year entries; preserve their unsplit originals."""
    groups: list = []
    for idx, line in enumerate(text.splitlines(), 1):
        m = PLAIN_REF_HEAD_RE.match(line)
        if m:
            groups.append([idx, int(m.group(1) or m.group(2)), m.group(3)])
        elif re.match(r"^\S.{1,170}?\(\s*(?:18|19|20)\d{2}[a-z]?\s*\)\s*[.。]", line):
            groups.append([idx, None, line.strip()])
        elif groups and line.strip() and not re.match(r"^\s*(?:#{1,6}\s|\[?附录|Appendix\b)", line, re.I):
            groups[-1][2] += " " + line.strip()
    records = []
    for idx, label, body in groups:
        doi_match = re.search(r"10\.\d{4,9}/[^\s，。<>\"]+", body, re.I)
        doi = doi_match.group().rstrip(".,;") if doi_match else None
        if doi and doi.endswith(")") and doi.count(")") > doi.count("("):
            doi = doi[:-1]
        fields = body[:doi_match.start()] if doi_match else body
        fields = re.sub(r"(?i)(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)$", "", fields).strip().rstrip(".,;")
        apa = re.match(r"^(.+?)\s*\(\s*((?:18|19|20)\d{2})[a-z]?\s*\)\s*[.。]\s*(.+)", fields)
        if apa:
            author_text, year_text, remainder = apa.groups()
        else:
            # An initial before a surname is not the end of the authors field ("I. Vale. ...").
            # In a family-first list the last initial may also be its terminal period.
            # Keep the first period after a non-initial word, where available.
            author_boundary = re.search(r"(?<!\b[A-Z])\.\s+|。\s*|\.(?=[\u4e00-\u9fff])", fields)
            parts = [fields[:author_boundary.start()], fields[author_boundary.end():]] if author_boundary else [fields]
            author_text, remainder = (parts if len(parts) == 2 else ("", ""))
            year_text = fields
        type_match = re.match(r"(.+?)\s*\[([A-Za-z/]+)\]\s*[,，.。]?\s*(.*)", remainder)
        if type_match:
            title, letter, venue_text = type_match.groups()
        else:
            parts = re.split(r"\.\s+|。\s*", remainder, maxsplit=1)
            title, letter, venue_text = parts[0], "", parts[1] if len(parts) == 2 else ""
        venue = re.split(r"[,，]\s*(?=\d|(?:18|19|20)\d{2})", venue_text, maxsplit=1)[0].strip(" ,.")
        vol = re.search(r"[,，]\s*(?:\d{4}\s*[,，]\s*)?(\d+)\s*(?:\(([^)]+)\))?\s*[:：,，]\s*(\d+(?:[-–—]+\d+)?)", venue_text)
        rec = _make_record(
            cite_key=f"txt_{label if label is not None else len(records)+1}",
            entry_type={"M": "book", "C": "inproceedings", "D": "thesis", "EB/OL": "electronic"}.get(letter.upper(), "article"),
            title=title.strip(), authors=split_authors_raw(author_text.strip(" .。")), year=parse_year(year_text),
            venue=venue, volume=vol.group(1) if vol else None, issue=vol.group(2) if vol else None,
            pages=vol.group(3) if vol else None, doi=doi,
            arxiv_id=extract_arxiv_id(body) if "arxiv" in body.lower() else None, url=None, keywords=[],
            abstract=None, source_file=source, line_no=idx, raw_format="plain_txt",
        )
        origin = rec["source_provenance"][0]
        origin["verbatim"] = body
        if label is not None:
            origin["original_number"] = label
        records.append(rec)
    return records


def parse_refworks_text(text: str, source: str) -> list:
    """Read the tagged RefWorks text export offered by CNKI."""
    records = []
    row = {}
    start = 1
    tags = {"T1": "title", "TI": "title", "JF": "journal", "JO": "journal",
            "YR": "year", "FD": "date", "VO": "volume", "IS": "issue", "DS": "database",
            "SP": "pages", "DO": "doi", "LK": "url", "AB": "abstract", "ID": "id"}

    def flush():
        for rec in parse_json_records(row, source):
            origin = rec["source_provenance"][0]
            if not row.get("id"):
                rec["raw_key"] = origin["raw_key"] = f"refworks_{len(records) + 1}"
            origin.pop("json_pointer", None)
            origin.update(format="refworks", line=start)
            records.append(rec)

    for i, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^([A-Z][A-Z0-9])\s+(.+)$", line)
        if not m:
            continue
        tag, value = m.group(1), m.group(2).strip()
        if tag == "RT":
            if row:
                flush()
            row = {"authors": [], "keywords": [], "type": "article" if "journal" in value.lower() else value}
            start = i
        elif tag in ("A1", "AU"):
            row.setdefault("authors", []).extend(s.strip() for s in re.split(r"[;；]", value) if s.strip())
        elif tag == "K1":
            row.setdefault("keywords", []).append(value)
        elif tag in tags:
            row[tags[tag]] = value
    flush()
    return records


def parse_text_source(text: str, source: str, suffix: str = "") -> List[Dict[str, Any]]:
    sample = text.lstrip("\ufeff \n\r\t")
    if suffix == ".bib" or BIB_ENTRY_HEAD_RE.search(sample):
        return parse_bibtex_text(sample, source)
    if suffix == ".ris" or re.match(r"TY\s*-\s*", sample):
        return parse_ris_text(sample, source)
    if suffix == ".enw" or re.match(r"%[0AT]\s+", sample):
        return parse_enw_text(sample, source)
    if re.match(r"RT\s+\S", sample):
        return parse_refworks_text(sample, source)
    if suffix in (".csv", ".tsv"):
        reader = csv.DictReader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
        records = []
        for row in reader:
            for rec in parse_json_records(row, source):
                origin = rec["source_provenance"][0]
                origin.pop("json_pointer", None)
                origin.update(format=suffix[1:], line=reader.line_num)
                if rec["raw_key"] == "json_1" and not any(row.get(key) for key in ("id", "cite_key", "key", "raw_key")):
                    rec["raw_key"] = origin["raw_key"] = f"row_{reader.line_num}"
                records.append(rec)
        return records
    if suffix == ".json" or sample.startswith(("{", '[{', '[\n')):
        return parse_json_records(json.loads(sample), source)
    return parse_plain_text(text, source)


def parse_plain_or_json(path: Path) -> List[Dict[str, Any]]:
    return parse_text_source(read_source_text(path), path.name, path.suffix.lower())


def make_raw_library(records: list, sources: list, errors: list) -> dict:
    for idx, rec in enumerate(records, 1):
        rec["raw_id"] = f"raw_{idx:04d}"
    return {"schema_version": "writing-04-raw-library/v1", "source_files": list(dict.fromkeys(sources)),
            "record_count": len(records), "parse_errors": errors, "records": records}


def parse_library_sources(paths: List[Path]) -> Dict[str, Any]:
    records, errors = [], []
    for path in paths:
        try:
            recs = parse_text_source(read_source_text(path), str(path), path.suffix.lower())
            if not recs:
                raise ValueError("zero_records_parsed")
            records.extend(recs)
        except (ValueError, OSError, csv.Error) as exc:
            errors.append({"file": str(path), "error": str(exc)})
    return make_raw_library(records, [str(p) for p in paths], errors)

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
