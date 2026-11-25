#!/usr/bin/env python3
"""Locate high-confidence template remnants in the selected editable manuscript and PDF."""

import argparse
import bisect
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from inspect_project import INCLUDE_RE
from lint_and_fix_tex import braced, mask_source
from prepare_case import pdf_text
from tex_utils import write_json_atomic


FIELD = re.compile(r"\\(title|shorttitle|runningtitle|author|affil|affiliation|institute)\*?\s*(?:\[[^\]]*\]\s*)?(?=\{)")
MARKER = re.compile(
    r"\[\s*(?:INSERT\s+[A-Z][\w \t\r\n()/,-]{0,85}?\s+HERE"
    r"|请(?:在此)?(?:插入|填写|补充)[\u3400-\u9fff A-Za-z]{1,20})\s*\]", re.I)
FILLERS = {
    "title": r"(?:your (?:paper |article |manuscript )?title(?: here)?|(?:paper|article|manuscript) title (?:here|goes here)|title of (?:your|the) (?:paper|article|manuscript)|insert (?:paper |article )?title here|请(?:在此)?填写(?:论文)?标题|在此输入(?:论文)?标题)",
    "author": r"(?:your (?:full )?name(?: here)?|author(?:'s)? name(?: here)?|firstname lastname|given name surname|请填写作者姓名|作者姓名待填)",
    "affiliation": r"(?:your (?:institution|university|affiliation)(?: here)?|insert (?:your )?affiliation here|institution name here|请填写(?:作者)?单位)",
}
SAMPLE_AUTHOR = re.compile(r"(?:first|second|third) author", re.I)
LITERAL_FILLER = re.compile("|".join([MARKER.pattern, *FILLERS.values()]), re.I)
LITERAL_COMMAND = re.compile(r"\\(?:texttt|detokenize|lstinline)\s*(?=\{)")
DEAD_BRANCH = re.compile(r"\\(?:iffalse|iftrue|if(?:case|cat|dim|eof|hbox|hmode|inner|mmode|num|odd|vmode|vbox|void|x)?|else|fi)\b")


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def blank(code, start, end):
    return "".join("\n" if ch == "\n" else " " for ch in code[start:end])


def active_text(raw):
    code, comments = mask_source(raw)
    # Inline examples in typewriter text can legitimately print a template instruction.
    edits = []
    for match in LITERAL_COMMAND.finditer(code):
        group = braced(code, match.end())
        if group:
            edits.append((match.start(), group[1]))
    for start, end in reversed(edits):
        code = code[:start] + blank(code, start, end) + code[end:]
    literal_quotes = set()
    for m in LITERAL_FILLER.finditer(raw):
        before = bisect.bisect_right(comments, m.start())
        line_start = raw.rfind("\n", 0, m.start()) + 1
        in_comment = before and comments[before - 1] >= line_start
        if not in_comment and not code[m.start():m.end()].strip():
            literal_quotes.add(normalized(m.group()).casefold())
    # Ignore branches whose truth follows directly from a literal TeX primitive.
    # Other conditions remain unknown, so the PDF decides whether the claim was printed.
    stack = []
    edits = []
    previous = 0
    for match in DEAD_BRANCH.finditer(code):
        if False in stack:
            edits.append((previous, match.start()))
        cmd = match.group()[1:]
        if cmd == "fi":
            if stack:
                stack.pop()
        elif cmd == "else":
            if stack and stack[-1] is not None:
                stack[-1] = not stack[-1]
        else:
            stack.append({"iffalse": False, "iftrue": True}.get(cmd))
        previous = match.end()
    if False in stack:
        edits.append((previous, len(code)))
    for start, end in reversed(edits):
        code = code[:start] + blank(code, start, end) + code[end:]
    return code, literal_quotes


def reachable(root, entry):
    """Follow the selected root's active literal input/include/import dependencies."""
    if not entry.is_relative_to(root) or not entry.is_file():
        return
    pending = [(entry, entry.parent)]
    seen = set()
    include_only = None
    while pending:
        path, local_base = pending.pop()
        if (path, local_base) in seen or len(seen) >= 512:
            continue
        seen.add((path, local_base))
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        code, literals = active_text(raw)
        yield path.relative_to(root).as_posix(), code, literals
        if path == entry:
            limit = re.search(r"\\includeonly\s*\{([^{}]*)\}", code)
            if limit:
                include_only = {s.strip().removesuffix(".tex") for s in limit[1].split(",")}
        for match in INCLUDE_RE.finditer(code):
            name = (match[2] or match[3] or match[6]).strip()
            if match[1] == "include" and include_only is not None and name.removesuffix(".tex") not in include_only:
                continue
            if any(c in name for c in "\\#$") or not name:
                continue
            base = local_base
            if match[4]:
                base = ((local_base if match[4] == "subimport" else entry.parent) / match[5]).resolve()
            found = False
            for folder in dict.fromkeys((base, entry.parent)):
                for suffix in ("", ".tex"):
                    target = (folder / (name + suffix)).resolve()
                    if target.is_relative_to(root) and target.is_file() and target.suffix.lower() == ".tex":
                        pending.append((target, base))
                        found = True
                        break
                if found:
                    break


def metadata(code):
    for command in FIELD.finditer(code):
        group = braced(code, command.end())
        if not group:
            continue
        field = command[1]
        category = "title" if "title" in field else "author" if field == "author" else "affiliation"
        for item in re.split(r"\\and\b", group[0]):
            # Restrict matching to an entire field or individual author. Valid anonymous
            # metadata, arbitrary prose, scientific titles and missing optional fields pass.
            value = normalized(item.strip().strip("[]").replace("~", " "))
            if re.fullmatch(FILLERS[category], value, re.I):
                yield command.start(), field, value, True
            elif category == "author" and SAMPLE_AUTHOR.fullmatch(value):
                yield command.start(), field, value, False


def check_manuscript(project_dir, main_tex, pdf=None):
    root = Path(project_dir).resolve()
    entry = (root / main_tex).resolve()
    findings = []
    files = []
    hidden_markers = set()
    for name, code, literals in reachable(root, entry):
        if name not in files:
            files.append(name)
        hidden_markers.update(literals)
        candidates = list(metadata(code))
        candidates.extend((m.start(), "body", m.group(), True) for m in MARKER.finditer(code))
        for offset, field, quote, strong in candidates:
            item = {"file": name, "line": code.count("\n", 0, offset) + 1,
                    "field": field, "quote": normalized(quote), "strong": strong}
            if item not in findings:
                findings.append(item)

    pages = {}
    rendered = "not_available"
    reason = None
    if pdf:
        try:
            content = pdf_text(Path(pdf))
            sections = re.split(r"(?m)^\[PDF page (\d+)\]\n", content)[1:]
            pages = {int(sections[i]): normalized(sections[i + 1]) for i in range(0, len(sections), 2)}
            rendered = "checked"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            reason = str(exc)
    blockers = []
    review = []
    for item in findings:
        # Avoid partial matches such as the placeholder "Author Name" inside "Author Names".
        pattern = re.compile(r"(?<!\w)" + re.escape(item["quote"]) + r"(?!\w)", re.I)
        hits = [p for p, body in pages.items() if pattern.search(body)]
        strong = item.pop("strong")
        if hits:
            item["pages"] = hits
        if hits and strong and item["quote"].casefold() not in hidden_markers:
            blockers.append(dict(item, reason="Template fill-in text in active manuscript and rendered PDF"))
        else:
            review.append(dict(item, reason="Confirm source field against the task, active template and rendered edition"))
    known = {item["quote"].casefold() for item in findings} | hidden_markers
    for page, body in pages.items():
        for marker in MARKER.finditer(body):
            quote = normalized(marker.group())
            if quote.casefold() not in known:
                review.append({"field": "rendered_body", "page": page, "quote": quote,
                               "reason": "Rendered marker without an attributable editable source; check template or example"})
    return {"checked_files": files, "rendered_check": rendered, "pdf_read_error": reason,
            "blockers": blockers, "review": review}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--main-tex", required=True)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = check_manuscript(args.project_dir, args.main_tex, args.pdf)
    write_json_atomic(args.out, result)
    print(json.dumps({k: len(result[k]) for k in ("blockers", "review")}))
    raise SystemExit(1 if result["blockers"] else 0)
