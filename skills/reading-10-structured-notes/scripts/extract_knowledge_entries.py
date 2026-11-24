#!/usr/bin/env python3
"""Extract deduplicated knowledge entries, terminology/acronym glossary, intra-paper conflicts, and search index."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic

ACRONYM_DEF_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9\-\s]{2,55}?)\s*\(([A-Z][A-Za-z0-9\-]{1,14})\)"
)
ZH_ACRONYM_DEF_RE = re.compile(
    r"([\u4e00-\u9fff]{2,18})\s*[（\(]([A-Za-z][A-Za-z0-9\-]{1,14})[）\)]"
)
CONDITION_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|MPa|kPa|bar|mL\s*g-1\s*h-1|mL/min|wt%|mol%|h|min|epochs?)\b",
    re.IGNORECASE,
)
METRIC_NUM_RE = re.compile(
    r"\b(selectivity|yield|conversion|accuracy|f1|precision|recall|auc)\b[^.\n]{0,45}?(\d+(?:\.\d+)?)\s*(%)?",
    re.IGNORECASE,
)
METRIC_NUM_REV_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%)?\s+(?:of\s+)?(selectivity|yield|conversion|accuracy|f1|precision|recall)",
    re.IGNORECASE,
)
COMPARE_CUE_RE = re.compile(
    r"\b(baseline|compared\s+to|versus|vs\.?|from\s+\d|increase[ds]?\s+from|dropped\s+from|control)\b",
    re.IGNORECASE,
)


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.lower()).strip()


def extract_knowledge_entries(doc: Dict[str, Any], skeleton: Dict[str, Any]) -> Dict[str, Any]:
    """Extract terminology glossary, deduplicated knowledge entries, conflicts, and search index."""
    blocks = doc.get("blocks", [])
    tables = doc.get("tables", [])

    # 1. Terminology & acronym canonicalization
    acronym_map: Dict[str, Dict[str, Any]] = {}
    for blk in blocks:
        if blk.get("role") in ("header", "footer", "table_ref", "reference"):
            continue
        txt = (blk.get("structured_text") or blk.get("text") or "").strip()
        for m in ACRONYM_DEF_RE.finditer(txt):
            full_name = re.sub(r"\s+", " ", m.group(1)).strip(" ,;:-")
            abbr = m.group(2).strip()
            # Filter out false hits like equation numbers or pure years
            if len(full_name.split()) > 8 or abbr.isdigit():
                continue
            if abbr not in acronym_map:
                acronym_map[abbr] = {
                    "acronym": abbr,
                    "full_name": full_name,
                    "canonical_key": _norm_key(full_name),
                    "definition_anchor": {
                        "pdf_page": blk["page"],
                        "printed_page": blk.get("printed_page"),
                        "section_id": blk.get("section_id"),
                        "block_id": blk["block_id"],
                        "bbox": blk["bbox"],
                        "quote_snippet": m.group(0)[:200],
                    },
                    "occurrence_count": 0,
                    "occurrence_anchors": [],
                }
        for m in ZH_ACRONYM_DEF_RE.finditer(txt):
            full_name = m.group(1).strip()
            abbr = m.group(2).strip()
            if abbr not in acronym_map:
                acronym_map[abbr] = {
                    "acronym": abbr,
                    "full_name": full_name,
                    "canonical_key": _norm_key(full_name),
                    "definition_anchor": {
                        "pdf_page": blk["page"],
                        "printed_page": blk.get("printed_page"),
                        "section_id": blk.get("section_id"),
                        "block_id": blk["block_id"],
                        "bbox": blk["bbox"],
                        "quote_snippet": m.group(0)[:200],
                    },
                    "occurrence_count": 0,
                    "occurrence_anchors": [],
                }

    # Count occurrences across all blocks
    for abbr, info in acronym_map.items():
        pat = re.compile(rf"\b{re.escape(abbr)}\b")
        for blk in blocks:
            txt = blk.get("structured_text") or blk.get("text") or ""
            if pat.search(txt):
                info["occurrence_count"] += len(pat.findall(txt))
                if len(info["occurrence_anchors"]) < 10:
                    info["occurrence_anchors"].append(
                        {
                            "pdf_page": blk["page"],
                            "section_id": blk.get("section_id"),
                            "block_id": blk["block_id"],
                            "bbox": blk["bbox"],
                        }
                    )

    terminology_glossary = sorted(acronym_map.values(), key=lambda x: (-x["occurrence_count"], x["acronym"]))

    # 2. Deduplicated knowledge entries from section cues + terminology
    entries_by_key: Dict[str, Dict[str, Any]] = {}
    entry_seq = 0

    def _add_entry(
        entry_type: str,
        canonical_term: str,
        statement: str,
        anchor: Dict[str, Any],
        tier: str = "machine_verified",
    ) -> None:
        nonlocal entry_seq
        # Canonicalize known acronyms inside dedup key
        norm_stmt = _norm_key(statement)
        for abbr, info in acronym_map.items():
            norm_stmt = re.sub(rf"\b{re.escape(abbr.lower())}\b", info["canonical_key"], norm_stmt)
        dedup_key = f"{entry_type}::{norm_stmt[:140]}"
        if dedup_key in entries_by_key:
            existing = entries_by_key[dedup_key]
            if not any(a.get("block_id") == anchor.get("block_id") and a.get("quote_snippet") == anchor.get("quote_snippet") for a in existing["anchors"]):
                existing["anchors"].append(anchor)
            return
        entry_seq += 1
        entries_by_key[dedup_key] = {
            "entry_id": f"ke_{entry_seq}",
            "entry_type": entry_type,
            "canonical_term": canonical_term,
            "statement": statement,
            "anchors": [anchor],
            "verification_tier": tier,
        }

    for g in terminology_glossary:
        _add_entry(
            "concept_definition",
            g["acronym"],
            f"{g['acronym']} denotes {g['full_name']}.",
            g["definition_anchor"],
            tier="machine_verified",
        )

    cue_to_entry_type = {
        "method_condition": ("variable_condition", "needs_human_review"),
        "quantitative_finding": ("quantitative_finding", "machine_verified"),
        "limitation_precondition": ("limitation_precondition", "needs_human_review"),
        "data_materials": ("method_component", "needs_human_review"),
    }

    for sn in skeleton.get("section_notes", []):
        for cue in sn.get("cue_evidence", []):
            anchor = {
                "pdf_page": cue["pdf_page"],
                "printed_page": cue.get("printed_page"),
                "section_id": cue.get("section_id"),
                "block_id": cue["block_id"],
                "bbox": cue["bbox"],
                "quote_snippet": cue["quote_snippet"],
            }
            for ct in cue.get("cue_types", []):
                if ct in cue_to_entry_type:
                    etype, tier = cue_to_entry_type[ct]
                    cond_hits = CONDITION_UNIT_RE.findall(cue["text"])
                    term = ", ".join(cond_hits[:3]) if (ct == "method_condition" and cond_hits) else sn["title"]
                    _add_entry(etype, term, cue["text"], anchor, tier=tier)

    knowledge_entries = list(entries_by_key.values())

    # 3. Intra-paper numeric conflict detector across blocks and tables
    metric_observations: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for blk in blocks:
        if blk.get("role") in ("header", "footer", "reference", "table_ref"):
            continue
        txt = (blk.get("structured_text") or blk.get("text") or "").strip()
        if COMPARE_CUE_RE.search(txt):
            continue
        for m in METRIC_NUM_RE.finditer(txt):
            metric = m.group(1).lower()
            val = m.group(2) + (m.group(3) or "")
            metric_observations[metric].append(
                {
                    "value": val,
                    "num": float(m.group(2)),
                    "pdf_page": blk["page"],
                    "section_id": blk.get("section_id"),
                    "block_id": blk["block_id"],
                    "bbox": blk["bbox"],
                    "quote_snippet": txt[:200],
                }
            )
        for m in METRIC_NUM_REV_RE.finditer(txt):
            metric = m.group(3).lower()
            val = m.group(1) + (m.group(2) or "")
            metric_observations[metric].append(
                {
                    "value": val,
                    "num": float(m.group(1)),
                    "pdf_page": blk["page"],
                    "section_id": blk.get("section_id"),
                    "block_id": blk["block_id"],
                    "bbox": blk["bbox"],
                    "quote_snippet": txt[:200],
                }
            )

    # Also check table column headers & records for matching metric names
    for tab in tables:
        if tab.get("is_image_table"):
            continue
        col_paths = tab.get("col_paths", [])
        for col in col_paths:
            for mname in ("selectivity", "yield", "conversion", "accuracy", "f1", "precision", "recall"):
                if mname in col.lower():
                    for rec in tab.get("records", []):
                        raw_v = str(rec.get(col, "")).strip()
                        num_m = re.match(r"^(\d+(?:\.\d+)?)\s*(%?)$", raw_v)
                        if num_m:
                            metric_observations[mname].append(
                                {
                                    "value": raw_v,
                                    "num": float(num_m.group(1)),
                                    "pdf_page": tab["pages"][0] if tab.get("pages") else 1,
                                    "section_id": tab.get("section_id"),
                                    "table_id": tab["table_id"],
                                    "bbox": tab["bbox"],
                                    "quote_snippet": f"{tab.get('label')} ({col}): {raw_v}",
                                }
                            )

    conflicts: List[Dict[str, Any]] = []
    conf_idx = 0
    for metric, obs_list in sorted(metric_observations.items()):
        distinct_vals = sorted({round(o["num"], 4) for o in obs_list})
        if len(distinct_vals) >= 2:
            # Check if values come from distinct locations and are fairly close (likely discrepancy vs unrelated baseline)
            locs = {o.get("block_id") or o.get("table_id") for o in obs_list}
            if len(locs) >= 2:
                conf_idx += 1
                conflicts.append(
                    {
                        "conflict_id": f"conf_{conf_idx}",
                        "metric": metric,
                        "values": [o["value"] for o in obs_list],
                        "observations": obs_list,
                        "severity": "medium",
                        "verification_tier": "needs_human_review",
                        "note": f"Multiple distinct numeric values observed for '{metric}' across locations {sorted(locs)}; verify whether different conditions/samples or textual inconsistency.",
                    }
                )

    # 4. Searchable inverted index
    index_terms: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"entry_ids": [], "section_ids": [], "pages": []})

    def _index_token(tok: str, entry_id: Optional[str] = None, sec_id: Optional[str] = None, page: Optional[int] = None) -> None:
        key = tok.lower().strip()
        if len(key) < 2:
            return
        bucket = index_terms[key]
        if entry_id and entry_id not in bucket["entry_ids"]:
            bucket["entry_ids"].append(entry_id)
        if sec_id and sec_id not in bucket["section_ids"]:
            bucket["section_ids"].append(sec_id)
        if page and page not in bucket["pages"]:
            bucket["pages"].append(page)

    for ke in knowledge_entries:
        for tok in re.findall(r"[A-Za-z0-9\-_]{2,}|[\u4e00-\u9fff]{2,}", ke["canonical_term"] + " " + ke["statement"]):
            _index_token(
                tok,
                entry_id=ke["entry_id"],
                sec_id=ke["anchors"][0].get("section_id"),
                page=ke["anchors"][0].get("pdf_page"),
            )

    for g in terminology_glossary:
        _index_token(g["acronym"], sec_id=g["definition_anchor"].get("section_id"), page=g["definition_anchor"].get("pdf_page"))

    # Keep top-frequency / meaningful index entries bounded cleanly
    search_index = {
        k: {
            "entry_ids": v["entry_ids"][:15],
            "section_ids": v["section_ids"][:15],
            "pages": sorted(v["pages"]),
        }
        for k, v in sorted(index_terms.items())
    }

    return {
        "knowledge_entries": knowledge_entries,
        "terminology_glossary": terminology_glossary,
        "conflicts": conflicts,
        "search_index": search_index,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract knowledge entries, acronyms, conflicts, and search index.")
    parser.add_argument("--parsed", required=True)
    parser.add_argument("--skeleton", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    doc = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    skel = json.loads(Path(args.skeleton).read_text(encoding="utf-8"))
    res = extract_knowledge_entries(doc, skel)
    write_json_atomic(args.out, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
