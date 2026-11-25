#!/usr/bin/env python3
"""Build an editable manuscript, synchronized library, claim audit and case-specific result."""
from __future__ import annotations

import argparse
from collections import defaultdict
import difflib
import json
from pathlib import Path
import re

from dedup_references import deduplicate_library
from editable_manuscript import export_docx
from ingest_materials import ingest_materials
from io_utils import write_json_atomic, write_text_lf
from result_contract import apply_contract
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
                           verify_doi_online=False, review=None, want_docx=False):
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
        draft = output / ("synced_manuscript" + ext)
        bib = output / ("references_synced.bib" if materials["is_latex"] else "bibliography_" + target + (".bib" if target == "bibtex" else ".txt"))
        write_text_lf(draft, sync["synced_manuscript"])
        write_text_lf(bib, sync["formatted_bibliography"])
        qa = verify_citations(draft.read_text(encoding="utf-8"), cmap, canonical, dedup, raw,
                              verify_doi_online=verify_doi_online, semantic_reviews=review.get("semantic_reviews"),
                              evidence_root=root, formatted_bibliography=bib.read_text(encoding="utf-8"))
        save_json("citation_map", cmap)
        save_json("change_log", sync["change_log"])
        save_json("qa_report", qa)
        save_json("claim_evidence", qa["claim_evidence"])
        patch = evidence_dir / "manuscript.diff"
        write_text_lf(patch, "".join(difflib.unified_diff(ms.splitlines(True), sync["synced_manuscript"].splitlines(True),
                                                          fromfile=str(source), tofile=draft.name)))
        artifacts.update(synced_manuscript=str(draft), bibliography_formatted=str(bib), manuscript_diff=str(patch))
        src = materials["manuscript_path"]
        docx_source = src if src and src.suffix.lower() == ".docx" else None
        if docx_source or want_docx or review.get("docx") is True:
            files, notes = export_docx(docx_source, ms, sync["synced_manuscript"], sync["formatted_bibliography"], output)
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
        if not problems and qa["passed"]:
            result["status"] = "completed"
        result.update(summary=f"{raw['record_count']} input records, {canonical['record_count']} distinct records, "
                      f"{len(cmap['in_text_occurrences'])} citation sites, {pending} claim/source pairs awaiting review.",
                      metrics=qa["metrics"], sources=[{"manuscript": source}, *[
                          {k: rec.get(k) for k in ("ref_id", "cite_key", "title", "doi", "source_provenance")}
                          for rec in sync["cited_records"]]], change_records=sync["change_log"],
                      verification={"passed": qa["passed"], "check_count": len(qa["checks"]),
                                    "failed_checks": [k for k, v in qa["checks"].items() if not v],
                                    "doi_verification_mode": qa["doi_verification_mode"],
                                    "pending_claim_source_pairs": pending})
        produced.update(manuscript=sync["synced_manuscript"], revised_manuscript=sync["synced_manuscript"],
                        bibliography=sync["formatted_bibliography"], references=sync["cited_records"],
                        citation_map=cmap, claim_evidence=qa["claim_evidence"], dedup_report=dedup,
                        qa_report=qa, evidence=qa["claim_evidence"], change_log=sync["change_log"])
        returned.update(qa_report=qa, sync_res=sync)
    apply_contract(result, case.get("output_contract"), produced, review.get("contract_values", {}))
    write_json_atomic(output / "result.json", result)
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
    parser.add_argument("--verify-doi-online", action="store_true")
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding="utf-8-sig")) if args.review else {}
    res = run_reference_pipeline(args.input_dir, args.work_dir, args.output_dir, case_path=args.case, style=args.style,
                                 review=review, want_docx=args.docx, verify_doi_online=args.verify_doi_online)["result"]
    print(json.dumps({"case_id": res["case_id"], "status": res["status"], "summary": res["summary"],
                      "uncertainty_count": len(res["uncertainties"])}, ensure_ascii=False))
    return 1 if res["status"] == "refused" else 0


if __name__ == "__main__":
    raise SystemExit(main())
