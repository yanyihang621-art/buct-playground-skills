#!/usr/bin/env python3
"""Segment manuscript into 1-to-1 guarded sentences with semantic invariants."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from text_utils import (
    classify_hedge_tier,
    classify_relation_type,
    extract_protected_tokens,
    has_negation_or_scope,
    load_style_profiles,
    write_json_atomic,
)

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\\(\[])|(?<=。)(?=[^\n\r\s])")


def _split_sentences_preserving_protected(text: str) -> List[str]:
    """Split a paragraph into sentences without breaking 'Fig. 1', 'et al.', or 'Eq. (1)'."""
    protected_abbrevs = ("Fig.", "Figs.", "Tab.", "Eq.", "et al.", "e.g.", "i.e.", "Dr.", "Prof.", "vs.")
    tmp = text
    placeholders: Dict[str, str] = {}
    for idx, ab in enumerate(protected_abbrevs):
        ph = f"__ABBR{idx}__"
        if ab in tmp:
            placeholders[ph] = ab
            tmp = tmp.replace(ab, ph)
    raw_parts = [p.strip() for p in SENT_SPLIT_RE.split(tmp) if p.strip()]
    out: List[str] = []
    for p in raw_parts:
        for ph, ab in placeholders.items():
            p = p.replace(ph, ab)
        out.append(p)
    return out or [text.strip()]


def segment_and_guard_manuscript(
    manuscript_text: str,
    *,
    is_latex: bool = False,
    style_path: Path | None = None,
) -> Dict[str, Any]:
    """Segment manuscript into ordered sentences while recording protected tokens and hedges."""
    profiles = load_style_profiles(style_path)
    lines = manuscript_text.replace("\r\n", "\n").splitlines()

    sentences: List[Dict[str, Any]] = []
    sent_counter = 0
    para_counter = 0
    in_verbatim = False
    verbatim_end = ""

    for line_idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue

        if in_verbatim:
            sent_counter += 1
            para_counter += 1
            sentences.append(
                {
                    "sent_id": f"sent_{sent_counter:04d}",
                    "para_id": para_counter,
                    "line": line_idx,
                    "role": "verbatim_block",
                    "source_text": raw_line,
                    "protected_tokens": extract_protected_tokens(raw_line),
                    "hedge_tier": "neutral",
                    "relation_type": "none",
                    "negation_markers": [],
                }
            )
            if verbatim_end in stripped:
                in_verbatim = False
                verbatim_end = ""
            continue

        if stripped.startswith("```") or stripped.startswith("$$") or (is_latex and any(stripped.startswith(x) for x in (r"\begin{equation}", r"\begin{align}", r"\begin{table}", r"\begin{figure}"))):
            sent_counter += 1
            para_counter += 1
            sentences.append(
                {
                    "sent_id": f"sent_{sent_counter:04d}",
                    "para_id": para_counter,
                    "line": line_idx,
                    "role": "verbatim_block",
                    "source_text": raw_line,
                    "protected_tokens": extract_protected_tokens(raw_line),
                    "hedge_tier": "neutral",
                    "relation_type": "none",
                    "negation_markers": [],
                }
            )
            if stripped.startswith("```"):
                in_verbatim, verbatim_end = True, "```"
            elif stripped.startswith("$$") and not (stripped.endswith("$$") and len(stripped) > 2):
                in_verbatim, verbatim_end = True, "$$"
            elif stripped.startswith(r"\begin{"):
                m_env = re.match(r"\\begin\{([^}]+)\}", stripped)
                if m_env and f"\\end{{{m_env.group(1)}}}" not in stripped:
                    in_verbatim, verbatim_end = True, f"\\end{{{m_env.group(1)}}}"
            continue

        is_heading = stripped.startswith("#") or (is_latex and any(stripped.startswith(c) for c in (r"\documentclass", r"\usepackage", r"\begin{document}", r"\end{document}", r"\title{", r"\author{", r"\maketitle", r"\section", r"\subsection")))
        para_counter += 1
        if is_heading:
            sent_counter += 1
            sentences.append(
                {
                    "sent_id": f"sent_{sent_counter:04d}",
                    "para_id": para_counter,
                    "line": line_idx,
                    "role": "structural" if (is_latex and not stripped.startswith(r"\section")) else "heading",
                    "source_text": raw_line,
                    "protected_tokens": extract_protected_tokens(raw_line),
                    "hedge_tier": "neutral",
                    "relation_type": "none",
                    "negation_markers": [],
                }
            )
            continue

        for s in _split_sentences_preserving_protected(stripped):
            sent_counter += 1
            sentences.append(
                {
                    "sent_id": f"sent_{sent_counter:04d}",
                    "para_id": para_counter,
                    "line": line_idx,
                    "role": "prose",
                    "source_text": s,
                    "protected_tokens": extract_protected_tokens(s),
                    "hedge_tier": classify_hedge_tier(s, profiles),
                    "relation_type": classify_relation_type(s, profiles),
                    "negation_markers": has_negation_or_scope(s),
                }
            )

    return {
        "schema_version": "writing-02-guarded-sentences/v1",
        "is_latex": is_latex,
        "sentence_count": len(sentences),
        "sentences": sentences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Segment manuscript into guarded sentences.")
    parser.add_argument("--manuscript", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    p = Path(args.manuscript)
    res = segment_and_guard_manuscript(p.read_text(encoding="utf-8", errors="replace"), is_latex=(p.suffix.lower() == ".tex"))
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"sentence_count": res["sentence_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
