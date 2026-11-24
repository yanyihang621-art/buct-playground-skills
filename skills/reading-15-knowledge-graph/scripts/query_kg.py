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


def execute_query(
    kg: Dict[str, Any],
    query_type: str,
    entity: Optional[str] = None,
    entity_a: Optional[str] = None,
    entity_b: Optional[str] = None,
    query_id: str = "q_1",
) -> Dict[str, Any]:
    """Run one deterministic query over kg.json and return subgraph + grounded evidence."""
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])
    node_map = {n["node_id"]: n for n in nodes}

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
            "answer_status": "supported" if shared or kg.get("do_not_merge_records") else "no_shared_concepts_found",
            "hit_count": len(shared),
            "shared_concepts": shared,
            "merge_decisions": kg.get("merge_decisions", []),
            "do_not_merge_records": kg.get("do_not_merge_records", []),
        }

    if query_type == "entity_neighborhood":
        target_node = None
        if entity:
            low = entity.lower()
            target_node = next((n for n in nodes if low in n["label"].lower() or any(low in a.lower() for a in n.get("aliases", []))), None)
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
        rel_edges = [e for e in edges if e["source_node"] == tid or e["target_node"] == tid]
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
        return {
            "query_id": query_id,
            "query_type": query_type,
            "answer_status": "supported",
            "hit_count": len(kg.get("conflicts", [])) + len(kg.get("uncertainties", [])),
            "evidence_strength_breakdown": strength_counts,
            "conflicts": kg.get("conflicts", []),
            "uncertainties": kg.get("uncertainties", []),
        }

    if query_type == "relation_evidence_probe":
        la = (entity_a or "").lower()
        lb = (entity_b or "").lower()
        matches = []
        for e in edges:
            sl = node_map.get(e["source_node"], {}).get("label", "").lower()
            tl = node_map.get(e["target_node"], {}).get("label", "").lower()
            if (la in sl and lb in tl) or (lb in sl and la in tl):
                matches.append(
                    {
                        "edge_id": e["edge_id"],
                        "source": node_map.get(e["source_node"], {}).get("label"),
                        "relation": e["relation_type"],
                        "target": node_map.get(e["target_node"], {}).get("label"),
                        "source_id": e["source_id"],
                        "paragraph_id": e["paragraph_id"],
                        "quote_snippet": e["quote_snippet"],
                    }
                )
        return {
            "query_id": query_id,
            "query_type": query_type,
            "entity_a": entity_a,
            "entity_b": entity_b,
            "answer_status": "supported" if matches else "no_direct_evidence_unverifiable",
            "hit_count": len(matches),
            "evidence_edges": matches,
        }

    return {"query_id": query_id, "query_type": query_type, "answer_status": "unsupported_query_type", "hit_count": 0}


def run_default_query_suite(kg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute 4+ real queries covering cross-paper alignment, neighborhood, comparison, conflicts, and negative probe."""
    return [
        execute_query(kg, "cross_paper_shared_concepts", query_id="q_1"),
        execute_query(kg, "entity_neighborhood", query_id="q_2"),
        execute_query(kg, "method_metric_comparison", query_id="q_3"),
        execute_query(kg, "conflict_and_boundary_audit", query_id="q_4"),
        execute_query(
            kg,
            "relation_evidence_probe",
            entity_a="Nonexistent Unreported Compound X",
            entity_b="Unreported Metric Y",
            query_id="q_5",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute grounded graph queries over kg.json.")
    parser.add_argument("--kg", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-default-suite", action="store_true")
    parser.add_argument("--query-type", default="cross_paper_shared_concepts")
    parser.add_argument("--entity", default=None)
    parser.add_argument("--entity-a", default=None)
    parser.add_argument("--entity-b", default=None)
    args = parser.parse_args()

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    if args.run_default_suite:
        out_obj = {"queries": run_default_query_suite(kg)}
    else:
        out_obj = {
            "queries": [
                execute_query(
                    kg,
                    args.query_type,
                    entity=args.entity,
                    entity_a=args.entity_a,
                    entity_b=args.entity_b,
                )
            ]
        }
    write_json_atomic(args.out, out_obj)
    print(json.dumps({"executed_query_count": len(out_obj["queries"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
