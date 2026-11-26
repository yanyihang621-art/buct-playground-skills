#!/usr/bin/env python3
"""Compare a polished manuscript with independently segmented source sentences."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from text_utils import edit_similarity, extract_protected_tokens, load_style_profiles, write_json_atomic

ALIGN = "sentence_alignment"
FACTS = "numeric_formula_citation_invariance"
CLAIM = "hedging_and_causality_conservation"
SCOPE = "negation_and_scope_invariance"
TERMS = "terminology_and_register_consistency"
AUDIT = "minimal_edit_and_explainability"
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|FIXME)\b|\[(?:insert|placeholder)\b", re.I)
GENERIC_REASON = {"polish", "improve", "better", "improve clarity", "improved clarity", "润色", "优化", "改善表述",
                  "agent 学术语域与句法微调"}
# Families admit common grammatical paraphrases (e.g. causes -> caused).
# Conditions, quantifier bounds and polarity are separate from hedging.
SCOPE_PATTERNS = {
    "negative": r"\b(?:not|never|no|neither|without|cannot|unable to|fail(?:s|ed)? to|non[- ]?significant(?:ly)?)\b|n['’]t\b|未能|无法|不能|并非|并不|没有|尚未|未|无|不(?!同)",
    "exclusive": r"\b(?:only|solely|exclusively)\b|仅(?:仅)?|只(?=[在能有是])",
    "if": r"\b(?:if|provided that|on condition that)\b|如果|若|倘若",
    "unless": r"\bunless\b|除非",
    "at_least": r"\b(?:at least|(?:no|not) (?:fewer|less) than|a minimum of)\b|至少|不低于|不少于",
    "at_most": r"\b(?:at most|(?:no|not) more than|a maximum of)\b|至多|不超过|最多",
    "approximately": r"\b(?:approx(?:\.|imately)?|about|roughly|nearly|around)\b|大约|约为|约(?=\s*\d)",
    "universal": r"\b(?:all|every|each)\b|所有|每(?:一|个|组|次)",
    "majority": r"\b(?:most|a majority of|the majority of)\b|大多数|大部分",
    "increase": r"\b(?:increas(?:e[ds]?|ing)|more and more|higher|rose|rise[sn]?|elevated|greater)\b|增(?:加|高|大)|升高|高于",
    "decrease": r"\b(?:decreas(?:e[ds]?|ing)|lower(?:ed)?|fell|fall[sn]?|reduc(?:e[ds]?|tion)|declined)\b|降(?:低|至)|减少|低于",
}
CERTAINTY = {
    "modal": r"\b(?:may|might|could|possibly|potentially|perhaps|likely|probab(?:ly|le)|possible|plausib(?:ly|le)|appear(?:s|ed)? to|seem(?:s|ed)? to)\b|可能|或许|也许|有望|似乎",
    "inference": r"\b(?:suggest(?:s|ed)?|indicat(?:e[sd]?|ing)|consistent with|tend(?:s|ed)? to)\b|提示|倾向于|初步表明",
    "absolute": r"\b(?:prov(?:e[sd]?|en)|definitively|definitely|undoubtedly|certainly|always|must|inevitably)\b|证明|必然|毫无疑问|绝对|始终",
    "correlational": r"\b(?:associat(?:ed|ion)|correlat(?:ed|ion|es?)|coincid(?:ed|es?) with|accompanied by|linked to)\b|相关|关联|伴随",
    "causal": r"\b(?:caus(?:e[ds]?|al|ation)|lead(?:s|ing)? to|led to|result(?:ed|s|ing)? (?:in|from)|driv(?:e[sn]?|ing)|drove|induc(?:e[sd]?|ing)|attributable to|due to|because of)\b|导致|引起|诱发|归因于|源于",
}
CLAUSES = re.compile(r"\s*\b(?:but|whereas|however|although)\b\s*|[;；]|然而|但是|而非", re.I)
SCOPE_PATTERNS = {key: re.compile(pattern, re.I) for key, pattern in SCOPE_PATTERNS.items()}
CERTAINTY = {key: re.compile(pattern, re.I) for key, pattern in CERTAINTY.items()}


def _markers(text: str, patterns: dict) -> dict[str, int]:
    text = re.sub(r"\bas is known to all\b[:,]?", "", text, flags=re.I)
    markers = {}
    if patterns is SCOPE_PATTERNS:
        # "no" in "no more than" and "most" in "at most" belong to the
        # bound. A separate "not" outside that bound still negates the claim.
        for bound in ("at_least", "at_most"):
            text, count = patterns[bound].subn(" ", text)
            if count:
                markers[bound] = count
    markers.update({key: len(matches) for key, pattern in patterns.items() if (matches := pattern.findall(text))})
    return markers


_CAUSAL_NOMINAL = re.compile(
    r"\b(?:result(?:s|ed)? in|lead(?:s)? to|led to|caus(?:e[ds]?))\s+"
    r"(?:an?|the)\s+(increase|decrease|reduction)\s+in\b", re.I)
_DIRECT_EFFECT = re.compile(r"\b(increas(?:e[sd]?)|decreas(?:e[sd]?)|reduc(?:e[sd]?))\b", re.I)


def _causal_frame(text: str) -> str:
    """Canonicalize only two local grammatical realizations of the same effect.

    Keep actor, object, scope, numbers and other clauses in order. Matching an
    effect word somewhere in each sentence would accept reversed arguments.
    """
    def effect(match):
        term = match[1].casefold()
        return "<up>" if term.startswith("incr") else "<down>"
    text = _CAUSAL_NOMINAL.sub(effect, text)
    text = _DIRECT_EFFECT.sub(effect, text)
    return " ".join(text.casefold().split())


_LANGUAGE_RULES = (
    ("plural_agreement", re.compile(
        r"\b(?:the|these|those|both|our)\s+(?:results|findings|measurements|samples|experiments|"
        r"observations|estimates|participants|patients|groups|models|methods|values|catalysts)\s+"
        r"(?:was|is|has|shows|indicates|suggests|demonstrates|exhibits)\b", re.I),
     "Check the finite verb against the plural subject."),
    ("count_noun_number", re.compile(
        r"\b(?:[2-9]|[1-9]\d+|two|three|four|five|six|seven|eight|nine|ten|several|many|multiple)\s+"
        r"(?:participant|patient|group|replicate|experiment|measurement|sample|dataset|chamber|trial|"
        r"specimen|condition|cycle)(?=\s*(?:[.,;:!?)]|$))", re.I),
     "Check whether this count noun needs a plural ending."),
    ("duplicated_function_word", re.compile(r"\b(the|of|and|with|from|these)\s+\1\b", re.I),
     "Check whether the repeated function word is a transcription error."),
)
_INLINE_LITERAL = re.compile(r"\x60[^\x60]*\x60|\$[^$\n]+\$|\\\(.*?\\\)|\\\[.*?\\\]")
_PREPOSITION = re.compile(r"\b(?:of|among|between|from|in|on|for|with|as|to|near|across)\s+$", re.I)


def language_diagnostics(text: str) -> list[dict]:
    """Return advisory surface issues with zero-based, end-exclusive character offsets."""
    # Retain offsets. Expressions and inline code may contain identifiers or
    # deliberately ungrammatical test strings.
    prose = _INLINE_LITERAL.sub(lambda m: " " * len(m[0]), text)
    issues = []
    for code, pattern, message in _LANGUAGE_RULES:
        for match in pattern.finditer(prose):
            if code == "plural_agreement" and _PREPOSITION.search(prose[:match.start()]):
                continue
            issues.append({"code": code, "start": match.start(), "end": match.end(),
                           "excerpt": text[match.start():match.end()], "message": message})
    return sorted(issues, key=lambda item: item["start"])


def _term_pattern(term: str) -> re.Pattern:
    start = r"(?<![A-Za-z0-9_-])" if term[:1].isascii() and term[:1].isalnum() else ""
    end = r"(?![A-Za-z0-9_-])" if term[-1:].isascii() and term[-1:].isalnum() else ""
    literal = re.escape(term)
    if term.isascii() and term.islower() and term[:1].isalpha():
        literal = "(?:" + literal + "|^" + re.escape(term.capitalize()) + ")"
    return re.compile(start + literal + end)


def _fact_key(token: str) -> str:
    # Preserve literal math, citation keys and protected markup, including
    # meaningful internal whitespace. Permit typography-only spaces in values.
    token = token.strip()
    return re.sub(r"\s+", "", token) if re.match(r"^[±≈~<>≤≥=+−.\d-]", token) else token


def _paired_subjects(sentence):
    """Identify explicit labels preceding paired results in a 'respectively' clause."""
    if not re.search(r"\brespectively\b|分别", sentence, re.I):
        return None
    # Start with supported, explicit constructions; other cases need the editor's read-back.
    head = re.split(r"\b(?:were|was|is|are|had|have|has|show(?:ed|s)?|yield(?:ed|s)?|reach(?:ed|es)?|"
                    r"record(?:ed)?|achiev(?:e[ds]?)|exhibit(?:ed)?)\b|分别", sentence, maxsplit=1, flags=re.I)[0]
    # Do not infer entity order from an enumerated list of numerical outcomes.
    if re.search(r"\d\s*[%°]", head):
        return None
    pair = re.split(r"\band\b|与|和|及|、", head, flags=re.I)
    if len(pair) != 2:
        return None
    ignore = {"the", "for", "in", "with", "of", "group", "groups", "arm", "arms", "condition", "conditions",
              "samples", "sample", "both", "cohort", "cohorts", "mean", "means", "rate", "rates"}
    def labels(side):
        side = re.sub(r"^(?:在|对于|其中)", "", side.strip())
        return {w.casefold() for w in re.findall(r"[A-Za-z][\w-]*|[\u3400-\u9fff]+", side, flags=re.ASCII)
                if w.casefold() not in ignore}
    a, b = map(labels, pair)
    return a-b, b-a


def verify_translation(row: dict, glossary: dict) -> list[dict]:
    """Check transferable facts and explicit bilingual terminology across one aligned pair."""
    import unicodedata

    src = unicodedata.normalize("NFKC", row["polished_text"])
    tgt = unicodedata.normalize("NFKC", row["translation"])
    location = {"sent_id": row["sent_id"], "source_location": row["source_location"]}
    issues = []
    numeric = r"(?<![0-9.])(?:[+−-]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:[eE][+−-]?\d+)?)(?:\s*%)?"
    formula = r"\\(?:cite[tp]?|eqref|ref|label)\{[^}]+\}|\$[^$\n]+\$"
    for label, pattern in (("numeric_values", numeric), ("math_or_latex_references", formula)):
        def items(text):
            text = re.sub(r"百分之\s*(\d+(?:\.\d+)?)", r"\1%", text)
            text = re.sub(r"(\d)\s*(?:percent|per cent)\b", r"\1%", text, flags=re.I)
            return Counter(m.group().replace("−", "-").replace(" ", "") for m in re.finditer(pattern, text))
        before, after = items(src), items(tgt)
        if before != after:
            issues.append({**location, "code": "translation_" + label, "blocking": True,
                           "missing": list((before-after).elements()), "added": list((after-before).elements())})
    aliases = {"千赫兹": "kHz", "赫兹": "Hz", "毫秒": "ms", "秒": "s", "分钟": "min", "小时": "h",
               "千克": "kg", "毫克": "mg", "微克": "μg", "克": "g", "毫升": "mL", "升": "L",
               "纳米": "nm", "微米": "μm", "毫米": "mm", "厘米": "cm", "米": "m",
               "千帕": "kPa", "兆帕": "MPa", "摄氏度": "°C", "开尔文": "K",
               "毫克/升": "mg/L", "微克/升": "μg/L", "毫克/毫升": "mg/mL"}
    aliases.update({"hertz": "Hz", "kilohertz": "kHz", "kelvin": "K", "degrees Celsius": "°C"})
    for name, symbol in (("second", "s"), ("millisecond", "ms"), ("minute", "min"), ("hour", "h"),
                         ("kilogram", "kg"), ("gram", "g"), ("milligram", "mg"), ("microgram", "μg"),
                         ("liter", "L"), ("litre", "L"), ("milliliter", "mL"), ("millilitre", "mL"),
                         ("meter", "m"), ("metre", "m"), ("nanometer", "nm"), ("micrometer", "μm")):
        aliases.update({name: symbol, name + "s": symbol})
    units = sorted(set(aliases) | set(aliases.values()), key=len, reverse=True)
    unit_pat = re.compile(r"([+−-]?\d+(?:\.\d+)?)\s*(" + "|".join(map(re.escape, units)) + r")(?![A-Za-z])")
    def measurements(text):
        return Counter((m[1].replace("−", "-"), aliases.get(m[2], m[2])) for m in unit_pat.finditer(text))
    left, right = measurements(src), measurements(tgt)
    if left != right:
        issues.append({**location, "code": "translation_measurement_units", "blocking": True,
                       "missing": list((left-right).elements()), "added": list((right-left).elements())})
    for entry in glossary["entries"]:
        if not entry.get("bilingual"):
            continue
        for variant in entry.get("variants", []):
            canonical = entry["canonical"]
            for a, b in ((variant, canonical), (canonical, variant)):
                if _term_pattern(a).search(src) and not _term_pattern(b).search(tgt):
                    issues.append({**location, "code": "check_translation_terminology", "source_term": a,
                                   "expected": b})
    return issues


def verify_polish(polished_doc: dict[str, Any], guarded: dict[str, Any], glossary: dict[str, Any],
                  *, style_path: Path | None = None) -> dict[str, Any]:
    profiles = load_style_profiles(style_path)
    src_sents = guarded.get("sentences", [])
    pol_sents = polished_doc.get("sentences", [])
    checks = dict.fromkeys((ALIGN, FACTS, CLAIM, SCOPE, TERMS, AUDIT), True)
    violations: list[dict] = []
    warnings: list[dict] = []
    evidence: list[dict] = []
    src_ids = [s.get("sent_id") for s in src_sents]
    pol_ids = [s.get("sent_id") for s in pol_sents]
    source_by_id = {s.get("sent_id"): s for s in src_sents}

    def fail(check: str, record: dict, reason: str, **details) -> None:
        checks[check] = False
        violations.append({"check": check, **record, "reason": reason, **details})

    if src_ids != pol_ids or len(set(pol_ids)) != len(pol_ids) or not src_ids:
        fail(ALIGN, {}, "empty_input_or_sentence_order_mismatch", expected_ids=src_ids, actual_ids=pol_ids)

    locked = [e for e in glossary.get("entries", []) if e.get("locked") and e.get("canonical")]
    phrases = profiles.get("banned_colloquial_or_hype_en", []) + profiles.get("banned_colloquial_or_hype_zh", [])
    locked_forms = [(e, [(v, _term_pattern(v)) for v in dict.fromkeys([e["canonical"], *e.get("variants", [])]) if v])
                    for e in locked]
    token_total = token_errors = edited = claim_count = claim_failures = 0
    register_failures = term_failures = 0
    language_rows = []
    source_flags = target_flags = cleared_flags = 0
    term_first_seen: dict[str, dict] = {}
    defined = set()
    for row in pol_sents:
        sid = row.get("sent_id")
        if sid not in source_by_id:
            continue
        source = source_by_id[sid]
        loc = {k: source[k] for k in ("sent_id", "line", "source_location") if k in source}
        original = source["source_text"]
        target = row.get("polished_text")
        if row.get("source_text") != original:
            fail(ALIGN, loc, "audit_source_differs_from_guarded_original")
        if not isinstance(target, str) or not target.strip():
            fail(ALIGN, loc, "empty_target")
            continue
        changed = original != target
        edited += int(changed)
        if source.get("role") in {"verbatim_block", "structural"} and changed:
            fail(FACTS, loc, "structural_or_verbatim_content_changed")

        before = [token.strip() for token in extract_protected_tokens(original)]
        after = [token.strip() for token in extract_protected_tokens(target)] if changed else before
        old_keys, new_keys = list(map(_fact_key, before)), list(map(_fact_key, after))
        old_counts, new_counts = Counter(old_keys), Counter(new_keys)
        missing = list((old_counts - new_counts).elements())
        added = list((new_counts - old_counts).elements())
        reorder = not added and not missing and old_keys != new_keys
        token_total += sum((old_counts | new_counts).values())
        token_errors += len(added) + len(missing) + int(reorder)
        if added or missing or reorder:
            fail(FACTS, loc, "protected_items_changed", removed=missing, added=added, order_changed=reorder)
        if changed and sum(bool(re.search(r"\d", x)) for x in before) >= 2:
            first, second = _paired_subjects(original), _paired_subjects(target)
            if first and second and first[0] & second[1] and first[1] & second[0] and not reorder:
                fail(FACTS, loc, "paired_entities_swapped_at_unchanged_values",
                     original_left=sorted(first[0] & second[1]), original_right=sorted(first[1] & second[0]),
                     protected_items=before)
        audit_row = {**loc, "protected_source_items": before, "protected_target_items": after}
        evidence.append(audit_row)
        if source.get("role") != "prose":
            continue

        claim_count += 1
        if changed:
            old_claim, new_claim = _markers(original, CERTAINTY), _markers(target, CERTAINTY)
            audit_row.update(source_claim_markers=old_claim, target_claim_markers=new_claim)
            if (old_claim.keys() ^ new_claim.keys()) == {"causal"} and _causal_frame(original) == _causal_frame(target):
                warnings.append({**loc, "reason": "review_equivalent_causal_paraphrase"})
            elif set(old_claim) != set(new_claim):
                fail(CLAIM, loc, "claim_strength_or_relation_changed", source=old_claim, target=new_claim)
                claim_failures += 1
            elif old_claim != new_claim:
                warnings.append({**loc, "reason": "review_clause_level_claim_strength", "source": old_claim, "target": new_claim})
            # A change from "not A but B" to "A but not B" retains the count.
            # Compare scopes within aligned contrastive clauses as well.
            old_clauses = [_markers(c, SCOPE_PATTERNS) for c in CLAUSES.split(original)]
            new_clauses = [_markers(c, SCOPE_PATTERNS) for c in CLAUSES.split(target)]
            old_scope, new_scope = _markers(original, SCOPE_PATTERNS), _markers(target, SCOPE_PATTERNS)
            if old_scope != new_scope or (len(old_clauses) == len(new_clauses) and old_clauses != new_clauses):
                fail(SCOPE, loc, "polarity_scope_quantity_or_direction_changed", source=old_clauses, target=new_clauses)
            elif len(old_clauses) != len(new_clauses) and (old_scope or old_claim):
                warnings.append({**loc, "reason": "review_scope_after_clause_reordering"})
            etype = str(row.get("edit_type") or "").strip().casefold()
            reason = str(row.get("rationale") or "").strip().strip(" .。").casefold()
            if etype in {"", "unchanged", "preserved"} or not reason or reason in GENERIC_REASON:
                fail(AUDIT, loc, "changed_sentence_needs_specific_type_and_rationale")
            sim = edit_similarity(original, target)
            if sim < 0.35:
                warnings.append({**loc, "reason": "review_extensive_rewrite_against_source", "character_similarity": sim})
        if PLACEHOLDER.search(target) and not PLACEHOLDER.search(original):
            fail(AUDIT, loc, "introduced_placeholder")

        out_of_scope = row.get("edit_type") in {"out_of_scope", "outside_scope"} or source.get("in_scope") is False
        if out_of_scope and not changed:
            continue
        source_issues = language_diagnostics(original)
        target_issues = language_diagnostics(target) if changed else source_issues
        if source_issues or target_issues:
            language_rows.append({**loc, "source": source_issues, "polished": target_issues})
            source_flags += len(source_issues)
            target_flags += len(target_issues)
            original_counts = Counter(item["code"] for item in source_issues)
            final_counts = Counter(item["code"] for item in target_issues)
            cleared_flags += sum((original_counts - final_counts).values())
            for item in target_issues:
                warnings.append({**loc, **item, "reason": "review_language_in_context"})
        for phrase in phrases:
            if _term_pattern(phrase.casefold()).search(target.casefold()):
                # A genuine quotation or explicitly retained source claim can
                # contain a listed phrase. Escalate newly introduced hype.
                if _term_pattern(phrase.casefold()).search(original.casefold()):
                    warnings.append({**loc, "reason": "review_register_in_context", "phrase": phrase})
                else:
                    register_failures += 1
                    fail(TERMS, loc, "introduced_colloquial_or_hype_expression", phrase=phrase)
        for entry, forms in locked_forms:
            if entry.get("bilingual"):
                continue  # A bilingual term pair constrains the translation column.
            canon = entry["canonical"]
            expansion = entry.get("full_form")
            if expansion and canon not in defined and _term_pattern(canon).search(target):
                defined.add(canon)
                original_definition = re.search(re.escape(expansion) + r"\s*\(" + re.escape(canon) + r"\)", original, re.I)
                if original_definition and expansion.casefold() not in target.casefold():
                    term_failures += 1
                    fail(TERMS, loc, "first_definition_lost_or_changed", abbreviation=canon, source_expansion=expansion)
                elif not original_definition and expansion.casefold() not in target.casefold():
                    warnings.append({**loc, "reason": "check_first_use_expansion", "abbreviation": canon,
                                     "attested_expansion": expansion})
            if any(p.search(target) for _, p in forms):
                term_first_seen.setdefault(canon, loc)
            # Auto-detected acronyms are not a supplied ban on substituting
            # the source's valid full expansion, e.g. "SVM" -> full name.
            if entry.get("source") != "custom_glossary" and not entry.get("variants"):
                continue
            if any(pattern.search(original) for _, pattern in forms) and not forms[0][1].search(target):
                term_failures += 1
                fail(TERMS, loc, "locked_term_missing", expected=canon)
            # A variant contained in its own canonical form is compliant.
            target_without_canonical = forms[0][1].sub(" ", target)
            for variant, pattern in forms[1:]:
                if pattern.search(target_without_canonical):
                    term_failures += 1
                    fail(TERMS, loc, "noncanonical_variant", variant=variant, expected=canon)

    if glossary.get("conflicts") and polished_doc.get("status") == "completed":
        fail(AUDIT, {}, "glossary_conflicts_require_partial_status")
    return {
        "schema_version": "writing-02-qa/v1", "passed": all(checks.values()), "checks": checks,
        "metrics": {
            "sentence_count": len(pol_sents), "edited_count": edited,
            "token_invariance": round(max(0.0, 1 - token_errors / token_total), 4) if token_total else 1.0,
            "hedging_causality_conservation": round(1 - claim_failures / claim_count, 4) if claim_count else 1.0,
            "terminology_consistency": float(term_failures == 0),
            "register_cleanliness": float(register_failures == 0),
            "source_language_flags": source_flags, "remaining_language_flags": target_flags,
            "cleared_language_flags": cleared_flags,
        },
        "violations": violations, "warnings": warnings, "language_diagnostics": language_rows,
        "evidence_by_sentence": evidence, "term_first_occurrence": term_first_seen,
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
