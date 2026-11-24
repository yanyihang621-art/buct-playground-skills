#!/usr/bin/env python3
"""Harbor end-to-end orchestrator for writing-13-latex."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compile_latex import compile_latex_project
from inspect_project import inspect_latex_project
from lint_and_fix_tex import lint_and_fix_tex_file
from tex_utils import write_json_atomic, write_text_lf


def run_latex_pipeline(
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    *,
    case_path: Optional[str | Path] = None,
    auto_fix: Optional[bool] = None,
    render_previews: Optional[bool] = None,
) -> Dict[str, Any]:
    """Inspect -> initial compile/diagnose -> deterministic safe autofix -> final reproducible compile -> result.json."""
    in_p = Path(input_dir)
    work_p = Path(work_dir) / "latex"
    out_p = Path(output_dir)
    proj_out = out_p / "latex_project"
    previews_dir = out_p / "previews"
    work_p.mkdir(parents=True, exist_ok=True)
    out_p.mkdir(parents=True, exist_ok=True)

    case_data: Dict[str, Any] = {}
    if case_path and Path(case_path).exists():
        case_data = json.loads(Path(case_path).read_text(encoding="utf-8"))
    elif (in_p / "case.json").exists():
        case_data = json.loads((in_p / "case.json").read_text(encoding="utf-8"))

    case_id = str(case_data.get("case_id", "local_latex_case"))
    main_hint = case_data.get("main_tex")
    do_fix = bool(case_data.get("auto_fix", True)) if auto_fix is None else auto_fix
    do_prev = bool(case_data.get("render_previews", True)) if render_previews is None else render_previews

    # Copy input project to editable /app/output/latex_project so originals stay untouched
    if proj_out.exists():
        shutil.rmtree(proj_out)
    proj_out.mkdir(parents=True, exist_ok=True)
    for item in in_p.iterdir():
        if item.name.lower() in ("case.json", "task.md", "resources.yaml", "challenge.json", "config.json"):
            continue
        dst = proj_out / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    pmap = inspect_latex_project(proj_out, main_tex_hint=main_hint)
    pmap_p = work_p / "project_map.json"
    write_json_atomic(pmap_p, pmap)

    if not pmap.get("found"):
        res_obj = {
            "schema_version": "writing-13-latex-result/v1",
            "challenge_id": "product-arena-07-865de7e4",
            "case_id": case_id,
            "status": "failed",
            "summary": "Failed: no .tex entrypoint found under input directory.",
            "metrics": {"initial_error_count": 1, "final_error_count": 1, "overfull_hbox_count": 0, "rendered_unresolved_count": 0, "page_count": 0, "passes_count": 0},
            "artifacts": {"project_map_json": str(pmap_p)},
            "change_records": [],
            "uncertainties": [{"code": "missing_main_tex"}],
            "verification": {"passed": False, "pdf_nonempty": False, "zero_unresolved_rendered": False, "repro_script_emitted": False},
        }
        write_json_atomic(out_p / "result.json", res_obj)
        return {"result": res_obj}

    main_tex = pmap["main_tex"]
    engine = str(case_data.get("engine") or pmap["recommended_engine"])
    bib_tool = pmap.get("bib_tool")

    # Initial diagnostic compile
    init_res = compile_latex_project(proj_out, main_tex, engine=engine, bib_tool=bib_tool, previews_dir=None)
    init_diag_p = work_p / "diagnostics_initial.json"
    write_json_atomic(init_diag_p, init_res["diagnostics"])

    # Static lint & safe deterministic autofix across all .tex files if requested or if errors exist
    change_records: List[Dict[str, Any]] = []
    diffs: List[str] = []
    static_issues: List[Dict[str, Any]] = []
    for rel_tex in pmap.get("tex_files", []):
        lres = lint_and_fix_tex_file(proj_out / rel_tex, apply_fixes=do_fix)
        change_records.extend(lres["changes_applied"])
        static_issues.extend(lres["static_issues"])
        if lres["patch_diff"]:
            diffs.append(lres["patch_diff"])

    patch_p = work_p / "patch.diff"
    write_text_lf(patch_p, "\n".join(diffs) + ("\n" if diffs else ""))

    # Final reproducible compile + preview render
    if change_records or do_prev:
        final_res = compile_latex_project(
            proj_out,
            main_tex,
            engine=engine,
            bib_tool=bib_tool,
            previews_dir=previews_dir if do_prev else None,
        )
    else:
        final_res = init_res

    final_manifest = final_res["manifest"]
    final_manifest["packages_detected"] = pmap.get("packages", [])
    final_diag = final_res["diagnostics"]

    manifest_p = work_p / "build_manifest.json"
    final_diag_p = work_p / "diagnostics_final.json"
    write_json_atomic(manifest_p, final_manifest)
    write_json_atomic(final_diag_p, final_diag)

    # Copy top-level compiled PDF to /app/output/<stem>.pdf for clean Harbor access
    top_pdf: Optional[Path] = None
    if final_manifest.get("output_pdf") and Path(final_manifest["output_pdf"]).exists():
        src_pdf = Path(final_manifest["output_pdf"])
        top_pdf = out_p / src_pdf.name
        if src_pdf.resolve() != top_pdf.resolve():
            shutil.copy2(src_pdf, top_pdf)

    uncertainties: List[Dict[str, Any]] = []
    for ma in pmap.get("missing_assets", []):
        uncertainties.append({"code": "missing_asset", **ma})
    for si in static_issues:
        uncertainties.append({"code": "static_lint_issue", **si})
    for rc in final_diag.get("root_causes", []):
        uncertainties.append({"code": "compile_root_cause", **rc})
    if final_manifest["rendered_unresolved_markers"]:
        uncertainties.append({"code": "rendered_unresolved_markers", "items": final_manifest["rendered_unresolved_markers"]})

    passed = (
        final_manifest["status"] == "completed"
        and final_diag["error_count"] == 0
        and len(final_manifest["rendered_unresolved_markers"]) == 0
        and final_manifest["page_count"] > 0
    )

    res_obj = {
        "schema_version": "writing-13-latex-result/v1",
        "challenge_id": "product-arena-07-865de7e4",
        "case_id": case_id,
        "status": final_manifest["status"],
        "summary": (
            f"LaTeX build status={final_manifest['status']} ({engine}): "
            f"errors {init_res['diagnostics']['error_count']} -> {final_diag['error_count']}, "
            f"pages={final_manifest['page_count']}, unresolved_markers={len(final_manifest['rendered_unresolved_markers'])}."
        ),
        "metrics": {
            "initial_error_count": init_res["diagnostics"]["error_count"],
            "final_error_count": final_diag["error_count"],
            "overfull_hbox_count": final_diag["overfull_hbox_count"],
            "rendered_unresolved_count": len(final_manifest["rendered_unresolved_markers"]),
            "page_count": final_manifest["page_count"],
            "passes_count": len(final_manifest["passes_executed"]),
        },
        "artifacts": {
            "project_map_json": str(pmap_p),
            "diagnostics_initial_json": str(init_diag_p),
            "diagnostics_final_json": str(final_diag_p),
            "patch_diff": str(patch_p),
            "build_manifest_json": str(manifest_p),
            "build_repro_sh": final_manifest.get("repro_script"),
            "latex_project_dir": str(proj_out),
            "output_pdf": str(top_pdf) if top_pdf else None,
            "previews": final_manifest.get("previews", []),
        },
        "change_records": change_records,
        "uncertainties": uncertainties,
        "verification": {
            "passed": passed,
            "pdf_nonempty": final_manifest["page_count"] > 0,
            "zero_unresolved_rendered": len(final_manifest["rendered_unresolved_markers"]) == 0,
            "repro_script_emitted": bool(final_manifest.get("repro_script") and Path(final_manifest["repro_script"]).exists()),
        },
    }
    write_json_atomic(out_p / "result.json", res_obj)
    return {"result": res_obj, "manifest": final_manifest, "diagnostics": final_diag, "init_diagnostics": init_res["diagnostics"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end LaTeX diagnosis, repair, and reproducible build.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case", default=None)
    parser.add_argument("--no-fix", action="store_true")
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args()

    out = run_latex_pipeline(
        args.input_dir,
        args.work_dir,
        args.output_dir,
        case_path=args.case,
        auto_fix=not args.no_fix,
        render_previews=not args.no_previews,
    )
    print(json.dumps(out["result"], ensure_ascii=False))
    return 0 if out["result"]["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
