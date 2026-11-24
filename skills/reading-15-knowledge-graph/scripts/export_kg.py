#!/usr/bin/env python3
"""Export Literature Knowledge Graph to valid GraphML XML, Graphviz DOT, and Markdown+Mermaid report."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_text_lf


def export_to_graphml(kg: Dict[str, Any]) -> str:
    """Serialize kg.json into valid GraphML XML."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="d0" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="d1" for="node" attr.name="entity_type" attr.type="string"/>',
        '  <key id="d2" for="node" attr.name="source_ids" attr.type="string"/>',
        '  <key id="e0" for="edge" attr.name="relation_type" attr.type="string"/>',
        '  <key id="e1" for="edge" attr.name="evidence_strength" attr.type="string"/>',
        '  <key id="e2" for="edge" attr.name="source_id" attr.type="string"/>',
        '  <key id="e3" for="edge" attr.name="paragraph_id" attr.type="string"/>',
        '  <graph id="LiteratureKG" edgedefault="directed">',
    ]
    for n in kg.get("nodes", []):
        nid = html_lib.escape(str(n["node_id"]), quote=True)
        lbl = html_lib.escape(str(n["label"]))
        et = html_lib.escape(str(n["entity_type"]))
        sids = html_lib.escape(",".join(n.get("source_ids", [])))
        lines.append(f'    <node id="{nid}">')
        lines.append(f'      <data key="d0">{lbl}</data>')
        lines.append(f'      <data key="d1">{et}</data>')
        lines.append(f'      <data key="d2">{sids}</data>')
        lines.append("    </node>")

    for e in kg.get("edges", []):
        eid = html_lib.escape(str(e["edge_id"]), quote=True)
        sn = html_lib.escape(str(e["source_node"]), quote=True)
        tn = html_lib.escape(str(e["target_node"]), quote=True)
        rt = html_lib.escape(str(e["relation_type"]))
        es = html_lib.escape(str(e.get("evidence_strength", "")))
        sid = html_lib.escape(str(e.get("source_id", "")))
        pid = html_lib.escape(str(e.get("paragraph_id", "")))
        lines.append(f'    <edge id="{eid}" source="{sn}" target="{tn}">')
        lines.append(f'      <data key="e0">{rt}</data>')
        lines.append(f'      <data key="e1">{es}</data>')
        lines.append(f'      <data key="e2">{sid}</data>')
        lines.append(f'      <data key="e3">{pid}</data>')
        lines.append("    </edge>")

    lines.extend(["  </graph>", "</graphml>"])
    xml_str = "\n".join(lines) + "\n"
    # Self-verify XML well-formedness
    ET.fromstring(xml_str)
    return xml_str


def export_to_dot(kg: Dict[str, Any]) -> str:
    """Serialize kg.json into Graphviz DOT."""
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines = ["digraph LiteratureKG {", '  rankdir="LR";']
    for n in kg.get("nodes", []):
        lines.append(f'  "{_esc(n["node_id"])}" [label="{_esc(n["label"])}\\n({n["entity_type"]})"];')
    for e in kg.get("edges", []):
        lines.append(f'  "{_esc(e["source_node"])}" -> "{_esc(e["target_node"])}" [label="{_esc(e["relation_type"])} [{_esc(e["source_id"])}]"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def export_to_markdown(kg: Dict[str, Any], queries: List[Dict[str, Any]]) -> str:
    """Render human-readable KG report with Mermaid diagram, provenance tables, alignment audit, and query results."""
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
        auths = ", ".join(sm.get("authors", []))
        lines.append(
            f"| `{sm.get('source_id')}` | {str(sm.get('title')).replace('|', ' ')} | {auths} | `{sm.get('doi')}` | {sm.get('year')} | `{sm.get('location_type')}` |"
        )
    lines.append("")

    # Mermaid overview (up to 25 edges for readability)
    if kg.get("edges"):
        lines.extend(["## 2. Knowledge Graph Preview (Mermaid)", "", "```mermaid", "flowchart LR"])
        node_map = {n["node_id"]: n for n in kg.get("nodes", [])}
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
        f"- **Protected Instance Separations (`do_not_merge_records`):** {len(kg.get('do_not_merge_records', []))}",
        "",
    ])
    for dnm in kg.get("do_not_merge_records", []):
        lines.append(f"- **DO NOT MERGE `{dnm['generic_term']}`:** {dnm['reason']} Instances: `{dnm['instance_nodes']}`")
    lines.append("")

    lines.extend(["## 4. Executed Graph Queries", ""])
    for q in queries:
        lines.append(f"### `{q.get('query_id')}` — `{q.get('query_type')}` (`{q.get('answer_status')}`, hits: {q.get('hit_count')})")
        lines.append("```json")
        lines.append(json.dumps(q, ensure_ascii=False, indent=2)[:1500])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def export_all(kg: Dict[str, Any], queries: List[Dict[str, Any]], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gml = export_to_graphml(kg)
    dot = export_to_dot(kg)
    md = export_to_markdown(kg, queries)
    write_text_lf(out_dir / "kg.graphml", gml)
    write_text_lf(out_dir / "kg.dot", dot)
    write_text_lf(out_dir / "kg_report.md", md)
    return {
        "graphml_path": str(out_dir / "kg.graphml"),
        "dot_path": str(out_dir / "kg.dot"),
        "markdown_path": str(out_dir / "kg_report.md"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export KG to GraphML, DOT, and Markdown.")
    parser.add_argument("--kg", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    kg = json.loads(Path(args.kg).read_text(encoding="utf-8"))
    qobj = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    paths = export_all(kg, qobj.get("queries", []), Path(args.out))
    print(json.dumps(paths, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
