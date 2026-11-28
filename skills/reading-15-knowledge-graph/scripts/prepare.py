#!/usr/bin/env python3
"""Ingest multi-paper corpus, render clean Markdown readers, write untruncated case_brief.md, and save early skeleton result.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ingest_corpus import ingest_corpus
from output_contract import build_skeleton, format_contract_tree, is_closed_contract, pick_status_value
from pdf_utils import write_json_atomic, write_text_lf

_SKIP_BRIEF_KEYS = {"case_id", "output_contract", "expected_output", "schema", "materials", "papers", "documents"}


def _export_paper_reader(paper: Dict[str, Any], work_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    sid = paper["source_id"]
    lines: List[str] = [
        f"# {sid}: {paper.get('title') or sid}",
        f"- Source: `{paper.get('source_file')}` | Location type: `{paper.get('location_type')}` | Status: `{paper.get('status')}`",
        f"- Authors: {', '.join(paper.get('authors') or ['not_reported'])}",
        f"- DOI: `{paper.get('doi') or 'not_reported'}` | Year: `{paper.get('year') or 'not_reported'}` | License: `{paper.get('license') or 'not_reported'}`",
        "",
    ]
    index_entries: List[Dict[str, Any]] = []
    cur_sec: Optional[str] = None
    sections_summary: List[Dict[str, Any]] = []
    sec_counts: Dict[str, int] = {}

    for para in paper.get("paragraphs", []):
        pid = para["paragraph_id"]
        sec = para.get("section_title") or "Body"
        page = para.get("pdf_page")
        ltype = para.get("location_type") or paper.get("location_type") or "unknown"
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", para.get("text", "")).strip()
        if not text:
            continue

        if sec != cur_sec:
            cur_sec = sec
            lines.append(f"## {sec}")
            lines.append("")
        sec_counts[sec] = sec_counts.get(sec, 0) + 1
        p_tag = f" p.{page}" if page is not None else ""
        lines.append(f"[{pid}{p_tag}] {text}")
        lines.append("")

        index_entries.append({
            "paper_id": sid,
            "anchor": pid,
            "kind": "paragraph",
            "location_type": ltype,
            "page": page,
            "section_id": para.get("section_id") or sec,
            "section_title": sec,
            "text": text,
            "bbox": para.get("bbox"),
            "native_locator": f"{sid}:{pid}",
        })

    for tab in paper.get("tables", []):
        tid = tab.get("table_id") or f"tab_{len(index_entries) + 1}"
        cap = tab.get("caption") or tab.get("label") or tid
        pages = tab.get("pages") or []
        t_page = pages[0] if pages else None
        md = tab.get("markdown") or ""
        notes = " ".join(tab.get("notes") or [])
        body_txt = "\n".join(filter(None, [cap, md, f"Notes: {notes}" if notes else ""])).strip()
        if not body_txt:
            continue
        lines.append(f"### Table {tid}: {cap}")
        lines.append("")
        lines.append(f"[{tid} p.{t_page if t_page is not None else '?'}]")
        if md:
            lines.append(md)
        if notes:
            lines.append(f"Notes: {notes}")
        lines.append("")
        index_entries.append({
            "paper_id": sid,
            "anchor": tid,
            "kind": "table",
            "location_type": "pdf_table" if paper.get("location_type") == "pdf_block" else "docx_table",
            "page": t_page,
            "section_id": tab.get("section_id") or "Tables",
            "section_title": cap,
            "text": body_txt,
            "table": tab,
            "native_locator": f"{sid}:{tid}",
        })

    if paper.get("xml_reference_notes"):
        lines.append("## XML Reference Notes (Tables / Figures / TeX Formulas from JATS)")
        lines.append("")
        for note in paper["xml_reference_notes"]:
            lines.append(f"- [{note['kind']} `{note['id']}`] {note['text']}")
        lines.append("")

    for sec, cnt in sec_counts.items():
        sections_summary.append({"section": sec, "paragraphs": cnt})

    reader_path = work_dir / "reader" / f"{sid}.md"
    write_text_lf(reader_path, "\n".join(lines).rstrip() + "\n")

    meta = {
        "source_id": sid,
        "title": paper.get("title") or sid,
        "authors": paper.get("authors") or ["not_reported"],
        "doi": paper.get("doi") or "not_reported",
        "year": paper.get("year") or "not_reported",
        "license": paper.get("license") or "not_reported",
        "source_file": paper.get("source_file"),
        "location_type": paper.get("location_type"),
        "status": paper.get("status"),
        "paragraph_count": len(paper.get("paragraphs", [])),
        "table_count": len(paper.get("tables", [])),
        "xml_notes_count": len(paper.get("xml_reference_notes", [])),
        "reader_path": str(reader_path),
        "sections": sections_summary,
        "uncertainties": paper.get("uncertainties", []),
    }
    return meta, index_entries


def _build_case_brief(
    case_id: str,
    case_data: Dict[str, Any],
    task_md_path: Optional[Path],
    contract: Any,
    corpus: Dict[str, Any],
    papers_meta: List[Dict[str, Any]],
    base_kg: Optional[Dict[str, Any]],
) -> str:
    lines: List[str] = [f"# Case Brief — {case_id}", ""]
    lines.append("## 1. Task Instructions & Case Fields (Untruncated)")
    for k, v in case_data.items():
        if k in _SKIP_BRIEF_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            lines.extend([f"### `{k}`", v.strip(), ""])
        elif isinstance(v, (list, dict)) and v:
            lines.extend([f"### `{k}`", "```json", json.dumps(v, ensure_ascii=False, indent=2), "```", ""])

    if task_md_path and task_md_path.is_file():
        txt = task_md_path.read_text(encoding="utf-8-sig", errors="replace").strip()
        if txt:
            lines.extend(["### `task.md`", txt, ""])

    lines.extend([
        "## 2. Output Contract",
        "```text",
        format_contract_tree(contract, max_len=24000),
        "```",
        "",
    ])

    lines.append(f"## 3. Papers & Provenance Manifest ({len(papers_meta)})")
    lines.append("| source_id | Title | DOI | Location Type | Paragraphs | Tables | Reader |")
    lines.append("|---|---|---|---|---:|---:|---|")
    for p in papers_meta:
        lines.append(
            f"| `{p['source_id']}` | {str(p['title']).replace('|', ' ')} | `{p['doi']}` | "
            f"`{p['location_type']}` | {p['paragraph_count']} | {p['table_count']} | `{p['reader_path']}` |"
        )
    lines.append("")

    if corpus.get("abbreviation_conflicts"):
        lines.append("## 4. Competing Abbreviation Definitions (DO NOT MERGE ACROSS PAPERS)")
        for conf in corpus["abbreviation_conflicts"]:
            lines.append(f"- `{conf['abbreviation']}`: {conf['definitions']} across `{conf['sources']}`")
        lines.append("")

    if corpus.get("shared_term_candidates"):
        lines.append("## 5. Shared Term Candidates Across Papers")
        lines.append(", ".join(f"`{t}`" for t in corpus["shared_term_candidates"][:30]))
        lines.append("")

    if corpus.get("boundary_records"):
        lines.append(f"## 6. Detected Boundary / Negation / Unreported Signals ({len(corpus['boundary_records'])})")
        for b in corpus["boundary_records"][:15]:
            lines.append(f"- [{b['source_id']}:{b['paragraph_id']} `{b['category']}`] {b['quote_snippet'][:140]}")
        lines.append("")

    if corpus.get("duplicate_formats_skipped") or corpus.get("missing_input_files"):
        lines.append("## 7. Corpus Warnings")
        for d in corpus.get("duplicate_formats_skipped", []):
            lines.append(f"- Duplicate format skipped: `{d['skipped_file']}` (same as `{d['duplicate_format_of']}`)")
        for m in corpus.get("missing_input_files", []):
            lines.append(f"- Missing input file: `{m}`")
        lines.append("")

    if base_kg:
        lines.append(f"## 8. Incremental Base KG Summary ({len(base_kg.get('nodes', []))} existing nodes, {len(base_kg.get('edges', []))} edges)")
        for n in base_kg.get("nodes", [])[:25]:
            lines.append(f"- `{n['node_id']}`: {n['label']} ({n['entity_type']}) from {n.get('source_ids')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_prepare(
    input_dir: Path,
    case_path: Path,
    work_dir: Path,
    out_dir: Path,
    ocr_mode: str = "auto",
    base_kg_path: Optional[Path] = None,
) -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_data.get("case_id") or "unknown_case")

    corpus = ingest_corpus(
        input_dir,
        work_dir / "ingested",
        case_path=case_path if case_path.is_file() else None,
        ocr_mode=ocr_mode,
    )

    all_index: List[Dict[str, Any]] = []
    papers_meta: List[Dict[str, Any]] = []
    for paper in corpus.get("papers", []):
        pmeta, entries = _export_paper_reader(paper, work_dir)
        papers_meta.append(pmeta)
        all_index.extend(entries)

    base_kg = None
    if base_kg_path and base_kg_path.is_file():
        base_kg = json.loads(base_kg_path.read_text(encoding="utf-8-sig"))

    all_unreadable = not papers_meta or not all_index
    meta_payload = {
        "case_id": case_id,
        "case_path": str(case_path),
        "input_dir": str(input_dir),
        "work_dir": str(work_dir),
        "out_dir": str(out_dir),
        "base_kg_path": str(base_kg_path) if base_kg_path else None,
        "corpus_json": str(work_dir / "ingested" / "corpus.json"),
        "papers": papers_meta,
        "missing_input_files": corpus.get("missing_input_files", []),
        "duplicate_formats_skipped": corpus.get("duplicate_formats_skipped", []),
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
            else "skeleton written by prepare; agent graph drafting pending"
        )
        skeleton.setdefault("uncertainties", [])
        for m in corpus.get("missing_input_files", []):
            skeleton["uncertainties"].append({"code": "missing_input_file", "message": str(m)})
    write_json_atomic(out_dir / "result.json", skeleton)

    answer_template = {
        "fields": build_skeleton(contract, case_id, status_intent="success"),
        "status": pick_status_value(contract, "unable" if all_unreadable else "success"),
        "status_reason": "",
        "uncertainties": [],
    }
    write_json_atomic(work_dir / "answer_template.json", answer_template)

    tmpl_src = SKILL_ROOT / "templates" / "kg_draft_template.json"
    if tmpl_src.is_file():
        write_json_atomic(work_dir / "kg_draft_template.json", json.loads(tmpl_src.read_text(encoding="utf-8")))

    task_md = (case_path.parent / "task.md") if case_path.is_file() else (input_dir / "task.md")
    if not task_md.is_file() and (input_dir / "task.md").is_file():
        task_md = input_dir / "task.md"
    brief_text = _build_case_brief(case_id, case_data, task_md, contract, corpus, papers_meta, base_kg)
    brief_path = work_dir / "case_brief.md"
    write_text_lf(brief_path, brief_text)

    summary_lines = [
        f"CASE_ID: {case_id}",
        f"CASE_BRIEF: {brief_path}",
        f"ANSWER_TEMPLATE: {work_dir / 'answer_template.json'}",
        f"KG_DRAFT_TEMPLATE: {work_dir / 'kg_draft_template.json'}",
        "--- CONTRACT SUMMARY ---",
        format_contract_tree(contract, max_len=4000),
        f"--- PAPERS ({len(papers_meta)}) ---",
    ]
    for p in papers_meta:
        summary_lines.append(
            f"* {p['source_id']}: {p['title']} | doi={p['doi']} | loc={p['location_type']} | "
            f"paras={p['paragraph_count']} | tabs={p['table_count']} | reader={p['reader_path']}"
        )
    if corpus.get("abbreviation_conflicts"):
        summary_lines.append(f"ABBREV_CONFLICTS: {[c['abbreviation'] for c in corpus['abbreviation_conflicts']]}")
    summary_lines.extend([
        "--- NEXT ---",
        f"1. Read {brief_path} and reader/*.md (or search with find.py --work {work_dir}).",
        f"2. Write {work_dir / 'kg_draft.json'} (and {work_dir / 'answer.json'} if contract has custom fields).",
        f"3. Run finalize.py --case {case_path} --work {work_dir} --out {out_dir}.",
    ])
    summary = "\n".join(summary_lines)
    if len(summary) > 12000:
        summary = summary[:11950] + f"\n...(truncated; read {brief_path})"
    print(summary)
    return meta_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Reading 15 workspace, readers, case brief, and skeleton result.json.")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--work", default="/app/work/r15")
    parser.add_argument("--out", default="/app/output")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    parser.add_argument("--base-kg", default=None)
    args = parser.parse_args()

    run_prepare(
        Path(args.input),
        Path(args.case),
        Path(args.work),
        Path(args.out),
        ocr_mode=args.ocr,
        base_kg_path=Path(args.base_kg) if args.base_kg else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
