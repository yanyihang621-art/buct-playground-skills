"""Ingest source documents, build compact reader/index/meta artifacts, and write a skeleton result.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import section_title_map, source_elements
from ingest_source import ingest_input
from output_contract import build_skeleton, is_closed_contract, format_contract_tree
from pdf_utils import write_json_atomic, write_text_lf

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
_ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _default_work_dir() -> str:
    skill_name = SCRIPT_DIR.parent.name
    if "reading-14" in skill_name:
        return "/app/work/r14"
    return "/app/work/r10"


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_identifiers(doc: Dict[str, Any]) -> Dict[str, str]:
    meta = doc.get("metadata", {}) or {}
    front_texts = [
        str(meta.get("title") or ""),
        str(meta.get("abstract") or ""),
    ]
    for blk in doc.get("blocks", [])[:15]:
        if (blk.get("page") or 1) <= 2:
            front_texts.append(blk.get("structured_text") or blk.get("text") or "")
    for hf in doc.get("headers_footers", [])[:8]:
        front_texts.append(hf.get("text") or "")
    for m in doc.get("marginalia", [])[:8]:
        front_texts.append(m.get("text") or "")
    blob = "\n".join(front_texts)

    doi = str(meta.get("doi") or "").strip()
    if not doi:
        m = _DOI_RE.search(blob)
        if m:
            doi = m.group(1).rstrip(".,;)]")

    arxiv_id = str(meta.get("arxiv_id") or "").strip()
    if not arxiv_id:
        m = _ARXIV_RE.search(blob)
        if m:
            arxiv_id = m.group(1)

    year = str(meta.get("year") or "").strip()
    if not year:
        # Prefer dates near arXiv/copyright/frontmatter blocks over arbitrary body numbers.
        for candidate in front_texts[2:]:
            if re.search(r"arXiv|published|submitted|accepted|copyright|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", candidate, re.I):
                ym = _YEAR_RE.search(candidate)
                if ym:
                    year = ym.group(1)
                    break
        if not year and arxiv_id and len(arxiv_id) >= 4:
            yy = int(arxiv_id[:2])
            year = f"{1900 + yy if yy >= 90 else 2000 + yy}"

    return {"doi": doi, "arxiv_id": arxiv_id, "year": year}


def _export_paper_artifacts(pid: str, doc: Dict[str, Any], work_dir: Path) -> Dict[str, Any]:
    titles, outline = section_title_map(doc)
    meta = doc.get("metadata", {}) or {}
    title = (meta.get("title") or "").strip() or pid
    src_file = doc.get("source_file") or ""
    page_count = doc.get("diagnostics", {}).get("page_count", 0)
    ids = _extract_identifiers(doc)

    lines: List[str] = [f"# {pid}: {title} (source: {Path(src_file).name}, {page_count} pages)", ""]
    if meta.get("authors"):
        lines.append(f"Authors: {', '.join(meta['authors'])}")
    if ids["year"] or ids["doi"] or ids["arxiv_id"]:
        parts = [f"{k}={v}" for k, v in (("year", ids["year"]), ("doi", ids["doi"]), ("arxiv", ids["arxiv_id"])) if v]
        lines.append("Metadata: " + " | ".join(parts))
    lines.append("")

    outline_lookup = {item["section_id"]: item for item in outline}
    sec_chars: Dict[str, int] = {}
    index_entries: List[Dict[str, Any]] = []
    cur_sec: Optional[str] = "__init__"

    for kind, eid, text, sec_id, page, bbox, native in source_elements(doc):
        clean_text = (text or "").replace("\x00", "?").strip()
        if not clean_text:
            continue
        sec_key = sec_id or "frontmatter"
        sec_title = titles.get(sec_key, "Frontmatter" if sec_key == "frontmatter" else sec_key)

        if sec_key != cur_sec:
            cur_sec = sec_key
            info = outline_lookup.get(sec_key)
            if info and info.get("page_start"):
                ps, pe = info["page_start"], info.get("page_end") or info["page_start"]
                prange = f" [p{ps}–{pe}]" if pe != ps else f" [p{ps}]"
            elif page:
                prange = f" [p{page}]"
            else:
                prange = ""
            lines.append(f"## §{sec_key} {sec_title}{prange}")
            lines.append("")

        index_entries.append({
            "paper_id": pid,
            "anchor": eid,
            "kind": kind,
            "page": page,
            "section_id": sec_key,
            "section_title": sec_title,
            "text": clean_text,
            "bbox": bbox,
            "native_locator": native,
        })
        sec_chars[sec_key] = sec_chars.get(sec_key, 0) + len(clean_text)

        # Skip repeating the section heading line inside reader markdown.
        if kind == "block" and clean_text.lower() == sec_title.lower():
            continue

        if kind == "block":
            lines.append(f"[{eid}] {clean_text}")
        elif kind == "table":
            p_tag = f" p{page}" if page is not None else ""
            lines.append(f"[{eid}{p_tag}] {clean_text}")
        elif kind == "figure":
            p_tag = f" p{page}" if page is not None else ""
            lines.append(f"[{eid}{p_tag}] {clean_text}")
        elif kind == "eq":
            p_tag = f" p{page}" if page is not None else ""
            lines.append(f"[{eid}{p_tag}] {clean_text}")
        lines.append("")

    reader_path = work_dir / "reader" / f"{pid}.md"
    write_text_lf(reader_path, "\n".join(lines).rstrip() + "\n")

    for item in outline:
        item["chars"] = sec_chars.get(item["section_id"], 0)
    if "frontmatter" in sec_chars and "frontmatter" not in outline_lookup:
        outline.insert(0, {
            "section_id": "frontmatter",
            "level": 1,
            "number": "",
            "title": "Frontmatter / Abstract",
            "page_start": 1,
            "page_end": 1,
            "chars": sec_chars["frontmatter"],
        })

    warnings = []
    for u in doc.get("uncertainties", []):
        if isinstance(u, dict):
            warnings.append({"code": u.get("code"), "severity": u.get("severity"), "pages": u.get("pages", []), "message": u.get("message")})
    diag = doc.get("diagnostics", {}) or {}
    if diag.get("encrypted"):
        warnings.append({"code": "encrypted_pdf", "severity": "high", "pages": [], "message": "PDF is encrypted and unreadable without credentials."})
    if diag.get("corrupt"):
        warnings.append({"code": "corrupt_pdf", "severity": "high", "pages": [], "message": "PDF appears corrupt."})

    paper_meta = {
        "paper_id": pid,
        "title": title,
        "authors": meta.get("authors") or [],
        "year": ids["year"] or "not_reported",
        "doi": ids["doi"] or "not_reported",
        "arxiv_id": ids["arxiv_id"] or "not_reported",
        "source_file": src_file,
        "sha256": _file_sha256(Path(src_file)) if src_file else "",
        "page_count": page_count,
        "status": doc.get("status", "partial"),
        "reader_path": str(reader_path),
        "outline": outline,
        "warnings": warnings,
    }
    return {"meta": paper_meta, "index_entries": index_entries}


def _collect_task_text(case_data: Dict[str, Any], task_md_path: Optional[Path]) -> str:
    chunks: List[str] = []
    for key in ("instruction", "task", "prompt", "question", "questions", "requirements", "goal", "query"):
        val = case_data.get(key)
        if isinstance(val, str) and val.strip():
            chunks.append(f"{key}: {val.strip()}")
        elif isinstance(val, list) and val:
            chunks.append(f"{key}: " + json.dumps(val, ensure_ascii=False))
        elif isinstance(val, dict) and val:
            chunks.append(f"{key}: " + json.dumps(val, ensure_ascii=False))
    if task_md_path and task_md_path.is_file():
        txt = task_md_path.read_text(encoding="utf-8-sig", errors="replace").strip()
        if txt:
            chunks.append("task.md:\n" + txt[:1200])
    combined = "\n".join(chunks)
    if len(combined) > 3000:
        combined = combined[:2980] + "\n...(truncated)"
    return combined or "(no explicit instruction text found in case.json)"


def run_prepare(input_dir: Path, case_path: Path, work_dir: Path, out_dir: Path) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_data.get("case_id") or "unknown_case")

    manifest = ingest_input(
        input_dir,
        work_dir / "ingested",
        case_path=case_path if case_path.is_file() else None,
        ocr_mode="auto",
        save_crops=False,
    )

    all_index: List[Dict[str, Any]] = []
    papers_meta: List[Dict[str, Any]] = []
    for entry in manifest.get("papers", []):
        doc_path = Path(entry["document_json"])
        doc = json.loads(doc_path.read_text(encoding="utf-8-sig"))
        exported = _export_paper_artifacts(entry["paper_id"], doc, work_dir)
        papers_meta.append(exported["meta"])
        all_index.extend(exported["index_entries"])

    global_uncertainties = list(manifest.get("uncertainties", []))
    all_unreadable = (
        not papers_meta
        or all(p.get("status") in {"refused", "unverifiable", "failed"} for p in papers_meta)
        or not all_index
    )

    meta_payload = {
        "case_id": case_id,
        "case_path": str(case_path),
        "input_dir": str(input_dir),
        "work_dir": str(work_dir),
        "out_dir": str(out_dir),
        "papers": papers_meta,
        "uncertainties": global_uncertainties,
    }
    write_json_atomic(work_dir / "index.json", all_index)
    write_json_atomic(work_dir / "meta.json", meta_payload)

    contract = case_data.get("output_contract")
    status_intent = "unable" if all_unreadable else "partial"
    skeleton = build_skeleton(contract, case_id, status_intent=status_intent)
    if not is_closed_contract(contract):
        skeleton["status_reason"] = (
            "input unreadable or missing; see uncertainties"
            if all_unreadable
            else "skeleton written by prepare; agent review pending"
        )
        if global_uncertainties and isinstance(skeleton.get("uncertainties"), list):
            skeleton["uncertainties"] = global_uncertainties
    elif isinstance(skeleton.get("uncertainties"), list) and global_uncertainties:
        # Populate contract-compatible uncertainty dicts if closed schema allows objects, else strings.
        props = (contract or {}).get("properties", {}) if isinstance(contract, dict) else {}
        u_items = (props.get("uncertainties") or {}).get("items", {}) if isinstance(props.get("uncertainties"), dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            skeleton["uncertainties"] = [f"{u.get('code')}: {u.get('message')}" for u in global_uncertainties]
        else:
            skeleton["uncertainties"] = global_uncertainties
    write_json_atomic(out_dir / "result.json", skeleton)

    task_md = (case_path.parent / "task.md") if case_path.is_file() else (input_dir / "task.md")
    lines = [
        f"CASE_ID: {case_id}",
        "--- TASK ---",
        _collect_task_text(case_data, task_md),
        "--- OUTPUT CONTRACT ---",
        format_contract_tree(contract),
        f"--- PAPERS ({len(papers_meta)}) ---",
    ]
    for p in papers_meta:
        lines.append(
            f"* {p['paper_id']}: {p['title']} | pages={p['page_count']} | status={p['status']} | "
            f"year={p['year']} | doi={p['doi']} | reader={p['reader_path']}"
        )
        if p["warnings"]:
            warn_brief = "; ".join(f"{w.get('code')}:{w.get('message')}" for w in p["warnings"][:4])
            lines.append(f"  warnings: {warn_brief[:240]}")
        for sec in p["outline"][:24]:
            ps = sec.get("page_start") or "?"
            pe = sec.get("page_end") or ps
            lines.append(f"  - {sec['section_id']}: {sec['title']} (p.{ps}-{pe}, {sec.get('chars', 0)} chars)")
        if len(p["outline"]) > 24:
            lines.append(f"  ... ({len(p['outline']) - 24} more sections in reader)")

    if global_uncertainties:
        lines.append("--- INGEST UNCERTAINTIES ---")
        for u in global_uncertainties[:6]:
            lines.append(f"  - [{u.get('code')}] {u.get('message')}")

    target_file = "claims.json" if "reading-14" in SCRIPT_DIR.parent.name else "notes.json"
    lines.extend([
        "--- NEXT ---",
        f"Skeleton written to {out_dir / 'result.json'}.",
        f"Read sections via find.py --work {work_dir} --section <sec_id> or open reader markdown,",
        f"write {work_dir / target_file} and {work_dir / 'answer.json'}, then run finalize.py.",
    ])
    summary = "\n".join(lines)
    if len(summary) > 6000:
        summary = summary[:5975] + "\n...(summary truncated)"
    print(summary)
    return meta_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare compact reader, index, metadata, and skeleton result.json.")
    parser.add_argument("--input", default="/input", help="Input directory or file (default /input)")
    parser.add_argument("--case", default="/input/case.json", help="Case JSON path (default /input/case.json)")
    parser.add_argument("--work", default=_default_work_dir(), help="Working directory")
    parser.add_argument("--out", default="/app/output", help="Output directory (default /app/output)")
    args = parser.parse_args()

    run_prepare(Path(args.input), Path(args.case), Path(args.work), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
