#!/usr/bin/env python3
"""Subscript/superscript, Greek/Symbol font decoding, and display/inline formula extractor."""

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

from pdf_utils import (
    bbox_union,
    decode_symbol_font_text,
    is_symbol_font,
    normalize_ligatures,
    round_bbox,
    write_json_atomic,
)

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

EQ_NUMBER_RE = re.compile(r"^\s*(?:\(([A-Za-z]?\d+(?:\.\d+)?[a-z]?)\)|\[(\d+(?:\.\d+)?)\])\s*$")
EQ_END_NUMBER_RE = re.compile(r"(?:\s|^)(\([A-Za-z]?\d+(?:\.\d+)?[a-z]?\)|\[\d+(?:\.\d+)?\])\s*$")
MATH_FONT_KEYWORDS = ("cmmi", "cmsy", "cmex", "cambriamath", "stix", "mtmi", "euclid", "symbol", "msam", "msbm")
MATH_CHAR_RE = re.compile(r"[=+\-*/×÷±≤≥≠≈∞∂∇∑∏∫√α-ωΑ-Ω^{}_]")
INLINE_CHEM_MATH_RE = re.compile(
    r"(?:[A-Z][a-z]?(?:_\{[^}]+\}|[0-9]+)+(?:\^\{[^}]+\})?|"
    r"[A-Za-z0-9]+(?:\^\{[^}]+\}|_\{[^}]+\})+)"
)


def _span_baseline_y(span: Dict[str, Any]) -> float:
    origin = span.get("origin")
    if origin and len(origin) >= 2:
        return float(origin[1])
    bbox = span.get("bbox", [0.0, 0.0, 0.0, 0.0])
    return float(bbox[3]) - float(span.get("size", 10.0)) * 0.2


def reconstruct_line_scripts(
    spans: Sequence[Dict[str, Any]], page_body_size: float = 10.0
) -> Tuple[str, str, str, List[Dict[str, Any]], List[Dict[str, str]]]:
    """Reconstruct raw text, plain text, and _{}/^{} structured text from line spans."""
    non_empty = [sp for sp in spans if sp.get("text", "") != ""]
    if not non_empty:
        return "", "", "", [], []

    # Sort spans left-to-right by x0
    sorted_spans = sorted(non_empty, key=lambda s: float(s.get("bbox", [0.0])[0]))

    size_weights: Counter[float] = Counter()
    y0_weights: Counter[float] = Counter()
    y1_weights: Counter[float] = Counter()
    for sp in sorted_spans:
        txt = sp.get("text", "").strip()
        if txt:
            sz = round(float(sp.get("size", page_body_size)), 2)
            sb = sp.get("bbox", [0.0, 0.0, 0.0, 0.0])
            w = len(txt)
            size_weights[sz] += w
            y0_weights[round(float(sb[1]), 1)] += w
            y1_weights[round(float(sb[3]), 1)] += w

    dominant_size = float(size_weights.most_common(1)[0][0]) if size_weights else page_body_size
    dom_y0 = float(y0_weights.most_common(1)[0][0]) if y0_weights else float(sorted_spans[0].get("bbox", [0, 0, 0, 0])[1])
    dom_y1 = float(y1_weights.most_common(1)[0][0]) if y1_weights else float(sorted_spans[0].get("bbox", [0, 0, 0, 0])[3])
    dominant_baseline = _span_baseline_y(sorted_spans[0])

    raw_parts: List[str] = []
    plain_parts: List[str] = []
    struct_parts: List[str] = []
    script_annotations: List[Dict[str, Any]] = []
    changes: List[Dict[str, str]] = []

    pending_kind: Optional[str] = None
    pending_buf: List[str] = []
    prev_x1: Optional[float] = None
    prev_was_script = False

    def _flush_pending() -> None:
        nonlocal pending_kind, pending_buf
        if pending_kind and pending_buf:
            joined = "".join(pending_buf)
            if pending_kind == "sup":
                struct_parts.append(f"^{{{joined}}}")
            else:
                struct_parts.append(f"_{{{joined}}}")
        pending_kind = None
        pending_buf = []

    for sp in sorted_spans:
        raw_txt = sp.get("text", "")
        sp_bbox = sp.get("bbox", [0.0, 0.0, 0.0, 0.0])
        sp_x0 = float(sp_bbox[0])
        sp_y0 = float(sp_bbox[1])
        sp_x1 = float(sp_bbox[2])
        sp_y1 = float(sp_bbox[3])

        font_name = str(sp.get("font", ""))
        proc_txt = raw_txt
        if is_symbol_font(font_name):
            proc_txt, sym_cnt = decode_symbol_font_text(proc_txt)
            if sym_cnt > 0:
                changes.append(
                    {
                        "type": "symbol_font_decode",
                        "font": font_name,
                        "before": raw_txt,
                        "after": proc_txt,
                    }
                )

        proc_txt, lig_cnt = normalize_ligatures(proc_txt)
        if lig_cnt > 0:
            changes.append(
                {
                    "type": "ligature_normalize",
                    "before": raw_txt,
                    "after": proc_txt,
                }
            )

        sp_size = float(sp.get("size", dominant_size))
        sp_base = _span_baseline_y(sp)
        base_diff = sp_base - dominant_baseline
        stripped = proc_txt.strip()

        kind: Optional[str] = None
        if stripped and sp_size <= dominant_size * 0.86 and dominant_size >= 6.0:
            # Check vertical bbox shift relative to dominant span bounds (robust to space-span origin quirks)
            if sp_y1 <= dom_y1 - 0.14 * dominant_size:
                kind = "sup"
            elif sp_y1 >= dom_y1 + 0.08 * dominant_size and sp_y0 >= dom_y0 + 0.14 * dominant_size:
                kind = "sub"
            elif base_diff <= -0.15 * dominant_size:
                kind = "sup"
            elif base_diff >= 0.12 * dominant_size:
                kind = "sub"

        # Insert a space between visually separated same-line spans when needed
        need_space = False
        if prev_x1 is not None and raw_parts:
            gap = sp_x0 - prev_x1
            gap_thresh = (0.45 if (kind is not None or prev_was_script) else 0.25) * dominant_size
            if (
                gap > max(2.2, gap_thresh)
                and not (plain_parts[-1].endswith((" ", "\t")))
                and not proc_txt.startswith((" ", "\t", ",", ".", ";", ":", ")", "]"))
            ):
                need_space = True

        if need_space:
            raw_parts.append(" ")
            plain_parts.append(" ")

        raw_parts.append(raw_txt)
        plain_parts.append(proc_txt)

        if kind is not None:
            if pending_kind == kind:
                pending_buf.append(stripped)
            else:
                _flush_pending()
                if need_space and struct_parts and not struct_parts[-1].endswith(" "):
                    struct_parts.append(" ")
                pending_kind = kind
                pending_buf = [stripped]
            script_annotations.append(
                {
                    "kind": kind,
                    "text": stripped,
                    "size": round(sp_size, 2),
                    "baseline_shift": round(base_diff, 2),
                    "bbox": round_bbox(sp_bbox),
                }
            )
            prev_was_script = True
        else:
            _flush_pending()
            if need_space and struct_parts and not struct_parts[-1].endswith(" "):
                struct_parts.append(" ")
            struct_parts.append(proc_txt)
            prev_was_script = False

        prev_x1 = sp_x1

    _flush_pending()

    raw_text = "".join(raw_parts)
    plain_text = re.sub(r" {2,}", " ", "".join(plain_parts))
    structured_text = re.sub(r" {2,}", " ", "".join(struct_parts))
    return raw_text, plain_text, structured_text, script_annotations, changes


def _is_math_font(font_name: str) -> bool:
    low = font_name.lower()
    return any(k in low for k in MATH_FONT_KEYWORDS)


def _crop_bbox_png(page: Any, bbox: Sequence[float], out_path: Path, dpi: int = 300) -> Optional[str]:
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pad = 4.0
        rect = fitz.Rect(
            max(0.0, float(bbox[0]) - pad),
            max(0.0, float(bbox[1]) - pad),
            min(float(page.rect.width), float(bbox[2]) + pad),
            min(float(page.rect.height), float(bbox[3]) + pad),
        )
        pix = page.get_pixmap(dpi=dpi, clip=rect)
        pix.save(str(out_path))
        return str(out_path)
    except Exception:
        return None


def extract_formulas(
    pdf_path: str | Path,
    blocks: Sequence[Dict[str, Any]],
    diagnostics: Dict[str, Any],
    crops_dir: Optional[Path] = None,
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Extract display equations and inline formula spans with page/bbox provenance."""
    display_equations: List[Dict[str, Any]] = []
    inline_spans: List[Dict[str, Any]] = []

    if diagnostics.get("encrypted") or diagnostics.get("corrupt"):
        return {
            "display_equations": [],
            "inline_formula_spans": [],
            "stats": {"display_count": 0, "inline_count": 0, "complex_2d_count": 0},
        }

    # Build quick page_num -> fitz page lookup only if cropping or inspecting math fonts
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
        # Group blocks by page for right-aligned equation label pairing
        blocks_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for blk in blocks:
            if max_pages is not None and blk["page"] > max_pages:
                continue
            blocks_by_page.setdefault(blk["page"], []).append(blk)

        eq_idx = 0
        consumed_ids = set()

        for page_num in sorted(blocks_by_page.keys()):
            page_blks = blocks_by_page[page_num]

            # Pass 1: pair standalone equation tag blocks e.g. "(1)" with left-adjacent formula block
            for idx, blk in enumerate(page_blks):
                if blk["block_id"] in consumed_ids:
                    continue
                txt = blk.get("text", "").strip()
                m_tag = EQ_NUMBER_RE.match(txt)
                if m_tag:
                    label = txt
                    y_mid = (blk["bbox"][1] + blk["bbox"][3]) / 2.0
                    best_partner: Optional[Dict[str, Any]] = None
                    best_dist = 9999.0
                    for cand in page_blks:
                        if cand["block_id"] == blk["block_id"] or cand["block_id"] in consumed_ids:
                            continue
                        if cand.get("role") in ("header", "footer", "table_ref"):
                            continue
                        cy_mid = (cand["bbox"][1] + cand["bbox"][3]) / 2.0
                        if abs(cy_mid - y_mid) <= 18.0 and cand["bbox"][2] <= blk["bbox"][0] + 10.0:
                            dist = blk["bbox"][0] - cand["bbox"][2]
                            if dist < best_dist:
                                best_dist = dist
                                best_partner = cand

                    if best_partner is not None:
                        eq_idx += 1
                        eq_id = f"eq_{eq_idx}"
                        eq_bbox = bbox_union([best_partner["bbox"], blk["bbox"]])
                        consumed_ids.add(blk["block_id"])
                        consumed_ids.add(best_partner["block_id"])

                        lines_info = best_partner.get("lines", [])
                        has_2d = len({round(ln["bbox"][1] / 4.0) for ln in lines_info}) >= 2 and (
                            len(lines_info) >= 2 and len(best_partner.get("text", "")) < 120
                        )
                        crop_path = None
                        if crops_dir is not None and doc is not None and page_num - 1 < doc.page_count:
                            crop_path = _crop_bbox_png(
                                doc[page_num - 1],
                                eq_bbox,
                                crops_dir / f"{eq_id}_p{page_num}.png",
                            )
                        display_equations.append(
                            {
                                "eq_id": eq_id,
                                "label": label,
                                "page": page_num,
                                "printed_page": best_partner.get("printed_page"),
                                "bbox": eq_bbox,
                                "section_id": best_partner.get("section_id"),
                                "block_ids": [best_partner["block_id"], blk["block_id"]],
                                "plain_text": f"{best_partner['text'].strip()} {label}".strip(),
                                "structured_text": f"{best_partner['structured_text'].strip()} {label}".strip(),
                                "has_complex_2d_layout": has_2d,
                                "crop_path": crop_path,
                                "confidence": 0.92 if not has_2d else 0.72,
                            }
                        )
                        continue

            # Pass 2: single block containing equation + trailing "(1)" or high math density
            for blk in page_blks:
                if blk["block_id"] in consumed_ids:
                    continue
                if blk.get("role") in ("header", "footer", "title", "table_ref", "reference"):
                    continue
                stxt = blk.get("structured_text", "").strip()
                ptxt = blk.get("text", "").strip()
                if not ptxt or len(ptxt) > 220:
                    continue

                m_end = EQ_END_NUMBER_RE.search(ptxt)
                math_chars = len(MATH_CHAR_RE.findall(stxt))
                math_density = math_chars / max(1, len(stxt))
                has_eq_sign = "=" in ptxt or "≈" in ptxt or "≤" in ptxt or "≥" in ptxt or "→" in ptxt

                is_display = False
                label = None
                if m_end and (has_eq_sign or math_chars >= 2) and len(ptxt) <= 140:
                    is_display = True
                    label = m_end.group(1)
                elif has_eq_sign and math_density >= 0.14 and len(ptxt) <= 110 and not ptxt.endswith("."):
                    is_display = True

                if is_display:
                    eq_idx += 1
                    eq_id = f"eq_{eq_idx}"
                    consumed_ids.add(blk["block_id"])
                    lines_info = blk.get("lines", [])
                    distinct_y_bands = len({round(((ln["bbox"][1] + ln["bbox"][3]) / 2.0) / 6.0) for ln in lines_info})
                    has_2d = distinct_y_bands >= 2 and any(
                        _is_math_font(sp_font) for sp_font in blk.get("fonts", [])
                    )
                    crop_path = None
                    if crops_dir is not None and doc is not None and page_num - 1 < doc.page_count:
                        crop_path = _crop_bbox_png(
                            doc[page_num - 1],
                            blk["bbox"],
                            crops_dir / f"{eq_id}_p{page_num}.png",
                        )
                    display_equations.append(
                        {
                            "eq_id": eq_id,
                            "label": label,
                            "page": page_num,
                            "printed_page": blk.get("printed_page"),
                            "bbox": blk["bbox"],
                            "section_id": blk.get("section_id"),
                            "block_ids": [blk["block_id"]],
                            "plain_text": ptxt,
                            "structured_text": stxt,
                            "has_complex_2d_layout": has_2d,
                            "crop_path": crop_path,
                            "confidence": 0.90 if not has_2d else 0.72,
                        }
                    )

            # Pass 3: collect inline chemical/math formula spans from non-display blocks
            for blk in page_blks:
                if blk["block_id"] in consumed_ids:
                    continue
                if blk.get("role") in ("header", "footer", "table_ref"):
                    continue
                stxt = blk.get("structured_text", "")
                if "_{" in stxt or "^{" in stxt or re.search(r"[α-ωΑ-Ω±≤≥≠≈×÷∂∑∫]", stxt):
                    matches = INLINE_CHEM_MATH_RE.findall(stxt)
                    # Also capture Greek/math tokens if no ^{}/_{} match
                    if not matches:
                        matches = re.findall(r"\S*[α-ωΑ-Ω±≤≥≠≈∂∑∫]\S*", stxt)
                    if matches:
                        inline_spans.append(
                            {
                                "block_id": blk["block_id"],
                                "page": page_num,
                                "bbox": blk["bbox"],
                                "expressions": matches[:20],
                            }
                        )

        complex_count = sum(1 for eq in display_equations if eq.get("has_complex_2d_layout"))
        return {
            "display_equations": display_equations,
            "inline_formula_spans": inline_spans,
            "stats": {
                "display_count": len(display_equations),
                "inline_block_count": len(inline_spans),
                "complex_2d_count": complex_count,
            },
        }
    finally:
        if doc is not None:
            doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract formulas from a pre-parsed document.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    # Standalone smoke check placeholder
    write_json_atomic(args.out, {"note": "Use parse_pdf.py for full pipeline execution", "pdf": args.pdf})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
