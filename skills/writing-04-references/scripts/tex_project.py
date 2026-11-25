"""Expand local TeX includes for citation order, restore an editable project, then verify its citations."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from io_utils import write_json_atomic, write_text_lf
from parse_bib_sources import read_source_text


# Protected spans are replaced only in the review projection, then restored byte-for-text.
PROTECTED = re.compile(
    r"\\[%\\]|%[^\n]*|\\verb\*?(?P<delimiter>[^\w\s])(?:(?!(?P=delimiter))[^\n])*(?P=delimiter)"
    r"|\\begin\{(?P<env>verbatim\*?|Verbatim|lstlisting|minted|comment)\}[\s\S]*?\\end\{(?P=env)\}"
    r"|\\iffalse\b[\s\S]*?\\fi\b|\\endinput\b[\s\S]*"
)
INCLUDE = re.compile(r"\\(?P<command>input|include)\b(?:[ \t]*\{(?P<braced>[^}\n]*)\}|[ \t]+(?P<bare>[\w./-]+))?")
ASSET_SUFFIXES = {".tex", ".ltx", ".bib", ".bst", ".cls", ".clo", ".sty", ".fd", ".def", ".dbx",
                  ".bbx", ".cbx", ".lbx", ".tikz", ".pgf", ".csv", ".tsv", ".dat", ".eps", ".svg",
                  ".pdf", ".png", ".jpeg", ".jpg", ".mps", ".icc"}


@dataclass
class TexPlan:
    root: Path
    entry: Path
    tag: str
    text: str = ""
    origins: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    includes: dict = field(default_factory=dict)
    literals: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    include_only: set | None = None

    def name(self, path):
        return path.relative_to(self.root).as_posix()


def _safe_file(path, root):
    """Reject links, input escapes and control sequences as local document dependencies."""
    normalized = Path(os.path.abspath(path))
    return normalized.is_relative_to(root) and normalized.resolve() == normalized and normalized.is_file()


def _resolve_include(plan, parent, name):
    if not name or any(c in name for c in "\\\0$#{}") or Path(name).is_absolute() or ":" in name:
        return None
    rel = Path(name)
    rel = rel if rel.suffix else rel.with_suffix(".tex")
    for base in (plan.entry.parent, parent.parent):
        p = base / rel
        if _safe_file(p, plan.root):
            return p.resolve()
    return None


def prepare_tex_project(input_dir, main_path) -> TexPlan:
    root, entry = Path(input_dir).resolve(), Path(main_path).absolute()
    if not _safe_file(entry, root):
        raise ValueError("TeX entry must be a regular file inside the input directory")
    entry = entry.resolve()
    salt = hashlib.sha256(entry.read_bytes()).hexdigest()[:16]
    plan = TexPlan(root, entry, "WREF" + salt)
    pieces, line_origins = [], []
    current = None

    def append(text, path=None, first=1):
        nonlocal current
        if not text:
            return
        pieces.append(text)
        for j, part in enumerate(text.split("\n")):
            if j:
                line_origins.append(current)
                current = None
            if current is None and part and path:
                current = {"source_file": plan.name(path), "source_line": first + j}

    def mask(match):
        original = match.group()
        # Escape-percent and TeX line-break commands are single tokens (no active comment).
        if original == r"\%":
            return original
        encoded = []
        for line in original.split("\n"):
            key = f"{plan.tag}L{len(plan.literals)}X"
            plan.literals[key] = line
            encoded.append(key)
        return "\n".join(encoded)

    def expand(path, parents):
        source = plan.sources.setdefault(path, read_source_text(path))
        safe = PROTECTED.sub(mask, source)
        if path == plan.entry:
            only = re.search(r"\\includeonly\s*\{([^}]*)\}", safe)
            if only:
                plan.include_only = {item.strip().removesuffix(".tex") for item in only[1].split(",")}
        start, line = 0, 1
        for match in INCLUDE.finditer(safe):
            prefix = safe[start:match.start()]
            append(prefix, path, line)
            line += prefix.count("\n")
            command, filename = match["command"], (match["braced"] or match["bare"] or "").strip()
            child = _resolve_include(plan, path, filename)
            skipped = command == "include" and plan.include_only is not None and filename.removesuffix(".tex") not in plan.include_only
            if skipped:
                append(match[0], path, line)
            elif child is None or child in parents or len(parents) >= 40 or len(plan.includes) >= 300:
                plan.issues.append({"code": "unexpanded_tex_include", "source_file": plan.name(path),
                                    "source_line": line, "command": match[0]})
                append(match[0], path, line)
            else:
                key = len(plan.includes)
                plan.includes[key] = (child, match[0])
                append(f"\n%{plan.tag}BEGIN{key}\n")
                expand(child, parents | {child})
                append(f"\n%{plan.tag}END{key}\n")
            start = match.end()
        append(safe[start:], path, line)

    expand(entry, {entry})
    plan.text = "".join(pieces)
    plan.origins = line_origins + [current]
    return plan


def _restored_sources(plan, edited):
    """Remove generated include boundaries, retain original commands and reconstruct each source."""
    event = re.compile(r"\n%" + plan.tag + r"(BEGIN|END)(\d+)\n")
    literal = re.compile(plan.tag + r"L\d+X")
    rewritten, stack = {}, [(None, plan.entry, [])]
    seen = set()
    cursor = 0
    for match in event.finditer(edited):
        stack[-1][2].append(edited[cursor:match.start()])
        key = int(match[2])
        path, command = plan.includes[key]
        if match[1] == "BEGIN":
            if key in seen:
                raise ValueError("duplicated include boundary")
            seen.add(key)
            stack[-1][2].append(command)
            stack.append((key, path, []))
        else:
            if stack[-1][0] != key:
                raise ValueError("include ordering changed during edit")
            _, _, parts = stack.pop()
            source = literal.sub(lambda m: plan.literals[m[0]], "".join(parts))
            if path in rewritten and rewritten[path] != source:
                raise ValueError("repeated include received inconsistent citation edits: " + plan.name(path))
            rewritten[path] = source
        cursor = match.end()
    if len(stack) != 1 or seen != set(plan.includes):
        raise ValueError("include boundary removed during edit")
    stack[0][2].append(edited[cursor:])
    rewritten[plan.entry] = literal.sub(lambda m: plan.literals[m[0]], "".join(stack[0][2]))
    return rewritten


def readable_tex(plan, text):
    """Expose the expanded manuscript with original comments and literals, retaining line numbers."""
    text = re.sub(r"^%" + plan.tag + r"(?:BEGIN|END)\d+$", "", text, flags=re.M)
    return re.sub(plan.tag + r"L\d+X", lambda m: plan.literals.get(m[0], m[0]), text)


def _copy_project(plan, target, rewritten, output):
    # Mirror the source tree under the same relative paths. Exclude compiler intermediates,
    # platform metadata and the entry's old PDF. Explicitly used outside chapters are retained.
    selected = set(rewritten)
    project_root = plan.entry.parent
    for base, folders, names in os.walk(project_root, followlinks=False):
        base = Path(base)
        folders[:] = [n for n in folders if not n.startswith(".") and (base / n).resolve() == base / n
                      and (base / n).resolve() != output.resolve()]
        for name in names:
            p = base / name
            if p.suffix.lower() in ASSET_SUFFIXES and not name.startswith(".") and _safe_file(p, plan.root):
                if p != plan.entry.with_suffix(".pdf"):
                    selected.add(p)
    # Some projects keep figures or a custom style next to the entry's containing folder.
    # Carry explicitly addressed dependencies inside the supplied input root as well.
    graphic_dirs = [project_root]
    for match in re.finditer(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})+)\s*\}", plan.text):
        graphic_dirs.extend(project_root / name for name in re.findall(r"\{([^{}]+)\}", match[1]))
    for match in re.finditer(r"\\(includegraphics|bibliographystyle|usepackage|documentclass)"
                             r"(?:\s*\[[^\]\n]*\])?\s*\{([^{}\n]+)\}", plan.text):
        cmd, raw = match.groups()
        suffixes = {"includegraphics": [".pdf", ".png", ".jpg", ".jpeg", ".eps"],
                    "bibliographystyle": [".bst"], "usepackage": [".sty"], "documentclass": [".cls"]}[cmd]
        for name in raw.split(","):
            rel = Path(name.strip())
            if rel.is_absolute() or "\\" in name or not name.strip():
                continue
            variations = [rel] if rel.suffix else [Path(str(rel) + ext) for ext in suffixes]
            for base in (graphic_dirs if cmd == "includegraphics" else [project_root]):
                for relative in variations:
                    p = base / relative
                    if _safe_file(p, plan.root):
                        selected.add(p.resolve())
    for src in sorted(selected):
        dst = target / plan.name(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src in rewritten:
            write_text_lf(dst, rewritten[src])
        else:
            shutil.copyfile(src, dst)
    return [plan.name(p) for p in sorted(selected)]


def _verify_build(project, relative_entry, source, cited_keys, output_dir, timeout):
    explicit = re.search(r"(?im)^\s*%\s*!\s*tex\s+program\s*=\s*(pdflatex|xelatex|lualatex)\b", source)
    engine = explicit[1].lower() if explicit else "xelatex" if re.search(r"fontspec|\\documentclass(?:\[[^]]*\])?\{ctex", source) else "pdflatex"
    info = {"engine": engine, "passes": [], "status": "unverified", "bibliography_keys": [],
            "unresolved_citation_count": None, "missing_bibliography_keys": [], "unresolved_diagnostics": []}
    preview = None
    if not shutil.which(engine):
        info["detail"] = "missing executable: " + engine
        return info, preview
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="reference-tex-") as tmp:
        clone = Path(tmp) / "project"
        shutil.copytree(project, clone)
        entry = clone / relative_entry
        cwd, stem = entry.parent, entry.stem
        args = [engine, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "-file-line-error", entry.name]
        if "miktex" in str(shutil.which(engine)).lower():
            args.insert(1, "--disable-installer")

        def run(command):
            if not shutil.which(command[0]):
                info["detail"] = "missing executable: " + command[0]
                return False
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                info["detail"] = "compile budget exceeded"
                return False
            try:
                proc = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=remaining, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                info["detail"] = type(exc).__name__
                return False
            info["passes"].append({"tool": command[0], "exit": proc.returncode})
            if proc.returncode:
                info["detail"] = proc.stdout[-2400:].decode("utf-8", "replace")
                return False
            return True

        if not run(args):
            return info, preview
        backend = "biber" if (cwd / (stem + ".bcf")).is_file() else "bibtex"
        if cited_keys:
            if not run([backend, stem]):
                return info, preview
        if not run(args) or not run(args):
            return info, preview
        log = (cwd / (stem + ".log")).read_text(encoding="utf-8", errors="replace")
        diagnostics = [line.strip() for line in log.splitlines() if re.search(
            r"citation\b.*undefined|there were undefined (?:references|citations)|please \(re\)run biber", line, re.I)]
        bbl = cwd / (stem + ".bbl")
        bibtext = bbl.read_text(encoding="utf-8", errors="replace") if bbl.is_file() else ""
        keys = sorted(set(re.findall(r"\\(?:bibitem(?:\[[\s\S]*?\])?|entry)\s*\{([^{}\n]+)\}", bibtext)))
        missing = sorted(set(cited_keys) - set(keys))
        unresolved = set(missing)
        for diag in diagnostics:
            unresolved.update(re.findall(r"citation\s+[\x60'‘]([^'’\x60]+)['’\x60]", diag, re.I))
        info.update(bibliography_keys=keys, missing_bibliography_keys=missing, unresolved_citation_count=len(unresolved),
                    unresolved_diagnostics=diagnostics, status="verified" if not (missing or diagnostics) else "partial")
        pdf = cwd / (stem + ".pdf")
        if pdf.is_file() and pdf.stat().st_size:
            preview = output_dir / "tex_manuscript_preview.pdf"
            shutil.copyfile(pdf, preview)
        else:
            info.update(status="unverified", detail="compiler produced no PDF")
        return info, preview


def _map_occurrences(plan, sync):
    before = plan.text.splitlines()
    after = sync["synced_manuscript"].splitlines()
    aligned = {}
    for tag, i, j, a, b in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and j - i == b - a == 1):
            aligned.update((a + n + 1, i + n) for n in range(b - a))
    for occ in sync["citation_map"]["in_text_occurrences"]:
        line = occ.get("edited_line", occ.get("line", 0))
        origin = aligned.get(line)
        if origin is not None and origin < len(plan.origins) and plan.origins[origin]:
            occ.update(plan.origins[origin])
        else:
            occ["source_locator_status"] = "edited_span_requires_review"


def export_tex_project(plan, sync, output_dir, *, compile_pdf=True, timeout=100):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    project = out / "tex_project"
    issues = list(plan.issues)
    cited = [rec["cite_key"] for rec in sync["cited_records"]]
    _map_occurrences(plan, sync)
    if re.search(r"\\nocite\s*\{", plan.text):
        issues.append({"code": "tex_nocite_requires_bibliography_review",
                       "detail": "Check all explicitly included uncited entries against the original library."})
    try:
        rewritten = _restored_sources(plan, sync["synced_manuscript"])
    except (ValueError, KeyError) as exc:
        issues.append({"code": "tex_edit_projection_conflict", "detail": str(exc)})
        rewritten = plan.sources
    rel_entry = plan.name(plan.entry)
    exported = project / rel_entry
    with tempfile.TemporaryDirectory(dir=out, prefix="tex-staging-") as staging:
        staged = Path(staging)
        files = _copy_project(plan, staged, rewritten, out)
        bib = staged / rel_entry
        bib = bib.parent / "references_synced.bib"
        write_text_lf(bib, sync["formatted_bibliography"])
        if project.exists():
            # This directory is dedicated to generated project material across local reruns.
            # Replace individual files and prune only files recorded in our prior manifest.
            manifest = out / "evidence" / "tex_project_report.json"
            if manifest.is_file():
                try:
                    previous = json.loads(manifest.read_text(encoding="utf-8"))
                    for rel in previous.get("copied_files", []):
                        target = project / rel
                        if rel not in files and _safe_file(target, project.resolve()):
                            target.unlink()
                except (OSError, ValueError):
                    pass
        shutil.copytree(staged, project, dirs_exist_ok=True)
    artifacts = {"tex_project_entry": str(exported), "tex_project_bibliography": str(exported.parent / "references_synced.bib")}
    report = {"entry": rel_entry, "included_files": [plan.name(p) for p in plan.sources], "copied_files": files}
    if compile_pdf:
        build, preview = _verify_build(project, rel_entry, rewritten[plan.entry], cited, out, timeout)
        report["compile"] = build
        if preview:
            artifacts["tex_project_pdf"] = str(preview)
        if build["status"] != "verified":
            issues.append({"code": "tex_citation_compile_unverified", "compile": build})
    report["issues"] = issues
    report_path = out / "evidence" / "tex_project_report.json"
    write_json_atomic(report_path, report)
    artifacts["tex_project_report"] = str(report_path)
    return artifacts, issues
