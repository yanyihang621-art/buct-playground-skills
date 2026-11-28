#!/usr/bin/env python3
"""Extract citeable passages from PDF (column-aware via parse_pdf), DOCX, BibTeX, RIS, JATS XML, XLSX, and plain text."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from parse_pdf import run_parse_pipeline

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"

REF_HEADING = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:references|bibliography|参考文献|文献目录)\s*[:：]?\s*$")
YEAR = r"(?:18|19|20)\d{2}"
GB_VOLUME = r"\d+(?:\s*[（(][\w\-]+[)）])?\s*[:：,，]\s*\d+(?:\s*[-–—]\s*\d+)?"
CN_ENTRY = re.compile(
    rf"^(?P<authors>[\u3400-\u9fff、，,；;·\s]{{2,65}}?)[.。]\s*"
    rf"(?P<title>[^.。]{{2,180}}?)(?:\s*\[[Jj]\])?\s*[.。]\s*"
    rf"(?P<venue>[^.。,，；;:]{{2,85}}?)\s*[,，]\s*(?P<year>{YEAR})\s*[,，]\s*{GB_VOLUME}[.。]?$"
)
APA_ENTRY = re.compile(
    rf"^(?P<authors>.{{3,180}}?)\s*\((?P<year>{YEAR})\)\.\s+"
    r"(?P<title>[^.]{3,200})\.\s+(?P<venue>[^.,]{3,100}?)(?:,\s*\d.*)?\.$"
)
APA_AUTHOR = re.compile(r"[^\W\d_][\w'’\- ]{0,60},\s*(?:[A-Z]\.\s*){1,4}")
LABELS = {
    "author": "authors", "authors": "authors", "作者": "authors",
    "title": "title", "题名": "title", "标题": "title",
    "year": "year", "publication year": "year", "年份": "year", "出版年": "year",
    "journal": "venue", "venue": "venue", "期刊": "venue", "刊名": "venue",
}


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace")


def _authors(text: str) -> List[str]:
    cn = re.split(r"\s*[,，、;；]\s*", text.strip())
    if all(re.fullmatch(r"[\u3400-\u9fff]{2,5}", name) for name in cn):
        if any(name in {"作者不详", "佚名", "无署名", "匿名", "未知"} for name in cn):
            return []
        return cn
    matches = list(APA_AUTHOR.finditer(text))
    if matches:
        sep = APA_AUTHOR.sub("|", text)
        if re.fullmatch(r"\s*\|(?:\s*(?:,\s*(?:&\s*)?|&|and)\s*\|)*\s*", sep):
            return [m.group().strip() for m in matches]
    return []


def _citation_header(line: str) -> Optional[Dict[str, Any]]:
    line = re.sub(r"^\s*[\[【](?!\s*\d+\s*[\]】])[^\]】]{1,60}[\]】]\s*", "", line).strip()
    for pattern in (CN_ENTRY, APA_ENTRY):
        match = pattern.fullmatch(line)
        if match:
            values: Dict[str, Any] = {k: v.strip() for k, v in match.groupdict().items()}
            values["authors"] = _authors(str(values["authors"]))
            if values["authors"]:
                return values
    return None


def document_sections(locator: str, text: str) -> Iterable[Tuple[str, str]]:
    m = REF_HEADING.search(text)
    if m and text[:m.start()].strip():
        yield locator + ", before references", text[:m.start()]
        yield locator + ", reference list", text[m.start():]
    else:
        yield locator, text


def single_paper_header(passages: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], int]]:
    """Detect bibliographic header in a standalone literature text; returns (fields, body_end_count) or None."""
    if not passages:
        return None
    header_lines: List[str] = []
    seen_entries = 0
    body_end = len(passages)
    for pos, passage in enumerate(passages):
        text = passage["text"]
        heading = REF_HEADING.search(text)
        if heading:
            text = text[:heading.start()]
            body_end = pos if not text.strip() else pos + 1
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(header_lines) < 10:
                header_lines.append(line)
            if len(line) < 512 and _citation_header(line):
                seen_entries += 1
                if seen_entries > 1:
                    return None
            elif len(header_lines) > 1 and (
                re.match(r"^\[\d{1,3}\]\s*\S", line)
                or re.match(r"^.{2,100}\((?:18|19|20)\d{2}[a-z]?\)\.\s+\S", line)
            ):
                return None
        if heading:
            break
    if len(header_lines) < 2:
        return None
    data = _citation_header(header_lines[0])
    if not data and not seen_entries:
        for width in range(2, min(len(header_lines), 4)):
            joined = " ".join(header_lines[:width])
            joined = re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", joined)
            data = _citation_header(joined)
            if data:
                break
    if not data:
        fields: Dict[str, Any] = {}
        for line in header_lines:
            m = re.fullmatch(r"([^:：]{1,18})[:：]\s*(.{1,240})", line)
            key = LABELS.get(m[1].strip().casefold()) if m else None
            if not key:
                break
            if key in fields:
                return None
            fields[key] = m[2].strip()
        if set(fields) != {"authors", "title", "venue", "year"} or not re.fullmatch(YEAR, str(fields["year"])):
            return None
        author_text = str(fields["authors"])
        fields["authors"] = _authors(author_text)
        if not fields["authors"]:
            names = re.split(r"\s*(?:;|\s+and\s+|&)\s*", author_text)
            if all(re.fullmatch(r"[A-Z][^\W\d_]+(?:[-’'][^\W\d_]+)*(?: [A-Z][^\W\d_]+(?:[-’'][^\W\d_]+)*){1,3}", n) for n in names):
                fields["authors"] = names
        if not fields["authors"] or len(header_lines) <= 4:
            return None
        data = fields
    return data, body_end


def line_passages(text: str) -> Iterable[Tuple[str, str]]:
    group: List[str] = []
    start = 1
    size = 0
    for index, line in enumerate(text.splitlines(), 1):
        if group and (not line.strip() or size + len(line) >= 1800):
            yield f"L{start}-{index - 1}", "\n".join(group)
            group.clear()
            size = 0
        if line.strip():
            if not group:
                start = index
            group.append(line)
            size += len(line) + 1
    if group:
        yield f"L{start}-{start + len(group) - 1}", "\n".join(group)


def pdf_structured_read(path: Path, parse_work_dir: Path, ocr_mode: str = "auto") -> Dict[str, Any]:
    """Column-aware PDF parsing via synced parse_pdf.run_parse_pipeline, with pypdf/pdftotext fallback."""
    try:
        out_dir = parse_work_dir / hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        doc = run_parse_pipeline(path, out_dir, ocr_mode=ocr_mode, save_crops=False)
        return doc
    except Exception:
        # Fallback if fitz is missing or fails unexpectedly
        pass

    blocks: List[Dict[str, Any]] = []
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        for i, page in enumerate(reader.pages, 1):
            txt = (page.extract_text() or "").strip()
            blocks.append({
                "block_id": f"p{i}_b01", "page": i, "role": "body",
                "section_id": None, "section_path": [f"Page {i}"],
                "text": txt, "structured_text": txt, "bbox": None,
            })
    except ImportError:
        exe = shutil.which("pdftotext")
        if not exe:
            raise RuntimeError("PDF text extraction requires PyMuPDF, pypdf, or pdftotext")
        res = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True, timeout=90)
        pages = res.stdout.decode("utf-8", errors="replace").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        for i, pt in enumerate(pages, 1):
            blocks.append({
                "block_id": f"p{i}_b01", "page": i, "role": "body",
                "section_id": None, "section_path": [f"Page {i}"],
                "text": pt.strip(), "structured_text": pt.strip(), "bbox": None,
            })
    return {
        "source_file": str(path),
        "status": "partial",
        "metadata": {"title": path.stem, "authors": []},
        "sections": [],
        "blocks": blocks,
        "tables": [],
        "uncertainties": [],
        "diagnostics": {"page_count": len(blocks)},
    }


def word_text(node: ET.Element) -> str:
    result: List[str] = []
    for item in node.iter():
        if item.tag in (WORD + "t", MATH + "t"):
            result.append(item.text or "")
        elif item.tag == WORD + "tab":
            result.append("\t")
        elif item.tag in (WORD + "br", WORD + "cr"):
            result.append("\n")
    return "".join(result)


def docx_passages(path: Path) -> Iterable[Tuple[str, str, str]]:
    """Yield (anchor, section_title, text) from DOCX paragraphs, tables, footnotes, and endnotes."""
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find(WORD + "body")
        if body is None:
            raise ValueError("DOCX document has no body")
        p_idx = t_idx = 0

        def blocks(node: ET.Element) -> Iterable[ET.Element]:
            for element in node:
                if element.tag in (WORD + "p", WORD + "tbl"):
                    yield element
                elif element.tag in (WORD + "sdt", WORD + "sdtContent", WORD + "customXml", WORD + "ins"):
                    yield from blocks(element)

        cur_sec = "Body"
        for block in blocks(body):
            if block.tag == WORD + "p":
                p_idx += 1
                txt = word_text(block).strip()
                if not txt:
                    continue
                pstyle = block.find(f"{WORD}pPr/{WORD}pStyle")
                if pstyle is not None and "heading" in str(pstyle.get(WORD + "val", "")).lower():
                    cur_sec = txt
                yield f"para_{p_idx}", cur_sec, txt
            else:
                t_idx += 1
                rows = []
                for tr in block.findall(WORD + "tr"):
                    cells = [" ".join(word_text(p).split()) for p in tr.findall(WORD + "tc")]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    yield f"tab_{t_idx}", cur_sec, "\n".join(rows)

        for name in ("footnotes", "endnotes"):
            part = f"word/{name}.xml"
            if part in archive.namelist():
                for note in ET.fromstring(archive.read(part)):
                    if note.get(WORD + "type"):
                        continue
                    nid = note.get(WORD + "id") or "0"
                    txt = "\n".join(word_text(p) for p in note.iter(WORD + "p")).strip()
                    if txt:
                        yield f"{name}_{nid}", name.title(), txt


def sheet_passages(path: Path) -> Iterable[Tuple[str, str, str]]:
    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Workbook reading requires openpyxl") from exc
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        for sheet in book:
            for r_idx, row in enumerate(sheet.iter_rows(), 1):
                cells = [cell for cell in row if cell.value is not None]
                if cells:
                    anchor = f"{sheet.title}_{cells[0].coordinate}:{cells[-1].coordinate}"
                    txt = "\t".join(f"{cell.coordinate}={cell.value}" for cell in cells)
                    yield anchor, f"Sheet {sheet.title}", txt
    finally:
        book.close()


def jats_xml_passages(path: Path) -> Tuple[Dict[str, Any], List[Tuple[str, str, str]]]:
    root = ET.fromstring(read_text(path))
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1]
    title_el = root.find(".//article-title")
    title = "".join(title_el.itertext()).strip() if title_el is not None else path.stem
    doi_el = root.find(".//article-id[@pub-id-type='doi']")
    doi = "".join(doi_el.itertext()).strip() if doi_el is not None else None
    authors: List[str] = []
    for c in root.findall(".//contrib[@contrib-type='author']"):
        nm = f"{c.findtext('name/given-names') or ''} {c.findtext('name/surname') or ''}".strip()
        if nm:
            authors.append(nm)
    year_el = root.find(".//pub-date/year")
    year = "".join(year_el.itertext()).strip() if year_el is not None else None

    out: List[Tuple[str, str, str]] = []
    for idx, p in enumerate(root.findall(".//abstract//p"), 1):
        txt = re.sub(r"\s+", " ", "".join(p.itertext())).strip()
        if txt:
            out.append((p.get("id") or f"abs_{idx}", "Abstract", txt))
    body = root.find(".//body")
    if body is not None:
        def walk(el: ET.Element, sec: str) -> None:
            if el.tag == "sec":
                h = el.find("title")
                if h is not None:
                    sec = "".join(h.itertext()).strip() or sec
            if el.tag == "p":
                txt = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
                if txt:
                    out.append((el.get("id") or f"p_{len(out) + 1}", sec, txt))
            else:
                for ch in el:
                    walk(ch, sec)
        walk(body, "Body")
    return {"title": title, "authors": authors, "doi": doi, "year": year}, out


def ris_entries(text: str) -> Iterable[Tuple[Dict[str, List[str]], str]]:
    tags: Dict[str, List[str]] = {}
    raw: List[str] = []
    last: Optional[str] = None
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
        if match and match[1] == "TY" and raw:
            yield tags, "\n".join(raw)
            tags, raw, last = {}, [], None
        raw.append(line)
        if match:
            last = match[1]
            tags.setdefault(last, []).append(match[2])
        elif last and line.strip():
            tags[last][-1] += "\n" + line
        if match and match[1] == "ER":
            yield tags, "\n".join(raw)
            tags, raw, last = {}, [], None
    if any(line.strip() for line in raw):
        yield tags, "\n".join(raw)


def bib_entries(text: str) -> Iterable[Tuple[Dict[str, str], str]]:
    marker = re.compile(r"@([\w-]+)\s*([({])")
    pos = 0
    while match := marker.search(text, pos):
        left = match[2]
        right = "}" if left == "{" else ")"
        cursor = match.end()
        depth = 1
        while depth and cursor < len(text):
            c = text[cursor]
            if c == "\\":
                cursor += 2
                continue
            if c == left:
                depth += 1
            if c == right:
                depth -= 1
            cursor += 1
        pos = cursor
        if depth or match[1].lower() in {"preamble", "comment", "string"}:
            continue
        body = text[match.end():cursor - 1]
        key, comma, rest = body.partition(",")
        if not comma:
            continue
        i = 0
        fields = {"cite_key": key.strip()}
        while m := re.search(r"([a-zA-Z][\w-]*)\s*=\s*", rest[i:]):
            name = m[1].lower()
            i += m.end()
            if i == len(rest):
                break
            start = i
            delimiter = rest[i]
            if delimiter in '{"':
                level = 0
                i += 1
                while i < len(rest):
                    token = rest[i]
                    if token == "\\":
                        i += 2
                        continue
                    if token == "}" and level == 0 and delimiter == "{":
                        break
                    if token == '"' and level == 0 and delimiter == '"':
                        break
                    level += (token == "{") - (token == "}")
                    i += 1
                fields[name] = rest[start + 1:i]
                i += 1
            else:
                stop = rest.find(",", i)
                i = stop if stop >= 0 else len(rest)
                fields[name] = rest[start:i].strip()
        yield fields, text[match.start():cursor]
