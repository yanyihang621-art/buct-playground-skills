#!/usr/bin/env python3
"""Prepare a source-indexed review packet and deliver a verified academic revision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_manuscript_terms import extract_manuscript_glossary
from ingest_manuscript import load_materials
from output_contract import contract_fields, fill_contract, normalized_key
from polish_sentences import polish_guarded_sentences, render_revision_report
from segment_and_guard import segment_and_guard_manuscript
from text_utils import write_json_atomic, write_text_lf
from verify_polish import verify_polish, verify_translation


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def edit_rows(payload):
    """Retain any sentence-specific ancillary data alongside the primary edit."""
    raw = payload.get("sentences", payload.get("edits", payload)) if isinstance(payload, dict) else payload
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [
            {**(v if isinstance(v, dict) else {"polished_text": v}), "sent_id": k}
            for k, v in raw.items()
            if k not in {"contract_values", "uncertainties", "bilingual_required", "deliverables", "register"}
        ]
    raise ValueError("edits must be a list of sentence records or a map keyed by sent_id")


def checked_edits(payload, guarded):
    original = {s["sent_id"]: s for s in guarded["sentences"]}
    accepted, issues, duplicates = {}, [], set()
    for index, row in enumerate(edit_rows(payload)):
        if not isinstance(row, dict) or not isinstance(row.get("sent_id"), str):
            issues.append({"code": "invalid_edit", "row": index})
            continue
        sid = row["sent_id"]
        if sid in accepted or sid in duplicates:
            accepted.pop(sid, None)
            duplicates.add(sid)
            issues.append({"code": "duplicate_edit", "sent_id": sid})
            continue
        src = original.get(sid)
        if not src:
            issues.append({"code": "unknown_sentence", "sent_id": sid})
        elif "source_text" in row and row["source_text"] != src["source_text"]:
            issues.append({"code": "source_mismatch", "sent_id": sid})
        elif not isinstance(row.get("polished_text"), str) or not row["polished_text"].strip():
            issues.append({"code": "empty_edit", "sent_id": sid})
        elif row["polished_text"] != src["source_text"] and (
            not str(row.get("edit_type") or "").strip() or not str(row.get("rationale") or "").strip()
        ):
            issues.append({"code": "missing_edit_reason_or_type", "sent_id": sid})
        else:
            accepted[sid] = row
    missing = [sid for sid, s in original.items() if s["role"] == "prose" and sid not in accepted]
    if missing:
        issues.append({"code": "unreviewed_sentences", "sent_ids": missing})
    return accepted, issues


def render_alignment(rows):
    def cell(value):
        return str(value).replace("|", r"\|").replace("\n", "<br>")
    lines = ["# Bilingual alignment", "", "| Sentence | Original | Polished | Translation |",
             "|---|---|---|---|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(row[k]) for k in ("sent_id", "source_text", "polished_text", "translation")) + " |")
    return "\n".join(lines) + "\n"


def empty_result(case_id, register):
    return {
        "schema_version": "writing-02-polishing-result/v2",
        "challenge_id": "product-arena-02-7051d92d",
        "case_id": case_id,
        "status": "partial",
        "register": register,
        "summary": "",
        "metrics": {},
        "artifacts": {},
        "sources": [],
        "change_records": [],
        "uncertainties": [],
        "verification": {"passed": False, "check_count": 0, "failed_checks": []},
    }


def publish(result, out_dir, contract):
    """Honor a closed case schema while retaining the full review alongside it."""
    if isinstance(contract, dict) and contract.get("additionalProperties") is False:
        keys = set(contract_fields(contract)) | {"case_id"}
        audit_path = out_dir / "polish_audit.json"
        result["artifacts"]["audit_json"] = str(audit_path.resolve())
        write_json_atomic(audit_path, result)
        saved = {k: v for k, v in result.items() if k in keys}
    else:
        saved = result
    write_json_atomic(out_dir / "result.json", saved)


def failed_result(result, out_dir, contract, code, message):
    result.update(status="refused", summary=message)
    result["uncertainties"].append({"code": code, "message": message})
    result["verification"]["failed_checks"] = [code]
    result["uncertainties"].extend(fill_contract(result, contract, {}, {}))
    publish(result, out_dir, contract)
    return {"result": result}


def run_polishing_pipeline(
    input_dir, work_dir, output_dir, *, case_path=None, edits_json=None, register=None,
    prepare_only=False, demo_rules=False, sources=None, contract_json=None,
):
    in_p, work_p, out_p = Path(input_dir), Path(work_dir) / "polishing", Path(output_dir)
    case_file = Path(case_path) if case_path else in_p / "case.json"
    case, contract = {}, {}
    result = empty_result("local_polish_case", register or "journal_article")
    try:
        if case_path or case_file.exists():
            case = read_json(case_file)
        if not isinstance(case, dict):
            raise ValueError("case.json must contain an object")
        contract = case.get("output_contract", {})
        reg = register or case.get("register") or case.get("genre") or "journal_article"
        result = empty_result(case.get("case_id", "local_polish_case"), str(reg))
        materials = load_materials(in_p, case, sources)
    except (OSError, ValueError, TypeError) as exc:
        return failed_result(result, out_p, contract, "input_error", str(exc))
    result["sources"] = materials["sources"]
    warnings = list(materials["warnings"])
    result["uncertainties"].extend(warnings)
    text = materials["text"]
    if not text.strip():
        return failed_result(result, out_p, contract, "missing_manuscript", "No readable manuscript found; inspect material warnings and source selection.")

    glossary = extract_manuscript_glossary(text, custom_glossary=materials["glossary"],
                                          glossary_conflicts_check=materials["glossary_overrides"])
    guarded = segment_and_guard_manuscript(text, is_latex=materials["is_latex"])
    for s in guarded["sentences"]:
        s["source_location"] = materials["line_map"][s["line"] - 1]
    work_files = {name: str(work_p / name) for name in [
        "materials.json", "glossary.json", "guarded_sentences.json", "manuscript.txt"]}
    write_json_atomic(work_p / "materials.json", materials)
    write_json_atomic(work_p / "glossary.json", glossary)
    write_json_atomic(work_p / "guarded_sentences.json", guarded)
    write_text_lf(work_p / "manuscript.txt", text)
    if prepare_only:
        return {"prepared": work_files, "case_id": result["case_id"], "sentence_count": guarded["sentence_count"],
                "register": reg, "contract_fields": list(contract_fields(contract)), "warnings": warnings}

    try:
        manifest = read_json(edits_json) if edits_json else {}
        meta = manifest if isinstance(manifest, dict) else {}
        if "case_id" in meta and meta["case_id"] != result["case_id"]:
            raise ValueError("The supplied edit manifest belongs to a different case_id")
        supplied = dict(meta.get("contract_values", {}))
        if contract_json:
            extra = read_json(contract_json)
            if not isinstance(extra, dict):
                raise ValueError("--contract-json requires a JSON object")
            supplied.update(extra)
        if not edits_json and not demo_rules:
            return failed_result(result, out_p, contract, "missing_editor_review",
                                 "Complete the review packet and pass --edits-json before finalizing.")
        reviewed, edit_issues = checked_edits(manifest, guarded) if edits_json else ({}, [])
        pol = polish_guarded_sentences(guarded, glossary, register=reg,
                                       edits_override={"sentences": list(reviewed.values())} if edits_json else None)
    except (OSError, ValueError, TypeError) as exc:
        return failed_result(result, out_p, contract, "invalid_edit_input", str(exc))

    doc = pol["polished_doc"]
    locations = {s["sent_id"]: s["source_location"] for s in guarded["sentences"]}
    for row in doc["sentences"]:
        row["source_location"] = locations[row["sent_id"]]
    qa = verify_polish(doc, guarded, glossary)
    if edits_json and not qa["passed"]:
        rejected = {v["sent_id"] for v in qa["violations"] if v.get("sent_id")}
        corrections = dict(reviewed)
        proposals = []
        for row in doc["sentences"]:
            sid = row["sent_id"]
            if sid in rejected and row["polished_text"] != row["source_text"]:
                proposals.append({**corrections[sid], "source_location": locations[sid]})
                corrections[sid] = {"sent_id": sid, "polished_text": row["source_text"], "edit_type": "needs_review",
                                    "rationale": "The proposed edit failed source checks. Retained the original pending review.",
                                    "uncertain": True}
        if proposals:
            pol = polish_guarded_sentences(guarded, glossary, register=reg,
                                           edits_override={"sentences": list(corrections.values())})
            doc = pol["polished_doc"]
            qa["rejected_edits"] = proposals
            qa["delivered_text_checks"] = verify_polish(doc, guarded, glossary)["checks"]
            qa["metrics"]["proposed_edited_count"] = qa["metrics"]["edited_count"]
            qa["metrics"]["edited_count"] = doc["edited_count"]
            reviewed = corrections
    doc["qa_report"] = qa
    uncertainties = result["uncertainties"]
    uncertainties.extend(glossary.get("conflicts", []))
    uncertainties.extend(edit_issues)
    uncertainties.extend(u for u in doc.get("uncertainties", []) if u.get("code") != "unreviewed_sentence")
    supplied_uncertainties = meta.get("uncertainties") or []
    for item in supplied_uncertainties if isinstance(supplied_uncertainties, list) else [supplied_uncertainties]:
        uncertainties.append(item if isinstance(item, dict) else {"code": "author_review", "message": str(item)})
    for row in doc["sentences"]:
        if row.get("uncertain") and row["sent_id"] in reviewed:
            uncertainties.append({"code": "author_review", "sent_id": row["sent_id"],
                                  "source_location": row["source_location"], "message": row["rationale"]})
    if not qa["passed"]:
        uncertainties.append({"code": "qa_violations", "items": qa.get("violations", [])})

    suffix = ".tex" if materials["is_latex"] else ".txt" if materials["format"] == "txt" else ".md"
    artifacts = {k: str((out_p / f).resolve()) for k, f in {
        "polished_manuscript": "polished_manuscript" + suffix, "revision_report_md": "revision_report.md",
        "polished_doc_json": "polished_doc.json", "glossary_json": "glossary.json", "qa_report_json": "qa_report.json",
        "patch_diff": "patch.diff", "guarded_sentences_json": "guarded_sentences.json", "source_map_json": "source_map.json",
    }.items()}
    source_map = [{
        "sent_id": row["sent_id"], "source_location": row["source_location"], "source_text": row["source_text"],
        "protected_tokens": row["protected_tokens"],
    } for row in doc["sentences"]]
    changes = [{k: row[k] for k in ("sent_id", "line", "source_location", "source_text", "polished_text", "edit_type", "rationale")}
               for row in doc["sentences"] if row["source_text"] != row["polished_text"]]

    fields = set(normalized_key(f) for f in contract_fields(contract))
    bilingual = bool(meta.get("bilingual_required") or case.get("bilingual_required") or
                     fields & {"bilingual", "bilingual_alignment", "bilingual_table", "translations"})
    aligned = []
    for row in doc["sentences"]:
        translation = reviewed.get(row["sent_id"], {}).get("translation", "")
        if isinstance(translation, str) and translation.strip():
            aligned.append({k: row[k] for k in ("sent_id", "source_location", "source_text", "polished_text")}
                           | {"translation": translation.strip()})
        elif bilingual and row["role"] == "prose":
            uncertainties.append({"code": "missing_translation", "sent_id": row["sent_id"]})
    if aligned:
        translation_issues = [issue for row in aligned for issue in verify_translation(row, glossary)]
        uncertainties.extend(translation_issues)
        qa["checks"]["translation_fact_preservation"] = not any(i.get("blocking") for i in translation_issues)
        qa["passed"] = all(qa["checks"].values())
        write_json_atomic(out_p / "bilingual_alignment.json", aligned)
        write_text_lf(out_p / "bilingual_alignment.md", render_alignment(aligned))
        artifacts["bilingual_json"] = str((out_p / "bilingual_alignment.json").resolve())
        artifacts["bilingual_md"] = str((out_p / "bilingual_alignment.md").resolve())

    formats = [meta.get("deliverables", []), case.get("output_format", ""), case.get("deliverables", []), contract]
    if materials["format"] == "docx" or ".docx" in [Path(str(s)).suffix.lower() for s in materials["sources"]] or "docx" in json.dumps(formats, ensure_ascii=False).lower():
        try:
            from export_docx import export_docx
            docx_artifacts, docx_warnings = export_docx(in_p, materials, pol["polished_manuscript"], out_p)
            artifacts.update(docx_artifacts)
            uncertainties.extend(docx_warnings)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            uncertainties.append({"code": "docx_export_failed", "message": str(exc), "blocking": True})

    paths = {k: Path(v) for k, v in artifacts.items() if isinstance(v, str)}
    write_text_lf(paths["polished_manuscript"], pol["polished_manuscript"])
    write_text_lf(paths["patch_diff"], pol["patch_diff"])
    for k, obj in (("source_map_json", source_map), ("guarded_sentences_json", guarded),
                   ("glossary_json", glossary), ("qa_report_json", qa)):
        write_json_atomic(paths[k], obj)
    failed_checks = [k for k, passed in qa["checks"].items() if not passed]
    result.update({
        "status": doc["status"], "metrics": dict(qa["metrics"]), "artifacts": artifacts, "change_records": changes,
        "polished_text": pol["polished_manuscript"], "source_map": source_map, "glossary": glossary,
        "verification": {"passed": qa["passed"], "check_count": len(qa["checks"]), "failed_checks": failed_checks},
    })
    result["metrics"].update(reviewed_prose_count=sum(s["sent_id"] in reviewed for s in guarded["sentences"] if s["role"] == "prose"),
                             total_prose_count=sum(s["role"] == "prose" for s in guarded["sentences"]))
    self_review = [u for u in uncertainties if isinstance(u, dict) and u.get("code") == "author_review"]
    catalog = {key: result["polished_text"] for key in ("full_text", "manuscript", "polished_manuscript",
               "revised_text", "revised_manuscript", "polished_draft", "final_text", "edited_text", "polished_version")}
    catalog.update({key: changes for key in ("revision_log", "change_log", "edit_log", "changes", "edits", "revision_history")})
    catalog.update({key: source_map for key in ("evidence", "provenance", "source_mapping", "fact_source_map")})
    catalog.update({key: doc["sentences"] for key in ("sentence_edits", "sentence_comparison", "sentence_revisions", "sentences")})
    catalog.update({key: aligned for key in ("bilingual", "bilingual_alignment", "bilingual_table", "translations")})
    catalog.update({key: glossary["entries"] for key in ("terminology", "term_table", "term_glossary", "terminology_table")})
    catalog.update({"limitations": uncertainties, "warnings": qa.get("warnings", []), "human_review_items": self_review,
                    "qa_report": qa, "patch_diff": pol["patch_diff"], **artifacts})
    # For semantic manuscript names the contract receives text; explicit *_path keys point at the file.
    catalog["polished_manuscript"] = pol["polished_manuscript"]
    catalog["polished_manuscript_path"] = artifacts["polished_manuscript"]
    result["summary"] = f"{doc['sentence_count']} source spans, {len(changes)} source-linked edits."
    issues = fill_contract(result, contract, supplied, catalog)
    uncertainties.extend(issues)
    blocking = (not qa["passed"] or doc["status"] == "partial" or bool(edit_issues or glossary.get("conflicts") or issues)
                or any(isinstance(u, dict) and (u.get("blocking") or u.get("code") == "missing_translation")
                       for u in uncertainties))
    result["status"] = doc["status"] = "partial" if blocking else "completed"
    result["summary"] = (f"{doc['sentence_count']} sentences, {doc['edited_count']} edited; "
                         f"{len(changes)} source-linked changes. Status: {result['status']}.")
    result["verification"]["contract_issues"] = issues
    result["verification"]["passed"] = not blocking
    doc["uncertainties"] = uncertainties
    write_json_atomic(paths["polished_doc_json"], doc)
    write_text_lf(paths["revision_report_md"], render_revision_report(doc))
    publish(result, out_p, contract)
    return {"result": result, "polished_doc": doc, "guarded": guarded, "glossary": glossary, "qa_report": qa}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--work-dir", default="/app/work")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--case", dest="case_path")
    parser.add_argument("--source", action="append", dest="sources", help="Select one manuscript path relative to the input root; repeat for chapters.")
    parser.add_argument("--register")
    parser.add_argument("--contract-json", "--contract-values", dest="contract_json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--edits-json")
    mode.add_argument("--demo-rules", "--rules-only", action="store_true", help="Local deterministic demonstration only.")
    out = run_polishing_pipeline(**vars(parser.parse_args()))
    if "prepared" in out:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    result = out["result"]
    print(json.dumps({"case_id": result["case_id"], "status": result["status"], "summary": result["summary"],
                      "uncertainty_count": len(result["uncertainties"]), "verification": result["verification"]}, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
