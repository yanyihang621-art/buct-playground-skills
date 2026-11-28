#!/usr/bin/env python3
"""Verify draft.md + optional evidence_ledger.json + optional answer.json, render citations & comparison matrix, export artifacts, and write contract-compliant result.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import IndexStore
from export_manuscript import case_exports, export_manuscript
from output_contract import (
    allows_null,
    contract_issues,
    contract_specs,
    field_conforms,
    is_closed_contract,
    pick_status_value,
    required_contract_fields,
)
from pdf_utils import write_json_atomic, write_text_lf
from publication_citations import PublicationCitations, case_citation_style
from verify_intro_rw import verify_manuscript


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sections(draft: str) -> Dict[str, str]:
    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", draft, re.MULTILINE))
    sections: Dict[str, str] = {}
    for i, h in enumerate(headings):
        nxt = next((m.start() for m in headings[i + 1:] if len(m[1]) <= len(h[1])), len(draft))
        sections[h[2].strip()] = draft[h.end():nxt].strip()
    return sections


def _build_clusters_and_matrix(papers: List[Dict[str, Any]]) -> tuple[Dict[str, Any], str]:
    clusters_map: Dict[str, List[Dict[str, Any]]] = {}
    for p in papers:
        route = str(p.get("route") or "").strip() or "Literature Route"
        clusters_map.setdefault(route, []).append(p)

    clusters: List[Dict[str, Any]] = []
    for idx, (rname, members) in enumerate(clusters_map.items(), 1):
        sorted_m = sorted(members, key=lambda x: (str(x.get("year") or "9999"), x.get("cite_key", "")))
        clusters.append({
            "cluster_id": f"route_{idx:02d}",
            "route_name": rname,
            "paper_count": len(sorted_m),
            "cite_keys": [m["cite_key"] for m in sorted_m],
            "shared_limitations": [m["limitation"] for m in sorted_m if m.get("limitation")],
            "papers": sorted_m,
        })

    def cell(v: Any) -> str:
        return " ".join(str(v or "—").split()).replace("|", "\\|")

    lines = [
        "# 相关工作技术路线对比矩阵 (Related Work Comparison Matrix)",
        "",
        "| Route / Comparison Axis | Study | Problem Setting | Evaluation | Key Finding | Limitation | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for cl in clusters:
        for p in cl["papers"]:
            locs = [f"{e.get('doc_id', e.get('source_id', ''))}:{e.get('anchor', '')}" for e in p.get("evidence", []) if isinstance(e, dict)]
            cells = [
                cl["route_name"], f"@{p['cite_key']}", p.get("problem_setting"),
                p.get("evaluation_system"), p.get("key_finding"), p.get("limitation"),
                ", ".join(dict.fromkeys(locs)) or ", ".join(p.get("source_ids", [])[:3]),
            ]
            lines.append("| " + " | ".join(cell(v) for v in cells) + " |")
    lines.append("")
    return {"schema_version": "writing-08-clusters/v2", "route_cluster_count": len(clusters), "clusters": clusters}, "\n".join(lines)


def _merge_paper_cards(materials: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(materials)
    papers_by_key = {p["cite_key"]: dict(p) for p in materials.get("papers", []) if isinstance(p, dict) and p.get("cite_key")}
    for card in ledger.get("paper_cards") or []:
        if not isinstance(card, dict) or not card.get("cite_key"):
            continue
        ck = str(card["cite_key"]).strip()
        existing = papers_by_key.get(ck, {"cite_key": ck, "authors": [], "source_ids": []})
        for k, v in card.items():
            if v not in (None, "", []):
                existing[k] = v
        papers_by_key[ck] = existing
    merged["papers"] = list(papers_by_key.values())
    return merged


def run_finalize(
    case_path: Path,
    work_dir: Path,
    out_dir: Path,
    *,
    formats: Optional[List[str]] = None,
    language_override: Optional[str] = None,
) -> int:
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_data = _load_json(case_path) if case_path.is_file() else {}
    case_id = str(case_data.get("case_id") or "unknown_case")
    meta = _load_json(work_dir / "meta.json")
    materials = _load_json(work_dir / "materials_index.json")
    index_entries = _load_json(work_dir / "index.json")
    index_store = IndexStore(index_entries)

    draft_path = work_dir / "draft.md"
    draft_raw = draft_path.read_text(encoding="utf-8-sig") if draft_path.is_file() else ""
    ledger_path = work_dir / "evidence_ledger.json"
    ledger = _load_json(ledger_path) if ledger_path.is_file() else {}
    if not isinstance(ledger, dict):
        ledger = {}

    ans_path = work_dir / "answer.json"
    answer_data = _load_json(ans_path) if ans_path.is_file() else None

    task_mode = str(ledger.get("task_mode") or meta.get("task_mode") or "full_intro_rw")
    lang = str(language_override or meta.get("language") or "en")
    cite_style = case_citation_style(case_data)

    materials = _merge_paper_cards(materials, ledger)
    papers = materials.get("papers", [])

    # Render [@key] / \cite{key} citations if used in draft
    formatter = PublicationCitations(draft_raw, papers, cite_style)
    draft_rendered, cite_issues = formatter.render()

    clusters, matrix_md = _build_clusters_and_matrix(papers)
    gaps = [g for g in (ledger.get("research_gaps") or []) if isinstance(g, dict)]
    contribs = [c for c in (ledger.get("contributions") or []) if isinstance(c, dict)]
    claims = [c for c in (ledger.get("claim_links") or []) if isinstance(c, dict)]
    changes = [c for c in (ledger.get("change_records") or []) if isinstance(c, dict)]
    uncertainties = [*(materials.get("material_gaps") or []), *(ledger.get("uncertainties") or [])]

    qa = verify_manuscript(
        draft_rendered,
        materials,
        ledger,
        index_store,
        task_mode=task_mode,
        language=lang,
    )
    must_fix = list(qa["must_fix"])
    for ci in cite_issues:
        if ci.get("code") == "unresolved_publication_citation":
            must_fix.append({"check": "citations", **ci})
        else:
            qa["warnings"].append({"check": "citations", **ci})

    # Export manuscript formats + comparison matrix + JSON sidecars
    contract = case_data.get("output_contract")
    case_formats, names = case_exports(case_data)
    if isinstance(ledger.get("artifact_names"), dict):
        names.update(ledger["artifact_names"])
    artifacts: Dict[str, str] = {}
    if draft_rendered.strip():
        artifacts, export_issues = export_manuscript(
            draft_rendered, out_dir, [*case_formats, *(formats or [])], names, lang
        )
        for ei in export_issues:
            must_fix.append({"check": "export", **ei})

    write_text_lf(out_dir / "comparison_matrix.md", matrix_md)
    artifacts["comparison_matrix_md"] = "comparison_matrix.md"

    source_manifest = materials.get("sources", [])
    sidecars = {
        "related_work_clusters": clusters,
        "gap_contribution_map": {"research_gaps": gaps, "contributions": contribs},
        "claim_evidence": claims,
        "sources": source_manifest,
        "qa_report": qa,
    }
    for sname, sdata in sidecars.items():
        write_json_atomic(out_dir / f"{sname}.json", sdata)
        artifacts[f"{sname}_json"] = f"{sname}.json"

    # Build contract-compliant result.json (NO fixed blocklist!)
    sections_map = _sections(draft_rendered)
    products: Dict[str, Any] = {
        "case_id": case_id,
        "manuscript": draft_rendered,
        "draft": draft_rendered,
        "draft_markdown": draft_rendered,
        "main_result": draft_rendered,
        "markdown": draft_rendered,
        "introduction_and_related_work": draft_rendered,
        "sections": sections_map,
        "comparison_matrix": matrix_md,
        "references": papers,
        "bibliography": papers,
        "research_gaps": gaps,
        "contributions": contribs,
        "claim_evidence": claims,
        "evidence": claims,
        "evidence_table": claims,
        "fact_source_mapping": claims,
        "sources": source_manifest,
        "change_records": changes,
        "revision_log": changes,
        "uncertainties": uncertainties,
        "limitations": uncertainties,
        "artifacts": artifacts,
        "metrics": qa["metrics"],
        "verification": qa["checks"],
    }
    aliases = {
        "research_background": ("research background", "background", "研究背景"),
        "introduction": ("introduction", "引言", "研究背景", "research background"),
        "related_work": ("related work", "related works", "相关工作", "相关研究"),
    }
    folded_secs = {t.casefold(): b for t, b in sections_map.items()}
    for field, lbls in aliases.items():
        for lbl in lbls:
            if lbl in folded_secs:
                products[field] = folded_secs[lbl]
                break
    for ext in ("md", "docx", "tex", "pdf", "png"):
        p = artifacts.get(f"intro_and_related_work_{ext}")
        if p:
            products[f"manuscript_{ext}"] = p
            products[f"{ext}_path"] = p
            products[f"output_{ext}"] = p
            if ext in ("pdf", "png"):
                products[f"preview_{ext}"] = p
                products[f"{ext}_preview"] = p

    ans_fields: Dict[str, Any] = {}
    ans_status: Optional[str] = None
    ans_uncertainties: List[Any] = []
    if isinstance(answer_data, dict):
        if isinstance(answer_data.get("fields"), dict):
            ans_fields = dict(answer_data["fields"])
        else:
            ans_fields = {k: v for k, v in answer_data.items() if k not in ("status", "uncertainties")}
        if isinstance(answer_data.get("status"), str):
            ans_status = answer_data["status"]
        if isinstance(answer_data.get("uncertainties"), list):
            ans_uncertainties = list(answer_data["uncertainties"])

    has_input = bool(index_entries)
    intent = "unable" if not has_input else ("partial" if (must_fix or not draft_rendered.strip()) else "success")
    if ans_status in ("unable", "unverifiable", "refused", "partial"):
        intent = "unable" if ans_status in ("unable", "unverifiable", "refused") else "partial"
    status_val = pick_status_value(contract, intent)

    closed = is_closed_contract(contract)
    specs = contract_specs(contract)
    required = required_contract_fields(contract)

    editorial_report = {
        "schema_version": "writing-08-intro-rw-result/v3",
        "challenge_id": "product-arena-04-4b82ad21",
        "case_id": case_id,
        "status": status_val,
        "language": lang,
        "task_mode": task_mode,
        "manuscript": draft_rendered,
        "metrics": qa["metrics"],
        "artifacts": artifacts,
        "sources": source_manifest,
        "claim_evidence": claims,
        "research_gaps": gaps,
        "contributions": contribs,
        "change_records": changes,
        "uncertainties": uncertainties + ans_uncertainties,
        "verification": {
            "passed": len(must_fix) == 0,
            "checks": qa["checks"],
            "must_fix": must_fix,
            "warnings_count": len(qa["warnings"]),
        },
    }
    write_json_atomic(out_dir / "editorial_report.json", editorial_report)

    result: Dict[str, Any] = {} if closed else dict(editorial_report)
    for key, spec in specs.items():
        if key in ans_fields:
            result[key] = ans_fields[key]
            continue
        if key == "case_id":
            result[key] = case_id
            continue
        if key == "status":
            result[key] = status_val
            continue
        if key in products and products[key] is not None:
            if field_conforms(spec, products[key], contract):
                result[key] = products[key]
        if key not in result and key in required:
            result[key] = None if allows_null(spec, contract) else ([] if isinstance(spec, dict) and spec.get("type") == "array" else "")

    if not closed:
        for k, v in ans_fields.items():
            if k not in result:
                result[k] = v
    else:
        result = {k: v for k, v in result.items() if k in set(specs.keys())}

    c_issues = contract_issues(contract, result)
    for ci in c_issues:
        must_fix.append({"check": "contract", **ci})
    if must_fix and "status" in result:
        result["status"] = pick_status_value(contract, "partial")

    write_json_atomic(out_dir / "result.json", result)
    report_out = {
        "pass": len(must_fix) == 0,
        "status": result.get("status"),
        "must_fix": must_fix,
        "warnings_count": len(qa["warnings"]),
        "metrics": qa["metrics"],
    }
    write_json_atomic(work_dir / "validation_report.json", report_out)
    print(json.dumps(report_out, ensure_ascii=False))
    return 0 if report_out["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Writing 08 draft/ledger, export artifacts, and write result.json.")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--work", default="/app/work/w08")
    parser.add_argument("--out", default="/app/output")
    parser.add_argument("--language", default=None)
    parser.add_argument("--format", action="append", default=[], choices=("md", "docx", "tex", "pdf", "png"))
    args = parser.parse_args()

    return run_finalize(
        Path(args.case),
        Path(args.work),
        Path(args.out),
        formats=args.format,
        language_override=args.language,
    )


if __name__ == "__main__":
    raise SystemExit(main())
