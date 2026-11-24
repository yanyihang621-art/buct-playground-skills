#!/usr/bin/env python3
"""Parse LaTeX (.log) and BibTeX/Biber (.blg) logs into structured root-cause diagnostics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tex_utils import write_json_atomic

FILE_LINE_ERR_RE = re.compile(r"^(?:\./)?((?:[A-Za-z]:)?[^:\r\n]+?\.(?:tex|sty|cls|bib)):(\d+):\s*(.+)$")
BANG_ERR_RE = re.compile(r"^!\s*(.+)$")
LINE_REF_RE = re.compile(r"^l\.(\d+)\b")
OVERFULL_RE = re.compile(r"Overfull \\hbox \(([0-9.]+pt) too wide\)")
UNDEF_REF_RE = re.compile(r"LaTeX Warning: Reference `([^']+)' on page (\d+) undefined")
UNDEF_CITE_RE = re.compile(r"LaTeX Warning: Citation `([^']+)' on page (\d+) undefined")
RERUN_RE = re.compile(r"Rerun to get (?:cross-references|citations) right|Please \(re\)run", re.I)


def _classify_error(msg: str) -> Dict[str, Any]:
    low = msg.lower()
    if "not found" in low and ("file `" in low or ".cls" in low or ".sty" in low):
        return {"category": "missing_file_or_cls", "severity": "fatal", "hint": "Verify .cls/.sty/.tex/figure path or install missing package."}
    if "environment" in low and "undefined" in low:
        return {"category": "missing_package", "severity": "error", "hint": "Import the package providing this environment (e.g., amsmath, booktabs)."}
    if "undefined control sequence" in low:
        return {"category": "undefined_control_sequence", "severity": "error", "hint": "Import missing package macro or escape literal characters."}
    if "missing $ inserted" in low or "display math should end" in low:
        return {"category": "math_delimiter_mismatch", "severity": "error", "hint": "Balance $ / $$ delimiters or wrap math tokens in math mode."}
    if "extra alignment tab" in low or "misplaced \\noalign" in low:
        return {"category": "table_column_mismatch", "severity": "error", "hint": "Verify '&' tab count matches tabular specifiers and no unescaped '%' hides row ends."}
    if "ended by \\end" in low or "emergency stop" in low:
        return {"category": "unclosed_environment", "severity": "fatal", "hint": "Close open \\begin{...} environments or brace groups."}
    if "fontspec" in low or "cannot be found" in low:
        return {"category": "font_missing", "severity": "fatal", "hint": "Select an installed system font or use pdflatex if Latin-only."}
    return {"category": "latex_error", "severity": "error", "hint": "Inspect the cited file and line number in .log."}


def parse_tex_logs(log_path: Path, blg_path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse .log and optional .blg files into structured diagnostics with cascade flags."""
    diagnostics: List[Dict[str, Any]] = []
    rerun_requested = False

    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        i = 0
        while i < len(lines):
            ln = lines[i]
            if RERUN_RE.search(ln):
                rerun_requested = True

            m_fl = FILE_LINE_ERR_RE.match(ln)
            if m_fl:
                fpath, lnum_s, msg = m_fl.group(1), m_fl.group(2), m_fl.group(3).strip()
                cls = _classify_error(msg)
                diagnostics.append(
                    {
                        "category": cls["category"],
                        "severity": cls["severity"],
                        "file": Path(fpath).name,
                        "line": int(lnum_s),
                        "message": msg,
                        "is_cascade": False,
                        "hint": cls["hint"],
                    }
                )
                i += 1
                continue

            m_bang = BANG_ERR_RE.match(ln)
            if m_bang:
                msg = m_bang.group(1).strip()
                # Avoid duplicating if immediately preceded by file:line:error for same message
                dup = bool(diagnostics and diagnostics[-1]["message"] in msg)
                if not dup:
                    lnum: Optional[int] = None
                    for j in range(i + 1, min(i + 8, len(lines))):
                        ml = LINE_REF_RE.match(lines[j])
                        if ml:
                            lnum = int(ml.group(1))
                            break
                    cls = _classify_error(msg)
                    diagnostics.append(
                        {
                            "category": cls["category"],
                            "severity": cls["severity"],
                            "file": None,
                            "line": lnum,
                            "message": msg,
                            "is_cascade": False,
                            "hint": cls["hint"],
                        }
                    )
                i += 1
                continue

            m_ref = UNDEF_REF_RE.search(ln)
            if m_ref:
                diagnostics.append(
                    {
                        "category": "undefined_reference_or_citation",
                        "severity": "warning",
                        "file": None,
                        "line": None,
                        "message": f"Undefined reference `{m_ref.group(1)}` on page {m_ref.group(2)}.",
                        "is_cascade": False,
                        "hint": "Check \\label spelling or rerun LaTeX.",
                    }
                )
            m_cite = UNDEF_CITE_RE.search(ln)
            if m_cite:
                diagnostics.append(
                    {
                        "category": "undefined_reference_or_citation",
                        "severity": "warning",
                        "file": None,
                        "line": None,
                        "message": f"Undefined citation `{m_cite.group(1)}` on page {m_cite.group(2)}.",
                        "is_cascade": False,
                        "hint": "Run bibtex/biber and verify key in .bib.",
                    }
                )
            m_ov = OVERFULL_RE.search(ln)
            if m_ov:
                lnum_ov: Optional[int] = None
                m_lines = re.search(r"at lines? (\d+)", ln)
                if m_lines:
                    lnum_ov = int(m_lines.group(1))
                diagnostics.append(
                    {
                        "category": "overfull_hbox",
                        "severity": "warning",
                        "file": None,
                        "line": lnum_ov,
                        "message": ln.strip(),
                        "is_cascade": False,
                        "hint": f"Horizontal overflow ({m_ov.group(1)}); adjust table/column width or allow line wrapping.",
                    }
                )
            i += 1

    if blg_path and blg_path.exists():
        for bln in blg_path.read_text(encoding="utf-8", errors="replace").splitlines():
            low = bln.lower()
            if "i couldn't open database file" in low or "error" in low or "i found no \\bibstyle" in low:
                diagnostics.append(
                    {
                        "category": "bib_error",
                        "severity": "error",
                        "file": blg_path.name,
                        "line": None,
                        "message": bln.strip(),
                        "is_cascade": False,
                        "hint": "Check .bib path and \\bibliographystyle declaration.",
                    }
                )

    # Mark secondary cascade errors that occur after an early fatal missing file/package error
    root_seen = False
    for d in diagnostics:
        if d["severity"] in ("fatal", "error"):
            if root_seen and d["category"] in ("undefined_control_sequence", "table_column_mismatch", "latex_error"):
                d["is_cascade"] = True
            if d["category"] in ("missing_file_or_cls", "missing_package", "font_missing"):
                root_seen = True

    errors = [d for d in diagnostics if d["severity"] in ("fatal", "error")]
    warnings = [d for d in diagnostics if d["severity"] == "warning"]
    root_causes = [d for d in errors if not d["is_cascade"]]
    undef_msgs = [d for d in warnings if d["category"] == "undefined_reference_or_citation"]

    return {
        "schema_version": "writing-13-diagnostics/v1",
        "log_file": str(log_path),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "rerun_requested": rerun_requested,
        "has_undefined_refs": any("reference" in d["message"].lower() for d in undef_msgs),
        "has_undefined_cites": any("citation" in d["message"].lower() for d in undef_msgs),
        "overfull_hbox_count": sum(1 for w in warnings if w["category"] == "overfull_hbox"),
        "root_causes": root_causes[:10],
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse LaTeX .log and .blg diagnostics.")
    parser.add_argument("--log", required=True)
    parser.add_argument("--blg", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rep = parse_tex_logs(Path(args.log), Path(args.blg) if args.blg else None)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"error_count": rep["error_count"], "warning_count": rep["warning_count"], "root_causes": len(rep["root_causes"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
