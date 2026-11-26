#!/usr/bin/env python3
"""Deterministic notes auditor: verifies contract, status honesty, section alignment, anchor existence, quote & numeric grounding."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_contract import contract_fields, contract_errors, schema_for
from pdf_utils import write_json_atomic

VALID_STATUSES = {"success", "partial", "unverifiable", "refused"}
PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]
SALIENT_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?(?:\s*%|\s*K\b|\s*MPa\b)")


def _required_keys(case_data: Dict[str, Any]) -> List[str]:
    return contract_fields(case_data, required_only=True) or (["case_id"] if schema_for(case_data) else ["case_id", "status"])


def _flatten_sec_ids(sections: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for s in sections:
        ids.append(s["section_id"])
        ids.extend(_flatten_sec_ids(s.get("children", [])))
    return ids


def _norm_text(s: str) -> str:
    text = unicodedata.normalize("NFC", s).replace("\u00ad", "").replace("−", "-").replace("\x00", "�")
    return " ".join(text.split())


def _quote_matches(quote: str, source_text: str) -> bool:
    snippet = _norm_text(quote)
    return bool(snippet) and snippet in _norm_text(source_text)


# Keep signs, decimal points, comparison operators, and unit case. Checking these
# against the cited evidence avoids borrowing a number from another experiment.
MEASUREMENT = re.compile(
    r"(?<![A-Za-z0-9_.])(?:[<>≤≥]=?\s*)?[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
    r"(?:\s*(?:[%％]|°[CF]|(?:[kMGTmunµμ]?(?:Pa|mol|M|g|m|L|s|Hz)|ppm|ppb|K|h)(?![A-Za-z])))?"
)
POLARITY = {
    "negation": r"\b(?:not|never|cannot|no|without|fail(?:ed|s)? to)\b|n['’]t\b|(?:并未|不能|未能|没有|未见|无显著)",
    "higher": r"\b(?:higher|greater|larger|increas(?:e|ed|es)|improv(?:e|ed|es))\b|(?:高于|大于|提高|增加)",
    "lower": r"\b(?:lower|less|smaller|decreas(?:e|ed|es)|reduc(?:e|ed|es))\b|(?:低于|小于|降低|减少)",
}


def _claim_problem(claim: str, evidence: List[str]) -> Optional[str]:
    """Screen an authored summary for local contradictions. Entailment still needs source review."""
    normalized = _norm_text(claim)
    quotes = [_norm_text(q) for q in evidence if q]
    if not normalized or not quotes:
        return "missing claim or evidence"
    if normalized in quotes:
        return None
    def quantities(text: str) -> Set[str]:
        return {re.sub(r"\s+", "", m.group()) for m in MEASUREMENT.finditer(text)}
    missing = quantities(normalized) - set().union(*(quantities(q) for q in quotes))
    if missing:
        return "quantity or unit outside cited evidence: " + ", ".join(sorted(missing))
    claim_order = re.findall(r"\w+|[<>≤≥]", normalized.casefold())
    words = set(claim_order)
    for quote in quotes:
        source_order = re.findall(r"\w+|[<>≤≥]", quote.casefold())
        source_words = set(source_order)
        if claim_order != source_order and sorted(claim_order) == sorted(source_order):
            return "reordered words or values may change the comparison or scope"
        if len(words & source_words) / max(1, len(words | source_words)) >= 0.55:
            for label, pattern in POLARITY.items():
                if bool(re.search(pattern, normalized, re.I)) != bool(re.search(pattern, quote, re.I)):
                    return f"check {label} against the cited sentence"
    return None


def audit_notes(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    docs_by_id: Dict[str, Dict[str, Any]],
    notes_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """Audit structured notes result against source document(s) and contract."""
    errors: List[str] = contract_errors(case_data, result)
    warnings: List[str] = []

    expected_cid = case_data.get("case_id")
    if expected_cid and result.get("case_id") != expected_cid:
        errors.append(f"case_id mismatch: expected '{expected_cid}', got '{result.get('case_id')}'.")

    for rk in _required_keys(case_data):
        if rk not in result:
            errors.append(f"Missing required output_contract key '{rk}'.")

    status = result.get("status", notes_bundle.get("status"))
    if status not in VALID_STATUSES:
        errors.append(f"Invalid status '{status}'.")

    any_encrypted_or_corrupt = any(
        d.get("diagnostics", {}).get("encrypted") or d.get("diagnostics", {}).get("corrupt")
        for d in docs_by_id.values()
    )
    if any_encrypted_or_corrupt and status == "success":
        if all(d.get("diagnostics", {}).get("encrypted") or d.get("diagnostics", {}).get("corrupt")
               for d in docs_by_id.values()):
            errors.append("Status cannot be 'success' when all source documents are unreadable.")
        else:
            warnings.append("Some sources are unreadable. Check the actual answer's scope against the readable evidence.")

    # Placeholder check
    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(serialized):
            errors.append(f"Found forbidden placeholder token matching {pat.pattern}.")

    # Build source element lookup per paper
    elem_lookup: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for pid, doc in docs_by_id.items():
        emap: Dict[str, Dict[str, Any]] = {}
        for b in doc.get("blocks", []):
            emap[b["block_id"]] = {"page": b["page"], "section": b.get("section_id"), "bbox": b.get("bbox"),
                                   "native": b.get("native_locator"),
                                   "text": b.get("structured_text") or b.get("text") or ""}
        for t in doc.get("tables", []):
            emap[t["table_id"]] = {
                "pages": t.get("pages", []),
                "text": (t.get("caption") or "") + " " + (t.get("markdown") or ""),
            }
        for f in doc.get("figures", []):
            emap[f["figure_id"]] = {"page": f["page"], "text": f.get("caption") or ""}
        for eq in doc.get("formulas", {}).get("display_equations", []):
            emap[eq["eq_id"]] = {"page": eq["page"], "text": eq.get("structured_text") or ""}
        elem_lookup[pid] = emap

    # Section alignment audit across papers
    sec_expected = 0
    sec_covered = 0
    order_monotonic = True
    for pn in notes_bundle.get("papers", []):
        pid = pn["paper_id"]
        doc = docs_by_id.get(pid, {})
        orig_secs = _flatten_sec_ids(doc.get("sections", []))
        note_secs = [sn["section_id"] for sn in pn.get("section_notes", [])]
        if orig_secs:
            sec_expected += len(orig_secs)
            matched = [sid for sid in orig_secs if sid in note_secs]
            sec_covered += len(matched)
            positions = [note_secs.index(sid) for sid in matched]
            if positions != sorted(positions):
                order_monotonic = False
                errors.append(f"Section order in section_notes for {pid} is not monotonic with paper hierarchy.")

    sec_cov_ratio = round(sec_covered / sec_expected, 4) if sec_expected > 0 else 1.0
    if sec_expected > 0 and sec_cov_ratio < 0.90:
        errors.append(f"Section alignment coverage too low: {sec_covered}/{sec_expected} ({sec_cov_ratio:.2%}).")

    # Anchor existence & quote grounding audit
    anchors_checked = 0
    anchors_valid = 0
    broken_anchors: List[str] = []

    def _verify_anchor(pid: str, anc: Dict[str, Any], ctx: str) -> None:
        nonlocal anchors_checked, anchors_valid
        anchors_checked += 1
        if anc.get("paper_id", pid) != pid:
            broken_anchors.append(f"{ctx}: anchor belongs to another paper")
            return
        emap = elem_lookup.get(pid, {})
        eid = anc.get("block_id") or anc.get("element_id") or anc.get("table_id") or anc.get("eq_id") or anc.get("figure_id")
        if not eid or eid not in emap:
            broken_anchors.append(f"{ctx}: missing element_id '{eid}' in {pid}")
            return
        src = emap[eid]
        if anc.get("native_locator") and src.get("native") != anc["native_locator"]:
            broken_anchors.append(f"{ctx}: changed native locator for {eid}")
            return
        if anc.get("source_lines") and src.get("bbox"):
            if list(anc["source_lines"]) != [int(src["bbox"][1]), int(src["bbox"][3])]:
                broken_anchors.append(f"{ctx}: source lines differ for {eid}")
                return
        p = anc.get("pdf_page")
        if "pages" in src:
            if p is not None and p not in src["pages"]:
                broken_anchors.append(f"{ctx}: page mismatch {p} not in {src['pages']} for {eid}")
                return
        elif p is not None and src.get("page") != p:
            broken_anchors.append(f"{ctx}: page mismatch {p} != {src.get('page')} for {eid}")
            return
        snip = anc.get("quote_snippet")
        if not snip or not _quote_matches(snip, src["text"]):
            broken_anchors.append(f"{ctx}: quote_snippet not grounded in {eid}")
            return
        anchors_valid += 1

    nums_checked = 0
    nums_grounded = 0
    summaries_screened = 0

    def check_claim(text: str, anchors: List[Dict[str, Any]], ctx: str, extractive: bool = False) -> None:
        nonlocal nums_checked, nums_grounded, summaries_screened
        quotes = [a.get("quote_snippet", "") for a in anchors]
        claim = _norm_text(text)
        if extractive:
            issue = None if claim and any(claim == _norm_text(q) for q in quotes) else "extract differs from its quotation"
        else:
            summaries_screened += 1
            issue = _claim_problem(claim, quotes)
        count = len(list(MEASUREMENT.finditer(claim)))
        nums_checked += count
        if issue:
            errors.append(f"{ctx}: {issue}")
        else:
            nums_grounded += count

    def check_card(pid: str, card: Dict[str, Any]) -> None:
        for name, slot in card.items():
            if not isinstance(slot, dict) or ("anchors" not in slot and "summary" not in slot):
                continue
            ctx = f"{pid}.paper_card.{name}"
            anchors = slot.get("anchors", [])
            for a in anchors:
                _verify_anchor(pid, a, ctx)
            if slot.get("status") in ("refused", "unverifiable", "not_reported"):
                continue
            for item in slot.get("items", []):
                check_claim(item, anchors, ctx, extractive=True)
            for sentence in slot.get("summary", "").split(" | "):
                if sentence:
                    check_claim(sentence, anchors, ctx)

    def check_sections(pid: str, sections: List[Dict[str, Any]]) -> None:
        for sn in sections:
            ctx = f"{pid}.section.{sn.get('section_id')}"
            for a in sn.get("anchors", []):
                _verify_anchor(pid, a, ctx)
            for cue in sn.get("cue_evidence", []):
                _verify_anchor(pid, cue, ctx)
                check_claim(cue["text"], [cue], ctx, extractive=True)
            for point in sn.get("key_points", []):
                anchor = point.get("anchor", {})
                _verify_anchor(pid, anchor, ctx)
                check_claim(point.get("text", ""), [anchor], ctx, extractive=True)
            if sn.get("summary"):
                anchors = sn.get("summary_anchors", sn.get("anchors", []))
                for a in anchors:
                    _verify_anchor(pid, a, ctx)
                    source = elem_lookup.get(pid, {}).get(a.get("block_id"), {})
                    if source.get("section") and source["section"] != sn.get("section_id"):
                        errors.append(f"{ctx}: cited block belongs to another section")
                check_claim(sn["summary"], anchors, ctx)

    def check_entry(pid: str, entry: Dict[str, Any]) -> None:
        ctx = f"{pid}.knowledge.{entry.get('entry_id')}"
        anchors = entry.get("anchors", [])
        for a in anchors:
            _verify_anchor(pid, a, ctx)
        if entry.get("entry_type") != "concept_definition":
            check_claim(entry.get("statement", ""), anchors, ctx, extractive=True)

    papers_to_check = notes_bundle.get("papers", [])
    for pn in papers_to_check:
        pid = pn["paper_id"]
        check_card(pid, pn.get("paper_card", {}))
        check_sections(pid, pn.get("section_notes", []))
        for entry in pn.get("knowledge_entries", []):
            check_entry(pid, entry)
        for g in pn.get("terminology_glossary", []):
            if g.get("definition_anchor"):
                _verify_anchor(pid, g["definition_anchor"], f"{pid}.glossary.{g['acronym']}")

    primary = papers_to_check[0] if papers_to_check else {}
    primary_id = primary.get("paper_id", "paper_1")
    if isinstance(result.get("paper_card"), dict) and result["paper_card"] != primary.get("paper_card"):
        check_card(primary_id, result["paper_card"])
    if isinstance(result.get("papers"), list) and all(isinstance(p, dict) and "paper_id" in p
                                                      for p in result["papers"]) and result["papers"] != papers_to_check:
        delivered_ids = [p.get("paper_id") for p in result["papers"]]
        if delivered_ids != [p.get("paper_id") for p in papers_to_check]:
            errors.append("Final papers field lost, added or reordered sources")
        for paper in result["papers"]:
            pid = paper.get("paper_id", "")
            check_card(pid, paper.get("paper_card", {}))
            check_sections(pid, paper.get("section_notes", []))
            for entry in paper.get("knowledge_entries", []):
                check_entry(pid, entry)
    delivered_cards = result.get("paper_cards")
    for idx, card in enumerate(delivered_cards if isinstance(delivered_cards, list) else []):
        if not isinstance(card, dict):
            continue
        if idx >= len(papers_to_check):
            errors.append(f"Final paper_cards[{idx}] has no source paper")
        elif card != papers_to_check[idx].get("paper_card"):
            check_card(papers_to_check[idx]["paper_id"], card)
    for field in ("section_notes", "section_summaries"):
        if field in result and result[field] != primary.get("section_notes"):
            values = result[field]
            if not isinstance(values, list) or not all(isinstance(s, dict) and s.get("section_id") for s in values):
                if not schema_for(case_data):
                    errors.append(f"Unexpected section representation for {field}")
                continue
            for paper in papers_to_check:
                pid = paper["paper_id"]
                grouped = [s for s in values if s.get("paper_id", primary_id) == pid]
                check_sections(pid, grouped)
                if [s["section_id"] for s in grouped] != [s["section_id"] for s in paper["section_notes"]]:
                    errors.append(f"Final {field} lost or reordered sections in {pid}")
    for field in ("knowledge_entries", "knowledge_points"):
        entries = result.get(field)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and "entry_type" in entry:
                check_entry(entry.get("paper_id", primary_id), entry)

    if broken_anchors:
        errors.extend(broken_anchors[:15])
    anchor_rate = round(anchors_valid / anchors_checked, 4) if anchors_checked else 1.0
    num_rate = round(nums_grounded / nums_checked, 4) if nums_checked else 1.0

    # Boundary field existence check
    if result.get("paper_card") and status not in ("refused", "unverifiable") and (
        not schema_for(case_data) or "bibliographic" in result["paper_card"]
    ):
        for req_slot in (
            "operating_conditions_and_preconditions",
            "limitations_and_threats_to_validity",
            "unreported_or_missing_items",
        ):
            if req_slot not in result["paper_card"]:
                errors.append(f"Missing boundary slot '{req_slot}' in paper_card.")

    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "status": status,
            "section_coverage_ratio": sec_cov_ratio,
            "section_order_monotonic": order_monotonic,
            "anchors_checked": anchors_checked,
            "anchors_valid": anchors_valid,
            "anchor_verification_rate": anchor_rate,
            "numeric_checked": nums_checked,
            "numeric_grounding_rate": num_rate,
            "summary_statements_screened": summaries_screened,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit structured notes output against source documents and contract.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result_data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    notes_bundle = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    docs_by_id: Dict[str, Dict[str, Any]] = {}
    for item in manifest.get("papers", []):
        dp = Path(item["document_json"])
        if dp.exists():
            docs_by_id[item["paper_id"]] = json.loads(dp.read_text(encoding="utf-8"))

    report = audit_notes(case_data, result_data, docs_by_id, notes_bundle)
    write_json_atomic(args.out, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
