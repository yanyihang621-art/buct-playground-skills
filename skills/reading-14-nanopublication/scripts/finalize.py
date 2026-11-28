"""Verify agent-authored atomic claims, serialize 4-graph RDF (TriG/N-Quads/JSON-LD), and write result.json."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import (
    IndexStore,
    check_numbers,
    format_anchor,
    has_hedge,
    has_negation,
    normalize_text,
    scan_fields_evidence,
    verify,
)
from output_contract import (
    build_skeleton,
    contract_issues,
    contract_specs,
    is_closed_contract,
    pick_status_value,
)
from pdf_utils import write_json_atomic, write_text_lf

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
_XSD_NS = "http://www.w3.org/2001/XMLSchema#"
_DCT_NS = "http://purl.org/dc/terms/"
_PROV_NS = "http://www.w3.org/ns/prov#"
_NP_NS = "http://www.nanopub.org/nschema#"
_NPV_NS = "https://w3id.org/np/vocab/"

_COMPOUND_RE = re.compile(r"(?:;|；|\b(?:while|whereas)\b|以及|,\s*and\b)", re.I)
_UNIT_VAL_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(%|mg/kg|mg|ug|g|kg|mV|kV|V|mA|A|K|MPa|kPa|Pa|atm|bar|nm|um|mm|cm|m|ms|h|s|Hz|kHz|MHz|vol%|wt%|M)\b",
    re.I,
)
_NQ_LINE_RE = re.compile(
    r'^(?:<[^>\s]+>|_:[A-Za-z0-9_]+)\s+<[^>\s]+>\s+(?:<[^>\s]+>|_:[A-Za-z0-9_]+|"(?:[^"\\]|\\.)*"(?:\^\^<[^>\s]+>|@[a-zA-Z-]+)?)\s+<[^>\s]+>\s*\.$'
)


def _load_predicates() -> Dict[str, str]:
    path = SKILL_DIR / "schemas" / "predicates.yaml"
    preds: Dict[str, str] = {}
    if not path.is_file():
        return preds
    cur_id = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        m_id = re.match(r"^\s*-\s*id:\s*([A-Za-z0-9_]+)\s*$", line)
        if m_id:
            cur_id = m_id.group(1)
            continue
        m_uri = re.match(r'^\s*uri:\s*"([^"]+)"\s*$', line)
        if m_uri and cur_id:
            preds[cur_id] = m_uri.group(1)
            cur_id = ""
    return preds


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_")
    if not s:
        s = "item"
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]
    return f"{s[:32]}_{digest}"


def _esc_nq(val: str) -> str:
    return (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _overlay(dst: Any, src: Any) -> Any:
    if isinstance(dst, dict) and isinstance(src, dict):
        out = dict(dst)
        for k, v in src.items():
            out[k] = _overlay(out[k], v) if k in out and isinstance(out[k], dict) and isinstance(v, dict) else copy.deepcopy(v)
        return out
    return copy.deepcopy(src)


def _load_claims(work_dir: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    files = sorted(work_dir.glob("claims*.json"))
    if not files:
        return None, []
    merged: Dict[str, Any] = {"paper": {}, "claims": []}
    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8-sig"))
        if isinstance(data.get("paper"), dict):
            for k, v in data["paper"].items():
                if v and not merged["paper"].get(k):
                    merged["paper"][k] = v
        if isinstance(data.get("claims"), list):
            merged["claims"].extend(data["claims"])
    return merged, [str(f) for f in files]


def _load_answer(work_dir: Path) -> Optional[Dict[str, Any]]:
    path = work_dir / "answer.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _claim_tokens(claim: Dict[str, Any]) -> set[str]:
    blob = " ".join([
        str(claim.get("subject") or ""),
        str(claim.get("object") or ""),
        str(claim.get("statement") or ""),
        json.dumps(claim.get("qualifiers") or {}, ensure_ascii=False, sort_keys=True),
    ])
    return set(normalize_text(blob).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _build_quads_and_jsonld(
    nanopubs: List[Dict[str, Any]],
    pred_uris: Dict[str, str],
    created_iso: str,
) -> Tuple[List[Tuple[str, Tuple[str, str, Optional[str], Optional[str]], str]], str, str, str]:
    """Return (quads, trig_str, nq_str, jsonld_str)."""
    # Each quad: (subj_iri, (obj_kind='iri'|'lit', obj_val, datatype_iri, lang), pred_iri, graph_iri)
    quads: List[Tuple[str, str, Tuple[str, str, Optional[str]], str]] = []
    jsonld_graphs: List[Dict[str, Any]] = []

    def add_iri(g: str, s: str, p: str, o: str) -> None:
        quads.append((s, p, ("iri", o, None), g))

    def add_lit(g: str, s: str, p: str, val: str, dt: Optional[str] = None) -> None:
        quads.append((s, p, ("lit", val, dt), g))

    for npub in nanopubs:
        base = npub["iri"]
        g_head = f"{base}/head"
        g_assert = f"{base}/assertion"
        g_prov = f"{base}/provenance"
        g_pub = f"{base}/pubinfo"

        # Head graph
        add_iri(g_head, base, f"{_RDF_NS}type", f"{_NP_NS}Nanopublication")
        add_iri(g_head, base, f"{_NP_NS}hasAssertion", g_assert)
        add_iri(g_head, base, f"{_NP_NS}hasProvenance", g_prov)
        add_iri(g_head, base, f"{_NP_NS}hasPublicationInfo", g_pub)

        # Assertion graph
        stmt_iri = f"{base}/statement"
        subj_iri = f"https://w3id.org/np/local/entity/{_slug(npub['subject'])}"
        obj_iri = f"https://w3id.org/np/local/entity/{_slug(npub['object'])}"
        pred_iri = pred_uris.get(npub["predicate"], f"{_NPV_NS}{npub['predicate']}")

        add_iri(g_assert, stmt_iri, f"{_RDF_NS}type", f"{_RDF_NS}Statement")
        add_iri(g_assert, stmt_iri, f"{_RDF_NS}subject", subj_iri)
        add_iri(g_assert, stmt_iri, f"{_RDF_NS}predicate", pred_iri)
        add_iri(g_assert, stmt_iri, f"{_RDF_NS}object", obj_iri)
        add_lit(g_assert, stmt_iri, f"{_RDFS_NS}label", npub["statement"])
        add_lit(g_assert, stmt_iri, f"{_NPV_NS}polarity", npub["polarity"])
        add_lit(g_assert, stmt_iri, f"{_NPV_NS}modality", npub["modality"])
        add_lit(g_assert, subj_iri, f"{_RDFS_NS}label", npub["subject"])
        add_lit(g_assert, obj_iri, f"{_RDFS_NS}label", npub["object"])

        for qk, qv in (npub.get("qualifiers") or {}).items():
            if qv is not None and str(qv).strip():
                add_lit(g_assert, stmt_iri, f"{_NPV_NS}qualifier_{re.sub(r'[^A-Za-z0-9_]', '_', qk)}", str(qv))

        if npub["polarity"] == "affirmative" and npub["modality"] == "asserted":
            add_iri(g_assert, subj_iri, pred_iri, obj_iri)

        # Provenance graph
        src_meta = npub["source_metadata"]
        doi = src_meta.get("doi")
        src_iri = f"https://doi.org/{doi}" if doi and doi != "not_reported" else f"urn:sha256:{src_meta.get('source_sha256') or 'unknown'}"
        add_lit(g_prov, g_assert, f"{_NPV_NS}confidence", str(npub.get("confidence", 0.0)), f"{_XSD_NS}decimal")
        if npub.get("confidence_basis"):
            add_lit(g_prov, g_assert, f"{_NPV_NS}confidenceBasis", npub["confidence_basis"])

        for idx, ev in enumerate(npub.get("resolved_evidence") or [], 1):
            ev_iri = f"{base}/ev_{idx}"
            add_iri(g_prov, g_assert, f"{_PROV_NS}wasDerivedFrom", ev_iri)
            add_lit(g_prov, ev_iri, f"{_PROV_NS}value", ev.get("quote", ""))
            add_lit(g_prov, ev_iri, f"{_NPV_NS}anchor", ev.get("anchor", ""))
            if isinstance(ev.get("page"), int):
                add_lit(g_prov, ev_iri, f"{_NPV_NS}page", str(ev["page"]), f"{_XSD_NS}integer")
            add_lit(g_prov, ev_iri, f"{_NPV_NS}evidenceType", npub.get("evidence_type", "author_statement"))
            add_iri(g_prov, ev_iri, f"{_PROV_NS}wasQuotedFrom", src_iri)

        # PubInfo graph
        add_lit(g_pub, base, f"{_DCT_NS}created", created_iso, f"{_XSD_NS}dateTime")
        add_lit(g_pub, base, f"{_DCT_NS}creator", "reading-14-nanopublication")
        add_iri(g_pub, base, f"{_DCT_NS}license", "https://creativecommons.org/licenses/by/4.0/")
        add_lit(g_pub, src_iri, f"{_DCT_NS}title", str(src_meta.get("title") or "not_reported"))
        for author in src_meta.get("authors") or []:
            add_lit(g_pub, src_iri, f"{_DCT_NS}creator", str(author))
        if src_meta.get("year") and src_meta["year"] != "not_reported":
            add_lit(g_pub, src_iri, f"{_DCT_NS}date", str(src_meta["year"]))
        ident = doi if doi and doi != "not_reported" else src_meta.get("arxiv_id")
        if ident and ident != "not_reported":
            add_lit(g_pub, src_iri, f"{_DCT_NS}identifier", str(ident))

    # Build N-Quads
    nq_lines: List[str] = []
    by_graph: Dict[str, List[Tuple[str, str, Tuple[str, str, Optional[str]]]]] = {}
    for s, p, o, g in quads:
        kind, val, dt = o
        o_str = f"<{val}>" if kind == "iri" else (f'"{_esc_nq(val)}"^^<{dt}>' if dt else f'"{_esc_nq(val)}"')
        nq_lines.append(f"<{s}> <{p}> {o_str} <{g}> .")
        by_graph.setdefault(g, []).append((s, p, o))

    # Build TriG
    trig_lines: List[str] = []
    for g, triples in by_graph.items():
        trig_lines.append(f"<{g}> {{")
        for s, p, (kind, val, dt) in triples:
            o_str = f"<{val}>" if kind == "iri" else (f'"{_esc_nq(val)}"^^<{dt}>' if dt else f'"{_esc_nq(val)}"')
            trig_lines.append(f"  <{s}> <{p}> {o_str} .")
        trig_lines.append("}")

    # Build Expanded JSON-LD
    for g, triples in by_graph.items():
        nodes: Dict[str, Dict[str, Any]] = {}
        for s, p, (kind, val, dt) in triples:
            node = nodes.setdefault(s, {"@id": s})
            if kind == "iri":
                if p == f"{_RDF_NS}type":
                    node.setdefault("@type", []).append(val)
                    continue
                obj_val: Any = {"@id": val}
            else:
                obj_val = {"@value": val, "@type": dt} if dt else {"@value": val}
            node.setdefault(p, []).append(obj_val)
        jsonld_graphs.append({"@id": g, "@graph": list(nodes.values())})

    return quads, "\n".join(trig_lines) + ("\n" if trig_lines else ""), "\n".join(nq_lines) + ("\n" if nq_lines else ""), json.dumps(jsonld_graphs, ensure_ascii=False, indent=2) + "\n"


def _validate_rdf_exports(nq_path: Path, trig_path: Path, jsonld_path: Path) -> Dict[str, Any]:
    nq_text = nq_path.read_text(encoding="utf-8")
    nq_lines = [ln for ln in nq_text.splitlines() if ln.strip()]
    bad_nq = [ln for ln in nq_lines if not _NQ_LINE_RE.match(ln)]
    json.loads(jsonld_path.read_text(encoding="utf-8"))

    try:
        import rdflib
    except ImportError:
        return {
            "mode": "rdflib_unavailable",
            "nq_lines": len(nq_lines),
            "nq_regex_errors": len(bad_nq),
            "consistent": len(bad_nq) == 0,
        }

    counts = {}
    for label, path, fmt in (("nquads", nq_path, "nquads"), ("trig", trig_path, "trig"), ("jsonld", jsonld_path, "json-ld")):
        ds = rdflib.Dataset()
        ds.parse(str(path), format=fmt)
        counts[label] = len(list(ds.quads((None, None, None, None))))
    consistent = len(set(counts.values())) == 1 and len(bad_nq) == 0
    return {
        "mode": "rdflib_parsed",
        "counts": counts,
        "nq_regex_errors": len(bad_nq),
        "consistent": consistent,
    }


def _project_claim_to_item(npub: Dict[str, Any], item_spec: Any) -> Dict[str, Any]:
    if not isinstance(item_spec, dict) or not isinstance(item_spec.get("properties"), dict):
        return copy.deepcopy(npub)
    props = item_spec["properties"]
    ev0 = (npub.get("resolved_evidence") or [{}])[0]
    syn = {
        "id": npub.get("id"),
        "iri": npub.get("iri"),
        "paper_id": npub.get("paper_id"),
        "section": npub.get("section"),
        "statement": npub.get("statement"),
        "assertion": npub.get("statement"),
        "claim": npub.get("statement"),
        "claim_text": npub.get("statement"),
        "atomic_statement": npub.get("statement"),
        "text": npub.get("statement"),
        "subject": npub.get("subject"),
        "predicate": npub.get("predicate"),
        "relation": npub.get("predicate"),
        "object": npub.get("object"),
        "qualifiers": npub.get("qualifiers"),
        "conditions": [v for v in (npub.get("qualifiers") or {}).values() if v],
        "polarity": npub.get("polarity"),
        "modality": npub.get("modality"),
        "evidence_type": npub.get("evidence_type"),
        "confidence": npub.get("confidence"),
        "confidence_basis": npub.get("confidence_basis"),
        "quote": ev0.get("quote", ""),
        "snippet": ev0.get("quote", ""),
        "quote_snippet": ev0.get("quote", ""),
        "evidence_quote": ev0.get("quote", ""),
        "anchor": ev0.get("anchor", ""),
        "block_id": ev0.get("anchor", ""),
        "element_id": ev0.get("anchor", ""),
        "page": ev0.get("page"),
        "pdf_page": ev0.get("page"),
        "location": f"p.{ev0.get('page', '?')} {ev0.get('anchor', '')}".strip(),
        "evidence": npub.get("resolved_evidence") if props.get("evidence", {}).get("type") == "array" else ev0.get("quote", ""),
        "source": npub.get("source_metadata"),
        "provenance": npub.get("source_metadata"),
        "source_metadata": npub.get("source_metadata"),
    }
    projected: Dict[str, Any] = {}
    for pname, pspec in props.items():
        if pname in syn and syn[pname] is not None:
            val = syn[pname]
            ptype = pspec.get("type") if isinstance(pspec, dict) else None
            if ptype == "string" and not isinstance(val, str):
                val = json.dumps(val, ensure_ascii=False)
            projected[pname] = val
    return projected


def _render_nanopubs_md(
    case_id: str,
    nanopubs: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    dedup_log: List[Dict[str, Any]],
    rdf_check: Dict[str, Any],
) -> str:
    lines = [
        f"# Nanopublications — {case_id}",
        "",
        "| ID | Statement | Polarity / Modality | Predicate | Evidence Quote | Anchor | Conf |",
        "|---|---|---|---|---|---|---|",
    ]
    for npub in nanopubs:
        ev0 = (npub.get("resolved_evidence") or [{}])[0]
        p = ev0.get("page")
        anc_disp = f"p.{p} {ev0.get('anchor', '')}" if p is not None else ev0.get("anchor", "")
        q = (ev0.get("quote") or "").replace("\n", " ").replace("|", "/")
        st = (npub.get("statement") or "").replace("\n", " ").replace("|", "/")
        lines.append(
            f"| {npub['id']} | {st} | {npub['polarity']} / {npub['modality']} | "
            f"`{npub['predicate']}` | {q} | {anc_disp} | {npub.get('confidence', '')} |"
        )
    lines.extend([
        "",
        "## Summary",
        f"- Nanopublications: {len(nanopubs)}",
        f"- Deduplicated merges: {len(dedup_log)}",
        f"- Cross-assertion conflicts: {len(conflicts)}",
        f"- RDF validation: `{json.dumps(rdf_check, ensure_ascii=False)}`",
    ])
    return "\n".join(lines) + "\n"


def run_finalize(case_path: Path, work_dir: Path, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = work_dir / "meta.json"
    index_path = work_dir / "index.json"
    if not meta_path.is_file() or not index_path.is_file():
        print("MUST_FIX: meta.json or index.json missing. Run prepare.py first.")
        return 2

    case_data: Dict[str, Any] = json.loads(case_path.read_text(encoding="utf-8-sig")) if case_path.is_file() else {}
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    index_entries = json.loads(index_path.read_text(encoding="utf-8-sig"))
    store = IndexStore(index_entries)

    case_id = str(case_data.get("case_id") or meta.get("case_id") or "unknown_case")
    contract = case_data.get("output_contract")
    closed = is_closed_contract(contract)
    papers_meta = meta.get("papers") or []
    papers_by_id = {p["paper_id"]: p for p in papers_meta}
    default_paper = papers_meta[0]["paper_id"] if papers_meta else "paper_1"
    multi_paper = len(papers_meta) > 1

    claims_bundle, claim_files = _load_claims(work_dir)
    answer = _load_answer(work_dir)
    if claims_bundle is None and answer is None:
        skeleton = build_skeleton(contract, case_id, status_intent="partial")
        if not closed:
            skeleton["status_reason"] = "missing both work/claims*.json and work/answer.json; skeleton preserved"
        write_json_atomic(out_dir / "result.json", skeleton)
        print("MUST_FIX: neither claims*.json nor answer.json found in", work_dir)
        return 2

    claims_bundle = claims_bundle or {"paper": {}, "claims": []}
    answer = answer or {}
    pred_uris = _load_predicates()

    checks: List[Dict[str, Any]] = []
    change_records: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    must_fix: List[Dict[str, Any]] = []

    derived_vals: set[str] = set()
    for dv in answer.get("derived_values") or []:
        if isinstance(dv, dict) and dv.get("value") is not None:
            derived_vals.add(str(dv["value"]).strip())

    raw_claims = claims_bundle.get("claims") or []
    verified_claims: List[Dict[str, Any]] = []
    paper_override = claims_bundle.get("paper") or {}

    for cidx, claim in enumerate(raw_claims):
        if not isinstance(claim, dict):
            continue
        cid = str(claim.get("id") or f"c{cidx + 1}")
        pid = str(claim.get("paper_id") or default_paper)
        pmeta = papers_by_id.get(pid, papers_meta[0] if papers_meta else {})
        src_meta = {
            "paper_id": pid,
            "title": paper_override.get("title") or pmeta.get("title") or "not_reported",
            "authors": paper_override.get("authors") or pmeta.get("authors") or [],
            "year": paper_override.get("year") or pmeta.get("year") or "not_reported",
            "doi": paper_override.get("doi") or pmeta.get("doi") or "not_reported",
            "arxiv_id": pmeta.get("arxiv_id") or "not_reported",
            "version": paper_override.get("version") or "not_reported",
            "venue": paper_override.get("venue") or "not_reported",
            "url": paper_override.get("url") or "not_reported",
            "source_file": pmeta.get("source_file") or "not_reported",
            "source_sha256": pmeta.get("sha256") or "",
        }
        sha12 = (src_meta["source_sha256"] or hashlib.sha256(pid.encode("utf-8")).hexdigest())[:12]

        resolved_evs: List[Dict[str, Any]] = []
        ev_texts: List[str] = list(derived_vals)
        joined_quotes = ""
        for eidx, ev in enumerate(claim.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            anc = str(ev.get("anchor") or ev.get("block_id") or ev.get("table_id") or ev.get("element_id") or "")
            quote = str(ev.get("quote") or ev.get("quote_snippet") or ev.get("snippet") or "")
            res = verify(quote, anc, pid, store)
            rec = {
                "context": f"claim.{cid}.evidence[{eidx}]",
                "paper_id": res["paper_id"],
                "anchor": format_anchor(res["paper_id"], res["anchor"], multi_paper),
                "page": res.get("page"),
                "section_id": res.get("section_id"),
                "section_title": res.get("section_title"),
                "quote": quote,
                "match": res["match"],
            }
            checks.append(rec)
            if res["match"] == "relocated":
                change_records.append({
                    "type": "anchor_relocated",
                    "context": rec["context"],
                    "old": anc,
                    "new": rec["anchor"],
                    "page": res.get("page"),
                })
            elif res["match"] == "unverified":
                must_fix.append({"code": "unverified_quote", "context": rec["context"], "anchor": anc, "quote": quote[:120]})
            elif res["match"] in {"too_short", "fuzzy"}:
                warnings.append({"code": f"quote_{res['match']}", "context": rec["context"], "anchor": anc, "quote": quote[:120]})
            if quote:
                ev_texts.append(quote)
                joined_quotes += " " + quote
            if res.get("block_text"):
                ev_texts.append(res["block_text"])
            resolved_evs.append({
                "paper_id": rec["paper_id"],
                "anchor": rec["anchor"],
                "page": rec["page"],
                "section_id": rec["section_id"],
                "quote": quote,
                "match": rec["match"],
            })

        stmt = str(claim.get("statement") or "")
        polarity = str(claim.get("polarity") or "affirmative")
        modality = str(claim.get("modality") or "asserted")
        predicate = str(claim.get("predicate") or "")

        # Atomicity check
        distinct_units = {m.group(1).lower() for m in _UNIT_VAL_RE.finditer(stmt)}
        if _COMPOUND_RE.search(stmt) or len(distinct_units) >= 2:
            warnings.append({"code": "possibly_compound", "context": f"claim.{cid}", "statement": stmt[:140]})

        # Modality & polarity consistency
        if joined_quotes and has_hedge(joined_quotes) and modality == "asserted":
            warnings.append({"code": "hedge_mismatch", "context": f"claim.{cid}", "statement": stmt[:140]})
        if joined_quotes and has_negation(joined_quotes) and polarity == "affirmative" and not has_negation(stmt):
            warnings.append({"code": "negation_dropped", "context": f"claim.{cid}", "statement": stmt[:140]})

        # Controlled predicate check (warn without mutating)
        pred_candidate = None
        if pred_uris and predicate not in pred_uris:
            pred_candidate = "associated_with"
            warnings.append({
                "code": "uncontrolled_predicate",
                "context": f"claim.{cid}",
                "predicate": predicate,
                "candidate": pred_candidate,
            })

        # Numeric grounding check
        qual_vals = " ".join(str(v) for v in (claim.get("qualifiers") or {}).values() if v)
        missing_nums = check_numbers(f"{stmt} {qual_vals}".strip(), ev_texts)
        if missing_nums:
            warnings.append({"code": "number_not_in_evidence", "context": f"claim.{cid}", "missing_numbers": missing_nums})

        norm_rec = {
            "id": cid,
            "iri": f"https://w3id.org/np/local/{sha12}/{cid}",
            "paper_id": pid,
            "section": str(claim.get("section") or (resolved_evs[0].get("section_id") if resolved_evs else "") or ""),
            "statement": stmt,
            "subject": str(claim.get("subject") or ""),
            "predicate": predicate,
            "object": str(claim.get("object") or ""),
            "qualifiers": claim.get("qualifiers") or {},
            "polarity": polarity,
            "modality": modality,
            "evidence_type": str(claim.get("evidence_type") or "author_statement"),
            "confidence": claim.get("confidence", 0.8),
            "confidence_basis": str(claim.get("confidence_basis") or ""),
            "resolved_evidence": resolved_evs,
            "source_metadata": src_meta,
        }
        if pred_candidate:
            norm_rec["predicate_candidate"] = pred_candidate
        verified_claims.append(norm_rec)

    # Deduplication (same predicate + Jaccard >= 0.85)
    deduped: List[Dict[str, Any]] = []
    dedup_tokens: List[set[str]] = []
    dedup_log: List[Dict[str, Any]] = []
    for rec in verified_claims:
        toks = _claim_tokens(rec)
        merged_into = None
        for idx, kept in enumerate(deduped):
            if kept["predicate"] == rec["predicate"] and kept["polarity"] == rec["polarity"]:
                sim = _jaccard(toks, dedup_tokens[idx])
                if sim >= 0.85:
                    merged_into = kept
                    existing_ev = {(e["anchor"], e["quote"]) for e in kept["resolved_evidence"]}
                    for ev in rec["resolved_evidence"]:
                        if (ev["anchor"], ev["quote"]) not in existing_ev:
                            kept["resolved_evidence"].append(ev)
                    dedup_log.append({
                        "kept_id": kept["id"],
                        "merged_id": rec["id"],
                        "similarity": round(sim, 3),
                    })
                    break
        if merged_into is None:
            deduped.append(rec)
            dedup_tokens.append(toks)

    # Conflict detection (same subject/predicate/object, opposite polarity)
    conflicts: List[Dict[str, Any]] = []
    by_spo: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for rec in deduped:
        key = (normalize_text(rec["subject"]), normalize_text(rec["predicate"]), normalize_text(rec["object"]))
        by_spo.setdefault(key, []).append(rec)
    for key, group in by_spo.items():
        pols = {g["polarity"] for g in group}
        if "affirmative" in pols and "negative" in pols:
            conflicts.append({
                "type": "polarity_conflict",
                "subject": group[0]["subject"],
                "predicate": group[0]["predicate"],
                "object": group[0]["object"],
                "claim_ids": [g["id"] for g in group],
            })

    # Verify answer fields & answer.evidence
    fields_payload = copy.deepcopy(answer.get("fields") or {})
    scan_fields_evidence(fields_payload, default_paper, store, multi_paper, change_records, warnings)
    for ptr, evs in (answer.get("evidence") or {}).items():
        if isinstance(evs, list):
            for eidx, ev in enumerate(evs):
                if isinstance(ev, dict):
                    res = verify(str(ev.get("quote") or ""), str(ev.get("anchor") or ""), default_paper, store)
                    checks.append({
                        "context": f"answer.evidence[{ptr}][{eidx}]",
                        "paper_id": res["paper_id"],
                        "anchor": format_anchor(res["paper_id"], res["anchor"], multi_paper),
                        "page": res.get("page"),
                        "quote": str(ev.get("quote") or ""),
                        "match": res["match"],
                    })
                    if res["match"] == "unverified":
                        must_fix.append({"code": "unverified_quote", "context": f"answer.evidence[{ptr}][{eidx}]", "quote": str(ev.get("quote") or "")[:120]})

    # Serialize RDF outputs
    created_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _, trig_str, nq_str, jsonld_str = _build_quads_and_jsonld(deduped, pred_uris, created_iso)
    trig_path = out_dir / "nanopubs.trig"
    nq_path = out_dir / "nanopubs.nq"
    jsonld_path = out_dir / "nanopubs.jsonld"
    md_path = out_dir / "nanopubs.md"
    ev_check_path = out_dir / "evidence_check.json"

    write_text_lf(trig_path, trig_str)
    write_text_lf(nq_path, nq_str)
    write_text_lf(jsonld_path, jsonld_str)
    rdf_check = _validate_rdf_exports(nq_path, trig_path, jsonld_path)
    if not rdf_check.get("consistent"):
        must_fix.append({"code": "rdf_export_inconsistent", "context": "exports", "message": json.dumps(rdf_check)})

    write_text_lf(md_path, _render_nanopubs_md(case_id, deduped, conflicts, dedup_log, rdf_check))

    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}
    write_json_atomic(ev_check_path, {
        "case_id": case_id,
        "claim_files": claim_files,
        "counts": counts,
        "must_fix": must_fix,
        "warnings": warnings,
        "change_records": change_records,
        "deduplication_log": dedup_log,
        "conflicts": conflicts,
        "rdf_validation": rdf_check,
        "checks": checks,
    })

    # Build result.json
    intent = str(answer.get("status") or ("partial" if must_fix else "success")).lower()
    if intent not in {"success", "partial", "unable"}:
        intent = "partial"
    if must_fix and intent == "success":
        intent = "partial"

    skeleton = build_skeleton(contract, case_id, status_intent=intent)
    result = _overlay(skeleton, fields_payload)
    specs = contract_specs(contract)
    if "case_id" in specs or not closed:
        result["case_id"] = case_id
    if "status" in specs or not closed:
        st_spec = specs.get("status")
        enum_vals = st_spec.get("enum") if isinstance(st_spec, dict) else None
        if isinstance(enum_vals, list) and result.get("status") not in enum_vals:
            result["status"] = pick_status_value(contract, intent)
        elif not result.get("status"):
            result["status"] = pick_status_value(contract, intent)

    # Auto-fill contract array fields matching nanopub|assertion|claim if left empty.
    for fname, fspec in specs.items():
        if re.search(r"nanopub|assertion|claim", fname, re.I) and isinstance(fspec, dict) and fspec.get("type") == "array":
            if not result.get(fname):
                item_spec = fspec.get("items")
                result[fname] = [_project_claim_to_item(npub, item_spec) for npub in deduped]
                if isinstance(item_spec, dict) and item_spec.get("additionalProperties") is False:
                    warnings.append({
                        "code": "auto_projected_contract_array",
                        "context": fname,
                        "message": "Auto-projected claims onto closed item schema; verify field alignment.",
                    })

    all_uncertainties = list(meta.get("uncertainties") or []) + list(answer.get("uncertainties") or [])
    if "uncertainties" in specs and not result.get("uncertainties") and all_uncertainties:
        u_items = (specs["uncertainties"] or {}).get("items", {}) if isinstance(specs["uncertainties"], dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            result["uncertainties"] = [
                u if isinstance(u, str) else f"{u.get('item') or u.get('code')}: {u.get('reason') or u.get('message')}"
                for u in all_uncertainties
            ]
        else:
            result["uncertainties"] = all_uncertainties

    primary_src_meta = deduped[0]["source_metadata"] if deduped else (papers_meta[0] if papers_meta else {})
    if "source_metadata" in specs and not result.get("source_metadata"):
        result["source_metadata"] = primary_src_meta

    sources_list = [
        {
            "paper_id": p["paper_id"],
            "title": p["title"],
            "year": p["year"],
            "doi": p["doi"],
            "source_file": p["source_file"],
            "sha256": p["sha256"],
        }
        for p in papers_meta
    ]

    if not closed:
        result["nanopub_count"] = len(deduped)
        result["nanopublications"] = deduped
        result["source_metadata"] = primary_src_meta
        result["sources"] = sources_list
        result["deduplication_log"] = dedup_log
        result["conflicts"] = conflicts
        result["uncertainties"] = all_uncertainties
        result["exports"] = {
            "trig": str(trig_path),
            "nquads": str(nq_path),
            "jsonld": str(jsonld_path),
            "markdown": str(md_path),
            "evidence_check": str(ev_check_path),
        }
        result["validation"] = {"counts": counts, "rdf": rdf_check, "warning_count": len(warnings), "must_fix_count": len(must_fix)}
        result["change_records"] = change_records
        result["status_reason"] = answer.get("status_reason") or (
            "verified with unresolved issues; see evidence_check.json" if must_fix else "nanopublications and RDF exports verified"
        )
    else:
        allowed = set(contract["properties"].keys())
        result = {k: v for k, v in result.items() if k in allowed}

    c_issues = contract_issues(contract, result)
    for ci in c_issues:
        must_fix.append({"code": ci["code"], "context": ci["field"], "message": ci["message"]})

    write_json_atomic(out_dir / "result.json", result)

    print(f"MUST_FIX ({len(must_fix)}):")
    for m in must_fix[:15]:
        print(f"  - [{m.get('code')}] {m.get('context')}: {m.get('message') or m.get('quote', '')}")
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings[:15]:
        print(f"  - [{w.get('code')}] {w.get('context')}: {w.get('missing_numbers') or w.get('statement') or w.get('predicate') or w.get('message', '')}")
    print(
        f"STATS: nanopubs={len(deduped)} dedup={len(dedup_log)} conflicts={len(conflicts)} "
        f"exact={counts['exact']} relocated={counts['relocated']} unverified={counts['unverified']} "
        f"rdf={rdf_check.get('mode')}:{rdf_check.get('consistent')} contract_errors={len(c_issues)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify claims, serialize 4-graph Nanopublications, and write result.json.")
    parser.add_argument("--case", default="/input/case.json", help="Case JSON path (default /input/case.json)")
    parser.add_argument("--work", default="/app/work/r14", help="Work directory (default /app/work/r14)")
    parser.add_argument("--out", default="/app/output", help="Output directory (default /app/output)")
    args = parser.parse_args()
    return run_finalize(Path(args.case), Path(args.work), Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
