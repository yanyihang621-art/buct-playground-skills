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
from urllib.parse import quote as url_quote

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence import (
    IndexStore,
    check_numbers,
    check_units,
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
    field_conforms,
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

_COMPOUND_RE = re.compile(
    r"(?:;|；|\bwhereas\b|,\s*while\b|,\s*and\s+(?:also|moreover|furthermore|it|they|we|the)\b|以及|同时|而且)",
    re.I,
)
_MULTI_SENT_RE = re.compile(r"(?<![A-Z0-9])[.!?。！？]\s+(?=[A-Z\u4e00-\u9fff])")
_ASSOC_CUE_RE = re.compile(r"\b(?:associated\s+with|correlat(?:ed|es)\s+with|linked\s+to)\b|相关", re.I)
_CAUSAL_CUE_RE = re.compile(
    r"\b(?:caus(?:e|es|ed)|induc(?:e|es|ed)|lead(?:s)?\s+to|led\s+to|result(?:s|ed)?\s+in|trigger(?:s|ed)?|driv(?:e|es|en)|due\s+to|because)\b|"
    r"(?:导致|引起|促使|造成)",
    re.I,
)
_NQ_LINE_RE = re.compile(
    r'^(?:<[^>\s]+>|_:[A-Za-z0-9_]+)\s+<[^>\s]+>\s+(?:<[^>\s]+>|_:[A-Za-z0-9_]+|"(?:[^"\\]|\\.)*"(?:\^\^<[^>\s]+>|@[a-zA-Z-]+)?)\s+<[^>\s]+>\s*\.$'
)
_ANTONYM_PAIRS = {
    frozenset(("increases", "decreases")),
    frozenset(("improves", "inhibits")),
    frozenset(("activates", "inhibits")),
    frozenset(("causes", "prevents")),
}


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


def _iri_safe_token(text: str) -> str:
    raw = (text or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    if not cleaned:
        cleaned = "item"
    if cleaned != raw or len(cleaned) > 48:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
        return f"{cleaned[:40]}_{digest}"
    return cleaned


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_")
    if not s:
        s = "item"
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]
    return f"{s[:32]}_{digest}"


def _normalize_doi(doi: Optional[str]) -> str:
    raw = str(doi or "").strip()
    if not raw or raw.lower() == "not_reported":
        return "not_reported"
    raw = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", raw, flags=re.I)
    raw = re.sub(r"^doi:\s*", "", raw, flags=re.I)
    return raw.strip() or "not_reported"


def _normalize_source_iri(doi: str, sha256: str) -> str:
    norm_doi = _normalize_doi(doi)
    if norm_doi != "not_reported":
        return "https://doi.org/" + url_quote(norm_doi, safe="/._-;():")
    return f"urn:sha256:{_iri_safe_token(sha256 or 'unknown')}"


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


def _is_empty_val(val: Any) -> bool:
    if val is None or val == "" or val == [] or val == {}:
        return True
    if isinstance(val, dict):
        return all(_is_empty_val(v) for v in val.values())
    return False


def _load_claims(work_dir: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    files = sorted(work_dir.glob("claims*.json"))
    if not files:
        return None, []
    merged: Dict[str, Any] = {"paper": {}, "papers": {}, "claims": []}
    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            merged["claims"].extend(data)
            continue
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("paper"), dict):
            for k, v in data["paper"].items():
                if v and not merged["paper"].get(k):
                    merged["paper"][k] = v
        if isinstance(data.get("papers"), dict):
            for pid, pinfo in data["papers"].items():
                if isinstance(pinfo, dict):
                    dst = merged["papers"].setdefault(pid, {})
                    for k, v in pinfo.items():
                        if v and not dst.get(k):
                            dst[k] = v
        for key in ("claims", "nanopublications", "assertions"):
            if isinstance(data.get(key), list):
                merged["claims"].extend(data[key])
                break
    return merged, [str(f) for f in files]


def _load_answer(work_dir: Path, contract: Any = None) -> Optional[Dict[str, Any]]:
    path = work_dir / "answer.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("fields"), dict) and raw["fields"]:
        return raw

    specs = contract_specs(contract)
    meta_only = {"fields", "status_reason", "derived_values", "_example_notice"}
    fields: Dict[str, Any] = {}
    pointer_ev: Dict[str, Any] = {}
    for k, v in raw.items():
        if k in meta_only:
            continue
        if k == "evidence" and isinstance(v, dict) and any(str(pk).startswith("/") for pk in v.keys()):
            pointer_ev = v
            if "evidence" in specs and field_conforms(specs["evidence"], v, contract):
                fields["evidence"] = v
            continue
        if k in {"status", "uncertainties"} and k not in specs and not is_closed_contract(contract):
            fields[k] = v
            continue
        fields[k] = v
    return {
        "fields": fields,
        "status": raw.get("status"),
        "status_reason": raw.get("status_reason"),
        "evidence": pointer_ev if pointer_ev else (raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}),
        "uncertainties": raw.get("uncertainties") if isinstance(raw.get("uncertainties"), list) else [],
        "derived_values": raw.get("derived_values") if isinstance(raw.get("derived_values"), list) else [],
    }


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


def _content_hash(rec: Dict[str, Any]) -> str:
    payload = {
        "subject": rec.get("subject"),
        "predicate": rec.get("predicate"),
        "object": rec.get("object"),
        "statement": rec.get("statement"),
        "polarity": rec.get("polarity"),
        "modality": rec.get("modality"),
        "qualifiers": rec.get("qualifiers"),
        "evidence": [{"anchor": e.get("anchor"), "quote": e.get("quote")} for e in rec.get("resolved_evidence") or []],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _build_four_graph_json(nanopubs: List[Dict[str, Any]], created_iso: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for npub in nanopubs:
        base = npub["iri"]
        out.append({
            "id": npub["id"],
            "iri": base,
            "head": {
                "graph": f"{base}/head",
                "type": f"{_NP_NS}Nanopublication",
                "hasAssertion": f"{base}/assertion",
                "hasProvenance": f"{base}/provenance",
                "hasPublicationInfo": f"{base}/pubinfo",
            },
            "assertion": {
                "graph": f"{base}/assertion",
                "statement": npub["statement"],
                "subject": npub["subject"],
                "predicate": npub["predicate"],
                "object": npub["object"],
                "polarity": npub["polarity"],
                "modality": npub["modality"],
                "qualifiers": npub.get("qualifiers") or {},
            },
            "provenance": {
                "graph": f"{base}/provenance",
                "evidence_type": npub.get("evidence_type"),
                "confidence": npub.get("confidence"),
                "confidence_basis": npub.get("confidence_basis"),
                "evidence": npub.get("resolved_evidence") or [],
            },
            "pubinfo": {
                "graph": f"{base}/pubinfo",
                "created": created_iso,
                "creator": "reading-14-nanopublication",
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "content_hash": npub.get("content_hash"),
                "source_metadata": npub.get("source_metadata") or {},
            },
        })
    return out


def _build_quads_and_jsonld(
    nanopubs: List[Dict[str, Any]],
    pred_uris: Dict[str, str],
    created_iso: str,
) -> Tuple[List[Tuple[str, str, Tuple[str, str, Optional[str]], str]], str, str, str]:
    """Return (quads, trig_str, nq_str, jsonld_str)."""
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
        pred_key = _iri_safe_token(npub["predicate"])
        pred_iri = pred_uris.get(npub["predicate"], f"{_NPV_NS}{pred_key}")

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
        src_iri = _normalize_source_iri(src_meta.get("doi"), src_meta.get("source_sha256"))
        add_lit(g_prov, g_assert, f"{_NPV_NS}confidence", str(npub.get("confidence", 0.0)), f"{_XSD_NS}decimal")
        if npub.get("confidence_basis"):
            add_lit(g_prov, g_assert, f"{_NPV_NS}confidenceBasis", npub["confidence_basis"])
        add_iri(g_prov, g_assert, f"{_PROV_NS}wasGeneratedBy", "https://w3id.org/np/local/agent/reading-14-nanopublication")

        for idx, ev in enumerate(npub.get("resolved_evidence") or [], 1):
            ev_iri = f"{base}/ev_{idx}"
            add_iri(g_prov, g_assert, f"{_PROV_NS}wasDerivedFrom", ev_iri)
            add_lit(g_prov, ev_iri, f"{_PROV_NS}value", ev.get("quote", ""))
            add_lit(g_prov, ev_iri, f"{_NPV_NS}anchor", ev.get("anchor", ""))
            if isinstance(ev.get("page"), int):
                add_lit(g_prov, ev_iri, f"{_NPV_NS}page", str(ev["page"]), f"{_XSD_NS}integer")
            if ev.get("section_id"):
                add_lit(g_prov, ev_iri, f"{_NPV_NS}sectionId", str(ev["section_id"]))
            if ev.get("section_title"):
                add_lit(g_prov, ev_iri, f"{_NPV_NS}sectionTitle", str(ev["section_title"]))
            if ev.get("native_locator"):
                add_lit(g_prov, ev_iri, f"{_NPV_NS}nativeLocator", json.dumps(ev["native_locator"], ensure_ascii=False))
            add_lit(g_prov, ev_iri, f"{_NPV_NS}evidenceType", npub.get("evidence_type", "author_statement"))
            add_iri(g_prov, ev_iri, f"{_PROV_NS}wasQuotedFrom", src_iri)

        # PubInfo graph
        add_lit(g_pub, base, f"{_DCT_NS}created", created_iso, f"{_XSD_NS}dateTime")
        add_lit(g_pub, base, f"{_DCT_NS}creator", "reading-14-nanopublication")
        add_iri(g_pub, base, f"{_DCT_NS}license", "https://creativecommons.org/licenses/by/4.0/")
        if npub.get("content_hash"):
            add_lit(g_pub, base, f"{_NPV_NS}contentHash", str(npub["content_hash"]))
        add_lit(g_pub, src_iri, f"{_DCT_NS}title", str(src_meta.get("title") or "not_reported"))
        for author in src_meta.get("authors") or []:
            add_lit(g_pub, src_iri, f"{_DCT_NS}creator", str(author))
        if src_meta.get("year") and src_meta["year"] != "not_reported":
            add_lit(g_pub, src_iri, f"{_DCT_NS}date", str(src_meta["year"]))
        norm_doi = _normalize_doi(src_meta.get("doi"))
        ident = norm_doi if norm_doi != "not_reported" else src_meta.get("arxiv_id")
        if ident and ident != "not_reported":
            add_lit(g_pub, src_iri, f"{_DCT_NS}identifier", str(ident))

    nq_lines: List[str] = []
    by_graph: Dict[str, List[Tuple[str, str, Tuple[str, str, Optional[str]]]]] = {}
    for s, p, o, g in quads:
        kind, val, dt = o
        o_str = f"<{val}>" if kind == "iri" else (f'"{_esc_nq(val)}"^^<{dt}>' if dt else f'"{_esc_nq(val)}"')
        nq_lines.append(f"<{s}> <{p}> {o_str} <{g}> .")
        by_graph.setdefault(g, []).append((s, p, o))

    trig_lines: List[str] = [
        f"@prefix rdf: <{_RDF_NS}> .",
        f"@prefix rdfs: <{_RDFS_NS}> .",
        f"@prefix xsd: <{_XSD_NS}> .",
        f"@prefix dct: <{_DCT_NS}> .",
        f"@prefix prov: <{_PROV_NS}> .",
        f"@prefix np: <{_NP_NS}> .",
        f"@prefix npv: <{_NPV_NS}> .",
        "",
    ] if by_graph else []
    for g, triples in by_graph.items():
        trig_lines.append(f"<{g}> {{")
        for s, p, (kind, val, dt) in triples:
            o_str = f"<{val}>" if kind == "iri" else (f'"{_esc_nq(val)}"^^<{dt}>' if dt else f'"{_esc_nq(val)}"')
            trig_lines.append(f"  <{s}> <{p}> {o_str} .")
        trig_lines.append("}")

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

    return (
        quads,
        "\n".join(trig_lines) + ("\n" if trig_lines else ""),
        "\n".join(nq_lines) + ("\n" if nq_lines else ""),
        json.dumps(jsonld_graphs, ensure_ascii=False, indent=2) + "\n",
    )


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


def _project_obj_to_spec(data: Dict[str, Any], spec: Any) -> Any:
    if not isinstance(spec, dict) or not isinstance(spec.get("properties"), dict):
        return copy.deepcopy(data)
    out: Dict[str, Any] = {}
    for pname, pspec in spec["properties"].items():
        if pname not in data or data[pname] is None:
            continue
        val = data[pname]
        pt = pspec.get("type") if isinstance(pspec, dict) else None
        if pt == "object" and isinstance(val, dict):
            out[pname] = _project_obj_to_spec(val, pspec)
        elif pt == "array" and isinstance(val, list) and isinstance(pspec.get("items"), dict):
            it_spec = pspec["items"]
            if it_spec.get("type") == "object" or isinstance(it_spec.get("properties"), dict):
                out[pname] = [_project_obj_to_spec(x, it_spec) if isinstance(x, dict) else x for x in val]
            else:
                out[pname] = val
        elif pt == "string" and not isinstance(val, str):
            out[pname] = json.dumps(val, ensure_ascii=False)
        else:
            out[pname] = val
    return out


def _project_claim_to_item(npub: Dict[str, Any], fg: Dict[str, Any], item_spec: Any) -> Dict[str, Any]:
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
        "assertion": fg["assertion"] if isinstance(props.get("assertion"), dict) and props["assertion"].get("type") == "object" else npub.get("statement"),
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
        "provenance": fg["provenance"] if isinstance(props.get("provenance"), dict) and "evidence" in (props["provenance"].get("properties") or {}) else npub.get("source_metadata"),
        "source_metadata": npub.get("source_metadata"),
        "head": fg["head"],
        "pubinfo": fg["pubinfo"],
    }
    return _project_obj_to_spec(syn, item_spec)


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
        "| ID | Triple (`S -> P -> O`) | Statement | Polarity / Modality | Evidence (All Quotes & Anchors) | Evidence Type / Conf |",
        "|---|---|---|---|---|---|",
    ]
    for npub in nanopubs:
        ev_parts = []
        for ev in npub.get("resolved_evidence") or []:
            p = ev.get("page")
            anc_disp = f"p.{p} {ev.get('anchor', '')}" if p is not None else ev.get("anchor", "")
            q = (ev.get("quote") or "").replace("\n", " ").replace("|", "/")
            ev_parts.append(f"[{anc_disp}] \"{q}\"")
        ev_cell = " ; ".join(ev_parts) or "-"
        triple = f"`{npub.get('subject', '')}` -> `{npub.get('predicate', '')}` -> `{npub.get('object', '')}`".replace("|", "/")
        st = (npub.get("statement") or "").replace("\n", " ").replace("|", "/")
        quals = ", ".join(f"{k}={v}" for k, v in (npub.get("qualifiers") or {}).items() if v)
        if quals:
            st = f"{st} ({quals.replace('|', '/')})"
        conf_cell = f"{npub.get('evidence_type', '')} / {npub.get('confidence', '')}"
        if npub.get("confidence_basis"):
            conf_cell += f": {str(npub['confidence_basis']).replace('|', '/')}"
        lines.append(
            f"| {npub['id']} | {triple} | {st} | {npub['polarity']} / {npub['modality']} | {ev_cell} | {conf_cell} |"
        )

    lines.extend(["", "## Source Provenance", ""])
    seen_sources = set()
    for npub in nanopubs:
        sm = npub.get("source_metadata") or {}
        key = (sm.get("paper_id"), sm.get("source_sha256"))
        if key in seen_sources:
            continue
        seen_sources.add(key)
        lines.append(
            f"- **{sm.get('paper_id', 'paper')}**: {sm.get('title', 'not_reported')} | "
            f"authors={', '.join(sm.get('authors') or []) or 'not_reported'} | year={sm.get('year', 'not_reported')} | "
            f"doi={sm.get('doi', 'not_reported')} | arxiv={sm.get('arxiv_id', 'not_reported')}"
        )

    if conflicts:
        lines.extend(["", "## Detected Conflicts", ""])
        for cf in conflicts:
            lines.append(f"- **[{cf.get('type')}]** claims={cf.get('claim_ids')}: {cf.get('subject')} / {cf.get('predicate')} / {cf.get('object')}")

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
    answer = _load_answer(work_dir, contract=contract)
    if claims_bundle is None and answer is None:
        skeleton = build_skeleton(contract, case_id, status_intent="partial")
        if not closed:
            skeleton["status_reason"] = "missing both work/claims*.json and work/answer.json; skeleton preserved"
        write_json_atomic(out_dir / "result.json", skeleton)
        print("MUST_FIX: neither claims*.json nor answer.json found in", work_dir)
        return 2

    claims_bundle = claims_bundle or {"paper": {}, "papers": {}, "claims": []}
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
    top_paper_override = claims_bundle.get("paper") or {}
    per_paper_overrides = claims_bundle.get("papers") or {}

    for cidx, claim in enumerate(raw_claims):
        if not isinstance(claim, dict):
            continue
        cid = str(claim.get("id") or f"c{cidx + 1}")
        pid = str(claim.get("paper_id") or default_paper)
        pmeta = papers_by_id.get(pid, papers_meta[0] if papers_meta else {})

        # Isolate per-paper metadata in multi-paper runs.
        claim_ov = claim.get("source_metadata") if isinstance(claim.get("source_metadata"), dict) else {}
        pid_ov = per_paper_overrides.get(pid) if isinstance(per_paper_overrides.get(pid), dict) else {}
        paper_ov = top_paper_override if (not multi_paper or pid == default_paper) else {}
        merged_ov = {**paper_ov, **pid_ov, **claim_ov}

        src_meta = {
            "paper_id": pid,
            "title": merged_ov.get("title") or pmeta.get("title") or "not_reported",
            "authors": merged_ov.get("authors") or pmeta.get("authors") or [],
            "year": merged_ov.get("year") or pmeta.get("year") or "not_reported",
            "doi": _normalize_doi(merged_ov.get("doi") or pmeta.get("doi")),
            "arxiv_id": merged_ov.get("arxiv_id") or pmeta.get("arxiv_id") or "not_reported",
            "version": merged_ov.get("version") or pmeta.get("version") or "not_reported",
            "venue": merged_ov.get("venue") or pmeta.get("venue") or "not_reported",
            "url": merged_ov.get("url") or pmeta.get("url") or "not_reported",
            "source_file": pmeta.get("source_file") or "not_reported",
            "source_sha256": pmeta.get("sha256") or "",
        }
        sha12 = _iri_safe_token((src_meta["source_sha256"] or hashlib.sha256(pid.encode("utf-8")).hexdigest())[:12])
        safe_cid = _iri_safe_token(cid)

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
            ev_item: Dict[str, Any] = {
                "paper_id": rec["paper_id"],
                "anchor": rec["anchor"],
                "page": rec["page"],
                "section_id": rec["section_id"],
                "section_title": rec["section_title"],
                "quote": quote,
                "match": rec["match"],
            }
            idx_entry = store.get(res["paper_id"], res["anchor"])
            if idx_entry and idx_entry.get("native_locator"):
                ev_item["native_locator"] = idx_entry["native_locator"]
            resolved_evs.append(ev_item)

        stmt = str(claim.get("statement") or "")
        polarity = str(claim.get("polarity") or "affirmative")
        modality = str(claim.get("modality") or "asserted")
        predicate = str(claim.get("predicate") or "")

        # Atomicity & length checks
        if len(stmt) > 280 or len(_MULTI_SENT_RE.findall(stmt)) >= 1:
            warnings.append({"code": "over_broad_assertion", "context": f"claim.{cid}", "statement": stmt[:140]})
        if _COMPOUND_RE.search(stmt):
            warnings.append({"code": "possibly_compound", "context": f"claim.{cid}", "statement": stmt[:140]})

        # Modality, polarity, & causal overclaim checks
        if joined_quotes and has_hedge(joined_quotes) and modality == "asserted":
            warnings.append({"code": "hedge_mismatch", "context": f"claim.{cid}", "statement": stmt[:140]})
        if joined_quotes and has_negation(joined_quotes) and polarity == "affirmative" and not has_negation(stmt):
            warnings.append({"code": "negation_dropped", "context": f"claim.{cid}", "statement": stmt[:140]})
        if predicate == "causes" and joined_quotes and _ASSOC_CUE_RE.search(joined_quotes) and not _CAUSAL_CUE_RE.search(joined_quotes):
            warnings.append({"code": "causal_overclaim", "context": f"claim.{cid}", "statement": stmt[:140]})

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

        # Numeric & unit grounding checks
        qual_vals = " ".join(str(v) for v in (claim.get("qualifiers") or {}).values() if v)
        claim_blob = f"{stmt} {qual_vals}".strip()
        missing_nums = check_numbers(claim_blob, ev_texts)
        if missing_nums:
            warnings.append({"code": "number_not_in_evidence", "context": f"claim.{cid}", "missing_numbers": missing_nums})
        unit_mismatches = check_units(claim_blob, ev_texts)
        if unit_mismatches:
            warnings.append({"code": "unit_mismatch", "context": f"claim.{cid}", "mismatches": unit_mismatches})

        norm_rec = {
            "id": cid,
            "iri": f"https://w3id.org/np/local/{sha12}/{safe_cid}",
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
        norm_rec["content_hash"] = _content_hash(norm_rec)
        if pred_candidate:
            norm_rec["predicate_candidate"] = pred_candidate
        verified_claims.append(norm_rec)

    # Deduplication (same predicate + polarity + Jaccard >= 0.85)
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

    # Conflict detection: polarity, antonym predicate, directional outperform
    conflicts: List[Dict[str, Any]] = []
    by_spo: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    by_so: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for rec in deduped:
        ns, npred, no = normalize_text(rec["subject"]), normalize_text(rec["predicate"]), normalize_text(rec["object"])
        by_spo.setdefault((ns, npred, no), []).append(rec)
        by_so.setdefault((ns, no), []).append(rec)

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

    seen_ant = set()
    for (ns, no), group in by_so.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a["polarity"] == b["polarity"] and frozenset((a["predicate"], b["predicate"])) in _ANTONYM_PAIRS:
                    pair_key = tuple(sorted((a["id"], b["id"])))
                    if pair_key not in seen_ant:
                        seen_ant.add(pair_key)
                        conflicts.append({
                            "type": "antonym_predicate_conflict",
                            "subject": a["subject"],
                            "predicate": f"{a['predicate']} vs {b['predicate']}",
                            "object": a["object"],
                            "claim_ids": [a["id"], b["id"]],
                        })

    seen_dir = set()
    outperform_claims = [r for r in deduped if r["predicate"] == "outperforms" and r["polarity"] == "affirmative"]
    for a in outperform_claims:
        for b in outperform_claims:
            if a["id"] < b["id"] and normalize_text(a["subject"]) == normalize_text(b["object"]) and normalize_text(a["object"]) == normalize_text(b["subject"]):
                pair_key = (a["id"], b["id"])
                if pair_key not in seen_dir:
                    seen_dir.add(pair_key)
                    conflicts.append({
                        "type": "directional_conflict",
                        "subject": a["subject"],
                        "predicate": "outperforms",
                        "object": a["object"],
                        "claim_ids": [a["id"], b["id"]],
                    })

    # Verify answer fields & answer.evidence
    fields_payload = copy.deepcopy(answer.get("fields") or {})
    scan_fields_evidence(fields_payload, default_paper, store, multi_paper, change_records, warnings, checks=checks)
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

    # Serialize 4-graph JSON + RDF outputs
    created_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    four_graph_list = _build_four_graph_json(deduped, created_iso)
    _, trig_str, nq_str, jsonld_str = _build_quads_and_jsonld(deduped, pred_uris, created_iso)
    trig_path = out_dir / "nanopubs.trig"
    nq_path = out_dir / "nanopubs.nq"
    jsonld_path = out_dir / "nanopubs.jsonld"
    np_json_path = out_dir / "nanopubs.json"
    md_path = out_dir / "nanopubs.md"
    ev_check_path = out_dir / "evidence_check.json"

    write_text_lf(trig_path, trig_str)
    write_text_lf(nq_path, nq_str)
    write_text_lf(jsonld_path, jsonld_str)
    write_json_atomic(np_json_path, {"case_id": case_id, "count": len(four_graph_list), "nanopublications": four_graph_list})
    rdf_check = _validate_rdf_exports(nq_path, trig_path, jsonld_path)
    if not rdf_check.get("consistent"):
        must_fix.append({"code": "rdf_export_inconsistent", "context": "exports", "message": json.dumps(rdf_check)})

    write_text_lf(md_path, _render_nanopubs_md(case_id, deduped, conflicts, dedup_log, rdf_check))

    counts = {k: sum(1 for c in checks if c["match"] == k) for k in ("exact", "relocated", "fuzzy", "unverified", "too_short")}

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

    # Auto-fill contract array fields matching nanopub|assertion|claim ONLY if left empty.
    for fname, fspec in specs.items():
        if re.search(r"nanopub|assertion|claim", fname, re.I) and isinstance(fspec, dict) and fspec.get("type") == "array":
            if not result.get(fname):
                item_spec = fspec.get("items")
                projected = [_project_claim_to_item(npub, fg, item_spec) for npub, fg in zip(deduped, four_graph_list)]
                if field_conforms(fspec, projected, contract):
                    result[fname] = projected

    all_uncertainties = list(meta.get("uncertainties") or []) + list(answer.get("uncertainties") or [])
    if "uncertainties" in specs and _is_empty_val(result.get("uncertainties")) and all_uncertainties:
        u_items = (specs["uncertainties"] or {}).get("items", {}) if isinstance(specs["uncertainties"], dict) else {}
        if isinstance(u_items, dict) and u_items.get("type") == "string":
            result["uncertainties"] = [
                u if isinstance(u, str) else f"{u.get('item') or u.get('code')}: {u.get('reason') or u.get('message')}"
                for u in all_uncertainties
            ]
        else:
            result["uncertainties"] = all_uncertainties

    primary_src_meta = deduped[0]["source_metadata"] if deduped else (papers_meta[0] if papers_meta else {})
    if "source_metadata" in specs and _is_empty_val(result.get("source_metadata")):
        if field_conforms(specs["source_metadata"], primary_src_meta, contract):
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
        enrichments = {
            "nanopub_count": len(deduped),
            "nanopublications": deduped,
            "source_metadata": primary_src_meta,
            "sources": sources_list,
            "deduplication_log": dedup_log,
            "conflicts": conflicts,
            "uncertainties": all_uncertainties,
            "exports": {
                "json": str(np_json_path),
                "trig": str(trig_path),
                "nquads": str(nq_path),
                "jsonld": str(jsonld_path),
                "markdown": str(md_path),
                "evidence_check": str(ev_check_path),
            },
            "verification": {"counts": counts, "rdf": rdf_check, "warning_count": len(warnings), "must_fix_count": len(must_fix)},
            "change_records": change_records,
            "status_reason": answer.get("status_reason") or (
                "verified with unresolved issues; see evidence_check.json" if must_fix else "nanopublications and RDF exports verified"
            ),
        }
        for ek, ev in enrichments.items():
            existing_empty = _is_empty_val(result.get(ek))
            conforms = (ek not in specs) or field_conforms(specs[ek], ev, contract)
            if existing_empty and conforms:
                result[ek] = ev
            elif ek != "status_reason" and not existing_empty:
                result[f"skill_{ek}"] = ev
            elif ek in specs and not conforms:
                result[f"skill_{ek}"] = ev
    else:
        allowed = set(contract["properties"].keys())
        result = {k: v for k, v in result.items() if k in allowed}

    # Run contract check BEFORE finalizing status and writing evidence_check.json & result.json.
    c_issues = contract_issues(contract, result)
    for ci in c_issues:
        must_fix.append({"code": ci["code"], "context": ci["field"], "message": ci["message"]})
    if c_issues and intent == "success":
        intent = "partial"

    if "status" in specs or not closed:
        st_spec = specs.get("status")
        enum_vals = st_spec.get("enum") if isinstance(st_spec, dict) else None
        if intent == "partial" or not result.get("status") or (isinstance(enum_vals, list) and result.get("status") not in enum_vals):
            result["status"] = pick_status_value(contract, intent)
    if not closed and not result.get("verification"):
        result["verification"] = {"counts": counts, "rdf": rdf_check, "warning_count": len(warnings), "must_fix_count": len(must_fix)}

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
    write_json_atomic(out_dir / "result.json", result)

    print(f"MUST_FIX ({len(must_fix)}):")
    for m in must_fix[:15]:
        print(f"  - [{m.get('code')}] {m.get('context')}: {m.get('message') or m.get('quote', '')}")
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings[:15]:
        detail = w.get("missing_numbers") or w.get("mismatches") or w.get("statement") or w.get("predicate") or w.get("message", "")
        print(f"  - [{w.get('code')}] {w.get('context')}: {detail}")
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
