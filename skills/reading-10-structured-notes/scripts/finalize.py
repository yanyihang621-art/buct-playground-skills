"""Verify agent-authored reading notes and case answers, render Markdown/JSON artifacts, and write result.json."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import (
    IndexStore,
    check_negation_and_hedge,
    check_numbers,
    format_anchor,
    scan_fields_evidence,
    verify,
)
from output_contract import (
    build_skeleton,
    contract_issues,
    contract_specs,
    is_closed_contract,
    pick_status_value,
)
from pdf_utils import write_json_atomic, write_text_lf


def _deep_merge_paper(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    if src.get("bibliography") and not dst.get("bibliography"):
        dst["bibliography"] = src["bibliography"]
    elif isinstance(src.get("bibliography"), dict) and isinstance(dst.get("bibliography"), dict):
        for k, v in src["bibliography"].items():
            if v and not dst["bibliography"].get(k):
                dst["bibliography"][k] = v

    if src.get("sections"):
        existing = {s.get("section_id"): s for s in dst.setdefault("sections", []) if s.get("section_id")}
        for sec in src["sections"]:
            sid = sec.get("section_id")
            if sid and sid in existing:
                old = existing[sid]
                if sec.get("summary") and not old.get("summary"):
                    old["summary"] = sec["summary"]
                if sec.get("links_to_next") and not old.get("links_to_next"):
                    old["links_to_next"] = sec["links_to_next"]
                old.setdefault("points", []).extend(sec.get("points") or [])
            else:
                dst["sections"].append(sec)

    if isinstance(src.get("card"), dict):
        dcard = dst.setdefault("card", {})
        for k, v in src["card"].items():
            if k not in dcard or not dcard[k]:
                dcard[k] = v
            elif isinstance(dcard[k], list) and isinstance(v, list):
                dcard[k].extend(v)

    if src.get("knowledge"):
        dst.setdefault("knowledge", []).extend(src["knowledge"])


def _load_notes(work_dir: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    files = sorted(work_dir.glob("notes*.json"))
    if not files:
        return None, []
    merged: Dict[str, Any] = {"language": "zh", "papers": [], "comparison": []}
    by_pid: Dict[str, Dict[str, Any]] = {}
    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8-sig"))
        if data.get("language"):
            merged["language"] = data["language"]
        for p in data.get("papers") or []:
            pid = p.get("paper_id") or f"paper_{len(by_pid) + 1}"
            p = dict(p)
            p["paper_id"] = pid
            if pid in by_pid:
                _deep_merge_paper(by_pid[pid], p)
            else:
                by_pid[pid] = p
                merged["papers"].append(p)
        if data.get("comparison"):
            merged["comparison"].extend(data["comparison"])
    return merged, [str(f) for f in files]


def _load_answer(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = work_dir / "answer.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _overlay(dst: Any, src: Any) -> Any:
    if isinstance(dst, dict) and isinstance(src, dict):
        out = dict(dst)
        for k, v in src.items():
            out[k] = _overlay(out[k], v) if k in out and isinstance(out[k], dict) and isinstance(v, dict) else copy.deepcopy(v)
        return out
    return copy.deepcopy(src)


def _lookup_pointer(root: Any, pointer: str) -> Any:
    if not pointer or pointer == "/":
        return root
    if not pointer.startswith("/"):
        return root.get(pointer) if isinstance(root, dict) else None
    cur = root
    for comp in pointer[1:].split("/"):
        key = comp.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list) and key.isdigit():
            idx = int(key)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _flatten_strings(val: Any) -> str:
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return " ".join(_flatten_strings(x) for x in val)
    if isinstance(val, dict):
        return " ".join(_flatten_strings(v) for k, v in val.items() if k not in ("anchor", "page", "pdf_page"))
    return "" if val is None else str(val)


def _verify_ev_list(
    ev_list: Any,
    claim_text: str,
    context_label: str,
    default_paper: str,
    store: IndexStore,
    multi_paper: bool,
    derived_vals: set[str],
    checks: List[Dict[str, Any]],
    change_records: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    must_fix: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(ev_list, list):
        return []
    resolved: List[Dict[str, Any]] = []
    ev_texts_for_nums: List[str] = list(derived_vals)
    any_quote = ""

    for idx, ev in enumerate(ev_list):
        if not isinstance(ev, dict):
            continue
        anc = str(ev.get("anchor") or ev.get("block_id") or ev.get("table_id") or ev.get("element_id") or "")
        quote = str(ev.get("quote") or ev.get("quote_snippet") or ev.get("snippet") or "")
        res = verify(quote, anc, default_paper, store)
        rec = {
            "context": f"{context_label}[{idx}]",
            "paper_id": res["paper_id"],
            "anchor": format_anchor(res["paper_id"], res["anchor"], multi_paper),
            "page": res.get("page"),
            "section_id": res.get("section_id"),
            "section_title": res.get("section_title"),
            "quote": quote,
            "match": res["match"],
        }
        checks.append(rec)
        if res["match"] == "relocated":
            old_anc = anc
            new_anc = rec["anchor"]
            if "anchor" in ev:
                ev["anchor"] = new_anc
            change_records.append({
                "type": "anchor_relocated",
                "context": rec["context"],
                "old": old_anc,
                "new": new_anc,
                "page": res.get("page"),
            })
        elif res["match"] == "unverified":
            must_fix.append({
                "code": "unverified_quote",
                "context": rec["context"],
                "anchor": anc,
                "quote": quote[:120],
            })
        elif res["match"] in {"too_short", "fuzzy"}:
            warnings.append({
                "code": f"quote_{res['match']}",
                "context": rec["context"],
                "anchor": anc,
                "quote": quote[:120],
            })

        if quote:
            ev_texts_for_nums.append(quote)
            any_quote += " " + quote
        if res.get("block_text"):
            ev_texts_for_nums.append(res["block_text"])
        if res.get("page") is not None and "page" in ev and ev["page"] != res["page"]:
            change_records.append({
                "type": "page_corrected",
                "context": rec["context"],
                "old": ev.get("page"),
                "new": res["page"],
            })
            ev["page"] = res["page"]
        resolved.append(rec)

    if claim_text and ev_texts_for_nums:
        missing_nums = check_numbers(claim_text, ev_texts_for_nums)
        if missing_nums:
            warnings.append({
                "code": "number_not_in_evidence",
                "context": context_label,
                "missing_numbers": missing_nums,
                "text": claim_text[:140],
            })
        for flag in check_negation_and_hedge(claim_text, any_quote):
            warnings.append({
                "code": flag,
                "context": context_label,
                "text": claim_text[:140],
            })
    return resolved


def _check_section_order(
    paper_note: Dict[str, Any],
    paper_meta: Dict[str, Any],
    warnings: List[Dict[str, Any]],
) -> None:
    outline = paper_meta.get("outline") or []
    if not outline:
        return
    outline_pos = {s["section_id"]: i for i, s in enumerate(outline)}
    noted_ids = [s.get("section_id") for s in paper_note.get("sections", []) if s.get("section_id")]
    positions = [outline_pos[sid] for sid in noted_ids if sid in outline_pos]
    if positions != sorted(positions):
        warnings.append({
            "code": "section_order_mismatch",
            "paper_id": paper_note.get("paper_id"),
            "noted_order": noted_ids,
        })
    noted_set = set(noted_ids)
    missed = []
    for sec in outline:
        sid = sec.get("section_id", "")
        title = (sec.get("title") or "").lower()
        if sid in noted_set or sid == "frontmatter":
            continue
        if re.search(r"references|bibliography|acknowledg|appendix|参考文献|致谢|附录", title):
            continue
        if (sec.get("chars") or 0) >= 250:
            missed.append(f"{sid} ({sec.get('title')})")
    if missed:
        warnings.append({
            "code": "missing_major_sections",
            "paper_id": paper_note.get("paper_id"),
            "sections": missed[:8],
        })


def _render_notes_md(
    case_id: str,
    notes: Dict[str, Any],
    papers_meta_by_id: Dict[str, Dict[str, Any]],
    checks: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    must_fix: List[Dict[str, Any]],
) -> str:
    lines: List[str] = [f"# Literature Notes — {case_id}", ""]
    multi = len(notes.get("papers", [])) > 1

    for paper in notes.get("papers", []):
        pid = paper.get("paper_id", "paper_1")
        meta = papers_meta_by_id.get(pid, {})
        bib = paper.get("bibliography") or {}
        title = bib.get("title") or meta.get("title") or pid
        authors = ", ".join(bib.get("authors") or meta.get("authors") or []) or "not_reported"
        year = bib.get("year") or meta.get("year") or "not_reported"
        ident = bib.get("identifier") or meta.get("doi") or meta.get("arxiv_id") or "not_reported"

        lines.extend([
            f"## {pid}: {title}",
            f"- **Authors**: {authors}",
            f"- **Year**: {year} | **Venue**: {bib.get('venue') or 'not_reported'} | **Identifier**: {ident}",
            f"- **Source**: `{meta.get('source_file', '')}` ({meta.get('page_count', '?')} pages)",
            "",
        ])

        card = paper.get("card") or {}
        if card:
            lines.extend(["### Paper Card", "", "| Slot | Content | Anchors |", "|---|---|---|"])
            for slot in ("research_question", "data_materials", "method", "conclusions"):
                obj = card.get(slot)
                if isinstance(obj, dict):
                    txt = (obj.get("text") or obj.get("summary") or "").replace("\n", " ")
                    ancs = ", ".join(
                        f"p.{e.get('page', '?')} {e.get('anchor', '')}"
                        for e in (obj.get("resolved_evidence") or obj.get("evidence") or [])
                    )
                    lines.append(f"| {slot} | {txt} | {ancs} |")
            for slot in ("conditions", "key_findings", "limitations", "not_reported"):
                items = card.get(slot) or []
                if isinstance(items, list) and items:
                    summary_parts = []
                    for it in items:
                        if isinstance(it, str):
                            summary_parts.append(it)
                        elif isinstance(it, dict):
                            summary_parts.append(it.get("text") or it.get("item") or json.dumps(it, ensure_ascii=False))
                    lines.append(f"| {slot} | {'; '.join(summary_parts).replace('|', '/')} | - |")
            lines.append("")

        sections = paper.get("sections") or []
        if sections:
            lines.append("### Section-Aligned Notes")
            lines.append("")
            for sec in sections:
                lines.append(f"#### §{sec.get('section_id', '')} {sec.get('title', '')} ({sec.get('role', 'section')})")
                if sec.get("summary"):
                    lines.append(sec["summary"])
                for pt in sec.get("points") or []:
                    ptype = pt.get("type", "claim")
                    anc_tags = []
                    quotes = []
                    for ev in pt.get("resolved_evidence") or pt.get("evidence") or []:
                        p_num = ev.get("page", "?")
                        s_id = ev.get("section_id") or sec.get("section_id") or ""
                        anc = ev.get("anchor", "")
                        anc_tags.append(f"[p.{p_num} §{s_id} {anc}]")
                        if ev.get("quote"):
                            quotes.append(f'"{ev["quote"]}"')
                    tag_str = " ".join(anc_tags)
                    q_str = f" — {' ; '.join(quotes)}" if quotes else ""
                    lines.append(f"- **[{ptype}]** {pt.get('text', '')} {tag_str}{q_str}")
                if sec.get("links_to_next"):
                    lines.append(f"- *Transition*: {sec['links_to_next']}")
                lines.append("")

        knowledge = paper.get("knowledge") or []
        if knowledge:
            lines.extend(["### Knowledge Entries", "", "| Term | Kind | Definition | Anchors |", "|---|---|---|---|"])
            for k in knowledge:
                ancs = ", ".join(e.get("anchor", "") for e in (k.get("evidence") or []))
                lines.append(
                    f"| {k.get('term', '')} | {k.get('kind', '')} | "
                    f"{(k.get('definition') or '').replace('|', '/')} | {ancs} |"
                )
            lines.append("")

    if notes.get("comparison") and not multi:
        lines.extend(["### Cross-Item Comparison", ""])
        for row in notes["comparison"]:
            lines.append(f"- **{row.get('dimension', '')}**: {json.dumps(row.get('values', {}), ensure_ascii=False)}")
        lines.append("")

    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}
    lines.extend([
        "### Verification Summary",
        f"- Total evidence checks: {len(checks)} ({counts})",
        f"- Must-fix issues: {len(must_fix)} | Warnings: {len(warnings)}",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _render_comparison_md(notes: Dict[str, Any]) -> str:
    papers = [p.get("paper_id", "paper") for p in notes.get("papers", [])]
    lines = ["# Cross-Paper Comparison", "", "| Dimension | " + " | ".join(papers) + " |", "|---|" + "|".join(["---"] * len(papers)) + "|"]
    for row in notes.get("comparison") or []:
        vals = row.get("values") or {}
        cells = [str(vals.get(pid, "not_reported")).replace("\n", " ").replace("|", "/") for pid in papers]
        lines.append(f"| {row.get('dimension', '')} | " + " | ".join(cells) + " |")
    return "\n".join(lines).rstrip() + "\n"


def run_finalize(case_path: Path, work_dir: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = work_dir / "meta.json"
    index_path = work_dir / "index.json"
    if not meta_path.is_file() or not index_path.is_file():
        print("MUST_FIX: meta.json or index.json missing. Run prepare.py first.")
        return 2

    case_data: Dict[str, Any] = json.loads(case_path.read_text(encoding="utf-8-sig")) if case_path.is_file() else {}
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    index_entries = json.loads(index_path.read_text(encoding="utf-8-sig"))
    store = IndexStore(index_entries)

    case_id = str(case_data.get("case_id") or meta.get("case_id") or "unknown_case")
    contract = case_data.get("output_contract")
    closed = is_closed_contract(contract)
    papers_meta = meta.get("papers") or []
    papers_meta_by_id = {p["paper_id"]: p for p in papers_meta}
    default_paper = papers_meta[0]["paper_id"] if papers_meta else "paper_1"
    multi_paper = len(papers_meta) > 1

    notes, note_files = _load_notes(work_dir)
    answer = _load_answer(work_dir)

    if notes is None and answer is None:
        skeleton = build_skeleton(contract, case_id, status_intent="partial")
        if not closed:
            skeleton["status_reason"] = "missing both work/notes*.json and work/answer.json; skeleton preserved"
        write_json_atomic(out_dir / "result.json", skeleton)
        print("MUST_FIX: neither notes*.json nor answer.json found in", work_dir)
        return 2

    notes = notes or {"language": "zh", "papers": [], "comparison": []}
    answer = answer or {}

    checks: List[Dict[str, Any]] = []
    change_records: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    must_fix: List[Dict[str, Any]] = []

    derived_vals: set[str] = set()
    for dv in answer.get("derived_values") or []:
        if isinstance(dv, dict):
            if dv.get("value") is not None:
                derived_vals.add(str(dv["value"]).strip())
            if isinstance(dv.get("inputs"), list):
                _verify_ev_list(
                    dv["inputs"],
                    str(dv.get("formula") or dv.get("value") or ""),
                    f"derived_values[{dv.get('field', '')}]",
                    default_paper,
                    store,
                    multi_paper,
                    set(),
                    checks,
                    change_records,
                    warnings,
                    must_fix,
                )

    for paper in notes.get("papers", []):
        pid = paper.get("paper_id") or default_paper
        if pid in papers_meta_by_id:
            _check_section_order(paper, papers_meta_by_id[pid], warnings)
        for sec in paper.get("sections") or []:
            sid = sec.get("section_id", "sec")
            for pidx, pt in enumerate(sec.get("points") or []):
                if isinstance(pt, dict):
                    pt["resolved_evidence"] = _verify_ev_list(
                        pt.get("evidence"),
                        str(pt.get("text") or ""),
                        f"{pid}.sections.{sid}.points[{pidx}]",
                        pid,
                        store,
                        multi_paper,
                        derived_vals,
                        checks,
                        change_records,
                        warnings,
                        must_fix,
                    )
        card = paper.get("card") or {}
        for slot, val in card.items():
            if isinstance(val, dict) and isinstance(val.get("evidence"), list):
                val["resolved_evidence"] = _verify_ev_list(
                    val["evidence"],
                    str(val.get("text") or val.get("summary") or ""),
                    f"{pid}.card.{slot}",
                    pid,
                    store,
                    multi_paper,
                    derived_vals,
                    checks,
                    change_records,
                    warnings,
                    must_fix,
                )
            elif isinstance(val, list):
                for lidx, item in enumerate(val):
                    if isinstance(item, dict) and isinstance(item.get("evidence"), list):
                        item["resolved_evidence"] = _verify_ev_list(
                            item["evidence"],
                            str(item.get("text") or item.get("summary") or ""),
                            f"{pid}.card.{slot}[{lidx}]",
                            pid,
                            store,
                            multi_paper,
                            derived_vals,
                            checks,
                            change_records,
                            warnings,
                            must_fix,
                        )
        for kidx, kitem in enumerate(paper.get("knowledge") or []):
            if isinstance(kitem, dict) and isinstance(kitem.get("evidence"), list):
                _verify_ev_list(
                    kitem["evidence"],
                    f"{kitem.get('term', '')}: {kitem.get('definition', '')}",
                    f"{pid}.knowledge[{kidx}]",
                    pid,
                    store,
                    multi_paper,
                    derived_vals,
                    checks,
                    change_records,
                    warnings,
                    must_fix,
                )

    fields_payload = copy.deepcopy(answer.get("fields") or {})
    scan_fields_evidence(fields_payload, default_paper, store, multi_paper, change_records, warnings)

    resolved_answer_ev: Dict[str, Any] = {}
    for ptr, evs in (answer.get("evidence") or {}).items():
        val = _lookup_pointer(fields_payload, ptr)
        txt = _flatten_strings(val)
        resolved_answer_ev[ptr] = _verify_ev_list(
            evs,
            txt,
            f"answer.evidence[{ptr}]",
            default_paper,
            store,
            multi_paper,
            derived_vals,
            checks,
            change_records,
            warnings,
            must_fix,
        )

    intent = str(answer.get("status") or ("partial" if must_fix else "success")).lower()
    if intent not in {"success", "partial", "unable"}:
        intent = "partial"
    if must_fix and intent == "success":
        intent = "partial"

    skeleton = build_skeleton(contract, case_id, status_intent=intent)
    result = _overlay(skeleton, fields_payload)
    if "case_id" in contract_specs(contract) or not closed:
        result["case_id"] = case_id

    specs = contract_specs(contract)
    if "status" in specs or not closed:
        st_spec = specs.get("status")
        enum_vals = st_spec.get("enum") if isinstance(st_spec, dict) else None
        if isinstance(enum_vals, list) and result.get("status") not in enum_vals:
            result["status"] = pick_status_value(contract, intent)
        elif not result.get("status"):
            result["status"] = pick_status_value(contract, intent)

    all_uncertainties = list(meta.get("uncertainties") or []) + list(answer.get("uncertainties") or [])
    if "uncertainties" in specs and not result.get("uncertainties") and all_uncertainties:
        u_items = (specs["uncertainties"] or {}).get("items", {}) if isinstance(specs["uncertainties"], dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            result["uncertainties"] = [
                u if isinstance(u, str) else f"{u.get('item') or u.get('code')}: {u.get('reason') or u.get('message')}"
                for u in all_uncertainties
            ]
        else:
            result["uncertainties"] = all_uncertainties

    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}
    sources_list = [
        {
            "paper_id": p["paper_id"],
            "title": p["title"],
            "source_file": p["source_file"],
            "sha256": p["sha256"],
            "page_count": p["page_count"],
        }
        for p in papers_meta
    ]

    notes_md_path = out_dir / "notes.md"
    notes_json_path = out_dir / "notes.json"
    ev_check_path = out_dir / "evidence_check.json"
    artifacts = {"notes_md": str(notes_md_path), "notes_json": str(notes_json_path), "evidence_check_json": str(ev_check_path)}

    write_text_lf(notes_md_path, _render_notes_md(case_id, notes, papers_meta_by_id, checks, warnings, must_fix))
    write_json_atomic(notes_json_path, notes)
    if multi_paper or notes.get("comparison"):
        comp_path = out_dir / "comparison.md"
        write_text_lf(comp_path, _render_comparison_md(notes))
        artifacts["comparison_md"] = str(comp_path)

    write_json_atomic(ev_check_path, {
        "case_id": case_id,
        "note_files": note_files,
        "counts": counts,
        "must_fix": must_fix,
        "warnings": warnings,
        "change_records": change_records,
        "checks": checks,
    })

    if not closed:
        result["status_reason"] = answer.get("status_reason") or (
            "verified with unresolved issues; see evidence_check.json" if must_fix else "notes and answers verified"
        )
        result["structured_notes"] = notes
        result["evidence"] = resolved_answer_ev
        result["uncertainties"] = all_uncertainties
        result["sources"] = sources_list
        result["artifacts"] = artifacts
        result["verification"] = {"counts": counts, "warning_count": len(warnings), "must_fix_count": len(must_fix)}
        result["change_records"] = change_records
    else:
        allowed = set(contract["properties"].keys())
        result = {k: v for k, v in result.items() if k in allowed}

    c_issues = contract_issues(contract, result)
    for ci in c_issues:
        must_fix.append({"code": ci["code"], "context": ci["field"], "message": ci["message"]})

    write_json_atomic(out_dir / "result.json", result)

    print(f"MUST_FIX ({len(must_fix)}):")
    for m in must_fix[:15]:
        print(f"  - [{m.get('code')}] {m.get('context')}: {m.get('message') or m.get('quote', '')}")
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings[:15]:
        ctx = w.get("context") or w.get("paper_id") or ""
        detail = w.get("missing_numbers") or w.get("sections") or w.get("text") or w.get("quote", "")
        print(f"  - [{w.get('code')}] {ctx}: {detail}")
    print(
        f"STATS: checks={len(checks)} exact={counts['exact']} relocated={counts['relocated']} "
        f"fuzzy={counts['fuzzy']} unverified={counts['unverified']} too_short={counts['too_short']} "
        f"contract_errors={len(c_issues)} result={out_dir / 'result.json'}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify notes/answers and write final result.json + Markdown.")
    parser.add_argument("--case", default="/input/case.json", help="Case JSON path (default /input/case.json)")
    parser.add_argument("--work", default="/app/work/r10", help="Work directory (default /app/work/r10)")
    parser.add_argument("--out", default="/app/output", help="Output directory (default /app/output)")
    args = parser.parse_args()
    return run_finalize(Path(args.case), Path(args.work), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
