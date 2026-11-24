#!/usr/bin/env python3
"""6-check deterministic citation & bibliography QA verifier."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Set

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import is_valid_doi_syntax, normalize_title, title_similarity, write_json_atomic

STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "have", "has",
    "been", "using", "used", "study", "results", "show", "shown", "high", "low", "over", "into",
    "paper", "method", "based", "analysis", "between", "their", "these", "also", "such",
}
UNRESOLVED_TAG_RE = re.compile(r"\[CITE:|\[@|TODO|TBD|FIXME", re.IGNORECASE)


def _content_tokens(text: str) -> Set[str]:
    norm = normalize_title(text)
    return {w for w in norm.split() if len(w) >= 3 and w not in STOP_WORDS}


def _check_doi_online(doi: str) -> bool:
    url = f"https://api.crossref.org/works/{doi}"
    req = urllib.request.Request(url, headers={"User-Agent": "buct-writing04-verifier/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def verify_citations(
    synced_manuscript: str,
    citation_map: Dict[str, Any],
    canonical_library: Dict[str, Any],
    dedup_report: Dict[str, Any],
    raw_library: Dict[str, Any],
    *,
    verify_doi_online: bool = False,
) -> Dict[str, Any]:
    """Run 6 deterministic checks across numbering, sync, dedup, DOIs, alignment, and honesty."""
    records = canonical_library.get("records", [])
    by_key = {r["cite_key"]: r for r in records}
    cited_order = citation_map.get("cited_order", [])
    occurrences = citation_map.get("in_text_occurrences", [])
    unresolved = citation_map.get("unresolved_keys", [])
    parse_errors = raw_library.get("parse_errors", [])

    violations: List[Dict[str, Any]] = []
    uncertainties: List[Dict[str, Any]] = []

    # 1. Numbering continuity (1..N contiguous with no gaps)
    assigned = [int(item["number"]) for item in cited_order]
    expected = list(range(1, len(cited_order) + 1))
    numbering_ok = (assigned == expected)
    if not numbering_ok:
        violations.append({"check": "numbering_continuity", "assigned": assigned, "expected": expected})

    # 2. Bidirectional sync (zero dangling refs, zero uncited bibliography entries if using cited_order)
    in_text_keys = {k for occ in occurrences for k in occ.get("cite_keys", [])}
    bib_keys = {item["cite_key"] for item in cited_order}
    missing_from_bib = sorted(in_text_keys - bib_keys)
    missing_from_lib = sorted(k for k in (in_text_keys | bib_keys) if k not in by_key)
    orphan_bib = sorted(bib_keys - in_text_keys)
    sync_ok = (len(unresolved) == 0) and (not missing_from_bib) and (not missing_from_lib) and (not orphan_bib)
    if not sync_ok:
        violations.append(
            {
                "check": "bidirectional_sync",
                "unresolved_keys": unresolved,
                "missing_from_bib": missing_from_bib,
                "missing_from_lib": missing_from_lib,
                "orphan_bib": orphan_bib,
            }
        )

    # 3. Dedup integrity (no remaining near-identical canonical records)
    dedup_ok = True
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            ra, rb = records[i], records[j]
            if ra.get("doi") and rb.get("doi") and ra["doi"].lower() == rb["doi"].lower():
                dedup_ok = False
                violations.append({"check": "dedup_integrity", "pair": [ra["cite_key"], rb["cite_key"]], "reason": "duplicate_doi"})
            elif title_similarity(ra.get("title", ""), rb.get("title", "")) >= 0.95 and ra.get("year") == rb.get("year"):
                dedup_ok = False
                violations.append({"check": "dedup_integrity", "pair": [ra["cite_key"], rb["cite_key"]], "reason": "duplicate_title_year"})

    # 4. Metadata & DOI syntax / optional online validation
    meta_ok = True
    doi_mode = "offline_syntax_only"
    for ck in sorted(bib_keys):
        rec = by_key.get(ck)
        if not rec:
            continue
        if rec.get("missing_fields"):
            uncertainties.append({"cite_key": ck, "code": "missing_metadata_fields", "fields": rec["missing_fields"]})
            if "title" in rec["missing_fields"] or "authors" in rec["missing_fields"]:
                meta_ok = False
                violations.append({"check": "metadata_and_doi_validity", "cite_key": ck, "missing": rec["missing_fields"]})
        doi = rec.get("doi")
        if doi:
            if not is_valid_doi_syntax(doi):
                meta_ok = False
                violations.append({"check": "metadata_and_doi_validity", "cite_key": ck, "doi": doi, "reason": "invalid_or_fabricated_doi_syntax"})
            elif verify_doi_online:
                doi_mode = "crossref_api_online"
                if not _check_doi_online(doi):
                    uncertainties.append({"cite_key": ck, "code": "doi_online_unresolved", "doi": doi})

    # 5. Claim-reference alignment check (flags sharp domain mismatches when tokens exist)
    align_ok = True
    for occ in occurrences:
        ctx = occ.get("context_sentence", "")
        ctx_toks = _content_tokens(ctx)
        if len(ctx_toks) < 4:
            continue
        for ck in occ.get("cite_keys", []):
            rec = by_key.get(ck)
            if not rec:
                continue
            ref_text = " ".join([rec.get("title") or "", " ".join(rec.get("keywords", [])), rec.get("abstract") or ""])
            ref_toks = _content_tokens(ref_text)
            if len(ref_toks) < 4:
                continue
            overlap = ctx_toks & ref_toks
            # Allow bilingual cross-script pairs (e.g. ZH sentence citing EN ref) while catching same-script zero-overlap mismatch
            same_script = not (any("\u4e00" <= c <= "\u9fff" for c in ctx) != any("\u4e00" <= c <= "\u9fff" for c in ref_text))
            if same_script and len(overlap) == 0:
                align_ok = False
                violations.append(
                    {
                        "check": "claim_reference_alignment",
                        "line": occ.get("line"),
                        "cite_key": ck,
                        "context_preview": ctx[:120],
                        "ref_title": rec.get("title"),
                        "reason": "zero_lexical_topic_overlap",
                    }
                )

    # 6. Placeholder & honesty check
    placeholder_ok = not bool(UNRESOLVED_TAG_RE.search(synced_manuscript))
    if not placeholder_ok:
        violations.append({"check": "placeholder_and_honesty", "reason": "unresolved_cite_or_todo_marker_in_output"})
    for pe in parse_errors:
        uncertainties.append({"code": "source_parse_issue", **pe})

    checks = {
        "numbering_continuity": numbering_ok,
        "bidirectional_sync": sync_ok,
        "dedup_integrity": dedup_ok,
        "metadata_and_doi_validity": meta_ok,
        "claim_reference_alignment": align_ok,
        "placeholder_and_honesty": placeholder_ok,
    }
    passed = all(checks.values())

    return {
        "schema_version": "writing-04-qa/v1",
        "passed": passed,
        "doi_verification_mode": doi_mode,
        "metrics": {
            "raw_record_count": dedup_report.get("raw_record_count", len(raw_library.get("records", []))),
            "canonical_record_count": len(records),
            "merged_duplicate_count": dedup_report.get("merged_duplicate_count", 0),
            "in_text_citation_count": len(occurrences),
            "bibliography_count": len(cited_order),
            "numbering_continuity": numbering_ok,
            "bidirectional_sync": sync_ok,
        },
        "checks": checks,
        "violations": violations,
        "uncertainties": uncertainties,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify synced manuscript citations and bibliography.")
    parser.add_argument("--manuscript", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--lib", required=True)
    parser.add_argument("--dedup", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--verify-doi-online", action="store_true")
    args = parser.parse_args()

    ms = Path(args.manuscript).read_text(encoding="utf-8")
    cmap = json.loads(Path(args.map).read_text(encoding="utf-8"))
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    dedup = json.loads(Path(args.dedup).read_text(encoding="utf-8"))
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))

    rep = verify_citations(ms, cmap, lib, dedup, raw, verify_doi_online=args.verify_doi_online)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
