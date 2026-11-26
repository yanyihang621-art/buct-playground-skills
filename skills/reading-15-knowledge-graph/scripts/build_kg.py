#!/usr/bin/env python3
"""Build evidence-grounded multi-paper Knowledge Graph with conservative entity alignment, do-not-merge records, and incremental updates."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
INSTANCE_CONCEPTS = {"Material", "Method", "Model", "Dataset", "Instrument"}
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic
from evidence_claims import canon as _canon_key, read_definitions, local_aliases, read_reviewed_claims, split_object_context

SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\(])")
CONDITION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|MPa|kPa|bar|mL\s*g-1\s*h-1|wt%|mol%|h|mA\s*cm-2)\b",
    re.IGNORECASE,
)
LEADING_COND_RE = re.compile(r"^((?:At|Under|In|For|When)\s+[^,;]{3,65}?),\s+(.+)$", re.IGNORECASE)
CITED_RE = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]|\b(?:previous|prior|earlier)\s+(?:studies|work|research)\b|\bet\s+al\.", re.IGNORECASE)
SPECULATIVE_RE = re.compile(r"\b(?:may|might|could|suggests?|likely|possibly|hypothesize)\b|(?:可能|推测)", re.IGNORECASE)

def _pattern(verb, passive=False):
    left, right = ("object", "subject") if passive else ("subject", "object")
    return re.compile(rf"^(?P<{left}>.{{2,120}}?)\s+(?:{verb})\s+(?P<{right}>.{{1,160}})$", re.I)


# In explicit "Y was reduced by X" constructions X is the agent. A magnitude
# following "by" is a change in the measurement, never an agent.
RELATION_PATTERNS = []
for relation, verbs, participles, subject_type, object_type in (
    ("inhibits", r"inhibits?|inhibited|blocks?|blocked|poisons?|poisoned|suppress(?:es|ed)?", "inhibited|blocked|poisoned|suppressed", "Material", "Metric"),
    ("decreases", r"decreas(?:e[sd]?)|reduc(?:e[sd]?)|lowers?|lowered", "decreased|reduced|lowered", "Material", "Metric"),
    ("increases", r"increas(?:e[sd]?)|enhanc(?:e[sd]?)|boosts?|boosted|promot(?:e[sd]?)|improv(?:e[sd]?)", "increased|enhanced|boosted|promoted|improved", "Material", "Metric"),
    ("catalyzes", r"cataly[sz]e[sd]?|mediate[sd]?", "catalyzed|catalysed|mediated", "Material", "Task"),
    ("outperforms", r"outperforms?|outperformed|surpass(?:es|ed)?|exceeds?|exceeded", "outperformed|surpassed|exceeded", "Method", "Method"),
    ("uses", r"use[sd]?|employs?|employed|utili[sz]e[sd]?|adopts?|adopted", "used|employed|utilized|utilised|adopted", "Method", "Material"),
    ("supports", r"supports?|supported", "supported", "Finding", "Finding"),
    ("contradicts", r"contradicts?|contradicted", "contradicted", "Finding", "Finding"),
):
    RELATION_PATTERNS.append((relation, _pattern(rf"(?:was|were|is|are)\s+(?:{participles})\s+by", True), subject_type, object_type))
    RELATION_PATTERNS.append((relation, _pattern(verbs), subject_type, object_type))
RELATION_PATTERNS.extend([
    ("evaluates_on", _pattern(r"(?:(?:is|are|was|were)\s+)?(?:evaluated|tested)\s+on|evaluates?\s+on"), "Method", "Task"),
    ("compares_with", _pattern(r"(?:is|are|was|were)\s+compared\s+(?:with|to)"), "Method", "Method"),
    ("achieves", _pattern(r"achieve[sd]?|reach(?:es|ed)?|yields?|yielded|delivers?|delivered|exhibits?|exhibited"), "Material", "Metric"),
    ("restricted_to", _pattern(r"(?:is|was)\s+restricted\s+to|only\s+valid\s+under|applies\s+only\s+to"), "Material", "Condition"),
])
NEGATED = re.compile(r"\b(?:no|not|never|cannot|without|failed\s+to|neither|lack(?:s|ed)?\s+of)\b|n['’]t\b", re.I)
ATTRIBUTION = re.compile(r"^(?:(?:previous|prior|earlier)\s+(?:work|studies|research)|we|our (?:results|analysis)|the (?:authors|results))\s+(?:suggest(?:s|ed)?|report(?:s|ed)?|show(?:s|ed)?|found|observed)(?:\s+that)?\s+", re.I)
MODAL_END = re.compile(r"\s+(?:may|might|could|can|would|likely|possibly)$", re.I)
REPORTED_BOUNDARIES = (
    ("unreported", re.compile(r"\bnot\s+(?:reported|provided|available|specified|measured)\b|未(?:报告|提供|测量)", re.I)),
    ("inconclusive", re.compile(r"\b(?:inconclusive|uncertain|not\s+(?:statistically\s+)?significant|no\s+significant)\b|无法确定|无统计学意义", re.I)),
    ("negative_statement", re.compile(r"\b(?:did|does|was|were|could|is|are)\s+not\s+(?!only\b)|\bno\s+(?:evidence|effect|difference)\b", re.I)),
)

CLAUSE_SPLIT_RE = re.compile(r"(?:;\s*(?:moreover|furthermore|however)?,?\s*|,\s+(?:whereas|while|and\s+also)\s+|；\s*)", re.IGNORECASE)
CONNECTOR_STRIP_RE = re.compile(r"^(?:moreover|furthermore|however|whereas|while|therefore)[,\s]+", re.IGNORECASE)


def load_ontology() -> Dict[str, Any]:
    return yaml.safe_load((SKILL_ROOT / "schemas" / "kg_ontology.yaml").read_text(encoding="utf-8"))


# Evidence tiers follow the source sentence and stay attached to the directed edge.


def _evidence_strength(sentence: str) -> str:
    if CITED_RE.search(sentence):
        return "prior_work_citation"
    if SPECULATIVE_RE.search(sentence):
        return "indirect_speculation"
    return "direct_author_evidence"


def _mention_type(label, role_default):
    # Prefer explicit noun heads over the usual type suggested by the predicate.
    for entity_type, head in (
        ("Dataset", r"(?:data\s?set|corpus|(?:validation|training|test|calibration) set)s?"),
        ("Model", r"(?:model|classifier|regressor)s?"),
        ("Instrument", r"(?:instrument|sensor|detector|camera|photometer|spectrometer|microscope)s?"),
        ("Method", r"(?:method|algorithm|protocol|procedure)s?"),
        ("Metric", r"(?:metric|accuracy|precision|latency|variance|error)"),
    ):
        if re.search(rf"\b{head}$", label, re.I):
            return entity_type
    return role_default


def make_review_plan(corpus, kg, per_source=10):
    """Show the agent original paragraphs, source by source, with draft claims to verify."""
    nodes = {node["node_id"]: node["label"] for node in kg["nodes"]}
    by_paragraph = {}
    for edge in kg["edges"]:
        if edge["relation_type"] != "instance_of":
            by_paragraph.setdefault((edge["source_id"], edge["paragraph_id"]), []).append({
                "subject": nodes[edge["source_node"]], "relation": edge["relation_type"],
                "object": nodes[edge["target_node"]], "evidence_strength": edge["evidence_strength"]})

    plans = []
    for paper in corpus.get("papers", []):
        sid = paper["source_id"]
        sections = {}
        anchored = 0
        for paragraph in paper.get("paragraphs", []):
            pid = paragraph["paragraph_id"]
            section = paragraph.get("section_title") or "Body"
            text = paragraph["text"]
            candidates = by_paragraph.get((sid, pid), [])
            anchored += bool(candidates)
            priority = 0
            if re.search(r"result|finding|experiment|method|conclu|limitation|结果|方法|讨论|结论", section, re.I):
                priority += 3
            if re.search(r"\d(?:\.\d+)?\s*(?:%|nm|ms|Hz|dB)\b|\b(?:measured|compared|found|observed|uncertain)\b", text, re.I):
                priority += 2
            if not candidates:
                priority += 1
            row = {"paragraph_id": pid, "section": section, "location_type": paragraph["location_type"],
                   "text": text, "candidate_relations": candidates}
            sections.setdefault(section, []).append((priority, row))
        # Rotate across sections so lengthy introductions cannot crowd out methods or results.
        ordered = [sorted(rows, key=lambda pair: -pair[0]) for rows in sections.values()]
        ordered.sort(key=lambda rows: -rows[0][0])
        selected = []
        for index in range(max((len(rows) for rows in ordered), default=0)):
            for rows in ordered:
                if index < len(rows) and len(selected) < per_source:
                    selected.append(rows[index][1])
            if len(selected) >= per_source:
                break
        plans.append({"source_id": sid, "source_file": paper["source_file"], "paragraph_count": len(paper.get("paragraphs", [])),
                      "paragraphs_with_candidate_relations": anchored, "selected_for_review": selected,
                      "section_index": {name: [row["paragraph_id"] for _, row in rows] for name, rows in sections.items()}})
    return {"sources": plans}


def build_kg(corpus: Dict[str, Any], base_kg: Optional[Dict[str, Any]] = None,
             reviewed_claims: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Construct or incrementally update an evidence-grounded multi-paper Knowledge Graph."""
    ontology = load_ontology()
    rel_meta = {r["id"]: r for r in ontology.get("relation_types", [])}

    papers = sorted(corpus.get("papers", []), key=lambda paper: paper["source_id"])
    reviewed = read_reviewed_claims(reviewed_claims, papers, ontology)
    review_only = (reviewed_claims or {}).get("mode") == "reviewed_only"
    if review_only and base_kg:
        raise ValueError("For a reviewed-only graph, rebuild from the complete corpus without --base-kg.")
    base_kg = copy.deepcopy(base_kg) if base_kg else base_kg
    uncertainties: List[Dict[str, Any]] = []
    for p in papers:
        uncertainties.extend(p.get("uncertainties", []))

    statuses = {p.get("status", "partial") for p in papers}
    # Preserve the existing graph and manifest even if an incremental source is unreadable.

    # Initialize from base_kg if incremental update
    nodes: List[Dict[str, Any]] = [dict(n) for n in base_kg.get("nodes", [])] if base_kg else []
    edges: List[Dict[str, Any]] = [dict(e) for e in base_kg.get("edges", [])] if base_kg else []
    merge_decisions: List[Dict[str, Any]] = []
    do_not_merge: List[Dict[str, Any]] = list(base_kg.get("do_not_merge_records", [])) if base_kg else []
    # Recompute derived decisions from the union of old and new nodes and edges.

    preserved_node_ids = {n["node_id"] for n in nodes}
    node_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for n in nodes:
        node_by_key[(n["canonical_key"], n["entity_type"])] = n

    next_node_num = max([int(re.sub(r"\D", "", n["node_id"]) or 0) for n in nodes] + [0]) + 1
    next_edge_num = max([int(re.sub(r"\D", "", e["edge_id"]) or 0) for e in edges] + [0]) + 1

    added_nodes_count = 0
    added_edges_count = 0
    merged_aliases_count = 0

    def _get_or_create_node(
        label: str,
        entity_type: str,
        source_id: str,
        anchor: Dict[str, Any],
        canonical: Optional[str] = None,
    ) -> Dict[str, Any]:
        nonlocal next_node_num, added_nodes_count, merged_aliases_count
        clean_lbl = re.sub(r"\s+", " ", label).strip(" .,;:")
        ckey = canonical or _canon_key(clean_lbl)
        existing = node_by_key.get((ckey, entity_type))

        if existing is not None:
            if clean_lbl not in existing["aliases"] and clean_lbl != existing["label"]:
                existing["aliases"].append(clean_lbl)
                merged_aliases_count += 1
            if source_id not in existing["source_ids"]:
                existing["source_ids"].append(source_id)
            if not any(a.get("source_id") == source_id and a.get("paragraph_id") == anchor.get("paragraph_id") for a in existing["evidence_anchors"]):
                existing["evidence_anchors"].append(anchor)
            return existing

        nid = f"n_{next_node_num}"
        next_node_num += 1
        added_nodes_count += 1
        node = {
            "node_id": nid,
            "label": clean_lbl,
            "canonical_key": ckey,
            "entity_type": entity_type,
            "aliases": [clean_lbl],
            "source_ids": [source_id],
            "evidence_anchors": [anchor],
        }
        nodes.append(node)
        node_by_key[(ckey, entity_type)] = node
        return node

    definitions = read_definitions(papers, base_kg)
    aliases, all_forms = local_aliases(definitions)

    def concept_key(label, sid):
        raw = _canon_key(label)
        # The declaration itself and a later short form resolve to the same local concept.
        declaration = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", label.strip())
        if declaration and aliases.get(sid, {}).get(_canon_key(declaration[2])) == _canon_key(declaration[1]):
            return _canon_key(declaration[1])
        resolved = aliases.get(sid, {}).get(raw)
        if resolved:
            return resolved
        # Unexpanded abbreviations cannot borrow another paper's definition.
        if raw in all_forms or re.fullmatch(r"[A-Z][A-Z0-9-]{1,12}", label.strip()):
            return f"{raw} [{sid}]"
        return raw

    def entity_node(label, entity_type, sid, anchor):
        key = concept_key(label, sid)
        if entity_type.endswith("Instance") or entity_type in {"Condition", "Researcher", "Finding"} or re.search(r"\d\s*%", label):
            key = f"{key} [{sid}]"
        node = _get_or_create_node(label, entity_type, sid, anchor, key)
        for definition in definitions_by_source.get((sid, key), []):
            if definition["full"] not in node["aliases"]:
                node["aliases"].append(definition["full"])
            if node["label"] == definition["short"]:
                node["label"] = definition["full"]
            if definition["short"] not in node["aliases"]:
                node["aliases"].append(definition["short"])
            definition_anchor = {k: definition[k] for k in ("source_id", "paragraph_id", "location_type", "pdf_page", "quote_snippet")}
            if definition_anchor not in node["evidence_anchors"]:
                node["evidence_anchors"].append(definition_anchor)
        return node

    definitions_by_source = {}
    for definition in definitions:
        definitions_by_source.setdefault((definition["source_id"], _canon_key(definition["full"])), []).append(definition)

    boundary_by_anchor = {(b["source_id"], b["paragraph_id"], b["quote_snippet"]): b
                          for b in (base_kg or {}).get("boundary_records", [])}
    # Extract per-paper nodes and edges
    raw_claims_by_paper: Dict[str, List[Dict[str, Any]]] = {}

    for paper in papers:
        sid = paper["source_id"]
        if paper.get("status") in ("refused", "unverifiable"):
            continue
        paras = paper.get("paragraphs", [])
        if not paras:
            continue

        # Authors remain in the per-paper bibliographic manifest. A body paragraph
        # that never names its authors cannot serve as evidence for an author node.

        paper_claims: List[Dict[str, Any]] = []
        for para in paras:
            pid = para["paragraph_id"]
            ltype = para["location_type"]
            pdf_p = para.get("pdf_page")
            for sentence in SENT_SPLIT_RE.split(para["text"]):
                for clause in CLAUSE_SPLIT_RE.split(sentence):
                    clause = clause.strip()
                    category = next((kind for kind, pattern in REPORTED_BOUNDARIES if pattern.search(clause)), None)
                    if category:
                        key = sid, pid, clause
                        boundary_by_anchor[key] = {"source_id": sid, "paragraph_id": pid, "quote_snippet": clause,
                                                   "location_type": ltype, "pdf_page": pdf_p, "category": category}
            if (sid, pid) in reviewed:
                paper_claims.extend(reviewed[(sid, pid)])
                continue
            if review_only:
                continue
            txt = para["text"].strip()
            if not txt:
                continue

            for sent in SENT_SPLIT_RE.split(txt):
                s_clean = sent.strip()
                if len(s_clean) < 16:
                    continue
                shared_conds: List[str] = []
                body = s_clean
                m_lead = LEADING_COND_RE.match(s_clean)
                if m_lead:
                    lead_c = CONDITION_RE.findall(m_lead.group(1))
                    shared_conds.extend(lead_c if lead_c else [m_lead.group(1).strip()])
                    body = m_lead.group(2).strip()

                for cl in CLAUSE_SPLIT_RE.split(body):
                    cl_clean = CONNECTOR_STRIP_RE.sub("", cl).strip().rstrip(".,;")
                    if len(cl_clean) < 10:
                        continue
                    evidence_text = s_clean if shared_conds or cl_clean not in s_clean else cl_clean
                    ev_str = _evidence_strength(cl_clean)
                    clause = ATTRIBUTION.sub("", cl_clean)
                    if NEGATED.search(clause) or re.match(r"^(?:if|whether)\b", clause, re.I):
                        continue
                    conds = list(shared_conds)
                    for cv in CONDITION_RE.findall(cl_clean):
                        if cv not in conds:
                            conds.append(cv)

                    for rtype, pat, def_st, def_ot in RELATION_PATTERNS:
                        m = pat.match(clause)
                        if not m:
                            continue
                        subj_raw = MODAL_END.sub("", m["subject"].strip(" ,;:"))
                        obj_raw, condition, purpose = split_object_context(m["object"].strip(" ,;:"), rtype)
                        if condition:
                            conds = [c for c in conds if c not in condition]
                            conds.append(condition)
                        if (not subj_raw or not obj_raw or subj_raw.lower() in ("it", "this", "they", "these", "we", "i")
                                or len(subj_raw.split()) > 12 or re.search(r"\b(?:is|are|was|were|been)$", subj_raw, re.I)
                                or re.match(r"^[~≈<>+−-]?\d+(?:\.\d+)?(?:\s*%|\b)", subj_raw)
                                or re.match(r"^(?:by|to|for|as)\b", obj_raw, re.I)):
                            continue

                        anchor = {
                            "source_id": sid,
                            "paragraph_id": pid,
                            "location_type": ltype,
                            "pdf_page": pdf_p,
                            "quote_snippet": evidence_text,
                        }
                        paper_claims.append(
                            {
                                "subj_raw": subj_raw,
                                "subj_type": _mention_type(subj_raw, def_st),
                                "rel_type": rtype,
                                "obj_raw": obj_raw,
                                "obj_type": _mention_type(obj_raw, def_ot),
                                "conds": conds,
                                "ev_strength": ev_str,
                                "purpose": purpose,
                                "anchor": anchor,
                            }
                        )
                        break

        # Also extract tabular edges if tables exist (PDF inputs)
        for tab in ([] if review_only else paper.get("tables", [])):
            if tab.get("is_image_table") or not tab.get("records"):
                continue
            cols = tab.get("col_paths", [])
            if len(cols) < 2:
                continue
            ent_col = cols[0]
            t_page = tab["pages"][0] if tab.get("pages") else None
            for rec in tab["records"]:
                s_val = str(rec.get(ent_col, "")).strip()
                if not s_val:
                    continue
                for m_col in cols[1:]:
                    v = str(rec.get(m_col, "")).strip()
                    if not v:
                        continue
                    snip = f"{tab.get('caption') or tab.get('label')} | {ent_col}={s_val}, {m_col}={v}"[:240]
                    anchor = {
                        "source_id": sid,
                        "paragraph_id": tab["table_id"],
                        "location_type": "pdf_table",
                        "pdf_page": t_page,
                        "quote_snippet": snip,
                    }
                    paper_claims.append(
                        {
                            "subj_raw": s_val,
                            "subj_type": "Material",
                            "rel_type": "achieves",
                            "obj_raw": f"{m_col} = {v}",
                            "obj_type": "Metric",
                            "conds": [tab.get("label") or tab["table_id"]],
                            "ev_strength": "direct_tabular_evidence",
                            "anchor": anchor,
                        }
                    )

        raw_claims_by_paper[sid] = paper_claims

    # Identify generic Material/Method subjects appearing across >=2 distinct papers with different conditions/results -> DO NOT MERGE instances!
    # A shared material or method name establishes a concept, not identity of its samples.
    occurrences = {}
    for sid, claims in raw_claims_by_paper.items():
        for claim in claims:
            for role in ("subj", "obj"):
                kind = claim[role + "_type"]
                if kind in INSTANCE_CONCEPTS:
                    key = (concept_key(claim[role + "_raw"], sid), kind)
                    occurrences.setdefault(key, set()).add(sid)
    for n in nodes:
        if n["entity_type"] in INSTANCE_CONCEPTS:
            key = (n["canonical_key"], n["entity_type"])
            occurrences.setdefault(key, set()).update(n["source_ids"])
    split_keys = {key for key, sources in occurrences.items() if len(sources) > 1}
    do_not_merge_tracker = {r["generic_key"]: r for r in do_not_merge if "generic_key" in r}
    instance_links = {(e["source_node"], e["target_node"]) for e in edges if e["relation_type"] == "instance_of"}

    def as_instance(node, sid, anchor, conditions, strength):
        nonlocal next_edge_num, added_edges_count
        if (node["canonical_key"], node["entity_type"]) not in split_keys:
            return node
        canonical = node["canonical_key"] + f" [{sid}]"
        instance = _get_or_create_node(f"{node['label']} [{sid}]", node["entity_type"] + "Instance", sid, anchor, canonical)
        link = instance["node_id"], node["node_id"]
        if link not in instance_links:
            edges.append({**anchor, "edge_id": f"e_{next_edge_num}", "source_node": link[0],
                          "relation_type": "instance_of", "target_node": link[1],
                          "conditions": conditions, "evidence_strength": strength})
            next_edge_num += 1
            added_edges_count += 1
            instance_links.add(link)
        record_key = node["entity_type"] + ":" + node["canonical_key"]
        record = do_not_merge_tracker.setdefault(record_key, {
            "generic_key": record_key, "generic_term": node["label"], "generic_node_id": node["node_id"],
            "instance_nodes": [], "sources_and_conditions": {},
            "reason": "Shared terminology identifies a concept. Each paper retains a separate experimental instance.",
        })
        if instance["node_id"] not in record["instance_nodes"]:
            record["instance_nodes"].append(instance["node_id"])
        recorded = record["sources_and_conditions"].get(sid, [])
        record["sources_and_conditions"][sid] = sorted(set(recorded + (conditions or ["unspecified"])))
        return instance

    # In incremental runs a previously single-source concept can now require a split.
    base_nodes = {n["node_id"]: n for n in nodes}
    for edge in list(edges):
        if edge["relation_type"] == "instance_of":
            continue
        anchor = {k: edge[k] for k in ("source_id", "paragraph_id", "location_type", "pdf_page", "quote_snippet")}
        for endpoint in ("source_node", "target_node"):
            instance = as_instance(base_nodes[edge[endpoint]], edge["source_id"], anchor,
                                   edge.get("conditions", []), edge["evidence_strength"])
            edge[endpoint] = instance["node_id"]

    def edge_identity(edge):
        return (edge["source_node"], edge["relation_type"], edge["target_node"], edge["source_id"],
                edge["paragraph_id"], edge["quote_snippet"], tuple(edge["conditions"]))
    seen_edges = {edge_identity(e) for e in edges}
    for sid, claims in raw_claims_by_paper.items():
        for claim in claims:
            anchor = claim["anchor"]
            endpoints = []
            for role in ("subj", "obj"):
                node = entity_node(claim[role + "_raw"], claim[role + "_type"], sid, anchor)
                endpoints.append(as_instance(node, sid, anchor, claim["conds"], claim["ev_strength"])["node_id"])
            edge = {**anchor, "source_node": endpoints[0], "relation_type": claim["rel_type"],
                    "target_node": endpoints[1], "conditions": claim["conds"], "evidence_strength": claim["ev_strength"]}
            if claim.get("purpose"):
                edge["purpose"] = claim["purpose"]
            identity = edge_identity(edge)
            if identity not in seen_edges:
                edges.append({**edge, "edge_id": f"e_{next_edge_num}"})
                seen_edges.add(identity)
                next_edge_num += 1
                added_edges_count += 1

    for short, forms in all_forms.items():
        if len(forms) < 2:
            continue
        evidence = [r for r in definitions if _canon_key(r["short"]) == short]
        do_not_merge_tracker[f"abbreviation:{short}"] = {
            "generic_key": f"abbreviation:{short}", "generic_term": evidence[0]["short"],
            "instance_nodes": [n["node_id"] for n in nodes if n["canonical_key"] in forms],
            "definition_evidence": evidence,
            "reason": "The same abbreviation has different explicit expansions. Each mention retains its paper's definition.",
        }
    do_not_merge = list(do_not_merge_tracker.values())

    # Record explicit merge_decisions for canonical concepts with >1 alias or >1 source
    existing_merge_keys = {m.get("canonical_node_id") for m in merge_decisions}
    for n in nodes:
        if n["entity_type"].endswith("Instance") or n["entity_type"] == "Researcher":
            continue
        if (len(n["aliases"]) > 1 or len(n["source_ids"]) >= 2) and n["node_id"] not in existing_merge_keys:
            merge_decisions.append(
                {
                    "canonical_node_id": n["node_id"],
                    "canonical_label": n["label"],
                    "entity_type": n["entity_type"],
                    "merged_aliases": n["aliases"],
                    "source_ids": n["source_ids"],
                    "reason": "Acronym/case/abstract-concept alignment across verified text spans (experimental instances kept separate where applicable).",
                }
            )
            existing_merge_keys.add(n["node_id"])

    # Compare opposite predicates for the same concepts without equating distinct experiments.
    node_map = {n["node_id"]: n for n in nodes}
    parent = {e["source_node"]: e["target_node"] for e in edges if e["relation_type"] == "instance_of"}
    groups = {}
    conflicts = []
    for edge in edges:
        if edge["relation_type"] == "instance_of":
            continue
        endpoints = tuple(parent.get(edge[k], edge[k]) for k in ("source_node", "target_node"))
        group = groups.setdefault(endpoints, {})
        opposing = rel_meta.get(edge["relation_type"], {}).get("contradicts", [])
        for relation, previous in group.items():
            if relation not in opposing and edge["relation_type"] not in rel_meta.get(relation, {}).get("contradicts", []):
                continue
            for earlier in previous:
                same_context = earlier["source_id"] == edge["source_id"] and earlier["conditions"] == edge["conditions"]
                conflicts.append({
                    "conflict_id": f"kg_conf_{len(conflicts) + 1}", "edge_ids": [earlier["edge_id"], edge["edge_id"]],
                    "relations": [relation, edge["relation_type"]], "sources": [earlier["source_id"], edge["source_id"]],
                    "classification": "opposed_same_context" if same_context else "different_context_comparison",
                    "conditions": [earlier["conditions"], edge["conditions"]],
                    "message": f"Opposed predicates for {node_map[endpoints[0]]['label']} and {node_map[endpoints[1]]['label']}; inspect both source contexts.",
                })
        group.setdefault(edge["relation_type"], []).append(edge)

    substantive_sources = {e["source_id"] for e in edges if e["relation_type"] != "instance_of"}
    for paper in papers:
        sid = paper["source_id"]
        if sid not in substantive_sources:
            uncertainties.append({"code": "no_verified_relations", "source_id": sid,
                                  "message": "No substantive relationship was extracted for this paper; review its source paragraphs if available."})
    for missing in corpus.get("missing_input_files", []):
        uncertainties.append({"code": "missing_input_file", "message": str(missing)})

    # Merge sources_manifest preserving base_kg sources + new corpus sources
    manifest_map: Dict[str, Dict[str, Any]] = {}
    if base_kg:
        for sm in base_kg.get("sources_manifest", []):
            manifest_map[sm["source_id"]] = sm
    for p in papers:
        manifest_map[p["source_id"]] = {
            "source_id": p["source_id"],
            "source_file": p["source_file"],
            "source_sha256": p.get("source_sha256"),
            "title": p["title"],
            "authors": p["authors"],
            "doi": p["doi"],
            "year": p["year"],
            "license": p.get("license", "not_reported"),
            "location_type": p["location_type"],
        }

    if edges:
        status = "partial" if uncertainties or statuses - {"success"} else "success"
    elif statuses == {"refused"}:
        status = "refused"
    elif not papers or statuses <= {"unverifiable", "refused"}:
        status = "unverifiable"
    else:
        status = "partial"
    kg_out: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": status,
        "sources_manifest": list(manifest_map.values()),
        "abbreviation_definitions": definitions,
        "nodes": nodes,
        "edges": edges,
        "merge_decisions": merge_decisions,
        "do_not_merge_records": do_not_merge,
        "conflicts": conflicts,
        "boundary_records": list(boundary_by_anchor.values()),
        "uncertainties": uncertainties,
        "extraction_mode": (reviewed_claims or {}).get("mode", "replace_paragraphs") if reviewed_claims else "rule_candidates",
        "reviewed_paragraphs": [{"source_id": sid, "paragraph_id": pid, "accepted_claims": len(claims)}
                                for (sid, pid), claims in sorted(reviewed.items())],
    }
    if base_kg is not None:
        kg_out["incremental_changelog"] = {
            "preserved_node_ids_count": len(preserved_node_ids),
            "all_base_node_ids_preserved": all(nid in {n["node_id"] for n in nodes} for nid in preserved_node_ids),
            "added_nodes": added_nodes_count,
            "merged_aliases": merged_aliases_count,
            "do_not_merge_count": len(do_not_merge),
            "added_edges": added_edges_count,
            "total_conflicts": len(conflicts),
        }
    return kg_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or incrementally update Literature Knowledge Graph.")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-kg", default=None, help="Optional existing kg.json for incremental update.")
    parser.add_argument("--claims", help="Optional paragraph-specific, evidence-checked replacements for rule extraction.")
    parser.add_argument("--review-out", help="Write a per-paper paragraph and candidate index for source review.")
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    base = json.loads(Path(args.base_kg).read_text(encoding="utf-8")) if args.base_kg and Path(args.base_kg).exists() else None
    reviewed = json.loads(Path(args.claims).read_text(encoding="utf-8")) if args.claims else None
    kg = build_kg(corpus, base_kg=base, reviewed_claims=reviewed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out_dir / "kg.json", kg)
    if args.review_out:
        write_json_atomic(args.review_out, make_review_plan(corpus, kg))
    print(json.dumps({"status": kg["status"], "nodes": len(kg["nodes"]), "edges": len(kg["edges"]), "do_not_merge": len(kg["do_not_merge_records"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
