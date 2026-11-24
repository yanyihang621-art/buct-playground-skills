#!/usr/bin/env python3
"""6-check deterministic semantic, hedging, invariant, and register verifier for polished manuscripts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from text_utils import (
    classify_hedge_tier,
    classify_relation_type,
    edit_similarity,
    has_negation_or_scope,
    load_style_profiles,
    write_json_atomic,
)

PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.I)


def verify_polish(
    polished_doc: Dict[str, Any],
    guarded: Dict[str, Any],
    glossary: Dict[str, Any],
    *,
    style_path: Path | None = None,
) -> Dict[str, Any]:
    """Audit sentence alignment, invariants, hedging/causality, negation, terminology/register, and explainability."""
    profiles = load_style_profiles(style_path)
    src_sents = guarded.get("sentences", [])
    pol_sents = polished_doc.get("sentences", [])

    violations: List[Dict[str, Any]] = []

    # 1. Sentence alignment
    src_ids = [s["sent_id"] for s in src_sents]
    pol_ids = [s["sent_id"] for s in pol_sents]
    empty_pol = [s["sent_id"] for s in pol_sents if not str(s.get("polished_text", "")).strip()]
    align_ok = (src_ids == pol_ids) and (len(set(pol_ids)) == len(pol_ids)) and (len(empty_pol) == 0)
    if not align_ok:
        violations.append({"check": "sentence_alignment", "empty_targets": empty_pol, "id_diff": list(set(src_ids) ^ set(pol_ids))})

    # 2. Numeric, equation, citation & cross-ref invariance
    tok_total = 0
    tok_kept = 0
    for s in pol_sents:
        src = s.get("source_text", "")
        pol = s.get("polished_text", "")
        for tok in s.get("protected_tokens", []):
            tok_total += 1
            if pol.count(tok) >= src.count(tok):
                tok_kept += 1
            else:
                violations.append({"check": "numeric_formula_citation_invariance", "sent_id": s["sent_id"], "missing_or_altered_token": tok})
    tok_fidelity = 1.0 if tok_total == 0 else round(tok_kept / tok_total, 4)
    invariance_ok = (tok_kept == tok_total)

    # 3. Hedging intensity & causality conservation
    hedge_ok = True
    hedge_checked = 0
    hedge_preserved = 0
    for s in pol_sents:
        if s.get("role") != "prose":
            continue
        src_h = s.get("hedge_tier", "neutral")
        pol_h = classify_hedge_tier(s["polished_text"], profiles)
        src_r = s.get("relation_type", "none")
        pol_r = classify_relation_type(s["polished_text"], profiles)
        hedge_checked += 1

        escalated_hedge = (src_h == "tentative" and pol_h in ("strong_absolute", "moderate")) or (src_h == "moderate" and pol_h == "strong_absolute")
        escalated_causal = (src_r == "correlational" and pol_r == "causal")
        if escalated_hedge or escalated_causal:
            hedge_ok = False
            violations.append(
                {
                    "check": "hedging_and_causality_conservation",
                    "sent_id": s["sent_id"],
                    "src_hedge": src_h,
                    "pol_hedge": pol_h,
                    "src_relation": src_r,
                    "pol_relation": pol_r,
                }
            )
        else:
            hedge_preserved += 1
    hedge_ratio = 1.0 if hedge_checked == 0 else round(hedge_preserved / hedge_checked, 4)

    # 4. Negation & scope invariance
    neg_ok = True
    for s in pol_sents:
        if s.get("role") != "prose":
            continue
        src_neg = s.get("negation_markers", [])
        if not src_neg:
            continue
        pol_neg = has_negation_or_scope(s["polished_text"])
        if not pol_neg:
            neg_ok = False
            violations.append({"check": "negation_and_scope_invariance", "sent_id": s["sent_id"], "expected_markers": src_neg})

    # 5. Terminology consistency & register cleanliness
    term_ok = True
    banned_en = profiles.get("banned_colloquial_or_hype_en", [])
    banned_zh = profiles.get("banned_colloquial_or_hype_zh", [])
    locked_entries = [e for e in glossary.get("entries", []) if e.get("locked")]
    for s in pol_sents:
        if s.get("role") != "prose":
            continue
        pol = s["polished_text"]
        low = pol.lower()
        for b in banned_en:
            if re.search(rf"(?<![A-Za-z]){re.escape(b)}(?![A-Za-z])", low):
                term_ok = False
                violations.append({"check": "terminology_and_register_consistency", "sent_id": s["sent_id"], "banned_phrase": b})
        for bz in banned_zh:
            if bz in pol:
                term_ok = False
                violations.append({"check": "terminology_and_register_consistency", "sent_id": s["sent_id"], "banned_phrase": bz})
        for entry in locked_entries:
            for var in entry.get("variants", []):
                if var != entry["canonical"] and re.search(rf"(?<![A-Za-z0-9\-]){re.escape(var)}(?![A-Za-z0-9\-])", pol):
                    term_ok = False
                    violations.append({"check": "terminology_and_register_consistency", "sent_id": s["sent_id"], "variant": var, "expected": entry["canonical"]})

    # 6. Minimal edit & explainability (plus glossary conflict honesty)
    explain_ok = True
    for s in pol_sents:
        if s["polished_text"] != s["source_text"]:
            if not str(s.get("edit_type", "")).strip() or not str(s.get("rationale", "")).strip():
                explain_ok = False
                violations.append({"check": "minimal_edit_and_explainability", "sent_id": s["sent_id"], "reason": "missing_edit_type_or_rationale"})
            if edit_similarity(s["source_text"], s["polished_text"]) < 0.25:
                violations.append({"check": "minimal_edit_and_explainability", "sent_id": s["sent_id"], "reason": "excessive_rewrite_ratio_below_0.25"})
                explain_ok = False
        if PLACEHOLDER_RE.search(s["polished_text"]):
            explain_ok = False
            violations.append({"check": "minimal_edit_and_explainability", "sent_id": s["sent_id"], "reason": "placeholder_leak"})

    if glossary.get("conflicts") and polished_doc.get("status") == "completed":
        explain_ok = False
        violations.append({"check": "minimal_edit_and_explainability", "reason": "glossary_conflicts_require_partial_status"})

    checks = {
        "sentence_alignment": align_ok,
        "numeric_formula_citation_invariance": invariance_ok,
        "hedging_and_causality_conservation": hedge_ok,
        "negation_and_scope_invariance": neg_ok,
        "terminology_and_register_consistency": term_ok,
        "minimal_edit_and_explainability": explain_ok,
    }
    passed = all(checks.values())

    return {
        "schema_version": "writing-02-qa/v1",
        "passed": passed,
        "metrics": {
            "sentence_count": len(pol_sents),
            "edited_count": polished_doc.get("edited_count", 0),
            "token_invariance": tok_fidelity,
            "hedging_causality_conservation": hedge_ratio,
            "terminology_consistency": 1.0 if term_ok else 0.0,
            "register_cleanliness": 1.0 if term_ok else 0.0,
        },
        "checks": checks,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify polished manuscript invariants and rationales.")
    parser.add_argument("--doc", required=True)
    parser.add_argument("--guarded", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    grd = json.loads(Path(args.guarded).read_text(encoding="utf-8"))
    gls = json.loads(Path(args.glossary).read_text(encoding="utf-8"))
    rep = verify_polish(doc, grd, gls)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
