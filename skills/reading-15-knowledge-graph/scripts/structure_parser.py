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

from pdf_utils import bbox_union, dehyphenate_lines, normalize_ligatures, round_bbox, write_json_atomic

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
REF_ITEM_RE = re.compile(r"^(?:\[(\d{1,4})\]\s*|(\d{1,3})[.)]\s+)(?=\S)")
REF_END_RE = re.compile(
    r"^(?:appendi(?:x|ces)\b|supplement(?:ary|al)?\s+(?:material|information)\b|"
    r"acknowledg(?:e)?ments?$|author\s+contributions?$|data\s+availability(?:\s+statement)?$|附录|致谢$)",
    re.IGNORECASE,
)
REF_YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}[a-z]?\b")
REF_AUTHOR_RE = re.compile(
    r"^(?:[A-ZÀ-Þ][^\W\d_]*[.,’'\-]?\s+(?:[A-ZÀ-Þ][\w’'\-]*\b|(?:van|von|de|di)\s+|et\s+al\.)|"
    r"[A-ZÀ-Þ][\w’'\-]+,\s*[A-ZÀ-Þ]|[\u4e00-\u9fff]{2,5}[，、,])"
)
REF_VENUE_RE = re.compile(
    r"\b(?:proceedings|conference|workshop|journal|volume|pages|press|transactions)\b", re.IGNORECASE
)
PUBLISHER_STAMP = re.compile(
    r"^(?:arxiv\s*:\s*\d|doi\s*:\s*10\.|https?://doi\.org/|published as a conference paper\b)",
    re.I,
)
TICK = r"[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?%?"
AXIS_TICKS = re.compile(rf"^(?:{TICK}\s+){{2}}{TICK}(?:\s|$)")


def _classify_heading(blk: Dict[str, Any], body_font_size: float) -> Optional[Tuple[int, Optional[str], str]]:
    if blk.get("role") in ("table_ref", "footnote"):
        return None
    txt = blk.get("text", "").strip()
    if not txt or len(txt) > 160 or AXIS_TICKS.match(txt) or not any(c.isalpha() for c in txt):
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
    page_height: float = 800.0,
) -> Dict[str, Any]:
    p1_blocks = [b for b in blocks if b["page"] == 1 and b.get("role") not in ("table_ref", "footnote")]
    title: Optional[str] = None
    authors: List[str] = []
    affiliations: List[str] = []
    abstract: Optional[str] = None
    keywords: List[str] = []
    byline_evidence: List[Dict[str, Any]] = []

    if p1_blocks:
        top_cands = []
        for block in p1_blocks:
            x0, y0, x1, y1 = block["bbox"]
            text = block["text"].strip()
            if PUBLISHER_STAMP.match(text):
                block["role"] = "publication_notice"
                continue
            if y0 >= page_height * 0.48 or y1 - y0 > 2 * (x1 - x0) or not text:
                continue
            if not ABSTRACT_HEADING_RE.match(text) and len(text) <= 450:
                top_cands.append(block)

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
                    if ABSTRACT_HEADING_RE.match(t) or re.match(
                        r"^(?:1\.?\s+Introduction|I\.\s+Introduction|1\.?\s+引言)", t, re.IGNORECASE
                    ):
                        break
                    if b["bbox"][1] > title_y + 220.0:
                        break
                    if b.get("role") == "publication_notice":
                        continue
                    lines = b.get("lines", [])
                    first_line = lines[0] if lines else {"text": t}
                    runs = first_line.get("spaced_runs", [])
                    names = (
                        [run["text"] for run in runs]
                        if runs
                        else re.split(r",|;|、|；|\band\b", first_line["text"])
                    )
                    names = [re.sub(r"[*∗†‡\d]+$", "", name.strip()).strip() for name in names if name.strip()]
                    name_row = bool(names) and all(
                        re.fullmatch(r"(?:[A-ZÀ-ÖØ-Þ][\w.·’'\-]*\s+){1,4}[\w.·’'\-]+|[\u4e00-\u9fff]{2,5}", name)
                        and not AFFILIATION_HINT_RE.search(name)
                        for name in names
                    )
                    if name_row and (len(runs) > 1 or (len(lines) > 1 and AFFILIATION_HINT_RE.search(t))):
                        authors.extend(names)
                        byline_evidence.append({
                            "field": "authors",
                            "page": b["page"],
                            "block_id": b["block_id"],
                            "bbox": first_line.get("bbox", b["bbox"]),
                            "text": first_line["text"],
                        })
                        for line in lines[1:]:
                            if "@" not in line["text"]:
                                affiliations.append(line["text"])
                                byline_evidence.append({
                                    "field": "affiliations",
                                    "page": b["page"],
                                    "block_id": b["block_id"],
                                    "bbox": line["bbox"],
                                    "text": line["text"],
                                })
                        b["role"] = "authors"
                    elif AFFILIATION_HINT_RE.search(t):
                        for line in (lines or [{"text": t}]):
                            lt = line["text"].strip()
                            if lt and "@" not in lt:
                                affiliations.append(lt)
                        b["role"] = "affiliation"
                    elif len(t) <= 180 and not t.endswith("."):
                        parts = [
                            re.sub(r"[*∗†‡\d]+$", "", p).strip()
                            for p in re.split(r",|;|\band\b", t)
                            if p.strip()
                        ]
                        authors.extend(p for p in parts if p)
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
            label = t[:m_abs.start(1)].rstrip(":： ").strip()
            b["section_path"] = [label]
            abs_parts = [inline_rest] if inline_rest else []
            following = []
            bounded = False
            for nxt in blocks[idx + 1:]:
                if nxt["page"] > 2:
                    break
                txt = nxt["text"].strip()
                if (
                    KEYWORDS_RE.match(txt)
                    or _classify_heading(nxt, body_font_size)
                    or FIGURE_CAPTION_RE.match(txt)
                    or nxt.get("role") in {"table_ref", "footnote"}
                ):
                    bounded = True
                    break
                following.append(nxt)
            if not bounded:
                following = [] if inline_rest else following[:1]
            for nxt in following:
                abs_parts.append(nxt["text"].strip())
                nxt["role"] = "abstract"
                nxt["section_path"] = [label]
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
        "byline_evidence": byline_evidence,
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
            suffix = blk["text"].strip()[m.end(2):]
            if re.match(
                r"^\s+(?:shows?|presents?|demonstrates?|depicts?|illustrates?|reports?|summari[sz]es?|compares?|is|was|gives?)\b",
                suffix,
                re.I,
            ):
                continue
            fig_idx += 1
            blk["role"] = "figure_caption"
            pnum = blk["page"]
            cb = blk["bbox"]
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
    entries = []
    margins: Dict[Tuple[int, Any], List[float]] = {}
    in_refs = False
    for blk in blocks:
        txt = blk["text"].strip()
        if REFERENCES_HEADING_RE.fullmatch(txt):
            in_refs = True
            continue
        if not in_refs:
            in_refs = any(REFERENCES_HEADING_RE.fullmatch(sp.strip()) for sp in blk.get("section_path", []))
            if not in_refs:
                continue
        if blk.get("role") in {"table_ref", "header", "footer", "footnote", "figure_caption"}:
            continue
        if REF_END_RE.match(txt) and (
            blk.get("is_bold")
            or blk.get("role") == "heading"
            or (len(txt) <= 120 and not txt.endswith((".", "。")) and not REF_YEAR_RE.search(txt))
        ):
            break
        if not txt:
            continue
        lines = blk.get("lines") or [{"bbox": blk["bbox"], "text": txt}]
        lines = [ln for ln in lines if ln.get("text", "").strip()]
        if not lines:
            continue
        region = (blk["page"], blk.get("column"))
        entries.append((blk, region, lines))
        start_x = lines[0]["bbox"][0]
        if len(lines) > 1 and 3 <= lines[1]["bbox"][0] - start_x <= 40 and (
            REF_ITEM_RE.match(txt) or REF_YEAR_RE.search(txt[:550])
        ):
            margins.setdefault(region, []).append(start_x)

    first_x = {region: sorted(values)[len(values) // 2] for region, values in margins.items()}
    refs: List[Dict[str, Any]] = []
    parts: List[str] = []
    for blk, region, lines in entries:
        blk["role"] = "reference"
        margin = first_x.get(region)
        for i, ln in enumerate(lines):
            text = ln["text"].strip()
            marker = REF_ITEM_RE.match(text)
            hanging_start = margin is not None and abs(ln["bbox"][0] - margin) <= 2
            author_start = False
            if margin is None and REF_AUTHOR_RE.match(text) and not (i == 0 and blk.get("continuation_of")):
                sample = blk["text"][:550] if i == 0 else text
                year = REF_YEAR_RE.search(sample)
                author_start = bool(year and not REF_VENUE_RE.search(sample[:year.start()]))
            if not refs or marker or (refs[-1]["marker"] is None and (hanging_start or author_start)):
                if refs:
                    refs[-1]["raw_text"] = dehyphenate_lines(parts)[0]
                parts = []
                refs.append({
                    "ref_id": f"ref_{len(refs) + 1}",
                    "marker": (marker[1] or marker[2]) if marker else None,
                    "page": blk["page"],
                    "printed_page": blk.get("printed_page"),
                    "bbox": list(ln["bbox"]),
                    "anchors": [],
                })
            parts.append(text)
            anchors = refs[-1]["anchors"]
            if i > 0 and anchors and anchors[-1]["block_id"] == blk.get("block_id"):
                anchors[-1]["bbox"] = bbox_union([anchors[-1]["bbox"], ln["bbox"]])
            else:
                anchors.append({
                    "page": blk["page"],
                    "printed_page": blk.get("printed_page"),
                    "block_id": blk.get("block_id"),
                    "bbox": list(ln["bbox"]),
                })
            refs[-1]["bbox"] = anchors[0]["bbox"]
    if refs:
        refs[-1]["raw_text"] = dehyphenate_lines(parts)[0]
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

    page_profiles = diagnostics.get("page_profiles") or [{}]
    metadata = _extract_metadata(blocks, body_font_size, toc_list, page_profiles[0].get("height", 800.0))

    root_sections: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    sec_counter = 0
    sec_by_id: Dict[str, Dict[str, Any]] = {}

    for blk in blocks:
        if blk.get("role") in ("title", "authors", "affiliation", "publication_notice", "abstract", "keywords", "footnote"):
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
