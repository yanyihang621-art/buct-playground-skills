#!/usr/bin/env python3
"""Package an editor-authored manuscript, its verified evidence and case-specific deliverables."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from cluster_related_work import cluster_related_works
from derive_gaps_and_contributions import build_gap_and_contribution_map
from draft_intro_related_work import render_intro_and_related_work
from export_manuscript import case_exports, export_manuscript
from ingest_materials import ingest_materials
from output_contract import contract_specs, required_contract_fields, resolve_contract
from publication_citations import PublicationCitations, case_citation_style
from rw_utils import write_json_atomic, write_text_lf
from verify_intro_rw import verify_intro_and_related_work


def _read_object(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{Path(path).name} must contain a JSON object")
    return data


def _publication_text(draft: str, papers: list, case: dict, materials: dict,
                      analysis: dict, gc_map: dict) -> tuple[str, list]:
    formatter = PublicationCitations(draft, papers, case_citation_style(case))
    finished, issues = formatter.render()
    if finished == draft:
        return draft, issues
    for key, field in (("research_gaps", "gap_statement"), ("contributions", "claim"),
                       ("claim_evidence", "claim")):
        for row in gc_map.get(key, []):
            if isinstance(row, dict) and isinstance(row.get(field), str):
                row[field] = formatter.fragment(row[field])
    for limit in materials.get("interpretation_limits", []):
        if isinstance(limit, dict) and isinstance(limit.get("manuscript_span"), str):
            limit["manuscript_span"] = formatter.fragment(limit["manuscript_span"])
    spans = materials.get("section_spans", {})
    for key in spans:
        spans[key] = formatter.fragment(spans[key])
    # Contract-specific prose may quote the draft. Keep source quotations and IDs verbatim.
    def render_field(value):
        if isinstance(value, list):
            return [render_field(v) for v in value]
        if isinstance(value, dict):
            return {k: v if k in {"quote","source_quote","source_id","source_ids","cite_key","cite_keys","evidence"}
                    else render_field(v) for k,v in value.items()}
        if isinstance(value, str):
            return finished if value == draft else formatter.fragment(value)
        return value
    if isinstance(analysis.get("contract_fields"), dict):
        analysis["contract_fields"] = render_field(analysis["contract_fields"])
    return finished, issues


def _sections(draft: str) -> dict:
    # Collect a complete section including its lower-level subheadings.
    headings = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", draft, re.MULTILINE))
    sections = {}
    for i, h in enumerate(headings):
        next_peer = next((m.start() for m in headings[i + 1:] if len(m[1]) <= len(h[1])), len(draft))
        sections[h[2].strip()] = draft[h.end():next_peer].strip()
    return sections


def _contract_products(result: dict, sections: dict, matrix: str, papers: list, gaps: list, contribs: list) -> dict:
    products = {
        "draft": result["manuscript"], "draft_markdown": result["manuscript"],
        "main_result": result["manuscript"], "markdown": result["manuscript"],
        "introduction_and_related_work": result["manuscript"], "sections": sections,
        "evidence": result["claim_evidence"], "evidence_table": result["claim_evidence"],
        "fact_source_mapping": result["claim_evidence"], "references": papers, "bibliography": papers,
        "comparison_matrix": matrix, "research_gaps": gaps, "contributions": contribs,
        "limitations": result["uncertainties"], "revision_log": result["change_records"],
    }
    aliases = {"research_background": ("research background", "background", "研究背景"),
               "introduction": ("introduction", "引言", "研究背景", "research background"),
               "related_work": ("related work", "related works", "相关工作", "相关研究")}
    folded = {title.casefold(): body for title, body in sections.items()}
    for field, labels in aliases.items():
        for name in labels:
            if name in folded:
                products[field] = folded[name]
                break
    for ext in ("md", "docx", "tex", "pdf", "png"):
        path = result["artifacts"].get("intro_and_related_work_" + ext)
        if path:
            products["manuscript_" + ext] = path
            products[ext + "_path"] = path
            products["output_" + ext] = path
            products[path] = path
            if ext in ("pdf", "png"):
                products["preview_" + ext] = path
                products[ext + "_preview"] = path
    return products


def _blocking_uncertainty(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("blocking") is True or item.get("required") is True:
        return True
    if str(item.get("severity", "")).lower() in {"fatal", "critical", "error", "high", "blocking"}:
        return True
    return item.get("code") in {"missing_study_facts", "empty_or_missing_literature_corpus",
                                "missing_required_input", "missing_required_artifact",
                                "missing_source_evidence", "unresolved_contract_field"}


def _change_source_audit(changes: list, passages: list) -> tuple[list, list]:
    by_id = {p["source_id"]: p for p in passages}
    locations, issues = [], []
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or "source_ids" not in change:
            continue
        ids = change["source_ids"]
        if not isinstance(ids, list):
            issues.append({"code": "invalid_change_source_ids", "record_index": index,
                           "message": "change_records[].source_ids must be an array of passage IDs."})
            continue
        for source_id in ids:
            passage = by_id.get(source_id) if isinstance(source_id, str) else None
            if not passage or not passage.get("path") or not passage.get("locator"):
                issues.append({"code": "unresolved_change_source", "record_index": index, "source_id": source_id,
                               "message": "No indexed passage resolves this revision's source ID to a path and locator."})
            else:
                locations.append({"record_index": index, "source_id": source_id,
                                  "path": passage["path"], "locator": passage["locator"]})
    return locations, issues


def run_intro_rw_pipeline(input_dir: str | Path, work_dir: str | Path, output_dir: str | Path, *,
                          case_path: str | Path | None = None, draft_file: str | Path | None = None,
                          language: str | None = None, analysis_file: str | Path | None = None,
                          materials_file: str | Path | None = None, formats: list[str] | None = None) -> dict:
    inp, work, out = Path(input_dir), Path(work_dir) / "intro_rw", Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case_file = Path(case_path) if case_path else inp / "case.json"
    case = _read_object(case_file) if case_path or case_file.exists() else {}
    lang = str(language or case.get("language") or case.get("output_language") or "en")
    materials = _read_object(materials_file) if materials_file else ingest_materials(inp, case)
    write_json_atomic(work / "materials_index.json", materials)
    analysis, editorial_issues = {}, []
    if analysis_file:
        from editorial_evidence import apply_editorial_evidence
        materials, analysis, editorial_issues = apply_editorial_evidence(materials, _read_object(analysis_file))

    papers = materials.get("papers", [])
    has_material = bool(papers or materials.get("study_facts") or
                        any(p.get("role") != "instruction" for p in materials.get("passages", [])))
    clusters = cluster_related_works(materials)
    matrix = clusters.pop("comparison_matrix_md")
    gc_map = build_gap_and_contribution_map(materials, clusters)
    for key in ("research_gaps", "contributions", "claim_evidence"):
        if key in analysis:
            gc_map[key] = analysis[key]
    cards = materials.get("claim_evidence", gc_map.get("claim_evidence", []))
    gc_map["claim_evidence"] = cards
    draft = ""
    mode = "missing_input"
    delivery_issues = list(editorial_issues)
    if has_material and draft_file:
        mode = "agent_manuscript"
        draft = Path(draft_file).read_text(encoding="utf-8-sig")
        if not draft.strip():
            delivery_issues.append({"code": "empty_agent_draft", "message": "The supplied manuscript is empty."})
    elif has_material:
        mode = "outline_only"
        draft = render_intro_and_related_work(materials, clusters, gc_map, language=lang)
        delivery_issues.append({"code": "awaiting_agent_draft",
                                "message": "Working outline generated. Review the evidence and write the final prose."})

    if mode == "agent_manuscript":
        draft, citation_issues = _publication_text(draft, papers, case, materials, analysis, gc_map)
        delivery_issues.extend(citation_issues)
    qa = verify_intro_and_related_work(draft, materials, clusters, gc_map, language=lang)
    uncertainties = [*materials.get("material_gaps", []), *analysis.get("uncertainties", [])]
    if any(_blocking_uncertainty(item) for item in uncertainties):
        delivery_issues.append({"code": "unresolved_required_material",
                                "message": "One or more recorded material gaps block a requested deliverable."})
    if analysis_file and draft_file and draft.strip() and not cards:
        attached_claims = [r for k in ("research_gaps", "contributions") for r in analysis.get(k, [])
                           if r.get("evidence") and (r.get("claim") or r.get("gap_statement") or "").strip()
                           and (r.get("claim") or r["gap_statement"]).strip() in draft]
        if not attached_claims:
            delivery_issues.append({"code": "missing_manuscript_evidence",
                                    "message": "Attach manuscript claims to quotations from the indexed source materials."})
    if qa["violations"]:
        uncertainties.append({"code": "manuscript_qa", "items": qa["violations"]})
    changes = analysis.get("change_records", [])
    change_sources, change_issues = _change_source_audit(changes, materials.get("passages", []))
    delivery_issues.extend(change_issues)

    artifacts = {}
    if draft:
        contract = case.get("output_contract")
        export_contract = dict(contract) if isinstance(contract, dict) else {}
        export_contract.update(properties=contract_specs(contract), required=sorted(required_contract_fields(contract)))
        case_formats, names = case_exports({**case, "output_contract": export_contract})
        requested_names = analysis.get("artifact_names", {})
        if isinstance(requested_names, dict):
            names.update(requested_names)
        else:
            delivery_issues.append({"code": "invalid_artifact_names", "message": "Use a format-to-filename object."})
        artifacts, export_issues = export_manuscript(draft, out, [*case_formats, *(formats or [])], names, lang)
        delivery_issues.extend(export_issues)
    write_text_lf(out / "comparison_matrix.md", matrix)
    artifacts["comparison_matrix_md"] = "comparison_matrix.md"
    source_manifest = materials.get("sources", [])
    if not source_manifest:
        source_manifest = [{k: p[k] for k in ("cite_key", "title", "authors", "year", "venue", "doi", "source_path",
                                             "locator", "source_ids") if k in p} for p in papers]
    output_records = {
        "related_work_clusters": clusters, "gap_contribution_map": gc_map,
        "claim_evidence": cards, "sources": source_manifest, "qa_report": qa,
    }
    for name, record in output_records.items():
        write_json_atomic(out / f"{name}.json", record)
        artifacts[name + "_json"] = f"{name}.json"
    research_gaps, contributions = gc_map["research_gaps"], gc_map["contributions"]
    result = {
        "schema_version": "writing-08-intro-rw-result/v1", "challenge_id": "product-arena-04-4b82ad21",
        "case_id": case.get("case_id", "local_intro_rw_case"), "status": "completed", "language": lang,
        "summary": "", "draft_mode": mode, "manuscript": draft, "metrics": qa["metrics"], "artifacts": artifacts,
        "sources": source_manifest, "claim_evidence": cards, "research_gaps": research_gaps, "contributions": contributions,
        "change_records": changes,
        "uncertainties": uncertainties,
        "verification": {"passed": bool(qa["passed"]), "check_count": len(qa["checks"]),
                         "action": "checked_manuscript", "checks": qa["checks"], "draft_mode": mode,
                         "change_sources": change_sources,
                         "failed_checks": [k for k, passed in qa["checks"].items() if not passed]},
    }
    if not has_material:
        result["status"] = "refused"
    elif not qa["passed"] or delivery_issues:
        result["status"] = "partial"
    result["summary"] = (f"{mode}: {len(papers)} literature records, {clusters['route_cluster_count']} comparison groups, "
                         f"{len(research_gaps)} proposed gaps, {len(contributions)} contribution entries, "
                         f"{len(cards)} claim-evidence links.")
    products = _contract_products(result, _sections(draft), matrix, papers, research_gaps, contributions)
    # Preserve full audit records while presenting the case's requested wire format.
    contract = case.get("output_contract")
    wire = dict(result)
    supplied_fields = analysis.get("contract_fields", {})
    if not isinstance(supplied_fields, dict):
        delivery_issues.append({"code": "invalid_contract_fields", "message": "contract_fields must be an object."})
        supplied_fields = {}
    contract_issues = resolve_contract(wire, contract, products, supplied_fields)
    reshaped = {key for key in wire.keys() & result.keys() if wire[key] != result[key]}
    for key in wire.keys() - result.keys():
        result[key] = wire[key]
    all_issues = [*delivery_issues, *contract_issues]
    if all_issues:
        result["uncertainties"].extend(all_issues)
        result["verification"]["passed"] = False
        result["verification"]["failed_checks"].extend(dict.fromkeys(row.get("code", "delivery_issue") for row in all_issues))
        if has_material:
            result["status"] = "partial"
    payload = {key: wire[key] if key in reshaped else value for key, value in result.items()}
    if isinstance(contract, dict) and contract.get("additionalProperties") is False:
        allowed = set(contract_specs(contract)) | {"case_id"}
        payload = {k: v for k, v in payload.items() if k in allowed}
    if reshaped or payload.keys() != result.keys():
        write_json_atomic(out / "editorial_report.json", result)
    write_json_atomic(out / "result.json", payload)
    return {"result": result, "materials": materials, "clusters": clusters, "gc_map": gc_map,
            "draft_md": draft, "qa_report": qa}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--work-dir", default="/app/work")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--case")
    parser.add_argument("--draft-file", help="Final manuscript written from the current case")
    parser.add_argument("--analysis", help="Editorial evidence card, gap, contribution and contract data")
    parser.add_argument("--materials-file", help="Original materials index produced by ingest_materials.py")
    parser.add_argument("--language")
    parser.add_argument("--format", action="append", choices=("md", "docx", "pdf", "tex", "png"), default=[])
    args = parser.parse_args()
    try:
        bundle = run_intro_rw_pipeline(args.input_dir, args.work_dir, args.output_dir, case_path=args.case,
                                       draft_file=args.draft_file, language=args.language, analysis_file=args.analysis,
                                       materials_file=args.materials_file, formats=args.format)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Input or file error: {exc}\n")
    r = bundle["result"]
    print(json.dumps({"case_id": r["case_id"], "status": r["status"], "verification": r["verification"],
                      "result": str(Path(args.output_dir) / "result.json")}, ensure_ascii=False))
    return 0 if r["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
