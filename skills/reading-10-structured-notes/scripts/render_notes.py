#!/usr/bin/env python3
"""Render human/Obsidian-ready structured_notes.md, cross-paper comparison_matrix, and search index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic, write_text_lf


def _fmt_anchors(anchors: List[Dict[str, Any]], limit: int = 3) -> str:
    if not anchors:
        return "—"
    parts = []
    for a in anchors[:limit]:
        eid = a.get("block_id") or a.get("table_id") or a.get("eq_id") or a.get("figure_id") or "loc"
        sec = a.get("section_id") or "-"
        if a.get("native_locator"):
            loc = a["native_locator"]
            address = loc.get("json_pointer") or loc.get("xml_xpath") or eid
            parts.append(f"`[{sec} | {address}]`")
        elif a.get("source_lines"):
            start, end = a["source_lines"]
            parts.append(f"`[lines {start}–{end} | {sec} | {eid}]`")
        else:
            parts.append(f"`[p.{a.get('pdf_page')} | {sec} | {eid} | bbox:{a.get('bbox')}]`")
    return "; ".join(parts).replace("|", "\\|")


def render_single_paper_markdown(paper_notes: Dict[str, Any]) -> str:
    """Render a complete Markdown note document for one paper."""
    pc = paper_notes.get("paper_card", {})
    bib = pc.get("bibliographic", {})
    title = bib.get("title") or paper_notes.get("paper_id", "Untitled Paper")
    tags = pc.get("reusability_tags", [])
    authors = bib.get("authors", [])

    lines: List[str] = [
        "---",
        f'paper_id: "{paper_notes.get("paper_id")}"',
        f'title: "{str(title).replace(chr(34), "")}"',
        f"authors: {json.dumps(authors, ensure_ascii=False)}",
        f'status: "{paper_notes.get("status")}"',
        f"tags: {json.dumps(tags, ensure_ascii=False)}",
        "---",
        "",
        f"# {title}",
        "",
        f"- **Source:** `{paper_notes.get('source_file')}` | **Pages:** {bib.get('page_count', 0)} | **Status:** `{paper_notes.get('status')}`",
    ]
    if authors:
        lines.append(f"- **Authors:** {', '.join(authors)}")
    if bib.get("affiliations"):
        lines.append(f"- **Affiliations:** {'; '.join(bib['affiliations'])}")
    lines.append("")

    # 1. Unified Paper Card
    lines.append("## 1. Unified Paper Card")
    lines.append("")
    lines.append("| Field | Extracted Summary | Tier | Provenance Anchors |")
    lines.append("|---|---|---|---|")
    for label, key in [
        ("Research Question", "research_question"),
        ("Data & Materials", "data_and_materials"),
        ("Methods & Setup", "methods_and_setup"),
        ("Conditions & Preconditions", "operating_conditions_and_preconditions"),
        ("Key Findings & Metrics", "key_findings_and_metrics"),
        ("Limitations & Validity", "limitations_and_threats_to_validity"),
    ]:
        slot = pc.get(key, {})
        val = str(slot.get("summary", "not_reported")).replace("|", "\\|").replace("\n", " ")
        tier = slot.get("verification_tier", "needs_human_review")
        anc = _fmt_anchors(slot.get("anchors", []))
        lines.append(f"| {label} | {val} | `{tier}` | {anc} |")

    unrep = pc.get("unreported_or_missing_items", [])
    lines.append(f"| Unreported / Missing | {', '.join(unrep) if unrep else 'None'} | `needs_human_review` | — |")
    lines.append("")

    # 2. Section-Aligned Notes
    lines.append("## 2. Section-Aligned Notes (Original Paper Structure)")
    lines.append("")
    for sn in paper_notes.get("section_notes", []):
        indent = "#" * min(5, 2 + int(sn.get("level", 1)))
        sec_label = f"{sn['number']} {sn['title']}".strip() if sn.get("number") else sn["title"]
        pages = f" · pp.{sn['pages'][0]}–{sn['pages'][1]}" if Path(paper_notes["source_file"]).suffix.lower() == ".pdf" else ""
        lines.append(f"{indent} {sec_label} (`{sn['section_id']}` · `{sn['discourse_role']}`{pages})")
        if sn.get("transition_cues"):
            lines.append(f"- *Transition cues:* {', '.join(sn['transition_cues'])}")
        xr = sn.get("cross_references", {})
        xr_parts = [f"{k}: {v}" for k, v in xr.items() if v]
        if xr_parts:
            lines.append(f"- *Cross-references:* {'; '.join(xr_parts)}")
        if sn.get("summary"):
            lines.append(f"{sn['summary']}  \n{_fmt_anchors(sn['summary_anchors'])}")
        if sn.get("key_points"):
            for point in sn["key_points"]:
                lines.append(f"- **{point['role']}** {point['text']} — {_fmt_anchors([point['anchor']])}")
        elif sn.get("lead_excerpt"):
            lines.append(f"- *Excerpt:* {sn['lead_excerpt']} ({_fmt_anchors(sn.get('anchors', []), limit=1)})")
        lines.append("")

    # 3. Knowledge Entries & Terminology
    lines.append("## 3. Knowledge Entries & Terminology Glossary")
    lines.append("")
    for ke in paper_notes.get("knowledge_entries", []):
        lines.append(
            f"- **`{ke['entry_id']}` ({ke['entry_type']} · `{ke['verification_tier']}`):** "
            f"{ke['statement']} — {_fmt_anchors(ke.get('anchors', []), limit=2)}"
        )
    lines.append("")
    if paper_notes.get("terminology_glossary"):
        lines.append("### Terminology & Acronyms")
        lines.append("| Acronym | Full Name | Occurrences | Definition Anchor |")
        lines.append("|---|---|---|---|")
        for g in paper_notes["terminology_glossary"]:
            anc = _fmt_anchors([g["definition_anchor"]], limit=1)
            lines.append(f"| `{g['acronym']}` | {g['full_name']} | {g['occurrence_count']} | {anc} |")
        lines.append("")

    # 4. Conflicts & Uncertainties
    if paper_notes.get("conflicts") or paper_notes.get("uncertainties"):
        lines.append("## 4. Intra-Paper Discrepancies & Uncertainties")
        lines.append("")
        for cf in paper_notes.get("conflicts", []):
            lines.append(f"- **[Conflict `{cf['conflict_id']}` | `{cf['metric']}`]:** {cf['note']} (values: {cf['values']})")
        for u in paper_notes.get("uncertainties", []):
            lines.append(f"- **[{u.get('severity', 'info')}] `{u.get('code')}`:** {u.get('message')}")
        lines.append("")

    # 5. Verification Ledger
    vl = paper_notes.get("verification_ledger", {})
    lines.append("## 5. Machine-Verified vs Human-Review Ledger")
    lines.append(f"- Machine-verified entries: `{vl.get('machine_verified_count', 0)}`")
    lines.append(f"- Needs human review entries: `{vl.get('needs_human_review_count', 0)}`")
    lines.append("")
    return "\n".join(lines)


def build_comparison_matrix(papers_notes: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    """Build cross-paper comparable Paper Card matrix (JSON rows + Markdown table)."""
    rows: List[Dict[str, Any]] = []
    md_lines: List[str] = [
        "# Cross-Paper Comparison Matrix",
        "",
        "| Paper ID | Title | Status | Research Question | Conditions & Preconditions | Key Findings | Limitations | Unreported |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for pn in papers_notes:
        pc = pn.get("paper_card", {})
        bib = pc.get("bibliographic", {})
        row = {
            "paper_id": pn.get("paper_id"),
            "title": bib.get("title") or pn.get("paper_id"),
            "status": pn.get("status"),
            "research_question": pc.get("research_question", {}).get("summary", "not_reported"),
            "data_and_materials": pc.get("data_and_materials", {}).get("summary", "not_reported"),
            "operating_conditions": pc.get("operating_conditions_and_preconditions", {}).get("summary", "not_reported"),
            "key_findings": pc.get("key_findings_and_metrics", {}).get("summary", "not_reported"),
            "limitations": pc.get("limitations_and_threats_to_validity", {}).get("summary", "not_reported"),
            "unreported": pc.get("unreported_or_missing_items", []),
        }
        rows.append(row)

        def _cell(s: Any) -> str:
            return str(s).replace("|", "\\|").replace("\n", " ")

        md_lines.append(
            f"| `{row['paper_id']}` | {_cell(row['title'])} | `{row['status']}` | "
            f"{_cell(row['research_question'])} | {_cell(row['operating_conditions'])} | "
            f"{_cell(row['key_findings'])} | {_cell(row['limitations'])} | {_cell(', '.join(row['unreported']) or 'None')} |"
        )
    return rows, "\n".join(md_lines) + "\n"


def annotate_text_locations(value: Any) -> None:
    """The Markdown/TXT parser stores 1-based source lines in the y coordinates."""
    if isinstance(value, list):
        for child in value:
            annotate_text_locations(child)
    elif isinstance(value, dict):
        if value.get("bbox") and "pdf_page" in value:
            value["source_lines"] = [int(value["bbox"][1]), int(value["bbox"][3])]
            value["pdf_page"] = None
            value["printed_page"] = None
        for child in value.values():
            annotate_text_locations(child)


def render_notes_bundle(
    docs_and_notes: List[Dict[str, Any]],
    out_dir: Path,
    input_issues=(),
) -> Dict[str, Any]:
    """Assemble full structured_notes.json, structured_notes.md, comparison_matrix, and notes_index.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    combined_search: Dict[str, Any] = {}
    all_uncertainties: List[Dict[str, Any]] = list(input_issues)
    md_sections: List[str] = []

    mv_total = 0
    hr_total = 0
    for pn in docs_and_notes:
        if Path(pn.get("source_file") or "").suffix.lower() in {".md", ".txt"}:
            annotate_text_locations(pn)
        vl = pn.get("verification_ledger", {})
        mv_total += int(vl.get("machine_verified_count", 0))
        hr_total += int(vl.get("needs_human_review_count", 0))
        all_uncertainties.extend(pn.get("uncertainties", []))
        md_sections.append(render_single_paper_markdown(pn))
        for term, hit in pn.get("search_index", {}).items():
            combined_search.setdefault(term, []).append({"paper_id": pn["paper_id"], **hit})

    comp_rows, comp_md = build_comparison_matrix(docs_and_notes)

    statuses = {pn.get("status", "partial") for pn in docs_and_notes}
    if not docs_and_notes:
        overall_status = "unverifiable"
    elif statuses == {"refused"}:
        overall_status = "refused"
    elif statuses == {"unverifiable"}:
        overall_status = "unverifiable"
    elif statuses == {"success"} and not all_uncertainties:
        overall_status = "success"
    else:
        overall_status = "partial"

    bundle = {
        "schema_version": "1.0.0",
        "status": overall_status,
        "paper_count": len(docs_and_notes),
        "papers": docs_and_notes,
        "comparison_matrix": comp_rows,
        "search_index": combined_search,
        "verification_ledger": {
            "machine_verified_count": mv_total,
            "needs_human_review_count": hr_total,
            "human_review_required_fields": [
                "research_question",
                "data_and_materials",
                "methods_and_setup",
                "operating_conditions_and_preconditions",
                "limitations_and_threats_to_validity",
                "conflicts",
            ],
        },
        "uncertainties": all_uncertainties,
    }

    write_json_atomic(out_dir / "structured_notes.json", bundle)
    write_text_lf(out_dir / "structured_notes.md", "\n---\n\n".join(md_sections))
    write_json_atomic(out_dir / "comparison_matrix.json", comp_rows)
    write_text_lf(out_dir / "comparison_matrix.md", comp_md)
    write_json_atomic(out_dir / "notes_index.json", combined_search)
    return bundle


def apply_section_reviews(skeleton: Dict[str, Any], doc: Dict[str, Any], reviews: Dict[str, Any]) -> None:
    """Attach the author's interpretations to exact quotations in each source section."""
    from audit_notes import _claim_problem

    sections = {s["section_id"]: s for s in skeleton["section_notes"]}
    for sid, reviewed in reviews.items():
        if sid not in sections:
            raise ValueError(f"Unknown section {skeleton['paper_id']}/{sid}")
        anchors = resolve_evidence(skeleton["paper_id"], doc, reviewed.get("evidence", []))
        for a in anchors:
            if a.get("block_id") not in sections[sid]["block_ids"] and a.get("section_id") != sid:
                raise ValueError(f"{sid}: evidence is outside this section")
        summary = reviewed.get("summary", "").strip()
        issue = _claim_problem(summary, [a["quote_snippet"] for a in anchors])
        if issue:
            raise ValueError(f"{sid}: {issue}")
        sections[sid].update(summary=summary, summary_anchors=anchors, verification_tier="needs_human_review")


def source_elements(doc):
    """Yield comparable body, figure, equation and table evidence with its original address."""
    for block in doc.get("blocks", []):
        yield ("block_id", block["block_id"], block.get("structured_text") or block.get("text") or "",
               block.get("section_id"), block.get("page"), block.get("bbox"), block.get("native_locator"))
    for table in doc.get("tables", []):
        content = "\n".join(filter(None, (table.get("caption"), table.get("markdown"))))
        yield ("table_id", table["table_id"], content, table.get("section_id"), next(iter(table.get("pages", [])), None),
               table.get("bbox"), None)
    for figure in doc.get("figures", []):
        yield ("figure_id", figure["figure_id"], figure.get("caption") or "", figure.get("section_id"),
               figure.get("page"), figure.get("caption_bbox"), None)
    for eq in doc.get("formulas", {}).get("display_equations", []):
        yield ("eq_id", eq["eq_id"], eq.get("structured_text") or "", eq.get("section_id"),
               eq.get("page"), eq.get("bbox"), None)


def resolve_evidence(pid, doc, citations):
    from audit_notes import _quote_matches
    elements = {e[1]: e for e in source_elements(doc)}
    anchors = []
    for citation in citations:
        eid = next((citation[k] for k in ("element_id", "block_id", "table_id", "figure_id", "eq_id") if citation.get(k)), None)
        quote = citation.get("quote_snippet", "")
        if citation.get("paper_id", pid) != pid or eid not in elements or not _quote_matches(quote, elements[eid][2]):
            raise ValueError(f"{pid}/{eid}: quote differs or element is missing from the named source")
        kind, _, _, section, page, bbox, native = elements[eid]
        anchor = {"paper_id": pid, "source_file": doc["source_file"], kind: eid, "section_id": section,
                  "pdf_page": page, "bbox": bbox, "quote_snippet": quote}
        if native:
            anchor["native_locator"] = native
        if Path(doc["source_file"]).suffix.lower() in {".md", ".txt"}:
            annotate_text_locations(anchor)
        anchors.append(anchor)
    return anchors


def apply_card_reviews(skeleton, doc, slots):
    from audit_notes import _claim_problem
    card = skeleton["paper_card"]
    for name, item in slots.items():
        if not isinstance(card.get(name), dict) or "anchors" not in card[name]:
            raise ValueError(f"Unknown Paper Card text field {name}")
        anchors = resolve_evidence(skeleton["paper_id"], doc, item.get("evidence", []))
        summary = item.get("summary", "").strip()
        status = item.get("status", "reviewed")
        if status == "not_reported" and not anchors:
            searched = item.get("searched_sections", [])
            ids = {s["section_id"] for s in skeleton["section_notes"]}
            if not summary or not searched or not set(searched).issubset(ids):
                raise ValueError(f"{name}: list the sections checked for an unreported field")
        else:
            issue = _claim_problem(summary, [a["quote_snippet"] for a in anchors])
            if issue:
                raise ValueError(f"{name}: {issue}")
        card[name] = {"summary": summary, "items": [a["quote_snippet"] for a in anchors], "anchors": anchors,
                      "status": status, "verification_tier": "needs_human_review",
                      "searched_sections": item.get("searched_sections", [])}
    card["unreported_or_missing_items"] = [name for name, slot in card.items()
                                           if isinstance(slot, dict) and slot.get("status") == "not_reported"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render structured notes bundle from parsed docs.")
    parser.add_argument("--manifest", required=True, help="ingest_manifest.json path.")
    parser.add_argument("--out", required=True, help="Output directory for notes bundle.")
    parser.add_argument("--review-json", help="Agent-authored section summaries and quoted evidence, keyed by paper and section ID.")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    reviews = json.loads(Path(args.review_json).read_text(encoding="utf-8")) if args.review_json else {}
    bundle = render_manifest(manifest, Path(args.out), reviews)
    print(json.dumps({"status": bundle["status"], "paper_count": bundle["paper_count"]}, ensure_ascii=False))
    return 0


def render_manifest(manifest, destination, reviews, cards=None):
    from extract_knowledge_entries import extract_knowledge_entries
    from extract_note_skeleton import build_note_skeleton
    cards = cards or {}
    if (set(reviews) | set(cards)) - {p["paper_id"] for p in manifest["papers"]}:
        raise ValueError("A section review names a paper outside the ingest manifest")
    papers_notes: List[Dict[str, Any]] = []
    for item in manifest.get("papers", []):
        doc = json.loads(Path(item["document_json"]).read_text(encoding="utf-8"))
        pid = item["paper_id"]
        skel = build_note_skeleton(doc, paper_id=pid)
        apply_section_reviews(skel, doc, reviews.get(pid, {}))
        if pid in cards:
            apply_card_reviews(skel, doc, cards[pid])
        ke_res = extract_knowledge_entries(doc, skel)
        assessed = [*ke_res["knowledge_entries"], *ke_res["conflicts"],
                    *(s for s in skel["paper_card"].values() if isinstance(s, dict))]
        mv_cnt = sum(e.get("verification_tier") == "machine_verified" for e in assessed)
        hr_cnt = sum(e.get("verification_tier") == "needs_human_review" for e in assessed)
        papers_notes.append(
            {
                **skel,
                "knowledge_entries": ke_res["knowledge_entries"],
                "terminology_glossary": ke_res["terminology_glossary"],
                "conflicts": ke_res["conflicts"],
                "search_index": ke_res["search_index"],
                "verification_ledger": {
                    "machine_verified_count": mv_cnt,
                    "needs_human_review_count": hr_cnt,
                },
            }
        )

        native = {b["block_id"]: b["native_locator"] for b in doc["blocks"] if b.get("native_locator")}
        if native:
            def label_locations(obj):
                if isinstance(obj, dict):
                    if obj.get("block_id") in native:
                        obj.setdefault("native_locator", native[obj["block_id"]])
                    for key, value in obj.items():
                        if key != "native_locator":
                            label_locations(value)
                elif isinstance(obj, list):
                    for value in obj:
                        label_locations(value)
            label_locations(papers_notes[-1])
    return render_notes_bundle(papers_notes, destination, manifest.get("uncertainties", []))


if __name__ == "__main__":
    raise SystemExit(main())
