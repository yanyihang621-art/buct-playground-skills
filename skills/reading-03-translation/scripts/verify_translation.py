#!/usr/bin/env python3
"""Deterministic QA verifier for bilingual literature translations."""

from __future__ import annotations

from collections import Counter
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from apply_translation import VERBATIM_ROLES, align_sentences, copied_prose, verbatim_allowed
from extract_terms import load_seed_glossary
from pdf_utils import write_json_atomic
from segment_and_protect import sentence_spans

LEFTOVER_TOKEN_RE = re.compile(r"__(?:EQ|CIT|REF|NUM)_\d{3}__")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME|PLACEHOLDER)\b|\[UNTRANSLATED\]", re.I)
# Include attached subscripts and digits within formulae, and accept equivalent minus glyphs.
NUMBER_RE = re.compile(r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+\-−]?\d+)?")
EN_CLAUSE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z])|,\s*(?:but|whereas|while)\s+", re.I)
ZH_CLAUSE = re.compile(r"[。！？；]|[，,]\s*(?:而|但(?:是)?|却)")
MODAL_RULE = {
    "en_patterns": [r"\b(?:may|might|possibly)\b"],
    "zh_markers": ["可能", "也许", "或许", "或能", "可望", "或可", "有望", "可以", "可"],
}


def numbers(text: str) -> Counter:
    # Citation / DOI / equation numbers are included on both sides.
    return Counter(n.replace("−", "-") for n in NUMBER_RE.findall(text))


def scope_preserved(source: str, target: str, rule: Dict[str, Any]) -> bool:
    def matches(clause: str) -> bool:
        return any(re.search(p, clause, re.I) for p in rule["en_patterns"])

    markers = rule["zh_markers"]
    if not any(m in target for m in markers):
        return False
    left = [part.strip() for part in EN_CLAUSE.split(source) if part.strip()]
    right = [part.strip() for part in ZH_CLAUSE.split(target) if part.strip()]
    if len(left) == len(right) and len(left) > 1:
        return all(not matches(en) or any(m in zh for m in markers) for en, zh in zip(left, right))
    return True



def verify_bilingual_translation(
    bilingual_doc: Dict[str, Any],
    segments_data: Dict[str, Any],
    glossary_data: Dict[str, Any],
    seed_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run 6 deterministic QA checks and return structured verification report."""
    seed = load_seed_glossary(seed_path)
    negation_pairs = seed.get("negation_pairs") or [
        {
            "en_patterns": [r"\bnot\b", r"\bnever\b", r"\bno\s+", r"\bwithout\b", r"\bunable\s+to\b", r"\bfailed\s+to\b"],
            "zh_markers": ["不", "未", "无", "没有", "非", "不能", "无法", "未能", "缺乏"],
        }
    ]

    negation_pairs = [*negation_pairs, MODAL_RULE]
    src_segs = segments_data.get("segments", [])
    bi_segs = bilingual_doc.get("segments", [])
    doc_status = bilingual_doc.get("status", "completed")
    input_doc_status = segments_data.get("doc_status", "completed")

    violations: List[Dict[str, Any]] = []
    human_review: List[Dict[str, Any]] = []

    # 1. Status honesty
    status_ok = True
    uncertainties = bilingual_doc.get("uncertainties", [])
    has_high_scan_issue = any(
        u.get("code") in ("ocr_unavailable", "scanned_no_text", "garbled_font_cid") or u.get("severity") == "high"
        for u in uncertainties
    )
    if input_doc_status == "refused" and doc_status != "refused":
        status_ok = False
        violations.append({"check": "status_honesty", "message": "Refused/encrypted source must produce status='refused'."})
    elif (input_doc_status == "partial" or has_high_scan_issue or (doc_status != "refused" and len(bi_segs) == 0)) and doc_status == "completed":
        status_ok = False
        violations.append({"check": "status_honesty", "message": "Partial/scanned/empty source cannot claim status='completed'."})

    if doc_status == "refused":
        return {
            "schema_version": "reading-03-qa/v1",
            "passed": status_ok,
            "doc_status": doc_status,
            "metrics": {
                "segment_count": 0,
                "translated_count": 0,
                "alignment_completeness": 1.0 if len(src_segs) == len(bi_segs) == 0 else 0.0,
                "protected_token_fidelity": 1.0,
                "terminology_consistency": 1.0,
                "negation_preservation": 1.0,
                "locked_term_count": glossary_data.get("entry_count", 0),
                "uncertainty_count": len(bilingual_doc.get("uncertainties", [])),
            },
            "checks": {"status_honesty": status_ok},
            "violations": violations,
            "human_review_items": bilingual_doc.get("uncertainties", []),
        }

    # 2. Alignment completeness (1-to-1 ordered IDs, non-empty target)
    src_ids = [s["segment_id"] for s in src_segs]
    bi_ids = [s["segment_id"] for s in bi_segs]
    source_by_id = {seg["segment_id"]: seg for seg in src_segs}
    wrong_sources = [
        seg["segment_id"] for seg in bi_segs if seg["segment_id"] in source_by_id
        and any(seg.get(k) != source_by_id[seg["segment_id"]].get(k)
                for k in ("source_text", "page", "block_id", "role"))
    ]
    empty_targets = [s["segment_id"] for s in bi_segs if not str(s.get("target_text", "")).strip()]
    align_ok = (src_ids == bi_ids) and (len(set(bi_ids)) == len(bi_ids)) and (len(empty_targets) == 0) and not wrong_sources
    for issue in uncertainties:
        if issue.get("code") in ("duplicate_translation_id", "unknown_translation_id", "conflicting_sentence_targets"):
            align_ok = False
            violations.append({"check": "alignment_completeness", **issue})
    if not align_ok:
        violations.append(
            {
                "check": "alignment_completeness",
                "missing_or_extra": list(set(src_ids) ^ set(bi_ids)),
                "empty_targets": empty_targets,
                "wrong_source_anchors": wrong_sources,
            }
        )

    # 3. Protected token fidelity (every token value preserved with same multiplicity)
    total_tokens = 0
    preserved_tokens = 0
    for seg in bi_segs:
        original = source_by_id.get(seg["segment_id"], {})
        tokens = original.get("protected_tokens") or {}
        tgt = str(seg.get("target_text", ""))
        src = str(original.get("source_text", ""))
        for _, val in tokens.items():
            total_tokens += 1
            expected_count = src.count(val)
            actual_count = tgt.count(val)
            if actual_count >= 1 and actual_count == expected_count:
                preserved_tokens += 1
            else:
                violations.append(
                    {
                        "check": "protected_token_fidelity",
                        "segment_id": seg["segment_id"],
                        "token_value": val,
                        "expected_count": expected_count,
                        "actual_count": actual_count,
                    }
                )
    token_fidelity = 1.0 if total_tokens == 0 else round(preserved_tokens / total_tokens, 4)
    tokens_ok = (preserved_tokens == total_tokens)

    quantities_ok = True
    for seg in bi_segs:
        original = source_by_id.get(seg["segment_id"], {})
        expected = numbers(original.get("source_text", ""))
        actual = numbers(seg.get("target_text", ""))
        if expected != actual:
            quantities_ok = False
            violations.append({"check": "numeric_fidelity", "segment_id": seg["segment_id"],
                               "missing": list((expected - actual).elements()), "added": list((actual - expected).elements())})

    sentence_ok = True
    paired_sentences = 0
    for seg in bi_segs:
        original = source_by_id.get(seg["segment_id"])
        if (not original or seg.get("role") not in {"body", "abstract", "caption", "footnote", "table_cell", "table_note"}
                or seg.get("status") not in {"translated", "uncertain"}):
            continue
        src, tgt = original["source_text"], seg["target_text"]
        spans = sentence_spans(src)
        pairs = seg["sentence_alignment"] if "sentence_alignment" in seg else align_sentences(original, tgt)
        if (len(pairs) != len(spans)
                or "".join(str(p.get("target_text", "")).strip() for p in pairs).replace(" ", "")
                != tgt.strip().replace(" ", "")):
            sentence_ok = False
            violations.append({"check": "sentence_alignment", "segment_id": seg["segment_id"],
                               "source_sentence_count": len(spans), "message":
                               "Reconcile possible omission against source sentences or supply sentence_targets."})
            continue
        for pair, (a, b) in zip(pairs, spans):
            target = str(pair.get("target_text", ""))
            reason = ""
            if (pair.get("source_start"), pair.get("source_end"), pair.get("source_text")) != (a, b, src[a:b]) or not target.strip():
                reason = "Missing translation or incorrect source offset."
            elif len(spans) > 1 and numbers(src[a:b]) != numbers(target):
                reason = "Quantity or identifier is missing, changed or attached to a different sentence."
            elif len(spans) > 1 and any(any(re.search(p, src[a:b], re.I) for p in rule["en_patterns"])
                                        and not scope_preserved(src[a:b], target, rule) for rule in negation_pairs):
                reason = "Check which sentence contains this negation or qualifier."
            if reason:
                sentence_ok = False
                violations.append({"check": "sentence_alignment", "segment_id": seg["segment_id"],
                                   "sentence_id": pair.get("sentence_id"), "source_text": src[a:b], "message": reason})
            else:
                paired_sentences += 1

    # 4. Terminology consistency across all translatable segments
    locked_entries = [e for e in glossary_data.get("entries", []) if e.get("status") in ("locked", "keep_verbatim")]
    term_checks = 0
    term_hits = 0
    for seg in bi_segs:
        if seg.get("role") in VERBATIM_ROLES:
            continue
        src = str(seg.get("source_text", ""))
        tgt = str(seg.get("target_text", ""))
        for entry in locked_entries:
            variants = [entry["source_term"]] + list(entry.get("aliases", []))
            appeared = any(
                re.search(rf"(?<![A-Za-z0-9\-]){re.escape(v)}(?![A-Za-z0-9\-])", src, re.IGNORECASE)
                for v in variants
            )
            if not appeared:
                continue
            term_checks += 1
            core = entry.get("core_zh") or entry["target_zh"]
            full = entry["target_zh"]
            if (full in tgt) or (core and core in tgt):
                term_hits += 1
            else:
                violations.append(
                    {
                        "check": "terminology_consistency",
                        "segment_id": seg["segment_id"],
                        "source_term": entry["source_term"],
                        "expected_zh": entry["target_zh"],
                    }
                )
    term_consistency = 1.0 if term_checks == 0 else round(term_hits / term_checks, 4)
    terms_ok = (term_hits == term_checks)

    # 5. Negation & scope preservation
    neg_checks = 0
    neg_hits = 0
    for seg in bi_segs:
        if seg.get("role") in VERBATIM_ROLES:
            continue
        src = str(seg.get("source_text", ""))
        tgt = str(seg.get("target_text", ""))
        for pair in negation_pairs:
            has_en_neg = any(re.search(p, src, re.IGNORECASE) for p in pair.get("en_patterns", []))
            if not has_en_neg:
                continue
            neg_checks += 1
            # Work on masked sentences to avoid splitting inside citations or figure labels.
            masked_src = seg.get("masked_text", src)
            masked_tgt = tgt
            for key, value in seg.get("protected_tokens", {}).items():
                masked_tgt = masked_tgt.replace(value, key)
            has_zh_neg = scope_preserved(masked_src, masked_tgt, pair)
            if has_zh_neg:
                neg_hits += 1
            else:
                violations.append(
                    {
                        "check": "negation_and_scope_preservation",
                        "segment_id": seg["segment_id"],
                        "message": "Source negation/scope qualifier missing corresponding Chinese marker.",
                    }
                )
    neg_ratio = 1.0 if neg_checks == 0 else round(neg_hits / neg_checks, 4)
    neg_ok = (neg_hits == neg_checks)

    # 6. Placeholder & untranslated prose leakage check
    leak_ok = True
    for seg in bi_segs:
        tgt = str(seg.get("target_text", ""))
        if LEFTOVER_TOKEN_RE.search(tgt) or PLACEHOLDER_RE.search(tgt):
            leak_ok = False
            violations.append(
                {
                    "check": "placeholder_and_leakage_check",
                    "segment_id": seg["segment_id"],
                    "message": "Unresolved placeholder token or TODO marker remained in target_text.",
                }
            )
        original = source_by_id.get(seg["segment_id"], {})
        if original and (seg.get("status") == "untranslatable" or copied_prose(original, tgt, locked_entries)):
            leak_ok = False
            violations.append({"check": "placeholder_and_leakage_check", "segment_id": seg["segment_id"],
                               "message": "Translation missing or source prose copied."})
        if seg.get("status") == "verbatim" and original and not verbatim_allowed(original, locked_entries):
            status_ok = False
            violations.append({"check": "status_honesty", "segment_id": seg["segment_id"],
                               "message": "Prose marked verbatim needs a translation."})
        if doc_status == "completed" and seg.get("status") in ("uncertain", "untranslatable"):
            status_ok = False
            violations.append({"check": "status_honesty", "segment_id": seg["segment_id"],
                               "message": "Pending segments prevent a completed result."})

    for t in bilingual_doc.get("tables_bilingual", []):
        if t.get("is_image_table") or t.get("status") not in ("extracted", "ok"):
            human_review.append(
                {
                    "item": t.get("table_id"),
                    "pages": t.get("pages", []),
                    "reason": f"Image or partial table (`{t.get('status')}`) requires human visual verification.",
                }
            )
    for u in bilingual_doc.get("uncertainties", []):
        human_review.append(u)

    checks = {
        "status_honesty": status_ok,
        "alignment_completeness": align_ok,
        "protected_token_fidelity": tokens_ok,
        "numeric_fidelity": quantities_ok,
        "sentence_alignment": sentence_ok,
        "terminology_consistency": terms_ok,
        "negation_and_scope_preservation": neg_ok,
        "placeholder_and_leakage_check": leak_ok,
    }
    passed = all(checks.values())

    return {
        "schema_version": "reading-03-qa/v1",
        "passed": passed,
        "doc_status": doc_status,
        "metrics": {
            "segment_count": len(bi_segs),
            "translated_count": sum(1 for s in bi_segs if s.get("status") == "translated"),
            "verbatim_count": sum(1 for s in bi_segs if s.get("status") == "verbatim"),
            "verified_sentence_pairs": paired_sentences,
            "pending_count": sum(1 for s in bi_segs if s.get("status") in ("uncertain", "untranslatable")),
            "alignment_completeness": 1.0 if align_ok else 0.0,
            "protected_token_fidelity": token_fidelity,
            "terminology_consistency": term_consistency,
            "negation_preservation": neg_ratio,
            "locked_term_count": len(locked_entries),
            "uncertainty_count": len(bilingual_doc.get("uncertainties", [])),
        },
        "checks": checks,
        "violations": violations,
        "human_review_items": human_review,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify bilingual literature translation quality & fidelity.")
    parser.add_argument("--bilingual", required=True, help="Path to bilingual_doc.json")
    parser.add_argument("--segments", required=True, help="Path to segments.json")
    parser.add_argument("--glossary", required=True, help="Path to glossary.json")
    parser.add_argument("--out", required=True, help="Output qa_report.json path")
    args = parser.parse_args()

    bi = json.loads(Path(args.bilingual).read_text(encoding="utf-8"))
    segs = json.loads(Path(args.segments).read_text(encoding="utf-8"))
    glos = json.loads(Path(args.glossary).read_text(encoding="utf-8"))

    report = verify_bilingual_translation(bi, segs, glos)
    write_json_atomic(Path(args.out), report)
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "violations_count": len(report["violations"])}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
