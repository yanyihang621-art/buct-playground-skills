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


def anchored(text, quote):
    """Return a real line interval despite line wrapping and Unicode ligatures."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    words = unicodedata.normalize("NFKC", quote).split()
    text = unicodedata.normalize("NFKC", text)
    match = re.search(r"\s+".join(re.escape(word) for word in words), text)
    if not match:
        return None
    return [text.count("\n", 0, match.start()) + 1, text.count("\n", 0, match.end()) + 1]


def inside(root, rel):
    path = (root / safe_name(rel)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path leaves project")
    return path


def source_text(name, items, input_dir, stage):
    if name.startswith("/input/"):
        name = name.removeprefix("/input/")
    entry = items.get(name, {})
    if entry.get("text_view"):
        return inside(stage, entry["text_view"]).read_text(encoding="utf-8")
    if name.startswith("case.json#"):
        pointer = name.partition("#")[2]
        value = json.loads((input_dir / "case.json").read_text(encoding="utf-8-sig"))
        if pointer and not pointer.startswith("/"):
            raise ValueError("expected JSON Pointer after #")
        for part in pointer.split("/")[1:]:
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
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
            "scope": "Presence of quoted text. Scientific equivalence and visual layout require inspection.",
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
