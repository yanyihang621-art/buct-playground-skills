"""Check declared manuscript boundaries and flag strong wording for author review."""

from __future__ import annotations

import re

from citation_audit import normalized


OBSERVATIONAL = re.compile(
    r"\b(?:observational|correlational|cross[- ]sectional|retrospective|ecological (?:study|analysis)|"
    r"(?:aggregate|group)[- ]level (?:data|comparison|analysis)|(?:grouped|stratified) by|"
    r"self[- ]selected|non[- ]?randomi[sz]ed|(?:not|never) randomly assigned|"
    r"without (?:an? )?intervention|no random (?:assignment|allocation))\b|"
    r"观察性|横断面|回顾性|分组比较|分层比较|组间比较|按[^。\n]{1,16}(?:分组|分层)|未经随机分配|非随机", re.I
)
INTERVENTION = re.compile(
    r"\b(?:randomi[sz]ed controlled (?:trial|study)|(?:were|was) randomly (?:assigned|allocated)|"
    r"assigned (?:at random|randomly)|we (?:randomly assigned|manipulated)|controlled intervention)\b|"
    r"随机对照(?:试验|实验|研究)|(?:受试者|样本|实验单位)(?:被)?随机分配|实施了干预", re.I
)
CAUSAL = re.compile(
    r"\b(?:causes?|caused|causal(?:ly)?|causation|leads? to|led to|results? in|resulted in|"
    r"drives?|driven by|produces?|attributable to|responsible for|determines?|guarantees?|"
    r"universally|always|never fails?|proves?|definitively|under all conditions)\b|"
    r"导致|造成|因果|归因于|决定了|必然|均能保证|所有情形|足以证明|完全由", re.I
)
QUALIFIED = re.compile(
    r"\b(?:cannot|can['’]t|could not|do(?:es)? not|did not|unable to|insufficient to|cannot by itself)"
    r"\s+(?:\w+[ -]){0,5}(?:establish|identify|infer|imply|prove|demonstrate|attribute|determine|conclude|test)|"
    r"\b(?:should not|must not|cannot|cannot be|is not|are not)\s+(?:\w+\s+){0,3}"
    r"(?:interpreted|inferred|taken|ascribed|attributed|evidence)\b|"
    r"\b(?:whether|hypothesi[sz]e|unclear (?:if|whether)|may|might|could potentially)\b|"
    r"\bno (?:evidence|basis|grounds?)\b|(?:不能|不应|无法|不足以|难以|尚不能)(?:据此|直接)?"
    r"(?:判断|证实|证明|推断|判定|解释|归因|断言|确定|建立)|是否|可能|尚待验证", re.I
)
NONPROSE = re.compile(r"<!--.*?-->|^`{3,}[^\n]*\n.*?^`{3,}[ \t]*$|^~{3,}[^\n]*\n.*?^~{3,}[ \t]*$", re.S | re.M)


def _visible(text):
    # Keep line coordinates stable and avoid treating a hidden editorial note as delivered prose.
    return NONPROSE.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), text)


def _agrees_with_limit(clause, match):
    before = clause[:match.start()]
    after = clause[match.end():]
    if QUALIFIED.search(before):
        return True
    if re.search(r"(?:causality|causation|causal.{0,12}(?:effect|inference)|因果.{0,5}(?:关系|效应))",
                 clause[match.start():], re.I):
        return bool(re.match(r".{0,28}(?:cannot be inferred|was not established|remains unclear|"
                             r"is not identified|unproven|未知|尚未证实|无法推断)", after, re.I))
    return False


def audit_interpretation(body, limits, passages, contributions, absolute_pattern):
    visible = _visible(body)
    folded = normalized(re.sub(r"[*`]", "", visible))
    violations, warnings = [], []
    expected = verified = 0
    for i, item in enumerate(limits, 1):
        if item.get("required_in_manuscript") is False:
            continue
        expected += 1
        span = item.get("manuscript_span")
        if isinstance(span, str) and span.strip() and normalized(re.sub(r"[*`]", "", span)) in folded:
            verified += 1
        else:
            violations.append({"check": "honesty_and_material_sufficiency",
                               "reason": "interpretation_limit_absent_from_manuscript", "row": i,
                               "kind": item.get("kind"), "manuscript_span": span, "rationale": item.get("reason"),
                               "action": "Add or restore the intended boundary near the claim and update its exact span."})

    own_ids = {e.get("source_id") for c in contributions for e in c.get("evidence", []) if isinstance(e, dict)}
    study = [p for p in passages.values() if p.get("role") == "study"
             or (p.get("role") == "material" and p.get("source_id") in own_ids)]
    observational = [p for p in study if OBSERVATIONAL.search(str(p.get("text", "")))]
    if observational and not any(INTERVENTION.search(str(p.get("text", ""))) for p in study):
        sources = [p["source_id"] for p in observational[:3]]
        for line_no, line in enumerate(visible.splitlines(), 1):
            # A qualification in a previous clause must not excuse a later unsupported assertion.
            for fragment in re.split(r"[。！？；;]|(?<=[.!?])\s+|\b(?:but|yet|whereas)\b|但是|然而", line):
                matches = sorted([*CAUSAL.finditer(fragment), *absolute_pattern.finditer(fragment)],
                                 key=lambda m: m.start())
                hit = next((m for m in matches if not _agrees_with_limit(fragment, m)), None)
                if hit:
                    warnings.append({"reason": "review_claim_strength_for_observational_design", "line": line_no,
                                     "phrase": hit.group(), "excerpt": fragment.strip()[:240], "source_ids": sources,
                                     "action": "Check attribution and scope. Explain what this comparison can establish."})
    return violations, warnings, {"interpretation_limits_expected": expected, "interpretation_limits_in_text": verified}
