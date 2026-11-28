"""Ingest source documents, build inline reader/index/brief/template artifacts, and write a skeleton result.json."""

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

from evidence import has_hedge, has_negation, section_title_map, source_elements
from ingest_source import CASE_FILE_KEYS, ingest_input
from output_contract import build_skeleton, format_contract_tree, is_closed_contract
from pdf_utils import write_json_atomic, write_text_lf
from structured_sources import INLINE_KEYS

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
_ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_LIMIT_CUE_RE = re.compile(
    r"\b(?:limitation|limit(?:ed|s)?|drawback|future\s+work|not\s+reported|only\s+(?:on|produce|evaluate)|"
    r"fail(?:s|ed)?\s+to|outperform(?:s|ed)?\s+the\s+other|further\s+research|caveat|shortcoming)\b|"
    r"(?:局限|不足|未报告|仅在|未来工作)",
    re.I,
)
_SKIP_BRIEF_KEYS = {
    "case_id", "output_contract", "expected_output", "schema",
    *CASE_FILE_KEYS, *INLINE_KEYS,
}


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
    front_texts = [str(meta.get("title") or ""), str(meta.get("abstract") or "")]
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

    version = str(meta.get("version") or "").strip()
    if not version and arxiv_id:
        mv = re.search(r"(v\d+)$", arxiv_id, re.I)
        if mv:
            version = mv.group(1)

    year = str(meta.get("year") or "").strip()
    if not year:
        for candidate in front_texts[2:]:
            if re.search(
                r"arXiv|published|submitted|accepted|copyright|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
                candidate,
                re.I,
            ):
                ym = _YEAR_RE.search(candidate)
                if ym:
                    year = ym.group(1)
                    break
        if not year and arxiv_id and len(arxiv_id) >= 4:
            yy = int(arxiv_id[:2])
            year = f"{1900 + yy if yy >= 90 else 2000 + yy}"

    venue = str(meta.get("venue") or "").strip()
    if not venue:
        for blk in doc.get("blocks", [])[:12]:
            if blk.get("role") == "publication_notice":
                venue = blk.get("text", "").strip()
                break

    url = str(meta.get("url") or "").strip()
    return {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "year": year,
        "version": version,
        "venue": venue,
        "url": url,
    }


def _export_paper_artifacts(pid: str, doc: Dict[str, Any], work_dir: Path) -> Dict[str, Any]:
    titles, outline = section_title_map(doc)
    meta = doc.get("metadata", {}) or {}
    title = (meta.get("title") or "").strip() or pid
    src_file = doc.get("source_file") or ""
    page_count = doc.get("diagnostics", {}).get("page_count", 0)
    ids = _extract_identifiers(doc)

    block_by_id = {b["block_id"]: b for b in doc.get("blocks", []) if b.get("block_id")}
    fig_caption_texts = {(b.get("text") or "").strip() for b in doc.get("blocks", []) if b.get("role") == "figure_caption"}

    lines: List[str] = [f"# {pid}: {title} (source: {Path(src_file).name}, {page_count} pages)", ""]
    if meta.get("authors"):
        lines.append(f"Authors: {', '.join(meta['authors'])}")
    meta_parts = [f"{k}={v}" for k, v in (
        ("year", ids["year"]), ("doi", ids["doi"]), ("arxiv", ids["arxiv_id"]),
        ("version", ids["version"]), ("venue", ids["venue"]),
    ) if v]
    if meta_parts:
        lines.append("Metadata: " + " | ".join(meta_parts))
    lines.append("")

    outline_lookup = {item["section_id"]: item for item in outline}
    sec_chars: Dict[str, int] = {}
    sec_tables: Dict[str, List[str]] = {}
    sec_figures: Dict[str, List[str]] = {}
    index_entries: List[Dict[str, Any]] = []
    signals: Dict[str, List[str]] = {"limitations": [], "negations": [], "hedges": []}
    cur_sec: Optional[str] = "__init__"

    for kind, eid, text, sec_id, page, bbox, native in source_elements(doc):
        clean_text = (text or "").replace("\x00", "?").strip()
        if not clean_text:
            continue
        sec_key = sec_id or "frontmatter"
        sec_title = titles.get(sec_key, "Frontmatter" if sec_key == "frontmatter" else sec_key)

        if kind == "table":
            sec_tables.setdefault(sec_key, []).append(eid)
        elif kind == "figure":
            sec_figures.setdefault(sec_key, []).append(eid)

        blk_info = block_by_id.get(eid, {}) if kind == "block" else {}
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
            "continuation_of": blk_info.get("continuation_of"),
            "continued_by": blk_info.get("continued_by"),
        })

        # Collect anchor-only reading signals (no synthetic summaries)
        if kind == "block" and blk_info.get("role") not in {"reference", "heading", "title", "authors"}:
            snip = clean_text[:96].replace("\n", " ")
            if len(signals["limitations"]) < 10 and _LIMIT_CUE_RE.search(clean_text):
                signals["limitations"].append(f"[{eid} p.{page or '?'}] {snip}")
            if len(signals["negations"]) < 10 and has_negation(clean_text):
                signals["negations"].append(f"[{eid} p.{page or '?'}] {snip}")
            if len(signals["hedges"]) < 10 and has_hedge(clean_text):
                signals["hedges"].append(f"[{eid} p.{page or '?'}] {snip}")

        # Avoid duplicating figure captions already rendered as figure_caption blocks
        if kind == "figure" and clean_text in fig_caption_texts:
            continue

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

        sec_chars[sec_key] = sec_chars.get(sec_key, 0) + len(clean_text)

        if kind == "block" and clean_text.lower() == sec_title.lower():
            continue

        if kind == "block":
            lines.append(f"[{eid}] {clean_text}")
        else:
            p_tag = f" p{page}" if page is not None else ""
            lines.append(f"[{eid}{p_tag}] {clean_text}")
        lines.append("")

    reader_path = work_dir / "reader" / f"{pid}.md"
    write_text_lf(reader_path, "\n".join(lines).rstrip() + "\n")

    for item in outline:
        sid = item["section_id"]
        item["chars"] = sec_chars.get(sid, 0)
        if sec_tables.get(sid):
            item["tables"] = sec_tables[sid]
        if sec_figures.get(sid):
            item["figures"] = sec_figures[sid]
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

    tables_summary = [
        {
            "table_id": t.get("table_id"),
            "pages": t.get("pages"),
            "section_id": t.get("section_id"),
            "caption": t.get("caption") or t.get("label"),
            "col_paths": t.get("col_paths", []),
            "n_rows": t.get("n_rows", 0),
            "is_image_table": bool(t.get("is_image_table")),
            "crop_path": t.get("crop_path"),
        }
        for t in doc.get("tables", [])
    ]
    figures_summary = [
        {
            "figure_id": f.get("figure_id"),
            "page": f.get("page"),
            "section_id": f.get("section_id"),
            "caption": (f.get("caption") or "")[:180],
        }
        for f in doc.get("figures", [])
    ]

    paper_meta = {
        "paper_id": pid,
        "title": title,
        "authors": meta.get("authors") or [],
        "year": ids["year"] or "not_reported",
        "doi": ids["doi"] or "not_reported",
        "arxiv_id": ids["arxiv_id"] or "not_reported",
        "version": ids["version"] or "not_reported",
        "venue": ids["venue"] or "not_reported",
        "url": ids["url"] or "not_reported",
        "source_file": src_file,
        "sha256": _file_sha256(Path(src_file)) if src_file else "",
        "page_count": page_count,
        "status": doc.get("status", "partial"),
        "reader_path": str(reader_path),
        "outline": outline,
        "tables": tables_summary,
        "figures": figures_summary,
        "signals": signals,
        "warnings": warnings,
    }
    return {"meta": paper_meta, "index_entries": index_entries}


def _build_case_brief(
    case_id: str,
    case_data: Dict[str, Any],
    task_md_path: Optional[Path],
    contract: Any,
    papers_meta: List[Dict[str, Any]],
    global_uncertainties: List[Dict[str, Any]],
) -> str:
    lines: List[str] = [f"# Case Brief — {case_id}", ""]

    lines.append("## 1. Task Instructions & Case Fields (Untruncated)")
    for k, v in case_data.items():
        if k in _SKIP_BRIEF_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            lines.append(f"### `{k}`")
            lines.append(v.strip())
            lines.append("")
        elif isinstance(v, (list, dict)) and v:
            lines.append(f"### `{k}`")
            lines.append("```json")
            lines.append(json.dumps(v, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

    if task_md_path and task_md_path.is_file():
        txt = task_md_path.read_text(encoding="utf-8-sig", errors="replace").strip()
        if txt:
            lines.append("### `task.md`")
            lines.append(txt)
            lines.append("")

    lines.append("## 2. Output Contract")
    lines.append("```text")
    lines.append(format_contract_tree(contract, max_len=24000))
    lines.append("```")
    if contract:
        lines.append("")
        lines.append("<details><summary>Raw output_contract JSON</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(contract, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("</details>")
    lines.append("")

    lines.append(f"## 3. Papers & Structure ({len(papers_meta)})")
    for p in papers_meta:
        lines.append(f"### `{p['paper_id']}`: {p['title']}")
        lines.append(
            f"- Authors: {', '.join(p['authors']) or 'not_reported'} | Year: {p['year']} | "
            f"DOI: {p['doi']} | arXiv: {p['arxiv_id']} | Venue: {p['venue']}"
        )
        lines.append(f"- Source: `{p['source_file']}` | Pages: {p['page_count']} | Status: `{p['status']}` | Reader: `{p['reader_path']}`")
        if p["warnings"]:
            lines.append("- Warnings:")
            for w in p["warnings"]:
                lines.append(f"  - `[{w.get('code')}]` pages={w.get('pages')}: {w.get('message')}")
        if p["outline"]:
            lines.append("- Section Outline:")
            for sec in p["outline"]:
                ps = sec.get("page_start") or "?"
                pe = sec.get("page_end") or ps
                extras = []
                if sec.get("tables"):
                    extras.append("tables=" + ",".join(sec["tables"]))
                if sec.get("figures"):
                    extras.append("figures=" + ",".join(sec["figures"]))
                ext_s = f" [{'; '.join(extras)}]" if extras else ""
                lines.append(f"  - `{sec['section_id']}`: {sec['title']} (p.{ps}-{pe}, {sec.get('chars', 0)} chars){ext_s}")
        if p["tables"]:
            lines.append("- Tables:")
            for t in p["tables"]:
                cols = ", ".join(t["col_paths"][:8])
                lines.append(f"  - `{t['table_id']}` (p.{t['pages']}, §{t.get('section_id') or '?'}): {t['caption']} | cols=[{cols}]")
        if p["figures"]:
            lines.append("- Figures:")
            for f in p["figures"]:
                lines.append(f"  - `{f['figure_id']}` (p.{f['page']}, §{f.get('section_id') or '?'}): {f['caption']}")
        sigs = p.get("signals") or {}
        if any(sigs.values()):
            lines.append("- Reading Signals (verify in reader before citing):")
            for label in ("limitations", "negations", "hedges"):
                for item in sigs.get(label, [])[:5]:
                    lines.append(f"  - `{label}`: {item}")
        lines.append("")

    if global_uncertainties:
        lines.append("## 4. Ingest Uncertainties")
        for u in global_uncertainties:
            lines.append(f"- `[{u.get('code')}]` {u.get('message')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


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
        props = (contract or {}).get("properties", {}) if isinstance(contract, dict) else {}
        u_items = (props.get("uncertainties") or {}).get("items", {}) if isinstance(props.get("uncertainties"), dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            skeleton["uncertainties"] = [f"{u.get('code')}: {u.get('message')}" for u in global_uncertainties]
        else:
            skeleton["uncertainties"] = global_uncertainties
    write_json_atomic(out_dir / "result.json", skeleton)

    answer_template = {
        "fields": build_skeleton(contract, case_id, status_intent="success"),
        "status": "unable" if all_unreadable else "success",
        "status_reason": "",
        "evidence": {},
        "uncertainties": [],
        "derived_values": [],
    }
    write_json_atomic(work_dir / "answer_template.json", answer_template)

    task_md = (case_path.parent / "task.md") if case_path.is_file() else (input_dir / "task.md")
    if not task_md.is_file() and (input_dir / "task.md").is_file():
        task_md = input_dir / "task.md"
    brief_text = _build_case_brief(case_id, case_data, task_md, contract, papers_meta, global_uncertainties)
    brief_path = work_dir / "case_brief.md"
    write_text_lf(brief_path, brief_text)

    target_file = "claims.json" if "reading-14" in SCRIPT_DIR.parent.name else "notes.json"
    summary_lines = [
        f"CASE_ID: {case_id}",
        f"CASE_BRIEF: {brief_path} (READ THIS IN FULL, plus {case_path} and {task_md if task_md.is_file() else 'task.md'})",
        f"ANSWER_TEMPLATE: {work_dir / 'answer_template.json'}",
        "--- CONTRACT SUMMARY ---",
        format_contract_tree(contract, max_len=5000),
        f"--- PAPERS ({len(papers_meta)}) ---",
    ]
    for p in papers_meta:
        summary_lines.append(
            f"* {p['paper_id']}: {p['title']} | pages={p['page_count']} | status={p['status']} | "
            f"year={p['year']} | doi={p['doi']} | reader={p['reader_path']}"
        )
        if p["warnings"]:
            warn_brief = "; ".join(f"{w.get('code')}:{w.get('message')}" for w in p["warnings"][:4])
            summary_lines.append(f"  warnings: {warn_brief[:240]}")
        for sec in p["outline"][:28]:
            ps = sec.get("page_start") or "?"
            pe = sec.get("page_end") or ps
            summary_lines.append(f"  - {sec['section_id']}: {sec['title']} (p.{ps}-{pe}, {sec.get('chars', 0)} chars)")
        if len(p["outline"]) > 28:
            summary_lines.append(f"  ... ({len(p['outline']) - 28} more sections in {brief_path})")

    summary_lines.extend([
        "--- NEXT ---",
        f"Skeleton written to {out_dir / 'result.json'}.",
        f"1. Read {brief_path} for untruncated task instructions, full output_contract, tables, and reading signals.",
        f"2. Read reader/*.md or inspect sections/tables with find.py --work {work_dir}.",
        f"3. Write {work_dir / target_file} and {work_dir / 'answer.json'} (based on answer_template.json), then run finalize.py.",
    ])
    summary = "\n".join(summary_lines)
    if len(summary) > 14000:
        summary = summary[:13950] + f"\n...(truncated; read full details in {brief_path})"
    print(summary)
    return meta_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare inline reader, case_brief, index, metadata, and skeleton result.json.")
    parser.add_argument("--input", default="/input", help="Input directory or file (default /input)")
    parser.add_argument("--case", default="/input/case.json", help="Case JSON path (default /input/case.json)")
    parser.add_argument("--work", default=_default_work_dir(), help="Working directory")
    parser.add_argument("--out", default="/app/output", help="Output directory (default /app/output)")
    args = parser.parse_args()

    run_prepare(Path(args.input), Path(args.case), Path(args.work), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
