#!/usr/bin/env python3
"""Multi-paper corpus ingestion supporting Harbor revised JATS/JSON manifests, XML math notes, PDF, DOCX, HTML, TeX, CSV, and MD/TXT."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from parse_pdf import run_parse_pipeline
from pdf_utils import write_json_atomic

IGNORE_FILES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}
PAPER_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".json", ".jsonl", ".xml", ".docx", ".html", ".htm", ".tex", ".csv", ".tsv"}
INLINE_MATERIAL_KEYS = ("materials", "papers", "documents", "articles", "sources", "corpus", "text", "passage", "content")

ABBREVIATION_RE = re.compile(r"([A-Za-z][A-Za-z0-9 \-]{2,100}?)\s*\(([A-Z][A-Za-z0-9\-]{1,12})\)")
REPORTED_BOUNDARIES = (
    ("unreported", re.compile(r"\bnot\s+(?:reported|provided|available|specified|measured)\b|未(?:报告|提供|测量|说明)", re.I)),
    ("inconclusive", re.compile(r"\b(?:inconclusive|uncertain|not\s+(?:statistically\s+)?significant|no\s+significant)\b|无法确定|无统计学意义", re.I)),
    ("negative_statement", re.compile(r"\b(?:did|does|was|were|could|is|are|have|has)\s+not\s+(?!only\b)|\bno\s+(?:evidence|effect|difference|improvement)\b|未观察到|无明显|不显著", re.I)),
)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\(])")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_authors(val: Any) -> List[str]:
    if not val:
        return ["not_reported"]
    if isinstance(val, str):
        parts = [a.strip() for a in re.split(r"\s*[;；]\s*|\s+and\s+|,\s*(?=[A-Z\u4e00-\u9fff])", val) if a.strip()]
        return parts or ["not_reported"]
    if isinstance(val, list):
        out: List[str] = []
        for a in val:
            if isinstance(a, str) and a.strip():
                out.append(a.strip())
            elif isinstance(a, dict):
                nm = a.get("name") or a.get("literal") or " ".join(
                    str(a[k]) for k in ("given", "given-names", "family", "surname") if a.get(k)
                )
                if nm and str(nm).strip():
                    out.append(str(nm).strip())
        return out or ["not_reported"]
    return ["not_reported"]


def _canon_term(label: str) -> str:
    t = re.sub(r"^(?:the|an?)\s+(?=\w)", "", (label or "").strip().casefold())
    t = re.sub(r"(?<=[^\W\d_])[-‐‑](?=[^\W\d_])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_long_form(prefix: str, short: str) -> Optional[str]:
    rem = prefix.lower()
    chars = [ch.lower() for ch in short if ch.isalnum()]
    if len(chars) < 2:
        return None
    pos = len(prefix)
    for i in reversed(range(len(chars))):
        while True:
            pos = rem.rfind(chars[i], 0, pos)
            if pos < 0:
                return None
            if i == 0 and pos > 0 and rem[pos - 1].isalnum():
                continue
            break
    phrase = prefix[pos:].strip(" ,;:-")
    if len(phrase.split()) <= min(2 * len(chars), len(chars) + 5) and len(phrase) > len(short):
        return phrase
    return None


def _extract_paragraphs_from_json_doc(
    data: Any, default_source_id: str, jats_index: bool = False
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Retain per-paper IDs without leaking root metadata across distinct papers."""
    paper = data if isinstance(data, dict) else {}
    sid = str(paper.get("source_id") or paper.get("paper_id") or paper.get("id") or default_source_id)
    meta = {
        "source_id": sid,
        "title": paper.get("title") or paper.get("paper_title"),
        "authors": _normalize_authors(paper.get("authors")),
        "doi": paper.get("doi"),
        "year": paper.get("year") or paper.get("publication_year"),
        "license": paper.get("license"),
    }
    paragraphs: List[Dict[str, Any]] = []

    def append(item: Any, section: str, fallback_id: str) -> None:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            return
        text = item.get("text") or item.get("content") or item.get("body") or item.get("paragraph")
        if not isinstance(text, str) or not text.strip():
            return
        reported_id = (
            item.get("jats_id")
            or item.get("paragraph_id")
            or item.get("pid")
            or item.get("block_id")
            or item.get("id")
        )
        locator = item.get("location_type") or (
            "jats_paragraph_id"
            if reported_id and (jats_index or item.get("jats_id") or paper.get("jats"))
            else "json_paragraph_id"
        )
        paragraphs.append({
            "source_id": str(item.get("source_id") or item.get("paper_id") or sid),
            "paragraph_id": str(reported_id or fallback_id),
            "location_type": locator if reported_id else "json_generated_locator",
            "pdf_page": None,
            "printed_page": None,
            "bbox": None,
            "section_title": str(item.get("section_title") or item.get("section") or section),
            "text": re.sub(r"\s+", " ", text).strip(),
        })

    if isinstance(paper.get("abstract"), str) and paper["abstract"].strip():
        append({"text": paper["abstract"], "id": paper.get("abstract_id")}, "Abstract", f"{sid}_abs")

    items = None
    if isinstance(data, list):
        items = data
    else:
        for key in ("paragraphs", "blocks", "items", "passages", "segments", "body_paragraphs", "jats_paragraphs"):
            if isinstance(paper.get(key), list) and paper[key]:
                items = paper[key]
                break

    if isinstance(items, list):
        for idx, item in enumerate(items, start=1):
            append(item, "Body", f"{sid}_p{idx}")

    if not items:
        for sec_idx, section in enumerate(paper.get("sections") or [], start=1):
            if isinstance(section, dict):
                sec_title = section.get("title") or section.get("heading") or f"Section {sec_idx}"
                sec_items = section.get("paragraphs") or section.get("blocks") or section.get("items") or []
                if isinstance(section.get("text"), str) and not sec_items:
                    append({"text": section["text"], "id": section.get("id")}, sec_title, f"{sid}_s{sec_idx}_p1")
                for idx, item in enumerate(sec_items, start=1):
                    append(item, sec_title, f"{sid}_s{sec_idx}_p{idx}")

    if not paragraphs and isinstance(paper.get("body"), str) and paper["body"].strip():
        for idx, chunk in enumerate(re.split(r"\n\s*\n", paper["body"].strip()), start=1):
            append({"text": chunk}, "Body", f"{sid}_b{idx}")

    return meta, paragraphs


def _jats_element_text(el: ET.Element) -> str:
    """Linearize JATS XML text cleanly while keeping subscript/superscript readable."""
    parts: List[str] = []

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in ("tex-math", "mml:math", "math"):
                txt = "".join(child.itertext()).strip()
                if txt:
                    parts.append(f" {txt} ")
            elif tag == "sub":
                txt = "".join(child.itertext()).strip()
                if txt:
                    parts.append(f"_{txt}")
            elif tag == "sup":
                txt = "".join(child.itertext()).strip()
                if txt:
                    parts.append(f"^{txt}")
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _extract_jats_xml_notes(xml_path: Path) -> List[Dict[str, str]]:
    """Extract tables, figure captions, and tex-math formulas from JATS XML for cross-checking."""
    notes: List[Dict[str, str]] = []
    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError:
        return notes
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1]

    for tw in root.findall(".//table-wrap"):
        tid = tw.get("id") or f"xml_tab_{len(notes) + 1}"
        cap = "".join(tw.findtext("caption") or "").strip()
        rows_txt: List[str] = []
        for tr in tw.findall(".//tr"):
            cells = [re.sub(r"\s+", " ", "".join(c.itertext())).strip() for c in tr if c.tag in ("th", "td")]
            if cells:
                rows_txt.append(" | ".join(cells))
        if rows_txt or cap:
            notes.append({
                "kind": "xml_table",
                "id": tid,
                "text": (f"{cap}: " if cap else "") + " ; ".join(rows_txt[:15]),
            })

    for fig in root.findall(".//fig"):
        fid = fig.get("id") or f"xml_fig_{len(notes) + 1}"
        cap = re.sub(r"\s+", " ", "".join((fig.find("caption") or fig).itertext())).strip()
        if cap:
            notes.append({"kind": "xml_figure", "id": fid, "text": cap})

    for idx, tm in enumerate(root.findall(".//tex-math"), start=1):
        txt = re.sub(r"\s+", " ", "".join(tm.itertext())).strip()
        if txt:
            notes.append({"kind": "xml_formula", "id": tm.get("id") or f"xml_eq_{idx}", "text": txt})

    return notes


def _parse_jats_xml(xml_path: Path, default_source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, str]]]:
    """Parse JATS XML (<article>) preserving <p id='...'> paragraph IDs with pdf_page=None."""
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]

    title_el = root.find(".//article-title")
    title = "".join(title_el.itertext()).strip() if title_el is not None else xml_path.stem
    doi_el = root.find(".//article-id[@pub-id-type='doi']")
    doi = "".join(doi_el.itertext()).strip() if doi_el is not None else None

    authors: List[str] = []
    for contrib in root.findall(".//contrib[@contrib-type='author']"):
        given = "".join(contrib.findtext("name/given-names") or "")
        surname = "".join(contrib.findtext("name/surname") or "")
        full = f"{given} {surname}".strip()
        if full:
            authors.append(full)

    year_el = root.find(".//pub-date/year")
    year = "".join(year_el.itertext()).strip() if year_el is not None else None

    paragraphs: List[Dict[str, Any]] = []
    for idx, p_el in enumerate(root.findall(".//abstract//p"), start=1):
        txt = _jats_element_text(p_el)
        if txt:
            pid = p_el.attrib.get("id") or f"{default_source_id}_abs_{idx}"
            paragraphs.append({
                "source_id": default_source_id,
                "paragraph_id": pid,
                "location_type": "jats_paragraph_id" if p_el.get("id") else "xml_generated_locator",
                "pdf_page": None,
                "printed_page": None,
                "bbox": None,
                "section_title": "Abstract",
                "text": txt,
            })

    body = root.find(".//body")
    if body is not None:
        def append_body(element: ET.Element, section: str) -> None:
            if element.tag == "sec":
                heading = element.find("title")
                if heading is not None:
                    section = "".join(heading.itertext()).strip() or section
            if element.tag == "p":
                txt = _jats_element_text(element)
                if txt:
                    pid = element.get("id") or f"{default_source_id}_body_{len(paragraphs) + 1}"
                    paragraphs.append({
                        "source_id": default_source_id,
                        "paragraph_id": pid,
                        "location_type": "jats_paragraph_id" if element.get("id") else "xml_generated_locator",
                        "pdf_page": None,
                        "printed_page": None,
                        "bbox": None,
                        "section_title": section,
                        "text": txt,
                    })
            else:
                for child in element:
                    append_body(child, section)

        append_body(body, "Body")

    xml_notes = _extract_jats_xml_notes(xml_path)
    meta = {
        "source_id": default_source_id,
        "title": title,
        "authors": authors or ["not_reported"],
        "doi": doi,
        "year": year,
        "license": None,
    }
    return meta, paragraphs, xml_notes


def _parse_plain_or_md(path: Path, sid: str) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    suf = path.suffix.lower()
    if suf in (".html", ".htm"):
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        raw = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>|</li>", "\n", raw, flags=re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)

    lines = raw.splitlines()
    title = path.stem
    authors: List[str] = []
    paras: List[Dict[str, Any]] = []
    sec_title = "Body"
    i = 0
    p_idx = 0
    while i < len(lines):
        ln = lines[i].strip()
        if not ln:
            i += 1
            continue
        if ln.startswith("#"):
            htxt = ln.lstrip("#").strip()
            if ln.startswith("# ") and title == path.stem and p_idx == 0:
                title = htxt
            else:
                sec_title = htxt
            i += 1
            continue
        if suf == ".tex":
            m_sec = re.match(r"\\(?:sub)*section\*?\{([^}]+)\}", ln)
            if m_sec:
                sec_title = m_sec.group(1).strip()
                i += 1
                continue
        start_ln = i + 1
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("#"):
            buf.append(lines[i].strip())
            i += 1
        txt = " ".join(buf)
        if txt.lower().startswith("authors:"):
            authors = _normalize_authors(txt.split(":", 1)[1])
            continue
        p_idx += 1
        paras.append({
            "source_id": sid,
            "paragraph_id": f"{sid}_line_{start_ln}_{i}",
            "location_type": "text_line_span",
            "pdf_page": None,
            "printed_page": None,
            "bbox": None,
            "section_title": sec_title,
            "text": txt,
        })
    return {
        "source_id": sid,
        "source_file": path.name,
        "source_sha256": _sha256_file(path),
        "title": title,
        "authors": authors or ["not_reported"],
        "doi": "not_reported",
        "year": "not_reported",
        "license": "not_reported",
        "status": "success" if paras else "unverifiable",
        "location_type": "text_line_span",
        "paragraphs": paras,
        "tables": [],
        "xml_reference_notes": [],
        "uncertainties": [],
        "anomalies": [],
    }


def _read_docx(path: Path, sid: str) -> Dict[str, Any]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f"{ns_w}body")
    paragraphs: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    if body is not None:
        p_idx = 0
        t_idx = 0
        for block in body:
            if block.tag == f"{ns_w}p":
                text = "".join(node.text or "" for node in block.iter(f"{ns_w}t")).strip()
                if text:
                    p_idx += 1
                    paragraphs.append({
                        "source_id": sid,
                        "paragraph_id": f"p{p_idx}",
                        "location_type": "docx_paragraph",
                        "pdf_page": None,
                        "printed_page": None,
                        "bbox": None,
                        "section_title": "Body",
                        "text": text,
                    })
            elif block.tag == f"{ns_w}tbl":
                t_idx += 1
                rows = []
                for tr in block.findall(f"{ns_w}tr"):
                    cells = [
                        re.sub(r"\s+", " ", "".join(t.text or "" for t in tc.iter(f"{ns_w}t"))).strip()
                        for tc in tr.findall(f"{ns_w}tc")
                    ]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    md_rows = [" | ".join(rows[0]), " | ".join("---" for _ in rows[0])]
                    md_rows.extend(" | ".join(r) for r in rows[1:])
                    tables.append({
                        "table_id": f"tab_{t_idx}",
                        "label": f"Table {t_idx}",
                        "caption": f"DOCX Table {t_idx}",
                        "pages": [],
                        "markdown": "\n".join(md_rows),
                        "col_paths": rows[0],
                        "records": [dict(zip(rows[0], r)) for r in rows[1:] if len(r) == len(rows[0])],
                    })
    return {
        "source_id": sid,
        "source_file": path.name,
        "source_sha256": _sha256_file(path),
        "title": path.stem,
        "authors": ["not_reported"],
        "doi": "not_reported",
        "year": "not_reported",
        "license": "not_reported",
        "status": "success" if (paragraphs or tables) else "unverifiable",
        "location_type": "docx_paragraph",
        "paragraphs": paragraphs,
        "tables": tables,
        "xml_reference_notes": [],
        "uncertainties": [],
        "anomalies": [],
    }


def _read_paper(fpath: Path, sid: str, out_p: Path, ocr_mode: str, jats_index: bool) -> Dict[str, Any]:
    suf = fpath.suffix.lower()
    if suf == ".pdf":
        pdf_out = out_p / "parsed" / _sha256_text(str(fpath))[:16]
        doc = run_parse_pipeline(fpath, pdf_out, ocr_mode=ocr_mode, save_crops=False)
        meta = doc.get("metadata", {})
        paras = [
            {
                "source_id": sid,
                "paragraph_id": b["block_id"],
                "location_type": "pdf_block",
                "pdf_page": b["page"],
                "printed_page": b.get("printed_page"),
                "bbox": b["bbox"],
                "section_id": b.get("section_id"),
                "section_title": " > ".join(b.get("section_path", [])) or b.get("role", "body"),
                "text": b.get("structured_text") or b.get("text") or "",
            }
            for b in doc.get("blocks", [])
            if b.get("role") not in ("header", "footer", "table_ref", "reference")
        ]
        # Keep bibliographic metadata from frontmatter if found
        doi = "not_reported"
        year = "not_reported"
        front = "\n".join(p["text"] for p in paras[:8])
        m_doi = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", front)
        if m_doi:
            doi = m_doi.group(1).rstrip(".,;)]")
        m_yr = re.search(r"\b((?:19|20)\d{2})\b", front)
        if m_yr:
            year = m_yr.group(1)
        return {
            "source_id": sid,
            "source_file": fpath.name,
            "source_sha256": _sha256_file(fpath),
            "title": meta.get("title") or fpath.stem,
            "authors": meta.get("authors") or ["not_reported"],
            "doi": doi,
            "year": year,
            "license": "not_reported",
            "status": doc.get("status", "partial"),
            "location_type": "pdf_block",
            "paragraphs": paras,
            "tables": doc.get("tables", []),
            "xml_reference_notes": [],
            "uncertainties": doc.get("uncertainties", []),
            "anomalies": doc.get("diagnostics", {}).get("anomalies", []),
        }
    if suf == ".xml":
        pm, paras, xml_notes = _parse_jats_xml(fpath, sid)
        return {
            "source_id": sid,
            "source_file": fpath.name,
            "source_sha256": _sha256_file(fpath),
            "title": pm.get("title") or fpath.stem,
            "authors": pm.get("authors") or ["not_reported"],
            "doi": pm.get("doi") or "not_reported",
            "year": pm.get("year") or "not_reported",
            "license": "not_reported",
            "status": "success" if paras else "unverifiable",
            "location_type": "jats_paragraph_id",
            "paragraphs": paras,
            "tables": [],
            "xml_reference_notes": xml_notes,
            "uncertainties": [],
            "anomalies": [],
        }
    if suf == ".json":
        raw_obj = json.loads(fpath.read_text(encoding="utf-8-sig"))
        pm, paras = _extract_paragraphs_from_json_doc(raw_obj, sid, jats_index)
        sid = pm.get("source_id") or sid
        return {
            "source_id": sid,
            "source_file": fpath.name,
            "source_sha256": _sha256_file(fpath),
            "title": pm.get("title") or fpath.stem,
            "authors": pm.get("authors") or ["not_reported"],
            "doi": pm.get("doi") or "not_reported",
            "year": pm.get("year") or "not_reported",
            "license": pm.get("license") or "not_reported",
            "status": "success" if paras else "unverifiable",
            "location_type": paras[0]["location_type"] if paras else "json_paragraph_id",
            "paragraphs": paras,
            "tables": [],
            "xml_reference_notes": [],
            "uncertainties": [],
            "anomalies": [],
        }
    if suf == ".jsonl":
        paras = []
        for idx, line in enumerate(fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            _, line_paras = _extract_paragraphs_from_json_doc(obj, sid, jats_index)
            paras.extend(line_paras)
        return {
            "source_id": sid,
            "source_file": fpath.name,
            "source_sha256": _sha256_file(fpath),
            "title": fpath.stem,
            "authors": ["not_reported"],
            "doi": "not_reported",
            "year": "not_reported",
            "license": "not_reported",
            "status": "success" if paras else "unverifiable",
            "location_type": "json_paragraph_id",
            "paragraphs": paras,
            "tables": [],
            "xml_reference_notes": [],
            "uncertainties": [],
            "anomalies": [],
        }
    if suf == ".docx":
        return _read_docx(fpath, sid)
    return _parse_plain_or_md(fpath, sid)


def _local_path(root: Path, parent: Path, name: str) -> Optional[Path]:
    path = (parent / name).resolve()
    if path.is_relative_to(root) and path.is_file():
        return path
    return None


def _entry_path(entry: Dict[str, Any]) -> str:
    for key in ("file", "path", "material_file", "filename", "source_file"):
        if isinstance(entry.get(key), str) and entry[key]:
            return entry[key]
    return ""


def _unavailable_paper(sid: str, file: str, code: str, message: str) -> Dict[str, Any]:
    return {
        "source_id": sid,
        "source_file": file,
        "source_sha256": None,
        "title": sid,
        "authors": ["not_reported"],
        "doi": "not_reported",
        "year": "not_reported",
        "license": "not_reported",
        "status": "unverifiable",
        "location_type": "unavailable",
        "paragraphs": [],
        "tables": [],
        "xml_reference_notes": [],
        "anomalies": [code],
        "uncertainties": [{"code": code, "message": message}],
    }


def _read_source(path: Path, sid: str, root: Path, out: Path, ocr: str, jats_index: bool = False) -> Dict[str, Any]:
    name = path.relative_to(root).as_posix()
    try:
        rec = _read_paper(path, sid, out, ocr, jats_index)
    except (OSError, ValueError, ET.ParseError, BadZipFile, KeyError) as error:
        return _unavailable_paper(sid, name, "unreadable_source", f"{name}: {type(error).__name__}")
    rec["source_file"] = name
    return rec


def _manifest_entries(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("sources", "papers", "corpus", "materials", "documents", "articles", "files", "entries", "records"):
            val = data.get(key)
            if val:
                data = val
                break
        else:
            data = []
    if isinstance(data, dict):
        return [dict(v, source_id=v.get("source_id") or k) for k, v in data.items() if isinstance(v, dict)]
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def _read_manifest(parent: Path, root: Path, out: Path, ocr: str) -> List[Dict[str, Any]]:
    manifest = parent / "corpus-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8-sig")) if manifest.is_file() else {}
    entries = _manifest_entries(data)
    by_id: Dict[str, Dict[str, Any]] = {}
    by_file: Dict[Path, str] = {}
    for index, entry in enumerate(entries, start=1):
        sid = str(entry.get("source_id") or entry.get("paper_id") or entry.get("id") or f"paper_{index}")
        by_id[sid] = entry
        name = _entry_path(entry)
        if name:
            path = _local_path(root, parent, name)
            if path and path.suffix.lower() in PAPER_EXTENSIONS and path.name not in {"material.json", "corpus-manifest.json"}:
                by_file[path] = sid

    for name, sid in (
        ("primary-material.json", "paper_1"),
        ("secondary-material.json", "paper_2"),
        ("secondary-publisher.xml", "paper_2"),
    ):
        path = _local_path(root, parent, name)
        if path:
            if name.endswith(".xml") and (parent / "secondary-material.json").is_file():
                continue
            by_file.setdefault(path, sid)

    papers: List[Dict[str, Any]] = []
    for path, default_sid in by_file.items():
        rec = _read_source(path, default_sid, root, out, ocr, jats_index=True)
        parsed_sid = rec["source_id"]
        sid = default_sid if default_sid in by_id else parsed_sid
        entry = by_id.get(sid, {})
        for key in ("doi", "title", "year", "license"):
            if entry.get(key):
                rec[key] = entry[key]
        if entry.get("authors"):
            rec["authors"] = _normalize_authors(entry["authors"])
        rec["source_id"] = sid
        if entry.get("source_hash") or entry.get("sha256"):
            rec["declared_source_hash"] = entry.get("source_hash") or entry["sha256"]
        for paragraph in rec["paragraphs"]:
            paragraph["source_id"] = sid
        # Attach paired secondary-publisher.xml formula/table cross-check notes to paper_2
        sec_xml = parent / "secondary-publisher.xml"
        if path.name == "secondary-material.json" and sec_xml.is_file():
            rec["xml_reference_notes"] = _extract_jats_xml_notes(sec_xml)
        papers.append(rec)

    combined = parent / "material.json"
    covered = {p["source_id"] for p in papers if p["paragraphs"]}
    if combined.is_file() and (not covered or set(by_id) - covered):
        combined_data = json.loads(combined.read_text(encoding="utf-8-sig"))
        _, paragraphs = _extract_paragraphs_from_json_doc(combined_data, "", jats_index=True)
        groups: Dict[str, list] = {}
        for para in paragraphs:
            sid = para["source_id"]
            if sid and sid not in covered and (not by_id or sid in by_id):
                groups.setdefault(sid, []).append(para)
        for sid, group in groups.items():
            entry = by_id.get(sid, {})
            papers = [p for p in papers if p["source_id"] != sid]
            sec_xml = parent / "secondary-publisher.xml"
            xml_notes = _extract_jats_xml_notes(sec_xml) if sid in {"paper_2", "secondary"} and sec_xml.is_file() else []
            papers.append({
                "source_id": sid,
                "source_file": combined.relative_to(root).as_posix(),
                "source_sha256": _sha256_file(combined),
                "title": entry.get("title") or sid,
                "authors": _normalize_authors(entry.get("authors")),
                "doi": entry.get("doi") or "not_reported",
                "year": entry.get("year") or "not_reported",
                "license": entry.get("license") or "not_reported",
                "status": "success",
                "location_type": group[0]["location_type"],
                "paragraphs": group,
                "tables": [],
                "xml_reference_notes": xml_notes,
                "uncertainties": [],
                "anomalies": [],
            })

    present = {p["source_id"] for p in papers}
    for sid, entry in by_id.items():
        if sid not in present:
            rec = _unavailable_paper(sid, _entry_path(entry), "missing_source", f"No readable material for {sid}.")
            for k in ("title", "doi", "year", "license"):
                if entry.get(k):
                    rec[k] = entry[k]
            if entry.get("authors"):
                rec["authors"] = _normalize_authors(entry["authors"])
            papers.append(rec)
    return papers


def _scan_abbreviations_and_boundaries(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    definitions: List[Dict[str, Any]] = []
    per_paper_defs: Dict[str, Dict[str, set[str]]] = {}
    all_forms: Dict[str, set[str]] = {}
    boundaries: List[Dict[str, Any]] = []
    term_sources: Dict[str, set[str]] = {}

    for paper in papers:
        sid = paper["source_id"]
        seen_defs = set()
        for para in paper.get("paragraphs", []):
            pid = para["paragraph_id"]
            text = para["text"]
            for m in ABBREVIATION_RE.finditer(text):
                short = m.group(2).strip()
                full = _extract_long_form(m.group(1), short)
                if not full:
                    continue
                dk = (sid, pid, short, full)
                if dk not in seen_defs:
                    seen_defs.add(dk)
                    start = m.start(1) + m.group(1).rfind(full)
                    definitions.append({
                        "short": short,
                        "full": full,
                        "source_id": sid,
                        "paragraph_id": pid,
                        "location_type": para["location_type"],
                        "pdf_page": para.get("pdf_page"),
                        "quote_snippet": text[start:m.end()],
                    })
                sk = _canon_term(short)
                fk = _canon_term(full)
                per_paper_defs.setdefault(sid, {}).setdefault(sk, set()).add(fk)
                all_forms.setdefault(sk, set()).add(fk)
                term_sources.setdefault(fk, set()).add(sid)

            for sent in SENT_SPLIT_RE.split(text):
                s = sent.strip()
                if not s:
                    continue
                cat = next((k for k, pat in REPORTED_BOUNDARIES if pat.search(s)), None)
                if cat:
                    boundaries.append({
                        "source_id": sid,
                        "paragraph_id": pid,
                        "location_type": para["location_type"],
                        "pdf_page": para.get("pdf_page"),
                        "category": cat,
                        "quote_snippet": s[:240],
                    })

    conflicts = []
    for short, forms in sorted(all_forms.items()):
        if len(forms) >= 2:
            ev = [d for d in definitions if _canon_term(d["short"]) == short]
            conflicts.append({
                "abbreviation": ev[0]["short"],
                "definitions": sorted(forms),
                "sources": sorted({d["source_id"] for d in ev}),
                "evidence": ev,
            })

    shared_candidates = sorted(term for term, sids in term_sources.items() if len(sids) >= 2)
    return {
        "abbreviation_definitions": definitions,
        "abbreviation_conflicts": conflicts,
        "shared_term_candidates": shared_candidates,
        "boundary_records": boundaries,
    }


def ingest_corpus(
    input_path: str | Path,
    out_dir: str | Path,
    case_path: Optional[str | Path] = None,
    ocr_mode: str = "auto",
) -> Dict[str, Any]:
    """Resolve the case inventory first, preserve per-paper isolation, and deduplicate multi-format copies."""
    source = Path(input_path).resolve()
    root = source if source.is_dir() else source.parent
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    case = json.loads(Path(case_path).read_text(encoding="utf-8-sig")) if case_path and Path(case_path).is_file() else {}
    declared = case.get("input_files") or []
    missing: List[str] = []

    if source.is_file():
        files = [source]
    elif isinstance(declared, list) and declared:
        files = []
        for entry in declared:
            name = entry if isinstance(entry, str) else _entry_path(entry) if isinstance(entry, dict) else ""
            path = _local_path(root, root, name) if name else None
            if path:
                files.append(path)
            elif name:
                missing.append(name)
    else:
        files = [
            p for p in root.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and not (out != root and p.is_relative_to(out))
            and p.resolve().is_relative_to(root)
        ]

    files = sorted({f for f in files if f.suffix.lower() in PAPER_EXTENSIONS and f.name.lower() not in IGNORE_FILES})
    manifests = sorted({f.parent for f in files if f.name == "corpus-manifest.json"})
    if not manifests:
        manifests = sorted({f.parent for f in files if f.name in {"primary-material.json", "secondary-material.json"}})

    records: List[Dict[str, Any]] = []
    if manifests:
        for parent in manifests:
            records.extend(_read_manifest(parent, root, out, ocr_mode))
    if manifests and declared:
        already_read = {rec["source_file"] for rec in records}
        loose = [
            f for f in files
            if f.relative_to(root).as_posix() not in already_read
            and f.name not in {"corpus-manifest.json", "material.json", "secondary-publisher.xml"}
        ]
    else:
        loose = [] if manifests else files

    for index, path in enumerate(loose, start=1):
        rec = _read_source(path, f"paper_{index}", root, out, ocr_mode)
        groups: Dict[str, list] = {}
        for para in rec["paragraphs"]:
            groups.setdefault(para["source_id"], []).append(para)
        if not groups or set(groups) == {rec["source_id"]}:
            records.append(rec)
            continue
        for sid, paragraphs in groups.items():
            scoped = dict(rec, source_id=sid, paragraphs=paragraphs)
            if sid != rec["source_id"]:
                scoped.update(
                    title=sid,
                    authors=["not_reported"],
                    doi="not_reported",
                    year="not_reported",
                    license="not_reported",
                )
            records.append(scoped)

    # Inline material fallback inside case.json if no external files found
    if not records and isinstance(case, dict):
        for k in INLINE_MATERIAL_KEYS:
            val = case.get(k)
            if isinstance(val, list) and val:
                for idx, item in enumerate(val, 1):
                    pm, paras = _extract_paragraphs_from_json_doc(item, f"paper_{idx}", jats_index=True)
                    if paras:
                        records.append({
                            "source_id": pm.get("source_id") or f"paper_{idx}",
                            "source_file": "case.json",
                            "source_sha256": _sha256_text(json.dumps(item, sort_keys=True, ensure_ascii=False)),
                            "title": pm.get("title") or f"paper_{idx}",
                            "authors": pm.get("authors") or ["not_reported"],
                            "doi": pm.get("doi") or "not_reported",
                            "year": pm.get("year") or "not_reported",
                            "license": pm.get("license") or "not_reported",
                            "status": "success",
                            "location_type": paras[0]["location_type"],
                            "paragraphs": paras,
                            "tables": [],
                            "xml_reference_notes": [],
                            "uncertainties": [],
                            "anomalies": [],
                        })
                if records:
                    break
            elif isinstance(val, str) and val.strip():
                paras = [
                    {
                        "source_id": "paper_1",
                        "paragraph_id": f"case_p{idx}",
                        "location_type": "json_paragraph_id",
                        "pdf_page": None,
                        "printed_page": None,
                        "bbox": None,
                        "section_title": "Body",
                        "text": chunk.strip(),
                    }
                    for idx, chunk in enumerate(re.split(r"\n\s*\n", val.strip()), 1)
                    if chunk.strip()
                ]
                if paras:
                    records.append({
                        "source_id": "paper_1",
                        "source_file": "case.json",
                        "source_sha256": _sha256_text(val),
                        "title": case.get("title") or "paper_1",
                        "authors": ["not_reported"],
                        "doi": "not_reported",
                        "year": "not_reported",
                        "license": "not_reported",
                        "status": "success",
                        "location_type": "json_paragraph_id",
                        "paragraphs": paras,
                        "tables": [],
                        "xml_reference_notes": [],
                        "uncertainties": [],
                        "anomalies": [],
                    })
                    break

    papers: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    fingerprints: Dict[str, str] = {}
    used_ids = set()
    doi_by_source: Dict[str, str] = {}

    for rec in records:
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(rec["doi"]).strip().casefold())
        body = " ".join(" ".join(p["text"].split()).casefold() for p in rec["paragraphs"])
        keys = ["text:" + _sha256_text(body)] if body else []
        if doi and doi != "not_reported":
            keys.append("doi:" + doi)
        match = None
        for key in keys:
            existing = fingerprints.get(key)
            if existing and (doi in ("", "not_reported") or doi_by_source[existing] in ("", "not_reported", doi)):
                match = existing
                break
        if match:
            duplicates.append({
                "skipped_file": rec["source_file"],
                "duplicate_format_of": match,
                "reason": "same_paper_multi_format_deduplicated",
            })
            continue
        sid = rec["source_id"]
        if sid in used_ids:
            sid = f"{sid}_{_sha256_text(rec['source_file'])[:8]}"
            rec["source_id"] = sid
            for para in rec["paragraphs"]:
                para["source_id"] = sid
        used_ids.add(sid)
        doi_by_source[sid] = doi
        fingerprints.update(dict.fromkeys(keys, sid))
        papers.append(rec)

    signals = _scan_abbreviations_and_boundaries(papers)
    corpus = {
        "schema_version": "2.0.0",
        "corpus_mode": "revised_manifest_jats" if manifests else "standard_files",
        "paper_count": len(papers),
        "papers": papers,
        "duplicate_formats_skipped": duplicates,
        "missing_input_files": missing,
        **signals,
    }
    write_json_atomic(out / "corpus.json", corpus)
    return corpus


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest multi-paper corpus into unified corpus.json.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    args = parser.parse_args()

    corpus = ingest_corpus(args.input, args.out, case_path=args.case, ocr_mode=args.ocr)
    print(json.dumps({
        "paper_count": corpus["paper_count"],
        "duplicate_formats_skipped": len(corpus["duplicate_formats_skipped"]),
        "missing_input_files": len(corpus["missing_input_files"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
