#!/usr/bin/env python3
"""Insert claim-level citations, renumber sequentially, and synchronize bibliography."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from format_references import format_library
from io_utils import write_json_atomic, write_text_lf

REF_SECTION_RE = re.compile(
    r"^(?:#{1,3}\s*(?:References?|参考文献|Bibliography)|\\begin\{thebibliography\}|\\bibliography\{)",
    re.IGNORECASE | re.MULTILINE,
)
CITE_TAG_RE = re.compile(
    r"\[@([A-Za-z0-9_:\-.;,\s@]+?)\]|\[CITE:([A-Za-z0-9_:\-.;,\s]+?)\]|\\cite[pt]?\{([^}]+)\}"
)


def _compress_numeric_list(nums: List[int]) -> str:
    if not nums:
        return ""
    uniq = sorted(set(nums))
    ranges: List[str] = []
    start = prev = uniq[0]
    for n in uniq[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}" if start == prev else (f"{start},{prev}" if prev == start + 1 else f"{start}–{prev}"))
            start = prev = n
    ranges.append(f"{start}" if start == prev else (f"{start},{prev}" if prev == start + 1 else f"{start}–{prev}"))
    return "[" + ",".join(ranges) + "]"


def _format_author_year_inline(recs: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for r in recs:
        auths = r.get("authors", [])
        yr = r.get("year") or "n.d."
        if not auths:
            who = "Anonymous"
        elif len(auths) == 1:
            who = auths[0].split(",")[0].split()[-1]
        elif len(auths) == 2:
            a1 = auths[0].split(",")[0].split()[-1]
            a2 = auths[1].split(",")[0].split()[-1]
            who = f"{a1} & {a2}"
        else:
            a1 = auths[0].split(",")[0].split()[-1]
            who = f"{a1} et al."
        parts.append(f"{who}, {yr}")
    return "(" + "; ".join(parts) + ")"


def _extract_sentence_context(text: str, pos: int) -> str:
    left = max(text.rfind("\n", 0, pos), text.rfind(". ", 0, pos), text.rfind("。", 0, pos))
    right_candidates = [idx for idx in (text.find("\n", pos), text.find(". ", pos), text.find("。", pos)) if idx != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[max(0, left + 1) : right].strip()


def _apply_claim_insertions(body: str, insert_claims: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """Insert [@key] directly at the target claim clause rather than paragraph end."""
    logs: List[Dict[str, Any]] = []
    updated = body
    for item in insert_claims:
        phrase = str(item.get("target_phrase", "")).strip()
        keys = [str(k).strip() for k in item.get("cite_keys", []) if str(k).strip()]
        if not phrase or not keys:
            continue
        idx = updated.find(phrase)
        if idx == -1:
            logs.append({"action": "claim_insert_missed", "target_phrase": phrase, "cite_keys": keys})
            continue
        insert_pos = idx + len(phrase)
        tag = " " + "[" + "; ".join(f"@{k}" for k in keys) + "]"
        # Avoid duplicate insertion if already immediately followed by citation tag
        after = updated[insert_pos : insert_pos + 32]
        if not re.match(r"^\s*\[(?:@|\d)", after):
            updated = updated[:insert_pos] + tag + updated[insert_pos:]
            logs.append(
                {
                    "action": "claim_inserted",
                    "target_phrase": phrase,
                    "inserted_tag": tag.strip(),
                    "line": updated.count("\n", 0, insert_pos) + 1,
                }
            )
    return updated, logs


def sync_manuscript(
    manuscript_text: str,
    canonical_library: Dict[str, Any],
    *,
    style: str = "gbt7714",
    is_latex: bool = False,
    insert_claims: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Synchronize in-text citations, numbers/author-year labels, and bibliography section."""
    records = canonical_library.get("records", [])
    alias_to_rec: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        alias_to_rec[rec["cite_key"]] = rec
        for al in rec.get("aliases", []):
            alias_to_rec[al] = rec

    m_sec = REF_SECTION_RE.search(manuscript_text)
    body = manuscript_text[: m_sec.start()].rstrip() if m_sec else manuscript_text.rstrip()

    change_log: List[Dict[str, Any]] = []
    if insert_claims:
        body, ins_logs = _apply_claim_insertions(body, insert_claims)
        change_log.extend(ins_logs)

    cited_order: List[str] = []
    key_to_num: Dict[str, int] = {}
    unresolved_keys: List[Dict[str, Any]] = []
    in_text_occurrences: List[Dict[str, Any]] = []

    def _split_keys(raw: str) -> List[str]:
        parts = re.split(r"[;,]\s*", raw.replace("@", "").strip())
        return [p.strip() for p in parts if p.strip()]

    def _replace_match(m: re.Match[str]) -> str:
        raw_grp = m.group(1) or m.group(2) or m.group(3) or ""
        raw_keys = _split_keys(raw_grp)
        line_no = body.count("\n", 0, m.start()) + 1
        ctx = _extract_sentence_context(body, m.start())

        resolved_recs: List[Dict[str, Any]] = []
        nums: List[int] = []
        canon_keys: List[str] = []

        for rk in raw_keys:
            rec = alias_to_rec.get(rk)
            if not rec:
                unresolved_keys.append({"raw_key": rk, "line": line_no, "context": ctx})
                return m.group(0)
            ck = rec["cite_key"]
            if ck not in key_to_num:
                cited_order.append(ck)
                key_to_num[ck] = len(cited_order)
            resolved_recs.append(rec)
            nums.append(key_to_num[ck])
            canon_keys.append(ck)

        if is_latex:
            replacement = f"\\cite{{{','.join(canon_keys)}}}"
        elif style.lower() == "apa7":
            replacement = _format_author_year_inline(resolved_recs)
        else:
            replacement = _compress_numeric_list(nums)

        in_text_occurrences.append(
            {
                "line": line_no,
                "original_tag": m.group(0),
                "rendered_tag": replacement,
                "cite_keys": canon_keys,
                "assigned_numbers": nums,
                "context_sentence": ctx,
            }
        )
        if m.group(0) != replacement:
            change_log.append(
                {"action": "citation_synced", "line": line_no, "from": m.group(0), "to": replacement, "keys": canon_keys}
            )
        return replacement

    synced_body = CITE_TAG_RE.sub(_replace_match, body)
    cited_records = [alias_to_rec[ck] for ck in cited_order]

    bib_text = format_library(cited_records, style="bibtex" if is_latex else style)
    if is_latex:
        synced_full = synced_body + "\n\n\\bibliography{references_synced}\n"
    elif style.lower() == "apa7":
        header = "## 参考文献 (References)"
        synced_full = synced_body + f"\n\n{header}\n\n" + bib_text
    else:
        header = "## 参考文献" if any("\u4e00" <= ch <= "\u9fff" for ch in synced_body) else "## References"
        synced_full = synced_body + f"\n\n{header}\n\n" + bib_text

    citation_map = {
        "schema_version": "writing-04-citation-map/v1",
        "style": style,
        "is_latex": is_latex,
        "cited_count": len(cited_records),
        "cited_order": [{"number": idx + 1, "cite_key": ck, "ref_id": alias_to_rec[ck]["ref_id"]} for idx, ck in enumerate(cited_order)],
        "in_text_occurrences": in_text_occurrences,
        "unresolved_keys": unresolved_keys,
    }

    return {
        "synced_manuscript": synced_full,
        "formatted_bibliography": bib_text,
        "citation_map": citation_map,
        "change_log": change_log,
        "cited_records": cited_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync citations and numbered/author-year bibliography in manuscript.")
    parser.add_argument("--manuscript", required=True, help="Input .md, .tex, or .txt manuscript")
    parser.add_argument("--lib", required=True, help="Canonical library JSON")
    parser.add_argument("--style", choices=("gbt7714", "apa7", "ieee"), default="gbt7714")
    parser.add_argument("--insert-map", default=None, help="Optional JSON file with insert_claims list")
    parser.add_argument("--out-manuscript", required=True, help="Output edited manuscript path")
    parser.add_argument("--out-bib", required=True, help="Output formatted bibliography path")
    parser.add_argument("--out-map", required=True, help="Output citation_map.json path")
    args = parser.parse_args()

    ms_path = Path(args.manuscript)
    text = ms_path.read_text(encoding="utf-8", errors="replace")
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    inserts = None
    if args.insert_map and Path(args.insert_map).exists():
        raw_ins = json.loads(Path(args.insert_map).read_text(encoding="utf-8"))
        inserts = raw_ins.get("insert_claims", raw_ins) if isinstance(raw_ins, dict) else raw_ins

    res = sync_manuscript(text, lib, style=args.style, is_latex=(ms_path.suffix.lower() == ".tex"), insert_claims=inserts)
    write_text_lf(Path(args.out_manuscript), res["synced_manuscript"])
    write_text_lf(Path(args.out_bib), res["formatted_bibliography"])
    write_json_atomic(Path(args.out_map), res["citation_map"])
    print(json.dumps({"cited_count": res["citation_map"]["cited_count"], "unresolved": len(res["citation_map"]["unresolved_keys"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
