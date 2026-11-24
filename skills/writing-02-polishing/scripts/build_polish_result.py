#!/usr/bin/env python3
"""Harbor end-to-end orchestrator for writing-02-polishing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_manuscript_terms import extract_manuscript_glossary
from polish_sentences import polish_guarded_sentences
from segment_and_guard import segment_and_guard_manuscript
from text_utils import write_json_atomic, write_text_lf
from verify_polish import verify_polish

DOC_EXTS = {".md", ".tex", ".txt"}
IGNORE_NAMES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}


def _discover_manuscript(in_dir: Path, case_data: Dict[str, Any]) -> Optional[Path]:
    if case_data.get("manuscript_file"):
        p = Path(str(case_data["manuscript_file"]))
        return p if p.exists() else (in_dir / p.name)
    if in_dir.is_dir():
        for p in sorted(in_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in DOC_EXTS and p.name.lower() not in IGNORE_NAMES:
                return p
    return None


def run_polishing_pipeline(
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    *,
    case_path: Optional[str | Path] = None,
    edits_json: Optional[str | Path] = None,
    register: Optional[str] = None,
) -> Dict[str, Any]:
    """Run glossary extraction -> sentence guard -> polishing -> 6-check QA -> result.json."""
    in_p = Path(input_dir)
    work_p = Path(work_dir) / "polishing"
    out_p = Path(output_dir)
    work_p.mkdir(parents=True, exist_ok=True)
    out_p.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    elif (in_p / "case.json").exists():
        case_data = json.loads((in_p / "case.json").read_text(encoding="utf-8"))

    case_id = str(case_data.get("case_id", "local_polish_case"))
    reg = str(register or case_data.get("register") or "journal_article")
    ms_path = _discover_manuscript(in_p, case_data)

    if ms_path is None or not ms_path.exists():
        res_obj = {
            "schema_version": "writing-02-polishing-result/v1",
            "challenge_id": "product-arena-02-7051d92d",
            "case_id": case_id,
            "status": "refused",
            "register": reg,
            "summary": "Refused: no manuscript input file (.md/.tex/.txt) found.",
            "metrics": {},
            "artifacts": {},
            "change_records": [],
            "uncertainties": [{"code": "missing_manuscript"}],
            "verification": {"passed": False, "check_count": 6, "failed_checks": ["missing_manuscript"]},
        }
        write_json_atomic(out_p / "result.json", res_obj)
        return {"result": res_obj}

    raw_text = ms_path.read_text(encoding="utf-8", errors="replace")
    is_latex = ms_path.suffix.lower() == ".tex"

    custom_glossary = {str(k): str(v) for k, v in (case_data.get("custom_glossary") or {}).items()}
    conflicts_in = case_data.get("glossary_overrides") or []
    glossary = extract_manuscript_glossary(raw_text, custom_glossary=custom_glossary, glossary_conflicts_check=conflicts_in)
    glos_p = work_p / "glossary.json"
    write_json_atomic(glos_p, glossary)

    guarded = segment_and_guard_manuscript(raw_text, is_latex=is_latex)
    grd_p = work_p / "guarded_sentences.json"
    write_json_atomic(grd_p, guarded)

    ov = json.loads(Path(edits_json).read_text(encoding="utf-8")) if (edits_json and Path(edits_json).exists()) else None
    pol_res = polish_guarded_sentences(guarded, glossary, register=reg, edits_override=ov)
    pol_doc = pol_res["polished_doc"]

    qa_rep = verify_polish(pol_doc, guarded, glossary)
    pol_doc["qa_report"] = qa_rep

    if not qa_rep["passed"] or glossary.get("conflicts"):
        pol_doc["status"] = "partial"

    pol_doc_p = work_p / "polished_doc.json"
    qa_p = work_p / "qa_report.json"
    diff_p = work_p / "patch.diff"
    ext = ".tex" if is_latex else ".md"
    out_ms_p = out_p / f"polished_manuscript{ext}"
    out_rep_p = out_p / "revision_report.md"

    write_json_atomic(pol_doc_p, pol_doc)
    write_json_atomic(qa_p, qa_rep)
    write_text_lf(diff_p, pol_res["patch_diff"])
    write_text_lf(out_ms_p, pol_res["polished_manuscript"])
    write_text_lf(out_rep_p, pol_res["revision_report_md"])

    change_records: List[Dict[str, Any]] = [
        {
            "sent_id": s["sent_id"],
            "line": s["line"],
            "edit_type": s["edit_type"],
            "source_text": s["source_text"],
            "polished_text": s["polished_text"],
            "rationale": s["rationale"],
        }
        for s in pol_doc["sentences"]
        if s["polished_text"] != s["source_text"]
    ]
    uncertainties: List[Dict[str, Any]] = list(glossary.get("conflicts", []))
    if qa_rep["violations"]:
        uncertainties.append({"code": "qa_violations", "items": qa_rep["violations"]})

    res_obj = {
        "schema_version": "writing-02-polishing-result/v1",
        "challenge_id": "product-arena-02-7051d92d",
        "case_id": case_id,
        "status": pol_doc["status"],
        "register": reg,
        "summary": (
            f"Status={pol_doc['status']} ({reg}): {pol_doc['sentence_count']} sentences processed, "
            f"{pol_doc['edited_count']} edited; token_invariance={qa_rep['metrics']['token_invariance']}."
        ),
        "metrics": qa_rep["metrics"],
        "artifacts": {
            "glossary_json": str(glos_p),
            "guarded_sentences_json": str(grd_p),
            "polished_doc_json": str(pol_doc_p),
            "polished_manuscript": str(out_ms_p),
            "revision_report_md": str(out_rep_p),
            "patch_diff": str(diff_p),
            "qa_report_json": str(qa_p),
        },
        "change_records": change_records,
        "uncertainties": uncertainties,
        "verification": {
            "passed": qa_rep["passed"],
            "check_count": len(qa_rep["checks"]),
            "failed_checks": [k for k, v in qa_rep["checks"].items() if not v],
        },
    }
    write_json_atomic(out_p / "result.json", res_obj)
    return {"result": res_obj, "polished_doc": pol_doc, "guarded": guarded, "glossary": glossary, "qa_report": qa_rep}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end academic polishing pipeline.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--edits-json", default=None)
    parser.add_argument("--register", default=None)
    args = parser.parse_args()

    out = run_polishing_pipeline(
        args.input_dir,
        args.work_dir,
        args.output_dir,
        case_path=args.case,
        edits_json=args.edits_json,
        register=args.register,
    )
    print(json.dumps(out["result"], ensure_ascii=False))
    return 0 if out["result"]["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
