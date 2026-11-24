#!/usr/bin/env python3
"""Harbor end-to-end orchestrator for writing-09-methods-experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from draft_methods_section import (
    render_methods_chapter,
    render_parameter_table_md,
    render_repro_and_questions_md,
)
from exp_utils import write_json_atomic, write_text_lf
from extract_experiment_facts import extract_experiment_facts
from verify_methods_exp import verify_methods_and_experiments


def run_methods_pipeline(
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    *,
    case_path: Optional[str | Path] = None,
    draft_file: Optional[str | Path] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Run extract_experiment_facts -> draft 6-section chapter & param table -> 6-check QA -> result.json."""
    in_p = Path(input_dir)
    work_p = Path(work_dir) / "methods"
    out_p = Path(output_dir)
    work_p.mkdir(parents=True, exist_ok=True)
    out_p.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    elif (in_p / "case.json").exists():
        case_data = json.loads((in_p / "case.json").read_text(encoding="utf-8"))

    case_id = str(case_data.get("case_id", "local_methods_case"))
    lang = str(language or case_data.get("language") or "en")
    is_zh = lang.lower().startswith("zh")

    extracted = extract_experiment_facts(in_p, case_data)
    facts = extracted["facts"]
    param_table = extracted["parameter_table"]

    facts_p = work_p / "experiment_facts.json"
    params_p = work_p / "parameter_table.json"
    write_json_atomic(facts_p, facts)
    write_json_atomic(params_p, param_table)

    if not facts["scanned_sources"]:
        res_obj = {
            "schema_version": "writing-09-methods-result/v1",
            "challenge_id": "product-arena-05-64faa4a2",
            "case_id": case_id,
            "status": "refused",
            "language": lang,
            "summary": "Refused: no experimental configs, logs, scripts, or specs found under input directory.",
            "metrics": {"parameter_count": 0, "conflict_count": 0, "step_count": 0, "dataset_count": 0, "baseline_count": 0, "missing_repro_count": 6, "parameter_prose_consistency": 1.0},
            "artifacts": {"experiment_facts_json": str(facts_p), "parameter_table_json": str(params_p)},
            "sources": [],
            "change_records": [],
            "uncertainties": [{"code": "empty_experimental_inputs"}],
            "author_questions": facts["author_questions"],
            "verification": {"passed": False, "check_count": 6, "failed_checks": ["empty_inputs"]},
        }
        write_json_atomic(out_p / "result.json", res_obj)
        return {"result": res_obj}

    if draft_file and Path(draft_file).exists():
        chapter_md = Path(draft_file).read_text(encoding="utf-8", errors="replace")
    else:
        chapter_md = render_methods_chapter(facts, param_table, language=lang)

    table_md = render_parameter_table_md(param_table, is_zh=is_zh)
    repro_md = render_repro_and_questions_md(facts, is_zh=is_zh)

    ch_p = out_p / "methods_and_experiments.md"
    tb_p = out_p / "parameter_table.md"
    rp_p = out_p / "reproducibility_and_questions.md"
    write_text_lf(ch_p, chapter_md)
    write_text_lf(tb_p, table_md)
    write_text_lf(rp_p, repro_md)

    # Honest status determination before running verifier
    has_gaps_or_conflicts = bool(facts["conflicts"] or facts["missing_repro_fields"] or not facts["steps"] or not facts["baselines"] or not facts["datasets"])
    prelim_status = "partial" if has_gaps_or_conflicts else "completed"

    qa_rep = verify_methods_and_experiments(
        chapter_md,
        repro_md,
        facts,
        param_table,
        status_claimed=prelim_status,
        language=lang,
    )
    qa_p = work_p / "qa_report.json"
    write_json_atomic(qa_p, qa_rep)

    final_status = "completed" if (prelim_status == "completed" and qa_rep["passed"]) else "partial"
    uncertainties = list(facts["conflicts"])
    if facts["missing_repro_fields"]:
        uncertainties.append({"code": "missing_reproducibility_fields", "fields": facts["missing_repro_fields"]})
    if qa_rep["violations"]:
        uncertainties.append({"code": "qa_violations", "items": qa_rep["violations"]})

    res_obj = {
        "schema_version": "writing-09-methods-result/v1",
        "challenge_id": "product-arena-05-64faa4a2",
        "case_id": case_id,
        "status": final_status,
        "language": lang,
        "summary": (
            f"Status={final_status} ({lang}): {param_table['parameter_count']} params extracted from {len(facts['scanned_sources'])} sources, "
            f"{len(facts['conflicts'])} conflicts, {len(facts['author_questions'])} author questions, consistency={qa_rep['metrics']['parameter_prose_consistency']}."
        ),
        "metrics": qa_rep["metrics"],
        "artifacts": {
            "experiment_facts_json": str(facts_p),
            "parameter_table_json": str(params_p),
            "methods_and_experiments_md": str(ch_p),
            "parameter_table_md": str(tb_p),
            "reproducibility_and_questions_md": str(rp_p),
            "qa_report_json": str(qa_p),
        },
        "sources": [{"file": sf} for sf in facts["scanned_sources"]],
        "change_records": [{"action": "synced_parameter_table", "parameter_count": param_table["parameter_count"]}],
        "uncertainties": uncertainties,
        "author_questions": facts["author_questions"],
        "verification": {
            "passed": qa_rep["passed"],
            "check_count": len(qa_rep["checks"]),
            "failed_checks": [k for k, v in qa_rep["checks"].items() if not v],
        },
    }
    write_json_atomic(out_p / "result.json", res_obj)
    return {"result": res_obj, "facts": facts, "param_table": param_table, "chapter_md": chapter_md, "repro_md": repro_md, "qa_report": qa_rep}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end Methods & Experiments pipeline.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--draft-file", default=None)
    parser.add_argument("--language", default=None)
    args = parser.parse_args()

    out = run_methods_pipeline(
        args.input_dir,
        args.work_dir,
        args.output_dir,
        case_path=args.case,
        draft_file=args.draft_file,
        language=args.language,
    )
    print(json.dumps(out["result"], ensure_ascii=False))
    return 0 if (out["result"]["status"] != "refused" and out["result"]["verification"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
