#!/usr/bin/env python3
"""Academic metadata, hierarchical section tree, figure pairing, and reference list parser."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import bbox_union, normalize_ligatures, round_bbox, write_json_atomic

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

ABSTRACT_HEADING_RE = re.compile(r"^(?:abstract|summary|摘\s*要)\b[:：]?\s*(.*)$", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^(?:keywords?|index\s+terms?|关键词)\b[:：]?\s*(.*)$", re.IGNORECASE)
REFERENCES_HEADING_RE = re.compile(r"^(?:\d+\.?\s*)?(?:references|bibliography|literature\s+cited|参考文献)\s*$", re.IGNORECASE)
AFFILIATION_HINT_RE = re.compile(
    r"(?:university|institute|department|laboratory|school\s+of|college\s+of|academy|center|centre|@|大学|学院|研究所|实验室)",
    flags=re.IGNORECASE,
)

# Heading patterns: (level, regex)
HEADING_PATTERNS: List[Tuple[int, re.Pattern[str]]] = [
    (3, re.compile(r"^(\d+\.\d+\.\d+)\.?\s+(.+)$")),
    (2, re.compile(r"^(\d+\.\d+)\.?\s+(.+)$")),
    (2, re.compile(r"^([A-Z]\.\d+)\.?\s+(.+)$")),
    (2, re.compile(r"^（([一二三四五六七八九十]+)）\s*(.+)$")),
    (1, re.compile(r"^(\d+)\.?\s+([A-Z\u4e00-\u9fff].+)$")),
    (1, re.compile(r"^([IVXLCDM]+)\.\s+([A-Z].+)$")),
    (1, re.compile(r"^([一二三四五六七八九十]+)[、.]\s*(.+)$")),
    (1, re.compile(r"^(Appendix\s+[A-Z])\b[:：.\s]*(.*)$", re.IGNORECASE)),
]

UNNUMBERED_L1_RE = re.compile(
    r"^(?:abstract|introduction|related\s+work|background|methods?|methodology|materials\s+and\s+methods|"
    r"experimental|experiments|results|discussion|results\s+and\s+discussion|conclusions?|acknowledgements?|"
    r"acknowledgments?|references|bibliography|appendix|摘要|引言|绪论|相关工作|方法|实验|结果与讨论|结论|致谢|参考文献)\s*$",
    flags=re.IGNORECASE,
)

FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:(Figure|Fig\.?|图)\s*([A-Za-z]?\d+(?:\.\d+)?))\b[:.\s]*(.*)$",
    flags=re.IGNORECASE,
)
REF_ITEM_RE = re.compile(r"^(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+(.+)$")


def _classify_heading(blk: Dict[str, Any], body_font_size: float) -> Optional[Tuple[int, Optional[str], str]]:
    if blk.get("role") in ("table_ref", "footnote"):
        return None
    txt = blk.get("text", "").strip()
    if not txt or len(txt) > 160:
        return None
    # Reject table/figure captions or sentences ending in period (except numbered like "1. Introduction")
    if FIGURE_CAPTION_RE.match(txt) or re.match(r"^(?:Table|Tab\.?|表)\s*\d+", txt, re.IGNORECASE):
        return None

    avg_sz = float(blk.get("avg_font_size", body_font_size))
    is_bold = bool(blk.get("is_bold", False))
    bigger_font = avg_sz >= body_font_size * 1.09
    prominent = bigger_font or (is_bold and len(txt) <= 100 and not txt.endswith("."))

    for lvl, pat in HEADING_PATTERNS:
        m = pat.match(txt)
        if m:
            num = m.group(1).strip()
            title = m.group(2).strip()
            # Guard against numbered list items or reference items inside prose/references
            if lvl == 1 and num.isdigit() and int(num) > 25:
                continue
            if len(title) < 2:
                continue
            # Require either prominent styling or short clean title without terminal period
            if prominent or (len(txt) <= 85 and not txt.endswith(".") and len(txt.split()) <= 12):
                return lvl, num, title

    if UNNUMBERED_L1_RE.match(txt) and (prominent or len(txt) <= 45):
        return 1, None, txt

    if bigger_font and len(txt) <= 90 and not txt.endswith(".") and blk.get("page", 1) >= 1:
        # Exclude title/author lines on top of page 1 (handled in metadata)
        if not (blk.get("page") == 1 and blk["bbox"][1] < 220 and avg_sz >= body_font_size * 1.35):
            return 1, None, txt

    return None


def _extract_metadata(
    blocks: Sequence[Dict[str, Any]],
    body_font_size: float,
    toc: List[Dict[str, Any]],
) -> Dict[str, Any]:
    p1_blocks = [b for b in blocks if b["page"] == 1 and b.get("role") not in ("table_ref", "footnote")]
    title: Optional[str] = None
    authors: List[str] = []
    affiliations: List[str] = []
    abstract: Optional[str] = None
    keywords: List[str] = []

    if p1_blocks:
        # Title: largest-font block in top 45% of page 1
        top_cands = [b for b in p1_blocks if b["bbox"][1] < 380.0 and not ABSTRACT_HEADING_RE.match(b["text"])]
        if top_cands:
            title_blk = max(top_cands, key=lambda b: (b["max_font_size"], -b["bbox"][1]))
            if title_blk["max_font_size"] >= body_font_size * 1.18 or title_blk["is_bold"]:
                title = title_blk["text"].strip()
                title_blk["role"] = "title"

                # Collect authors / affiliations between title and Abstract / first numbered section
                title_y = title_blk["bbox"][3]
                for b in p1_blocks:
                    if b["block_id"] == title_blk["block_id"]:
                        continue
                    if b["bbox"][1] < title_y - 5.0:
                        continue
                    t = b["text"].strip()
                    if ABSTRACT_HEADING_RE.match(t) or re.match(r"^(?:1\.?\s+Introduction|I\.\s+Introduction|1\.?\s+引言)", t, re.IGNORECASE):
                        break
                    if b["bbox"][1] > title_y + 220.0:
                        break
                    if AFFILIATION_HINT_RE.search(t):
                        affiliations.append(t)
                        b["role"] = "affiliation"
                    elif len(t) <= 180 and not t.endswith("."):
                        # Split comma/and-separated authors if single clean line
                        parts = [re.sub(r"[*†‡\d]+$", "", p).strip() for p in re.split(r",|;|\band\b", t) if p.strip()]
                        authors.extend([p for p in parts if p])
                        b["role"] = "authors"

    # Extract Abstract and Keywords from Page 1 (or Page 2)
    for idx, b in enumerate(blocks):
        if b["page"] > 2:
            break
        t = b["text"].strip()
        m_abs = ABSTRACT_HEADING_RE.match(t)
        if m_abs and abstract is None:
            inline_rest = m_abs.group(1).strip()
            b["role"] = "abstract"
            abs_parts = [inline_rest] if inline_rest else []
            # Look ahead to immediate body block(s) if inline abstract was empty
            if not inline_rest and idx + 1 < len(blocks):
                nxt = blocks[idx + 1]
                if nxt["page"] <= 2 and not KEYWORDS_RE.match(nxt["text"]) and not _classify_heading(nxt, body_font_size):
                    abs_parts.append(nxt["text"].strip())
                    nxt["role"] = "abstract"
            abstract = " ".join(abs_parts).strip() or None
            continue

        m_kw = KEYWORDS_RE.match(t)
        if m_kw and not keywords:
            kw_str = m_kw.group(1).strip()
            b["role"] = "keywords"
            if kw_str:
                keywords = [k.strip() for k in re.split(r"[;；,，、]", kw_str) if k.strip()]

    return {
        "title": title,
        "authors": authors,
        "affiliations": affiliations,
        "abstract": abstract,
        "keywords": keywords,
        "toc": toc,
    }


def _extract_figures(
    pdf_path: str | Path, blocks: Sequence[Dict[str, Any]], max_pages: Optional[int]
) -> List[Dict[str, Any]]:
    figures: List[Dict[str, Any]] = []
    doc = None
    if fitz is not None and Path(pdf_path).exists():
        try:
            doc = fitz.open(str(pdf_path))
            if doc.needs_pass and not doc.authenticate(""):
                doc.close()
                doc = None
        except Exception:
            doc = None

    try:
        img_boxes_by_page: Dict[int, List[List[float]]] = {}
        if doc is not None:
            limit = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
            for idx in range(limit):
                pnum = idx + 1
                page = doc[idx]
                boxes: List[List[float]] = []
                try:
                    for info in page.get_image_info(xrefs=False):
                        ib = info.get("bbox")
                        if ib and (ib[2] - ib[0]) >= 35.0 and (ib[3] - ib[1]) >= 25.0:
                            boxes.append(round_bbox(ib))
                except Exception:
                    pass
                # Also cluster large vector drawing regions if no raster images found
                if not boxes:
                    draw_rects = []
                    for drw in page.get_drawings() or []:
                        r = drw.get("rect")
                        if r and (r.width >= 30.0 or r.height >= 25.0) and r.height > 4.0:
                            draw_rects.append([float(r.x0), float(r.y0), float(r.x1), float(r.y1)])
                    if draw_rects:
                        boxes.append(bbox_union(draw_rects))
                img_boxes_by_page[pnum] = boxes

        fig_idx = 0
        for blk in blocks:
            if blk.get("role") in ("table_ref", "header", "footer"):
                continue
            m = FIGURE_CAPTION_RE.match(blk["text"].strip())
            if not m:
                continue
            fig_idx += 1
            blk["role"] = "figure_caption"
            pnum = blk["page"]
            cb = blk["bbox"]
            # Find nearest image box above (or below) caption
            best_img = None
            best_dist = 9999.0
            for ib in img_boxes_by_page.get(pnum, []):
                h_ov = max(0.0, min(ib[2], cb[2] + 60.0) - max(ib[0], cb[0] - 60.0))
                if h_ov <= 0.0:
                    continue
                if 0.0 <= cb[1] - ib[3] <= 260.0:
                    d = cb[1] - ib[3]
                elif 0.0 <= ib[1] - cb[3] <= 220.0:
                    d = ib[1] - cb[3] + 10.0
                else:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_img = ib

            figures.append(
                {
                    "figure_id": f"fig_{fig_idx}",
                    "label": f"{m.group(1)} {m.group(2)}".strip(),
                    "caption": blk["text"].strip(),
                    "page": pnum,
                    "printed_page": blk.get("printed_page"),
                    "caption_bbox": cb,
                    "image_bbox": best_img,
                    "section_id": blk.get("section_id"),
                }
            )
        return figures
    finally:
        if doc is not None:
            doc.close()


def _extract_references(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    in_refs = False
    ref_idx = 0

    for blk in blocks:
        txt = blk["text"].strip()
        if REFERENCES_HEADING_RE.match(txt):
            in_refs = True
            continue

        # Detect start of references by "[1] Author..." even without heading if near end
        if not in_refs:
            if blk.get("section_path") and any(
                "reference" in sp.lower() or "参考文献" in sp for sp in blk["section_path"]
            ):
                in_refs = True
            else:
                continue

        # Stop if a new top-level Appendix heading starts
        if re.match(r"^Appendix\s+[A-Z]\b", txt, re.IGNORECASE) and len(txt) <= 80:
            in_refs = False
            continue

        if blk.get("role") in ("table_ref", "header", "footer", "figure_caption"):
            continue

        blk["role"] = "reference"
        # Split multi-entry blocks if multiple [n] markers appear on separate lines
        lines = blk.get("lines", [])
        if lines and sum(1 for ln in lines if REF_ITEM_RE.match(ln["text"])) >= 2:
            cur_marker = None
            cur_parts: List[str] = []
            cur_boxes: List[List[float]] = []
            for ln in lines:
                m = REF_ITEM_RE.match(ln["text"])
                if m:
                    if cur_parts:
                        ref_idx += 1
                        refs.append(
                            {
                                "ref_id": f"ref_{ref_idx}",
                                "marker": cur_marker,
                                "raw_text": " ".join(cur_parts),
                                "page": blk["page"],
                                "printed_page": blk.get("printed_page"),
                                "bbox": bbox_union(cur_boxes),
                            }
                        )
                    cur_marker = m.group(1) or m.group(2)
                    cur_parts = [ln["text"]]
                    cur_boxes = [ln["bbox"]]
                else:
                    cur_parts.append(ln["text"])
                    cur_boxes.append(ln["bbox"])
            if cur_parts:
                ref_idx += 1
                refs.append(
                    {
                        "ref_id": f"ref_{ref_idx}",
                        "marker": cur_marker,
                        "raw_text": " ".join(cur_parts),
                        "page": blk["page"],
                        "printed_page": blk.get("printed_page"),
                        "bbox": bbox_union(cur_boxes),
                    }
                )
        else:
            m = REF_ITEM_RE.match(txt)
            marker = (m.group(1) or m.group(2)) if m else None
            if marker is None and refs and blk["page"] == refs[-1]["page"] and not txt.startswith("["):
                # Continuation of previous reference entry
                refs[-1]["raw_text"] = refs[-1]["raw_text"] + " " + txt
                refs[-1]["bbox"] = bbox_union([refs[-1]["bbox"], blk["bbox"]])
            else:
                ref_idx += 1
                refs.append(
                    {
                        "ref_id": f"ref_{ref_idx}",
                        "marker": marker,
                        "raw_text": txt,
                        "page": blk["page"],
                        "printed_page": blk.get("printed_page"),
                        "bbox": blk["bbox"],
                    }
                )

    return refs


def parse_document_structure(
    pdf_path: str | Path,
    layout_data: Dict[str, Any],
    tables: Sequence[Dict[str, Any]],
    formulas: Dict[str, Any],
    diagnostics: Dict[str, Any],
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Build metadata, hierarchical section tree, figure/table section links, and reference list."""
    blocks = layout_data.get("blocks", [])
    body_font_size = float(diagnostics.get("body_font_size", 10.0))

    # Read PDF bookmarks / TOC when present
    toc_list: List[Dict[str, Any]] = []
    if fitz is not None and not diagnostics.get("encrypted") and not diagnostics.get("corrupt") and Path(pdf_path).exists():
        try:
            doc = fitz.open(str(pdf_path))
            if not doc.needs_pass or doc.authenticate(""):
                for item in doc.get_toc() or []:
                    if len(item) >= 3:
                        t_txt, _ = normalize_ligatures(str(item[1]).strip())
                        toc_list.append({"level": int(item[0]), "title": t_txt, "page": int(item[2])})
            doc.close()
        except Exception:
            toc_list = []

    metadata = _extract_metadata(blocks, body_font_size, toc_list)

    # Build hierarchical section tree
    root_sections: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    sec_counter = 0
    sec_by_id: Dict[str, Dict[str, Any]] = {}

    for blk in blocks:
        if blk.get("role") in ("title", "authors", "affiliation", "abstract", "keywords", "table_ref", "footnote"):
            continue
        cls = _classify_heading(blk, body_font_size)
        if cls is not None:
            lvl, num, s_title = cls
            sec_counter += 1
            sec_id = f"sec_{sec_counter}"
            blk["role"] = "heading"
            node: Dict[str, Any] = {
                "section_id": sec_id,
                "level": lvl,
                "number": num,
                "title": s_title,
                "page_start": blk["page"],
                "page_end": blk["page"],
                "bbox": blk["bbox"],
                "block_ids": [blk["block_id"]],
                "children": [],
            }
            sec_by_id[sec_id] = node

            while stack and stack[-1]["level"] >= lvl:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                root_sections.append(node)
            stack.append(node)

            blk["section_id"] = sec_id
            blk["section_path"] = [
                (f"{s['number']} {s['title']}".strip() if s["number"] else s["title"]) for s in stack
            ]
        elif stack:
            cur_sec = stack[-1]
            blk["section_id"] = cur_sec["section_id"]
            blk["section_path"] = [
                (f"{s['number']} {s['title']}".strip() if s["number"] else s["title"]) for s in stack
            ]
            cur_sec["block_ids"].append(blk["block_id"])
            for s in stack:
                s["page_end"] = max(s["page_end"], blk["page"])

    # Assign section_id to tables, formulas, and extract figures & references
    blk_by_id = {b["block_id"]: b for b in blocks}
    for tab in tables:
        anchor = next((b for b in blocks if b.get("table_id") == tab["table_id"]), None)
        tab["section_id"] = anchor.get("section_id") if anchor else None
        tab["section_path"] = anchor.get("section_path", []) if anchor else []

    for eq in formulas.get("display_equations", []):
        if not eq.get("section_id") and eq.get("block_ids"):
            ref_b = blk_by_id.get(eq["block_ids"][0])
            if ref_b:
                eq["section_id"] = ref_b.get("section_id")

    figures = _extract_figures(pdf_path, blocks, max_pages=max_pages)
    references = _extract_references(blocks)

    return {
        "metadata": metadata,
        "sections": root_sections,
        "figures": figures,
        "references": references,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse structural metadata and sections.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    write_json_atomic(args.out, {"note": "Use parse_pdf.py for full pipeline execution", "pdf": args.pdf})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
