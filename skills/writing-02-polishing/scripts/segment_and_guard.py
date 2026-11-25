#!/usr/bin/env python3
"""Index editable sentence spans without changing the manuscript's layout."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from text_utils import (classify_hedge_tier, classify_relation_type, extract_protected_tokens,
                        has_negation_or_scope, load_style_profiles, write_json_atomic)

BOUNDARY = re.compile(r"""[.!?](?:["”’)]*)(?=\s+\S)|[。！？]+[”’」』]*""")
ABBREVIATION = re.compile(r"(?:\b(?:figs?|tabs?|eqs?|dr|prof|vs|mr|mrs|no|vol|sec|ref|refs|cf|approx)|\bet al|[ei]\.[ge]|\b[A-Z])\.$", re.I)
LITERAL = re.compile(r"\x60+[^\x60]*\x60+|\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)|https?://\S+")
ENV = re.compile(r"\\begin\{((?:align|equation|gather|multline|table|figure|tabular|verbatim|lstlisting|tikzpicture)\*?)\}")
TEX_COMMAND = re.compile(r"\\(?:documentclass|usepackage|begin|end|newcommand|renewcommand|label|bibliography|bibliographystyle|includegraphics|author|maketitle)\b")
TEX_TITLE = re.compile(r"\\(?:(?:sub)*section|chapter|title|paragraph)\*?\{")

def spans(text):
    """Yield exact trimmed spans; retain punctuation and leave inter-sentence whitespace untouched."""
    literals = [m.span() for m in LITERAL.finditer(text)]
    cursor = 0
    for match in BOUNDARY.finditer(text):
        if any(a <= match.start() < b for a, b in literals):
            continue
        if match.group().startswith(".") and ABBREVIATION.search(text[:match.start()+1]):
            continue
        start = cursor + len(text[cursor:match.end()]) - len(text[cursor:match.end()].lstrip())
        if start < match.end():
            yield start, match.end()
        cursor = match.end()
    last = len(text.rstrip())
    start = cursor + len(text[cursor:]) - len(text[cursor:].lstrip())
    if start < last:
        yield start, last

def segment_and_guard_manuscript(manuscript_text, *, is_latex=False, style_path=None):
    profiles = load_style_profiles(style_path)
    rows = []
    end_marker = ""
    start = 0
    for line, full in enumerate(manuscript_text.splitlines(keepends=True), 1):
        body = full.rstrip("\r\n")
        txt = body.strip()
        line_start = start
        start += len(full)
        if not txt:
            continue
        left = len(body) - len(body.lstrip())
        role = "prose"
        if end_marker:
            role = "verbatim_block"
            if end_marker in txt:
                end_marker = ""
        elif txt.startswith(("\x60\x60\x60", "~~~", "$$", r"\[")) or (is_latex and ENV.match(txt)):
            role = "verbatim_block"
            marker = re.match(r"^(?:\x60{3,}|~{3,})", txt)
            if marker:
                end_marker = marker.group() if marker.group() not in txt[marker.end():] else ""
            elif txt.startswith(("$$", r"\[")):
                close = "$$" if txt.startswith("$$") else r"\]"
                end_marker = close if close not in txt[2:] else ""
            else:
                close = r"\end{" + ENV.match(txt).group(1) + "}"
                end_marker = close if close not in txt else ""
        elif re.match(r"^#{1,6}\s", txt) or (is_latex and TEX_TITLE.match(txt)):
            role = "heading"
        elif (is_latex and (TEX_COMMAND.match(txt) or txt.startswith("%"))) or re.fullmatch(r"[-=*_\s]{3,}", txt):
            role = "structural"
        boundaries = spans(txt) if role == "prose" else [(0, len(txt))]
        for a, b in boundaries:
            source = txt[a:b]
            rows.append({
                "sent_id": f"sent_{len(rows)+1:04d}", "para_id": line, "line": line, "role": role,
                "start": line_start + left + a, "end": line_start + left + b, "source_text": source,
                "protected_tokens": extract_protected_tokens(source),
                "hedge_tier": classify_hedge_tier(source, profiles),
                "relation_type": classify_relation_type(source, profiles),
                "negation_markers": has_negation_or_scope(source),
            })
    return {"schema_version": "writing-02-guarded-sentences/v2", "is_latex": is_latex,
            "source_text": manuscript_text, "sentence_count": len(rows), "sentences": rows}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuscript", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    res = segment_and_guard_manuscript(args.manuscript.read_text(encoding="utf-8"), is_latex=args.manuscript.suffix.lower() == ".tex")
    write_json_atomic(args.out, res)
    print(json.dumps({"sentence_count": res["sentence_count"]}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
