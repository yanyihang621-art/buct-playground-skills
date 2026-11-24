#!/usr/bin/env python3
"""Derive research gaps strictly from cited limitations and align contributions to author evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rw_utils import write_json_atomic


def build_gap_and_contribution_map(materials: Dict[str, Any], clusters: Dict[str, Any]) -> Dict[str, Any]:
    """Derive literature-grounded gaps and 1-to-1 evidence-anchored contribution claims."""
    papers_by_key = {p["cite_key"]: p for p in materials.get("papers", [])}
    study_facts = materials.get("study_facts", {})

    # 1. Derive gaps directly from cited paper limitations per route cluster
    gaps: List[Dict[str, Any]] = []
    for cl in clusters.get("clusters", []):
        limiting_keys = [p["cite_key"] for p in cl["papers"] if p.get("limitation")]
        if not limiting_keys:
            continue
        lim_summary = "; ".join(f"{papers_by_key[k]['limitation']} [@{k}]" for k in limiting_keys)
        gaps.append(
            {
                "gap_id": f"gap_{len(gaps) + 1:02d}",
                "route_name": cl["route_name"],
                "gap_statement": f"Within {cl['route_name']}, existing studies leave unresolved limitations: {lim_summary}.",
                "derived_from_cite_keys": limiting_keys,
                "author_judgment": False,
                "grounded": True,
            }
        )

    # Also integrate explicit study-specific gaps if they cite real keys
    for idx, eg in enumerate(study_facts.get("target_gaps", []), start=1):
        if isinstance(eg, dict):
            cites = [k for k in eg.get("derived_from_cite_keys", []) if k in papers_by_key]
            gaps.append(
                {
                    "gap_id": eg.get("gap_id") or f"gap_custom_{idx:02d}",
                    "route_name": eg.get("route_name", "Target Problem"),
                    "gap_statement": str(eg.get("gap_statement", "")),
                    "derived_from_cite_keys": cites,
                    "author_judgment": not bool(cites),
                    "grounded": bool(cites),
                }
            )

    # 2. Align each contribution claim 1-to-1 to author study evidence anchors
    contributions: List[Dict[str, Any]] = []
    evidence_items = study_facts.get("contributions", [])
    for idx, item in enumerate(evidence_items, start=1):
        if not isinstance(item, dict):
            continue
        anchor = item.get("evidence_anchor")
        has_valid_anchor = isinstance(anchor, dict) and any(
            anchor.get(k) for k in ("figure_or_table_ref", "method_ref", "metric_value")
        )
        default_gap = gaps[min(idx - 1, len(gaps) - 1)]["gap_id"] if gaps else "gap_unlinked"
        contributions.append(
            {
                "contrib_id": item.get("contrib_id") or f"contrib_{idx:02d}",
                "claim": str(item.get("claim", "")).strip(),
                "addresses_gap_id": str(item.get("addresses_gap_id") or default_gap),
                "evidence_anchor": anchor if isinstance(anchor, dict) else None,
                "grounded": has_valid_anchor,
            }
        )

    return {
        "schema_version": "writing-08-gap-contrib-map/v1",
        "research_gaps": gaps,
        "contributions": contributions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build evidence-grounded gap & contribution map.")
    parser.add_argument("--materials", required=True)
    parser.add_argument("--clusters", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))
    clus = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    res = build_gap_and_contribution_map(mats, clus)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"gaps": len(res["research_gaps"]), "contributions": len(res["contributions"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
