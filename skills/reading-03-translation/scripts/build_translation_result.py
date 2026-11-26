#!/usr/bin/env python3
"""Prepare source-backed segments and publish the bilingual package next to result.json."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Optional

from apply_translation import apply_translations, write_rendered_outputs
from extract_terms import extract_glossary
from ingest_source import ingest_input
from material_sources import read_case, read_glossary_files
from pdf_utils import write_json_atomic
from segment_and_protect import segment_document, select_scope
from translation_contract import published_fields, resolve_contract
from verify_translation import verify_bilingual_translation


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_draft(path):
    """A single draft retains the original API. A directory holds one authored JSON per batch."""
    path = Path(path)
    if path.is_file():
        return _load(path)
    if not path.is_dir():
        raise FileNotFoundError(path)
    draft = {"segments": [], "glossary_additions": {}, "contract_values": {}}
    for part in sorted(path.glob("*.json")):
        batch = _load(part)
        if not isinstance(batch, dict) or not isinstance(batch.get("segments", []), list):
            raise ValueError(f"{part.name}: expected an object with a segments list")
        for row in batch.get("segments", []):
            if not isinstance(row, dict) or not row.get("segment_id"):
                raise ValueError(f"{part.name}: each draft row needs a segment_id")
            if not isinstance(row.get("target_text"), str) and not isinstance(row.get("sentence_targets"), list):
                raise ValueError(f"{part.name}: supply target_text or sentence_targets for {row['segment_id']}")
            draft["segments"].append(row)
        for key in ("contract_values", "glossary_additions"):
            values = batch.get(key, {})
            if not isinstance(values, dict):
                raise ValueError(f"{part.name}: {key} must be an object")
            for name, value in values.items():
                if name in draft[key] and draft[key][name] != value:
                    raise ValueError(f"{part.name}: conflicting {key} entry {name!r}; reconcile the drafts")
                draft[key][name] = value
    return draft


def _copy_crops(doc, paper_root, output_root, paper_id):
    """Copy only referenced clips and keep their links relative to the result."""
    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key, val in item.items():
                if key == "crop_path" and isinstance(val, str) and val:
                    src = Path(val).resolve()
                    if src.is_file() and src.is_relative_to(paper_root):
                        relative = Path("translation", "sources", paper_id) / src.relative_to(paper_root)
                        dest = output_root / relative
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if dest.resolve() != src:
                            shutil.copyfile(src, dest)
                        item[key] = relative.as_posix()
                elif isinstance(val, (list, dict)):
                    visit(val)
    visit(doc)


def _source_document(manifest, output_root):
    """Give independent papers disjoint block/table IDs before segmenting them."""
    papers = manifest["papers"]
    document = {"source_file": ", ".join(p["source_file"] for p in papers), "blocks": [], "tables": [],
                "uncertainties": list(manifest["issues"]), "sources": []}
    available = []
    for paper in papers:
        path = Path(paper["document_json"])
        doc = _load(path)
        _copy_crops(doc, path.parent.resolve(), output_root, paper["paper_id"])
        prefix = paper["paper_id"] + "_" if len(papers) > 1 else ""
        for row in doc.get("blocks", []) + doc.get("tables", []):
            for key in ("block_id", "continued_by", "continuation_of", "table_id"):
                if row.get(key):
                    row[key] = prefix + row[key]
        document["blocks"].extend(doc.get("blocks", []))
        document["tables"].extend(doc.get("tables", []))
        document["uncertainties"].extend(doc.get("uncertainties", []))
        document["sources"].append({key: paper[key] for key in ("paper_id", "source_file", "format", "title", "status")})
        available.append(paper["status"])
    if not papers or all(s == "refused" for s in available):
        status = "refused"
        if not papers and not document["uncertainties"]:
            document["uncertainties"].append({"code": "no_source_material", "detail": "No translation target could be located."})
    elif any(s not in ("success", "completed") for s in available) or manifest["issues"]:
        status = "partial"
    else:
        status = "success"
    document["status"] = status
    return document


def _attach_locators(segments, document):
    blocks = {b["block_id"]: b for b in document["blocks"]}
    tables = {t["table_id"]: t for t in document["tables"]}
    for seg in segments["segments"]:
        item = blocks.get(seg["block_id"]) or tables.get(seg.get("table_id")) or {}
        where = {**item.get("source_locator", {}), **seg.get("source_locator", {})}
        if "table_id" in seg:
            where["table_id"] = seg["table_id"]
        seg["source_locator"] = where
        # On cross-page merges, retain each actual source span.
        chain = []
        seen = set()
        while item and item.get("block_id") not in seen:
            seen.add(item.get("block_id"))
            if item.get("source_locator"):
                chain.append(item["source_locator"])
            item = blocks.get(item.get("continued_by"))
        if len(chain) > 1:
            seg["source_spans"] = chain


def run_translation_pipeline(
    input_path: str | Path, work_dir: str | Path, result_path: Optional[str | Path] = None,
    case_path: Optional[str | Path] = None, translations_json: Optional[str | Path] = None,
    ocr_mode: str = "auto", prepare_only: bool = False,
) -> dict[str, Any]:
    work = Path(work_dir)
    stage = work / "translation"
    # Preserve an edited result and its clips when a translator reopens the work index.
    publish = result_path is not None and (not prepare_only or not Path(result_path).exists())
    output_root = Path(result_path).parent if publish else work
    final = output_root / "translation"
    case = read_case(Path(input_path), case_path)
    manifest = ingest_input(input_path, work / "ingest", case_path=case_path, ocr_mode=ocr_mode, save_crops=True,
                            excluded_dirs=(stage, final))
    document = _source_document(manifest, output_root)
    overrides = read_draft(translations_json) if translations_json else {}
    if not isinstance(overrides, dict):
        raise ValueError("translations JSON must be a segment map or an object containing segments")
    input_root = Path(input_path)
    glossary_root = Path(case_path).parent if case_path else (input_root if input_root.is_dir() else input_root.parent)
    file_terms, file_issues = read_glossary_files(glossary_root, case, manifest["auxiliary_files"])
    if file_issues:
        document["uncertainties"].extend(file_issues)
        if document["status"] == "success":
            document["status"] = "partial"
    custom = {}
    for term_map in (overrides.get("glossary_additions", {}), file_terms, case.get("custom_glossary", {})):
        if not isinstance(term_map, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                                    for k, v in term_map.items()):
            raise ValueError("Expected a mapping from source terms to nonempty Chinese glossary terms")
        custom.update(term_map)
    glossary = extract_glossary(document, custom_glossary=custom)
    segments = segment_document(document)
    _attach_locators(segments, document)
    segments = select_scope(segments, case)
    write_json_atomic(stage / "segments.json", segments)
    write_json_atomic(stage / "glossary.json", glossary)
    preparation = {"papers": document["sources"], "segment_count": len(segments["segments"]),
                   "status": segments["doc_status"], "uncertainties": segments["uncertainties"],
                   "segments_path": str(stage / "segments.json"), "glossary_path": str(stage / "glossary.json")}
    if prepare_only and not publish:
        return {**preparation, "checkpoint_written": False}

    bilingual = apply_translations(segments, glossary, translations_override=overrides)
    seg_by_id = {s["segment_id"]: s for s in segments["segments"]}
    for item in bilingual["segments"]:
        source = seg_by_id[item["segment_id"]]
        for key in ("source_locator", "source_spans"):
            if key in source:
                item[key] = source[key]
    qa = verify_bilingual_translation(bilingual, segments, glossary)
    report_status = bilingual["status"]
    if report_status == "completed" and not qa["passed"]:
        report_status = "partial"
    bilingual["status"] = report_status
    bilingual["qa_report"] = qa
    names = {"document_json": "document.json", "segments_json": "segments.json", "glossary_json": "glossary.json",
             "qa_report_json": "qa_report.json"}
    for key, product in (("document_json", document), ("segments_json", segments), ("glossary_json", glossary),
                         ("qa_report_json", qa)):
        write_json_atomic(final / names[key], product)
    rendered = write_rendered_outputs(bilingual, final)
    names.update({key: Path(path).name for key, path in rendered.items()})
    artifacts = {key: (Path("translation") / name).as_posix() for key, name in names.items()}
    references = [{"segment_id": s["segment_id"], **s["source_locator"],
                   "quote": s["source_text"], **({"source_spans": s["source_spans"]} if s.get("source_spans") else {})}
                  for s in bilingual["segments"]]
    changes = [{"segment_id": s["segment_id"], "status": s["status"], "rationale": s.get("rationale", "")}
               for s in bilingual["segments"]]
    case_id = case.get("case_id") if case else None
    uncertainties = list(bilingual["uncertainties"])
    if case and case_id is None:
        uncertainties.append({"code": "case_id_missing", "detail": "The input case has no case_id."})
        if report_status == "completed":
            report_status = "partial"
    result = {
        "schema_version": "reading-03-translation-result/v2", "challenge_id": "product-arena-01-5a9928c1",
        "case_id": case_id, "status": report_status, "source_lang": bilingual["source_lang"],
        "target_lang": bilingual["target_lang"], "sources": document["sources"], "evidence": references,
        "segments": bilingual["segments"], "tables_bilingual": bilingual["tables_bilingual"], "glossary": glossary,
        "summary": f"{qa['metrics']['translated_count']}/{len(segments['segments'])} segments rendered; "
                   f"{len(qa['violations'])} QA findings.",
        "metrics": qa["metrics"], "artifacts": artifacts, "uncertainties": uncertainties,
        "change_records": changes, "qa_report": qa,
        "verification": {"passed": qa["passed"], "check_count": len(qa["checks"]),
                         "failed_checks": [key for key, ok in qa["checks"].items() if not ok]},
    }
    translation_text = "\n\n".join(s["target_text"] for s in bilingual["segments"])
    products = {"translation": translation_text, "translated_text": translation_text, "translation_text": translation_text,
                "bilingual_segments": bilingual["segments"], "translations": bilingual["segments"],
                "terminology": glossary["entries"], "evidence_map": references, "source_map": references,
                "change_log": changes, "qa": qa, **artifacts}
    supplied = overrides.get("contract_values", {})
    if not isinstance(supplied, dict):
        raise ValueError("contract_values must be an object")
    contract = case.get("output_contract")
    issues = resolve_contract(result, contract, products, supplied)
    view = published_fields(result, contract)
    if view is not result and "case_id" not in contract["properties"]:
        issues.append({"code": "conflicting_output_contract", "field": "case_id",
                       "message": "The runtime requires case_id, but the supplied closed schema forbids it."})
    if issues:
        result["uncertainties"].extend(issues)
        result["verification"]["passed"] = False
        result["verification"]["failed_checks"].append("output_contract")
        if result["status"] in {"completed", "success"}:
            result["status"] = "partial"
    if result_path:
        if view is not result:
            write_json_atomic(final / "delivery_details.json", result)
            view = published_fields(result, contract)
        write_json_atomic(Path(result_path), view)
    if prepare_only:
        return {**preparation, "checkpoint_written": True, "checkpoint_status": result["status"],
                "result_path": str(result_path)}
    return {"result": result, "bilingual": bilingual, "qa_report": qa, "glossary": glossary, "segments": segments}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run evidence-linked English-to-Chinese paper translation.")
    parser.add_argument("--input", required=True, help="Source directory or a case-selected single paper")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--result-out", help="Publish result.json and its sibling translation/ artifacts here")
    parser.add_argument("--case", help="Defaults to case.json in the input directory")
    parser.add_argument("--translations-json", help="Authored JSON draft, or directory containing one JSON per batch")
    parser.add_argument("--prepare-only", action="store_true", help="Write the locked glossary and segment index for translation")
    parser.add_argument("--summary", action="store_true", help="Print counts and the first QA findings instead of the full bilingual result")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    args = parser.parse_args()
    out = run_translation_pipeline(args.input, args.work_dir, args.result_out, args.case, args.translations_json,
                                   args.ocr, args.prepare_only)
    if args.prepare_only:
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["segment_count"] else 1
    result = out["result"]
    report = result if not args.summary else {
        "case_id": result["case_id"], "status": result["status"], "summary": result["summary"],
        "verification": result["verification"], "first_findings": result["qa_report"]["violations"][:8],
        "result_path": args.result_out,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if out["result"]["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
