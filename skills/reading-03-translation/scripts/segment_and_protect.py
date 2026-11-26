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
    r"(?:mA/cm2|(?:m|k|M|G|T)?Hz|(?:n|µ|μ|m|k|M|G)?W|(?:μ|µ|m|k|M)?J|(?:n|µ|μ|m|k)?A|(?:m|µ|μ|k)?Ω|dB|°F|%|wt%|mol%|vol%|at%|°C|K|MPa|kPa|Pa|bar|atm|mL/min|mL|L|µL|uL|mg|g|kg|mmol|mol|"
    r"h\^-1|s\^-1|h|min|ms|s|nm|μm|um|mm|cm|m|eV|kJ/mol|kJ|kcal/mol|mA|V|mV|ppm|ppb)"
)
NUMBER = r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+\-−]?\d+)?"
NUM_UNIT_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:[±~<>≤≥]\s*)?{NUMBER}(?:\s*[×x]\s*10\^?[−\-]?\d+)?(?:\s*±\s*{NUMBER})?\s*{UNIT_EXPR}(?![A-Za-z0-9_])"
)
STANDALONE_NUMBER_RE = re.compile(rf"(?<![A-Za-z0-9_.]){NUMBER}(?![A-Za-z0-9_]|\.\d)")

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9(\[$"“])')
ABBREVIATION_WORD = re.compile(r"\b(?:figs?|eqs?|tabs?|refs?|secs?|dr|prof|vs|i\.e|e\.g|et al)\.$", re.I)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Offsets in the actual segment (before masking), excluding inter-sentence whitespace."""
    spans = []
    start = 0
    for boundary in SENTENCE_SPLIT_RE.finditer(text):
        end = boundary.start()
        previous = text[start:end]
        if (ABBREVIATION_WORD.search(text, start, end) or re.search(r"\b[A-Z]\.$", previous)
                or re.fullmatch(r"(?:Table|Figure|Fig\.?|Scheme)\s+\d+[a-z]?\.", previous, re.I)):
            continue
        spans.append((start, end))
        start = boundary.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def mask_protected_tokens(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace equations, citations, cross-refs, and numbers+units with deterministic placeholders."""
    patterns: List[Tuple[str, re.Pattern[str]]] = [
        ("EQ", DISPLAY_EQ_RE),
        ("EQ", INLINE_EQ_RE),
        ("CIT", NUMERIC_CIT_RE),
        ("CIT", AUTHOR_CIT_RE),
        ("REF", CROSS_REF_RE),
        ("NUM", NUM_UNIT_RE),
        ("NUM", STANDALONE_NUMBER_RE),
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
    sents = [text[a:b] for a, b in sentence_spans(text)]
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
        source_blocks = [blk]
        while cur_blk.get("continued_by"):
            nxt_id = cur_blk["continued_by"]
            nxt = by_id.get(nxt_id)
            if not nxt or nxt_id == bid or nxt_id in consumed_continuations:
                break
            source_blocks.append(nxt)
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
                    "source_blocks": [
                        {"block_id": b.get("block_id"), "page": b.get("page"), "bbox": b.get("bbox"),
                         "source_locator": b.get("source_locator", {})}
                        for b in source_blocks
                    ],
                    "source_locator": blk.get("source_locator", {}),
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
    def table_segment(text: str, role: str, table: Dict[str, Any], index: int, cell=None) -> str:
        # Reuse a caption when the parser exposes it in both the block and table views.
        tid = table["table_id"]
        pages = table.get("pages") or [1]
        if role == "table_caption":
            for segment in segments:
                same_source = segment.get("source_locator", {}).get("source") == table.get("source_locator", {}).get("source")
                if same_source and segment.get("page") in pages and segment["source_text"] == text:
                    return segment["segment_id"]
        sid = f"{tid}_{role}_{index}" if cell is None else f"{tid}_r{cell['row']}_c{cell['column']}"
        masked, tokens = mask_protected_tokens(text)
        locator = {**table.get("source_locator", {}), "table_id": tid, "field": role, "index": index}
        if cell:
            locator.update(cell)
        segments.append({
            "segment_id": sid, "block_id": tid, "page": locator.get("pdf_page") or pages[0], "table_id": tid, "role": role,
            "source_locator": locator, "section_path": table.get("section_path", []),
            "reading_order": len(segments) + 1, "source_text": text, "masked_text": masked,
            "protected_tokens": tokens,
        })
        return sid

    for tab in doc_data.get("tables", []):
        cap = tab.get("caption") or tab.get("label")
        ids = {
            "caption": table_segment(str(cap), "table_caption", tab, 1) if cap else None,
            "columns": [table_segment(str(col), "table_column", tab, i) for i, col in enumerate(tab.get("col_paths", []), 1)],
            "notes": [table_segment(str(note), "table_note", tab, i) for i, note in enumerate(tab.get("notes", []), 1)],
        }
        # Use ordered cells so identical column names do not erase data. Retain
        # blank/merged positions and read PDF cell-specific geometry where available.
        header_count = tab.get("header_row_count", 1)
        grid = tab.get("data_rows")
        if grid is None:
            grid = [list(record.values()) for record in tab.get("records", [])]
        metadata = {(cell["row"], cell["col"]): cell for cell in tab.get("cells", [])}
        grid_ids = []
        for ri, values in enumerate(grid):
            row_ids = []
            for ci, value in enumerate(values):
                text = str(value).strip() if value is not None else ""
                if not text:
                    row_ids.append(None)
                    continue
                cell = {"row": header_count + ri + 1, "column": ci + 1}
                meta = metadata.get((header_count + ri, ci), {})
                if meta.get("page") and tab.get("source_locator", {}).get("pdf_page"):
                    cell.update(pdf_page=meta["page"], bbox=meta.get("bbox"))
                row_ids.append(table_segment(text, "table_cell", tab, ci + 1, cell))
            grid_ids.append(row_ids)
        ids["rows"] = grid_ids
        tables_out.append(
            {
                "table_id": tab.get("table_id"),
                "translation_ids": ids,
                "label": tab.get("label"),
                "caption": tab.get("caption"),
                "notes": tab.get("notes", []),
                "pages": tab.get("pages", [1]),
                "col_paths": tab.get("col_paths", []),
                "records": tab.get("records", []),
                "rows_en": grid,
                "markdown": tab.get("markdown"),
                "is_image_table": bool(tab.get("is_image_table", False)),
                "status": tab.get("status", "extracted"),
                "crop_path": tab.get("crop_path"),
            }
        )

    # Put the completed table back where its placeholder appeared in the original
    # reading order. Stable sorting keeps rows, columns and split prose in order.
    position = {b["block_id"]: i for i, b in enumerate(blocks) if b.get("block_id")}
    position.update({b["table_id"]: i for i, b in enumerate(blocks)
                     if b.get("role") == "table_ref" and b.get("table_id")})
    segments.sort(key=lambda s: position.get(s["block_id"], len(blocks)))
    for i, segment in enumerate(segments, 1):
        segment["reading_order"] = i
    return {
        "schema_version": "reading-03-segments/v1",
        "source_file": source_file,
        "doc_status": doc_status,
        "segment_count": len(segments),
        "segments": segments,
        "tables_to_translate": tables_out,
        "uncertainties": uncertainties,
    }


def select_scope(segments: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a case-requested passage range after attaching the original source locators."""
    scope = case.get("translation_scope")
    if not isinstance(scope, dict):
        return segments
    fields = ("segment_ids", "block_ids", "paragraph_ids", "pages", "sections")

    def as_set(value):
        items = value if isinstance(value, list) else [value]
        return {str(x).strip().casefold() for x in items if x is not None and str(x).strip()}

    requested = {key: as_set(scope[key]) for key in fields if scope.get(key)}
    bounds = scope.get("page_range")
    if not requested and bounds is None:
        return segments
    issues = list(segments.get("uncertainties", []))
    page_range = None
    if bounds is not None:
        try:
            start, end = bounds
            start, end = int(start), int(end)
            if not isinstance(bounds, (list, tuple)) or start < 1 or start > end:
                raise ValueError
            page_range = (start, end)
        except (TypeError, ValueError):
            issues.append({"code": "invalid_page_range", "detail": "Expected [first_pdf_page, last_pdf_page]."})
            page_range = (1, 0)

    found = {key: set() for key in requested}
    selected = []
    for seg in segments["segments"]:
        locs = seg.get("source_spans") or [seg.get("source_locator") or {}]
        blocks = {b["block_id"] for b in seg.get("source_blocks", []) if b.get("block_id")}
        blocks.add(seg["block_id"])
        pages = {int(loc["pdf_page"]) for loc in locs if loc.get("pdf_page") is not None}
        if page_range and not any(page_range[0] <= p <= page_range[1] for p in pages):
            continue
        attributes = {
            "segment_ids": [seg["segment_id"]], "block_ids": blocks,
            "paragraph_ids": [v for loc in locs for v in (loc.get("paragraph_id"), loc.get("docx_paragraph"))],
            "pages": pages, "sections": seg.get("section_path", []),
        }
        matches = {key: requested[key] & as_set(list(attributes[key])) for key in requested}
        if all(matches.values()):
            selected.append(seg)
            for key in matches:
                found[key].update(matches[key])

    for key in requested:
        missing = sorted(requested[key] - found[key])
        if missing:
            issues.append({"code": "scope_item_not_found", "field": key, "requested": missing})
    if not selected:
        issues.append({"code": "scope_empty", "detail": "No segment matched the passage specified in the case."})
    ids = {seg["segment_id"] for seg in selected}
    tables = []
    for table in segments.get("tables_to_translate", []):
        tx = table.get("translation_ids", {})
        members = [tx.get("caption"), *tx.get("columns", []), *tx.get("notes", []),
                   *(sid for row in tx.get("rows", []) for sid in row)]
        if any(sid in ids for sid in members):
            tables.append(table)
    doc_status = segments["doc_status"]
    if len(issues) > len(segments.get("uncertainties", [])) and doc_status != "refused":
        doc_status = "partial"
    return {**segments, "doc_status": doc_status, "segments": selected, "segment_count": len(selected),
            "tables_to_translate": tables, "translation_scope": scope, "uncertainties": issues}


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
