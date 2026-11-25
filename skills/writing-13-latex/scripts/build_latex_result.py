#!/usr/bin/env python3
"""Build the original and edited LaTeX projects and publish self-contained evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from build_evidence import apply_contract, copy_sources, source_diff
from compile_latex import compile_latex_project
from inspect_project import inspect_latex_project
from lint_and_fix_tex import lint_and_fix_project
from tex_utils import write_json_atomic
from verify_manuscript import verify


def read_object(path):
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return obj


def empty_build():
    return {
        "manifest": {"schema_version": "writing-13-build-manifest/v1", "status": "failed",
                     "engine": "undetermined", "bib_tool": None, "main_tex": None,
                     "passes_executed": [], "page_count": 0, "pdf_sha256": None,
                     "rendered_unresolved_markers": [], "previews": [], "output_pdf": None,
                     "repro_command": None},
        "diagnostics": {"schema_version": "writing-13-diagnostics/v1", "error_count": 1,
                        "warning_count": 0, "diagnostics": [], "rerun_requested": False,
                        "has_undefined_refs": False, "has_undefined_cites": False,
                        "overfull_hbox_count": 0, "root_causes": [{"category": "missing_main_tex"}]},
    }


def select_engine(pmap, hint):
    engine = str(hint or pmap.get("recommended_engine", "pdflatex")).strip().lower()
    if engine not in {"pdflatex", "xelatex", "lualatex"}:
        raise ValueError(f"Unsupported TeX engine {engine!r}")
    return engine


def safe_main_hint(root, hint):
    if hint:
        path = (root / hint).resolve()
        if not path.is_relative_to(root):
            raise ValueError("main_tex must be inside the source project")
        return path.relative_to(root).as_posix()
    return None


def copy_build_logs(build, root, pmap, evidence, name):
    if not pmap.get("found"):
        return
    main = root / pmap["main_tex"]
    paths = []
    for ext in (".log", ".blg"):
        log = main.with_suffix(ext)
        if log.is_file():
            dest = evidence / (name + ext)
            shutil.copy2(log, dest)
            paths.append("evidence/" + dest.name)
    build["diagnostics"]["log_file"] = next((p for p in paths if p.endswith(".log")), None)
    build["diagnostics"]["log_files"] = paths
    build["manifest"]["build_logs"] = paths


def build_measurements(build, found):
    """Report observed builds of the two source revisions on this toolchain."""
    if not found:
        return {"attempted": False, "reason": "No matching TeX entrypoint in this revision."}
    manifest, diag = build["manifest"], build["diagnostics"]
    return {
        "attempted": bool(manifest["passes_executed"]),
        "entrypoint": manifest["main_tex"],
        "engine": manifest["engine"],
        "version": manifest.get("engine_version"),
        "status": manifest["status"],
        "passes": [{"tool": p["tool"], "exit_code": p["returncode"]} for p in manifest["passes_executed"]],
        "error_count": diag["error_count"],
        "warnings": diag["warning_count"],
        "overfull_hboxes": diag["overfull_hbox_count"],
        "page_boundary_check": manifest.get("layout_audit", {}).get("status", "unverified"),
        "unresolved_keys": diag.get("unresolved_keys", []),
        "page_count": manifest["page_count"],
        "pdf_sha256": manifest["pdf_sha256"],
        "diagnostic_logs": manifest.get("build_logs", []),
    }


def review_data(report):
    for field in ("source_mapping", "task_checks", "change_records", "uncertainties", "confirmed_comments"):
        entries = report.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError(f"report.{field} must be a list of objects")
    if not isinstance(report.get("contract_values", {}), dict):
        raise ValueError("report.contract_values must be an object")


def copy_unbuilt_project(source, dest, hint):
    pmap = inspect_latex_project(source, main_tex_hint=safe_main_hint(source, hint))
    omit = []
    if pmap.get("found"):
        main = Path(pmap["main_tex"])
        # A supplied previous build is evidence, never proof that this run compiled.
        omit = [main.with_suffix(ext) for ext in (".pdf", ".log", ".blg", ".out")]
    copy_sources(source, dest, skip_case=True, omit=omit)
    return pmap


def run_latex_pipeline(input_dir, work_dir, output_dir, *, case_path=None, auto_fix=None,
                       render_previews=None, project_dir=None, report_path=None,
                       main_tex=None, engine=None, stage=None, material_dir=None):
    input_root, work, output = (Path(p).resolve() for p in (input_dir, work_dir, output_dir))
    edited = Path(project_dir).resolve() if project_dir else input_root
    for source in (input_root, edited):
        if not source.is_dir() or work.is_relative_to(source) or output.is_relative_to(source):
            raise ValueError("Use a source directory separate from work and output")
    if output == work or work.is_relative_to(output) or any(
        source.is_relative_to(output) for source in (input_root, edited)
    ):
        raise ValueError("Keep compiler work and source trees outside the delivery directory")
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    # A failed retry must leave the last complete result and its files together.
    # The staging tree shares the final filesystem, including when /app/output is a mount.
    with tempfile.TemporaryDirectory(dir=output, prefix=".latex-stage-") as temp:
        transfer = Path(temp)
        run = _build(input_root, edited, work, transfer, case_path=case_path, auto_fix=auto_fix,
                     render_previews=render_previews, report_path=report_path,
                     main_tex=main_tex, engine=engine, stage=stage, material_dir=material_dir)
        publish(transfer, output)
    return run


def publish(stage, output):
    names = [p.name for p in sorted(stage.iterdir()) if p.name != "result.json"]
    old = output / "result.json"
    if old.exists():
        old_artifacts = read_object(old).get("artifacts") or {}
        old_pdf = old_artifacts.get("output_pdf")
        if old_artifacts.get("evidence_checks_json") == "evidence_checks.json" and "evidence_checks.json" not in names:
            names.append("evidence_checks.json")
        if isinstance(old_pdf, str):
            path = (output / old_pdf).resolve()
            if path.parent == output and path.suffix.lower() == ".pdf" and path.name not in names:
                names.append(path.name)
    names.append("result.json")
    backup = stage / ".previous"
    backup.mkdir()
    installed = []
    try:
        for name in names:
            target = output / name
            if target.is_symlink():
                raise ValueError(f"Refusing to publish through a symlink at {target}")
            if target.exists():
                target.replace(backup / name)
            if (stage / name).exists():
                (stage / name).replace(target)
                installed.append(name)
    except BaseException:
        for name in reversed(installed):
            (output / name).replace(stage / name)
        for saved in backup.iterdir():
            saved.replace(output / saved.name)
        raise


def _build(input_root, edited, work, output, *, case_path, auto_fix, render_previews,
           report_path, main_tex, engine, stage, material_dir):
    case_file = Path(case_path) if case_path else input_root / "case.json"
    case = read_object(case_file) if case_file.is_file() else {}
    report = read_object(Path(report_path)) if report_path else {}
    review_data(report)
    do_fix = case.get("auto_fix", True) if auto_fix is None else auto_fix
    do_prev = case.get("render_previews", True) if render_previews is None else render_previews
    entry = main_tex or case.get("main_tex")
    engine_hint = engine or case.get("engine")
    evidence, previews, delivery = (output / name for name in ("evidence", "previews", "latex_project"))
    for directory in (evidence, previews, delivery):
        directory.mkdir()

    with tempfile.TemporaryDirectory(dir=work, prefix="latex-build-") as tmp:
        scratch = Path(tmp)
        original = scratch / "original"
        final = scratch / "final"
        # Keep one pristine snapshot for the diff. Both TeX invocations run on copies.
        original_map = copy_unbuilt_project(input_root, original, case.get("main_tex") or entry)
        pmap = copy_unbuilt_project(edited, final, entry)
        init = empty_build()
        if original_map.get("found"):
            baseline = scratch / "baseline"
            shutil.copytree(original, baseline)
            init = compile_latex_project(
                baseline, original_map["main_tex"], engine=select_engine(original_map, engine_hint),
                bib_tool=original_map.get("bib_tool"))
            copy_build_logs(init, baseline, original_map, evidence, "initial")
        lint = {"changes_applied": [], "static_issues": []}
        built = empty_build()
        if pmap.get("found"):
            lint = lint_and_fix_project(final, pmap["main_tex"], apply_fixes=bool(do_fix))
            if lint["changes_applied"]:
                # A preamble repair can move all subsequent source-line anchors.
                lint["static_issues"] = lint_and_fix_project(
                    final, pmap["main_tex"], apply_fixes=False)["static_issues"]
            pmap = inspect_latex_project(final, main_tex_hint=pmap["main_tex"])
        # Capture sources before the first TeX command can write any auxiliary files.
        shutil.copytree(final, delivery, dirs_exist_ok=True)
        file_changes, source_index = source_diff(original, delivery, evidence)
        if pmap.get("found"):
            built = compile_latex_project(
                final, pmap["main_tex"], engine=select_engine(pmap, engine_hint),
                bib_tool=pmap.get("bib_tool"), previews_dir=previews if do_prev else None)
            copy_build_logs(built, final, pmap, evidence, "final")
        manifest, diagnostics = built["manifest"], built["diagnostics"]
        if manifest.get("output_pdf"):
            pdf = Path(manifest["output_pdf"])
            shutil.copy2(pdf, output / pdf.name)
            manifest["output_pdf"] = pdf.name
        manifest["previews"] = [Path(p).relative_to(output).as_posix() for p in manifest.get("previews", [])]
        if manifest.get("repro_script"):
            repro = Path(manifest["repro_script"])
            if repro.is_file():
                repro_out = delivery / repro.relative_to(final)
                if repro_out.exists() and repro.read_bytes() != repro_out.read_bytes():
                    repro_out = repro_out.with_name("reproduce_submission.sh")
                shutil.copy2(repro, repro_out)
                manifest["repro_script"] = repro_out.relative_to(output).as_posix()
            else:
                manifest["repro_script"] = None

    pmap["project_dir"] = "latex_project"
    manifest["packages_detected"] = pmap.get("packages", [])
    write_json_atomic(evidence / "project_map.json", pmap)
    write_json_atomic(evidence / "diagnostics_initial.json", init["diagnostics"])
    write_json_atomic(evidence / "diagnostics_final.json", diagnostics)
    write_json_atomic(evidence / "build_manifest.json", manifest)
    write_json_atomic(evidence / "source_inventory.json", source_index)
    write_json_atomic(evidence / "file_changes.json", file_changes)
    write_json_atomic(evidence / "build_comparison.json", {
        "scope": "Observed original and revised source builds in the current environment.",
        "original": build_measurements(init, original_map.get("found")),
        "revised": build_measurements(built, pmap.get("found")),
        "changed_source_files": [item["file"] for item in file_changes],
    })
    if report_path:
        write_json_atomic(evidence / "case_review.json", report)
    checks = report.get("task_checks", [])
    unresolved = manifest.get("rendered_unresolved_markers") or []
    uncertainties = list(report.get("uncertainties", []))
    layout = manifest.get("layout_audit", {})
    layout_pages = sorted({item["page"] for item in layout.get("findings", [])})
    if layout_pages:
        uncertainties.append({"code": "page_boundary_findings", "status": layout["status"],
                              "pages": layout_pages, "source": "evidence/build_manifest.json"})
    for item in pmap.get("missing_assets", []):
        uncertainties.append({"code": "missing_asset", "detail": item})
    confirmations = {(row.get("file"), str(row.get("line"))) for row in report.get("confirmed_comments", [])
                     if isinstance(row.get("reason"), str) and row["reason"].strip()}
    open_percents = []
    for item in lint["static_issues"]:
        if item.get("rule") == "ambiguous_numeric_percent":
            if (item.get("file"), str(item.get("line"))) in confirmations:
                continue
            open_percents.append(item)
        uncertainties.append({"code": "static_lint_issue", **item})
    for item in diagnostics.get("root_causes", []):
        uncertainties.append({"code": "compile_root_cause", **item})
    if unresolved:
        uncertainties.append({"code": "rendered_unresolved_markers", "items": unresolved})
    if diagnostics.get("has_undefined_refs") or diagnostics.get("has_undefined_cites"):
        uncertainties.append({"code": "unresolved_log_reference", "source": "evidence/diagnostics_final.json"})
    blockers = [c for c in checks if c.get("passed") is False or
                (c.get("status") and str(c["status"]).lower() not in
                 {"pass", "passed", "ok", "verified", "completed", "met", "satisfied", "not_applicable", "n/a"})]
    if blockers:
        uncertainties.append({"code": "open_task_requirements", "checks": blockers})
    pdf_ok = bool(manifest.get("output_pdf") and manifest["page_count"] > 0)
    previews_ok = not do_prev or bool(manifest["previews"])
    if pdf_ok and not previews_ok:
        uncertainties.append({"code": "preview_unavailable"})
    passed = (manifest["status"] == "completed" and pdf_ok and previews_ok and
              not diagnostics["error_count"] and not unresolved and not blockers and not open_percents and
              not diagnostics.get("has_undefined_refs") and not diagnostics.get("has_undefined_cites"))
    status = "completed" if passed else "failed" if manifest["status"] == "failed" else "partial"
    artifacts = {
        "main_tex": f"latex_project/{pmap['main_tex']}" if pmap.get("found") else None,
        "latex_project_dir": "latex_project",
        "output_pdf": manifest.get("output_pdf"),
        "previews": manifest["previews"],
        "build_repro_sh": manifest.get("repro_script"),
        "build_manifest_json": "evidence/build_manifest.json",
        "project_map_json": "evidence/project_map.json",
        "diagnostics_initial_json": "evidence/diagnostics_initial.json",
        "diagnostics_final_json": "evidence/diagnostics_final.json",
        "patch_diff": "evidence/patch.diff",
        "source_inventory_json": "evidence/source_inventory.json",
        "build_comparison_json": "evidence/build_comparison.json",
        "file_changes_json": "evidence/file_changes.json",
    }
    if report_path:
        artifacts["case_review_json"] = "evidence/case_review.json"
    evidence_checks = None
    if stage is not None and report_path:
        source_dir = Path(material_dir).resolve() if material_dir else case_file.resolve().parent
        final_pdf = output / artifacts["output_pdf"] if artifacts["output_pdf"] else None
        try:
            evidence_checks = verify(source_dir, Path(stage).resolve(), delivery,
                                     Path(report_path), final_pdf)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            evidence_checks = {"checked": 0, "verified": 0, "needs_manual_review": 0,
                               "failed": 1, "error": str(exc), "checks": []}
        write_json_atomic(output / "evidence_checks.json", evidence_checks)
        artifacts["evidence_checks_json"] = "evidence_checks.json"
        if evidence_checks["failed"]:
            uncertainties.append({"code": "source_evidence_check_failed", "path": "evidence_checks.json"})
            passed = False
            if status == "completed":
                status = "partial"
        if evidence_checks["needs_manual_review"]:
            uncertainties.append({"code": "source_evidence_needs_manual_review",
                                  "count": evidence_checks["needs_manual_review"],
                                  "path": "evidence_checks.json"})
    initial_errors = init["diagnostics"]["error_count"] if original_map.get("found") else None
    result = {
        "schema_version": "writing-13-latex-result/v1",
        "challenge_id": "product-arena-07-865de7e4",
        "case_id": case.get("case_id", "local_latex_case"),
        "status": status,
        "main_result": {"editable_source": artifacts["main_tex"], "pdf": artifacts["output_pdf"]},
        "metrics": {"initial_error_count": initial_errors,
                    "final_error_count": diagnostics["error_count"],
                    "overfull_hbox_count": diagnostics["overfull_hbox_count"],
                    "rendered_unresolved_count": len(unresolved), "page_count": manifest["page_count"],
                    "layout_review_pages": layout_pages,
                    "passes_count": len(manifest["passes_executed"])},
        "artifacts": artifacts,
        "source_mapping": report.get("source_mapping", []),
        "task_checks": checks,
        "change_records": report.get("change_records", []) + lint["changes_applied"] + file_changes,
        "uncertainties": uncertainties,
        "summary": report.get("summary") or (
            f"{manifest.get('engine', 'LaTeX')}: {manifest['page_count']} pages, "
            f"{diagnostics['error_count']} final errors, {len(file_changes)} changed files."),
        "verification": {"passed": bool(passed), "pdf_nonempty": pdf_ok,
                         "page_boundary_check": layout.get("status", "unverified"),
                         "zero_unresolved_rendered": pdf_ok and not unresolved,
                         "repro_script_emitted": bool(manifest.get("repro_script")),
                         "unreviewed_numeric_percent": len(open_percents),
                         "reviewed_task_checks": len(checks),
                         "source_mapping_entries": len(report.get("source_mapping", []))},
    }
    if evidence_checks is not None:
        result["verification"]["source_evidence"] = {
            k: evidence_checks[k] for k in ("checked", "verified", "failed", "needs_manual_review")
        }
    apply_contract(result, case.get("output_contract"), report.get("contract_values", {}))
    write_json_atomic(output / "result.json", result)
    return {"result": result, "manifest": manifest, "diagnostics": diagnostics,
            "init_diagnostics": init["diagnostics"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="/input", help="Original source tree for before/after evidence")
    parser.add_argument("--work-dir", default="/app/work")
    parser.add_argument("--output-dir", default="/app/output")
    parser.add_argument("--case", help="Original case.json, even if sources were unpacked separately")
    parser.add_argument("--project-dir", help="Source tree already revised to meet the actual task")
    parser.add_argument("--report", help="JSON with task_checks, source_mapping, change_records, uncertainties and contract_values")
    parser.add_argument("--stage", help="Prepared case directory containing materials.json and baseline/")
    parser.add_argument("--material-dir", help="Original materials directory for citation checks (defaults to case.json directory)")
    parser.add_argument("--main-tex", help="Entrypoint relative to revised project root")
    parser.add_argument("--engine", choices=("pdflatex", "xelatex", "lualatex"))
    parser.add_argument("--no-fix", action="store_true")
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args()
    run = run_latex_pipeline(args.input_dir, args.work_dir, args.output_dir, case_path=args.case,
                             auto_fix=False if args.no_fix else None,
                             render_previews=False if args.no_previews else None,
                             project_dir=args.project_dir, report_path=args.report,
                             main_tex=args.main_tex, engine=args.engine,
                             stage=args.stage, material_dir=args.material_dir)
    print(json.dumps({"case_id": run["result"]["case_id"], "status": run["result"]["status"],
                      "metrics": run["result"]["metrics"], "verification": run["result"]["verification"]},
                     ensure_ascii=False))
    return 0 if run["result"]["verification"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
