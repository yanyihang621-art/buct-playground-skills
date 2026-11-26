#!/usr/bin/env python3
"""Run one case using the same tools as the interactive review workflow."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pdf_utils import write_json_atomic

SCRIPTS = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(script, *arguments):
    subprocess.run([sys.executable, "-B", str(SCRIPTS / script), *map(str, arguments)], check=True)


def checkpoint(path, code, reason):
    result = read(path)
    if result.get("status") == "success":
        result["status"] = "partial"
    result.setdefault("uncertainties", []).append({"code": code, "message": reason})
    write_json_atomic(path, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/input"))
    parser.add_argument("--case", type=Path, help="Defaults to case.json under --input.")
    parser.add_argument("--work", type=Path, default=Path("/app/work/nanopubs"))
    parser.add_argument("--out", type=Path, default=Path("/app/output"))
    parser.add_argument("--reviewed", type=Path, help="Evidence-checked revisions in the SKILL.md format.")
    parser.add_argument("--answers", type=Path, help="Answers to the case-specific output fields.")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    args = parser.parse_args()
    case = args.case or args.input / "case.json"
    work, out = args.work.resolve(), args.out.resolve()
    if any(folder.is_relative_to(args.input.resolve()) for folder in (work, out)):
        parser.error("Place work and output outside the input directory to keep source discovery reproducible.")
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    result = out / "result.json"
    report = work / "validation_report.json"
    manifest = work / "input" / "ingest_manifest.json"
    bundle = work / "nanopubs.json"
    run("ingest_source.py", "--input", args.input, "--case", case, "--out", manifest.parent, "--ocr", args.ocr)
    extract = ["--manifest", manifest, "--out", work]
    run("decompose_assertions.py", *extract)
    build = ["--case", case, "--nanopubs", bundle, "--manifest", manifest, "--out", result]
    run("build_nanopub_result.py", *build)
    checkpoint(result, "pending_review", "Extraction finished. Review and export are pending.")
    if args.reviewed:
        run("decompose_assertions.py", *extract, "--reviewed", args.reviewed)
    run("serialize_nanopubs.py", "--nanopubs", bundle, "--out", out)
    exports = ["--trig", out / "nanopubs.trig", "--jsonld", out / "nanopubs.jsonld",
               "--nquads", out / "nanopubs.nq", "--markdown", out / "nanopubs.md"]
    answers = ["--override-json", args.answers] if args.answers else []
    run("build_nanopub_result.py", *build, *exports, *answers)
    report.unlink(missing_ok=True)
    try:
        run("validate_nanopubs.py", "--case", case, "--manifest", manifest, "--nanopubs", bundle,
            "--result", result, "--nquads", out / "nanopubs.nq", "--out", report)
    except subprocess.CalledProcessError as error:
        details = read(report).get("errors", []) if report.is_file() else []
        checkpoint(result, "validation_failed", " | ".join(details[:8]) or "Validator exited before confirming the deliverables.")
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
