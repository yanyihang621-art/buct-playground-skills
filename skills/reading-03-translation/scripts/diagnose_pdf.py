#!/usr/bin/env python3
"""Pre-flight PDF diagnostics: encryption, scanned/hybrid detection, garbled fonts, page numbering."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import (
    bbox_area,
    detect_garbled_metrics,
    probe_capabilities,
    round_bbox,
    write_json_atomic,
)

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

try:
    from pypdf import PdfReader  # type: ignore
except ImportError:
    PdfReader = None


ROMAN_MAP = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roman_to_int(s: str) -> Optional[int]:
    low = s.strip().lower()
    if not low or not re.fullmatch(r"[ivxlc]+", low):
        return None
    total = 0
    prev = 0
    for ch in reversed(low):
        val = ROMAN_MAP[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total if 1 <= total <= 399 else None


def _parse_printed_page_candidate(text: str) -> Optional[Tuple[str, Optional[int]]]:
    s = text.strip()
    if not s or len(s) > 36:
        return None

    patterns = [
        r"^[-–—\s]*(\d{1,4})[-–—\s]*$",
        r"^Page\s+(\d{1,4})(?:\s+of\s+\d{1,4})?$",
        r"^p\.?\s*(\d{1,4})$",
        r"^第\s*(\d{1,4})\s*页$",
        r"^(\d{1,4})\s*/\s*\d{1,4}$",
    ]
    for pat in patterns:
        m = re.match(pat, s, flags=re.IGNORECASE)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 5000:
                return (str(num), num)

    if re.fullmatch(r"[-–—\s]*([ivxlc]{1,7})[-–—\s]*", s, flags=re.IGNORECASE):
        m = re.fullmatch(r"[-–—\s]*([ivxlc]{1,7})[-–—\s]*", s, flags=re.IGNORECASE)
        if m:
            rom = m.group(1)
            val = _roman_to_int(rom)
            if val is not None and val <= 60:
                return (rom.lower(), None)

    return None


def _extract_printed_page_for_fitz_page(
    page_dict: Dict[str, Any], page_w: float, page_h: float
) -> Optional[Dict[str, Any]]:
    candidates: List[Tuple[int, str, Optional[int], List[float]]] = []
    top_limit = page_h * 0.12
    bot_limit = page_h * 0.88

    for blk in page_dict.get("blocks", []):
        if blk.get("type", 0) != 0:
            continue
        bbox = blk.get("bbox", [0.0, 0.0, 0.0, 0.0])
        y_mid = (bbox[1] + bbox[3]) / 2.0
        in_bottom = y_mid >= bot_limit
        in_top = y_mid <= top_limit
        if not (in_bottom or in_top):
            continue

        lines = blk.get("lines", [])
        text = " ".join(
            "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
            for ln in lines
        ).strip()

        parsed = _parse_printed_page_candidate(text)
        if parsed is None:
            # Also check individual lines in case header/footer shares a block
            for ln in lines:
                ln_text = "".join(sp.get("text", "") for sp in ln.get("spans", [])).strip()
                ln_parsed = _parse_printed_page_candidate(ln_text)
                if ln_parsed is not None:
                    x_mid = (ln["bbox"][0] + ln["bbox"][2]) / 2.0
                    centered = abs(x_mid - page_w / 2.0) < page_w * 0.25
                    score = (2 if in_bottom else 1) + (2 if centered else 0)
                    candidates.append((score, ln_parsed[0], ln_parsed[1], round_bbox(ln["bbox"])))
            continue

        x_mid = (bbox[0] + bbox[2]) / 2.0
        centered = abs(x_mid - page_w / 2.0) < page_w * 0.25
        score = (2 if in_bottom else 1) + (2 if centered else 0)
        candidates.append((score, parsed[0], parsed[1], round_bbox(bbox)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, label, pint, box = candidates[0]
    return {"printed_page": label, "printed_int": pint, "bbox": box}


def diagnose_pdf(pdf_path: str | Path, max_pages: Optional[int] = None) -> Dict[str, Any]:
    """Inspect a PDF file and return structural, encoding, and anomaly diagnostics."""
    caps = probe_capabilities()
    path = Path(pdf_path)

    base: Dict[str, Any] = {
        "source_file": str(path),
        "file_exists": path.exists(),
        "file_size_bytes": path.stat().st_size if path.exists() else 0,
        "encrypted": False,
        "corrupt": False,
        "page_count": 0,
        "analyzed_pages": 0,
        "body_font_size": 10.0,
        "page_profiles": [],
        "page_number_map": [],
        "anomalies": [],
        "environment_capabilities": caps,
    }

    if not path.exists():
        base["anomalies"].append(
            {
                "code": "missing_file",
                "severity": "critical",
                "pages": [],
                "detail": f"Input PDF not found: {path}",
                "recommended_action": "Verify the input path in /input/case.json.",
            }
        )
        return base

    if fitz is None:
        base["anomalies"].append(
            {
                "code": "pymupdf_unavailable",
                "severity": "high",
                "pages": [],
                "detail": "PyMuPDF (fitz) is not installed.",
                "recommended_action": "Install pymupdf or fall back to pypdf/pdftotext.",
            }
        )
        if PdfReader is not None:
            try:
                reader = PdfReader(str(path))
                if reader.is_encrypted:
                    base["encrypted"] = True
                    base["anomalies"].append(
                        {
                            "code": "encrypted_pdf",
                            "severity": "critical",
                            "pages": [],
                            "detail": "PDF is encrypted and requires a password.",
                            "recommended_action": "Request decrypted PDF or password.",
                        }
                    )
                    return base
                base["page_count"] = len(reader.pages)
            except Exception as exc:
                base["corrupt"] = True
                base["anomalies"].append(
                    {
                        "code": "corrupt_pdf",
                        "severity": "critical",
                        "pages": [],
                        "detail": f"Failed to open PDF: {exc}",
                        "recommended_action": "Verify file integrity.",
                    }
                )
        return base

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        base["corrupt"] = True
        base["anomalies"].append(
            {
                "code": "corrupt_pdf",
                "severity": "critical",
                "pages": [],
                "detail": f"Failed to open PDF with PyMuPDF: {exc}",
                "recommended_action": "Verify PDF file integrity.",
            }
        )
        return base

    try:
        if doc.needs_pass:
            # Check empty password just in case permissions-only lock
            unlocked = doc.authenticate("")
            if not unlocked:
                base["encrypted"] = True
                base["page_count"] = int(doc.page_count)
                base["anomalies"].append(
                    {
                        "code": "encrypted_pdf",
                        "severity": "critical",
                        "pages": list(range(1, base["page_count"] + 1)),
                        "detail": "PDF is password-protected (needs_pass=True) and cannot be decrypted without credentials.",
                        "recommended_action": "Refuse text fabrication and request an unlocked PDF or password.",
                    }
                )
                return base

        total_pages = int(doc.page_count)
        base["page_count"] = total_pages
        limit = total_pages if max_pages is None else min(total_pages, max_pages)
        base["analyzed_pages"] = limit

        font_size_weights: Counter[float] = Counter()
        scanned_pages: List[int] = []
        hybrid_pages: List[int] = []
        garbled_pages: List[int] = []
        empty_pages: List[int] = []

        for idx in range(limit):
            page_num = idx + 1
            page = doc[idx]
            rect = page.rect
            page_w = float(rect.width)
            page_h = float(rect.height)
            page_area = max(1.0, page_w * page_h)

            page_dict = page.get_text("dict")
            raw_text = page.get_text("text") or ""
            stripped = raw_text.strip()
            words = page.get_text("words") or []
            char_count = len(stripped)
            word_count = len(words)

            # Image coverage using get_image_info when available
            img_area = 0.0
            img_count = 0
            try:
                img_infos = page.get_image_info(xrefs=False)
                img_count = len(img_infos)
                for info in img_infos:
                    ib = info.get("bbox")
                    if ib and len(ib) >= 4:
                        img_area += bbox_area(ib)
            except Exception:
                imgs = page.get_images(full=False)
                img_count = len(imgs)
                for blk in page_dict.get("blocks", []):
                    if blk.get("type") == 1:
                        img_area += bbox_area(blk.get("bbox", [0, 0, 0, 0]))

            img_cov = min(1.0, img_area / page_area)
            drawing_count = len(page.get_drawings() or [])

            # Accumulate font sizes for body_font_size
            for blk in page_dict.get("blocks", []):
                if blk.get("type", 0) != 0:
                    continue
                for ln in blk.get("lines", []):
                    for sp in ln.get("spans", []):
                        stxt = sp.get("text", "").strip()
                        if stxt:
                            sz = round(float(sp.get("size", 10.0)), 1)
                            if 6.0 <= sz <= 18.0:
                                font_size_weights[sz] += len(stxt)

            garbled = detect_garbled_metrics(raw_text)

            if char_count < 25 and img_cov >= 0.25:
                page_type = "scanned_no_text"
                scanned_pages.append(page_num)
            elif char_count < 10 and img_count == 0 and drawing_count == 0:
                page_type = "empty_page"
                empty_pages.append(page_num)
            elif garbled["is_garbled"]:
                page_type = "garbled_font_cid"
                garbled_pages.append(page_num)
            elif img_cov >= 0.35 and char_count < 220:
                page_type = "hybrid_mixed"
                hybrid_pages.append(page_num)
            else:
                page_type = "digital_text"

            printed_info = _extract_printed_page_for_fitz_page(page_dict, page_w, page_h)
            base["page_number_map"].append(
                {
                    "pdf_page": page_num,
                    "printed_page": printed_info["printed_page"] if printed_info else None,
                    "printed_int": printed_info["printed_int"] if printed_info else None,
                    "bbox": printed_info["bbox"] if printed_info else None,
                }
            )

            base["page_profiles"].append(
                {
                    "page": page_num,
                    "width": round(page_w, 2),
                    "height": round(page_h, 2),
                    "char_count": char_count,
                    "word_count": word_count,
                    "image_count": img_count,
                    "image_coverage_ratio": round(img_cov, 4),
                    "drawing_count": drawing_count,
                    "garbled_metrics": garbled,
                    "page_type": page_type,
                    "printed_page": printed_info["printed_page"] if printed_info else None,
                }
            )

        if font_size_weights:
            base["body_font_size"] = float(font_size_weights.most_common(1)[0][0])

        if scanned_pages:
            base["anomalies"].append(
                {
                    "code": "scanned_no_text",
                    "severity": "high",
                    "pages": scanned_pages,
                    "detail": f"Pages {scanned_pages} lack a digital text layer and have significant image coverage.",
                    "recommended_action": "Run adaptive OCR pipeline (300 DPI + preprocessing) or visual inspection.",
                }
            )
        if hybrid_pages:
            base["anomalies"].append(
                {
                    "code": "hybrid_mixed_pages",
                    "severity": "medium",
                    "pages": hybrid_pages,
                    "detail": f"Pages {hybrid_pages} combine sparse digital text with high image coverage.",
                    "recommended_action": "Inspect image regions (e.g., embedded figure/table images).",
                }
            )
        if garbled_pages:
            base["anomalies"].append(
                {
                    "code": "garbled_font_cid",
                    "severity": "high",
                    "pages": garbled_pages,
                    "detail": f"Pages {garbled_pages} contain corrupted/unmapped glyph encodings (U+FFFD, PUA, or CID).",
                    "recommended_action": "Keep raw text layer in provenance and run OCR fallback on affected pages.",
                }
            )
        if empty_pages:
            base["anomalies"].append(
                {
                    "code": "empty_pages",
                    "severity": "low",
                    "pages": empty_pages,
                    "detail": f"Pages {empty_pages} contain no text, images, or vector drawings.",
                    "recommended_action": "Note blank pages explicitly.",
                }
            )

        # Page number sequence anomalies
        arabic_seq = [
            (item["pdf_page"], item["printed_int"])
            for item in base["page_number_map"]
            if item["printed_int"] is not None
        ]
        if len(arabic_seq) >= 2:
            out_of_order: List[int] = []
            duplicates: List[int] = []
            seen_ints: Dict[int, int] = {}
            prev_int = arabic_seq[0][1]
            seen_ints[prev_int] = arabic_seq[0][0]
            for pdf_p, pint in arabic_seq[1:]:
                if pint in seen_ints:
                    duplicates.append(pdf_p)
                else:
                    seen_ints[pint] = pdf_p
                if pint < prev_int:
                    out_of_order.append(pdf_p)
                prev_int = pint

            if out_of_order:
                base["anomalies"].append(
                    {
                        "code": "disordered_page_numbers",
                        "severity": "medium",
                        "pages": out_of_order,
                        "detail": (
                            "Printed page numbers are non-monotonic across PDF pages: "
                            f"{[(p, n) for p, n in arabic_seq]}."
                        ),
                        "recommended_action": "Always bind provenance to physical pdf_page while also recording printed_page.",
                    }
                )
            if duplicates:
                base["anomalies"].append(
                    {
                        "code": "duplicate_printed_pages",
                        "severity": "medium",
                        "pages": duplicates,
                        "detail": f"Duplicate printed page numbers detected on PDF pages {duplicates}.",
                        "recommended_action": "Disambiguate provenance using physical pdf_page.",
                    }
                )
    finally:
        doc.close()

    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose PDF readability, layout type, and anomalies.")
    parser.add_argument("--pdf", required=True, help="Path to input PDF.")
    parser.add_argument("--out", required=True, help="Output diagnostics JSON path.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional page cap.")
    args = parser.parse_args()

    diag = diagnose_pdf(args.pdf, max_pages=args.max_pages)
    write_json_atomic(args.out, diag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
