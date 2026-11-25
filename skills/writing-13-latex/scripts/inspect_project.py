#!/usr/bin/env python3
"""Discover a TeX entry point, its reachable source files, assets and build tools."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tex_utils import probe_tool_version, write_json_atomic

DOC_CLASS_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}")
PKG_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\]\s*)?\{([^}]+)\}")
ENGINE_RE = re.compile(r"^\s*%\s*!\s*TeX\s+(?:TS-)?program\s*=\s*(pdflatex|xelatex|lualatex)\b", re.I | re.M)
INCLUDE_RE = re.compile(r"\\(input|include|subfile)\b\s*(?:\{([^}]+)\}|([^\s{}%]+))|\\(subimport|import)\s*\{([^}]*)\}\s*\{([^}]+)\}")
GRAPHICS_RE = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}")
BIB_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}")


def _text(path):
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    cleaned = re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", raw)
    return raw, cleaned


def inspect_latex_project(project_dir: Path, main_tex_hint: str | None = None) -> dict:
    root = project_dir.resolve()
    named_roots = {"main", "paper", "manuscript", "article", "thesis"}
    ancillary = {"template", "templates", "example", "examples", "sample", "samples", "docs"}
    def rank(path):
        relative = path.relative_to(root)
        return (bool(ancillary.intersection(p.lower() for p in relative.parts)),
                path.stem.lower() not in named_roots, len(relative.parts), relative.as_posix())

    sources = sorted((p for p in root.rglob("*.tex") if p.is_file() and p.resolve().is_relative_to(root)), key=rank)
    candidates = [p for p in sources if DOC_CLASS_RE.search(_text(p)[1])]
    explicit = (root / main_tex_hint).resolve() if main_tex_hint else None
    if explicit:
        main = explicit if explicit.is_relative_to(root) and explicit.is_file() and explicit.suffix.lower() == ".tex" else None
    else:
        main = candidates[0] if candidates else None

    result = {"schema_version": "writing-13-project-map/v1", "found": main is not None,
              "project_dir": str(project_dir), "main_tex": None,
              "main_candidates": [p.relative_to(root).as_posix() for p in candidates],
              "missing_assets": [], "tex_files": [], "bib_files": [], "graphic_files": [],
              "local_template_files": [], "packages": [], "fonts_requested": [], "dynamic_paths": []}
    if not main:
        result["missing_assets"].append({"type": "main_tex", "ref": str(main_tex_hint or "documentclass"), "from": "."})
        return result

    work = main.parent
    raw, main_text = _text(main)
    magic = ENGINE_RE.search(raw)
    doc_class = DOC_CLASS_RE.search(main_text)
    result.update(main_tex=main.relative_to(root).as_posix(), documentclass=doc_class.group(1) if doc_class else None)
    search_graphics = []
    visited = set()
    unicode_needed = False
    lua_needed = False
    biblatex_backend = None
    bibliography_declared = False

    def add(key, value):
        if value not in result[key]:
            result[key].append(value)

    def resolve_ref(ref, bases, extensions, kind, origin, line, report=True):
        if not ref or any(ch in ref for ch in ("\\", "#", "{", "}")):
            add("dynamic_paths", {"type": kind, "ref": ref, "from": origin, "line": line})
            return None
        for base in bases:
            for ext in extensions:
                candidate = (base / (ref + ext)).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    return candidate
        if report:
            add("missing_assets", {"type": kind, "ref": ref, "from": origin, "line": line})
        return None

    def walk(path, base, kind):
        nonlocal unicode_needed, lua_needed, bibliography_declared, biblatex_backend
        key = (path, base)
        if key in visited or len(visited) >= 512:
            return
        visited.add(key)
        origin = path.relative_to(root).as_posix()
        add(kind, origin)
        _, txt = _text(path)
        def line_at(match):
            return txt.count("\n", 0, match.start()) + 1

        unicode_needed |= bool(re.search(r"[\u3400-\u9fff]", txt))
        if result["documentclass"] and result["documentclass"].startswith("ctex"):
            unicode_needed = True
        for pattern in re.finditer(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\}\s*)+)\}", txt):
            for directory in re.findall(r"\{([^{}]*)\}", pattern.group(1)):
                search_graphics.append(base / directory)
        classes = list(re.finditer(r"\\(?:documentclass|LoadClass)\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}", txt))
        for match in classes:
            resolved = resolve_ref(match[1], [base, work], [".cls"], "class", origin, line_at(match), False)
            if resolved:
                walk(resolved, base, "local_template_files")
        for match in PKG_RE.finditer(txt):
            for pkg in (x.strip() for x in match[2].split(",")):
                add("packages", pkg)
                unicode_needed |= pkg in ("fontspec", "xeCJK", "ctex", "unicode-math")
                lua_needed |= pkg in ("luacode", "luatexja", "luaotfload")
                if pkg == "biblatex":
                    biblatex_backend = "bibtex" if re.search(r"\bbackend\s*=\s*bibtex(?:8)?\b", match[1] or "", re.I) else "biber"
                resolved = resolve_ref(pkg, [base, work], [".sty"], "package", origin, line_at(match), False)
                if resolved:
                    walk(resolved, base, "local_template_files")
        for name in re.findall(r"\\set(?:main|sans|mono|CJKmain|CJKsans|CJKmono)font\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}", txt):
            add("fonts_requested", name)
        for m in INCLUDE_RE.finditer(txt):
            ref = (m[2] or m[3] or m[6]).strip()
            include_base = base
            if m[4]:
                include_base = ((base if m[4] == "subimport" else work) / m[5]).resolve()
            resolved = resolve_ref(ref, [include_base, work], ["", ".tex"], "tex_include", origin, line_at(m))
            if resolved:
                walk(resolved, include_base, "tex_files")
        for m in BIB_RE.finditer(txt):
            bibliography_declared = True
            for ref in (x.strip() for x in m[1].split(",")):
                resolved = resolve_ref(ref, [base, work], ["", ".bib"], "bib_file", origin, line_at(m))
                if resolved:
                    add("bib_files", resolved.relative_to(root).as_posix())
        for m in GRAPHICS_RE.finditer(txt):
            ref = m[1].strip()
            resolved = resolve_ref(ref, [base, work, *search_graphics], ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".mps"], "graphic", origin, line_at(m))
            if resolved:
                add("graphic_files", resolved.relative_to(root).as_posix())

    walk(main, work, "tex_files")
    engine = magic[1].lower() if magic else "lualatex" if lua_needed else "xelatex" if unicode_needed else "pdflatex"
    bib_tool = (biblatex_backend or "bibtex") if bibliography_declared else None
    result.update(recommended_engine=engine, bib_tool=bib_tool,
                  toolchain={tool: probe_tool_version(tool) for tool in [engine, bib_tool] if tool})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--main-tex")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    res = inspect_latex_project(Path(args.project_dir), args.main_tex)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({k: res.get(k) for k in ("found", "main_tex", "recommended_engine", "bib_tool", "missing_assets")}, ensure_ascii=False))
    return 0 if res["found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
