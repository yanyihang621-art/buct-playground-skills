#!/usr/bin/env python3
"""Run one case using the same tools as the interactive review workflow."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from query_kg import execute_query, run_default_query_suite
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
    parser.add_argument("--work", type=Path, default=Path("/app/work/graph"))
    parser.add_argument("--out", type=Path, default=Path("/app/output"))
    parser.add_argument("--reviewed", type=Path, help="Evidence-checked revisions in the SKILL.md format.")
    parser.add_argument("--answers", type=Path, help="Answers to the case-specific output fields.")
    parser.add_argument("--ocr", choices=("auto", "always", "off"), default="auto")
    parser.add_argument("--query-plan", type=Path, help="JSON array of additional query specs for case questions.")
    parser.add_argument("--base-kg", type=Path, help="Prior graph for an incremental case.")
    args = parser.parse_args()
    case = args.case or args.input / "case.json"
    work, out = args.work.resolve(), args.out.resolve()
    if any(folder.is_relative_to(args.input.resolve()) for folder in (work, out)):
        parser.error("Place work and output outside the input directory to keep source discovery reproducible.")
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    result = out / "result.json"
    report = work / "validation_report.json"
    corpus = work / "ingested" / "corpus.json"
    graph = out / "kg.json"
    query_path = out / "executed_queries.json"
    base = ["--base-kg", args.base_kg] if args.base_kg else []
    run("ingest_corpus.py", "--input", args.input, "--case", case, "--out", corpus.parent, "--ocr", args.ocr)
    build = ["--corpus", corpus, "--out", out, "--review-out", work / "review_plan.json", *base]
    run("build_kg.py", *build)
    initial_queries = run_default_query_suite(read(graph))
    write_json_atomic(query_path, {"queries": initial_queries})
    assemble = ["--case", case, "--kg", graph, "--queries", query_path, "--out", result]
    run("build_kg_result.py", *assemble)
    checkpoint(result, "pending_review", "Graph and initial queries saved. Export and validation are pending.")
    if args.reviewed:
        run("build_kg.py", *build, "--claims", args.reviewed)
        initial_queries = run_default_query_suite(read(graph))
    queries = list(initial_queries)
    if args.query_plan:
        kg = read(graph)
        plan = read(args.query_plan)
        if isinstance(plan, dict):
            plan = plan.get("queries")
        if not isinstance(plan, list):
            raise ValueError("--query-plan expects an array or an object with a queries array")
        for i, spec in enumerate(plan, 1):
            if not isinstance(spec, dict) or "query_type" not in spec:
                raise ValueError(f"Query {i} must supply query_type")
            answer = execute_query(kg, query_id=f"case_q_{i}", **spec)
            if answer["answer_status"] == "unsupported_query_type":
                raise ValueError(f"Unknown query type for query {i}")
            queries.append({**answer, "parameters": spec})
    write_json_atomic(query_path, {"queries": queries})
    run("export_kg.py", "--kg", graph, "--queries", query_path, "--out", out)
    answers = ["--override-json", args.answers] if args.answers else []
    run("build_kg_result.py", *assemble, "--graphml", out / "kg.graphml", "--markdown", out / "kg_report.md", *answers)
    report.unlink(missing_ok=True)
    try:
        run("validate_kg.py", "--case", case, "--result", result, "--kg", graph, "--corpus", corpus,
            "--queries", query_path, "--graphml", out / "kg.graphml", "--out", report, *base)
    except subprocess.CalledProcessError as error:
        details = read(report).get("errors", []) if report.is_file() else []
        checkpoint(result, "validation_failed", " | ".join(details[:8]) or "Validator exited before confirming the deliverables.")
        return error.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
