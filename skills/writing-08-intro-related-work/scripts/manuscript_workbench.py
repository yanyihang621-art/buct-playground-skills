#!/usr/bin/env python3
"""Prepare a paged reading packet and compile an evidence-annotated author manuscript."""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import tempfile

from build_intro_rw_result import run_intro_rw_pipeline
from citation_audit import Citations, REFERENCES
from ingest_materials import ingest_materials
from output_contract import contract_specs, required_contract_fields
from rw_utils import write_json_atomic, write_text_lf

MARK = re.compile(r"<!--\s*ev\b(.*?)-->", re.S)
KINDS = {"background", "comparison", "related_work", "gap", "contribution", "limit"}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def case_view(raw):
    """Unwrap unambiguous case envelopes while leaving scientific JSON untouched."""
    node = raw
    inherited = {}
    for _ in range(5):
        if isinstance(node, list) and len(node) == 1:
            node = node[0]
            continue
        if not isinstance(node, dict):
            return inherited
        inherited.update({k: v for k, v in node.items() if k in
                          ("case_id", "output_contract", "instructions", "language", "output_language")})
        if "case_id" not in inherited and "output_contract" in node and isinstance(node.get("id"), (str, int)):
            inherited["case_id"] = node["id"]
        children = [node[k] for k in ("case", "case_data", "input_case", "input", "payload", "data")
                    if isinstance(node.get(k), dict)
                    and ("case_id" in node[k] or "output_contract" in node[k])]
        if len(children) != 1:
            return {**inherited, **node}
        node = children[0]
    return inherited


def state_of(root):
    entries = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        checksum = sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(block)
        entries.append((rel.as_posix(), checksum.hexdigest()))
    return entries


def find_case(root, requested):
    if requested:
        return requested.resolve()
    default = root / "case.json"
    if default.is_file():
        return default
    matches = []
    for path in sorted(root.glob("*.json")):
        if path.stat().st_size > 2_000_000 or path.is_symlink():
            continue
        try:
            obj = case_view(load_json(path))
        except ValueError:
            continue
        if "case_id" in obj or "output_contract" in obj:
            matches.append(path)
    if len(matches) > 1:
        raise ValueError("More than one case description found. Select one with --case FILE.")
    return matches[0] if matches else default


def fragments(text):
    # Exact contiguous excerpts. Keep table rows and wrapped measurements together.
    for line in text.splitlines():
        line = line.strip()
        if len(line) <= 450:
            if line:
                yield line
            continue
        while len(line) > 450:
            choices = [m.end() for m in re.finditer(r"[。！？；]|[.!?;]\s+|\s+", line[:450])]
            end = choices[-1] if choices and choices[-1] > 180 else 450
            yield line[:end].strip()
            line = line[end:].strip()
        if line:
            yield line


def make_units(index):
    papers_by_source = defaultdict(list)
    for paper in index["papers"]:
        for sid in paper.get("source_ids", []):
            papers_by_source[sid].append(paper["cite_key"])
    units = {}
    for passage in index["passages"]:
        sid = passage["source_id"]
        for quote in fragments(passage["text"]):
            eid = "E" + digest([passage["path"], passage["locator"], quote])[:9]
            row = {k: passage[k] for k in ("source_id", "path", "locator", "role")}
            row.update(quote=quote, papers=papers_by_source[sid])
            if eid in units and units[eid] != row:
                raise ValueError("Evidence ID collision; split the documents and prepare again.")
            units[eid] = row
    return units


def format_unit(eid, row):
    keys = " " + ", ".join("@" + k for k in row["papers"]) if row["papers"] else ""
    return f"{eid} [{row['role']}{keys}] {row['path']} · {row['locator']}\n  {row['quote']}"


def save_checkpoint(out, case, result):
    """Write an honest early delivery even when the case permits only named fields."""
    out.mkdir(parents=True, exist_ok=True)
    contract = case.get("output_contract")
    for field in required_contract_fields(contract):
        result.setdefault(field, None)
    write_json_atomic(out / "editorial_report.json", result)
    if isinstance(contract, dict) and contract.get("additionalProperties") is False:
        allowed = set(contract_specs(contract)) | {"case_id"}
        result = {k:v for k,v in result.items() if k in allowed}
    write_json_atomic(out / "result.json", result)


def prepare(args):
    work, root = args.work_dir.resolve(), args.input_dir.resolve()
    if not root.is_dir() or work.is_relative_to(root) or args.output_dir.resolve().is_relative_to(root):
        raise ValueError("Use an existing input directory and separate work/output directories outside it.")
    case_path = find_case(root, args.case)
    if args.case and not case_path.exists():
        raise ValueError(f"Case file not found: {case_path}")
    raw = load_json(case_path) if case_path.is_file() else {}
    case = case_view(raw)
    case.setdefault("case_id", None)
    external = case_path.is_file() and not case_path.is_relative_to(root) and not (root / "case.json").exists()
    index = ingest_materials(root, raw if external and isinstance(raw, dict) else None)
    units = make_units(index)
    work.mkdir(parents=True, exist_ok=True)
    write_json_atomic(work / "materials_index.json", index)
    write_json_atomic(work / "units.json", units)
    write_json_atomic(work / "case_view.json", case)
    metadata = {"root": str(root), "case_path": str(case_path), "case_digest": digest(raw),
                "input_signature": digest(state_of(root)), "units": digest(units), "index": digest(index)}
    write_json_atomic(work / "packet.json", metadata)
    groups = defaultdict(list)
    for eid, unit in units.items():
        groups[unit["path"]].append(eid)
    lines = [f"Case {case.get('case_id', '(no case_id supplied)')}",
             "Required result fields: " + ", ".join(sorted(required_contract_fields(case.get("output_contract")))),
             "Read case_view.json for the entire contract and the original task for formatting constraints.",
             "Use read --source PATH or read --find TEXT to retrieve exact evidence beyond these excerpts.",
             f"{len(groups)} readable files, {len(units)} excerpt IDs, {index['paper_count']} indexed works."]
    if case.get("instructions"):
        lines.append("Instructions (first 900 characters): " + str(case["instructions"])[:900])
    for p in index["papers"][:24]:
        lines.append(f"@{p['cite_key']} · {p.get('title')} · {p.get('year') or '?'}")
    for src, ids in list(groups.items())[:36]:
        lines.append(f"{src} ({len(ids)} excerpts)")
    # Interleave documents so a long paper cannot displace the author protocol or other works.
    used = 0
    paths = sorted(groups, key=lambda k: (units[groups[k][0]]["role"] not in ("study", "instruction"), k))
    for turn in range(4):
        for path in paths:
            ids = groups[path]
            if turn >= len(ids):
                continue
            eid = ids[turn]
            excerpt = format_unit(eid, units[eid])
            if used + len(excerpt) <= 8000:
                lines.append(excerpt)
                used += len(excerpt)
    if index["material_gaps"] or index["review_notes"]:
        lines.append("Inspect material_gaps/review_notes in materials_index.json.")
    brief = "\n\n".join(lines)
    write_text_lf(work / "reading.md", brief + "\n")
    if not (args.output_dir / "result.json").exists():
        save_checkpoint(args.output_dir, case, {
            "case_id": case.get("case_id"), "status": "partial", "draft_mode": "reading_packet",
            "sources": index["sources"], "references": index["papers"], "manuscript": "",
            "uncertainties": [*index["material_gaps"], {"code":"awaiting_authored_manuscript",
                                "message":"Sources indexed. The manuscript has not yet been authored."}]})
    print(brief)
    return 0


def read(args):
    units = load_json(args.work_dir / "units.json")
    needle = (args.find or "").casefold()
    rows = [(eid, row) for eid, row in units.items()
            if (not args.source or args.source in (eid, row["source_id"], row["path"]))
            and needle in " ".join((row["path"], row["locator"], row["quote"])).casefold()]
    if args.offset < 0 or args.limit <= 0:
        raise ValueError("offset must be >=0 and limit must be positive.")
    for eid, row in rows[args.offset:args.offset + args.limit]:
        print(format_unit(eid, row))
    print(f"Matches: {len(rows)}; returned {len(rows[args.offset:args.offset + args.limit])}.")
    return 0


def card_refs(ids, units):
    refs = []
    for eid in ids:
        if eid not in units:
            raise ValueError(f"Unknown evidence ID {eid}; use read to locate a current excerpt.")
        row = units[eid]
        if row["role"] == "instruction":
            raise ValueError(f"{eid} points to an instruction. Use the scientific source.")
        refs.append({"source_id": row["source_id"], "quote": row["quote"]})
    if not refs:
        raise ValueError("Attach at least one excerpt ID.")
    return refs


def compile_draft(raw, notes, index, units, case):
    """A marker binds the preceding prose since the preceding marker or blank line."""
    errors, prev = [], 0
    spans = defaultdict(list)
    plain = MARK.sub("", raw)
    cards = notes.setdefault("paper_annotations", [])
    papers = {p["cite_key"]: p.copy() for p in index["papers"]}
    key_by_unit = defaultdict(set)
    for card in cards:
        key = card.get("cite_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Each paper annotation needs a cite_key.")
        if "units" in card:
            ids = card.pop("units")
            card["evidence"] = card_refs(ids, units)
            for eid in ids:
                key_by_unit[eid].add(key)
        papers[key] = {**papers.get(key, {}), **card}
    parts = REFERENCES.split(plain, maxsplit=1)
    citations = Citations(list(papers.values()), parts[1] if len(parts) == 2 else "")
    for match in MARK.finditer(raw):
        before = raw[prev:match.start()].rstrip()
        prev = match.end()
        line_no = raw.count("\n", 0, match.start()) + 1
        claim = re.split(r"\n[ \t]*\n", before)[-1].strip()
        claim = re.sub(r"^(?:#{1,6}[^\n]*\n)+", "", claim).strip()
        try:
            parts = match[1].split()
            if not parts or not claim:
                raise ValueError("Put the marker immediately after the sentence it describes.")
            role, _, link = parts[0].partition(":")
            if role not in KINDS:
                raise ValueError("Unknown role: " + role)
            ids = [x for x in parts[1:] if not x.startswith("@")]
            refs = card_refs(ids, units)
            explicit = {x[1:] for x in parts[1:] if x.startswith("@")}
            keys = explicit or citations.resolve(claim)[0]
            if not keys:
                keys = set().union(*(key_by_unit[eid] or set(units[eid]["papers"]) for eid in ids))
            row = {"kind": role, "claim": claim, "evidence": refs}
            spans[role].append(claim)
            if link:
                spans[role + ":" + link].append(claim)
            if role == "limit":
                notes.setdefault("interpretation_limits", []).append(
                    {"kind": link or "scope", "manuscript_span": claim, "evidence": refs})
                continue
            if keys and role != "contribution":
                row["cite_keys"] = sorted(keys)
            notes.setdefault("claim_evidence", []).append(row)
            if role == "gap":
                gaps = notes.setdefault("research_gaps", [])
                gaps.append({"gap_id": link or f"G{len(gaps)+1}", "gap_statement": claim,
                             "derived_from_cite_keys": sorted(keys), "evidence": refs})
            if role == "contribution":
                item = {"claim": claim, "evidence": refs}
                if link:
                    item["addresses_gap_id"] = link
                notes.setdefault("contributions", []).append(item)
        except ValueError as exc:
            errors.append({"code": "annotation_error", "line": line_no, "message": str(exc), "blocking": True})
    if "<!--ev" in plain or re.search(r"<!--\s*ev\b", plain):
        raise ValueError("An evidence marker is unclosed. Close it with -->.")
    specs = contract_specs(case.get("output_contract"))
    for field, selector in notes.pop("contract_bindings", {}).items():
        if not isinstance(selector, str) or not spans.get(selector) or field not in specs:
            errors.append({"code": "unresolved_binding", "field": field, "selector": selector, "blocking": True})
            continue
        spec = specs[field]
        type_name = spec.get("type") if isinstance(spec, dict) else spec
        value = spans[selector] if type_name == "array" else "\n\n".join(spans[selector])
        notes.setdefault("contract_fields", {}).setdefault(field, value)
    notes.setdefault("uncertainties", []).extend(errors)
    return plain, notes


def recover(args, draft, error):
    """Keep existing deliveries. On first failure save a readable draft and explicit partial."""
    work, out = args.work_dir, args.output_dir
    work.mkdir(parents=True, exist_ok=True)
    issue = {"code": "workbench_failure", "message": str(error), "blocking": True}
    write_json_atomic(work / "last_error.json", issue)
    if not draft.strip():
        return
    try:
        case_file = find_case(args.input_dir.resolve(), args.case)
        case = case_view(load_json(case_file)) if case_file.is_file() else {}
    except (OSError, ValueError):
        case = {}
    if (out / "result.json").exists():
        prior = out / "editorial_report.json"
        try:
            saved = load_json(prior) if prior.is_file() else {}
        except (ValueError, OSError):
            return
        if saved.get("draft_mode") != "reading_packet" or saved.get("case_id") != case.get("case_id"):
            return
    out.mkdir(parents=True, exist_ok=True)
    write_text_lf(out / "recovered_draft.md", draft)
    result = {"case_id": case.get("case_id"), "status": "partial", "manuscript": draft,
              "artifacts": {"recovered_draft_md": "recovered_draft.md"}, "uncertainties": [issue]}
    save_checkpoint(out, case, result)


def finish(args):
    work, out = args.work_dir, args.output_dir
    raw = args.draft.read_text(encoding="utf-8-sig")
    clean = ""
    try:
        meta = load_json(work / "packet.json")
        root = args.input_dir.resolve()
        case_path = find_case(root, args.case)
        source_case = load_json(case_path) if case_path.is_file() else {}
        if (str(root) != meta["root"] or str(case_path) != meta["case_path"] or
                digest(source_case) != meta["case_digest"] or digest(state_of(root)) != meta["input_signature"]):
            raise ValueError("Input changed or belongs to another case. Run prepare on this input and check excerpt IDs.")
        units, index = load_json(work / "units.json"), load_json(work / "materials_index.json")
        if digest(units) != meta["units"] or digest(index) != meta["index"]:
            raise ValueError("Reading packet changed; rerun prepare.")
        case = case_view(source_case)
        case.setdefault("case_id", None)
        if case != load_json(work / "case_view.json"):
            raise ValueError("Prepared case metadata changed; rerun prepare.")
        clean = MARK.sub("", raw)
        notes = load_json(args.notes) if args.notes else {}
        if not isinstance(notes, dict):
            raise ValueError("Notes must be a JSON object.")
        clean, notes = compile_draft(raw, notes, index, units, case)
        if case["case_id"] in (None, ""):
            notes.setdefault("uncertainties", []).append({"code":"missing_case_id", "blocking":True,
                         "message":"No case identifier was supplied."})
        draft = work / "compiled.md"
        analysis = work / "analysis_compiled.json"
        write_text_lf(draft, clean)
        write_json_atomic(analysis, notes)
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out.parent, prefix=".intro-rw-") as tempdir:
            stage = Path(tempdir)
            run = run_intro_rw_pipeline(root, work / "build", stage,
                        case_path=work / "case_view.json", materials_file=work / "materials_index.json",
                        draft_file=draft, analysis_file=analysis, language=args.language, formats=args.format)
            result = run["result"]
            if not (stage / "editorial_report.json").exists():
                write_json_atomic(stage / "editorial_report.json", result)
            # Move the manifest last. Never erase a prior completed delivery on a failed rerun.
            previous = out / "editorial_report.json"
            if not previous.exists():
                previous = out / "result.json"
            same_completed = False
            if previous.exists():
                prior = load_json(previous)
                if prior.get("case_id") != result["case_id"]:
                    raise ValueError("Output contains a different case. Choose a separate output directory.")
                same_completed = prior.get("case_id") == result["case_id"] and prior.get("status") == "completed"
            retain = same_completed and result["status"] != "completed"
            destination = work / "partial_attempt" if retain else out
            if retain:
                destination.mkdir(parents=True, exist_ok=True)
            paths = sorted((p for p in stage.rglob("*") if p.is_file()), key=lambda p:p == stage / "result.json")
            for p in paths:
                target = destination / p.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, target.with_suffix(target.suffix + ".tmp"))
                target.with_suffix(target.suffix + ".tmp").replace(target)
            print(json.dumps({"status":result["status"],"case_id":result["case_id"],
                              "delivery":str(destination / "result.json"),
                              "failed":result["verification"]["failed_checks"],
                              "repair":result["uncertainties"][:3]}, ensure_ascii=False))
            return 0 if result["status"] == "completed" else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        recover(args, clean, exc)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "read", "finish"):
        p = subs.add_parser(command)
        p.add_argument("--work-dir", type=Path, default=Path("/app/work/intro_packet"))
        p.add_argument("--input-dir", type=Path, default=Path("/input"))
        p.add_argument("--output-dir", type=Path, default=Path("/app/output"))
        p.add_argument("--case", type=Path)
        if command == "read":
            p.add_argument("--source")
            p.add_argument("--find")
            p.add_argument("--limit", type=int, default=12)
            p.add_argument("--offset", type=int, default=0)
        if command == "finish":
            p.add_argument("--draft", type=Path, required=True)
            p.add_argument("--notes", type=Path)
            p.add_argument("--language")
            p.add_argument("--format", action="append", default=[], choices=("md","docx","tex","pdf","png"))
    args = parser.parse_args()
    try:
        return {"prepare":prepare, "read":read, "finish":finish}[args.command](args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"{args.command}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
