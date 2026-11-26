"""Prepare navigable source texts, then publish the Agent's grounded notes and case answers."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from decimal import Decimal

from audit_notes import MEASUREMENT, _claim_problem, audit_notes
from build_note_result import build_note_result
from ingest_source import ingest_input
from output_contract import contract_fields
from pdf_utils import write_json_atomic, write_text_lf
from render_notes import render_manifest, resolve_evidence, source_elements


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    with path.open("rb") as stream:
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_reader(pid, doc, work):
    """The readable source packet omits parser internals but retains entire paragraphs and table rows."""
    lines = [f"# {doc['metadata'].get('title') or pid}", f"Source: {doc['source_file']}", ""]
    titles = {}
    def walk(sections):
        for sec in sections:
            titles[sec["section_id"]] = sec.get("title", "")
            walk(sec.get("children", []))
    walk(doc.get("sections", []))
    for kind, eid, text, section, page, bbox, native in source_elements(doc):
        if not text:
            continue
        loc = f"page {page}" if page is not None else ""
        if native:
            loc = native.get("json_pointer") or native.get("xml_xpath") or eid
        elif bbox and Path(doc["source_file"]).suffix.lower() in {".md", ".txt"}:
            loc = f"lines {int(bbox[1])}-{int(bbox[3])}"
        lines.extend([f"[{pid} | {section or 'frontmatter'} {titles.get(section, '')} | {kind}={eid} | {loc}]",
                      text.replace("\x00", "�"), ""])
    write_text_lf(work / "reader" / (pid + ".md"), "\n".join(lines))


def lookup(value, pointer):
    if not pointer.startswith("/"):
        raise ValueError(f"Expected a JSON Pointer beginning with /: {pointer}")
    for component in pointer[1:].split("/"):
        key = component.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def leaves(value, path):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from leaves(item, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list) and value:
        for i, item in enumerate(value):
            yield from leaves(item, f"{path}/{i}")
    else:
        yield path, value


def check_cited_value(pointer, value, anchors, derived=None):
    if derived:
        operands = [Decimal(str(v)) for v in derived["operands"]]
        if len(operands) != 2 or not all(v.is_finite() for v in operands):
            raise ValueError(f"{pointer}: provide two finite source measurements")
        for raw in derived["operands"]:
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise ValueError(f"{pointer}: calculation operands must be numbers")
            check_cited_value(pointer, raw, anchors)
        a, b = operands
        ops = {"difference": lambda: a-b, "sum": lambda: a+b, "ratio": lambda: a/b,
               "percent_change": lambda: (a-b)*100/b}
        op = derived.get("operation")
        places = derived.get("decimal_places")
        if op not in ops or (op in ("ratio", "percent_change") and not b):
            raise ValueError(f"{pointer}: invalid calculation")
        answer = ops[op]()
        if places is not None:
            if not isinstance(places, int) or not 0 <= places <= 16:
                raise ValueError(f"{pointer}: invalid decimal_places")
            answer = round(answer, places)
        unit = derived.get("unit", "")
        if unit and not (unit == "%" and op == "percent_change") and not any(unit in q["quote_snippet"] for q in anchors):
            raise ValueError(f"{pointer}: calculation unit is absent from the cited source")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if Decimal(str(value)) != answer:
                raise ValueError(f"{pointer}: answer does not match the verified calculation")
            return
        anchors = [*anchors, {"quote_snippet": format(answer, "f") + " " + unit}]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers = []
        for a in anchors:
            for measurement in MEASUREMENT.finditer(a["quote_snippet"]):
                number = re.search(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?", measurement.group())
                if number:
                    numbers.append(Decimal(number.group()))
        if Decimal(str(value)) not in numbers:
            raise ValueError(f"{pointer}: number is absent from the quoted evidence")
    elif isinstance(value, str):
        issue = _claim_problem(value, [a["quote_snippet"] for a in anchors])
        if issue:
            raise ValueError(f"{pointer}: {issue}")


def review_field_evidence(review, documents):
    fields = dict(review.get("fields", {}))
    cited = {}
    derivations = review.get("derivations", {})
    applied = set()
    for pointer, citations in review.get("field_evidence", {}).items():
        value = lookup(fields, pointer)
        anchors = []
        for citation in citations:
            pid = citation["paper_id"]
            if pid not in documents:
                raise ValueError(f"{pointer}: unknown paper {pid}")
            anchors.extend(resolve_evidence(pid, documents[pid], [citation]))
        if not anchors:
            raise ValueError(f"{pointer}: no cited evidence")
        for leaf, scalar in leaves(value, pointer):
            check_cited_value(leaf, scalar, anchors, derivations.get(leaf))
            if leaf in derivations:
                applied.add(leaf)
        cited[pointer] = anchors
    if set(derivations) != applied:
        raise ValueError("Each derivation must name a cited scalar answer")
    limits = review.get("field_limits", {})
    for pointer, limit in limits.items():
        lookup(fields, pointer)
        if not isinstance(limit, dict) or not limit.get("reason") or not limit.get("checked"):
            raise ValueError(f"{pointer}: describe the missing evidence and the scope checked")
    administrative = {"case_id", "status", "uncertainties", "anomalies", "change_log", "sources", "evidence",
                      "provenance", "artifacts", "files", "verification_ledger"}
    for pointer, value in leaves(fields, ""):
        if not pointer:
            continue
        key = pointer.split("/")[1]
        if key in administrative or re.search(r"(?:^|_)(?:file|files|path|paths)$", key):
            continue
        if not any(pointer == root or pointer.startswith(root + "/") for root in (*cited, *limits)):
            raise ValueError(f"{pointer}: supply field_evidence or explain the unavailable evidence in field_limits")
    return fields, cited


def run(args):
    work, out = args.work.resolve(), args.out.resolve()
    input_dir = args.input.resolve()
    case_path = (args.case or input_dir / "case.json").resolve()
    case = read(case_path)
    manifest_file = work / "ingested" / "ingest_manifest.json"
    if args.stage == "prepare":
        manifest = ingest_input(input_dir, manifest_file.parent, case_path, ocr_mode=args.ocr)
        manifest["source_hashes"] = {str(p): digest(p) for p in
                                     {case_path, *(Path(item["source_file"]) for item in manifest["papers"])}}
        write_json_atomic(manifest_file, manifest)
    else:
        manifest = read(manifest_file)
        if str(case_path) not in manifest.get("source_hashes", {}) or any(
            not Path(p).is_file() or digest(Path(p)) != h for p, h in manifest["source_hashes"].items()
        ):
            raise ValueError("The input case or a source changed. Run prepare again.")
    documents = {p["paper_id"]: read(Path(p["document_json"])) for p in manifest["papers"]}
    if args.stage == "prepare":
        for pid, doc in documents.items():
            export_reader(pid, doc, work)
    review = read(args.review or work / "review.json") if args.stage == "finish" else {}
    if review and review.get("case_id", case.get("case_id")) != case.get("case_id"):
        raise ValueError("Review case_id differs from the source case.")
    fields, cited = review_field_evidence(review, documents)
    if fields.get("case_id", case.get("case_id")) != case.get("case_id"):
        raise ValueError("fields.case_id differs from the source case.")
    limits = review.get("field_limits", {})
    sections, cards = {}, {}
    for pid, paper in review.get("papers", {}).items():
        sections[pid], cards[pid] = paper.get("sections", {}), paper.get("card", {})
    if cited:
        evidence = [{**anchor, "supports": pointer} for pointer, anchors in cited.items() for anchor in anchors]
        if "evidence" in contract_fields(case) and "evidence" not in fields:
            fields["evidence"] = evidence
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as temp:
        stage = Path(temp)
        notes_dir = stage / "notes"
        bundle = render_manifest(manifest, notes_dir, sections, cards)
        if args.stage == "prepare" and bundle["status"] == "success":
            fields["status"] = "partial"
        if limits:
            bundle["uncertainties"].extend({"code": "evidence_gap", "field": k, **v} for k, v in limits.items())
            if fields.get("status", bundle["status"]) == "success":
                fields["status"] = "partial"
            if bundle["status"] == "success":
                bundle["status"] = "partial"
            write_json_atomic(notes_dir / "structured_notes.json", bundle)
        if args.stage == "finish" and not (any(sections.values()) or any(cards.values()) or cited or limits) and documents:
            if bundle["status"] == "success":
                raise ValueError("Provide a source-backed reading or a case-specific answer in review.json.")
        result = build_note_result(case, bundle, list(documents.values()),
                                   (notes_dir / "structured_notes.md").read_text(encoding="utf-8"),
                                   (notes_dir / "comparison_matrix.md").read_text(encoding="utf-8"),
                                   fields)
        report = audit_notes(case, result, documents, bundle)
        report["phase"] = args.stage
        report["source_bundle_status"] = bundle["status"]
        report["task_status_reason"] = review.get("status_reason")
        report["reviewed_sections"] = {pid: list(rows) for pid, rows in sections.items()}
        report["reviewed_fields"] = list(cited)
        write_json_atomic(work / "latest_audit.json", report)
        if args.stage == "finish" and not report["pass"]:
            raise ValueError(f"Final audit failed: {report['errors'][:8]}. See {work / 'latest_audit.json'}")
        write_json_atomic(notes_dir / "audit_report.json", report)
        write_json_atomic(notes_dir / "case_evidence.json", cited)
        write_json_atomic(notes_dir / "case_limits.json", limits)
        write_json_atomic(notes_dir / "case_derivations.json", review.get("derivations", {}))
        write_json_atomic(stage / "result.json", result)
        for file in sorted(stage.rglob("*"), key=lambda p: p.name == "result.json"):
            if file.is_file():
                target = out / file.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as fd:
                    with file.open("rb") as source:
                        shutil.copyfileobj(source, fd)
                os.replace(fd.name, target)
    return {"phase": args.stage, "papers": len(documents), "status": result.get("status", bundle["status"]),
            "audit": report["pass"], "fields": contract_fields(case), "result": str(out / "result.json"),
            "source_reading_dir": str(work / "reader"), "contract_or_audit_errors": report["errors"][:5]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stage", choices=["prepare", "finish"])
    p.add_argument("--input", type=Path, default=Path("/input"))
    p.add_argument("--case", type=Path)
    p.add_argument("--work", type=Path, default=Path("/app/work/reading"))
    p.add_argument("--out", type=Path, default=Path("/app/output"))
    p.add_argument("--review", type=Path)
    p.add_argument("--ocr", choices=["auto", "always", "off"], default="auto")
    args = p.parse_args()
    print(json.dumps(run(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
