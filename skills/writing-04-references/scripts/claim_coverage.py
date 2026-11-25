#!/usr/bin/env python3
"""Trace cited and uncited manuscript clauses to candidate passages in the supplied materials."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import unicodedata

from io_utils import write_json_atomic
from ingest_materials import read_docx_paragraphs

REFERENCE = re.compile(r"^\s*(?:#{1,6}\s*|\d+[.、]?\s*)?(?:references|bibliography|参考文献)(?:\s*\(references\))?[:：]?\s*$", re.I)
HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|(?:appendix|acknowledg(?:e)?ments|附录|致谢)(?:\b|[A-Z甲乙一二三\s]).*$)", re.I)
LITERAL = re.compile(r"\x60[^\x60\n]*\x60|\$\$.*?\$\$|\$[^$\n]+\$|https?://[^\s<>]+|\\(?:ref|label|eqref)\{[^}\n]*\}")
BOUNDARY = re.compile(r"[。！？;；!?]+|(?<!\d)\.(?=\s|$)|(?<=\d)\.(?=\s|$)|"
                      r"(?:[,，]\s*)?(?=\b(?:whereas|while|but|although|however)\b|(?:但是|然而|相比之下|而(?:在|对|其|该)))", re.I)
COORD = re.compile(r"[,，]\s*(?=(?:and\s+)?(?:[A-Za-z][\w-]*\s+){1,4}(?:decreas|increas|improv|reduc|declin|remain|rose|fell))", re.I)
QUANTITY = re.compile(r"(?<![\d.])([-+−]?\d+(?:[.,]\d+)?)\s*(percentage\s+points?|个百分点|%|％|mg/L|µg/mL|mg|μg|kg|mmol|mL|mmHg|kPa|MPa|°C|km|ms|hours?|days?|分钟|小时|天|秒|倍)(?![a-zA-Z])", re.I)
UP = re.compile(r"\b(?:increas\w*|rose|risen|higher|elevat\w*)\b|(?:提高|上升|增加|升高|增长)")
DOWN = re.compile(r"\b(?:decreas\w*|fell|lower|reduc\w*|declin\w*)\b|(?:降低|下降|减少|减小)")
NEG = re.compile(r"\b(?:not|no|unchanged|unaffected|failed to)\b|(?:未见|未能|没有|无显著|未改变|不显著)", re.I)
REPORT = re.compile(r"\b(?:studies|researchers|previous(?:ly)?|literature|reported|demonstrated|observed|associated|"
                    r"correlat\w*|causes?|predict\w*|suggest\w*|compared|significantly|outperform\w*)\b|"
                    r"(?:既往|研究表明|已有研究|据报道|有研究|文献报道|显著|优于|相关|导致|证实|发现)", re.I)
SELF = re.compile(r"\b(?:we|our)\b|(?:我们|本研究|本文|本实验)", re.I)
STOP = set("""a an and the or for at as with from over under can could could was were are is did do had has have by when in on to of
these this that results result study studies found used using research whereas reported previously showed shows indicate also measured
increased increase increases higher decreased decrease reduced reduction lower increased significantly than but while manuscript paragraph""".split())
CJK_STOP = {"我们","本研","研究","究表","表明","有研","发现","显著","结果","增加","下降","降低","提高","有所","可以","相关","报道","文献"}


def fold(s):
    return " ".join(unicodedata.normalize("NFKC", str(s or "")).casefold().split())


def terms(text):
    s = fold(text)
    result = {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", s) if w not in STOP}
    for sequence in re.findall(r"[\u3400-\u9fff]{2,}", s):
        result.update(sequence[i:i + 2] for i in range(len(sequence) - 1))
    return result - CJK_STOP


def clauses(line, mask=()):
    """Return exact spans; keep periods in initials, common abbreviations and decimals."""
    shadow = list(line)
    for start, end in mask:
        shadow[start:end] = " " * (end - start)
    view = "".join(shadow)
    bounds = {0, len(line)}
    for hit in BOUNDARY.finditer(view):
        if hit.group().startswith(".") and re.search(r"\b(?:e\.g|i\.e|et al|fig|eq|dr|mr|vs|[A-Za-z])$", view[:hit.start()], re.I):
            continue
        bounds.add(hit.end())
    bounds.update(m.end() for m in COORD.finditer(view))
    ordered = sorted(bounds)
    for left, right in zip(ordered, ordered[1:]):
        while left < right and line[left].isspace():
            left += 1
        while right > left and line[right - 1].isspace():
            right -= 1
        if right > left:
            yield left, right


def document_lines(text):
    in_references, fence, section = False, "", ""
    for line_no, line in enumerate(text.splitlines(), 1):
        delimiter = re.match(r"^[ \t]*(\x60{3,}|~{3,})", line)
        if delimiter:
            if not fence:
                fence = delimiter[1]
            elif delimiter[1][0] == fence[0] and len(delimiter[1]) >= len(fence):
                fence = ""
            continue
        if fence or not line.strip():
            continue
        if REFERENCE.match(line) or r"\begin{thebibliography}" in line:
            in_references = True
            continue
        if HEADING.match(line) or r"\end{thebibliography}" in line:
            in_references, section = False, line.strip().lstrip("#").strip()
            continue
        if not in_references and not line.strip().startswith(("\\documentclass", "\\usepackage", "\\bibliography", "\\begin{", "\\end{", "%")):
            yield line_no, line, section


def prose_blocks(text, soft_wrap):
    """Join adjacent Markdown lines continuing a sentence while retaining source newlines."""
    block = []
    last = ""
    for n, line, section in document_lines(text):
        continuation = (soft_wrap and block and block[-1][0] == n - 1
                        and not re.search(r"[.!?。！？:：;；|]$", last)
                        and not re.match(r"^\s*(?:[-+*]\s|\d+[.)]\s|\|)", line)
                        and (bool(re.match(r"^\s*[a-z0-9\[]", line)) or bool(re.search(r"[\u3400-\u9fff]$", last))))
        if block and not continuation:
            yield block
            block = []
        block.append((n, line, section))
        last = line.rstrip()
    if block:
        yield block


def catalog(records, passages):
    entries, postings, per_key = [], defaultdict(set), defaultdict(list)
    def add(key, text, locator, role):
        for line in str(text or "").splitlines():
            for start, end in clauses(line):
                quote = line[start:end].strip()
                if len(quote) < 6:
                    continue
                wordset = terms(quote)
                item = (key, quote, locator, role, wordset)
                i = len(entries)
                entries.append(item)
                per_key[key].append(i)
                for term in wordset:
                    postings[term].add(i)
    for rec in records:
        for field in ("title", "abstract"):
            if rec.get(field):
                add(rec["cite_key"], rec[field], {"source_field": field, "input_locator": rec.get("field_sources", {}).get(field)}, field)
    files = defaultdict(list)
    for passage in passages or []:
        if isinstance(passage, dict) and passage.get("source_file") and passage.get("text"):
            files[passage["source_file"]].append(passage)
    for name, group in files.items():
        header = fold(" ".join(item["text"] for item in group[:40])[:4000])
        header = re.split(r"\breferences\b|参考文献|\bbibliography\b", header, maxsplit=1)[0]
        matches = [r for r in records if any(value and value in header for value in
                   (fold(r.get("doi")), fold(r.get("title")) if len(fold(r.get("title"))) >= 8 else ""))]
        if len(matches) != 1:
            continue
        key = matches[0]["cite_key"]
        title = fold(matches[0].get("title")).rstrip(".")
        ended = False
        for part in group:
            for pos, line in enumerate(part["text"].splitlines(), 1):
                if REFERENCE.match(line):
                    ended = True
                if ended:
                    break
                if fold(line).rstrip(".") == title:
                    continue
                locator = {k: v for k, v in part.items() if k in ("page", "line", "paragraph", "source_file")}
                locator.update(source_field="evidence_file", line_within_passage=pos)
                add(key, line, locator, "fulltext")
    return entries, postings, per_key


def quantities(text):
    out = defaultdict(set)
    for hit in QUANTITY.finditer(fold(text)):
        unit = hit[2].lower()
        unit = {"percentage point": "percentage points", "个百分点": "percentage points", "hours": "hour", "days": "day"}.get(unit, unit)
        try:
            if re.search(r",\d{3}$", hit[1]):
                continue  # Thousands grouping and decimal comma cannot be distinguished here.
            out[unit].add(Decimal(hit[1].replace("−","-").replace(",", ".")))
        except InvalidOperation:
            continue
    return out


def direction(text):
    s = fold(text)
    if NEG.search(s):
        return "negated"
    up, down = bool(UP.search(s)), bool(DOWN.search(s))
    return "up" if up and not down else "down" if down and not up else ""


def select_evidence(claim, keys, index, top_k=3):
    entries, postings, per_key = index
    tokens = terms(claim)
    hits = set().union(*(postings.get(w, ()) for w in tokens))
    for key in keys:
        hits.update(per_key.get(key, ()))
    ranked = []
    count = len(entries)
    for i in hits:
        key, quote, locator, role, source_terms = entries[i]
        overlap = tokens & source_terms
        weight = sum(math.log((count + 1) / (len(postings[t]) + 1)) + 1 for t in overlap)
        score = weight / math.sqrt(max(1, len(source_terms)))
        if role == "title":
            score *= 0.5
        ranked.append((score, i, overlap))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    selected = []
    for key in keys:
        pool = [x for x in ranked if entries[x[1]][0] == key]
        passages = [x for x in pool if entries[x[1]][3] != "title"]
        selected.extend((passages or pool)[:2])
    used = {i for _, i, _ in selected}
    alternatives = []
    alt_keys = set(keys)
    for score, i, shared in ranked:
        key = entries[i][0]
        if key not in alt_keys and len(shared) >= 2 and i not in used:
            alternatives.append((score, i, shared))
            alt_keys.add(key)
            if len(alternatives) >= top_k:
                break
    results = []
    cq = quantities(claim)
    for score, i, overlap in selected + alternatives:
        key, quote, locator, role, _ = entries[i]
        notices = []
        sq = quantities(quote)
        if key in keys and role != "title" and len(overlap) >= 2:
            # A different value, unit or polarity is a reading prompt. It may concern
            # another population, time point or role in a comparison.
            for unit in sorted(cq.keys() & sq.keys()):
                absent = cq[unit] - sq[unit]
                if absent:
                    notices.append({"type": "different_values_same_unit", "unit": unit,
                                    "claim_values": sorted(map(str, cq[unit])), "passage_values": sorted(map(str, sq[unit])),
                                    "claim_values_missing_from_passage": sorted(map(str, absent))})
            cd, sd = direction(claim), direction(quote)
            if cd and sd and cd != sd:
                notices.append({"type":"compare_direction_or_negation", "claim":cd, "passage":sd})
        results.append({"cite_key": key, "relation": "currently_cited" if key in keys else "retrieval_candidate",
                        "basis": role, "source_quote": quote, **locator, "matched_terms": sorted(overlap)[:12],
                        "checks_for_reader": notices})
    return results


def mapped_tags(lines, citation_map, field):
    by_line = defaultdict(list)
    offsets = defaultdict(int)
    unmapped = []
    items = citation_map.get("in_text_occurrences", [])
    def line_of(occ):
        return occ.get("edited_line", occ.get("line")) if field == "rendered_tag" else occ.get("line")
    remaining = Counter((line_of(o), o.get(field, "")) for o in items)
    for idx, occ in enumerate(items, 1):
        line = line_of(occ)
        token = occ.get(field, "")
        if not isinstance(line, int) or not 1 <= line <= len(lines) or not token:
            unmapped.append(idx)
            continue
        content = lines[line - 1]
        literal = [m.span() for m in LITERAL.finditer(content)]
        positions = [m.start() for m in re.finditer(re.escape(token), content)
                     if m.start() >= offsets[line] and not any(a <= m.start() < b for a, b in literal)]
        known_count = remaining[line, token]
        remaining[line, token] -= 1
        if len(positions) > known_count:
            context = occ.get("context_sentence") or ""
            original = occ.get("original_tag") or token
            prefix = context.split(original, 1)[0].strip()[-30:] if original in context else ""
            positions = [pos for pos in positions if prefix and content[:pos].rstrip().endswith(prefix)]
        if not positions or len(positions) > known_count:
            unmapped.append(idx)
            continue
        pos = positions[0]
        end = pos + len(token)
        offsets[line] = end
        by_line[line].append({"start":pos,"end":end,"tag":token,"occurrence_index":idx,
                              "cite_keys":occ.get("cite_keys",[])})
    return by_line, unmapped


def analyze_claim_coverage(manuscript_text, canonical_library, citation_map, *, evidence_passages=(),
                           manuscript_source=None, marker_field="rendered_tag", word_paragraphs=None):
    lines = manuscript_text.splitlines()
    paragraph_by_line = []
    if word_paragraphs is not None and "\n".join(word_paragraphs).splitlines() == lines:
        for i, paragraph in enumerate(word_paragraphs, 1):
            paragraph_by_line.extend([i] * (paragraph.count("\n") + 1))
    mapped, unmatched = mapped_tags(lines, citation_map, marker_field)
    index = catalog(canonical_library.get("records", []), evidence_passages)
    rows = []
    source_name = str(manuscript_source or "").lower()
    soft_wrap = word_paragraphs is None and not source_name.endswith(".docx")
    for block in prose_blocks(manuscript_text, soft_wrap):
        first, _, section = block[0]
        line = "\n".join(content for _, content, _ in block)
        markers = []
        offset = 0
        for ln, content, _ in block:
            for marker in mapped.get(ln, []):
                markers.append({**marker, "start":offset+marker["start"], "end":offset+marker["end"],
                                "line":ln, "start_column":marker["start"]+1, "end_column_inclusive":marker["end"]})
            offset += len(content) + 1
        literal = [m.span() for m in LITERAL.finditer(line)]
        if not line.strip("-:| "):
            continue
        spans = literal + [(m["start"],m["end"]) for m in markers]
        for a,b in clauses(line,spans):
            local = [m for m in markers if a <= m["start"] < b]
            masked = list(line[a:b])
            for start,end in spans:
                if a <= start < b:
                    masked[start-a:min(end,b)-a] = " "*(min(end,b)-start)
            stripped = "".join(masked).strip(" \t。；;:：.?!？！,，")
            if len(re.findall(r"[^\W_]",stripped)) < 5:
                continue
            keys = list(dict.fromkeys(k for m in local for k in m["cite_keys"]))
            cues = []
            if quantities(stripped):
                cues.append("quantified_result")
            if REPORT.search(stripped):
                cues.append("attributed_or_empirical_claim")
            if direction(stripped):
                cues.append("direction_or_negation")
            if SELF.search(stripped):
                cues.append("check_author_own_material")
            priority = ("cited_scope" if keys else "author_result_or_method" if "check_author_own_material" in cues
                        else "inspect_missing_citation" if cues else "read_in_context")
            n = first + line.count("\n", 0, a)
            col = a - line.rfind("\n", 0, a)
            end_line = first + line.count("\n", 0, b)
            end_col = b - line.rfind("\n", 0, b) - 1
            row = {"id": f"L{n}C{col}", "line":n, "end_line":end_line,
                   "start_column":col, "end_column_inclusive":end_col,
                   "section":section,"quote":line[a:b],"triage":priority,"signals":cues,
                   "markers":[{k:v for k,v in m.items() if k not in ("start","end")} for m in local]}
            if paragraph_by_line:
                row["word_paragraph"] = paragraph_by_line[n - 1]
            if keys or cues:
                row["source_candidates"] = select_evidence(stripped,keys,index)
            positions = []
            for marker in markers:
                if marker in local:
                    continue
                gap = line[marker["end"]:a] if marker["end"] <= a else line[b-1:marker["start"]]
                if not re.search(r"[。！？!?]|\.(?=\s|$)", gap):
                    positions.append(marker)
            if positions:
                other = min(positions, key=lambda m:min(abs(m["start"]-b),abs(m["end"]-a)))
                adjacency = "adjacent_marker_on_line" if n <= other["line"] <= end_line else "adjacent_marker_in_block"
                row[adjacency] = {"occurrence_index":other["occurrence_index"],"tag":other["tag"],"line":other["line"]}
            if priority == "inspect_missing_citation" and stripped in line and line.count(stripped) == 1:
                row["insertion_anchor"] = {"line":n,"target_phrase":stripped}
            rows.append(row)
    return {"schema_version":"writing-04-clause-audit/v1","manuscript_source":manuscript_source,
            "coordinate_system":"1-based Unicode code point line and columns in the supplied manuscript text",
            "marker_field":marker_field,"unlocated_occurrence_indices":unmatched,
            "summary":{k:sum(r["triage"]==k for r in rows) for k in ("cited_scope","inspect_missing_citation","author_result_or_method","read_in_context")},
            "clauses":rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("manuscript","lib","map","out"):
        ap.add_argument("--"+name, type=Path, required=True)
    ap.add_argument("--passages",type=Path)
    ap.add_argument("--marker-field",choices=["rendered_tag","original_tag"],default="rendered_tag")
    args = ap.parse_args()
    paragraphs = read_docx_paragraphs(args.manuscript) if args.manuscript.suffix.lower()==".docx" else None
    manuscript = ("\n".join(paragraphs) if paragraphs is not None
                  else args.manuscript.read_text(encoding="utf-8-sig"))
    def read(p):
        return json.loads(p.read_text(encoding="utf-8-sig"))
    report = analyze_claim_coverage(manuscript, read(args.lib), read(args.map), manuscript_source=args.manuscript.name,
                                    evidence_passages=read(args.passages) if args.passages else [], marker_field=args.marker_field,
                                    word_paragraphs=paragraphs)
    write_json_atomic(args.out,report)
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
