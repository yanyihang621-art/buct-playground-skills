#!/usr/bin/env python3
"""Draft editable 4-part Introduction & Related Work manuscript with synchronized citations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rw_utils import extract_inline_cite_keys, write_text_lf


def _format_ref_line(p: Dict[str, Any], idx: int) -> str:
    auths = ", ".join(p.get("authors", [])[:3]) + (" et al." if len(p.get("authors", [])) > 3 else "")
    if not auths:
        auths = "Unknown"
    yr = p.get("year") or "s.a."
    venue = p.get("venue") or "[venue not supplied]"
    doi = f" DOI:{p['doi']}." if p.get("doi") else ""
    return f"[{idx}] {auths}. {p.get('title', 'Untitled')}. {venue}, {yr}.{doi} (`@{p['cite_key']}`)"


def render_intro_and_related_work(
    materials: Dict[str, Any],
    clusters: Dict[str, Any],
    gc_map: Dict[str, Any],
    *,
    language: str = "en",
) -> str:
    """Render a concrete, non-cliche, route-clustered, evidence-aligned Markdown draft."""
    facts = materials.get("study_facts", {})
    papers_by_key = {p["cite_key"]: p for p in materials.get("papers", [])}
    gaps = gc_map.get("research_gaps", [])
    contribs = gc_map.get("contributions", [])
    is_zh = language.lower().startswith("zh")

    title = facts.get("paper_title") or ("引言与相关工作" if is_zh else "Introduction and Related Work")
    bg = facts.get("background_context") or ""

    lines: List[str] = [f"# {title}", ""]

    # 1. Research Background
    lines.append("## 研究背景" if is_zh else "## Research Background")
    if bg:
        lines.append(bg.strip())
    elif facts.get("target_problem"):
        lines.append(facts["target_problem"])
    else:
        lines.append("[Material gap: missing background facts.]")
    lines.append("")

    # 2. Related Work (clustered by technical route)
    lines.append("## 相关工作" if is_zh else "## Related Work")
    for cl in clusters.get("clusters", []):
        lines.append(f"### {cl['route_name']}")
        parts: List[str] = []
        for p in cl["papers"]:
            k = p["cite_key"]
            finding = p.get("key_finding") or "[finding requires source review]"
            lim = p.get("limitation")
            if is_zh:
                stmt = f"在{p.get('evaluation_system') or '既有测试条件'}下，研究者验证了{finding} [@{k}]"
                if lim:
                    stmt += f"，但存在{lim}的局限"
                parts.append(stmt + "。")
            else:
                stmt = f"Under {p.get('evaluation_system') or 'reported operating conditions'}, {finding} [@{k}]"
                if lim:
                    stmt += f"; however, this approach remains constrained by {lim}"
                parts.append(stmt + ".")
        lines.append(" ".join(parts))
        lines.append("")

    # 3. Research Gaps (grounded in cited limitations)
    lines.append("## 研究空白" if is_zh else "## Research Gaps")
    for g in gaps:
        tag = " [Author judgment / 作者判断]" if g.get("author_judgment") else ""
        lines.append(f"- **{g['gap_id']}**{tag}: {g['gap_statement']}")
    if not gaps:
        lines.append("- [Insufficient cited limitations to derive grounded research gaps.]")
    lines.append("")

    # 4. Contributions (aligned 1-to-1 to author study evidence anchors)
    lines.append("## 主要贡献" if is_zh else "## Contributions")
    for c in contribs:
        anc = c.get("evidence_anchor") or {}
        anc_str = ", ".join(f"{k}={v}" for k, v in anc.items() if v)
        lines.append(
            f"- **{c['contrib_id']}** (addresses `{c['addresses_gap_id']}` | evidence: `{anc_str or 'UNVERIFIED'}`): {c['claim']}"
        )
    if not contribs:
        lines.append("- [Missing study_facts contributions.]")
    lines.append("")

    # References section
    cited_order = []
    for k in extract_inline_cite_keys("\n".join(lines)):
        if k in papers_by_key and k not in cited_order:
            cited_order.append(k)
    lines.append("## 参考文献" if is_zh else "## References")
    for idx, k in enumerate(cited_order, start=1):
        lines.append(_format_ref_line(papers_by_key[k], idx))
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft 4-part Introduction & Related Work Markdown.")
    parser.add_argument("--materials", required=True)
    parser.add_argument("--clusters", required=True)
    parser.add_argument("--gc-map", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))
    clus = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    gc = json.loads(Path(args.gc_map).read_text(encoding="utf-8"))
    md = render_intro_and_related_work(mats, clus, gc, language=args.language)
    write_text_lf(Path(args.out), md)
    print(json.dumps({"out": str(args.out), "length": len(md)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
