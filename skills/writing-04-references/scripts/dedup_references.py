#!/usr/bin/env python3
"""Explainable bibliographic deduplication with anti-false-merge guards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from io_utils import first_author_surname, title_similarity, write_json_atomic


def _richness_score(rec: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Rank published journal/conference versions over preprints and richer metadata."""
    is_published = 1 if rec.get("entry_type") not in ("preprint", "electronic", "misc") and rec.get("doi") else 0
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

    # Guard 1: distinct non-empty DOIs never merge
    if doi_a and doi_b and doi_a.lower() != doi_b.lower():
        return False, "distinct_doi_guard", sim, "Distinct DOIs prevent merging."

    # Exact DOI match
    if doi_a and doi_b and doi_a.lower() == doi_b.lower():
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
    pre_a = a.get("entry_type") == "preprint" or (bool(arx_a) and not doi_a)
    pre_b = b.get("entry_type") == "preprint" or (bool(arx_b) and not doi_b)
    if (pre_a != pre_b) and sim >= 0.85 and (same_first_author or not fa_a or not fa_b):
        return (
            True,
            "preprint_to_published",
            sim,
            f"Preprint and published record share first author '{fa_a or fa_b}' and title similarity {sim}.",
        )

    # Normalized title + author + year fuzzy match across DB exports
    year_ok = (ya is None or yb is None or abs(ya - yb) <= 1)
    author_ok = (same_first_author or not fa_a or not fa_b)
    if sim >= 0.90 and year_ok and author_ok:
        return (
            True,
            "title_author_year_fuzzy",
            sim,
            f"Normalized title similarity {sim} with compatible year ({ya}/{yb}) and first author ({fa_a or fa_b}).",
        )

    return False, "no_match", sim, "Criteria not met."


def deduplicate_library(raw_library: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Cluster duplicate records, merge metadata safely, and emit an explainable dedup report."""
    records = raw_library.get("records", [])
    clusters: List[List[Dict[str, Any]]] = []
    cluster_reasons: List[List[Dict[str, Any]]] = []
    rejected_same_author: List[Dict[str, Any]] = []

    for rec in records:
        matched_idx: Optional[int] = None
        matched_info: Optional[Dict[str, Any]] = None
        for ci, cluster in enumerate(clusters):
            rep = max(cluster, key=_richness_score)
            should_merge, rule, sim, expl = _evaluate_pair(rep, rec)
            if should_merge:
                matched_idx = ci
                matched_info = {
                    "merged_raw_id": rec["raw_id"],
                    "merged_raw_key": rec["raw_key"],
                    "into_raw_id": rep["raw_id"],
                    "rule": rule,
                    "title_similarity": sim,
                    "explanation": expl,
                }
                break
            elif rule == "anti_false_merge_same_author_year" or (
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
        if matched_idx is not None and matched_info is not None:
            clusters[matched_idx].append(rec)
            cluster_reasons[matched_idx].append(matched_info)
        else:
            clusters.append([rec])
            cluster_reasons.append([])

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

        aliases: List[str] = []
        provenance: List[Dict[str, Any]] = []
        authors = list(primary.get("authors", []))
        title = primary.get("title", "")
        year = primary.get("year")
        venue = primary.get("venue")
        volume = primary.get("volume")
        issue = primary.get("issue")
        pages = primary.get("pages")
        doi = primary.get("doi")
        arxiv_id = primary.get("arxiv_id")
        url = primary.get("url")
        abstract = primary.get("abstract")
        kw_set: List[str] = list(primary.get("keywords", []))

        for member in cluster:
            rk = member["raw_key"]
            if rk not in aliases:
                aliases.append(rk)
            provenance.extend(member.get("source_provenance", []))
            if not authors and member.get("authors"):
                authors = list(member["authors"])
            if year is None and member.get("year") is not None:
                year = member["year"]
            if not venue and member.get("venue"):
                venue = member["venue"]
            if not volume and member.get("volume"):
                volume = member["volume"]
            if not issue and member.get("issue"):
                issue = member["issue"]
            if not pages and member.get("pages"):
                pages = member["pages"]
            if not doi and member.get("doi"):
                doi = member["doi"]
            if not arxiv_id and member.get("arxiv_id"):
                arxiv_id = member["arxiv_id"]
            if not url and member.get("url"):
                url = member["url"]
            if not abstract and member.get("abstract"):
                abstract = member["abstract"]
            for k in member.get("keywords", []):
                if k not in kw_set:
                    kw_set.append(k)

        if cite_key not in aliases:
            aliases.insert(0, cite_key)

        missing: List[str] = []
        if not title:
            missing.append("title")
        if not authors:
            missing.append("authors")
        if year is None:
            missing.append("year")
        if not venue and not arxiv_id:
            missing.append("venue")
        if not doi and not arxiv_id and not url:
            missing.append("identifier")

        ref_id = f"ref_{idx:04d}"
        canonical_records.append(
            {
                "ref_id": ref_id,
                "cite_key": cite_key,
                "aliases": aliases,
                "entry_type": primary.get("entry_type", "article"),
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "volume": volume,
                "issue": issue,
                "pages": pages,
                "doi": doi,
                "arxiv_id": arxiv_id,
                "url": url,
                "keywords": kw_set,
                "abstract": abstract,
                "language": primary.get("language", "en"),
                "missing_fields": missing,
                "source_provenance": provenance,
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
