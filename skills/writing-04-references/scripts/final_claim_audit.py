"""Reconcile clause coverage with located, occurrence-specific semantic evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from claim_coverage import fold, quantities
from verify_citations import _read_evidence


ATTRIBUTION = re.compile(
    r"\b(?:(?:prior|previous|independent|external|published|earlier|other)\s+(?:\w+\s+){0,2}"
    r"(?:stud(?:y|ies)|research|literature|reports?|trials?|investigators?|researchers?)|"
    r"(?:studies|researchers|investigators|the literature)\s+(?:have\s+)?(?:found|reported|showed|demonstrated)|"
    r"according to|et al\.)\b|(?:既往研究|此前研究|独立研究|其他研究|已有研究|有研究(?:报道|表明|发现)|文献报道|据报道)", re.I)
RESULT_VERB = re.compile(
    r"\b(?:reported|found|showed|observed|increas\w*|reduc\w*|decreas\w*|rose|fell|demonstrated|was|were)\b|"
    r"(?:报告|报道|发现|表明|观察|上升|提高|下降|减少|增加|降低|达到|为)", re.I)
# Intentionally narrow. Generic adjective sentiment cannot establish measurement direction.
PREDICATE = re.compile(
    r"\b(?:(?:did|was|were|has|have|had|is|are)\s+)?(?P<neg>not\s+)?"
    r"(?P<verb>increas(?:e|ed|es)|decreas(?:e|ed|es)|reduc(?:e|ed|es)|declin(?:e|ed|es)|"
    r"rose|fell|higher|lower)\b|(?P<zhneg>未|没有)?(?P<zhverb>上升|升高|增加|提高|下降|降低|减少)", re.I)
QVALUE = re.compile(r"\d+(?:[.,]\d+)?")
EXCEPTION = re.compile(
    r"\b(?:approximately|about|around|roughly|at least|at most|up to|range|confidence|median|mean|odds|"
    r"baseline|relative|absolute|compared with|compared to|respectively|versus|vs\.?)\b|"
    r"(?:约|至少|至多|不超过|中位|均值|置信|基线|相对|绝对|分别|相比|对比)|[~≈<>≤≥±]", re.I)
METRIC_PREFIX = re.compile(
    r"^.*(?:\b(?:found|shows?|showed|reported|suggested|observed|demonstrated)\s+(?:that\s+)?|"
    r"(?:研究表明|研究发现|研究报道|研究显示|据报道)[，,：:]?)")
INTRO = re.compile(r"^(?:and|but|whereas|while|however|therefore|the|a|an)\s+|^(?:而|但|发现|报道|表明|该)")
VAGUE = {"it", "they", "this", "that", "value", "rate", "levels", "effect", "results", "values", "parameter",
         "结果", "数值", "水平", "其", "它", "该指标"}
UNITS = {"%": "%", "percentage points": "percentage points", "个百分点": "percentage points"}
FILE_TEXT = {".csv", ".tsv", ".json", ".yaml", ".yml", ".log"}


@dataclass(frozen=True)
class Measurement:
    metric: str
    unit: str
    value: Decimal
    sense: str
    relation: str
    condition: str


def _measurement(text):
    text = fold(text).rstrip(".,;:!?。；，：！？ ")
    matches = list(PREDICATE.finditer(text))
    if len(matches) != 1 or EXCEPTION.search(text):
        return None
    pred = matches[0]
    left = METRIC_PREFIX.sub("", text[:pred.start()]).strip(" :，,：")
    while INTRO.search(left):
        left = INTRO.sub("", left, count=1)
    if not left or left in VAGUE or len(left) > 90 or re.search(r"[.;:!?，。；：！？]", left):
        return None
    # Require a single observed metric, direction, quantity and unit in this clause.
    if re.search(r"\b(?:and|or|from|to)\b|及|与|和|、", left):
        return None
    tail = text[pred.end():].strip()
    nums = list(QVALUE.finditer(tail))
    units = quantities(tail)
    if len(nums) != 1 or len(units) != 1:
        return None
    unit, values = next(iter(units.items()))
    value = next(iter(values))
    if len(values) != 1 or value <= 0:
        return None
    relation = tail[:nums[0].start()].strip()
    if relation not in {"", "by", "to", "了", "至", "到", "为"}:
        return None
    relation = {"了":"by", "至":"to", "到":"to", "为":"to"}.get(relation, relation)
    unit_tail = tail[nums[0].end():].lstrip()
    # Only compare when the trailing experimental conditions also match exactly.
    alias = next((name for name, standardized in UNITS.items() if standardized == unit and unit_tail.startswith(name)), unit)
    condition = unit_tail[len(alias):].strip(" ,，")
    raw = pred["verb"] or pred["zhverb"]
    sense = "up" if re.match(r"increas|rose|higher|上升|升高|增加|提高", raw) else "down"
    if pred["neg"] or pred["zhneg"]:
        sense = "not_" + sense
    return Measurement(left, unit, value, sense, relation, condition)


def _conflict(claim, source):
    a, b = _measurement(claim), _measurement(source)
    if not a or not b:
        return []
    if (a.metric, a.unit, a.relation, a.condition) != (b.metric, b.unit, b.relation, b.condition):
        return []
    flags = []
    # Distinct written precision can represent the same rounded value.
    if a.value != b.value:
        precision = max(a.value.as_tuple().exponent, b.value.as_tuple().exponent)
        same_precision = a.value.as_tuple().exponent == b.value.as_tuple().exponent
        if same_precision or abs(a.value - b.value) > Decimal("0.5").scaleb(precision):
            flags.append({"kind": "quantity", "metric": a.metric, "unit": a.unit,
                          "claim_value": str(a.value), "source_value": str(b.value)})
    opposite = {frozenset(["up", "down"]), frozenset(["up", "not_up"]), frozenset(["down", "not_down"])}
    if frozenset([a.sense, b.sense]) in opposite:
        flags.append({"kind": "direction", "metric": a.metric, "unit": a.unit,
                      "claim_direction": a.sense, "source_direction": b.sense})
    return flags


def _own_source(decision, evidence_root, manuscript_source):
    filename = decision.get("source_file")
    quote = fold(decision.get("source_quote"))
    if not evidence_root or not isinstance(filename, str) or not quote:
        return False
    try:
        root = Path(evidence_root).resolve()
        relative = Path(filename)
        target = (root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(root) or not target.is_file():
            return False
        if manuscript_source and target == (root / str(manuscript_source)).resolve():
            return False
        if target.name.casefold() in {"case.json", "task.md", "result.json"}:
            return False
        if target.suffix.lower() in FILE_TEXT:
            content = target.read_text(encoding="utf-8-sig")
        else:
            content, _ = _read_evidence(target, int(decision.get("page") or 0), target.stat().st_mtime_ns)
        return quote in fold(content)
    except (OSError, ValueError, TypeError, BadZipFile, ParseError, KeyError):
        return False


def _close(decision, clause, high, checked, adjacent, root, manuscript):
    quote = fold(decision.get("claim_quote"))
    if not quote or quote not in fold(clause.get("quote")) or not str(decision.get("reason") or "").strip():
        return False, "unlocated_claim_or_missing_reason"
    action = decision.get("decision")
    if action == "not_required":
        return (not high), "external_quantitative_attribution_requires_source" if high else ""
    if action == "own_result":
        if ATTRIBUTION.search(quote) or not _own_source(decision, root, manuscript):
            return False, "authorship_or_primary_material_not_confirmed"
        source = decision["source_quote"]
        if _conflict(quote, source):
            return False, "primary_material_conflicts_with_claim"
        return not high, "external_attribution_requires_edit" if high else ""
    if action == "shared_citation":
        occurrence, key = decision.get("occurrence_index"), decision.get("cite_key")
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or not isinstance(key, str):
            return False, "invalid_shared_reference_locator"
        ident = (occurrence, key)
        existing = checked.get(ident)
        if not existing or ident not in adjacent:
            return False, "shared_reference_not_adjacent_and_supported"
        src = existing.get("source_quote")
        # Bind the shared-citation explanation to the already verified source quotation.
        if not src or fold(src) != fold(decision.get("source_quote")) or _conflict(quote, src):
            return False, "shared_source_not_confirmed"
        if high and any(not vals.issubset(quantities(src).get(unit, set())) for unit, vals in quantities(clause["quote"]).items()):
            return False, "shared_quote_does_not_contain_attributed_quantity"
        return True, ""
    if action == "insufficient_evidence":
        return False, "evidence_gap_acknowledged"
    return False, "unknown_resolution"


def final_claim_audit(coverage, qa_report, *, resolutions=(), evidence_root=None):
    """Return outstanding material contradictions and unresolved high-priority external claims.

    Only QA-anchored quotations from the cited occurrence may produce a numeric contradiction.
    Retrieval candidates remain advisory. The caller should keep partial for blocking items.
    """
    clauses = coverage.get("clauses", [])
    checked = {(r.get("occurrence_index"), r.get("cite_key")): r for r in qa_report.get("claim_evidence", [])
               if r.get("verdict") == "supported" and r.get("quote_located")}
    by_clause = defaultdict(list)
    for item in resolutions or ():
        if isinstance(item, dict) and isinstance(item.get("claim_id"), str):
            by_clause[item["claim_id"]].append(item)
    blocking, queue, resolved = [], [], []
    indexed_markers = []
    for clause in clauses:
        indexed_markers.append({(m["occurrence_index"], key) for m in clause.get("markers", [])
                                for key in m["cite_keys"]})
    for i, clause in enumerate(clauses):
        point = {"claim_id": clause["id"], "line": clause["line"], "claim_text": clause["quote"]}
        for ident in sorted(indexed_markers[i]):
            review = checked.get(ident)
            if not review:
                continue
            claim = review.get("claim_quote") or ""
            source = review.get("source_quote") or ""
            if fold(claim) not in fold(clause["quote"]):
                continue
            comparison_claim = claim
            if _measurement(claim) is None:
                full_clause = clause["quote"]
                for marker in clause["markers"]:
                    if marker.get("tag"):
                        full_clause = full_clause.replace(marker["tag"], "", 1)
                # A shorter review anchor still points to the complete measured clause.
                if fold(claim) in fold(full_clause):
                    comparison_claim = full_clause
            flags = _conflict(comparison_claim, source)
            if flags:
                blocking.append({**point, "code": "reviewed_quote_conflict", "occurrence_index": ident[0],
                                 "cite_key": ident[1], "claim_quote": claim, "compared_claim": comparison_claim, "source_quote": source,
                                 "source_field": review.get("source_field"), "source_file": review.get("source_file"),
                                 "page": review.get("page"), "conflicts": flags})
        decisions = by_clause.get(clause["id"], [])
        if clause.get("triage") != "inspect_missing_citation" and not (decisions and clause.get("triage") == "author_result_or_method"):
            continue
        text = clause["quote"]
        high = bool(ATTRIBUTION.search(text) and RESULT_VERB.search(text) and quantities(text))
        issue = {**point, "code": "unresolved_external_quantitative_claim" if high else "possible_missing_citation"}
        if len(decisions) == 1:
            neighbors = set()
            for j in (i-1, i+1):
                if 0 <= j < len(clauses) and clauses[j].get("section") == clause.get("section"):
                    left, right = (clauses[j], clause) if j < i else (clause, clauses[j])
                    if right["line"] - left.get("end_line", left["line"]) <= 1:
                        neighbors.update(indexed_markers[j])
            decision = decisions[0]
            valid, why = _close(decision, clause, high, checked, neighbors, evidence_root, coverage.get("manuscript_source"))
            if valid:
                resolved.append({**point, "decision":decision["decision"], "reason":decision["reason"],
                                 **{k:decision[k] for k in ("source_file","source_quote","occurrence_index","cite_key","page") if k in decision}})
                continue
            issue["resolution_issue"] = why
            if why == "evidence_gap_acknowledged":
                issue.update(code="acknowledged_citation_evidence_gap", reason=decision["reason"])
                blocking.append(issue)
                continue
        elif len(decisions) > 1:
            issue["resolution_issue"] = "multiple_decisions_for_clause"
        (blocking if high else queue).append(issue)
    known = {row["id"] for row in clauses}
    for stale in sorted(by_clause.keys() - known):
        queue.append({"code":"stale_resolution", "claim_id":stale})
    counts = dict(Counter(row["code"] for row in blocking))
    return {"schema_version":"writing-04-final-claim-audit/v1", "passed":not blocking,
            "blocking":blocking, "review_queue":queue, "resolved":resolved,
            "summary":{"blocking_count":len(blocking), "advisory_count":len(queue), "resolved_count":len(resolved),
                       "blocking_by_code":counts}}
