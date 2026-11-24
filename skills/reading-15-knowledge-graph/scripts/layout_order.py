#!/usr/bin/env python3
"""Multi-column XY-cut reading order reconstruction, header/footer/marginalia isolation, and paragraph continuation."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from extract_formulas import reconstruct_line_scripts
from pdf_utils import bbox_overlap_ratio, bbox_union, dehyphenate_lines, round_bbox, write_json_atomic

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

ARXIV_SIDE_RE = re.compile(r"arxiv:\s*\d{4}\.\d{4,5}", flags=re.IGNORECASE)
FOOTNOTE_START_RE = re.compile(r"^(?:[*†‡§]|(?:\d{1,2}|[a-d])\s+[A-Z\u4e00-\u9fff]|Note\b)", flags=re.UNICODE)
PAGE_NUM_ONLY_RE = re.compile(r"^[-–—\s]*(?:\d{1,4}|[ivxlc]{1,7}|Page\s+\d+(?:\s+of\s+\d+)?|第\s*\d+\s*页)[-–—\s]*$", re.IGNORECASE)
SENTENCE_END_RE = re.compile(r"[.!?。！？:：]$")
CONTINUATION_START_RE = re.compile(r"^(?:[a-z(,;]|and\b|or\b|where\b|which\b|that\b|[\u4e00-\u9fff])")


def _normalize_hf_text(text: str) -> str:
    s = re.sub(r"\d+", "#", text.strip().lower())
    s = re.sub(r"\s+", " ", s)
    return s


def _detect_columns(
    blocks: Sequence[Dict[str, Any]], page_w: float
) -> List[Tuple[float, float]]:
    """Detect column x-intervals using horizontal coverage of narrow blocks."""
    narrow = [
        b
        for b in blocks
        if (b["bbox"][2] - b["bbox"][0]) < page_w * 0.56
        and (b["bbox"][2] - b["bbox"][0]) >= 20.0
    ]
    if len(narrow) < 2:
        return [(0.0, page_w)]

    w_int = max(100, int(round(page_w)))
    cov = [0] * w_int
    for b in narrow:
        x0 = max(0, int(b["bbox"][0]))
        x1 = min(w_int, int(b["bbox"][2]))
        for i in range(x0, x1):
            cov[i] += 1

    # Search for deep gutters inside [0.22*page_w, 0.78*page_w]
    left_bound = int(page_w * 0.22)
    right_bound = int(page_w * 0.78)
    gutters: List[Tuple[int, int]] = []
    in_zero = False
    z_start = 0
    for x in range(left_bound, right_bound):
        if cov[x] == 0 and not in_zero:
            in_zero = True
            z_start = x
        elif cov[x] > 0 and in_zero:
            in_zero = False
            if x - z_start >= 8:
                gutters.append((z_start, x))
    if in_zero and right_bound - z_start >= 8:
        gutters.append((z_start, right_bound))

    if not gutters:
        return [(0.0, page_w)]

    # Keep gutters that have meaningful text blocks on both sides
    valid_splits: List[float] = []
    for gs, ge in gutters:
        mid = (gs + ge) / 2.0
        left_cnt = sum(1 for b in narrow if b["bbox"][2] <= mid + 4.0)
        right_cnt = sum(1 for b in narrow if b["bbox"][0] >= mid - 4.0)
        if left_cnt >= 1 and right_cnt >= 1:
            valid_splits.append(mid)

    if not valid_splits:
        return [(0.0, page_w)]

    # Limit to at most 2 splits (3 columns)
    if len(valid_splits) > 2:
        valid_splits = [valid_splits[0], valid_splits[-1]]

    cols: List[Tuple[float, float]] = []
    prev_x = 0.0
    for sp in valid_splits:
        cols.append((prev_x, sp))
        prev_x = sp
    cols.append((prev_x, page_w))
    return cols


def _order_band_blocks(
    band_blocks: List[Dict[str, Any]],
    cols: List[Tuple[float, float]],
) -> List[Dict[str, Any]]:
    """Order blocks inside a horizontal band across columns (left-to-right, top-to-bottom)."""
    if len(cols) == 1:
        for b in band_blocks:
            b["column"] = 0
        return sorted(band_blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))

    col_buckets: List[List[Dict[str, Any]]] = [[] for _ in cols]
    for b in band_blocks:
        bx0, _, bx1, _ = b["bbox"]
        b_mid = (bx0 + bx1) / 2.0
        col_idx = min(
            range(len(cols)),
            key=lambda ci: 0.0 if cols[ci][0] <= b_mid <= cols[ci][1] else abs((cols[ci][0] + cols[ci][1]) / 2.0 - b_mid),
        )
        b["column"] = col_idx
        col_buckets[col_idx].append(b)

    ordered: List[Dict[str, Any]] = []
    for bucket in col_buckets:
        bucket.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
        ordered.extend(bucket)
    return ordered


def _merge_visual_lines(lines_data: List[Dict[str, Any]], body_font_size: float) -> List[Dict[str, Any]]:
    """Merge PyMuPDF lines that belong to the same visual row (e.g., split sub/superscripts)."""
    if len(lines_data) <= 1:
        return lines_data

    def _line_meta(ln: Dict[str, Any]) -> Tuple[float, float, float]:
        bbox = ln.get("bbox", [0.0, 0.0, 0.0, 0.0])
        ymid = (float(bbox[1]) + float(bbox[3])) / 2.0
        max_sz = max((float(sp.get("size", body_font_size)) for sp in ln.get("spans", [])), default=body_font_size)
        return ymid, float(bbox[0]), max_sz

    sorted_lines = sorted(lines_data, key=lambda ln: (_line_meta(ln)[0], _line_meta(ln)[1]))
    y_tol = max(4.5, 0.45 * body_font_size)
    groups: List[Dict[str, Any]] = []

    for ln in sorted_lines:
        ymid, _, max_sz = _line_meta(ln)
        placed = False
        for grp in groups:
            if abs(ymid - grp["anchor_ymid"]) <= y_tol:
                grp["spans"].extend(ln.get("spans", []))
                grp["bboxes"].append(ln.get("bbox", [0.0, 0.0, 0.0, 0.0]))
                if max_sz > grp["anchor_size"]:
                    grp["anchor_size"] = max_sz
                    grp["anchor_ymid"] = ymid
                placed = True
                break
        if not placed:
            groups.append(
                {
                    "anchor_ymid": ymid,
                    "anchor_size": max_sz,
                    "spans": list(ln.get("spans", [])),
                    "bboxes": [ln.get("bbox", [0.0, 0.0, 0.0, 0.0])],
                }
            )

    merged: List[Dict[str, Any]] = []
    for grp in groups:
        merged.append({"bbox": bbox_union(grp["bboxes"]), "spans": grp["spans"]})
    return merged


def _extract_raw_blocks_fitz(
    page: Any, page_num: int, body_font_size: float
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    pdict = page.get_text("dict")
    raw_blocks: List[Dict[str, Any]] = []
    changes: List[Dict[str, str]] = []

    for blk in pdict.get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        lines_data = _merge_visual_lines(blk.get("lines", []), body_font_size)
        if not lines_data:
            continue

        plain_lines: List[str] = []
        struct_lines: List[str] = []
        raw_lines: List[str] = []
        line_summaries: List[Dict[str, Any]] = []
        fonts_seen = set()
        max_font_size = 0.0
        weighted_size_sum = 0.0
        weighted_char_sum = 0
        is_bold_chars = 0

        for ln in lines_data:
            spans = ln.get("spans", [])
            if not spans:
                continue
            r_txt, p_txt, s_txt, script_ann, ln_changes = reconstruct_line_scripts(
                spans, page_body_size=body_font_size
            )
            if ln_changes:
                for ch in ln_changes:
                    ch["page"] = page_num
                changes.extend(ln_changes)

            if not p_txt.strip():
                continue

            raw_lines.append(r_txt)
            plain_lines.append(p_txt)
            struct_lines.append(s_txt)
            line_summaries.append(
                {
                    "bbox": round_bbox(ln.get("bbox", blk["bbox"])),
                    "text": p_txt.strip(),
                    "structured_text": s_txt.strip(),
                    "scripts": script_ann,
                }
            )
            for sp in spans:
                st = sp.get("text", "").strip()
                if not st:
                    continue
                fn = str(sp.get("font", ""))
                fonts_seen.add(fn)
                sz = float(sp.get("size", body_font_size))
                max_font_size = max(max_font_size, sz)
                weighted_size_sum += sz * len(st)
                weighted_char_sum += len(st)
                if (int(sp.get("flags", 0)) & 16) or "bold" in fn.lower():
                    is_bold_chars += len(st)

        if not plain_lines:
            continue

        joined_plain, dehy_changes = dehyphenate_lines(plain_lines)
        joined_struct, _ = dehyphenate_lines(struct_lines)
        for ch in dehy_changes:
            ch["page"] = page_num
        changes.extend(dehy_changes)

        avg_sz = (weighted_size_sum / weighted_char_sum) if weighted_char_sum else body_font_size
        is_bold = (is_bold_chars / max(1, weighted_char_sum)) >= 0.65

        raw_blocks.append(
            {
                "page": page_num,
                "bbox": round_bbox(blk["bbox"]),
                "raw_text": "\n".join(raw_lines),
                "text": joined_plain,
                "structured_text": joined_struct,
                "lines": line_summaries,
                "avg_font_size": round(avg_sz, 2),
                "max_font_size": round(max_font_size, 2),
                "is_bold": is_bold,
                "fonts": sorted(fonts_seen),
                "confidence": 0.98,
                "extraction_method": "pymupdf_dict",
            }
        )

    return raw_blocks, changes


def extract_ordered_blocks(
    pdf_path: str | Path,
    diagnostics: Dict[str, Any],
    tables: Sequence[Dict[str, Any]],
    ocr_results: Optional[Dict[int, Dict[str, Any]]] = None,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Extract, isolate headers/footers/marginalia, mask tables, and order multi-column blocks."""
    if diagnostics.get("encrypted") or diagnostics.get("corrupt") or fitz is None:
        return {
            "blocks": [],
            "headers_footers": [],
            "marginalia": [],
            "footnotes": [],
            "merged_paragraphs": [],
            "column_profiles": [],
            "change_log": [],
        }

    path = Path(pdf_path)
    doc = fitz.open(str(path))
    ocr_results = ocr_results or {}
    body_font_size = float(diagnostics.get("body_font_size", 10.0))
    page_num_map = {item["pdf_page"]: item.get("printed_page") for item in diagnostics.get("page_number_map", [])}
    profile_map = {item["page"]: item for item in diagnostics.get("page_profiles", [])}

    try:
        limit = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        per_page_raw: Dict[int, List[Dict[str, Any]]] = {}
        page_dims: Dict[int, Tuple[float, float]] = {}
        all_changes: List[Dict[str, str]] = []

        for idx in range(limit):
            page_num = idx + 1
            page = doc[idx]
            pw, ph = float(page.rect.width), float(page.rect.height)
            page_dims[page_num] = (pw, ph)
            prof = profile_map.get(page_num, {})
            ptype = prof.get("page_type", "digital_text")

            ocr_page = ocr_results.get(page_num)
            if ptype in ("scanned_no_text", "garbled_font_cid") and ocr_page and ocr_page.get("blocks"):
                if ptype == "garbled_font_cid":
                    all_changes.append(
                        {
                            "type": "garbled_layer_replaced_with_ocr",
                            "page": page_num,
                            "detail": f"Replaced garbled text layer on page {page_num} with OCR output (mean_conf={ocr_page.get('mean_confidence')}).",
                        }
                    )
                ocr_blks = []
                for ob in ocr_page["blocks"]:
                    ocr_blks.append(
                        {
                            "page": page_num,
                            "bbox": ob["bbox"],
                            "raw_text": ob["text"],
                            "text": ob["text"],
                            "structured_text": ob["text"],
                            "lines": ob.get("lines", []),
                            "avg_font_size": body_font_size,
                            "max_font_size": body_font_size,
                            "is_bold": False,
                            "fonts": ["OCR"],
                            "confidence": ob.get("confidence", 0.7),
                            "extraction_method": "tesseract_ocr",
                        }
                    )
                per_page_raw[page_num] = ocr_blks
            else:
                blks, chgs = _extract_raw_blocks_fitz(page, page_num, body_font_size)
                per_page_raw[page_num] = blks
                all_changes.extend(chgs)

        # Protect Page 1's largest-font block (Paper Title) from header filtering
        p1_title_bbox = None
        if 1 in per_page_raw and per_page_raw[1]:
            p1_top = [b for b in per_page_raw[1] if b["bbox"][1] < page_dims[1][1] * 0.40]
            if p1_top:
                p1_title_bbox = max(p1_top, key=lambda b: b["max_font_size"])["bbox"]

        # Cross-page frequency of top/bottom edge normalized strings
        edge_counts: Counter[str] = Counter()
        for pnum, blks in per_page_raw.items():
            pw, ph = page_dims[pnum]
            seen_on_page = set()
            for b in blks:
                y_mid = (b["bbox"][1] + b["bbox"][3]) / 2.0
                if y_mid <= ph * 0.11 or y_mid >= ph * 0.89:
                    norm = _normalize_hf_text(b["text"])
                    if norm and norm not in seen_on_page and len(b["text"]) <= 160:
                        seen_on_page.add(norm)
                        edge_counts[norm] += 1

        # Build table lookup per page (excluding image tables which have no extracted cell grid)
        tables_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for tab in tables:
            if tab.get("is_image_table"):
                continue
            for p_str, tb in tab.get("bboxes_by_page", {}).items():
                tables_by_page.setdefault(int(p_str), []).append({"table_id": tab["table_id"], "bbox": tb, "label": tab.get("label")})

        headers_footers: List[Dict[str, Any]] = []
        marginalia: List[Dict[str, Any]] = []
        footnotes: List[Dict[str, Any]] = []
        ordered_all: List[Dict[str, Any]] = []
        column_profiles: List[Dict[str, Any]] = []

        for pnum in range(1, limit + 1):
            pw, ph = page_dims[pnum]
            blks = per_page_raw.get(pnum, [])
            body_candidates: List[Dict[str, Any]] = []

            for b in blks:
                bx0, by0, bx1, by1 = b["bbox"]
                y_mid = (by0 + by1) / 2.0
                x_mid = (bx0 + bx1) / 2.0
                txt = b["text"].strip()

                # Never filter page 1 title
                if pnum == 1 and p1_title_bbox == b["bbox"]:
                    body_candidates.append(b)
                    continue

                # Marginalia (left/right edge stamps, e.g. arXiv vertical text)
                if (x_mid <= pw * 0.075 or x_mid >= pw * 0.925) and (
                    ARXIV_SIDE_RE.search(txt) or (bx1 - bx0) <= 30.0 and (by1 - by0) >= 60.0
                ):
                    marginalia.append(
                        {
                            "page": pnum,
                            "printed_page": page_num_map.get(pnum),
                            "bbox": b["bbox"],
                            "text": txt,
                        }
                    )
                    continue

                # Header / footer isolation (never swallow non-repeating footnotes)
                is_top = y_mid <= ph * 0.11
                is_bot = y_mid >= ph * 0.89
                is_very_edge = y_mid <= ph * 0.075 or y_mid >= ph * 0.93
                norm = _normalize_hf_text(txt)
                is_footnote_like = is_bot and FOOTNOTE_START_RE.match(txt) and edge_counts[norm] < 2
                if (is_top or is_bot) and not is_footnote_like and (
                    PAGE_NUM_ONLY_RE.match(txt)
                    or (edge_counts[norm] >= 2 and len(txt) <= 160)
                    or (is_very_edge and b["avg_font_size"] <= body_font_size * 0.92 and len(txt) <= 120)
                ):
                    headers_footers.append(
                        {
                            "role": "header" if is_top else "footer",
                            "page": pnum,
                            "printed_page": page_num_map.get(pnum),
                            "bbox": b["bbox"],
                            "text": txt,
                        }
                    )
                    continue

                # Table masking
                p_tabs = tables_by_page.get(pnum, [])
                if any(bbox_overlap_ratio(b["bbox"], t["bbox"]) >= 0.60 for t in p_tabs):
                    continue

                body_candidates.append(b)

            # Add table anchor references so table reading position is preserved
            for t in tables_by_page.get(pnum, []):
                body_candidates.append(
                    {
                        "page": pnum,
                        "bbox": t["bbox"],
                        "raw_text": f"[{t.get('label') or t['table_id']}]",
                        "text": f"[{t.get('label') or t['table_id']}]",
                        "structured_text": f"[{t.get('label') or t['table_id']}]",
                        "lines": [],
                        "avg_font_size": body_font_size,
                        "max_font_size": body_font_size,
                        "is_bold": False,
                        "fonts": [],
                        "confidence": 1.0,
                        "extraction_method": "table_anchor",
                        "preset_role": "table_ref",
                        "table_id": t["table_id"],
                    }
                )

            cols = _detect_columns(body_candidates, pw)
            column_profiles.append(
                {
                    "page": pnum,
                    "column_count": len(cols),
                    "columns": [[round(c[0], 2), round(c[1], 2)] for c in cols],
                }
            )

            # Separate spanning full-width blocks from column blocks
            spanning: List[Dict[str, Any]] = []
            in_cols: List[Dict[str, Any]] = []
            gutters = [cols[i][1] for i in range(len(cols) - 1)]

            for b in body_candidates:
                bx0, _, bx1, _ = b["bbox"]
                crosses = any(bx0 < g - 10.0 and bx1 > g + 10.0 for g in gutters)
                if len(cols) > 1 and crosses:
                    b["column"] = "full"
                    spanning.append(b)
                else:
                    in_cols.append(b)

            spanning.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

            # Recursive XY-cut horizontal banding around spanning blocks
            page_ordered: List[Dict[str, Any]] = []
            prev_y = -1e6
            for sp in spanning:
                sp_mid_y = (sp["bbox"][1] + sp["bbox"][3]) / 2.0
                band = [b for b in in_cols if prev_y <= ((b["bbox"][1] + b["bbox"][3]) / 2.0) < sp_mid_y]
                if band:
                    page_ordered.extend(_order_band_blocks(band, cols))
                page_ordered.append(sp)
                prev_y = sp_mid_y

            tail_band = [b for b in in_cols if ((b["bbox"][1] + b["bbox"][3]) / 2.0) >= prev_y]
            if tail_band:
                page_ordered.extend(_order_band_blocks(tail_band, cols))

            # Tag footnotes & assign per-page block IDs
            for b_idx, b in enumerate(page_ordered, start=1):
                role = b.pop("preset_role", None) or "body"
                if role == "body":
                    by_mid = (b["bbox"][1] + b["bbox"][3]) / 2.0
                    if (
                        by_mid >= ph * 0.72
                        and b["avg_font_size"] <= body_font_size * 0.88
                        and FOOTNOTE_START_RE.match(b["text"])
                    ):
                        role = "footnote"
                        footnotes.append(
                            {
                                "page": pnum,
                                "printed_page": page_num_map.get(pnum),
                                "bbox": b["bbox"],
                                "text": b["text"],
                            }
                        )

                b["block_id"] = f"p{pnum}_b{b_idx:02d}"
                b["role"] = role
                b["printed_page"] = page_num_map.get(pnum)
                b["section_id"] = None
                b["section_path"] = []
                b["continuation_of"] = None
                b["continued_by"] = None
                ordered_all.append(b)

        # Assign global reading order & detect cross-column / cross-page paragraph continuations
        merged_paragraphs: List[Dict[str, Any]] = []
        last_body: Optional[Dict[str, Any]] = None
        caption_like_re = re.compile(r"^\s*(?:Figure|Fig\.?|Table|Tab\.?|图|表)\s*[A-Za-z]?\d+", re.IGNORECASE)
        for r_idx, blk in enumerate(ordered_all, start=1):
            blk["reading_order"] = r_idx
            if blk["role"] != "body":
                continue
            txt = blk["text"].strip()
            if not txt:
                continue
            # Floating figure/table captions should not break cross-page prose paragraph linking
            if caption_like_re.match(txt):
                continue

            if last_body is not None:
                prev_txt = last_body["text"].strip()
                crossed_col_or_page = (
                    blk["page"] > last_body["page"] or blk["column"] != last_body["column"]
                )
                # Ensure prev block is not a short heading-like fragment
                if (
                    crossed_col_or_page
                    and len(prev_txt) >= 35
                    and not SENTENCE_END_RE.search(prev_txt)
                    and CONTINUATION_START_RE.match(txt)
                    and blk["avg_font_size"] <= body_font_size * 1.12
                    and last_body["avg_font_size"] <= body_font_size * 1.12
                ):
                    blk["continuation_of"] = last_body["block_id"]
                    last_body["continued_by"] = blk["block_id"]
                    merged_text, cross_chgs = dehyphenate_lines([prev_txt, txt])
                    merged_paragraphs.append(
                        {
                            "from_block": last_body["block_id"],
                            "to_block": blk["block_id"],
                            "pages": sorted({last_body["page"], blk["page"]}),
                            "merged_text": merged_text,
                        }
                    )
                    if cross_chgs:
                        all_changes.extend(cross_chgs)

            last_body = blk

        return {
            "blocks": ordered_all,
            "headers_footers": headers_footers,
            "marginalia": marginalia,
            "footnotes": footnotes,
            "merged_paragraphs": merged_paragraphs,
            "column_profiles": column_profiles,
            "change_log": all_changes,
        }
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract ordered multi-column blocks from a PDF.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from diagnose_pdf import diagnose_pdf

    diag = diagnose_pdf(args.pdf)
    ordered = extract_ordered_blocks(args.pdf, diag, tables=[])
    write_json_atomic(args.out, ordered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
