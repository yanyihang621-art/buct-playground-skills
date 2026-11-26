#!/usr/bin/env python3
"""6-Gate Nanopublication quality auditor: granularity, controlled vocab, contradiction, grounding, provenance, N-Quads."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from decompose_assertions import _match_clause_to_triple, assertion_scope, extract_source_metadata, load_relation_vocabulary
from output_contract import contract_issues
from pdf_utils import write_json_atomic
from serialize_nanopubs import serialize_to_jsonld, serialize_to_markdown, serialize_to_trig_and_nquads, verify_nquads_syntax

VALID_STATUSES = {"success", "partial", "unverifiable", "refused"}
PLACEHOLDER_PATS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
]
MULTI_SENT_RE = re.compile(r"[.!?。！？]\s+[A-Z\u4e00-\u9fff]")
UNSPLIT_COMPOUND_RE = re.compile(r"(?:;\s*(?:moreover|furthermore)|,\s+whereas\s+.{15,}\s+and\s+also)", re.IGNORECASE)
NEG_SNIPPET_RE = re.compile(r"\b(?:does\s+not|did\s+not|no\s+significant\s+effect|inhibits?|decreases?|reduces?|suppresses?|fails?)\b", re.IGNORECASE)
POS_SNIPPET_RE = re.compile(r"\b(?:increases?|enhances?|catalyzes?|improves?|promotes?|boosts?)\b", re.IGNORECASE)


def _norm_tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9\u4e00-\u9fff]+", s.lower())


def validate_nanopubs(
    case_data: Dict[str, Any],
    result: Dict[str, Any],
    bundle: Dict[str, Any],
    docs: List[Dict[str, Any]],
    nquads_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all 6 deterministic quality gates on Nanopublication deliverables."""
    vocab = load_relation_vocabulary()
    errors: List[str] = []
    warnings: List[str] = []
    gate_failures: Dict[str, List[str]] = {
        "gate1_over_broad": [],
        "gate2_relation_or_contradiction": [],
        "gate3_grounding_and_entity": [],
        "gate4_provenance_metadata": [],
        "gate5_cross_assertion_conflicts": [],
        "gate6_nquads_syntax": [],
    }

    expected_cid = case_data.get("case_id")
    if expected_cid and result.get("case_id") != expected_cid:
        errors.append(f"case_id mismatch: expected '{expected_cid}', got '{result.get('case_id')}'.")

    errors.extend(issue["message"] for issue in contract_issues(case_data.get("output_contract"), result))

    status = result.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"Invalid status '{status}'.")

    any_enc_corrupt = any(
        d.get("diagnostics", {}).get("encrypted") or d.get("diagnostics", {}).get("corrupt") for d in docs
    )
    if any_enc_corrupt and status == "success":
        errors.append("Status cannot be 'success' when source PDF is encrypted or corrupt.")

    serialized = json.dumps(result, ensure_ascii=False)
    for pat in PLACEHOLDER_PATS:
        if pat.search(serialized):
            errors.append(f"Placeholder token matching {pat.pattern} found in result.")

    # Build source element lookup across all docs
    elem_map = {}
    for doc in docs:
        sha = extract_source_metadata(doc)["source_sha256"]
        for b in doc.get("blocks", []):
            elem_map[sha, b["block_id"]] = {"bbox": b.get("bbox"), "page": b.get("page"),
                                           "native": b.get("native_locator"),
                                           "text": b.get("structured_text") or b.get("text") or ""}
        for t in doc.get("tables", []):
            elem_map[sha, t["table_id"]] = {
                "bbox": t["bbox"], "pages": t.get("pages", []), "table": t,
                "text": f"{t.get('caption') or ''} {t.get('label') or ''} {t.get('markdown') or ''}",
            }

    nps = bundle.get("nanopublications", [])
    if result.get("nanopublications") != nps or result.get("nanopub_count") != len(nps):
        errors.append("Result claims/count differ from the reviewed bundle.")
    if bundle.get("nanopub_count") != len(nps):
        errors.append("Bundle count differs from its records.")
    if "assertions" in result:
        summaries = result["assertions"]
        if not isinstance(summaries, list) or len(summaries) != len(nps) or any(
            not isinstance(summary, dict) or any(summary.get(k) != v for k, v in record["assertion"].items())
            for summary, record in zip(summaries, nps)
        ):
            errors.append("Contract assertion summaries differ from the reviewed bundle.")
    if status not in ("refused", "unverifiable"):
        smeta = bundle.get("source_metadata", {})
        for req_m in ("paper_title", "source_file", "source_sha256", "authors", "year", "doi"):
            if req_m not in smeta or smeta[req_m] in (None, "", []):
                gate_failures["gate4_provenance_metadata"].append(f"missing_provenance_metadata: source_metadata.{req_m}")

    grounded_count = 0
    for np_obj in nps:
        nid = np_obj.get("nanopub_id", "unknown")
        # Gate 4 structure checks
        for gkey in ("nanopub_uri", "head", "assertion", "provenance", "pubinfo"):
            if gkey not in np_obj or not np_obj[gkey]:
                gate_failures["gate4_provenance_metadata"].append(f"{nid}: missing_provenance_metadata '{gkey}'")
        pi = np_obj.get("pubinfo", {})
        if not pi.get("confidence_basis"):
            gate_failures["gate4_provenance_metadata"].append(f"{nid}: missing confidence_basis")

        a = np_obj.get("assertion", {})
        p = np_obj.get("provenance", {})
        stmt = str(a.get("atomic_statement", ""))

        # Gate 1: over_broad_assertion
        if len(stmt) > 280 or MULTI_SENT_RE.search(stmt) or UNSPLIT_COMPOUND_RE.search(stmt):
            gate_failures["gate1_over_broad"].append(f"{nid}: over_broad_assertion (len={len(stmt)})")

        # Gate 2: controlled vocabulary & polarity contradiction
        pred = a.get("predicate", "")
        if pred not in vocab:
            gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: invalid_relation_type '{pred}'")
        else:
            pol = vocab[pred]["polarity"]
            snip = str(p.get("minimal_evidence_snippet", ""))
            # Only check pure single-clause snippet polarity (skip multi-clause source snippets if clause matches)
            if a.get("predicate_uri") != vocab[pred]["uri"]:
                gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: predicate URI disagrees with vocabulary")
            if p.get("location", {}).get("block_id"):
                modality, polarity = assertion_scope(snip)
                if ((a.get("modality", "asserted"), a.get("polarity", "affirmative")) != (modality, polarity)
                        or assertion_scope(stmt) != assertion_scope(" ".join([p.get("condition_quote", ""), snip]))):
                    gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: changed modality or grammatical polarity")
                expected = _match_clause_to_triple(snip, vocab)
                if expected and (expected[1] != pred or a.get("subject", "").casefold() == expected[2].casefold()):
                    gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: evidence predicate or direction mismatch")
                # Digits can be grounded in a clause or its separate shared condition;
                # a copied quote cannot justify inserting a new quantitative result.
                numbers = lambda s: set(re.findall(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s))
                if (numbers(str(a.get("object", ""))) | numbers(stmt)) - numbers(snip + " " + str(p.get("condition_quote", ""))):
                    gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: numerical value absent from quoted evidence")
                if not all(str(cond).casefold() in stmt.casefold() for cond in a.get("conditions", [])):
                    gate_failures["gate2_relation_or_contradiction"].append(f"{nid}: statement omits a condition")
            if len(snip) <= 140 and a.get("polarity") != "negative":
                if pol == "positive" and NEG_SNIPPET_RE.search(snip) and not POS_SNIPPET_RE.search(snip):
                    gate_failures["gate2_relation_or_contradiction"].append(
                        f"{nid}: assertion_evidence_contradiction (positive '{pred}' vs negative evidence)"
                    )
                elif pol == "negative" and POS_SNIPPET_RE.search(snip) and not NEG_SNIPPET_RE.search(snip):
                    gate_failures["gate2_relation_or_contradiction"].append(
                        f"{nid}: assertion_evidence_contradiction (negative '{pred}' vs positive evidence)"
                    )

        # Gate 3: evidence grounding & entity confusion
        loc = p.get("location", {})
        eid = loc.get("block_id") or loc.get("table_id")
        snip = str(p.get("minimal_evidence_snippet", ""))
        key = (p.get("source_metadata", {}).get("source_sha256"), eid)
        if not eid or key not in elem_map:
            gate_failures["gate3_grounding_and_entity"].append(f"{nid}: evidence_not_grounded (missing '{eid}')")
        else:
            src = elem_map[key]
            pg = loc.get("pdf_page")
            page_ok = (pg in src["pages"]) if "pages" in src else (pg == src["page"])
            if loc.get("location_type") == "text_lines":
                rect = src["bbox"]
                page_ok = pg is None and (loc.get("line_start"), loc.get("line_end")) == (int(rect[1]), int(rect[3]))
            if src.get("native"):
                page_ok = page_ok and all(loc.get(k) == v for k, v in src["native"].items())
            if "table" not in src and loc.get("bbox") is not None and loc["bbox"] != src.get("bbox"):
                page_ok = False
            if "table" in src and "row_index" in loc:
                table = src["table"]
                columns = table.get("col_paths", [])
                ri, col = loc["row_index"], loc.get("column_name")
                if isinstance(ri, int) and col in columns:
                    cell = next((c for c in table.get("cells", []) if c["row"] == ri - 1 + table.get("header_row_count", 1)
                                 and c["col"] == columns.index(col)), {})
                    if cell.get("native_locator"):
                        page_ok = pg is None and all(loc.get(k) == v for k, v in cell["native_locator"].items())
                    elif cell.get("bbox"):
                        page_ok = pg == cell.get("page") and loc.get("bbox") == cell["bbox"]
            if not page_ok:
                gate_failures["gate3_grounding_and_entity"].append(f"{nid}: evidence_not_grounded (page mismatch {pg})")
            else:
                def norm(text: str) -> str:
                    return " ".join(text.split()).casefold()
                condition = p.get("condition_quote", "")
                if "table" in src:
                    table = src["table"]
                    columns = table.get("col_paths") or []
                    rows = table.get("records", [])
                    ri, col = loc.get("row_index"), loc.get("column_name")
                    cell_match = (columns and isinstance(ri, int) and 1 <= ri <= len(rows) and col in columns[1:]
                                  and str(rows[ri - 1].get(columns[0], "")) == a.get("subject") == loc.get("row_label")
                                  and f"{col} = {rows[ri - 1].get(col)}" == a.get("object")
                                  and str(rows[ri - 1].get(col)) == snip)
                    if ri is None and col is None and columns:
                        # Earlier full records quoted a formatted caption/row/column tuple.
                        caption = table.get("caption") or table.get("label") or table["table_id"]
                        cell_match = any(
                            str(row.get(columns[0])) == a.get("subject") and
                            f"{column} = {row.get(column)}" == a.get("object") and
                            snip == f"{caption} | {columns[0]}={a['subject']}, {column}={row.get(column)}"[:260]
                            for row in rows for column in columns[1:])
                    if not cell_match:
                        gate_failures["gate3_grounding_and_entity"].append(f"{nid}: table record/column mismatch")
                    else:
                        grounded_count += 1
                elif not snip.strip() or norm(snip) not in norm(src["text"]) or norm(condition) not in norm(src["text"]):
                    gate_failures["gate3_grounding_and_entity"].append(f"{nid}: source quote or condition mismatch")
                elif len(snip) > 650:
                    gate_failures["gate3_grounding_and_entity"].append(f"{nid}: evidence_too_bloated (len={len(snip)})")
                else:
                    grounded_count += 1

        # Entity confusion check: core tokens of subject must appear in evidence snippet
        subj_toks = [t for t in _norm_tokens(str(a.get("subject", ""))) if len(t) >= 2]
        snip_toks = set(_norm_tokens(snip))
        if not loc.get("table_id") and subj_toks and not any(t in snip_toks for t in subj_toks):
            gate_failures["gate3_grounding_and_entity"].append(
                f"{nid}: entity_confusion (subject '{a.get('subject')}' absent from evidence snippet)"
            )

    # Gate 5: cross-assertion conflicts (report as warnings, not pipeline crash)
    for cf in bundle.get("conflicts", []):
        gate_failures["gate5_cross_assertion_conflicts"].append(cf.get("message", ""))
        warnings.append(f"cross_assertion_conflict: {cf.get('message')}")

    # Gate 6: N-Quads syntax check
    nq_res = None
    if nquads_text is not None or any(key in result for key in ("trig", "jsonld", "nquads", "markdown")):
        expected_trig, expected_nq = serialize_to_trig_and_nquads(bundle)
        if nquads_text is not None and set(nquads_text.splitlines()) != set(expected_nq.splitlines()):
            gate_failures["gate6_nquads_syntax"].append("N-Quads differ from the reviewed claims.")
        for key, expected in (("trig", expected_trig), ("jsonld", serialize_to_jsonld(bundle)),
                              ("nquads", expected_nq), ("markdown", serialize_to_markdown(bundle))):
            if key in result and result[key] != expected:
                gate_failures["gate6_nquads_syntax"].append(f"Embedded {key} differs from the reviewed claims.")
    if nquads_text is not None:
        nq_res = verify_nquads_syntax(nquads_text)
        if not nq_res["valid"]:
            gate_failures["gate6_nquads_syntax"].extend(nq_res["errors"])

    for gname in ("gate1_over_broad", "gate2_relation_or_contradiction", "gate3_grounding_and_entity", "gate4_provenance_metadata", "gate6_nquads_syntax"):
        errors.extend(gate_failures[gname])

    anchor_rate = round(grounded_count / len(nps), 4) if nps else 1.0
    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "gate_results": {k: len(v) == 0 for k, v in gate_failures.items()},
        "metrics": {
            "status": status,
            "nanopub_count": len(nps),
            "anchor_grounding_rate": anchor_rate,
            "deduplication_merges": len(bundle.get("deduplication_log", [])),
            "cross_assertion_conflicts": len(bundle.get("conflicts", [])),
            "nquads_valid": nq_res["valid"] if nq_res else None,
            "nquads_quad_count": nq_res["quad_count"] if nq_res else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 6-gate Nanopublication quality validator.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--nanopubs", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--nquads", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    case_data = json.loads(Path(args.case).read_text(encoding="utf-8"))
    result_data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    bundle = json.loads(Path(args.nanopubs).read_text(encoding="utf-8"))
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    docs: List[Dict[str, Any]] = []
    for item in manifest.get("papers", []):
        dp = Path(item["document_json"])
        if dp.exists():
            docs.append(json.loads(dp.read_text(encoding="utf-8")))

    nq_txt = Path(args.nquads).read_text(encoding="utf-8") if args.nquads and Path(args.nquads).exists() else None
    rep = validate_nanopubs(case_data, result_data, bundle, docs, nquads_text=nq_txt)
    write_json_atomic(args.out, rep)
    print(json.dumps(rep, ensure_ascii=False))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
