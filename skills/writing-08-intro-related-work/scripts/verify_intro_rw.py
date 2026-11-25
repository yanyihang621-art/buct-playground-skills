#!/usr/bin/env python3
"""Audit the manuscript body, source-linked claims, and literature/study boundaries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from citation_audit import Citations, COMPARISON, REFERENCES, normalized
from rw_utils import load_writing_rules, write_json_atomic


SECTION_TITLES = {
    "background": r"background|introduction|motivation|研究背景|背景|引言|绪论",
    "related_work": r"related (?:work|studies)|prior (?:work|research)|literature review|state of the art|相关工作|文献综述|研究现状",
    "gap": r"research gap|gaps?\b|open (?:question|problem)|unresolved|研究空白|研究缺口|研究不足|待解决",
    "contribution": r"contribution|our (?:study|work|approach)|present (?:study|work)|本文工作|研究贡献|主要贡献|本文贡献|本研究",
}
SECTION_ALIASES = {"comparison": "related_work", "research_gap": "gap", "contributions": "contribution"}
HEADING = re.compile(r"(?m)^(?:#{1,6}\s+(.+)|\\(?:sub)*section\*?\{([^}]+)\})[ \t]*$")
ABSOLUTE = re.compile(
    r"\b(?:the first(?: ever)?|unprecedented|universally|all existing (?:studies|methods)|"
    r"outperforms? all|state[- ]of[- ]the[- ]art|no (?:prior|previous) (?:work|study|studies))\b|"
    r"首次|迄今首个|国际领先|所有现有|全面超越|此前尚无|填补了.{0,8}空白", re.I
)
PERCENT = re.compile(r"(?<![\d.])([−+-]?\d+(?:\.\d+)?)\s*(?:%|％)")
MEASUREMENT = re.compile(
    r"(?<![\w.])([−+-]?\d+(?:\.\d+)?)\s*(MPa|kPa|Pa|mV|V|kW|mW|W|ms|s|h|min|"
    r"nM|µM|mM|nm|µm|mm|cm|km|kg|mg|kJ|GHz|MHz|kHz|Hz|K|°C|dB|mL|mAh|MB|GB)(?![a-zA-Z])"
)
LOCATOR = re.compile(
    r"(?<![A-Za-z])(?:fig(?:ure)?\.?|table|eq(?:uation)?\.?|sec(?:tion)?\.?|图|表|式|节)"
    r"\s*\(?\s*(?:[Ss]?\d+(?:\.\d+)*(?:[A-Za-z])?)(?![A-Za-z0-9])", re.I
)
UNRESOLVED = re.compile(r"\[(?:material gap|todo\b|citation needed|待补|待核)|\bUNVERIFIED\b", re.I)


def text_rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _plain(text):
    # Ignore typography around a span, preserving punctuation, units, and negation.
    return normalized(re.sub(r"[*`]", "", str(text or "")))


def _heading_level(heading):
    line = heading.group(0)
    if line.startswith("#"):
        return len(line) - len(line.lstrip("#"))
    return 2 + line.split("section", 1)[0].count("sub")


def classify_sections(body, spans, claim_rows, rules, language):
    sections = {role: [] for role in SECTION_TITLES}
    rules_key = "required_sections_zh" if language.startswith("zh") else "required_sections_en"
    configured = dict(zip(SECTION_TITLES, rules.get(rules_key, [])))
    headings = list(HEADING.finditer(body))
    for i, head in enumerate(headings):
        title = _plain(head.group(1) or head.group(2))
        # Take the entire section including any named methodological subgroups.
        level = _heading_level(head)
        end = next((h.start() for h in headings[i + 1:] if _heading_level(h) <= level), len(body))
        part = body[head.end():end]
        for role, pattern in SECTION_TITLES.items():
            if re.search(pattern, title) or (configured.get(role) and normalized(configured[role]) == title):
                if HEADING.sub("", part).strip():
                    sections[role].append(part)
    plain = _plain(body)
    for role, span in spans.items():
        role = SECTION_ALIASES.get(role, role)
        if role in sections and isinstance(span, str) and _plain(span) and _plain(span) in plain:
            sections[role].append(span)
    for row in claim_rows:
        kind = row.get("kind") or row.get("role")
        role = SECTION_ALIASES.get(kind, kind)
        claim = row.get("claim")
        if role in sections and isinstance(claim, str) and _plain(claim) and _plain(claim) in plain:
            sections[role].append(claim)
    return sections


def _locator_id(text):
    text = normalized(text)
    text = re.sub(r"fig(?:ure)?\.?|图", "fig", text)
    text = re.sub(r"table|表", "table", text)
    text = re.sub(r"sec(?:tion)?\.?|节", "sec", text)
    text = re.sub(r"eq(?:uation)?\.?|式", "eq", text)
    return re.sub(r"[\s()]", "", text)


def _source_text(row, passages, *, author_only=False):
    parts = []
    for evidence in text_rows(row.get("evidence")):
        source = passages.get(evidence.get("source_id"), {})
        if not source or source.get("role") == "instruction":
            continue
        if author_only and source.get("role") == "literature":
            continue
        quote = evidence.get("quote")
        if quote and normalized(quote) in normalized(source.get("text")):
            parts.append(quote)
    return "\n".join(parts)


def _missing_percentages(claim, source):
    # Percent-to-fraction conversion is common in underlying tables.
    source = normalized(source).replace("−", "-")
    present = {float(n) for n in PERCENT.findall(source)}
    fractions = {float(x) for x in re.findall(r"(?<![\w.])[-+]?0\.\d+(?!\d)", source)}
    missing = []
    for value in PERCENT.findall(claim):
        n = float(value.replace("−", "-"))
        if n not in present and not any(abs(f * 100 - n) < 1e-8 for f in fractions):
            missing.append(value + "%")
    return sorted(set(missing))


def _paragraph_keys(body, claim, citations):
    """Locate the containing paragraph, including a citation following a ledger excerpt."""
    needle = _plain(claim)
    if not needle:
        return set()
    keys = set()
    for para in re.split(r"\n\s*\n", body):
        if needle in _plain(para):
            keys.update(citations.resolve(para)[0])
    return keys


def verify_intro_and_related_work(draft_md, materials, clusters, gc_map, *, language="en", rules_path=None):
    rules = load_writing_rules(rules_path)
    papers = text_rows(materials.get("papers"))
    parts = REFERENCES.split(draft_md, maxsplit=1)
    body, bibliography = parts[0], parts[1] if len(parts) > 1 else ""
    citations = Citations(papers, bibliography)
    passages = {s["source_id"]: s for s in text_rows(materials.get("passages")) if s.get("source_id")}
    facts = materials.get("study_facts") or {}
    author_source = json.dumps(facts, ensure_ascii=False) if facts else ""
    material_gaps = materials.get("material_gaps") or []
    claim_rows = text_rows(materials.get("claim_evidence") or gc_map.get("claim_evidence"))
    gaps = text_rows(gc_map.get("research_gaps"))
    contributions = text_rows(gc_map.get("contributions"))
    plain_body = _plain(body)
    violations, warnings = [], []
    struct = "four_part_structure_completeness"
    synthesis = "anti_cliche_and_anti_stitching"
    gap_check = "gap_evidence_grounding"
    contribution_check = "contribution_evidence_alignment"
    coverage = "citation_coverage_and_metadata"
    honesty = "honesty_and_material_sufficiency"

    def flag(check, reason, **details):
        violations.append({"check": check, "reason": reason, **details})

    spans = materials.get("section_spans") or gc_map.get("section_spans") or {}
    sections = classify_sections(body, spans if isinstance(spans, dict) else {}, claim_rows, rules, language)
    missing = [role for role, fragments in sections.items() if not fragments]
    if missing:
        flag(struct, "role_missing_or_no_body_text", missing_sections=missing)

    cliches = [p for p in rules.get("banned_cliche_openings", []) if re.search(p, body, re.I)]
    if cliches:
        flag(synthesis, "generic_opening", patterns=cliches)
    related = "\n\n".join(sections["related_work"])
    comparative = []
    for para in dict.fromkeys(re.split(r"\n\s*\n", related)):
        keys = citations.resolve(para)[0].intersection(citations.papers)
        if len(keys) >= 2 and COMPARISON.search(para):
            comparative.append(sorted(keys))
    thematic_heading = bool(re.search(r"(?m)^#{3,6}\s+\S|^\\sub(?:sub)?section\{", related))
    if len(papers) >= 2 and not comparative:
        listed = len({k for k in citations.resolve(related)[0] if k in citations.papers}) >= 2
        if listed and not thematic_heading:
            warnings.append({"reason": "review_cross_paper_synthesis", "section": "related_work"})
            # Detect the specific mechanical stitch: a source per paragraph with no shared-axis comparison.
            paragraphs = [p for p in re.split(r"\n\s*\n|\n(?=Under |在)", related) if p.strip()]
            per_paragraph = [citations.resolve(p)[0].intersection(citations.papers) for p in paragraphs]
            if not any(len(v) >= 2 for v in per_paragraph) and not any(
                re.search(r"\b(?:by contrast|unlike|whereas|similarly|both)\b|相较|相比|前者|两者", p, re.I)
                for p in paragraphs
            ):
                flag(synthesis, "isolated_summaries_without_cross_work_comparison",
                     cite_keys=sorted(set().union(*per_paragraph)))
        elif listed:
            warnings.append({"reason": "route_subheadings_present_review_comparison_axis"})

    body_keys, missing_nums, ambiguous = citations.resolve(body)
    unknown = sorted(body_keys.difference(citations.papers))
    covered = sorted(body_keys.intersection(citations.papers))
    if unknown:
        flag(coverage, "unknown_inline_citation", cite_keys=unknown)
    if missing_nums:
        report = {"reason": "numeric_reference_unresolved", "numbers": sorted(missing_nums)}
        if citations.numbers:
            flag(coverage, **report)
        else:
            warnings.append(report)
    if ambiguous:
        warnings.append({"reason": "ambiguous_citation", "labels": sorted(ambiguous)})
    if papers and not covered:
        flag(coverage, "no_source_resolved_in_manuscript_body")
    missing_meta = {key: [f for f in ("title", "authors", "year", "venue") if not citations.papers[key].get(f)]
                    for key in covered}
    for key, absent in missing_meta.items():
        if absent:
            warnings.append({"reason": "bibliographic_fields_unavailable", "cite_key": key, "missing": absent})

    grounded_gaps = 0
    gap_ids = {g.get("gap_id") for g in gaps if g.get("gap_id")}
    if not gaps or not sections["gap"]:
        flag(gap_check, "no_documented_gap_or_bounded_judgment")
    for g in gaps:
        keys = set(g.get("derived_from_cite_keys") or [])
        if keys and keys.issubset(covered):
            grounded_gaps += 1
            local_keys = set()
            for fragment in sections["gap"]:
                local_keys.update(citations.resolve(fragment)[0])
                local_keys.update(_paragraph_keys(body, fragment, citations))
            if keys.difference(local_keys):
                warnings.append({"reason": "check_gap_premises_near_gap_sentence", "gap_id": g.get("gap_id"),
                                 "cite_keys": sorted(keys.difference(local_keys))})
        elif g.get("author_judgment") is not True or keys:
            flag(gap_check, "gap_citations_missing_from_body_or_corpus", gap_id=g.get("gap_id"),
                 unresolved=sorted(keys.difference(covered)))
        else:
            warnings.append({"reason": "scope_of_author_judgment_requires_review", "gap_id": g.get("gap_id")})

    grounded_contribs = 0
    if not contributions or not sections["contribution"]:
        flag(contribution_check, "no_confirmed_study_contribution")
    for c in contributions:
        cid = c.get("contrib_id")
        source = _source_text(c, passages, author_only=True)
        if c.get("evidence") and not source:
            flag(contribution_check, "no_study_or_neutral_source_supports_contribution", contrib_id=cid)
            continue
        if c.get("addresses_gap_id") and c["addresses_gap_id"] not in gap_ids:
            flag(contribution_check, "unknown_target_gap", contrib_id=cid, gap_id=c["addresses_gap_id"])
        anchors = c.get("evidence_anchor") or {}
        if not source and not (isinstance(anchors, dict) and any(anchors.values()) and author_source):
            flag(contribution_check, "missing_study_evidence", contrib_id=cid)
            continue
        source = source or author_source
        failed = []
        for field in ("figure_or_table_ref", "method_ref", "metric_value"):
            val = str(anchors.get(field) or "").strip() if isinstance(anchors, dict) else ""
            if val and normalized(val) not in normalized(source):
                missing_pct = _missing_percentages(val, source)
                if not PERCENT.fullmatch(val) or missing_pct:
                    source_ids = {_locator_id(v) for v in LOCATOR.findall(source)}
                    loc = LOCATOR.fullmatch(val)
                    if not loc or _locator_id(val) not in source_ids:
                        if c.get("evidence") and field == "method_ref" and not loc:
                            warnings.append({"reason": "review_descriptive_method_locator", "contrib_id": cid,
                                             "locator": val})
                        else:
                            failed.append(f"{field}={val}")
        if failed:
            flag(contribution_check, "unbacked_anchors", contrib_id=cid, unbacked_anchors=failed)
        else:
            grounded_contribs += 1

    for i, row in enumerate(claim_rows, 1):
        claim = str(row.get("claim") or "")
        if not _plain(claim) or _plain(claim) not in plain_body:
            flag(honesty, "claim_evidence_span_absent_from_draft", row=i, claim=claim[:160])
            continue
        role = row.get("kind") or row.get("role")
        source = _source_text(row, passages, author_only=role == "contribution")
        if not source:
            flag(honesty, "claim_missing_verifiable_quote", row=i)
            continue
        expected = set(row.get("cite_keys") or [])
        missing_keys = expected.difference(_paragraph_keys(body, claim, citations))
        if missing_keys:
            flag(coverage, "claim_citations_missing_from_its_paragraph", row=i, cite_keys=sorted(missing_keys))
        numbers = _missing_percentages(claim, source)
        if numbers:
            flag(honesty, "claim_percentage_needs_matching_source_or_calculation",
                 row=i, values=numbers, claim=claim[:160])
        supplied_values = {(float(n.replace("−", "-")), unit) for n, unit in MEASUREMENT.findall(source)}
        for n, unit in MEASUREMENT.findall(claim):
            value = float(n.replace("−", "-"))
            if (value, unit) not in supplied_values:
                warnings.append({"reason": "confirm_measurement_or_unit_conversion", "row": i, "value": n + " " + unit})
        source_labels = {_locator_id(label) for label in LOCATOR.findall(source)}
        for label in LOCATOR.findall(claim):
            if _locator_id(label) not in source_labels:
                warnings.append({"reason": "verify_figure_or_table_at_source_location", "row": i, "label": label})
        if ABSOLUTE.search(claim) and not ABSOLUTE.search(source):
            warnings.append({"reason": "confirm_strength_and_scope_against_source", "row": i, "claim": claim[:160]})
    # Audit the actual prose too: a new value appended after ledger creation can otherwise go unnoticed.
    study_claims = "\n".join(sections["contribution"])
    study_texts = [s["text"] for s in passages.values() if s.get("role") == "study"]
    study_texts.extend(_source_text(c, passages, author_only=True) for c in contributions)
    study_texts.append(author_source)
    if study_claims and any(study_texts):
        unbacked = _missing_percentages(study_claims, "\n".join(study_texts))
        if unbacked:
            flag(contribution_check, "draft_percentage_absent_from_study_sources", values=unbacked)
    for match in ABSOLUTE.finditer(body):
        warnings.append({"reason": "review_absolute_or_novelty_claim", "line": body.count("\n", 0, match.start()) + 1,
                         "phrase": match.group()})
    if UNRESOLVED.search(body):
        flag(honesty, "draft_contains_explicit_unresolved_placeholder")
    if not papers or not (facts or any(c.get("evidence") for c in contributions)):
        flag(honesty, "insufficient_literature_or_study_evidence", material_gaps=material_gaps)
    elif material_gaps:
        warnings.append({"reason": "review_declared_material_limits", "material_gaps": material_gaps})

    checks = {name: not any(v["check"] == name for v in violations)
              for name in (struct, synthesis, gap_check, contribution_check, coverage, honesty)}
    return {
        "schema_version": "writing-08-qa/v1", "passed": all(checks.values()),
        "checks": checks, "violations": violations, "warnings": warnings,
        "metrics": {
            "literature_count": len(papers), "route_cluster_count": clusters.get("route_cluster_count", 0),
            "gap_count": len(gaps), "contribution_count": len(contributions),
            "citation_coverage_ratio": round(len(covered) / len(papers), 4) if papers else 0.0,
            "grounded_gap_ratio": round(grounded_gaps / len(gaps), 4) if gaps else 0.0,
            "grounded_contribution_ratio": round(grounded_contribs / len(contributions), 4) if contributions else 0.0,
            "cited_in_manuscript": covered, "uncited_supplied_keys": sorted(set(citations.papers).difference(covered)),
            "cross_work_comparisons": comparative, "claim_evidence_rows": len(claim_rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify drafted Introduction & Related Work against 6 QA gates.")
    parser.add_argument("--draft", required=True)
    parser.add_argument("--materials", required=True)
    parser.add_argument("--clusters", required=True)
    parser.add_argument("--gc-map", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    md = Path(args.draft).read_text(encoding="utf-8")
    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))
    clus = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    gc = json.loads(Path(args.gc_map).read_text(encoding="utf-8"))
    rep = verify_intro_and_related_work(md, mats, clus, gc, language=args.language)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
