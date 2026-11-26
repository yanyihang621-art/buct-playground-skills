#!/usr/bin/env python3
"""6-check deterministic citation & bibliography QA verifier."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Set
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from format_references import format_library
from io_utils import first_author_surname, is_valid_doi_syntax, normalize_title, title_similarity, write_json_atomic

STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "were", "was", "are", "have", "has",
    "been", "using", "used", "study", "results", "show", "shown", "high", "low", "over", "into",
    "paper", "method", "based", "analysis", "between", "their", "these", "also", "such",
}
UNRESOLVED_TAG_RE = re.compile(r"\[CITE:|\[@|\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)


def _content_tokens(text: str) -> Set[str]:
    norm = normalize_title(text)
    words = set(re.findall(r"[a-z0-9]{3,}", norm)) - STOP_WORDS
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", norm):
        words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return words


def _check_doi_online(rec: dict, timeout: float) -> dict:
    doi = rec["doi"]
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")
    req = urllib.request.Request(url, headers={"User-Agent": "buct-writing04-verifier/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            meta = json.load(resp)["message"]
    except urllib.error.HTTPError as exc:
        return {"status": "not_in_crossref" if exc.code == 404 else "service_error", "http": exc.code}
    except (OSError, ValueError, KeyError):
        return {"status": "service_unavailable"}
    title = (meta.get("title") or [""])[0]
    title_score = title_similarity(rec.get("title"), title)
    exact_doi = str(meta.get("DOI") or "").casefold() == doi.casefold()
    conflicts = []
    first = (meta.get("author") or [{}])[0]
    family = first.get("family") or first.get("name")
    surname = first_author_surname(rec.get("authors", []))
    if family and surname and _fold(family) not in (_fold(surname), _fold(rec["authors"][0])):
        conflicts.append({"field": "first_author", "supplied": rec["authors"][0], "registered": family})
    years = set()
    for key in ("published", "published-online", "published-print", "issued"):
        dates = (meta.get(key) or {}).get("date-parts")
        if dates and dates[0]:
            years.add(str(dates[0][0]))
    if rec.get("year") and years and str(rec["year"]) not in years:
        conflicts.append({"field": "year", "supplied": rec["year"], "registered": sorted(years)})
    for local, registry in (("volume", "volume"), ("issue", "issue"), ("pages", "page")):
        if rec.get(local) and meta.get(registry):
            left, right = [re.sub(r"[\s–—-]+", "", _fold(x)) for x in (rec[local], meta[registry])]
            if left != right:
                conflicts.append({"field": local, "supplied": rec[local], "registered": meta[registry]})
    return {"status": "metadata_consistent" if exact_doi and title_score >= 0.85 and not conflicts else "metadata_review",
            "registered_doi": meta.get("DOI"), "registered_title": title, "title_similarity": title_score,
            "source_url": url, "field_conflicts": conflicts}


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


@lru_cache(maxsize=32)
def _read_evidence(filename: Path, page: int, mtime_ns: int) -> tuple[str, str]:
    """Read the requested passage and the identifying front matter from an input file."""
    if filename.suffix.lower() == ".pdf":
        try:
            import fitz
            with fitz.open(filename) as doc:
                if page < 1 or page > len(doc):
                    return "", ""
                return doc[page - 1].get_text(), doc[0].get_text()[:6000]
        except (ImportError, RuntimeError, ValueError):
            return "", ""
    if filename.suffix.lower() == ".docx":
        with ZipFile(filename) as doc:
            root = ElementTree.fromstring(doc.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = ["".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns)]
        content = "\n".join(paragraphs)
    elif filename.suffix.lower() in (".md", ".txt", ".tex"):
        content = filename.read_text(encoding="utf-8-sig")
    else:
        return "", ""
    return content, content[:4000]


def _anchored_source(review: dict, record: dict, evidence_root: Path | None) -> bool:
    quote = _fold(review.get("source_quote"))
    field = review.get("source_field")
    if not quote:
        return False
    if field in ("title", "abstract"):
        return quote in _fold(record.get(field))
    if field != "evidence_file" or not evidence_root or not review.get("source_file"):
        return False
    try:
        root = Path(evidence_root).resolve()
        relative = Path(review["source_file"])
        file = (root / relative).resolve()
        if relative.is_absolute() or not file.is_relative_to(root) or not file.is_file():
            return False
        passage, header = _read_evidence(file, int(review.get("page", 0)), file.stat().st_mtime_ns)
    except (OSError, TypeError, ValueError, BadZipFile, KeyError, ElementTree.ParseError):
        return False
    header = _fold(header)
    identity = [_fold(record.get(k)) for k in ("title", "doi", "arxiv_id") if record.get(k)]
    return quote in _fold(passage) and any(identifier in header for identifier in identity)


def _inspect_claims(occurrences, by_key, semantic_reviews, evidence_root):
    evidence, pending, issues, limits = [], [], [], []
    reviews = []
    for review in semantic_reviews or []:
        if isinstance(review, dict) and str(review.get("line", "")).isdigit() and review.get("cite_key"):
            reviews.append(review)
        else:
            issues.append({"check": "claim_reference_alignment", "reason": "invalid_semantic_review"})
    used = set()
    for occurrence_index, occ in enumerate(occurrences, start=1):
        ctx = occ.get("context_sentence") or ""
        claim = ctx.replace(occ.get("original_tag") or "\0", "")
        for key in dict.fromkeys(occ.get("cite_keys", [])):
            if key not in by_key:
                continue
            rec = by_key[key]
            source_text = " ".join([rec.get("title") or "", " ".join(rec.get("keywords") or []), rec.get("abstract") or ""])
            overlap = sorted(_content_tokens(claim) & _content_tokens(source_text))
            row = {"line": occ.get("line"), "occurrence_index": occurrence_index,
                   "cite_key": key, "ref_id": rec.get("ref_id"), "context_sentence": ctx,
                   "source_title": rec.get("title"), "source_locator": rec.get("source_provenance", []),
                   "topic_terms_in_common": overlap[:12], "verdict": "unreviewed"}
            matching = [(i, r) for i, r in enumerate(reviews) if int(r["line"]) == occ.get("line")
                        and r["cite_key"] == key and _fold(r.get("claim_quote"))
                        and str(r.get("occurrence_index", occurrence_index)) == str(occurrence_index)
                        and _fold(r["claim_quote"]) in _fold(ctx)]
            if not matching:
                pending.append({"line": occ.get("line"), "occurrence_index": occurrence_index, "cite_key": key,
                                "reason": "needs_semantic_review" if overlap else "no_lexical_overlap_review"})
            elif len(matching) > 1:
                issues.append({"check": "claim_reference_alignment", "line": occ.get("line"), "cite_key": key,
                               "reason": "ambiguous_semantic_reviews"})
            else:
                i, r = matching[0]
                used.add(i)
                verdict = r.get("verdict")
                has_anchor = _anchored_source(r, rec, evidence_root)
                valid = bool(str(r.get("reason") or "").strip()) and (
                    verdict == "insufficient" and (not r.get("source_quote") or has_anchor)
                    or verdict in ("supported", "partial", "mismatch") and has_anchor)
                if not valid:
                    issues.append({"check": "claim_reference_alignment", "line": occ.get("line"), "cite_key": key,
                                   "reason": "unsupported_review_or_unlocated_quote"})
                else:
                    row.update({k: r[k] for k in ("claim_quote", "verdict", "source_field", "source_file", "page",
                                                   "source_quote", "reason") if k in r})
                    row["quote_located"] = has_anchor
                    if verdict == "mismatch":
                        issues.append({"check": "claim_reference_alignment", "reason": "reviewed_mismatch",
                                       "line": occ.get("line"), "cite_key": key, "detail": r["reason"]})
                    elif verdict != "supported":
                        limits.append({"code": "limited_claim_support", "line": occ.get("line"),
                                       "cite_key": key, "verdict": verdict, "reason": r["reason"]})
            evidence.append(row)
    for i, review in enumerate(reviews):
        if i not in used:
            issues.append({"check": "claim_reference_alignment", "reason": "unmatched_review",
                           "line": review["line"], "cite_key": review["cite_key"]})
    return evidence, pending, issues, limits


def verify_citations(
    synced_manuscript: str,
    citation_map: Dict[str, Any],
    canonical_library: Dict[str, Any],
    dedup_report: Dict[str, Any],
    raw_library: Dict[str, Any],
    *,
    verify_doi_online: bool = False,
    semantic_reviews: List[Dict[str, Any]] | None = None,
    evidence_root: Path | None = None,
    formatted_bibliography: str | None = None,
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
    provenance_checks = []
    review_queue = []

    # 1. Numbering continuity (1..N contiguous with no gaps)
    assigned = [item.get("number") for item in cited_order]
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
    numbers = {r["cite_key"]: r["number"] for r in cited_order}
    inconsistent = []
    lines = synced_manuscript.splitlines()
    tags_by_line = Counter()
    for occ in occurrences:
        keys = occ.get("cite_keys", [])
        labels = occ.get("assigned_numbers", [])
        line, tag = occ.get("edited_line", occ.get("line")), occ.get("rendered_tag")
        if labels != [numbers.get(key) for key in keys]:
            inconsistent.append({"line": line, "reason": "assigned_numbers_do_not_match_keys"})
        if isinstance(line, int) and tag:
            tags_by_line[line, tag] += 1
    for (line, tag), count in tags_by_line.items():
        if not 1 <= line <= len(lines) or lines[line - 1].count(tag) < count:
            inconsistent.append({"line": line, "tag": tag, "reason": "mapped_tag_absent_in_edited_manuscript"})
    cited_records = [by_key[r["cite_key"]] for r in cited_order if r["cite_key"] in by_key]
    if cited_records:
        fmt = "bibtex" if citation_map.get("is_latex") else citation_map.get("style", "gbt7714")
        expected_bib = format_library(cited_records, fmt).strip()
        expected_forms = [expected_bib]
        if fmt == "apa7":
            expected_forms.append(format_library(cited_records, "apa7-markdown").strip())
        if formatted_bibliography is not None and formatted_bibliography.strip() not in expected_forms:
            inconsistent.append({"reason": "formatted_bibliography_differs_from_mapped_records"})
        if not citation_map.get("is_latex") and not any(form in synced_manuscript for form in expected_forms):
            inconsistent.append({"reason": "manuscript_bibliography_differs_from_mapped_records"})
    sync_ok = not (unresolved or missing_from_bib or missing_from_lib or orphan_bib or inconsistent
                   or len(bib_keys) != len(cited_order))
    if not sync_ok:
        violations.append(
            {
                "check": "bidirectional_sync",
                "unresolved_keys": unresolved,
                "missing_from_bib": missing_from_bib,
                "missing_from_lib": missing_from_lib,
                "orphan_bib": orphan_bib,
                "map_inconsistencies": inconsistent,
            }
        )

    # 3. Dedup integrity (no remaining near-identical canonical records)
    dedup_ok = True
    seen_doi = {}
    for record in records:
        doi = (record.get("doi") or "").casefold()
        if doi in seen_doi:
            dedup_ok = False
            violations.append({"check": "dedup_integrity", "pair": [seen_doi[doi], record["cite_key"]],
                               "reason": "conflicting_canonical_records_share_identifier"})
        if doi:
            seen_doi[doi] = record["cite_key"]
    review_queue.extend({"check": "dedup_integrity", **pair} for pair in dedup_report.get("review_pairs", []))

    # 4. Metadata & DOI syntax / optional online validation
    meta_ok = True
    doi_mode = "crossref_api_online" if verify_doi_online else "offline_syntax_only"
    lookup_deadline = time.monotonic() + 30
    raw_by_id = {r["raw_id"]: r for r in raw_library.get("records", []) if r.get("raw_id")}
    for ck in sorted(bib_keys):
        rec = by_key.get(ck)
        if not rec:
            continue
        missing = list(rec.get("missing_fields", []))
        if "identifier" in missing and rec.get("source_provenance") and all(rec.get(k) for k in ("authors", "title", "year", "venue")):
            missing.remove("identifier")
            review_queue.append({"cite_key": ck, "reason": "identified_from_supplied_bibliographic_record_only"})
        if missing:
            uncertainties.append({"cite_key": ck, "code": "missing_metadata_fields", "fields": missing})
            if "title" in missing or "authors" in missing:
                meta_ok = False
                violations.append({"check": "metadata_and_doi_validity", "cite_key": ck, "missing": rec["missing_fields"]})
        doi = rec.get("doi")
        evidence_item = {"cite_key": ck, "local_source": rec.get("source_provenance", []), "doi": doi,
                         "status": "no_doi_supplied"}
        confirmed = []
        for field, locator in rec.get("field_sources", {}).items():
            original = raw_by_id.get(locator.get("raw_id"))
            if original and original.get(field) == rec.get(field):
                confirmed.append(field)
            else:
                meta_ok = False
                violations.append({"check": "metadata_and_doi_validity", "cite_key": ck,
                                   "field": field, "reason": "value_differs_from_cited_input_record", "locator": locator})
        evidence_item["fields_verified_against_input"] = confirmed
        provenance_checks.append(evidence_item)
        if doi:
            if not is_valid_doi_syntax(doi):
                meta_ok = False
                evidence_item["status"] = "invalid_or_placeholder"
                violations.append({"check": "metadata_and_doi_validity", "cite_key": ck, "doi": doi, "reason": "invalid_or_placeholder_doi"})
            elif verify_doi_online:
                remaining = lookup_deadline - time.monotonic()
                if remaining < 0.25:
                    evidence_item["status"] = "lookup_budget_exhausted"
                else:
                    evidence_item.update(_check_doi_online(rec, timeout=min(6, remaining)))
                if evidence_item["status"] != "metadata_consistent":
                    uncertainties.append({"cite_key": ck, "code": evidence_item["status"], "doi": doi})
            else:
                evidence_item["status"] = "syntax_only"

    # 5. A lexical screen routes claims for reading. A supported verdict also needs a located quotation.
    evidence, pending, issues, limits = _inspect_claims(occurrences, by_key, semantic_reviews, evidence_root)
    violations.extend(issues)
    uncertainties.extend(limits)
    review_queue.extend(pending)
    align_ok = not issues

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
            "claim_source_pairs": len(evidence),
            "reviewed_claim_source_pairs": sum(r["verdict"] != "unreviewed" for r in evidence),
            "uncited_library_entries": len(set(by_key) - in_text_keys),
        },
        "claim_evidence": evidence,
        "review_queue": review_queue,
        "source_verification": provenance_checks,
        "uncited_library_keys": sorted(set(by_key) - in_text_keys),
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
    parser.add_argument("--semantic-reviews", help="UTF-8 JSON list of claim-scoped reviews")
    parser.add_argument("--evidence-root", type=Path, help="Root containing supplied evidence files")
    parser.add_argument("--bibliography", type=Path, help="Edited standalone bibliography to verify")
    args = parser.parse_args()

    ms = Path(args.manuscript).read_text(encoding="utf-8")
    cmap = json.loads(Path(args.map).read_text(encoding="utf-8"))
    lib = json.loads(Path(args.lib).read_text(encoding="utf-8"))
    dedup = json.loads(Path(args.dedup).read_text(encoding="utf-8"))
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))

    reviews = json.loads(Path(args.semantic_reviews).read_text(encoding="utf-8")) if args.semantic_reviews else None
    bib = args.bibliography.read_text(encoding="utf-8") if args.bibliography else None
    rep = verify_citations(ms, cmap, lib, dedup, raw, verify_doi_online=args.verify_doi_online,
                           semantic_reviews=reviews, evidence_root=args.evidence_root, formatted_bibliography=bib)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
