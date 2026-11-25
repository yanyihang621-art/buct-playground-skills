#!/usr/bin/env python3
"""Parse TeX and bibliography logs into localized findings from the actual build."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tex_utils import write_json_atomic

FILE_LINE = re.compile(r"^(?:\./)?((?:[A-Za-z]:)?[^:\r\n]+?\.(?:tex|sty|cls|def|cfg|bib|bbl|aux)):(\d+):\s*(.*)$")
RERUN = re.compile(r"rerun to get|please \(re\)run|please rerun|rerun LaTeX|label\(s\) may have changed", re.I)
UNDEFINED = re.compile(r"\b(Reference|Citation)\s+[\x60']([^']+)'(?:\s+on page\s+(\S+))?.*?\bundefined", re.I)
OVERFULL = re.compile(r"Overfull \\([hv])box \(([\d.]+)pt too (?:wide|high)\)")
LINE = re.compile(r"(?:on input line|at lines?)\s+(\d+)")


def classify(msg):
    low = msg.lower()
    if "requires either xetex or luatex" in low or "requires either xelatex or lualatex" in low:
        return "wrong_engine", "fatal", "Use the engine specified by the template, magic comment or font package."
    if "fontspec" in low and "error" in low or "font" in low and ("cannot be found" in low or "not loadable" in low):
        return "font_missing", "error", "Match the requested font family to installed fonts. Record any authorized substitution."
    if "not found" in low and ("file" in low or ".cls" in low or ".sty" in low):
        return "missing_file_or_cls", "fatal", "Check the named asset, template or package relative to the build directory."
    if "environment" in low and "undefined" in low:
        return "missing_package", "error", "Check the environment spelling and the package that defines it."
    if "undefined control sequence" in low:
        return "undefined_control_sequence", "error", "Inspect the command in the source excerpt and the defining package."
    if "missing $" in low or "display math should end" in low or "bad math environment delimiter" in low:
        return "math_delimiter_mismatch", "error", "Match math delimiters without changing the formula."
    if "extra alignment tab" in low or "misplaced \\noalign" in low or "misplaced alignment" in low:
        return "table_column_mismatch", "error", "Inspect this row's separators, column declarations and row terminator."
    if "ended by \\end" in low:
        return "unclosed_environment", "error", "Match the named begin/end pair and inspect adjacent groups."
    if "missing }" in low or "extra }" in low or "file ended while scanning" in low or "runaway argument" in low:
        return "brace_mismatch", "error", "Inspect the preceding argument for a missing or extra brace or comment."
    if "option clash" in low:
        return "package_option_clash", "error", "Reconcile class-provided and explicitly loaded package options."
    if "unicode character" in low:
        return "unsupported_character", "error", "Match the engine and encoding to the template, preserving the source symbol."
    if "emergency stop" in low or "fatal error occurred" in low:
        return "compilation_stopped", "fatal", "Inspect the earliest preceding source error."
    return "latex_error", "error", "Inspect the cited file, line and surrounding source."


def parse_tex_logs(log_path: Path, blg_path: Path | None = None) -> dict:
    entries = []
    rerun = False
    flags = {"reference": False, "citation": False}
    keys = {"reference": [], "citation": []}

    def add(cat, severity, msg, hint, file=None, line=None, **details):
        entries.append(dict(category=cat, severity=severity, message=msg, hint=hint,
                            file=file, line=line, is_cascade=False, **details))

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.is_file() else []
    for i, ln in enumerate(lines):
        msg = ln.strip()
        rerun |= bool(RERUN.search(msg))
        error = FILE_LINE.match(ln)
        if error or ln.startswith("!"):
            file, number, error_msg = (error[1].replace("\\", "/"), int(error[2]), error[3]) if error else (None, None, ln.lstrip("! "))
            if file and i and re.match(r"^(?:[A-Z]:[\\/]|/)", lines[i-1], re.I) and len(lines[i-1]) >= 75:
                file = (lines[i-1] + file).replace("\\", "/")
            if "font" in error_msg.lower():
                for following in lines[i+1:i+4]:
                    if following.startswith("(fontspec)") and "font" in following:
                        error_msg += " " + following.split(")", 1)[1].strip()
                        break
                    if "not loadable" in following:
                        error_msg += following.strip()
                        break
            context = ""
            for following in lines[i + 1:i + 11]:
                location = re.match(r"^l\.(\d+)\s*(.*)", following)
                if location:
                    number = number or int(location[1])
                    context = location[2].strip()
                    break
                if following.startswith("!") or FILE_LINE.match(following):
                    break
            category, severity, hint = classify(error_msg)
            add(category, severity, error_msg, hint, file, number, source_excerpt=context)
            continue
        location = LINE.search(ln)
        number = int(location[1]) if location else None
        # TeX wraps long warnings at max_print_line. Read a continuation for line/key.
        if "Warning:" in msg and i + 1 < len(lines) and re.search(r"undefined|on input line|^line \d", lines[i + 1]):
            msg += " " + lines[i + 1].strip()
            location = LINE.search(msg)
            number = int(location[1]) if location else number
        unresolved = UNDEFINED.search(msg)
        if unresolved:
            kind, key = unresolved[1].lower(), unresolved[2]
            flags[kind] = True
            if key not in keys[kind]:
                keys[kind].append(key)
            add("undefined_reference_or_citation", "warning", msg, "Check the source key, its definition, bibliography backend and rerun state.", line=number, key=key, kind=kind)
        if "undefined references" in msg.lower():
            flags["reference"] = True
        if "undefined citations" in msg.lower():
            flags["citation"] = True
        overflow = OVERFULL.search(msg)
        if overflow:
            add("overfull_" + overflow[1] + "box", "warning", msg, "Inspect the rendered page and reflow the affected row or paragraph.", line=number, overflow_pt=float(overflow[2]))
        elif "multiply defined" in msg.lower() and "Label" in msg:
            add("duplicate_label", "warning", msg, "Give distinct objects distinct labels, then update the intended references.", line=number)
        elif msg.startswith("Missing character:"):
            add("missing_glyph", "warning", msg, "Inspect the page and choose a supported font for the required glyph.", line=number)
        elif "LaTeX Font Warning:" in msg:
            add("font_warning", "warning", msg, "Check the selected font against the template and the rendered page.", line=number)

    if blg_path and blg_path.is_file():
        for i, ln in enumerate(blg_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"(?:I couldn't open|I found no \\bib|^\s*Repeated entry|^.*>\s*ERROR\s+-|^\s*ERROR\s+-|I was expecting)", ln, re.I):
                add("bib_error", "error", ln.strip(), "Check the named database, key, syntax and bibliography style.", blg_path.name, i)
            elif re.search(r"didn't find a database entry|didn.t find a database entry|cannot find.*(?:key|entry)", ln, re.I):
                flags["citation"] = True
                add("bib_missing_entry", "warning", ln.strip(), "Locate the matching entry in the source database.", blg_path.name, i)

    previous_root = None
    for entry in entries:
        if entry["severity"] not in ("fatal", "error"):
            continue
        if previous_root:
            near = (entry["file"] == previous_root["file"] and entry["line"] is not None
                    and previous_root["line"] is not None and 0 <= entry["line"] - previous_root["line"] <= 3)
            entry["is_cascade"] = entry["category"] == "compilation_stopped" or (
                near and previous_root["category"] in ("missing_package", "missing_file_or_cls", "font_missing")
                and entry["category"] in ("undefined_control_sequence", "table_column_mismatch", "latex_error"))
        if not entry["is_cascade"]:
            previous_root = entry
    errors = [e for e in entries if e["severity"] in ("fatal", "error")]
    return dict(schema_version="writing-13-diagnostics/v1", log_file=str(log_path), error_count=len(errors),
                warning_count=sum(e["severity"] == "warning" for e in entries), rerun_requested=rerun,
                has_undefined_refs=flags["reference"], has_undefined_cites=flags["citation"],
                unresolved_keys=keys, overfull_hbox_count=sum(e["category"] == "overfull_hbox" for e in entries),
                root_causes=[e for e in errors if not e["is_cascade"]][:12], diagnostics=entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True)
    parser.add_argument("--blg")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = parse_tex_logs(Path(args.log), Path(args.blg) if args.blg else None)
    write_json_atomic(Path(args.out), report)
    print(json.dumps({k: report[k] for k in ("error_count", "warning_count", "unresolved_keys")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
