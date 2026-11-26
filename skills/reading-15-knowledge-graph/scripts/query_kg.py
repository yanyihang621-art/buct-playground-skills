#!/usr/bin/env python3
"""Execute real subgraph and evidence-grounded queries against kg.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic
from evidence_claims import canon


def _matches(nodes, term, edges=(), source_id=None):
    text = (term or "").strip().casefold()
    if not text:
        return []
    by_id = [n for n in nodes if n["node_id"].casefold() == text]
    if by_id:
        return by_id
    named = [(n, {str(s).casefold() for s in [n["label"], *n.get("aliases", [])]}) for n in nodes]
    exact = [n for n, labels in named if text in labels]
    normal = canon(text)
    matched = exact or [node for node, labels in named if normal and any(canon(label) == normal for label in labels)]
    if not matched and len(text) > 3:
        matched = [n for n, labels in named if any(text in s for s in labels)]
    if source_id and len(matched) == 2:
        ids = {node["node_id"] for node in matched}
        for e in edges:
            if (e["source_id"] == source_id and e["relation_type"] == "instance_of"
                    and ids == {e["source_node"], e["target_node"]}):
                return [node for node in matched if node["node_id"] == e["source_node"]]
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
) -> Dict[str, Any]:
    """Run one deterministic query over kg.json and return subgraph + grounded evidence."""
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    node_map = {n["node_id"]: n for n in nodes}

    if source_id is not None:
        nodes = [n for n in nodes if source_id in n["source_ids"]]
        edges = [e for e in edges if e["source_id"] == source_id]
    def represented_ids(node_id):
        return {node_id} | {e["source_node"] for e in edges if e["relation_type"] == "instance_of"
                            and e["target_node"] == node_id}

    if query_type == "paper_evidence":
        rows = [
            {"edge_id": e["edge_id"], "source": node_map[e["source_node"]]["label"],
             "relation": e["relation_type"], "target": node_map[e["target_node"]]["label"],
             "conditions": e.get("conditions", []), "evidence_strength": e["evidence_strength"],
             "source_id": e["source_id"], "paragraph_id": e["paragraph_id"],
             "location_type": e["location_type"], "pdf_page": e.get("pdf_page"),
             "quote_snippet": e["quote_snippet"]}
            for e in edges if e["relation_type"] != "instance_of"
        ]
        return {"query_id": query_id, "query_type": query_type, "source_id": source_id,
                "answer_status": "supported" if rows else "no_relations_extracted",
                "hit_count": len(rows), "evidence_edges": rows}
    if query_type == "cross_paper_shared_concepts":
        shared = [
            {
                "node_id": n["node_id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "aliases": n["aliases"],
                "source_ids": n["source_ids"],
                "evidence_anchors": n["evidence_anchors"],
            }
            for n in nodes
            if len(n.get("source_ids", [])) >= 2
        ]
        return {
            "query_id": query_id,
            "query_type": query_type,
            "answer_status": "supported" if shared else "no_shared_concepts_found",
            "hit_count": len(shared),
            "shared_concepts": shared,
            "merge_decisions": kg.get("merge_decisions", []),
            "do_not_merge_records": kg.get("do_not_merge_records", []),
        }

    if query_type == "entity_neighborhood":
        target_node = None
        if entity:
            matches = _matches(nodes, entity, edges, source_id)
            if len(matches) > 1:
                return {"query_id": query_id, "query_type": query_type, "target": entity,
                        "answer_status": "ambiguous_entity", "hit_count": 0,
                        "candidates": [{"node_id": n["node_id"], "label": n["label"], "source_ids": n["source_ids"]} for n in matches]}
            target_node = matches[0] if matches else None
        elif nodes:
            # Pick highest-degree non-Researcher node
            deg: Dict[str, int] = {}
            for e in edges:
                deg[e["source_node"]] = deg.get(e["source_node"], 0) + 1
                deg[e["target_node"]] = deg.get(e["target_node"], 0) + 1
            cands = [n for n in nodes if n["entity_type"] != "Researcher"] or nodes
            target_node = max(cands, key=lambda n: deg.get(n["node_id"], 0))

        if target_node is None:
            return {
                "query_id": query_id,
                "query_type": query_type,
                "target": entity,
                "answer_status": "entity_not_found_unverifiable",
                "hit_count": 0,
                "subgraph_edges": [],
            }

        tid = target_node["node_id"]
        members = represented_ids(tid)
        rel_edges = [e for e in edges if e["source_node"] in members or e["target_node"] in members]
        formatted_edges = [
            {
                "edge_id": e["edge_id"],
                "source": node_map.get(e["source_node"], {}).get("label"),
                "relation_type": e["relation_type"],
                "target": node_map.get(e["target_node"], {}).get("label"),
                "conditions": e.get("conditions", []),
                "evidence_strength": e.get("evidence_strength"),
                "source_id": e["source_id"],
                "paragraph_id": e["paragraph_id"],
                "location_type": e["location_type"],
                "pdf_page": e.get("pdf_page"),
                "quote_snippet": e["quote_snippet"],
            }
            for e in rel_edges
        ]
        return {
            "query_id": query_id,
            "query_type": query_type,
            "target": target_node["label"],
            "answer_status": "supported" if formatted_edges else "isolated_node",
            "hit_count": len(formatted_edges),
            "target_node": target_node,
            "subgraph_edges": formatted_edges,
        }

    if query_type == "method_metric_comparison":
        rows = []
        for e in edges:
            if e["relation_type"] in ("achieves", "increases", "decreases", "inhibits", "outperforms", "catalyzes"):
                rows.append(
                    {
                        "edge_id": e["edge_id"],
                        "source_id": e["source_id"],
                        "paragraph_id": e["paragraph_id"],
                        "location_type": e["location_type"],
                        "pdf_page": e.get("pdf_page"),
                        "subject": node_map.get(e["source_node"], {}).get("label"),
                        "relation": e["relation_type"],
                        "target": node_map.get(e["target_node"], {}).get("label"),
                        "conditions": e.get("conditions", []),
                        "evidence_strength": e["evidence_strength"],
                        "quote_snippet": e["quote_snippet"],
                    }
                )
        return {
            "query_id": query_id,
            "query_type": query_type,
            "answer_status": "supported" if rows else "no_metric_edges_unverifiable",
            "hit_count": len(rows),
            "comparison_rows": rows,
        }

    if query_type == "conflict_and_boundary_audit":
        strength_counts: Dict[str, int] = {}
        for e in edges:
            st = e.get("evidence_strength", "unknown")
            strength_counts[st] = strength_counts.get(st, 0) + 1
        boundaries = [b for b in kg.get("boundary_records", []) if source_id is None or b["source_id"] == source_id]
        conflicts = [c for c in kg.get("conflicts", []) if source_id is None or source_id in c.get("sources", [])]
        unknowns = [u for u in kg.get("uncertainties", []) if source_id is None or u.get("source_id") == source_id]
        return {
            "query_id": query_id,
            "query_type": query_type,
            "answer_status": "supported",
            "hit_count": len(conflicts) + len(unknowns) + len(boundaries),
            "evidence_strength_breakdown": strength_counts,
            "conflicts": conflicts, "boundary_evidence": boundaries, "uncertainties": unknowns,
        }

    if query_type == "relation_evidence_probe":
        if direction not in ("forward", "either"):
            raise ValueError("direction must be 'forward' or 'either'")
        left = _matches(nodes, entity_a, edges, source_id)
        right = _matches(nodes, entity_b, edges, source_id)
        matches = []
        if len(left) > 1 or len(right) > 1:
            return {"query_id": query_id, "query_type": query_type, "entity_a": entity_a,
                    "entity_b": entity_b, "answer_status": "ambiguous_entity", "hit_count": 0, "evidence_edges": []}
        left_ids = represented_ids(left[0]["node_id"]) if left else set()
        right_ids = represented_ids(right[0]["node_id"]) if right else set()
        for e in edges:
            if relation and e["relation_type"] != relation:
                continue
            # Identity links are structural. Include them only for an explicit
            # identity question or a literal pair of graph endpoints.
            if e["relation_type"] == "instance_of":
                left_match = {n["node_id"] for n in left}
                right_match = {n["node_id"] for n in right}
            else:
                left_match, right_match = left_ids, right_ids
            forwards = e["source_node"] in left_match and e["target_node"] in right_match
            backwards = e["source_node"] in right_match and e["target_node"] in left_match
            if forwards or (direction == "either" and backwards):
                matches.append(
                    {
                        "edge_id": e["edge_id"],
                        "source": node_map.get(e["source_node"], {}).get("label"),
                        "relation": e["relation_type"],
                        "target": node_map.get(e["target_node"], {}).get("label"),
                        "source_id": e["source_id"],
                        "paragraph_id": e["paragraph_id"],
                        "location_type": e["location_type"], "pdf_page": e.get("pdf_page"),
                        "conditions": e.get("conditions", []), "evidence_strength": e["evidence_strength"],
                        "quote_snippet": e["quote_snippet"],
                    }
                )
        return {
            "query_id": query_id,
            "query_type": query_type,
            "entity_a": entity_a,
            "entity_b": entity_b,
            "direction": direction,
            "relation_filter": relation,
            "has_direct_observation": any(row["evidence_strength"] in ("direct_author_evidence", "direct_tabular_evidence")
                                          for row in matches),
            "answer_status": "supported" if matches else "no_direct_evidence_unverifiable",
            "hit_count": len(matches),
            "evidence_edges": matches,
        }

    return {"query_id": query_id, "query_type": query_type, "answer_status": "unsupported_query_type", "hit_count": 0}


def run_default_query_suite(kg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run actual corpus queries with replayable parameters and per-paper evidence."""
    params = [
        {"query_type": "cross_paper_shared_concepts"}, {"query_type": "entity_neighborhood"},
        {"query_type": "method_metric_comparison"}, {"query_type": "conflict_and_boundary_audit"},
    ]
    for entry in kg.get("sources_manifest", []):
        params.append({"query_type": "paper_evidence", "source_id": entry["source_id"]})
    edges = [e for e in kg.get("edges", []) if e["relation_type"] != "instance_of"]
    if edges:
        edge = edges[0]
        params.append({"query_type": "relation_evidence_probe", "entity_a": edge["source_node"],
                       "entity_b": edge["target_node"], "source_id": edge["source_id"],
                       "relation": edge["relation_type"], "direction": "forward"})
    results = []
    for index, spec in enumerate(params, 1):
        query = execute_query(kg, query_id=f"q_{index}", **spec)
        results.append({**query, "parameters": spec})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute grounded graph queries over kg.json.")
    parser.add_argument("--kg", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-default-suite", action="store_true")
    parser.add_argument("--query-type", default="cross_paper_shared_concepts")
    parser.add_argument("--entity", default=None)
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--entity-a", default=None)
    parser.add_argument("--entity-b", default=None)
    parser.add_argument("--direction", choices=("forward", "either"), default="either")
    parser.add_argument("--relation", help="Restrict a relation probe to one ontology predicate.")
    args = parser.parse_args()

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    if args.run_default_suite:
        out_obj = {"queries": run_default_query_suite(kg)}
    else:
        spec = {"query_type": args.query_type, "entity": args.entity, "entity_a": args.entity_a,
                "entity_b": args.entity_b, "source_id": args.source_id}
        if args.query_type == "relation_evidence_probe":
            spec.update(direction=args.direction, relation=args.relation)
        out_obj = {"queries": [{**execute_query(kg, **spec), "parameters": spec}]}
    write_json_atomic(args.out, out_obj)
    print(json.dumps({"executed_query_count": len(out_obj["queries"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
