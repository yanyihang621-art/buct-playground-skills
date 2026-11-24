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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def _extract_paragraphs_from_json_doc(data: Any, default_source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Extract per-paper metadata and paragraph records from flexible JSON material structures."""
    meta: Dict[str, Any] = {
        "source_id": default_source_id,
        "title": None,
        "authors": [],
        "doi": None,
        "year": None,
        "license": None,
    }
    paragraphs: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        meta["source_id"] = str(data.get("source_id") or data.get("id") or default_source_id)
        meta["title"] = data.get("title") or data.get("paper_title")
        if isinstance(data.get("authors"), list):
            meta["authors"] = [str(a) for a in data["authors"]]
        meta["doi"] = data.get("doi")
        meta["year"] = str(data["year"]) if data.get("year") else None
        meta["license"] = data.get("license")

        # Abstract if present
        if data.get("abstract"):
            paragraphs.append(
                {
                    "source_id": meta["source_id"],
                    "paragraph_id": str(data.get("abstract_id") or f"{meta['source_id']}_abs"),
                    "location_type": "jats_paragraph_id" if data.get("jats") else "json_paragraph_id",
                    "pdf_page": None,
                    "printed_page": None,
                    "bbox": None,
                    "section_title": "Abstract",
                    "text": str(data["abstract"]).strip(),
                }
            )

        raw_list = data.get("paragraphs") or data.get("blocks") or data.get("items") or []
        if isinstance(raw_list, list):
            for idx, item in enumerate(raw_list, start=1):
                if isinstance(item, dict):
                    txt = str(item.get("text") or item.get("content") or "").strip()
                    if not txt:
                        continue
                    pid = str(item.get("paragraph_id") or item.get("id") or item.get("pid") or f"{meta['source_id']}_p{idx}")
                    sid = str(item.get("source_id") or meta["source_id"])
                    paragraphs.append(
                        {
                            "source_id": sid,
                            "paragraph_id": pid,
                            "location_type": str(item.get("location_type") or "jats_paragraph_id"),
                            "pdf_page": None,
                            "printed_page": None,
                            "bbox": None,
                            "section_title": str(item.get("section_title") or item.get("section") or "Body"),
                            "text": txt,
                        }
                    )
        # Nested sections list
        secs = data.get("sections")
        if isinstance(secs, list) and not raw_list:
            for s_idx, sec in enumerate(secs, start=1):
                if not isinstance(sec, dict):
                    continue
                stitle = str(sec.get("title") or f"Section {s_idx}")
                for p_idx, pitem in enumerate(sec.get("paragraphs", []), start=1):
                    if isinstance(pitem, dict):
                        txt = str(pitem.get("text") or pitem.get("content") or "").strip()
                        pid = str(pitem.get("paragraph_id") or pitem.get("id") or f"{meta['source_id']}_s{s_idx}_p{p_idx}")
                    else:
                        txt = str(pitem).strip()
                        pid = f"{meta['source_id']}_s{s_idx}_p{p_idx}"
                    if txt:
                        paragraphs.append(
                            {
                                "source_id": meta["source_id"],
                                "paragraph_id": pid,
                                "location_type": "jats_paragraph_id",
                                "pdf_page": None,
                                "printed_page": None,
                                "bbox": None,
                                "section_title": stitle,
                                "text": txt,
                            }
                        )
    elif isinstance(data, list):
        for idx, item in enumerate(data, start=1):
            if isinstance(item, dict):
                txt = str(item.get("text") or item.get("content") or "").strip()
                if not txt:
                    continue
                sid = str(item.get("source_id") or default_source_id)
                pid = str(item.get("paragraph_id") or item.get("id") or f"{sid}_p{idx}")
                paragraphs.append(
                    {
                        "source_id": sid,
                        "paragraph_id": pid,
                        "location_type": str(item.get("location_type") or "jats_paragraph_id"),
                        "pdf_page": None,
                        "printed_page": None,
                        "bbox": None,
                        "section_title": str(item.get("section_title") or item.get("section") or "Body"),
                        "text": txt,
                    }
                )
    return meta, paragraphs


def _parse_jats_xml(xml_path: Path, default_source_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse JATS XML (<article>) preserving <p id='...'> paragraph IDs without fake PDF page numbers."""
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
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
                    "location_type": "jats_paragraph_id",
                    "pdf_page": None,
                    "printed_page": None,
                    "bbox": None,
                    "section_title": "Abstract",
                    "text": txt,
                }
            )

    # Body sections
    body = root.find(".//body")
    if body is not None:
        sec_idx = 0
        for sec in body.findall(".//sec"):
            sec_idx += 1
            stitle = "".join(sec.find("title").itertext()).strip() if sec.find("title") is not None else f"Section {sec_idx}"
            for p_idx, p_el in enumerate(sec.findall("p"), start=1):
                txt = re.sub(r"\s+", " ", "".join(p_el.itertext())).strip()
                if txt:
                    pid = p_el.attrib.get("id") or f"{default_source_id}_s{sec_idx}_p{p_idx}"
                    paragraphs.append(
                        {
                            "source_id": default_source_id,
                            "paragraph_id": pid,
                            "location_type": "jats_paragraph_id",
                            "pdf_page": None,
                            "printed_page": None,
                            "bbox": None,
                            "section_title": stitle,
                            "text": txt,
                        }
                    )

    meta = {
        "source_id": default_source_id,
        "title": title,
        "authors": authors,
        "doi": doi,
        "year": year,
        "license": None,
    }
    return meta, paragraphs


def _ingest_revised_manifest(in_dir: Path) -> Optional[Dict[str, Any]]:
    """Ingest Harbor revised corpus-manifest.json / primary-material.json / secondary-material.json / JATS XML."""
    manifest_path = in_dir / "corpus-manifest.json"
    prim_path = in_dir / "primary-material.json"
    sec_path = in_dir / "secondary-material.json"
    if not (manifest_path.exists() or prim_path.exists() or sec_path.exists()):
        return None

    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    entries = (
        manifest_raw.get("sources")
        or manifest_raw.get("papers")
        or manifest_raw.get("corpus")
        or manifest_raw.get("materials")
        or []
    )
    if isinstance(entries, dict):
        entries = [dict({"source_id": k}, **v) for k, v in entries.items()]

    # Build source_id -> manifest metadata lookup
    manifest_meta_by_file: Dict[str, Dict[str, Any]] = {}
    manifest_meta_by_id: Dict[str, Dict[str, Any]] = {}
    for idx, ent in enumerate(entries, start=1):
        if not isinstance(ent, dict):
            continue
        sid = str(ent.get("source_id") or ent.get("id") or f"paper_{idx}")
        manifest_meta_by_id[sid] = ent
        for k in ("file", "path", "material_file", "filename"):
            if ent.get(k):
                manifest_meta_by_file[Path(str(ent[k])).name] = dict(ent, source_id=sid)

    papers_out: List[Dict[str, Any]] = []
    seen_sids = set()

    candidate_files: List[Tuple[str, Path]] = []
    if prim_path.exists():
        candidate_files.append(("paper_1", prim_path))
    if sec_path.exists():
        candidate_files.append(("paper_2", sec_path))

    # Also check any manifest-listed files not already added
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        fpath_str = ent.get("file") or ent.get("path") or ent.get("material_file")
        if fpath_str:
            fp = in_dir / str(fpath_str)
            if fp.exists() and fp.name not in ("material.json", "corpus-manifest.json"):
                if not any(fp.resolve() == p.resolve() for _, p in candidate_files):
                    candidate_files.append((str(ent.get("source_id") or fp.stem), fp))

    # Fallback: JATS XML if secondary-material.json wasn't present
    if not sec_path.exists() and (in_dir / "secondary-publisher.xml").exists():
        candidate_files.append(("paper_2", in_dir / "secondary-publisher.xml"))

    for default_sid, fpath in candidate_files:
        m_entry = manifest_meta_by_file.get(fpath.name, {})
        if fpath.suffix.lower() == ".xml":
            parsed_meta, paras = _parse_jats_xml(fpath, m_entry.get("source_id") or default_sid)
        else:
            raw_obj = json.loads(fpath.read_text(encoding="utf-8"))
            parsed_meta, paras = _extract_paragraphs_from_json_doc(raw_obj, m_entry.get("source_id") or default_sid)

        sid = str(m_entry.get("source_id") or parsed_meta["source_id"] or default_sid)
        if sid in seen_sids:
            continue
        seen_sids.add(sid)

        # Normalize all paragraph source_id values strictly to this paper's source_id
        for p in paras:
            p["source_id"] = sid

        papers_out.append(
            {
                "source_id": sid,
                "source_file": str(fpath.name),
                "source_sha256": m_entry.get("source_hash") or m_entry.get("sha256") or _sha256_file(fpath),
                "title": m_entry.get("title") or parsed_meta.get("title") or sid,
                "authors": m_entry.get("authors") or parsed_meta.get("authors") or ["not_reported"],
                "doi": m_entry.get("doi") or parsed_meta.get("doi") or "not_reported",
                "year": str(m_entry.get("year") or parsed_meta.get("year") or "not_reported"),
                "license": m_entry.get("license") or parsed_meta.get("license") or "not_reported",
                "status": "success" if paras else "unverifiable",
                "location_type": "jats_paragraph_id",
                "paragraphs": paras,
                "tables": [],
                "uncertainties": [],
                "anomalies": [],
            }
        )

    # If material.json exists and no separate primary/secondary files were found, split by source_id inside material.json
    mat_path = in_dir / "material.json"
    if not papers_out and mat_path.exists():
        mat_raw = json.loads(mat_path.read_text(encoding="utf-8"))
        _, all_paras = _extract_paragraphs_from_json_doc(mat_raw, "paper_1")
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for p in all_paras:
            grouped.setdefault(p["source_id"], []).append(p)
        for sid, paras in grouped.items():
            m_ent = manifest_meta_by_id.get(sid, {})
            papers_out.append(
                {
                    "source_id": sid,
                    "source_file": mat_path.name,
                    "source_sha256": m_ent.get("source_hash") or _sha256_file(mat_path),
                    "title": m_ent.get("title") or sid,
                    "authors": m_ent.get("authors") or ["not_reported"],
                    "doi": m_ent.get("doi") or "not_reported",
                    "year": str(m_ent.get("year") or "not_reported"),
                    "license": m_ent.get("license") or "not_reported",
                    "status": "success" if paras else "unverifiable",
                    "location_type": "jats_paragraph_id",
                    "paragraphs": paras,
                    "tables": [],
                    "uncertainties": [],
                    "anomalies": [],
                }
            )

    return {
        "schema_version": "1.0.0",
        "corpus_mode": "revised_manifest_jats",
        "paper_count": len(papers_out),
        "papers": papers_out,
        "duplicate_formats_skipped": [],
    }


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


def ingest_corpus(
    input_path: str | Path,
    out_dir: str | Path,
    case_path: Optional[str | Path] = None,
    ocr_mode: str = "auto",
) -> Dict[str, Any]:
    """Ingest a single or multi-paper corpus into unified corpus.json."""
    in_p = Path(input_path)
    out_p = Path(out_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    if in_p.is_dir():
        revised = _ingest_revised_manifest(in_p)
        if revised is not None and revised["papers"]:
            write_json_atomic(out_p / "corpus.json", revised)
            return revised

    # Standard file discovery (PDF / MD / TXT / JSON)
    files: List[Path] = []
    if case_path and Path(case_path).exists():
        try:
            cobj = json.loads(Path(case_path).read_text(encoding="utf-8"))
            if isinstance(cobj.get("input_files"), list) and cobj["input_files"]:
                base = in_p if in_p.is_dir() else in_p.parent
                files = [base / str(x) if not Path(str(x)).is_absolute() else Path(str(x)) for x in cobj["input_files"]]
                files = [f for f in files if f.exists()]
        except Exception:
            files = []

    if not files:
        if in_p.is_file():
            files = [in_p]
        elif in_p.is_dir():
            files = [
                p
                for p in sorted(in_p.iterdir())
                if p.is_file()
                and p.suffix.lower() in (".pdf", ".md", ".txt", ".json", ".xml")
                and p.name.lower() not in IGNORE_FILES
            ]

    papers: List[Dict[str, Any]] = []
    dup_skipped: List[Dict[str, str]] = []
    seen_fingerprints: Dict[str, str] = {}

    for idx, fpath in enumerate(files, start=1):
        sid = f"paper_{idx}"
        suf = fpath.suffix.lower()
        if suf == ".pdf":
            pdf_out = out_p / "parsed" / sid
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
            raw_obj = json.loads(fpath.read_text(encoding="utf-8"))
            pm, paras = _extract_paragraphs_from_json_doc(raw_obj, sid)
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
                "location_type": "json_paragraph_id",
                "paragraphs": paras,
                "tables": [],
                "uncertainties": [],
                "anomalies": [],
            }
        else:
            rec = _parse_plain_or_md(fpath, sid)

        # Same-paper multi-format guard: identical normalized text hash or identical non-empty DOI
        norm_body = re.sub(r"\s+", " ", " ".join(p["text"].lower() for p in rec["paragraphs"])).strip()
        fp_body = _sha256_text(norm_body) if norm_body else rec["source_sha256"]
        fp_doi = f"doi:{rec['doi'].lower()}" if rec["doi"] != "not_reported" else None

        dup_of = seen_fingerprints.get(fp_body) or (seen_fingerprints.get(fp_doi) if fp_doi else None)
        if dup_of:
            dup_skipped.append(
                {
                    "skipped_file": rec["source_file"],
                    "duplicate_format_of": dup_of,
                    "reason": "same_paper_multi_format_deduplicated",
                }
            )
            continue

        seen_fingerprints[fp_body] = sid
        if fp_doi:
            seen_fingerprints[fp_doi] = sid
        papers.append(rec)

    corpus = {
        "schema_version": "1.0.0",
        "corpus_mode": "standard_files",
        "paper_count": len(papers),
        "papers": papers,
        "duplicate_formats_skipped": dup_skipped,
    }
    write_json_atomic(out_p / "corpus.json", corpus)
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
