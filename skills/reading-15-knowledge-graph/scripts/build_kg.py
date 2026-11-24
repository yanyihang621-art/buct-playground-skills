#!/usr/bin/env python3
"""Build evidence-grounded multi-paper Knowledge Graph with conservative entity alignment, do-not-merge records, and incremental updates."""

from __future__ import annotations

import argparse
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

SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+(?=[A-Z0-9\u4e00-\u9fff\(])")
ACRONYM_RE = re.compile(r"([A-Za-z][A-Za-z0-9\-\s]{2,50}?)\s*\(([A-Z][A-Za-z0-9\-]{1,12})\)")
CONDITION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|MPa|kPa|bar|mL\s*g-1\s*h-1|wt%|mol%|h|mA\s*cm-2)\b",
    re.IGNORECASE,
)
LEADING_COND_RE = re.compile(r"^((?:At|Under|In|For|When)\s+[^,;]{3,65}?),\s+(.+)$", re.IGNORECASE)
CITED_RE = re.compile(r"(?:\[\d+\]|\b(?:previous|prior|earlier)\s+(?:studies|work)\b|\bet\s+al\.,?\s*(?:19|20)\d{2})", re.IGNORECASE)
SPECULATIVE_RE = re.compile(r"\b(?:may|might|could|suggests?|likely|possibly|hypothesize)\b|(?:可能|推测)", re.IGNORECASE)

RELATION_PATTERNS: List[Tuple[str, re.Pattern[str], str, str]] = [
    ("inhibits", re.compile(r"^(.{2,65}?)\s+(?:inhibits?|blocks?|poisons?|suppresses?)\s+(.{2,85})$", re.IGNORECASE), "Material", "Metric"),
    ("decreases", re.compile(r"^(.{2,65}?)\s+(?:decreases?|reduces?|lowers?)\s+(.{2,85})$", re.IGNORECASE), "Material", "Metric"),
    ("increases", re.compile(r"^(.{2,65}?)\s+(?:increases?|enhances?|boosts?|promotes?|improves?)\s+(.{2,85})$", re.IGNORECASE), "Material", "Metric"),
    ("catalyzes", re.compile(r"^(.{2,65}?)\s+(?:catalyzes?|catalyses?|mediates?)\s+(.{2,85})$", re.IGNORECASE), "Material", "Task"),
    ("outperforms", re.compile(r"^(.{2,65}?)\s+(?:outperforms?|surpasses?|exceeds?)\s+(.{2,85})$", re.IGNORECASE), "Method", "Method"),
    ("uses", re.compile(r"^(.{2,65}?)\s+(?:uses?|employs?|utilizes?|adopts?)\s+(.{2,85})$", re.IGNORECASE), "Method", "Material"),
    ("evaluates_on", re.compile(r"^(.{2,65}?)\s+(?:was\s+evaluated\s+on|evaluates?\s+on|tested\s+on)\s+(.{2,85})$", re.IGNORECASE), "Method", "Task"),
    ("achieves", re.compile(r"^(.{2,65}?)\s+(?:achieves?|reaches?|yields?|delivers?|exhibits?)\s+(.{2,85})$", re.IGNORECASE), "Material", "Metric"),
    ("restricted_to", re.compile(r"^(.{2,65}?)\s+(?:is\s+restricted\s+to|only\s+valid\s+under|applies\s+only\s+to)\s+(.{2,85})$", re.IGNORECASE), "Material", "Condition"),
]

CLAUSE_SPLIT_RE = re.compile(r"(?:;\s*(?:moreover|furthermore|however)?,?\s*|,\s+(?:whereas|while|and\s+also)\s+|；\s*)", re.IGNORECASE)
CONNECTOR_STRIP_RE = re.compile(r"^(?:moreover|furthermore|however|whereas|while|therefore)[,\s]+", re.IGNORECASE)


def load_ontology() -> Dict[str, Any]:
    return yaml.safe_load((SKILL_ROOT / "schemas" / "kg_ontology.yaml").read_text(encoding="utf-8"))


def _canon_key(label: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", label.lower()).strip()


def _evidence_strength(sentence: str) -> str:
    if CITED_RE.search(sentence):
        return "prior_work_citation"
    if SPECULATIVE_RE.search(sentence):
        return "indirect_speculation"
    return "direct_author_evidence"


def build_kg(corpus: Dict[str, Any], base_kg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Construct or incrementally update an evidence-grounded multi-paper Knowledge Graph."""
    ontology = load_ontology()
    rel_meta = {r["id"]: r for r in ontology.get("relation_types", [])}

    papers = corpus.get("papers", [])
    uncertainties: List[Dict[str, Any]] = []
    for p in papers:
        uncertainties.extend(p.get("uncertainties", []))

    statuses = {p.get("status", "partial") for p in papers}
    if not papers or statuses == {"unverifiable"}:
        return {
            "schema_version": "1.0.0",
            "status": "unverifiable",
            "sources_manifest": [{k: p[k] for k in ("source_id", "source_file", "title", "authors", "doi", "year", "location_type")} for p in papers],
            "nodes": base_kg.get("nodes", []) if base_kg else [],
            "edges": base_kg.get("edges", []) if base_kg else [],
            "merge_decisions": base_kg.get("merge_decisions", []) if base_kg else [],
            "do_not_merge_records": base_kg.get("do_not_merge_records", []) if base_kg else [],
            "conflicts": base_kg.get("conflicts", []) if base_kg else [],
            "uncertainties": uncertainties,
        }
    if statuses == {"refused"}:
        return {
            "schema_version": "1.0.0",
            "status": "refused",
            "sources_manifest": [{k: p[k] for k in ("source_id", "source_file", "title", "authors", "doi", "year", "location_type")} for p in papers],
            "nodes": base_kg.get("nodes", []) if base_kg else [],
            "edges": base_kg.get("edges", []) if base_kg else [],
            "merge_decisions": base_kg.get("merge_decisions", []) if base_kg else [],
            "do_not_merge_records": base_kg.get("do_not_merge_records", []) if base_kg else [],
            "conflicts": base_kg.get("conflicts", []) if base_kg else [],
            "uncertainties": uncertainties,
        }

    # Initialize from base_kg if incremental update
    nodes: List[Dict[str, Any]] = [dict(n) for n in base_kg.get("nodes", [])] if base_kg else []
    edges: List[Dict[str, Any]] = [dict(e) for e in base_kg.get("edges", [])] if base_kg else []
    merge_decisions: List[Dict[str, Any]] = list(base_kg.get("merge_decisions", [])) if base_kg else []
    do_not_merge: List[Dict[str, Any]] = list(base_kg.get("do_not_merge_records", [])) if base_kg else []
    conflicts: List[Dict[str, Any]] = list(base_kg.get("conflicts", [])) if base_kg else []

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
        alias_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        nonlocal next_node_num, added_nodes_count, merged_aliases_count
        clean_lbl = re.sub(r"\s+", " ", label).strip(" .,;:")
        ckey = _canon_key(clean_lbl)
        if alias_map and ckey in alias_map:
            ckey = alias_map[ckey]

        # Check existing node with matching canonical_key (preferring same entity_type or Task/Method/Material)
        existing = node_by_key.get((ckey, entity_type))
        if existing is None:
            for (k_ck, k_et), cand in node_by_key.items():
                if k_ck == ckey and k_et not in ("MaterialInstance", "MethodInstance", "Researcher"):
                    existing = cand
                    break

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

    # Global acronym map discovered across corpus (only canonicalize abstract concepts/tasks, NOT sample instances!)
    global_acronym_to_full: Dict[str, str] = {}
    acronym_sources: Dict[str, List[Tuple[str, str, str]]] = {}

    for paper in papers:
        sid = paper["source_id"]
        for para in paper.get("paragraphs", []):
            txt = para["text"]
            for m in ACRONYM_RE.finditer(txt):
                full = re.sub(r"\s+", " ", m.group(1)).strip(" ,;:-")
                abbr = m.group(2).strip()
                if len(full.split()) > 7 or abbr.isdigit():
                    continue
                f_key = _canon_key(full)
                a_key = _canon_key(abbr)
                global_acronym_to_full[a_key] = f_key
                acronym_sources.setdefault(f_key, []).append((sid, abbr, full))

    # Extract per-paper nodes and edges
    raw_claims_by_paper: Dict[str, List[Dict[str, Any]]] = {}

    for paper in papers:
        sid = paper["source_id"]
        if paper.get("status") in ("refused", "unverifiable"):
            continue
        paras = paper.get("paragraphs", [])
        if not paras:
            continue

        # Record Researcher nodes anchored strictly to this paper's first paragraph
        first_para = paras[0]
        first_anchor = {
            "source_id": sid,
            "paragraph_id": first_para["paragraph_id"],
            "location_type": first_para["location_type"],
            "pdf_page": first_para.get("pdf_page"),
            "quote_snippet": first_para["text"][:220],
        }
        for auth in paper.get("authors", []):
            if auth and auth != "not_reported":
                _get_or_create_node(auth, "Researcher", sid, first_anchor)

        paper_claims: List[Dict[str, Any]] = []
        for para in paras:
            pid = para["paragraph_id"]
            ltype = para["location_type"]
            pdf_p = para.get("pdf_page")
            txt = para["text"].strip()
            if not txt:
                continue

            for sent in SENT_SPLIT_RE.split(txt):
                s_clean = sent.strip()
                if len(s_clean) < 16:
                    continue
                ev_str = _evidence_strength(s_clean)
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
                    conds = list(shared_conds)
                    for cv in CONDITION_RE.findall(cl_clean):
                        if cv not in conds:
                            conds.append(cv)

                    for rtype, pat, def_st, def_ot in RELATION_PATTERNS:
                        m = pat.match(cl_clean)
                        if not m:
                            continue
                        subj_raw = m.group(1).strip(" ,;:")
                        obj_raw = m.group(2).strip(" ,;:")
                        if not subj_raw or not obj_raw or subj_raw.lower() in ("it", "this", "they", "these"):
                            continue
                        if len(subj_raw.split()) > 8:
                            subj_raw = " ".join(subj_raw.split()[-5:])

                        anchor = {
                            "source_id": sid,
                            "paragraph_id": pid,
                            "location_type": ltype,
                            "pdf_page": pdf_p,
                            "quote_snippet": s_clean[:240],
                        }
                        paper_claims.append(
                            {
                                "subj_raw": subj_raw,
                                "subj_type": def_st,
                                "rel_type": rtype,
                                "obj_raw": obj_raw,
                                "obj_type": def_ot,
                                "conds": conds,
                                "ev_strength": ev_str,
                                "anchor": anchor,
                            }
                        )
                        break

        # Also extract tabular edges if tables exist (PDF inputs)
        for tab in paper.get("tables", []):
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
    subj_paper_conds: Dict[str, Dict[str, set]] = {}
    for sid, claims in raw_claims_by_paper.items():
        for c in claims:
            sk = global_acronym_to_full.get(_canon_key(c["subj_raw"]), _canon_key(c["subj_raw"]))
            subj_paper_conds.setdefault(sk, {}).setdefault(sid, set()).update(c["conds"] or ["unspecified"])

    # Also check already-existing nodes from base_kg for incremental multi-paper conflict detection
    for n in nodes:
        if n["entity_type"] in ("Material", "Method"):
            for anc in n.get("evidence_anchors", []):
                subj_paper_conds.setdefault(n["canonical_key"], {}).setdefault(anc["source_id"], set()).add("base_kg")

    split_instance_keys = {
        sk
        for sk, by_paper in subj_paper_conds.items()
        if len(by_paper) >= 2
    }

    # Materialize nodes & edges with Do-Not-Merge Instance Preservation
    do_not_merge_tracker: Dict[str, Dict[str, Any]] = {r["generic_key"]: r for r in do_not_merge if "generic_key" in r}

    for sid, claims in raw_claims_by_paper.items():
        for c in claims:
            anc = c["anchor"]
            sk = global_acronym_to_full.get(_canon_key(c["subj_raw"]), _canon_key(c["subj_raw"]))
            ok = global_acronym_to_full.get(_canon_key(c["obj_raw"]), _canon_key(c["obj_raw"]))

            # Canonical target node (shared abstract Task/Metric/Concept)
            tgt_node = _get_or_create_node(c["obj_raw"], c["obj_type"], sid, anc, alias_map=global_acronym_to_full)

            # Subject node: if generic material/method appears across multiple papers, keep paper-scoped instance node!
            if sk in split_instance_keys and c["subj_type"] in ("Material", "Method"):
                generic_node = _get_or_create_node(c["subj_raw"], c["subj_type"], sid, anc, alias_map=global_acronym_to_full)
                inst_type = "MaterialInstance" if c["subj_type"] == "Material" else "MethodInstance"
                inst_label = f"{c['subj_raw']} [{sid}]"
                src_node = _get_or_create_node(inst_label, inst_type, sid, anc)

                # Link instance -> generic concept via instance_of if not already linked
                if not any(
                    e["source_node"] == src_node["node_id"]
                    and e["relation_type"] == "instance_of"
                    and e["target_node"] == generic_node["node_id"]
                    for e in edges
                ):
                    edges.append(
                        {
                            "edge_id": f"e_{next_edge_num}",
                            "source_node": src_node["node_id"],
                            "relation_type": "instance_of",
                            "target_node": generic_node["node_id"],
                            "conditions": c["conds"],
                            "evidence_strength": c["ev_strength"],
                            "source_id": sid,
                            "paragraph_id": anc["paragraph_id"],
                            "location_type": anc["location_type"],
                            "pdf_page": anc.get("pdf_page"),
                            "quote_snippet": anc["quote_snippet"],
                        }
                    )
                    next_edge_num += 1
                    added_edges_count += 1

                rec_entry = do_not_merge_tracker.setdefault(
                    sk,
                    {
                        "generic_key": sk,
                        "generic_term": generic_node["label"],
                        "generic_node_id": generic_node["node_id"],
                        "instance_nodes": [],
                        "sources_and_conditions": {},
                        "reason": (
                            f"Generic term '{generic_node['label']}' appears across multiple papers under distinct experimental "
                            "conditions, samples, or metrics; paper-scoped instance nodes kept separate to avoid false conclusion merging."
                        ),
                    },
                )
                if src_node["node_id"] not in rec_entry["instance_nodes"]:
                    rec_entry["instance_nodes"].append(src_node["node_id"])
                rec_entry["sources_and_conditions"][sid] = sorted(subj_paper_conds[sk].get(sid, []))
            else:
                src_node = _get_or_create_node(c["subj_raw"], c["subj_type"], sid, anc, alias_map=global_acronym_to_full)

            # Add directed relation edge (deduplicating exact identical edge)
            if not any(
                e["source_node"] == src_node["node_id"]
                and e["relation_type"] == c["rel_type"]
                and e["target_node"] == tgt_node["node_id"]
                and e["source_id"] == sid
                and e["paragraph_id"] == anc["paragraph_id"]
                for e in edges
            ):
                edges.append(
                    {
                        "edge_id": f"e_{next_edge_num}",
                        "source_node": src_node["node_id"],
                        "relation_type": c["rel_type"],
                        "target_node": tgt_node["node_id"],
                        "conditions": c["conds"],
                        "evidence_strength": c["ev_strength"],
                        "source_id": sid,
                        "paragraph_id": anc["paragraph_id"],
                        "location_type": anc["location_type"],
                        "pdf_page": anc.get("pdf_page"),
                        "quote_snippet": anc["quote_snippet"],
                    }
                )
                next_edge_num += 1
                added_edges_count += 1

    do_not_merge = list(do_not_merge_tracker.values())

    # Record explicit merge_decisions for canonical concepts with >1 alias or >1 source
    existing_merge_keys = {m.get("canonical_node_id") for m in merge_decisions}
    for n in nodes:
        if n["entity_type"] in ("MaterialInstance", "MethodInstance", "Researcher"):
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

    # Detect contradicting relations across edges
    node_map = {n["node_id"]: n for n in nodes}
    existing_conf_pairs = {tuple(sorted(cf.get("edge_ids", []))) for cf in conflicts}
    c_seq = len(conflicts)
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            e1, e2 = edges[i], edges[j]
            if e1["relation_type"] == "instance_of" or e2["relation_type"] == "instance_of":
                continue
            s1 = node_map.get(e1["source_node"], {}).get("canonical_key", "")
            s2 = node_map.get(e2["source_node"], {}).get("canonical_key", "")
            t1 = node_map.get(e1["target_node"], {}).get("canonical_key", "")
            t2 = node_map.get(e2["target_node"], {}).get("canonical_key", "")
            # Strip [paper_x] suffix when comparing generic subject concept for conflict check
            s1_base = re.sub(r"\s+paper \d+$", "", s1)
            s2_base = re.sub(r"\s+paper \d+$", "", s2)
            if s1_base == s2_base and t1 == t2:
                r1, r2 = e1["relation_type"], e2["relation_type"]
                if r2 in rel_meta.get(r1, {}).get("contradicts", []) or r1 in rel_meta.get(r2, {}).get("contradicts", []):
                    pair_k = tuple(sorted([e1["edge_id"], e2["edge_id"]]))
                    if pair_k not in existing_conf_pairs:
                        c_seq += 1
                        conflicts.append(
                            {
                                "conflict_id": f"kg_conf_{c_seq}",
                                "edge_ids": [e1["edge_id"], e2["edge_id"]],
                                "relations": [r1, r2],
                                "sources": [e1["source_id"], e2["source_id"]],
                                "message": f"Conflicting relations '{r1}' ({e1['source_id']}) vs '{r2}' ({e2['source_id']}) between '{s1_base}' and '{t1}'.",
                            }
                        )
                        existing_conf_pairs.add(pair_k)

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

    kg_out: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": "partial" if uncertainties else "success",
        "sources_manifest": list(manifest_map.values()),
        "nodes": nodes,
        "edges": edges,
        "merge_decisions": merge_decisions,
        "do_not_merge_records": do_not_merge,
        "conflicts": conflicts,
        "uncertainties": uncertainties,
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
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    base = json.loads(Path(args.base_kg).read_text(encoding="utf-8")) if args.base_kg and Path(args.base_kg).exists() else None
    kg = build_kg(corpus, base_kg=base)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out_dir / "kg.json", kg)
    print(json.dumps({"status": kg["status"], "nodes": len(kg["nodes"]), "edges": len(kg["edges"]), "do_not_merge": len(kg["do_not_merge_records"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
