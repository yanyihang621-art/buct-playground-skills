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
        '  <key id="d3" for="node" attr.name="evidence_anchors_json" attr.type="string"/>',
        '  <key id="e0" for="edge" attr.name="relation_type" attr.type="string"/>',
        '  <key id="e1" for="edge" attr.name="evidence_strength" attr.type="string"/>',
        '  <key id="e2" for="edge" attr.name="source_id" attr.type="string"/>',
        '  <key id="e3" for="edge" attr.name="paragraph_id" attr.type="string"/>',
        '  <key id="e4" for="edge" attr.name="quote_snippet" attr.type="string"/>',
        '  <key id="e5" for="edge" attr.name="location_type" attr.type="string"/>',
        '  <key id="e6" for="edge" attr.name="conditions_json" attr.type="string"/>',
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
        anchors = html_lib.escape(json.dumps(n.get("evidence_anchors", []), ensure_ascii=False))
        lines.append(f'      <data key="d3">{anchors}</data>')
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
        lines.append(f'      <data key="e4">{html_lib.escape(e["quote_snippet"])}</data>')
        lines.append(f'      <data key="e5">{html_lib.escape(e["location_type"])}</data>')
        conditions = html_lib.escape(json.dumps(e["conditions"], ensure_ascii=False))
        lines.append(f'      <data key="e6">{conditions}</data>')
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
        authors = sm.get("authors") or []
        authors = authors if isinstance(authors, list) else [authors]
        names = []
        for author in authors:
            if isinstance(author, dict):
                name = author.get("name") or author.get("literal") or " ".join(
                    str(author[k]) for k in ("given", "family") if author.get(k))
                names.append(str(name) if name else json.dumps(author, ensure_ascii=False))
            else:
                names.append(str(author))
        auths = ", ".join(names).replace("|", " ")
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
        rows = q.get("subgraph_edges") or q.get("evidence_edges") or q.get("comparison_rows") or []
        for row in rows[:5]:
            subject = row.get("subject", row.get("source", ""))
            relation = row.get("relation", row.get("relation_type", ""))
            lines.append(f"- {subject} → {relation} → {row['target']} "
                         f"({row['source_id']}, {row['paragraph_id']}) — {row['quote_snippet']}")
        if not rows:
            for node in q.get("shared_concepts", [])[:5]:
                anchors = ", ".join(f"{a['source_id']}/{a['paragraph_id']}" for a in node["evidence_anchors"])
                lines.append(f"- {node['label']} ({anchors})")
            for issue in q.get("conflicts", [])[:5]:
                lines.append(f"- {issue['message']} ({', '.join(issue['edge_ids'])})")
        lines.append("")
    return "\n".join(lines)


def render_preview(kg: Dict[str, Any], target: Path) -> bool:
    """Draw real directed edges, sampling round-robin so one large paper cannot dominate."""
    try:
        import fitz
    except ImportError:
        return False
    by_source = {}
    for edge in kg.get("edges", []):
        if edge["relation_type"] != "instance_of":
            by_source.setdefault(edge["source_id"], []).append(edge)
    if not by_source:
        return False
    selected = []
    for level in range(max(map(len, by_source.values()))):
        selected.extend(rows[level] for rows in by_source.values() if len(rows) > level)
        if len(selected) >= 8:
            break
    selected = selected[:8]
    nodes = {n["node_id"]: n for n in kg["nodes"]}
    dark, soft, teal = (0.09, 0.16, 0.23), (0.29, 0.37, 0.44), (0.04, 0.39, 0.39)
    document = fitz.open()
    try:
        page = document.new_page(width=870, height=90 + 75 * len(selected))

        def put(x, y, value, size=10, color=dark, bold=False):
            font = "china-s" if any(ord(ch) > 255 for ch in value) else ("hebo" if bold else "helv")
            page.insert_text((x, y), value, fontsize=size, fontname=font, color=color)

        def label_lines(text, width, size):
            lines = []
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

        put(26, 36, "Evidence-linked graph", 22, bold=True)
        covered = len({e["source_id"] for e in selected})
        total = sum(map(len, by_source.values()))
        put(26, 57, f"{len(selected)} of {total} substantive edges  |  {covered} of {len(kg['sources_manifest'])} sources", 11, soft)
        for i, edge in enumerate(selected):
            top = 74 + 75 * i
            for x, node_key in ((26, "source_node"), (554, "target_node")):
                node = nodes[edge[node_key]]
                page.draw_rect(fitz.Rect(x, top, x + 284, top + 49), color=(0.77, 0.83, 0.84), fill=(0.95, 0.97, 0.97), width=0.7)
                put(x + 10, top + 12, node["entity_type"], 8, teal, bold=True)
                for line_num, line in enumerate(label_lines(node["label"], 260, 11)):
                    put(x + 10, top + 27 + 12 * line_num, line, 11)
            mid_y = top + 28
            page.draw_line((310, mid_y), (551, mid_y), color=teal, width=1.2)
            page.draw_polyline([(544, mid_y - 3), (551, mid_y), (544, mid_y + 3)], color=teal, width=1.2)
            put(325, top + 20, edge["relation_type"].replace("_", " "), 10, teal, bold=True)
            put(26, top + 62, f"{edge['source_id']}  /  {edge['paragraph_id']}  /  {edge['edge_id']}", 9, soft)
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(target))
    finally:
        document.close()
    return True


def export_all(kg: Dict[str, Any], queries: List[Dict[str, Any]], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gml = export_to_graphml(kg)
    dot = export_to_dot(kg)
    md = export_to_markdown(kg, queries)
    write_text_lf(out_dir / "kg.graphml", gml)
    write_text_lf(out_dir / "kg.dot", dot)
    write_text_lf(out_dir / "kg_report.md", md)
    paths = {
        "graphml_path": str(out_dir / "kg.graphml"),
        "dot_path": str(out_dir / "kg.dot"),
        "markdown_path": str(out_dir / "kg_report.md"),
    }
    preview = out_dir / "graph_preview.png"
    if render_preview(kg, preview):
        paths["preview_path"] = str(preview)
    else:
        preview.unlink(missing_ok=True)
    return paths


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
