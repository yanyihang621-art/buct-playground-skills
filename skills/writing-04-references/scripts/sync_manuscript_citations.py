#!/usr/bin/env python3
"""Resolve original citation markers, then number by first appearance in the edited manuscript."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

from format_references import apa_year_labels, format_library
from io_utils import first_author_surname, normalize_title, write_json_atomic, write_text_lf

HEADING = re.compile(r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\d+(?:\.\d+)*[.、]?[ \t]+)?(?:references?(?: cited)?|bibliography|引用文献|参考文献(?:\s*[(（]references[)）])?)[:：]?[ \t]*$")
ENTRY = re.compile(r"(?m)^[ \t]*(?:\[(\d+)\]|(\d+)[.)、])[ \t]*(\S.*)$")
TEX_BIB = re.compile(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}|\\bibliography\{[^}]+\}", re.S)
NUMERIC = r"\d+(?:\s*[-–—,，;；]\s*\d+)*"
AUTHOR = r"(?:[A-ZÀ-Þ][\w'’\-]+(?:[ \t]+(?:et[ \t]+al\.?|and[ \t]+[\w'’\-]+|&[ \t]+[\w'’\-]+))?|[\u3400-\u9fff]{2,8}(?:等)?)"
AY_ITEM = rf"{AUTHOR}\s*[,，]?\s*(?:19|20)\d{{2}}[a-z]?"
MARKER = re.compile(
    r"(?P<literal>" + r"\x60{3}[\s\S]*?\x60{3}|\x60[^\n\x60]+\x60|\$\$[\s\S]*?\$\$|\$[^\n$]+\$|\\\[[\s\S]*?\\\])"
    r"|(?P<key>\[(?:@|CITE:)[^\]\n]+\])"
    r"|(?P<tex>\\(?:cite(?:p|t|alp|author|year)?|parencite|textcite|autocite)\*?(?:\[[^]\n]*\]){0,2}\{(?P<ids>[^}\n]+)\})"
    rf"|(?P<super><sup>\s*(?:\[(?P<sup_br>{NUMERIC})\]|(?P<sup_num>{NUMERIC}))\s*</sup>)"
    rf"|(?P<number>(?<![\w\\])\[(?P<numbers>{NUMERIC})\](?!\s*\())"
    rf"|(?P<attached>(?<=[\w])\[(?P<attached_numbers>{NUMERIC})\](?!\s*\())"
    rf"|(?P<author>[(（](?P<names>{AY_ITEM}(?:\s*[;；]\s*{AY_ITEM})*)[)）])"
)
DOI = re.compile(r"10\.\d{4,9}/[^\s<>]+", re.I)


def compressed(numbers):
    runs = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return "[" + ",".join(f"{r[0]}–{r[-1]}" if len(r) >= 3 else ",".join(map(str, r)) for r in runs) + "]"


def split_original(text):
    heading = HEADING.search(text)
    if not heading:
        return text, ""
    suffix = text[heading.end():]
    next_heading = re.search(r"(?im)^(?:#{1,6}\s+\S|(?:appendix\b|附录|致谢|acknowledg(?:e)?ments\b).*$)", suffix)
    end = heading.end() + next_heading.start() if next_heading else len(text)
    return text[:heading.start()] + text[end:], text[heading.start():end]


def original_numbers(section, records):
    candidates = defaultdict(set)
    rows = list(ENTRY.finditer(section))
    for idx, match in enumerate(rows):
        entry = section[match.start():rows[idx + 1].start() if idx + 1 < len(rows) else len(section)]
        dois = {s.lower().rstrip(".,;，。") for s in DOI.findall(entry)}
        normalized = " " + normalize_title(entry) + " "
        matches = [r for r in records if r.get("doi") and r["doi"].lower() in dois]
        if not dois:
            matches = [r for r in records if len(normalize_title(r.get("title"))) >= 4 and
                       " " + normalize_title(r["title"]) + " " in normalized]
        number = match.group(1) or match.group(2)
        candidates[number].add(matches[0]["cite_key"] if len(matches) == 1 else None)
    return {number: next(iter(keys)) for number, keys in candidates.items() if len(keys) == 1 and None not in keys}


def expand_numbers(raw):
    numbers = []
    for group in re.split(r"[,，;；]", raw):
        ends = [int(n.strip()) for n in re.split(r"[-–—]", group)]
        if ends[-1] < ends[0] or ends[-1] - ends[0] > 250:
            return []
        numbers.extend(str(i) for i in range(ends[0], ends[-1] + 1))
    return numbers


def apply_edits(text, edits, log, issues):
    """Anchored edits must identify one source span. Ambiguous spans stay visible."""
    for edit in edits:
        if not isinstance(edit, dict):
            issues.append({"reason": "invalid_edit"})
            continue
        phrase = edit.get("target_phrase")
        keys = edit.get("cite_keys")
        if not isinstance(phrase, str) or not phrase.strip() or not isinstance(keys, list) or not keys:
            issues.append({"reason": "invalid_edit", "target_phrase": phrase})
            continue
        matches = [m for m in re.finditer(re.escape(phrase), text)
                   if edit.get("line") is None or text.count("\n", 0, m.start()) + 1 == edit["line"]]
        if len(matches) != 1:
            issues.append({"reason": "nonunique_or_missing_anchor", "target_phrase": phrase, "matches": len(matches)})
            continue
        tag = "[" + "; ".join("@" + str(k) for k in keys) + "]"
        m = matches[0]
        replacement = tag if edit.get("replace_marker") else phrase + " " + tag
        if not edit.get("replace_marker") and re.match(r"\s*(?:\[(?:@|CITE:)|\\cite)", text[m.end():]):
            issues.append({"reason": "existing_marker_at_insertion", "target_phrase": phrase})
            continue
        text = text[:m.start()] + replacement + text[m.end():]
        log.append({"action": "marker_replaced" if edit.get("replace_marker") else "claim_inserted",
                    "from": phrase, "to": replacement, "line": text.count("\n", 0, m.start()) + 1})
    return text


def author_key(item, records):
    yr = re.search(r"((?:19|20)\d{2})([a-z]?)$", item)
    surname = re.split(r"\s+(?:et al\.?|and|&)|\s*[,，]", item[:yr.start()].strip().rstrip(",，").removesuffix("等"), maxsplit=1)[0]
    candidates = [r for r in records if first_author_surname(r["authors"]) == surname.strip().casefold()
                  and str(r.get("year")) == yr[1]]
    # Original a/b suffixes belong to the source bibliography and must not be inferred.
    return candidates[0]["cite_key"] if len(candidates) == 1 and not yr[2] else None


def author_label(rec, labels):
    names = [(a.split(",", 1)[0] if "," in a else a.split()[-1]) for a in rec["authors"] if a.strip()]
    name = "Anonymous" if not names else names[0]
    if len(names) == 2:
        name = " & ".join(names)
    if len(names) > 2:
        name += " et al."
    return name + ", " + labels[rec["cite_key"]]


def context_at(body, start, end):
    left = list(re.finditer(r"\n|[。！？]|(?<=[a-z0-9])\.(?=\s+[A-Z])", body[:start]))
    right = re.search(r"\n|[。！？]|(?<=[a-z0-9\]])\.(?=\s|$)", body[end:])
    return body[left[-1].end() if left else 0:end + right.end() if right else len(body)].strip()


def sync_manuscript(manuscript_text, canonical_library, *, style="gbt7714", is_latex=False,
                    insert_claims=None, original_number_map=None, text_edits=None, non_citations=None):
    manuscript_text = manuscript_text.replace("\r\n", "\n").replace("\r", "\n")
    records = canonical_library.get("records", [])
    aliases = defaultdict(list)
    by_key = {r["cite_key"]: r for r in records}
    for record in records:
        for alias in set([record["cite_key"], *record.get("aliases", [])]):
            aliases[alias].append(record["cite_key"])
    body, old_section = (manuscript_text, "") if is_latex else split_original(manuscript_text)
    old_map = original_numbers(old_section, records)
    issues, changes, occurrences = [], [], []
    for n, key in (original_number_map or {}).items():
        if str(n) in old_map and old_map[str(n)] != key:
            issues.append({"reason": "contradictory_number_map", "raw_key": str(n)})
            del old_map[str(n)]
        elif isinstance(key, str):
            old_map[str(n)] = key
    for edit in text_edits or []:
        before, after = edit.get("before"), edit.get("after")
        if not before or not isinstance(after, str) or not edit.get("reason") or body.count(before) != 1:
            issues.append({"reason": "unanchored_text_edit", "before": before})
            continue
        body = body.replace(before, after, 1)
        changes.append({"action": "reviewed_text_edit", "from": before, "to": after, "reason": edit["reason"],
                        "source": edit.get("source")})
    body = apply_edits(body, insert_claims or [], changes, issues)
    order = {}
    replacements = []
    unsafe_number = False
    protected = {(r.get("line"), r.get("marker"), r.get("context")) for r in (non_citations or []) if r.get("reason")}
    for marker in MARKER.finditer(body):
        if marker["literal"]:
            continue
        # Markdown link destinations, TeX optional dimensions and code are not citations.
        line = body.count("\n", 0, marker.start()) + 1
        context = context_at(body, marker.start(), marker.end())
        if any(line == ln and marker[0] == token and quote and quote in context for ln, token, quote in protected):
            continue
        if marker["key"]:
            keys = re.split(r"\s*[,;]\s*", re.sub(r"^\[(?:CITE:)?|]$", "", marker[0]).replace("@", ""))
        elif marker["tex"]:
            keys = [k.strip() for k in marker["ids"].split(",")]
        elif marker["author"]:
            keys = [author_key(x.strip(), records) for x in re.split(r"[;；]", marker["names"])]
        else:
            nums = expand_numbers(marker["numbers"] or marker["attached_numbers"] or marker["sup_br"] or marker["sup_num"])
            keys = [old_map.get(n) for n in nums]
        if not keys or any(len(aliases.get(k, [])) != 1 for k in keys):
            if marker["number"] or marker["attached"] or marker["super"]:
                unsafe_number = True
            issues.append({"line": line, "original_tag": marker[0], "reason": "unmapped_or_ambiguous",
                           "raw_keys": keys, "context": context})
            continue
        keys = list(dict.fromkeys(aliases[k][0] for k in keys))
        for k in keys:
            order.setdefault(k, len(order) + 1)
        replacements.append((marker, keys, line, context))
    cited = [by_key[k] for k in order]
    labels = apa_year_labels(cited) if style == "apa7" else {}
    parts, last = [], 0
    for m, keys, line, context in replacements:
        nums = [order[k] for k in keys]
        if is_latex:
            new = (m[0][:m.start("ids") - m.start()] + ",".join(keys) + "}"
                   if m["tex"] else "\\cite{" + ",".join(keys) + "}")
        elif style == "apa7":
            new = "(" + "; ".join(author_label(by_key[k], labels) for k in sorted(keys, key=lambda k: author_label(by_key[k], labels))) + ")"
        else:
            new = compressed(nums)
            if m["super"]:
                new = "<sup>" + new + "</sup>"
        parts.extend([body[last:m.start()], new])
        last = m.end()
        occurrences.append({"line": line, "original_tag": m[0], "rendered_tag": new, "cite_keys": keys,
                            "assigned_numbers": nums, "context_sentence": context})
        if m[0] != new:
            changes.append({"action": "citation_synced", "line": line, "from": m[0], "to": new, "keys": keys})
    edited = "".join(parts) + body[last:]
    bibliography = format_library(cited, "bibtex" if is_latex else style)
    if is_latex and cited and not issues:
        command = r"\bibliography{references_synced}"
        if re.search(r"\\(?:usepackage(?:\[[^]]*\])?\{biblatex\}|addbibresource\{)", edited):
            if re.search(r"\\addbibresource\{[^}]+\}", edited):
                edited = re.sub(r"\\addbibresource\{[^}]+\}", lambda _: r"\addbibresource{references_synced.bib}", edited, count=1)
            else:
                edited = edited.replace(r"\begin{document}", "\\addbibresource{references_synced.bib}\n\\begin{document}", 1)
            command = r"\printbibliography"
        elif r"\bibliographystyle{" not in edited:
            command = "\\bibliographystyle{" + ("apalike" if style == "apa7" else "unsrt") + "}\n" + command
        if TEX_BIB.search(edited):
            edited = TEX_BIB.sub(lambda _: command, edited, count=1)
        elif r"\printbibliography" not in edited:
            at = edited.find(r"\end{document}")
            at = len(edited) if at < 0 else at
            edited = edited[:at] + command + "\n" + edited[at:]
    elif not is_latex and ((old_section and issues) or unsafe_number):
        # Original numbering cannot safely coexist with newly assigned numbers.
        edited = manuscript_text
        changes = [{"action": "review_required", "reason": "original bibliography and body retained together"}]
        occurrences, cited, order, bibliography = [], [], {}, old_section
    elif not is_latex and cited:
        title = "## 参考文献" if re.search(r"[\u3400-\u9fff]", edited) else "## References"
        edited = edited.rstrip() + "\n\n" + title + "\n\n" + bibliography
    elif not cited and old_section:
        edited = manuscript_text
    if is_latex and occurrences:
        # Bibliography commands can insert lines into the preamble. Track final positions separately.
        search_pos = 0
        for occ in occurrences:
            found = edited.find(occ["rendered_tag"], search_pos)
            if found >= 0:
                occ["edited_line"] = edited.count("\n", 0, found) + 1
                search_pos = found + len(occ["rendered_tag"])
    citation_map = {"schema_version": "writing-04-citation-map/v2", "style": style, "is_latex": is_latex,
                    "cited_count": len(cited), "original_number_map": old_map,
                    "cited_order": [{"number": n, "cite_key": k, "ref_id": by_key[k]["ref_id"]} for k, n in order.items()],
                    "in_text_occurrences": occurrences, "unresolved_keys": issues}
    return {"synced_manuscript": edited, "formatted_bibliography": bibliography, "citation_map": citation_map,
            "change_log": changes, "cited_records": cited}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("manuscript", "lib", "out-manuscript", "out-bib", "out-map"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--style", default="gbt7714")
    p.add_argument("--insert-map", type=Path)
    args = p.parse_args()
    edits = json.loads(args.insert_map.read_text(encoding="utf-8")) if args.insert_map else []
    res = sync_manuscript(args.manuscript.read_text(encoding="utf-8"), json.loads(args.lib.read_text(encoding="utf-8")),
                          style=args.style, is_latex=args.manuscript.suffix == ".tex",
                          insert_claims=edits.get("insert_claims", []) if isinstance(edits, dict) else edits)
    write_text_lf(args.out_manuscript, res["synced_manuscript"])
    write_text_lf(args.out_bib, res["formatted_bibliography"])
    write_json_atomic(args.out_map, res["citation_map"])


if __name__ == "__main__":
    main()
