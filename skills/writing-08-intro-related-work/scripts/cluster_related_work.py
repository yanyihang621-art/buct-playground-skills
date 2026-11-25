#!/usr/bin/env python3
"""Group related works by technical route rather than chronological stitching."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rw_utils import write_json_atomic, write_text_lf

def _infer_route(paper: Dict[str, Any]) -> str:
    return str(paper.get("route") or "").strip() or "Unclassified (review source)"


def _cell(value: Any) -> str:
    return " ".join(str(value or "—").split()).replace("|", "\\|")


def cluster_related_works(materials: Dict[str, Any]) -> Dict[str, Any]:
    """Group ingested literature into technical routes and render comparison_matrix.md."""
    papers = materials.get("papers", [])
    clusters_map: Dict[str, List[Dict[str, Any]]] = {}
    for p in papers:
        route = _infer_route(p)
        clusters_map.setdefault(route, []).append(p)

    clusters: List[Dict[str, Any]] = []
    for idx, (route_name, members) in enumerate(clusters_map.items(), start=1):
        # Order within route chronologically, while comparing across routes
        members_sorted = sorted(members, key=lambda x: (str(x.get("year") or "9999"), x.get("cite_key", "")))
        limitations = [m["limitation"] for m in members_sorted if m.get("limitation")]
        clusters.append(
            {
                "cluster_id": f"route_{idx:02d}",
                "route_name": route_name,
                "paper_count": len(members_sorted),
                "cite_keys": [m["cite_key"] for m in members_sorted],
                "shared_limitations": limitations,
                "papers": members_sorted,
            }
        )

    lines: List[str] = [
        "# 相关工作技术路线对比矩阵 (Related Work Comparison Matrix)",
        "",
        "| Route / comparison axis | Study | Setting | Evaluation | Finding | Boundary | Source |",
        "|---|---|---|---|---|---|---|",
    ]
    for cl in clusters:
        for p in cl["papers"]:
            locators = [f"{e['path']} {e['locator']}" for e in p.get("evidence", [])]
            cells = [cl["route_name"], f"@{p['cite_key']}", p.get("problem_setting"),
                     p.get("evaluation_system"), p.get("key_finding"), p.get("limitation"),
                     ", ".join(dict.fromkeys(locators or p.get("source_ids", [])))]
            lines.append("| " + " | ".join(_cell(v) for v in cells) + " |")
    lines.append("")

    return {
        "schema_version": "writing-08-clusters/v1",
        "route_cluster_count": len(clusters),
        "clusters": clusters,
        "comparison_matrix_md": "\n".join(lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster related work papers by methodological route.")
    parser.add_argument("--materials", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-matrix", required=True)
    args = parser.parse_args()

    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))
    res = cluster_related_works(mats)
    write_text_lf(Path(args.out_matrix), res.pop("comparison_matrix_md"))
    write_json_atomic(Path(args.out_json), res)
    print(json.dumps({"route_cluster_count": res["route_cluster_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
