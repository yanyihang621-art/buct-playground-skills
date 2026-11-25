#!/usr/bin/env python3
"""Audit reachable TeX sources and apply conservative, evidence-linked repairs."""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import os
import re
import tempfile
from pathlib import Path

from tex_utils import write_json_atomic, write_text_lf


LITERAL_ENV = r"verbatim\*?|Verbatim|lstlisting|minted|comment|filecontents\*?"
TOKEN = re.compile(
    r"\\begin\s*\{(" + LITERAL_ENV + r")\}"
    r"|\\(verb\*?|lstinline)(?![A-Za-z])"
    r"|\\(?:url|nolinkurl|path)\{"
    r"|\\[A-Za-z@]+|\\[^\n]|%"
)
ENV = re.compile(r"\\(begin|end)\s*\{([A-Za-z0-9@*_-]+)\}")
INCLUDE = re.compile(r"\\(input|include|subfile)\s*(?:\{([^{}]+)\}|([^\s{}\\]+))")
PACKAGES = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
RULES = {
    "booktabs": re.compile(r"\\(?:toprule|midrule|bottomrule|cmidrule|addlinespace)\b"),
    "amsmath": re.compile(r"\\(?:eqref|dfrac|tfrac|numberwithin)\b|\\begin\s*\{(?:align(?:at)?|gather|multline|split|aligned|cases|[bpvBV]?matrix)\*?\}"),
    "graphicx": re.compile(r"\\includegraphics\b"),
    "multirow": re.compile(r"\\multirow\b"),
    "tabularx": re.compile(r"\\begin\s*\{tabularx\}"),
    "longtable": re.compile(r"\\begin\s*\{longtable\}"),
}
CITE = re.compile(r"\\(?:[Cc]ite[tp]|[Cc]ite|[Pp]arencite|[Tt]extcite|[Aa]utocite|nocite|footcite|citeauthor|citeyear)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]+)\}")
REF = re.compile(r"\\(?:ref|eqref|pageref|[Cc]ref|autoref|nameref)\*?\s*\{([^{}]+)\}")
LABEL = re.compile(r"\\label\s*\{([^{}]+)\}")
BIBITEM = re.compile(r"\\bibitem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
BIBSOURCE = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
BIBENTRY = re.compile(r"(?im)^[ \t]*@(?!comment\b|preamble\b|string\b)\w+\s*[({]\s*([^,\s]+)\s*,")
DEFINITION = re.compile(r"\\(?:newcommand|renewcommand|providecommand|def|let|newenvironment|NewDocumentCommand)\*?\s*\{?\s*\\?([A-Za-z@]+)")
DOCSTART = re.compile(r"(?m)^[ \t]*\\begin\s*\{document\}")
ESCAPES_OR_DELIMITERS = re.compile(r"\\[A-Za-z@]+|\\[^\n]|[{}]|\$\$?")
TABLE_START = re.compile(r"\\begin\s*\{(tabular\*?|tabularx|longtable)\}\s*(?:\[[^\]]*\]\s*)?")
SIMPLE_KEY = re.compile(r"^[\w:./+-]+$")


def mask_source(text):
    """Mask opaque TeX and comments, preserving offsets and line breaks."""
    parts = list(text)
    comments = []
    pos = 0
    while token := TOKEN.search(text, pos):
        start = token.start()
        end = token.end()
        if token.group(1):
            close = re.search(r"\\end\s*\{" + re.escape(token.group(1)) + r"\}", text[end:])
            end = end + close.end() if close else len(text)
        elif token.group(2):
            # Inline verbatim uses an arbitrary delimiter. Options of lstinline are opaque.
            if text[end:end + 1] == "[" and token.group(2) == "lstinline":
                opt = re.match(r"\[[^\]\n]*\]", text[end:])
                if opt:
                    end += opt.end()
            if end < len(text) and text[end] not in "\r\n":
                stop = text.find(text[end], end + 1)
                newline = text.find("\n", end)
                end = stop + 1 if stop >= 0 and (newline < 0 or stop < newline) else (newline if newline >= 0 else len(text))
        elif token.group().startswith((r"\url{", r"\path{", r"\nolinkurl{")):
            arg = braced(text, end - 1)
            end = arg[1] if arg else end
        elif token.group() == "%":
            comments.append(start)
            newline = text.find("\n", end)
            end = newline if newline >= 0 else len(text)
        elif token.group() == r"\endinput" and not text[text.rfind("\n", 0, start) + 1:start].strip():
            end = len(text)
        else:
            pos = end
            continue
        parts[start:end] = [c if c in "\r\n" else " " for c in text[start:end]]
        pos = end
    return "".join(parts), comments


def braced(text, pos):
    """Read one balanced braced argument, or leave unsupported syntax to the engine."""
    if text[pos:pos + 1] != "{":
        return None
    depth = 1
    for token in ESCAPES_OR_DELIMITERS.finditer(text, pos + 1):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return text[pos + 1:token.start()], token.end()
    return None


class Source:
    def __init__(self, path, root):
        self.path = path
        self.name = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.text = raw.decode("utf-8-sig")
        self.code, self.comments = mask_source(self.text)
        self.newlines = [m.start() for m in re.finditer("\n", self.text)]
        self.edits = []

    def line(self, char):
        return bisect.bisect_left(self.newlines, char) + 1

    def issue(self, char, rule, message, **details):
        return dict(file=self.name, line=self.line(char), rule=rule, message=message, **details)

    def edit(self, start, stop, value, rule, evidence):
        self.edits.append((start, stop, value, self.issue(start, rule, evidence, before=self.text[start:stop], after=value)))


def project_path(name, caller, entry, root, extension=""):
    # The top-level build CWD and local includes cover conventional and import-based projects.
    if not name or any(ch in name for ch in ("\\", "#", "$", "\x00")):
        return None
    rel = Path(name)
    if rel.is_absolute() or rel.drive:
        return None
    if extension and not rel.suffix:
        rel = rel.with_suffix(extension)
    for base in dict.fromkeys((entry.parent, caller.parent, root)):
        candidate = (base / rel).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def sources_from(root, main, follow, issues):
    sources = {}
    pending = [main]
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            source = Source(path, root)
        except UnicodeDecodeError:
            issues.append(dict(file=path.relative_to(root).as_posix(), line=1, rule="source_encoding",
                               message="UTF-8 decoding failed. Inspect the source encoding before editing."))
            continue
        sources[path] = source
        if follow:
            for match in INCLUDE.finditer(source.code):
                name = (match.group(2) or match.group(3)).strip()
                child = project_path(name, path, main, root, ".tex")
                if child:
                    pending.append(child)
                elif "\\" not in name and "#" not in name:
                    issues.append(source.issue(match.start(), "unresolved_input", f"Review include path {name!r} against the build CWD and TeX search paths."))
    return sources


def column_count(spec):
    """Return None for custom/unsupported preambles rather than guessing column counts."""
    pos = count = 0
    while pos < len(spec):
        token = spec[pos]
        pos += 1
        if token.isspace() or token == "|":
            continue
        if token in "lcrX":
            count += 1
            continue
        if token not in "@!><pmb*":
            return None
        while pos < len(spec) and spec[pos].isspace():
            pos += 1
        arg = braced(spec, pos)
        if not arg:
            return None
        value, pos = arg
        if token == "*":
            while pos < len(spec) and spec[pos].isspace():
                pos += 1
            sub = braced(spec, pos)
            if not sub or not re.fullmatch(r"\s*\d{1,3}\s*", value):
                return None
            nested = column_count(sub[0])
            if nested is None:
                return None
            count += int(value) * nested
            pos = sub[1]
        elif token in "pmb":
            count += 1
    return count or None


def table_checks(source, issues):
    code = source.code
    for start in TABLE_START.finditer(code):
        env = start.group(1)
        end = re.search(r"\\end\s*\{" + re.escape(env) + r"\}", code[start.end():])
        if not end:
            continue
        limit = start.end() + end.start()
        pos = start.end()
        if env in ("tabularx", "tabular*"):
            width = braced(code, pos)
            if not width:
                continue
            pos = width[1]
            while pos < limit and code[pos].isspace():
                pos += 1
        spec = braced(code, pos)
        if not spec:
            continue
        count = column_count(spec[0])
        body = code[spec[1]:limit]
        if count is None or r"\begin" in body:
            continue
        # Report only flat, surplus ordinary cells; short rows and spans can be intentional.
        row_start = spec[1]
        depth = ampersands = 0
        for token in re.finditer(r"\\[A-Za-z@]+|\\[^\n]|[{}&]", body):
            value = token.group()
            index = spec[1] + token.start()
            if value == "{":
                depth += 1
            elif value == "}":
                depth -= 1
            elif depth == 0 and value == "&":
                ampersands += 1
            elif depth == 0 and value in (r"\\", r"\tabularnewline", r"\cr"):
                if ampersands >= count and r"\multicolumn" not in code[row_start:index]:
                    issues.append(source.issue(index, "tabular_column_mismatch", f"Row contains {ampersands + 1} cells for {count} declared columns. Check the original row values before editing."))
                ampersands = 0
                row_start = spec[1] + token.end()


def syntax_checks(source, issues):
    envs = []
    for match in ENV.finditer(source.code):
        kind, name = match.groups()
        if kind == "begin":
            envs.append((name, match.start()))
        elif envs and envs[-1][0] == name:
            envs.pop()
        else:
            expected = envs[-1][0] if envs else None
            issues.append(source.issue(match.start(), "mismatched_environment", f"Found end of {name!r}, open environment in this file is {expected!r}. Check adjacent includes."))
    for env, offset in envs:
        issues.append(source.issue(offset, "unclosed_environment", f"Opening {env!r} has no paired end within this file."))
    opens = []
    math = None
    for token in ESCAPES_OR_DELIMITERS.finditer(source.code):
        value = token.group()
        if value == "{":
            opens.append(token.start())
        elif value == "}":
            if opens:
                opens.pop()
            else:
                issues.append(source.issue(token.start(), "unmatched_brace", "Closing brace has no local opening brace. Inspect the enclosing argument and include boundary."))
        elif value in ("$", "$$"):
            if math is None:
                math = (value, token.start())
            elif math[0] == value:
                math = None
    if math:
        issues.append(source.issue(math[1], "unmatched_math_delimiter", "Dollar-delimited math appears unpaired within this file. Check against the TeX log before changing symbols."))
    for offset in opens:
        issues.append(source.issue(offset, "unmatched_brace", "Opening brace has no local closing brace. Check the macro argument against the engine log."))
    table_checks(source, issues)


def review_percent(source, issues):
    table_spans = []
    for table in TABLE_START.finditer(source.code):
        end = re.search(r"\\end\s*\{" + re.escape(table.group(1)) + r"\}", source.code[table.end():])
        if end:
            table_spans.append((table.end(), table.end() + end.start()))
    for offset in source.comments:
        if offset == 0 or not source.text[offset - 1].isdigit():
            continue
        start = source.text.rfind("\n", 0, offset) + 1
        end = source.text.find("\n", offset)
        suffix = source.text[offset + 1:end if end >= 0 else None].rstrip("\r")
        if not suffix.strip():
            continue
        # A numeric table cell, a swallowed row terminator, and a following row rule
        # together identify a structurally broken row; ordinary comments remain intact.
        prefix = source.code[start:offset]
        if (any(a < offset < b for a, b in table_spans)
                and re.search(r"&[ \t]*[-+]?\d+(?:[.,]\d+)?$", prefix)
                and re.fullmatch(r"[ \t]*\\\\[ \t]*", suffix)
                and end >= 0 and re.match(r"\s*\\(?:bottomrule|midrule|hline)\b", source.code[end:])):
            source.edit(offset, offset + 1, r"\%", "restore_percent_and_row_end",
                        "Numeric table cell hid a row terminator immediately before a horizontal rule.")
            continue
        issues.append(source.issue(offset, "ambiguous_numeric_percent",
            "This percent sign starts a TeX comment. If the source material means a percentage, escape it and confirm the restored text or row in the PDF."))


def package_edits(main, sources, issues):
    start = DOCSTART.search(main.code)
    if not start:
        return
    whole = "\n".join(s.code for s in sources.values())
    loaded = {p.strip() for m in PACKAGES.finditer(whole) for p in m.group(1).split(",")}
    if "mathtools" in loaded:
        loaded.add("amsmath")
    definitions = set(DEFINITION.findall(whole))
    standard = re.search(r"\\documentclass\s*(?:\[[^\]]*\]\s*)?\{(?:article|book|report|letter)\}", main.code[:start.start()])
    insert = start.start()
    # Keep existing order, in particular hyperref followed by cleveref or bookmark.
    hyp = re.search(r"(?m)^[ \t]*\\usepackage\s*(?:\[[^\]]*\]\s*)?\{[^{}]*\bhyperref\b[^{}]*\}", main.code[:insert])
    if hyp:
        insert = hyp.start()
    newline = "\r\n" if "\r\n" in main.text else "\n"
    for package, uses in RULES.items():
        if package in loaded:
            continue
        for source in sources.values():
            # Look in manuscript bodies, not in preamble command declarations.
            pos = start.end() if source is main else 0
            match = uses.search(source.code, pos)
            if not match:
                continue
            name = re.search(r"\\([A-Za-z@]+)", match.group()).group(1)
            if (name == "begin" and re.search(r"\{([^{}]+)\}", match.group()).group(1) in definitions) or name in definitions:
                continue
            evidence = f"{match.group()} at {source.name}:{source.line(match.start())}"
            if standard:
                main.edit(insert, insert, f"\\usepackage{{{package}}}{newline}", "add_missing_package", evidence)
            else:
                issues.append(source.issue(match.start(), "check_template_package", f"Check whether the specified class supplies {package!r}, required by {evidence}."))
            break


def reference_checks(root, entry, sources, issues):
    labels = {}
    bib = {}
    checks = []
    for source in sources.values():
        for expr, index in ((LABEL, labels), (BIBITEM, bib)):
            for match in expr.finditer(source.code):
                key = match.group(1).strip()
                index.setdefault(key, []).append(f"{source.name}:{source.line(match.start())}")
        for match in BIBSOURCE.finditer(source.code):
            for name in match.group(1).split(","):
                bibfile = project_path(name.strip(), source.path, entry, root, ".bib")
                if not bibfile:
                    issues.append(source.issue(match.start(), "unresolved_bibliography", f"Verify bibliography file {name.strip()!r} against supplied assets and actual backend search paths."))
                    continue
                try:
                    entries = bibfile.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    issues.append(source.issue(match.start(), "bibliography_encoding", f"Inspect the encoding of {name.strip()!r} before validating keys."))
                    continue
                line_breaks = [m.start() for m in re.finditer("\n", entries)]
                for item in BIBENTRY.finditer(entries):
                    loc = f"{bibfile.relative_to(root).as_posix()}:{bisect.bisect_left(line_breaks, item.start()) + 1}"
                    owners = bib.setdefault(item.group(1), [])
                    if loc not in owners:
                        owners.append(loc)
        for expr, kind, index in ((REF, "reference", labels), (CITE, "citation", bib)):
            for match in expr.finditer(source.code):
                for key in match.group(1).split(","):
                    key = key.strip()
                    if SIMPLE_KEY.fullmatch(key):
                        checks.append((source, match.start(), kind, key, index))
    for source, offset, kind, key, index in checks:
        owners = index.get(key, [])
        if len(owners) == 1:
            continue
        suggestions = difflib.get_close_matches(key, index, n=3, cutoff=0.65) if not owners else []
        issues.append(source.issue(offset, f"{kind}_key_review",
            f"{key!r} has {len(owners)} literal definitions in indexed material. Verify target identity using the original source and final build.",
            key=key, definitions=owners, candidates={k: index[k] for k in suggestions}))


GRAPHICS = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
IMAGE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".eps", ".mps", ".svg", ".ps", ".bmp", ".tif", ".tiff"}


def graphics_checks(root, entry, sources, issues):
    calls = [(src, match) for src in sources.values() for match in GRAPHICS.finditer(src.code)]
    if not calls:
        return
    # Honor the working directory and literal graphicspath entries; installed packages,
    # macro-generated paths, and backend-specific conversions require runtime evidence.
    bases = [entry.parent]
    for source in sources.values():
        for declaration in re.finditer(r"\\graphicspath\s*(?=\{)", source.code):
            content = braced(source.code, declaration.end())
            if content:
                for dirname in re.findall(r"\{([^{}\\#$]*)\}", content[0]):
                    folder = (entry.parent / dirname.strip()).resolve()
                    if folder.is_relative_to(root) and folder not in bases:
                        bases.append(folder)
    images = []
    for path in root.rglob("*"):
        if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file() and path.resolve().is_relative_to(root):
            images.append(path)
    for source, match in calls:
        argument = match.group(1).strip()
        name = Path(argument)
        if not argument or any(ch in argument for ch in "\\#$") or name.is_absolute() or name.drive:
            continue
        expected = []
        for folder in bases:
            # Resolve lexical dots without following symlinks or canonicalizing case on Windows.
            requested = Path(os.path.normpath(folder / name))
            if requested.is_relative_to(root):
                expected.append(requested.relative_to(root).as_posix())
        available = {}
        alternatives = []
        for path in images:
            supplied = path if name.suffix else path.with_suffix("")
            rel = supplied.relative_to(root).as_posix()
            if rel in expected:
                break
            if supplied.name.casefold() == name.name.casefold():
                alternatives.append(path.relative_to(root).as_posix())
            for folder, request in ((base, Path(os.path.normpath(base / name))) for base in bases):
                if request.as_posix().casefold() == supplied.as_posix().casefold():
                    corrected = os.path.relpath(supplied, folder).replace(os.sep, "/")
                    available[corrected] = path.relative_to(root).as_posix()
        else:
            if len(available) == 1:
                corrected, actual = next(iter(available.items()))
                source.edit(match.start(1), match.end(1), corrected, "graphic_path_case",
                            f"Case-sensitive TeX path spelling differs from supplied asset {actual}.")
            else:
                issues.append(source.issue(match.start(), "graphic_path_review",
                    f"Confirm the file named by {argument!r} against the original figure and the graphics driver.",
                    candidates=sorted(set(alternatives))[:10]))


def finish(sources, issues, apply):
    changes = []
    patches = []
    for source in sources.values():
        if not source.edits:
            continue
        updated = source.text
        for start, stop, value, record in reversed(sorted(source.edits, key=lambda item: item[0])):
            updated = updated[:start] + value + updated[stop:]
        changes.extend(item[3] for item in source.edits)
        diff = difflib.unified_diff(source.text.splitlines(), updated.splitlines(),
                                   f"a/{source.name}", f"b/{source.name}", lineterm="")
        patches.extend(diff)
        if apply:
            data = updated.encode("utf-8-sig" if source.bom else "utf-8")
            fd, tmp = tempfile.mkstemp(dir=source.path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.chmod(tmp, source.path.stat().st_mode)
                os.replace(tmp, source.path)
            finally:
                Path(tmp).unlink(missing_ok=True)
    return dict(changes_applied=changes if apply else [], proposed_changes=changes,
                static_issues=issues, patch_diff="\n".join(patches))


def audit(root, main, apply, follow):
    issues = []
    sources = sources_from(root, main, follow, issues)
    for source in sources.values():
        syntax_checks(source, issues)
        review_percent(source, issues)
    if main in sources:
        package_edits(sources[main], sources, issues)
    if follow:
        reference_checks(root, main, sources, issues)
        graphics_checks(root, main, sources, issues)
    return dict(file=main.relative_to(root).as_posix(), audited_files=[s.name for s in sources.values()],
                **finish(sources, issues, apply))


def lint_and_fix_project(project_root: Path, main_tex: str | Path, apply_fixes: bool = True):
    root = Path(project_root).resolve()
    main = (root / main_tex).resolve()
    if not main.is_relative_to(root):
        raise ValueError("Main TeX source must be inside the project.")
    return audit(root, main, apply_fixes, True)


def lint_and_fix_tex_file(tex_path: Path, apply_fixes: bool = True):
    path = Path(tex_path).resolve()
    return audit(path.parent, path, apply_fixes, False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tex", type=Path)
    group.add_argument("--project", type=Path)
    parser.add_argument("--main", help="Root manuscript, relative to --project")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out-diff", type=Path)
    parser.add_argument("--out-log", type=Path)
    args = parser.parse_args()
    if args.project and not args.main:
        parser.error("--project requires --main")
    result = lint_and_fix_project(args.project, args.main, args.apply) if args.project else lint_and_fix_tex_file(args.tex, args.apply)
    if args.out_diff:
        write_text_lf(args.out_diff, result["patch_diff"] + ("\n" if result["patch_diff"] else ""))
    if args.out_log:
        write_json_atomic(args.out_log, result)
    print(json.dumps({k: result[k] for k in ("file", "audited_files", "changes_applied", "static_issues")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
