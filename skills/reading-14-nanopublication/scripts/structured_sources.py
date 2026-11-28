"""Read article content from JSON, JSONL, JATS XML, DOCX, HTML, LaTeX, CSV/TSV, and BibTeX, retaining native source addresses."""

from __future__ import annotations

import csv
from html.parser import HTMLParser
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List


DOCUMENT_LISTS = ("papers", "documents", "articles", "source_documents")
CONTENT_KEYS = (
    "abstract", "sections", "body", "body_text", "paragraphs", "passages", "blocks",
    "excerpts", "sentences", "text", "content", "full_text", "excerpt", "captions",
)
INLINE_KEYS = (
    *DOCUMENT_LISTS,
    "materials", "passages", "paragraphs", "source_text", "source_document",
    "source_materials", "sources", "document", "paper", "article", "input_materials",
    "passage", "excerpt", "abstract", "paper_text", "input_text", "manuscript",
    "context_text", "claims_source",
)
FILE_KEYS = ("path", "file", "file_path", "filename", "source_file", "local_path")


def pointer(path, component):
    return path + "/" + str(component).replace("~", "~0").replace("/", "~1")


def material_path(value):
    if isinstance(value, dict):
        return next((value[k] for k in FILE_KEYS if isinstance(value.get(k), str)), None)
    return None


def _block(text, locator, context):
    return {
        "block_id": locator,
        "text": text,
        "role": context.get("role", "body"),
        "section_id": context.get("section"),
        "page": context.get("pdf_page"),
        "printed_page": context.get("printed_page"),
        "bbox": None,
        "native_locator": {k: v for k, v in context.items() if k != "role"},
    }


def _document(path, meta, blocks, root):
    return {
        "source_file": str(path),
        "source_record": root,
        "metadata": meta,
        "blocks": blocks,
        "status": "success" if blocks else "unverifiable",
        "tables": [],
        "uncertainties": [],
        "change_log": [],
        "diagnostics": {"anomalies": []},
    }


def _table(address, label, columns, rows, row_paths, kind):
    clean_cols = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(columns)]
    if len(clean_cols) < 2:
        return None
    # Disambiguate duplicate column headers cleanly rather than dropping the table.
    seen: Dict[str, int] = {}
    unique_cols: List[str] = []
    for col in clean_cols:
        cnt = seen.get(col, 0) + 1
        seen[col] = cnt
        unique_cols.append(col if cnt == 1 else f"{col}_{cnt}")

    records, cells = [], []
    for row, (values, addresses) in enumerate(zip(rows, row_paths), 1):
        norm_vals = list(values[:len(unique_cols)]) + [""] * max(0, len(unique_cols) - len(values))
        records.append(dict(zip(unique_cols, norm_vals)))
        for col_idx, cell_path in enumerate(addresses[:len(unique_cols)]):
            cells.append({"row": row, "col": col_idx, "native_locator": {"location_type": kind, kind: cell_path}})
    return {
        "table_id": address,
        "label": label or address,
        "col_paths": unique_cols,
        "records": records,
        "cells": cells,
        "header_row_count": 1,
        "pages": [],
        "bbox": None,
    }


def _json_tables(node, root):
    tables = node.get("tables", [])
    if not isinstance(tables, list):
        return
    for i, tab in enumerate(tables):
        if not isinstance(tab, dict):
            continue
        address = pointer(pointer(root, "tables"), i)
        name = next((k for k in ("records", "rows", "data") if isinstance(tab.get(k), list)), None)
        if not name or not tab[name]:
            continue
        rows = tab[name]
        columns = tab.get("columns") or tab.get("headers") or (list(rows[0]) if isinstance(rows[0], dict) else [])
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            continue
        values, addresses = [], []
        for j, row in enumerate(rows):
            if not isinstance(row, (list, dict)):
                break
            keys = columns if isinstance(row, dict) else list(range(len(row)))
            vals = [row.get(c) for c in keys] if isinstance(row, dict) else row
            if any(isinstance(v, (list, dict)) for v in vals):
                break
            values.append(["" if v is None else str(v) for v in vals])
            addresses.append([pointer(pointer(pointer(address, name), j), k) for k in keys])
        else:
            table = _table(address, tab.get("label") or tab.get("id"), columns, values, addresses, "json_pointer")
            if table:
                if tab.get("caption"):
                    table["caption"] = str(tab["caption"])
                yield table


def _json_blocks(node, path, context):
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _json_blocks(item, pointer(path, index), context)
    elif isinstance(node, str):
        if node.strip() and not re.fullmatch(r"[^\n]{1,200}\.(?:pdf|xml|jats|md|txt|json|jsonl|docx|html|htm|tex|csv|tsv|bib)", node.strip(), re.I):
            yield _block(node.strip(), path, {**context, "location_type": "json_pointer", "json_pointer": path})
    elif isinstance(node, dict):
        ctx = dict(context)
        for key in ("paragraph_id", "passage_id", "block_id", "para_id", "id"):
            if node.get(key) is not None and not isinstance(node[key], (dict, list)):
                ctx["original_id"] = str(node[key])
                break
        for key in ("section", "section_title", "heading", "title"):
            if isinstance(node.get(key), str):
                if path and (key != "title" or "sections" in path.split("/")):
                    label = node[key]
                    ancestry = list(ctx.get("section_path", []))
                    if not ancestry or ancestry[-1] != label:
                        ancestry.append(label)
                    ctx.update(section=label, section_path=ancestry)
                break
        for key in ("pdf_page", "printed_page", "page"):
            if isinstance(node.get(key), (str, int)):
                ctx[key] = node[key]
        for key in CONTENT_KEYS:
            if key in node:
                part_ctx = {**ctx, "section": "Abstract", "section_path": ["Abstract"]} if key == "abstract" else ctx
                yield from _json_blocks(node[key], pointer(path, key), part_ctx)


def _json_document(path, node, root):
    meta = {}
    if isinstance(node, dict):
        for record in (node, node.get("metadata"), node.get("bibliography")):
            if isinstance(record, dict):
                for key in ("title", "authors", "year", "doi", "version", "arxiv_id", "venue", "url"):
                    if record.get(key):
                        meta[key] = record[key]
    authors = meta.get("authors", [])
    if not isinstance(authors, list):
        authors = [authors]
    meta["authors"] = [
        str(a.get("name") or " ".join(str(a[k]) for k in ("given", "family") if a.get(k)))
        if isinstance(a, dict)
        else str(a)
        for a in authors
    ]
    doc = _document(path, meta, list(_json_blocks(node, root, {})), root)
    if isinstance(node, dict):
        doc["tables"] = list(_json_tables(node, root))
        if doc["tables"]:
            doc["status"] = "success"
    return doc


def json_documents(path: Path, data, *, inline=False):
    """Keep metadata and block ids separate for each article in a collection."""
    groups = []
    if not inline and isinstance(data, dict) and (set(CONTENT_KEYS) | {"tables"}).intersection(data):
        return [_json_document(path, data, "")]
    if isinstance(data, dict):
        keys = INLINE_KEYS if inline else (*DOCUMENT_LISTS, "materials", "sources", "paper", "document", "article")
        for key in keys:
            if key in data:
                groups.append((key, data[key]))
    if not groups:
        return [] if inline else [_json_document(path, data, "")]
    docs = []
    for key, value in groups:
        root = pointer("", key)
        if isinstance(value, list) and key not in {"passages", "paragraphs"}:
            docs.extend(_json_document(path, item, pointer(root, i)) for i, item in enumerate(value))
        else:
            docs.append(_json_document(path, value, root))
    return [doc for doc in docs if doc["blocks"] or doc["tables"]]


def jsonl_documents(path: Path):
    docs = []
    for idx, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        rec = json.loads(s)
        if isinstance(rec, dict):
            docs.extend(json_documents(path, rec))
        else:
            docs.append(_document(path, {}, [_block(str(rec), f"/line/{idx}", {"location_type": "jsonl_line", "line": idx})], f"/line/{idx}"))
    return docs


def jats_documents(path):
    root = ET.parse(path).getroot()
    for elem in root.iter():
        elem.tag = elem.tag.split("}")[-1]
    articles = [root] if root.tag == "article" else root.findall(".//article")
    result = []
    for n, article in enumerate(articles, 1):
        article_path = "/article" if root is article else f"//article[{n}]"

        def text(elem):
            return re.sub(r"\s+", " ", "".join(elem.itertext())).strip() if elem is not None else ""

        front = article.find("front/article-meta")
        meta = {}
        if front is not None:
            meta["title"] = text(front.find("title-group/article-title"))
            meta["doi"] = text(front.find("article-id[@pub-id-type='doi']"))
            meta["year"] = text(front.find("pub-date/year"))
            meta["version"] = text(front.find("article-version"))
            meta["authors"] = []
            for author in front.findall("contrib-group/contrib"):
                if author.get("contrib-type", "author") == "author":
                    name = " ".join(filter(None, (text(author.find("name/given-names")), text(author.find("name/surname")))))
                    if name or text(author.find("collab")):
                        meta["authors"].append(name or text(author.find("collab")))
        journal = article.find("front/journal-meta/journal-title-group/journal-title")
        if journal is not None and text(journal):
            meta["venue"] = text(journal)

        blocks, tables = [], []

        def walk(elem, address, ancestry):
            if elem.tag in {"ref-list", "sub-article", "back"}:
                return
            if elem.tag in {"sec", "abstract"}:
                title_txt = text(elem.find("title")) or elem.get("id") or elem.tag
                ancestry = [*ancestry, title_txt]
            section = ancestry[-1] if ancestry else None
            if elem.tag == "table":
                header = elem.findall("./thead/tr")
                body = elem.findall("./tbody/tr")
                if not header and elem.findall("./tr"):
                    trs = elem.findall("./tr")
                    header = trs[:1]
                    body = trs[1:]
                if len(header) >= 1 and body and not any(
                    cell.get("rowspan", "1") != "1" or cell.get("colspan", "1") != "1" for cell in elem.iter()
                ):
                    cols = [text(cell) for cell in header[-1]]
                    rows = [[text(cell) for cell in row] for row in body]
                    paths = [[f"{address}/tbody[1]/tr[{r}]/*[{c}]" for c in range(1, len(row) + 1)]
                             for r, row in enumerate(body, 1)]
                    parsed = _table(address, elem.get("id"), cols, rows, paths, "xml_xpath")
                    if parsed:
                        parsed["section_id"] = section
                        tables.append(parsed)
                return
            if elem.tag == "p":
                ctx = {"section": section, "section_path": ancestry, "location_type": "xml_xpath", "xml_xpath": address}
                identity = elem.get("id") or elem.get("{http://www.w3.org/XML/1998/namespace}id")
                if identity:
                    ctx["original_id"] = identity
                if text(elem):
                    blocks.append(_block(text(elem), address, ctx))
                return
            counts = {}
            for child in elem:
                counts[child.tag] = counts.get(child.tag, 0) + 1
                walk(child, f"{address}/{child.tag}[{counts[child.tag]}]", ancestry)

        walk(article, article_path, [])
        doc = _document(path, meta, blocks, article_path)
        doc["tables"] = tables
        if tables:
            doc["status"] = "success"
        result.append(doc)
    return result


def docx_documents(path: Path):
    """Extract headings, paragraphs, and tables from a .docx manuscript."""
    blocks: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    section_stack: List[Tuple[int, str]] = []

    try:
        import docx  # type: ignore
        from docx.document import Document as _DocxDoc  # type: ignore
        from docx.oxml.table import CT_Tbl  # type: ignore
        from docx.oxml.text.paragraph import CT_P  # type: ignore
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore

        d = docx.Document(str(path))
        props = d.core_properties
        if getattr(props, "title", None):
            meta["title"] = str(props.title).strip()
        if getattr(props, "author", None):
            meta["authors"] = [a.strip() for a in re.split(r"[;,]", str(props.author)) if a.strip()]

        para_idx = 0
        tab_idx = 0
        for child in d.element.body.iterchildren():
            if isinstance(child, CT_P):
                para = Paragraph(child, d)
                txt = re.sub(r"\s+", " ", para.text or "").strip()
                if not txt:
                    continue
                para_idx += 1
                style_name = (para.style.name if para.style else "") or ""
                m_head = re.match(r"^(?:Heading|标题)\s*(\d+)", style_name, re.I)
                if m_head or style_name.lower() == "title":
                    if style_name.lower() == "title" and not meta.get("title"):
                        meta["title"] = txt
                        continue
                    lvl = int(m_head.group(1)) if m_head else 1
                    while section_stack and section_stack[-1][0] >= lvl:
                        section_stack.pop()
                    section_stack.append((lvl, txt))
                    continue
                ancestry = [s[1] for s in section_stack] or ["Body"]
                if not meta.get("title") and para_idx == 1 and len(txt) <= 200:
                    meta["title"] = txt
                loc = f"docx_para:{para_idx}"
                blocks.append(_block(txt, loc, {
                    "section": ancestry[-1],
                    "section_path": ancestry,
                    "location_type": "docx_paragraph",
                    "docx_paragraph": para_idx,
                }))
            elif isinstance(child, CT_Tbl):
                tbl = Table(child, d)
                raw_rows = [[re.sub(r"\s+", " ", c.text or "").strip() for c in row.cells] for row in tbl.rows]
                if len(raw_rows) >= 2 and len(raw_rows[0]) >= 2:
                    tab_idx += 1
                    ancestry = [s[1] for s in section_stack] or ["Body"]
                    cols = raw_rows[0]
                    data_rows = raw_rows[1:]
                    paths = [[f"docx_table:{tab_idx}:r{r}c{c}" for c in range(1, len(cols) + 1)]
                             for r in range(1, len(data_rows) + 1)]
                    parsed = _table(f"tab_{tab_idx}", f"Table {tab_idx}", cols, data_rows, paths, "docx_table")
                    if parsed:
                        parsed["section_id"] = ancestry[-1]
                        tables.append(parsed)
    except Exception:
        # Pure-stdlib XML fallback
        with zipfile.ZipFile(str(path), "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        p_idx = 0
        for p_el in root.iter(f"{ns}p"):
            txt = "".join(t.text or "" for t in p_el.iter(f"{ns}t")).strip()
            if not txt:
                continue
            p_idx += 1
            loc = f"docx_para:{p_idx}"
            blocks.append(_block(txt, loc, {
                "section": "Body",
                "section_path": ["Body"],
                "location_type": "docx_paragraph",
                "docx_paragraph": p_idx,
            }))

    doc = _document(path, meta, blocks, "/docx")
    doc["tables"] = tables
    if tables:
        doc["status"] = "success"
    return [doc]


class _HTMLDocParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title = ""
        self.meta: Dict[str, Any] = {"authors": []}
        self.section_stack: List[Tuple[int, str]] = []
        self.cur_tag = ""
        self.cur_buf: List[str] = []
        self.blocks: List[Dict[str, Any]] = []
        self.tables: List[Dict[str, Any]] = []
        self.in_table = False
        self.table_rows: List[List[str]] = []
        self.cur_row: List[str] = []
        self.in_cell = False
        self.cell_buf: List[str] = []
        self.p_idx = 0
        self.t_idx = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        attr_map = dict(attrs)
        if t in {"script", "style", "nav", "footer", "noscript"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if t == "meta":
            name = (attr_map.get("name") or "").lower()
            content = (attr_map.get("content") or "").strip()
            if name == "citation_title" and content:
                self.meta["title"] = content
            elif name == "citation_author" and content:
                self.meta["authors"].append(content)
            elif name == "citation_doi" and content:
                self.meta["doi"] = content
            elif name in {"citation_publication_date", "citation_date"} and content:
                m = re.search(r"(19|20)\d{2}", content)
                if m:
                    self.meta["year"] = m.group(0)
        elif t == "title":
            self.in_title = True
        elif t in {"h1", "h2", "h3", "h4", "p", "li"} and not self.in_table:
            self.cur_tag = t
            self.cur_buf = []
        elif t == "table":
            self.in_table = True
            self.table_rows = []
        elif t == "tr" and self.in_table:
            self.cur_row = []
        elif t in {"th", "td"} and self.in_table:
            self.in_cell = True
            self.cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in {"script", "style", "nav", "footer", "noscript"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if t == "title":
            self.in_title = False
            if self.title.strip() and not self.meta.get("title"):
                self.meta["title"] = re.sub(r"\s+", " ", self.title).strip()
        elif t in {"h1", "h2", "h3", "h4"} and self.cur_tag == t:
            txt = re.sub(r"\s+", " ", "".join(self.cur_buf)).strip()
            self.cur_tag = ""
            if txt:
                lvl = int(t[1])
                while self.section_stack and self.section_stack[-1][0] >= lvl:
                    self.section_stack.pop()
                self.section_stack.append((lvl, txt))
        elif t in {"p", "li"} and self.cur_tag == t:
            txt = re.sub(r"\s+", " ", "".join(self.cur_buf)).strip()
            self.cur_tag = ""
            if txt:
                self.p_idx += 1
                ancestry = [s[1] for s in self.section_stack] or ["Body"]
                self.blocks.append(_block(txt, f"html_p:{self.p_idx}", {
                    "section": ancestry[-1],
                    "section_path": ancestry,
                    "location_type": "html_paragraph",
                    "html_paragraph": self.p_idx,
                }))
        elif t in {"th", "td"} and self.in_cell:
            self.cur_row.append(re.sub(r"\s+", " ", "".join(self.cell_buf)).strip())
            self.in_cell = False
        elif t == "tr" and self.in_table:
            if any(self.cur_row):
                self.table_rows.append(self.cur_row)
        elif t == "table" and self.in_table:
            self.in_table = False
            if len(self.table_rows) >= 2 and len(self.table_rows[0]) >= 2:
                self.t_idx += 1
                ancestry = [s[1] for s in self.section_stack] or ["Body"]
                cols = self.table_rows[0]
                rows = self.table_rows[1:]
                paths = [[f"html_table:{self.t_idx}:r{r}c{c}" for c in range(1, len(cols) + 1)]
                         for r in range(1, len(rows) + 1)]
                parsed = _table(f"tab_{self.t_idx}", f"Table {self.t_idx}", cols, rows, paths, "html_table")
                if parsed:
                    parsed["section_id"] = ancestry[-1]
                    self.tables.append(parsed)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title += data
        if self.in_cell:
            self.cell_buf.append(data)
        elif self.cur_tag:
            self.cur_buf.append(data)


def html_documents(path: Path):
    parser = _HTMLDocParser()
    parser.feed(path.read_text(encoding="utf-8-sig", errors="replace"))
    parser.close()
    doc = _document(path, parser.meta, parser.blocks, "/html")
    doc["tables"] = parser.tables
    if parser.tables:
        doc["status"] = "success"
    return [doc]


def tex_documents(path: Path):
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    cleaned = "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in raw.splitlines())
    meta: Dict[str, Any] = {}
    m_title = re.search(r"\\title(?:\[[^\]]*\])?\{([^{}]+)\}", cleaned)
    if m_title:
        meta["title"] = re.sub(r"\s+", " ", m_title.group(1)).strip()
    m_auth = re.search(r"\\author\{([^{}]+)\}", cleaned)
    if m_auth:
        meta["authors"] = [a.strip() for a in re.split(r"\\and|,|;", re.sub(r"\\[a-zA-Z]+\{[^}]*\}", "", m_auth.group(1))) if a.strip()]

    m_doc = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", cleaned, re.S)
    body = m_doc.group(1) if m_doc else cleaned

    blocks: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    section_stack: List[Tuple[int, str]] = []
    p_idx = 0
    t_idx = 0

    m_abs = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S)
    if m_abs:
        abs_txt = re.sub(r"\s+", " ", m_abs.group(1)).strip()
        if abs_txt:
            meta["abstract"] = abs_txt
            p_idx += 1
            blocks.append(_block(abs_txt, f"tex_p:{p_idx}", {
                "section": "Abstract",
                "section_path": ["Abstract"],
                "location_type": "tex_paragraph",
                "tex_paragraph": p_idx,
            }))
        body = body.replace(m_abs.group(0), "\n")

    for m_tab in re.finditer(r"\\begin\{tabular\}\{[^}]*\}(.*?)\\end\{tabular\}", body, re.S):
        raw_rows = [r.strip() for r in re.split(r"\\\\", m_tab.group(1)) if r.strip()]
        parsed_rows = []
        for r in raw_rows:
            r_clean = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|cline\{[^}]*\})", "", r).strip()
            if "&" in r_clean:
                parsed_rows.append([re.sub(r"\s+", " ", c).strip() for c in r_clean.split("&")])
        if len(parsed_rows) >= 2 and len(parsed_rows[0]) >= 2:
            t_idx += 1
            paths = [[f"tex_table:{t_idx}:r{ri}c{ci}" for ci in range(1, len(parsed_rows[0]) + 1)]
                     for ri in range(1, len(parsed_rows))]
            tab = _table(f"tab_{t_idx}", f"Table {t_idx}", parsed_rows[0], parsed_rows[1:], paths, "tex_table")
            if tab:
                tables.append(tab)
        body = body.replace(m_tab.group(0), "\n")

    sec_re = re.compile(r"\\(sub)*section\*?\{([^{}]+)\}")
    chunks = re.split(r"\n\s*\n+", body)
    for chunk in chunks:
        s = chunk.strip()
        if not s:
            continue
        m_sec = sec_re.search(s)
        if m_sec and len(s) < 200:
            lvl = 1 + len(m_sec.group(1) or "") // 3
            title = re.sub(r"\s+", " ", m_sec.group(2)).strip()
            while section_stack and section_stack[-1][0] >= lvl:
                section_stack.pop()
            section_stack.append((lvl, title))
            continue
        txt = re.sub(r"\\(?:maketitle|tableofcontents|clearpage|newpage)\b", "", s)
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt or txt.startswith("\\begin{"):
            continue
        p_idx += 1
        ancestry = [x[1] for x in section_stack] or ["Body"]
        blocks.append(_block(txt, f"tex_p:{p_idx}", {
            "section": ancestry[-1],
            "section_path": ancestry,
            "location_type": "tex_paragraph",
            "tex_paragraph": p_idx,
        }))

    doc = _document(path, meta, blocks, "/tex")
    doc["tables"] = tables
    if tables:
        doc["status"] = "success"
    return [doc]


def csv_documents(path: Path):
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=delimiter))
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [_document(path, {"title": path.stem}, [], "/csv")]
    cols = rows[0] if len(rows) >= 2 and len(rows[0]) >= 2 else [f"col_{i + 1}" for i in range(len(rows[0]))]
    data_rows = rows[1:] if len(rows) >= 2 and len(rows[0]) >= 2 else rows
    blocks = []
    for idx, r in enumerate(data_rows, 1):
        line_txt = " | ".join(f"{cols[ci] if ci < len(cols) else ci}: {val}" for ci, val in enumerate(r))
        blocks.append(_block(line_txt, f"csv_row:{idx}", {
            "section": "Table Data",
            "section_path": ["Table Data"],
            "location_type": "csv_row",
            "csv_row": idx,
        }))
    doc = _document(path, {"title": path.stem}, blocks, "/csv")
    if len(cols) >= 2 and data_rows:
        paths = [[f"csv_r{r}c{c}" for c in range(1, len(cols) + 1)] for r in range(1, len(data_rows) + 1)]
        tab = _table("tab_1", path.stem, cols, data_rows, paths, "csv_cell")
        if tab:
            tab["section_id"] = "Table Data"
            doc["tables"] = [tab]
            doc["status"] = "success"
    return [doc]


def bib_documents(path: Path):
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    entries = re.split(r"(?=@[A-Za-z]+\s*\{)", raw)
    blocks = []
    for idx, entry in enumerate(entries, 1):
        txt = re.sub(r"\s+", " ", entry).strip()
        if not txt or not txt.startswith("@"):
            continue
        blocks.append(_block(txt, f"bib_{idx}", {
            "section": "Bibliography",
            "section_path": ["Bibliography"],
            "location_type": "bibtex_entry",
            "bib_index": idx,
        }))
    return [_document(path, {"title": path.stem}, blocks, "/bib")]


def adapt_article(doc):
    """Build the section tree without inventing physical pages for structured records."""
    sections, parents, stack = [], {}, []
    for index, block in enumerate(doc["blocks"], 1):
        loc = block["native_locator"]
        names = loc.get("section_path") or [loc.get("section") or "Body"]
        common = 0
        while common < min(len(names), len(stack)) and stack[common]["title"] == names[common]:
            common += 1
        stack = stack[:common]
        for name in names[common:]:
            sid = f"sec_{len(parents) + 1}"
            node = {
                "section_id": sid,
                "title": name,
                "number": None,
                "level": len(stack) + 1,
                "page_start": block.get("page"),
                "page_end": block.get("page"),
                "block_ids": [],
                "children": [],
            }
            (stack[-1]["children"] if stack else sections).append(node)
            stack.append(node)
            parents[sid] = node
        for section in stack:
            if block.get("page") is not None:
                section["page_end"] = block["page"]
        stack[-1]["block_ids"].append(block["block_id"])
        block.update(
            section_id=stack[-1]["section_id"],
            section_path=names,
            reading_order=index,
            structured_text=block["text"],
        )
    for table in doc["tables"]:
        table["n_rows"] = len(table["records"]) + 1
        table["n_cols"] = len(table["col_paths"])
        table["status"] = "extracted"
        table["markdown"] = "\n".join(
            [
                "| " + " | ".join(table["col_paths"]) + " |",
                "| " + " | ".join(["---"] * len(table["col_paths"])) + " |",
                *[
                    "| " + " | ".join(str(row.get(col, "")).replace("|", "\\|") for col in table["col_paths"]) + " |"
                    for row in table["records"]
                ],
            ]
        )
        names = [s["section_id"] for s in parents.values() if s["title"] == table.get("section_id")]
        table["section_id"] = names[0] if len(names) == 1 else (list(parents.keys())[0] if len(parents) == 1 else None)
    doc["sections"] = sections
    return doc


def read_structured(path: Path):
    ext = path.suffix.lower()
    if ext == ".json":
        docs = json_documents(path, json.loads(path.read_text(encoding="utf-8-sig")))
    elif ext == ".jsonl":
        docs = jsonl_documents(path)
    elif ext in {".xml", ".jats"}:
        docs = jats_documents(path)
    elif ext == ".docx":
        docs = docx_documents(path)
    elif ext in {".html", ".htm"}:
        docs = html_documents(path)
    elif ext == ".tex":
        docs = tex_documents(path)
    elif ext in {".csv", ".tsv"}:
        docs = csv_documents(path)
    elif ext == ".bib":
        docs = bib_documents(path)
    else:
        docs = []
    return [adapt_article(d) for d in docs]
