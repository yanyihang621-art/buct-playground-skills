#!/usr/bin/env python3
"""Segment document.json in reading order and deterministically mask protected tokens."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pdf_utils import write_json_atomic

# Ordered masking patterns (non-overlapping left-to-right scan)
DISPLAY_EQ_RE = re.compile(r"\$\$[^$]+\$\$")
INLINE_EQ_RE = re.compile(r"\$[^$\n]+\$")
NUMERIC_CIT_RE = re.compile(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]")
AUTHOR_CIT_RE = re.compile(
    r"\([A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+))?(?:,\s*\d{4}[a-z]?)(?:;\s*[A-Z][A-Za-z\-]+(?:\s+(?:et\s+al\.|and\s+[A-Z][A-Za-z\-]+))?,\s*\d{4}[a-z]?)*\)"
)
CROSS_REF_RE = re.compile(
    r"\b(?:Figs?\.|Figures?|Tables?|Tabs?\.|Eqs?\.|Equations?|Sections?|Schemes?)\s*\(?\d+(?:\.\d+)*[a-zA-Z]?\)?"
)
UNIT_EXPR = (
    r"(?:%|wt%|mol%|vol%|at%|°C|K|MPa|kPa|Pa|bar|atm|mL/min|mL|L|µL|uL|mg|g|kg|mmol|mol|"
    r"h\^-1|s\^-1|h|min|ms|s|nm|μm|um|mm|cm|m|eV|kJ/mol|kJ|kcal/mol|mA/cm2|mA|V|mV|ppm|ppb)"
)
NUM_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:[±~<>≤≥]?\s*\d+(?:\.\d+)?(?:\s*[×x]\s*10\^?[−\-]?\d+)?(?:\s*±\s*\d+(?:\.\d+)?)?\s*{UNIT_EXPR})(?![A-Za-z0-9_])"
)
DECIMAL_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+\.\d+(?![A-Za-z0-9_.])")

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[$])")


def mask_protected_tokens(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace equations, citations, cross-refs, and numbers+units with deterministic placeholders."""
    patterns: List[Tuple[str, re.Pattern[str]]] = [
        ("EQ", DISPLAY_EQ_RE),
        ("EQ", INLINE_EQ_RE),
        ("CIT", NUMERIC_CIT_RE),
        ("CIT", AUTHOR_CIT_RE),
        ("REF", CROSS_REF_RE),
        ("NUM", NUM_UNIT_RE),
        ("NUM", DECIMAL_RE),
    ]
    spans: List[Tuple[int, int, str, str]] = []
    occupied: List[Tuple[int, int]] = []

    def _overlaps(s: int, e: int) -> bool:
        return any(not (e <= os or s >= oe) for os, oe in occupied)

    for kind, pat in patterns:
        for m in pat.finditer(text):
            s, e = m.start(), m.end()
            if not _overlaps(s, e):
                occupied.append((s, e))
                spans.append((s, e, kind, m.group(0)))

    spans.sort(key=lambda x: x[0])
    counters: Dict[str, int] = {"EQ": 0, "CIT": 0, "REF": 0, "NUM": 0}
    tokens: Dict[str, str] = {}
    out_parts: List[str] = []
    cursor = 0
    for s, e, kind, val in spans:
        out_parts.append(text[cursor:s])
        counters[kind] += 1
        key = f"__{kind}_{counters[kind]:03d}__"
        tokens[key] = val
        out_parts.append(key)
        cursor = e
    out_parts.append(text[cursor:])
    return "".join(out_parts), tokens


def _merge_broken_english(parts: List[str]) -> str:
    merged = ""
    for p in parts:
        chunk = p.strip()
        if not chunk:
            continue
        if not merged:
            merged = chunk
        elif merged.endswith("-") and chunk[0].islower():
            merged = merged[:-1] + chunk
        else:
            merged = f"{merged} {chunk}"
    return merged


def _split_long_paragraph(text: str, max_chars: int = 420) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    sents = SENTENCE_SPLIT_RE.split(text)
    if len(sents) <= 1:
        return [text]
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for s in sents:
        if cur and cur_len + len(s) + 1 > max_chars:
            chunks.append(" ".join(cur))
            cur = [s]
            cur_len = len(s)
        else:
            cur.append(s)
            cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def segment_document(doc_data: Dict[str, Any], max_chars: int = 420) -> Dict[str, Any]:
    """Convert document.json blocks & tables into ordered 1-to-1 translation segments."""
    doc_status = doc_data.get("status", "completed")
    source_file = doc_data.get("source_file", "")
    uncertainties = list(doc_data.get("uncertainties", []))

    if doc_status == "refused":
        return {
            "schema_version": "reading-03-segments/v1",
            "source_file": source_file,
            "doc_status": "refused",
            "segment_count": 0,
            "segments": [],
            "tables_to_translate": [],
            "uncertainties": uncertainties,
        }

    blocks = doc_data.get("blocks", [])
    by_id = {b.get("block_id"): b for b in blocks if b.get("block_id")}
    consumed_continuations: set[str] = set()
    segments: List[Dict[str, Any]] = []
    seg_counter = 0

    for blk in blocks:
        bid = str(blk.get("block_id", ""))
        if bid in consumed_continuations:
            continue
        role = str(blk.get("role", "body"))
        if role == "table_ref":
            continue

        text_parts = [str(blk.get("structured_text") or blk.get("text") or "")]
        cur_blk = blk
        while cur_blk.get("continued_by"):
            nxt_id = cur_blk["continued_by"]
            nxt = by_id.get(nxt_id)
            if not nxt or nxt_id in consumed_continuations:
                break
            consumed_continuations.add(nxt_id)
            text_parts.append(str(nxt.get("structured_text") or nxt.get("text") or ""))
            cur_blk = nxt

        full_text = _merge_broken_english(text_parts).strip()
        if not full_text:
            continue

        # Determine whether sub-segmentation is appropriate
        if role in ("body", "abstract") and len(full_text) > max_chars:
            sub_chunks = _split_long_paragraph(full_text, max_chars=max_chars)
        else:
            sub_chunks = [full_text]

        for sub_idx, chunk in enumerate(sub_chunks, start=1):
            seg_counter += 1
            suffix = f"_s{sub_idx}" if len(sub_chunks) > 1 else ""
            seg_id = f"seg_{seg_counter:04d}_{bid}{suffix}"
            masked, tokens = mask_protected_tokens(chunk)
            segments.append(
                {
                    "segment_id": seg_id,
                    "block_id": bid,
                    "page": int(blk.get("page", 1)),
                    "bbox": blk.get("bbox"),
                    "role": role,
                    "section_path": blk.get("section_path", []),
                    "reading_order": seg_counter,
                    "source_text": chunk,
                    "masked_text": masked,
                    "protected_tokens": tokens,
                }
            )

    # Tables metadata to translate captions/headers/notes
    tables_out: List[Dict[str, Any]] = []
    for tab in doc_data.get("tables", []):
        tables_out.append(
            {
                "table_id": tab.get("table_id"),
                "label": tab.get("label"),
                "caption": tab.get("caption"),
                "notes": tab.get("notes", []),
                "pages": tab.get("pages", [1]),
                "col_paths": tab.get("col_paths", []),
                "records": tab.get("records", []),
                "markdown": tab.get("markdown"),
                "is_image_table": bool(tab.get("is_image_table", False)),
                "status": tab.get("status", "extracted"),
                "crop_path": tab.get("crop_path"),
            }
        )

    return {
        "schema_version": "reading-03-segments/v1",
        "source_file": source_file,
        "doc_status": doc_status,
        "segment_count": len(segments),
        "segments": segments,
        "tables_to_translate": tables_out,
        "uncertainties": uncertainties,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Segment document.json and mask protected tokens.")
    parser.add_argument("--doc", required=True, help="Path to parsed document.json")
    parser.add_argument("--out", required=True, help="Output segments.json path")
    parser.add_argument("--max-chars", type=int, default=420, help="Split paragraphs longer than N chars at sentence boundaries")
    args = parser.parse_args()

    doc_data = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    res = segment_document(doc_data, max_chars=args.max_chars)
    write_json_atomic(Path(args.out), res)
    print(json.dumps({"doc_status": res["doc_status"], "segment_count": res["segment_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
