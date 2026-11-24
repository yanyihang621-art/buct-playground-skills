#!/usr/bin/env python3
"""Harbor end-to-end orchestrator for writing-08-intro-related-work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cluster_related_work import cluster_related_works
from derive_gaps_and_contributions import build_gap_and_contribution_map
from draft_intro_related_work import render_intro_and_related_work
from ingest_materials import ingest_materials
from rw_utils import write_json_atomic, write_text_lf
from verify_intro_rw import verify_intro_and_related_work


def run_intro_rw_pipeline(
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    *,
    case_path: Optional[str | Path] = None,
    draft_file: Optional[str | Path] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ingest -> cluster -> derive gaps/contributions -> draft -> verify -> result.json."""
    in_p = Path(input_dir)
    work_p = Path(work_dir) / "intro_rw"
    out_p = Path(output_dir)
    work_p.mkdir(parents=True, exist_ok=True)
    out_p.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    elif (in_p / "case.json").exists():
        case_data = json.loads((in_p / "case.json").read_text(encoding="utf-8"))

    case_id = str(case_data.get("case_id", "local_intro_rw_case"))
    lang = str(language or case_data.get("language") or "en")

    materials = ingest_materials(in_p, case_data)
    mats_p = work_p / "materials_index.json"
    write_json_atomic(mats_p, materials)

    if materials["paper_count"] == 0 and not materials["study_facts"]:
        res_obj = {
            "schema_version": "writing-08-intro-rw-result/v1",
            "challenge_id": "product-arena-04-4b82ad21",
            "case_id": case_id,
            "status": "refused",
            "language": lang,
            "summary": "Refused: missing both literature corpus and study_facts inputs.",
            "metrics": {"literature_count": 0, "route_cluster_count": 0, "gap_count": 0, "contribution_count": 0},
            "artifacts": {"materials_index_json": str(mats_p)},
            "sources": [],
            "change_records": [],
            "uncertainties": materials["material_gaps"],
            "verification": {"passed": False, "check_count": 6, "failed_checks": ["honesty_and_material_sufficiency"]},
        }
        write_json_atomic(out_p / "result.json", res_obj)
        return {"result": res_obj}

    clusters_full = cluster_related_works(materials)
    matrix_md = clusters_full.pop("comparison_matrix_md")
    clus_p = work_p / "related_work_clusters.json"
    matrix_p = out_p / "comparison_matrix.md"
    write_json_atomic(clus_p, clusters_full)
    write_text_lf(matrix_p, matrix_md)

    gc_map = build_gap_and_contribution_map(materials, clusters_full)
    gc_p = work_p / "gap_contribution_map.json"
    write_json_atomic(gc_p, gc_map)

    if draft_file and Path(draft_file).exists():
        draft_md = Path(draft_file).read_text(encoding="utf-8", errors="replace")
    else:
        draft_md = render_intro_and_related_work(materials, clusters_full, gc_map, language=lang)

    draft_p = out_p / "intro_and_related_work.md"
    write_text_lf(draft_p, draft_md)

    qa_rep = verify_intro_and_related_work(draft_md, materials, clusters_full, gc_map, language=lang)
    qa_p = work_p / "qa_report.json"
    write_json_atomic(qa_p, qa_rep)

    status = "completed" if qa_rep["passed"] else "partial"
    uncertainties = list(materials.get("material_gaps", []))
    if qa_rep["violations"]:
        uncertainties.append({"code": "qa_violations", "items": qa_rep["violations"]})

    res_obj = {
        "schema_version": "writing-08-intro-rw-result/v1",
        "challenge_id": "product-arena-04-4b82ad21",
        "case_id": case_id,
        "status": status,
        "language": lang,
        "summary": (
            f"Status={status} ({lang}): clustered {materials['paper_count']} works into {clusters_full['route_cluster_count']} routes, "
            f"{len(gc_map['research_gaps'])} grounded gaps, {len(gc_map['contributions'])} evidence-aligned contributions."
        ),
        "metrics": qa_rep["metrics"],
        "artifacts": {
            "materials_index_json": str(mats_p),
            "related_work_clusters_json": str(clus_p),
            "gap_contribution_map_json": str(gc_p),
            "comparison_matrix_md": str(matrix_p),
            "intro_and_related_work_md": str(draft_p),
            "qa_report_json": str(qa_p),
        },
        "sources": [{"cite_key": p["cite_key"], "title": p["title"], "year": p["year"], "doi": p.get("doi")} for p in materials["papers"]],
        "change_records": [{"action": "route_clustered", "route_count": clusters_full["route_cluster_count"]}],
        "uncertainties": uncertainties,
        "verification": {
            "passed": qa_rep["passed"],
            "check_count": len(qa_rep["checks"]),
            "failed_checks": [k for k, v in qa_rep["checks"].items() if not v],
        },
    }
    write_json_atomic(out_p / "result.json", res_obj)
    return {"result": res_obj, "materials": materials, "clusters": clusters_full, "gc_map": gc_map, "draft_md": draft_md, "qa_report": qa_rep}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end Introduction & Related Work pipeline.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--draft-file", default=None)
    parser.add_argument("--language", default=None)
    args = parser.parse_args()

    out = run_intro_rw_pipeline(
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
