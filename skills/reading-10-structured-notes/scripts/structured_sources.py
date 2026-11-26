"""Read article content from JSON and JATS, retaining native source addresses."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


DOCUMENT_LISTS = ("papers", "documents", "articles", "source_documents")
CONTENT_KEYS = ("abstract", "sections", "body", "body_text", "paragraphs", "passages", "blocks",
                "excerpts", "sentences", "text", "content", "full_text", "excerpt", "captions")
INLINE_KEYS = (*DOCUMENT_LISTS, "materials", "passages", "paragraphs", "source_text", "source_document",
               "source_materials", "sources", "document", "paper", "article", "input_materials")
FILE_KEYS = ("path", "file", "file_path", "filename", "source_file", "local_path")


def pointer(path, component):
    return path + "/" + str(component).replace("~", "~0").replace("/", "~1")


def material_path(value):
    if isinstance(value, dict):
        return next((value[k] for k in FILE_KEYS if isinstance(value.get(k), str)), None)
    return None


def _block(text, locator, context):
    return {"block_id": locator, "text": text, "role": context.get("role", "body"),
            "section_id": context.get("section"), "page": context.get("pdf_page"),
            "printed_page": context.get("printed_page"), "bbox": None,
            "native_locator": {k: v for k, v in context.items() if k != "role"}}


def _document(path, meta, blocks, root):
    return {"source_file": str(path), "source_record": root, "metadata": meta, "blocks": blocks,
            "status": "success" if blocks else "unverifiable", "tables": [],
            "uncertainties": [], "change_log": [], "diagnostics": {"anomalies": []}}


def _table(address, label, columns, rows, row_paths, kind):
    if len(columns) < 2 or len(set(columns)) != len(columns) or any(not col for col in columns):
        return None
    records, cells = [], []
    for row, (values, addresses) in enumerate(zip(rows, row_paths), 1):
        if len(values) != len(columns):
            return None
        records.append(dict(zip(columns, values)))
        for col, cell_path in enumerate(addresses):
            cells.append({"row": row, "col": col, "native_locator": {
                "location_type": kind, kind: cell_path}})
    return {"table_id": address, "label": label or address, "col_paths": columns, "records": records,
            "cells": cells, "header_row_count": 1, "pages": [], "bbox": None}


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
                yield table


def _json_blocks(node, path, context):
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _json_blocks(item, pointer(path, index), context)
    elif isinstance(node, str):
        if node.strip() and not re.fullmatch(r"[^\n]{1,200}\.(?:pdf|xml|jats|md|txt|json)", node.strip(), re.I):
            yield _block(node.strip(), path, {**context, "location_type": "json_pointer", "json_pointer": path})
    elif isinstance(node, dict):
        ctx = dict(context)
        for key in ("paragraph_id", "passage_id", "block_id", "para_id", "id"):
            if node.get(key) is not None and not isinstance(node[key], (dict, list)):
                ctx["original_id"] = str(node[key])
                break
        for key in ("section", "section_title", "heading", "title"):
            if isinstance(node.get(key), str):
                # The document title is bibliographic metadata, not a body section.
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
                for key in ("title", "authors", "year", "doi", "version", "arxiv_id"):
                    if record.get(key):
                        meta[key] = record[key]
    authors = meta.get("authors", [])
    if not isinstance(authors, list):
        authors = [authors]
    meta["authors"] = [str(a.get("name") or " ".join(str(a[k]) for k in ("given", "family") if a.get(k)))
                       if isinstance(a, dict) else str(a) for a in authors]
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


def jats_documents(path):
    root = ET.parse(path).getroot()
    for elem in root.iter():
        elem.tag = elem.tag.split("}")[-1]
    articles = [root] if root.tag == "article" else root.findall(".//article")
    result = []
    for n, article in enumerate(articles, 1):
        article_path = "/article" if root is article else f"//article[{n}]"
        def text(elem):
            return "".join(elem.itertext()).strip() if elem is not None else ""
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
        blocks, tables = [], []
        def walk(elem, address, ancestry):
            if elem.tag in {"ref-list", "sub-article", "back"}:
                return
            if elem.tag in {"sec", "abstract"}:
                ancestry = [*ancestry, text(elem.find("title")) or elem.get("id") or elem.tag]
            section = ancestry[-1] if ancestry else None
            if elem.tag == "table":
                header = elem.findall("./thead/tr")
                body = elem.findall("./tbody/tr")
                if len(header) == 1 and body and not any(
                    cell.get("rowspan", "1") != "1" or cell.get("colspan", "1") != "1" for cell in elem.iter()
                ):
                    cols = [text(cell) for cell in header[0]]
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


def read_structured(path):
    if path.suffix.lower() == ".json":
        return json_documents(path, json.loads(path.read_text(encoding="utf-8-sig")))
    return jats_documents(path)


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
            node = {"section_id": sid, "title": name, "number": None, "level": len(stack) + 1,
                    "page_start": block.get("page"), "page_end": block.get("page"),
                    "block_ids": [], "children": []}
            (stack[-1]["children"] if stack else sections).append(node)
            stack.append(node)
            parents[sid] = node
        for section in stack:
            if block.get("page") is not None:
                section["page_end"] = block["page"]
        stack[-1]["block_ids"].append(block["block_id"])
        block.update(section_id=stack[-1]["section_id"], section_path=names,
                     reading_order=index, structured_text=block["text"])
    for table in doc["tables"]:
        table["n_rows"] = len(table["records"]) + 1
        table["n_cols"] = len(table["col_paths"])
        table["status"] = "extracted"
        table["markdown"] = "\n".join("| " + " | ".join(values) + " |" for values in
                                      [table["col_paths"], *[[row[col] for col in table["col_paths"]]
                                                          for row in table["records"]]])
        names = [s["section_id"] for s in parents.values() if s["title"] == table.get("section_id")]
        table["section_id"] = names[0] if len(names) == 1 else None
    doc["sections"] = sections
    return doc
