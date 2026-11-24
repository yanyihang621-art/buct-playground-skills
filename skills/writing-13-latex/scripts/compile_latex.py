#!/usr/bin/env python3
"""Reproducible multi-pass LaTeX compiler with PDF/PNG audit and build manifest output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz  # PyMuPDF

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from parse_tex_log import parse_tex_logs
from tex_utils import probe_tool_version, run_cmd, sha256_file, write_json_atomic, write_text_lf

UNRESOLVED_PDF_RE = re.compile(r"\?\?|\[\?\]")


def _audit_pdf(pdf_path: Path, previews_dir: Optional[Path], max_previews: int = 4) -> Dict[str, Any]:
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return {"pdf_nonempty": False, "page_count": 0, "rendered_unresolved_markers": [], "previews": [], "pdf_sha256": None}

    doc = fitz.open(str(pdf_path))
    page_count = doc.page_count
    unresolved: List[str] = []
    previews: List[str] = []

    if previews_dir is not None:
        previews_dir.mkdir(parents=True, exist_ok=True)

    for idx in range(page_count):
        page = doc[idx]
        txt = page.get_text("text") or ""
        for m in UNRESOLVED_PDF_RE.findall(txt):
            unresolved.append(f"p.{idx + 1}:{m}")
        if previews_dir is not None and idx < max_previews:
            pix = page.get_pixmap(dpi=150)
            png_p = previews_dir / f"page_{idx + 1:03d}.png"
            pix.save(str(png_p))
            previews.append(str(png_p))
    doc.close()

    return {
        "pdf_nonempty": page_count > 0,
        "page_count": page_count,
        "rendered_unresolved_markers": unresolved,
        "previews": previews,
        "pdf_sha256": sha256_file(pdf_path),
    }


def compile_latex_project(
    project_dir: Path,
    main_tex: str,
    *,
    engine: str = "pdflatex",
    bib_tool: Optional[str] = None,
    previews_dir: Optional[Path] = None,
    timeout_s: int = 60,
) -> Dict[str, Any]:
    """Execute reproducible LaTeX -> BibTeX/Biber -> LaTeX -> LaTeX compilation and audit PDF."""
    tex_path = project_dir / main_tex
    stem = tex_path.stem
    work_cwd = tex_path.parent
    tex_rel = tex_path.name

    eng_info = probe_tool_version(engine)
    bib_info = probe_tool_version(bib_tool) if bib_tool else None

    if not eng_info["available"]:
        return {
            "manifest": {
                "schema_version": "writing-13-build-manifest/v1",
                "status": "failed",
                "engine": engine,
                "engine_version": "not_installed",
                "bib_tool": bib_tool,
                "bib_tool_version": None,
                "main_tex": main_tex,
                "passes_executed": [],
                "output_pdf": None,
                "pdf_sha256": None,
                "page_count": 0,
                "rendered_unresolved_markers": [],
                "previews": [],
                "repro_command": f"{engine} -interaction=nonstopmode -file-line-error {tex_rel}",
                "packages_detected": [],
            },
            "diagnostics": {
                "schema_version": "writing-13-diagnostics/v1",
                "error_count": 1,
                "warning_count": 0,
                "has_undefined_refs": False,
                "has_undefined_cites": False,
                "overfull_hbox_count": 0,
                "root_causes": [{"category": "missing_engine", "severity": "fatal", "message": f"{engine} not available on PATH"}],
                "diagnostics": [],
            },
        }

    passes: List[Dict[str, Any]] = []
    tex_cmd = [engine, "-interaction=nonstopmode", "-file-line-error", tex_rel]

    # Pass 1: LaTeX
    p1 = run_cmd(tex_cmd, work_cwd, timeout_s=timeout_s)
    passes.append({"step": 1, "tool": engine, **p1})

    # Pass 2: BibTeX / Biber if declared and Pass 1 produced .aux/.bcf without fatal stop
    if bib_tool and bib_info and bib_info["available"]:
        pb = run_cmd([bib_tool, stem], work_cwd, timeout_s=timeout_s)
        passes.append({"step": 2, "tool": bib_tool, **pb})
        # Pass 3 & Pass 4: resolve bibliography citations & cross-references
        p3 = run_cmd(tex_cmd, work_cwd, timeout_s=timeout_s)
        passes.append({"step": 3, "tool": engine, **p3})
        p4 = run_cmd(tex_cmd, work_cwd, timeout_s=timeout_s)
        passes.append({"step": 4, "tool": engine, **p4})
    else:
        # Check if Pass 1 requested rerun (e.g., \ref / TOC)
        log_p = work_cwd / f"{stem}.log"
        diag1 = parse_tex_logs(log_p)
        if diag1["rerun_requested"] or diag1["has_undefined_refs"]:
            p2 = run_cmd(tex_cmd, work_cwd, timeout_s=timeout_s)
            passes.append({"step": 2, "tool": engine, **p2})

    log_path = work_cwd / f"{stem}.log"
    blg_path = work_cwd / f"{stem}.blg"
    diag_final = parse_tex_logs(log_path, blg_path if bib_tool else None)

    pdf_path = work_cwd / f"{stem}.pdf"
    pdf_audit = _audit_pdf(pdf_path, previews_dir=previews_dir)

    last_tex_pass = [p for p in passes if p["tool"] == engine][-1]
    if not pdf_audit["pdf_nonempty"] or last_tex_pass["timed_out"]:
        status = "failed"
    elif diag_final["error_count"] > 0 or pdf_audit["rendered_unresolved_markers"]:
        status = "partial"
    else:
        status = "completed"

    repro_cmds = [f"{engine} -interaction=nonstopmode -file-line-error {tex_rel}"]
    if bib_tool:
        repro_cmds.extend(
            [
                f"{bib_tool} {stem}",
                f"{engine} -interaction=nonstopmode -file-line-error {tex_rel}",
                f"{engine} -interaction=nonstopmode -file-line-error {tex_rel}",
            ]
        )
    elif len(passes) > 1:
        repro_cmds.append(f"{engine} -interaction=nonstopmode -file-line-error {tex_rel}")

    repro_sh = "#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(repro_cmds) + "\n"
    write_text_lf(work_cwd / "build_repro.sh", repro_sh)

    manifest = {
        "schema_version": "writing-13-build-manifest/v1",
        "status": status,
        "engine": engine,
        "engine_version": eng_info["version"],
        "bib_tool": bib_tool,
        "bib_tool_version": bib_info["version"] if bib_info else None,
        "main_tex": main_tex,
        "passes_executed": [{"step": p["step"], "tool": p["tool"], "returncode": p["returncode"], "timed_out": p["timed_out"]} for p in passes],
        "output_pdf": str(pdf_path) if pdf_audit["pdf_nonempty"] else None,
        "pdf_sha256": pdf_audit["pdf_sha256"],
        "page_count": pdf_audit["page_count"],
        "rendered_unresolved_markers": pdf_audit["rendered_unresolved_markers"],
        "previews": pdf_audit["previews"],
        "repro_command": " && ".join(repro_cmds),
        "repro_script": str(work_cwd / "build_repro.sh"),
        "packages_detected": [],
    }
    return {"manifest": manifest, "diagnostics": diag_final}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile a LaTeX project reproducibly and audit the PDF.")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--main-tex", required=True)
    parser.add_argument("--engine", choices=("pdflatex", "xelatex", "lualatex"), default="pdflatex")
    parser.add_argument("--bib-tool", choices=("bibtex", "biber", "none"), default="none")
    parser.add_argument("--previews-dir", default=None)
    parser.add_argument("--out-manifest", required=True)
    parser.add_argument("--out-diag", required=True)
    args = parser.parse_args()

    res = compile_latex_project(
        Path(args.project_dir),
        args.main_tex,
        engine=args.engine,
        bib_tool=None if args.bib_tool == "none" else args.bib_tool,
        previews_dir=Path(args.previews_dir) if args.previews_dir else None,
    )
    write_json_atomic(Path(args.out_manifest), res["manifest"])
    write_json_atomic(Path(args.out_diag), res["diagnostics"])
    print(json.dumps({"status": res["manifest"]["status"], "page_count": res["manifest"]["page_count"], "errors": res["diagnostics"]["error_count"]}, ensure_ascii=False))
    return 0 if res["manifest"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
