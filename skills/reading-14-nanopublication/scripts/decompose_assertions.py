#!/usr/bin/env python3
"""Decompose scientific claims into atomic 4-graph Nanopublications with controlled predicates and provenance."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\(])")
CLAUSE_SPLIT_RE = re.compile(
    r"(?:;\s*(?:moreover|furthermore|in\s+addition|however)?,?\s*|,\s+(?:whereas|while|and\s+also|which\s+leads\s+to|resulting\s+in)\s+|；\s*|，同时\s*|，从而\s*)",
    re.IGNORECASE,
)
LEADING_COND_RE = re.compile(
    r"^((?:At|Under|In|For|When|With|Assuming)\s+[^,;]{3,65}?),\s+(.+)$",
    re.IGNORECASE,
)
LEADING_CONNECTOR_RE = re.compile(
    r"^(?:moreover|furthermore|however|in\s+addition|additionally|therefore|consequently|meanwhile|whereas|while)[,\s]+",
    re.IGNORECASE,
)
CONDITION_VAL_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|MPa|kPa|bar|mL\s*g-1\s*h-1|mL/min|wt%|mol%|h|min|mA\s*cm-2|M\s+KOH|pH\s*[<≤>]?\s*\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20[0-3]\d)\b")

CITED_PRIOR_RE = re.compile(
    r"(?:\[\d+(?:,\s*\d+)*\]|\b(?:previous|prior|earlier)\s+(?:studies|work|reports?)\b|\breported\s+(?:by|that)\b|\b(?:et\s+al\.,?\s*(?:19|20)\d{2})\b|已有研究|前人研究)",
    re.IGNORECASE,
)
HEDGING_RE = re.compile(
    r"\b(?:may|might|could|suggests?|likely|possibly|potentially|hypothesize[sd]?|appears?\s+to|indicate[sd]?\s+possible)\b|(?:可能|推测|或许)",
    re.IGNORECASE,
)
XREF_RE = re.compile(r"\b(?:Table|Tab\.?|Figure|Fig\.?|Eq\.?)\s*[A-Za-z]?\d+(?:\.\d+)?|(?:表|图)\s*\d+", re.IGNORECASE)


def load_relation_vocabulary() -> Dict[str, Dict[str, Any]]:
    """Load controlled scientific predicates from schemas/relation_vocabulary.yaml."""
    vocab_path = SKILL_ROOT / "schemas" / "relation_vocabulary.yaml"
    data = yaml.safe_load(vocab_path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in data.get("relations", [])}


def _file_sha256(path_str: str) -> str:
    p = Path(path_str)
    if not p.exists() or not p.is_file():
        return "not_reported"
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_source_metadata(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Extract bibliographic & provenance identifiers from parsed document.json."""
    src_file = doc.get("source_file", "unknown")
    meta = doc.get("metadata", {})
    blocks = doc.get("blocks", [])
    hf = doc.get("headers_footers", [])
    marg = doc.get("marginalia", [])

    # Search frontmatter, headers/footers, marginalia for DOI, arXiv, and year
    probe_chunks = [meta.get("title") or "", meta.get("abstract") or ""]
    probe_chunks.extend(item.get("text", "") for item in hf)
    probe_chunks.extend(item.get("text", "") for item in marg)
    for b in blocks[:8]:
        probe_chunks.append(b.get("text", ""))
    probe_text = " ".join(probe_chunks)

    m_doi = DOI_RE.search(probe_text)
    doi = m_doi.group(1).rstrip(".,;)") if m_doi else "not_reported"

    m_arxiv = ARXIV_RE.search(probe_text)
    arxiv_id = m_arxiv.group(1) if m_arxiv else "not_reported"

    year_matches = YEAR_RE.findall(probe_text)
    year = year_matches[0] if year_matches else "not_reported"

    version = "not_reported"
    if arxiv_id != "not_reported":
        mv = re.search(r"(v\d+)$", arxiv_id)
        if mv:
            version = mv.group(1)
    if version == "not_reported" and doi != "not_reported":
        version = "published_doi"

    return {
        "paper_title": meta.get("title") or Path(src_file).stem,
        "authors": meta.get("authors") or ["not_reported"],
        "affiliations": meta.get("affiliations") or [],
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "version": version,
        "source_file": Path(src_file).name,
        "source_sha256": _file_sha256(src_file),
    }


def split_into_atomic_clauses(sentence: str) -> List[Tuple[str, List[str]]]:
    """Split compound/causal sentence into atomic clauses while propagating shared leading conditions."""
    s = re.sub(r"\s+", " ", sentence).strip()
    if not s:
        return []

    shared_conds: List[str] = []
    body = s
    m_lead = LEADING_COND_RE.match(s)
    if m_lead:
        lead_phrase = m_lead.group(1).strip()
        cond_vals = CONDITION_VAL_RE.findall(lead_phrase)
        shared_conds.extend(cond_vals if cond_vals else [lead_phrase])
        body = m_lead.group(2).strip()

    raw_clauses = [c.strip() for c in CLAUSE_SPLIT_RE.split(body) if c.strip()]
    if not raw_clauses:
        raw_clauses = [body]

    results: List[Tuple[str, List[str]]] = []
    for cl in raw_clauses:
        cl_clean = LEADING_CONNECTOR_RE.sub("", cl).strip().rstrip(";,")
        if len(cl_clean) < 10:
            continue
        conds = list(shared_conds)
        for cv in CONDITION_VAL_RE.findall(cl_clean):
            if cv not in conds:
                conds.append(cv)
        results.append((cl_clean, conds))
    return results


def _match_clause_to_triple(
    clause: str, vocab: Dict[str, Dict[str, Any]]
) -> Optional[Tuple[str, str, str]]:
    """Map an atomic clause to (subject, controlled_predicate, object)."""
    # Strip trailing period for clean object extraction
    text = clause.strip().rstrip(".")

    # Ordered explicit patterns (most specific negation/restriction first)
    explicit_patterns: List[Tuple[str, re.Pattern[str]]] = [
        ("does_not_affect", re.compile(r"^(.{2,70}?)\s+(?:does\s+not\s+affect|has\s+no\s+(?:significant\s+)?effect\s+on|shows?\s+negligible\s+impact\s+on)\s+(.{2,90})$", re.IGNORECASE)),
        ("fails_under", re.compile(r"^(.{2,70}?)\s+(?:fails?\s+under|degrades?\s+under|loses\s+activity\s+under)\s+(.{2,90})$", re.IGNORECASE)),
        ("restricted_to", re.compile(r"^(.{2,70}?)\s+(?:is\s+restricted\s+to|only\s+valid\s+under|applies\s+only\s+to)\s+(.{2,90})$", re.IGNORECASE)),
        ("inhibits", re.compile(r"^(.{2,70}?)\s+(?:inhibits?|blocks?|poisons?|deactivates?|suppresses?)\s+(.{2,90})$", re.IGNORECASE)),
        ("decreases", re.compile(r"^(.{2,70}?)\s+(?:decreases?|reduces?|lowers?|diminishes?|attenuates?)\s+(.{2,90})$", re.IGNORECASE)),
        ("catalyzes", re.compile(r"^(.{2,70}?)\s+(?:catalyzes?|catalyses?|mediates?)\s+(.{2,90})$", re.IGNORECASE)),
        ("increases", re.compile(r"^(.{2,70}?)\s+(?:increases?|enhances?|elevates?|boosts?|promotes?|raises?|accelerates?)\s+(.{2,90})$", re.IGNORECASE)),
        ("improves", re.compile(r"^(.{2,70}?)\s+(?:improves?|stabilizes?)\s+(.{2,90})$", re.IGNORECASE)),
        ("outperforms", re.compile(r"^(.{2,70}?)\s+(?:outperforms?|surpasses?|exceeds?)\s+(.{2,90})$", re.IGNORECASE)),
        ("causes", re.compile(r"^(.{2,70}?)\s+(?:may\s+induce|might\s+induce|causes?|induces?|leads?\s+to|results?\s+in|triggers?|drives?)\s+(.{2,90})$", re.IGNORECASE)),
        ("achieves_metric", re.compile(r"^(.{2,70}?)\s+(?:achieves?|reaches?|exhibits?|yields?|delivers?)\s+(.{2,90})$", re.IGNORECASE)),
        ("correlates_with", re.compile(r"^(.{2,70}?)\s+(?:correlates?\s+with|scales\s+linearly\s+with|is\s+proportional\s+to)\s+(.{2,90})$", re.IGNORECASE)),
        ("associates_with", re.compile(r"^(.{2,70}?)\s+(?:is\s+associated\s+with|co-occurs\s+with)\s+(.{2,90})$", re.IGNORECASE)),
        ("requires_condition", re.compile(r"^(.{2,70}?)\s+(?:requires?|depends\s+on)\s+(.{2,90})$", re.IGNORECASE)),
        ("composed_of", re.compile(r"^(.{2,70}?)\s+(?:is\s+composed\s+of|consists?\s+of)\s+(.{2,90})$", re.IGNORECASE)),
        ("measured_at", re.compile(r"^(.{2,70}?)\s+(?:was\s+measured\s+at|were\s+measured\s+at|operated\s+at|tested\s+at)\s+(.{2,90})$", re.IGNORECASE)),
    ]

    for pred_id, pat in explicit_patterns:
        if pred_id not in vocab:
            continue
        m = pat.match(text)
        if m:
            subj = re.sub(r"^(?:that|which)\s+", "", m.group(1).strip(), flags=re.IGNORECASE).strip(" ,;:")
            obj = m.group(2).strip(" ,;:")
            # Guard against pronouns or overly long multi-clause subjects
            if not subj or not obj or subj.lower() in ("it", "this", "they", "these", "which", "that"):
                continue
            if len(subj.split()) > 10:
                subj = " ".join(subj.split()[-6:])
            return subj, pred_id, obj

    # Check explicit Chinese trigger patterns
    zh_patterns: List[Tuple[str, re.Pattern[str]]] = [
        ("does_not_affect", re.compile(r"^(.{2,35}?)(?:不影响|无显著影响)(.{2,45})$")),
        ("inhibits", re.compile(r"^(.{2,35}?)(?:抑制|阻断)(.{2,45})$")),
        ("decreases", re.compile(r"^(.{2,35}?)(?:降低了?|减少了?)(.{2,45})$")),
        ("increases", re.compile(r"^(.{2,35}?)(?:提高了?|增加了?|促进了?|增强了?)(.{2,45})$")),
        ("catalyzes", re.compile(r"^(.{2,35}?)(?:催化|介导)(.{2,45})$")),
        ("causes", re.compile(r"^(.{2,35}?)(?:导致了?|引起了?|诱发了?)(.{2,45})$")),
        ("achieves_metric", re.compile(r"^(.{2,35}?)(?:达到了?|实现了?)(.{2,45})$")),
    ]
    for pred_id, pat in zh_patterns:
        if pred_id not in vocab:
            continue
        m = pat.match(text)
        if m:
            return m.group(1).strip(), pred_id, m.group(2).strip()

    return None


def _classify_evidence_type(sentence: str) -> str:
    if CITED_PRIOR_RE.search(sentence):
        return "cited_prior_work"
    if HEDGING_RE.search(sentence):
        return "indirect_inference"
    if XREF_RE.search(sentence):
        return "direct_tabular_or_figure"
    return "direct_experimental"


def _make_nanopub_uri(subj: str, pred: str, obj: str, conds: Sequence[str], src_sha: str) -> str:
    norm = f"{subj.lower().strip()}|{pred}|{obj.lower().strip()}|{'|'.join(sorted(c.lower().strip() for c in conds))}|{src_sha[:12]}"
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
    return f"urn:nanopub:sha256:{digest}"


def _build_atomic_statement(subj: str, pred_label: str, obj: str, conds: Sequence[str]) -> str:
    cond_prefix = f"Under {', '.join(conds)}, " if conds else ""
    stmt = f"{cond_prefix}{subj} {pred_label} {obj}."
    return stmt[:275]


def decompose_document(doc: Dict[str, Any], vocab: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Extract atomic 4-graph Nanopublications from one parsed document.json."""
    src_meta = extract_source_metadata(doc)
    diag = doc.get("diagnostics", {})
    status = doc.get("status", "partial")
    uncertainties = list(doc.get("uncertainties", []))

    if status == "refused" or diag.get("encrypted") or diag.get("corrupt"):
        return {
            "schema_version": "1.0.0",
            "status": "refused",
            "source_metadata": src_meta,
            "nanopub_count": 0,
            "nanopublications": [],
            "deduplication_log": [],
            "conflicts": [],
            "uncertainties": uncertainties,
        }

    if status == "unverifiable":
        return {
            "schema_version": "1.0.0",
            "status": "unverifiable",
            "source_metadata": src_meta,
            "nanopub_count": 0,
            "nanopublications": [],
            "deduplication_log": [],
            "conflicts": [],
            "uncertainties": uncertainties,
        }

    created_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    creator = "reading-14-nanopublication/1.0.0"

    raw_nps: List[Dict[str, Any]] = []

    # 1. Decompose prose blocks
    for blk in doc.get("blocks", []):
        if blk.get("role") in ("header", "footer", "title", "authors", "affiliation", "keywords", "table_ref", "reference"):
            continue
        txt = (blk.get("structured_text") or blk.get("text") or "").strip()
        if not txt:
            continue

        sentences = SENTENCE_SPLIT_RE.split(txt)
        for sent in sentences:
            s_clean = sent.strip()
            if len(s_clean) < 16:
                continue
            ev_type = _classify_evidence_type(s_clean)
            xrefs = [m.group(0) for m in XREF_RE.finditer(s_clean)]
            clauses = split_into_atomic_clauses(s_clean)

            for clause, conds in clauses:
                matched = _match_clause_to_triple(clause, vocab)
                if matched is None:
                    continue
                subj, pred_id, obj = matched
                pred_info = vocab[pred_id]
                np_uri = _make_nanopub_uri(subj, pred_id, obj, conds, src_meta["source_sha256"])

                conf = 0.90 if ev_type in ("direct_experimental", "direct_tabular_or_figure") else (0.72 if ev_type == "indirect_inference" else 0.78)
                basis = f"{ev_type} grounded in {blk['block_id']} (p.{blk['page']}); controlled predicate '{pred_id}'."
                if conds:
                    basis += f" Conditions preserved: {', '.join(conds)}."

                ev_entry = {
                    "pdf_page": blk["page"],
                    "printed_page": blk.get("printed_page"),
                    "section_id": blk.get("section_id"),
                    "block_id": blk["block_id"],
                    "bbox": blk["bbox"],
                    "quote_snippet": s_clean[:260],
                }
                raw_nps.append(
                    {
                        "nanopub_uri": np_uri,
                        "head": {
                            "assertion_graph": f"{np_uri}#assertion",
                            "provenance_graph": f"{np_uri}#provenance",
                            "pubinfo_graph": f"{np_uri}#pubinfo",
                        },
                        "assertion": {
                            "subject": subj,
                            "predicate": pred_id,
                            "predicate_uri": pred_info["uri"],
                            "object": obj,
                            "conditions": conds,
                            "atomic_statement": _build_atomic_statement(subj, pred_id.replace("_", " "), obj, conds),
                        },
                        "provenance": {
                            "evidence_type": ev_type,
                            "minimal_evidence_snippet": s_clean[:260],
                            "location": {
                                "pdf_page": blk["page"],
                                "printed_page": blk.get("printed_page"),
                                "section_id": blk.get("section_id"),
                                "block_id": blk["block_id"],
                                "bbox": blk["bbox"],
                            },
                            "cross_element_refs": xrefs,
                            "source_metadata": src_meta,
                            "supporting_evidences": [ev_entry],
                        },
                        "pubinfo": {
                            "created_at": created_at,
                            "creator": creator,
                            "version": "1.0",
                            "confidence_score": conf,
                            "confidence_basis": basis,
                        },
                    }
                )

    # 2. Extract tabular Nanopublications from structured tables
    for tab in doc.get("tables", []):
        if tab.get("is_image_table") or not tab.get("records"):
            continue
        col_paths = tab.get("col_paths", [])
        if len(col_paths) < 2:
            continue
        entity_col = col_paths[0]
        t_page = tab["pages"][0] if tab.get("pages") else 1
        t_cap = tab.get("caption") or tab.get("label") or tab["table_id"]

        for rec in tab["records"]:
            subj = str(rec.get(entity_col, "")).strip()
            if not subj:
                continue
            for metric_col in col_paths[1:]:
                val = str(rec.get(metric_col, "")).strip()
                if not val:
                    continue
                pred_id = "achieves_metric" if re.search(r"\d", val) else "has_property"
                pred_info = vocab[pred_id]
                obj = f"{metric_col} = {val}"
                conds = [tab.get("label") or tab["table_id"]]
                np_uri = _make_nanopub_uri(subj, pred_id, obj, conds, src_meta["source_sha256"])
                snip = f"{t_cap} | {entity_col}={subj}, {metric_col}={val}"[:260]
                ev_entry = {
                    "pdf_page": t_page,
                    "printed_page": None,
                    "section_id": tab.get("section_id"),
                    "table_id": tab["table_id"],
                    "bbox": tab["bbox"],
                    "quote_snippet": snip,
                }
                raw_nps.append(
                    {
                        "nanopub_uri": np_uri,
                        "head": {
                            "assertion_graph": f"{np_uri}#assertion",
                            "provenance_graph": f"{np_uri}#provenance",
                            "pubinfo_graph": f"{np_uri}#pubinfo",
                        },
                        "assertion": {
                            "subject": subj,
                            "predicate": pred_id,
                            "predicate_uri": pred_info["uri"],
                            "object": obj,
                            "conditions": conds,
                            "atomic_statement": _build_atomic_statement(subj, pred_id.replace("_", " "), obj, conds),
                        },
                        "provenance": {
                            "evidence_type": "direct_tabular_or_figure",
                            "minimal_evidence_snippet": snip,
                            "location": {
                                "pdf_page": t_page,
                                "printed_page": None,
                                "section_id": tab.get("section_id"),
                                "table_id": tab["table_id"],
                                "bbox": tab["bbox"],
                            },
                            "cross_element_refs": [tab.get("label") or tab["table_id"]],
                            "source_metadata": src_meta,
                            "supporting_evidences": [ev_entry],
                        },
                        "pubinfo": {
                            "created_at": created_at,
                            "creator": creator,
                            "version": "1.0",
                            "confidence_score": 0.94,
                            "confidence_basis": f"direct_tabular_or_figure cell extracted from {tab['table_id']} (pages {tab['pages']}).",
                        },
                    }
                )

    # 3. Deterministic Deduplication by nanopub_uri
    dedup_map: Dict[str, Dict[str, Any]] = {}
    dedup_log: List[Dict[str, Any]] = []
    for np_obj in raw_nps:
        uri = np_obj["nanopub_uri"]
        if uri in dedup_map:
            existing = dedup_map[uri]
            new_ev = np_obj["provenance"]["supporting_evidences"][0]
            existing["provenance"]["supporting_evidences"].append(new_ev)
            dedup_log.append(
                {
                    "nanopub_uri": uri,
                    "merged_location": new_ev,
                    "reason": "Identical normalized (subject, predicate, object, conditions) merged across locations.",
                }
            )
        else:
            dedup_map[uri] = np_obj

    final_nps: List[Dict[str, Any]] = []
    for idx, np_obj in enumerate(dedup_map.values(), start=1):
        np_obj["nanopub_id"] = f"np_{idx}"
        final_nps.append(np_obj)

    # 4. Cross-Assertion Conflict Detection
    conflicts: List[Dict[str, Any]] = []
    by_so: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for np_obj in final_nps:
        a = np_obj["assertion"]
        key = (re.sub(r"\s+", " ", a["subject"].lower().strip()), re.sub(r"\s+", " ", a["object"].lower().strip()))
        by_so.setdefault(key, []).append(np_obj)

    c_seq = 0
    for (subj_k, obj_k), group in by_so.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                p1 = group[i]["assertion"]["predicate"]
                p2 = group[j]["assertion"]["predicate"]
                if p2 in vocab.get(p1, {}).get("contradicts", []) or p1 in vocab.get(p2, {}).get("contradicts", []):
                    c_seq += 1
                    conflicts.append(
                        {
                            "conflict_id": f"np_conf_{c_seq}",
                            "code": "cross_assertion_conflict",
                            "subject": group[i]["assertion"]["subject"],
                            "object": group[i]["assertion"]["object"],
                            "nanopub_ids": [group[i]["nanopub_id"], group[j]["nanopub_id"]],
                            "predicates": [p1, p2],
                            "locations": [group[i]["provenance"]["location"], group[j]["provenance"]["location"]],
                            "message": f"Conflicting predicates '{p1}' vs '{p2}' for ({subj_k}, {obj_k}).",
                        }
                    )

    if not final_nps:
        uncertainties.append(
            {
                "code": "no_assertions_extracted",
                "severity": "medium",
                "pages": [],
                "message": "No structured claims matched the controlled predicate patterns; manual or LLM refinement recommended.",
            }
        )

    final_status = "partial" if uncertainties else "success"
    return {
        "schema_version": "1.0.0",
        "status": final_status,
        "source_metadata": src_meta,
        "nanopub_count": len(final_nps),
        "nanopublications": final_nps,
        "deduplication_log": dedup_log,
        "conflicts": conflicts,
        "uncertainties": uncertainties,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Decompose ingested documents into 4-graph Nanopublications.")
    parser.add_argument("--manifest", required=True, help="Path to ingest_manifest.json.")
    parser.add_argument("--out", required=True, help="Output directory for nanopubs.json.")
    args = parser.parse_args()

    vocab = load_relation_vocabulary()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    papers = manifest.get("papers", [])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not papers:
        bundle = {
            "schema_version": "1.0.0",
            "status": "unverifiable",
            "source_metadata": {},
            "nanopub_count": 0,
            "nanopublications": [],
            "deduplication_log": [],
            "conflicts": [],
            "uncertainties": [{"code": "no_input_papers", "severity": "critical", "pages": [], "message": "No input documents ingested."}],
        }
    else:
        # Process primary paper (or merge across papers if multiple)
        all_nps: List[Dict[str, Any]] = []
        all_dedup: List[Dict[str, Any]] = []
        all_conf: List[Dict[str, Any]] = []
        all_unc: List[Dict[str, Any]] = []
        statuses = set()
        primary_meta: Dict[str, Any] = {}

        for idx, item in enumerate(papers):
            doc = json.loads(Path(item["document_json"]).read_text(encoding="utf-8"))
            res = decompose_document(doc, vocab)
            if idx == 0:
                primary_meta = res["source_metadata"]
            statuses.add(res["status"])
            all_nps.extend(res["nanopublications"])
            all_dedup.extend(res["deduplication_log"])
            all_conf.extend(res["conflicts"])
            all_unc.extend(res["uncertainties"])

        for seq, np_obj in enumerate(all_nps, start=1):
            np_obj["nanopub_id"] = f"np_{seq}"

        if statuses == {"refused"}:
            st = "refused"
        elif statuses == {"unverifiable"}:
            st = "unverifiable"
        elif statuses == {"success"} and not all_unc:
            st = "success"
        else:
            st = "partial"

        bundle = {
            "schema_version": "1.0.0",
            "status": st,
            "source_metadata": primary_meta,
            "nanopub_count": len(all_nps),
            "nanopublications": all_nps,
            "deduplication_log": all_dedup,
            "conflicts": all_conf,
            "uncertainties": all_unc,
        }

    write_json_atomic(out_dir / "nanopubs.json", bundle)
    print(json.dumps({"status": bundle["status"], "nanopub_count": bundle["nanopub_count"], "conflicts": len(bundle["conflicts"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
