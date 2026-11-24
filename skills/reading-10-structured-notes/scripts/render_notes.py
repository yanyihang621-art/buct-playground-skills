#!/usr/bin/env python3
"""Render human/Obsidian-ready structured_notes.md, cross-paper comparison_matrix, and search index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic, write_text_lf


def _fmt_anchors(anchors: List[Dict[str, Any]], limit: int = 3) -> str:
    if not anchors:
        return "—"
    parts = []
    for a in anchors[:limit]:
        eid = a.get("block_id") or a.get("table_id") or a.get("eq_id") or "loc"
        sec = a.get("section_id") or "-"
        parts.append(f"`[p.{a.get('pdf_page')} | {sec} | {eid} | bbox:{a.get('bbox')}]`")
    return "; ".join(parts)


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
        lines.append(f"{indent} {sec_label} (`{sn['section_id']}` · `{sn['discourse_role']}` · pp.{sn['pages'][0]}–{sn['pages'][1]})")
        if sn.get("transition_cues"):
            lines.append(f"- *Transition cues:* {', '.join(sn['transition_cues'])}")
        xr = sn.get("cross_references", {})
        xr_parts = [f"{k}: {v}" for k, v in xr.items() if v]
        if xr_parts:
            lines.append(f"- *Cross-references:* {'; '.join(xr_parts)}")
        if sn.get("cue_evidence"):
            for ce in sn["cue_evidence"]:
                tags_str = ", ".join(ce.get("cue_types", []))
                lines.append(
                    f"- **[{tags_str}]** {ce['text']}  \n"
                    f"  <sub>`[p.{ce['pdf_page']} | {sn['section_id']} | {ce['block_id']} | bbox:{ce['bbox']}]`</sub>"
                )
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
            return str(s)[:140].replace("|", "\\|").replace("\n", " ")

        md_lines.append(
            f"| `{row['paper_id']}` | {_cell(row['title'])} | `{row['status']}` | "
            f"{_cell(row['research_question'])} | {_cell(row['operating_conditions'])} | "
            f"{_cell(row['key_findings'])} | {_cell(row['limitations'])} | {_cell(', '.join(row['unreported']) or 'None')} |"
        )
    return rows, "\n".join(md_lines) + "\n"


def render_notes_bundle(
    docs_and_notes: List[Dict[str, Any]],
    out_dir: Path,
) -> Dict[str, Any]:
    """Assemble full structured_notes.json, structured_notes.md, comparison_matrix, and notes_index.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    combined_search: Dict[str, Any] = {}
    all_uncertainties: List[Dict[str, Any]] = []
    md_sections: List[str] = []

    mv_total = 0
    hr_total = 0
    for pn in docs_and_notes:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Render structured notes bundle from parsed docs.")
    parser.add_argument("--manifest", required=True, help="ingest_manifest.json path.")
    parser.add_argument("--out", required=True, help="Output directory for notes bundle.")
    args = parser.parse_args()

    from extract_knowledge_entries import extract_knowledge_entries
    from extract_note_skeleton import build_note_skeleton

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    papers_notes: List[Dict[str, Any]] = []
    for item in manifest.get("papers", []):
        doc = json.loads(Path(item["document_json"]).read_text(encoding="utf-8"))
        pid = item["paper_id"]
        skel = build_note_skeleton(doc, paper_id=pid)
        ke_res = extract_knowledge_entries(doc, skel)
        mv_cnt = sum(1 for e in ke_res["knowledge_entries"] if e["verification_tier"] == "machine_verified") + 4
        hr_cnt = sum(1 for e in ke_res["knowledge_entries"] if e["verification_tier"] == "needs_human_review") + 5 + len(ke_res["conflicts"])
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

    bundle = render_notes_bundle(papers_notes, Path(args.out))
    print(json.dumps({"status": bundle["status"], "paper_count": bundle["paper_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
