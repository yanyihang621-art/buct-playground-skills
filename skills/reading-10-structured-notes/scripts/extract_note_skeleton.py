#!/usr/bin/env python3
"""Build section-aligned note skeletons and unified 12-field Paper Cards with verbatim anchors."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff\(])|(?<=[。！？])\s*")

CUE_PATTERNS: Dict[str, re.Pattern[str]] = {
    "objective_problem": re.compile(
        r"\b(we\s+(?:investigate|propose|introduce|present|study|aim|explore|ask|develop|design)|"
        r"this (?:study|paper|work) (?:asks|aims|introduces|examines|investigates|addresses)|"
        r"(?:objective|aim)\s+of|research\s+question|our\s+hypothesis|to\s+address\s+(?:this|the))\b|"
        r"(?:本文(?:提出|研究|旨在|探讨)|针对.{1,20}问题|研究目标)",
        re.IGNORECASE,
    ),
    "data_materials": re.compile(
        r"\b(datasets?|corpora|corpus|benchmarks?|samples?|specimens?|catalysts?|synthesized|prepared|purchased|"
        r"collected\s+from|training\s+set|test\s+set|feed\s+gas)\b|"
        r"(?:数据集|语料|样品|催化剂|材料|制备|合成|采集)",
        re.IGNORECASE,
    ),
    "method_condition": re.compile(
        r"(?:\b(?:train(?:ed)?|evaluat(?:e|ed)|measur(?:e|ed)|test(?:ed)?|calculat(?:e|ed)|"
        r"normaliz(?:e|ed)|fit(?:ted)?|estimat(?:e|es|ed)|use(?:d)?|operated|characterized|annealed|calcined|"
        r"under\s+the\s+condition|temperature|pressure|ghsv|flow\s+rate|learning\s+rate|batch\s+size|"
        r"protocol|pipeline|architecture)\b|\b\d+(?:\.\d+)?\s*(?:K|°C|MPa|bar|kPa|mL|h-1|wt%|mol%|epochs?|mg)\b|"
        r"(?:实验条件|操作条件|参数|温度|压力|空速|假设|前提))",
        re.IGNORECASE,
    ),
    "operating_condition": re.compile(
        r"\b(?:under\b|only when|provided (?:that|the)|assuming|conditional on|restricted to|"
        r"at (?:a |an )?(?:temperature|pressure|flow rate)|(?:batch size|learning rate|seed) (?:of |was |is )?\d)|"
        r"\b\d+(?:\.\d+)?\s*(?:K\b|°C|MPa\b|bar\b|kPa\b|mL\b|epochs?\b|mg\b)|"
        r"(?:在.{1,25}(?:条件下|温度下|压力下)|仅当|前提|假设|工况|批量大小|学习率)", re.I
    ),
    "quantitative_finding": re.compile(
        r"(?:\b(?:selectivity|yield|conversion|accuracy|f1|precision|recall|auc|r2|rmse|mae|"
        r"outperforms?|achieves?|reaches?|increases?|improves?|reduces?|stability)\b.*?\d|"
        r"\d+(?:\.\d+)?\s*(?:%|±|\u00b1)|p\s*[<≤]\s*0\.\d+|(?:达到|提升|优于|选择性|转化率|准确率).{0,15}\d)",
        re.IGNORECASE,
    ),
    "limitation_precondition": re.compile(
        r"\b(limitations?|caveats?|restricted\s+to|only\s+valid|only\s+under|requires?\s+dry|"
        r"fail(?:s|ed)?\s+when|drawback|threat\s+to\s+validity|future\s+work|not\s+reported|"
        r"cannot\s+generalize|may not (?:hold|apply|generalize)|not (?:tested|evaluated|validated)|"
        r"remain(?:s)? (?:unclear|unknown|to be (?:seen|tested))|does not (?:work|apply)|fails? to (?:converge|generalize)|"
        r"differences? .{0,72}(?:may be|likely) due to|not (?:directly )?comparable|generalizability|limited (?:by|to))\b|"
        r"(?:局限|不足|限制|仅适用|前提条件|未报告|未来工作)",
        re.IGNORECASE,
    ),
    "qualitative_finding": re.compile(
        r"\b(?:we (?:found|observed|conclude)|results? (?:suggest|show|indicate)s?|"
        r"(?:did not|failed to) (?:improve|increase|reduce|differ|detect)|"
        r"(?:higher|lower|greater|smaller) than|no (?:significant )?difference)\b|"
        r"(?:研究发现|结果表明|未能提高|无显著差异|显著低于|显著高于)", re.I
    ),
}

SECTION_PRIORITIES = {
    "background_problem": ("objective_problem", "limitation_precondition"),
    "methods_materials": ("data_materials", "method_condition", "operating_condition"),
    "results_analysis": ("quantitative_finding", "qualitative_finding", "limitation_precondition", "method_condition"),
    "discussion_limitations": ("limitation_precondition", "qualitative_finding", "quantitative_finding"),
    "conclusion": ("qualitative_finding", "quantitative_finding", "limitation_precondition"),
}

ROLE_PREFERENCE = {
    "objective_problem": ("abstract", "background_problem", "conclusion"),
    "data_materials": ("methods_materials", "results_analysis"),
    "method_condition": ("methods_materials", "abstract"),
    "operating_condition": ("methods_materials", "results_analysis", "discussion_limitations"),
    "qualitative_finding": ("results_analysis", "conclusion", "abstract"),
    "limitation_precondition": ("discussion_limitations", "conclusion", "results_analysis"),
}
AUTHOR_ACTION = re.compile(r"\b(?:we|this (?:study|paper|work))\s+(?:\w+\s+){0,2}(?:use|train|test|collect|evaluat|observ|found|investigat|propos|introduc|examin|report|measure|conclud|study|show)|(?:本研究|本文|我们)", re.I)
AMOUNT = re.compile(r"\d\s*(?:%|±|°C|K\b|MPa\b|epochs?\b)|\d+\.\d+|\b\d+\s+(?:samples|participants|subjects|images|iterations)\b", re.I)
AXIS_LABEL = re.compile(r"^(?:[+−-]?\d+(?:\.\d+)?\s+){2}[+−-]?\d+(?:\.\d+)?(?:\s|$)")
PROCEDURE = re.compile(r"\b(?:we (?:first |then |also )?(?:train|test|use|run|measur|implement|apply|calculat|collect|set|sampl|initializ)|(?:proposed|our) (?:method|algorithm)|we (?:randomly |directly )?(?:split|draw)|were (?:trained|measured|collected|prepared))|(?:我们(?:首先|随后)?(?:采用|训练|测定)|本方法)", re.I)
DATA_USE = re.compile(r"\b(?:on|using|from|with|collected|recruited|consist(?:s|ed)? of)\b.{0,65}\b(?:datasets?|corpus|cohort|participants|samples?|training set)\b|\b(?:datasets?|corpus|cohort) (?:contains?|comprises?|consists?|was)|(?:使用|采集|纳入).{0,25}(?:数据集|样本|语料)", re.I)
EXPLICIT_SETTING = re.compile(r"\b(?:batch size|learning rate|seed|sequence length|pressure|temperature)\b.{0,20}\d|\d+(?:\.\d+)?\s*(?:K\b|°C|MPa\b|kPa\b|bar\b)|\b(?:only (?:when|under)|provided that|not directly comparable)\b|(?:仅当|仅在|仅适用|前提条件)", re.I)


def _slot_score(kind, hit):
    text = hit["text"]
    roles = ROLE_PREFERENCE.get(kind, ())
    role = hit.get("discourse_role")
    score = (len(roles) - roles.index(role) + 1) if role in roles else 0
    if AUTHOR_ACTION.search(text):
        score += 4
    if kind == "method_condition" and PROCEDURE.search(text):
        score += 7
    if kind == "data_materials" and DATA_USE.search(text):
        score += 6
    if kind == "operating_condition" and PROCEDURE.search(text):
        score += 4
    if kind == "operating_condition" and EXPLICIT_SETTING.search(text):
        score += 8
    if kind in ("data_materials", "operating_condition", "qualitative_finding") and AMOUNT.search(text):
        score += 2
    if role == "related_work" or re.match(r"^(?:Fig(?:ure)?\.?|Table|Eq\.?)\s*\d", text, re.I):
        score -= 4
    return score


def _section_points(role: str, cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Select different evidence roles across the whole section; retain original order and clauses."""
    selected = {}
    priorities = SECTION_PRIORITIES.get(role, ()) + tuple(CUE_PATTERNS)
    for kind in dict.fromkeys(priorities):
        candidates = [(idx, item) for idx, item in enumerate(cues) if kind in item["cue_types"]]
        if not candidates:
            continue
        # Prefer a claim that includes its condition or measured value. Later evidence
        # breaks ties so a concluding observation can survive a long lead-in.
        idx, item = max(candidates, key=lambda pair: (
            len(pair[1]["cue_types"]), bool(re.search(r"\d", pair[1]["text"])), pair[0]))
        if idx not in selected and len(selected) < 4:
            selected[idx] = {
                "role": kind, "text": item["text"],
                "anchor": {key: item[key] for key in (
                    "pdf_page", "printed_page", "section_id", "block_id", "bbox", "quote_snippet")},
            }
    return [selected[idx] for idx in sorted(selected)]

CROSS_REF_RE = {
    "tables": re.compile(r"\b(?:Table|Tab\.?)\s*([A-Za-z]?\d+(?:\.\d+)?)|表\s*(\d+)", re.IGNORECASE),
    "figures": re.compile(r"\b(?:Figure|Fig\.?)\s*([A-Za-z]?\d+(?:\.\d+)?)|图\s*(\d+)", re.IGNORECASE),
    "equations": re.compile(r"\b(?:Eq\.?|Equation)\s*\(?([A-Za-z]?\d+(?:\.\d+)?)\)?|式\s*\(?(\d+)\)?", re.IGNORECASE),
    "sections": re.compile(r"\b(?:Section|Sec\.?)\s*(\d+(?:\.\d+)*)|第\s*(\d+)\s*节", re.IGNORECASE),
}

TRANSITION_RE = re.compile(
    r"\b(next|subsequently|building\s+on|in\s+the\s+following\s+section|therefore|consequently|"
    r"as\s+shown\s+above|in\s+contrast|to\s+address\s+this)\b|(?:接下来|基于上述|因此|综上所述)",
    re.IGNORECASE,
)


def _infer_discourse_role(title: str, level: int) -> str:
    low = title.lower()
    if any(k in low for k in ("abstract", "summary", "摘要")):
        return "abstract"
    if any(k in low for k in ("introduction", "background", "motivation", "引言", "绪论")):
        return "background_problem"
    if any(k in low for k in ("related work", "literature review", "相关工作")):
        return "related_work"
    if any(k in low for k in ("method", "materials", "algorithm", "synthesis", "setup", "experimental design", "experimental procedure", "实验设置", "实验方法", "方法", "材料")):
        return "methods_materials"
    if any(k in low for k in ("result", "evaluation", "experiment", "finding", "performance", "结果", "实验", "性能")):
        return "results_analysis"
    if any(k in low for k in ("discussion", "limitation", "threat", "讨论", "局限")):
        return "discussion_limitations"
    if any(k in low for k in ("conclusion", "future", "结论", "总结")):
        return "conclusion"
    if any(k in low for k in ("reference", "bibliography", "参考文献")):
        return "references"
    return "other"


def _flatten_sections(sections: Sequence[Dict[str, Any]], parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    for sec in sections:
        copy_sec = dict(sec)
        copy_sec["parent_section_id"] = parent_id
        flat.append(copy_sec)
        flat.extend(_flatten_sections(sec.get("children", []), parent_id=sec["section_id"]))
    return flat


def _make_anchor(blk: Dict[str, Any], quote: Optional[str] = None) -> Dict[str, Any]:
    snip = (quote or blk.get("structured_text") or blk.get("text") or "").strip()
    return {
        "pdf_page": blk["page"],
        "printed_page": blk.get("printed_page"),
        "section_id": blk.get("section_id"),
        "block_id": blk["block_id"],
        "bbox": blk["bbox"],
        "quote_snippet": snip[:240],
    }


def build_note_skeleton(doc: Dict[str, Any], paper_id: str = "paper_1") -> Dict[str, Any]:
    """Build section-aligned note skeleton and 12-field Paper Card from a parsed document.json."""
    status = doc.get("status", "partial")
    meta = doc.get("metadata", {})
    blocks = doc.get("blocks", [])
    tables = doc.get("tables", [])
    figures = doc.get("figures", [])
    equations = doc.get("formulas", {}).get("display_equations", [])
    diag = doc.get("diagnostics", {})
    uncertainties = list(doc.get("uncertainties", []))

    if status == "refused" or (diag.get("encrypted") or diag.get("corrupt")):
        return {
            "paper_id": paper_id,
            "source_file": doc.get("source_file"),
            "status": "refused",
            "section_notes": [],
            "paper_card": {
                "bibliographic": {
                    "title": meta.get("title"),
                    "authors": [],
                    "affiliations": [],
                    "keywords": [],
                    "page_count": diag.get("page_count", 0),
                    "verification_tier": "machine_verified",
                },
                "research_question": {"status": "refused", "summary": "Document is encrypted or corrupt.", "anchors": [], "verification_tier": "needs_human_review"},
                "data_and_materials": {"status": "refused", "summary": "Document unreadable.", "anchors": [], "verification_tier": "needs_human_review"},
                "methods_and_setup": {"status": "refused", "summary": "Document unreadable.", "anchors": [], "verification_tier": "needs_human_review"},
                "operating_conditions_and_preconditions": {"status": "refused", "summary": "Document unreadable.", "anchors": [], "verification_tier": "needs_human_review"},
                "key_findings_and_metrics": {"status": "refused", "items": [], "anchors": [], "verification_tier": "machine_verified"},
                "tables_and_figures_digest": {"table_count": 0, "figure_count": 0, "items": [], "verification_tier": "machine_verified"},
                "key_equations_and_variables": {"equation_count": 0, "items": [], "verification_tier": "machine_verified"},
                "limitations_and_threats_to_validity": {"status": "refused", "summary": "Document unreadable.", "anchors": [], "verification_tier": "needs_human_review"},
                "unreported_or_missing_items": ["all_fields_unreadable_due_to_encryption_or_corruption"],
                "reusability_tags": [],
                "verification_summary": {"section_count": 0, "machine_verified_fields": 3, "needs_human_review_fields": 5},
            },
            "uncertainties": uncertainties,
        }

    blk_by_id = {b["block_id"]: b for b in blocks}
    flat_secs = _flatten_sections(doc.get("sections", []))

    # Fallback section if paper had no detected section headings
    if not flat_secs and blocks:
        flat_secs = [
            {
                "section_id": "sec_unstructured",
                "parent_section_id": None,
                "level": 1,
                "number": None,
                "title": "Full Document (No Explicit Section Headings Detected)",
                "page_start": blocks[0]["page"],
                "page_end": blocks[-1]["page"],
                "bbox": blocks[0]["bbox"],
                "block_ids": [b["block_id"] for b in blocks],
            }
        ]
        uncertainties.append(
            {
                "code": "no_section_headings_detected",
                "severity": "medium",
                "pages": sorted({b["page"] for b in blocks}),
                "message": "No explicit numbered or styled section headings detected; grouped under sec_unstructured.",
            }
        )

    # Also capture frontmatter / abstract blocks before the first section
    assigned_bids = {bid for sec in flat_secs for bid in sec.get("block_ids", [])}
    front_blocks = [
        b
        for b in blocks
        if b["block_id"] not in assigned_bids and b.get("role") in ("abstract", "body")
    ]

    # Collect cues across all sections + abstract
    all_cues: Dict[str, List[Dict[str, Any]]] = {k: [] for k in CUE_PATTERNS}
    section_notes: List[Dict[str, Any]] = []

    def _scan_block_cues(blk: Dict[str, Any], sec_id: Optional[str], role: str) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]], List[str]]:
        txt = (blk.get("structured_text") or blk.get("text") or "").strip()
        if not txt:
            return [], {"tables": [], "figures": [], "equations": [], "sections": []}, []
        sents = SENTENCE_SPLIT_RE.split(txt)
        matched_items: List[Dict[str, Any]] = []
        for sent in sents:
            s_clean = sent.strip()
            if len(s_clean) < 12 or AXIS_LABEL.match(s_clean):
                continue
            cues_hit = [cname for cname, pat in CUE_PATTERNS.items() if pat.search(s_clean)]
            if cues_hit:
                item = {
                    "cue_types": cues_hit,
                    "text": s_clean,
                    "discourse_role": role,
                    "pdf_page": blk["page"],
                    "printed_page": blk.get("printed_page"),
                    "section_id": sec_id or blk.get("section_id"),
                    "block_id": blk["block_id"],
                    "bbox": blk["bbox"],
                    "quote_snippet": s_clean,
                }
                matched_items.append(item)
                for cn in cues_hit:
                    all_cues[cn].append(item)

        xrefs = {k: sorted({next(g for g in m.groups() if g) for m in pat.finditer(txt)}) for k, pat in CROSS_REF_RE.items()}
        transitions = [m.group(0) for m in TRANSITION_RE.finditer(txt)]
        return matched_items, xrefs, transitions

    for b in front_blocks:
        _scan_block_cues(b, b.get("section_id") or "frontmatter", b.get("role", "body"))

    section_roles = {}
    for sec in flat_secs:
        sec_id = sec["section_id"]
        sec_blocks = [blk_by_id[bid] for bid in sec.get("block_ids", []) if bid in blk_by_id]
        role = _infer_discourse_role(sec["title"], sec["level"])
        if role == "other":
            role = section_roles.get(sec.get("parent_section_id"), role)
        section_roles[sec_id] = role

        sec_cues: List[Dict[str, Any]] = []
        sec_xrefs: Dict[str, set] = {"tables": set(), "figures": set(), "equations": set(), "sections": set()}
        sec_transitions: List[str] = []
        body_texts: List[str] = []

        for blk in sec_blocks:
            if blk.get("role") == "heading":
                continue
            if blk.get("role") == "table_ref":
                sec_xrefs["tables"].add(blk.get("table_id", ""))
                continue
            body_texts.append((blk.get("structured_text") or blk.get("text") or "").strip())
            if role == "references" or blk.get("role") == "reference":
                continue
            c_items, xr, tr = _scan_block_cues(blk, sec_id, role)
            sec_cues.extend(c_items)
            for k, vals in xr.items():
                sec_xrefs[k].update(vals)
            sec_transitions.extend(tr)

        points = _section_points(role, sec_cues) if role != "references" else []
        sec_anchors = [_make_anchor(blk) for blk in sec_blocks if blk.get("role") != "heading"][:5]
        if points:
            sec_anchors = [point["anchor"] for point in points]
        if not sec_anchors and sec_blocks:
            sec_anchors = [_make_anchor(sec_blocks[0])]

        sec_path = sec_blocks[0].get("section_path", [sec["title"]]) if sec_blocks else [sec["title"]]
        lead_summary = " ".join(body_texts)[:360].strip()

        section_notes.append(
            {
                "section_id": sec_id,
                "parent_section_id": sec.get("parent_section_id"),
                "level": sec["level"],
                "number": sec.get("number"),
                "title": sec["title"],
                "section_path": sec_path,
                "discourse_role": role,
                "pages": [sec["page_start"], sec["page_end"]],
                "block_ids": sec.get("block_ids", []),
                "lead_excerpt": lead_summary,
                "key_points": points,
                "cue_evidence": sec_cues,
                "cross_references": {k: sorted(v - {""}) for k, v in sec_xrefs.items()},
                "transition_cues": sorted(set(sec_transitions)),
                "anchors": sec_anchors,
                "verification_tier": "machine_verified" if sec_cues or sec_anchors else "needs_human_review",
            }
        )

    # Helper to construct a Paper Card slot from cue hits
    def _build_slot(cue_key: str, extra_items: Optional[List[Dict[str, Any]]] = None, tier: str = "needs_human_review") -> Dict[str, Any]:
        hits = list(all_cues.get(cue_key, []))
        if extra_items:
            hits.extend(extra_items)
        # Deduplicate by (block_id, text)
        seen = set()
        uniq_hits: List[Dict[str, Any]] = []
        for h in hits:
            k = (h.get("block_id"), h.get("text", ""))
            if k not in seen:
                seen.add(k)
                uniq_hits.append(h)

        if not uniq_hits:
            return {
                "status": "not_reported",
                "summary": "not_reported",
                "items": [],
                "anchors": [],
                "verification_tier": tier,
            }

        uniq_hits.sort(key=lambda hit: _slot_score(cue_key, hit), reverse=True)
        # Give the reader distinct observations before secondary clauses from the same paragraph.
        first_by_block = {}
        for hit in uniq_hits:
            first_by_block.setdefault(hit["block_id"], hit)
        selected = {id(hit) for hit in first_by_block.values()}
        uniq_hits = list(first_by_block.values()) + [hit for hit in uniq_hits if id(hit) not in selected]
        anchors = [
            {
                "pdf_page": h["pdf_page"],
                "printed_page": h.get("printed_page"),
                "section_id": h.get("section_id"),
                "block_id": h["block_id"],
                "bbox": h["bbox"],
                "quote_snippet": h["quote_snippet"],
            }
            for h in uniq_hits[:10]
        ]
        summary = " | ".join(h["text"] for h in uniq_hits[:4])
        return {
            "status": "extracted",
            "summary": summary,
            "items": [h["text"] for h in uniq_hits[:10]],
            "anchors": anchors,
            "verification_tier": tier,
        }

    # Include Abstract block as fallback objective_problem cue if no explicit cue hit
    abs_fallback = []
    if not all_cues["objective_problem"] and meta.get("abstract"):
        abs_blk = next((b for b in blocks if b.get("role") == "abstract" and len(b["text"]) > 20), None)
        if abs_blk:
            abs_fallback.append(
                {
                    "text": abs_blk.get("structured_text") or abs_blk.get("text") or "",
                    "pdf_page": abs_blk["page"],
                    "printed_page": abs_blk.get("printed_page"),
                    "section_id": abs_blk.get("section_id"),
                    "block_id": abs_blk["block_id"],
                    "bbox": abs_blk["bbox"],
                    "quote_snippet": abs_blk.get("structured_text") or abs_blk.get("text") or "",
                }
            )

    rq_slot = _build_slot("objective_problem", extra_items=abs_fallback, tier="needs_human_review")
    data_slot = _build_slot("data_materials", tier="needs_human_review")
    method_slot = _build_slot("method_condition", tier="needs_human_review")
    cond_slot = _build_slot("operating_condition", tier="needs_human_review")
    finding_slot = _build_slot("qualitative_finding", extra_items=all_cues["quantitative_finding"])
    limit_slot = _build_slot("limitation_precondition", tier="needs_human_review")

    tables_digest = [
        {
            "table_id": t["table_id"],
            "label": t.get("label"),
            "caption": t.get("caption"),
            "pages": t.get("pages", []),
            "bbox": t["bbox"],
            "n_rows": t.get("n_rows", 0),
            "n_cols": t.get("n_cols", 0),
            "col_paths": t.get("col_paths", []),
            "is_image_table": t.get("is_image_table", False),
            "status": t.get("status"),
        }
        for t in tables
    ]
    figures_digest = [
        {
            "figure_id": f["figure_id"],
            "label": f.get("label"),
            "caption": f.get("caption"),
            "page": f["page"],
            "bbox": f.get("caption_bbox"),
        }
        for f in figures
    ]
    eq_digest = [
        {
            "eq_id": eq["eq_id"],
            "label": eq.get("label"),
            "page": eq["page"],
            "bbox": eq["bbox"],
            "structured_text": eq["structured_text"],
        }
        for eq in equations
    ]

    unreported: List[str] = []
    for fname, slot in [
        ("research_question", rq_slot),
        ("data_and_materials", data_slot),
        ("methods_and_setup", method_slot),
        ("operating_conditions_and_preconditions", cond_slot),
        ("key_findings_and_metrics", finding_slot),
        ("limitations_and_threats_to_validity", limit_slot),
    ]:
        if slot["status"] == "not_reported":
            unreported.append(fname)

    tags = sorted(
        set(
            [kw.lower().replace(" ", "_") for kw in meta.get("keywords", [])]
            + [sn["discourse_role"] for sn in section_notes if sn["discourse_role"] not in ("other", "references")]
        )
    )

    paper_card = {
        "bibliographic": {
            "title": meta.get("title"),
            "authors": meta.get("authors", []),
            "affiliations": meta.get("affiliations", []),
            "keywords": meta.get("keywords", []),
            "abstract": meta.get("abstract"),
            "page_count": diag.get("page_count", 0),
            "verification_tier": "machine_verified",
        },
        "research_question": rq_slot,
        "data_and_materials": data_slot,
        "methods_and_setup": method_slot,
        "operating_conditions_and_preconditions": cond_slot,
        "key_findings_and_metrics": finding_slot,
        "tables_and_figures_digest": {
            "table_count": len(tables_digest),
            "figure_count": len(figures_digest),
            "tables": tables_digest,
            "figures": figures_digest,
            "verification_tier": "machine_verified",
        },
        "key_equations_and_variables": {
            "equation_count": len(eq_digest),
            "equations": eq_digest,
            "inline_formula_blocks": len(doc.get("formulas", {}).get("inline_formula_spans", [])),
            "verification_tier": "machine_verified",
        },
        "limitations_and_threats_to_validity": limit_slot,
        "unreported_or_missing_items": unreported,
        "reusability_tags": tags,
        "verification_summary": {
            "section_count": len(section_notes),
            "machine_verified_fields": 3,
            "needs_human_review_fields": 6,
        },
    }

    return {
        "paper_id": paper_id,
        "source_file": doc.get("source_file"),
        "status": status,
        "section_notes": section_notes,
        "paper_card": paper_card,
        "uncertainties": uncertainties,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build section-aligned note skeleton and Paper Card.")
    parser.add_argument("--parsed", required=True, help="Path to parsed document.json.")
    parser.add_argument("--paper-id", default="paper_1")
    parser.add_argument("--out", required=True, help="Output skeleton JSON path.")
    args = parser.parse_args()

    doc = json.loads(Path(args.parsed).read_text(encoding="utf-8"))
    skel = build_note_skeleton(doc, paper_id=args.paper_id)
    write_json_atomic(args.out, skel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
