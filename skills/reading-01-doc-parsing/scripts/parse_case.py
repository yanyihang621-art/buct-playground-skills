#!/usr/bin/env python3
"""Parse the case's primary PDF and supplements, keeping source-qualified checkpoints."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import fitz

from build_result import build_result, publish_result
from output_contract import contract_fields, contract_issues, strict_fields
from pdf_utils import write_json_atomic
from validate_output import validate_output


def preview_document(path):
    """Capture actual first-page text and boxes before costly layout analysis or OCR."""
    doc = {"source_file": str(path), "status": "partial", "blocks": [], "diagnostics": {},
           "uncertainties": []}
    try:
        with fitz.open(path) as pdf:
            doc["diagnostics"]["page_count"] = len(pdf)
            if pdf.needs_pass:
                doc["status"] = "refused"
                doc["diagnostics"]["encrypted"] = True
                reason = "Password required to read this PDF."
            else:
                reason = "First-page text preview. Layout, tables, OCR and later pages await full parsing."
                for block in pdf[0].get_text("blocks") if len(pdf) else []:
                    if block[6] == 0 and block[4].strip():
                        doc["blocks"].append({"block_id": f"preview_{block[5]}", "role": "preview",
                                              "page": 1, "printed_page": None, "section_id": None,
                                              "bbox": list(block[:4]), "text": block[4].strip(),
                                              "structured_text": block[4].strip()})
    except (OSError, RuntimeError, ValueError) as exc:
        doc["status"] = "refused"
        reason = f"Cannot open PDF: {exc}"
    doc["uncertainties"].append({"code": "source_preview", "pages": [], "message": reason})
    return doc


def finalize(case, result, answers=None):
    keys = contract_fields(case)
    if answers:
        for key in dict.fromkeys(keys + ["task_answers"]):
            if key in answers and key not in {"case_id", "status", "documents", "primary_source_id", "uncertainties"}:
                result[key] = answers[key]
        # Preserve parser diagnostics when an agent documents a missing result,
        # contradictory measurements, or visually reviewed content.
        for note in answers.get("uncertainties", []):
            if isinstance(note, str):
                note = {"code": "source_limitation", "message": note}
            if isinstance(note, dict) and note.get("message") and note not in result["uncertainties"]:
                result["uncertainties"].append(note)
    result["uncertainties"] = [item for item in result["uncertainties"]
                               if item.get("code") not in {"awaiting_task_synthesis", "contract_field_unfilled",
                                                           "contract_field_invalid"}]
    result["uncertainties"].extend(contract_issues(case, result))
    states = {doc["status"] for doc in result["documents"]}
    if len(states) == 1 and "success" not in states:
        result["status"] = next(iter(states))
    else:
        result["status"] = "success" if states == {"success"} and not result["uncertainties"] else "partial"
    return result


def checkpoint(case, entries, out_dir):
    primary, *supplements = entries
    result = dict(primary["result"])
    result["primary_source_id"] = primary["source_id"]
    result["documents"] = [{key: entry[key] for key in ("source_id", "status", "parsed_path")} for entry in entries]
    # Element IDs are local to a PDF. Keep the primary document's descriptive
    # metadata and mark every combined evidence or structure row with its origin.
    for key in ("sources", "evidence", "provenance", "sections", "tables", "figures", "references",
                "paragraphs", "blocks", "footnotes", "equations", "inline_formulas", "uncertainties", "change_log", "anomalies"):
        if key in result and isinstance(result[key], list):
            result[key] = [dict(item, source_id=entry["source_id"]) if isinstance(item, dict) else item
                           for entry in entries for item in entry["result"].get(key, [])]
    if isinstance(result.get("formulas"), list):
        result["formulas"] = [dict(eq, source_id=entry["source_id"])
                              for entry in entries for eq in entry["result"]["formulas"]]
    elif isinstance(result.get("formulas"), dict):
        result["formulas"] = dict(result["formulas"])
        for key in ("display_equations", "inline_formula_spans"):
            result["formulas"][key] = [dict(eq, source_id=entry["source_id"]) for entry in entries
                                       for eq in entry["result"]["formulas"].get(key, [])]
        counts = {}
        for entry in entries:
            for metric, value in entry["result"]["formulas"].get("stats", {}).items():
                counts[metric] = counts.get(metric, 0) + value
        result["formulas"]["stats"] = counts
    if supplements:
        for key in ("full_text", "markdown"):
            if key in result:
                result[key] = "\n\n".join(f"## {e['source_id']}\n\n{e['result'][key]}" for e in entries)
    publish_result(out_dir / "result.json", case, finalize(case, result))
    return result


def parse_case(case_path, pdfs, out_dir, budget, ocr_mode="auto"):
    deadline = time.monotonic() + budget
    case_path = Path(case_path).resolve()
    case = json.loads(case_path.read_text(encoding="utf-8-sig"))
    out_dir = Path(out_dir).resolve()
    materials_dir = out_dir / "documents"
    materials_dir.mkdir(parents=True, exist_ok=True)
    # A new artifact directory prevents interrupted retries reading stale files.
    run_dir = Path(tempfile.mkdtemp(prefix="parse-", dir=materials_dir))
    entries = []
    unique_pdfs = dict.fromkeys((case_path.parent / p).resolve() for p in pdfs)
    for index, pdf in enumerate(unique_pdfs, start=1):
        source = Path(os.path.relpath(pdf, case_path.parent)).as_posix() if pdf.drive == case_path.drive else pdf.as_posix()
        dest = run_dir / f"doc_{index:03d}"
        preview = preview_document(pdf)
        write_json_atomic(dest / "preview.json", preview)
        entries.append({"source_id": source, "pdf": pdf, "dest": dest, "status": preview["status"],
                        "result": build_result(case, preview), "parsed_path": (dest / "preview.json").relative_to(out_dir).as_posix()})
    if not entries:
        raise ValueError("Select at least one source PDF named by the case.")
    result = checkpoint(case, entries, out_dir)
    for index, entry in enumerate(entries):
        remaining = (deadline - time.monotonic()) / (len(entries) - index)
        if remaining <= 0:
            break
        dest = entry["dest"]
        command = [sys.executable, str(Path(__file__).with_name("parse_pdf.py")), "--pdf", str(entry["pdf"]),
                   "--out", str(dest), "--ocr", ocr_mode, "--crops"]
        try:
            subprocess.run(command, check=True, timeout=remaining, stdout=subprocess.DEVNULL)
            parsed = json.loads((dest / "document.json").read_text(encoding="utf-8"))
            markdown = (dest / "full_text.md").read_text(encoding="utf-8")
            document_result = build_result(case, parsed, markdown_text=markdown)
            report = validate_output(case, document_result, parsed)
            write_json_atomic(dest / "validation.json", report)
            entry.update(result=document_result, status=parsed["status"],
                         parsed_path=(dest / "document.json").relative_to(out_dir).as_posix())
            if not report["pass"]:
                entry["status"] = "partial"
                entry["result"]["uncertainties"].append({"code": "validation_error", "message": " ".join(report["errors"])})
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            entry["result"]["uncertainties"].append({"code": "incomplete_parse", "message": str(exc), "pages": []})
        result = checkpoint(case, entries, out_dir)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pdf", action="append", type=Path, help="Primary PDF first, then supplements; repeat per PDF.")
    mode.add_argument("--answers", type=Path, help="Apply JSON case-field values to the current checkpoint without reparsing.")
    parser.add_argument("--budget", type=float, default=600, help="Full-parse time allowance in seconds; 0 retains the preview.")
    parser.add_argument("--ocr", choices=("auto", "off", "always"), default="auto")
    args = parser.parse_args()
    if args.answers:
        path = args.out / "result.json"
        case = json.loads(args.case.read_text(encoding="utf-8-sig"))
        checkpoint_path = path.with_name("parse_manifest.json") if strict_fields(case) is not None else path
        result = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if result["case_id"] != case.get("case_id"):
            parser.error("The checkpoint belongs to a different case.")
        answers = json.loads(args.answers.read_text(encoding="utf-8-sig"))
        if not isinstance(answers, dict):
            parser.error("--answers must contain a JSON object keyed by the case output fields.")
        publish_result(path, case, finalize(case, result, answers))
    else:
        parse_case(args.case, args.pdf, args.out, args.budget, args.ocr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
