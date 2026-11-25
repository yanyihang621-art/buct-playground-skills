"""Extract citeable passages from documents while retaining their native locations."""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def read_text(path):
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030")


def line_passages(text):
    # Preserve line breaks and group short consecutive lines within paragraphs.
    group = []
    start = 1
    size = 0
    for index, line in enumerate(text.splitlines(), 1):
        if group and (not line.strip() or size + len(line) >= 1800):
            yield f"lines {start}-{index - 1}", "\n".join(group)
            group.clear()
            size = 0
        if line.strip():
            if not group:
                start = index
            group.append(line)
            size += len(line) + 1
    if group:
        yield f"lines {start}-{start + len(group) - 1}", "\n".join(group)


def pdf_passages(path):
    try:
        import fitz
    except ImportError:
        fitz = None
    if fitz is not None:
        with fitz.open(path) as doc:
            if doc.needs_pass:
                raise ValueError("Encrypted PDF requires a password")
            for i, page in enumerate(doc, 1):
                yield f"page {i}", page.get_text("text", sort=True)
        return
    try:
        from pypdf import PdfReader
    except ImportError:
        exe = shutil.which("pdftotext")
        if not exe:
            raise RuntimeError("PDF text extraction requires PyMuPDF, pypdf or pdftotext")
        result = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"],
                                capture_output=True, check=True, timeout=90)
        pages = result.stdout.decode("utf-8").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        for i, page_text in enumerate(pages, 1):
            yield f"page {i}", page_text
        return
    for i, page in enumerate(PdfReader(str(path)).pages, 1):
        yield f"page {i}", page.extract_text() or ""


def word_text(node):
    result = []
    for item in node.iter():
        if item.tag in (WORD + "t", MATH + "t"):
            result.append(item.text or "")
        elif item.tag == WORD + "tab":
            result.append("\t")
        elif item.tag in (WORD + "br", WORD + "cr"):
            result.append("\n")
    return "".join(result)


def sheet_passages(path):
    """Keep native cell addresses together with row values; never recalculate formulas."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Use LibreOffice to inspect this workbook, or install openpyxl") from exc
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        for sheet in book:
            for row in sheet.iter_rows():
                cells = [cell for cell in row if cell.value is not None]
                if cells:
                    yield (f"sheet {sheet.title!r} {cells[0].coordinate}:{cells[-1].coordinate}",
                           "\t".join(f"{cell.coordinate}={cell.value}" for cell in cells))
    finally:
        book.close()


def ris_entries(text):
    """Retain tag continuations and verbatim source records, including UTF-8 names."""
    tags, raw = {}, []
    last = None
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


def docx_passages(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        body = root.find(WORD + "body")
        if body is None:
            raise ValueError("DOCX document has no body")
        paragraph_count = table_count = 0

        def blocks(node):
            for element in node:
                if element.tag in (WORD + "p", WORD + "tbl"):
                    yield element
                elif element.tag in (WORD + "sdt", WORD + "sdtContent", WORD + "customXml", WORD + "ins"):
                    yield from blocks(element)

        for block in blocks(body):
            if block.tag == WORD + "p":
                paragraph_count += 1
                yield f"paragraph {paragraph_count}", word_text(block)
            else:
                table_count += 1
                for row_index, row in enumerate(block.findall(WORD + "tr"), 1):
                    cells = ["\n".join(word_text(p) for p in cell.iter(WORD + "p"))
                             for cell in row.findall(WORD + "tc")]
                    yield f"table {table_count} row {row_index}", "\t".join(cells)
        for name in ("footnotes", "endnotes"):
            part = f"word/{name}.xml"
            if part in archive.namelist():
                for note in ET.fromstring(archive.read(part)):
                    if note.get(WORD + "type"):
                        continue
                    yield f"{name} id {note.get(WORD + 'id')}", "\n".join(
                        word_text(p) for p in note.iter(WORD + "p"))


def bib_entries(text):
    """Scan balanced entries, preserving nested TeX groups and unquoted years."""
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
