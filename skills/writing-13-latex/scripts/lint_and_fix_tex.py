#!/usr/bin/env python3
"""Static LaTeX linter and deterministic safe auto-fixer with unified diff output."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tex_utils import write_json_atomic, write_text_lf

ENV_TOKEN_RE = re.compile(r"\\(begin|end)\{([A-Za-z0-9_*]+)\}")
UNESCAPED_NUM_PCT_RE = re.compile(r"(?<=\d)%")


def _check_env_balance(lines: List[str]) -> List[Dict[str, Any]]:
    stack: List[Tuple[str, int]] = []
    issues: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        code = re.sub(r"(?<!\\)%.*$", "", line)
        for kind, env in ENV_TOKEN_RE.findall(code):
            if kind == "begin":
                stack.append((env, idx))
            else:
                if not stack:
                    issues.append({"rule": "unclosed_environment", "line": idx, "message": f"Unexpected \\end{{{env}}}"})
                elif stack[-1][0] != env:
                    top_env, top_ln = stack.pop()
                    issues.append({"rule": "mismatched_environment", "line": idx, "message": f"\\begin{{{top_env}}} (line {top_ln}) ended by \\end{{{env}}}"})
                else:
                    stack.pop()
    for env, ln in stack:
        issues.append({"rule": "unclosed_environment", "line": ln, "message": f"Unclosed \\begin{{{env}}}"})
    return issues


def _check_tabular_columns(lines: List[str]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    in_tab = False
    expected_cols = 0
    for idx, line in enumerate(lines, start=1):
        code = re.sub(r"(?<!\\)%.*$", "", line).strip()
        m_beg = re.search(r"\\begin\{tabular\}\{([^}]+)\}", code)
        if m_beg:
            spec = re.sub(r"[|@{}*0-9.\[\]]", "", m_beg.group(1))
            # Count alignment letters l, c, r, p, X
            expected_cols = sum(1 for ch in spec if ch in ("l", "c", "r", "p", "m", "b", "X"))
            in_tab = expected_cols > 0
            continue
        if r"\end{tabular}" in code:
            in_tab = False
            expected_cols = 0
            continue
        if in_tab and r"\\" in code and not r"\multicolumn" in code and not r"\cline" in code:
            # Count unescaped '&'
            amps = len(re.findall(r"(?<!\\)&", code))
            if amps > expected_cols - 1:
                issues.append(
                    {
                        "rule": "tabular_column_mismatch",
                        "line": idx,
                        "message": f"Row has {amps + 1} columns (&={amps}) exceeding tabular spec ({expected_cols}).",
                    }
                )
    return issues


def lint_and_fix_tex_file(tex_path: Path, apply_fixes: bool = True) -> Dict[str, Any]:
    """Audit a .tex file and apply deterministic safe fixes when apply_fixes=True."""
    orig_text = tex_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    orig_lines = orig_text.splitlines()
    new_lines = list(orig_lines)
    changes: List[Dict[str, Any]] = []

    # 1. Escape unescaped '%' right after digits (e.g. 91.8% -> 91.8\%) in document body
    in_doc = False
    for i, ln in enumerate(new_lines):
        if r"\begin{document}" in ln:
            in_doc = True
            continue
        if not in_doc:
            continue
        if UNESCAPED_NUM_PCT_RE.search(ln):
            fixed_ln = UNESCAPED_NUM_PCT_RE.sub(r"\\%", ln)
            if fixed_ln != ln:
                new_lines[i] = fixed_ln
                changes.append({"file": tex_path.name, "line": i + 1, "rule": "escape_percent_after_digit", "before": ln.strip(), "after": fixed_ln.strip()})

    joined = "\n".join(new_lines)
    doc_beg_idx = next((i for i, ln in enumerate(new_lines) if r"\begin{document}" in ln), None)

    # 2. Ensure required standard packages exist in preamble when macros are used
    if doc_beg_idx is not None:
        preamble = "\n".join(new_lines[:doc_beg_idx])
        inserts: List[str] = []
        if any(tok in joined for tok in (r"\toprule", r"\midrule", r"\bottomrule")) and "booktabs" not in preamble:
            inserts.append(r"\usepackage{booktabs}")
            changes.append({"file": tex_path.name, "line": doc_beg_idx + 1, "rule": "add_missing_package", "package": "booktabs"})
        if any(tok in joined for tok in (r"\begin{align}", r"\eqref{")) and "amsmath" not in preamble:
            inserts.append(r"\usepackage{amsmath}")
            changes.append({"file": tex_path.name, "line": doc_beg_idx + 1, "rule": "add_missing_package", "package": "amsmath"})
        if r"\includegraphics" in joined and "graphicx" not in preamble:
            inserts.append(r"\usepackage{graphicx}")
            changes.append({"file": tex_path.name, "line": doc_beg_idx + 1, "rule": "add_missing_package", "package": "graphicx"})
        if inserts:
            new_lines = new_lines[:doc_beg_idx] + inserts + new_lines[doc_beg_idx:]
            doc_beg_idx += len(inserts)

        # 3. Ensure hyperref loads last in preamble if present early
        hyp_idxs = [i for i, ln in enumerate(new_lines[:doc_beg_idx]) if re.match(r"^\s*\\usepackage(?:\[[^\]]*\])?\{hyperref\}\s*$", ln)]
        if hyp_idxs:
            h_idx = hyp_idxs[0]
            trailing_pkgs = [j for j in range(h_idx + 1, doc_beg_idx) if re.match(r"^\s*\\usepackage", new_lines[j])]
            if trailing_pkgs:
                h_line = new_lines.pop(h_idx)
                new_lines.insert(doc_beg_idx - 1, h_line)
                changes.append({"file": tex_path.name, "line": h_idx + 1, "rule": "reorder_hyperref_last"})

    new_text = "\n".join(new_lines) + ("\n" if orig_text.endswith("\n") or new_lines else "")
    diff_lines = list(
        difflib.unified_diff(
            orig_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{tex_path.name}",
            tofile=f"b/{tex_path.name}",
            lineterm="",
        )
    )
    if apply_fixes and changes and new_text != orig_text:
        write_text_lf(tex_path, new_text)

    static_issues = _check_env_balance(new_lines if apply_fixes else orig_lines) + _check_tabular_columns(
        new_lines if apply_fixes else orig_lines
    )

    return {
        "file": tex_path.name,
        "changes_applied": changes if apply_fixes else [],
        "proposed_changes": changes,
        "static_issues": static_issues,
        "patch_diff": "\n".join(diff_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint and safely auto-fix LaTeX source files.")
    parser.add_argument("--tex", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out-diff", default=None)
    parser.add_argument("--out-log", default=None)
    args = parser.parse_args()

    res = lint_and_fix_tex_file(Path(args.tex), apply_fixes=args.apply)
    if args.out_diff:
        write_text_lf(Path(args.out_diff), res["patch_diff"] + ("\n" if res["patch_diff"] else ""))
    if args.out_log:
        write_json_atomic(Path(args.out_log), res)
    print(json.dumps({"file": res["file"], "changes": len(res["changes_applied"]), "static_issues": len(res["static_issues"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
