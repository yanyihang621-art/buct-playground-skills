#!/usr/bin/env python3
"""Build an editable manuscript, synchronized library, claim audit and case-specific result."""
from __future__ import annotations

import argparse
from collections import defaultdict
import difflib
import json
from pathlib import Path
import re
import shutil

from dedup_references import deduplicate_library
from editable_manuscript import export_docx
from format_references import format_library
from ingest_materials import ingest_materials
from io_utils import write_json_atomic, write_text_lf
from result_contract import apply_contract
from contract_delivery import artifact_manifest, publish_named_outputs, publish_result
from claim_coverage import analyze_claim_coverage
from final_claim_audit import final_claim_audit
from tex_project import prepare_tex_project, export_tex_project, readable_tex
from word_citations import prepare_word, embedded_word_records
from parse_bib_sources import make_raw_library
from sync_manuscript_citations import sync_manuscript
from verify_citations import verify_citations


def style_id(value):
    style = re.sub(r"[\s./_–—−-]", "", str(value or "gbt7714")).lower()
    if style in {"apa", "apa7", "apa7th", "apa7thedition", "apa第7版"}:
        return "apa7"
    if style in {"gbt7714", "gbt77142015", "顺序编码制"}:
        return "gbt7714"
    return style if style in {"ieee", "bibtex"} else None


def declared_numbers(canonical):
    embedded, exported = defaultdict(set), defaultdict(set)
    for rec in canonical["records"]:
        for origin in rec.get("source_provenance", []):
            number = origin.get("original_number")
            if number is not None:
                target = embedded if origin.get("section") == "manuscript_bibliography" else exported
                target[str(number)].add(rec["cite_key"])
    return {k: next(iter(v)) for k, v in {**exported, **embedded}.items() if len(v) == 1}


def run_reference_pipeline(input_dir, work_dir, output_dir, *, case_path=None, style=None,
                           verify_doi_online=False, review=None, want_docx=False, draft_only=False):
    root, work, output = Path(input_dir), Path(work_dir) / "references", Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case_file = Path(case_path) if case_path else root / "case.json"
    review = review or {}
    problems = list(review.get("uncertainties", []))
    case = {}
    if case_file.exists():
        try:
            case = json.loads(case_file.read_text(encoding="utf-8-sig"))
            if not isinstance(case, dict):
                raise ValueError("case.json must contain an object")
        except (ValueError, OSError) as exc:
            case = {}
            problems.append({"code": "unreadable_case", "detail": str(exc)})
    requested = style or review.get("style") or case.get("style") or case.get("citation_style") or "gbt7714"
    target = style_id(requested)
    if target is None:
        problems.append({"code": "unimplemented_style", "requested": requested, "draft_style": "gbt7714"})
        target = "gbt7714"
    # A reviewer can explicitly select the input after inspecting ambiguous candidates.
    selected = dict(case)
    for key in ("manuscript_file", "bib_files"):
        if key in review:
            selected[key] = review[key]
    materials = ingest_materials(root, selected, case_path=case_file)
    raw = materials["raw_library"]
    src = materials["manuscript_path"]
    if not raw["records"] and src and src.suffix.lower() == ".docx" and materials["manuscript_text"]:
        embedded = embedded_word_records(src, materials["manuscript_source"])
        if embedded:
            raw = make_raw_library(embedded, [materials["manuscript_source"]], raw["parse_errors"])
    canonical, dedup = deduplicate_library(raw)
    ms, source = materials["manuscript_text"], materials["manuscript_source"]
    problems.extend(materials["uncertainties"])
    evidence_dir = output / "evidence"
    artifacts = {}

    def save_json(name, data):
        path = evidence_dir / (name + ".json")
        write_json_atomic(path, data)
        artifacts[name + "_json"] = str(path)

    save_json("raw_library", raw)
    save_json("canonical_library", canonical)
    save_json("dedup_report", dedup)
    save_json("material_inventory", materials["material_inventory"])
    save_json("manuscript_selection", materials["manuscript_selection"])
    docx_source = src if ms.strip() and src and src.suffix.lower() == ".docx" else None
    word = prepare_word(docx_source, canonical, work) if docx_source else None
    project = prepare_tex_project(root, src) if ms.strip() and src and src.suffix.lower() == ".tex" else None
    if word:
        ms = word["text"]
        problems.extend(word["issues"])
        save_json("word_citation_audit", word["audit"])
        if word["audit"]["fields"]:
            original_fields = evidence_dir / "original_with_live_fields.docx"
            shutil.copyfile(src, original_fields)
            artifacts["original_live_field_source"] = str(original_fields)
    if project:
        ms = project.text
    write_json_atomic(work / "evidence_passages.json", materials["evidence_passages"])
    write_text_lf(work / "original_manuscript.txt", ms)
    result = {"schema_version": "writing-04-references-result/v2", "challenge_id": "product-arena-03-429cc14d",
              "case_id": case.get("case_id", case.get("id", "local_case")), "status": "partial",
              "style": target, "summary": "", "metrics": {}, "artifacts": artifacts, "sources": [],
              "change_records": [], "uncertainties": problems, "verification": {}}
    produced = {}
    returned = {"result": result, "raw_library": raw, "canonical_library": canonical, "dedup_report": dedup}
    if not ms.strip() or not canonical["records"]:
        problems.append({"code": "missing_readable_inputs", "manuscript": source, "records": len(canonical["records"])})
        result.update(status="refused", summary="Manuscript or library unavailable. Inspect material_inventory and raw_library.parse_errors.",
                      sources=raw.get("source_files", []), verification={"passed": False, "failed_checks": ["missing_inputs"]})
        if ms.strip():
            draft = output / "manuscript_for_review.txt"
            write_text_lf(draft, ms)
            artifacts["source_manuscript"] = str(draft)
    else:
        numbers = declared_numbers(canonical)
        numbers.update(review.get("original_number_map", {}))
        sync = sync_manuscript(ms, canonical, style=target, is_latex=materials["is_latex"],
                               insert_claims=(case.get("insert_claims") or []) + (review.get("insert_claims") or []),
                               original_number_map=numbers, text_edits=review.get("text_edits"),
                               non_citations=review.get("non_citations"))
        cmap = sync["citation_map"]
        ext = ".tex" if materials["is_latex"] else ".md"
        draft = (work if project else output) / ("synced_manuscript" + ext)
        bib = output / ("references_synced.bib" if materials["is_latex"] else "bibliography_" + target + (".bib" if target == "bibtex" else ".txt"))
        write_text_lf(draft, sync["synced_manuscript"])
        write_text_lf(bib, sync["formatted_bibliography"])
        if project:
            # Anchor semantic decisions in the chapter, figure caption or appendix where the citation lives.
            tex_files, tex_issues = export_tex_project(project, sync, output, compile_pdf=not draft_only)
            artifacts.update(tex_files)
            problems.extend(tex_issues)
        visible_ms = readable_tex(project, sync["synced_manuscript"]) if project else sync["synced_manuscript"]
        coverage = analyze_claim_coverage(visible_ms, canonical, cmap, evidence_passages=materials["evidence_passages"],
                                          manuscript_source=source)
        save_json("claim_coverage", coverage)
        delivered_ms = visible_ms
        if target == "apa7" and ext == ".md" and sync["cited_records"] and not cmap["unresolved_keys"]:
            plain_bib = sync["formatted_bibliography"]
            if plain_bib.strip() and visible_ms.endswith(plain_bib):
                styled_bib = format_library(sync["cited_records"], "apa7-markdown")
                delivered_ms = visible_ms[:-len(plain_bib)] + styled_bib
                write_text_lf(draft, delivered_ms)
                formatted = output / "bibliography_apa7.md"
                write_text_lf(formatted, styled_bib)
                artifacts["bibliography_styled"] = str(formatted)
        qa = verify_citations(draft.read_text(encoding="utf-8"), cmap, canonical, dedup, raw,
                              verify_doi_online=verify_doi_online, semantic_reviews=review.get("semantic_reviews"),
                              evidence_root=root, formatted_bibliography=bib.read_text(encoding="utf-8"))
        audit = final_claim_audit(coverage, qa, resolutions=review.get("claim_resolutions", []), evidence_root=root)
        save_json("final_claim_audit", audit)
        if audit["blocking"]:
            problems.append({"code": "claim_audit_open", "items": audit["blocking"]})
        save_json("citation_map", cmap)
        retained = cmap["retained_original_occurrences"]
        if retained:
            known = {rec["cite_key"]: rec for rec in canonical["records"]}
            save_json("retained_citations", [{**row, "sources": [
                {field: known[key].get(field) for field in ("cite_key", "title", "doi", "source_provenance")}
                for key in row["cite_keys"]]} for row in retained])
        save_json("change_log", sync["change_log"])
        save_json("qa_report", qa)
        save_json("claim_evidence", qa["claim_evidence"])
        patch = evidence_dir / "manuscript.diff"
        original = readable_tex(project, ms) if project else ms
        write_text_lf(patch, "".join(difflib.unified_diff(original.splitlines(True), delivered_ms.splitlines(True),
                                                          fromfile=str(source), tofile=draft.name)))
        artifacts.update(synced_manuscript=artifacts.get("tex_project_entry", str(draft)),
                         bibliography_formatted=artifacts.get("tex_project_bibliography", str(bib)), manuscript_diff=str(patch))
        if not draft_only and (docx_source or want_docx or review.get("docx") is True):
            apa_records = sync["cited_records"] if target == "apa7" and not cmap["unresolved_keys"] else None
            files, notes = export_docx(docx_source, ms, visible_ms, sync["formatted_bibliography"], output,
                                       word_context=word, apa_records=apa_records)
            artifacts.update(files)
            problems.extend(notes)
        problems.extend(qa["uncertainties"])
        if qa["violations"]:
            problems.append({"code": "qa_violations", "items": qa["violations"]})
        pending = sum(row["verdict"] == "unreviewed" for row in qa["claim_evidence"])
        if pending:
            problems.append({"code": "claims_await_review", "pairs": pending})
        if not cmap["cited_count"]:
            problems.append({"code": "no_resolved_citations", "action": "review original markers and unsupported claims"})
        if draft_only:
            problems.append({"code": "draft_pending_final_export"})
        if not problems and qa["passed"]:
            result["status"] = "completed"
        result.update(summary=f"{raw['record_count']} input records, {canonical['record_count']} distinct records, "
                      f"{len(cmap['in_text_occurrences'])} citation sites, {pending} claim/source pairs awaiting review.",
                      metrics={**qa["metrics"], "retained_original_sites": len(retained)}, sources=[{"manuscript": source}, *[
                          {k: rec.get(k) for k in ("ref_id", "cite_key", "title", "doi", "source_provenance")}
                          for rec in sync["cited_records"]]], change_records=sync["change_log"],
                      verification={"passed": qa["passed"] and audit["passed"], "check_count": len(qa["checks"]) + 1,
                                    "failed_checks": [k for k, v in qa["checks"].items() if not v]
                                    + ([] if audit["passed"] else ["claim_coverage_and_factual_consistency"]),
                                    "doi_verification_mode": qa["doi_verification_mode"],
                                    "pending_claim_source_pairs": pending})
        produced.update(manuscript=delivered_ms, revised_manuscript=delivered_ms, claim_coverage=coverage,
                        final_claim_audit=audit,
                        bibliography=sync["formatted_bibliography"], references=sync["cited_records"],
                        citation_map=cmap, claim_evidence=qa["claim_evidence"], dedup_report=dedup,
                        qa_report=qa, evidence=qa["claim_evidence"], change_log=sync["change_log"])
        returned.update(qa_report=qa, sync_res=sync)
    publish_named_outputs(output, artifacts, review.get("output_names", {}), problems)
    artifacts["manifest"] = artifact_manifest(output, artifacts)
    if problems and result["status"] == "completed":
        result["status"] = "partial"
    produced.update(artifacts)
    apply_contract(result, case.get("output_contract"), produced, review.get("contract_values", {}))
    publish_result(output, case, result)
    return returned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--work-dir", default="/app/work")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--case")
    parser.add_argument("--style")
    parser.add_argument("--review", type=Path, help="Reviewed anchors, source quotations and task-specific contract fields")
    parser.add_argument("--docx", action="store_true")
    parser.add_argument("--draft", action="store_true", help="Produce review materials before rendering or compiling")
    parser.add_argument("--verify-doi-online", action="store_true")
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8-sig")) if args.review else {}
    res = run_reference_pipeline(args.input_dir, args.work_dir, args.output_dir, case_path=args.case, style=args.style,
                                 review=review, want_docx=args.docx, verify_doi_online=args.verify_doi_online,
                                 draft_only=args.draft)["result"]
    print(json.dumps({"case_id": res["case_id"], "status": res["status"], "summary": res["summary"],
                      "uncertainty_count": len(res["uncertainties"])}, ensure_ascii=False))
    return 1 if res["status"] == "refused" else 0


if __name__ == "__main__":
    raise SystemExit(main())
