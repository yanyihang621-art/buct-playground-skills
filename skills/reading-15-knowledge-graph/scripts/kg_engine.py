#!/usr/bin/env python3
"""Deterministic KG engine: quote-verified graph construction, conservative alignment, incremental updates, 8 query types, and GraphML/DOT/Markdown/PNG export."""

from __future__ import annotations

import copy
import html as html_lib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

from evidence import IndexStore, check_negation_and_hedge, check_units, normalize_text
from pdf_utils import write_text_lf

INSTANCE_CONCEPTS = {"Material", "Method", "Model", "Dataset", "Instrument"}
BLOCKING_UNCERTAINTY_CODES = {
    "missing_input_file",
    "unreadable_source",
    "encrypted_pdf",
    "corrupt_pdf",
    "ocr_unavailable",
    "empty_document",
    "scan_without_ocr",
}


def is_blocking_uncertainty(u: Any) -> bool:
    """Return True only when an uncertainty represents missing/unreadable core input rather than an info/scope note."""
    if not isinstance(u, dict):
        return False
    if u.get("blocking") is True:
        return True
    sev = str(u.get("severity") or "").strip().lower()
    if sev in ("high", "critical", "fatal"):
        return True
    code = str(u.get("code") or u.get("type") or "").strip().lower()
    return code in BLOCKING_UNCERTAINTY_CODES


def _extract_provenance(item: Dict[str, Any]) -> Tuple[str, str, str, List[Tuple[str, str, str]]]:
    """Support flat source_id/paragraph_id/quote, combined anchor 'sid:pid', and nested evidence[] lists."""
    sid = str(item.get("source_id") or item.get("paper_id") or item.get("doc_id") or "").strip()
    pid = str(item.get("paragraph_id") or item.get("block_id") or item.get("locator") or "").strip()
    quote = str(item.get("quote") or item.get("quote_snippet") or item.get("verbatim_quote") or "").strip()
    raw_anchor = str(item.get("anchor") or "").strip()
    if raw_anchor:
        if ":" in raw_anchor and not sid:
            sid, anc_pid = raw_anchor.split(":", 1)
            pid = pid or anc_pid.strip()
        elif sid and raw_anchor.startswith(f"{sid}:"):
            pid = pid or raw_anchor[len(sid) + 1:].strip()
        elif not pid:
            pid = raw_anchor

    extras: List[Tuple[str, str, str]] = []
    ev_raw = item.get("evidence")
    ev_list = ev_raw if isinstance(ev_raw, list) else ([ev_raw] if isinstance(ev_raw, dict) else [])
    for idx, ev in enumerate(ev_list):
        if isinstance(ev, dict):
            e_sid = str(ev.get("source_id") or ev.get("paper_id") or ev.get("doc_id") or "").strip()
            e_pid = str(ev.get("paragraph_id") or ev.get("block_id") or ev.get("locator") or "").strip()
            e_anc = str(ev.get("anchor") or "").strip()
            if e_anc:
                if ":" in e_anc and not e_sid:
                    e_sid, e_anc_pid = e_anc.split(":", 1)
                    e_pid = e_pid or e_anc_pid.strip()
                elif e_sid and e_anc.startswith(f"{e_sid}:"):
                    e_pid = e_pid or e_anc[len(e_sid) + 1:].strip()
                elif not e_pid:
                    e_pid = e_anc
            e_sid = e_sid or sid
            e_q = str(ev.get("quote") or ev.get("quote_snippet") or ev.get("verbatim_quote") or "").strip() or quote
            if idx == 0 and (not sid or not pid or not quote):
                sid = sid or e_sid
                pid = pid or e_pid
                quote = quote or e_q
            elif e_sid and e_q:
                extras.append((e_sid, e_pid, e_q))
        elif isinstance(ev, str) and ev.strip() and not quote:
            quote = ev.strip()
    return sid.strip(), pid.strip(), quote.strip(), extras


def load_ontology() -> Dict[str, Any]:
    return yaml.safe_load((SKILL_ROOT / "schemas" / "kg_ontology.yaml").read_text(encoding="utf-8"))


def _canon_key(label: str) -> str:
    text = unicodedata.normalize("NFC", str(label or "")).casefold()
    text = re.sub(r"^(?:the|an?)\s+(?=\w)", "", text)
    text = re.sub(r"(?<=[^\W\d_])[-‐‑](?=[^\W\d_])", " ", text)
    return re.sub(r"[^\w+−%°±⁺⁻/\-]+", " ", text).strip()


def _load_draft_parts(work_dir: Path) -> Dict[str, Any]:
    """Load and merge work/kg_draft.json and any work/kg_draft_*.json files."""
    files: List[Path] = []
    main_draft = work_dir / "kg_draft.json"
    if main_draft.is_file():
        files.append(main_draft)
    files.extend(sorted(p for p in work_dir.glob("kg_draft_*.json") if p.name != "kg_draft_template.json"))

    merged: Dict[str, Any] = {
        "entities": [],
        "relations": [],
        "merge_decisions": [],
        "do_not_merge_notes": [],
        "queries": [],
        "uncertainties": [],
    }
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(obj, dict):
            continue
        for k in merged:
            val = obj.get(k)
            if isinstance(val, list):
                merged[k].extend(val)
    # Also support optional work/queries.json
    qfile = work_dir / "queries.json"
    if qfile.is_file():
        qobj = json.loads(qfile.read_text(encoding="utf-8-sig"))
        if isinstance(qobj, list):
            merged["queries"].extend(qobj)
        elif isinstance(qobj, dict) and isinstance(qobj.get("queries"), list):
            merged["queries"].extend(qobj["queries"])
    return merged


def _build_local_aliases(definitions: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, set[str]]]:
    per_source: Dict[str, Dict[str, set[str]]] = {}
    all_forms: Dict[str, set[str]] = {}
    for row in definitions:
        sk = _canon_key(row["short"])
        fk = _canon_key(row["full"])
        per_source.setdefault(row["source_id"], {}).setdefault(sk, set()).add(fk)
        all_forms.setdefault(sk, set()).add(fk)
    aliases: Dict[str, Dict[str, str]] = {}
    for sid, local in per_source.items():
        aliases[sid] = {k: next(iter(vals)) for k, vals in local.items() if len(vals) == 1}
    return aliases, all_forms


def _verify_quote_anchor(
    quote: str,
    source_id: str,
    paragraph_id: str,
    store: IndexStore,
    paper_loc_types: Dict[str, str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Verify quote against IndexStore within the specified source_id, preserving pdf_page=None for non-PDFs."""
    q_clean = (quote or "").strip()
    if not q_clean:
        return None, "empty quote"
    vr = store.get(source_id, paragraph_id)
    res_dict: Dict[str, Any]
    from evidence import verify as ev_verify
    res_dict = ev_verify(q_clean, f"{source_id}:{paragraph_id}" if paragraph_id else source_id, source_id, store)
    if res_dict["match"] in ("unverified", "too_short"):
        return None, f"unverified quote ({res_dict['match']}) at {source_id}:{paragraph_id}"
    # Enforce same-paper provenance (do not relocate across papers)
    actual_sid = res_dict["paper_id"]
    if source_id and actual_sid != source_id:
        # Check if quote exists directly in any block of source_id
        nq = normalize_text(q_clean)
        found_item = None
        for item in store.by_paper.get(source_id, []):
            if nq in store.norm_texts[item["_idx"]]:
                found_item = item
                break
        if found_item is None:
            return None, f"quote not found in declared source_id={source_id} (matched {actual_sid} instead)"
        res_dict = {
            "match": "relocated",
            "paper_id": source_id,
            "anchor": found_item["anchor"],
            "page": found_item.get("page"),
        }

    matched_entry = store.get(res_dict["paper_id"], res_dict["anchor"])
    ltype = (
        matched_entry.get("location_type")
        if matched_entry
        else paper_loc_types.get(res_dict["paper_id"], "json_paragraph_id")
    )
    is_pdf = ltype in ("pdf_block", "pdf_table")
    pdf_page = (matched_entry.get("page") if matched_entry else res_dict.get("page")) if is_pdf else None
    anchor = {
        "source_id": res_dict["paper_id"],
        "paragraph_id": res_dict["anchor"],
        "location_type": ltype,
        "pdf_page": pdf_page,
        "quote_snippet": q_clean[:320],
        "block_text": matched_entry.get("text", "") if matched_entry else "",
    }
    return anchor, None


def compile_kg(
    corpus: Dict[str, Any],
    index_entries: List[Dict[str, Any]],
    draft: Dict[str, Any],
    base_kg: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compile verified KG from Agent's kg_draft.json + corpus + optional base_kg."""
    ontology = load_ontology()
    entity_types = set(ontology.get("entity_types", []))
    rel_meta = {r["id"]: r for r in ontology.get("relation_types", [])}
    valid_strengths = set(ontology.get("evidence_strength_tiers", []))

    store = IndexStore(index_entries)
    papers = sorted(corpus.get("papers", []), key=lambda p: p["source_id"])
    paper_loc_types = {p["source_id"]: p.get("location_type", "json_paragraph_id") for p in papers}
    must_fix: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    uncertainties: List[Dict[str, Any]] = []

    for p in papers:
        uncertainties.extend(p.get("uncertainties", []))
    for missing in corpus.get("missing_input_files", []):
        uncertainties.append({"code": "missing_input_file", "message": str(missing)})
    for u in draft.get("uncertainties", []):
        if isinstance(u, dict):
            uncertainties.append(u)
        elif isinstance(u, str) and u.strip():
            uncertainties.append({"code": "agent_uncertainty", "message": u.strip()})

    base_kg = copy.deepcopy(base_kg) if base_kg else None
    nodes: List[Dict[str, Any]] = [dict(n) for n in base_kg.get("nodes", [])] if base_kg else []
    edges: List[Dict[str, Any]] = [dict(e) for e in base_kg.get("edges", [])] if base_kg else []
    preserved_node_ids = {n["node_id"] for n in nodes}

    node_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {
        (n["canonical_key"], n["entity_type"]): n for n in nodes
    }
    next_node_num = max([int(re.sub(r"\D", "", n["node_id"]) or 0) for n in nodes] + [0]) + 1
    next_edge_num = max([int(re.sub(r"\D", "", e["edge_id"]) or 0) for e in edges] + [0]) + 1
    added_nodes = 0
    added_edges = 0
    merged_aliases = 0

    definitions = list(corpus.get("abbreviation_definitions", []))
    if base_kg:
        seen_d = {(d["source_id"], d["paragraph_id"], d["short"], d["full"]) for d in definitions}
        for d in base_kg.get("abbreviation_definitions", []):
            dk = (d["source_id"], d["paragraph_id"], d["short"], d["full"])
            if dk not in seen_d:
                definitions.append(d)
                seen_d.add(dk)
    aliases_by_paper, all_forms = _build_local_aliases(definitions)

    # Check agent-supplied allow_instance_merge overrides
    allow_instance_merge_keys: set[str] = set()
    agent_merge_reasons: Dict[str, Dict[str, Any]] = {}
    for md in draft.get("merge_decisions", []):
        if isinstance(md, dict) and md.get("canonical_label"):
            ck = _canon_key(md["canonical_label"])
            agent_merge_reasons[ck] = md
            if md.get("allow_instance_merge") is True:
                allow_instance_merge_keys.add(ck)

    def _concept_key(label: str, sid: str) -> str:
        raw = _canon_key(label)
        decl = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", label.strip())
        if decl and aliases_by_paper.get(sid, {}).get(_canon_key(decl[2])) == _canon_key(decl[1]):
            return _canon_key(decl[1])
        resolved = aliases_by_paper.get(sid, {}).get(raw)
        if resolved:
            return resolved
        if raw in all_forms or re.fullmatch(r"[A-Z][A-Z0-9\-]{1,12}", label.strip()):
            return f"{raw} [{sid}]"
        return raw

    def _get_or_create_node(
        label: str,
        etype: str,
        sid: str,
        anchor_clean: Dict[str, Any],
        ckey: str,
        extra_aliases: Sequence[str] = (),
    ) -> Dict[str, Any]:
        nonlocal next_node_num, added_nodes, merged_aliases
        clean_lbl = re.sub(r"\s+", " ", label).strip(" .,;:")
        existing = node_by_key.get((ckey, etype))
        if existing is not None:
            for cand in [clean_lbl, *extra_aliases]:
                cand_c = re.sub(r"\s+", " ", str(cand)).strip(" .,;:")
                if cand_c and cand_c not in existing["aliases"]:
                    existing["aliases"].append(cand_c)
                    merged_aliases += 1
            if sid not in existing["source_ids"]:
                existing["source_ids"].append(sid)
            if not any(
                a.get("source_id") == anchor_clean["source_id"]
                and a.get("paragraph_id") == anchor_clean["paragraph_id"]
                for a in existing["evidence_anchors"]
            ):
                existing["evidence_anchors"].append(anchor_clean)
            return existing

        nid = f"n_{next_node_num}"
        next_node_num += 1
        added_nodes += 1
        all_al = [clean_lbl]
        for cand in extra_aliases:
            cand_c = re.sub(r"\s+", " ", str(cand)).strip(" .,;:")
            if cand_c and cand_c not in all_al:
                all_al.append(cand_c)
        node = {
            "node_id": nid,
            "label": clean_lbl,
            "canonical_key": ckey,
            "entity_type": etype,
            "aliases": all_al,
            "source_ids": [sid],
            "evidence_anchors": [anchor_clean],
        }
        nodes.append(node)
        node_by_key[(ckey, etype)] = node
        return node

    # Pass 1: verify explicit entities
    verified_entities: List[Dict[str, Any]] = []
    entity_aliases_map: Dict[Tuple[str, str], List[str]] = {}
    for idx, ent in enumerate(draft.get("entities", []), 1):
        if not isinstance(ent, dict):
            continue
        label = str(ent.get("label") or ent.get("name") or "").strip()
        etype = str(ent.get("entity_type") or ent.get("type") or "Concept").strip()
        sid, pid, quote, extra_ev = _extract_provenance(ent)
        if not label or not sid:
            must_fix.append({"code": "invalid_entity", "index": idx, "message": "Entity missing label or source_id."})
            continue
        if etype not in entity_types:
            warnings.append({"code": "unknown_entity_type", "label": label, "entity_type": etype})
        anc, err = _verify_quote_anchor(quote, sid, pid, store, paper_loc_types)
        if err or anc is None:
            must_fix.append({"code": "unverified_quote", "context": f"entity:{label}", "message": err or "unverified"})
            continue
        extra_anchors: List[Dict[str, Any]] = []
        for e_sid, e_pid, e_q in extra_ev:
            e_anc, e_err = _verify_quote_anchor(e_q, e_sid, e_pid, store, paper_loc_types)
            if e_err or e_anc is None:
                must_fix.append({"code": "unverified_quote", "context": f"entity:{label}", "message": e_err or "unverified"})
            else:
                e_anc.pop("block_text", None)
                extra_anchors.append(e_anc)
        block_text = anc.pop("block_text", "")
        if normalize_text(label) not in normalize_text(anc["quote_snippet"]) and normalize_text(label) not in normalize_text(block_text):
            warnings.append({"code": "entity_label_not_literal_in_quote", "label": label, "source_id": sid, "paragraph_id": anc["paragraph_id"]})
        aliases_in = [str(a) for a in (ent.get("aliases") or []) if isinstance(a, str) and a.strip()]
        entity_aliases_map[(sid, _concept_key(label, sid))] = aliases_in
        verified_entities.append({
            "label": label,
            "etype": etype,
            "sid": sid,
            "anchor": anc,
            "extra_anchors": extra_anchors,
            "aliases": aliases_in,
        })

    # Pass 2: verify relations
    verified_relations: List[Dict[str, Any]] = []
    for idx, rel in enumerate(draft.get("relations", []), 1):
        if not isinstance(rel, dict):
            continue
        subj = str(rel.get("subject") or rel.get("source") or "").strip()
        obj = str(rel.get("object") or rel.get("target") or "").strip()
        rtype = str(rel.get("relation") or rel.get("relation_type") or rel.get("predicate") or "").strip()
        stype = str(rel.get("subject_type") or "Concept").strip()
        otype = str(rel.get("object_type") or "Concept").strip()
        polarity = str(rel.get("polarity") or "affirmative").strip().lower()
        if polarity not in ("affirmative", "negative"):
            polarity = "affirmative"
        strength = str(rel.get("evidence_strength") or "direct_author_evidence").strip()
        if strength not in valid_strengths:
            warnings.append({"code": "nonstandard_evidence_strength", "index": idx, "value": strength})
        if rtype not in rel_meta:
            warnings.append({"code": "custom_relation_used", "index": idx, "relation": rtype})
        sid, pid, quote, extra_ev = _extract_provenance(rel)
        conds = [str(c).strip() for c in (rel.get("conditions") or []) if str(c).strip()]
        if not subj or not obj or not rtype or not sid:
            must_fix.append({"code": "invalid_relation", "index": idx, "message": "Relation missing subject/object/relation/source_id."})
            continue
        anc, err = _verify_quote_anchor(quote, sid, pid, store, paper_loc_types)
        if err or anc is None:
            must_fix.append({"code": "unverified_quote", "context": f"relation:{subj}->{rtype}->{obj}", "message": err or "unverified"})
            continue
        for e_sid, e_pid, e_q in extra_ev:
            _, e_err = _verify_quote_anchor(e_q, e_sid, e_pid, store, paper_loc_types)
            if e_err:
                must_fix.append({"code": "unverified_quote", "context": f"relation:{subj}->{rtype}->{obj}", "message": e_err})
        block_text = anc.pop("block_text", "")
        ev_texts = [anc["quote_snippet"], block_text]
        um = check_units(f"{subj} {obj} {' '.join(conds)}", ev_texts)
        if um:
            warnings.append({"code": "unit_mismatch", "context": f"relation:{subj}->{obj}", "mismatches": um})
        nh = check_negation_and_hedge(f"{subj} {rtype} {obj}", anc["quote_snippet"])
        if "negation_dropped" in nh and polarity != "negative" and rtype not in ("inhibits", "decreases", "prevents", "contradicts"):
            warnings.append({"code": "negation_dropped", "context": f"relation:{subj}->{rtype}->{obj}", "quote": anc["quote_snippet"][:160]})
        if "hedge_dropped" in nh and strength == "direct_author_evidence":
            warnings.append({"code": "hedge_dropped", "context": f"relation:{subj}->{rtype}->{obj}", "quote": anc["quote_snippet"][:160]})

        verified_relations.append({
            "subj": subj, "stype": stype, "obj": obj, "otype": otype,
            "rtype": rtype, "polarity": polarity, "conds": conds,
            "strength": strength, "sid": sid, "anchor": anc,
        })

    # Determine which INSTANCE_CONCEPTS appear across >=2 distinct papers -> split into Concept + Instance nodes
    occurrences: Dict[Tuple[str, str], set[str]] = {}
    for ent in verified_entities:
        if ent["etype"] in INSTANCE_CONCEPTS:
            ck = _concept_key(ent["label"], ent["sid"])
            if ck not in allow_instance_merge_keys:
                occurrences.setdefault((ck, ent["etype"]), set()).add(ent["sid"])
    for rel in verified_relations:
        for lbl, et in ((rel["subj"], rel["stype"]), (rel["obj"], rel["otype"])):
            if et in INSTANCE_CONCEPTS:
                ck = _concept_key(lbl, rel["sid"])
                if ck not in allow_instance_merge_keys:
                    occurrences.setdefault((ck, et), set()).add(rel["sid"])
    for n in nodes:
        if n["entity_type"] in INSTANCE_CONCEPTS and n["canonical_key"] not in allow_instance_merge_keys:
            occurrences.setdefault((n["canonical_key"], n["entity_type"]), set()).update(n["source_ids"])

    split_keys = {k for k, sids in occurrences.items() if len(sids) >= 2}
    do_not_merge_tracker: Dict[str, Dict[str, Any]] = {
        r["generic_key"]: r for r in (base_kg or {}).get("do_not_merge_records", []) if isinstance(r, dict) and "generic_key" in r
    }
    instance_links = {(e["source_node"], e["target_node"]) for e in edges if e["relation_type"] == "instance_of"}

    def _resolve_node_and_instance(
        label: str, etype: str, sid: str, anchor: Dict[str, Any], conds: List[str], strength: str, extra_aliases: Sequence[str] = ()
    ) -> Dict[str, Any]:
        nonlocal next_edge_num, added_edges
        key = _concept_key(label, sid)
        if etype.endswith("Instance") or etype in {"Condition", "Researcher", "Finding"} or re.search(r"\d\s*%", label):
            if not key.endswith(f"[{sid}]"):
                key = f"{key} [{sid}]"
        concept = _get_or_create_node(label, etype, sid, anchor, key, extra_aliases)
        if (key, etype) not in split_keys:
            return concept
        inst_key = f"{key} [{sid}]"
        inst_node = _get_or_create_node(f"{concept['label']} [{sid}]", etype + "Instance", sid, anchor, inst_key)
        link = (inst_node["node_id"], concept["node_id"])
        if link not in instance_links:
            edges.append({
                **anchor,
                "edge_id": f"e_{next_edge_num}",
                "source_node": link[0],
                "relation_type": "instance_of",
                "target_node": link[1],
                "polarity": "affirmative",
                "conditions": conds,
                "evidence_strength": strength,
            })
            next_edge_num += 1
            added_edges += 1
            instance_links.add(link)
        rkey = f"{etype}:{key}"
        rec = do_not_merge_tracker.setdefault(rkey, {
            "generic_key": rkey,
            "generic_term": concept["label"],
            "generic_node_id": concept["node_id"],
            "instance_nodes": [],
            "sources_and_conditions": {},
            "reason": "Shared scientific term across papers; experimental instances kept distinct via instance_of.",
        })
        if inst_node["node_id"] not in rec["instance_nodes"]:
            rec["instance_nodes"].append(inst_node["node_id"])
        prev_conds = rec["sources_and_conditions"].get(sid, [])
        rec["sources_and_conditions"][sid] = sorted(set(prev_conds + (conds or ["unspecified"])))
        return inst_node

    # Re-link existing base edges ONLY if endpoint is not yet an Instance node and now belongs to split_keys
    base_node_map = {n["node_id"]: n for n in nodes}
    for edge in list(edges):
        if edge["relation_type"] == "instance_of":
            continue
        anc = {k: edge[k] for k in ("source_id", "paragraph_id", "location_type", "pdf_page", "quote_snippet")}
        for ep in ("source_node", "target_node"):
            bn = base_node_map[edge[ep]]
            if bn["entity_type"].endswith("Instance"):
                continue
            if (bn["canonical_key"], bn["entity_type"]) in split_keys:
                inst = _resolve_node_and_instance(
                    bn["label"],
                    bn["entity_type"],
                    edge["source_id"],
                    anc,
                    edge.get("conditions", []),
                    edge["evidence_strength"],
                )
                edge[ep] = inst["node_id"]

    # Create entity nodes
    for ent in verified_entities:
        node = _resolve_node_and_instance(
            ent["label"], ent["etype"], ent["sid"], ent["anchor"], [], "direct_author_evidence", ent["aliases"]
        )
        for ea in ent.get("extra_anchors") or []:
            if not any(
                a.get("source_id") == ea["source_id"] and a.get("paragraph_id") == ea["paragraph_id"]
                for a in node["evidence_anchors"]
            ):
                node["evidence_anchors"].append(ea)

    # Create relation edges
    def _edge_id_tuple(e: Dict[str, Any]) -> tuple:
        return (
            e["source_node"], e["relation_type"], e["target_node"],
            e.get("polarity", "affirmative"), e["source_id"], e["paragraph_id"],
            e["quote_snippet"], tuple(e.get("conditions", [])),
        )

    seen_edges = {_edge_id_tuple(e) for e in edges}
    for rel in verified_relations:
        s_node = _resolve_node_and_instance(
            rel["subj"], rel["stype"], rel["sid"], rel["anchor"], rel["conds"], rel["strength"],
            entity_aliases_map.get((rel["sid"], _concept_key(rel["subj"], rel["sid"])), ()),
        )
        o_node = _resolve_node_and_instance(
            rel["obj"], rel["otype"], rel["sid"], rel["anchor"], rel["conds"], rel["strength"],
            entity_aliases_map.get((rel["sid"], _concept_key(rel["obj"], rel["sid"])), ()),
        )
        edge = {
            **rel["anchor"],
            "source_node": s_node["node_id"],
            "relation_type": rel["rtype"],
            "target_node": o_node["node_id"],
            "polarity": rel["polarity"],
            "conditions": rel["conds"],
            "evidence_strength": rel["strength"],
        }
        et = _edge_id_tuple(edge)
        if et not in seen_edges:
            edges.append({**edge, "edge_id": f"e_{next_edge_num}"})
            seen_edges.add(et)
            next_edge_num += 1
            added_edges += 1

    # Record abbreviation conflicts & agent do_not_merge_notes
    for short, forms in all_forms.items():
        if len(forms) >= 2:
            ev = [d for d in definitions if _canon_key(d["short"]) == short]
            do_not_merge_tracker[f"abbreviation:{short}"] = {
                "generic_key": f"abbreviation:{short}",
                "generic_term": ev[0]["short"],
                "instance_nodes": [n["node_id"] for n in nodes if n["canonical_key"] in forms or f"{short} [" in n["canonical_key"]],
                "definition_evidence": ev,
                "reason": "Same abbreviation has distinct definitions across papers; kept paper-scoped.",
            }
    for note in draft.get("do_not_merge_notes", []):
        if isinstance(note, dict) and note.get("term"):
            rk = f"agent_note:{_canon_key(note['term'])}"
            do_not_merge_tracker.setdefault(rk, {
                "generic_key": rk,
                "generic_term": str(note["term"]),
                "source_ids": note.get("source_ids", []),
                "reason": str(note.get("reason") or "Preserved distinct per agent review."),
            })
    do_not_merge = list(do_not_merge_tracker.values())

    # Merge decisions
    merge_decisions: List[Dict[str, Any]] = []
    seen_merge_ids = set()
    for n in nodes:
        if n["entity_type"].endswith("Instance") or n["entity_type"] == "Researcher":
            continue
        if len(n["aliases"]) > 1 or len(n["source_ids"]) >= 2 or n["canonical_key"] in agent_merge_reasons:
            custom = agent_merge_reasons.get(n["canonical_key"], {})
            merge_decisions.append({
                "canonical_node_id": n["node_id"],
                "canonical_label": n["label"],
                "entity_type": n["entity_type"],
                "merged_aliases": n["aliases"],
                "source_ids": n["source_ids"],
                "reason": custom.get("reason") or "Per-paper abbreviation/case alignment across verified text spans.",
            })
            seen_merge_ids.add(n["node_id"])

    # Detect contradictions (opposing predicates OR affirmative vs negative on same endpoints)
    node_map = {n["node_id"]: n for n in nodes}
    parent_map = {e["source_node"]: e["target_node"] for e in edges if e["relation_type"] == "instance_of"}
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    conflicts: List[Dict[str, Any]] = []
    for edge in edges:
        if edge["relation_type"] == "instance_of":
            continue
        ep_pair = (parent_map.get(edge["source_node"], edge["source_node"]), parent_map.get(edge["target_node"], edge["target_node"]))
        opposing = set(rel_meta.get(edge["relation_type"], {}).get("contradicts", []))
        for prev in groups.get(ep_pair, []):
            prev_opp = set(rel_meta.get(prev["relation_type"], {}).get("contradicts", []))
            pred_conflict = prev["relation_type"] in opposing or edge["relation_type"] in prev_opp
            pol_conflict = (
                prev["relation_type"] == edge["relation_type"]
                and prev.get("polarity", "affirmative") != edge.get("polarity", "affirmative")
            )
            if pred_conflict or pol_conflict:
                same_ctx = prev["source_id"] == edge["source_id"] and prev.get("conditions", []) == edge.get("conditions", [])
                conflicts.append({
                    "conflict_id": f"kg_conf_{len(conflicts) + 1}",
                    "edge_ids": [prev["edge_id"], edge["edge_id"]],
                    "relations": [prev["relation_type"], edge["relation_type"]],
                    "polarities": [prev.get("polarity", "affirmative"), edge.get("polarity", "affirmative")],
                    "sources": [prev["source_id"], edge["source_id"]],
                    "classification": "opposed_same_context" if same_ctx else "different_context_comparison",
                    "conditions": [prev.get("conditions", []), edge.get("conditions", [])],
                    "message": f"Conflicting claims between {node_map[ep_pair[0]]['label']} and {node_map[ep_pair[1]]['label']}.",
                })
        groups.setdefault(ep_pair, []).append(edge)

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

    substantive_sources = {e["source_id"] for e in edges if e["relation_type"] != "instance_of"}
    for p in papers:
        if p["source_id"] not in substantive_sources and p.get("paragraphs"):
            warnings.append({"code": "paper_without_edges", "source_id": p["source_id"]})

    blocking_unc = [u for u in uncertainties if is_blocking_uncertainty(u)]
    unreadable_papers = [p for p in papers if p.get("status") in ("unverifiable", "refused", "failed")]
    has_substantive = any(e.get("relation_type") != "instance_of" for e in edges)
    if not papers or (unreadable_papers and len(unreadable_papers) == len(papers)):
        status = "unverifiable"
    elif has_substantive:
        status = "partial" if (blocking_unc or must_fix or unreadable_papers) else "success"
    else:
        status = "partial"

    boundary_records = list(corpus.get("boundary_records", []))
    kg_out: Dict[str, Any] = {
        "schema_version": "2.0.0",
        "status": status,
        "sources_manifest": list(manifest_map.values()),
        "abbreviation_definitions": definitions,
        "nodes": nodes,
        "edges": edges,
        "merge_decisions": merge_decisions,
        "do_not_merge_records": do_not_merge,
        "conflicts": conflicts,
        "boundary_records": boundary_records,
        "uncertainties": uncertainties,
        "extraction_mode": "agent_verified",
    }
    if base_kg is not None:
        cur_ids = {n["node_id"] for n in nodes}
        kg_out["incremental_changelog"] = {
            "preserved_node_ids_count": len(preserved_node_ids),
            "all_base_node_ids_preserved": all(nid in cur_ids for nid in preserved_node_ids),
            "added_nodes": added_nodes,
            "merged_aliases": merged_aliases,
            "do_not_merge_count": len(do_not_merge),
            "added_edges": added_edges,
            "total_conflicts": len(conflicts),
        }
    return kg_out, must_fix, warnings


# ---------------------------------------------------------------------------
# Replayable 8-type Query Engine
# ---------------------------------------------------------------------------

def _match_nodes(nodes: List[Dict[str, Any]], term: Optional[str], edges: Sequence[Dict[str, Any]] = (), source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    text = (term or "").strip().casefold()
    if not text:
        return []
    by_id = [n for n in nodes if n["node_id"].casefold() == text]
    if by_id:
        return by_id
    named = [(n, {str(s).casefold() for s in [n["label"], *n.get("aliases", [])]}) for n in nodes]
    exact = [n for n, labels in named if text in labels]
    norm = _canon_key(text)
    matched = exact or [n for n, labels in named if norm and any(_canon_key(lbl) == norm for lbl in labels)]
    if not matched and len(text) > 3:
        matched = [n for n, labels in named if any(text in s for s in labels)]
    if source_id and len(matched) == 2:
        ids = {n["node_id"] for n in matched}
        for e in edges:
            if e["source_id"] == source_id and e["relation_type"] == "instance_of" and ids == {e["source_node"], e["target_node"]}:
                return [n for n in matched if n["node_id"] == e["source_node"]]
    return matched


def execute_query(
    kg: Dict[str, Any],
    query_type: str,
    entity: Optional[str] = None,
    entity_a: Optional[str] = None,
    entity_b: Optional[str] = None,
    query_id: str = "q_1",
    source_id: Optional[str] = None,
    direction: str = "either",
    relation: Optional[str] = None,
    max_hops: int = 3,
) -> Dict[str, Any]:
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    node_map = {n["node_id"]: n for n in nodes}

    if source_id is not None:
        nodes = [n for n in nodes if source_id in n.get("source_ids", [])]
        edges = [e for e in edges if e["source_id"] == source_id]

    def rep_ids(nid: str) -> set[str]:
        return {nid} | {e["source_node"] for e in edges if e["relation_type"] == "instance_of" and e["target_node"] == nid}

    def fmt_edge(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "edge_id": e["edge_id"],
            "source": node_map.get(e["source_node"], {}).get("label"),
            "relation_type": e["relation_type"],
            "target": node_map.get(e["target_node"], {}).get("label"),
            "polarity": e.get("polarity", "affirmative"),
            "conditions": e.get("conditions", []),
            "evidence_strength": e.get("evidence_strength"),
            "source_id": e["source_id"],
            "paragraph_id": e["paragraph_id"],
            "location_type": e["location_type"],
            "pdf_page": e.get("pdf_page"),
            "quote_snippet": e["quote_snippet"],
        }

    if query_type == "paper_evidence":
        rows = [fmt_edge(e) for e in edges if e["relation_type"] != "instance_of"]
        return {
            "query_id": query_id, "query_type": query_type, "source_id": source_id,
            "answer_status": "supported" if rows else "no_relations_extracted",
            "hit_count": len(rows), "evidence_edges": rows,
        }

    if query_type == "cross_paper_shared_concepts":
        shared = [
            {
                "node_id": n["node_id"], "label": n["label"], "entity_type": n["entity_type"],
                "aliases": n["aliases"], "source_ids": n["source_ids"], "evidence_anchors": n["evidence_anchors"],
            }
            for n in nodes if len(n.get("source_ids", [])) >= 2
        ]
        return {
            "query_id": query_id, "query_type": query_type,
            "answer_status": "supported" if shared else "no_shared_concepts_found",
            "hit_count": len(shared), "shared_concepts": shared,
            "merge_decisions": kg.get("merge_decisions", []),
            "do_not_merge_records": kg.get("do_not_merge_records", []),
        }

    if query_type == "cross_paper_comparison":
        shared_nodes = [n for n in nodes if len(n.get("source_ids", [])) >= 2]
        if entity:
            matched = _match_nodes(shared_nodes, entity, edges, source_id)
            shared_nodes = matched
        comparisons = []
        for cn in shared_nodes:
            members = rep_ids(cn["node_id"])
            by_paper: Dict[str, List[Dict[str, Any]]] = {}
            for e in edges:
                if e["relation_type"] != "instance_of" and (e["source_node"] in members or e["target_node"] in members):
                    by_paper.setdefault(e["source_id"], []).append(fmt_edge(e))
            comparisons.append({
                "concept": cn["label"], "entity_type": cn["entity_type"],
                "source_ids": cn["source_ids"], "per_paper_evidence": by_paper,
            })
        return {
            "query_id": query_id, "query_type": query_type, "target": entity,
            "answer_status": "supported" if comparisons else "no_shared_concepts_found",
            "hit_count": len(comparisons), "comparisons": comparisons,
            "do_not_merge_records": kg.get("do_not_merge_records", []),
        }

    if query_type == "entity_neighborhood":
        target_node = None
        if entity:
            matches = _match_nodes(nodes, entity, edges, source_id)
            if len(matches) > 1:
                return {
                    "query_id": query_id, "query_type": query_type, "target": entity,
                    "answer_status": "ambiguous_entity", "hit_count": 0,
                    "candidates": [{"node_id": n["node_id"], "label": n["label"], "source_ids": n["source_ids"]} for n in matches],
                }
            target_node = matches[0] if matches else None
        elif nodes:
            deg: Dict[str, int] = {}
            for e in edges:
                deg[e["source_node"]] = deg.get(e["source_node"], 0) + 1
                deg[e["target_node"]] = deg.get(e["target_node"], 0) + 1
            cands = [n for n in nodes if n["entity_type"] != "Researcher"] or nodes
            target_node = max(cands, key=lambda n: deg.get(n["node_id"], 0))
        if target_node is None:
            return {"query_id": query_id, "query_type": query_type, "target": entity, "answer_status": "entity_not_found_unverifiable", "hit_count": 0, "subgraph_edges": []}
        members = rep_ids(target_node["node_id"])
        rel_edges = [fmt_edge(e) for e in edges if e["source_node"] in members or e["target_node"] in members]
        return {
            "query_id": query_id, "query_type": query_type, "target": target_node["label"],
            "answer_status": "supported" if rel_edges else "isolated_node",
            "hit_count": len(rel_edges), "target_node": target_node, "subgraph_edges": rel_edges,
        }

    if query_type == "method_metric_comparison":
        metric_rels = {"achieves", "increases", "decreases", "inhibits", "outperforms", "catalyzes", "comparable_to", "differs_from"}
        rows = [fmt_edge(e) for e in edges if e["relation_type"] in metric_rels]
        return {
            "query_id": query_id, "query_type": query_type,
            "answer_status": "supported" if rows else "no_metric_edges_unverifiable",
            "hit_count": len(rows), "comparison_rows": rows,
        }

    if query_type == "conflict_and_boundary_audit":
        sc: Dict[str, int] = {}
        for e in edges:
            st = e.get("evidence_strength", "unknown")
            sc[st] = sc.get(st, 0) + 1
        boundaries = [b for b in kg.get("boundary_records", []) if source_id is None or b["source_id"] == source_id]
        conflicts = [c for c in kg.get("conflicts", []) if source_id is None or source_id in c.get("sources", [])]
        unknowns = [u for u in kg.get("uncertainties", []) if source_id is None or u.get("source_id") == source_id]
        return {
            "query_id": query_id, "query_type": query_type, "answer_status": "supported",
            "hit_count": len(conflicts) + len(boundaries) + len(unknowns),
            "evidence_strength_breakdown": sc, "conflicts": conflicts,
            "boundary_evidence": boundaries, "uncertainties": unknowns,
        }

    if query_type == "relation_evidence_probe":
        if direction not in ("forward", "either"):
            direction = "either"
        left = _match_nodes(nodes, entity_a, edges, source_id)
        right = _match_nodes(nodes, entity_b, edges, source_id)
        if len(left) > 1 or len(right) > 1:
            return {"query_id": query_id, "query_type": query_type, "entity_a": entity_a, "entity_b": entity_b, "answer_status": "ambiguous_entity", "hit_count": 0, "evidence_edges": []}
        left_ids = rep_ids(left[0]["node_id"]) if left else set()
        right_ids = rep_ids(right[0]["node_id"]) if right else set()
        matches = []
        for e in edges:
            if relation and e["relation_type"] != relation:
                continue
            lm = {n["node_id"] for n in left} if e["relation_type"] == "instance_of" else left_ids
            rm = {n["node_id"] for n in right} if e["relation_type"] == "instance_of" else right_ids
            fwd = e["source_node"] in lm and e["target_node"] in rm
            bwd = e["source_node"] in rm and e["target_node"] in lm
            if fwd or (direction == "either" and bwd):
                matches.append(fmt_edge(e))
        return {
            "query_id": query_id, "query_type": query_type, "entity_a": entity_a, "entity_b": entity_b,
            "direction": direction, "relation_filter": relation,
            "has_direct_observation": any(r["evidence_strength"] in ("direct_author_evidence", "direct_tabular_evidence") for r in matches),
            "answer_status": "supported" if matches else "no_direct_evidence_unverifiable",
            "hit_count": len(matches), "evidence_edges": matches,
        }

    if query_type == "path_search":
        left = _match_nodes(nodes, entity_a, edges, source_id)
        right = _match_nodes(nodes, entity_b, edges, source_id)
        if not left or not right:
            return {"query_id": query_id, "query_type": query_type, "entity_a": entity_a, "entity_b": entity_b, "answer_status": "entity_not_found_unverifiable", "hit_count": 0, "paths": []}
        adj: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for e in edges:
            adj.setdefault(e["source_node"], []).append((e["target_node"], e))
            adj.setdefault(e["target_node"], []).append((e["source_node"], e))
        targets = set().union(*(rep_ids(r["node_id"]) for r in right))
        starts = list(set().union(*(rep_ids(l["node_id"]) for l in left)))
        queue: deque = deque((s, []) for s in starts)
        visited = set(starts)
        found_paths: List[List[Dict[str, Any]]] = []
        while queue and len(found_paths) < 3:
            cur, path = queue.popleft()
            if cur in targets and path:
                found_paths.append([fmt_edge(pe) for pe in path])
                continue
            if len(path) >= max_hops:
                continue
            for nxt, edge in adj.get(cur, []):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, [*path, edge]))
        return {
            "query_id": query_id, "query_type": query_type, "entity_a": entity_a, "entity_b": entity_b,
            "answer_status": "supported" if found_paths else "no_path_found_unverifiable",
            "hit_count": len(found_paths), "paths": found_paths,
        }

    return {"query_id": query_id, "query_type": query_type, "answer_status": "unsupported_query_type", "hit_count": 0}


def execute_all_queries(kg: Dict[str, Any], custom_queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run agent-specified queries plus default suite ensuring >= 3 distinct evidence-backed queries covering every paper."""
    specs: List[Tuple[str, Dict[str, Any]]] = []
    seen_specs = set()
    used_ids = set()

    def _add_spec(qid: Optional[str], spec: Dict[str, Any]) -> None:
        clean = {k: v for k, v in spec.items() if k != "query_id" and v is not None}
        if "query_type" not in clean:
            return
        sig = json.dumps(clean, sort_keys=True, ensure_ascii=False)
        if sig in seen_specs:
            return
        seen_specs.add(sig)
        rid = qid if (qid and qid not in used_ids) else f"q_{len(specs) + 1}"
        while rid in used_ids:
            rid = f"{rid}_x"
        used_ids.add(rid)
        specs.append((rid, clean))

    for q in custom_queries:
        if isinstance(q, dict) and q.get("query_type"):
            _add_spec(q.get("query_id"), q)

    for sm in kg.get("sources_manifest", []):
        _add_spec(None, {"query_type": "paper_evidence", "source_id": sm["source_id"]})
    _add_spec(None, {"query_type": "cross_paper_shared_concepts"})
    _add_spec(None, {"query_type": "cross_paper_comparison"})
    _add_spec(None, {"query_type": "entity_neighborhood"})
    _add_spec(None, {"query_type": "method_metric_comparison"})
    _add_spec(None, {"query_type": "conflict_and_boundary_audit"})

    edges = [e for e in kg.get("edges", []) if e["relation_type"] != "instance_of"]
    if edges:
        e0 = edges[0]
        _add_spec(None, {
            "query_type": "relation_evidence_probe",
            "entity_a": e0["source_node"],
            "entity_b": e0["target_node"],
            "source_id": e0["source_id"],
            "relation": e0["relation_type"],
            "direction": "forward",
        })

    results = []
    for qid, spec in specs:
        ans = execute_query(kg, query_id=qid, **spec)
        results.append({**ans, "parameters": spec})
    return results


# ---------------------------------------------------------------------------
# Exporters: GraphML XML, DOT, Markdown+Mermaid, PNG Preview
# ---------------------------------------------------------------------------

def export_to_graphml(kg: Dict[str, Any]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="d0" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="d1" for="node" attr.name="entity_type" attr.type="string"/>',
        '  <key id="d2" for="node" attr.name="source_ids" attr.type="string"/>',
        '  <key id="d3" for="node" attr.name="evidence_anchors_json" attr.type="string"/>',
        '  <key id="e0" for="edge" attr.name="relation_type" attr.type="string"/>',
        '  <key id="e1" for="edge" attr.name="polarity" attr.type="string"/>',
        '  <key id="e2" for="edge" attr.name="evidence_strength" attr.type="string"/>',
        '  <key id="e3" for="edge" attr.name="source_id" attr.type="string"/>',
        '  <key id="e4" for="edge" attr.name="paragraph_id" attr.type="string"/>',
        '  <key id="e5" for="edge" attr.name="quote_snippet" attr.type="string"/>',
        '  <key id="e6" for="edge" attr.name="location_type" attr.type="string"/>',
        '  <key id="e7" for="edge" attr.name="conditions_json" attr.type="string"/>',
        '  <graph id="LiteratureKG" edgedefault="directed">',
    ]
    for n in kg.get("nodes", []):
        nid = html_lib.escape(str(n["node_id"]), quote=True)
        lines.extend([
            f'    <node id="{nid}">',
            f'      <data key="d0">{html_lib.escape(str(n["label"]))}</data>',
            f'      <data key="d1">{html_lib.escape(str(n["entity_type"]))}</data>',
            f'      <data key="d2">{html_lib.escape(",".join(n.get("source_ids", [])))}</data>',
            f'      <data key="d3">{html_lib.escape(json.dumps(n.get("evidence_anchors", []), ensure_ascii=False))}</data>',
            "    </node>",
        ])
    for e in kg.get("edges", []):
        eid = html_lib.escape(str(e["edge_id"]), quote=True)
        sn = html_lib.escape(str(e["source_node"]), quote=True)
        tn = html_lib.escape(str(e["target_node"]), quote=True)
        lines.extend([
            f'    <edge id="{eid}" source="{sn}" target="{tn}">',
            f'      <data key="e0">{html_lib.escape(str(e["relation_type"]))}</data>',
            f'      <data key="e1">{html_lib.escape(str(e.get("polarity", "affirmative")))}</data>',
            f'      <data key="e2">{html_lib.escape(str(e.get("evidence_strength", "")))}</data>',
            f'      <data key="e3">{html_lib.escape(str(e.get("source_id", "")))}</data>',
            f'      <data key="e4">{html_lib.escape(str(e.get("paragraph_id", "")))}</data>',
            f'      <data key="e5">{html_lib.escape(str(e["quote_snippet"]))}</data>',
            f'      <data key="e6">{html_lib.escape(str(e["location_type"]))}</data>',
            f'      <data key="e7">{html_lib.escape(json.dumps(e.get("conditions", []), ensure_ascii=False))}</data>',
            "    </edge>",
        ])
    lines.extend(["  </graph>", "</graphml>"])
    xml_str = "\n".join(lines) + "\n"
    ET.fromstring(xml_str)
    return xml_str


def export_to_dot(kg: Dict[str, Any]) -> str:
    def _esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    lines = ["digraph LiteratureKG {", '  rankdir="LR";', '  node [shape=box, style="rounded,filled", fillcolor="#F5F8FA"];']
    for n in kg.get("nodes", []):
        shape = "ellipse" if n["entity_type"].endswith("Instance") else "box"
        fill = "#EAF4F4" if len(n.get("source_ids", [])) >= 2 else "#F5F8FA"
        lines.append(f'  "{_esc(n["node_id"])}" [label="{_esc(n["label"])}\\n({n["entity_type"]})", shape={shape}, fillcolor="{fill}"];')
    for e in kg.get("edges", []):
        style = "dashed" if e["relation_type"] == "instance_of" else "solid"
        lines.append(f'  "{_esc(e["source_node"])}" -> "{_esc(e["target_node"])}" [label="{_esc(e["relation_type"])} [{_esc(e["source_id"])}]", style={style}];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def export_to_markdown(kg: Dict[str, Any], queries: List[Dict[str, Any]]) -> str:
    lines: List[str] = [
        "# Literature Knowledge Graph Report",
        "",
        f"- **Status:** `{kg.get('status')}` | **Nodes:** {len(kg.get('nodes', []))} | **Edges:** {len(kg.get('edges', []))} | **Conflicts:** {len(kg.get('conflicts', []))}",
        "",
        "## 1. Corpus Sources Manifest",
        "",
        "| source_id | Title | Authors | DOI | Year | Location Type |",
        "|---|---|---|---|---|---|",
    ]
    for sm in kg.get("sources_manifest", []):
        auths = ", ".join(str(a) for a in (sm.get("authors") or [])).replace("|", " ")
        lines.append(
            f"| `{sm.get('source_id')}` | {str(sm.get('title')).replace('|', ' ')} | {auths} | `{sm.get('doi')}` | {sm.get('year')} | `{sm.get('location_type')}` |"
        )
    lines.append("")

    if kg.get("edges"):
        lines.extend(["## 2. Knowledge Graph Preview (Mermaid)", "", "```mermaid", "flowchart LR"])
        for n in kg.get("nodes", [])[:30]:
            safe_lbl = str(n["label"]).replace('"', "'")
            lines.append(f'  {n["node_id"]}["{safe_lbl} ({n["entity_type"]})"]')
        for e in kg.get("edges", [])[:25]:
            lines.append(f'  {e["source_node"]} -->|"{e["relation_type"]} [{e["source_id"]}]"| {e["target_node"]}')
        lines.extend(["```", ""])

    lines.extend([
        "## 3. Alignment & Do-Not-Merge Audit",
        "",
        f"- **Canonical Merges (`merge_decisions`):** {len(kg.get('merge_decisions', []))}",
        f"- **Protected Separations (`do_not_merge_records`):** {len(kg.get('do_not_merge_records', []))}",
        "",
    ])
    for dnm in kg.get("do_not_merge_records", []):
        lines.append(f"- **DO NOT MERGE `{dnm.get('generic_term')}`:** {dnm.get('reason')} Instances: `{dnm.get('instance_nodes', [])}`")
    lines.append("")

    if kg.get("conflicts"):
        lines.extend(["## 4. Conflicts & Contrasting Contexts", ""])
        for c in kg["conflicts"]:
            lines.append(f"- `{c['conflict_id']}` ({c['classification']}): {c['message']} Edges: `{c['edge_ids']}`")
        lines.append("")

    lines.extend(["## 5. Executed Graph Queries", ""])
    for q in queries:
        lines.append(f"### `{q.get('query_id')}` — `{q.get('query_type')}` (`{q.get('answer_status')}`, hits: {q.get('hit_count')})")
        rows = q.get("subgraph_edges") or q.get("evidence_edges") or q.get("comparison_rows") or []
        for row in rows[:8]:
            subj = row.get("subject", row.get("source", ""))
            rel = row.get("relation", row.get("relation_type", ""))
            lines.append(f"- {subj} -> {rel} -> {row['target']} ({row['source_id']}, {row['paragraph_id']}) — {row['quote_snippet']}")
        for comp in q.get("comparisons", [])[:5]:
            lines.append(f"- Concept `{comp['concept']}` across {comp['source_ids']}: { {k: len(v) for k, v in comp['per_paper_evidence'].items()} }")
        for path in q.get("paths", [])[:3]:
            chain = " ; ".join(f"{s['source']} -[{s['relation_type']}]-> {s['target']} ({s['source_id']}:{s['paragraph_id']})" for s in path)
            lines.append(f"- Path: {chain}")
        lines.append("")
    return "\n".join(lines)


def render_preview(kg: Dict[str, Any], target: Path) -> bool:
    try:
        import fitz  # type: ignore
    except ImportError:
        return False
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for edge in kg.get("edges", []):
        if edge["relation_type"] != "instance_of":
            by_source.setdefault(edge["source_id"], []).append(edge)
    if not by_source:
        return False
    selected: List[Dict[str, Any]] = []
    for level in range(max(map(len, by_source.values()))):
        selected.extend(rows[level] for rows in by_source.values() if len(rows) > level)
        if len(selected) >= 10:
            break
    selected = selected[:10]
    nodes = {n["node_id"]: n for n in kg["nodes"]}
    dark, soft, teal = (0.09, 0.16, 0.23), (0.29, 0.37, 0.44), (0.04, 0.39, 0.39)
    palette = [(0.12, 0.42, 0.62), (0.65, 0.32, 0.15), (0.20, 0.52, 0.32), (0.48, 0.26, 0.60)]
    paper_colors = {sm["source_id"]: palette[i % len(palette)] for i, sm in enumerate(kg.get("sources_manifest", []))}

    doc = fitz.open()
    try:
        page = doc.new_page(width=880, height=94 + 75 * len(selected))

        def put(x: float, y: float, value: str, size: float = 10, color=dark, bold: bool = False) -> None:
            font = "china-s" if any(ord(ch) > 255 for ch in value) else ("hebo" if bold else "helv")
            page.insert_text((x, y), value, fontsize=size, fontname=font, color=color)

        def label_lines(text: str, width: float, size: float) -> List[str]:
            lines: List[str] = []
            text = " ".join(text.split())
            while text and len(lines) < 2:
                font = "china-s" if any(ord(ch) > 255 for ch in text) else "helv"
                length = 0
                for length in range(1, len(text) + 1):
                    if fitz.get_text_length(text[:length], fontname=font, fontsize=size) > width:
                        length = max(length - 1, 1)
                        break
                split = text.rfind(" ", 0, length + 1) if length < len(text) else length
                if split < max(1, length // 2):
                    split = length
                lines.append(text[:split].strip())
                text = text[split:].strip()
            if text:
                lines[-1] = lines[-1].rstrip("., ") + "..."
            return lines

        put(26, 36, "Evidence-Grounded Multi-Paper Knowledge Graph", 20, bold=True)
        covered = len({e["source_id"] for e in selected})
        total = sum(map(len, by_source.values()))
        put(26, 56, f"{len(selected)} of {total} substantive edges  |  {covered} of {len(kg['sources_manifest'])} sources", 11, soft)
        for i, edge in enumerate(selected):
            top = 74 + 75 * i
            scolor = paper_colors.get(edge["source_id"], teal)
            for x, node_key in ((26, "source_node"), (564, "target_node")):
                node = nodes[edge[node_key]]
                page.draw_rect(fitz.Rect(x, top, x + 286, top + 49), color=(0.77, 0.83, 0.84), fill=(0.95, 0.97, 0.97), width=0.8)
                put(x + 10, top + 12, node["entity_type"], 8, scolor, bold=True)
                for ln_i, ln in enumerate(label_lines(node["label"], 262, 10.5)):
                    put(x + 10, top + 27 + 12 * ln_i, ln, 10.5)
            mid_y = top + 28
            page.draw_line((312, mid_y), (561, mid_y), color=scolor, width=1.3)
            page.draw_polyline([(554, mid_y - 3), (561, mid_y), (554, mid_y + 3)], color=scolor, width=1.3)
            put(326, top + 20, edge["relation_type"].replace("_", " "), 10, scolor, bold=True)
            put(26, top + 62, f"[{edge['source_id']}:{edge['paragraph_id']}] {edge['edge_id']} ({edge['evidence_strength']})", 8.5, soft)
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(target))
    finally:
        doc.close()
    return True


def export_all(kg: Dict[str, Any], queries: List[Dict[str, Any]], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gml = export_to_graphml(kg)
    dot = export_to_dot(kg)
    md = export_to_markdown(kg, queries)
    write_text_lf(out_dir / "kg.graphml", gml)
    write_text_lf(out_dir / "kg.dot", dot)
    write_text_lf(out_dir / "kg_report.md", md)
    artifacts = {
        "graph_json": "kg.json",
        "queries_json": "executed_queries.json",
        "graphml": "kg.graphml",
        "dot": "kg.dot",
        "report": "kg_report.md",
    }
    preview = out_dir / "graph_preview.png"
    if render_preview(kg, preview):
        artifacts["preview"] = "graph_preview.png"
    else:
        preview.unlink(missing_ok=True)
    return artifacts
