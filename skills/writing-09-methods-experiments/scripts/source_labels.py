"""Expand manuscript-side evidence references into a location-specific claim ledger."""

from collections import defaultdict
import hashlib
import json
import re
import unicodedata


def label_for(row):
    key = [row[k] for k in ("source", "locator", "text")]
    data = json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "E" + hashlib.sha256(data).hexdigest()[:12]


MARK = re.compile(r"⟦([^⟦⟧\n]*)⟧")
ITEM = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)")


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def expand_labels(draft, evidence):
    if "⟦" not in draft and "⟧" not in draft:
        return draft, [], []
    by_id = defaultdict(set)
    for row in evidence:
        quote = row["text"]
        if row.get("kind") in {"structured", "inline", "parameter"} and " = " in quote:
            quote = quote.split(" = ", 1)[1]
        by_id[label_for(row)].add((row["source"], row["locator"], quote))
    links, issues, output = [], [], []
    fence = None
    for line_no, line in enumerate(draft.splitlines(keepends=True), 1):
        delimiter = re.match(r"^\s*(`{3,}|~{3,})", line)
        if delimiter:
            mark = delimiter[1]
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            output.append(line)
            continue
        if fence or ("⟦" not in line and "⟧" not in line):
            output.append(line)
            continue
        cursor, clean = 0, ""
        for match in MARK.finditer(line):
            fragment = line[cursor:match.start()]
            clean += fragment
            claim = ITEM.sub("", fragment).strip(" \t|")
            keys = [key.strip() for key in match[1].split(",")]
            hits = [by_id.get(key, set()) for key in keys]
            if not claim or not all(len(found) == 1 for found in hits):
                issues.append({"code": "invalid_evidence_label", "line": line_no, "labels": keys})
            else:
                occurrence = normalized(clean).count(normalized(claim))
                for found in hits:
                    source, locator, quote = next(iter(found))
                    links.append({"claim": claim, "source": source, "locator": locator, "quote": quote,
                                  "line": line_no, "occurrence": occurrence})
            cursor = match.end()
        clean += line[cursor:]
        if "⟦" in clean or "⟧" in clean:
            issues.append({"code": "incomplete_evidence_label", "line": line_no})
        # Preserve every unmarked line verbatim, including intentional Markdown hard breaks.
        output.append(re.sub(r"[ \t]+(\r?\n|$)", r"\1", clean))
    return "".join(output), links, issues
