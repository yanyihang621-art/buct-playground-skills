#!/usr/bin/env python3
"""Apply reviewed sentence edits to exact source spans and produce a human-readable revision history."""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

from text_utils import write_json_atomic, write_text_lf

# Small local demonstration. The official workflow uses the editor's reviews.
DEMO_RULES = [
    (r"\bMore and more researchers\b", "An increasing number of researchers", "register", "Use a formal trend expression and retain researchers as the subject."),
    (r"\bIn a word,\s*", "In summary, ", "register", "Replace the conversational transition."),
    (r"\b(?:As is known to all|Needless to say),\s*", "", "concision", "Delete the unsupported introductory aside."),
    (r"\ba lot of\b", "many", "register", "Use a concise quantifier for the count noun."),
    (r"\bpretty good\b", "good", "register", "Remove the conversational modifier."),
    (r"\bIn order to investigate\b", "To investigate", "concision", "Shorten the purpose clause."),
    (r"\bThe results shows\b", "The results show", "grammar", "Use a plural verb with results."),
    (r"众所周知[，,]\s*|毋庸置疑[，,]\s*", "", "concision", "删去句首评述语，直接陈述原文主张。"),
    (r"划时代突破式的", "", "register", "删去修饰失活现象的不当宣传语，保留原文对失活的判断。"),
]

def _demo_edit(source, glossary):
    text = source
    types, reasons = [], []
    for entry in glossary["entries"]:
        if entry.get("bilingual"):
            continue
        for variant in entry.get("variants", []):
            if variant == entry["canonical"]:
                continue
            pattern = r"(?<![A-Za-z0-9-])" + re.escape(variant) + r"(?![A-Za-z0-9-])"
            revised = re.sub(pattern, lambda _: entry["canonical"], text)
            if revised != text:
                types.append("terminology")
                reasons.append(f"Use the supplied term {entry['canonical']} for {variant}.")
                text = revised
    for pattern, replacement, category, reason in DEMO_RULES:
        revised = re.sub(pattern, replacement, text, flags=re.I)
        if revised != text:
            types.append(category)
            reasons.append(reason)
            if not replacement and re.match(pattern, text, re.I) and revised[:1].isascii():
                revised = revised[:1].upper() + revised[1:]
            text = revised
    return text, "+".join(dict.fromkeys(types)) or "unchanged", " ".join(reasons) or "No local demonstration rule matched."

def _patch(source, target):
    lines = difflib.unified_diff(source.splitlines(keepends=True), target.splitlines(keepends=True),
                                 fromfile="a/manuscript", tofile="b/manuscript")
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)

def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")

def changed_spans(source, revised):
    """Locate changed words and punctuation in both versions of a sentence."""
    pattern = re.compile(r"[\u3400-\u9fff]|[^\W\d_]+|[\d.]+|\s+|[^\w\s]|_", re.UNICODE)
    old, new = (list(pattern.finditer(text)) for text in (source, revised))
    matcher = difflib.SequenceMatcher(None, [m.group() for m in old], [m.group() for m in new], autojunk=False)
    changes = []
    for operation, a, b, c, d in matcher.get_opcodes():
        if operation == "equal":
            continue
        start = old[a].start() if a < len(old) else len(source)
        end = old[b-1].end() if b > a else start
        new_start = new[c].start() if c < len(new) else len(revised)
        new_end = new[d-1].end() if d > c else new_start
        changes.append({"operation": operation, "original": source[start:end], "replacement": revised[new_start:new_end],
                        "source_span": [start, end], "revision_span": [new_start, new_end]})
    return changes


def render_revision_report(doc):
    lines = [f"# Revision record · {doc['register']}", "",
             f"Status: {doc['status']} · {doc['edited_count']} of {doc['sentence_count']} spans changed.", "",
             "| ID | Source location | Original | Revision | Changed wording | Type | Reason |",
             "|---|---|---|---|---|---|---|"]
    for row in doc["sentences"]:
        loc = row.get("source_location") or {}
        if isinstance(loc, dict):
            loc = " ".join(str(loc.get(k, "")) for k in ("source", "location")).strip()
        label = loc or f"line {row['line']}"
        edits = ["“" + e["original"] + "” → “" + e["replacement"] + "”" for e in row.get("changed_spans", [])]
        vals = [row["sent_id"], label, row["source_text"], row["polished_text"], " / ".join(edits),
                row["edit_type"], row["rationale"]]
        lines.append("| " + " | ".join(_cell(x) for x in vals) + " |")
    return "\n".join(lines) + "\n"

def _edits_by_id(payload):
    if isinstance(payload, dict):
        items = payload.get("sentences", payload.get("edits", payload))
    else:
        items = payload
    if isinstance(items, list):
        return {row["sent_id"]: row for row in items if isinstance(row, dict) and "sent_id" in row}
    if isinstance(items, dict):
        return {k: v if isinstance(v, dict) else {"polished_text": v} for k, v in items.items()}
    return {}

def _heading_signature(text):
    prefix = re.match(r"(?:#{1,6}\s+|\\[a-zA-Z]+\*?\{)", text)
    if not prefix:
        return None
    return prefix.group(), (text.count("{"), text.count("}")) if text.startswith("\\") else ()

def polish_guarded_sentences(guarded, glossary, *, register="journal_article", edits_override=None):
    overrides = _edits_by_id(edits_override)
    rows, gaps = [], []
    for original in guarded["sentences"]:
        sid, role, src = (original[k] for k in ("sent_id", "role", "source_text"))
        row = dict(original)
        edit = overrides.get(sid)
        polished, category, reason = src, "preserved", "Source formatting or heading retained."
        uncertain = False
        if role in ("prose", "heading") and edit is not None:
            polished = edit.get("polished_text", src)
            category = str(edit.get("edit_type", "")).strip()
            reason = str(edit.get("rationale", "")).strip()
            uncertain = bool(edit.get("uncertain"))
            issue = None
            if not isinstance(polished, str) or not polished.strip():
                issue = "empty_revision"
            elif edit.get("source_text", src) != src:
                issue = "stale_source"
            elif role == "heading" and _heading_signature(src) != _heading_signature(polished):
                issue = "heading_markup_changed"
            elif "\n" in polished or "\r" in polished:
                issue = "revision_crosses_source_line"
            if issue:
                polished, category, reason = src, "needs_review", f"Retained source ({issue})."
                gaps.append({"code": issue, "sent_id": sid})
                uncertain = True
            if isinstance(edit.get("translation"), str):
                row["translation"] = edit["translation"]
            if isinstance(edit.get("changes"), list):
                row["changes"] = edit["changes"]
        elif role == "prose":
            if edits_override is None:
                polished, category, reason = _demo_edit(src, glossary)
            else:
                category, reason, uncertain = "unreviewed", "No editor decision received for this sentence.", True
                gaps.append({"code": "unreviewed_sentence", "sent_id": sid})
        row.update(polished_text=polished, edit_type=category, rationale=reason, uncertain=uncertain)
        if polished != src:
            row["changed_spans"] = changed_spans(src, polished)
        rows.append(row)

    source = guarded.get("source_text")
    if source is None:  # An older serialized guard index does not contain source offsets.
        source = "\n\n".join(r["source_text"] for r in rows) + "\n"
        target = "\n\n".join(r["polished_text"] for r in rows) + "\n"
    else:
        parts, cursor = [], 0
        for row in rows:
            a, b = row["start"], row["end"]
            if a < cursor or source[a:b] != row["source_text"]:
                raise ValueError(f"Inconsistent source span {row['sent_id']}")
            parts.extend([source[cursor:a], row["polished_text"]])
            cursor = b
        target = "".join([*parts, source[cursor:]])

    doc = {"schema_version": "writing-02-polish-doc/v1", "register": register,
           "status": "partial" if glossary.get("conflicts") or any(r["uncertain"] for r in rows) else "completed",
           "sentence_count": len(rows), "edited_count": sum(r["source_text"] != r["polished_text"] for r in rows),
           "sentences": rows, "glossary": glossary, "uncertainties": gaps, "qa_report": {}}
    return {"polished_doc": doc, "polished_manuscript": target, "revision_report_md": render_revision_report(doc),
            "patch_diff": _patch(source, target), "orig_reconstructed": source}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("guarded", "glossary", "edits-json", "out-doc", "out-ms", "out-report"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--register", default="journal_article")
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    res = polish_guarded_sentences(read(args.guarded), read(args.glossary), register=args.register, edits_override=read(args.edits_json))
    write_json_atomic(args.out_doc, res["polished_doc"])
    write_text_lf(args.out_ms, res["polished_manuscript"])
    write_text_lf(args.out_report, res["revision_report_md"])

if __name__ == "__main__":
    main()
