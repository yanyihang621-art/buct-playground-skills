#!/usr/bin/env python3
"""Check claimed source and output quotations and give their actual positions for review."""
import argparse
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from prepare_case import pdf_text, safe_name
from tex_utils import write_json_atomic


def normalized(text):
    return unicodedata.normalize("NFKC", text).replace("\N{MINUS SIGN}", "-")


def quote_pattern(quote):
    quote = normalized(quote).strip()
    pattern = r"\s+".join(re.escape(word) for word in quote.split())
    # An anchor may start/end with a number, even inside a longer prose quotation.
    # Keep TeX punctuation, CSV commas, percent signs and trailing sentence stops legal.
    if re.match(r"[+-]?(?:\d|\.\d)", quote):
        pattern = r"(?<![\d.+-])(?<!\d[eE])" + pattern
    if quote[-1:].isdigit():
        pattern += r"(?!\d|\.\d|[eE][+-]?\d)"
    return pattern


def anchored(text, quote):
    """Return a real line interval despite line wrapping and Unicode ligatures."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    text = normalized(text)
    match = re.search(quote_pattern(quote), text)
    if not match:
        return None
    return [text.count("\n", 0, match.start()) + 1, text.count("\n", 0, match.end()) + 1]


def word_row(page_number, words):
    """Join words by x while retaining a map from text offsets to PDF coordinates."""
    pieces = []
    coords = []
    index = 0
    for word in sorted(words, key=lambda w: w[0]):
        value = normalized(word[4])
        pieces.append(value)
        coords.append((index, index + len(value), word[:4]))
        index += len(value) + 1
    return page_number, " ".join(pieces), coords, None


def physical_rows(pdf):
    try:
        import fitz
    except ImportError:
        process = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
                                 capture_output=True, timeout=45, check=True)
        result = []
        for page, body in enumerate(process.stdout.decode("utf-8").split("\f"), 1):
            for lineno, line in enumerate(body.splitlines(), 1):
                if line.strip():
                    result.append((page, normalized(line), None, lineno))
        return result
    result = []
    with fitz.open(pdf) as doc:
        for page_number, page in enumerate(doc, 1):
            # get_text("text") can serialize separate cells on separate logical lines.
            # Group by physical vertical center instead, then read the cells left to right.
            words = sorted(page.get_text("words"), key=lambda w: (w[1] + w[3]) / 2)
            current = []
            center = height = 0
            for w in words:
                cy, h = (w[1] + w[3]) / 2, w[3] - w[1]
                if current and cy - center > 0.32 * min(h, height):
                    result.append(word_row(page_number, current))
                    current = []
                if not current:
                    center, height = cy, h
                current.append(w)
            if current:
                result.append(word_row(page_number, current))
    return result


def matching_rows(rows, values, page):
    if (not isinstance(values, list) or len(values) < 2 or
            any(type(v) not in (str, int, float) or not str(v).strip() for v in values)):
        raise ValueError("pdf_row requires at least two nonempty cell values in visual order")
    pattern = re.compile(r"(?<!\S)" + r"\s+".join(quote_pattern(str(v)) for v in values) + r"(?!\S)")
    matches = []
    for number, text, coords, line in rows:
        if page is not None and int(page) != number:
            continue
        for m in pattern.finditer(text):
            hit = {"page": number, "text": m.group()}
            if coords is None:
                hit.update(method="pdftotext-layout", line=line, columns=[m.start()+1, m.end()])
            else:
                boxes = [box for start, end, box in coords if start < m.end() and end > m.start()]
                hit.update(method="pymupdf-words", bbox_pt=[round(v, 2) for v in
                           (min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes))])
            matches.append(hit)
    return matches


def inside(root, rel):
    path = (root / safe_name(rel)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path leaves project")
    return path


class JsonNumber(str):
    """Literal scientific number, including trailing zeros and exponent spelling."""


def quoted_json(value):
    if isinstance(value, JsonNumber):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(quoted_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k, ensure_ascii=False) + ": " + quoted_json(v)
                               for k, v in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False)


def source_text(name, items, input_dir, stage):
    if name.startswith("/input/"):
        name = name.removeprefix("/input/")
    entry = items.get(name, {})
    if entry.get("text_view"):
        return inside(stage, entry["text_view"]).read_text(encoding="utf-8")
    if name.startswith("case.json#"):
        pointer = name.partition("#")[2]
        value = json.loads((input_dir / "case.json").read_text(encoding="utf-8-sig"),
                           parse_float=JsonNumber, parse_int=JsonNumber)
        if pointer and not pointer.startswith("/"):
            raise ValueError("expected JSON Pointer after #")
        for part in pointer.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else quoted_json(value)
    if entry.get("project_path"):
        return inside(stage / "baseline", entry["project_path"]).read_text(encoding="utf-8-sig")
    if "!/" in name:
        archive, _, member = name.partition("!/")
        return inside(stage / "unpacked", archive + "/" + member).read_text(encoding="utf-8-sig")
    return inside(input_dir, name).read_text(encoding="utf-8-sig")


def verify(input_dir, stage, project, report, pdf=None):
    entries = json.loads((stage / "materials.json").read_text(encoding="utf-8"))
    items = {entry["source"]: entry for entry in entries["files"]}
    rows = json.loads(report.read_text(encoding="utf-8-sig")).get("source_mapping", [])
    pages = {}
    pdf_error = None
    if pdf and any(row.get("pdf_quote") for row in rows):
        try:
            text = pdf_text(pdf)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            pdf_error = str(exc)
            text = ""
        matches = list(re.finditer(r"(?m)^\[PDF page (\d+)\]\n", text))
        for idx, marker in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            pages[int(marker[1])] = text[marker.end():end]
    layout = []
    layout_error = None
    if pdf and any("pdf_row" in row for row in rows):
        try:
            layout = physical_rows(pdf)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            layout_error = str(exc)
    checked = []
    for index, row in enumerate(rows, 1):
        check = {"index": index, "verified": False}
        missing = []
        failures = []
        try:
            for side in ("source", "target"):
                quote = row.get(side + "_quote")
                if not quote:
                    missing.append(side + "_quote")
                    continue
                check[side] = row[side]
                body = (source_text(row["source"], items, input_dir, stage) if side == "source"
                        else inside(project, row["target"]).read_text(encoding="utf-8-sig"))
                check[side + "_lines"] = anchored(body, quote)
                if not check[side + "_lines"]:
                    failures.append(side + " quote not found")
            if row.get("pdf_quote") and pdf:
                requested = row.get("page")
                check["pdf_pages"] = [n for n, page in pages.items()
                                      if (requested is None or n == int(requested)) and anchored(page, row["pdf_quote"])]
                if not check["pdf_pages"]:
                    failures.append(pdf_error or "PDF quote not found on requested page(s)")
            elif row.get("pdf_quote"):
                failures.append("PDF unavailable for requested quote")
            if "pdf_row" in row:
                hits = matching_rows(layout, row["pdf_row"], row.get("page"))
                check["pdf_row_matches"] = hits
                if not hits:
                    failures.append(layout_error or ("PDF row cells not found in order on one rendered line" if pdf else "PDF unavailable for requested row"))
        except (AttributeError, TypeError, KeyError, IndexError, ValueError, OSError) as exc:
            failures.append(str(exc))
        if failures:
            check.update(status="failed", errors=failures)
        elif missing:
            check.update(status="needs_manual_review", missing_anchors=missing)
        else:
            check.update(status="verified", verified=True)
        checked.append(check)
    return {"checked": len(checked), "verified": sum(item["verified"] for item in checked),
            "failed": sum(item["status"] == "failed" for item in checked),
            "needs_manual_review": sum(item["status"] == "needs_manual_review" for item in checked),
            "scope": "Quoted text and requested ordered PDF row cells. Scientific equivalence and visual layout require inspection.",
            "checks": checked}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("input-dir", "stage", "project-dir", "report", "out"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args()
    result = verify(args.input_dir, args.stage, args.project_dir, args.report, args.pdf)
    write_json_atomic(args.out, result)
    print(json.dumps({k: result[k] for k in ("checked", "verified", "failed", "needs_manual_review")}))
    raise SystemExit(1 if result["failed"] else 0)
