#!/usr/bin/env python3
"""Adaptive OCR pipeline with 300 DPI rendering, deskew/contrast preprocessing, and confidence tracking."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import round_bbox, write_json_atomic

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None

try:
    from PIL import Image, ImageEnhance, ImageOps  # type: ignore
except ImportError:
    Image = None  # type: ignore
    ImageEnhance = None  # type: ignore
    ImageOps = None  # type: ignore

try:
    import pytesseract  # type: ignore
except ImportError:
    pytesseract = None


def _select_tesseract_lang() -> Tuple[bool, str]:
    tess_bin = shutil.which("tesseract")
    if not tess_bin and pytesseract is None:
        return False, "eng"
    if not tess_bin:
        return False, "eng"
    try:
        proc = subprocess.run(
            [tess_bin, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        langs = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
        if "chi_sim" in langs and "eng" in langs:
            return True, "chi_sim+eng"
        if "eng" in langs:
            return True, "eng"
        return True, "eng"
    except Exception:
        return True, "eng"


def preprocess_pil_image(img: Any) -> Tuple[Any, float]:
    """Convert to grayscale, percentile-normalize contrast, and estimate small-angle deskew."""
    if Image is None:
        return img, 0.0

    gray = ImageOps.grayscale(img)
    best_angle = 0.0

    if np is not None:
        arr = np.asarray(gray, dtype=np.float32)
        p2, p98 = np.percentile(arr, (2, 98))
        if p98 > p2 + 5.0:
            norm = np.clip((arr - p2) * 255.0 / (p98 - p2), 0.0, 255.0).astype(np.uint8)
            gray = Image.fromarray(norm, mode="L")

        # Small-angle deskew test on downsampled thumbnail for speed
        thumb = gray.copy()
        thumb.thumbnail((900, 900))
        t_arr = 255.0 - np.asarray(thumb, dtype=np.float32)
        best_var = float(np.var(t_arr.sum(axis=1)))
        for ang in (-2.5, -2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0, 2.5):
            rot = thumb.rotate(ang, resample=Image.BILINEAR, fillcolor=255)
            r_arr = 255.0 - np.asarray(rot, dtype=np.float32)
            v = float(np.var(r_arr.sum(axis=1)))
            if v > best_var * 1.02:
                best_var = v
                best_angle = ang

        if abs(best_angle) >= 0.5:
            gray = gray.rotate(best_angle, resample=Image.BICUBIC, fillcolor=255)

    if ImageEnhance is not None:
        gray = ImageEnhance.Contrast(gray).enhance(1.25)
        gray = ImageEnhance.Sharpness(gray).enhance(1.15)

    return gray, best_angle


def _parse_tesseract_tsv(
    tsv_text: str,
    page_num: int,
    scale_x: float,
    scale_y: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> Dict[str, Any]:
    lines_map: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    confs: List[float] = []
    low_conf_spans: List[Dict[str, Any]] = []

    rows = tsv_text.splitlines()
    if not rows:
        return {"blocks": [], "text": "", "mean_confidence": 0.0, "p10_confidence": 0.0, "low_confidence_spans": []}

    for row in rows[1:]:
        parts = row.split("\t")
        if len(parts) < 12:
            continue
        try:
            blk_id = int(parts[2])
            par_id = int(parts[3])
            line_id = int(parts[4])
            left = float(parts[6])
            top = float(parts[7])
            width = float(parts[8])
            height = float(parts[9])
            conf = float(parts[10])
        except ValueError:
            continue

        word = parts[11].strip()
        if not word or conf < 0:
            continue

        bbox = round_bbox(
            [
                offset_x + left * scale_x,
                offset_y + top * scale_y,
                offset_x + (left + width) * scale_x,
                offset_y + (top + height) * scale_y,
            ]
        )
        confs.append(conf)
        if conf < 60.0:
            low_conf_spans.append({"text": word, "conf": round(conf, 1), "page": page_num, "bbox": bbox})

        key = (blk_id, par_id, line_id)
        lines_map.setdefault(key, []).append({"word": word, "conf": conf, "bbox": bbox})

    blocks_grouped: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for (blk_id, par_id, _), words in sorted(lines_map.items()):
        line_text = " ".join(w["word"] for w in words)
        line_bbox = [
            min(w["bbox"][0] for w in words),
            min(w["bbox"][1] for w in words),
            max(w["bbox"][2] for w in words),
            max(w["bbox"][3] for w in words),
        ]
        line_conf = sum(w["conf"] for w in words) / max(1, len(words))
        blocks_grouped.setdefault((blk_id, par_id), []).append(
            {"text": line_text, "bbox": round_bbox(line_bbox), "conf": line_conf}
        )

    out_blocks: List[Dict[str, Any]] = []
    for idx, (_, ln_list) in enumerate(sorted(blocks_grouped.items()), start=1):
        blk_text = " ".join(ln["text"] for ln in ln_list).strip()
        if not blk_text:
            continue
        blk_bbox = [
            min(ln["bbox"][0] for ln in ln_list),
            min(ln["bbox"][1] for ln in ln_list),
            max(ln["bbox"][2] for ln in ln_list),
            max(ln["bbox"][3] for ln in ln_list),
        ]
        blk_conf = sum(ln["conf"] for ln in ln_list) / max(1, len(ln_list))
        out_blocks.append(
            {
                "ocr_block_index": idx,
                "page": page_num,
                "bbox": round_bbox(blk_bbox),
                "text": blk_text,
                "lines": ln_list,
                "confidence": round(blk_conf / 100.0, 3),
                "extraction_method": "tesseract_ocr",
            }
        )

    mean_conf = sum(confs) / len(confs) if confs else 0.0
    sorted_confs = sorted(confs)
    p10_conf = sorted_confs[max(0, int(0.1 * len(sorted_confs)))] if sorted_confs else 0.0
    full_text = "\n".join(b["text"] for b in out_blocks)
    return {
        "blocks": out_blocks,
        "text": full_text,
        "mean_confidence": round(mean_conf, 2),
        "p10_confidence": round(p10_conf, 2),
        "low_confidence_spans": low_conf_spans[:50],
    }


def run_ocr_on_image(
    pil_image: Any,
    page_num: int = 1,
    offset_bbox: Optional[Sequence[float]] = None,
    dpi: int = 300,
    psm: int = 3,
    save_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run preprocessing and Tesseract OCR on a PIL Image, degrading honestly if unavailable."""
    if Image is None:
        return {
            "page": page_num,
            "ocr_status": "unavailable",
            "reason": "Pillow (PIL) is not installed; cannot preprocess image for OCR.",
            "blocks": [],
            "text": "",
            "mean_confidence": 0.0,
            "p10_confidence": 0.0,
            "low_confidence_spans": [],
            "image_path": None,
        }

    pre_img, deskew_angle = preprocess_pil_image(pil_image)
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        pre_img.save(str(save_path))

    tess_avail, lang = _select_tesseract_lang()
    if not tess_avail:
        return {
            "page": page_num,
            "ocr_status": "unavailable",
            "reason": "Tesseract OCR binary is not installed in the runtime environment; saved rendered image for visual inspection.",
            "deskew_angle": deskew_angle,
            "blocks": [],
            "text": "",
            "mean_confidence": 0.0,
            "p10_confidence": 0.0,
            "low_confidence_spans": [],
            "image_path": str(save_path) if save_path else None,
        }

    scale = 72.0 / float(dpi)
    off_x = float(offset_bbox[0]) if offset_bbox else 0.0
    off_y = float(offset_bbox[1]) if offset_bbox else 0.0

    try:
        if pytesseract is not None:
            tsv_text = pytesseract.image_to_data(
                pre_img, lang=lang, config=f"--psm {psm}"
            )
        else:
            # CLI fallback using temporary image
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_png = Path(tf.name)
            try:
                pre_img.save(str(tmp_png))
                proc = subprocess.run(
                    ["tesseract", str(tmp_png), "stdout", "-l", lang, "--psm", str(psm), "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or f"tesseract exit {proc.returncode}")
                tsv_text = proc.stdout
            finally:
                if tmp_png.exists():
                    tmp_png.unlink()

        parsed = _parse_tesseract_tsv(
            tsv_text,
            page_num=page_num,
            scale_x=scale,
            scale_y=scale,
            offset_x=off_x,
            offset_y=off_y,
        )
        parsed.update(
            {
                "page": page_num,
                "ocr_status": "ok" if parsed["blocks"] else "empty",
                "lang": lang,
                "deskew_angle": deskew_angle,
                "image_path": str(save_path) if save_path else None,
            }
        )
        return parsed
    except Exception as exc:
        return {
            "page": page_num,
            "ocr_status": "failed",
            "reason": f"Tesseract OCR execution failed: {exc}",
            "deskew_angle": deskew_angle,
            "blocks": [],
            "text": "",
            "mean_confidence": 0.0,
            "p10_confidence": 0.0,
            "low_confidence_spans": [],
            "image_path": str(save_path) if save_path else None,
        }


def run_ocr_on_page(
    pdf_path: str | Path,
    page_num: int,
    crops_dir: Optional[Path] = None,
    dpi: int = 300,
) -> Dict[str, Any]:
    """Render a PDF page at `dpi` and run adaptive OCR."""
    if fitz is None or Image is None:
        return {
            "page": page_num,
            "ocr_status": "unavailable",
            "reason": "PyMuPDF or Pillow unavailable for page rendering.",
            "blocks": [],
            "text": "",
            "mean_confidence": 0.0,
            "p10_confidence": 0.0,
            "low_confidence_spans": [],
            "image_path": None,
        }

    doc = fitz.open(str(pdf_path))
    try:
        if page_num < 1 or page_num > doc.page_count:
            return {
                "page": page_num,
                "ocr_status": "failed",
                "reason": f"Requested page {page_num} out of bounds (1..{doc.page_count}).",
                "blocks": [],
                "text": "",
                "mean_confidence": 0.0,
                "p10_confidence": 0.0,
                "low_confidence_spans": [],
                "image_path": None,
            }
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        save_path = crops_dir / f"ocr_page_{page_num}.png" if crops_dir is not None else None
        return run_ocr_on_image(img, page_num=page_num, dpi=dpi, save_path=save_path)
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run adaptive OCR on a single PDF page.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    res = run_ocr_on_page(args.pdf, page_num=args.page, dpi=args.dpi)
    write_json_atomic(args.out, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
