#!/usr/bin/env python3
"""6-check deterministic QA verifier for Introduction & Related Work writing."""

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

from rw_utils import extract_inline_cite_keys, load_writing_rules, write_json_atomic


def verify_intro_and_related_work(
    draft_md: str,
    materials: Dict[str, Any],
    clusters: Dict[str, Any],
    gc_map: Dict[str, Any],
    *,
    language: str = "en",
    rules_path: Path | None = None,
) -> Dict[str, Any]:
    """Verify structure, anti-cliche/stitching, gap grounding, contribution evidence, citation coverage, and honesty."""
    rules = load_writing_rules(rules_path)
    papers = materials.get("papers", [])
    papers_by_key = {p["cite_key"]: p for p in papers}
    study_facts = materials.get("study_facts", {})
    material_gaps = materials.get("material_gaps", [])
    violations: List[Dict[str, Any]] = []

    # 1. Four-part structure completeness
    req_secs = rules.get("required_sections_zh" if language.startswith("zh") else "required_sections_en", [])
    missing_secs = [sec for sec in req_secs if not re.search(rf"^#{{1,3}}\s*{re.escape(sec)}", draft_md, re.MULTILINE | re.IGNORECASE)]
    struct_ok = (len(missing_secs) == 0)
    if not struct_ok:
        violations.append({"check": "four_part_structure_completeness", "missing_sections": missing_secs})

    # 2. Anti-cliche & anti-chronological-stitching
    cliche_hits: List[str] = []
    for pat_s in rules.get("banned_cliche_openings", []):
        if re.search(pat_s, draft_md, re.IGNORECASE):
            cliche_hits.append(pat_s)
    has_route_subheadings = bool(re.search(r"^###\s+.+", draft_md, re.MULTILINE))
    enough_routes = (clusters.get("route_cluster_count", 0) >= 2) if len(papers) >= 3 else True
    anti_cliche_ok = (not cliche_hits) and has_route_subheadings and enough_routes
    if not anti_cliche_ok:
        violations.append(
            {
                "check": "anti_cliche_and_anti_stitching",
                "cliche_hits": cliche_hits,
                "has_route_subheadings": has_route_subheadings,
                "route_cluster_count": clusters.get("route_cluster_count", 0),
            }
        )

    # 3. Gap grounding (every gap cites valid ingested papers or is explicitly labeled author_judgment)
    gaps = gc_map.get("research_gaps", [])
    grounded_gaps = 0
    gap_ok = len(gaps) > 0
    for g in gaps:
        valid_cites = [k for k in g.get("derived_from_cite_keys", []) if k in papers_by_key]
        if valid_cites or g.get("author_judgment"):
            if valid_cites:
                grounded_gaps += 1
        else:
            gap_ok = False
            violations.append({"check": "gap_evidence_grounding", "gap_id": g.get("gap_id"), "reason": "ungrounded_gap_without_cited_limitation"})
    grounded_gap_ratio = 1.0 if not gaps else round(grounded_gaps / len(gaps), 4)

    # 4. Contribution evidence alignment (anchor values match author's actual study_facts)
    contribs = gc_map.get("contributions", [])
    facts_blob = json.dumps(study_facts, ensure_ascii=False)
    grounded_contribs = 0
    contrib_ok = len(contribs) > 0
    for c in contribs:
        anc = c.get("evidence_anchor")
        if not isinstance(anc, dict) or not any(anc.values()):
            contrib_ok = False
            violations.append({"check": "contribution_evidence_alignment", "contrib_id": c.get("contrib_id"), "reason": "missing_evidence_anchor"})
            continue
        # Verify cited table/metric values are actually backed by study_facts
        bad_vals = []
        for ak in ("figure_or_table_ref", "metric_value"):
            val = str(anc.get(ak) or "").strip()
            if val and val not in facts_blob:
                bad_vals.append(f"{ak}={val}")
        if bad_vals:
            contrib_ok = False
            violations.append({"check": "contribution_evidence_alignment", "contrib_id": c.get("contrib_id"), "unbacked_anchors": bad_vals})
        else:
            grounded_contribs += 1
    grounded_contrib_ratio = 1.0 if not contribs else round(grounded_contribs / len(contribs), 4)

    # 5. Citation coverage & metadata validity
    inline_keys = extract_inline_cite_keys(draft_md)
    unknown_keys = sorted({k for k in inline_keys if k not in papers_by_key})
    covered_keys = sorted({k for k in inline_keys if k in papers_by_key})
    cov_ratio = 1.0 if not papers else round(len(covered_keys) / len(papers), 4)
    missing_meta = [p["cite_key"] for p in papers if not p.get("title") or not p.get("authors") or not p.get("year")]
    cite_ok = (len(unknown_keys) == 0) and (cov_ratio >= 0.75) and (len(missing_meta) == 0)
    if not cite_ok:
        violations.append(
            {
                "check": "citation_coverage_and_metadata",
                "unknown_inline_keys": unknown_keys,
                "coverage_ratio": cov_ratio,
                "missing_metadata_keys": missing_meta,
            }
        )

    # 6. Honesty & material sufficiency
    honesty_ok = (len(material_gaps) == 0) and (len(papers) > 0) and bool(study_facts)
    if not honesty_ok:
        violations.append({"check": "honesty_and_material_sufficiency", "material_gaps": material_gaps})

    checks = {
        "four_part_structure_completeness": struct_ok,
        "anti_cliche_and_anti_stitching": anti_cliche_ok,
        "gap_evidence_grounding": gap_ok,
        "contribution_evidence_alignment": contrib_ok,
        "citation_coverage_and_metadata": cite_ok,
        "honesty_and_material_sufficiency": honesty_ok,
    }
    passed = all(checks.values())

    return {
        "schema_version": "writing-08-qa/v1",
        "passed": passed,
        "metrics": {
            "literature_count": len(papers),
            "route_cluster_count": clusters.get("route_cluster_count", 0),
            "gap_count": len(gaps),
            "contribution_count": len(contribs),
            "citation_coverage_ratio": cov_ratio,
            "grounded_gap_ratio": grounded_gap_ratio,
            "grounded_contribution_ratio": grounded_contrib_ratio,
        },
        "checks": checks,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify drafted Introduction & Related Work against 6 QA gates.")
    parser.add_argument("--draft", required=True)
    parser.add_argument("--materials", required=True)
    parser.add_argument("--clusters", required=True)
    parser.add_argument("--gc-map", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    md = Path(args.draft).read_text(encoding="utf-8")
    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))
    clus = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
    gc = json.loads(Path(args.gc_map).read_text(encoding="utf-8"))
    rep = verify_intro_and_related_work(md, mats, clus, gc, language=args.language)
    write_json_atomic(Path(args.out), rep)
    print(json.dumps({"passed": rep["passed"], "checks": rep["checks"], "violations": len(rep["violations"])}, ensure_ascii=False))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
