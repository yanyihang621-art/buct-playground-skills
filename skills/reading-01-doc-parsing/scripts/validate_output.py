#!/usr/bin/env python3
"""Deterministic output auditor: checks contract completeness, provenance, status honesty, and verbatim recall."""

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

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:
    PdfReader = None

PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]
VALID_STATUSES = {"success", "partial", "unverifiable", "refused"}


def _required_keys_from_case(case_data: Dict[str, Any]) -> List[str]:
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


def _valid_bbox(b: Any) -> bool:
    return (
        isinstance(b, list)
        and len(b) == 4
        and all(isinstance(v, (int, float)) for v in b)
        and b[2] >= b[0]
        and b[3] >= b[1]
    )


def _independent_word_recall(pdf_path: Path, doc: Dict[str, Any]) -> Optional[float]:
    if PdfReader is None or not pdf_path.exists():
        return None
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            return None
        digital_pages = {
            p["page"]
            for p in doc.get("diagnostics", {}).get("page_profiles", [])
            if p.get("page_type") == "digital_text"
        }
        if not digital_pages:
            return None

        ref_tokens: Set[str] = set()
        for pnum in digital_pages:
            if pnum - 1 < len(reader.pages):
                raw = reader.pages[pnum - 1].extract_text() or ""
                for tok in re.findall(r"[A-Za-z0-9]{3,}", raw.lower()):
                    ref_tokens.add(tok)

        if len(ref_tokens) < 15:
            return None

        # Gather all extracted text across blocks, headers/footers, tables, formulas
        ext_chunks: List[str] = []
        for b in doc.get("blocks", []):
            ext_chunks.append(b.get("text", ""))
        for hf in doc.get("headers_footers", []):
            ext_chunks.append(hf.get("text", ""))
        for tab in doc.get("tables", []):
            ext_chunks.append(tab.get("caption") or "")
            ext_chunks.append(tab.get("markdown") or "")
            for note in tab.get("notes", []):
                ext_chunks.append(note)

        ext_tokens = set(re.findall(r"[A-Za-z0-9]{3,}", " ".join(ext_chunks).lower()))
        # Also allow dehyphenated merges (e.g., ref had 'hydro' + 'genation', ext has 'hydrogenation')
        joined_ext = " ".join(ext_chunks).lower()
        hits = sum(1 for tok in ref_tokens if tok in ext_tokens or tok in joined_ext)
        return round(hits / len(ref_tokens), 4)
    except Exception:
        return None


def validate_output(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    doc: Dict[str, Any],
    pdf_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate result.json against case contract, provenance, status honesty, and verbatim recall."""
    errors: List[str] = []
    warnings: List[str] = []

    expected_case_id = case_data.get("case_id")
    if expected_case_id and result.get("case_id") != expected_case_id:
        errors.append(f"case_id mismatch: expected '{expected_case_id}', got '{result.get('case_id')}'.")

    req_keys = _required_keys_from_case(case_data)
    for rk in req_keys:
        if rk not in result:
            errors.append(f"Missing required output_contract field '{rk}'.")

    status = result.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"Invalid status '{status}'; must be one of {sorted(VALID_STATUSES)}.")

    diag = doc.get("diagnostics", {})
    page_count = int(diag.get("page_count", 0))
    if (diag.get("encrypted") or diag.get("corrupt")) and status == "success":
        errors.append("Status cannot be 'success' when PDF is encrypted or corrupt.")

    if status == "success" and doc.get("uncertainties"):
        warnings.append("Result status is 'success' even though document uncertainties exist; prefer 'partial'.")

    # Provenance coverage check across blocks, tables, and sources
    prov_total = 0
    prov_valid = 0
    for blk in doc.get("blocks", []):
        prov_total += 1
        p = blk.get("page")
        if isinstance(p, int) and 1 <= p <= max(1, page_count) and _valid_bbox(blk.get("bbox")):
            prov_valid += 1

    for tab in doc.get("tables", []):
        prov_total += 1
        pages = tab.get("pages", [])
        if pages and all(isinstance(p, int) and 1 <= p <= max(1, page_count) for p in pages) and _valid_bbox(tab.get("bbox")):
            prov_valid += 1

    for src in result.get("sources", []) if isinstance(result.get("sources"), list) else []:
        prov_total += 1
        p = src.get("pdf_page")
        if isinstance(p, int) and 1 <= p <= max(1, page_count) and _valid_bbox(src.get("bbox")):
            prov_valid += 1

    prov_ratio = round(prov_valid / prov_total, 4) if prov_total > 0 else 1.0
    if prov_total > 0 and prov_ratio < 0.95:
        errors.append(f"Provenance coverage too low: {prov_valid}/{prov_total} ({prov_ratio:.2%}).")

    # Placeholder check (skipping notice fields)
    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(serialized):
            errors.append(f"Found forbidden placeholder token matching {pat.pattern} in result.json.")

    recall = _independent_word_recall(pdf_path, doc) if pdf_path else None
    if recall is not None and recall < 0.85:
        warnings.append(f"Independent pypdf token recall is {recall:.2%} (< 85%); verify no blocks were dropped.")

    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "provenance_checked": prov_total,
            "provenance_valid": prov_valid,
            "provenance_coverage_ratio": prov_ratio,
            "independent_pypdf_recall": recall,
            "status": status,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate result.json against case contract and provenance rules.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--parsed", required=True)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result_data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    doc_data = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    pdf_p = Path(args.pdf) if args.pdf else None

    report = validate_output(case_data, result_data, doc_data, pdf_path=pdf_p)
    write_json_atomic(args.out, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
