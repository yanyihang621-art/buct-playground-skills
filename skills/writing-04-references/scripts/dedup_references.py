#!/usr/bin/env python3
"""Explainable bibliographic deduplication with anti-false-merge guards."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import CJK_RE, first_author_surname, is_valid_doi_syntax, normalize_title, title_similarity, write_json_atomic


def _is_preprint(rec):
    return (rec.get("entry_type") == "preprint" or "arxiv" in (rec.get("venue") or "").lower()
            or bool(rec.get("arxiv_id") and rec.get("entry_type") in {"misc", "electronic"}))


def _richness_score(rec: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Rank published journal/conference versions over preprints and richer metadata."""
    is_published = int(bool(rec.get("venue")) and not _is_preprint(rec) and rec.get("entry_type") not in ("electronic", "misc"))
    has_doi = 1 if rec.get("doi") else 0
    field_score = sum(
        1 for k in ("venue", "volume", "issue", "pages", "year", "abstract") if rec.get(k)
    ) + len(rec.get("authors", []))
    fmt_bonus = 1 if rec["source_provenance"][0].get("format") == "bibtex" else 0
    return (is_published, has_doi, field_score, fmt_bonus)


def _evaluate_pair(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[bool, str, float, str]:
    """Return (should_merge, rule, title_sim, explanation)."""
    doi_a, doi_b = a.get("doi"), b.get("doi")
    arx_a, arx_b = a.get("arxiv_id"), b.get("arxiv_id")
    sim = title_similarity(a.get("title", ""), b.get("title", ""))
    fa_a = first_author_surname(a.get("authors", []))
    fa_b = first_author_surname(b.get("authors", []))
    same_first_author = bool(fa_a and fa_b and fa_a == fa_b)
    ya, yb = a.get("year"), b.get("year")

    # Preserve separately identified manifestations even when their titles coincide.
    if doi_a and doi_b and doi_a.lower() != doi_b.lower():
        return False, "distinct_doi_guard", sim, "Distinct DOIs prevent merging."

    arx_a = re.sub(r"v\d+$", "", arx_a.lower()) if arx_a else None
    arx_b = re.sub(r"v\d+$", "", arx_b.lower()) if arx_b else None
    if arx_a and arx_b and arx_a != arx_b:
        return False, "distinct_arxiv_guard", sim, "Different arXiv identifiers; retained separate records."

    if doi_a and doi_b and doi_a.lower() == doi_b.lower():
        if not is_valid_doi_syntax(doi_a):
            return False, "identifier_conflict", sim, "Malformed shared identifier requires review."
        titles = [r.get("title") or "" for r in (a, b)]
        same_script = bool(CJK_RE.search(titles[0])) == bool(CJK_RE.search(titles[1]))
        if all(titles) and same_script and (sim < 0.26 or (sim < 0.48 and not same_first_author)):
            return False, "identifier_conflict", sim, "Shared DOI conflicts with titles; check the underlying records."
        return True, "doi_exact", sim, f"Exact normalized DOI match ({doi_a})."

    # Exact arXiv ID match
    if arx_a and arx_b and arx_a.lower() == arx_b.lower():
        return True, "arxiv_exact", sim, f"Exact arXiv ID match ({arx_a})."

    # Guard 2: reject same-author same-year distinct papers when titles diverge
    if sim < 0.72:
        if same_first_author and ya == yb and ya is not None:
            return False, "anti_false_merge_same_author_year", sim, "Same first author & year but divergent titles; kept separate."
        return False, "title_divergent", sim, "Title similarity below merge threshold."

    # Preprint -> published journal/conference match
    pre_a = _is_preprint(a)
    pre_b = _is_preprint(b)
    if (pre_a != pre_b) and sim >= 0.85 and same_first_author:
        return (
            True,
            "preprint_to_published",
            sim,
            f"Preprint and published record share first author '{fa_a or fa_b}' and title similarity {sim}.",
        )

    # Normalized title + author + year fuzzy match across DB exports
    year_ok = (ya is None or yb is None or abs(ya - yb) <= 1)
    author_ok = same_first_author
    if sim >= 0.90 and year_ok and author_ok and (sim == 1 or (ya and yb)):
        return (
            True,
            "title_author_year_fuzzy",
            sim,
            f"Normalized title similarity {sim} with compatible year ({ya}/{yb}) and first author ({fa_a or fa_b}).",
        )

    if sim >= 0.87:
        return False, "possible_duplicate", sim, "Similar titles with incomplete or contradictory author/year information."
    return False, "no_match", sim, "Criteria not met."


def _candidate_keys(rec):
    """All merge rules require a shared identifier or first author; titles also find review pairs."""
    keys = []
    if rec.get("doi"):
        keys.append(("doi", rec["doi"].casefold()))
    if rec.get("arxiv_id"):
        keys.append(("arxiv", re.sub(r"v\d+$", "", rec["arxiv_id"].casefold())))
    surname = first_author_surname(rec.get("authors", []))
    if surname:
        keys.append(("author", surname))
    title = normalize_title(rec.get("title"))
    if title:
        keys.append(("title", title))
        words = title.split()
        if len(words) > 3:
            keys.extend([("opening", tuple(words[:3])), ("ending", tuple(words[-3:]))])
    return keys


def deduplicate_library(raw_library: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Cluster duplicate records, merge metadata safely, and emit an explainable dedup report."""
    records = raw_library.get("records", [])
    clusters: List[List[Dict[str, Any]]] = []
    cluster_reasons: List[List[Dict[str, Any]]] = []
    rejected_same_author: List[Dict[str, Any]] = []
    review_pairs: List[Dict[str, Any]] = []
    candidate_index = defaultdict(set)

    for rec in records:
        matches = []
        keys = _candidate_keys(rec)
        candidates = set().union(*(candidate_index[k] for k in keys))
        for ci in sorted(candidates):
            cluster = clusters[ci]
            rep = max(cluster, key=_richness_score)
            should_merge, rule, sim, expl = _evaluate_pair(rep, rec)
            # A sparse record must never bridge incompatible identifier-bearing members.
            if should_merge and len(cluster) > 1:
                for member in cluster:
                    _, other_rule, _, other_explanation = _evaluate_pair(member, rec)
                    if other_rule in {"distinct_doi_guard", "identifier_conflict", "distinct_arxiv_guard"}:
                        should_merge, rule, expl = False, other_rule, other_explanation
                        break
            if should_merge:
                matched_info = {
                    "merged_raw_id": rec["raw_id"],
                    "merged_raw_key": rec["raw_key"],
                    "into_raw_id": rep["raw_id"],
                    "rule": rule,
                    "title_similarity": sim,
                    "explanation": expl,
                }
                matches.append((ci, matched_info))
                continue
            if rule in ("identifier_conflict", "possible_duplicate") or (rule in ("distinct_doi_guard", "distinct_arxiv_guard") and sim >= 0.85):
                review_pairs.append({"pair": [rep["raw_id"], rec["raw_id"]], "keys": [rep["raw_key"], rec["raw_key"]],
                                     "rule": rule, "title_similarity": sim, "reason": expl})
            if rule == "anti_false_merge_same_author_year" or (
                first_author_surname(rep.get("authors", []))
                and first_author_surname(rep.get("authors", [])) == first_author_surname(rec.get("authors", []))
                and rep.get("year") is not None
                and rep.get("year") == rec.get("year")
                and sim < 0.72
            ):
                rejected_same_author.append(
                    {
                        "pair": [rep["raw_key"], rec["raw_key"]],
                        "rule": rule,
                        "title_similarity": sim,
                        "explanation": expl,
                    }
                )
        strong_matches = [m for m in matches if m[1]["rule"] in ("doi_exact", "arxiv_exact")]
        matches = strong_matches or matches
        if len(matches) == 1:
            matched_idx, matched_info = matches[0]
            clusters[matched_idx].append(rec)
            cluster_reasons[matched_idx].append(matched_info)
        else:
            if len(matches) > 1:
                review_pairs.append({"record": rec["raw_id"], "rule": "multiple_candidate_clusters",
                                     "candidate_raw_ids": [m["into_raw_id"] for _, m in matches]})
            matched_idx = len(clusters)
            clusters.append([rec])
            cluster_reasons.append([])
        for key in keys:
            candidate_index[key].add(matched_idx)

    canonical_records: List[Dict[str, Any]] = []
    merged_groups: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()

    for idx, cluster in enumerate(clusters, start=1):
        primary = max(cluster, key=_richness_score)
        base_key = primary["raw_key"]
        cite_key = base_key
        suffix = 2
        while cite_key in seen_keys:
            cite_key = f"{base_key}_{suffix}"
            suffix += 1
        seen_keys.add(cite_key)

        aliases = list(dict.fromkeys([cite_key, *(r["raw_key"] for r in cluster)]))
        provenance = [point for r in cluster for point in r.get("source_provenance", [])]
        ordered = [primary, *(r for r in cluster if r is not primary)]
        # Version-specific fields must belong to the selected manifestation. Keep the
        # preprint's DOI and results in its source snapshot when citing a journal paper.
        same_version = [r for r in ordered if _is_preprint(r) == _is_preprint(primary)]
        metadata = {}
        field_sources = {}
        for field in ("title", "authors", "year", "venue", "volume", "issue", "pages", "doi", "arxiv_id", "url", "abstract"):
            pool = ordered if field == "title" else same_version
            source = next((r for r in pool if r.get(field)), None)
            default = [] if field == "authors" else "" if field == "title" else None
            metadata[field] = source[field] if source else default
            if source:
                field_sources[field] = {"raw_id": source["raw_id"], **source["source_provenance"][0]}
        keywords = list(dict.fromkeys(k for member in same_version for k in member.get("keywords", [])))
        missing = [field for field in ("title", "authors", "year") if not metadata[field]]
        if not metadata["venue"] and not metadata["arxiv_id"]:
            missing.append("venue")
        if not any(metadata[k] for k in ("doi", "arxiv_id", "url")):
            missing.append("identifier")

        ref_id = f"ref_{idx:04d}"
        canonical_records.append(
            {
                "ref_id": ref_id,
                "cite_key": cite_key,
                "aliases": aliases,
                "entry_type": primary.get("entry_type", "article"),
                **metadata,
                "keywords": keywords,
                "language": primary.get("language", "en"),
                "missing_fields": missing,
                "source_provenance": provenance,
                "field_sources": field_sources,
            }
        )

        if len(cluster) > 1:
            merged_groups.append(
                {
                    "canonical_ref_id": ref_id,
                    "canonical_key": cite_key,
                    "aliases": aliases,
                    "primary_selected_from": primary["source_provenance"][0],
                    "merges": cluster_reasons[idx - 1],
                    "source_versions": [{k: r.get(k) for k in ("raw_id", "raw_key", "entry_type", "title", "year", "doi", "arxiv_id")}
                                        for r in cluster],
                }
            )

    canonical_lib = {
        "schema_version": "writing-04-canonical-library/v1",
        "record_count": len(canonical_records),
        "records": canonical_records,
    }
    dedup_report = {
        "schema_version": "writing-04-dedup-report/v1",
        "raw_record_count": len(records),
        "canonical_record_count": len(canonical_records),
        "merged_duplicate_count": len(records) - len(canonical_records),
        "merged_groups": merged_groups,
        "anti_false_merge_guards": rejected_same_author,
        "review_pairs": review_pairs,
    }
    return canonical_lib, dedup_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate raw_library.json with explainable rules.")
    parser.add_argument("--raw", required=True, help="Input raw_library.json")
    parser.add_argument("--out-lib", required=True, help="Output canonical_library.json")
    parser.add_argument("--out-report", required=True, help="Output dedup_report.json")
    args = parser.parse_args()

    raw_lib = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    canon, report = deduplicate_library(raw_lib)
    write_json_atomic(Path(args.out_lib), canon)
    write_json_atomic(Path(args.out_report), report)
    print(
        json.dumps(
            {
                "raw_record_count": report["raw_record_count"],
                "canonical_record_count": report["canonical_record_count"],
                "merged_duplicate_count": report["merged_duplicate_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
