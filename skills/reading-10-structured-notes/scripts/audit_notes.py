#!/usr/bin/env python3
"""Deterministic notes auditor: verifies contract, status honesty, section alignment, anchor existence, quote & numeric grounding."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

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
    contract = case_data.get("output_contract")
    if not contract:
        return ["case_id", "status"]
    if isinstance(contract, list):
        return [str(k) for k in contract]
    if isinstance(contract, dict):
        if "required" in contract and isinstance(contract["required"], list):
            return [str(k) for k in contract["required"]]
        return [str(k) for k in contract.keys()]
    return ["case_id", "status"]


def _flatten_sec_ids(sections: List[Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for s in sections:
        ids.append(s["section_id"])
        ids.extend(_flatten_sec_ids(s.get("children", [])))
    return ids


def _norm_words(s: str) -> List[str]:
    return re.findall(r"[a-z0-9\u4e00-\u9fff]+", s.lower())


def _quote_matches(quote: str, source_text: str) -> bool:
    if not quote or not source_text:
        return False
    nq = " ".join(_norm_words(quote))
    ns = " ".join(_norm_words(source_text))
    if not nq:
        return True
    if nq in ns:
        return True
    q_words = set(nq.split())
    s_words = set(ns.split())
    overlap = len(q_words & s_words) / max(1, len(q_words))
    return overlap >= 0.70


def audit_notes(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    docs_by_id: Dict[str, Dict[str, Any]],
    notes_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """Audit structured notes result against source document(s) and contract."""
    errors: List[str] = []
    warnings: List[str] = []

    expected_cid = case_data.get("case_id")
    if expected_cid and result.get("case_id") != expected_cid:
        errors.append(f"case_id mismatch: expected '{expected_cid}', got '{result.get('case_id')}'.")

    for rk in _required_keys(case_data):
        if rk not in result:
            errors.append(f"Missing required output_contract key '{rk}'.")

    status = result.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"Invalid status '{status}'.")

    any_encrypted_or_corrupt = any(
        d.get("diagnostics", {}).get("encrypted") or d.get("diagnostics", {}).get("corrupt")
        for d in docs_by_id.values()
    )
    if any_encrypted_or_corrupt and status == "success":
        errors.append("Status cannot be 'success' when source PDF is encrypted or corrupt.")

    # Placeholder check
    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(serialized):
            errors.append(f"Found forbidden placeholder token matching {pat.pattern}.")

    # Build source element lookup per paper
    elem_lookup: Dict[str, Dict[str, Dict[str, Any]]] = {}
    full_text_by_paper: Dict[str, str] = {}
    for pid, doc in docs_by_id.items():
        emap: Dict[str, Dict[str, Any]] = {}
        chunks: List[str] = []
        for b in doc.get("blocks", []):
            emap[b["block_id"]] = {"page": b["page"], "text": b.get("structured_text") or b.get("text") or ""}
            chunks.append(emap[b["block_id"]]["text"])
        for t in doc.get("tables", []):
            emap[t["table_id"]] = {
                "pages": t.get("pages", []),
                "text": (t.get("caption") or "") + " " + (t.get("markdown") or ""),
            }
            chunks.append(emap[t["table_id"]]["text"])
        for f in doc.get("figures", []):
            emap[f["figure_id"]] = {"page": f["page"], "text": f.get("caption") or ""}
        for eq in doc.get("formulas", {}).get("display_equations", []):
            emap[eq["eq_id"]] = {"page": eq["page"], "text": eq.get("structured_text") or ""}
        elem_lookup[pid] = emap
        full_text_by_paper[pid] = " ".join(chunks)

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
        emap = elem_lookup.get(pid, {})
        eid = anc.get("block_id") or anc.get("element_id") or anc.get("table_id") or anc.get("eq_id") or anc.get("figure_id")
        if not eid or eid not in emap:
            broken_anchors.append(f"{ctx}: missing element_id '{eid}' in {pid}")
            return
        src = emap[eid]
        p = anc.get("pdf_page")
        if "pages" in src:
            if p is not None and p not in src["pages"]:
                broken_anchors.append(f"{ctx}: page mismatch {p} not in {src['pages']} for {eid}")
                return
        elif p is not None and src.get("page") != p:
            broken_anchors.append(f"{ctx}: page mismatch {p} != {src.get('page')} for {eid}")
            return
        snip = anc.get("quote_snippet")
        if snip and not _quote_matches(snip, src["text"]):
            broken_anchors.append(f"{ctx}: quote_snippet not grounded in {eid}")
            return
        anchors_valid += 1

    # Audit anchors inside result (if single-paper or multi-paper)
    papers_to_check = notes_bundle.get("papers", [])
    for pn in papers_to_check:
        pid = pn["paper_id"]
        pc = pn.get("paper_card", {})
        for slot_name in (
            "research_question",
            "data_and_materials",
            "methods_and_setup",
            "operating_conditions_and_preconditions",
            "key_findings_and_metrics",
            "limitations_and_threats_to_validity",
        ):
            slot = pc.get(slot_name, {})
            for a in slot.get("anchors", []):
                _verify_anchor(pid, a, f"{pid}.paper_card.{slot_name}")
        for sn in pn.get("section_notes", []):
            for a in sn.get("anchors", []):
                _verify_anchor(pid, a, f"{pid}.section.{sn['section_id']}")
            for ce in sn.get("cue_evidence", []):
                _verify_anchor(pid, ce, f"{pid}.section.{sn['section_id']}.cue")
        for ke in pn.get("knowledge_entries", []):
            for a in ke.get("anchors", []):
                _verify_anchor(pid, a, f"{pid}.knowledge.{ke['entry_id']}")
        for g in pn.get("terminology_glossary", []):
            if g.get("definition_anchor"):
                _verify_anchor(pid, g["definition_anchor"], f"{pid}.glossary.{g['acronym']}")

    # Also check any top-level overridden paper_card / section_notes / knowledge_entries in result.json
    if result.get("paper_card"):
        default_pid = "paper_1"
        for slot_name in (
            "research_question",
            "data_and_materials",
            "methods_and_setup",
            "operating_conditions_and_preconditions",
            "key_findings_and_metrics",
            "limitations_and_threats_to_validity",
        ):
            slot = result["paper_card"].get(slot_name, {})
            if isinstance(slot, dict):
                for a in slot.get("anchors", []):
                    _verify_anchor(a.get("paper_id", default_pid), a, f"result.paper_card.{slot_name}")

    if broken_anchors:
        errors.extend(broken_anchors[:15])

    anchor_rate = round(anchors_valid / anchors_checked, 4) if anchors_checked > 0 else 1.0

    # Numeric grounding check on key_findings_and_metrics
    nums_checked = 0
    nums_grounded = 0
    for pn in papers_to_check:
        pid = pn["paper_id"]
        src_full = full_text_by_paper.get(pid, "")
        kf = pn.get("paper_card", {}).get("key_findings_and_metrics", {})
        for item_txt in kf.get("items", []):
            for num_tok in SALIENT_NUM_RE.findall(str(item_txt)):
                nums_checked += 1
                num_core = re.sub(r"\s+", "", num_tok)
                if num_tok in src_full or num_core in re.sub(r"\s+", "", src_full):
                    nums_grounded += 1
                else:
                    warnings.append(f"Number '{num_tok}' in {pid} key_findings not verbatim in source text.")
    num_rate = round(nums_grounded / nums_checked, 4) if nums_checked > 0 else 1.0

    # Boundary field existence check
    if result.get("paper_card") and status not in ("refused", "unverifiable"):
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
