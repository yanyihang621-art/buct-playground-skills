#!/usr/bin/env python3
"""Harbor end-to-end orchestrator for writing-04-references."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dedup_references import deduplicate_library
from io_utils import write_json_atomic, write_text_lf
from parse_bib_sources import parse_library_sources
from sync_manuscript_citations import sync_manuscript
from verify_citations import verify_citations

BIB_EXTENSIONS = {".bib", ".ris", ".enw", ".json"}
DOC_EXTENSIONS = {".md", ".tex", ".txt"}
IGNORE_NAMES = {"case.json", "task.md", "resources.yaml", "readme.md", "challenge.json", "config.json"}


def _discover_inputs(input_dir: Path, case_data: Dict[str, Any]) -> Tuple[Optional[Path], List[Path]]:
    ms_path: Optional[Path] = None
    bib_paths: List[Path] = []

    if case_data.get("manuscript_file"):
        p = Path(str(case_data["manuscript_file"]))
        ms_path = p if p.exists() else (input_dir / p.name)
    if isinstance(case_data.get("bib_files"), list):
        for bf in case_data["bib_files"]:
            p = Path(str(bf))
            bib_paths.append(p if p.exists() else (input_dir / p.name))

    if ms_path is None and input_dir.is_dir():
        for p in sorted(input_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in DOC_EXTENSIONS and p.name.lower() not in IGNORE_NAMES:
                ms_path = p
                break
    if not bib_paths and input_dir.is_dir():
        for p in sorted(input_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in BIB_EXTENSIONS and p.name.lower() not in IGNORE_NAMES:
                bib_paths.append(p)
    return ms_path, bib_paths


def run_reference_pipeline(
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    *,
    case_path: Optional[str | Path] = None,
    style: Optional[str] = None,
    verify_doi_online: bool = False,
) -> Dict[str, Any]:
    """Run parse -> deduplicate -> claim insert & sync -> verify -> Harbor result.json."""
    in_p = Path(input_dir)
    work_p = Path(work_dir) / "references"
    out_p = Path(output_dir)
    work_p.mkdir(parents=True, exist_ok=True)
    out_p.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    elif (in_p / "case.json").exists():
        case_data = json.loads((in_p / "case.json").read_text(encoding="utf-8"))

    target_style = (style or case_data.get("style") or "gbt7714").lower()
    case_id = str(case_data.get("case_id", "local_case"))
    ms_path, bib_paths = _discover_inputs(in_p, case_data)

    raw_lib = parse_library_sources(bib_paths)
    raw_lib_p = work_p / "raw_library.json"
    write_json_atomic(raw_lib_p, raw_lib)

    canon_lib, dedup_rep = deduplicate_library(raw_lib)
    canon_p = work_p / "canonical_library.json"
    dedup_p = work_p / "dedup_report.json"
    write_json_atomic(canon_p, canon_lib)
    write_json_atomic(dedup_p, dedup_rep)

    if ms_path is None or not ms_path.exists() or len(bib_paths) == 0:
        res_obj = {
            "schema_version": "writing-04-references-result/v1",
            "challenge_id": "product-arena-03-429cc14d",
            "case_id": case_id,
            "status": "refused",
            "style": target_style,
            "summary": "Refused: missing manuscript or bibliography source files.",
            "metrics": dedup_rep,
            "artifacts": {"raw_library_json": str(raw_lib_p), "canonical_library_json": str(canon_p)},
            "sources": raw_lib.get("source_files", []),
            "change_records": [],
            "uncertainties": [{"code": "missing_required_inputs", "ms_found": bool(ms_path and ms_path.exists()), "bib_count": len(bib_paths)}],
            "verification": {"passed": False, "check_count": 6, "failed_checks": ["missing_inputs"]},
        }
        write_json_atomic(out_p / "result.json", res_obj)
        return {"result": res_obj}

    ms_text = ms_path.read_text(encoding="utf-8", errors="replace")
    is_latex = ms_path.suffix.lower() == ".tex"
    sync_res = sync_manuscript(
        ms_text,
        canon_lib,
        style=target_style,
        is_latex=is_latex,
        insert_claims=case_data.get("insert_claims"),
    )

    synced_ext = ".tex" if is_latex else ".md"
    synced_ms_p = out_p / f"synced_manuscript{synced_ext}"
    bib_ext = ".bib" if is_latex else ".txt"
    synced_bib_p = out_p / f"bibliography_{target_style}{bib_ext}"
    cmap_p = work_p / "citation_map.json"
    clog_p = work_p / "change_log.json"

    write_text_lf(synced_ms_p, sync_res["synced_manuscript"])
    write_text_lf(synced_bib_p, sync_res["formatted_bibliography"])
    write_json_atomic(cmap_p, sync_res["citation_map"])
    write_json_atomic(clog_p, sync_res["change_log"])

    qa_rep = verify_citations(
        sync_res["synced_manuscript"],
        sync_res["citation_map"],
        canon_lib,
        dedup_rep,
        raw_lib,
        verify_doi_online=verify_doi_online,
    )
    qa_p = work_p / "qa_report.json"
    write_json_atomic(qa_p, qa_rep)

    has_issues = (not qa_rep["passed"]) or bool(qa_rep["uncertainties"])
    final_status = "completed" if qa_rep["passed"] and not qa_rep["uncertainties"] else "partial"

    res_obj = {
        "schema_version": "writing-04-references-result/v1",
        "challenge_id": "product-arena-03-429cc14d",
        "case_id": case_id,
        "status": final_status,
        "style": target_style,
        "summary": (
            f"Status={final_status}: {dedup_rep['raw_record_count']} raw -> {dedup_rep['canonical_record_count']} canonical records "
            f"({dedup_rep['merged_duplicate_count']} merged); {sync_res['citation_map']['cited_count']} citations synced ({target_style})."
        ),
        "metrics": qa_rep["metrics"],
        "artifacts": {
            "raw_library_json": str(raw_lib_p),
            "canonical_library_json": str(canon_p),
            "dedup_report_json": str(dedup_p),
            "citation_map_json": str(cmap_p),
            "change_log_json": str(clog_p),
            "synced_manuscript": str(synced_ms_p),
            "bibliography_formatted": str(synced_bib_p),
            "qa_report_json": str(qa_p),
        },
        "sources": [{"ref_id": r["ref_id"], "cite_key": r["cite_key"], "doi": r.get("doi"), "source_provenance": r["source_provenance"]} for r in sync_res["cited_records"]],
        "change_records": sync_res["change_log"],
        "uncertainties": qa_rep["uncertainties"] + ([{"code": "qa_violations", "items": qa_rep["violations"]}] if has_issues and qa_rep["violations"] else []),
        "verification": {
            "passed": qa_rep["passed"],
            "check_count": len(qa_rep["checks"]),
            "failed_checks": [k for k, v in qa_rep["checks"].items() if not v],
            "doi_verification_mode": qa_rep["doi_verification_mode"],
        },
    }
    write_json_atomic(out_p / "result.json", res_obj)
    return {"result": res_obj, "qa_report": qa_rep, "canonical_library": canon_lib, "dedup_report": dedup_rep, "raw_library": raw_lib, "sync_res": sync_res}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end reference & citation management pipeline.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--style", default=None)
    parser.add_argument("--verify-doi-online", action="store_true")
    args = parser.parse_args()

    out = run_reference_pipeline(
        args.input_dir,
        args.work_dir,
        args.output_dir,
        case_path=args.case,
        style=args.style,
        verify_doi_online=args.verify_doi_online,
    )
    print(json.dumps(out["result"], ensure_ascii=False))
    return 0 if out["result"]["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
