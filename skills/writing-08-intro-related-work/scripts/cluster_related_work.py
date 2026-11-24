#!/usr/bin/env python3
"""Group related works by technical route rather than chronological stitching."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rw_utils import write_json_atomic, write_text_lf

TAXONOMY_HINTS = [
    ("Cu-Based / Oxide Catalysts", ("cu-zno", "copper", "al2o3", "oxide", "CuZn", "铜基")),
    ("Noble Metal / Bimetallic Catalysts", ("pd", "pt", "rh", "ru", "noble", "bimetallic", "贵金属")),
    ("MOF / Porous Framework Catalysts", ("mof", "zeolite", "zif", "framework", "metal-organic", "框架")),
    ("Operando / Mechanistic Characterization", ("operando", "in situ", "ftir", "xps", "dft", "mechanism", "原位", "机理")),
]


def _infer_route(paper: Dict[str, Any]) -> str:
    explicit = str(paper.get("route", "")).strip()
    if explicit:
        return explicit
    blob = f"{paper.get('title', '')} {paper.get('key_finding', '')} {paper.get('problem_setting', '')}".lower()
    for label, kws in TAXONOMY_HINTS:
        if any(kw in blob for kw in kws):
            return label
    return "General / Baseline Approaches"


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
        members_sorted = sorted(members, key=lambda x: (x.get("year") or 9999, x.get("cite_key", "")))
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
        "| 技术路线 (Route) | 文献 Key | 问题设定 (Problem) | 评价体系 (Evaluation) | 核心结论 (Key Finding) | 局限性 (Limitation) |",
        "|---|---|---|---|---|---|",
    ]
    for cl in clusters:
        for p in cl["papers"]:
            lines.append(
                f"| {cl['route_name']} | `@{p['cite_key']}` ({p.get('year') or 's.a.'}) | "
                f"{(p.get('problem_setting') or '-').replace('|', '/')} | "
                f"{(p.get('evaluation_system') or '-').replace('|', '/')} | "
                f"{(p.get('key_finding') or p.get('title') or '-').replace('|', '/')} | "
                f"{(p.get('limitation') or '[Unspecified]').replace('|', '/')} |"
            )
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
