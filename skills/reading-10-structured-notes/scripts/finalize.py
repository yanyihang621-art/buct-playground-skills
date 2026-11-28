"""Verify agent-authored reading notes and case answers, project contracts safely, and write deliverables."""

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
    check_units,
    format_anchor,
    scan_fields_evidence,
    verify,
)
from output_contract import (
    build_skeleton,
    contract_issues,
    contract_specs,
    field_conforms,
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
        if isinstance(data, list):
            papers_list = data
        elif isinstance(data, dict):
            if data.get("language"):
                merged["language"] = data["language"]
            if "papers" in data and isinstance(data["papers"], list):
                papers_list = data["papers"]
            elif any(k in data for k in ("sections", "card", "knowledge", "bibliography")):
                papers_list = [data]
            else:
                papers_list = []
            if data.get("comparison"):
                merged["comparison"].extend(data["comparison"])
        else:
            papers_list = []

        for p in papers_list:
            if not isinstance(p, dict):
                continue
            pid = p.get("paper_id") or f"paper_{len(by_pid) + 1}"
            p = dict(p)
            p["paper_id"] = pid
            if pid in by_pid:
                _deep_merge_paper(by_pid[pid], p)
            else:
                by_pid[pid] = p
                merged["papers"].append(p)
    return merged, [str(f) for f in files]


def _load_answer(work_dir: Path, contract: Any = None) -> Optional[Dict[str, Any]]:
    path = work_dir / "answer.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("fields"), dict) and raw["fields"]:
        return raw

    # Support unwrapped answer.json where contract fields are at the top level.
    specs = contract_specs(contract)
    meta_only = {"fields", "status_reason", "derived_values", "_example_notice"}
    fields: Dict[str, Any] = {}
    pointer_ev: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in meta_only:
            continue
        if k == "evidence" and isinstance(v, dict) and any(str(pk).startswith("/") for pk in v.keys()):
            pointer_ev = v
            if "evidence" in specs and field_conforms(specs["evidence"], v, contract):
                fields["evidence"] = v
            continue
        if k in {"status", "uncertainties"} and k not in specs and not is_closed_contract(contract):
            fields[k] = v
            continue
        fields[k] = v
    return {
        "fields": fields,
        "status": raw.get("status"),
        "status_reason": raw.get("status_reason"),
        "evidence": pointer_ev if pointer_ev else (raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}),
        "uncertainties": raw.get("uncertainties") if isinstance(raw.get("uncertainties"), list) else [],
        "derived_values": raw.get("derived_values") if isinstance(raw.get("derived_values"), list) else [],
    }


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


def _is_empty_val(val: Any) -> bool:
    if val is None or val == "" or val == [] or val == {}:
        return True
    if isinstance(val, dict):
        return all(_is_empty_val(v) for v in val.values())
    return False


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
        if res.get("span_anchors"):
            rec["span_anchors"] = res["span_anchors"]
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
        unit_mismatches = check_units(claim_text, ev_texts_for_nums)
        if unit_mismatches:
            warnings.append({
                "code": "unit_mismatch",
                "context": context_label,
                "mismatches": unit_mismatches,
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


def _project_card_to_spec(paper: Dict[str, Any], pmeta: Dict[str, Any], spec: Any) -> Any:
    bib = paper.get("bibliography") or {}
    card = paper.get("card") or {}

    def _slot_text(val: Any) -> str:
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            return str(val.get("text") or val.get("summary") or val.get("item") or "")
        if isinstance(val, list):
            return "; ".join(_slot_text(x) for x in val if _slot_text(x))
        return ""

    raw_map = {
        "title": bib.get("title") or pmeta.get("title") or "",
        "authors": bib.get("authors") or pmeta.get("authors") or [],
        "year": bib.get("year") or pmeta.get("year") or "not_reported",
        "venue": bib.get("venue") or pmeta.get("venue") or "not_reported",
        "identifier": bib.get("identifier") or pmeta.get("doi") or pmeta.get("arxiv_id") or "not_reported",
        "research_question": card.get("research_question"),
        "data_materials": card.get("data_materials"),
        "method": card.get("method"),
        "conditions": card.get("conditions"),
        "key_findings": card.get("key_findings"),
        "conclusions": card.get("conclusions"),
        "limitations": card.get("limitations"),
        "not_reported": card.get("not_reported"),
    }
    if isinstance(spec, dict) and isinstance(spec.get("properties"), dict):
        out: Dict[str, Any] = {}
        for k, cspec in spec["properties"].items():
            if k not in raw_map or raw_map[k] is None:
                continue
            val = raw_map[k]
            ct = cspec.get("type") if isinstance(cspec, dict) else None
            if ct == "string" and not isinstance(val, str):
                out[k] = _slot_text(val)
            elif ct == "array" and isinstance(val, list):
                item_t = (cspec.get("items") or {}).get("type") if isinstance(cspec.get("items"), dict) else None
                if item_t == "string":
                    out[k] = [_slot_text(x) for x in val if _slot_text(x)]
                else:
                    out[k] = copy.deepcopy(val)
            else:
                out[k] = copy.deepcopy(val)
        return out
    return copy.deepcopy(card)


def _project_sections_to_spec(sections: List[Dict[str, Any]], spec: Any) -> Any:
    item_spec = spec.get("items") if isinstance(spec, dict) else None
    if not isinstance(item_spec, dict) or not isinstance(item_spec.get("properties"), dict):
        return copy.deepcopy(sections)
    props = item_spec["properties"]
    out: List[Dict[str, Any]] = []
    for sec in sections:
        evs: List[Dict[str, Any]] = []
        for pt in sec.get("points") or []:
            for ev in pt.get("resolved_evidence") or pt.get("evidence") or []:
                evs.append({
                    k: v for k, v in {
                        "anchor": ev.get("anchor", ""),
                        "page": ev.get("page"),
                        "quote": ev.get("quote", ""),
                    }.items() if v is not None
                })
        cand = {
            "section_id": sec.get("section_id", ""),
            "section": sec.get("title", ""),
            "title": sec.get("title", ""),
            "role": sec.get("role", "section"),
            "summary": sec.get("summary", ""),
            "points": sec.get("points", []),
            "links_to_next": sec.get("links_to_next", ""),
            "evidence": evs,
        }
        projected: Dict[str, Any] = {}
        for k, kspec in props.items():
            if k in cand and cand[k] is not None:
                projected[k] = cand[k]
        out.append(projected)
    return out


def _project_notes_to_contract(
    result: Dict[str, Any],
    contract: Any,
    notes: Dict[str, Any],
    papers_meta_by_id: Dict[str, Dict[str, Any]],
    sources_list: List[Dict[str, Any]],
    checks: List[Dict[str, Any]],
) -> None:
    specs = contract_specs(contract)
    if not specs or not notes.get("papers"):
        return
    primary = notes["papers"][0]
    pmeta = papers_meta_by_id.get(primary.get("paper_id", ""), {})
    card = primary.get("card") or {}

    for fname, fspec in specs.items():
        if not _is_empty_val(result.get(fname)):
            continue
        low = fname.lower()
        cand: Any = None
        if low in {"paper_card", "card"}:
            cand = _project_card_to_spec(primary, pmeta, fspec)
        elif low in {"section_notes", "section_summaries", "sections"}:
            cand = _project_sections_to_spec(primary.get("sections") or [], fspec)
        elif low in {"knowledge", "knowledge_entries", "knowledge_points", "glossary"}:
            cand = copy.deepcopy(primary.get("knowledge") or [])
        elif low in {"comparison", "comparison_matrix"}:
            cand = copy.deepcopy(notes.get("comparison") or [])
        elif low in {"bibliography", "metadata"}:
            cand = copy.deepcopy(primary.get("bibliography") or {})
        elif low in {"key_findings", "findings"}:
            cand = copy.deepcopy(card.get("key_findings") or [])
        elif low == "limitations":
            cand = copy.deepcopy(card.get("limitations") or [])
        elif low == "not_reported":
            cand = copy.deepcopy(card.get("not_reported") or [])
        elif low == "summary":
            parts = [s.get("summary", "") for s in (primary.get("sections") or []) if s.get("summary")]
            if not parts and isinstance(card.get("conclusions"), dict):
                parts = [card["conclusions"].get("text", "")]
            cand = " ".join(parts).strip()
        elif low == "sources":
            cand = copy.deepcopy(sources_list)
        elif low == "evidence" and isinstance(fspec, dict) and fspec.get("type") == "array":
            cand = [
                {k: v for k, v in {"anchor": c.get("anchor", ""), "page": c.get("page"), "quote": c.get("quote", "")}.items() if v is not None}
                for c in checks
            ]
        if cand is not None and not _is_empty_val(cand) and field_conforms(fspec, cand, contract):
            result[fname] = cand


def _format_anchor_tags(evs: Any) -> str:
    if not isinstance(evs, list) or not evs:
        return "-"
    tags = []
    for e in evs:
        if isinstance(e, dict):
            p = e.get("page")
            anc = e.get("anchor", "")
            tags.append(f"p.{p} {anc}".strip() if p is not None else anc)
    return ", ".join(t for t in tags if t) or "-"


def _render_notes_md(
    case_id: str,
    status: str,
    notes: Dict[str, Any],
    answer: Dict[str, Any],
    papers_meta_by_id: Dict[str, Dict[str, Any]],
    checks: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    must_fix: List[Dict[str, Any]],
    uncertainties: List[Any],
) -> str:
    multi = len(notes.get("papers", [])) > 1
    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}

    lines: List[str] = [
        "---",
        f'case_id: "{case_id}"',
        f'status: "{status}"',
        f"papers: {len(notes.get('papers', []))}",
        f"verified_quotes: {counts['exact'] + counts['relocated']}",
        'skill: "reading-10-structured-notes"',
        "---",
        "",
        f"# Literature Notes — {case_id}",
        "",
    ]

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
            f"- **Year**: {year} | **Venue**: {bib.get('venue') or meta.get('venue') or 'not_reported'} | **Identifier**: {ident}",
            f"- **Source**: `{meta.get('source_file', '')}` ({meta.get('page_count', '?')} pages)",
            "",
        ])

        card = paper.get("card") or {}
        if card:
            lines.extend(["### Paper Card", "", "| Slot | Content | Anchors |", "|---|---|---|"])
            for slot in ("research_question", "data_materials", "method", "conclusions"):
                obj = card.get(slot)
                if isinstance(obj, dict):
                    txt = (obj.get("text") or obj.get("summary") or "").replace("\n", " ").replace("|", "/")
                    ancs = _format_anchor_tags(obj.get("resolved_evidence") or obj.get("evidence"))
                    lines.append(f"| {slot} | {txt} | {ancs} |")
            for slot in ("conditions", "key_findings", "limitations", "not_reported"):
                items = card.get(slot) or []
                if isinstance(items, list) and items:
                    summary_parts: List[str] = []
                    slot_evs: List[Any] = []
                    searched_refs: List[str] = []
                    for it in items:
                        if isinstance(it, str):
                            summary_parts.append(it)
                        elif isinstance(it, dict):
                            base_txt = it.get("text") or it.get("item") or ""
                            if slot == "key_findings":
                                extras = [f"{k}={it[k]}" for k in ("metric", "value", "comparator", "condition") if it.get(k)]
                                if extras:
                                    base_txt = f"{base_txt} ({', '.join(extras)})"
                            elif slot == "limitations" and it.get("source"):
                                base_txt = f"[{it['source']}] {base_txt}"
                            elif not base_txt:
                                base_txt = json.dumps(it, ensure_ascii=False)
                            summary_parts.append(base_txt)
                            slot_evs.extend(it.get("resolved_evidence") or it.get("evidence") or [])
                            if it.get("searched"):
                                searched_refs.extend(str(s) for s in it["searched"])
                    anc_col = _format_anchor_tags(slot_evs)
                    if anc_col == "-" and searched_refs:
                        anc_col = "searched: " + ", ".join(dict.fromkeys(searched_refs))
                    lines.append(f"| {slot} | {'; '.join(summary_parts).replace('|', '/')} | {anc_col} |")
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
                ancs = _format_anchor_tags(k.get("evidence"))
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

    fields = answer.get("fields") or {}
    if fields:
        lines.extend(["## Case Answers & Grounded Evidence", ""])
        for fk, fv in fields.items():
            if fk in {"case_id", "status", "paper_card", "section_notes"}:
                continue
            lines.append(f"### `{fk}`")
            if isinstance(fv, str):
                lines.append(fv)
            else:
                lines.append("```json")
                lines.append(json.dumps(fv, ensure_ascii=False, indent=2)[:4000])
                lines.append("```")
            lines.append("")

    lines.extend([
        "## Verification & Review Boundaries",
        f"- **Machine-verified**: {len(checks)} evidence checks ({counts}); must-fix={len(must_fix)}.",
        f"- **Needs human review**: warnings={len(warnings)}; uncertainties={len(uncertainties)}.",
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


def _build_notes_index(notes: Dict[str, Any]) -> Dict[str, Any]:
    terms: Dict[str, List[Dict[str, Any]]] = {}
    sections_idx: List[Dict[str, Any]] = []
    for paper in notes.get("papers", []):
        pid = paper.get("paper_id", "paper_1")
        for sec in paper.get("sections") or []:
            anchors = [
                ev.get("anchor")
                for pt in (sec.get("points") or [])
                for ev in (pt.get("resolved_evidence") or pt.get("evidence") or [])
                if ev.get("anchor")
            ]
            sections_idx.append({
                "paper_id": pid,
                "section_id": sec.get("section_id"),
                "title": sec.get("title"),
                "role": sec.get("role"),
                "anchors": list(dict.fromkeys(anchors)),
            })
        for k in paper.get("knowledge") or []:
            term = str(k.get("term") or "").strip()
            if not term:
                continue
            entry = {
                "paper_id": pid,
                "kind": k.get("kind"),
                "definition": str(k.get("definition") or "")[:200],
                "anchors": [e.get("anchor") for e in (k.get("evidence") or []) if e.get("anchor")],
            }
            terms.setdefault(term, []).append(entry)
            for al in k.get("aliases") or []:
                if al:
                    terms.setdefault(str(al), []).append(entry)
    return {"terms": terms, "sections": sections_idx}


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
    answer = _load_answer(work_dir, contract=contract)

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
    scan_fields_evidence(fields_payload, default_paper, store, multi_paper, change_records, warnings, checks=checks)

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

    intent = str(answer.get("status") or ("partial" if must_fix else "success")).lower()
    if intent not in {"success", "partial", "unable"}:
        intent = "partial"
    if must_fix and intent == "success":
        intent = "partial"

    skeleton = build_skeleton(contract, case_id, status_intent=intent)
    result = _overlay(skeleton, fields_payload)
    _project_notes_to_contract(result, contract, notes, papers_meta_by_id, sources_list, checks)

    specs = contract_specs(contract)
    if "case_id" in specs or not closed:
        result["case_id"] = case_id

    all_uncertainties = list(meta.get("uncertainties") or []) + list(answer.get("uncertainties") or [])
    if "uncertainties" in specs and _is_empty_val(result.get("uncertainties")) and all_uncertainties:
        u_items = (specs["uncertainties"] or {}).get("items", {}) if isinstance(specs["uncertainties"], dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            result["uncertainties"] = [
                u if isinstance(u, str) else f"{u.get('item') or u.get('code')}: {u.get('reason') or u.get('message')}"
                for u in all_uncertainties
            ]
        else:
            result["uncertainties"] = all_uncertainties

    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}
    notes_md_path = out_dir / "notes.md"
    notes_json_path = out_dir / "notes.json"
    notes_idx_path = out_dir / "notes_index.json"
    ev_check_path = out_dir / "evidence_check.json"
    artifacts: Dict[str, str] = {
        "notes_md": str(notes_md_path),
        "notes_json": str(notes_json_path),
        "notes_index_json": str(notes_idx_path),
        "evidence_check_json": str(ev_check_path),
    }
    if multi_paper or notes.get("comparison"):
        comp_path = out_dir / "comparison.md"
        write_text_lf(comp_path, _render_comparison_md(notes))
        artifacts["comparison_md"] = str(comp_path)

    if not closed:
        enrichments = {
            "status_reason": answer.get("status_reason") or (
                "verified with unresolved issues; see evidence_check.json" if must_fix else "notes and answers verified"
            ),
            "structured_notes": notes,
            "evidence": resolved_answer_ev,
            "uncertainties": all_uncertainties,
            "sources": sources_list,
            "artifacts": artifacts,
            "verification": {"counts": counts, "warning_count": len(warnings), "must_fix_count": len(must_fix)},
            "change_records": change_records,
        }
        for ek, ev in enrichments.items():
            existing_empty = _is_empty_val(result.get(ek))
            conforms = (ek not in specs) or field_conforms(specs[ek], ev, contract)
            if existing_empty and conforms:
                result[ek] = ev
            elif ek != "status_reason" and not existing_empty:
                result[f"skill_{ek}"] = ev
            elif ek in specs and not conforms:
                result[f"skill_{ek}"] = ev
    else:
        allowed = set(contract["properties"].keys())
        result = {k: v for k, v in result.items() if k in allowed}

    # Check contract issues BEFORE finalizing status and writing artifacts.
    c_issues = contract_issues(contract, result)
    for ci in c_issues:
        must_fix.append({"code": ci["code"], "context": ci["field"], "message": ci["message"]})
    if c_issues and intent == "success":
        intent = "partial"

    if "status" in specs or not closed:
        st_spec = specs.get("status")
        enum_vals = st_spec.get("enum") if isinstance(st_spec, dict) else None
        if intent == "partial" or not result.get("status") or (isinstance(enum_vals, list) and result.get("status") not in enum_vals):
            result["status"] = pick_status_value(contract, intent)
    if not closed and not result.get("verification"):
        result["verification"] = {"counts": counts, "warning_count": len(warnings), "must_fix_count": len(must_fix)}

    final_status = str(result.get("status") or intent)
    write_text_lf(
        notes_md_path,
        _render_notes_md(case_id, final_status, notes, answer, papers_meta_by_id, checks, warnings, must_fix, all_uncertainties),
    )
    write_json_atomic(notes_json_path, notes)
    write_json_atomic(notes_idx_path, _build_notes_index(notes))
    write_json_atomic(ev_check_path, {
        "case_id": case_id,
        "note_files": note_files,
        "counts": counts,
        "must_fix": must_fix,
        "warnings": warnings,
        "change_records": change_records,
        "checks": checks,
    })
    write_json_atomic(out_dir / "result.json", result)

    print(f"MUST_FIX ({len(must_fix)}):")
    for m in must_fix[:15]:
        print(f"  - [{m.get('code')}] {m.get('context')}: {m.get('message') or m.get('quote', '')}")
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings[:15]:
        ctx = w.get("context") or w.get("paper_id") or ""
        detail = w.get("missing_numbers") or w.get("mismatches") or w.get("sections") or w.get("text") or w.get("quote", "")
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
