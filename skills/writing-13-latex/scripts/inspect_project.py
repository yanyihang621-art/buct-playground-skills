#!/usr/bin/env python3
"""Inspect a LaTeX project: detect main .tex, inputs, engine, bib tool, and missing assets."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tex_utils import probe_tool_version, write_json_atomic

DOC_CLASS_RE = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}")
INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
BIB_RES_RE = re.compile(r"\\(?:bibliography|addbibresource)\{([^}]+)\}")
USEPKG_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _find_main_tex(project_dir: Path, preferred: Optional[str] = None) -> Optional[Path]:
    if preferred:
        p = project_dir / preferred
        if p.exists():
            return p
    tex_files = sorted(project_dir.rglob("*.tex"))
    for p in tex_files:
        txt = p.read_text(encoding="utf-8", errors="replace")
        if DOC_CLASS_RE.search(txt) and r"\begin{document}" in txt:
            return p
    return tex_files[0] if tex_files else None


def inspect_latex_project(project_dir: Path, main_tex_hint: Optional[str] = None) -> Dict[str, Any]:
    """Scan main .tex and subfiles to plan TeX engine, bib tool, and asset checks."""
    main_p = _find_main_tex(project_dir, main_tex_hint)
    if main_p is None:
        return {
            "schema_version": "writing-13-project-map/v1",
            "found": False,
            "project_dir": str(project_dir),
            "main_tex": None,
            "missing_assets": ["main_tex"],
        }

    visited: Set[Path] = set()
    tex_graph: List[str] = []
    missing_assets: List[Dict[str, str]] = []
    packages: List[str] = []
    bib_files: List[str] = []
    uses_biblatex = False
    uses_cjk_or_fontspec = False
    doc_class: Optional[str] = None

    def _walk(fp: Path) -> None:
        nonlocal doc_class, uses_biblatex, uses_cjk_or_fontspec
        resolved = fp.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        rel = fp.relative_to(project_dir).as_posix()
        tex_graph.append(rel)

        # Strip LaTeX % comments when detecting directives (not escaped \%)
        raw = fp.read_text(encoding="utf-8", errors="replace")
        cleaned_lines = [re.sub(r"(?<!\\)%.*$", "", ln) for ln in raw.splitlines()]
        txt = "\n".join(cleaned_lines)

        if doc_class is None:
            mc = DOC_CLASS_RE.search(txt)
            if mc:
                doc_class = mc.group(1).strip()

        for mp in USEPKG_RE.findall(txt):
            for pkg in mp.split(","):
                p_name = pkg.strip()
                if p_name and p_name not in packages:
                    packages.append(p_name)
                if p_name in ("fontspec", "xeCJK", "ctex", "luatexja"):
                    uses_cjk_or_fontspec = True
                if p_name == "biblatex":
                    uses_biblatex = True

        if doc_class in ("ctexart", "ctexbook", "ctexrep") or CJK_RE.search(txt):
            uses_cjk_or_fontspec = True

        for inc in INPUT_RE.findall(txt):
            inc_clean = inc.strip()
            cand = project_dir / (inc_clean if inc_clean.endswith(".tex") else f"{inc_clean}.tex")
            if cand.exists():
                _walk(cand)
            else:
                missing_assets.append({"type": "tex_include", "ref": inc_clean, "from": rel})

        for bib in BIB_RES_RE.findall(txt):
            for bitem in bib.split(","):
                bclean = bitem.strip()
                if not bclean:
                    continue
                bcand = project_dir / (bclean if bclean.endswith(".bib") else f"{bclean}.bib")
                if bcand.exists():
                    if bcand.relative_to(project_dir).as_posix() not in bib_files:
                        bib_files.append(bcand.relative_to(project_dir).as_posix())
                else:
                    missing_assets.append({"type": "bib_file", "ref": bclean, "from": rel})

        for g in GRAPHICS_RE.findall(txt):
            gclean = g.strip()
            exts = [""] if Path(gclean).suffix else [".pdf", ".png", ".jpg", ".jpeg", ".eps"]
            if not any((project_dir / f"{gclean}{ext}").exists() for ext in exts):
                missing_assets.append({"type": "graphic", "ref": gclean, "from": rel})

    _walk(main_p)

    recommended_engine = "xelatex" if uses_cjk_or_fontspec else "pdflatex"
    has_cite_or_bib = bool(bib_files)
    bib_tool: Optional[str] = None
    if has_cite_or_bib:
        bib_tool = "biber" if uses_biblatex else "bibtex"

    return {
        "schema_version": "writing-13-project-map/v1",
        "found": True,
        "project_dir": str(project_dir),
        "main_tex": main_p.relative_to(project_dir).as_posix(),
        "documentclass": doc_class,
        "tex_files": tex_graph,
        "packages": packages,
        "bib_files": bib_files,
        "recommended_engine": recommended_engine,
        "bib_tool": bib_tool,
        "missing_assets": missing_assets,
        "toolchain": {
            recommended_engine: probe_tool_version(recommended_engine),
            **({bib_tool: probe_tool_version(bib_tool)} if bib_tool else {}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect LaTeX project structure and dependencies.")
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--main-tex", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    res = inspect_latex_project(Path(args.project_dir), main_tex_hint=args.main_tex)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"found": res["found"], "main_tex": res["main_tex"], "engine": res.get("recommended_engine"), "bib_tool": res.get("bib_tool")}, ensure_ascii=False))
    return 0 if res["found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
