#!/usr/bin/env python3
"""Task-aware 7-gate QA audit separating hard blockers (must_fix) from advisory warnings."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from citation_audit import COMPARISON, Citations, REFERENCES, normalized
from evidence import IndexStore, check_negation_and_hedge, check_numbers, check_units, verify as ev_verify
from interpretation_audit import audit_interpretation

SECTION_TITLES = {
    "background": r"background|introduction|motivation|研究背景|背景|引言|绪论",
    "related_work": r"related (?:work|studies)|prior (?:work|research)|literature review|state of the art|相关工作|文献综述|研究现状",
    "gap": r"research gap|gaps?\b|open (?:question|problem)|unresolved|研究空白|研究缺口|研究不足|待解决",
    "contribution": r"contribution|our (?:study|work|approach)|present (?:study|work)|本文工作|研究贡献|主要贡献|本文贡献|本研究",
}
HEADING = re.compile(r"(?m)^(?:#{1,6}\s+(.+)|\\(?:sub)*section\*?\{([^}]+)\})[ \t]*$")
ABSOLUTE = re.compile(
    r"\b(?:the first(?: ever)?|unprecedented|universally|all existing (?:studies|methods)|"
    r"outperforms? all|state[- ]of[- ]the[- ]art|no (?:prior|previous) (?:work|study|studies))\b|"
    r"首次|迄今首个|国际领先|所有现有|全面超越|此前尚无|填补了.{0,8}空白",
    re.I,
)
UNRESOLVED = re.compile(r"(?i:\[(?:material gap|todo\b|citation needed|待补|待核)\])|\b(?:TODO|TBD|FIXME|UNVERIFIED)\b")


def load_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or (SKILL_ROOT / "schemas" / "writing_rules.yaml")
    return yaml.safe_load(target.read_text(encoding="utf-8"))


def _plain(text: str) -> str:
    return normalized(re.sub(r"[*`]", "", str(text or "")))


def _heading_level(m: re.Match) -> int:
    ln = m.group(0)
    if ln.startswith("#"):
        return len(ln) - len(ln.lstrip("#"))
    return 2 + ln.split("section", 1)[0].count("sub")


def classify_sections(body: str, rules: Dict[str, Any], language: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {role: [] for role in SECTION_TITLES}
    rkey = "required_sections_zh" if language.startswith("zh") else "required_sections_en"
    configured = dict(zip(SECTION_TITLES, rules.get(rkey, [])))
    headings = list(HEADING.finditer(body))
    for i, head in enumerate(headings):
        title = _plain(head.group(1) or head.group(2))
        lvl = _heading_level(head)
        end = next((h.start() for h in headings[i + 1:] if _heading_level(h) <= lvl), len(body))
        part = body[head.end():end]
        for role, pattern in SECTION_TITLES.items():
            if re.search(pattern, title) or (configured.get(role) and normalized(configured[role]) == title):
                if HEADING.sub("", part).strip():
                    sections[role].append(part)
    return sections


def verify_manuscript(
    draft_md: str,
    materials: Dict[str, Any],
    ledger: Dict[str, Any],
    index_store: IndexStore,
    *,
    task_mode: str = "full_intro_rw",
    language: str = "en",
    rules_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run 7-gate QA audit on manuscript + evidence ledger; return must_fix, warnings, checks, metrics."""
    rules = load_rules(rules_path)
    must_fix: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    if not draft_md or not draft_md.strip():
        must_fix.append({"check": "structure", "code": "empty_manuscript", "message": "Manuscript draft is empty."})
        return {
            "schema_version": "writing-08-qa/v2",
            "passed": False,
            "must_fix": must_fix,
            "warnings": warnings,
            "checks": {"structure": False},
            "metrics": {"literature_count": len(materials.get("papers", [])), "citation_coverage_ratio": 0.0},
        }

    parts = REFERENCES.split(draft_md, maxsplit=1)
    body, bibliography = parts[0], parts[1] if len(parts) > 1 else ""
    papers = [p for p in materials.get("papers", []) if isinstance(p, dict)]
    citations = Citations(papers, bibliography)

    # Gate 1: Unresolved placeholders (MUST_FIX)
    if UNRESOLVED.search(draft_md):
        must_fix.append({"check": "honesty", "code": "unresolved_placeholder", "message": "Manuscript contains unresolved TODO/TBD/placeholder token."})

    # Gate 2: Section structure (task-mode aware; never false-blocks continuous prose or narrow revisions)
    sections = classify_sections(body, rules, language)
    present_roles = [r for r, frags in sections.items() if frags]
    if task_mode == "full_intro_rw":
        missing_roles = [r for r in SECTION_TITLES if r not in present_roles]
        if missing_roles:
            warnings.append({"check": "structure", "code": "missing_section_heading", "missing_sections": missing_roles})
    elif task_mode == "related_work_only" and not sections["related_work"] and len(body.strip()) < 80:
        warnings.append({"check": "structure", "code": "short_related_work"})

    # Gate 3: Anti-cliché & cross-paper synthesis (advisory warnings)
    cliches = [p for p in rules.get("banned_cliche_openings", []) if re.search(p, body, re.I)]
    if cliches:
        warnings.append({"check": "synthesis", "code": "banned_cliche_opening", "patterns": cliches})

    related = "\n\n".join(sections["related_work"]) or body
    comparative = []
    for para in dict.fromkeys(re.split(r"\n\s*\n", related)):
        keys = citations.resolve(para)[0].intersection(citations.papers)
        if len(keys) >= 2 and COMPARISON.search(para):
            comparative.append(sorted(keys))
    if len(papers) >= 2 and not comparative:
        paragraphs = [p for p in re.split(r"\n\s*\n", related) if p.strip()]
        per_para = [citations.resolve(p)[0].intersection(citations.papers) for p in paragraphs]
        if not any(len(v) >= 2 for v in per_para):
            warnings.append({"check": "synthesis", "code": "weak_cross_paper_synthesis", "message": "Compare >= 2 papers on a shared axis rather than summarizing one per paragraph."})

    # Gate 4: Citations (unknown inline [@key] is MUST_FIX; missing numeric/metadata are warnings)
    body_keys, missing_nums, ambiguous = citations.resolve(body)
    unknown_keys = sorted(body_keys.difference(citations.papers))
    covered = sorted(body_keys.intersection(citations.papers))
    if unknown_keys:
        must_fix.append({"check": "citations", "code": "unknown_inline_citation", "cite_keys": unknown_keys, "message": f"Unknown citation key(s): {unknown_keys}"})
    if missing_nums:
        warnings.append({"check": "citations", "code": "numeric_reference_unresolved", "numbers": sorted(missing_nums)})
    if ambiguous:
        warnings.append({"check": "citations", "code": "ambiguous_citation", "labels": sorted(ambiguous)})
    if papers and not covered:
        warnings.append({"check": "citations", "code": "no_supplied_paper_cited"})

    # Gate 5: Evidence quotes & numeric/unit/negation/hedge fidelity
    derived_vals = {str(d.get("value") or "").strip() for d in (ledger.get("derived_values") or []) if isinstance(d, dict)}
    ev_items: List[tuple[str, str, Dict[str, Any]]] = []
    for idx, card in enumerate(ledger.get("paper_cards") or []):
        if isinstance(card, dict):
            for ev in card.get("evidence") or []:
                if isinstance(ev, dict):
                    ev_items.append((f"paper_card:{card.get('cite_key', idx)}", card.get("key_finding") or card.get("limitation") or "", ev))
    for field in ("claim_links", "research_gaps", "contributions"):
        for idx, row in enumerate(ledger.get(field) or []):
            if isinstance(row, dict):
                claim_txt = str(row.get("claim") or row.get("gap_statement") or "")
                for ev in row.get("evidence") or []:
                    if isinstance(ev, dict):
                        ev_items.append((f"{field}[{idx}]", claim_txt, ev))

    verified_quotes = 0
    for ctx, claim_txt, ev in ev_items:
        quote = str(ev.get("quote") or "").strip()
        did = str(ev.get("doc_id") or ev.get("source_id") or "doc_1")
        anc = str(ev.get("anchor") or "")
        vr = ev_verify(quote, f"{did}:{anc}" if anc else did, did, index_store)
        if vr["match"] in ("unverified", "too_short"):
            must_fix.append({"check": "grounding", "code": "unverified_quote", "context": ctx, "quote": quote[:120], "message": f"Quote unverified ({vr['match']}) at {ctx}."})
            continue
        verified_quotes += 1
        ev_texts = [quote, vr.get("block_text", "")]
        if claim_txt:
            um = check_units(claim_txt, ev_texts)
            if um:
                warnings.append({"check": "fidelity", "code": "unit_mismatch", "context": ctx, "mismatches": um})
            nh = check_negation_and_hedge(claim_txt, quote)
            for flag in nh:
                warnings.append({"check": "fidelity", "code": flag, "context": ctx, "quote": quote[:120]})
            missing_nums_claim = [n for n in check_numbers(claim_txt, ev_texts) if n not in derived_vals]
            if missing_nums_claim:
                warnings.append({"check": "fidelity", "code": "number_not_in_evidence", "context": ctx, "numbers": missing_nums_claim})

    # Gate 6: Gap & Contribution alignment (warnings)
    gaps = [g for g in (ledger.get("research_gaps") or []) if isinstance(g, dict)]
    contribs = [c for c in (ledger.get("contributions") or []) if isinstance(c, dict)]
    for g in gaps:
        if not g.get("derived_from_cite_keys") and not g.get("author_judgment"):
            warnings.append({"check": "gaps", "code": "gap_missing_literature_premise", "gap_id": g.get("gap_id")})
    for c in contribs:
        if not c.get("evidence") and not materials.get("study_facts"):
            warnings.append({"check": "contributions", "code": "contribution_missing_study_evidence", "contrib_id": c.get("contrib_id")})

    # Gate 7: Observational design & interpretation limits (advisory)
    passages_map = {p["source_id"]: p for p in materials.get("passages", []) if p.get("source_id")}
    limits = [l for l in (ledger.get("interpretation_limits") or []) if isinstance(l, dict)]
    limit_issues, wording_alerts, limit_metrics = audit_interpretation(body, limits, passages_map, contribs, ABSOLUTE)
    for li in limit_issues:
        warnings.append({"check": "interpretation", "code": li.get("reason", "interpretation_limit_absent"), **li})
    for wa in wording_alerts:
        warnings.append({"check": "interpretation", "code": wa.get("reason", "review_claim_strength"), **wa})
    for m in ABSOLUTE.finditer(body):
        warnings.append({"check": "interpretation", "code": "absolute_novelty_claim", "phrase": m.group()})

    checks = {
        "structure_and_completeness": not any(x["check"] == "structure" for x in must_fix),
        "citation_resolution": not any(x["check"] == "citations" for x in must_fix),
        "evidence_grounding": not any(x["check"] == "grounding" for x in must_fix),
        "honesty_and_placeholders": not any(x["check"] == "honesty" for x in must_fix),
    }
    return {
        "schema_version": "writing-08-qa/v2",
        "passed": len(must_fix) == 0,
        "must_fix": must_fix,
        "warnings": warnings,
        "checks": checks,
        "metrics": {
            "task_mode": task_mode,
            "literature_count": len(papers),
            "citation_coverage_ratio": round(len(covered) / len(papers), 4) if papers else 0.0,
            "cited_in_manuscript": covered,
            "uncited_supplied_keys": sorted(set(citations.papers).difference(covered)),
            "verified_quotes_count": verified_quotes,
            "cross_work_comparisons": comparative,
            "gap_count": len(gaps),
            "contribution_count": len(contribs),
            **limit_metrics,
        },
    }
