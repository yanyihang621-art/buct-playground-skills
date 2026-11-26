#!/usr/bin/env python3
"""Multi-paper corpus ingestion supporting Harbor revised JATS/JSON manifests and PDF/MD/TXT with multi-format dedup."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from parse_pdf import run_parse_pipeline
from pdf_utils import write_json_atomic

IGNORE_FILES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_paragraphs_from_json_doc(data: Any, default_source_id: str,
                                      jats_index: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Retain per-paper IDs, and distinguish supplied locators from generated ones."""
    paper = data if isinstance(data, dict) else {}
    sid = str(paper.get("source_id") or paper.get("id") or default_source_id)
    meta = {
        "source_id": sid, "title": paper.get("title") or paper.get("paper_title"),
        "authors": paper.get("authors") if isinstance(paper.get("authors"), list) else [],
        "doi": paper.get("doi"), "year": paper.get("year"), "license": paper.get("license"),
    }
    paragraphs: List[Dict[str, Any]] = []

    def append(item: Any, section: str, fallback_id: str) -> None:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            return
        text = item.get("text") or item.get("content")
        if not isinstance(text, str) or not text.strip():
            return
        reported_id = item.get("jats_id") or item.get("paragraph_id") or item.get("pid") or item.get("id")
        locator = item.get("location_type") or (
            "jats_paragraph_id" if reported_id and (jats_index or item.get("jats_id") or paper.get("jats"))
            else "json_paragraph_id"
        )
        paragraphs.append({
            "source_id": str(item.get("source_id") or sid), "paragraph_id": str(reported_id or fallback_id),
            "location_type": locator if reported_id else "json_generated_locator",
            "pdf_page": None, "printed_page": None, "bbox": None,
            "section_title": item.get("section_title") or item.get("section") or section, "text": text.strip(),
        })

    if isinstance(paper.get("abstract"), str):
        append({"text": paper["abstract"], "id": paper.get("abstract_id")}, "Abstract", f"{sid}_abs")
    items = data if isinstance(data, list) else paper.get("paragraphs") or paper.get("blocks") or paper.get("items") or []
    if isinstance(items, list):
        for idx, item in enumerate(items, start=1):
            append(item, "Body", f"{sid}_p{idx}")
    if not items:
        for sec_idx, section in enumerate(paper.get("sections") or [], start=1):
            if isinstance(section, dict):
                for idx, item in enumerate(section.get("paragraphs") or [], start=1):
                    append(item, section.get("title") or f"Section {sec_idx}", f"{sid}_s{sec_idx}_p{idx}")
    return meta, paragraphs


def _parse_jats_xml(xml_path: Path, default_source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse JATS XML (<article>) preserving <p id='...'> paragraph IDs without fake PDF page numbers."""
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
    # Abstract paragraphs
    for idx, p_el in enumerate(root.findall(".//abstract//p"), start=1):
        txt = re.sub(r"\s+", " ", "".join(p_el.itertext())).strip()
        if txt:
            pid = p_el.attrib.get("id") or f"{default_source_id}_abs_{idx}"
            paragraphs.append(
                {
                    "source_id": default_source_id,
                    "paragraph_id": pid,
                    "location_type": "jats_paragraph_id" if p_el.get("id") else "xml_generated_locator",
                    "pdf_page": None,
                    "printed_page": None,
                    "bbox": None,
                    "section_title": "Abstract",
                    "text": txt,
                }
            )

    # Visit all body paragraphs in source order, including direct children of body.
    body = root.find(".//body")
    if body is not None:
        def append_body(element: ET.Element, section: str) -> None:
            if element.tag == "sec":
                heading = element.find("title")
                if heading is not None:
                    section = "".join(heading.itertext()).strip() or section
            if element.tag == "p":
                txt = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
                if txt:
                    pid = element.get("id") or f"{default_source_id}_body_{len(paragraphs) + 1}"
                    paragraphs.append({
                        "source_id": default_source_id, "paragraph_id": pid,
                        "location_type": "jats_paragraph_id" if element.get("id") else "xml_generated_locator",
                        "pdf_page": None, "printed_page": None, "bbox": None,
                        "section_title": section, "text": txt,
                    })
            else:
                for child in element:
                    append_body(child, section)
        append_body(body, "Body")

    meta = {
        "source_id": default_source_id,
        "title": title,
        "authors": authors,
        "doi": doi,
        "year": year,
        "license": None,
    }
    return meta, paragraphs


def _parse_plain_or_md(path: Path, sid: str) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
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
        start_ln = i + 1
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("#"):
            buf.append(lines[i].strip())
            i += 1
        txt = " ".join(buf)
        if txt.lower().startswith("authors:"):
            authors = [a.strip() for a in txt.split(":", 1)[1].split(",") if a.strip()]
            continue
        p_idx += 1
        paras.append(
            {
                "source_id": sid,
                "paragraph_id": f"{sid}_line_{start_ln}_{i}",
                "location_type": "text_line_span",
                "pdf_page": None,
                "printed_page": None,
                "bbox": None,
                "section_title": sec_title,
                "text": txt,
            }
        )
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
        "uncertainties": [],
        "anomalies": [],
    }


PAPER_EXTENSIONS = {".pdf", ".md", ".txt", ".json", ".xml", ".docx"}


def _read_docx(path: Path, sid: str) -> Dict[str, Any]:
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for index, element in enumerate(root.findall(".//w:body//w:p", ns), start=1):
        text = "".join(node.text or "" for node in element.findall(".//w:t", ns)).strip()
        if text:
            paragraphs.append({
                "source_id": sid, "paragraph_id": f"p{index}", "location_type": "docx_paragraph",
                "pdf_page": None, "printed_page": None, "bbox": None, "section_title": "Body", "text": text,
            })
    return {
        "source_id": sid, "source_file": path.name, "source_sha256": _sha256_file(path),
        "title": path.stem, "authors": ["not_reported"], "doi": "not_reported",
        "year": "not_reported", "license": "not_reported", "status": "success" if paragraphs else "unverifiable",
        "location_type": "docx_paragraph", "paragraphs": paragraphs, "tables": [], "uncertainties": [], "anomalies": [],
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
                "section_title": " > ".join(b.get("section_path", [])) or b.get("role", "body"),
                "text": b.get("structured_text") or b.get("text") or "",
            }
            for b in doc.get("blocks", [])
            if b.get("role") not in ("header", "footer", "table_ref", "reference")
        ]
        rec = {
            "source_id": sid,
            "source_file": fpath.name,
            "source_sha256": _sha256_file(fpath),
            "title": meta.get("title") or fpath.stem,
            "authors": meta.get("authors") or ["not_reported"],
            "doi": "not_reported",
            "year": "not_reported",
            "license": "not_reported",
            "status": doc.get("status", "partial"),
            "location_type": "pdf_block",
            "paragraphs": paras,
            "tables": doc.get("tables", []),
            "uncertainties": doc.get("uncertainties", []),
            "anomalies": doc.get("diagnostics", {}).get("anomalies", []),
        }
    elif suf == ".xml":
        pm, paras = _parse_jats_xml(fpath, sid)
        rec = {
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
            "uncertainties": [],
            "anomalies": [],
        }
    elif suf == ".json":
        raw_obj = json.loads(fpath.read_text(encoding="utf-8-sig"))
        pm, paras = _extract_paragraphs_from_json_doc(raw_obj, sid, jats_index)
        sid = pm.get("source_id") or sid
        rec = {
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
            "uncertainties": [],
            "anomalies": [],
        }
    elif suf == ".docx":
        rec = _read_docx(fpath, sid)
    else:
        rec = _parse_plain_or_md(fpath, sid)

    return rec


def _local_path(root: Path, parent: Path, name: str) -> Optional[Path]:
    path = (parent / name).resolve()
    if path.is_relative_to(root) and path.is_file():
        return path
    return None


def _entry_path(entry: Dict[str, Any]) -> str:
    for key in ("file", "path", "material_file", "filename"):
        if isinstance(entry.get(key), str) and entry[key]:
            return entry[key]
    return ""


def _unavailable_paper(sid: str, file: str, code: str, message: str) -> Dict[str, Any]:
    return {
        "source_id": sid, "source_file": file, "source_sha256": None,
        "title": sid, "authors": ["not_reported"], "doi": "not_reported",
        "year": "not_reported", "license": "not_reported", "status": "unverifiable",
        "location_type": "unavailable", "paragraphs": [], "tables": [], "anomalies": [code],
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


def _read_manifest(parent: Path, root: Path, out: Path, ocr: str) -> List[Dict[str, Any]]:
    manifest = parent / "corpus-manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8-sig")) if manifest.is_file() else {}
    if isinstance(data, dict):
        entries = data.get("sources") or data.get("papers") or data.get("corpus") or data.get("materials") or []
    else:
        entries = data
    if isinstance(entries, dict):
        entries = [dict(value, source_id=value.get("source_id") or key) for key, value in entries.items() if isinstance(value, dict)]
    by_id = {}
    by_file = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("source_id") or entry.get("id") or f"paper_{index}")
        by_id[sid] = entry
        name = _entry_path(entry)
        if name:
            path = _local_path(root, parent, name)
            if path and path.suffix.lower() in PAPER_EXTENSIONS and path.name not in {"material.json", "corpus-manifest.json"}:
                by_file[path] = sid

    # Some releases use conventional filenames and a metadata-only manifest.
    for name, sid in (("primary-material.json", "paper_1"), ("secondary-material.json", "paper_2"),
                      ("secondary-publisher.xml", "paper_2")):
        path = _local_path(root, parent, name)
        if path:
            if name.endswith(".xml") and (parent / "secondary-material.json").is_file():
                continue
            by_file.setdefault(path, sid)

    papers = []
    for path, default_sid in by_file.items():
        rec = _read_source(path, default_sid, root, out, ocr, jats_index=True)
        parsed_sid = rec["source_id"]
        sid = default_sid if default_sid in by_id else parsed_sid
        entry = by_id.get(sid, {})
        for key in ("doi", "title", "authors", "year", "license"):
            if entry.get(key):
                rec[key] = entry[key]
        rec["source_id"] = sid
        if entry.get("source_hash") or entry.get("sha256"):
            rec["declared_source_hash"] = entry.get("source_hash") or entry["sha256"]
        for paragraph in rec["paragraphs"]:
            paragraph["source_id"] = sid
        papers.append(rec)

    # A combined index can fill an absent paper only with paragraphs attributed to that paper.
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
            papers.append({
                "source_id": sid, "source_file": combined.relative_to(root).as_posix(),
                "source_sha256": _sha256_file(combined),
                "title": entry.get("title") or sid, "authors": entry.get("authors") or ["not_reported"],
                "doi": entry.get("doi") or "not_reported", "year": entry.get("year") or "not_reported",
                "license": entry.get("license") or "not_reported", "status": "success",
                "location_type": group[0]["location_type"], "paragraphs": group, "tables": [],
                "uncertainties": [], "anomalies": [],
            })
    present = {p["source_id"] for p in papers}
    for sid, entry in by_id.items():
        if sid not in present:
            rec = _unavailable_paper(sid, _entry_path(entry), "missing_source", f"No readable material for {sid}.")
            rec.update({key: entry[key] for key in ("title", "authors", "doi", "year", "license") if entry.get(key)})
            papers.append(rec)
    return papers


def ingest_corpus(input_path: str | Path, out_dir: str | Path, case_path: Optional[str | Path] = None,
                  ocr_mode: str = "auto") -> Dict[str, Any]:
    """Resolve the case inventory first, and deduplicate representations after parsing."""
    source = Path(input_path).resolve()
    root = source if source.is_dir() else source.parent
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    case = json.loads(Path(case_path).read_text(encoding="utf-8-sig")) if case_path and Path(case_path).is_file() else {}
    declared = case.get("input_files") or []
    missing = []
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
        files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()
                 and not (out != root and p.is_relative_to(out)) and p.resolve().is_relative_to(root)]
    files = sorted(set(f for f in files if f.suffix.lower() in PAPER_EXTENSIONS and f.name.lower() not in IGNORE_FILES))
    manifests = sorted({f.parent for f in files if f.name == "corpus-manifest.json"})
    if not manifests:
        manifests = sorted({f.parent for f in files if f.name in {"primary-material.json", "secondary-material.json"}})
    records = []
    if manifests:
        for parent in manifests:
            records.extend(_read_manifest(parent, root, out, ocr_mode))
    if manifests and declared:
        already_read = {rec["source_file"] for rec in records}
        loose = [f for f in files if f.relative_to(root).as_posix() not in already_read and
                 f.name not in {"corpus-manifest.json", "material.json", "secondary-publisher.xml"}]
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
        # Some standalone JSON indexes contain independent papers. Root metadata
        # can be assigned only to the paper explicitly identified by the root.
        for sid, paragraphs in groups.items():
            scoped = dict(rec, source_id=sid, paragraphs=paragraphs)
            if sid != rec["source_id"]:
                scoped.update(title=sid, authors=["not_reported"], doi="not_reported",
                              year="not_reported", license="not_reported")
            records.append(scoped)

    papers, duplicates = [], []
    fingerprints: Dict[str, str] = {}
    used_ids = set()
    doi_by_source = {}
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
            duplicates.append({"skipped_file": rec["source_file"], "duplicate_format_of": match,
                               "reason": "same_paper_multi_format_deduplicated"})
            continue
        sid = rec["source_id"]
        if sid in used_ids:
            sid = f"{sid}_{_sha256_text(rec['source_file'])[:10]}"
            rec["source_id"] = sid
            for para in rec["paragraphs"]:
                para["source_id"] = sid
        used_ids.add(sid)
        doi_by_source[sid] = doi
        fingerprints.update(dict.fromkeys(keys, sid))
        papers.append(rec)

    corpus = {"schema_version": "1.0.0", "corpus_mode": "revised_manifest_jats" if manifests else "standard_files",
              "paper_count": len(papers), "papers": papers, "duplicate_formats_skipped": duplicates,
              "missing_input_files": missing}
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
    print(json.dumps({"paper_count": corpus["paper_count"], "duplicate_formats_skipped": len(corpus["duplicate_formats_skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
