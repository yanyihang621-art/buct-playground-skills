#!/usr/bin/env python3
"""Build in a fresh source tree, resolve the bibliography, inspect the PDF, and record evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from xml.etree.ElementTree import ParseError

from inspect_project import inspect_latex_project
from parse_tex_log import parse_tex_logs
from pdf_layout import glyph_findings, poppler_findings, preview_pages
from tex_utils import probe_tool_version, run_cmd, sha256_file, write_json_atomic, write_text_lf

ENGINES = ("pdflatex", "xelatex", "lualatex")
BIB_TOOLS = ("bibtex", "biber")
MARKER = re.compile(r"\?\?|\[\?\]")


def _audit_pdf(path, previews, diagnostics=()):
    audit = dict(pdf_nonempty=False, page_count=0, rendered_unresolved_markers=[], previews=[], pdf_sha256=None,
                 pdf_fonts=[], layout={"method": None, "status": "unverified", "findings": []})
    if not path.is_file() or not path.stat().st_size:
        return audit
    audit["pdf_sha256"] = sha256_file(path)
    if previews:
        previews.mkdir(exist_ok=True, parents=True)
    layout = audit["layout"]
    findings = layout["findings"]
    unresolved_pages = []
    try:
        try:
            import fitz
        except ImportError:
            fitz = None
        if fitz is not None:
            with fitz.open(str(path)) as doc:
                audit["audit_tool"] = "PyMuPDF"
                audit["page_count"] = doc.page_count
                layout["method"] = "painted_glyphs_against_display_box"
                for i, page in enumerate(doc):
                    markers = MARKER.findall(page.get_text())
                    audit["rendered_unresolved_markers"].extend(f"p.{i+1}:{m}" for m in markers)
                    findings.extend(glyph_findings(page))
                    if markers:
                        unresolved_pages.append(i+1)
                    for font in page.get_fonts():
                        name = re.sub(r"^[A-Z]{6}\+", "", font[3])
                        if name and name not in audit["pdf_fonts"]:
                            audit["pdf_fonts"].append(name)
                layout["status"] = ("clipped" if any(f["severity"] == "error" for f in findings)
                                    else "review" if findings else "checked")
                if previews:
                    for number in preview_pages(doc.page_count, findings, unresolved_pages, diagnostics):
                        page = doc[number-1]
                        png = previews / f"page_{number:03}.png"
                        scale = min(1.75, 1500 / max(1, page.rect.width, page.rect.height))
                        page.get_pixmap(matrix=fitz.Matrix(scale, scale)).save(str(png))
                        audit["previews"].append(str(png))
        else:
            def poppler(command):
                return subprocess.run(command, check=True, capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
            audit["audit_tool"] = "Poppler"
            layout.update(method="visible_word_boxes", status="limited",
                          limitation="Visible words can omit off-page glyphs. Review flagged pages against the editable source.")
            info = poppler(["pdfinfo", str(path)])
            pages = re.search(r"^Pages:\s*(\d+)", info, re.M)
            if not pages:
                raise ValueError("pdfinfo did not report a page count")
            audit["page_count"] = int(pages[1])
            pages_text = poppler(["pdftotext", "-enc", "UTF-8", str(path), "-"]).split("\f")
            for i, page_text in enumerate(pages_text, 1):
                markers = MARKER.findall(page_text)
                audit["rendered_unresolved_markers"].extend(f"p.{i}:{m}" for m in markers)
                if markers:
                    unresolved_pages.append(i)
            try:
                bbox_xml = poppler(["pdftotext", "-cropbox", "-bbox", "-enc", "UTF-8", str(path), "-"])
                findings.extend(poppler_findings(bbox_xml))
            except (OSError, ValueError, KeyError, ParseError, subprocess.SubprocessError) as error:
                layout["limitation"] += f" Bounding box extraction unavailable: {error}"
            # TeX box warnings alone never imply physical clipping. A visible edge crossing
            # and a layout warning warrant inspection when the paint trace is unavailable.
            if findings and any(d["category"] in ("overfull_hbox", "overfull_vbox") for d in diagnostics):
                layout["status"] = "review"
            if previews:
                for i in preview_pages(audit["page_count"], findings, unresolved_pages, diagnostics):
                    prefix = previews / f"page_{i:03}"
                    poppler(["pdftoppm", "-f", str(i), "-l", str(i), "-singlefile", "-scale-to", "1500",
                             "-png", str(path), str(prefix)])
                    audit["previews"].append(str(prefix.with_suffix(".png")))
    except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        audit["audit_error"] = str(error)
    audit["pdf_nonempty"] = audit["page_count"] > 0
    return audit


def _evidence_files(fls, work):
    """Fingerprint the class, packages and fonts actually opened, including local templates."""
    deps = []
    seen = set()
    if not fls.is_file():
        return deps
    for line in fls.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("INPUT "):
            continue
        asset = (work / line[6:].strip('"')).resolve()
        if asset in seen or asset.suffix.lower() not in (".cls", ".sty", ".bst", ".bbx", ".cbx", ".pfb", ".ttf", ".otf", ".ttc"):
            continue
        seen.add(asset)
        if asset.is_file():
            item = {"name": asset.name, "sha256": sha256_file(asset)}
            if asset.suffix in (".cls", ".sty"):
                with asset.open("rb") as handle:
                    header = handle.read(32768).decode("latin1")
                version = re.search(r"\\Provides(?:Class|Package)\s*\{[^}]+\}\s*\[([^\]]+)\]", header)
                if version:
                    item["version"] = " ".join(version[1].split())[:200]
            deps.append(item)
    return deps


def compile_latex_project(project_dir: Path, main_tex: str, *, engine: str = "pdflatex",
                          bib_tool: str | None = None, previews_dir: Path | None = None, timeout_s: int = 60,
                          build_cwd_hint: str | Path | None = None) -> dict:
    root = project_dir.resolve()
    source = (root / main_tex).resolve()
    relative = source.relative_to(root) if source.is_relative_to(root) else None
    eng = probe_tool_version(engine) if engine in ENGINES else {"available": False, "version": "unsupported"}
    bib = probe_tool_version(bib_tool) if bib_tool in BIB_TOOLS else None
    manifest = dict(schema_version="writing-13-build-manifest/v1", status="failed", engine=engine, engine_version=eng["version"],
                    bib_tool=bib_tool, bib_tool_version=bib["version"] if bib else None, main_tex=main_tex,
                    passes_executed=[], output_pdf=None, pdf_sha256=None, page_count=0, rendered_unresolved_markers=[],
                    previews=[], repro_command="", repro_script=None, packages_detected=[], dependencies=[], build_logs=[])
    diag = parse_tex_logs(root / "__unused_build_log__")
    result = {"manifest": manifest, "diagnostics": diag}

    def issue(category, msg):
        entry = dict(category=category, severity="error", file=main_tex, line=None, message=msg, is_cascade=False)
        diag["error_count"] += 1
        diag["root_causes"].append(entry)
        diag["diagnostics"].append(entry)

    if relative is None or not source.is_file() or source.suffix.lower() != ".tex":
        issue("invalid_entrypoint", "Select an existing .tex entrypoint inside the project.")
        return result
    if not eng["available"]:
        issue("missing_engine", f"Requested engine {engine!r} is unavailable.")
        return result
    if bib_tool and bib_tool not in BIB_TOOLS:
        issue("unsupported_bibliography_tool", f"Unknown bibliography backend {bib_tool!r}.")
        return result

    try:
        project = inspect_latex_project(root, main_tex, build_cwd_hint=build_cwd_hint, probe_tools=False)
    except ValueError as exc:
        issue("invalid_build_directory", str(exc))
        return result
    build_dir = root / project["build_cwd"]
    tex_file = Path(os.path.relpath(source, build_dir)).as_posix()
    if not tex_file.startswith("../"):
        tex_file = "./" + tex_file
    stem = source.stem
    passes = manifest["passes_executed"]
    flags = ["-interaction=nonstopmode", "-file-line-error", "-recorder"]
    if "miktex" in eng["version"].lower():
        flags += ["-disable-write18", "-disable-installer"]
    else:
        flags.append("-no-shell-escape")
    tex_cmd = [engine, *flags, tex_file]
    deadline = time.monotonic() + min(240, 4 * max(1, timeout_s))
    # Never read a PDF, log or stale aux from an earlier build as new evidence.
    def ignore(directory, names):
        ignored = []
        for name in names:
            path = Path(directory) / name
            if path.is_symlink() or name in (".git", "__pycache__") or name.endswith((".aux", ".bcf", ".blg", ".fls", ".fdb_latexmk", ".run.xml", ".synctex.gz")):
                ignored.append(name)
            elif path.parent in (source.parent, build_dir) and name in {f"{stem}.{ext}" for ext in ("pdf", "log", "out", "toc", "lof", "lot")}:
                ignored.append(name)
            elif bib_tool and path.parent in (source.parent, build_dir) and name == f"{stem}.bbl":
                ignored.append(name)
        return ignored

    with tempfile.TemporaryDirectory(prefix="skill-tex-") as temp:
        tree = Path(temp).resolve() / "src"
        shutil.copytree(root, tree, ignore=ignore)
        work = tree / project["build_cwd"]
        log = work / (stem + ".log")
        blg = work / (stem + ".blg")

        def run(command):
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                item = {"returncode": -1, "timed_out": True, "stderr_tail": "Total build time budget exhausted.", "stdout_tail": ""}
            else:
                try:
                    item = run_cmd(command, work, timeout_s=min(timeout_s, remaining))
                except OSError as exc:
                    item = {"returncode": -1, "timed_out": False, "stderr_tail": str(exc), "stdout_tail": ""}
            record = dict(step=len(passes) + 1, tool=command[0], argv=command,
                          returncode=item["returncode"], timed_out=item["timed_out"])
            if item["returncode"]:
                record["error_tail"] = (item["stderr_tail"] + "\n" + item["stdout_tail"]).strip()[-3000:]
            passes.append(record)
            return item["returncode"] == 0

        tex_ok = run(tex_cmd)
        missing_bib = bib_tool and not bib["available"]
        bib_ok = not missing_bib
        if tex_ok and bib_tool and bib_ok:
            bib_ok = run([bib_tool, "./" + stem])
        # BibTeX/Biber output needs to be read by TeX before it can update the citation labels.
        required = 2 if bib_tool and bib_ok else 0
        last_state = None
        for _ in range(4):
            if not tex_ok or not bib_ok:
                break
            current = parse_tex_logs(log)
            needs_run = current["rerun_requested"] or current["has_undefined_cites"] or current["has_undefined_refs"]
            state_files = list(work.rglob("*.aux"))
            for ext in (".toc", ".out", ".lof", ".lot", ".bcf"):
                sidecar = work / (stem + ext)
                if sidecar.is_file():
                    state_files.append(sidecar)
            state = tuple(sha256_file(p) for p in sorted(state_files))
            if required <= 0 and (not needs_run or state == last_state):
                break
            required -= 1
            last_state = state
            tex_ok = run(tex_cmd)

        diag.update(parse_tex_logs(log, blg if bib_tool else None))
        # Place evidence beside the edited main source so the caller can archive it.
        for evidence in (log, blg):
            if evidence.is_file():
                dest = source.parent / evidence.name
                shutil.copy2(evidence, dest)
                manifest["build_logs"].append(str(dest))
        diag["log_file"] = str(source.parent / log.name) if log.is_file() else None
        for entry in diag["diagnostics"]:
            if entry.get("file"):
                location = Path(entry["file"])
                if location.is_absolute() and location.is_relative_to(tree):
                    entry["file"] = location.relative_to(tree).as_posix()
                elif not location.is_absolute() and (work / location).is_file():
                    logged = (work / location).resolve()
                    if logged.is_relative_to(tree):
                        entry["file"] = logged.relative_to(tree).as_posix()
        if missing_bib:
            issue("missing_bibliography_tool", f"Requested backend {bib_tool} is unavailable.")
        for p in passes:
            if p["returncode"] and (p["timed_out"] or not diag["error_count"]):
                issue("build_timeout" if p["timed_out"] else "tool_exit", f"{p['tool']} returned {p['returncode']}. {p.get('error_tail', '')[-700:]}")
        pdf = work / (stem + ".pdf")
        audit = _audit_pdf(pdf, previews_dir.resolve() if previews_dir else None, diag["diagnostics"])
        manifest.update({k: audit[k] for k in ("page_count", "previews", "pdf_sha256", "rendered_unresolved_markers", "pdf_fonts")})
        manifest["audit_tool"] = audit.get("audit_tool")
        manifest["layout_audit"] = audit["layout"]
        layout_pages = sorted({f["page"] for f in audit["layout"]["findings"]})
        if audit["layout"]["status"] == "clipped":
            clipped_pages = sorted({f["page"] for f in audit["layout"]["findings"] if f["severity"] == "error"})
            issue("page_text_clipped", f"Painted text crosses the display boundary on PDF pages {clipped_pages}. Inspect the cited glyph positions and preview.")
        elif audit["layout"]["status"] == "review":
            diag["diagnostics"].append({"category": "page_edge_review", "severity": "warning", "file": pdf.name,
                                        "line": None, "message": f"Review possible clipped text on pages {layout_pages}.",
                                        "is_cascade": False})
            diag["warning_count"] += 1
        if "audit_error" in audit:
            issue("pdf_audit_failed", audit["audit_error"])
        if audit["pdf_sha256"]:
            dest = source.with_suffix(".pdf")
            shutil.copy2(pdf, dest)
            manifest["output_pdf"] = str(dest)
            unresolved = diag["has_undefined_cites"] or diag["has_undefined_refs"] or diag["rerun_requested"]
            lost_content = any(d["category"] in ("missing_glyph", "duplicate_label") for d in diag["diagnostics"])
            incomplete = (not tex_ok or not bib_ok or diag["error_count"] or unresolved or lost_content or
                          audit["layout"]["status"] == "review" or
                          not audit["pdf_nonempty"] or audit["rendered_unresolved_markers"])
            manifest["status"] = "partial" if incomplete else "completed"
        elif not diag["error_count"]:
            issue("missing_pdf", "The engine did not produce a PDF.")
        manifest["dependencies"] = _evidence_files(work / (stem + ".fls"), work)

    # Use quoted arguments and change directory relative to the delivered script.
    commands = [shlex.join(p["argv"]) for p in passes]
    repro = source.parent / "build_repro.sh"
    relative_cwd = Path(os.path.relpath(build_dir, source.parent)).as_posix()
    move = 'cd -- "$(dirname -- "$0")"\n'
    if relative_cwd != ".":
        move += "cd -- " + shlex.quote(relative_cwd) + "\n"
    write_text_lf(repro, '#!/usr/bin/env bash\nset -euo pipefail\n' + move + "\n".join(commands) + "\n")
    manifest.update(repro_command=" && ".join(commands), repro_script=str(repro), build_cwd=project["build_cwd"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--main-tex", required=True)
    parser.add_argument("--build-cwd", help="Existing project-relative build directory")
    parser.add_argument("--engine", choices=ENGINES, default="pdflatex")
    parser.add_argument("--bib-tool", choices=(*BIB_TOOLS, "none"), default="none")
    parser.add_argument("--previews-dir")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--out-manifest", required=True)
    parser.add_argument("--out-diag", required=True)
    args = parser.parse_args()
    res = compile_latex_project(Path(args.project_dir), args.main_tex, engine=args.engine,
                                bib_tool=None if args.bib_tool == "none" else args.bib_tool,
                                previews_dir=Path(args.previews_dir) if args.previews_dir else None,
                                timeout_s=max(1, args.timeout), build_cwd_hint=args.build_cwd)
    write_json_atomic(Path(args.out_manifest), res["manifest"])
    write_json_atomic(Path(args.out_diag), res["diagnostics"])
    print(json.dumps({"status": res["manifest"]["status"], "page_count": res["manifest"]["page_count"],
                      "errors": res["diagnostics"]["error_count"]}, ensure_ascii=False))
    return 0 if res["manifest"]["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
