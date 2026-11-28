#!/usr/bin/env python3
"""Prepare Writing 08 reading packet: column-aware Markdown readers, untruncated case_brief.md, answer_template.json, ledger_template.json, and early skeleton result.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ingest_materials import ingest_materials
from output_contract import build_skeleton, format_contract_tree, is_closed_contract, pick_status_value
from pdf_utils import write_json_atomic, write_text_lf
from publication_citations import case_citation_style

_SKIP_BRIEF_KEYS = {"case_id", "output_contract", "expected_output", "schema"}


def _detect_task_mode(case_data: Dict[str, Any], task_md_text: str) -> str:
    blob = " ".join([
        str(case_data.get("task_type") or ""),
        str(case_data.get("instructions") or ""),
        str(case_data.get("requirements") or ""),
        task_md_text[:1000],
    ]).casefold()
    if re.search(r"\b(?:revise|revision|polish|rewrite|improve|update)\b|修改|润色|改写|修订", blob) and not re.search(r"write from scratch|从头撰写", blob):
        if re.search(r"related work only|only.{0,20}related work|仅.{0,6}相关工作", blob):
            return "related_work_only"
        return "revision"
    if re.search(r"related work only|only.{0,20}related work|仅.{0,6}相关工作", blob):
        return "related_work_only"
    if re.search(r"introduction only|only.{0,20}introduction|仅.{0,6}引言", blob):
        return "intro_only"
    return "full_intro_rw"


def _detect_language(case_data: Dict[str, Any], materials: Dict[str, Any]) -> str:
    for k in ("output_language", "language", "lang"):
        val = case_data.get(k)
        if isinstance(val, str) and val.strip():
            return "zh" if val.strip().lower().startswith("zh") or "中文" in val else "en"
    instr = str(case_data.get("instructions") or case_data.get("requirements") or "")
    if re.search(r"中文|汉语|Chinese", instr, re.I):
        return "zh"
    if re.search(r"English|英文|英语", instr, re.I):
        return "en"
    zh_chars = sum(len(re.findall(r"[\u4e00-\u9fff]", p["text"])) for p in materials.get("passages", [])[:20])
    return "zh" if zh_chars > 80 else "en"


def _export_readers(materials: Dict[str, Any], work_dir: Path) -> List[Dict[str, Any]]:
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for p in materials.get("passages", []):
        by_doc.setdefault(p["doc_id"], []).append(p)

    index_entries: List[Dict[str, Any]] = []
    reader_dir = work_dir / "reader"
    reader_dir.mkdir(parents=True, exist_ok=True)

    for src in materials.get("sources", []):
        did = src["doc_id"]
        rows = by_doc.get(did, [])
        lines: List[str] = [
            f"# {did}: {src['path']} (role: `{src['role']}`, format: `{src['format']}`)",
            "",
        ]
        cur_sec: Optional[str] = None
        for r in rows:
            sec = r.get("section_title") or "Body"
            if sec != cur_sec:
                cur_sec = sec
                lines.extend([f"## {sec}", ""])
            anc = r.get("anchor") or r["source_id"]
            pg = f" p.{r['page']}" if r.get("page") is not None else ""
            clean_txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", r["text"])
            lines.extend([f"[{anc}{pg}] {clean_txt}", ""])

            index_entries.append({
                "paper_id": did,
                "anchor": anc,
                "source_id": r["source_id"],
                "doc_id": did,
                "path": r["path"],
                "kind": r.get("kind", "block"),
                "page": r.get("page"),
                "section_id": sec,
                "section_title": sec,
                "role": r.get("role"),
                "text": r["text"],
                "native_locator": r.get("locator"),
            })
        if rows:
            write_text_lf(reader_dir / f"{did}.md", "\n".join(lines).rstrip() + "\n")
    return index_entries


def _build_case_brief(
    case_id: str,
    case_data: Dict[str, Any],
    task_md_path: Optional[Path],
    contract: Any,
    materials: Dict[str, Any],
    task_mode: str,
    language: str,
    citation_style: str,
    work_dir: Path,
) -> str:
    lines: List[str] = [
        f"# Case Brief — {case_id}",
        f"- Detected Language: `{language}` | Task Mode: `{task_mode}` | Citation Style: `{citation_style}`",
        "",
        "## 1. Task Instructions & Case Fields (Untruncated)",
    ]
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

    papers = materials.get("papers", [])
    lines.append(f"## 3. Indexed Literature References ({len(papers)})")
    if papers:
        lines.append("| cite_key | Authors | Year | Title | Venue | DOI |")
        lines.append("|---|---|---:|---|---|---|")
        for p in papers:
            auths = ", ".join(p.get("authors") or [])[:50]
            lines.append(
                f"| `@{p['cite_key']}` | {auths.replace('|', ' ')} | {p.get('year') or ''} | "
                f"{str(p.get('title') or '').replace('|', ' ')[:70]} | {str(p.get('venue') or '').replace('|', ' ')[:30]} | `{p.get('doi') or ''}` |"
            )
    else:
        lines.append("*(No formal BibTeX/RIS entries extracted; inspect readers below and supply `paper_cards` in `evidence_ledger.json` if needed.)*")
    lines.append("")

    lines.append(f"## 4. Source Documents & Readers ({len(materials.get('sources', []))})")
    lines.append("| doc_id | File | Provisional Role | Passages | Reader |")
    lines.append("|---|---|---|---:|---|")
    for s in materials.get("sources", []):
        rpath = work_dir / "reader" / f"{s['doc_id']}.md"
        lines.append(f"| `{s['doc_id']}` | `{s['path']}` | `{s['role']}` | {s['passage_count']} | `{rpath if rpath.is_file() else '-'}` |")
    lines.append("")

    if materials.get("study_facts"):
        lines.extend(["## 5. Extracted Author Study Facts", "```json", json.dumps(materials["study_facts"], ensure_ascii=False, indent=2)[:6000], "```", ""])

    if materials.get("material_gaps") or materials.get("review_notes"):
        lines.append("## 6. Material Gaps & Notes")
        for g in materials.get("material_gaps", []):
            lines.append(f"- `[{g.get('code')}]` {g.get('path', '')}: {g.get('message')}")
        for n in materials.get("review_notes", []):
            lines.append(f"- `[note]` {n.get('path', '')}: {n.get('message')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def run_prepare(input_dir: Path, case_path: Path, work_dir: Path, out_dir: Path, ocr_mode: str = "auto") -> Dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8-sig"))
    case_id = str(case_data.get("case_id") or "unknown_case")

    materials = ingest_materials(
        input_dir,
        case_data if case_data else None,
        parse_work_dir=work_dir / "parsed",
        ocr_mode=ocr_mode,
    )
    index_entries = _export_readers(materials, work_dir)
    write_json_atomic(work_dir / "materials_index.json", materials)
    write_json_atomic(work_dir / "index.json", index_entries)

    task_md = (case_path.parent / "task.md") if case_path.is_file() else (input_dir / "task.md")
    if not task_md.is_file() and (input_dir / "task.md").is_file():
        task_md = input_dir / "task.md"
    task_md_text = task_md.read_text(encoding="utf-8-sig", errors="replace") if task_md.is_file() else ""

    task_mode = _detect_task_mode(case_data, task_md_text)
    language = _detect_language(case_data, materials)
    citation_style = case_citation_style(case_data)

    meta_payload = {
        "case_id": case_id,
        "case_path": str(case_path),
        "input_dir": str(input_dir),
        "work_dir": str(work_dir),
        "out_dir": str(out_dir),
        "task_mode": task_mode,
        "language": language,
        "citation_style": citation_style,
        "sources": materials.get("sources", []),
        "paper_count": len(materials.get("papers", [])),
    }
    write_json_atomic(work_dir / "meta.json", meta_payload)

    contract = case_data.get("output_contract")
    all_unreadable = not index_entries
    skeleton = build_skeleton(contract, case_id, status_intent="unable" if all_unreadable else "partial")
    if not is_closed_contract(contract):
        skeleton["status_reason"] = (
            "input unreadable or empty" if all_unreadable else "skeleton written by prepare; manuscript authoring pending"
        )
    write_json_atomic(out_dir / "result.json", skeleton)

    answer_template = {
        "fields": build_skeleton(contract, case_id, status_intent="success"),
        "status": pick_status_value(contract, "unable" if all_unreadable else "success"),
        "uncertainties": [],
    }
    write_json_atomic(work_dir / "answer_template.json", answer_template)

    tmpl_src = SKILL_ROOT / "templates" / "ledger_template.json"
    if tmpl_src.is_file():
        tmpl_obj = json.loads(tmpl_src.read_text(encoding="utf-8"))
        tmpl_obj["task_mode"] = task_mode
        write_json_atomic(work_dir / "ledger_template.json", tmpl_obj)

    brief = _build_case_brief(case_id, case_data, task_md, contract, materials, task_mode, language, citation_style, work_dir)
    brief_path = work_dir / "case_brief.md"
    write_text_lf(brief_path, brief)

    summary_lines = [
        f"CASE_ID: {case_id} | lang={language} | mode={task_mode} | cite_style={citation_style}",
        f"CASE_BRIEF: {brief_path}",
        f"ANSWER_TEMPLATE: {work_dir / 'answer_template.json'}",
        f"LEDGER_TEMPLATE: {work_dir / 'ledger_template.json'}",
        "--- CONTRACT SUMMARY ---",
        format_contract_tree(contract, max_len=4000),
        f"--- SOURCES ({len(materials.get('sources', []))}) | PAPERS ({len(materials.get('papers', []))}) ---",
    ]
    for s in materials.get("sources", [])[:20]:
        summary_lines.append(f"* {s['doc_id']} [{s['role']}]: {s['path']} ({s['passage_count']} passages) -> {work_dir / 'reader' / (s['doc_id'] + '.md')}")
    summary_lines.extend([
        "--- NEXT ---",
        f"1. Read {brief_path} and reader/*.md (or search with find.py --work {work_dir}).",
        f"2. Write {work_dir / 'draft.md'} and optional {work_dir / 'evidence_ledger.json'} (plus {work_dir / 'answer.json'} if needed).",
        f"3. Run finalize.py --case {case_path} --work {work_dir} --out {out_dir}.",
    ])
    summary = "\n".join(summary_lines)
    if len(summary) > 12000:
        summary = summary[:11950] + f"\n...(truncated; read {brief_path})"
    print(summary)
    return meta_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Writing 08 workspace, readers, case brief, and skeleton result.json.")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--work", default="/app/work/w08")
    parser.add_argument("--out", default="/app/output")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    args = parser.parse_args()

    run_prepare(Path(args.input), Path(args.case), Path(args.work), Path(args.out), ocr_mode=args.ocr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
